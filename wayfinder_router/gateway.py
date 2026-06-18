"""Optional OpenAI-compatible routing gateway (WF-ADR-0004).

This is the impure layer: it holds bring-your-own keys and calls upstream models.
It ships behind the ``wayfinder-router[gateway]`` extra; ``fastapi`` / ``uvicorn`` /
``httpx`` are imported lazily so the deterministic core stays dependency-free.

A client points its OpenAI-compatible ``base_url`` at this gateway. For each
request the gateway scores the prompt with the pure core, maps the recommended
model name to a configured upstream, and forwards the call with the user's key.
Keys are read from the environment at request time and never appear in
``wayfinder-router.toml``, in the scored path, or in any test fixture.

A request may steer the routing decision per call without changing any
application code, through OpenAI-compatible channels (WF-ADR-0011). This only
moves *which threshold/decision applies*; it never adds inference, so the
WF-ADR-0001/0004 boundary holds:

- the OpenAI ``model`` field is a routing directive — ``auto`` (or any
  unrecognized value) scores per config, an exact configured endpoint name
  pins the call to that endpoint, and ``prefer-local`` / ``prefer-cloud`` pin
  to the low / high end of the configured router;
- an ``X-Wayfinder-Threshold`` header (a number in ``0.0``–``1.0``) re-decides
  the call at that binary cut, reusing the configured scoring weights.

Every response carries the decision signal: ``x-wayfinder-router-model`` (the
chosen endpoint), ``x-wayfinder-router-score`` (the structural score, always
computed), and ``x-wayfinder-router-mode`` (``scored`` / ``pinned`` /
``threshold-override``).

Config (`wayfinder-router.toml`)::

    [gateway.models.local]
    base_url = "http://localhost:11434/v1"
    model = "llama3.2"

    [gateway.models.cloud]
    base_url = "https://api.example.com/v1"
    model = "big-model"
    api_key_env = "EXAMPLE_API_KEY"   # name of the env var holding the key
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .complexity import RoutingConfig, Tier, recommend_tier, score_complexity
from .config import WayfinderConfigError, find_config_file, load_routing_config
from .feedback import DEFAULT_LOG, record_label

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_app
    from fastapi import FastAPI, Response

_INSTALL_HINT = "the gateway needs its extra: pip install 'wayfinder-router[gateway]'"


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
    """Read `[gateway.models.<name>]` from the nearest ``wayfinder-router.toml``."""
    path = find_config_file(start_dir)
    if path is None:
        return GatewayConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {path}: {exc}") from exc
    return gateway_config_from_toml(text, where=str(path))


def gateway_config_from_toml(text: str, where: str = "wayfinder-router.toml") -> GatewayConfig:
    """Parse a :class:`GatewayConfig` from ``wayfinder-router.toml`` text (file-free)."""
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


def dump_gateway_toml(gateway: GatewayConfig) -> str:
    """Serialize a :class:`GatewayConfig` back to ``[gateway.models.*]`` TOML.

    Used by recalibration to preserve the endpoint mapping when it rewrites the
    routing section. Emits ``api_key_env`` (the env-var *name*) — never a secret.
    """
    blocks: list[str] = []
    for name, model in gateway.models.items():
        lines = [
            f"[gateway.models.{name}]",
            f'base_url = "{model.base_url}"',
            f'model = "{model.model}"',
        ]
        if model.api_key_env:
            lines.append(f'api_key_env = "{model.api_key_env}"')
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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


# Per-request override transport (WF-ADR-0011). These are pure and offline: they
# only move which threshold/decision applies, never invoke a model.
THRESHOLD_HEADER = "x-wayfinder-threshold"
_AUTO = "auto"  # the OpenAI `model` sentinel meaning "Wayfinder decides"
_PREFER_LOW = "prefer-local"
_PREFER_HIGH = "prefer-cloud"


class BadOverride(Exception):
    """A per-request override was supplied but is malformed or not applicable."""


def resolve_pin(model_field: object, routing: RoutingConfig, gateway: GatewayConfig) -> str | None:
    """Resolve an explicit endpoint pin from the OpenAI ``model`` field, or ``None``.

    ``auto``, an empty value, or any string that is neither a configured endpoint
    name nor a ``prefer-*`` alias returns ``None`` — the request asks Wayfinder to
    score and decide (kept tolerant so ordinary OpenAI ``model`` ids pass through).
    ``prefer-local`` / ``prefer-cloud`` resolve to the low / high end of the
    configured router (the first / last tier's model).
    """
    if not isinstance(model_field, str):
        return None
    name = model_field.strip()
    if not name or name == _AUTO:
        return None
    if name == _PREFER_LOW:
        return routing.tiers[0].model if routing.tiers else None
    if name == _PREFER_HIGH:
        return routing.tiers[-1].model if routing.tiers else None
    return name if name in gateway.models else None


def parse_threshold_header(value: str | None) -> float | None:
    """Parse the ``X-Wayfinder-Threshold`` header into a ``0.0``–``1.0`` cut, or ``None``.

    Raises :class:`BadOverride` when the header is present but not a number in range.
    """
    if value is None:
        return None
    try:
        threshold = float(value)
    except ValueError as exc:
        raise BadOverride(
            f"{THRESHOLD_HEADER} must be a number in 0.0-1.0, got {value!r}"
        ) from exc
    if not 0.0 <= threshold <= 1.0:
        raise BadOverride(f"{THRESHOLD_HEADER} must be in 0.0-1.0, got {threshold}")
    return threshold


def threshold_tiers(routing: RoutingConfig, threshold: float) -> tuple[Tier, ...]:
    """Binary tiers at ``threshold`` reusing the configured router's endpoint names.

    The threshold override is only well-defined for a binary (two-tier) router;
    a classifier or a multi-tier router has no single cut to move, so this raises
    :class:`BadOverride`.
    """
    if routing.classifier is not None or len(routing.tiers) != 2:
        raise BadOverride(
            f"{THRESHOLD_HEADER} applies only to a binary (two-tier) router; this "
            "gateway is configured for classifier or multi-tier routing"
        )
    return (Tier(0.0, routing.tiers[0].model), Tier(threshold, routing.tiers[1].model))


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


def invoke_model(model: GatewayModel, prompt: str, timeout: float = 60.0) -> str:
    """Run ``prompt`` through one upstream model and return its text (BYO key).

    The single-prompt call the onboarding harness uses to A/B a local vs hosted
    model. It forwards an OpenAI-compatible chat request with the model's key
    (read from the environment) and returns the assistant content. Reuses
    :func:`forward_request`, so tests substitute the network the same way.
    """
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    body = {"model": model.model, "messages": [{"role": "user", "content": prompt}]}
    url = model.base_url.rstrip("/") + "/chat/completions"
    status, content, _ = forward_request(url, headers, body, timeout)
    if status >= 400:
        raise RuntimeError(f"{model.model} upstream returned {status}: {content[:200]!r}")
    try:
        data = json.loads(content)
        return str(data["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{model.model} returned an unexpected response shape: {exc}") from exc


class _ConfigHolder:
    """Caches routing + gateway config, reloading when ``wayfinder-router.toml`` changes.

    Lets a recalibration (CLI, cron, or UI) take effect on the running gateway
    with no restart: each request checks the config file's mtime and re-reads only
    when it moved. A malformed mid-flight write keeps the last-good config (the
    marker advances so it is not retried every request) rather than failing serving.
    """

    def __init__(self, start_dir: str) -> None:
        self.start_dir = start_dir
        self._routing = load_routing_config(start_dir)
        self._gateway = load_gateway_config(start_dir)
        self._mtime = self._mtime_now()

    def _mtime_now(self) -> float | None:
        path = find_config_file(self.start_dir)
        if path is None:
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def current(self) -> tuple[RoutingConfig, GatewayConfig]:
        mtime = self._mtime_now()
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._routing = load_routing_config(self.start_dir)
                self._gateway = load_gateway_config(self.start_dir)
            except WayfinderConfigError:
                pass  # keep last-good config; the marker advanced so we do not thrash
        return self._routing, self._gateway


def build_app(start_dir: str = ".") -> FastAPI:
    """Build the FastAPI gateway app; config hot-reloads on ``wayfinder-router.toml`` change."""
    try:
        from fastapi import Body, FastAPI, Header, Response
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc

    holder = _ConfigHolder(start_dir)
    app = FastAPI(title="wayfinder-router-gateway")

    @app.get("/healthz")
    def healthz() -> dict:
        _, gateway = holder.current()
        return {"status": "ok", "models": sorted(gateway.models)}

    @app.post("/v1/feedback")
    def feedback(body: dict = Body(...)) -> object:  # noqa: B008 - FastAPI default
        # Steady-state escalate loop: the caller records which model was good
        # enough for a prompt; the label feeds the next recalibration.
        raw_text, raw_label = body.get("text"), body.get("label")
        if not isinstance(raw_text, str) or not raw_text:
            return JSONResponse(status_code=400, content={"error": "missing 'text'"})
        if not isinstance(raw_label, str) or not raw_label:
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        record_label(str(Path(start_dir) / DEFAULT_LOG), raw_text, raw_label)
        return {"ok": True}

    @app.post("/v1/chat/completions")
    def chat_completions(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        x_wayfinder_threshold: str | None = Header(default=None),
    ) -> Response:
        routing, gateway = holder.current()
        # Score once (always reported); a per-request override only changes which
        # endpoint the score routes to, never how it is computed (WF-ADR-0011).
        decision = score_complexity(extract_prompt(body.get("messages")), config=routing)

        pin = resolve_pin(body.get("model"), routing, gateway)
        if pin is not None:
            chosen, mode = pin, "pinned"
        else:
            try:
                threshold = parse_threshold_header(x_wayfinder_threshold)
                if threshold is not None:
                    chosen = recommend_tier(decision.score, threshold_tiers(routing, threshold))
                    mode = "threshold-override"
                else:
                    chosen, mode = decision.recommendation, "scored"
            except BadOverride as exc:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
                )

        target = gateway.models.get(chosen)
        if target is None:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"no gateway endpoint configured for model '{chosen}'",
                        "type": "wayfinder_router_misconfigured",
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
                "x-wayfinder-router-model": chosen,
                "x-wayfinder-router-score": f"{decision.score:.2f}",
                "x-wayfinder-router-mode": mode,
            },
        )

    return app


def run(  # pragma: no cover
    start_dir: str = ".", host: str = "127.0.0.1", port: int = 8088
) -> None:
    """Serve the gateway with uvicorn (the `wayfinder-router serve` command)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    uvicorn.run(build_app(start_dir), host=host, port=port)
