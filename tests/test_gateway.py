"""Tests for the optional OpenAI-compatible routing gateway (WF-ADR-0004).

The gateway is the impure layer; these tests substitute the upstream call so no
network or real key is involved, and assert the routing + key handling are wired
correctly. The deterministic core is tested separately and never touched here.
"""

from __future__ import annotations

import json
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


def test_path_tolerance_chat_completions_without_v1_prefix(client):
    # A client whose base_url omits the /v1 prefix calls /chat/completions; route it anyway.
    test_client, captured = client
    resp = test_client.post("/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"


def test_path_tolerance_models_without_v1_prefix(client):
    # /models (no /v1) returns the same OpenAI-compatible list as /v1/models.
    test_client, _ = client
    v1 = test_client.get("/v1/models").json()
    bare = test_client.get("/models").json()
    assert bare == v1
    assert {m["id"] for m in bare["data"]} >= {"auto", "local", "cloud"}


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


def test_invoke_messages_forwards_full_conversation(monkeypatch):
    captured: dict = {}

    def fake_forward(url, headers, json_body, timeout=60.0):
        captured.update(url=url, body=json_body)
        return 200, b'{"choices":[{"message":{"content":"ok"}}]}', "application/json"

    monkeypatch.setattr(gateway, "forward_request", fake_forward)
    model = gateway.GatewayModel(base_url="http://h/v1", model="big")
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert gateway.invoke_messages(model, msgs) == "ok"
    assert captured["body"]["messages"] == msgs  # the whole history is forwarded
    assert captured["url"].endswith("/chat/completions")


def test_parse_sse_deltas_extracts_content_until_done():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        "",  # blank keep-alive
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',  # no content -> skipped
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"ignored"}}]}',  # after DONE -> not yielded
    ]
    assert "".join(gateway.parse_sse_deltas(lines)) == "Hello"


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


def test_api_key_cmd_is_parsed_and_round_trips():
    body = (
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        'api_key_env = "EXAMPLE_API_KEY"\n'
        'api_key_cmd = "op read op://Private/example/credential"\n'
    )
    config = gateway.gateway_config_from_toml(body)
    assert config.models["cloud"].api_key_cmd == "op read op://Private/example/credential"
    # The command (a reference, not a secret) survives the dumper recalibration uses.
    again = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(config))
    assert again.models["cloud"].api_key_cmd == "op read op://Private/example/credential"


def test_api_key_cmd_requires_api_key_env():
    body = (
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        'api_key_cmd = "op read op://Private/example/credential"\n'
    )
    with pytest.raises(gateway.WayfinderConfigError, match="api_key_env"):
        gateway.gateway_config_from_toml(body)


def test_empty_api_key_cmd_is_rejected():
    body = (
        "[gateway.models.cloud]\n"
        'base_url = "https://api.example.com/v1"\n'
        'model = "big-model"\n'
        'api_key_env = "EXAMPLE_API_KEY"\n'
        'api_key_cmd = ""\n'
    )
    with pytest.raises(gateway.WayfinderConfigError, match="api_key_cmd"):
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
    assert set(first) == {"request_id", "model", "score", "mode", "ts", "cost"}
    # The cost block is dollars + token counts only — still no prompt text (WF-DESIGN-0007/0008).
    assert set(first["cost"]) == {"realized", "baseline", "saved", "tokens", "unit", "estimated"}
    assert first["model"] == "cloud"
    assert "a secret prompt body" not in test_client.get("/router/recent").text


def test_router_dashboard_serves_self_contained_html(client):
    test_client, _ = client
    resp = test_client.get("/router")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Wayfinder routing" in resp.text
    assert "/router/recent" in resp.text  # the page polls the JSON endpoint


# --- savings & cost accounting (WF-DESIGN-0007 / 0008) -----------------------
_PRICED_CONFIG = (
    "[routing]\nthreshold = 0.2\n\n"
    "[gateway.models.local]\n"
    'base_url = "http://localhost:11434/v1"\n'
    'model = "llama3.2"\n'
    "cost_per_1k = 0.0\n\n"
    "[gateway.models.cloud]\n"
    'base_url = "https://api.example.com/v1"\n'
    'model = "big-model"\n'
    'api_key_env = "EXAMPLE_API_KEY"\n'
    "cost_per_1k = 0.01\n"
)


def test_savings_endpoint_reports_route_mix(client):
    test_client, _ = client
    test_client.post("/v1/chat/completions", json=TRIVIAL)  # -> local
    test_client.post("/v1/chat/completions", json=COMPLEX)  # -> cloud
    rep = test_client.get("/v1/savings").json()
    assert rep["requests"] == 2
    assert set(rep["by_route"]) == {"local", "cloud"}
    assert rep["unit"] == "relative" and rep["priced"] is False  # no cost_per_1k in CONFIG
    assert rep["saved"] >= 0
    assert "price_table_version" in rep
    assert test_client.get("/savings").json()["requests"] == 2  # path tolerance (no /v1)


