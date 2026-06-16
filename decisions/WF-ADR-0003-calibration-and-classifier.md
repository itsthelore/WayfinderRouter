---
schema_version: 1
id: WF-ADR-0003
type: decision
tags: [calibration, classifier, determinism]
---

# WF-ADR-0003: Calibration and the Classifier Mode

## Status

Accepted

## Category

Architecture

## Context

The structural score is a *proxy*: whether it tracks "this prompt needs the
bigger model" is empirical and per-workload. Picking a threshold (or tier
breakpoints, or feature weights) by hand is guesswork. Teams need a principled,
reproducible way to derive the routing boundary from data — and, for models that
differ in *kind* rather than difficulty, a router that is not limited to one
ordinal axis.

Both must hold the line WF-ADR-0001 drew: Wayfinder recommends, never invokes a
model, and every routing signal is a deterministic, offline function of the
query. Calibration may use a model *offline to label data* (that is the caller's
oracle), but the shipped runtime calls nothing.

## Decision

Add an offline `wayfinder calibrate` command and a classifier runtime mode.

- **Calibrate** reads a labeled JSONL dataset (`{"text", "label"}`) and emits a
  `wayfinder.toml` fragment. Three modes:
  - `threshold` — binary: sweep the cut maximizing separation accuracy between
    two labels; emit a two-tier config.
  - `tiers` — ordinal multi-class: order labels by mean score, sweep each
    adjacent breakpoint; emit an N-tier config (WF-ADR-0002).
  - `classifier` — fit a multinomial-logistic model; emit a classifier config.
- **Classifier mode** gives each candidate model a linear score over the *same*
  normalized feature vector the scalar score uses (`SATURATION` is the one
  feature transform), and `argmax` picks the model. Inference is a few dot
  products — deterministic, no model call. It takes precedence over tiers when
  configured.
- **Training is deterministic**: zero initialization, full-batch gradient descent
  in fixed data order, fixed iteration count — no randomness, so the same dataset
  yields the same weights. (Cross-platform float arithmetic is the only wrinkle,
  the same one the scorer has; each fit is internally reproducible.)
- **Stdlib only**: the fit is hand-written (`math` + lists), so the package keeps
  `dependencies = []`. No numpy, no scikit-learn.
- The boundary holds: `calibrate` and the runtime never call a model; labels come
  from the caller's oracle (tests, production signals, or an offline judge).

## Consequences

### Positive

- The routing boundary becomes data-driven and reproducible: one command, a
  config fragment to drop in, the runtime untouched.
- The classifier handles "different kind, not harder" routing that a single
  ordinal score cannot, while staying a deterministic linear model.
- Sharing `normalized_features` means calibration never invents a feature scale
  the runtime does not also apply.

### Negative

- Pure-Python full-batch gradient descent is slow on very large datasets
  (calibration is offline, so this is acceptable; `--iterations` is tunable).
- A classifier config is less glanceable than tier breakpoints — fitted weights,
  not human-set cuts. Tiers remain the inspectable option.

## Alternatives Considered

### Hand-tuned thresholds only

Make users guess the cut.

#### Disadvantages

- Guesswork; no reproducible link from data to boundary.

### Depend on numpy / scikit-learn for the fit

Use a library optimizer.

#### Disadvantages

- Breaks the stdlib-only, zero-dependency principle (WF-ADR-0001) for an offline
  step a small hand-written solver covers.

### An LLM-judge router at runtime

Ask a model how to route.

#### Disadvantages

- Non-deterministic, costs a model call to decide on a model call, and crosses
  the WF-ADR-0001 boundary. Calibration may use a judge *offline*; the runtime
  must not.

## Success Measures

- `calibrate` on a separable labeled set reports accuracy 1.0 and its emitted
  config round-trips: written to `wayfinder.toml` and loaded, it routes the same
  prompts the same way.
- Re-running `calibrate` on the same dataset yields byte-identical output.
- No `calibrate` or runtime path imports a model SDK or makes a network call.
