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


def _clf_toml(models: str, intercepts: str, weights: str) -> str:
    return (
        "[routing.classifier]\n"
        f"models = {models}\n"
        f"intercepts = {intercepts}\n\n"
        "[routing.classifier.weights]\n"
        f"{weights}\n"
    )


# --- defaults + binary threshold --------------------------------------------


def test_no_config_yields_default_binary(tmp_path):
    config = load_routing_config(str(tmp_path))
    assert config.classifier is None
    assert config.tiers[0].model == "local"
    assert config.tiers[1].min_score == DEFAULT_THRESHOLD


def test_threshold_sets_the_binary_cut(tmp_path):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    config = load_routing_config(start)
    assert config.tiers == RoutingConfig.binary(0.8).tiers


def test_env_overrides_file_threshold(tmp_path, monkeypatch):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    monkeypatch.setenv(THRESHOLD_ENV, "0.2")
    assert load_routing_config(start).tiers[1].min_score == 0.2


def test_weights_merge_over_defaults(tmp_path):
    start = _write(tmp_path, "[routing]\nweights = { word_count = 9.0 }\n")
    config = load_routing_config(start)
    assert config.weights["word_count"] == 9.0
    assert config.weights["heading_count"] == RoutingConfig().weights["heading_count"]


def test_config_is_discovered_by_walking_up(tmp_path):
    _write(tmp_path, "[routing]\nthreshold = 0.9\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_routing_config(str(nested)).tiers[1].min_score == 0.9


# --- tiers ------------------------------------------------------------------


def test_tiers_are_parsed_and_sorted(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.6\nmodel = \"large\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"medium\"\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is None
    assert [t.model for t in config.tiers] == ["small", "medium", "large"]
    assert [t.min_score for t in config.tiers] == [0.0, 0.3, 0.6]


@pytest.mark.parametrize(
    "body",
    [
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"m\"\n",  # no 0.0 tier
        (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"a\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"b\"\n"
        ),  # duplicate
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"\"\n",  # empty model
        "[[routing.tiers]]\nmin_score = 2.0\nmodel = \"a\"\n",  # out of range
    ],
)
def test_malformed_tiers_are_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


# --- classifier -------------------------------------------------------------


def test_classifier_is_parsed(tmp_path):
    body = (
        "[routing.classifier]\n"
        'models = ["local", "cloud"]\n'
        "intercepts = [0.5, -0.5]\n\n"
        "[routing.classifier.weights]\n"
        "word_count = [0.0, 2.0]\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is not None
    assert config.classifier.models == ("local", "cloud")
    assert config.classifier.weights["word_count"] == (0.0, 2.0)
    # Unspecified features default to a zero vector of the right width.
    assert config.classifier.weights["heading_count"] == (0.0, 0.0)


def test_classifier_takes_precedence_over_tiers(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"x\"\n\n"
        "[routing.classifier]\n"
        'models = ["local", "cloud"]\n'
        "intercepts = [0.0, 0.0]\n\n"
        "[routing.classifier.weights]\n"
        "word_count = [0.0, 1.0]\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is not None


@pytest.mark.parametrize(
    "body",
    [
        _clf_toml('["only"]', "[0.0]", "word_count = [0.0]"),  # <2 models
        _clf_toml('["a", "b"]', "[0.0]", "word_count = [0.0, 1.0]"),  # intercepts wrong length
        _clf_toml('["a", "b"]', "[0.0, 0.0]", "bogus = [0.0, 1.0]"),  # unknown feature
        _clf_toml('["a", "b"]', "[0.0, 0.0]", "word_count = [0.0]"),  # weight wrong length
    ],
)
def test_malformed_classifier_is_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


# --- threshold validation ---------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "[routing]\nthreshold = 2.0\n",
        '[routing]\nthreshold = "high"\n',
        "routing = 1\n",
        "[routing]\nweights = { word_count = -1.0 }\n",
    ],
)
def test_malformed_config_is_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


def test_malformed_env_threshold_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, "nope")
    with pytest.raises(WayfinderConfigError):
        load_routing_config(str(tmp_path))
