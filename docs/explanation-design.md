# Explanation: why the agent is built this way

This agent is driven by a model that cannot write. Every unusual decision in the
code follows from that one constraint.

## The problem

TypeSafe's Jev is a System One model. It returns typed judgments and
probabilities, not text. From the primitives documentation:

> The model returns a probability distribution over your options or levels,
> never a value outside them.

Three primitives exist: `Choice` picks one of up to 255 enumerated options,
`Noul` gives the probability a condition holds, `Score` places something on an
ordered scale. There is no string primitive. The function-calling cookbook is
blunt about arguments Jev cannot supply:

> Free text, numbers and dates work the same way: no question, and the
> function's default stands.

So for the goal *"search Google for cute puppy pics"*, Jev cannot produce
`"cute puppy pics"`. It cannot produce any string. A naive reading says Jev is
unusable for a voice agent, because a voice agent is nothing but strings.

The usual escape is to bolt on a second model that writes text. That is what
`browser-use/jev-ultrafast` does: it gates a small LLM to fire only when the
chosen operation is `TYPE_TEXT`. It works, and it costs a second provider, a
second API key, a second failure mode, and the ability to hallucinate.

## The approach

Turn every decision into a menu that code builds, and let Jev point at an item.

```
  spoken goal ──> code generates every plausible answer ──> Jev picks one
                  ·  page controls, numbered 0..N              (Choice)
                  ·  candidate substrings of the goal          (Choice)
                  ·  the fixed operation list                  (Choice)
                  ·  "is the goal achieved?"                   (Noul)
```

Two places this matters.

**Page controls become numbers.** `SNAPSHOT_JS` walks the DOM and emits a
numbered table of operable controls. The Choice options are `"0"`, `"1"`,
`"2"`. Jev returns an index. It cannot name a button that is not on the page,
because the button's number was never offered.

**Strings become spans of your own words.** `spans()` over-generates substrings
of the goal: every suffix up to 12 words, plus every window opening after a
trigger word like `for`, `search` or `named`, plus anything in quotes. For
*"open chrome and search google for cute puppy pics"* that is about 30
candidates, with `cute puppy pics` reachable three different ways. Jev picks
one. This is TypeSafe's documented value-extraction recipe, and it comes with a
guarantee:

> Because TypeSafe only ever chooses among the spans the regex found, the value
> you get back is one of those spans, copied unchanged. It cannot invent a value
> or transpose a digit.

The agent therefore cannot search for something you did not say. Not "is
unlikely to": cannot. The string is a substring of your transcript or the run
stops.

### One request per step

Each step sends the full state and **all** questions in a single
`system_one` call: the operation choice, the done judgment, and the arguments
for every operation, including the ones that will not be chosen. The dispatcher
reads only the chosen operation's answers and throws the rest away.

This looks wasteful and is not. Independent questions are evaluated in parallel,
and the state dominates the token count. TypeSafe measured a 13-question batch
at 12.2x cheaper and 10x faster than 13 separate calls, for the reason they
state plainly: "The document dominates every request. N single-question calls
pay for it N times, in N round trips; the batched call pays once."

In practice a step is about 3,000 input tokens, roughly $0.00013. A ten-step
goal costs a tenth of a cent.

### The loop holds the goal, not the model

Code keeps the goal and the history; Jev judges one bounded step at a time
against fresh observations. This is the docs' "respond to changing state"
pattern, and it is why a failed action is productive: the failure string lands
in `history`, and the next judgment sees it and routes around it.

```
  observe ──> one Jev request ──> execute ──> record outcome ──┐
     ^                                                         │
     └─────────────────────────────────────────────────────────┘
                    until done / blocked / stuck / step cap
```

## Trade-offs

**Span extraction cannot repair a mistranscription.** If Whisper hears
"kate's" for "k8s", no candidate contains the right word. A generating model
could guess it. This one cannot, and answers `__none__` instead. Refusing
beats silently searching for the wrong thing, but it is a real capability lost.

**Spans cannot normalize.** Ask for a search "in title case" and you get your
words verbatim, because verbatim is the guarantee. Anything requiring rewritten
text needs the jev-ultrafast handoff to a writing model.

**The element table is a token budget, not a page model.** Capped at 120
controls, main document only. No shadow DOM, no iframes, no canvas. A page that
hides its search box in a shadow root is invisible to the agent.

**Numbered indices are not stable identities.** The number means "position in
the snapshot I just took". Pages change under you. Mitigated by re-resolving
the index immediately before acting and failing loudly when the table shrank,
but a page that reorders itself between snapshot and click will get the wrong
element.

## Why the confidence floors differ

The first version gated every step on one threshold and aborted whole goals
over a 0.45 operation choice. That was wrong, and the logs said why.

Confidence measures how concentrated the probability distribution is. Being
torn between "click the search button" and "press enter" spreads probability
across two options that are *both correct*. The docs name this directly:
"Several acceptable alternatives can also spread probability; low confidence
need not invalidate a harmless preference choice." YouTube's search-suggestion
dropdown is the pathological case: eight near-identical entries, none wrong.