def test_savings_priced_uses_exact_usage(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(_PRICED_CONFIG, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        body = b'{"object":"chat.completion","usage":{"prompt_tokens":1000,"completion_tokens":0}}'
        return 200, body, "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    tc.post("/v1/chat/completions", json=TRIVIAL)  # local, 1000 prompt tokens from usage
    rep = tc.get("/v1/savings").json()
    assert rep["priced"] is True and rep["unit"] == "usd"
    assert rep["requests"] == 1 and rep["estimated_requests"] == 0  # exact, not estimated
    assert rep["realized"] == 0.0  # local is free
    assert rep["baseline"] == 0.01  # always-frontier (cloud) for 1000 tokens
    assert rep["saved"] == 0.01


def test_metrics_expose_cost_counters(client):
    test_client, _ = client
    test_client.post("/v1/chat/completions", json=TRIVIAL)
    text = test_client.get("/metrics").text
    assert "wayfinder_router_realized_cost_total" in text
    assert "wayfinder_router_baseline_cost_total" in text
    assert "wayfinder_router_savings_cost_total" in text


def test_savings_persisted_and_reloaded(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(_PRICED_CONFIG, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, b'{"usage":{"prompt_tokens":1000,"completion_tokens":0}}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    tc.post("/v1/chat/completions", json=TRIVIAL)
    assert (tmp_path / "wayfinder-savings.json").exists()  # persisted best-effort
    # A fresh gateway at the same dir loads the prior ledger.
    tc2 = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    assert tc2.get("/v1/savings").json()["requests"] == 1


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


# --- decision-first demo UI (WF-ADR-0020) -----------------------------------

_CONTRIB_KEYS = {"name", "value", "normalized", "weight", "contribution"}


def test_demo_page_is_served(client):
    test_client, _ = client
    resp = test_client.get("/demo")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Wayfinder" in resp.text
    assert "/v1/chat/completions" in resp.text  # the page calls the routing endpoint
    assert "X-Wayfinder-Debug" in resp.text  # it opts into the decision payload


def _dry_run_client(tmp_path, config="[routing]\nthreshold = 0.2\n"):
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    return TestClient(gateway.build_app(start_dir=str(tmp_path), dry_run=True))


def test_dry_run_decision_carries_explain_for_the_demo(tmp_path):
    # Keyless: no [gateway.models] at all. The demo still gets the full "why".
    resp = _dry_run_client(tmp_path).post("/v1/chat/completions", json=COMPLEX)
    wf = resp.json()["wayfinder"]
    assert wf["dry_run"] is True
    contribs = wf["contributions"]
    assert isinstance(contribs, list) and contribs
    assert all(_CONTRIB_KEYS <= set(c) for c in contribs)
    assert any(c["contribution"] > 0 for c in contribs)  # a heavy prompt has real signal
    assert "word_count" in wf["features"]
    assert wf["cost"]["estimated"] is True  # no cost metadata configured -> relative units
    assert wf["cost"]["saved"] >= 0


def test_debug_payload_carries_contributions_on_relayed_responses(client):
    test_client, _ = client
    resp = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Debug": "true"}
    )
    wf = resp.json()["wayfinder"]
    assert wf["contributions"] and all(_CONTRIB_KEYS <= set(c) for c in wf["contributions"])
    assert resp.json()["id"] == "resp-1"  # the relayed upstream body is preserved


def test_threshold_override_is_visible_in_the_decision(tmp_path):
    resp = _dry_run_client(tmp_path).post(
        "/v1/chat/completions", json=TRIVIAL, headers={"X-Wayfinder-Threshold": "0.95"}
    )
    wf = resp.json()["wayfinder"]
    assert wf["mode"] == "threshold-override"
    assert isinstance(wf["contributions"], list)


def test_cost_block_uses_configured_cost_when_present(tmp_path):
    config = (
        "[routing]\nthreshold = 0.2\n\n"
        "[gateway.models.local]\n"
        'base_url = "http://x/v1"\nmodel = "s"\ncost_per_1k = 0.1\n\n'
        "[gateway.models.cloud]\n"
        'base_url = "http://y/v1"\nmodel = "b"\ncost_per_1k = 2.0\n'
    )
    resp = _dry_run_client(tmp_path, config).post("/v1/chat/completions", json=COMPLEX)
    cost = resp.json()["wayfinder"]["cost"]
    assert cost["estimated"] is False
    assert cost["unit"].startswith("$")
    assert cost["baseline"] >= cost["per_call"] and cost["saved"] >= 0


def test_scored_path_runs_no_explain(client, monkeypatch):
    # Boundary guard (WF-ADR-0001): explain_score must never run on the scored relay path.
    import wayfinder_router.gateway as gw

    def _boom(*a, **k):
        raise AssertionError("explain_score ran on the scored path")

    monkeypatch.setattr(gw, "explain_score", _boom)
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)  # no debug header
    assert resp.status_code == 200 and "wayfinder" not in resp.json()


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


# --- multi-turn routing scope (WF-ADR-0021) ---------------------------------

_MULTI = [
    {"role": "system", "content": "You are a terse assistant."},
    {"role": "user", "content": COMPLEX_TEXT},
    {"role": "assistant", "content": "Here is a long structured answer.\n" + "- point\n" * 20},
    {"role": "user", "content": "thanks!"},
]


def test_extract_prompt_turn_scopes_to_system_plus_latest_user():
    text = gateway.extract_prompt(_MULTI, route_on="turn")
    assert "terse assistant" in text  # standing system context kept
    assert "thanks!" in text  # the new ask kept
    assert "# Plan" not in text  # earlier user turn dropped
    assert "structured answer" not in text  # assistant reply never scored


def test_extract_prompt_last_user_is_just_the_newest_user_turn():
    assert gateway.extract_prompt(_MULTI, route_on="last_user") == "thanks!"


def test_extract_prompt_user_keeps_all_user_turns_only():
    text = gateway.extract_prompt(_MULTI, route_on="user")
    assert "# Plan" in text and "thanks!" in text
    assert "terse assistant" not in text and "structured answer" not in text


def test_extract_prompt_all_is_the_legacy_whole_transcript():
    text = gateway.extract_prompt(_MULTI, route_on="all")
    assert all(s in text for s in ("terse assistant", "# Plan", "structured answer", "thanks!"))


def test_extract_prompt_falls_back_to_last_message_when_no_user_turn():
    # A role-less or assistant-only payload must not score empty and route blind.
    assert gateway.extract_prompt([{"role": "assistant", "content": "orphan"}]) == "orphan"
    assert gateway.extract_prompt("not a list") == ""


def test_extract_prompt_handles_array_of_parts_content():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "part one"}, {"text": "part two"}]}]
    assert gateway.extract_prompt(msgs, route_on="last_user") == "part one\npart two"


def test_default_scope_does_not_drift_over_a_chat(tmp_path):
    # The bug this fixes: a trivial follow-up after a heavy exchange inherited the
    # heavy transcript and routed cloud. Default "turn" scores system+latest user.
    client = _dry_run_client(tmp_path)  # threshold 0.2
    wf = client.post("/v1/chat/completions", json={"model": "auto", "messages": _MULTI}).json()[
        "wayfinder"
    ]
    assert wf["model"] == "local"  # the "thanks!" turn is trivial
    solo = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [_MULTI[0], _MULTI[-1]]},
    ).json()["wayfinder"]
    assert abs(wf["score"] - solo["score"]) < 1e-9  # no drift from the back-scroll


def test_all_scope_restores_legacy_whole_transcript_scoring(tmp_path):
    client = _dry_run_client(
        tmp_path, config='[routing]\nthreshold = 0.2\n\n[gateway]\nroute_on = "all"\n'
    )
    wf = client.post("/v1/chat/completions", json={"model": "auto", "messages": _MULTI}).json()[
        "wayfinder"
    ]
    assert wf["model"] == "cloud"  # the heavy transcript pushes the same turn to cloud


