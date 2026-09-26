# How to use your real Chrome profile

You will accomplish: the agent acting inside your logged-in Chrome, so goals
that need a signed-in account work.

**Read the trade-off first.** By default the agent starts its own Chrome against
`.chrome-profile/`, which is logged out. That is deliberate: it keeps the agent
out of your accounts, and it means it never fights the Chrome you already have
open. Following this guide removes both protections. A misheard goal then acts
as *you*, inside your mail, your accounts, your session cookies.

The typing guardrail still applies: `is_secret()` refuses to type on login,
payment and banking pages regardless of profile. Clicking is not restricted.

## Prerequisites

- Chrome closed. Chrome will not open a debugging port on a profile another
  instance has locked, so you cannot attach to an already-running Chrome.
- You have read the paragraph above.

## Steps

1. Quit Chrome completely. Cmd-Q, not just closing windows.

   ```bash
   osascript -e 'tell application "Google Chrome" to quit'
   ```

   Confirm nothing is left:

   ```bash
   pgrep -f "Google Chrome" || echo "chrome is closed"
   ```

2. Relaunch Chrome yourself with a debugging port, pointed at your real profile.

   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --remote-debugging-port=9222 \
     --user-data-dir="$HOME/Library/Application Support/Google/Chrome" &
   ```

   Your normal windows, tabs, extensions and logins come back.

3. Confirm the port is live before starting the agent.

   ```bash
   curl -s http://127.0.0.1:9222/json/version
   ```

   You want JSON naming your Chrome build. Empty output means the port is not
   open: Chrome was still running in step 1.

4. Start the agent. It finds the existing port and attaches instead of launching
   its own Chrome.

   ```bash
   cd Jev-Agentic
   uv run --env-file .env agent.py
   ```

   The startup line tells you which happened:

   ```
   chrome: existing on port 9222
   ```

   `existing` means it attached to yours. `launched` means it started its own and
   you are still on the dedicated profile, so step 2 did not take effect.

## Verification

Run a goal that only works signed in:

```bash
uv run --env-file .env agent.py --goal "go to gmail and tell me how many unread emails I have"
```

On the dedicated profile this lands on a sign-in page and the run ends
`blocked`. On your real profile it reaches your inbox.

## Going back to the isolated profile

Quit the Chrome you launched with the port. The agent finds no port on the next
run and starts its own against `.chrome-profile/` again. Nothing to
uninstall, nothing to edit.

## How the attach works

`ensure_chrome()` in `browser.py`:

1. Probes `http://127.0.0.1:9222/json/version`.
2. If it answers, returns `existing` and attaches. **Your profile choice wins**,
   because whatever is on that port is what it connects to.
3. If not, launches Chrome with `--remote-debugging-port=9222` and
   `--user-data-dir=<the dedicated profile>`, plus `--no-first-run`,
   `--no-default-browser-check` and `--restore-last-session=false`.
4. Polls for up to 15 seconds, then raises
   `Chrome did not open a debugging port on 9222`.

The port is `CDP_PORT` in `browser.py`. Changing it changes both the probe and
the launch, so they cannot disagree.

## Troubleshooting

**`chrome: launched` when you wanted `existing`.** The port was not open when
the agent started. Chrome was still running during step 1, so your step-2 launch
silently reused the existing instance and ignored the flag. Quit properly,
verify with `pgrep`, relaunch.

**`Chrome did not open a debugging port on 9222`.** Something else holds the
port, or Chrome refused to start. Check with
`lsof -nP -iTCP:9222 -sTCP:LISTEN`.

**Your windows did not come back in step 2.** The `--user-data-dir` path is
wrong. The default on macOS is
`~/Library/Application Support/Google/Chrome`. A wrong path creates a fresh
empty profile rather than failing.

**The agent acts on the wrong tab.** It follows the last page in the context, so
opening tabs by hand mid-run moves it. `Browser._active()` re-resolves the page
on every snapshot, so let it settle and it follows you.

**Anything on port 9222 is fair game.** The agent attaches to whatever answers.
Do not leave that port open to a browser you did not intend to hand over.

## Related

- [explanation-design.md](explanation-design.md) for why the dedicated profile is the default
- [reference-log.md](reference-log.md) for what the snapshot reads from the page
- [tutorial-first-goal.md](tutorial-first-goal.md) for the default path
