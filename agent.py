#!/usr/bin/env python3
# Copyright 2026 Dheeraj Ponnaganti
# Licensed under the Apache License, Version 2.0.
# See the LICENSE file in the project root for the full text.
"""Always-on macOS voice agent. You speak a goal; it works until the goal is met.

One Jev request per step. Jev returns typed judgments only (Choice/Noul/Score,
never generated text), so this code supplies every possible answer and Jev picks:
page controls become numbered Choice options, and free strings like a search
query come from candidate spans of the goal. Jev cannot name an element that is
not on the page or invent a query the user did not say.
"""

import argparse
import collections
import difflib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from typesafe_sdk import Choice, Noul, TypeSafeClient

HERE = Path(__file__).parent
LOG = HERE / "log.jsonl"
PROFILE = HERE / ".chrome-profile"

# --- calibration knobs. A real microphone in a real room needs tuning. ---
SR, BLOCK = 16000, 1600          # 16 kHz mono, 100 ms blocks
VAD_MULT = 4.0                   # RMS threshold = noise_floor * VAD_MULT
VAD_FLOOR = 0.010
HANGOVER = 7                     # blocks of silence that close an utterance
MAX_BLOCKS = 120                 # hard cap per utterance (12 s)
MIN_SEC = 0.4                    # drop coughs and door slams
WAKE = "computer"
WAKE_RATIO = 0.75                # difflib slack for fuzzy transcription
SAY_DEADTIME = 0.25
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"

MAX_STEPS = 15                   # hard cap per goal
# Gate on ARGUMENT confidence, not on the operation choice. Being torn between
# "click the button" and "press enter" is ordinary ambiguity that the next step
# corrects. The floors then split by what a mistake costs: a wrong click is
# undone by one `back`, and several page controls are often equally acceptable,
# which spreads probability without being an error. Typing the wrong string, or
# opening the wrong app, is what a user actually notices.
TARGET_FLOOR = 0.30             # which numbered element to act on
VALUE_FLOOR = 0.55              # a query, a URL, text to type, an app name
OP_FLOOR = 0.25                 # the op choice only has to beat noise
DONE_AT = 0.7                    # Noul above this ends the loop
STUCK_REPEATS = 3                # same action, no page change -> blocked

NONE = "__none__"
STOP = re.compile(r"\b(stop|cancel|abort|nevermind|never mind|quit it|that's enough)\b")
YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm)\b")
NO = re.compile(r"\b(no|nope|cancel|stop|don'?t|never mind|nevermind|abort)\b")

APP_DIRS = ["/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", str(Path.home() / "Applications")]
BROWSERS = ["Google Chrome", "Brave Browser", "Firefox", "Safari"]


def installed_apps():
    """Discover apps instead of hardcoding them. A hardcoded list built from a
    truncated directory listing is why "open android studio" once failed."""
    seen = {}
    for d in APP_DIRS:
        try:
            for p in sorted(Path(d).glob("*.app")):
                seen.setdefault(p.stem, None)
        except OSError:
            pass
    return [a for a in seen if not a.startswith(".")][:230]   # Choice caps at 255


APPS = installed_apps()

MEDIA_KEYS = {
    "play_pause": "kp:play-pause", "next": "kp:play-next",
    "previous": "kp:play-previous", "mute": "kp:mute",
    "volume_up": "kp:volume-up", "volume_down": "kp:volume-down",
}
WINDOW_KEYS = {
    "quit": ('"q"', "command down"),
    "minimize": ('"m"', "command down"),
    "fullscreen": ('"f"', "{command down, control down}"),
    "new_window": ('"n"', "command down"),
}

# Operations needing an out-loud yes even mid-loop, because they are not undoable.
IRREVERSIBLE = {"quit_app"}

# Free, reversible, and never worth abandoning a goal over. A low-confidence
# "scroll" means Jev was torn between scrolling and clicking, not that scrolling
# is risky, so these run regardless of confidence.
HARMLESS = {"scroll_down", "scroll_up", "back", "new_tab", "wait", "press_enter"}


# ------------------------------------------------------------------- OS actions

