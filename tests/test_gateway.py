"""Tests for the optional OpenAI-compatible routing gateway (WF-ADR-0004).

The gateway is the impure layer; these tests substitute the upstream call so no
network or real key is involved, and assert the routing + key handling are wired
correctly. The deterministic core is tested separately and never touched here.
"""

from __future__ import annotations

import os
import time

import pytest

# Skip the whole module cleanly if the gateway extra is not installed.
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from wayfinder_router import gateway  # noqa: E402

TRIVIAL = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
COMPLEX_TEXT = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(14))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)
COMPLEX = {"model": "auto", "messages": [{"role": "user", "content": COMPLEX_TEXT}]}

CONFIG = (
    "[routing]\nthreshold = 0.2\n\n"
    "[gateway.models.local]\n"
    'base_url = "http://localhost:11434/v1"\n'
    'model = "llama3.2"\n\n'
    "[gateway.models.cloud]\n"
    'base_url = "https://api.example.com/v1"\n'
    'model = "big-model"\n'
    'api_key_env = "EXAMPLE_API_KEY"\n'
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")
    captured: dict = {}

    def fake_forward(url, headers, json_body, timeout=60.0):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json_body
        return 200, b'{"id": "resp-1", "object": "chat.completion"}', "application/json"

    monkeypatch.setattr(gateway, "forward_request", fake_forward)
    app = gateway.build_app(start_dir=str(tmp_path))
    return TestClient(app), captured


def test_healthz_lists_models(client):
    test_client, _ = client
    resp = test_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["models"] == ["cloud", "local"]


def test_trivial_prompt_routes_to_local_upstream(client, monkeypatch):
    test_client, captured = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"
    # Forwarded to the local upstream, with the upstream model id, no auth header.
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["body"]["model"] == "llama3.2"
    assert "Authorization" not in captured["headers"]


def test_complex_prompt_routes_to_cloud_with_byo_key(client, monkeypatch):
    test_client, captured = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["body"]["model"] == "big-model"
    # The BYO key is read from the env at request time and injected.
    assert captured["headers"]["Authorization"] == "Bearer sekret"
    # ...and never appears in the response surface.
    assert "sekret" not in resp.text


def test_unconfigured_model_is_a_clear_misconfig_error(tmp_path, monkeypatch):
    # Routing recommends "cloud" but only "local" has an endpoint.
    (tmp_path / "wayfinder-router.toml").write_text(
        "[routing]\nthreshold = 0.2\n\n"
        "[gateway.models.local]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "llama3.2"\n',
        encoding="utf-8",
    )
    def fake_forward(*args, **kwargs):
        return 200, b"{}", "application/json"

    monkeypatch.setattr(gateway, "forward_request", fake_forward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 500
    assert resp.json()["error"]["type"] == "wayfinder_router_misconfigured"


def test_response_body_is_relayed_unchanged(client):
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.json() == {"id": "resp-1", "object": "chat.completion"}


# --- invoke_model (the onboarding/A-B caller) -------------------------------


def test_invoke_model_returns_assistant_text_with_byo_key(monkeypatch):
    captured: dict = {}

    def fake_forward(url, headers, json_body, timeout=60.0):
        captured.update(url=url, headers=headers, body=json_body)
        return 200, b'{"choices":[{"message":{"content":"hi from model"}}]}', "application/json"

    monkeypatch.setattr(gateway, "forward_request", fake_forward)
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    model = gateway.GatewayModel(
        base_url="https://api.example.com/v1", model="big", api_key_env="EXAMPLE_API_KEY"
    )
    assert gateway.invoke_model(model, "hello") == "hi from model"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["body"]["model"] == "big"
    assert captured["body"]["messages"][0]["content"] == "hello"
    assert captured["headers"]["Authorization"] == "Bearer sekret"


def test_invoke_model_raises_on_error_status(monkeypatch):
    monkeypatch.setattr(gateway, "forward_request", lambda *a, **k: (500, b"boom", "text/plain"))
    model = gateway.GatewayModel(base_url="http://x/v1", model="m")
    with pytest.raises(RuntimeError):
        gateway.invoke_model(model, "hi")


# --- /v1/feedback (the steady-state escalate loop) --------------------------


def test_feedback_records_a_label(client, tmp_path):
    test_client, _ = client
    resp = test_client.post("/v1/feedback", json={"text": "a prompt", "label": "cloud"})
    assert resp.status_code == 200
    log = tmp_path / "wayfinder-router-feedback.jsonl"
    assert log.read_text(encoding="utf-8").strip() == '{"text": "a prompt", "label": "cloud"}'


def test_feedback_missing_fields_is_400(client):
    test_client, _ = client
    assert test_client.post("/v1/feedback", json={"text": "x"}).status_code == 400
    assert test_client.post("/v1/feedback", json={"label": "cloud"}).status_code == 400


# --- hot-reload (scheduled recalibration takes effect live) -----------------


_TWO_MODELS = (
    '[gateway.models.local]\nbase_url = "http://l/v1"\nmodel = "l"\n\n'
    '[gateway.models.cloud]\nbase_url = "http://c/v1"\nmodel = "c"\n'
)


def _ok_forward(*args, **kwargs):
    return 200, b"{}", "application/json"


def _write_config(path, threshold):
    path.write_text(f"[routing]\nthreshold = {threshold}\n\n" + _TWO_MODELS, encoding="utf-8")
    # Push mtime forward so the holder's change-detection fires deterministically.
    future = time.time() + 10
    os.utime(path, (future, future))


def test_gateway_hot_reloads_when_config_changes(tmp_path, monkeypatch):
    config = tmp_path / "wayfinder-router.toml"
    _write_config(config, 0.9)  # COMPLEX (~0.38) is below 0.9 -> local
    monkeypatch.setattr(gateway, "forward_request", _ok_forward)
    client = TestClient(gateway.build_app(start_dir=str(tmp_path)))

    first = client.post("/v1/chat/completions", json=COMPLEX)
    assert first.headers["x-wayfinder-router-model"] == "local"

    _write_config(config, 0.05)  # now COMPLEX is at/above 0.05 -> cloud
    second = client.post("/v1/chat/completions", json=COMPLEX)
    assert second.headers["x-wayfinder-router-model"] == "cloud"


def test_gateway_keeps_last_good_config_on_bad_write(tmp_path, monkeypatch):
    config = tmp_path / "wayfinder-router.toml"
    _write_config(config, 0.9)  # COMPLEX -> local
    monkeypatch.setattr(gateway, "forward_request", _ok_forward)
    client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    first = client.post("/v1/chat/completions", json=COMPLEX)
    assert first.headers["x-wayfinder-router-model"] == "local"

    config.write_text("[routing]\nthreshold = 5\n\n" + _TWO_MODELS, encoding="utf-8")  # invalid
    future = time.time() + 20
    os.utime(config, (future, future))
    # Serving continues on the last-good config instead of failing.
    again = client.post("/v1/chat/completions", json=COMPLEX)
    assert again.headers["x-wayfinder-router-model"] == "local"


# --- per-request routing override (WF-ADR-0011) -----------------------------


def test_scored_default_reports_mode(client):
    # With no override the gateway scores and decides, and says so in the signal.
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert resp.headers["x-wayfinder-router-mode"] == "scored"


def test_model_field_pins_to_configured_endpoint(client, monkeypatch):
    # A trivial prompt scores ~0 (would route local) but the request pins "cloud".
    test_client, captured = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "cloud", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"
    # The structural score is still computed and reported even when pinned.
    assert resp.headers["x-wayfinder-router-score"] == "0.00"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["body"]["model"] == "big-model"


def test_prefer_local_pins_low_end(client):
    # A complex prompt would route cloud; prefer-local pins it to the low end.
    test_client, captured = client
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "prefer-local", "messages": [{"role": "user", "content": COMPLEX_TEXT}]},
    )
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"
    assert captured["body"]["model"] == "llama3.2"


