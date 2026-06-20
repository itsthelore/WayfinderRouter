---
schema_version: 1
id: WF-ADR-0026
type: decision
tags: [demo, ui, threads, conversations, client-state]
---

# WF-ADR-0026: Conversation Threads in the Demo (Client-Side, localStorage)

## Status

Accepted

## Category

Technical

## Context

The demo handled a single conversation held in a JS array. A thread sidebar (new chat, list,
switch, search) was requested — the question is *where conversation state lives*. Wayfinder's
gateway is deliberately **stateless**: it never stores conversations; the client sends the full
transcript on every request, and sticky / cool-down are computed from that transcript, not from
server memory (WF-ADR-0022). Adding server-side conversation storage would mean a store, eventually
auth and multi-user — a real departure from that posture, for a demo.

## Decision

Manage threads **entirely client-side, in `localStorage`**. The gateway gains no state.

- A thread is `{id, title, created, items[]}`; `items` is the render log
  (`{role:'user'|'assistant'|'note', content, wf?, dry?}`). The API request body is derived from
  `items` (user turns + assistant turns that have content); the stored `wf` decision lets a thread
  re-render its routing strips and recompute its saved-tally on switch.
- Sidebar: **New chat**, **Search** (client-side filter over titles + message text), the **Chats**
  list (active highlight, hover-to-delete), and a collapse toggle; Settings stays the top-right gear.
  Titles are derived from the first user message — **no model call to name a thread** (that would be
  ironic for a cost router, and it's free).
- Per-thread vs global: routing **settings stay global** (apply to the active thread) for now; the
  **latch is naturally per-thread** because it reads that thread's transcript — switching threads
  gives correct per-conversation latching with no extra state.
- We deliberately did **not** replicate the reference's Projects / Plugins / Automations — surface
  area a router demo doesn't have; faking it would be dishonest.

## Consequences

### Positive

- Real multi-conversation UX (persists across reloads on the same browser) with **zero new gateway
  state** — no DB, no auth, no server-side transcripts; the stateless router is intact.
- Per-thread sticky/cool-down falls out for free (the transcript is the state).
- Self-contained: still one HTML string, no build, no storage service.

### Negative / Risks

- Threads are per-browser, not synced across devices — acceptable for a demo; cross-device would
  need the server store we explicitly avoided.
- `localStorage` is unbounded here; a very long history could grow it. Acceptable for a demo;
  capping/pruning is a future nicety.
- The demo drifts further from WF-ADR-0020's "deliberately thin" framing toward a real chat app —
  a conscious, requested step.

## Alternatives Considered

- **Server-side thread store.** Rejected: adds conversation state, persistence, and (eventually)
  auth/multi-user to a stateless router — the opposite of the project's posture.
- **Ephemeral in-memory only.** Rejected: a sidebar you can't return to after reload is half a
  feature.
- **Naming threads via a model call.** Rejected: a model call to title a chat, in a tool whose
  point is avoiding needless model calls. First-message truncation is free and clear.

## Related Decisions

- WF-ADR-0022 (stateless latch from the client transcript — the posture this preserves),
  WF-ADR-0020 (the demo), WF-ADR-0001 (stateless/deterministic core), WF-ADR-0025 (the other
  client-side surface — read-only model status)
