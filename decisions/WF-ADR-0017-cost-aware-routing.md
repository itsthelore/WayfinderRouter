---
schema_version: 1
id: WF-ADR-0017
type: decision
tags: [routing, cost, calibration, config, gateway]
---

# WF-ADR-0017: Cost-Aware Routing and Calibration

## Status

Accepted

## Category

Technical

## Context

Routing is expressed as a raw `0.0`–`1.0` threshold (or tiers). But the thing an
operator actually optimizes is cost versus quality: "spend as little as possible
while staying good enough." The v0.1.6 benchmark (WF-ADR-0015) already reasons in
exactly those terms — it reports cost savings and picks a cost-aware knee
(`PGR × cost_savings`) — yet the config and the `calibrate` path have no notion of
cost at all. The user translates a threshold into money by hand.

## Decision

Two additive pieces, scoped deliberately tight:

1. **Cost metadata.** An optional `cost` on `Tier` (an additive field on the frozen
   dataclass) and `cost_per_1k` on `[gateway.models.<name>]`. Purely
   informational: surfaced in the dashboard and the metrics endpoint
   (WF-ADR-0018), and consumed by calibration. It does **not** touch per-request
   scoring.
2. **A cost-aware calibration objective.** `wayfinder-router calibrate --objective
   cost-quality --target-savings X` (or `--max-cost`) selects the threshold or
   tiers that maximize quality subject to a cost ceiling — the benchmark knee
   logic, moved into `calibrate`.

The scored path stays deterministic and free. Cost only affects *where the cut is
placed* (at calibration time) and *what is reported*; it never enters the
per-request decision. The WF-ADR-0001/0004 boundary holds.

Explicitly out of scope for v1: live spend metering and token-level costing (which
would need a tokenizer dependency and per-provider price tables). v1 uses a flat
per-request or per-1k-words cost so the harness stays deterministic and
dependency-free. Live metering is a separate future decision.

## Consequences

### Positive

- Ties the cost-savings story together and matches how the benchmark already
  thinks; the threshold becomes meaningful in money terms.
- Calibration can target a savings goal instead of a bare score.

### Negative

- More config surface, and the cost numbers are estimates, not billed truth.

### Risks

- Scope creep toward a billing system. Mitigation: this ADR fences v1 to config
  metadata plus a calibration objective; anything live is a later decision.

## Alternatives Considered

### Route by live token cost at request time

#### Disadvantages

- Needs a tokenizer (a dependency) and per-provider price tables, is
  non-deterministic, and pulls billing concerns onto the scored path. Rejected.

### Do nothing; the threshold is enough

#### Disadvantages

- Leaves the cost story implicit and makes the operator translate threshold into
  savings by hand — the exact gap the benchmark already exposes.

## Success Measures

- A user can express per-model cost and calibrate to a savings target, and the
  chosen cut reproduces the benchmark knee on their own data.
- The scored path is unchanged and still deterministic.

## Related Decisions

- WF-ADR-0015 (the benchmark cost-quality framing this reuses)
- WF-ADR-0002 / WF-ADR-0003 (the tiers and classifier a cut is placed on)
- WF-ADR-0001 / WF-ADR-0004 (the boundary preserved)
- WF-ADR-0018 (the metrics endpoint that surfaces the cost counters)
