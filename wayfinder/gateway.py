"""Optional OpenAI-compatible routing gateway (WF-ADR-0004).

This is the impure layer: it holds bring-your-own keys and calls upstream models.
It ships behind the ``wayfinder[gateway]`` extra; ``fastapi`` / ``uvicorn`` /
``httpx`` are imported lazily so the deterministic core stays dependency-free.

A client points its OpenAI-compatible ``base_url`` at this gateway. For each
request the gateway scores the prompt with the pure core, maps the recommended
model name to a configured upstream, and forwards the call with the user's key.
Keys are read from the environment at request time and never appear in
``wayfinder.toml``, in the scored path, or in any test fixture.

Config (`wayfinder.toml`)::

    [gateway.models.local]
    base_url = "http://localhost:11434/v1"
    model = "llama3.2"

    [gateway.models.cloud]
    base_url = "https://api.example.com/v1"
    model = "big-model"
    api_key_env = "EXAMPLE_API_KEY"   # name of the env var holding the key
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .complexity import score_complexity
from .config import WayfinderConfigError, find_config_file, load_routing_config

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_app
    from fastapi import FastAPI, Response

_INSTALL_HINT = "the gateway needs its extra: pip install 'wayfinder[gateway]'"


class GatewayUnavailable(Exception):
    """The gateway extra (fastapi / uvicorn / httpx) is not installed."""


@dataclass(frozen=True)
class GatewayModel:
    """An upstream endpoint a recommended model name maps to."""

    base_url: str  # OpenAI-compatible base, e.g. http://localhost:11434/v1
    model: str  # the upstream model id to send in the forwarded request
    api_key_env: str | None = None  # env var holding the key, or None for no auth


@dataclass(frozen=True)
class GatewayConfig:
    """Maps recommended model names to upstream endpoints (from `[gateway.models]`)."""

    models: dict[str, GatewayModel] = field(default_factory=dict)


def load_gateway_config(start_dir: str = ".") -> GatewayConfig:
    """Read `[gateway.models.<name>]` from the nearest ``wayfinder.toml``."""
    path = find_config_file(start_dir)
    if path is None:
        return GatewayConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {path}: {exc}") from exc
    return gateway_config_from_toml(text, where=str(path))


def gateway_config_from_toml(text: str, where: str = "wayfinder.toml") -> GatewayConfig:
    """Parse a :class:`GatewayConfig` from ``wayfinder.toml`` text (file-free)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WayfinderConfigError(f"{where}: invalid TOML: {exc}") from exc
    gateway = data.get("gateway")
    if gateway is None:
        return GatewayConfig()
    if not isinstance(gateway, dict):
        raise WayfinderConfigError(f"{where}: '[gateway]' must be a table")
    raw_models = gateway.get("models") or {}
    if not isinstance(raw_models, dict):
        raise WayfinderConfigError(f"{where}: '[gateway.models]' must be a table")
    models: dict[str, GatewayModel] = {}
    for name, entry in raw_models.items():
        if not isinstance(entry, dict):
            raise WayfinderConfigError(f"{where}: '[gateway.models.{name}]' must be a table")
        base_url = entry.get("base_url")
        model = entry.get("model")
        api_key_env = entry.get("api_key_env")
        if not isinstance(base_url, str) or not base_url:
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.base_url' must be a string"
            )
        if not isinstance(model, str) or not model:
            raise WayfinderConfigError(f"{where}: 'gateway.models.{name}.model' must be a string")
        if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.api_key_env' must be a non-empty string"
            )
        models[name] = GatewayModel(base_url=base_url, model=model, api_key_env=api_key_env)
    return GatewayConfig(models=models)


def extract_prompt(messages: object) -> str:
    """Deterministically join the text of OpenAI-style chat messages for scoring.

    Handles both plain string content and the array-of-parts content form.
    """
    parts: list[str] = []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
    return "\n".join(parts)


def forward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = 60.0
) -> tuple[int, bytes, str]:
    """POST ``json_body`` to ``url``; return ``(status, content, content_type)``.

    Isolated so tests can substitute it without a real upstream.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    response = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    return response.status_code, response.content, response.headers.get(
        "content-type", "application/json"
    )


def build_app(start_dir: str = ".") -> FastAPI:
    """Build the FastAPI gateway app, loading routing + gateway config once."""
    try:
        from fastapi import Body, FastAPI, Response
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc

    routing = load_routing_config(start_dir)
    gateway = load_gateway_config(start_dir)
    app = FastAPI(title="wayfinder-gateway")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "models": sorted(gateway.models)}

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict = Body(...)) -> Response:  # noqa: B008 - FastAPI default
        decision = score_complexity(extract_prompt(body.get("messages")), config=routing)
        target = gateway.models.get(decision.recommendation)
        if target is None:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": (
                            f"no gateway endpoint configured for model "
                            f"'{decision.recommendation}'"
                        ),
                        "type": "wayfinder_misconfigured",
                    }
                },
            )
        headers = {"Content-Type": "application/json"}
        if target.api_key_env:
            key = os.environ.get(target.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        forward_body = {**body, "model": target.model}
        url = target.base_url.rstrip("/") + "/chat/completions"
        status, content, content_type = forward_request(url, headers, forward_body)
        return Response(
            content=content,
            status_code=status,
            media_type=content_type,
            headers={
                "x-wayfinder-model": decision.recommendation,
                "x-wayfinder-score": f"{decision.score:.2f}",
            },
        )

    return app


def run(  # pragma: no cover
    start_dir: str = ".", host: str = "127.0.0.1", port: int = 8088
) -> None:
    """Serve the gateway with uvicorn (the `wayfinder serve` command)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    uvicorn.run(build_app(start_dir), host=host, port=port)
