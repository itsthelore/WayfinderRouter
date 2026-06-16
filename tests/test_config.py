"""Tests for Wayfinder's own config loader (wayfinder.toml, no RAC)."""

from __future__ import annotations

import pytest
from wayfinder.complexity import DEFAULT_THRESHOLD
from wayfinder.config import THRESHOLD_ENV

from wayfinder import RoutingConfig, WayfinderConfigError, load_routing_config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(THRESHOLD_ENV, raising=False)


def _write(tmp_path, body: str) -> str:
    (tmp_path / "wayfinder.toml").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_no_config_yields_defaults(tmp_path):
    config = load_routing_config(str(tmp_path))
    assert config == RoutingConfig()
    assert config.threshold == DEFAULT_THRESHOLD


def test_config_overrides_threshold(tmp_path):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    assert load_routing_config(start).threshold == 0.8


def test_config_merges_weights_over_defaults(tmp_path):
    start = _write(tmp_path, "[routing]\nweights = { word_count = 9.0 }\n")
    config = load_routing_config(start)
    assert config.weights["word_count"] == 9.0
    assert config.weights["heading_count"] == RoutingConfig().weights["heading_count"]


def test_env_overrides_file_threshold(tmp_path, monkeypatch):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    monkeypatch.setenv(THRESHOLD_ENV, "0.2")
    assert load_routing_config(start).threshold == 0.2


def test_config_is_discovered_by_walking_up(tmp_path):
    _write(tmp_path, "[routing]\nthreshold = 0.9\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_routing_config(str(nested)).threshold == 0.9


@pytest.mark.parametrize(
    "body",
    [
        "[routing]\nthreshold = 2.0\n",  # out of range
        '[routing]\nthreshold = "high"\n',  # not a number
        "routing = 1\n",  # routing not a table
        "[routing]\nweights = { bogus_feature = 1.0 }\n",  # unknown feature
        "[routing]\nweights = { word_count = -1.0 }\n",  # negative weight
    ],
)
def test_malformed_config_is_rejected(tmp_path, body):
    start = _write(tmp_path, body)
    with pytest.raises(WayfinderConfigError):
        load_routing_config(start)


def test_malformed_env_threshold_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, "nope")
    with pytest.raises(WayfinderConfigError):
        load_routing_config(str(tmp_path))
