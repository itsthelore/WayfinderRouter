---
schema_version: 1
id: WF-DESIGN-0005
type: design
tags: [routing, evaluation, features, weights, ablation, honesty, benchmark]
---

# WF-DESIGN-0005: Structural Feature Audit (does the 7-feature score beat word-count alone?)

## Status

Proposed

> A pre-registered ablation: does the default 7-feature structural score actually earn its
> complexity, or is `word_count` alone just as good? Run it on the shipped datasets with the
> existing harness, then **prune, re-weight, or justify** each feature by the result — and
> publish the report even if the honest answer is "simpler is as good." Reuses the benchmark
> methodology (WF-ADR-0015); changes no scoring math.

## Context

The router's default scalar score is a weighted sum of seven structural features
(`word_count` 3.0, `list_item_count` 2.0, `heading_count`/`code_block_count` 1.5,
`table_row_count`/`link_count`/`max_heading_depth` 1.0; `complexity.py:61-73`), saturated and
normalised. Three findings suggest that machinery may not be pulling its weight:

- On the illustrative set, the **plain length baseline beats the full structural score**
  (PGR 0.67 vs 0.60; `benchmarks/results.md`).
- On RouterBench (36,497 graded prompts) the structural router's **skill is ≈ random**
  (−0.038 ± 0.003 held-out), and structural heaviness *anti-correlates* with the real frontier
  gap (−0.512 across 86 buckets; `benchmarks/routerbench-results.md`,
  `benchmarks/calibration-eval.md`).
- `word_count` already carries the **largest default weight (3.0 of 11.0)** and saturates at
  400 words, so the score is length-dominated by construction — the other six features may be
  adding variance, not signal, outside the long/structured regime Wayfinder claims to serve.

No artifact has ever measured the **marginal contribution of each feature**. The weights are
hand-set (WF-ADR-0001/0002), not audited. Before anyone adds, re-weights, or removes a
feature, we should know which ones earn their place — and in which traffic regime. An audit
that might *delete* features is the most on-brand move this project can make: it is the same
honesty that put the lexical cues at weight 0.0 (WF-ADR-0016).

## User Need

A maintainer (and a skeptical evaluator) wants a reproducible answer to "do the extra features
beat `word_count` alone, and where?" — so the default weights are either **simplified** (less
surface area, same skill) or **justified feature-by-feature** with numbers, not asserted.

## Design

### Pre-registered hypothesis

**H0:** the full 7-feature structural score does **not** beat `word_count`-only on skill / PGR,
at each dataset's cost-aware knee, across the shipped datasets. The audit is designed to be
able to *reject the project's own default* — that is the point.

### Method (reuse the existing harness)

Drive `benchmarks/harness.py` (`sweep` → `knee`, reporting `skill`, `PGR`, `cost_saved`,
latency). Weights are already config-driven, so every arm is a weight vector — no scoring-math
change. Arms:

1. **full** — shipped default weights.
2. **word_count-only** — all other structural weights 0.
3. **leave-one-out** — seven arms, each zeroing one feature, to read its marginal skill.
4. **add-one-in** — `word_count` plus exactly one other feature, to read standalone lift.
5. (optional) **uniform** and **a small re-weighting** as sanity points.

Run every arm on **all shipped datasets** — illustrative, blind cross-provider, and RouterBench
— at the cost-aware knee, with held-out splits where the dataset is large enough (RouterBench),
reporting mean ± std so "within noise" is decidable rather than eyeballed. A thin ablation
driver (`benchmarks/feature_audit.py`) emits a checked-in markdown report in the same style as
`calibration-eval.md`.

### Pre-registered decision rule

- **word_count-only within noise of full (everywhere)** → simplify: drop the dead features
  from the default weights (keep them *computed and reported* per WF-ADR-0016 precedent, just
  weighted 0), shrinking the default to the signal that actually pays.
- **a feature consistently hurts** (negative leave-one-out skill) → drop or down-weight it.
- **full clearly beats length on the long/structured (agentic/document) regime** → keep it,
  and document *per feature* the regime where it earns its weight, so the claim is scoped.

Any change to default weights is a behaviour change → recorded in a follow-up **ADR** and a
golden-test update; the audit itself ships only the report and the driver.

## Constraints

- **Offline, deterministic, stdlib-only** (WF-ADR-0001); reuses the harness (WF-ADR-0015), adds
  no dependency.
- **No change to the scoring formula or feature extraction** — only weight vectors vary. The
  audit cannot, by construction, alter routing for existing configs.
- Honest baselines (`length_threshold`, always-local/cloud, oracle, stable-random) and the
  `skill` metric are the yardstick — no bespoke metric that could flatter the default.

## Rationale

The project's entire credibility is *not overclaiming*. A measured admission that six features
add little outside the long/structured regime — or a measured justification that they do —
both strengthen the pitch. It also de-risks every future router change: you cannot sensibly
add or re-weight a feature without first knowing the marginal value of the ones you have.

## Alternatives

- **Keep the current weights unaudited** (status quo) — leaves "7 features" an unmeasured claim
  a reviewer can puncture in one `sweep`.
- **Add more structural features instead** — rejected: the ceiling is semantic, not feature
  count (RouterBench anti-correlation); more structure won't move skill and risks the same
  curated-signal trap as the lexicon.
- **Replace the score with `word_count` outright, no audit** — premature; the long/structured
  regime may genuinely use the other features. Measure first.

## Accessibility

The deliverable is a plain-text / markdown table (per-arm skill ± std, per-dataset), legible
without colour, with a machine-readable JSON alongside. No GUI; one command reproduces it.

## Open Questions

- **Regime coverage**: do the shipped datasets fairly represent the agentic/document traffic
  Wayfinder claims to serve, or do we need a new long/structured eval set to test that claim
  honestly (the regime where the extra features *should* pay)?
- A defensible significance test for skill differences at small N (the illustrative set is 24
  prompts; bootstrap CIs?).
- Whether "computed but weighted 0" (the WF-ADR-0016 pattern) or outright removal from
  `FEATURE_ORDER` is the right shape for any feature the audit retires.

## Success Measures

- A **pre-registered, reproducible ablation report** checked into `benchmarks/`, runnable with
  one command, showing per-feature marginal skill across all datasets with mean ± std.
- A **decision recorded as an ADR**: default weights either provably simplified (fewer weighted
  features, skill within noise) or justified per feature with the regime they pay in.
- The README's "7 structural features" framing is updated to match whatever the audit finds —
  no claim the numbers don't support.

## Related

WF-ADR-0001 (deterministic core / the hand-set weights being audited), WF-ADR-0002 (tiered
routing), WF-ADR-0015 (benchmark methodology this reuses), WF-ADR-0016 (the off-by-default
precedent for "computed but weighted 0"), the benchmark harness, WF-DESIGN-0004 (calibration —
the audit's findings feed its default config), WF-ROADMAP-0005.