def safe_url(u):
    """Spoken address -> https URL. Reject any scheme but http(s) BEFORE touching
    the string: "file:///etc/passwd" has no dot, so appending ".com" and
    prefixing "https://" would produce a URL that parses as scheme https."""
    u = re.sub(r"\s+(dot|point)\s+", ".", u.strip().lower()).replace(" ", "")
    if re.search(r"[^\x21-\x7e]", u):                     # control chars, newlines
        raise ValueError(f"refusing url: {u!r}")
    m = re.match(r"^([a-z][a-z0-9+.\-]*)://", u)
    if m:
        scheme, rest = m.group(1), u[m.end():]
        if scheme not in ("http", "https"):
            raise ValueError(f"refusing url: {u!r}")
    else:
        if ":" in u.split("/")[0]:                        # javascript:, file:/, data:
            raise ValueError(f"refusing url: {u!r}")
        scheme, rest = "https", u
    host, _, path = rest.partition("/")
    if "." not in host:
        host += ".com"
    label = r"[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"           # no leading/trailing hyphen
    if not re.fullmatch(rf"{label}(?:\.{label})+(?::\d+)?", host):
        raise ValueError(f"refusing url: {u!r}")
    return f"{scheme}://{host}" + (f"/{path}" if path else "")


def search_url(q):
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"


def build(op, a):
    """OS-level op -> one argv list. Pure. This function IS the allowlist for
    anything that leaves the browser."""
    if op == "open_app":
        return ["open", "-a", a["app"]]                    # launches AND focuses
    if op == "media":
        return ["cliclick", MEDIA_KEYS[a["action"]]]
    if op == "quit_app":
        key, mod = WINDOW_KEYS["quit"]
        return ["osascript", "-e",
                f'tell application "System Events" to keystroke {key} using {mod}']
    if op == "window":
        key, mod = WINDOW_KEYS[a["action"]]
        return ["osascript", "-e",
                f'tell application "System Events" to keystroke {key} using {mod}']
    if op == "type_here":
        text = re.sub(r"[\x00-\x1f\x7f]", "", a["text"])   # never send Return
        return ["cliclick", "-w", "40", "t:" + text]
    raise ValueError(f"unknown os op: {op!r}")


def frontmost_app():
    try:
        out = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of '
                                'first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------ candidate spans

TRIGGER = re.compile(
    r"^(?:for|about|search|google|look|up|find|type|write|say|dictate|quote|to|go|"
    r"open|in|on|launch|navigate|visit|show|me|play|called|named|titled|enter)$")


def spans(cmd, max_len=12, cap=180):
    """Over-find, per the value-extraction cookbook: every suffix, plus every
    window opening after a trigger word. Jev picks; it cannot invent."""
    toks = cmd.split()
    n = len(toks)
    out = [" ".join(toks[i:]) for i in range(n) if n - i <= max_len]
    for i, t in enumerate(toks):
        if TRIGGER.match(t.lower().strip(".,;:!?")):
            for j in range(i + 2, min(i + 2 + max_len, n + 1)):
                out.append(" ".join(toks[i + 1:j]))
    quoted = re.findall(r'"([^"]{1,120})"', cmd)
    return list(dict.fromkeys(q for q in quoted + out if q))[:cap]


def url_spans(cmd, cap=60):
    low = cmd.lower()
    out = re.findall(r"[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:/\S*)?", low)
    out += re.findall(r"[a-z0-9\-]+ (?:dot|point) (?:com|org|net|io|dev|ai|co|uk)", low)
    out += [w for w in low.split() if w in (
        "google", "youtube", "github", "gmail", "reddit", "twitter", "x",
        "wikipedia", "netflix", "amazon", "linkedin", "news", "maps")]
    return list(dict.fromkeys(out))[:cap]


WORD = re.compile(r"[A-Za-z0-9']+")


