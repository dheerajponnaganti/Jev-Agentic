# Reference: command line

Complete surface of `agent.py`. Everything here is read from the source; the
authority is `main()` in `agent.py`.

## Invocation

```bash
uv run --env-file .env agent.py [flags]
```

`--env-file .env` loads `TYPESAFE_API_KEY`. Without a key the process exits with
`TYPESAFE_API_KEY is not set. Get one at https://console.typesafe.ai/keys`.
`--selfcheck` is the one mode that needs no key.

## Flags

| Flag | Type | Default | Effect |
|---|---|---|---|
| `--goal TEXT` | string | unset | Run one goal from text and exit. No microphone, no wake word. Omit to enter always-on voice mode. |
| `--dry-run` | switch | off | Ask Jev for each step and print the decision, but execute nothing. Costs API tokens. |
| `--selfcheck` | switch | off | Run the assertion suite and exit. No microphone, no network, no API key. |
| `--no-browser` | switch | off | Skip the Chrome connection. Browser operations return `no browser attached`; OS operations still work. |
| `--quiet` | switch | off | Disable spoken narration. Steps still print to stdout. |
| `--max-steps N` | int | `15` | Hard cap on steps per goal. Overwrites `MAX_STEPS`. |

Flags combine. `--goal X --dry-run` is the normal way to test a phrasing.

## Exit behaviour

`--selfcheck` exits non-zero on the first failed assertion, printing the
assertion message. Everything else exits 0 unless the API key is missing.
`Agent.run()` never raises into the caller: live mode catches per-goal
exceptions, logs them, and keeps listening.

## Outcomes

`Agent.run(goal)` returns one of these strings. Text mode prints it as
`result: <outcome>`.

| Outcome | Meaning |
|---|---|
| `done` | Jev chose `done`, or the done judgment reached `DONE_AT`. |
| `blocked` | Jev chose `blocked`, or the chosen operation had no usable target on screen. |
| `unsure` | A confidence floor was not met. Nothing was executed. |
| `stuck` | The same operation with the same arguments came back `STUCK_REPEATS` times. |
| `declined` | An irreversible operation was not confirmed out loud. |
| `refused` | Policy refusal, currently only typing on a login or payment page. |
| `stopped` | The kill word was heard between steps. |
| `step_cap` | Reached `--max-steps` without finishing. |
| `error` | The TypeSafe request failed. Logged with the exception. |

## Voice mode

With no `--goal`, the agent warms Whisper, opens the microphone, and waits.

- Address it with the wake word: `computer, <goal>`. One leading filler is
  allowed, so `hey computer` and `okay computer` also work.
- The wake word is matched fuzzily, at `difflib` ratio `WAKE_RATIO` (0.75), so
  `commuter` and `computa` still wake it. The word must be the first or second
  token, so "my computer is slow" does not.
- Say `computer` alone and it answers "Yes?" and takes your next utterance as
  the goal.
- Say `stop`, `cancel`, `abort`, `nevermind`, `quit it` or `that's enough`
  between steps to end the run.
- `Ctrl-C` exits.

## Operations

17 operations, the `OPS` dict in `agent.py`. Jev chooses exactly one per step:
9 browser, 5 operating system, 3 control.

### Browser (`BROWSER_OPS`)

Need a Chrome connection. With `--no-browser` they return `no browser attached`.

| Operation | Arguments | Effect |
|---|---|---|
| `click` | `target` | Click a numbered control. |
| `type` | `target`, `text` | Select-all then type into a numbered field. |
| `press_enter` | none | Press Return to submit. |
| `scroll_down` | none | Scroll 600 px down. |
| `scroll_up` | none | Scroll 600 px up. |
| `goto` | `url` | Navigate to an address, through `safe_url()`. |
| `web_search` | `query` | Navigate to `google.com/search?q=<query>`. |
| `new_tab` | none | Open an empty tab. |
| `back` | none | Browser back. |

### Operating system

