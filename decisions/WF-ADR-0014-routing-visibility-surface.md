---
schema_version: 1
id: WF-ADR-0014
type: decision
tags: [gateway, observability, control-surface, privacy, boundary]
---

# WF-ADR-0014: Routing Visibility Surface

## Status

Accepted

## Category

Architecture

## Context

Wayfinder deliberately ships no bespoke chat UI (WF-ADR-0005 is an operator console,
off the traffic path; WF-ADR-0010 puts the turnkey chat experience in a separate
fork). Instead the control surface is *distributed* across the tools a user already
runs: the host client's model dropdown is the routing-mode picker (now fed by
`/v1/models`, WF-ADR-0012), the `X-Wayfinder-Threshold` header is the fine control,
and the `x-wayfinder-router-*` response headers report each decision.

That picker — the "in" side — works. The "out" side does not: most chat clients do
not surface response headers, so a user cannot see *where a prompt went* or *why*.
"Is this even routing?" is unanswerable without a network inspector. The control
surface is coherent in theory but invisible in practice.

The fix is not to build a UI; it is to make the existing surface *visible*. The
constraint is the boundary (WF-ADR-0001/0004) and a privacy line: visibility must
not store prompt text, hold keys, or call a model, and it must not change the
default response for strict clients.

## Decision

Two read-only surfaces and one opt-in response annotation, all metadata-only.

- **`GET /router/recent`** returns the last N routing decisions as JSON —
  `model`, `score`, `mode`, `request_id`, `ts` — plus a per-model count. **Decision
  metadata only; never prompt text.** It is held in a bounded in-memory ring (200
  entries), not persisted: this is ephemeral observability, distinct from the
  feedback label log (WF-ADR-0006) which *does* persist (labels, by consent). Pure
  and offline — no key, no model call, no network, like `/healthz`.
- **`GET /router`** serves a tiny, self-contained dashboard (no CDN, no web font, no
  build step) that polls `/router/recent` and shows the recent decisions, the
  per-model split, and scores at a glance. It is *not* the `wayfinder-router ui`
  operator console — that is the tuning bench, off the traffic path; this is the
  "is it working?" cockpit for the running gateway.
- **`X-Wayfinder-Debug: true`** (opt-in request header) surfaces the decision *in
  the response*: a `wayfinder` object injected into a non-streaming JSON body, or a
  trailing `wayfinder` SSE event on a stream — for clients that render the body but
  hide headers. Off by default, so the relayed response stays byte-clean for strict
  clients; the `x-wayfinder-router-*` headers carry the decision either way.

The boundary holds: scoring is unchanged, no prompt text is stored or exposed, and
the new routes read in-memory metadata and serve static HTML only.

## Consequences

### Positive

- A user can see routing happening — and where — without a bespoke UI or a network
  inspector, closing the gap between "it works" and "I feel in control".
- The decision can be rendered inside a chat client (via the debug body field)
  without forking it.
- Visibility is privacy-preserving by construction: metadata only, ephemeral,
  no keys.

### Negative

- The gateway holds a little in-memory state (the ring) it did not before; it is
  bounded and lost on restart, which is the intended ephemerality.
- A third "surface" (dashboard) exists alongside the operator console; the README
  states which is which.

### Risks

- A future contributor extends the ring to store prompt text for "richer" debugging.
  Mitigation: this ADR draws the line explicitly — metadata only — and the feedback
  log remains the single, consented place prompt text is persisted.

## Alternatives Considered

### Always inject the decision into the response body

Make every response carry the `wayfinder` object.

#### Disadvantages

- Mutates the relayed payload for every client; strict OpenAI clients may reject an
  unexpected top-level field. Opt-in via a header keeps the default byte-clean.

### Persist a routing-decision log to disk

Write decisions to a file for later inspection.

#### Disadvantages

- Duplicates the feedback log's persistence and raises the prompt-text/privacy
  question. Ephemeral in-memory metadata answers "is it working?" without either.

## Success Measures

- `GET /router` shows recent routing decisions at a glance on a running gateway;
  `GET /router/recent` returns the same as JSON, metadata only.
- `X-Wayfinder-Debug: true` adds a `wayfinder` object to the response while the
  default response is unchanged.
- No prompt text appears in `/router/recent`, and the scored path and prior gateway
  tests are unchanged.

## Related Decisions

- WF-ADR-0012 (the `/v1/models` picker — the "in" side this complements)
- WF-ADR-0011 (the override channels whose decisions this makes visible)
- WF-ADR-0005 (the operator console this read-only view is deliberately distinct from)
- WF-ADR-0010 (the chat fork whose per-conversation slider is the richer control)
- WF-ADR-0001 / WF-ADR-0004 (the boundary visibility stays inside)
