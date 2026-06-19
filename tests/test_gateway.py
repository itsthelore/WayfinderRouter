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

    async def fake_aforward(url, headers, json_body, timeout=60.0):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json_body
        return 200, b'{"id": "resp-1", "object": "chat.completion"}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake_aforward)
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
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 500
    assert resp.json()["error"]["type"] == "wayfinder_router_misconfigured"


def test_response_body_is_relayed_unchanged(client):
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.json() == {"id": "resp-1", "object": "chat.completion"}


def test_response_carries_a_request_id(client):
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.headers.get("x-wayfinder-router-request-id")


# --- invoke_model (the onboarding/A-B caller, synchronous) ------------------


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


# --- cost metadata (WF-ADR-0017) --------------------------------------------


def test_cost_per_1k_is_parsed_and_round_trips():
    body = (
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        'api_key_env = "EXAMPLE_API_KEY"\n'
        "cost_per_1k = 12.5\n"
    )
    config = gateway.gateway_config_from_toml(body)
    assert config.models["cloud"].cost_per_1k == 12.5
    # Round-trips through the dumper used by recalibration.
    again = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(config))
    assert again.models["cloud"].cost_per_1k == 12.5


def test_cost_per_1k_is_optional():
    body = (
        "[gateway.models.local]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "llama3.2"\n'
    )
    assert gateway.gateway_config_from_toml(body).models["local"].cost_per_1k is None


def test_negative_cost_per_1k_is_rejected():
    body = (
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        "cost_per_1k = -1.0\n"
    )
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(body)


# --- streaming + upstream errors (WF-ADR-0013) ------------------------------


def test_streaming_relays_sse_chunks(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def fake_stream(url, headers, json_body, timeout=60.0):
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(gateway, "aforward_stream", fake_stream)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert resp.headers["x-wayfinder-router-mode"] == "scored"
    assert b'"delta"' in resp.content and b"[DONE]" in resp.content


def test_streaming_upstream_error_becomes_a_terminal_sse_event(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def boom_stream(url, headers, json_body, timeout=60.0):
        raise gateway.UpstreamError("connection refused")
        yield b""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(gateway, "aforward_stream", boom_stream)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200  # the stream already started with a 200
    assert b"wayfinder_router_upstream_error" in resp.content
    assert b"[DONE]" in resp.content


def test_non_streaming_upstream_error_is_a_502(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def boom(url, headers, json_body, timeout=60.0):
        raise gateway.UpstreamError("connection refused")

    monkeypatch.setattr(gateway, "aforward_request", boom)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "wayfinder_router_upstream_error"
    assert resp.headers["x-wayfinder-router-model"] == "local"


# --- dry-run (try the router with no backends) ------------------------------


def test_dry_run_returns_the_decision_without_an_upstream(tmp_path):
    # No [gateway.models] at all; dry-run still reports the routing decision.
    (tmp_path / "wayfinder-router.toml").write_text("[routing]\nthreshold = 0.2\n", encoding="utf-8")
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path), dry_run=True))
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    decision = resp.json()["wayfinder"]
    assert decision["model"] == "local"
    assert decision["mode"] == "scored"
    assert decision["dry_run"] is True
    assert resp.headers["x-wayfinder-router-mode"] == "scored"


# --- routing visibility (/router, X-Wayfinder-Debug, WF-ADR-0014) -----------


def test_router_recent_tracks_decisions_without_prompt_text(client):
    test_client, _ = client
    test_client.post("/v1/chat/completions", json=TRIVIAL)
    test_client.post(
        "/v1/chat/completions",
        json={"model": "cloud", "messages": [{"role": "user", "content": "a secret prompt body"}]},
    )
    body = test_client.get("/router/recent").json()
    assert body["total"] == 2
    assert body["by_model"] == {"local": 1, "cloud": 1}
    # Most-recent-first; metadata only, never the prompt text.
    first = body["recent"][0]
    assert set(first) == {"request_id", "model", "score", "mode", "ts"}
    assert first["model"] == "cloud"
    assert "a secret prompt body" not in test_client.get("/router/recent").text


def test_router_dashboard_serves_self_contained_html(client):
    test_client, _ = client
    resp = test_client.get("/router")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Wayfinder routing" in resp.text
    assert "/router/recent" in resp.text  # the page polls the JSON endpoint


def test_debug_header_injects_the_decision_into_the_response_body(client):
    test_client, _ = client
    resp = test_client.post(
        "/v1/chat/completions", json=TRIVIAL, headers={"X-Wayfinder-Debug": "true"}
    )
    assert resp.status_code == 200
    decision = resp.json()["wayfinder"]
    assert decision["model"] == "local"
    assert decision["mode"] == "scored"
    # The relayed upstream payload is preserved alongside the injected field.
    assert resp.json()["id"] == "resp-1"


