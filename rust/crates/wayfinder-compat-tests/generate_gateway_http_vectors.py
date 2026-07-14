#!/usr/bin/env python3
"""Generate the Python-authoritative deterministic gateway HTTP corpus.

The corpus exercises only endpoints already implemented by the Rust gateway.
It uses FastAPI's in-process TestClient: no listener, provider, credential, or
network access is involved.  Request ids, recent-entry timestamps, and the
machine-dependent decision-latency histogram observations are the only values
normalized.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))
warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from wayfinder_router import __version__  # noqa: E402
from wayfinder_router.gateway import build_app  # noqa: E402

logging.disable(logging.CRITICAL)


MISSING_KEY_ENV = "WAYFINDER_HTTP_CORPUS_MISSING_KEY"
APPLICATION_HEADERS = (
    "allow",
    "content-type",
    "x-wayfinder-router-decision-only",
    "x-wayfinder-router-mode",
    "x-wayfinder-router-model",
    "x-wayfinder-router-offline",
    "x-wayfinder-router-request-id",
    "x-wayfinder-router-score",
)

CONFIGURED_TOML = """\
[routing]
threshold = 0.5

[gateway]
offline = true

[gateway.models.local]
base_url = "http://127.0.0.1:11434/v1"
model = "provider-local"

[gateway.models.cloud]
base_url = "https://cloud.example/v1"
model = "provider-cloud"
api_key_env = "WAYFINDER_HTTP_CORPUS_MISSING_KEY"
"""

DECISION_ONLY_TOML = """\
[routing]
threshold = 0.5
"""

class Normalizer:
    """Preserve request-id correlation while replacing dynamic values."""

    def __init__(self) -> None:
        self._request_ids: dict[str, str] = {}

    def request_id(self, value: str) -> str:
        marker = self._request_ids.get(value)
        if marker is None:
            marker = f"<request_id:{len(self._request_ids) + 1}>"
            self._request_ids[value] = marker
        return marker

    def json_value(self, value: Any, *, key: str | None = None) -> Any:
        if key == "request_id" and isinstance(value, str):
            return self.request_id(value)
        if key == "ts" and isinstance(value, int | float):
            return "<timestamp>"
        if isinstance(value, list):
            return [self.json_value(item) for item in value]
        if isinstance(value, Mapping):
            return {
                name: self.json_value(item, key=name)
                for name, item in value.items()
            }
        return value

    @staticmethod
    def latency_metrics(text: str) -> str:
        lines: list[str] = []
        prefix = "wayfinder_router_decision_latency_seconds"
        for line in text.splitlines():
            if line.startswith(f"{prefix}_bucket{{") and 'le="+Inf"' not in line:
                line = re.sub(r" \S+$", " <latency-bucket>", line)
            elif line.startswith(f"{prefix}_sum "):
                line = f"{prefix}_sum <latency-sum>"
            lines.append(line)
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _capture(
    client: TestClient,
    normalizer: Normalizer,
    *,
    name: str,
    state: str,
    method: str,
    path: str,
    json_body: Any = None,
    raw_body: str | None = None,
    normalize_latency: bool = False,
) -> dict[str, Any]:
    request: dict[str, Any] = {"method": method, "path": path}
    kwargs: dict[str, Any] = {}
    if raw_body is not None:
        request["raw_body"] = raw_body
        kwargs["content"] = raw_body.encode()
        kwargs["headers"] = {"content-type": "application/json"}
    elif json_body is not None:
        request["json"] = json_body
        kwargs["json"] = json_body

    response = client.request(method, path, **kwargs)
    headers = {
        header: response.headers[header]
        for header in APPLICATION_HEADERS
        if header in response.headers
    }
    request_id = headers.get("x-wayfinder-router-request-id")
    if request_id is not None:
        headers["x-wayfinder-router-request-id"] = normalizer.request_id(request_id)

    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        body = {"kind": "json", "value": normalizer.json_value(response.json())}
    else:
        text = response.text
        if normalize_latency:
            text = normalizer.latency_metrics(text)
        body = {"kind": "text", "value": text}

    return {
        "name": name,
        "state": state,
        "request": request,
        "response": {
            "status": response.status_code,
            "headers": headers,
            "body": body,
        },
    }


def generate() -> dict[str, Any]:
    os.environ.pop(MISSING_KEY_ENV, None)
    normalizer = Normalizer()
    with tempfile.TemporaryDirectory(prefix="wayfinder-http-corpus-") as root:
        root_path = Path(root)
        configured_path = root_path / "configured"
        decision_only_path = root_path / "decision-only"
        configured_path.mkdir()
        decision_only_path.mkdir()
        (configured_path / "wayfinder-router.toml").write_text(
            CONFIGURED_TOML, encoding="utf-8"
        )
        (decision_only_path / "wayfinder-router.toml").write_text(
            DECISION_ONLY_TOML, encoding="utf-8"
        )

        configured = TestClient(build_app(str(configured_path), dry_run=True))
        decision_only = TestClient(build_app(str(decision_only_path), dry_run=False))
        c = lambda **kwargs: _capture(  # noqa: E731 - compact declarative corpus
            configured, normalizer, state="configured_dry_run", **kwargs
        )
        d = lambda **kwargs: _capture(  # noqa: E731 - compact declarative corpus
            decision_only, normalizer, state="decision_only", **kwargs
        )

        cases = [
            c(name="health_configured", method="GET", path="/healthz"),
            c(name="metrics_initial", method="GET", path="/metrics"),
            c(name="models_v1", method="GET", path="/v1/models"),
            c(name="models_alias", method="GET", path="/models"),
            c(name="router_models", method="GET", path="/router/models"),
            c(name="router_profiles", method="GET", path="/router/profiles"),
            c(
                name="chat_dry_run_v1",
                method="POST",
                path="/v1/chat/completions",
                json_body={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ),
            c(
                name="metrics_after_one_dry_run",
                method="GET",
                path="/metrics",
                normalize_latency=True,
            ),
            c(
                name="chat_dry_run_alias_pinned",
                method="POST",
                path="/chat/completions",
                json_body={
                    "model": "cloud",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            ),
            c(
                name="recent_after_two_limit_one",
                method="GET",
                path="/router/recent?limit=1",
            ),
            c(
                name="recent_after_two_default",
                method="GET",
                path="/router/recent",
            ),
            c(
                name="recent_invalid_limit",
                method="GET",
                path="/router/recent?limit=abc",
            ),
            c(
                name="chat_empty_body",
                method="POST",
                path="/v1/chat/completions",
                raw_body="",
            ),
            c(
                name="chat_wrong_shape",
                method="POST",
                path="/v1/chat/completions",
                json_body=[],
            ),
            c(
                name="chat_malformed_json",
                method="POST",
                path="/v1/chat/completions",
                raw_body="{",
            ),
            c(name="not_found", method="GET", path="/not-a-route"),
            c(name="health_method_not_allowed", method="POST", path="/healthz"),
            c(
                name="chat_method_not_allowed",
                method="GET",
                path="/v1/chat/completions",
            ),
            d(
                name="chat_decision_only_v1",
                method="POST",
                path="/v1/chat/completions",
                json_body={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ),
            d(
                name="chat_decision_only_alias",
                method="POST",
                path="/chat/completions",
                json_body={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ),
        ]

    return {
        "schema": 1,
        "authority": "wayfinder_router.gateway.build_app/FastAPI TestClient",
        "version": __version__,
        "normalization": [
            "request_id values preserve correlation through numbered markers",
            "recent-entry ts values become <timestamp>",
            "post-decision finite latency buckets and latency sum become markers",
        ],
        "known_rust_mismatches": [],
        "header_contract": list(APPLICATION_HEADERS),
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(generate(), indent=2, sort_keys=False, ensure_ascii=False))
