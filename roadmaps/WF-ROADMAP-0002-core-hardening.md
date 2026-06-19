---
schema_version: 1
id: WF-ROADMAP-0002
type: roadmap
tags: [v0.1.7, v0.2.0, v0.2.1, scoring, cost, observability, core]
---

# Roadmap: Core hardening (v0.1.7 → v0.2.1)

## Status

Planned

## Context

The v0.1.0–v0.1.6 arc made Wayfinder adoptable and credible: streaming and gateway
hardening (WF-ADR-0013), a visible control surface (WF-ADR-0014), a reproducible
benchmark (WF-ADR-0015), and a clean README. That work made the product easy to run
and honest about itself — it did not make the router *smarter* or easier to
*operate*. The benchmark surfaced the real weakness (short-but-hard prompts), and
production use surfaces the missing observability.

This roadmap sharpens the engine itself, in three independent improvements, each
inside the WF-ADR-0001 boundary, sequenced by risk — before the v0.2.0
`wayfinder-chat` fork (WF-ROADMAP-0001) consumes the gateway.

## Outcomes

- The gateway is observable in production, not just glanceable.
- The scorer separates short-but-hard prompts from short-easy ones, closing the
  benchmark's documented hole.
- Routing can be calibrated to a cost target, not just a bare score.

## Initiatives

Sequenced by risk; each is independent and ships on its own version line.

### Initiative 1 — Gateway metrics endpoint (`wayfinder-router` v0.1.7, WF-ADR-0018)

Lowest risk, additive, independent. A hand-rolled Prometheus `GET /metrics`
endpoint, incremented at the existing decision hook in `chat_completions`,
metadata-only. Ship first.

### Initiative 2 — Lexical difficulty signals (`wayfinder-router` v0.2.0, WF-ADR-0016)

New deterministic lexical features (reasoning terms, math symbols, constraints,
questions) attack the benchmark's short-hard hole, flowing through the existing
`FEATURE_ORDER`-driven machinery. Outcome (see WF-ADR-0016 amendment): a
cross-provider double-blind test showed the lift does not generalize — the lexicon
catches an author's vocabulary, not difficulty — so the features ship **opt-in, at
weight 0.0**, and default routing is unchanged from v0.1.x. Cost-aware routing
(Initiative 3) became the v0.2.0 headline instead.

### Initiative 3 — Cost-aware routing (`wayfinder-router` v0.2.1, WF-ADR-0017)

Optional per-model cost metadata plus a cost-aware `calibrate` objective that
reuses the benchmark knee. Builds on the benchmark's cost framing and on Initiative
1's cost counters.

## Constraints

- Every item preserves WF-ADR-0001: no model call, no key or network on the scored
  path, deterministic.
- Initiative 2 must not regress the benchmark's easy buckets; the benchmark is the
  gate, and no improvement is claimed that was not measured.
- Initiative 3 stays config plus calibration; no live billing or tokenizer in v1.

## Non-Goals

- The `wayfinder-chat` fork (WF-ROADMAP-0001), which is deferred.
- Any model call or semantic judge in the core.

## Success Measures

- v0.1.7: `GET /metrics` scrapes clean and leaks no prompt text.
- v0.2.0: benchmark `hard-short` rises from 0.00, overall PGR is at or above the
  length baseline, and the easy buckets do not regress.
- v0.2.1: a user calibrates to a savings target and the chosen cut reproduces the
  benchmark knee on their data.

## Risks

- **Behaviour change (Initiative 2):** new default weights re-route existing
  deployments. Mitigation: benchmark gating, a minor version bump, a loud
  changelog, or the calibration-only `0.0`-default path.
- **Scope creep (Initiative 3):** cost work drifting toward a billing system.
  Mitigation: WF-ADR-0017 fences v1 to config plus a calibration objective.

## Related Decisions

- WF-ADR-0016, WF-ADR-0017, WF-ADR-0018 (the three decisions this roadmap sequences)
- WF-ADR-0015 (the benchmark — the gate for Initiative 2 and the framing for
  Initiative 3)
- WF-ADR-0001 (the deterministic boundary preserved throughout)
- WF-ROADMAP-0001 (the v0.2.0 `wayfinder-chat` fork that follows this work)
