"""Tests for recalibration — re-fit the routing config from the feedback log."""

from __future__ import annotations

import pytest
from wayfinder.calibrate import CalibrationError
from wayfinder.config import load_routing_config
from wayfinder.gateway import load_gateway_config

from wayfinder import recalibrate, record_label, score_complexity

COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)


def _log(tmp_path, rows) -> str:
    path = str(tmp_path / "wayfinder-feedback.jsonl")
    for text, label in rows:
        record_label(path, text, label)
    return path


def _balanced(tmp_path):
    return _log(tmp_path, [("hi", "local")] * 4 + [(COMPLEX, "cloud")] * 4)


def test_recalibrate_writes_a_config_that_routes_the_labeled_way(tmp_path):
    config = str(tmp_path / "wayfinder.toml")
    result = recalibrate(_balanced(tmp_path), config, "threshold")
    assert result.written and result.label_count == 8
    assert result.summary["accuracy"] == 1.0
    loaded = load_routing_config(str(tmp_path))
    assert score_complexity("hi", config=loaded).recommendation == "local"
    assert score_complexity(COMPLEX, config=loaded).recommendation == "cloud"


def test_recalibrate_preserves_the_gateway_section(tmp_path):
    config = tmp_path / "wayfinder.toml"
    config.write_text(
        '[gateway.models.local]\nbase_url = "http://l/v1"\nmodel = "l"\napi_key_env = "K1"\n\n'
        '[gateway.models.cloud]\nbase_url = "http://c/v1"\nmodel = "c"\napi_key_env = "K2"\n',
        encoding="utf-8",
    )
    recalibrate(_balanced(tmp_path), str(config), "threshold")
    gateway = load_gateway_config(str(tmp_path))
    assert set(gateway.models) == {"local", "cloud"}
    # The env-var name survives; the routing section is rewritten alongside it.
    assert gateway.models["cloud"].api_key_env == "K2"
    text = config.read_text(encoding="utf-8")
    assert "[[routing.tiers]]" in text and "[gateway.models.cloud]" in text


def test_recalibrate_skips_below_min_labels_without_writing(tmp_path):
    config = tmp_path / "wayfinder.toml"
    result = recalibrate(_log(tmp_path, [("hi", "local")]), str(config), "threshold", min_labels=2)
    assert result.written is False
    assert result.reason and not config.exists()


def test_recalibrate_is_deterministic(tmp_path):
    log = _balanced(tmp_path)
    config = str(tmp_path / "wayfinder.toml")
    first = recalibrate(log, config, "threshold").toml
    second = recalibrate(log, config, "threshold").toml
    assert first == second


def test_recalibrate_propagates_calibration_error(tmp_path):
    # threshold mode needs both arms represented; one label is an error, not a skip.
    log = _log(tmp_path, [("hi", "local")] * 3)
    with pytest.raises(CalibrationError):
        recalibrate(log, str(tmp_path / "wayfinder.toml"), "threshold")
