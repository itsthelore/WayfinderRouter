---
schema_version: 1
id: WF-ADR-0029
type: decision
tags: [packaging, distribution, cli, tui, dependencies]
---

# WF-ADR-0029: The Terminal Chat Ships in the Default Install

## Status

Accepted

## Category

Technical

## Context

The terminal chat (`wayfinder-router chat`, WF-DESIGN-0001) is now the lowest-barrier
front door to Wayfinder: a decision-first, brand-coloured TUI that routes, explains,
and — with `[gateway.models]` — replies. Until now it lived behind a `[tui]` extra
(rich + textual), so a bare `pip install wayfinder-router` left `chat` unavailable and
told the user to re-install with the extra.

That extra step is friction squarely against the chosen product direction (the TUI as
the primary entry point). We want `pip install wayfinder-router` — and the no-install
`uvx wayfinder-router chat` — to land the user straight in the chat.

The base wheel has, to date, been dependency-free (stdlib-only scorer + tomllib),
a property other records lean on: WF-ADR-0028 calls the base package "deliberately
zero-dependency," and the README sold it. That property is *separate* from
WF-ADR-0001, whose decision is **independence from RAC** ("zero runtime dependency on
RAC"); rich and textual are not RAC and do not touch that boundary. So the question
here is only whether the convenience surface (the TUI) belongs in the default install
or behind an extra.

## Decision

Promote `rich` and `textual` from the `[tui]` extra into the package's **core
`dependencies`**, so the terminal chat is present after a plain
`pip install wayfinder-router` (and via `uvx` / `pipx`) with nothing extra to add.

- **The scorer and library stay import-light.** rich/textual are imported *lazily*,
  only when the TUI actually runs; `import wayfinder_router` and the
  `route`/`calibrate`/Python-API paths load nothing beyond the standard library, so
  embedding Wayfinder in another tool pulls no UI stack at import time.
- **The gateway and the local UI remain extras** (`[gateway]`, `[ui]`); only the TUI is
  promoted, because it is the default human entry point while those are deployment
  surfaces.
- **`[tui]` is kept as a no-op alias** (`tui = []`) so existing
  `pip install "wayfinder-router[tui]"` commands keep working.
- **WF-ADR-0001 is untouched.** Wayfinder still has zero dependency on RAC and still
  owns its own config; this decision changes only the default *convenience* footprint.

## Consequences

### Positive

- `pip install wayfinder-router` / `uvx wayfinder-router chat` work out of the box — the
  lowest-barrier path the product direction wants.
- One obvious install command; no "now re-install with `[tui]`" detour.

### Negative

- The base install is no longer dependency-free: every install (including
  scorer-only or gateway-only users) now pulls rich + textual.
- The README's "zero dependencies" framing is retired in favour of "dependency-light
  scorer; the terminal chat is included."

### Risks

- **Footprint creep for embedders.** Someone who only wants the scorer now installs the
  UI stack. Mitigation: rich/textual are pure-Python (no compiled extensions, fast
  install), and the lazy imports mean they are never *loaded* unless the TUI runs — so
  embedding stays cheap at runtime even if the wheels are present.
- **Drift with WF-ADR-0028.** That ADR describes the base as zero-dependency. Mitigation:
  this ADR is the newer record; WF-ADR-0028's binary still bundles the `[gateway]`
  extra and is unaffected by the base now including the TUI.

## Alternatives Considered

### Keep the `[tui]` extra; smooth the missing-extra path

Leave the base dependency-free and, when `chat` is run without rich/textual, print a
single copy-paste install hint. Preserves the lean base, but still fails the "always
there / one command" goal — the user must install the extra once before the chat works.

### Promote only `rich`, keep `textual` as the extra

`rich` is small and would make CLI output nicer everywhere, but the chat is a Textual
app, so `chat` would still require the extra. Does not meet the goal.

### A separate distribution for the app (e.g. `wayfinder-router-tui`)

A second package whose only job is to pull the TUI deps. More machinery and a more
confusing install story than simply bundling, for no real benefit at this size.

## Success Measures

- `pip install wayfinder-router` followed by `wayfinder-router chat` works with no extra
  install; `uvx wayfinder-router chat --dry-run` runs with no install and no keys.
- `import wayfinder_router` and `wayfinder-router route` import no third-party module
  (verified by the scorer's stdlib-only import path; rich/textual load only under the TUI).
- `pip install "wayfinder-router[tui]"` still succeeds (no-op alias).

## Related

- WF-DESIGN-0001 (the terminal chat this makes default)
- WF-ADR-0001 (independence from RAC — unchanged; this is a separate, convenience-footprint decision)
- WF-ADR-0028 (described the base as zero-dependency; superseded on that point for the default install)
- WF-ROADMAP-0004 (packaging & distribution — uvx/pipx one-command run)
