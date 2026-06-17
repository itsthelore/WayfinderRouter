"""Tests for the local Explain/Playground UI (WF-ADR-0005).

The UI is a thin consumer of the pure core. ``score_payload`` is tested directly
(no extra needed); the web endpoints use FastAPI's TestClient.
"""

from __future__ import annotations

import json

import pytest
from wayfinder.calibrate import CalibrationError
from wayfinder.complexity import FEATURE_ORDER
from wayfinder.ui import (
    calibrate_payload,
    current_config_text,
    onboard_arms,
    onboard_dataset_text,
    onboard_run,
    record_onboard_label,
    save_config_text,
    score_payload,
    validate_config_text,
)

TRIVIAL = "hi"
COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)
GW_CONFIG = (
    '[gateway.models.local]\nbase_url = "http://localhost/v1"\nmodel = "l"\n\n'
    '[gateway.models.cloud]\nbase_url = "http://cloud/v1"\nmodel = "c"\n'
)


def _with_gateway(tmp_path) -> str:
    (tmp_path / "wayfinder.toml").write_text(GW_CONFIG, encoding="utf-8")
    return str(tmp_path)


def _fake_forward(url, headers, json_body, timeout=60.0):
    content = ('{"choices":[{"message":{"content":"reply:' + json_body["model"] + '"}}]}').encode()
    return 200, content, "application/json"
DATASET = "\n".join(
    json.dumps(r)
    for r in [{"text": TRIVIAL, "label": "local"}] * 4 + [{"text": COMPLEX, "label": "cloud"}] * 4
)


# --- pure helpers (no extra needed) -----------------------------------------


def test_score_payload_is_explainable_and_pure(tmp_path):
    payload = score_payload(COMPLEX, start_dir=str(tmp_path))
    assert payload["schema_version"] == "2"
    assert payload["recommendation"] in ("local", "cloud")
    assert [c["name"] for c in payload["contributions"]] == list(FEATURE_ORDER)
    total = sum(c["contribution"] for c in payload["contributions"])
    assert round(total, 2) == payload["score"]


def test_score_payload_threshold_override(tmp_path):
    # threshold 0.0 routes everything (score >= 0.0) to cloud.
    payload = score_payload(TRIVIAL, start_dir=str(tmp_path), threshold=0.0)
    assert payload["recommendation"] == "cloud"


def test_calibrate_payload_threshold_returns_curve_and_fragment():
    payload = calibrate_payload(DATASET, "threshold")
    assert payload["summary"]["accuracy"] == 1.0
    assert "[[routing.tiers]]" in payload["toml"]
    assert payload["curve"] and max(p["accuracy"] for p in payload["curve"]) == 1.0


def test_calibrate_payload_classifier_has_no_curve():
    payload = calibrate_payload(DATASET, "classifier")
    assert "[routing.classifier]" in payload["toml"]
    assert "curve" not in payload


def test_calibrate_payload_rejects_bad_dataset():
    with pytest.raises(CalibrationError):
        calibrate_payload('{"text": "no label"}', "threshold")


def test_current_config_text_defaults_when_absent(tmp_path):
    assert "[[routing.tiers]]" in current_config_text(str(tmp_path))


def test_validate_config_text_accepts_and_rejects():
    assert validate_config_text("[routing]\nthreshold = 0.6\n") is None
    assert validate_config_text("[routing]\nthreshold = 2.0\n") is not None


def test_save_config_writes_valid_and_refuses_invalid(tmp_path):
    assert save_config_text("[routing]\nthreshold = 0.7\n", str(tmp_path)) is None
    assert (tmp_path / "wayfinder.toml").read_text(encoding="utf-8").startswith("[routing]")
    # Invalid config is rejected and the file is not overwritten with garbage.
    assert save_config_text("[routing]\nthreshold = 9\n", str(tmp_path)) is not None
    assert "0.7" in (tmp_path / "wayfinder.toml").read_text(encoding="utf-8")


def test_onboard_arms_lists_the_two_gateway_models(tmp_path):
    assert onboard_arms(_with_gateway(tmp_path)) == ["local", "cloud"]


