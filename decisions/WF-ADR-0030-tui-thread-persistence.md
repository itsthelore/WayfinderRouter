---
schema_version: 1
id: WF-ADR-0030
type: decision
tags: [tui, threads, conversations, persistence, client-state]
---

# WF-ADR-0030: Terminal-Chat Conversations Persist to Disk

## Status

Accepted

## Category

Technical

## Context

The terminal chat (WF-DESIGN-0001) held a single in-memory conversation: quit and it
was gone. The demo solved the same need with client-side threads in `localStorage`
(WF-ADR-0026) — a browser store the terminal does not have. The question is where the
TUI's conversation state lives.

The constraints are the same as the demo's: the gateway is deliberately **stateless**
(it never stores transcripts; sticky/cool-down are computed from the client's transcript,
WF-ADR-0022), and the core is stdlib-only and deterministic (WF-ADR-0001). So thread
state must live on the client, and for a terminal the natural, durable store is the
user's own disk.

## Decision

Persist conversations as **JSON files on the user's disk**, one file per thread, managed
entirely by the TUI. The gateway gains no state.

- **Location**: `$WAYFINDER_DATA_DIR` if set, else `$XDG_DATA_HOME/wayfinder/threads`,
  else `~/.local/share/wayfinder/threads`. The env override keeps it testable and lets
  users relocate it.
- **Shape**: `{id, title, created, updated, messages[]}`, where `messages` is the
  OpenAI-style transcript already sent to the relay. The id is a sortable timestamp plus
  a short random suffix and is the filename. Titles derive from the first user message,
  truncated — **no model call to name a chat** (WF-ADR-0026), which would be doubly ironic
  in a cost router.
- **Lifecycle**: the active thread auto-saves as the conversation advances (after each
  turn and its reply), so nothing is lost on quit or crash. `/new` starts a fresh thread;
  `/threads` lists saved conversations newest-first; `/open <n>` loads one and continues it
  (same id, so it updates in place). `/btw` asides are ephemeral and never persisted.
- **Re-render on load** is reconstructed deterministically: each user message is re-scored
  with the offline core to show its routing strip, so a loaded thread looks like a live one
  without persisting decisions. (It reflects the *current* threshold/config — a feature, not
  a bug: it shows how the turn routes now.)
- **Settings stay global**; the sticky latch remains naturally per-thread because it reads
  that thread's transcript (WF-ADR-0022) — switching threads needs no extra state.

The richer demo affordances (folders, drag-to-file, pinning, inline rename) are **not**
ported in this cut; the terminal gets the core loop — new / list / open / auto-save — and
can grow the rest later.

## Consequences

### Positive

- Conversations survive across sessions, on the user's own machine, with **zero new gateway
  state** — no DB, no auth, no server transcripts; the stateless router is intact.
- Pure and stdlib-only (`wayfinder_router/threads.py`), so it is unit-testable without a
  terminal and adds no runtime dependency.
- Per-thread sticky/cool-down falls out for free (the transcript is the state).

### Negative / Risks

- Threads are per-machine, not synced across devices — acceptable, matching the demo's
  per-browser scope; cross-device would need the server store we explicitly avoid.
- The directory is unbounded; a very long history could accumulate files. Acceptable for now;
  capping/pruning is a future nicety (as in WF-ADR-0026).
- A loaded thread's strips are re-scored at *current* settings, so they can differ from when
  the turn was first sent. Documented above; persisting decisions verbatim is a later option.

## Alternatives Considered

### Ephemeral in-memory only (status quo)

Rejected: losing every conversation on quit is half a chat. Persistence is the point.

### A server-side thread store

Rejected for the same reason as WF-ADR-0026: it adds conversation state, persistence, and
eventually auth/multi-user to a stateless router — the opposite of the project's posture.

### Naming threads via a model call

Rejected (WF-ADR-0026): a model call to title a chat, in a tool whose point is avoiding
needless model calls. First-message truncation is free and clear.

## Success Measures

- A conversation held in `chat` is still listed by `/threads` and re-openable with `/open`
  after the process exits and restarts.
- `wayfinder_router/threads.py` round-trips a thread (save → load) with no terminal, and
  `list_threads` orders newest-first.
- The gateway and the scorer gain no state; `/btw` asides never appear in a saved thread.

## Related

- WF-ADR-0026 (the demo's localStorage threads — the model this mirrors on disk)
- WF-ADR-0022 (stateless latch from the client transcript — the posture preserved)
- WF-ADR-0001 (deterministic, stdlib-only core — `threads.py` adds no dependency)
- WF-DESIGN-0001 (the terminal chat this persists)
