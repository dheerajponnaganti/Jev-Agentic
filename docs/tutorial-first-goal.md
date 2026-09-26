# Tutorial: your first spoken goal

You will get a voice agent running on your Mac and watch it carry out a goal you
speak out loud: Chrome opens, a search runs, and it clicks through to a result,
while you watch. By the end you will understand the four-step loop it runs and
be able to read its reasoning.

Budget about 15 minutes, most of it a one-time model download.

## What you'll need

- A Mac with Apple Silicon. Transcription runs on the GPU through MLX.
- macOS 13 or later. Written and tested on macOS 27.
- [uv](https://docs.astral.sh/uv/), the Python package manager: `brew install uv`.
- `cliclick`, for media and window keys: `brew install cliclick`.
- Google Chrome in `/Applications`.
- A TypeSafe API key from [console.typesafe.ai/keys](https://console.typesafe.ai/keys).
  Running a goal costs about $0.00013 a step, so this tutorial costs well under
  a cent.
- A working microphone.

## Step 1: Clone and install

```bash
git clone https://github.com/dheerajponnaganti/Jev-Agentic.git
cd Jev-Agentic
uv sync
```

`uv` reads `pyproject.toml`, pins Python 3.12 (Whisper's `numba` dependency does
not yet build on 3.14), and installs five packages into `.venv/`.

```
Installed 47 packages in 1.2s
```

## Step 2: Prove the logic works

Before any microphone or network, run the self-check.

```bash
uv run agent.py --selfcheck
```

```
selfcheck ok (6 os mappings, 102 apps discovered)
```

That is your first real result, and it tells you three things. Every operation
Jev can choose has a working implementation. Every URL the agent would refuse is
refused, including `file:///etc/passwd` and `javascript:alert(1)`. And it found
your applications by scanning `/Applications` at startup, which is why it knows
about Android Studio without anyone typing "Android Studio" into a list.

This mode needs no API key. Run it any time you change the code.

## Step 3: Watch it do something

Put your key in `.env`:

```bash
echo 'TYPESAFE_API_KEY=your-key-here' > .env && chmod 600 .env
```

Now give it a goal as text. No microphone yet, so if something breaks you know
it is not the audio.

```bash
uv run --env-file .env agent.py --goal "go to youtube and search for lofi hip hop beats"
```

Chrome opens and you watch it work:

```
chrome: launched on port 9222
goal: 'go to youtube and search for lofi hip hop beats'
[1] op=goto op_conf=0.78 done=0.01 tok=2877
  ~ opening youtube
  -> opened https://youtube.com
[2] op=type op_conf=0.70 done=0.01 tok=3024
  ~ typing lofi hip hop beats
  -> typed 'lofi hip hop beats' into [2] Search
[3] op=press_enter op_conf=0.80 done=0.08 tok=4409
  ~ pressed Enter
[4] op=done op_conf=0.68 done=0.85 tok=3201
  ~ Done.
result: done
```

That is the whole agent. Read one line:

```
[2] op=type op_conf=0.70 done=0.01 tok=3024
```

Step 2. It chose `type` with 0.70 confidence. Its judgment that the goal was
already finished sat at 0.01, so it kept going. The decision cost 3,024 input
tokens, about $0.00013.

Four things happen per step: it looks at the screen, asks Jev once for the next
action, does it, and records what happened. `done=` is Jev answering "is this
finished?" on every step. It climbed 0.01 → 0.01 → 0.08 → 0.85, and at 0.85 the
loop stopped.

The Chrome that opened is **not** your usual Chrome. It runs a separate profile
in `.chrome-profile/`, logged out, so the agent cannot act inside your accounts.
[howto-real-chrome.md](howto-real-chrome.md) covers changing that, and why you
might not want to.

## Step 4: Grant the permissions

Browser goals worked because `open` and CDP need no permission. Media, window
and typing keys do, and macOS denies them **silently**.

```bash
cliclick -m test kp:play-pause
```

Clean output means you already have it:

```
Running in test mode. These command(s) would be executed:
Press + release play-pause key
```

A warning about `Accessibility privileges not enabled` means work to do: add
your terminal under System Settings → Privacy & Security → Accessibility, then
**fully quit and reopen it**, because macOS caches the answer at process start.
[howto-permissions.md](howto-permissions.md) has the details and the traps.

## Step 5: Speak to it

```bash
uv run --env-file .env agent.py
```

```
warming whisper...
chrome: existing on port 9222
listening. say "computer, <what you want>"   (ctrl-c to stop)
```

First run downloads the Whisper model, about 1.6 GB, once. macOS prompts for the
microphone; accept it.

Now say, out loud:

> **computer, search google for cute puppy pics and open the first result**

It prints what it heard, then works, narrating each step:

```
heard: 'Computer, search google for cute puppy pics and open the first result.'
[1] op=web_search op_conf=0.90 done=0.01 tok=2951
  ~ searching for cute puppy pics
  -> opened https://www.google.com/search?q=cute+puppy+pics
[2] op=click op_conf=0.89 done=0.10 tok=5079
  ~ clicking Cute little puppy.🐶 Instagram
  -> clicked [32] Cute little puppy.🐶 Instagram
[3] op=done op_conf=0.51 done=0.26 tok=5089
  ~ Done.
```

Two more things to try:

Say **computer** on its own. It answers "Yes?" and treats your next sentence as
the goal, so you do not have to fit everything into one breath.

Start a goal, then say **stop** while it is working. It checks between steps and
ends the run.

## Step 6: Read its mind

Every step is recorded.

```bash
tail -3 log.jsonl | python3 -m json.tool
```

```json
{
    "goal": "search google for cute puppy pics and open the first result",
    "step": 2,
    "op": "click",
    "args": {"target": 32, "label": "Cute little puppy.🐶 Instagram"},
    "conf": 0.89,
    "done": 0.1,
    "url": "https://www.google.com/search?q=cute+puppy+pics",
    "dry_run": false
}
```

This is how you fix it when it misbehaves. Not by guessing: the log names which
confidence was too low and which element it picked.
[howto-tune.md](howto-tune.md) maps symptoms to the constant to change.

## What you built

A voice agent that takes a spoken goal and works until it is met, using a model
that **cannot write a single word**.

That constraint is the interesting part. Jev returns typed judgments only, never
text, so it could never produce the string `"cute puppy pics"`. Instead the code
builds every plausible answer and Jev points at one: page controls become
numbered options, and the search query is a substring of your own sentence. The
consequence is a guarantee rather than a hope. It cannot search for something
you did not say, and it cannot click a button that is not on the page.

You have also seen the safety rails, which is why the agent stops rather than
flailing: a 15-step cap, a spoken kill word, loop detection, confidence floors
set by what a mistake costs, spoken confirmation before quitting an app, and a
flat refusal to type on login or payment pages.

Where to go next:

- [howto-tune.md](howto-tune.md) when it mishears or picks wrong
- [reference-cli.md](reference-cli.md) for all 17 operations, 6 flags and every constant
- [explanation-design.md](explanation-design.md) for why a model that cannot write turned out to be the safer choice
- [howto-add-operation.md](howto-add-operation.md) to teach it something new