Execute through `build()`, which returns an argv list.

| Operation | Arguments | Runs |
|---|---|---|
| `open_app` | `app` | `open -a <app>`, which launches and focuses. |
| `quit_app` | none | `osascript` Cmd-Q on the front app. Always confirms. |
| `window` | `action` | `osascript` keystroke: `minimize`, `fullscreen`, `new_window`. |
| `type_here` | `text` | `cliclick -w 40 t:<text>` into the focused field. |
| `media` | `action` | `cliclick kp:<key>`, a real system media key. |

### Control (`CONTROL`)

Resolved by the loop, never executed.

| Operation | Effect |
|---|---|
| `wait` | Sleep 1.2 s, record "waited for the page", continue. |
| `done` | End the run as `done`. |
| `blocked` | End the run as `blocked`. |

## Operation arguments

`media.action`: `play_pause`, `next`, `previous`, `volume_up`, `volume_down`,
`mute`.

`window.action`: `minimize`, `fullscreen`, `new_window`. Quitting is the
separate `quit_app` operation so it can carry its own confirmation.

`target` is an integer index into the current page snapshot. `text`, `query` and
`url` are spans of your spoken goal. `app` is one of the discovered
applications.

Every argument question also offers `__none__`. When Jev picks it, the run ends
as `blocked` with `I could not find what I needed on screen`.

## Tuning constants

Module level in `agent.py`. Editing them needs no other change.

### Audio

| Constant | Default | Meaning |
|---|---|---|
| `SR` | `16000` | Sample rate, Hz. Whisper expects 16 kHz. |
| `BLOCK` | `1600` | Frames per callback, 100 ms. |
| `VAD_MULT` | `4.0` | Speech threshold as a multiple of the adaptive noise floor. |
| `VAD_FLOOR` | `0.010` | Absolute minimum RMS threshold. |
| `HANGOVER` | `7` | Silent blocks that close an utterance, 700 ms. |
| `MAX_BLOCKS` | `120` | Hard cap per utterance, 12 s. |
| `MIN_SEC` | `0.4` | Utterances shorter than this are discarded. |
| `SAY_DEADTIME` | `0.25` | Seconds the microphone stays gated after speaking. |

### Wake word

| Constant | Default | Meaning |
|---|---|---|
| `WAKE` | `"computer"` | The wake word. |
| `WAKE_RATIO` | `0.75` | Minimum `difflib` similarity to accept it. |

### The loop

| Constant | Default | Meaning |
|---|---|---|
| `MAX_STEPS` | `15` | Steps per goal. |
| `DONE_AT` | `0.7` | Done judgment at or above this ends the run. |
| `STUCK_REPEATS` | `3` | Identical actions that trigger `stuck`. |
| `TARGET_FLOOR` | `0.30` | Minimum confidence for an element index. |
| `VALUE_FLOOR` | `0.55` | Minimum confidence for a query, URL, app name or typed text. |
| `OP_FLOOR` | `0.25` | Minimum confidence for the operation choice. |

`TARGET_FLOOR < VALUE_FLOOR` is asserted by `--selfcheck`. See
[explanation-design.md](explanation-design.md) for why they differ.

### Model

| Constant | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | Whisper weights, fetched on first run. |

The TypeSafe model is `jev-latest`, set in `main()` via `TypeSafeClient(model="jev-latest")`.

## Paths

| Path | Contents |
|---|---|
| `.env` | `TYPESAFE_API_KEY=...`. Gitignored, mode 600. |
| `log.jsonl` | One JSON object per step. Gitignored. |
| `.chrome-profile/` | The dedicated Chrome profile. Gitignored. |

## Related

- [tutorial-first-goal.md](tutorial-first-goal.md) to run it the first time
- [reference-log.md](reference-log.md) for the log format
- [explanation-design.md](explanation-design.md) for why the loop is shaped this way
- [howto-permissions.md](howto-permissions.md) if operations silently do nothing
