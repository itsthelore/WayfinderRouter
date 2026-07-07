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

from wayfinder_router import gateway, vkeys  # noqa: E402

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


# --- Decision-only degrade: a no-models live gateway answers with the decision (WF-ADR-0042) ---

_NO_MODELS_CONFIG = "[routing]\nthreshold = 0.2\n"


def test_no_models_live_returns_decision_only(tmp_path, monkeypatch):
    # The onboarding cold-start: a LIVE gateway with no [gateway.models] returns the routing
    # decision (HTTP 200) instead of a 500, and never contacts an upstream.
    (tmp_path / "wayfinder-router.toml").write_text(_NO_MODELS_CONFIG, encoding="utf-8")
    called = False

    async def fail_aforward(*a, **k):
        nonlocal called
        called = True
        return 200, b"{}", "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fail_aforward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-decision-only"] == "true"
    body = resp.json()["wayfinder"]
    assert body["decision_only"] is True
    assert "dry_run" not in body
    assert body["model"] and isinstance(body["score"], (int, float))
    assert called is False  # no upstream was contacted — the decision stays offline (WF-ADR-0001)


def test_dry_run_still_flags_dry_run_not_decision_only(tmp_path, monkeypatch):
    # An explicit --dry-run (models configured) keeps its own flag; it is not decision-only.
    (tmp_path / "wayfinder-router.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    test_client = TestClient(gateway.build_app(start_dir=str(tmp_path), dry_run=True))
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    body = resp.json()["wayfinder"]
    assert body["dry_run"] is True
    assert "decision_only" not in body
    assert "x-wayfinder-router-decision-only" not in resp.headers


def test_no_models_live_decision_matches_dry_run(tmp_path, monkeypatch):
    # The decision a no-models live gateway returns is identical to the dry-run decision for the
    # same prompt+config — only DELIVERY is skipped, the decision is unchanged (WF-ADR-0001).
    (tmp_path / "wayfinder-router.toml").write_text(_NO_MODELS_CONFIG, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", _ok_aforward)
    live = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    dry = TestClient(gateway.build_app(start_dir=str(tmp_path), dry_run=True))
    lw = live.post("/v1/chat/completions", json=COMPLEX).json()["wayfinder"]
    dw = dry.post("/v1/chat/completions", json=COMPLEX).json()["wayfinder"]
    assert (lw["model"], lw["score"], lw["features"]) == (dw["model"], dw["score"], dw["features"])
    assert lw["decision_only"] is True and dw["dry_run"] is True


def test_response_body_is_relayed_unchanged(client):
    test_client, _ = client
    resp = test_client.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.json() == {"id": "resp-1", "object": "chat.completion"}


# --- Offline-first delivery (WF-ADR-0039) -----------------------------------

_OFFLINE_CONFIG = (
    "[routing]\nthreshold = 0.2\n\n"
    "[gateway]\noffline = true\n\n"
    "[gateway.models.local]\n"
    'base_url = "http://localhost:11434/v1"\n'
    'model = "llama3.2"\n\n'
    "[gateway.models.cloud]\n"
    'base_url = "https://api.example.com/v1"\n'
    'model = "big-model"\n'
    'api_key_env = "EXAMPLE_API_KEY"\n'
)


def test_offline_config_forces_cheapest_tier(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(_OFFLINE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    captured: dict = {}

    async def fake(url, headers, json_body, timeout=60.0):
        captured["url"] = url
        captured["body"] = json_body
        return 200, b'{"id": "r"}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = tc.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    # The scored decision is unchanged (the complex prompt still *scores* cloud)...
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    # ...but delivery degrades to the cheapest/local tier, and the dear tier is never called.
    assert resp.headers["x-wayfinder-router-offline"] == "true"
    assert resp.headers["x-wayfinder-router-served-by"] == "local"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["body"]["model"] == "llama3.2"
    # An offline degrade is signaled by the offline header, not as a failover.
    assert "x-wayfinder-router-failover" not in resp.headers


def test_offline_header_forces_cheapest_tier(client, monkeypatch):
    test_client, captured = client  # CONFIG has offline off; the header turns it on per request
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post(
        "/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Offline": "true"}
    )
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-offline"] == "true"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"  # served local, not cloud


def test_offline_off_by_default_still_routes_cloud(client, monkeypatch):
    test_client, captured = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post("/v1/chat/completions", json=COMPLEX)
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert "x-wayfinder-router-offline" not in resp.headers


def test_offline_config_round_trips():
    cfg = gateway.gateway_config_from_toml(
        '[gateway]\noffline = true\n\n[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    )
    assert cfg.offline is True
    dumped = gateway.dump_gateway_toml(cfg)
    assert "offline = true" in dumped
    assert gateway.gateway_config_from_toml(dumped).offline is True


def test_offline_must_be_boolean():
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml('[gateway]\noffline = "yes"\n')


def test_offline_does_not_replay_cloud_cache(tmp_path, monkeypatch):
    # WF-ADR-0039: a cloud answer cached while online must NOT be replayed for an offline
    # request — offline serves (and keys the cache on) the cheapest/local tier instead.
    (tmp_path / "wayfinder-router.toml").write_text(_CACHE_ON, encoding="utf-8")
    seen: list[str] = []

    async def fake(url, headers, json_body, timeout=60.0):
        seen.append(url)
        tag = "cloud" if "cloud.test" in url else "local"
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": f"answer from {tag}"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            "object": "chat.completion",
        }
        return 200, json.dumps(body).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))

    # 1) Online: COMPLEX scores cloud, is served by the cloud tier, and cached under its key.
    online = tc.post("/v1/chat/completions", json=COMPLEX)
    assert online.headers["x-wayfinder-router-served-by"] == "cloud"
    assert online.headers["x-wayfinder-router-cache"] == "miss"
    assert b"answer from cloud" in online.content
    assert seen[-1].startswith("http://cloud.test")

    # 2) Offline, same prompt: the decision is unchanged (cloud) but delivery degrades to local;
    #    the cloud cache entry is NOT replayed — the local upstream is called and marked offline.
    off = tc.post("/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Offline": "true"})
    assert off.status_code == 200
    assert off.headers["x-wayfinder-router-model"] == "cloud"
    assert off.headers["x-wayfinder-router-served-by"] == "local"
    assert off.headers["x-wayfinder-router-offline"] == "true"
    assert off.headers["x-wayfinder-router-cache"] == "miss"
    assert b"answer from local" in off.content  # not the cached cloud body
    assert seen[-1].startswith("http://local.test")

    # 3) A second offline request replays the LOCAL tier's own cached answer, still marked offline.
    again = tc.post("/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Offline": "true"})
    assert again.headers["x-wayfinder-router-cache"] == "hit"
    assert again.headers["x-wayfinder-router-served-by"] == "local"
    assert again.headers["x-wayfinder-router-offline"] == "true"
    assert b"answer from local" in again.content