So the floors split by what a mistake costs:

| Judgment | Floor | Why |
|---|---|---|
| Element index | `0.30` | A wrong click is undone by one `back`, and equally-good controls spread probability without being an error. |
| Query, URL, app, typed text | `0.55` | Typing the wrong string or opening the wrong app is what you actually notice. |
| Operation choice | `0.25` | Only has to beat noise. The next step corrects a bad guess. |
| Argument-less operations | none | Scrolling at 0.38 is not a risk. It is ambiguity, and it runs. |

Confidence is combined with **minimum, not product**, following the cookbook:
call confidence is "the least certain judgement in the call, rather than the
product of all of them, since one wrong argument is enough to spoil the
result." Multiplying four confident judgments would manufacture a low score out
of nothing.

`Noul` has no confidence field, so a Noul used as a gate contributes
`max(p, 1-p)`. A Noul at 0.5 is maximal uncertainty and correctly drags the
minimum down. That mapping is this codebase's extension, not a documented rule,
and it is commented as such in `conf_of()`.

## Why the browser is driven two different ways

Opening a URL uses `open -a "Google Chrome" <url>`. Clicking inside a page uses
CDP. They look inconsistent and are not.

`open -a` launches Chrome if needed, opens a tab in the existing window, and
brings it forward, in one exec, and triggers **no** Automation permission.
The AppleScript equivalent, `tell application "Google Chrome" to make new tab`,
does the same work but adds a per-application Automation prompt for every
browser. Routing everything through `open` and synthetic keystrokes means the
whole agent needs two permissions, Microphone and Accessibility, instead of two
plus one per target application.

Acting *inside* a page needs to see the page, which `open` cannot do, so that
path uses CDP against a dedicated Chrome profile in `.chrome-profile/`.
Dedicated, not your real profile, and deliberately: it keeps the agent out of
your logged-in sessions, and it means it never fights the Chrome you already
have open. The cost is that it starts logged out.

## Why there is no shell operation

`build()` returns an argv **list** for one of five literal operation names and
raises `ValueError` on anything else. `shell=True` appears nowhere in the
codebase. That is the allowlist, and it is structural: adding a shell escape
would mean editing that function, not passing a flag.

Spoken text reaches the operating system in exactly three places, each
neutered:

1. `quote_plus`'d into a Google query string.
2. Through `safe_url()`, which validates the scheme **before** normalizing the
   string. This ordering is load-bearing. `file:///etc/passwd` contains no dot,
   so an earlier version appended `.com` and prefixed `https://`, producing
   `https://file:///etc/passwd.com`, which parses with scheme `https` and
   passes a naive check. The self-check caught it.
3. As a single argv element after `cliclick t:`, where quotes, `&`, backticks
   and `$` are inert because no shell ever sees them.

A maximally adversarial transcript can search Google for something stupid, or
type something stupid into a focused field. It cannot run a command.

## Why typing is refused, not confirmed, on login pages

Confirmation is the wrong tool for a password field. The failure mode is not
"the user did not want this", it is "a voice agent should not be here at all",
and a spoken yes does not make it safe.

`is_secret()` matches the parsed **hostname** against a bank and
identity-provider list, the path against `login|checkout|password|auth|2fa` and
friends, and checks for any `type="password"` field on the page. Any hit and
`type` raises `PermissionError`, which ends the run rather than retrying,
because a policy refusal would refuse identically on the next step.

Matching the hostname rather than the whole URL is deliberate. An earlier
version ran the host pattern against the full URL, where `accounts.google` is
preceded by `//` rather than a dot, so the `(?:^|\.)` anchor never matched.

## Alternatives considered

**A second model for text generation**, as in jev-ultrafast. Rejected for v1
because span selection is free, deterministic, and cannot hallucinate. Still
the right answer if you need text the user did not speak.

**Apple's native `SpeechAnalyzer`** instead of `mlx-whisper`. On paper the
better choice on macOS 26+: fully on-device, streaming, no 60-second ceiling.
Rejected because a plain `swiftc` binary has no bundle, so it needs an
`Info.plist` injected at link time or TCC hard-denies the microphone, plus a
second language, a build step and an IPC protocol, to avoid three `uv add`
lines. Worth revisiting if post-utterance latency ever exceeds ~1.5 s.

**AppleScript for browser control.** Rejected over the per-application
Automation prompts, as above.

**`tell application "Music" to playpause`** for media. Rejected because it only
ever controls Music, and launches Music when nothing is playing. `cliclick
kp:play-pause` synthesizes the real media key, so it reaches whatever owns
media right now: a YouTube tab, Discord, anything.

**`<select>` support.** Dropped from v1. Clicking usually opens a dropdown
anyway, and speculative per-dropdown option questions cannot be built without
knowing which dropdown was chosen, which would force a second request.

## Related

- [reference-cli.md](reference-cli.md) for the constants named here
- [reference-log.md](reference-log.md) for the state and element table shapes
- [howto-tune.md](howto-tune.md) to adjust the floors on your own data
- [tutorial-first-goal.md](tutorial-first-goal.md) to see the loop run
