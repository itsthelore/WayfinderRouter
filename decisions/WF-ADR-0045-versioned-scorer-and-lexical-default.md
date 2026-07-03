---
schema_version: 1
id: WF-ADR-0045
type: decision
tags: [scoring, complexity, lexical, parity, schema-version, quality]
---

# WF-ADR-0045: A versioned scorer that can turn lexical signals on by default

## Status

Proposed

## Category

Technical

## Context

The best routing-quality operating point Phase Q found on
`benchmarks/dataset.jsonl` turns the four lexical features on. With
`reasoning_term_count=5.0, math_symbol_count=3.0, constraint_term_count=1.5`
(the documented opt-in recipe, `benchmarks.blind_eval.OPTED_IN_WEIGHTS`), the
cost-aware knee sits at **t=0.11: PGR 0.80, quality 0.875, skill +0.30** — the
strongest skill and efficiency of any config measured, and clearly above the
structural knee at a comparable ~50% cloud operating point (PGR 0.80 vs 0.60,
quality 0.875 vs 0.75). Held-out (5-fold, leakage-free) the lexical-on knee is
also the best structural-family config: **PGR mean 0.693, skill +0.262**, vs
the structural knee's 0.443 / +0.059.

The mechanism is exactly the weakness WF-ADR-0016 documented: the **hard-short**
bucket (6 short prompts with no structural tell) scores 0/6 under structure
alone because it never clears any cut. All 6 carry a lexical signal, so turning
the weights on lifts that bucket **0/6 → 6/6** — the dominant driver of the
+0.20 PGR.

**The tradeoff, stated up front and not buried:** turning lexical weights on
*regresses* the hard-short-structured bucket **1.00 → 0.25**. Raising the
lexical weights enlarges Σweights, which divides every score down, pushing some
structured prompts back below the cut. Net across 24 rows it is still a clear
win (PGR 0.60 → 0.80), but it is **not free** — it trades a structural bucket
for the lexical one. On a 24-row set each bucket is 4–6 prompts, so both the
0→6 rescue and the 1.00→0.25 regression are a handful of prompts moving.

Two settled decisions stand in the way, and this ADR must supersede both
explicitly rather than route around them:

- **WF-ADR-0016 ships the four lexical features at weight 0.0.** That was the
  right call *on the evidence it had*: a cross-provider double-blind
  (`benchmarks/blind_eval.py`) found the curated lexicon caught only ~20% of
  independently-authored hard prompts and lost to a length baseline — a curated
  keyword list detects an author's vocabulary, not difficulty in general. The
  Phase Q gains do **not** refute that; the 24-row set and the seed corpus share
  an author with the scorer, so their difficulty lives in the lexicon's
  vocabulary — exactly the ADR-0016 caveat, confirmed, not overturned. Turning
  the default on is therefore a genuine decision reversal that must own the
  generalization risk, not a bug fix.

- **WF-ADR-0043 froze the scorer's numerics and the JS parity byte-mirror.**
  The rebuilt scorer reproduces the legacy outputs exactly; `DEFAULT_WEIGHTS`,
  feature/summation order, rounding, and the emitted `features`/`score` with
  `schema_version` "3" are pinned, and mirrored byte-for-byte in
  `clients/shared` (enforced by `clients/shared/test/parity.mjs` and
  `tools/golden.py`). Changing a default weight changes scored bytes, which the
  parity gate exists to catch.

## Decision

Introduce a **versioned scorer** and, under a new version, ship calibrated
non-zero lexical weights as the default — never by mutating the frozen `"3"`
surface in place.

1. **`scorer_version` becomes an explicit, declared contract.** Add a scorer
   version to `RoutingConfig` and bump the emitted `schema_version` (from `"3"`
   to `"4"`). Version `"3"` remains the frozen WF-ADR-0043 surface: structural
   weights, lexical at 0.0, byte-identical. Version `"4"` carries the calibrated
   lexical-on defaults. Configs and clients select a version; nothing about
   `"3"` moves.

2. **The parity mirror tracks a declared version, not a single frozen
   formula.** The JS decision core and the golden corpus are regenerated
   **per version**. `"3"` corpus stays byte-identical (WF-ADR-0043 unbroken);
   `"4"` gets its own parity corpus and its own JS constants, re-ported **in
   lockstep** — the Python default-weight change and the JS byte-mirror land in
   the same change, or the parity gate fails by construction. This is the
   precise parity-gate implication: parity is not weakened, it is
   *version-scoped*. A mixed state (Python emits `"4"`, JS still mirrors `"3"`)
   is a hard parity failure, which is the desired guardrail.

