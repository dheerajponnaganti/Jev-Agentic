# How to add a new operation

You will accomplish: teaching the agent a new thing it can do, wired so that
Jev can choose it and the self-check proves it is reachable.

Worked example: `lock_screen`, which locks the Mac. Four edits in `agent.py`,
one of them optional, all in the same file.

## Prerequisites

- A shell command that performs the action, tested by hand first.
- Decide up front whether the operation takes arguments, and whether it is
  reversible. Those two answers drive every edit below.

## Steps

1. Prove the command works outside the agent. Do this first, so a later failure
   is unambiguously the wiring and not the command.

   ```bash
   osascript -e 'tell application "System Events" to keystroke "q" using {control down, command down}'
   ```

2. Add it to `OPS`. This dict is the Choice criteria Jev sees, so the
   description is a prompt, not a comment. Describe **when to choose it**, in
   the words a user would speak, and say what it is not for when a neighbouring
   operation could be confused with it.

   ```python
   OPS = {
       ...
       "lock_screen": "Lock the screen so the Mac needs a password to wake. "
                      "Not for quitting an application or closing a window.",
   }
   ```

3. Classify it. Every operation must land in exactly one of three sets, or the
   self-check fails.

   ```python
   OS_OPS = {"open_app", "quit_app", "window", "type_here", "media", "lock_screen"}
   ```

   `OS_OPS` lives inside `selfcheck()`. Also consider:

   - `HARMLESS`: free and reversible, runs regardless of confidence. Locking is
     not harmless (it interrupts you), so leave it out.
   - `IRREVERSIBLE`: requires a spoken yes even mid-loop. Locking is
     recoverable with a password, so this is a judgement call. Add it if you
     would rather be asked.
   - `BROWSER_OPS`: only for operations that act inside a page.

4. Implement it in `build()`, returning an argv **list**. Never a string, never
   `shell=True`. This function is the allowlist: an operation absent from it
   cannot execute, by construction.

   ```python
   def build(op, a):
       ...
       if op == "lock_screen":
           return ["osascript", "-e", 'tell application "System Events" to '
                                      'keystroke "q" using {control down, command down}']
   ```

5. If it takes arguments, add a question and register it. Skip this step for
   `lock_screen`, which takes none.

   Add a `Choice` to `step_questions()` keyed `<op>.<arg>`:

   ```python
   "lock_screen.delay": Choice(
       instructions="How long to wait before locking?",
       criteria={"now": "Immediately.", "one_minute": "After a minute."}),
   ```

   Register which answers the dispatcher reads:

   ```python
   NEEDS = {
       ...
       "lock_screen": ["lock_screen.delay"],
   }
   ```

   The key after the dot becomes the `args` field name. `target` is special: it
   is cast to `int` and gated by `TARGET_FLOOR`. Everything else is a string
   gated by `VALUE_FLOOR`.

6. Add a line to `describe()` if the narration would otherwise be unhelpful.
   Without one, the agent speaks the operation name with underscores replaced,
   so `lock_screen` narrates as "lock screen", which is fine. An operation with
   arguments usually needs a case, or it narrates the bare name and hides what
   it is about to do:

   ```python
   if op == "lock_screen":
       return "locking the screen"
   ```

7. Add it to the self-check. For an argument-less OS operation, one line in the
   `cases` list:

   ```python
   (("lock_screen", {}),
    ["osascript", "-e", 'tell application "System Events" to '
                        'keystroke "q" using {control down, command down}']),
   ```

8. Run the self-check.

   ```bash
   uv run agent.py --selfcheck
   ```

## Verification

The self-check proves the wiring. Two of its assertions exist specifically to
catch mistakes in this procedure:

```python
assert set(OPS) == BROWSER_OPS | OS_OPS | CONTROL
```

Forget step 3 and this names your operation as unreachable, which is how two
operations were once discovered to be implemented but impossible for Jev to
choose.

```python
for op, keys in NEEDS.items():
    assert describe(op, a) != op
```

Add arguments without a `describe()` case and this fails, because narration that
says only the operation name tells the user nothing about what is about to
happen.

Then confirm Jev actually picks it, without executing anything:

```bash
uv run --env-file .env agent.py --dry-run --max-steps 2 --goal "lock my screen"
```

You want the operation chosen with a healthy `op_conf`:

```
[1] op=lock_screen op_conf=0.97 done=0.02 tok=2910
  ~ locking the screen
```

Finally, run it for real.

## Adding a browser operation instead

Steps 2, 3, 5, 6 and 7 are the same. Instead of `build()`, add a method to
`Browser` in `browser.py` that **returns a human-readable outcome string**, then
a branch in `Agent._execute()`. The return value goes into `history`, so
`clicked [3] Submit` is useful to the next judgment and `True` is not.

Add the name to `BROWSER_OPS`, not `OS_OPS`. Operations there automatically
return `no browser attached` under `--no-browser`.

## Troubleshooting

**`unreachable or unimplemented ops: {'lock_screen'}`.** Step 3. The name is in
`OPS` but not in any classification set.

**`ValueError: unknown os op: 'lock_screen'`.** Step 4. Classified but never
implemented in `build()`.

**`KeyError: 'lock_screen.delay'`.** You added it to `NEEDS` without adding the
matching question in `step_questions()`. The two must agree.

**`describe() ignores lock_screen arguments`.** Step 6.

**Jev never chooses it.** The description in `OPS` is doing the work, so fix
the description, not the code. Say when to choose it in spoken language, and
add a `Not for ...` clause naming the operation it is being confused with. Check
which one is winning with `--dry-run`.

**It is chosen but confidence is low.** Its description overlaps a neighbour.
Sharpen both, not just the new one.

## Related

- [reference-cli.md](reference-cli.md) for the full operation table
- [explanation-design.md](explanation-design.md) for why `build()` returns argv lists
- [howto-tune.md](howto-tune.md) for the confidence floors your operation meets
