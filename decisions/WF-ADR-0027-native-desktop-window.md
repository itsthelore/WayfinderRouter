---
schema_version: 1
id: WF-ADR-0027
type: decision
tags: [packaging, desktop, demo, gateway, distribution]
---

# WF-ADR-0027: A Native Desktop Window for the Demo (Thin Wrapper, Not a Fork)

## Status

Superseded by WF-ADR-0042

> The spike this ADR deferred to was run (Tauri vs pywebview, plus an Ink terminal-client
> comparison), and the outcome outgrew the URL-wrapper framing: the desktop surface is a
> **Tauri v2 menu-bar client** rendering the gateway's decisions over loopback HTTP — not a
> wrapped `/demo` tab. See WF-ADR-0042 for the architecture, WF-DESIGN-0012 for the popover
> design, and WF-ROADMAP-0009 for delivery. The "don't grow a fork" discipline argued here
> survives intact — the client renders decisions and never makes them.

## Category

Technical

## Context

WF-ROADMAP-0004 (Initiative 4) wants the decision-first `/demo` UI to open in a
native window so Wayfinder feels like a product, not a `localhost` browser tab. The
gateway already serves `/demo` and `wayfinder-router webchat` already boots it and
opens a browser (WF-ADR-0020). The open question is *how* to present it as a native
app without taking on the maintenance tail of a bespoke desktop application — the
same "don't grow a fork" discipline WF-ADR-0020 applied to the demo itself.

Two thin wrappers are viable, and they trade off differently:

- **pywebview** — Python-native; launches the gateway in-process and renders
  `/demo` in the OS webview. One toolchain, reuses `chat`, no new build system.
- **Pake** (`https://github.com/tw93/pake`) — a Tauri/Rust wrapper that turns the
  served URL into a tiny (~5 MB) native macOS/Windows/Linux app. Smallest binary
  and the most "instant native app," but adds a Rust/Tauri build step outside the
  Python toolchain.

## Decision

Wrap the **existing `/demo` URL in a thin native shell**, rather than building a
bespoke Electron/Tauri application. The wrapper boots the gateway via the existing
`chat` path and points a native window at it; it stays entirely in the
distribution layer and never touches the deterministic core (WF-ADR-0001).

The tool choice is **deferred to a spike**: build a throwaway of both Pake and
pywebview, measure binary size and build complexity, and select one. Current lean
is **Pake** for the smallest, most native-feeling artifact, with **pywebview** as
the fallback if staying single-toolchain outweighs binary size. The outcome (and
the measurements that drove it) will be recorded as an amendment to this ADR.

## Consequences

### Positive

- A double-click, native-window Wayfinder that reuses the existing UI and request
  contract — no second UI, no fork to rebase.
- Reinforces the product story without committing to maintain a full desktop app.

### Negative

- A new OS-facing surface to keep working across OS/webview updates.
- The build toolchain depends on the choice (a Rust/Tauri step for Pake, or webview
  engine differences across platforms for pywebview).

### Risks

- **Toolchain creep.** Pake introduces Rust/Tauri outside the Python build.
  Mitigation: keep it a thin URL wrapper; the binary/app is additive and never the
  default path (pip/uvx remain primary).
- **Webview inconsistency.** pywebview renders on per-OS engines. Mitigation: the
  demo is already plain, dependency-free HTML/CSS/JS (WF-ADR-0020), which minimizes
  engine-specific behaviour.

## Alternatives Considered

### A bespoke Electron/Tauri desktop app

A fully custom shell. Rejected for the first cut: it is exactly the heavy,
perpetually-maintained surface WF-ADR-0020 set out to avoid; a thin URL wrapper
delivers the native feel at a fraction of the cost.

### Browser-only (status quo)

`wayfinder-router webchat` already opens `/demo` in the default browser. Kept as the
default; this ADR is only about an *additional* native presentation, not a
replacement.

### Installable PWA

Lighter than a wrapper, but still browser-bound and inconsistent across platforms;
it does not deliver the "double-click native app" outcome the roadmap wants.

## Success Measures

- A double-clicked app opens `/demo` in a native window on at least macOS.
- Binary size and build complexity are measured for both Pake and pywebview.
- The chosen approach and its rationale are recorded as an amendment here.

## Related

- WF-ROADMAP-0004 (Initiative 4 — the desktop-window initiative this decides)
- WF-ADR-0020 (decision-first demo UI and the `chat` launcher being wrapped)
- WF-ADR-0004 (the OpenAI-compatible gateway being served)
- WF-ADR-0008 (packaging and integration surfaces)
- WF-ADR-0001 (the deterministic boundary preserved throughout)
