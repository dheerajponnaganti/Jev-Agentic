# Jev Agentic

Always-on macOS voice agent. You speak a goal, it works until the goal is met.

```
computer, search google for cute puppy pics and open the first result
computer, go to youtube and search for lofi hip hop beats
computer, open android studio
computer, turn the volume down
```

## Quick start

```bash
git clone https://github.com/dheerajponnaganti/Jev-Agentic.git
cd Jev-Agentic
uv sync
cp .env.example .env        # then add your key from console.typesafe.ai/keys
uv run agent.py --selfcheck                                  # no key needed
uv run --env-file .env agent.py                              # always-on voice
```

Full walkthrough: [docs/tutorial-first-goal.md](docs/tutorial-first-goal.md).

## Documentation

New here? Start with the tutorial.

| Doc | For |
|---|---|
| [Tutorial: your first spoken goal](docs/tutorial-first-goal.md) | Never run it. Install to a working spoken goal in 6 steps. |
| [How to grant the macOS permissions](docs/howto-permissions.md) | Media, window or typing commands do nothing and give no error. |
| [How to tune it when it misbehaves](docs/howto-tune.md) | It misheard you, clicked the wrong thing, or stopped early. |
| [How to use your real Chrome profile](docs/howto-real-chrome.md) | A goal needs you signed in. |
| [How to add a new operation](docs/howto-add-operation.md) | Teaching it something it cannot do yet. |
| [Reference: command line](docs/reference-cli.md) | Every flag, operation, argument and tuning constant. |
| [Reference: log.jsonl and step state](docs/reference-log.md) | Every logged field, and what the model actually sees. |
| [Explanation: why it is built this way](docs/explanation-design.md) | The design, the trade-offs, and what was rejected. |

## How it decides

TypeSafe Jev returns typed judgments only: Choice, Noul, Score, never generated
text. "The model returns a probability distribution over your options or levels,
never a value outside them."

So this code supplies every possible answer and Jev picks one:

- **Which action** is a Choice over 17 operations.
- **Which element** is a Choice whose options are the numbered controls actually
  on the page. Jev cannot name an element that is not there.
- **Which string** (a search query, a URL, text to type) is a Choice over
  candidate spans of your spoken goal, TypeSafe's documented value-extraction
  recipe. The answer is one of those spans copied verbatim, so it cannot
  hallucinate a query.
- **Whether it is finished** is a Noul over the page text and the history.

One request per step, all questions batched, because independent questions are
evaluated in parallel. About 3,000 input tokens per step, roughly $0.00013.
A ten-step goal costs about a tenth of a cent.

The loop keeps the goal and its history in code and asks for one bounded step at
a time, which is the "respond to changing state" pattern from the docs and the
same shape as `browser-use/jev-ultrafast`.

## Run

```bash
uv run agent.py --selfcheck                       # no mic, no network, no key
uv run --env-file .env agent.py --dry-run --goal "open obsidian"
uv run --env-file .env agent.py --goal "go to youtube and search for lofi beats"
uv run --env-file .env agent.py                   # always-on mic
```

Run live mode in **your own terminal**, not through another tool: macOS
attributes Microphone and Accessibility permission to the process that launched
it.

Flags: `--dry-run` decides without acting, `--no-browser` for OS actions only,
`--quiet` disables spoken narration, `--max-steps N` changes the cap.

## Chrome

Browser control needs a debugging port, so the agent starts its own Chrome
against a dedicated profile in `.chrome-profile/`. That keeps it out of your
logged-in sessions and stops it fighting the Chrome you already have open. It is
a real Chrome window, not a bundled Chromium.

To drive your normal profile instead, quit Chrome and relaunch it yourself with
`--remote-debugging-port=9222`; the agent attaches to an existing port when it
finds one. Understand that this gives the agent your sessions.

## Permissions

| Needs | Granted to |
|---|---|
| Microphone | the terminal app you run this from (prompts on first use) |
| Accessibility | the same terminal app, for `cliclick` and System Events keystrokes |

Accessibility does not prompt reliably from a CLI. Add your terminal under
System Settings → Privacy & Security → Accessibility, then **fully quit and
reopen it**: TCC is cached at process start. Verify with
`cliclick -m test kp:play-pause`, which must not warn.

## Guardrails

- **Step cap** of 15 per goal, then it stops and says so.
- **Spoken kill word.** Say "stop" or "cancel" between steps.
- **Stuck detector.** The same action with the same arguments three times ends
  the run, which is why the signature covers every argument: two different
  `open_app` calls must not look like one repeated action.
- **Confidence floors, split by consequence.** A wrong click costs one `back`,
  and several page controls are often equally acceptable, which spreads
  probability without being an error, so element targets need only 0.30. A query,
  URL, app name or typed string needs 0.55, because typing the wrong thing is
  what you actually notice. Ops with no arguments, like scrolling, run
  regardless: being torn between scroll and click is ambiguity the next step
  corrects.
- **Irreversible actions confirm out loud.** Quitting an app asks first, mid-loop.
- **Typing is refused, not confirmed, on login, payment and banking pages,**
  detected from the parsed hostname, the path, and any password field on the
  page. A policy refusal ends the run rather than retrying, since it would
  refuse identically next step.
- **No arbitrary shell.** `build()` is the allowlist for anything leaving the
  browser: it returns an argv list for one of five literal ops and raises
  otherwise. `shell=True` appears nowhere. Spoken text reaches the OS only
  `quote_plus`'d into a query, through `safe_url`'s http(s) scheme check, or as
  one argv element after `t:`.
- **Every step is logged** to `log.jsonl` with its state, answers and
  confidences, for diagnosing a bad run afterwards.

## Files

| File | Job |
|---|---|
| `agent.py` | mic, VAD, wake word, spans, the Jev questions, the step loop, dispatch, `--selfcheck` |
| `browser.py` | Chrome over CDP, the indexed element snapshot, the page actions |
| `log.jsonl` | one line per step |

## Tuning

Knobs are module-level constants at the top of `agent.py`: `VAD_MULT`,
`WAKE_RATIO`, `MAX_STEPS`, `DONE_AT`, and the three confidence floors. Tune them
from `log.jsonl`, not from intuition.

## Known limits

- One goal at a time. Saying a new goal mid-run does not queue it.
- No dropdown (`<select>`) support. Clicking usually opens them anyway; add it
  when a real page needs it.
- No shadow DOM, iframes, canvas or file uploads. The snapshot reads the main
  document only.
- Whisper mistranscriptions are unfixable downstream: if it hears "kate's" for
  "k8s", no span strategy recovers the word.
- `done` is Jev's judgment from visible page text. For a goal whose completion
  is not visible, it can stop early or run to the cap.

## License

Copyright 2026 Dheeraj Ponnaganti

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
