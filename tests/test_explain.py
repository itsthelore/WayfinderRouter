"""Tests for the Phase 0 core additions: explain, sweep curve, config writer."""

from __future__ import annotations

import json

import pytest
from wayfinder.calibrate import CalibrationError
from wayfinder.complexity import FEATURE_ORDER

from wayfinder import (
    ClassifierModel,
    RoutingConfig,
    Tier,
    calibrate,
    dump_routing_toml,
    explain_score,
    extract_features,
    load_dataset,
    load_routing_config,
    scalar_score,
    score_complexity,
    sweep_curve,
)

COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)


# --- explain ----------------------------------------------------------------


def test_explain_has_one_contribution_per_feature():
    contributions = explain_score(extract_features(COMPLEX), RoutingConfig().weights)
    assert [c.name for c in contributions] == list(FEATURE_ORDER)


def test_contributions_sum_to_the_unrounded_score():
    features = extract_features(COMPLEX)
    weights = RoutingConfig().weights
    contributions = explain_score(features, weights)
    total = sum(c.contribution for c in contributions)
    # The reported score is the same sum rounded to 2dp.
    assert round(total, 2) == scalar_score(features, weights)


def test_explain_is_deterministic():
    features = extract_features(COMPLEX)
    weights = RoutingConfig().weights
    a = [c.to_dict() for c in explain_score(features, weights)]
    b = [c.to_dict() for c in explain_score(features, weights)]
    assert a == b


# --- sweep curve ------------------------------------------------------------


def _dataset(tmp_path, rows) -> str:
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def test_sweep_curve_covers_a_perfect_cut(tmp_path):
    rows = [{"text": "hi", "label": "local"}] * 4 + [{"text": COMPLEX, "label": "cloud"}] * 4
    curve = sweep_curve(load_dataset(_dataset(tmp_path, rows)))
    assert all(0.0 <= acc <= 1.0 for _, acc in curve)
    # Separable data: some cut classifies everything correctly.
    assert max(acc for _, acc in curve) == 1.0


def test_sweep_curve_requires_two_labels(tmp_path):
    rows = [{"text": "hi", "label": "only"}]
    with pytest.raises(CalibrationError):
        sweep_curve(load_dataset(_dataset(tmp_path, rows)))


# --- config writer (round-trip) ---------------------------------------------


def _roundtrip(tmp_path, config: RoutingConfig) -> RoutingConfig:
    (tmp_path / "wayfinder.toml").write_text(dump_routing_toml(config), encoding="utf-8")
    return load_routing_config(str(tmp_path))


def test_dump_binary_round_trips(tmp_path):
    loaded = _roundtrip(tmp_path, RoutingConfig.binary(0.6))
    assert loaded.tiers == RoutingConfig.binary(0.6).tiers
    assert loaded.classifier is None


def test_dump_weights_round_trip(tmp_path):
    config = RoutingConfig.binary(0.5, weights={**RoutingConfig().weights, "word_count": 9.0})
    loaded = _roundtrip(tmp_path, config)
    assert loaded.weights["word_count"] == 9.0


def test_dump_tiers_round_trip(tmp_path):
    tiers = (Tier(0.0, "small"), Tier(0.3, "medium"), Tier(0.6, "large"))
    loaded = _roundtrip(tmp_path, RoutingConfig(tiers=tiers))
    assert loaded.tiers == tiers


def test_dump_classifier_round_trips_and_predicts_the_same(tmp_path):
    clf = ClassifierModel(
        models=("small", "big"),
        weights={name: (0.0, 0.0) for name in FEATURE_ORDER} | {"word_count": (0.0, 5.0)},
        intercepts=(1.0, 0.0),
    )
    loaded = _roundtrip(tmp_path, RoutingConfig(classifier=clf))
    assert loaded.classifier is not None
    assert loaded.classifier.models == clf.models
    assert loaded.classifier.intercepts == clf.intercepts
    assert loaded.classifier.weights == clf.weights


def test_calibrated_classifier_dump_round_trips(tmp_path):
    rows = [{"text": "hi", "label": "local"}] * 5 + [{"text": COMPLEX, "label": "cloud"}] * 5
    fragment = calibrate(load_dataset(_dataset(tmp_path, rows)), "classifier").toml
    (tmp_path / "wayfinder.toml").write_text(fragment, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    # Re-dumping the loaded config and reloading is a fixed point.
    again = _roundtrip(tmp_path, config)
    assert again.classifier == config.classifier
    assert score_complexity(COMPLEX, config=config).recommendation == "cloud"
