---
schema_version: 1
id: WF-ADR-0002
type: decision
tags: [routing, tiers, configuration]
---

# WF-ADR-0002: Tiered Routing

## Status

Accepted

## Category

Architecture

## Context

The first router (WF-ADR-0001) was binary: one structural score compared to one
threshold, routing to `local` or `cloud`. Real deployments have more than two
destinations — a small local model, a mid local model, a big local model, a
cloud model — and want the *same* deterministic score to choose among them.

A single threshold cannot express that. We need an N-way generalization that
keeps the binary case behaving exactly as before, stays deterministic, and adds
no runtime cost beyond the score it already computes.

## Decision

The router maps the structural score to a model through ordered **tiers**.

- A tier is `(min_score, model)`. Tiers are sorted ascending by `min_score`, the
  first tier has `min_score = 0.0`, and `min_score` values are strictly
  increasing. The recommendation is the model of the highest tier whose
  `min_score` the score reaches.
- The binary local/cloud router is exactly the two-tier case
  `[(0.0, "local"), (threshold, "cloud")]`, so `score >= threshold` still routes
  up — existing behavior and the `--threshold` override are preserved.
- Tiers are configured in `wayfinder.toml` under `[[routing.tiers]]`, or built
  from a `threshold` for the default binary router. Selection is O(1) after
  scoring; still deterministic, still no model call.
- The recommendation is now a **configured model name**, not the literals
  `local`/`cloud`. Wayfinder names the destination; the caller maps the name to
  an endpoint and runs inference (the WF-ADR-0001 boundary holds).

## Consequences

### Positive

- One score, any number of destinations, with the binary router as a special
  case — no new mechanism, no new cost.
- Tier breakpoints are exactly what offline calibration produces (WF-ADR-0003),
  so the config is data-drivable.

### Negative

- A single scalar collapses everything onto one "heaviness" axis; routing among
  *specialized* models that differ in kind, not difficulty, needs the classifier
  (WF-ADR-0003), not tiers.
- The JSON contract changed (`threshold` → `tiers`, plus a `mode` field), so the
  output schema_version moved to 2. Acceptable pre-1.0.

## Alternatives Considered

### Keep the binary threshold only

Stay two-destination forever.

#### Disadvantages

- Cannot express the common small/mid/large-local + cloud ladder.

### Jump straight to a multi-dimensional classifier

Skip tiers; only ship the classifier.

#### Disadvantages

- Overkill when difficulty really is ordinal; tiers are trivially inspectable and
  need no fitting. Tiers and the classifier are complementary, so we ship both.

## Success Measures

- `[(0.0, "local"), (t, "cloud")]` reproduces the pre-tier binary behavior
  exactly, including the `--threshold` override.
- A three-tier config routes a prompt to the highest band its score reaches,
  deterministically.