def test_offline_overrides_budget_block(tmp_path, monkeypatch):
    # WF-ADR-0039: a hard budget block must not 402 an offline request — offline delivery routes
    # to the cheapest/local tier (zero cloud spend), so the block softens to a degrade.
    tc, calls = _budget_client(tmp_path, monkeypatch, _budget_config(0.001, on_breach="block"))
    first = tc.post("/v1/chat/completions", json=COMPLEX)  # under budget -> cloud
    assert first.status_code == 200
    assert first.headers["x-wayfinder-router-served-by"] == "cloud"

    # Now over budget. Online this is a 402 (see test_budget_block_returns_402_after_breach);
    # with offline on it is delivered from local instead, never rejected.
    off = tc.post("/v1/chat/completions", json=COMPLEX, headers={"X-Wayfinder-Offline": "true"})
    assert off.status_code == 200
    assert off.headers["x-wayfinder-router-served-by"] == "local"
    assert off.headers["x-wayfinder-router-offline"] == "true"
    assert off.headers["x-wayfinder-router-budget"] == "degraded"
    assert off.headers["x-wayfinder-router-model"] == "cloud"  # the scored decision is unchanged
    assert calls["n"] == 2  # both requests reached an upstream; neither was blocked


def test_healthz_reports_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    (tmp_path / "wayfinder-router.toml").write_text(_OFFLINE_CONFIG, encoding="utf-8")
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    assert tc.get("/healthz").json()["offline"] is True


def test_healthz_offline_false_by_default(client):
    test_client, _ = client  # CONFIG has no [gateway] offline
    assert test_client.get("/healthz").json()["offline"] is False


