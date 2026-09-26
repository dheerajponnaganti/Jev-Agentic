# How to tune the agent when it misbehaves

You will accomplish: turning a vague "it did the wrong thing" into a specific
constant to change, using `log.jsonl` rather than guesswork.

The rule: **change one constant, from evidence in the log, and re-test the same
goal.** Every knob in this agent interacts with a real microphone in a real room
or with a model's probability distribution. Neither responds to intuition.

## Prerequisites

- At least one run recorded in `log.jsonl`.
- A goal you can repeat, to test against.
- An API key, since every test round costs about $0.00013 per step.

## Steps

1. Reproduce the problem cheaply, without speaking. Text mode skips the
   microphone entirely and isolates the decision layer.

   ```bash
   uv run --env-file .env agent.py --dry-run --goal "computer, <your goal>"
   ```

   `--dry-run` decides without acting, so you can iterate on a phrasing without
   opening fifteen tabs.

2. Read the last decision.

   ```bash
   tail -1 log.jsonl | python3 -m json.tool
   ```

3. Match the symptom to the knob.

   | Symptom | Look at | Change |
   |---|---|---|
   | Run ended `unsure` | which of `target_conf`, `value_conf`, `op_conf` is lowest | lower that floor |
   | Clicked the wrong control | `args.target`, and the element table in the run output | raise `TARGET_FLOOR` |
   | Searched for the wrong words | `args.query` | see step 5 |
   | Stopped early, goal unfinished | `done` on the last step | raise `DONE_AT` |
   | Kept going after finishing | `done` climbing but under 0.7 | lower `DONE_AT` |
   | `result: step_cap` | `history` repeating | raise `--max-steps`, or look for a loop |
   | `result: stuck` | `op` and `args` | usually correct behaviour; the page is not responding |
   | Never heard the wake word | nothing logged at all | see step 6 |
   | Triggered on background talk | `goal` full of unrelated words | raise `WAKE_RATIO` |

4. Change exactly one constant in `agent.py`, then re-run the same goal from
   step 1. The constants are module level, at the top of the file, and need no
   other edit. [reference-cli.md](reference-cli.md) lists all of them with
   defaults.

5. If the query or typed text is wrong, the fix is usually a trigger word, not
   a threshold. `spans()` builds candidates from every suffix of your goal plus
   every window after a trigger word. Check whether the right phrase was even
   offered:

   ```bash
   uv run python -c "
   import agent
   for s in agent.spans('<your goal without the wake word>'): print(s)"
   ```

   If the phrase you wanted is missing, add the word that should have introduced
   it to the `TRIGGER` regex. If it is present but Jev chose a different one,
   the `web_search.query` instructions need sharpening, not the regex.

   You can also force the exact words by quoting them out loud: `spans()` keeps
   anything in quotes as its own candidate.

6. If the wake word never fires, check what Whisper actually heard. The live
   output prints every transcript, including ones it ignored:

   ```
   heard: 'my computer is slow'  (not addressed to me)
   ```

   No line at all means the voice detector never closed an utterance. Lower
   `VAD_MULT` (quiet room or distant microphone) or raise it (noisy room
   triggering on nothing).

## Verification

Re-run the original goal and confirm the outcome changed in the direction you
wanted:

```bash
uv run --env-file .env agent.py --dry-run --goal "computer, <your goal>"
tail -1 log.jsonl | python3 -m json.tool
```

Then confirm you did not break the rest:

```bash
uv run agent.py --selfcheck
```

The self-check asserts `TARGET_FLOOR < VALUE_FLOOR`, so inverting the floors
fails loudly rather than quietly degrading.

## Reading the log in bulk

Ten minutes of real use tells you more than any single run.

Every failed run:

```bash
grep -E '"result": *"(refused|unsure|stuck|no_target|step_cap)"' log.jsonl | tail -20
```

Confidence distribution per operation, to see whether a floor is set sanely:

```bash
python3 -c "
import json, collections
d = collections.defaultdict(list)
for l in open('log.jsonl'):
    o = json.loads(l)
    if 'op' in o and 'conf' in o: d[o['op']].append(o['conf'])
for op, cs in sorted(d.items()):
    print(f'{op:<12} n={len(cs):<3} min={min(cs):.2f} median={sorted(cs)[len(cs)//2]:.2f}')"
```

If an operation's median sits just below its floor, the floor is wrong, not the
model.

## Troubleshooting

**Every change makes it worse.** You are probably tuning against one utterance.
Collect 20 before trusting a threshold.

**Lowering a floor made it act confidently wrong.** That is the floor doing its
job. Put it back and fix the instructions in `step_questions()` instead: a
Choice with unclear criteria produces a flat distribution, and no threshold
repairs that.

**`log.jsonl` is empty after a run.** Logging is best-effort and silent on
failure. Check the directory is writable. The agent will not tell you.

**It hears itself and answers its own question.** `SAY_DEADTIME` is too short
for your speakers. Raise it. Loud speakers and AirPods both do this.

**A word is consistently mistranscribed.** No amount of tuning fixes this: span
extraction can only pick words Whisper produced. Say it differently, or spell
it. See the trade-offs in [explanation-design.md](explanation-design.md).

## Related

- [reference-log.md](reference-log.md) for every field in `log.jsonl`
- [reference-cli.md](reference-cli.md) for all constants and defaults
- [explanation-design.md](explanation-design.md) for why the floors differ
- [howto-add-operation.md](howto-add-operation.md) if tuning is not enough