def test_route_on_parses_and_defaults_to_turn():
    assert gateway.gateway_config_from_toml("[routing]\nthreshold=0.2\n").route_on == "turn"
    assert gateway.gateway_config_from_toml('[gateway]\nroute_on = "user"\n').route_on == "user"


def test_route_on_rejects_unknown_scope():
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml('[gateway]\nroute_on = "everything"\n')


def test_dump_gateway_toml_round_trips_route_on():
    dumped = gateway.dump_gateway_toml(gateway.GatewayConfig(models={}, route_on="last_user"))
    assert 'route_on = "last_user"' in dumped
    assert gateway.gateway_config_from_toml(dumped).route_on == "last_user"
    # the default scope stays out of the dump, keeping configs clean
    assert "route_on" not in gateway.dump_gateway_toml(gateway.GatewayConfig(route_on="turn"))


# --- conversation latch / sticky-auto (WF-ADR-0022) -------------------------

_HARD_THEN_TRIVIAL = [
    {"role": "user", "content": COMPLEX_TEXT},  # heavy -> cloud at threshold 0.2
    {"role": "assistant", "content": "Here is a long structured answer."},
    {"role": "user", "content": "thanks!"},  # the current turn is trivial
]


def test_conversation_high_water_is_a_max_over_turns_not_a_sum():
    from wayfinder_router.complexity import RoutingConfig, binary_tiers

    tiers = binary_tiers(0.2)
    routing = RoutingConfig(tiers=tiers)
    assert gateway.conversation_high_water(_HARD_THEN_TRIVIAL, routing, tiers) == "cloud"
    trivial = [{"role": "user", "content": "hi"}, {"role": "user", "content": "thanks"}]
    assert gateway.conversation_high_water(trivial, routing, tiers) == "local"
    assert gateway.conversation_high_water([{"role": "assistant", "content": "x"}], routing, tiers) is None


def test_sticky_latches_a_hard_chat_to_cloud_via_header(tmp_path):
    client = _dry_run_client(tmp_path)  # threshold 0.2
    off = client.post("/v1/chat/completions", json={"model": "auto", "messages": _HARD_THEN_TRIVIAL})
    assert off.json()["wayfinder"]["model"] == "local"  # the gap: trivial follow-up routes cheap
    on = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": _HARD_THEN_TRIVIAL},
        headers={"X-Wayfinder-Sticky": "true"},
    )
    wf = on.json()["wayfinder"]
    assert wf["model"] == "cloud" and wf["mode"] == "sticky"
    assert wf["score"] == 0.0  # reported score stays the current turn's — the "why" is honest


def test_sticky_does_not_escalate_an_all_trivial_chat(tmp_path):
    client = _dry_run_client(tmp_path)
    convo = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
             {"role": "user", "content": "thanks"}]
    wf = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": convo},
        headers={"X-Wayfinder-Sticky": "true"},
    ).json()["wayfinder"]
    assert wf["model"] == "local" and wf["mode"] == "scored"  # nothing to latch onto


def test_sticky_from_config_default(tmp_path):
    client = _dry_run_client(
        tmp_path, config='[routing]\nthreshold = 0.2\n\n[gateway]\nsticky = true\n'
    )
    wf = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": _HARD_THEN_TRIVIAL}
    ).json()["wayfinder"]
    assert wf["model"] == "cloud" and wf["mode"] == "sticky"


def test_explicit_pin_beats_sticky(tmp_path):
    # A pin is the operator's explicit choice; the latch must not override it.
    client = _dry_run_client(tmp_path)
    wf = client.post(
        "/v1/chat/completions",
        json={"model": "prefer-local", "messages": _HARD_THEN_TRIVIAL},
        headers={"X-Wayfinder-Sticky": "true"},
    ).json()["wayfinder"]
    assert wf["model"] == "local" and wf["mode"] == "pinned"


def test_route_on_header_overrides_the_configured_scope(tmp_path):
    client = _dry_run_client(tmp_path)  # default scope "turn"
    # Scoring the whole transcript (heavy) routes the trivial current turn to cloud.
    wf = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": _HARD_THEN_TRIVIAL},
        headers={"X-Wayfinder-Route-On": "all"},
    ).json()["wayfinder"]
    assert wf["model"] == "cloud"


def test_resolve_sticky_and_route_on_header_parsers():
    assert gateway.resolve_sticky(None, True) is True  # absent -> config default
    assert gateway.resolve_sticky("false", True) is False
    assert gateway.resolve_sticky("YES", False) is True
    assert gateway.parse_route_on_header(None) is None
    assert gateway.parse_route_on_header("  USER ") == "user"
    with pytest.raises(gateway.BadOverride):
        gateway.resolve_sticky("maybe", False)
    with pytest.raises(gateway.BadOverride):
        gateway.parse_route_on_header("everything")


def test_bad_override_headers_return_400(tmp_path):
    client = _dry_run_client(tmp_path)
    for header in ({"X-Wayfinder-Sticky": "maybe"}, {"X-Wayfinder-Route-On": "nope"}):
        resp = client.post("/v1/chat/completions", json=TRIVIAL, headers=header)
        assert resp.status_code == 400
        assert resp.json()["error"]["type"] == "wayfinder_router_bad_override"


def test_sticky_round_trips_through_dump_gateway_toml():
    cfg = gateway.GatewayConfig(models={}, route_on="user", sticky=True)
    dumped = gateway.dump_gateway_toml(cfg)
    assert "sticky = true" in dumped and 'route_on = "user"' in dumped
    back = gateway.gateway_config_from_toml(dumped)
    assert back.sticky is True and back.route_on == "user"
    assert "sticky" not in gateway.dump_gateway_toml(gateway.GatewayConfig())  # default stays out


def test_sticky_config_rejects_non_boolean(tmp_path):
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml('[gateway]\nsticky = "yes"\n')


# --- conversation latch cool-down (WF-ADR-0022) -----------------------------

def _hard_then_calm(*calm):
    convo = [{"role": "user", "content": COMPLEX_TEXT}, {"role": "assistant", "content": "ok"}]
    for c in calm:
        convo += [{"role": "user", "content": c}, {"role": "assistant", "content": "ok"}]
    return convo


