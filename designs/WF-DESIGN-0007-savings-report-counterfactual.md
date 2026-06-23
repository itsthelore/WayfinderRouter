---
schema_version: 1
id: WF-DESIGN-0007
type: design
tags: [cost, finops, savings, observability, gateway, counterfactual]
---

# WF-DESIGN-0007: Savings Report & Auditable Counterfactual

## Status

Proposed

> Turn Wayfinder's session cost tally into a **persisted, per-period savings report** with an
> explicit, auditable counterfactual — "you spent $X; always routing to the frontier model
> would have cost $Y; you saved $Y−X (Z%)" — broken down by period, route mix, and (later)
> tag/key. This is the unique wedge: every competitor tracks *cost*; none proves *savings*.
> Pure arithmetic on token counts × a pinned price table — no model call (WF-ADR-0001).

## Context

Wayfinder's whole value proposition is *spend avoided*. The TUI already shows a per-session
savings tally (`SessionCost`), `/metrics` exposes counters (WF-ADR-0018), and gateway models
carry optional `cost_per_1k` metadata (WF-ADR-0017). What is missing is the durable, defensible
artifact a cost owner actually needs: a savings figure per day/week/month, with a baseline they
can audit, that survives a process restart and can be shown to finance. FinOps practice
explicitly prioritizes "quantifying business value," and the routing-as-cost-lever literature is
built entirely around the cheap-vs-frontier price gap — yet no adjacent product *leads* with a
savings number, because they are cost trackers, not routers.

## User Need

A FinOps/cost owner (and an individual developer) wants a durable, per-period answer to "how
much did routing save me, versus not routing at all?" — credible enough to put in a report,
reproducible, and attributable over time.

## Design

### The counterfactual

For each handled request, compute two figures from token usage and the price table:

- `realized_cost = (prompt_tokens + completion_tokens scaled per side) × price(chosen_model)`
- `baseline_cost = same token usage × price(baseline_model)`
- `savings = baseline_cost − realized_cost` (may be negative for an escalated turn — kept
  honest, not floored).

`baseline_model` is the "what you'd have paid without Wayfinder" reference — by default the
most-capable (most expensive) configured tier; configurable (e.g. a named always-on model).
With more than two tiers the baseline is a single declared frontier target, not the next tier
up, so the number answers "vs always-frontier."

### Auditability (the credibility hinge)

- **Pin the price table.** Persist the `cost_per_1k` values and a `price_table_version` +
  timestamp *with each aggregated record*, so a historical savings figure is reproducible even
  after prices change (WF-ADR-0017).
- **Label token provenance.** Prefer upstream `usage` token counts when the provider returns
  them; fall back to the ~4-chars/token estimate only when absent, and mark such records
  `estimated` so finance can see which rows are exact.

### Surfaces

- **TUI:** extend `/cost` from a session tally to a period view (today / 7d / 30d / all),
  showing realized, baseline, saved, % saved, and route mix.
- **Gateway endpoint:** `GET /v1/savings?period=…&group_by=…` returning the same aggregates as
  JSON (decision-only; no prompt text).
- **`/metrics`:** add `wayfinder_savings_usd_total`, `wayfinder_baseline_usd_total`,
  `wayfinder_realized_usd_total`, and per-route request counters, so existing dashboards/alerts
  can consume them (WF-ADR-0018).
- **Export:** a CSV/JSON dump per period for ingestion into a central FinOps tool.

### Storage

A bounded, on-disk aggregate store (extend the existing in-memory decision ring to optional
persistence; daily buckets keep it tiny). Self-hosted, no external service.

## Constraints

- **No model call, no network on the computation** (WF-ADR-0001): savings is arithmetic over
  token counts and a static table.
- **Metadata only** — savings needs token counts, route, model, timestamp, and (optionally) a
  tag; never prompt text. Preserves the WF-ADR-0011 posture by construction.
- **Honest by default:** negative savings are shown, estimates are labelled, the baseline is
  stated explicitly. A number finance can't trust is worse than no number.

## Rationale

Savings is the one cost story Wayfinder can tell that nobody else can, and it is the daily
reward (habit loop) for individuals and the ROI artifact for cost owners. Making it persisted,
per-period, and auditable converts a nice TUI flourish into a reason to open Wayfinder every day.

## Alternatives

- **Lean on an external FinOps tool** — but then Wayfinder's differentiation is invisible and
  the counterfactual (which only Wayfinder can compute, because only it knows the route taken)
  is lost.
- **Per-request only, no aggregation** — leaves the user to roll up the numbers; fails the
  "show finance a monthly figure" job.
- **Floor savings at zero** — flatters the tool and destroys credibility; rejected.

## Accessibility

All figures render as plain text / tables, legible without colour, with a machine-readable
JSON/CSV alongside. No GUI required.

## Open Questions

- Baseline definition with >2 tiers: a single declared frontier target (proposed) vs a
  configurable per-request counterfactual.
- Streaming responses: where to read token usage when the upstream reports it only in a final
  SSE event.
- How much attribution to bake in now vs defer to the Wave-2 tagging/keys work.

## Success Measures

- A cost owner can produce a per-period savings figure that reconciles with provider invoices
  within a small, documented margin.
- The reported number is reproducible from stored records after a price change (pinned table).
- The savings view is the most-visited surface in gateway deployments (instrumented).

## Related

WF-ADR-0001 (no model call), WF-ADR-0017 (cost metadata / price table), WF-ADR-0018 (`/metrics`),
WF-ADR-0011 (metadata only, never prompt text), WF-DESIGN-0008 (observability/dashboard this
feeds), WF-ROADMAP-0006 (daily-use roadmap, item 3).
