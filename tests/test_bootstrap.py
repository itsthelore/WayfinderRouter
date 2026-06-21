"""Tests for the bootstrap scaffolding (the pure init/doctor helpers)."""

from __future__ import annotations

from wayfinder_router import bootstrap
from wayfinder_router.config import routing_config_from_toml
from wayfinder_router.gateway import gateway_config_from_toml


def test_hybrid_preset_config_round_trips():
    text = bootstrap.render_config(bootstrap.PRESETS["hybrid"])
    gw = gateway_config_from_toml(text)
    assert set(gw.models) == {"local", "cloud"}
    assert gw.models["local"].base_url == "http://localhost:11434/v1"
    assert gw.models["local"].api_key_env is None  # keyless local arm
    assert gw.models["cloud"].api_key_env == "ANTHROPIC_API_KEY"
    # the [routing] threshold shorthand parses to local/cloud tiers
    routing = routing_config_from_toml(text)
    assert [t.model for t in routing.tiers] == ["local", "cloud"]


def test_env_example_lists_names_without_secrets():
    text = bootstrap.render_env_example(bootstrap.PRESETS["hybrid"])
    assert "ANTHROPIC_API_KEY=" in text
    for line in text.splitlines():
        if line.startswith("ANTHROPIC_API_KEY"):
            assert line == "ANTHROPIC_API_KEY="  # the name only — never a value


def test_key_status_flags_keyless_and_missing(monkeypatch):
    gw = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    by_name = {s.name: s for s in bootstrap.key_status(gw.models)}
    assert by_name["local"].env_var is None and by_name["local"].ok is True  # keyless
    assert by_name["cloud"].env_var == "ANTHROPIC_API_KEY" and by_name["cloud"].ok is False
    assert bootstrap.missing_keys(list(by_name.values())) == ["ANTHROPIC_API_KEY"]


def test_key_status_ok_when_key_set(monkeypatch):
    gw = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    statuses = bootstrap.key_status(gw.models)
    assert all(s.ok for s in statuses)
    assert bootstrap.missing_keys(statuses) == []
