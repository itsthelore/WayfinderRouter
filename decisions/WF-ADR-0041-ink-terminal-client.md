---
schema_version: 1
id: WF-ADR-0041
type: decision
tags: [client, tui, ink, packaging, strategy-d, npm]
---

# WF-ADR-0041: Ship the Ink terminal client as a separate npm package

## Status

Accepted

## Category

Technical

## Context

Wayfinder's cross-surface direction is **one backend, many thin clients** ("Strategy D"): the
gateway makes the routing decision offline and deterministically (WF-ADR-0001) and serves replies;
clients only render what the gateway returns over its HTTP API (`/v1/chat/completions` with
`X-Wayfinder-Debug`, `/router/models`, `/healthz`, `/v1/savings`). The launchd service (WF-ADR-0038)
and the macOS menu-bar (WF-ADR-0040) are the OS-side expression of this; an in-process **Textual**
chat (`wayfinder-router chat`) already ships in the Python package.

A throwaway spike proved an **Ink** (React-for-the-terminal) client could faithfully reproduce the
Textual TUI and drive the gateway end-to-end — decision from the response headers, replies streamed
from the SSE body, the "why" enriched from the trailing `event: wayfinder` — with no embedded scorer
and no parity gate. The open question was how to *ship* it: Ink is a Node/React tool, while
`wayfinder-router` is a stdlib-only Python package whose value rests on "zero runtime dependencies,
nothing to rot."

## Decision

Ship the Ink client as a **standalone npm package, `wayfinder-terminal`, co-located in this repo at
`clients/terminal/`**, published independently to npm and run with `npx wayfinder-terminal --base-url …` (or a
global install). It is a pure gateway client: it **never scores** — every decision it shows is the
gateway's.

- Layout: `src/app.jsx` (the UI), `src/gateway.js` (the wire contract), `src/theme.js` (palette),
  `bin/wayfinder.js` (plain-JS CLI entry). esbuild bundles `src/` → `dist/`, leaving `react`/`ink`
  external (installed as dependencies); the bin needs no transpile.
- Runtime dependencies are only `react` + `ink`. Node ≥ 18 (uses global `fetch`/`TextDecoder`).
- The Python package keeps a one-line pointer to it for discovery; it does **not** depend on it.

## Alternatives considered

- **Bundle the built JS into the Python wheel and shell out to `node` from a `wayfinder-router ink`
  verb.** Rejected: it puts a Node app inside a Python wheel, still requires Node on the user's
  machine (the wheel can't carry a runtime), and couples a client's release to the backend's — all to
  avoid one `npx`. It also erodes the "zero-dependency core" property.
- **Only ever offer the in-process Textual chat.** Kept (it still ships), but Textual is Python-only;
  the Ink client's React components are the seed for a future web/desktop client over the same
  gateway, which Textual cannot be.

## Consequences

- A second published artifact and a second release lane (npm, versioned independently of the PyPI
  CalVer). Using the client requires Node; the gateway and the Textual chat do not.
- The decision boundary is unchanged: the client makes no routing decision (WF-ADR-0001); it is a
  consumer of the gateway like the menu-bar (WF-ADR-0040) and `chat_core` clients.
- The same React/Ink components can later seed a web/desktop client over the identical gateway API.