def strip_wake(text):
    """Return the command after the wake word, or None if not addressed to us.

    Slices the ORIGINAL text rather than rejoining lowercased tokens, so
    dictation keeps its capitals and commas. Only trailing sentence punctuation
    is dropped, since a stray period would ride along into a search query."""
    words = list(WORD.finditer(text))
    if not words:
        return None
    low = [w.group().lower() for w in words]
    i = 2 if low[0] in ("hey", "ok", "okay") and len(low) > 1 else 1
    head = low[i - 1]
    if head != WAKE and difflib.SequenceMatcher(None, head, WAKE).ratio() < WAKE_RATIO:
        return None
    return text[words[i - 1].end():].lstrip(" ,.:;-—").rstrip(" .!?").strip()


# ----------------------------------------------------------------- the questions

OPS = {
    "click": "Click a control that is listed in page_elements. Use this to press "
             "a button, follow a link, or open a search result.",
    "type": "Type text into a text field listed in page_elements. Use this to "
            "fill a search box or a form field.",
    "press_enter": "Press Return to submit what has already been typed. Choose "
                   "this immediately after `history` shows text was typed into a "
                   "search box or form field and it still needs submitting.",
    "scroll_down": "Scroll the page down to reveal content below the viewport.",
    "scroll_up": "Scroll the page up.",
    "web_search": "Open a Google results page for a query. The fastest way to "
                  "start when the goal is to find something on the web.",
    "goto": "Navigate the browser straight to a website or address.",
    "new_tab": "Open a fresh empty browser tab.",
    "back": "Go back to the previous page.",
    "open_app": "Launch or switch to a macOS application outside the browser.",
    "quit_app": "Quit the application that is currently in front.",
    "window": "Change the front application's window: minimize it, make it "
              "fullscreen, or open a new window. Not for quitting.",
    "type_here": "Type text into the focused field of a macOS app outside the "
                 "browser, for dictating into a note or a document.",
    "media": "Control playback or system audio: play, pause, skip, louder, "
             "quieter, mute.",
    "wait": "Wait briefly because the page is still loading or changing.",
    "done": "The goal has been achieved. Nothing further is needed.",
    "blocked": "The goal cannot be achieved from here: it needs a login, a "
               "CAPTCHA, a payment, or something this agent must not do.",
}


def step_questions(goal, state, elements):
    """One request. Every argument is speculative; the dispatcher reads only the
    chosen op's answers. Independent questions run in parallel, so batching them
    costs almost nothing over asking for the operation alone."""
    el_click = {str(e["i"]): e["desc"] for e in elements if not e["typeable"]}
    el_type = {str(e["i"]): e["desc"] for e in elements if e["typeable"]}
    text_opts = spans(goal)
    return {
        "__op__": Choice(
            instructions="You are driving a Mac to achieve `goal`. `history` is "
                         "what has already been done, `page_elements` lists the "
                         "controls currently on screen, and `page_text` is what "
                         "is visible. Choose the single next operation that makes "
                         "the most progress. Do not repeat an action in `history` "
                         "that already succeeded. If the goal is visibly achieved, "
                         "choose done.",
            criteria=OPS),
        "__done__": Noul(
            instructions="Judging only from `page_text`, `page_url` and `history`, "
                         "the goal stated in `goal` has already been fully achieved "
                         "and the user would consider this finished."),
        "click.target": Choice(
            instructions="Which numbered control in `page_elements` should be "
                         "clicked to advance the goal? Prefer the most direct "
                         "match. For a search results page, the first organic "
                         "result link is usually the intended one.",
            criteria=el_click or {NONE: "There is nothing worth clicking."}),
        "type.target": Choice(
            instructions="Which numbered text field in `page_elements` should "
                         "receive the text?",
            criteria=el_type or {NONE: "There is no text field to type into."}),
        "type.text": Choice(
            instructions="Exactly the words to type, taken from the goal. Exclude "
                         "command words like search, google, for, type, open, and "
                         "exclude application or website names that are not part "
                         "of the text itself.",
            criteria={t: None for t in text_opts} |
                     {NONE: "None of these is the text to type."}),
        "web_search.query": Choice(
            instructions="Exactly the words to search for, taken from the goal. "
                         "Exclude the command words (search, google, for, look up, "
                         "find) and any browser or application name. Prefer the "
                         "shortest span that is the complete search subject.",
            criteria={t: None for t in text_opts} |
                     {NONE: "None of these is the search subject."}),
        "goto.url": Choice(
            instructions="The website or address to open, exactly as stated in "
                         "the goal.",
            criteria={u: None for u in url_spans(goal)} |
                     {NONE: "The goal names no website address."}),
        "open_app.app": Choice(
            instructions="Which application should be launched or brought to the "
                         "front? Match how people say it out loud: 'chrome' is "
                         "Google Chrome, 'code' or 'vs code' is Visual Studio "
                         "Code, 'my notes' is Obsidian, 'android studio' is "
                         "Android Studio.",
            criteria={a: None for a in APPS}),
        "window.action": Choice(
            instructions="What should happen to the front application's window?",
            criteria={
                "minimize": "Minimize, hide, or send the window to the dock.",
                "fullscreen": "Enter or leave fullscreen, maximize the window.",
                "new_window": "Open a new, empty window of the application.",
            }),
        "type_here.text": Choice(
            instructions="Exactly the words to type into the focused field, "
                         "verbatim, taken from the goal. Exclude the dictation "
                         "command itself (type, write, dictate, say).",
            criteria={t: None for t in text_opts} |
                     {NONE: "None of these is the text to type."}),
        "media.action": Choice(
            instructions="Which playback or audio control does the goal ask for?",
            criteria={
                "play_pause": "Start playing, resume, pause, or stop playback.",
                "next": "Skip forward: next track, next song, skip this one.",
                "previous": "Go back: previous track, last song.",
                "volume_up": "Make it louder, turn it up.",
                "volume_down": "Make it quieter, turn it down.",
                "mute": "Mute, silence, or unmute the system audio.",
            }),
    }