def test_cooldown_decays_the_latch_after_n_calm_turns():
    from wayfinder_router.complexity import RoutingConfig, binary_tiers

    tiers = binary_tiers(0.2)
    routing = RoutingConfig(tiers=tiers)

    def hwm(convo, cd):
        return gateway.conversation_high_water(convo, routing, tiers, cooldown=cd)
    # monotonic: never steps down
    assert hwm(_hard_then_calm("thanks", "ok", "more"), 0) == "cloud"
    # cooldown=2: holds through 1 calm turn, decays on the 2nd
    assert hwm(_hard_then_calm("thanks"), 2) == "cloud"
    assert hwm(_hard_then_calm("thanks", "ok"), 2) == "local"
    # a fresh hard turn re-arms the latch after it has decayed
    rearmed = _hard_then_calm("thanks", "ok") + [
        {"role": "user", "content": COMPLEX_TEXT}, {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "thanks"},
    ]
    assert hwm(rearmed, 2) == "cloud"


def test_cooldown_via_header_releases_a_quiet_chat(tmp_path):
    client = _dry_run_client(tmp_path)
    convo = _hard_then_calm("thanks", "ok")  # heavy turn, then two calm turns
    latched = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": convo},
        headers={"X-Wayfinder-Sticky": "true"},
    ).json()["wayfinder"]
    assert latched["model"] == "cloud" and latched["mode"] == "sticky"
    cooled = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": convo},
        headers={"X-Wayfinder-Sticky": "true", "X-Wayfinder-Sticky-Cooldown": "2"},
    ).json()["wayfinder"]
    assert cooled["model"] == "local" and cooled["mode"] == "scored"


def test_cooldown_from_config(tmp_path):
    client = _dry_run_client(
        tmp_path,
        config='[routing]\nthreshold = 0.2\n\n[gateway]\nsticky = true\nsticky_cooldown = 2\n',
    )
    wf = client.post(
        "/v1/chat/completions", json={"model": "auto", "messages": _hard_then_calm("thanks", "ok")}
    ).json()["wayfinder"]
    assert wf["model"] == "local"  # decayed back after two calm turns


def test_resolve_sticky_cooldown_header():
    assert gateway.resolve_sticky_cooldown(None, 3) == 3  # absent -> default
    assert gateway.resolve_sticky_cooldown("0", 3) == 0
    assert gateway.resolve_sticky_cooldown(" 5 ", 0) == 5
    with pytest.raises(gateway.BadOverride):
        gateway.resolve_sticky_cooldown("-1", 0)
    with pytest.raises(gateway.BadOverride):
        gateway.resolve_sticky_cooldown("soon", 0)


def test_bad_cooldown_header_returns_400(tmp_path):
    resp = _dry_run_client(tmp_path).post(
        "/v1/chat/completions", json=TRIVIAL, headers={"X-Wayfinder-Sticky-Cooldown": "lots"}
    )
    assert resp.status_code == 400


def test_cooldown_config_rejects_negative_and_round_trips():
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml("[gateway]\nsticky_cooldown = -2\n")
    dumped = gateway.dump_gateway_toml(gateway.GatewayConfig(sticky=True, sticky_cooldown=3))
    assert "sticky_cooldown = 3" in dumped
    assert gateway.gateway_config_from_toml(dumped).sticky_cooldown == 3


# --- in-demo scoring overrides + export (WF-ADR-0023) -----------------------

# Structurally trivial but lexically hard — the cold-start case sticky can't catch.
_COLD = {"model": "auto", "messages": [{"role": "user", "content": "Prove the halting problem is undecidable."}]}


def test_lexical_weight_override_catches_a_short_hard_prompt(tmp_path):
    client = _dry_run_client(tmp_path)  # threshold 0.2, lexical weights default 0.0
    base = client.post("/v1/chat/completions", json=_COLD).json()["wayfinder"]
    assert base["model"] == "local"  # no structure, no lexical -> cheap
    tuned = client.post(
        "/v1/chat/completions",
        json={**_COLD, "wayfinder_tuning": {"weights": {"reasoning_term_count": 6.0}}},
    ).json()["wayfinder"]
    assert tuned["model"] == "cloud"
    assert any(c["name"] == "reasoning_term_count" and c["contribution"] > 0 for c in tuned["contributions"])


def test_custom_lexicon_terms_override(tmp_path):
    client = _dry_run_client(tmp_path)
    # A word that isn't a default reasoning term; only fires once we add it.
    msg = {"model": "auto", "messages": [{"role": "user", "content": "Please frobnicate the widget."}]}
    tuned = client.post(
        "/v1/chat/completions",
        json={**msg, "wayfinder_tuning": {
            "weights": {"reasoning_term_count": 9.0},
            "lexicon": {"reasoning_terms": ["frobnicate"]},
        }},
    ).json()["wayfinder"]
    assert tuned["model"] == "cloud"


def test_apply_scoring_overrides_validates_and_is_pure():
    from wayfinder_router.complexity import RoutingConfig

    routing = RoutingConfig()
    assert gateway.apply_scoring_overrides(routing, None) is routing  # absent -> unchanged
    tuned = gateway.apply_scoring_overrides(routing, {"weights": {"word_count": 5.0}})
    assert tuned.weights["word_count"] == 5.0
    assert routing.weights["word_count"] != 5.0  # original untouched (pure)
    for bad in ({"weights": {"nope": 1.0}}, {"weights": {"word_count": -1}},
                {"weights": {"word_count": "x"}}, {"lexicon": {"reasoning_terms": [1, 2]}}, []):
        with pytest.raises(gateway.BadOverride):
            gateway.apply_scoring_overrides(routing, bad)


def test_tuning_is_not_forwarded_upstream(client, monkeypatch):
    test_client, _ = client
    seen = {}

    async def _spy(url, headers, json_body, timeout=60.0):
        seen.update(json_body)
        return 200, b'{"choices":[{"message":{"content":"ok"}}]}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", _spy)
    test_client.post(
        "/v1/chat/completions",
        json={**COMPLEX, "wayfinder_tuning": {"weights": {"word_count": 4.0}}},
    )
    assert "wayfinder_tuning" not in seen  # popped before the relay


