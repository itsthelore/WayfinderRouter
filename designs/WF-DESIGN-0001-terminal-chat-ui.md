---
schema_version: 1
id: WF-DESIGN-0001
type: design
tags: [tui, cli, demo, gateway, brand, v0.3.0]
---

# WF-DESIGN-0001: Wayfinder Terminal Chat (a Claude-Code-style TUI, Wayfinder-branded)

## Status

Proposed

> Design spec for a terminal chat UI that mirrors the Claude-Code-in-a-terminal
> experience while surfacing Wayfinder's routing decision, in the Wayfinder palette.
> Companion to the browser demo (WF-ADR-0020); slots under WF-ROADMAP-0004.
>
> Implemented (first cut): `wayfinder-router chat` ships the Rich TUI — collapsible
> decisions (`/why`), a `/settings` panel, and **in-process replies**: when
> `[gateway.models]` are configured it calls the chosen model via the gateway's
> `invoke_messages` relay (no server spawned), printing the reply under each decision;
> keyless / `--dry-run` stays decision-only. The HTTP thin-client form (attach to a
> remote gateway via `--base-url`) remains future. The browser surface is
> `wayfinder-router webchat`.

## Context

The `/demo` web UI (WF-ADR-0020) proves the pitch in a browser: a chat that shows
the routing decision (local vs cloud), the structural score and *why*, and the cost
saved. But the routing decision is compact, structured text — `model · score ·
features · $saved` — which is inherently terminal-friendly, and the audience
(developers, operators) already lives in a terminal. A TUI that looks and feels like
**Claude Code running in a terminal**, but branded as Wayfinder and centred on the
*decision*, would be a more native, SSH-able, scriptable front door than a browser
tab — without a fork, without a browser, and closer to the project's "small,
terminal-first, boring on purpose" ethos (WF-ADR-0001).

This design defines that surface: layout, interaction, brand/colour mapping, and the
architecture that lets it reuse everything the gateway already exposes.

## User Need

- **Who:** developers and operators evaluating or running Wayfinder, often over SSH
  or inside an existing terminal workflow; the same people who reach for `curl`,
  `ollama run`, or Claude Code rather than a browser.
- **What they need:** to chat through Wayfinder and *see the decision happen* —
  which model handled each turn, how hard the prompt scored, why, and what it saved —
  with the immediacy and keyboard-only flow of a native terminal app, and visual
  identity that reads unmistakably as Wayfinder.
- **Job:** "Let me talk to my models in the terminal and watch Wayfinder route each
  message, tune the threshold live, and keep my conversations — without leaving the
  shell or spinning up a browser."

## Design

### Shape (mirrors Claude Code)

An **inline** terminal app (preserves scrollback; not a full alt-screen takeover by
default), with three persistent regions, exactly like Claude Code:

1. **Welcome box** on launch — a rounded box with the Wayfinder mark, the active
   config (threshold, scope, sticky), and the configured models with key status.
