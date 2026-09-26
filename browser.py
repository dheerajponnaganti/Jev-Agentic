# Copyright 2026 Dheeraj Ponnaganti
# Licensed under the Apache License, Version 2.0.
# See the LICENSE file in the project root for the full text.

"""Chrome control over CDP, and the indexed element table Jev chooses from.

Connects to the user's real Chrome (so actions physically happen in their
browser), not to a bundled Chromium. Chrome must be running with a remote
debugging port; `ensure_chrome()` starts one if there is none.

The snapshot is a numbered table of operable controls. Jev picks an index, never
a selector, so it cannot name an element that does not exist.
"""

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Typing is refused outright on these, rather than confirmed. A voice agent has
# no business in a password or payment field.
SECRET_HOST = re.compile(
    r"(?:^|\.)(?:"
    r"bank|banking|chase|revolut|wise|paypal|stripe|coinbase|binance"
    r"|accounts\.google|login\.microsoft|appleid\.apple|id\.apple"
    r")", re.I)
SECRET_PATH = re.compile(r"login|signin|sign-in|password|passwd|checkout|payment"
                         r"|billing|card|auth|oauth|2fa|otp|verify", re.I)

# JS runs in the page and returns the operable-control table. Controls only:
# anything Jev cannot act on is noise that costs tokens and invites wrong picks.
SNAPSHOT_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) return null;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity < 0.05) return null;
    return r;
  };
  const label = (el) => {
    let t = el.getAttribute('aria-label') || el.getAttribute('placeholder')
         || el.getAttribute('title') || el.getAttribute('alt') || '';
    if (!t && el.labels && el.labels[0]) t = el.labels[0].innerText || '';
    if (!t) t = (el.innerText || el.value || '').trim();
    return t.replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  const SEL = 'a[href],button,input,textarea,select,summary,[role=button],' +
              '[role=link],[role=textbox],[role=combobox],[role=checkbox],' +
              '[role=radio],[role=tab],[role=menuitem],[role=option],[contenteditable=true]';
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (el.disabled || el.getAttribute('aria-hidden') === 'true') continue;
    const r = vis(el);
    if (!r) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    const typeable = tag === 'textarea' || el.isContentEditable ||
      (tag === 'input' && !['checkbox','radio','submit','button','reset','file','range','color'].includes(type));
    const text = label(el);
    if (!text && !typeable && tag !== 'select') continue;   // unlabelled, unusable
    out.push({
      i: out.length,
      tag, role: el.getAttribute('role') || '', type,
      text, typeable,
      secret: type === 'password',
      selectable: tag === 'select',
      options: tag === 'select'
        ? [...el.options].slice(0, 25).map(o => o.text.trim().slice(0, 60)) : undefined,
      value: typeable ? String(el.value || el.innerText || '').slice(0, 80) : undefined,
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
    });
    if (out.length >= 120) break;                            // token budget
  }
  return {
    url: location.href,
    title: document.title.slice(0, 200),
    scroll: Math.round(scrollY), height: Math.round(document.body.scrollHeight),
    text: (document.body.innerText || '').replace(/\s+\n/g, '\n').slice(0, 3000),
    elements: out,
  };
}
"""


def is_secret(url, elements=()):
    """True for pages a voice agent must not type into. The host pattern is
    matched against the parsed hostname, not the whole URL, or "//accounts."
    would never match an anchor expecting start-of-string or a dot."""
    host = urllib.parse.urlsplit(url).hostname or ""
    rest = url[len(urllib.parse.urlsplit(url).scheme) + 3 + len(host):]
    return bool(SECRET_HOST.search(host) or SECRET_PATH.search(rest)
                or any(e.get("secret") for e in elements))


def _http(path, timeout=2):
    with urllib.request.urlopen(CDP_URL + path, timeout=timeout) as r:
        return json.load(r)


def port_live():
    try:
        _http("/json/version")
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def ensure_chrome(profile, wait=15):
    """Start Chrome with a debugging port if none is listening.

    Uses a dedicated profile directory by default so it never fights the Chrome
    you already have open, and so the agent cannot act inside your logged-in
    sessions. Pass your real profile only if you quit Chrome first.
    """
    if port_live():
        return "existing"
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--restore-last-session=false"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait * 4):
        if port_live():
            return "launched"
        time.sleep(0.25)
    raise RuntimeError(f"Chrome did not open a debugging port on {CDP_PORT}")


class Browser:
    """Thin wrapper over Playwright's CDP connection. One active page."""

    def __init__(self, profile):
        from playwright.sync_api import sync_playwright
        self.started = ensure_chrome(profile)
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.connect_over_cdp(CDP_URL)
        ctx = self._b.contexts[0] if self._b.contexts else self._b.new_context()
        self.ctx = ctx
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()

    def close(self):
        try:
            self._pw.stop()                  # leaves Chrome itself running
        except Exception:
            pass

    # --- observing -------------------------------------------------------

    def _active(self):
        """Whichever page is frontmost. Chrome can change it under us."""
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if pages and self.page not in pages:
            self.page = pages[-1]
        return self.page

    def snapshot(self, settle=0.0):
        if settle:
            time.sleep(settle)
        p = self._active()
        try:
            p.wait_for_load_state("domcontentloaded", timeout=4000)
        except Exception:
            pass                             # a slow page is still worth reading
        snap = p.evaluate(SNAPSHOT_JS)
        snap["secret_page"] = is_secret(snap["url"], snap["elements"])
        snap["tabs"] = [t.title()[:60] for t in self.ctx.pages if not t.is_closed()][:10]
        return snap

    def element_table(self, snap):
        """One line per control, the exact text Jev sees."""
        rows = []
        for e in snap["elements"]:
            kind = ("select" if e["selectable"]
                    else "input" if e["typeable"]
                    else e["role"] or e["tag"])
            row = f'[{e["i"]}] {kind}: {e["text"] or "(no label)"}'
            if e.get("value"):
                row += f' (contains "{e["value"]}")'
            if e.get("options"):
                row += f' options: {", ".join(e["options"])}'
            rows.append(row)
        return "\n".join(rows)

    # --- acting ----------------------------------------------------------

    def _handle(self, idx):
        """Re-resolve the index against a fresh query, then verify the label
        still matches what we were told. A stale index clicks the wrong thing."""
        p = self._active()
        snap = p.evaluate(SNAPSHOT_JS)
        if idx >= len(snap["elements"]):
            raise IndexError(f"element {idx} is gone (page has {len(snap['elements'])})")
        return p, snap["elements"][idx]

    def click(self, idx):
        p, e = self._handle(idx)
        p.mouse.click(e["x"], e["y"])
        return f'clicked [{idx}] {e["text"][:60]}'

    def type_into(self, idx, text):
        p, e = self._handle(idx)
        if e["secret"]:
            raise PermissionError("refusing to type into a password field")
        p.mouse.click(e["x"], e["y"])
        p.keyboard.press("Meta+a")
        p.keyboard.type(text, delay=12)
        return f'typed {text!r} into [{idx}] {e["text"][:40]}'

    def press(self, key):
        self._active().keyboard.press(key)
        return f"pressed {key}"

    def goto(self, url):
        self._active().goto(url, wait_until="domcontentloaded", timeout=20000)
        return f"opened {url}"

    def scroll(self, dy):
        self._active().mouse.wheel(0, dy)
        return f"scrolled {'down' if dy > 0 else 'up'}"

    def new_tab(self, url=None):
        self.page = self.ctx.new_page()
        if url:
            self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return f"new tab {url or ''}".strip()

    def back(self):
        self._active().go_back(wait_until="domcontentloaded", timeout=15000)
        return "went back"