def conf_of(ans):
    """Choice carries .confidence. Noul has none, so a Noul used as a gate
    contributes max(p, 1-p): 0.5 is maximal uncertainty and must drag the
    minimum down. Our extension, not a documented rule."""
    c = getattr(ans, "confidence", None)
    return c if c is not None else max(ans.noul, 1.0 - ans.noul)


NEEDS = {                       # op -> the answer keys the dispatcher reads
    "click": ["click.target"],
    "type": ["type.target", "type.text"],
    "web_search": ["web_search.query"],
    "goto": ["goto.url"],
    "open_app": ["open_app.app"],
    "media": ["media.action"],
    "window": ["window.action"],
    "type_here": ["type_here.text"],
}

# Ops the loop resolves itself rather than executing. Kept explicit so the
# reachability check below can prove every other op has a real implementation.
CONTROL = {"wait", "done", "blocked"}
BROWSER_OPS = {"click", "type", "press_enter", "scroll_down", "scroll_up",
               "goto", "web_search", "new_tab", "back"}


def read_step(r, op):
    """Return (args, target_conf, value_conf). Everything else was speculative."""
    args, tconf, vconf = {}, 1.0, 1.0
    for key in NEEDS.get(op, []):
        a = r.answers[key]
        if a.choice == NONE:
            return None, conf_of(a), conf_of(a)
        field = key.split(".", 1)[1]
        if field == "target":
            args[field] = int(a.choice)
            tconf = min(tconf, conf_of(a))
        else:
            args[field] = a.choice
            vconf = min(vconf, conf_of(a))
    return args, tconf, vconf


# ------------------------------------------------------------------- speaking

muted = threading.Event()
audio_q = queue.Queue()


def speak(text):
    muted.set()
    try:
        subprocess.run(["say", text], timeout=25)
    except Exception:
        pass
    time.sleep(SAY_DEADTIME)
    while not audio_q.empty():          # drain what leaked in
        try:
            audio_q.get_nowait()
        except queue.Empty:
            break
    muted.clear()


# ---------------------------------------------------------------------- audio

def mic_stream():
    import sounddevice as sd

    def cb(indata, frames, t, status):
        if muted.is_set():
            return                       # hard gate: our own `say` never reaches VAD
        audio_q.put(indata[:, 0].copy())

    return sd.InputStream(samplerate=SR, blocksize=BLOCK, channels=1,
                          dtype="float32", callback=cb)