3. **The default-weights reversal is version-gated and evidence-gated.**
   Version `"4"` may set the four lexical weights non-zero **only** if the
   calibrated set beats the length baseline held-out on the shipping evaluation
   (not the 24-row in-sample point estimate). WF-ADR-0016's "off by default"
   stands for version `"3"` forever; this ADR supersedes it *for version `"4"`*,
   on new, held-out, cross-provider evidence — restating, not ignoring, the
   generalization bar that set it to 0.0.

4. **The explicit off-by-default pin is re-baselined, not deleted.**
   `tests/test_complexity.py::test_lexical_signals_are_off_by_default` asserts
   the `"3"` behavior; it remains true for `"3"`. Version `"4"` gets its own
   assertions (lexical-on routing, the hard-short rescue, and the
   hard-short-structured regression documented as expected). The
   `dump_routing_toml` weight round-trip and TOML validation extend to carry
   `scorer_version`.

## Consequences

- **Positive.** The router routes on the difficulty it can actually see. On
  `benchmarks/dataset.jsonl`, projected default-path quality moves to **PGR
  ~0.80, quality ~0.875, skill +0.30** at t=0.11 — the measured best point.
- **Positive.** Parity is preserved *and* made evolvable: the byte-mirror stops
  being a single frozen formula and becomes a versioned contract, which unblocks
  every future scorer change (new features, non-linear terms) behind a version
  bump instead of a permanent freeze.
- **Negative / the tradeoff.** hard-short-structured regresses **1.00 → 0.25**
  in the measured config. Any shipped `"4"` weight set must either accept that
  trade with eyes open or be re-calibrated to soften it; the ADR ships no weight
  vector that has not been measured on both buckets.
- **Negative.** A version bump is real surface: two scorer versions to test, two
  parity corpora to maintain, a migration story for existing configs (default
  to `"3"` on read; opt into `"4"`). The JS re-port is mandatory and must land
  atomically with the Python change.
- **Small-sample caveat (governing).** The PGR 0.80 / +0.50-quality headlines
  are single-sample point estimates on 24 rows (held-out sd 0.16–0.31); the
  in-sample lexical numbers are flattered by the shared-author bias
  `blind_eval.py` exists to catch. These experiments justify *investing in* the
  versioned scorer — the mechanism and direction are clear and reproduced — and
  are nowhere near enough to *set the shipped weight vector*. That is gated on
  the RouterBench-scale, held-out, cross-provider run.

## Alternatives Considered

- **Bump `DEFAULT_WEIGHTS` in place under `schema_version` "3".** Rejected:
  it breaks WF-ADR-0043's parity freeze silently and strands every pinned `"3"`
  byte and the JS mirror. Superseding a byte contract requires a new declared
  version, not an in-place edit.
- **Keep lexical opt-in forever; document it harder.** Rejected as the quality
  strategy: per-config lexical activation already works today and breaks nothing
  — but it leaves the *default* experience on the structural blind spot the
  benchmark documents. The measured win is specifically about the default.
- **Non-linear or embedding-based scorer.** Out of scope and boundary-breaking
  (WF-ADR-0001). The versioned-scorer registry this ADR adds is the seam a
  future deterministic scorer would use, but v1 of it ships only calibrated
  linear lexical weights.

## Success Measures

- Version `"4"` default config scores **PGR ≥ 0.75** on
  `benchmarks/dataset.jsonl` with the hard-short bucket at 6/6, and the
  hard-short-structured regression is documented and asserted, not hidden.
  Prove: `python -m benchmarks.run` on a `scorer_version = "4"` default vs a
  `"3"` default.
- The `"3"` parity corpus regenerates byte-identical (`python tools/golden.py`;
  `node clients/shared/test/parity.mjs` green); the `"4"` corpus has its own
  Python↔JS parity, green, generated in the same change.
- `test_lexical_signals_are_off_by_default` still passes for `"3"`; new `"4"`
  assertions pin the lexical-on behavior.
- Shipped `"4"` weights beat the length baseline **held-out** on the shipping
  evaluation, not just in-sample on 24 rows.

## Related

- WF-ADR-0016 (lexical signals off by default — superseded *for version "4"*,
  its generalization bar restated)
- WF-ADR-0043 (scorer numerics + JS parity freeze — preserved for "3",
  extended to a versioned contract)
- WF-ADR-0017 / WF-ADR-0019 (calibration and configurable lexicon — the fit
  machinery the "4" weights come from)
- WF-ADR-0044 (calibrated default cut — independent operating-point work that
  does not touch the scorer; this ADR is the scorer half)
- WF-ROADMAP-0010 (Phase Q measurements and Phase X staging)
