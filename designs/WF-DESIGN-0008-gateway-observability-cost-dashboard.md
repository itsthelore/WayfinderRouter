---
schema_version: 1
id: WF-DESIGN-0008
type: design
tags: [observability, gateway, cost, dashboard, logging, privacy]
---

# WF-DESIGN-0008: Gateway Observability — Per-Request Logs & Cost/Savings Dashboard

## Status

Proposed

> Grow the read-only `/router` view into an observability cockpit: a queryable per-request log
> and a cost/savings dashboard (route mix, spend, latency, top models/keys, over time). Default
> stays **metadata-only** to preserve the "never prompt text" posture (WF-ADR-0011/0014);
> full prompt/response capture is strictly opt-in with retention controls. Deterministic capture
> plus arithmetic — no model call (WF-ADR-0001).

## Context

Wayfinder exposes aggregate Prometheus `/metrics` (WF-ADR-0018) and a read-only `/router`
dashboard of recent decisions (metadata only, never prompt text — WF-ADR-0011/0014). Adjacent
products treat **per-request logs/traces and a cost dashboard** as table stakes: Helicone (HQL,
sessions, alerts), Langfuse (hierarchical traces + cost-at-ingestion), Cloudflare and Vercel AI
Gateways (per-request logs with tokens/cost/latency, cost analytics). The gap is a queryable
record of *what happened* and *where spend went*, checkable daily — without abandoning
Wayfinder's privacy stance.

## User Need

A developer/operator/cost owner wants to answer, daily and at a glance: is routing working, what
did each request cost and route to, where is latency, and which models/keys/tags dominate spend —
and to drill into an individual request when something looks wrong.

## Design

### The record (metadata-first)

Each request produces a structured record: timestamp, request id, routed model + tier, complexity
score, prompt/completion token counts, realized cost (and baseline/savings, per WF-DESIGN-0007),
decision latency and upstream latency, status, and any tags / virtual-key id (Wave 2). This is
all decision/transport metadata — **no prompt or response text**.

### Privacy: full-text capture is opt-in

Capturing prompt/response bodies (the Helicone/Langfuse default) is powerful but conflicts with
Wayfinder's "never prompt text" posture. Therefore:

- **Default: metadata-only.** Bodies are never stored.
- **Opt-in: full capture**, behind an explicit config flag, with a configurable retention window
  and clear labelling. Off by default; documented as a deliberate choice the operator makes for
  their own self-hosted data.

### Storage

Extend the current in-memory ring to an optional bounded on-disk store (e.g. SQLite or an
append-only log) with configurable retention. Self-hosted, no external service, no new heavy
dependency on the base install.

### Surfaces

- **Dashboard:** grow `/router` into a cockpit — route mix over time, spend & savings trend
  (WF-DESIGN-0007), latency percentiles, top models / keys / tags — filterable by period and tag.
- **Query API:** `GET /v1/requests?…` (and the existing `/router/recent`) for programmatic
  access and a simple query surface, returning metadata records.
- **`/metrics`:** extended with the cost/savings gauges from WF-DESIGN-0007.

All capture and aggregation is deterministic; nothing here calls a model.

## Constraints

- **Metadata-only by default** (WF-ADR-0011); full-text is opt-in with retention.
- **No model call, self-hosted** (WF-ADR-0001/0004); no external logging service.
- **Bounded resource use:** retention caps and daily bucketing keep storage small; must not bloat
  the scored path or the base install.

## Rationale

A dashboard you check is a daily touchpoint, and per-request logs are how operators build trust
that routing is doing the right thing. Doing it *without* defaulting to prompt capture is itself
on-brand — privacy as a feature, matching the "metadata only" promise the project already makes.

## Alternatives

- **Stay on aggregate `/metrics` only** — leaves "why did *this* request route oddly?" unanswerable
  and cedes the observability table stakes to competitors.
- **Default to full-text capture (Helicone-style)** — best ergonomics, but breaks the privacy
  posture; rejected as a default, offered as opt-in.
- **Push to an external store (OTel/Langfuse)** — useful as an *additional* exporter later, but the
  self-hosted, no-dependency local view comes first.

## Accessibility

Plain-text/JSON records and tables; the dashboard remains readable without colour; every figure has
a machine-readable equivalent.

## Open Questions

- Storage backend (SQLite vs append-only file) and default retention.
- Whether to ship an optional OpenTelemetry exporter for teams already on Langfuse/Helicone.
- How request tagging is supplied (header? virtual key? both) — coordinate with Wave-2 keys.

## Success Measures

- An operator can find and inspect a specific request's routing metadata within seconds.
- The cost/savings dashboard is the most-visited gateway surface (instrumented).
- Zero prompt text is stored unless full capture is explicitly enabled.

## Related

WF-ADR-0011 / WF-ADR-0014 (metadata-only decisions; read-only dashboard), WF-ADR-0018 (`/metrics`),
WF-ADR-0004 (gateway), WF-ADR-0001 (no model call), WF-DESIGN-0007 (savings, which this surfaces),
WF-ROADMAP-0006 (item 2).