class Ears:
    """One VAD state machine over the mic queue, shared by the wake loop, the
    yes/no prompts and the mid-loop stop check, so they cannot fight over audio."""

    def __init__(self):
        import numpy as np
        self.np = np
        self.floor = 0.005
        self.ring = collections.deque(maxlen=5)
        self.voiced = self.silent = 0
        self.buf = None

    def _feed(self, b):
        rms = float(self.np.sqrt(self.np.mean(b * b)))
        if self.buf is None:
            self.floor = 0.995 * self.floor + 0.005 * rms      # adaptive noise floor
        thresh = max(self.floor * VAD_MULT, VAD_FLOOR)
        self.ring.append(b)
        if rms > thresh:
            self.voiced, self.silent = self.voiced + 1, 0
            if self.buf is None and self.voiced >= 2:
                self.buf = list(self.ring)                     # pre-roll keeps "com-"
        else:
            self.voiced = 0
            if self.buf is not None:
                self.silent += 1
        if self.buf is None:
            return None
        self.buf.append(b)
        if self.silent >= HANGOVER or len(self.buf) >= MAX_BLOCKS:
            a = self.np.concatenate(self.buf)
            self.buf, self.silent = None, 0
            return a if len(a) >= SR * MIN_SEC else None
        return None

    def next(self, timeout=None):
        """Block for one utterance. None on timeout."""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            if deadline and time.time() > deadline:
                return None
            try:
                b = audio_q.get(timeout=0.25)
            except queue.Empty:
                continue
            a = self._feed(b)
            if a is not None:
                return a

    def poll(self):
        """Non-blocking: an utterance only if one has already finished arriving."""
        while True:
            try:
                b = audio_q.get_nowait()
            except queue.Empty:
                return None
            a = self._feed(b)
            if a is not None:
                return a


_whisper = None


def transcribe(audio):
    """The one seam that changes if STT is swapped."""
    global _whisper
    if _whisper is None:
        import mlx_whisper
        _whisper = mlx_whisper
    return _whisper.transcribe(audio, path_or_hf_repo=WHISPER_MODEL,
                               language="en")["text"].strip()


# ----------------------------------------------------------------------- log

