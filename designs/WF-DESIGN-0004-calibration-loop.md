---
schema_version: 1
id: WF-DESIGN-0004
type: design
tags: [calibration, evaluation, routing, cli, drift, honesty]
---

# WF-DESIGN-0004: One-Command Calibration Loop (cross-validated lift + drift)

## Status

Proposed

> Turn calibration from a manual, unmeasured knob into a single guided command:
> `wayfinder calibrate --from logs.jsonl` fits weights and threshold on *your* traffic and
> prints an **honest, cross-validated** report of whether it beats baselines — and only then
> recommends turning on the lexical cues. A `drift` check warns when live traffic diverges
> from what you calibrated on. This is the moat: "tune it on your own data", made trustworthy.

## Context

The README already invites "Calibrate on your data" and "Learn from feedback", and the core
supports fitted weights and a fitted threshold (WF-ADR-0002/0003). What is missing is a
**single, guided, measured** workflow. Two things make today's tuning risky: a user cannot
easily tell whether their tuning *helped* or merely *overfit*, and the project's most
defensible honesty claim — the lexical cues ship off by default because a double-blind eval
showed they do not generalise — only becomes a *strength* if a user can re-test that claim on
their own vocabulary and see the lift (or its absence). The benchmark harness already computes
accuracy/cost against honest baselines and an oracle; calibration should reuse it, not
reinvent it.

## User Need

Point the tool at my logs, get back (a) a fitted config and (b) an honest, cross-validated
report of whether it beats the baselines **on my data** — and be warned later when my live
traffic has drifted far enough from the calibration set that the fit is stale.

## Design

### Input: a tolerant labelled dataset

JSONL rows `{prompt, label}` where `label` is the tier that handled the prompt well (or the
preferred model name). Ship a converter that derives a starter dataset from gateway logs plus
the chat's thumbs/feedback signal, so labels are cheap to produce.

### `wayfinder calibrate --from logs.jsonl`

Fit weights and threshold using the existing linear / multinomial model, under **k-fold
cross-validation**, then emit:

- a **fitted config** (drop-in `wayfinder-router.toml` fragment), and
- a **report** — a table of accuracy and estimated cost against honest baselines
  (word-count-only, always-local, always-cloud) and the **oracle** ceiling, with CV
  mean ± std, in the same plain tone as the README's "How it compares".

Crucially: the report recommends **enabling the lexical cues only if they beat the structural
baseline out-of-fold**. That is the principled, per-user version of "off by default" — the
claim is re-tested on the user's vocabulary rather than asserted.

### `wayfinder drift --baseline cal.json --against recent.jsonl`

Per-feature distributional distance between the calibration set and recent traffic, with a
simple flag and a "consider re-calibrating" nudge when a feature has moved materially.

All of it is offline and deterministic, and reuses the benchmark harness.

## Constraints

- **Offline and deterministic**; reuses the existing benchmark/eval code rather than a new
  engine. The fit is small and linear.
- **Core stays import-light** (WF-ADR-0001): calibration is a CLI/dev path and may sit behind
  an extra; it must not pull heavy deps into the base wheel or the scorer import path.
- Fitted artifacts are plain, inspectable config — no opaque pickles.

## Rationale

This is the direct, credible answer to "it misses semantic difficulty": *calibrate it to your
domain — here is the cross-validated lift, against honest baselines, with overfitting checked.*
The CV, the baselines, and the drift check are what make it trustworthy rather than a knob you
turn and hope. It is also the most on-brand differentiator: deterministic, self-hosted, and
measured, where the hosted/learned routers ask you to trust their platform.

## Alternatives

- **Online / automatic learning from feedback** — deferred: it needs guardrails (poisoning,
  feedback bias) and a story for non-determinism; the offline, reproducible loop comes first.
- **A hosted calibration service** — off-brand (a server, your prompts leave the box).
- **Status quo (hand-edit weights)** — unmeasured; the user cannot tell help from overfit.

## Accessibility

The report is plain text / a markdown table, legible without colour, with a machine-readable
JSON alongside for scripting. No step requires a GUI.

## Open Questions

- **Label acquisition is the hard part** — how much can be auto-derived from gateway logs +
  thumbs feedback versus hand-labelled? A good converter is most of the battle.
  *(Partly resolved: WF-ADR-0037 adds an automated sufficiency judge — `wayfinder-router judge`
  runs two tiers and auto-labels "was the cheaper one good enough?", gated by judge-vs-gold κ and
  the same cross-validated lift below before any config is trusted. Still needs a small human gold
  set; an LLM judge is a planned drop-in.)*
- CV fold strategy for small datasets (few hundred rows) without misleading variance.
- How to present the cost-vs-accuracy tradeoff — a single recommended threshold, or a small
  frontier the user picks a point on.

## Success Measures

- A **worked example in the repo**: sample logs in, a report out that shows measurable lift
  (or honestly shows none), end to end.
- A documented `calibrate` → `drift` round-trip.
- The lexical-on recommendation fires **only** when the cues generalise on held-out folds.

## Related

WF-ADR-0002/0003 (scored / multi-tier model), WF-DESIGN-0003 (its confidence mapping can be
calibrated here), the benchmark harness, WF-ROADMAP-0005.