def test_explain_payload_marks_offline(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setenv("EXAMPLE_API_KEY", "sekret")
    resp = test_client.post(
        "/v1/chat/completions",
        json=COMPLEX,
        headers={"X-Wayfinder-Offline": "true", "X-Wayfinder-Debug": "true"},
    )
    assert resp.json()["wayfinder"]["offline"] is True
    assert resp.headers["x-wayfinder-router-offline"] == "true"


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
    assert set(first) == {"request_id", "model", "score", "mode", "ts", "cost", "decision_ms"}
    # The cost block is dollars + token counts only — still no prompt text (WF-DESIGN-0007/0008).
    assert set(first["cost"]) == {"realized", "baseline", "saved", "tokens", "unit", "estimated"}
    assert first["model"] == "cloud"
    assert "a secret prompt body" not in test_client.get("/router/recent").text
    assert isinstance(body["p50_decision_ms"], float) and body["p50_decision_ms"] >= 0


def test_router_recent_p50_decision_ms_is_none_with_no_history(client):
    test_client, _ = client
    body = test_client.get("/router/recent").json()
    assert body["total"] == 0
    assert body["p50_decision_ms"] is None


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


def test_router_models_reports_context_window_and_enabled(tmp_path):
    cfg = (
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://localhost:11434/v1"\nmodel = "m"\n\n'
        '[gateway.models.cloud]\nbase_url = "https://api.anthropic.com/v1"\nmodel = "m2"\n'
        "context_window = 200000\nenabled = false\n"
    )
    (tmp_path / "wayfinder-router.toml").write_text(cfg, encoding="utf-8")
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    by = {m["name"]: m for m in tc.get("/router/models").json()["models"]}
    assert by["local"]["context_window"] is None and by["local"]["enabled"] is True  # unset defaults
    assert by["cloud"]["context_window"] == 200000 and by["cloud"]["enabled"] is False


def test_router_models_carries_the_tier_ladder(tmp_path):
    cfg = (
        '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n\n'
        '[[routing.tiers]]\nmin_score = 0.6\nmodel = "cloud"\n\n'
        '[gateway.models.local]\nbase_url = "http://localhost:11434/v1"\nmodel = "m"\n\n'
        '[gateway.models.cloud]\nbase_url = "https://api.anthropic.com/v1"\nmodel = "m2"\n'
    )
    (tmp_path / "wayfinder-router.toml").write_text(cfg, encoding="utf-8")
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    tiers = tc.get("/router/models").json()["tiers"]
    assert tiers == [{"model": "local", "min_score": 0.0}, {"model": "cloud", "min_score": 0.6}]


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


def test_enabled_defaults_true_and_round_trips():
    gw = gateway.gateway_config_from_toml(_RELIABILITY_CONFIG)
    assert gw.models["local"].enabled is True  # default, unset in the fixture TOML
    disabled = "[gateway.models.a]\nbase_url='http://x/v1'\nmodel='m'\nenabled=false\n"
    assert gateway.gateway_config_from_toml(disabled).models["a"].enabled is False
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gateway.gateway_config_from_toml(disabled)))
    assert back.models["a"].enabled is False


def test_enabled_must_be_a_boolean():
    bad = "[gateway.models.a]\nbase_url='http://x/v1'\nmodel='m'\nenabled='nope'\n"
    with pytest.raises(gateway.WayfinderConfigError, match="must be a boolean"):
        gateway.gateway_config_from_toml(bad)


def test_disabled_model_is_skipped_at_delivery_never_at_decision(tmp_path, monkeypatch):
    # cloud is disabled but still the SCORED decision (WF-ADR-0001: enabled is delivery-only) —
    # delivery falls through to its same-tier fallback exactly like a broken endpoint would,
    # and primary.example.com is never even attempted (unlike a live failure, which IS tried).
    config = _RELIABILITY_CONFIG.replace(
        'base_url = "https://primary.example.com/v1"\nmodel = "big-1"\nfallbacks = ["cloud2"]\n',
        'base_url = "https://primary.example.com/v1"\nmodel = "big-1"\nfallbacks = ["cloud2"]\nenabled = false\n',
    )
    seen = []

    async def fake(url, headers, json_body, timeout=60.0):
        seen.append(url)
        return 200, b'{"object":"chat.completion"}', "application/json"

    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = tc.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # decision: unchanged
    assert resp.headers["x-wayfinder-router-served-by"] == "cloud2"  # delivery: fell through
    assert all("primary.example.com" not in u for u in seen)  # never even attempted


def test_disabled_model_with_no_fallback_fails_fast_with_a_clear_error(tmp_path, monkeypatch):
    config = (
        "[routing]\nthreshold = 0.2\n\n"
        "[gateway.models.local]\nbase_url = \"http://local.test/v1\"\nmodel = \"m-local\"\nenabled = false\n"
    )

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, b'{"object":"chat.completion"}', "application/json"

    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 503  # every candidate (just "local") is filtered out


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