def test_bad_tuning_returns_400(tmp_path):
    resp = _dry_run_client(tmp_path).post(
        "/v1/chat/completions", json={**_COLD, "wayfinder_tuning": {"weights": {"ghost": 1.0}}}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "wayfinder_router_bad_override"


def test_export_config_renders_tuned_routing_toml(tmp_path):
    client = _dry_run_client(tmp_path)
    toml = client.post(
        "/router/config",
        json={"weights": {"reasoning_term_count": 6.0}, "lexicon": {"reasoning_terms": ["prove", "qed"]}},
    ).text
    assert "[routing]" in toml and "reasoning_term_count = 6.0" in toml
    assert "[routing.lexicon]" in toml and '"prove"' in toml and '"qed"' in toml
    # round-trips back through the config parser
    from wayfinder_router.config import routing_config_from_toml

    cfg = routing_config_from_toml(toml)
    assert cfg.weights["reasoning_term_count"] == 6.0
    assert "prove" in cfg.lexicon.reasoning_terms


def test_export_config_rejects_bad_tuning(tmp_path):
    resp = _dry_run_client(tmp_path).post("/router/config", json={"weights": {"word_count": -3}})
    assert resp.status_code == 400


def test_demo_page_exposes_advanced_tuning(client):
    test_client, _ = client
    text = test_client.get("/demo").text
    assert 'id="lex"' in text and 'id="weights"' in text  # advanced controls present
    assert "/router/config" in text  # export wired


def test_router_profiles_endpoint_lists_curated_and_mined(client):
    test_client, _ = client
    data = test_client.get("/router/profiles").json()["profiles"]
    assert len(data) >= 4
    assert {"id", "name", "source", "reasoning_terms", "note"} <= set(data[0])
    assert any(p["source"] == "curated" for p in data)
    assert any(p["source"] == "mined" for p in data)


def test_demo_page_has_profile_picker(client):
    text = client[0].get("/demo").text
    assert 'id="profile"' in text and "/router/profiles" in text


# --- read-only models / key status (WF-ADR-0025) ----------------------------

def test_router_models_reports_endpoints_and_key_status_without_secrets(monkeypatch, tmp_path):
    cfg = (
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://localhost:11434/v1"\nmodel = "mistral:7b"\n\n'
        '[gateway.models.cloud]\nbase_url = "https://api.anthropic.com/v1"\nmodel = "claude-x"\n'
        'api_key_env = "WF_TEST_KEY"\n'
    )
    (tmp_path / "wayfinder-router.toml").write_text(cfg, encoding="utf-8")
    monkeypatch.delenv("WF_TEST_KEY", raising=False)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    by = {m["name"]: m for m in tc.get("/router/models").json()["models"]}
    assert by["local"]["api_key_env"] is None and by["local"]["key_ok"] is True  # no key needed
    assert by["cloud"]["api_key_env"] == "WF_TEST_KEY" and by["cloud"]["key_ok"] is False
    assert by["cloud"]["endpoint"] == "https://api.anthropic.com/v1"
    monkeypatch.setenv("WF_TEST_KEY", "secret-value")
    assert {m["name"]: m["key_ok"] for m in tc.get("/router/models").json()["models"]}["cloud"] is True
    assert "secret-value" not in tc.get("/router/models").text  # only the env-var name, never the value


def test_demo_page_has_models_status(client):
    text = client[0].get("/demo").text
    assert 'id="models"' in text and "/router/models" in text


# --- reliability: retry / same-tier fallback / circuit breaker (WF-ADR-0031) ---
_RELIABILITY_CONFIG = (
    "[gateway]\nretries = 1\nbreaker_threshold = 2\n\n"
    "[routing]\nthreshold = 0.2\n\n"
    "[gateway.models.local]\n"
    'base_url = "http://localhost:11434/v1"\nmodel = "llama3.2"\n\n'
    "[gateway.models.cloud]\n"
    'base_url = "https://primary.example.com/v1"\nmodel = "big-1"\nfallbacks = ["cloud2"]\n\n'
    "[gateway.models.cloud2]\n"
    'base_url = "https://backup.example.com/v1"\nmodel = "big-2"\n'
)


def _reliability_client(tmp_path, monkeypatch, fake):
    (tmp_path / "wayfinder-router.toml").write_text(_RELIABILITY_CONFIG, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path)))


def test_fallbacks_and_reliability_config_round_trip():
    gw = gateway.gateway_config_from_toml(_RELIABILITY_CONFIG)
    assert gw.models["cloud"].fallbacks == ("cloud2",)
    assert gw.retries == 1 and gw.breaker_threshold == 2
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw))
    assert back.models["cloud"].fallbacks == ("cloud2",)
    assert back.retries == 1 and back.breaker_threshold == 2


def test_unknown_fallback_is_a_config_error():
    bad = "[gateway.models.a]\nbase_url='http://x/v1'\nmodel='m'\nfallbacks=['nope']\n"
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(bad)


def test_retry_then_success_on_transient_failure(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake(url, headers, json_body, timeout=60.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise gateway.UpstreamError("transient")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _reliability_client(tmp_path, monkeypatch, fake)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # -> local
    assert resp.status_code == 200
    assert calls["n"] == 2  # failed once, retried, succeeded
    assert resp.headers["x-wayfinder-router-served-by"] == "local"
    assert "x-wayfinder-router-failover" not in resp.headers


def test_same_tier_fallback_when_primary_keeps_failing(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        if "primary.example.com" in url:
            raise gateway.UpstreamError("primary down")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _reliability_client(tmp_path, monkeypatch, fake)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # cloud fails -> cloud2 serves
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-served-by"] == "cloud2"
    assert resp.headers["x-wayfinder-router-failover"] == "true"


def test_non_retryable_4xx_returned_without_fallback(tmp_path, monkeypatch):
    seen = []

    async def fake(url, headers, json_body, timeout=60.0):
        seen.append(url)
        return 400, b'{"error": {"message": "bad request"}}', "application/json"

    tc = _reliability_client(tmp_path, monkeypatch, fake)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # cloud returns 400
    assert resp.status_code == 400
    assert all("backup.example.com" not in u for u in seen)  # cloud2 never tried
    assert resp.headers["x-wayfinder-router-served-by"] == "cloud"


def test_circuit_opens_after_threshold_then_fails_fast(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        raise gateway.UpstreamError("always down")

    tc = _reliability_client(tmp_path, monkeypatch, fake)  # local has no fallback
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 502  # failure 1
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 502  # failure 2 -> opens
    third = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert third.status_code == 503
    assert third.json()["error"]["type"] == "wayfinder_router_circuit_open"


# --- reliability Phase 2: cross-tier failover + pre-call checks (WF-ADR-0031) ---
def _failover_config(failover: str, *, cloud_ctx: int | None = None) -> str:
    cloud_extra = f"context_window = {cloud_ctx}\n" if cloud_ctx else ""
    return (
        f'[gateway]\nretries = 0\nfailover = "{failover}"\n\n'
        "[routing]\nthreshold = 0.2\n\n"
        "[gateway.models.local]\n"
        'base_url = "http://local.test/v1"\nmodel = "m-local"\n\n'
        "[gateway.models.cloud]\n"
        'base_url = "http://cloud.test/v1"\nmodel = "m-cloud"\n' + cloud_extra
    )


def _failover_client(tmp_path, monkeypatch, config, fake):
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path)))


def test_failover_and_context_window_round_trip():
    cfg = _failover_config("degrade", cloud_ctx=8000)
    gw = gateway.gateway_config_from_toml(cfg)
    assert gw.failover == "degrade"
    assert gw.models["cloud"].context_window == 8000
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw))
    assert back.failover == "degrade" and back.models["cloud"].context_window == 8000


