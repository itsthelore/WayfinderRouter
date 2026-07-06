"""THE FROZEN ORACLE GATE for the product detectors (WF-DESIGN-0013 §4, Contracts inv. 9).

Spec-first (additive-only, WF-ADR-0044). ``wayfinder_router.detectors`` does not exist
yet, so this file errors at collection until it is built; the benchmark-side imports and
the committed-JSON shape it depends on are real and were verified at build time.

WHAT THE FROZEN ORACLE IS
    ``benchmarks/detector-validation-results.json`` is the committed, frozen oracle: the
    per-detector confusion table and micro/macro rollup that the reference
    ``benchmarks/detectors.DETECTORS`` produce on ``benchmarks.detector_validation
    .full_corpus``. This file first *re-derives it at run start* from the benchmark
    detectors and asserts it reproduces the committed JSON byte-for-value (the oracle is
    verified reproduced before it is used as a floor), then asserts the PRODUCT detector
    set meets that same floor with zero regression, per name.

Contracts pinned (WF-DESIGN-0013 §4 / invariant 9):
  - product ``DETECTORS`` reproduce micro precision >= 0.812 and micro recall >= 0.867,
    and per-detector precision/recall >= the committed table — "zero regression".

AMBIGUITY RESOLVED (recall threshold):
    The committed frozen micro recall is exactly 0.8666666..., of which the design's
    "0.867" is the 3-decimal display (``detector-validation-results.md``). Asserting the
    RAW float ``>= 0.867`` would fail against the very oracle it must reproduce. Strictest
    faithful reading: the true no-regression gate is "product rollup >= the committed JSON
    floor" (exact), and the design's published 3-decimal thresholds are asserted via
    ``round(x, 3)``. Both are checked below.
"""

from __future__ import annotations

import json
from pathlib import Path

import benchmarks.detectors as bench
from benchmarks.detector_validation import evaluate, full_corpus, micro_macro
from wayfinder_router.detectors import DETECTORS as PRODUCT_DETECTORS

# Mirror the committed-test idiom (tests/test_detector_validation.py): the corpus and the
# frozen oracle JSON live under <repo>/benchmarks/, one level up from tests/.
BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
CORPUS = BENCH_DIR / "detector-corpus.jsonl"
ORACLE_JSON = BENCH_DIR / "detector-validation-results.json"

# Published design thresholds (WF-DESIGN-0013 §4 / invariant 9), as 3-decimal displays.
MICRO_PRECISION_FLOOR = 0.812
MICRO_RECALL_FLOOR = 0.867


def _oracle() -> dict:
    return json.loads(ORACLE_JSON.read_text(encoding="utf-8"))


def test_committed_json_has_the_expected_shape():
    # The frozen oracle's structure is what the no-regression assertions index into.
    j = _oracle()
    assert set(j) == {"detectors", "rollup"}
    assert set(j["rollup"]) == {
        "micro_precision", "micro_recall", "micro_f1",
        "macro_precision", "macro_recall", "macro_f1",
    }
    for name, entry in j["detectors"].items():
        assert set(entry) == {"tp", "fp", "fn", "tn", "precision", "recall", "f1"}, name


def test_frozen_oracle_reproduced_at_run_start():
    # Gate precondition: the benchmark reference detectors, run on full_corpus now, must
    # reproduce the committed JSON exactly — otherwise the floor is not trustworthy.
    j = _oracle()
    stats = evaluate(full_corpus(CORPUS), bench.DETECTORS)
    for name, s in stats.items():
        e = j["detectors"][name]
        assert (s.tp, s.fp, s.fn, s.tn) == (e["tp"], e["fp"], e["fn"], e["tn"]), name
        assert s.precision == e["precision"] and s.recall == e["recall"], name
    roll = micro_macro(stats)
    for k, v in j["rollup"].items():
        assert roll[k] == v, k


def test_product_detectors_reproduce_benchmark_evaluation():
    # §4: product patterns/validators are byte-identical, so evaluating the product set on
    # full_corpus yields the SAME per-detector confusion counts as the benchmark set.
    items = full_corpus(CORPUS)
    prod = evaluate(items, PRODUCT_DETECTORS)
    ref = evaluate(items, bench.DETECTORS)
    assert set(prod) == set(ref)
    for name in ref:
        p, r = prod[name], ref[name]
        assert (p.tp, p.fp, p.fn, p.tn) == (r.tp, r.fp, r.fn, r.tn), name


def test_product_micro_precision_and_recall_meet_gate():
    # Invariant 9: micro P >= 0.812 and micro R >= 0.867. Checked two ways:
    #  (a) against the committed frozen floor as raw floats (the true no-regression gate),
    #  (b) against the design's published 3-decimal thresholds via round().
    roll = micro_macro(evaluate(full_corpus(CORPUS), PRODUCT_DETECTORS))
    floor = _oracle()["rollup"]
    assert roll["micro_precision"] >= floor["micro_precision"]
    assert roll["micro_recall"] >= floor["micro_recall"]
    assert round(roll["micro_precision"], 3) >= MICRO_PRECISION_FLOOR
    assert round(roll["micro_recall"], 3) >= MICRO_RECALL_FLOOR


def test_product_per_detector_precision_recall_no_regression():
    # Invariant 9: per-detector precision/recall >= the committed table, every name.
    j = _oracle()
    prod = evaluate(full_corpus(CORPUS), PRODUCT_DETECTORS)
    assert set(prod) == set(j["detectors"])
    for name, s in prod.items():
        e = j["detectors"][name]
        assert s.precision >= e["precision"], f"{name} precision regressed"
        assert s.recall >= e["recall"], f"{name} recall regressed"


def test_product_rollup_equals_committed_oracle_exactly():
    # Byte-identical patterns => the product rollup is not merely >= but EQUAL to the
    # frozen oracle: reproduction "by construction" (§4).
    roll = micro_macro(evaluate(full_corpus(CORPUS), PRODUCT_DETECTORS))
    for k, v in _oracle()["rollup"].items():
        assert roll[k] == v, k