def test_auth_failure_opens_breaker_then_degrades(tmp_path, monkeypatch):
    # WF-ADR-0031: a stale/expired upstream key (401) is a target failure, not a "success." Repeats
    # open the breaker so the next request degrades to the local tier instead of forever 401-ing.
    async def fake(url, headers, json_body, timeout=60.0):
        if "cloud.test" in url:
            return 401, b'{"error": {"message": "invalid api key"}}', "application/json"
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("degrade"), fake)
    for _ in range(5):  # the default breaker threshold is 5 consecutive failures
        r = tc.post("/v1/chat/completions", json=COMPLEX)  # scores cloud -> bad key -> 401
        assert r.status_code == 401
        assert r.headers["x-wayfinder-router-served-by"] == "cloud"
    # The breaker is now open for cloud, so delivery degrades to local on the next request.
    degraded = tc.post("/v1/chat/completions", json=COMPLEX)
    assert degraded.status_code == 200
    assert degraded.headers["x-wayfinder-router-served-by"] == "local"


def test_client_4xx_does_not_open_breaker(tmp_path, monkeypatch):
    # An ordinary client 4xx (the caller's fault) means the target is reachable, so it must NOT count
    # as a breaker failure — repeated 400s never spuriously degrade an otherwise-healthy upstream.
    async def fake(url, headers, json_body, timeout=60.0):
        if "cloud.test" in url:
            return 400, b'{"error": {"message": "bad request"}}', "application/json"
        return 200, b'{"object":"chat.completion"}', "application/json"

    tc = _failover_client(tmp_path, monkeypatch, _failover_config("degrade"), fake)
    for _ in range(8):  # well past the breaker threshold
        r = tc.post("/v1/chat/completions", json=COMPLEX)
        assert r.status_code == 400
        assert r.headers["x-wayfinder-router-served-by"] == "cloud"  # never degrades


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


def test_budget_uses_current_priced_state_after_reload(tmp_path, monkeypatch):
    # The budget gate reads priced-ness from the *current* config, not the ledger's lagging flag: a
    # hot reload that drops cost_per_1k makes the very next request unpriced (budget a no-op), not
    # one request late. With the stale-flag bug this request would still be 402'd.
    config = tmp_path / "wayfinder-router.toml"
    models = (
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\n{lc}\n'
        '[gateway.models.cloud]\nbase_url = "http://cloud.test/v1"\nmodel = "m-cloud"\n{cc}'
    )
    budget = '[gateway.budget]\nlimit = 0.001\non_breach = "block"\n\n'
    config.write_text(
        budget + models.format(lc="cost_per_1k = 0.0\n", cc="cost_per_1k = 1.0\n"), encoding="utf-8"
    )

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, b'{"object":"chat.completion"}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    assert tc.post("/v1/chat/completions", json=COMPLEX).status_code == 200  # priced; spends > cap
    assert tc.post("/v1/chat/completions", json=COMPLEX).status_code == 402  # now over budget

    # Hot-reload to an UNPRICED config (no cost_per_1k anywhere): the budget must go no-op at once.
    config.write_text(budget + models.format(lc="", cc=""), encoding="utf-8")
    future = time.time() + 10  # push mtime so the holder's change-detection fires
    os.utime(config, (future, future))
    assert tc.post("/v1/chat/completions", json=COMPLEX).status_code == 200  # 402 with the stale bug


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


# --- rate limiting: RPM / TPM caps -> 429 (WF-ADR-0034) ---
def _ratelimit_client(tmp_path, monkeypatch, rl_block, *, cache=False, clock=None):
    cache_block = "[gateway.cache]\nenabled = true\n\n" if cache else ""
    config = (
        f"[gateway.rate_limit]\n{rl_block}\n\n" + cache_block +
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\n'
    )
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    calls = {"n": 0}

    async def fake(url, headers, json_body, timeout=60.0):
        calls["n"] += 1
        return 200, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},  # 60 tokens/turn
        }).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    # Pin the limiter's clock so a run that straddles a real minute boundary can't roll the window
    # and turn an expected 429 into a 200. Tests that exercise window rolling pass their own clock.
    the_clock = clock if clock is not None else (lambda: 1000.0)
    return TestClient(gateway.build_app(start_dir=str(tmp_path), clock=the_clock)), calls


def test_rate_limit_rpm_returns_429(tmp_path, monkeypatch):
    tc, calls = _ratelimit_client(tmp_path, monkeypatch, "rpm = 2")
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200
    third = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert third.status_code == 429
    assert third.json()["error"]["type"] == "wayfinder_router_rate_limited"
    assert third.headers["x-wayfinder-router-rate-limit"] == "rpm"
    assert int(third.headers["Retry-After"]) >= 1
    assert calls["n"] == 2  # the rejected request never reached the upstream


