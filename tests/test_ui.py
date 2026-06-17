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