def test_failover_degrade_serves_cheaper_tier_without_recomputing_decision(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        if "cloud.test" in url:
            raise gateway.UpstreamError("cloud down")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("degrade"), fake)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # routes to cloud -> degrade to local
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # decision unchanged
    assert resp.headers["x-wayfinder-router-served-by"] == "local"  # delivery changed
    assert resp.headers["x-wayfinder-router-failover"] == "true"


def test_failover_escalate_serves_dearer_tier(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        if "local.test" in url:
            raise gateway.UpstreamError("local down")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("escalate"), fake)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # routes to local -> escalate to cloud
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-served-by"] == "cloud"


def test_failover_same_tier_default_does_not_cross_tiers(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        if "cloud.test" in url:
            raise gateway.UpstreamError("cloud down")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("same-tier"), fake)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # cloud fails, no cross-tier
    assert resp.status_code == 502  # does NOT silently fall to local


def test_failover_header_overrides_config_policy(tmp_path, monkeypatch):
    async def fake(url, headers, json_body, timeout=60.0):
        if "cloud.test" in url:
            raise gateway.UpstreamError("cloud down")
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("same-tier"), fake)
    resp = tc.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Failover": "degrade"}
    )
    assert resp.status_code == 200  # the header enabled degrade for this request
    assert resp.headers["x-wayfinder-router-served-by"] == "local"


def test_precall_skips_target_that_cannot_fit_the_prompt(tmp_path, monkeypatch):
    seen = []

    async def fake(url, headers, json_body, timeout=60.0):
        seen.append(url)
        return 200, b'{"object":"chat.completion"}', "application/json"

    # cloud's window is 1 token; the COMPLEX prompt can't fit, so cloud is skipped pre-call
    # and degrade serves local — cloud is never even called.
    tc = _failover_client(tmp_path, monkeypatch, _failover_config("degrade", cloud_ctx=1), fake)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-served-by"] == "local"
    assert all("cloud.test" not in u for u in seen)  # pre-call skipped cloud before any call


# --- budgets: a spend cap that degrades to the cheapest tier or blocks (WF-ROADMAP-0006) ---
def _budget_config(limit, *, window="day", on_breach="degrade", priced=True) -> str:
    """A two-tier config (local 0.0 / cloud 1.0 per 1k) with a `[gateway.budget]` cap.

    ``priced=False`` drops the cost metadata so the price table falls back to relative
    units — exercising that a budget is a no-op when there are no real dollars to cap.
    """
    cost_local = "cost_per_1k = 0.0\n" if priced else ""
    cost_cloud = "cost_per_1k = 1.0\n" if priced else ""
    budget = [f"[gateway.budget]\nlimit = {limit}"]
    if window != "day":
        budget.append(f'window = "{window}"')
    if on_breach != "degrade":
        budget.append(f'on_breach = "{on_breach}"')
    return (
        "\n".join(budget) + "\n\n"
        "[routing]\nthreshold = 0.2\n\n"
        "[gateway.models.local]\n"
        'base_url = "http://local.test/v1"\nmodel = "m-local"\n' + cost_local + "\n"
        "[gateway.models.cloud]\n"
        'base_url = "http://cloud.test/v1"\nmodel = "m-cloud"\n' + cost_cloud
    )


def _budget_client(tmp_path, monkeypatch, config):
    """A client over ``config`` whose upstream always returns 200; counts upstream calls."""
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    calls = {"n": 0}

    async def fake(url, headers, json_body, timeout=60.0):
        calls["n"] += 1
        return 200, b'{"object":"chat.completion"}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path))), calls


def test_budget_degrade_routes_complex_to_cheapest_tier_after_breach(tmp_path, monkeypatch):
    tc, calls = _budget_client(tmp_path, monkeypatch, _budget_config(0.001))
    first = tc.post("/v1/chat/completions", json=COMPLEX)  # spend starts at 0 -> cloud
    assert first.status_code == 200
    assert first.headers["x-wayfinder-router-model"] == "cloud"
    assert "x-wayfinder-router-budget" not in first.headers

    second = tc.post("/v1/chat/completions", json=COMPLEX)  # now over budget -> degrade to local
    assert second.status_code == 200
    assert second.headers["x-wayfinder-router-model"] == "local"  # route overridden
    assert second.headers["x-wayfinder-router-mode"] == "budget-degraded"
    assert second.headers["x-wayfinder-router-budget"] == "degraded"
    assert second.headers["x-wayfinder-router-served-by"] == "local"
    # The decision (score) is unchanged — it still scores as a cloud-worthy prompt; only the
    # route the gateway delivers it to changed (WF-ADR-0001).
    assert float(second.headers["x-wayfinder-router-score"]) >= 0.2
    assert calls["n"] == 2  # both requests reached an upstream (local is still a real call)


def test_budget_block_returns_402_after_breach(tmp_path, monkeypatch):
    tc, calls = _budget_client(tmp_path, monkeypatch, _budget_config(0.001, on_breach="block"))
    assert tc.post("/v1/chat/completions", json=COMPLEX).status_code == 200  # under budget
    blocked = tc.post("/v1/chat/completions", json=COMPLEX)  # over budget -> blocked
    assert blocked.status_code == 402
    assert blocked.json()["error"]["type"] == "wayfinder_router_budget_exhausted"
    assert blocked.headers["x-wayfinder-router-budget"] == "blocked"
    assert calls["n"] == 1  # the blocked request never reached an upstream


def test_under_budget_routes_normally(tmp_path, monkeypatch):
    tc, _ = _budget_client(tmp_path, monkeypatch, _budget_config(1000.0))
    for _ in range(3):
        resp = tc.post("/v1/chat/completions", json=COMPLEX)
        assert resp.status_code == 200
        assert resp.headers["x-wayfinder-router-model"] == "cloud"  # never degraded
        assert resp.headers["x-wayfinder-router-mode"] == "scored"
        assert "x-wayfinder-router-budget" not in resp.headers