def test_rate_limit_tpm_returns_429(tmp_path, monkeypatch):
    tc, calls = _ratelimit_client(tmp_path, monkeypatch, "tpm = 10")  # one 60-token turn exceeds it
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200
    second = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert second.status_code == 429
    assert second.headers["x-wayfinder-router-rate-limit"] == "tpm"
    assert calls["n"] == 1


def test_rate_limit_window_rolls_with_the_injected_clock(tmp_path, monkeypatch):
    # The limiter's clock is injectable through build_app, so window rolling is deterministic — and
    # the fixed-clock helpers above can't flake into a 200 at a real minute boundary.
    now = {"t": 1000.0}
    tc, _ = _ratelimit_client(tmp_path, monkeypatch, "rpm = 1", clock=lambda: now["t"])
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200  # window A
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 429  # window A, over rpm=1
    now["t"] += 61  # advance past the 60s window
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200  # window B, fresh budget


def test_no_rate_limit_by_default(tmp_path, monkeypatch):
    (tmp_path / "wayfinder-router.toml").write_text(
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\n',
        encoding="utf-8",
    )

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"hi"}}]}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    tc = TestClient(gateway.build_app(start_dir=str(tmp_path)))
    for _ in range(20):
        assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200  # unlimited


def test_cache_hit_counts_toward_rpm(tmp_path, monkeypatch):
    # A cache hit still consumes a request slot, so the 3rd identical request is rejected even
    # though only the first reached the upstream.
    tc, calls = _ratelimit_client(tmp_path, monkeypatch, "rpm = 2", cache=True)
    assert tc.post("/v1/chat/completions", json=TRIVIAL).headers["x-wayfinder-router-cache"] == "miss"
    assert tc.post("/v1/chat/completions", json=TRIVIAL).headers["x-wayfinder-router-cache"] == "hit"
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 429
    assert calls["n"] == 1  # only the first (miss) hit the upstream


def test_rate_limit_metric_increments(tmp_path, monkeypatch):
    tc, _ = _ratelimit_client(tmp_path, monkeypatch, "rpm = 1")
    tc.post("/v1/chat/completions", json=TRIVIAL)
    tc.post("/v1/chat/completions", json=TRIVIAL)  # 429
    assert 'wayfinder_router_rate_limited_total{limit="rpm"} 1' in tc.get("/metrics").text


def test_rate_limit_config_round_trips():
    body = (
        "[gateway.rate_limit]\nrpm = 60\ntpm = 100000\nwindow = 30\n\n"
        '[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    )
    gw = gateway.gateway_config_from_toml(body)
    assert gw.rate_limit == gateway.RateLimit(rpm=60, tpm=100000, window=30.0)
    back = gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw))
    assert back.rate_limit == gw.rate_limit


@pytest.mark.parametrize(
    "table",
    [
        "[gateway.rate_limit]\nwindow = 60\n",  # neither rpm nor tpm
        "[gateway.rate_limit]\nrpm = 0\n",  # not positive
        "[gateway.rate_limit]\ntpm = -5\n",  # negative
        "[gateway.rate_limit]\nrpm = true\n",  # bool is not an int
        "[gateway.rate_limit]\nrpm = 10\nwindow = 0\n",  # window not positive
        "[gateway]\nrate_limit = 5\n",  # must be a table
    ],
)
def test_bad_rate_limit_config_rejected(table):
    config = table + '\n[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(config)


# --- virtual keys: auth + attribution + per-key budgets/limits (WF-ADR-0035) ---
def _vkeys_config(keys_toml, *, extra=""):
    return (
        keys_toml + "\n" + extra +
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\ncost_per_1k = 0.0\n\n'
        '[gateway.models.cloud]\nbase_url = "http://cloud.test/v1"\nmodel = "m-cloud"\ncost_per_1k = 1.0\n'
    )


def _vkeys_client(tmp_path, monkeypatch, config):
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")

    async def fake(url, headers, json_body, timeout=60.0):
        return 200, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},
        }).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    # Pin the per-key limiter's clock too, so the per-key rate-limit test can't flake at a window roll.
    return TestClient(gateway.build_app(start_dir=str(tmp_path), clock=lambda: 1000.0))


def test_gateway_open_without_keys(tmp_path, monkeypatch):
    tc = _vkeys_client(tmp_path, monkeypatch, _vkeys_config(""))  # no [gateway.keys]
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 200  # no auth required