def log(**kw):
    try:
        with LOG.open("a") as f:
            f.write(json.dumps(kw, default=str) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------- the loop

def describe(op, args):
    if op == "web_search":
        return f"searching for {args['query']}"
    if op == "goto":
        return f"opening {args['url']}"
    if op == "open_app":
        return f"opening {args['app']}"
    if op == "click":
        return f"clicking {args.get('label', args['target'])}"
    if op == "type":
        return f"typing {args['text']}"
    if op in ("media", "window"):
        return args["action"].replace("_", " ")
    if op == "type_here":
        return f"typing {args['text']}"
    if op == "quit_app":
        return "quitting the front app"
    return op.replace("_", " ")


class Agent:
    def __init__(self, client, browser=None, dry_run=False, verbose=True,
                 narrate=True, ask=None, ears=None):
        self.client, self.br, self.dry = client, browser, dry_run
        self.verbose, self.narrate, self.ask, self.ears = verbose, narrate, ask, ears

    def _say(self, text):
        if self.narrate:
            speak(text)
        print(f"  ~ {text}")

    def _state(self, goal, history):
        st = {"goal": goal, "step": len(history) + 1, "max_steps": MAX_STEPS,
              "history": history[-6:] or ["nothing yet"],
              "frontmost_app": frontmost_app()}
        elements = []
        if self.br:
            snap = self.br.snapshot()
            st |= {"page_url": snap["url"], "page_title": snap["title"],
                   "page_text": snap["text"], "open_tabs": snap["tabs"],
                   "scrolled": f'{snap["scroll"]} of {snap["height"]} px'}
            st["page_elements"] = self.br.element_table(snap)
            elements = [{"i": e["i"], "typeable": e["typeable"],
                         "desc": f'{e["text"] or "(no label)"}'
                                 f'{" [" + e["role"] + "]" if e["role"] else ""}'}
                        for e in snap["elements"]]
            self._secret = snap["secret_page"]
        else:
            st["page_elements"] = "(browser not attached)"
            self._secret = False
        return st, elements

    def _execute(self, op, args):
        if op in BROWSER_OPS:
            if not self.br:
                return "no browser attached"
            if op == "click":
                return self.br.click(args["target"])
            if op == "type":
                if self._secret:
                    raise PermissionError("refusing to type on a login or payment page")
                return self.br.type_into(args["target"], args["text"])
            if op == "press_enter":
                return self.br.press("Enter")
            if op == "scroll_down":
                return self.br.scroll(600)
            if op == "scroll_up":
                return self.br.scroll(-600)
            if op == "goto":
                return self.br.goto(safe_url(args["url"]))
            if op == "web_search":
                return self.br.goto(search_url(args["query"]))
            if op == "new_tab":
                return self.br.new_tab()
            if op == "back":
                return self.br.back()
        argv = build(op, args)
        print(f"  $ {' '.join(argv)}")
        subprocess.run(argv, check=False)
        return f"ran {argv[0]}"

    def run(self, goal):
        history, last, repeats = [], None, 0
        for step in range(1, MAX_STEPS + 1):
            if self.ears:                      # spoken kill word, checked between steps
                a = self.ears.poll()
                if a is not None and STOP.search(transcribe(a).lower()):
                    self._say("Stopped.")
                    log(goal=goal, step=step, result="user_stopped")
                    return "stopped"

            state, elements = self._state(goal, history)
            try:
                r = self.client.system_one(state, step_questions(goal, state, elements))
            except Exception as e:
                log(goal=goal, step=step, error=repr(e))
                self._say("I could not reach the brain.")
                return "error"

            op_ans, done_ans = r.answers["__op__"], r.answers["__done__"]
            op = op_ans.choice
            if self.verbose:
                print(f"[{step}] op={op} op_conf={op_ans.confidence:.2f} "
                      f"done={done_ans.noul:.2f} tok={r.usage.input_tokens}")

            if done_ans.noul >= DONE_AT or op == "done":
                self._say("Done.")
                log(goal=goal, step=step, result="done", done=done_ans.noul)
                return "done"
            if op == "blocked":
                self._say("I am blocked on this one.")
                log(goal=goal, step=step, result="blocked")
                return "blocked"
            if op == "wait":
                time.sleep(1.2)
                history.append("waited for the page")
                continue

            args, tconf, vconf = read_step(r, op)
            conf = min(op_ans.confidence, tconf, vconf)
            if args is None:
                log(goal=goal, step=step, op=op, result="no_target", conf=conf)
                self._say("I could not find what I needed on screen.")
                return "blocked"
            if op not in HARMLESS and (tconf < TARGET_FLOOR or vconf < VALUE_FLOOR
                                       or op_ans.confidence < OP_FLOOR):
                log(goal=goal, step=step, op=op, args=args, conf=conf,
                    target_conf=tconf, value_conf=vconf,
                    op_conf=op_ans.confidence, result="refused")
                self._say("I am not sure enough to do that.")
                return "unsure"

            if op == "click":
                args["label"] = next((e["desc"] for e in elements
                                      if e["i"] == args["target"]), args["target"])
            sig = (op, tuple(sorted(args.items())))   # every argument, or two
            #                                            different apps look alike
            repeats = repeats + 1 if sig == last else 0
            last = sig
            if repeats >= STUCK_REPEATS:
                self._say("I am going in circles, stopping.")
                log(goal=goal, step=step, op=op, args=args, result="stuck")
                return "stuck"

            if op in IRREVERSIBLE and self.ask and not self.ask(describe(op, args)):
                log(goal=goal, step=step, op=op, args=args, result="declined")
                return "declined"

            self._say(describe(op, args))
            log(goal=goal, step=step, op=op, args=args, conf=conf,
                done=done_ans.noul, url=state.get("page_url"), dry_run=self.dry)
            if self.dry:
                history.append(f"(dry run) {describe(op, args)}")
                continue
            try:
                outcome = self._execute(op, args)
            except PermissionError as e:
                # a policy refusal will refuse identically next step, so stop
                print(f"  ! {e}")
                self._say("I will not do that on this page.")
                log(goal=goal, step=step, op=op, result="refused_by_policy",
                    detail=str(e))
                return "refused"
            except Exception as e:
                outcome = f"failed: {e}"
                print(f"  ! {outcome}")
            history.append(outcome)
            if self.verbose:
                print(f"  -> {outcome}")
        self._say(f"I stopped after {MAX_STEPS} steps.")
        log(goal=goal, result="step_cap")
        return "step_cap"


# --------------------------------------------------------------------- live

def run_live(client, browser, dry_run, verbose, narrate):
    import numpy as np
    print("warming whisper...")
    transcribe(np.zeros(SR // 2, dtype=np.float32))
    ears = Ears()

    def ask(desc):
        speak(f"{desc}?")
        a = ears.next(timeout=6)
        if a is None:
            return False
        reply = transcribe(a).lower()
        print(f"  reply: {reply!r}")
        return bool(YES.search(reply)) and not NO.search(reply)

    agent = Agent(client, browser, dry_run, verbose, narrate, ask, ears)
    with mic_stream():
        print(f'listening. say "{WAKE}, <what you want>"   (ctrl-c to stop)')
        while True:
            a = ears.next()
            text = transcribe(a)
            if not text:
                continue
            goal = strip_wake(text)
            print(f"heard: {text!r}" + ("" if goal else "  (not addressed to me)"))
            if goal is None:
                continue
            if not goal:
                speak("Yes?")
                a = ears.next(timeout=8)
                if a is None:
                    continue
                goal = transcribe(a).strip().rstrip(".!?")
                print(f"goal: {goal!r}")
                if not goal:
                    continue
            try:
                agent.run(goal)
            except Exception as e:
                log(goal=goal, error=repr(e))
                print(f"  error: {e}", file=sys.stderr)      # the loop must not die


# ------------------------------------------------------------------ selfcheck

def selfcheck():
    cases = [
        (("open_app", {"app": "Obsidian"}), ["open", "-a", "Obsidian"]),
        (("media", {"action": "play_pause"}), ["cliclick", "kp:play-pause"]),
        (("media", {"action": "volume_down"}), ["cliclick", "kp:volume-down"]),
        (("window", {"action": "minimize"}),
         ["osascript", "-e", 'tell application "System Events" to '
                             'keystroke "m" using command down']),
        (("quit_app", {}),
         ["osascript", "-e", 'tell application "System Events" to '
                             'keystroke "q" using command down']),
        (("type_here", {"text": 'say "hi" && rm -rf /'}),
         ["cliclick", "-w", "40", 't:say "hi" && rm -rf /']),
    ]
    for (op, a), want in cases:
        got = build(op, a)
        assert got == want, f"{op} {a}\n  got  {got}\n  want {want}"

    assert search_url("cute puppy pics") == \
        "https://www.google.com/search?q=cute+puppy+pics"

    assert strip_wake("Computer, open chrome.") == "open chrome"
    assert strip_wake("commuter pause") == "pause"           # fuzzy transcription
    assert strip_wake("Hey computer, pause") == "pause"
    assert strip_wake("Computer, type Dear Sarah, thanks for the invite.") == \
        "type Dear Sarah, thanks for the invite"
    assert strip_wake("my computer is slow") is None         # wake word not leading
    assert strip_wake("") is None
    assert strip_wake("computer") == ""                      # bare wake word

    s = spans("open chrome and search google for cute puppy pics")
    assert "cute puppy pics" in s, s
    assert 'a b c' in spans('type "a b c" into the box')      # quoted spans kept
    assert "github dot com" in url_spans("go to github dot com")

    assert safe_url("github dot com") == "https://github.com"
    assert safe_url("github") == "https://github.com"
    assert safe_url("news.ycombinator.com/newest") == "https://news.ycombinator.com/newest"
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
                "ftp://x.com/a", "evil.com/a\nrm", "-.com"):
        try:
            safe_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"safe_url accepted {bad!r}")

    for bad_op in ("shell", "eval", "click"):                # click is browser-only
        try:
            build(bad_op, {})
        except (ValueError, KeyError):
            pass
        else:
            raise AssertionError(f"build accepted {bad_op!r}")

    assert abs(conf_of(type("N", (), {"noul": 0.5})()) - 0.5) < 1e-9
    assert abs(conf_of(type("N", (), {"noul": 0.02})()) - 0.98) < 1e-9

    assert "Android Studio" in APPS, "app discovery missed Android Studio"
    assert "Google Chrome" in APPS
    assert len(APPS) <= 255, "Choice allows at most 255 options"

    import browser as B
    assert B.is_secret("https://accounts.google.com/signin")
    assert B.is_secret("https://shop.com/checkout")
    assert B.is_secret("https://www.revolut.com/x")
    assert B.is_secret("https://x.com/a", [{"secret": True}])   # a password field
    assert not B.is_secret("https://www.google.com/search?q=puppies")
    assert not B.is_secret("https://news.ycombinator.com/")

    assert set(NEEDS) <= set(OPS), "NEEDS names an op that does not exist"
    # every op Jev can choose must be executable, and every executable op must be
    # offerable. This is what caught `window` and `type_here` being unreachable.
    OS_OPS = {"open_app", "quit_app", "window", "type_here", "media"}
    assert set(OPS) == BROWSER_OPS | OS_OPS | CONTROL, (
        "unreachable or unimplemented ops: "
        f"{set(OPS) ^ (BROWSER_OPS | OS_OPS | CONTROL)}")
    for op in OS_OPS - {"open_app", "window", "media", "type_here"}:
        build(op, {})                                  # must not raise
    assert set(NEEDS) & BROWSER_OPS == {"click", "type", "goto", "web_search"}
    # every op with arguments must describe them, or narration says only the op name
    for op, keys in NEEDS.items():
        a = {k.split(".", 1)[1]: ("minimize" if op == "window" else
                                  "play_pause" if op == "media" else
                                  1 if k.endswith("target") else "x") for k in keys}
        assert describe(op, a) != op, f"describe() ignores {op} arguments"
    assert HARMLESS <= set(OPS), "HARMLESS names an op that does not exist"
    assert IRREVERSIBLE <= set(OPS)
    assert not (HARMLESS & IRREVERSIBLE), "an op cannot be both"
    assert not (HARMLESS & set(NEEDS)), "a no-argument op must take no arguments"
    assert TARGET_FLOOR < VALUE_FLOOR, "a click is cheaper to undo than a value"

    # two different apps must not look like a repeat of one action
    sig = lambda op, a: (op, tuple(sorted(a.items())))
    assert sig("open_app", {"app": "Obsidian"}) != sig("open_app", {"app": "Xcode"})
    assert sig("click", {"target": 1}) == sig("click", {"target": 1})
    print(f"selfcheck ok ({len(cases)} os mappings, {len(APPS)} apps discovered)")


def main():
    global MAX_STEPS
    p = argparse.ArgumentParser()
    p.add_argument("--goal", help="run one goal as text, no microphone")
    p.add_argument("--dry-run", action="store_true", help="decide, do not act")
    p.add_argument("--selfcheck", action="store_true")
    p.add_argument("--no-browser", action="store_true", help="OS actions only")
    p.add_argument("--quiet", action="store_true", help="no spoken narration")
    p.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = p.parse_args()

    if args.selfcheck:
        selfcheck()
        return
    MAX_STEPS = args.max_steps
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY is not set. Get one at https://console.typesafe.ai/keys")

    br = None
    if not args.no_browser:
        import browser as B
        br = B.Browser(str(PROFILE))
        print(f"chrome: {br.started} on port {B.CDP_PORT}")
    try:
        with TypeSafeClient(model="jev-latest") as client:
            if args.goal:
                def ask_tty(desc):
                    if not sys.stdin.isatty():
                        print(f"  would confirm: {desc}? (no tty, declining)")
                        return False
                    return input(f"  {desc}? [y/N] ").strip().lower().startswith("y")

                a = Agent(client, br, args.dry_run, True, not args.quiet, ask_tty)
                print(f"goal: {args.goal!r}")
                print("result:", a.run(args.goal))
            else:
                run_live(client, br, args.dry_run, True, not args.quiet)
    finally:
        if br:
            br.close()


if __name__ == "__main__":
    main()
