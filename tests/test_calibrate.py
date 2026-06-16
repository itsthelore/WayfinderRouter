"""Tests for offline calibration (threshold sweep, tiers, classifier fit)."""

from __future__ import annotations

import json

import pytest
from wayfinder.calibrate import CalibrationError, calibrate
from wayfinder.config import load_routing_config

from wayfinder import load_dataset, score_complexity

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
    (tmp_path / "wayfinder.toml").write_text(calibrate(samples, "threshold").toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert score_complexity(SIMPLE, config=config).recommendation == "local"
    assert score_complexity(LARGE, config=config).recommendation == "cloud"


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

    (tmp_path / "wayfinder.toml").write_text(result.toml, encoding="utf-8")
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

    (tmp_path / "wayfinder.toml").write_text(result.toml, encoding="utf-8")
    config = load_routing_config(str(tmp_path))
    assert config.classifier is not None
    assert score_complexity(SIMPLE, config=config).recommendation == "local"
    assert score_complexity(LARGE, config=config).recommendation == "cloud"


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