def test_missing_or_invalid_key_is_401(tmp_path, monkeypatch):
    _, h = vkeys.generate()
    tc = _vkeys_client(tmp_path, monkeypatch, _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\n'))
    assert tc.post("/v1/chat/completions", json=TRIVIAL).status_code == 401  # no Authorization
    bad = tc.post("/v1/chat/completions", json=TRIVIAL, headers={"Authorization": "Bearer wf-nope"})
    assert bad.status_code == 401
    assert bad.json()["error"]["type"] == "wayfinder_router_unauthorized"
    assert bad.headers.get("WWW-Authenticate") == "Bearer"


def test_unauthenticated_flood_is_rate_limited_before_auth(tmp_path, monkeypatch):
    # Rate-limit admission is the outermost guardrail (WF-ADR-0034): an unauthenticated flood gets a
    # 429 once the gateway-wide cap is hit, not an endless run of one-at-a-time 401s (each of which
    # costs a SHA-256 + constant-time compare against every configured key).
    _, h = vkeys.generate()
    cfg = _vkeys_config(
        f'[gateway.keys.team-a]\nhash = "{h}"\n',
        extra="[gateway.rate_limit]\nrpm = 1\n\n",
    )
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    first = tc.post("/v1/chat/completions", json=TRIVIAL)  # admitted (rpm=1), then auth -> 401
    assert first.status_code == 401
    second = tc.post("/v1/chat/completions", json=TRIVIAL)  # rpm exhausted -> 429 *before* auth
    assert second.status_code == 429
    assert second.headers["x-wayfinder-router-rate-limit"] == "rpm"


def test_valid_key_authorizes_and_attributes(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    tc = _vkeys_client(tmp_path, monkeypatch, _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\n'))
    resp = tc.post("/v1/chat/completions", json=COMPLEX, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert 'wayfinder_router_key_requests_total{key="team-a"} 1' in tc.get("/metrics").text
    by_key = tc.get("/v1/savings").json()["by_key"]
    assert "team-a" in by_key and by_key["team-a"]["realized"] > 0  # spend attributed to the key


def test_per_key_rate_limit_429(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(
        f'[gateway.keys.team-a]\nhash = "{h}"\n[gateway.keys.team-a.rate_limit]\nrpm = 1\n'
    )
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    hdr = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/chat/completions", json=TRIVIAL, headers=hdr).status_code == 200
    assert tc.post("/v1/chat/completions", json=TRIVIAL, headers=hdr).status_code == 429


def test_per_key_budget_blocks(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(
        f'[gateway.keys.team-a]\nhash = "{h}"\n'
        '[gateway.keys.team-a.budget]\nlimit = 0.001\non_breach = "block"\n'
    )
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    hdr = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/chat/completions", json=COMPLEX, headers=hdr).status_code == 200  # spends
    blocked = tc.post("/v1/chat/completions", json=COMPLEX, headers=hdr)
    assert blocked.status_code == 402
    assert blocked.json()["error"]["type"] == "wayfinder_router_budget_exhausted"


def test_per_key_block_beats_gateway_degrade(tmp_path, monkeypatch):
    # Gateway-wide budget would degrade; the key's budget blocks -> the stricter (block) wins.
    key, h = vkeys.generate()
    cfg = _vkeys_config(
        f'[gateway.keys.team-a]\nhash = "{h}"\n'
        '[gateway.keys.team-a.budget]\nlimit = 0.001\non_breach = "block"\n',
        extra='[gateway.budget]\nlimit = 0.001\non_breach = "degrade"\n\n',
    )
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    hdr = {"Authorization": f"Bearer {key}"}
    assert tc.post("/v1/chat/completions", json=COMPLEX, headers=hdr).status_code == 200
    assert tc.post("/v1/chat/completions", json=COMPLEX, headers=hdr).status_code == 402  # block wins


def test_v1_messages_requires_key(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    tc = _vkeys_client(tmp_path, monkeypatch, _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\n'))
    req = {"model": "claude-x", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}
    assert tc.post("/v1/messages", json=req).status_code == 401  # Claude Code needs a key too
    ok = tc.post("/v1/messages", json=req, headers={"Authorization": f"Bearer {key}"})
    assert ok.status_code == 200


def test_vkeys_config_round_trips():
    _, h = vkeys.generate()
    body = (
        f'[gateway.keys.team-a]\nhash = "{h}"\ntags = ["a", "prod"]\n'
        "[gateway.keys.team-a.rate_limit]\nrpm = 5\n"
        "[gateway.keys.team-a.budget]\nlimit = 2.0\n\n"
        '[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    )
    gw = gateway.gateway_config_from_toml(body)
    vk = gw.keys["team-a"]
    assert vk.tags == ("a", "prod") and vk.rate_limit.rpm == 5 and vk.budget.limit == 2.0
    assert gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw)).keys == gw.keys


@pytest.mark.parametrize(
    "table",
    [
        '[gateway.keys.x]\nhash = "tooshort"\n',  # not a 64-char hex digest
        '[gateway.keys.x]\ntags = ["a"]\n',  # missing hash
        '[gateway.keys.x]\nhash = "' + "g" * 64 + '"\n',  # not hex
        "[gateway]\nkeys = 5\n",  # keys must be a table
    ],
)
def test_bad_vkeys_config_rejected(table):
    config = table + '\n[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(config)


# --- per-key model allowlists: clamp to nearest allowed tier (WF-ADR-0035 follow-up) ---
def test_clamp_to_allowed_pure():
    c = gateway._clamp_to_allowed
    ladder = ["local", "mid", "cloud"]
    assert c("cloud", ladder, frozenset({"cloud"})) == "cloud"        # already allowed
    assert c("cloud", ladder, frozenset()) == "cloud"                 # unrestricted
    assert c("cloud", ladder, frozenset({"local"})) == "local"        # clamp down
    assert c("cloud", ladder, frozenset({"local", "mid"})) == "mid"   # nearest allowed below
    assert c("local", ladder, frozenset({"cloud"})) == "cloud"        # up when none at/below
    assert c("x", [], frozenset({"a"})) == "a"                        # no ladder -> stable choice


def test_key_allowlist_clamps_down(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["local"]\n')
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    resp = tc.post("/v1/chat/completions", json=COMPLEX, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"  # clamped from cloud (not allowed)
    assert resp.headers["x-wayfinder-router-mode"] == "key-scoped"
    assert resp.headers["x-wayfinder-router-served-by"] == "local"


def test_key_allowlist_permits_listed_model(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["local", "cloud"]\n')
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    resp = tc.post("/v1/chat/completions", json=COMPLEX, headers={"Authorization": f"Bearer {key}"})
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # allowed -> no clamp
    assert resp.headers["x-wayfinder-router-mode"] == "scored"


def test_key_allowlist_clamps_up_when_none_at_or_below(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["cloud"]\n')
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL, headers={"Authorization": f"Bearer {key}"})
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # only cloud allowed -> clamp up
    assert resp.headers["x-wayfinder-router-mode"] == "key-scoped"


def test_key_allowlist_round_trips_and_rejects_unknown():
    _, h = vkeys.generate()
    good = _vkeys_config(f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["local", "cloud"]\n')
    gw = gateway.gateway_config_from_toml(good)
    assert gw.keys["team-a"].models == ("local", "cloud")
    assert gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw)).keys == gw.keys
    bad = (
        f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["ghost"]\n\n'
        '[gateway.models.local]\nbase_url = "http://x/v1"\nmodel = "m"\n'
    )
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(bad)


# --- informational X-RateLimit-* headers (WF-ADR-0034) ---
def test_rate_limit_headers_on_success(tmp_path, monkeypatch):
    tc, _ = _ratelimit_client(tmp_path, monkeypatch, "rpm = 10")
    r = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert r.status_code == 200
    assert r.headers["x-ratelimit-limit"] == "10"
    assert r.headers["x-ratelimit-remaining"] == "9"  # one request consumed this window
    assert int(r.headers["x-ratelimit-reset"]) >= 1


def test_no_rate_limit_headers_when_unlimited(client):
    tc, _ = client
    r = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert "x-ratelimit-limit" not in r.headers  # no headers unless a rate limit is configured


def test_rate_limit_headers_reflect_tighter_key_limit(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    cfg = _vkeys_config(
        f'[gateway.keys.team-a]\nhash = "{h}"\n[gateway.keys.team-a.rate_limit]\nrpm = 2\n',
        extra="[gateway.rate_limit]\nrpm = 100\n\n",
    )
    tc = _vkeys_client(tmp_path, monkeypatch, cfg)
    r = tc.post("/v1/chat/completions", json=TRIVIAL, headers={"Authorization": f"Bearer {key}"})
    assert r.headers["x-ratelimit-limit"] == "2"  # the key's tighter cap, not the gateway's 100
    assert r.headers["x-ratelimit-remaining"] == "1"


# --- in-message slash routing directives (WF-ADR-0036) ---
def _slash_client(tmp_path, monkeypatch, *, enabled=True, extra=""):
    sd = "slash_directives = true\n" if enabled else ""
    config = (
        f"[gateway]\n{sd}\n" + extra +
        "[routing]\nthreshold = 0.2\n\n"
        '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\n\n'
        '[gateway.models.cloud]\nbase_url = "http://cloud.test/v1"\nmodel = "m-cloud"\n'
    )
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    captured: dict = {}

    async def fake(url, headers, json_body, timeout=60.0):
        captured["body"] = json_body
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"hi"}}]}', "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake)
    return TestClient(gateway.build_app(start_dir=str(tmp_path))), captured


def test_resolve_slash_directive_pure():
    from wayfinder_router.complexity import RoutingConfig
    r = RoutingConfig.binary(threshold=0.5)
    gw = gateway.gateway_config_from_toml(
        '[gateway.models.local]\nbase_url="http://x/v1"\nmodel="m"\n\n'
        '[gateway.models.cloud]\nbase_url="http://y/v1"\nmodel="c"\n'
    )
    f = gateway.resolve_slash_directive
    assert f([{"role": "user", "content": "/local do it"}], r, gw) == (
        "local", [{"role": "user", "content": "do it"}]
    )
    assert f([{"role": "user", "content": "/prefer-hosted hi"}], r, gw)[0] == "cloud"
    assert f([{"role": "user", "content": "/auto hi"}], r, gw) == (
        None, [{"role": "user", "content": "hi"}]
    )
    assert f([{"role": "user", "content": "/foo hi"}], r, gw) == (None, None)       # unknown
    assert f([{"role": "user", "content": "/etc/passwd?"}], r, gw) == (None, None)  # a path
    assert f([{"role": "user", "content": "plain text"}], r, gw) == (None, None)
    assert f([{"role": "user", "content": "/localhost?"}], r, gw) == (None, None)   # not /local


def test_slash_directive_routes_and_strips(tmp_path, monkeypatch):
    tc, captured = _slash_client(tmp_path, monkeypatch)
    resp = tc.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "/local " + COMPLEX_TEXT}]})
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"  # forced, despite a complex prompt
    assert resp.headers["x-wayfinder-router-mode"] == "slash-pinned"
    assert captured["body"]["messages"][-1]["content"] == COMPLEX_TEXT  # directive stripped upstream


def test_slash_directive_off_by_default(tmp_path, monkeypatch):
    tc, captured = _slash_client(tmp_path, monkeypatch, enabled=False)
    resp = tc.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "/local hi"}]})
    assert captured["body"]["messages"][-1]["content"] == "/local hi"  # untouched, ordinary text
    assert resp.headers["x-wayfinder-router-mode"] != "slash-pinned"


