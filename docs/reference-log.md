# Reference: log.jsonl and the step state

Two data shapes worth knowing: what the agent writes about each step, and what
it sends to Jev.

## log.jsonl

One JSON object per line, appended by `log()` in `agent.py`. Writes are
best-effort: a failure to log never interrupts a run. Gitignored, so it is
local to your machine.

Fields vary by outcome. Nothing is guaranteed except that the object is valid
JSON on one line.

### An executed step

```json
{"goal": "go to youtube and search for lofi hip hop beats",
 "step": 2, "op": "type",
 "args": {"target": 2, "text": "lofi hip hop beats"},
 "conf": 0.70, "done": 0.01,
 "url": "https://youtube.com/", "dry_run": false}
```

| Field | Type | Meaning |
|---|---|---|
| `goal` | string | The goal, wake word stripped. |
| `step` | int | 1-based step number. |
| `op` | string | The chosen operation. |
| `args` | object | Arguments the dispatcher read. Speculative answers are absent. |
| `conf` | float | `min(op_conf, target_conf, value_conf)`. |
| `done` | float | The done judgment, 0 to 1. |
| `url` | string | Page URL at the time of the decision. Absent with `--no-browser`. |
| `dry_run` | bool | True when nothing was executed. |

### A refused step

```json
{"goal": "go to youtube and search for lofi hip hop beats",
 "step": 1, "op": "click", "args": {"target": 6},
 "conf": 0.45, "target_conf": 0.46, "value_conf": 1.0,
 "op_conf": 0.45, "result": "refused"}
```

`target_conf`, `value_conf` and `op_conf` appear **only** on refusals, which is
the point: when a run stops as `unsure`, the log says which of the three floors
was missed. `value_conf` is `1.0` when the operation takes no value argument.

### A terminal line

Every run ends with one object carrying `result`.

| `result` | Extra fields | Written when |
|---|---|---|
| `done` | `done` | Goal achieved. |
| `blocked` | | Jev chose `blocked`. |
| `no_target` | `conf` | An argument came back `__none__`. |
| `refused` | `target_conf`, `value_conf`, `op_conf` | A confidence floor was missed. |
| `refused_by_policy` | `detail` | Typing on a login or payment page. |
| `declined` | | An irreversible operation was not confirmed. |
| `stuck` | `op`, `args` | Identical action repeated. |
| `user_stopped` | | Kill word heard. |
| `step_cap` | | Ran out of steps. |
| (none) | `error` | The TypeSafe call raised. `error` holds the `repr`. |

### Reading it

Every step of every goal, newest last:

```bash
python3 -c "
import json
for l in open('log.jsonl'):
    d = json.loads(l)
    print(f\"{d.get('step','-'):>3} {d.get('op','-'):<12} {d.get('conf',''):<6} {d.get('result','')}\")"
```

Only the runs that failed:

```bash
grep -E '"result": *"(refused|unsure|stuck|no_target|step_cap)"' log.jsonl | tail -20
```

## The state sent to Jev

Built by `Agent._state()`. This is the `state` argument of `system_one`, and it
is the only thing Jev sees, so a fact absent here cannot inform a decision.

| Key | Always | Meaning |
|---|---|---|
| `goal` | yes | The goal, verbatim after the wake word. |
| `step` | yes | Current step number. |
| `max_steps` | yes | The cap, so Jev knows its budget. |
| `history` | yes | Last 6 outcome strings, or `["nothing yet"]`. |
| `frontmost_app` | yes | From `osascript`, or `unknown` if it fails. |
| `page_url` | browser only | Current URL. |
| `page_title` | browser only | Document title, 200 chars. |
| `page_text` | browser only | Visible `innerText`, 3000 chars. |
| `open_tabs` | browser only | Up to 10 tab titles, 60 chars each. |
| `scrolled` | browser only | `"<scrollY> of <scrollHeight> px"`. |
| `page_elements` | yes | The element table, or `"(browser not attached)"`. |

`history` holds what actually happened, not what was intended: the return value
of each `Browser` method, or `failed: <error>`. That is what lets Jev route
around a failure rather than repeating it.

## The element table

`Browser.element_table()` renders one line per control. Jev chooses the number.

```
[0] link: Gmail
[2] input: Search (contains "lofi hip hop beats")
[7] button: Search by voice
[14] link: Images
[32] link: Cute little puppy.🐶 Instagram
```

Format: `[index] kind: label`, then `(contains "value")` for a field that
already holds text.

`kind` is `select` for a `<select>`, `input` for anything typeable, otherwise
the ARIA role if present, otherwise the tag name.

### What gets in

`SNAPSHOT_JS` in `browser.py` collects `a[href]`, `button`, `input`,
`textarea`, `select`, `summary`, `[contenteditable=true]`, and the ARIA roles
`button`, `link`, `textbox`, `combobox`, `checkbox`, `radio`, `tab`,
`menuitem`, `option`.

Excluded: anything disabled, `aria-hidden="true"`, `type="hidden"`, smaller
than 2x2 px, outside the viewport, `visibility: hidden`, `display: none`,
opacity below 0.05, and any unlabelled element that is not typeable and not a
`<select>`.

Capped at **120 elements**, a token budget, not a limit of the Choice
primitive, which allows 255 options.

Labels resolve in order: `aria-label`, `placeholder`, `title`, `alt`, the
associated `<label>`, then `innerText` or `value`. Collapsed whitespace,
truncated at 120 characters.

### Indices are per snapshot

`i` is the position in the current snapshot, not a stable identity. The page
changes, the numbering changes. Every action re-runs the snapshot and
re-resolves the index (`Browser._handle()`), then raises
`element N is gone (page has M)` if the table shrank. The failure is recorded
in `history` and Jev picks again.

## Related

- [reference-cli.md](reference-cli.md) for flags, operations and constants
- [howto-tune.md](howto-tune.md) to turn these numbers into fixes
- [explanation-design.md](explanation-design.md) for why elements are numbered
