"""Tests for the local Explain/Playground UI (WF-ADR-0005).

The UI is a thin consumer of the pure core. ``score_payload`` is tested directly
(no extra needed); the web endpoints use FastAPI's TestClient.
"""

from __future__ import annotations

import pytest
from wayfinder.complexity import FEATURE_ORDER
from wayfinder.ui import score_payload

TRIVIAL = "hi"
COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)


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