def test_slash_prefer_hosted_pins_high_tier(tmp_path, monkeypatch):
    tc, _ = _slash_client(tmp_path, monkeypatch)
    resp = tc.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "/prefer-hosted hi"}]})
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-mode"] == "slash-pinned"


def test_model_field_pin_beats_slash(tmp_path, monkeypatch):
    tc, _ = _slash_client(tmp_path, monkeypatch)
    resp = tc.post("/v1/chat/completions", json={
        "model": "cloud", "messages": [{"role": "user", "content": "/local hi"}]})
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # explicit API pin wins
    assert resp.headers["x-wayfinder-router-mode"] == "pinned"


def test_slash_auto_forces_scoring_and_strips(tmp_path, monkeypatch):
    tc, captured = _slash_client(tmp_path, monkeypatch)
    resp = tc.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "/auto hi"}]})
    assert resp.headers["x-wayfinder-router-mode"] == "scored"
    assert captured["body"]["messages"][-1]["content"] == "hi"  # stripped even when not pinning


def test_slash_pin_subject_to_key_allowlist(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    extra = f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["local"]\n\n'
    tc, _ = _slash_client(tmp_path, monkeypatch, extra=extra)
    resp = tc.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "/cloud hi"}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.headers["x-wayfinder-router-model"] == "local"  # /cloud clamped to the allowed tier
    assert resp.headers["x-wayfinder-router-mode"] == "key-scoped"


def test_slash_directives_config_round_trips():
    gw = gateway.gateway_config_from_toml(
        '[gateway]\nslash_directives = true\n\n[gateway.models.local]\nbase_url="http://x/v1"\nmodel="m"\n'
    )
    assert gw.slash_directives is True
    assert gateway.gateway_config_from_toml(gateway.dump_gateway_toml(gw)).slash_directives is True


def test_bad_slash_directives_config_rejected():
    with pytest.raises(gateway.WayfinderConfigError):
        gateway.gateway_config_from_toml(
            '[gateway]\nslash_directives = "yes"\n\n[gateway.models.local]\nbase_url="http://x/v1"\nmodel="m"\n'
        )
