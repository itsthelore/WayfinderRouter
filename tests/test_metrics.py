"""Tests for the gateway Prometheus /metrics endpoint (WF-ADR-0018).

Like the rest of the gateway tests, the upstream call is substituted so no network
or real key is involved. The endpoint reads in-memory counters only; these assert
the series are present, increment with the right labels, and never leak prompt text.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from wayfinder_router import gateway  # noqa: E402

CONFIG = (
    "[routing]\nthreshold = 0.5\n\n"
    "[gateway.models.local]\n"
    'base_url = "http://localhost:11434/v1"\n'
    'model = "llama3.2"\n\n'
    "[gateway.models.cloud]\n"
    'base_url = "https://api.example.com/v1"\n'
    'model = "big-model"\n'
)
TRIVIAL = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


async def _ok_aforward(url, headers, json_body, timeout=60.0):
    return 200, b'{"id": "resp-1", "object": "chat.completion"}', "application/json"


def _client(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    return TestClient(gateway.build_app(start_dir=str(tmp_path)))


def test_metrics_is_prometheus_text_with_build_info(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE wayfinder_router_requests_total counter" in resp.text
    assert "wayfinder_router_build_info{version=" in resp.text


def test_requests_total_increments_with_model_and_mode_labels(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions", json=TRIVIAL)
    text = client.get("/metrics").text
    assert 'wayfinder_router_requests_total{model="local",mode="scored"} 1' in text


def test_decision_latency_histogram_is_present(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions", json=TRIVIAL)
    text = client.get("/metrics").text
    assert "# TYPE wayfinder_router_decision_latency_seconds histogram" in text
    assert 'wayfinder_router_decision_latency_seconds_bucket{le="+Inf"} 1' in text
    assert "wayfinder_router_decision_latency_seconds_count 1" in text


def test_metrics_never_leak_prompt_text(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "a secret prompt body"}]},
    )
    assert "a secret prompt body" not in client.get("/metrics").text


def test_model_cost_gauge_is_exposed_when_configured(tmp_path, monkeypatch):
    config = (
        "[routing]\nthreshold = 0.5\n\n"
        "[gateway.models.local]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "llama3.2"\n'
        "cost_per_1k = 0.0\n\n"
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        "cost_per_1k = 10.0\n"
    )
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    text = client.get("/metrics").text
    assert "# TYPE wayfinder_router_model_cost_per_1k gauge" in text
    assert 'wayfinder_router_model_cost_per_1k{model="cloud"} 10' in text
    assert 'wayfinder_router_model_cost_per_1k{model="local"} 0' in text


def test_model_cost_gauge_absent_without_cost_metadata(tmp_path, monkeypatch):
    # No cost_per_1k configured -> the gauge block is omitted entirely.
    client = _client(tmp_path, monkeypatch)
    assert "wayfinder_router_model_cost_per_1k" not in client.get("/metrics").text


def test_upstream_error_increments_the_error_counter(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def boom(url, headers, json_body, timeout=60.0):
        raise gateway.UpstreamError("connection refused")

    monkeypatch.setattr(gateway, "aforward_request", boom)
    client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 502
    text = client.get("/metrics").text
    assert 'wayfinder_router_upstream_errors_total{model="local"} 1' in text
