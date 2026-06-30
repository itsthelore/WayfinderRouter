"""Tests for offline calibration (threshold sweep, tiers, classifier fit)."""

from __future__ import annotations

import json

import pytest
from wayfinder_router.calibrate import CalibrationError, calibrate
from wayfinder_router.config import load_routing_config

from wayfinder_router import load_dataset, score_complexity

SIMPLE = "hi there"
MEDIUM = "# Task\n\nDo a few things.\n\n- one\n- two\n- three\n- four\n"
LARGE = (
    "# Plan\n\n## Context\n\n"
    + ("Lots of detail here about the system and its many moving parts. " * 12)
    + "\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(14))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx = 1\n```\n\n| a | b |\n| - | - |\n"
)


def _dataset(tmp_path, rows) -> str:
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def _binary_rows():
    return [{"text": SIMPLE, "label": "local"}] * 5 + [{"text": LARGE, "label": "cloud"}] * 5


# --- threshold mode ---------------------------------------------------------


def test_threshold_calibration_separates_two_arms(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    result = calibrate(samples, "threshold")
    assert result.summary["mode"] == "threshold"
    assert result.summary["accuracy"] == 1.0
    assert result.summary["models"] == ["local", "cloud"]
    assert 0.0 < result.summary["threshold"] <= 1.0


def test_threshold_calibration_is_deterministic(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    assert calibrate(samples, "threshold").toml == calibrate(samples, "threshold").toml


def test_threshold_mode_requires_exactly_two_labels(tmp_path):
    rows = [
        {"text": SIMPLE, "label": "a"},
        {"text": MEDIUM, "label": "b"},
        {"text": LARGE, "label": "c"},
    ]
    samples = load_dataset(_dataset(tmp_path, rows))
    with pytest.raises(CalibrationError):
        calibrate(samples, "threshold")


def test_threshold_round_trips_into_a_usable_config(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    (tmp_path / "wayfinder-router.toml").write_text(calibrate(samples, "threshold").toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert score_complexity(SIMPLE, config=config).recommendation == "local"
    assert score_complexity(LARGE, config=config).recommendation == "cloud"


# --- cost-aware threshold (WF-ADR-0017) -------------------------------------


def test_cost_quality_holds_the_savings_target_and_emits_cost(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    result = calibrate(samples, "threshold", objective="cost-quality", target_savings=0.4)
    assert result.summary["objective"] == "cost-quality"
    assert result.summary["cost_savings"] >= 0.4
    # The emitted config records the per-arm cost metadata and still routes right.
    (tmp_path / "wayfinder-router.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert [t.cost for t in config.tiers] == [0.2, 1.0]
    assert score_complexity(SIMPLE, config=config).recommendation == "local"
    assert score_complexity(LARGE, config=config).recommendation == "cloud"


def test_cost_quality_respects_custom_costs(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    result = calibrate(
        samples, "threshold", objective="cost-quality", target_savings=0.3,
        costs={"local": 0.1, "cloud": 1.0},
    )
    (tmp_path / "wayfinder-router.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert [t.cost for t in config.tiers] == [0.1, 1.0]


def test_cost_quality_rejects_an_unreachable_target(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    with pytest.raises(CalibrationError):
        calibrate(samples, "threshold", objective="cost-quality", target_savings=0.99)


def test_cost_quality_needs_a_target(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    with pytest.raises(CalibrationError):
        calibrate(samples, "threshold", objective="cost-quality")


# --- cost-aware knee (WF-ADR-0017) ------------------------------------------


def _skewed_samples():
    """Cloud dominates and the score only weakly separates the arms — the case where the
    accuracy objective collapses toward always-routing-cloud (cf. benchmarks/calibration-eval.md)."""
    from wayfinder_router.calibrate import Sample

    return (
        [Sample({}, "cloud", 0.1) for _ in range(30)]
        + [Sample({}, "cloud", 0.2) for _ in range(50)]
        + [Sample({}, "local", 0.0) for _ in range(10)]
        + [Sample({}, "local", 0.1) for _ in range(10)]
    )


def test_knee_saves_more_than_accuracy_on_skewed_labels():
    samples = _skewed_samples()
    acc = calibrate(samples, "threshold", objective="accuracy")
    knee = calibrate(samples, "threshold", objective="knee")
    assert knee.summary["objective"] == "knee"
    # accuracy puts the cut low (routes almost everything to cloud); the knee cuts higher
    # and keeps a real share local, so it saves materially more cost.
    assert knee.summary["threshold"] > acc.summary["threshold"]
    assert knee.summary["cost_savings"] >= 0.3
    assert 0.0 < knee.summary["quality_recovered"] <= 1.0


def test_knee_is_deterministic():
    samples = _skewed_samples()
    assert calibrate(samples, "threshold", objective="knee").toml == \
        calibrate(samples, "threshold", objective="knee").toml


def test_knee_needs_no_target_and_emits_cost(tmp_path):
    result = calibrate(_skewed_samples(), "threshold", objective="knee")  # no target_savings
    (tmp_path / "wayfinder-router.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert [t.cost for t in config.tiers] == [0.2, 1.0]
    assert config.tiers[-1].model == "cloud"


def test_knee_is_threshold_only():
    with pytest.raises(CalibrationError):
        calibrate(_skewed_samples(), "classifier", objective="knee")



def test_cost_quality_only_in_threshold_mode(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    with pytest.raises(CalibrationError):
        calibrate(samples, "classifier", objective="cost-quality", target_savings=0.4)


def test_accuracy_objective_emits_no_cost_metadata(tmp_path):
    # The default objective is unchanged: tiers carry no cost.
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    result = calibrate(samples, "threshold")
    assert "cost" not in result.toml
    assert "objective" not in result.summary


# --- tiers mode -------------------------------------------------------------


def test_tiers_calibration_orders_and_separates(tmp_path):
    rows = (
        [{"text": SIMPLE, "label": "small"}] * 4
        + [{"text": MEDIUM, "label": "medium"}] * 4
        + [{"text": LARGE, "label": "large"}] * 4
    )
    samples = load_dataset(_dataset(tmp_path, rows))
    result = calibrate(samples, "tiers")
    assert result.summary["mode"] == "tiers"
    assert result.summary["models"] == ["small", "medium", "large"]
    assert result.summary["accuracy"] == 1.0

    (tmp_path / "wayfinder-router.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert score_complexity(SIMPLE, config=config).recommendation == "small"
    assert score_complexity(LARGE, config=config).recommendation == "large"


# --- classifier mode --------------------------------------------------------


def test_classifier_fit_is_deterministic(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    first = calibrate(samples, "classifier", iterations=200).toml
    second = calibrate(samples, "classifier", iterations=200).toml
    assert first == second


def test_classifier_round_trips_and_predicts(tmp_path):
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    result = calibrate(samples, "classifier", iterations=400)
    assert result.summary["mode"] == "classifier"
    assert result.summary["accuracy"] == 1.0

    (tmp_path / "wayfinder-router.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert config.classifier is not None
    assert score_complexity(SIMPLE, config=config).recommendation == "local"
    assert score_complexity(LARGE, config=config).recommendation == "cloud"


def test_classifier_rejects_nonpositive_l2(tmp_path):
    # l2 is a caller-supplied knob (CLI --l2). l2<=0 can make the Hessian singular on separable
    # data — a clean CalibrationError, not a raw ZeroDivisionError from the solver.
    samples = load_dataset(_dataset(tmp_path, _binary_rows()))
    with pytest.raises(CalibrationError):
        calibrate(samples, "classifier", l2=0.0)
    with pytest.raises(CalibrationError):
        calibrate(samples, "classifier", l2=-0.5)


# --- dataset loading --------------------------------------------------------


def test_empty_dataset_is_rejected(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(CalibrationError):
        load_dataset(str(path))


def test_malformed_row_is_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"text": "hi"}\n', encoding="utf-8")  # missing label
    with pytest.raises(CalibrationError):
        load_dataset(str(path))