def test_prefer_cloud_pins_high_end(client, monkeypatch):
    test_client, captured = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "prefer-cloud", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"


def test_unknown_model_id_falls_through_to_scoring(client):
    # An ordinary OpenAI model id is not a directive; the gateway scores as usual.
    test_client, _ = client
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.headers["x-wayfinder-router-mode"] == "scored"
    assert resp.headers["x-wayfinder-router-model"] == "local"


def test_threshold_header_overrides_the_cut(client):
    # COMPLEX scores ~0.38; the configured cut (0.2) routes cloud, but a per-request
    # threshold of 0.9 moves the boundary above the score and routes local instead.
    test_client, _ = client
    resp = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Threshold": "0.9"}
    )
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert resp.headers["x-wayfinder-router-mode"] == "threshold-override"


def test_pin_takes_precedence_over_threshold_header(client):
    # An explicit endpoint pin wins over a threshold header on the same request.
    test_client, _ = client
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "local", "messages": [{"role": "user", "content": COMPLEX_TEXT}]},
        headers={"X-Wayfinder-Threshold": "0.0"},
    )
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"
    assert resp.headers["x-wayfinder-router-model"] == "local"


def test_bad_threshold_header_is_400(client):
    test_client, _ = client
    not_a_number = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Threshold": "nope"}
    )
    assert not_a_number.status_code == 400
    assert not_a_number.json()["error"]["type"] == "wayfinder_router_bad_override"
    out_of_range = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Threshold": "1.5"}
    )
    assert out_of_range.status_code == 400


def test_threshold_override_rejected_for_multitier_router(tmp_path, monkeypatch):
    # The cut is only well-defined for a binary router; a multi-tier config 400s.
    (tmp_path / "wayfinder-router.toml").write_text(
        '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n\n'
        '[[routing.tiers]]\nmin_score = 0.4\nmodel = "mid"\n\n'
        '[[routing.tiers]]\nmin_score = 0.7\nmodel = "cloud"\n\n'
        '[gateway.models.local]\nbase_url = "http://l/v1"\nmodel = "l"\n\n'
        '[gateway.models.mid]\nbase_url = "http://m/v1"\nmodel = "m"\n\n'
        '[gateway.models.cloud]\nbase_url = "http://c/v1"\nmodel = "c"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway, "forward_request", _ok_forward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Threshold": "0.5"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "wayfinder_router_bad_override"