2. **Transcript** — scrollback of turns. Each model reply carries an inline
   **decision line** (Wayfinder's differentiator) that replaces the web demo's
   per-reply `?` popover.
3. **Composer** — a rounded, accent-bordered input box pinned at the bottom with a
   `›` prompt and a one-line hint/status footer beneath it (shortcuts + the running
   saved-vs-cloud tally).

A spinner with live status (`✦ routing…` → `✦ cloud · claude-sonnet-4-6`) shows
while a turn is in flight, matching Claude Code's working indicator.

### Mockup

```
╭──────────────────────────────────────────────────────────────────╮
│  ✦ Wayfinder   routing on · threshold 0.30 · scope turn · sticky off│
│  local  llama3.1:8b        cloud  claude-sonnet-4-6  ⚠ key missing  │
╰──────────────────────────────────────────────────────────────────╯

› Summarize this paragraph in one line.

  ● LOCAL  llama3.1:8b   score 0.18   saved ~$0.011         ⌄ why
  Wayfinder scores the prompt's structure and keeps the simple ones local…

› Prove the halting problem is undecidable via the diagonal argument.

  ◆ CLOUD  claude-sonnet-4-6   score 0.71                   ⌄ why
  Assume a total decider H(p, x). Construct D(p) = if H(p, p) loop else halt…

╭─ ask anything ───────────────────────────────────────────────────╮
│ › ▏                                                                │
╰──────────────────────────────────────────────────────────────────╯
  enter send · ⇧⏎ newline · / commands · ↑ history · ctrl+c quit   saved $0.43
```

Expanding **why** (toggle on the decision line) reveals the feature breakdown —
the same `explain_score` contributions the web demo shows:

```
  ● LOCAL  llama3.1:8b   score 0.18   saved ~$0.011         ⌃ why
    word_count       12   weight 0.40   → 0.05
    reasoning_terms   0   weight 1.00   → 0.00
    constraints       1   weight 0.80   → 0.08
    cut 0.30 · score 0.18 < cut → local
```

### Interaction model (keyboard-first, Claude-Code-like)

- **Enter** sends; **Shift+Enter** (or trailing `\`) newline; **↑/↓** history;
  **Ctrl+C** cancels an in-flight turn, again to quit; **Esc** interrupts; **Tab**
  expands/collapses the focused reply's *why*.
- **Slash commands** replace the web Settings flyout (a command palette, very
  Claude-Code): `/threshold 0.3`, `/scope turn|last_user|user|all`,
  `/sticky on|off [cooldown N]`, `/profile <id>`, `/models` (key status),
  `/export` (round-trippable `[routing]` TOML, via `POST /router/config`),
  `/new`, `/threads`, `/help`, `/quit`. Typing `/` opens an autocomplete menu.
- **Threads**: `/threads` lists saved conversations; `/new` starts one. (Textual
  variant: a collapsible left pane, echoing the web sidebar.) Persisted to disk
  (see Constraints), which is strictly more durable than the web demo's
  `localStorage` (WF-ADR-0026).

### Architecture — a thin client over the gateway contract

The TUI is to the terminal what `/demo` is to the browser: **pure presentation over
the existing contract**, no new routing logic.

- It `POST`s `/v1/chat/completions` with `model:"auto"` and the live
  `X-Wayfinder-Threshold` / `-Route-On` / `-Sticky` headers, and reads the decision
  from the response headers (`x-wayfinder-router-model` / `-score` / `-mode`) plus
  the `X-Wayfinder-Debug` payload for the *why* breakdown — exactly as the web demo
  does (WF-ADR-0020, WF-ADR-0011).
- It reads `/router/models` and `/router/profiles` for status and profile pickers.
- **Gateway lifecycle:** `wayfinder-router chat` boots the gateway the same way
  `webchat` does (reusing `gateway.run`/`build_app`) and attaches in-process; or
  `--base-url URL` attaches to an already-running gateway. `--dry-run` gives the
  keyless decision-only demo (routes, shows the decision, prints a "routed, not
  answered" note) just like the web demo.

This means the deterministic core and the relay are untouched; the TUI adds only a
terminal renderer + input loop.

## Constraints

- **Boundary (WF-ADR-0001).** The TUI is an impure presentation surface; it never
  enters the scored path. The scorer/`explain_score` stay stdlib-only and offline.
- **Dependencies are opt-in.** The brand-fidelity goal (truecolour, rounded boxes,
  live/streaming widgets) wants a TUI toolkit. Ship it as an opt-in extra
  (`pip install "wayfinder-router[tui]"`), exactly like `[gateway]`/`[ui]`; the base
  wheel stays zero-dependency. A degraded **stdlib-only** REPL remains possible for
  the no-extra case (see Alternatives).
- **Persistence:** threads/folders saved as JSON under the XDG config dir
  (`~/.config/wayfinder-router/threads/`), never secrets — keys stay in the
  environment (WF-ADR-0008).
- **Terminal reality:** honour the user's terminal background (paint *foreground*
  accents and borders, do not flood a full-screen background); degrade colour
  gracefully (truecolour → 256 → 16 → monochrome); respect `NO_COLOR`.
- **Reuse, don't reinvent:** consume the gateway contract and `explain_score`; no
  second scoring or relay implementation.

## Rationale

- **Decision-first is text-first.** The router's output is structured metadata; a
  terminal renders it with zero ceremony and maximum density — the TUI plays to
  Wayfinder's strength rather than dressing it up.
- **Thin client = no fork, no drift.** Reusing the `/v1` + `/router/*` contract means
  the TUI and the web demo can never disagree about a decision, and the engine is
  the single source of truth (mirrors the WF-ADR-0020 reasoning).
- **Recommended toolkit: Textual** (built on Rich). It gives the full
  Claude-Code-like experience — a pinned input box, panes for threads, live/streaming
  updates, truecolour, rounded borders — in one well-maintained library, as a `[tui]`
  extra. Rich-only is the lighter fallback if panes aren't needed (see Alternatives).
- **Brand carries through colour + glyph, not chrome.** Wayfinder's identity in a
  terminal is the green/amber local-vs-cloud language and the `✦` mark, applied to
  text and borders — instantly recognisable without a GUI.

## Alternatives

### Rich only (no Textual)

Rich alone can render the welcome box, decision lines, *why* tables, and a prompt
loop with live updates — lighter than full Textual and closer to Claude Code's
inline-scrollback model. **Carried as the likely first cut**; Textual is adopted when
we want a persistent input box with side panes (threads). Decide in a spike.

### Stdlib-only REPL (`readline`/`curses`)

Keeps the zero-dependency purity (no `[tui]` extra). A `readline` line-REPL is easy
but cannot match the brand fidelity (rounded boxes, truecolour, live streaming);
`curses` can do more but is laborious and weaker on truecolour. Acceptable as a
**fallback mode** when the extra isn't installed, not as the branded experience.

### Browser demo only (status quo)

`wayfinder-router webchat` already serves `/demo`. Kept — this TUI is an *additional*
surface for terminal-bound users, not a replacement.

## Accessibility

- **Never colour alone.** Local vs cloud is encoded by **glyph + label + colour**
  (`● LOCAL` green, `◆ CLOUD` amber), so colourblind users and `NO_COLOR` terminals
  still read the decision. Warnings use `⚠` plus the warn colour.
- **Colour degradation:** truecolour → xterm-256 nearest → 16-colour → monochrome;
  honour `NO_COLOR` (monochrome with glyphs) and `--theme light|dark|auto`.
- **Keyboard-only by nature**; every action has a key or slash command, all
  discoverable via `/help` and the footer hint line.
- **Screen readers:** TUIs are inherently limited here; provide a `--plain`
  line-oriented mode (no live regions, one line per event) that is friendlier to
  screen readers and to logging/piping.
- **Contrast:** the chosen accents meet the web UI's contrast targets; verify the
  256/16-colour fallbacks keep local/cloud and text/muted distinguishable.

## Style Guidance

### Palette (from `wayfinder_router/demo.html`)

| Role | Light | Dark | Terminal use |
| --- | --- | --- | --- |
| Local / accent | `#10a37f` | `#19c8a4` | `● LOCAL`, composer border, focus, `✦` mark |
| Cloud | `#bd6a13` | `#e0a25c` | `◆ CLOUD` |
| Text | `#0d0d0d` | `#ececec` | primary message text |
| Muted | `#6b6b78` | `#9a9aa6` | hints, footer, *why* labels, secondary |
| Line | `#ececef` | `rgba(255,255,255,.08)` | box borders (use `line-strong` for focus) |
| Warn | `#d97706` | `#d97706` | missing key, validation, cautions |

The surface backgrounds (`#ffffff`/`#1e1e20`) are reference only — the TUI inherits
the terminal's background and theme; it paints foreground accents and borders, never
a full-screen fill.

### Tone & motifs (match Claude Code, brand it Wayfinder)

- Rounded box-drawing (`╭ ╮ ╰ ╯`) for the welcome box and composer; thin rules for
  separators.
- A single `✦` mark for Wayfinder (welcome, spinner) — the equivalent of Claude
  Code's `✻`.
- Lowercase, terse hint lines (`enter send · ⇧⏎ newline · / commands · ctrl+c quit`).
- A working spinner with live status text; quiet, low-chrome, generous spacing —
  the same restraint as the web demo (WF-ADR-0020).
- System-monospace only (the terminal's font); no fonts fetched, no glyphs that
  aren't broadly available (provide ASCII fallbacks for `● ◆ ✦ ⚠ ⌄ ⌃` when the
  locale/term can't render them).

## Open Questions

- **Toolkit:** Rich-only first cut vs Textual from the start — settle in a spike
  against the mockup (binary size, streaming feel, threads-pane need).
- **Gateway lifecycle:** auto-spawn in-process (like `webchat`) vs require a running
  gateway vs both (`--base-url`). Default?
- **Streaming:** token-by-token now (depends on gateway streaming, WF-ADR-0013) or
  non-streaming with a spinner first?
- **Theme auto-detection:** OSC 11 background query vs `COLORFGBG` vs default-dark —
  which is reliable enough to default to `auto`?
- **Command surface:** settled — `wayfinder-router chat` is the terminal chat and
  `wayfinder-router webchat` is the browser UI (formerly `chat`).
- **Threads UI:** inline `/threads` list (Rich) vs a persistent left pane (Textual).

## Related

- WF-ADR-0020 (the browser demo this mirrors; same decision-first contract)
- WF-ADR-0011 (per-request override headers the TUI sets live)
- WF-ADR-0004 (the OpenAI-compatible gateway it clients)
- WF-ADR-0025 / WF-ADR-0024 (models/key status and lexicon profiles it surfaces)
- WF-ADR-0026 (web threads via localStorage; the TUI persists to disk instead)
- WF-ROADMAP-0004 (packaging & distribution — the `[tui]` extra slots here)
- WF-ADR-0001 (the deterministic boundary preserved; the TUI is presentation only)