def test_budget_is_a_no_op_without_real_prices(tmp_path, monkeypatch):
    # Relative-unit demo (no cost_per_1k): there are no dollars to cap, so the budget never
    # fires even though the tiny limit is "exceeded" in relative units.
    tc, _ = _budget_client(tmp_path, monkeypatch, _budget_config(0.001, priced=False))
    for _ in range(3):
        resp = tc.post("/v1/chat/completions", json=COMPLEX)
        assert resp.status_code == 200
        assert resp.headers["x-wayfinder-router-model"] == "cloud"
        assert "x-wayfinder-router-budget" not in resp.headers


def test_budget_config_round_trips():
    gw = gateway.gateway_config_from_toml(_budget_config(2.5, window="month", on_breach="block"))
    assert gw.budget == gateway.Budget(limit=2.5, window="month", on_breach="block")
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw))
    assert back.budget == gw.budget


def test_budget_config_defaults_round_trip():
    gw = gateway.gateway_config_from_toml(_budget_config(5))
    assert gw.budget == gateway.Budget(limit=5.0, window="day", on_breach="degrade")
    dumped = gateway.dump_gateway_toml(gw)
    assert "window" not in dumped and "on_breach" not in dumped  # defaults omitted
    assert gateway.gateway_config_from_toml(dumped).budget == gw.budget


@pytest.mark.parametrize(
    "table",
    [
        "[gateway.budget]\nlimit = 0\n",  # not positive
        "[gateway.budget]\nlimit = -1.0\n",  # negative
        "[gateway.budget]\nlimit = true\n",  # bool is not a number
        '[gateway.budget]\nlimit = 1.0\nwindow = "year"\n',  # bad window
        '[gateway.budget]\nlimit = 1.0\non_breach = "panic"\n',  # bad breach
        "[gateway.budget]\nwindow = \"day\"\n",  # missing limit
        "[gateway]\nbudget = 5\n",  # budget must be a table
    ],
)
def test_bad_budget_config_rejected(table):
    config = table + '\n[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(config)


# --- Claude Code adapter: Anthropic /v1/messages translation (WF-DESIGN-0011) ---
def _messages_client(tmp_path, monkeypatch, *, completion="ok", reply=None, stream_chunks=None):
    """A client over CONFIG; the fake upstream returns ``reply`` (or an OpenAI completion)."""
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        body = reply if reply is not None else {
            "id": "cmpl-1",
            "choices": [{"message": {"role": "assistant", "content": completion},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        return 200, json.dumps(body).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    if stream_chunks is not None:
        async def fake_stream(url, headers, json_body, timeout=60.0):
            for chunk in stream_chunks:
                yield chunk
        monkeypatch.setattr(gateway, "aforward_stream", fake_stream)
    return TestClient(gateway.build_app(start_dir=str(tmp_path)))


def test_messages_non_streaming_round_trip(tmp_path, monkeypatch):
    tc = _messages_client(tmp_path, monkeypatch, completion="Hi there")
    resp = tc.post("/v1/messages", json={
        "model": "claude-opus-4", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "message" and body["role"] == "assistant"
    assert body["model"] == "claude-opus-4"  # the requested id is echoed back
    assert body["content"] == [{"type": "text", "text": "Hi there"}]
    assert body["stop_reason"] == "end_turn"
    assert body["usage"] == {"input_tokens": 5, "output_tokens": 3}
    assert body["id"].startswith("msg_")


def test_messages_decision_headers_match_chat_completions(tmp_path, monkeypatch):
    # Same logical prompt via both endpoints -> identical scored decision (one router, WF-ADR-0001).
    tc = _messages_client(tmp_path, monkeypatch)
    text = COMPLEX_TEXT
    via_oai = tc.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": text}]})
    via_anthropic = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 16, "messages": [{"role": "user", "content": text}]})
    for header in ("x-wayfinder-router-model", "x-wayfinder-router-score", "x-wayfinder-router-mode"):
        assert via_anthropic.headers[header] == via_oai.headers[header]
    assert via_anthropic.headers["x-wayfinder-router-model"] == "cloud"


def test_messages_path_tolerance_without_v1(tmp_path, monkeypatch):
    tc = _messages_client(tmp_path, monkeypatch, completion="x")
    resp = tc.post("/messages", json={
        "model": "claude-x", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200 and resp.json()["type"] == "message"


def test_messages_tool_call_round_trip(tmp_path, monkeypatch):
    reply = {
        "id": "cmpl-2",
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }
    tc = _messages_client(tmp_path, monkeypatch, reply=reply)
    resp = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 64,
        "messages": [{"role": "user", "content": "weather in Paris?"}],
        "tools": [{"name": "get_weather", "input_schema": {"type": "object"}}],
    })
    body = resp.json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}]


def test_messages_streaming_event_sequence(tmp_path, monkeypatch):
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    tc = _messages_client(tmp_path, monkeypatch, stream_chunks=chunks)
    resp = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 64, "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    text = resp.text
    assert "event: message_start" in text
    assert '"type":"text_delta","text":"Hel"' in text
    assert "event: content_block_stop" in text
    assert '"stop_reason":"end_turn"' in text
    assert text.rstrip().endswith("event: message_stop\ndata: {\"type\":\"message_stop\"}")


def test_messages_error_is_reshaped_to_anthropic_envelope(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        return 400, b'{"error": {"message": "bad request", "type": "x"}}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 400
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["message"] == "bad request"


def test_messages_budget_degrade_is_surfaced(tmp_path, monkeypatch):
    config = _budget_config(0.001)  # local 0.0 / cloud 1.0, day cap
    tc = _budget_messages_client(tmp_path, monkeypatch, config)
    first = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 64, "messages": [{"role": "user", "content": COMPLEX_TEXT}]})
    assert first.headers["x-wayfinder-router-model"] == "cloud"  # over... not yet
    second = tc.post("/v1/messages", json={
        "model": "claude-x", "max_tokens": 64, "messages": [{"role": "user", "content": COMPLEX_TEXT}]})
    assert second.status_code == 200
    assert second.headers["x-wayfinder-router-model"] == "local"  # degraded
    assert second.headers["x-wayfinder-router-budget"] == "degraded"
    assert second.json()["type"] == "message"


