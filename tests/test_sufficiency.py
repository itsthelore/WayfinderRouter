"""Tests for the judge-label trust gates (WF-ADR-0037).

Pure: builds ``Sample``s directly (accuracy-objective calibration reads only
``score``/``label``), so the gates test with no model and no dataset files.
"""

from __future__ import annotations

import pytest

from wayfinder_router import cohens_kappa, cross_validated_accuracy, evaluate
from wayfinder_router.calibrate import Sample
from wayfinder_router.sufficiency import confusion_matrix, majority_baseline


def _s(label: str, score: float) -> Sample:
    return Sample(features={}, label=label, score=score)


# A cleanly separable set: low scores are "local", high scores are "cloud".
SEPARABLE = [
    _s("local", 0.10), _s("local", 0.15), _s("local", 0.20), _s("local", 0.25),
    _s("cloud", 0.75), _s("cloud", 0.80), _s("cloud", 0.85), _s("cloud", 0.90),
]
# No routable signal: both labels spread across the same scores.
NOISE = [
    _s("local", 0.4), _s("cloud", 0.4), _s("local", 0.5), _s("cloud", 0.5),
    _s("local", 0.6), _s("cloud", 0.6), _s("local", 0.45), _s("cloud", 0.55),
]


def test_kappa_perfect_agreement():
    assert cohens_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")]) == 1.0


def test_kappa_all_one_label_both_sides_is_perfect():
    # p_e == 1 and observed perfect -> 1.0 (a constant prediction that is always right).
    assert cohens_kappa([("a", "a"), ("a", "a")]) == 1.0


def test_kappa_worse_than_chance_is_negative():
    assert cohens_kappa([("a", "b"), ("b", "a"), ("a", "b"), ("b", "a")]) < 0.0


def test_confusion_matrix_counts():
    m = confusion_matrix([("local", "local"), ("local", "cloud"), ("cloud", "cloud")])
    assert m["local"]["local"] == 1
    assert m["local"]["cloud"] == 1
    assert m["cloud"]["cloud"] == 1
    assert m["cloud"]["local"] == 0


def test_majority_baseline():
    assert majority_baseline(SEPARABLE) == 0.5
    assert majority_baseline([_s("local", 0.1)] * 9 + [_s("cloud", 0.9)]) == 0.9


def test_cross_validated_accuracy_high_on_separable():
    # < 1.0 even on cleanly separable data: holding out the lowest-scoring cloud sample
    # pushes the fitted cut above it, so that fold misclassifies it — honest CV behaviour.
    assert cross_validated_accuracy(SEPARABLE, k=4) >= 0.8


def test_cross_validated_accuracy_low_on_noise():
    assert cross_validated_accuracy(NOISE, k=4) <= 0.6


def test_cross_validated_accuracy_rejects_fewer_than_two_folds():
    # A bad k must surface as an error, not a silent 0.0 that reads as a genuine "no lift".
    for bad in (1, 0, -1):
        with pytest.raises(ValueError):
            cross_validated_accuracy(SEPARABLE, k=bad)


def test_gates_pass_on_good_judge_and_separable_labels():
    gold = [("local", "local")] * 5 + [("cloud", "cloud")] * 5
    report = evaluate(gold, SEPARABLE)
    assert report.passed
    assert report.kappa == 1.0
    assert report.lift > 0
    assert "PASS" in report.render()


def test_gates_refuse_on_low_kappa():
    gold = [("local", "cloud"), ("cloud", "local")] * 5  # judge disagrees with humans
    report = evaluate(gold, SEPARABLE)
    assert not report.passed
    assert any("kappa" in f for f in report.failures)


def test_gates_refuse_without_a_gold_set():
    report = evaluate([], SEPARABLE)
    assert not report.passed
    assert any("gold" in f for f in report.failures)


def test_gates_refuse_on_degenerate_labels():
    gold = [("local", "local")] * 10
    degenerate = [_s("local", 0.1)] * 8  # only one arm represented
    report = evaluate(gold, degenerate)
    assert not report.passed
    assert any("degenerate" in f for f in report.failures)


def test_gates_refuse_without_out_of_fold_lift():
    gold = [("local", "local")] * 5 + [("cloud", "cloud")] * 5
    report = evaluate(gold, NOISE)
    assert not report.passed
    assert any("lift" in f for f in report.failures)