def test_onboard_record_and_dataset_feed_calibrate(tmp_path):
    start_dir = _with_gateway(tmp_path)
    assert record_onboard_label(start_dir, "hi", "local") == 1
    assert record_onboard_label(start_dir, COMPLEX, "cloud") == 2
    dataset = onboard_dataset_text(start_dir)
    assert calibrate_payload(dataset, "threshold")["summary"]["samples"] == 2


def test_onboard_run_invokes_each_arm(tmp_path, monkeypatch):
    from wayfinder import gateway

    monkeypatch.setattr(gateway, "forward_request", _fake_forward)
    assert onboard_run(_with_gateway(tmp_path), "hi") == {"local": "reply:l", "cloud": "reply:c"}


# --- web endpoints ----------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from wayfinder.ui import build_ui_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return TestClient(build_ui_app(start_dir=str(tmp_path)))


def test_index_serves_the_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Wayfinder" in resp.text


def test_api_score_returns_contributions(client):
    resp = client.post("/api/score", json={"prompt": COMPLEX})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["contributions"]) == len(FEATURE_ORDER)
    assert data["recommendation"] in ("local", "cloud")


def test_api_score_threshold_override_changes_routing(client):
    low = client.post("/api/score", json={"prompt": TRIVIAL, "threshold": 0.0}).json()
    assert low["recommendation"] == "cloud"
    high = client.post("/api/score", json={"prompt": TRIVIAL, "threshold": 1.0}).json()
    assert high["recommendation"] == "local"


def test_api_calibrate_returns_fragment_and_curve(client):
    resp = client.post("/api/calibrate", json={"dataset": DATASET, "mode": "threshold"})
    assert resp.status_code == 200
    data = resp.json()
    assert "[[routing.tiers]]" in data["toml"]
    assert data["summary"]["accuracy"] == 1.0
    assert data["curve"]


def test_api_calibrate_bad_dataset_is_400(client):
    resp = client.post("/api/calibrate", json={"dataset": "not json", "mode": "threshold"})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_api_config_get_validate_save_round_trip(client, tmp_path):
    assert "[[routing.tiers]]" in client.get("/api/config").json()["toml"]

    bad = client.post("/api/config/validate", json={"toml": "[routing]\nthreshold = 5\n"}).json()
    assert bad["ok"] is False and bad["error"]

    good = client.post("/api/config/validate", json={"toml": "[routing]\nthreshold = 0.6\n"}).json()
    assert good["ok"] is True

    saved = client.post("/api/config/save", json={"toml": "[routing]\nthreshold = 0.6\n"})
    assert saved.status_code == 200
    assert (tmp_path / "wayfinder.toml").read_text(encoding="utf-8").startswith("[routing]")


def test_api_config_save_rejects_invalid(client):
    resp = client.post("/api/config/save", json={"toml": "[routing]\nthreshold = 9\n"})
    assert resp.status_code == 400
    assert "error" in resp.json()


# --- onboard endpoints ------------------------------------------------------


@pytest.fixture
def ob_client(tmp_path, monkeypatch):
    from wayfinder import gateway

    monkeypatch.setattr(gateway, "forward_request", _fake_forward)
    return TestClient(build_ui_app(start_dir=_with_gateway(tmp_path))), tmp_path


def test_api_onboard_state_lists_arms(ob_client):
    client, _ = ob_client
    data = client.get("/api/onboard").json()
    assert data["arms"] == ["local", "cloud"]
    assert data["count"] == 0


def test_api_onboard_run_returns_both_outputs(ob_client):
    client, _ = ob_client
    data = client.post("/api/onboard/run", json={"prompt": "hi"}).json()
    assert set(data["outputs"]) == {"local", "cloud"}


def test_api_onboard_run_missing_prompt_is_400(ob_client):
    client, _ = ob_client
    assert client.post("/api/onboard/run", json={}).status_code == 400


def test_api_onboard_record_writes_the_shared_log(ob_client):
    client, tmp_path = ob_client
    resp = client.post("/api/onboard/record", json={"prompt": "hi", "label": "local"})
    assert resp.json() == {"ok": True, "count": 1}
    assert (tmp_path / "wayfinder-feedback.jsonl").is_file()
    dataset = client.get("/api/onboard/dataset").json()["dataset"]
    assert '"label": "local"' in dataset
