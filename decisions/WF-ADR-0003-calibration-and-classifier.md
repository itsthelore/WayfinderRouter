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
- **The solver is L2-regularized Newton/IRLS** (amended from the initial
  gradient descent): a gradient and Hessian accumulated in fixed data order,
  solved by Gaussian elimination with partial pivoting, stopped on a tolerance.
  Zero initialization, no randomness — the same dataset yields the same weights.
  The L2 ridge keeps the Hessian positive-definite, which both makes the solve
  well-posed on perfectly separable data (where unregularized logistic weights
  diverge) and bounds the fitted weights. The feature space is tiny (7 features ×
  a few classes), so it converges in a handful of iterations regardless of
  dataset size. (Cross-platform float arithmetic is the only wrinkle, the same one
  the scorer has; each fit is internally reproducible.)
- **Stdlib only by default**: the solver is hand-written (`math` + lists), so the
  package keeps `dependencies = []`. No numpy, no scikit-learn in the core. A
  future optional `wayfinder[fast]` extra may use numpy for the linear-algebra
  step, lazily imported with a pure-Python fallback; the two paths need not be
  byte-identical, only each internally deterministic, and the emitted config
  records which produced it. (scikit-learn is rejected — its solvers add seed and
  tolerance nondeterminism.)
- **Online calibration**, when a gateway or UI later observes routing outcomes,
  is a deterministic *batch replay*: outcomes append to a log, and recalibration
  re-fits on the full, deterministically-ordered set — never streaming SGD, which
  cannot be made reproducible. This is recorded as accepted direction, not built
  now.
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

- The pure-Python Hessian solve is O(params³) per iteration; fine for this tiny
  parameter space, but a very large *number of models* would eventually motivate
  the `wayfinder[fast]` extra (calibration is offline, so this is rarely urgent).
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
