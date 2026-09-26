# How to grant the macOS permissions

You will accomplish: media, window and typing operations actually doing
something instead of silently failing, and the microphone capturing audio.

This is the most likely thing to block a working first run, because macOS
denies one of these permissions **without any error message**. The agent prints
`$ cliclick kp:play-pause`, reports success, and nothing happens.

## Prerequisites

- macOS 13 or later (written against macOS 27).
- `cliclick` installed: `brew install cliclick`.
- The terminal application you intend to run the agent from. Which one matters:
  macOS attributes permission to the *responsible process*, which is the
  terminal, not Python.

## Steps

1. Check whether Accessibility is already granted.

   ```bash
   cliclick -m test kp:play-pause
   ```

   Granted looks like this:

   ```
   Running in test mode. These command(s) would be executed:
   Press + release play-pause key
   ```

   Not granted prints a warning containing `Accessibility privileges not
   enabled`. If you got the clean output, skip to step 5.

2. Open Accessibility settings.

   ```bash
   open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
   ```

3. Add your terminal. Click `+`, pick your terminal application from
   `/Applications` (Terminal, iTerm, Ghostty, Warp, or Visual Studio Code if you
   run it from the built-in terminal), and make sure its toggle is **on**.

4. Fully quit and reopen that terminal. Cmd-Q, not just closing the window.
   TCC caches permissions at process start, so a running terminal keeps its old
   answer no matter what the settings pane says. This step is skipped more often
   than any other and is why "I already granted it" usually means "it is still
   denied".

5. Confirm Accessibility now works, for real this time.

   ```bash
   cliclick kp:play-pause
   ```

   Play something in a browser tab first. The audio should pause.

6. Trigger the Microphone prompt by starting the agent.

   ```bash
   cd Jev-Agentic
   uv run --env-file .env agent.py
   ```

   macOS shows a microphone prompt on first audio capture. Accept it. Unlike
   Accessibility, this one does prompt reliably.

## Verification

Run a goal that needs Accessibility and watch for the physical result:

```bash
uv run --env-file .env agent.py --goal "turn the volume down"
```

The volume HUD should appear on screen. If the command prints
`$ cliclick kp:volume-down` and nothing happens, Accessibility is still denied:
return to step 3, and check you granted it to the terminal you are actually
running in.

## What needs what

| Action | Permission | Granted to |
|---|---|---|
| Microphone capture | Microphone | your terminal application |
| `cliclick`, System Events keystrokes | Accessibility | your terminal application |
| `open -a`, `say`, Whisper transcription | none | |
| Chrome over CDP | none | |
| `tell application "<name>"` | Automation, per app | avoided by design |

Automation never appears because no operation uses `tell application` against a
named app. See [explanation-design.md](explanation-design.md) for why that was
worth the constraint.

## Troubleshooting

**Granted it, still denied.** You did not fully quit the terminal. Cmd-Q and
reopen. Verify with step 1.

**Granted to the wrong app.** If you launch from Visual Studio Code's
integrated terminal, VS Code needs the permission, not Terminal. Whichever
application owns the window you type in is the one to add.

**The entry is present but the toggle is off.** Adding an app does not enable
it. Check the toggle.

**Stale entry after an upgrade.** Remove your terminal from the list with `-`,
re-add it, quit and reopen.

**Microphone never prompts.** Check the terminal is not already denied under
Privacy & Security → Microphone. A previous "Don't Allow" sticks and never
re-prompts; toggle it on manually.

**Nothing works after moving the project.** Permissions attach to the terminal,
not the project, so moving the directory changes nothing. Look elsewhere.

**Running under launchd later.** The responsible process becomes the Python
binary, so you would grant Microphone and Accessibility to the resolved
interpreter under `~/.local/share/uv/python/...`, not to a terminal. Until then,
run it in a terminal tab.

## Related

- [tutorial-first-goal.md](tutorial-first-goal.md) for the full first run
- [reference-cli.md](reference-cli.md) for which operations use which binary
- [howto-tune.md](howto-tune.md) if it hears you but misbehaves