def test_default_response_body_omits_the_decision(client):
    # Without the opt-in header the body is byte-clean (strict clients unaffected).
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert "wayfinder" not in resp.json()


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


def test_feedback_requires_a_token_when_configured(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("WAYFINDER_ROUTER_FEEDBACK_TOKEN", "s3cret")
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    unauth = test_client.post("/v1/feedback", json={"text": "a", "label": "cloud"})
    assert unauth.status_code == 401
    ok = test_client.post(
        "/v1/feedback",
        json={"text": "a", "label": "cloud"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 200


# --- hot-reload (scheduled recalibration takes effect live) -----------------


_TWO_MODELS = (
    '[gateway.models.local]\nbase_url = "http://l/v1"\nmodel = "l"\n\n'
    '[gateway.models.cloud]\nbase_url = "http://c/v1"\nmodel = "c"\n'
)


async def _ok_aforward(*args, **kwargs):
    return 200, b"{}", "application/json"


def _write_config(path, threshold):
    path.write_text(f"[routing]\nthreshold = {threshold}\n\n" + _TWO_MODELS, encoding="utf-8")
    # Push mtime forward so the holder's change-detection fires deterministically.
    future = time.time() + 10
    os.utime(path, (future, future))


def test_gateway_hot_reloads_when_config_changes(tmp_path, monkeypatch):
    config = tmp_path / "wayfinder-router.toml"
    _write_config(config, 0.9)  # COMPLEX (~0.38) is below 0.9 -> local
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    client = TestClient(gateway.build_app(start_dir=str(tmp_path)))

    first = client.post("/v1/chat/completions", json=COMPLEX)
    assert first.headers["x-wayfinder-router-model"] == "local"

    _write_config(config, 0.05)  # now COMPLEX is at/above 0.05 -> cloud
    second = client.post("/v1/chat/completions", json=COMPLEX)
    assert second.headers["x-wayfinder-router-model"] == "cloud"


def test_gateway_keeps_last_good_config_on_bad_write(tmp_path, monkeypatch):
    config = tmp_path / "wayfinder-router.toml"
    _write_config(config, 0.9)  # COMPLEX -> local
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
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
    # prefer-cloud is the v0.1.2 name, kept as a silent back-compat alias of prefer-hosted.
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
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Threshold": "0.5"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "wayfinder_router_bad_override"


def test_prefer_hosted_pins_high_end(client, monkeypatch):
    # prefer-hosted is the canonical high-end directive (v0.1.3+); a trivial prompt
    # that would score local is pinned to the high end instead.
    test_client, captured = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "prefer-hosted", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"


# --- model discovery (/v1/models, WF-ADR-0012) ------------------------------


CLASSIFIER_CONFIG = (
    "[routing.classifier]\n"
    'models = ["local", "cloud"]\n'
    "intercepts = [0.0, 0.0]\n\n"
    "[routing.classifier.weights]\n"
    "word_count = [0.0, 1.0]\n\n"
    '[gateway.models.local]\nbase_url = "http://l/v1"\nmodel = "l"\n\n'
    '[gateway.models.cloud]\nbase_url = "http://c/v1"\nmodel = "c"\n'
)


def test_list_models_advertises_directives_and_endpoints(client):
    test_client, _ = client
    resp = test_client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    # auto + the prefer-* directives (binary router) + each configured endpoint.
    assert ids == ["auto", "prefer-local", "prefer-hosted", "local", "cloud"]
    assert all(m["object"] == "model" and m["owned_by"] == "wayfinder" for m in body["data"])
    # The renamed-away v0.1.2 name is not advertised (it still resolves, as an alias).
    assert "prefer-cloud" not in ids


def test_list_models_omits_prefer_directives_for_a_classifier(tmp_path):
    (tmp_path / "wayfinder-router.toml").write_text(CLASSIFIER_CONFIG, encoding="utf-8")
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    ids = [m["id"] for m in test_client.get("/v1/models").json()["data"]]
    assert ids == ["auto", "local", "cloud"]
    assert "prefer-local" not in ids and "prefer-hosted" not in ids


def test_prefer_directive_falls_through_to_scoring_under_a_classifier(tmp_path, monkeypatch):
    # A classifier has no ordered ladder, so prefer-* is not a pin — the gateway scores.
    (tmp_path / "wayfinder-router.toml").write_text(CLASSIFIER_CONFIG, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post(
        "/v1/chat/completions",
        json={"model": "prefer-hosted", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-mode"] == "scored"
