---
schema_version: 1
id: WF-ADR-0016
type: decision
tags: [scoring, complexity, calibration, benchmark, accuracy]
---

# WF-ADR-0016: Lexical Difficulty Signals in the Deterministic Scorer

## Status

Accepted

## Category

Technical

## Context

The scorer reads seven purely *structural* features (`FEATURE_ORDER`: word count,
headings and their depth, list items, links, code blocks, table rows). A short but
hard prompt — "Prove √2 is irrational" — has no structure, so it scores near zero
and routes local.

The v0.1.6 benchmark (WF-ADR-0015) put a number on the gap: the `hard-short`
bucket scores **0.00 accuracy**, and a tuned length baseline (PGR 0.67) beats
Wayfinder (PGR 0.60) on that illustrative set. This is the weakness a skeptic names
first, and structure alone cannot close it: calibration can only reweight features
that already exist, it cannot recover signal the features never captured.

Any fix must stay inside the WF-ADR-0001 boundary: no model call, deterministic,
offline. So the question is whether *deterministic* signals exist that separate
short-hard from short-easy. They do — in the words themselves.

## Decision

Add deterministic **lexical** difficulty signals to the scorer, scanned by curated
keyword and regex passes over the prompt body. Still pure text, still no model
call. New features appended to `FEATURE_ORDER`:

- `reasoning_term_count` — a curated lexicon of hard-task verbs and nouns (prove,
  derive, optimize, refactor, theorem, invariant, complexity, concurrency,
  trade-off, …), word-boundary, case-insensitive.
- `math_symbol_count` — density of math and logic glyphs and LaTeX-ish tokens
  (∑ ∫ √ ≤ ≠ ∂ ∀ ∃, `\frac`, inline `$…$`, `^`).
- `constraint_term_count` — multi-constraint markers (must, without, only, ensure,
  "such that", "subject to").
- `question_count` — interrogative markers.
- (optional) `inline_code_token_count` — backtick spans and `snake_case` /
  `CamelCase` / `func()` identifiers that signal technical specificity without a
  fenced block.

Because the scorer is `FEATURE_ORDER`-driven, the new features flow through
`normalized_features`, `scalar_score`, the classifier, `explain_score`, and the
`config.py` validation and `dump_routing_toml` round-trip with **no structural
change** — only the three constant tables (`FEATURE_ORDER`, `DEFAULT_WEIGHTS`,
`SATURATION`) and `extract_features` grow.

Two sub-decisions are deferred to implementation, taken with the maintainer:

- **Default weights.** Either ship non-zero defaults that beat the length baseline
  *without* regressing the easy buckets (a behaviour change → minor version bump and
  a loud changelog), or ship the features with `0.0` default weight (purely
  additive, calibration-only) if the benchmark cannot clear that bar cleanly. The
  benchmark is the arbiter; we publish no improvement we did not measure.
- **JSON contract.** Keep `schema_version` `"2"` (the new feature keys are additive)
  or bump to `"3"` to signal the expanded set. Lean toward `"3"`.

## Consequences

### Positive

- Attacks the one documented, measured weakness directly, and the benchmark proves
  whether it worked before any claim is made.
- Stays fully deterministic and offline; the boundary is untouched.
- Small blast radius: `config.py` needs no new parsing, the classifier and explain
  surfaces adapt for free.

### Negative

- Turning on non-zero defaults re-routes existing deployments on `pip install -U`;
  mitigated by the version bump and changelog, or avoided by the `0.0`-default path.
- The lexicon is English-centric and, in principle, game-able; it is kept small and
  documented in this ADR rather than open-ended.

### Risks

- False positives on easy prompts containing a trigger word ("prove you read
  this"). Mitigation: conservative saturations and a benchmark guard that the easy
  buckets do not regress.
- Some short-hard prompts carry no lexical tell ("what is the 50th digit of pi?");
  the README caveat that the score is a proxy, not a verdict, stays.

## Alternatives Considered

### Call a small model or embedding to judge difficulty

#### Disadvantages

- Breaks the entire boundary — a model call, non-determinism, latency, and a key on
  the scored path. Not doing this is the whole point of Wayfinder.

### Leave the scorer as-is and rely only on calibration

#### Disadvantages

- Calibration reweights existing features; it cannot recover signal the features
  never captured. Short-hard stays at zero.

## Success Measures

- Benchmark `hard-short` accuracy rises materially from 0.00; overall PGR is at or
  above the length baseline, with the easy buckets unregressed.
- Determinism and the WF-ADR-0001 boundary preserved; `config.py` needs no new
  parsing logic.

## Related Decisions

- WF-ADR-0001 (the deterministic, no-model-call core this preserves)
- WF-ADR-0002 / WF-ADR-0003 (the tiers and classifier that consume the features)
- WF-ADR-0015 (the benchmark that gates the default-weights decision)