def _budget_messages_client(tmp_path, monkeypatch, config):
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 0},
        }).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path)))


# --- exact-match response cache (WF-ADR-0033) ---
_CACHE_MODELS = (
    "[routing]\nthreshold = 0.2\n\n"
    '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\ncost_per_1k = 0.0\n\n'
    '[gateway.models.cloud]\nbase_url = "http://cloud.test/v1"\nmodel = "m-cloud"\ncost_per_1k = 1.0\n'
)
_CACHE_ON = "[gateway.cache]\nenabled = true\n\n" + _CACHE_MODELS


def _cache_client(tmp_path, monkeypatch, config, *, payload=None):
    """A client whose upstream returns a valid (or given) 200 completion; counts upstream calls."""
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    calls = {"n": 0}
    body = payload if payload is not None else {
        "choices": [{"message": {"role": "assistant", "content": "hello there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        "object": "chat.completion",
    }

    async def fake(url, headers, json_body, timeout=60.0):
        calls["n"] += 1
        return 200, json.dumps(body).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path))), calls


def _metric(tc, name):
    for line in tc.get("/metrics").text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[-1])
    return None


def test_cache_hit_serves_without_second_upstream_call(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON)
    first = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert first.status_code == 200
    assert first.headers["x-wayfinder-router-cache"] == "miss"
    second = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert second.status_code == 200
    assert second.headers["x-wayfinder-router-cache"] == "hit"
    assert second.headers["x-wayfinder-router-served-by"] == "local"
    assert second.content == first.content  # byte-identical replay
    assert calls["n"] == 1  # the upstream was called exactly once


def test_cache_off_by_default(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_MODELS)  # no [gateway.cache]
    tc.post("/v1/chat/completions", json=TRIVIAL)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert calls["n"] == 2  # no caching happens
    assert "x-wayfinder-router-cache" not in resp.headers


def test_nonzero_temperature_is_not_cached(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON)
    req = {**TRIVIAL, "temperature": 0.7}
    tc.post("/v1/chat/completions", json=req)
    resp = tc.post("/v1/chat/completions", json=req)
    assert calls["n"] == 2  # sampling request passes through uncached
    assert "x-wayfinder-router-cache" not in resp.headers


def test_tools_request_is_not_cached(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON)
    req = {**TRIVIAL, "tools": [{"type": "function", "function": {"name": "f"}}]}
    tc.post("/v1/chat/completions", json=req)
    tc.post("/v1/chat/completions", json=req)
    assert calls["n"] == 2  # tool requests are never cached


def test_cache_skips_error_shaped_200(tmp_path, monkeypatch):
    # An upstream that returns HTTP 200 with an error body must NOT be cached and replayed.
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON, payload={"error": {"message": "overloaded"}})
    tc.post("/v1/chat/completions", json=TRIVIAL)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert calls["n"] == 2  # the poisoned 200 was not stored
    assert resp.headers["x-wayfinder-router-cache"] == "miss"


def test_cache_hit_is_free_and_does_not_add_realized_cost(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON)
    tc.post("/v1/chat/completions", json=COMPLEX)  # cloud, cost > 0, miss -> records realized
    realized_after_miss = _metric(tc, "wayfinder_router_realized_cost_total")
    assert realized_after_miss > 0
    tc.post("/v1/chat/completions", json=COMPLEX)  # identical -> hit
    assert calls["n"] == 1
    # A hit is free: realized spend (what budget.spent() reads) is unchanged; the avoided cost
    # is reported on a separate cache counter instead.
    assert _metric(tc, "wayfinder_router_realized_cost_total") == realized_after_miss
    assert _metric(tc, "wayfinder_router_cache_hits_total") == 1
    assert _metric(tc, "wayfinder_router_cache_avoided_cost_total") > 0


def test_cache_covers_v1_messages(tmp_path, monkeypatch):
    tc, calls = _cache_client(tmp_path, monkeypatch, _CACHE_ON)
    req = {"model": "claude-x", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    tc.post("/v1/messages", json=req)
    resp = tc.post("/v1/messages", json=req)
    assert calls["n"] == 1  # the Anthropic endpoint dedupes via the same cache layer
    assert resp.headers.get("x-wayfinder-router-cache") == "hit"


def test_streaming_is_not_cached(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(_CACHE_ON, encoding="utf-8")
    calls = {"req": 0, "stream": 0}

    async def fake_req(url, headers, json_body, timeout=60.0):
        calls["req"] += 1
        return 200, json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        ).encode(), "application/json"

    async def fake_stream(url, headers, json_body, timeout=60.0):
        calls["stream"] += 1
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(gateway, "aforward_request", fake_req)
    monkeypatch.setattr(gateway, "aforward_stream", fake_stream)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    tc.post("/v1/chat/completions", json=TRIVIAL)  # non-stream miss -> stored
    resp = tc.post("/v1/chat/completions", json={**TRIVIAL, "stream": True})  # stream bypasses cache
    assert calls["stream"] == 1  # the stream request reached the upstream, not the cache
    assert "x-wayfinder-router-cache" not in resp.headers


def test_cache_config_round_trips():
    gw = gateway.gateway_config_from_toml(
        "[gateway.cache]\nenabled = true\nttl = 600\nmax_entries = 2048\nmax_bytes = 134217728\n\n"
        + _CACHE_MODELS
    )
    assert gw.cache == gateway.CacheConfig(enabled=True, ttl=600.0, max_entries=2048, max_bytes=134217728)
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw))
    assert back.cache == gw.cache


def test_cache_config_defaults_round_trip():
    gw = gateway.gateway_config_from_toml("[gateway.cache]\nenabled = true\n\n" + _CACHE_MODELS)
    assert gw.cache == gateway.CacheConfig(enabled=True)
    dumped = gateway.dump_gateway_toml(gw)
    assert "ttl" not in dumped and "max_entries" not in dumped and "max_bytes" not in dumped
    assert gateway.gateway_config_from_toml(dumped).cache == gw.cache


@pytest.mark.parametrize(
    "table",
    [
        '[gateway.cache]\nenabled = "yes"\n',  # not a bool
        "[gateway.cache]\nttl = -1\n",  # negative
        "[gateway.cache]\nmax_entries = 0\n",  # not positive
        "[gateway.cache]\nmax_bytes = 0\n",  # not positive
        "[gateway]\ncache = 5\n",  # cache must be a table
    ],
)
def test_bad_cache_config_rejected(table):
    config = table + '\n[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(config)
