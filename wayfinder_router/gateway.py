"""Optional OpenAI-compatible routing gateway (WF-ADR-0004).

This is the impure layer: it holds bring-your-own keys and calls upstream models.
It ships behind the ``wayfinder-router[gateway]`` extra; ``fastapi`` / ``uvicorn`` /
``httpx`` are imported lazily so the deterministic core stays dependency-free.

A client points its OpenAI-compatible ``base_url`` at this gateway. For each
request the gateway scores the prompt with the pure core, maps the recommended
model name to a configured upstream, and forwards the call with the user's key.
Keys are read from the environment at request time and never appear in
``wayfinder-router.toml``, in the scored path, or in any test fixture.

Streaming is first-class (WF-ADR-0013): a request with ``stream: true`` is relayed
back as a Server-Sent-Events stream so chat clients render tokens progressively.
The forward path is async (``httpx.AsyncClient``) so concurrent requests do not
block one another. Upstream transport failures become an OpenAI-shaped
``wayfinder_router_upstream_error`` rather than a bare 500, every request carries
an ``x-wayfinder-router-request-id`` for tracing, and the upstream timeout is
configurable (``WAYFINDER_ROUTER_TIMEOUT``).

A request may steer the routing decision per call without changing any
application code, through OpenAI-compatible channels (WF-ADR-0011). This only
moves *which threshold/decision applies*; it never adds inference, so the
WF-ADR-0001/0004 boundary holds:

- the OpenAI ``model`` field is a routing directive — ``auto`` (or any
  unrecognized value) scores per config, an exact configured endpoint name
  pins the call to that endpoint, and ``prefer-local`` / ``prefer-hosted`` pin
  to the low / high end of the configured router (``prefer-cloud`` is a
  back-compat alias of ``prefer-hosted``);
- an ``X-Wayfinder-Threshold`` header (a number in ``0.0``–``1.0``) re-decides
  the call at that binary cut, reusing the configured scoring weights.

Note the score is a *structural* proxy (length, headings, lists, code, links), not
a verdict on semantic difficulty: a short but hard prompt scores low. Calibrate the
threshold on your own traffic (``wayfinder-router calibrate``); the default is only
a starting point.

Every response carries the decision signal: ``x-wayfinder-router-model`` (the
chosen endpoint), ``x-wayfinder-router-score`` (the structural score, always
computed), ``x-wayfinder-router-mode`` (``scored`` / ``pinned`` /
``threshold-override``), and ``x-wayfinder-router-request-id``. ``GET /router`` shows
recent decisions at a glance and ``X-Wayfinder-Debug: true`` surfaces the decision in
the response body (WF-ADR-0014).

``GET /metrics`` exposes the same decisions as Prometheus counters and histograms —
metadata only, never prompt text, off the scored path — hand-rolled with no extra
dependency (WF-ADR-0018).

``GET /v1/models`` advertises the selectable options — ``auto``,
``prefer-local`` / ``prefer-hosted`` (for a tiered router), and the configured
endpoint names — so a client discovers them without a hand-written list
(WF-ADR-0012).

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

import asyncio
import importlib.resources
import json
import logging
import os
import time
import tomllib
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .complexity import (
    RoutingConfig,
    Tier,
    explain_score,
    recommend_tier,
    score_complexity,
)
from .config import (
    WayfinderConfigError,
    dump_routing_toml,
    find_config_file,
    load_routing_config,
)
from .feedback import DEFAULT_LOG, record_label
from .profiles import PROFILES
from . import pricing, reliability

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_app
    from fastapi import FastAPI, Response

logger = logging.getLogger("wayfinder_router.gateway")

_INSTALL_HINT = "the gateway needs its extra: pip install 'wayfinder-router[gateway]'"
_TIMEOUT_ENV = "WAYFINDER_ROUTER_TIMEOUT"
_FEEDBACK_TOKEN_ENV = "WAYFINDER_ROUTER_FEEDBACK_TOKEN"
_SAVINGS_FILE_ENV = "WAYFINDER_ROUTER_SAVINGS_FILE"  # persist the savings ledger here (WF-DESIGN-0007)
_SAVINGS_SAVE_INTERVAL = 5.0  # seconds; debounce best-effort disk snapshots
_DEFAULT_TIMEOUT = 60.0
_RECENT_MAX = 200  # routing decisions kept in memory for the /router view (metadata only)

# A tiny, self-contained "is routing working?" dashboard (WF-ADR-0014). No CDN, no
# build step, no prompt text — it polls /router/recent (decision metadata only).
_DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wayfinder routing</title><style>
body{font:14px ui-sans-serif,system-ui,sans-serif;margin:2rem;color:#1b1f1d;background:#f4efe6}
h1{font-size:1.1rem;margin:0 0 .25rem}#counts{color:#5c635f;margin-bottom:1rem}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #ddd4c4}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:#5c635f}
td{font-variant-numeric:tabular-nums}code{font-family:ui-monospace,monospace;color:#0c655d}
.pill{display:inline-block;padding:.05rem .55rem;border-radius:999px;background:#d8ede9;color:#0c655d;font-size:.8rem}
@media(prefers-color-scheme:dark){body{background:#0e1614;color:#eef2ee}th,#counts{color:#9aa6a0}
td,th{border-color:#28332f}code{color:#46c8b9}.pill{background:#142e2a;color:#46c8b9}}
</style></head><body>
<h1>Wayfinder routing <span id="total" class="pill">…</span></h1>
<div id="counts"></div>
<table><thead><tr><th>when</th><th>model</th><th>score</th><th>mode</th><th>request id</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
async function tick(){
  try{
    const d=await (await fetch('/router/recent?limit=50')).json();
    total.textContent=d.total+' routed';
    counts.textContent=Object.entries(d.by_model).map(([k,v])=>k+': '+v).join('  ·  ');
    rows.innerHTML=d.recent.map(x=>`<tr><td>${new Date(x.ts*1000).toLocaleTimeString()}</td>`+
      `<td>${x.model}</td><td>${x.score.toFixed(2)}</td><td>${x.mode}</td>`+
      `<td><code>${x.request_id}</code></td></tr>`).join('');
  }catch(e){counts.textContent='gateway unreachable';}
}
tick();setInterval(tick,2000);
</script></body></html>"""

# The decision-first chat demo (WF-ADR-0020). The markup is the canonical file
# ``wayfinder_router/demo.html`` (shipped as package data); it is read once here so
# the page stays a single self-contained asset: no build, no CDN, no fonts fetched
# (system stack only). It calls /v1/chat/completions with model="auto" +
# X-Wayfinder-Debug to show the decision (model / score / why / cost); pair with
# --dry-run for a keyless demo. Richer chat features are the trigger to upstream
# into LibreChat, not to grow this page.
_DEMO_HTML = (importlib.resources.files("wayfinder_router") / "demo.html").read_text(
    encoding="utf-8"
)

# --- metrics (WF-ADR-0018) --------------------------------------------------
# Prometheus histogram bucket bounds, in seconds. Decision latency is a text scan
# with no model call, so its buckets are sub-millisecond; upstream latency spans a
# model round-trip, so its buckets are coarse.
_DECISION_BUCKETS = (0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05)
_UPSTREAM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _new_hist(bounds: tuple[float, ...]) -> dict:
    return {"bounds": bounds, "counts": [0] * len(bounds), "sum": 0.0, "count": 0}


def _observe(hist: dict, value: float) -> None:
    hist["sum"] += value
    hist["count"] += 1
    for i, bound in enumerate(hist["bounds"]):
        if value <= bound:
            hist["counts"][i] += 1


def _label_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_histogram(name: str, hist: dict, label_pairs: str = "") -> list[str]:
    sep = "," if label_pairs else ""
    out: list[str] = []
    for bound, count in zip(hist["bounds"], hist["counts"], strict=True):
        out.append(f'{name}_bucket{{{label_pairs}{sep}le="{bound:g}"}} {count}')
    out.append(f'{name}_bucket{{{label_pairs}{sep}le="+Inf"}} {hist["count"]}')
    braces = f"{{{label_pairs}}}" if label_pairs else ""
    out.append(f"{name}_sum{braces} {hist['sum']:g}")
    out.append(f"{name}_count{braces} {hist['count']}")
    return out


class Metrics:
    """In-memory gateway metrics rendered in the Prometheus text format (WF-ADR-0018).

    Metadata only — ``model`` and ``mode`` labels, never prompt text — mirroring
    the /router ring's privacy stance. Pure in-process counters; the /metrics
    endpoint that reads them is off the scored path (no key, no model call, no
    network). Counters reset on restart, as Prometheus expects.
    """

    def __init__(self, version: str) -> None:
        self.version = version
        self.requests: dict[tuple[str, str], int] = {}  # (model, mode) -> count
        self.upstream_errors: dict[str, int] = {}  # model -> count
        self.reload_failures = 0
        self.decision = _new_hist(_DECISION_BUCKETS)
        self.upstream: dict[str, dict] = {}  # model -> histogram
        self.model_costs: dict[str, float] = {}  # model -> cost_per_1k (WF-ADR-0017)
        self.realized_cost = 0.0  # cumulative realized spend (WF-DESIGN-0007)
        self.baseline_cost = 0.0  # cumulative always-frontier counterfactual

    def set_model_costs(self, costs: dict[str, float]) -> None:
        """Record per-model cost metadata to surface as a gauge (informational)."""
        self.model_costs = dict(costs)

    def observe_cost(self, realized: float, baseline: float) -> None:
        """Accumulate realized spend and the always-frontier baseline (WF-DESIGN-0007)."""
        self.realized_cost = round(self.realized_cost + realized, 6)
        self.baseline_cost = round(self.baseline_cost + baseline, 6)

    def observe_decision(self, model: str, mode: str, seconds: float) -> None:
        key = (model, mode)
        self.requests[key] = self.requests.get(key, 0) + 1
        _observe(self.decision, seconds)

    def observe_upstream(self, model: str, seconds: float) -> None:
        hist = self.upstream.get(model)
        if hist is None:
            hist = self.upstream[model] = _new_hist(_UPSTREAM_BUCKETS)
        _observe(hist, seconds)

    def observe_upstream_error(self, model: str) -> None:
        self.upstream_errors[model] = self.upstream_errors.get(model, 0) + 1

    def record_reload_failure(self) -> None:
        self.reload_failures += 1

    def render(self) -> str:
        lines: list[str] = []
        lines.append("# HELP wayfinder_router_build_info Build information.")
        lines.append("# TYPE wayfinder_router_build_info gauge")
        lines.append(f'wayfinder_router_build_info{{version="{_label_escape(self.version)}"}} 1')

        lines.append("# HELP wayfinder_router_requests_total Routed requests by model and mode.")
        lines.append("# TYPE wayfinder_router_requests_total counter")
        for (model, mode), n in sorted(self.requests.items()):
            labels = f'model="{_label_escape(model)}",mode="{_label_escape(mode)}"'
            lines.append(f"wayfinder_router_requests_total{{{labels}}} {n}")

        lines.append(
            "# HELP wayfinder_router_upstream_errors_total Upstream transport failures by model."
        )
        lines.append("# TYPE wayfinder_router_upstream_errors_total counter")
        for model, n in sorted(self.upstream_errors.items()):
            lines.append(
                f'wayfinder_router_upstream_errors_total{{model="{_label_escape(model)}"}} {n}'
            )

        lines.append(
            "# HELP wayfinder_router_config_reload_failures_total "
            "Config reloads that failed and kept the last-good config."
        )
        lines.append("# TYPE wayfinder_router_config_reload_failures_total counter")
        lines.append(f"wayfinder_router_config_reload_failures_total {self.reload_failures}")

        if self.model_costs:
            lines.append(
                "# HELP wayfinder_router_model_cost_per_1k "
                "Configured per-1k-token cost by model (informational, WF-ADR-0017)."
            )
            lines.append("# TYPE wayfinder_router_model_cost_per_1k gauge")
            for model, cost in sorted(self.model_costs.items()):
                lines.append(
                    f'wayfinder_router_model_cost_per_1k{{model="{_label_escape(model)}"}} {cost:g}'
                )

        lines.append(
            "# HELP wayfinder_router_realized_cost_total Cumulative realized spend on the chosen "
            "tier (USD, or relative units when no cost_per_1k is configured; WF-DESIGN-0007)."
        )
        lines.append("# TYPE wayfinder_router_realized_cost_total counter")
        lines.append(f"wayfinder_router_realized_cost_total {self.realized_cost:g}")
        lines.append(
            "# HELP wayfinder_router_baseline_cost_total Cumulative cost had every request gone to "
            "the dearest tier (the always-frontier counterfactual)."
        )
        lines.append("# TYPE wayfinder_router_baseline_cost_total counter")
        lines.append(f"wayfinder_router_baseline_cost_total {self.baseline_cost:g}")
        lines.append(
            "# HELP wayfinder_router_savings_cost_total Cumulative savings vs always-frontier "
            "(baseline minus realized)."
        )
        lines.append("# TYPE wayfinder_router_savings_cost_total counter")
        lines.append(
            f"wayfinder_router_savings_cost_total {round(self.baseline_cost - self.realized_cost, 6):g}"
        )

        lines.append(
            "# HELP wayfinder_router_decision_latency_seconds "
            "Time to score a prompt and pick a model (no model call)."
        )
        lines.append("# TYPE wayfinder_router_decision_latency_seconds histogram")
        lines += _render_histogram("wayfinder_router_decision_latency_seconds", self.decision)

        lines.append(
            "# HELP wayfinder_router_upstream_latency_seconds "
            "Upstream model round-trip time by model."
        )
        lines.append("# TYPE wayfinder_router_upstream_latency_seconds histogram")
        for model, hist in sorted(self.upstream.items()):
            lines += _render_histogram(
                "wayfinder_router_upstream_latency_seconds",
                hist,
                f'model="{_label_escape(model)}"',
            )
        return "\n".join(lines) + "\n"


class GatewayUnavailable(Exception):
    """The gateway extra (fastapi / uvicorn / httpx) is not installed."""


class UpstreamError(Exception):
    """An upstream call failed at the transport level (timeout, connection)."""


@dataclass(frozen=True)
class GatewayModel:
    """An upstream endpoint a recommended model name maps to."""

    base_url: str  # OpenAI-compatible base, e.g. http://localhost:11434/v1
    model: str  # the upstream model id to send in the forwarded request
    api_key_env: str | None = None  # env var holding the key, or None for no auth
    api_key_cmd: str | None = None  # command that fills api_key_env when unset (WF-DESIGN-0006)
    cost_per_1k: float | None = None  # optional cost metadata (WF-ADR-0017), informational
    fallbacks: tuple[str, ...] = ()  # same-tier endpoints to try if this one fails (WF-ADR-0031)
    context_window: int | None = None  # optional token limit for the pre-call check (WF-ADR-0031)


@dataclass(frozen=True)
class Budget:
    """A spend cap on the savings ledger's realized cost (WF-ROADMAP-0006).

    On breach the gateway either **degrades** to the cheapest tier — the failover
    ``degrade`` primitive (WF-ADR-0031), which never raises cost — or **blocks** the
    request with HTTP 402. Enforced only when the price table is real (``priced``):
    a relative-unit demo has no dollars to cap, so the budget is a no-op there. The
    cap never changes the scored *decision* (WF-ADR-0001); it changes *delivery*.
    """

    limit: float  # spend ceiling in the ledger's unit, over ``window``
    window: str = "day"  # "day" | "month" | "all"
    on_breach: str = "degrade"  # "degrade" (to cheapest tier) | "block" (402)


@dataclass(frozen=True)
class GatewayConfig:
    """Maps recommended model names to upstream endpoints (from `[gateway.models]`).

    ``route_on`` selects which part of a multi-turn chat the router scores
    (WF-ADR-0021); see :func:`extract_prompt`. Default ``"turn"``. ``sticky``
    latches a conversation to the highest tier any of its turns has needed
    (WF-ADR-0022), so a chat that goes hard stays on the big model. Default off.
    ``sticky_cooldown`` is the number of calm turns after which the latch decays
    back down (``0`` = never; monotonic).
    """

    models: dict[str, GatewayModel] = field(default_factory=dict)
    route_on: str = "turn"
    sticky: bool = False
    sticky_cooldown: int = 0
    # Reliability (WF-ADR-0031): bounded retries on transport/429/5xx, and a per-target
    # circuit breaker. ``failover`` governs crossing tiers on exhaustion — "same-tier"
    # (default), "degrade" (cheaper), or "escalate" (dearer); same-tier fallbacks are per-model.
    retries: int = 2
    breaker_threshold: int = 5
    breaker_cooldown: float = 30.0
    failover: str = "same-tier"
    # Budget (WF-ROADMAP-0006): an optional spend cap that degrades to the cheapest tier
    # (or blocks) once the period's realized cost is reached. ``None`` = no cap.
    budget: Budget | None = None


# Which chat-message text the router scores. The deterministic core scores
# whatever string it is handed (WF-ADR-0001); this only chooses that string so a
# multi-turn chat does not drift toward cloud as the transcript grows.
ROUTE_ON_SCOPES = ("turn", "last_user", "user", "all")

# Budget windows and breach behaviours (WF-ROADMAP-0006).
BUDGET_WINDOWS = ("day", "month", "all")
BUDGET_BREACH = ("degrade", "block")


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


def _budget_from_toml(raw: object, where: str) -> Budget | None:
    """Parse and validate the optional ``[gateway.budget]`` table (WF-ROADMAP-0006)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WayfinderConfigError(f"{where}: '[gateway.budget]' must be a table")
    limit = raw.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        raise WayfinderConfigError(f"{where}: 'gateway.budget.limit' must be a positive number")
    window = raw.get("window", "day")
    if window not in BUDGET_WINDOWS:
        raise WayfinderConfigError(
            f"{where}: 'gateway.budget.window' must be one of {', '.join(BUDGET_WINDOWS)}"
        )
    on_breach = raw.get("on_breach", "degrade")
    if on_breach not in BUDGET_BREACH:
        raise WayfinderConfigError(
            f"{where}: 'gateway.budget.on_breach' must be one of {', '.join(BUDGET_BREACH)}"
        )
    return Budget(limit=float(limit), window=window, on_breach=on_breach)


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
    route_on = gateway.get("route_on", "turn")
    if route_on not in ROUTE_ON_SCOPES:
        raise WayfinderConfigError(
            f"{where}: 'gateway.route_on' must be one of {', '.join(ROUTE_ON_SCOPES)}"
        )
    sticky = gateway.get("sticky", False)
    if not isinstance(sticky, bool):
        raise WayfinderConfigError(f"{where}: 'gateway.sticky' must be a boolean")
    cooldown = gateway.get("sticky_cooldown", 0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise WayfinderConfigError(f"{where}: 'gateway.sticky_cooldown' must be a non-negative integer")
    retries = gateway.get("retries", 2)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise WayfinderConfigError(f"{where}: 'gateway.retries' must be a non-negative integer")
    breaker_threshold = gateway.get("breaker_threshold", 5)
    if isinstance(breaker_threshold, bool) or not isinstance(breaker_threshold, int) or breaker_threshold < 1:
        raise WayfinderConfigError(f"{where}: 'gateway.breaker_threshold' must be a positive integer")
    breaker_cooldown = gateway.get("breaker_cooldown", 30.0)
    if isinstance(breaker_cooldown, bool) or not isinstance(breaker_cooldown, (int, float)) or breaker_cooldown < 0:
        raise WayfinderConfigError(f"{where}: 'gateway.breaker_cooldown' must be a non-negative number")
    failover = gateway.get("failover", "same-tier")
    if failover not in reliability.FAILOVER_POLICIES:
        raise WayfinderConfigError(
            f"{where}: 'gateway.failover' must be one of {', '.join(reliability.FAILOVER_POLICIES)}"
        )
    budget = _budget_from_toml(gateway.get("budget"), where)
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
        api_key_cmd = entry.get("api_key_cmd")
        if api_key_cmd is not None and (not isinstance(api_key_cmd, str) or not api_key_cmd):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.api_key_cmd' must be a non-empty string"
            )
        if api_key_cmd is not None and api_key_env is None:
            # The command fills a named variable; without one there is nowhere to put the key.
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.api_key_cmd' needs 'api_key_env' to name "
                "the variable it fills"
            )
        cost_per_1k = entry.get("cost_per_1k")
        if cost_per_1k is not None and (
            isinstance(cost_per_1k, bool)
            or not isinstance(cost_per_1k, (int, float))
            or cost_per_1k < 0
        ):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.cost_per_1k' must be a non-negative number"
            )
        raw_fallbacks = entry.get("fallbacks", [])
        if not isinstance(raw_fallbacks, list) or not all(
            isinstance(f, str) and f for f in raw_fallbacks
        ):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.fallbacks' must be a list of model names"
            )
        context_window = entry.get("context_window")
        if context_window is not None and (
            isinstance(context_window, bool) or not isinstance(context_window, int)
            or context_window < 1
        ):
            raise WayfinderConfigError(
                f"{where}: 'gateway.models.{name}.context_window' must be a positive integer"
            )
        models[name] = GatewayModel(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            api_key_cmd=api_key_cmd,
            cost_per_1k=float(cost_per_1k) if cost_per_1k is not None else None,
            fallbacks=tuple(raw_fallbacks),
            context_window=context_window,
        )
    for name, gm in models.items():  # fallbacks must name other configured models
        for fb in gm.fallbacks:
            if fb not in models:
                raise WayfinderConfigError(
                    f"{where}: 'gateway.models.{name}.fallbacks' names unknown model '{fb}'"
                )
            if fb == name:
                raise WayfinderConfigError(
                    f"{where}: 'gateway.models.{name}.fallbacks' cannot include itself"
                )
    return GatewayConfig(
        models=models, route_on=route_on, sticky=sticky, sticky_cooldown=cooldown,
        retries=retries, breaker_threshold=breaker_threshold, breaker_cooldown=breaker_cooldown,
        failover=failover, budget=budget,
    )


def dump_gateway_toml(gateway: GatewayConfig) -> str:
    """Serialize a :class:`GatewayConfig` back to ``[gateway.models.*]`` TOML.

    Used by recalibration to preserve the endpoint mapping when it rewrites the
    routing section. Emits ``api_key_env`` (the env-var *name*) and ``api_key_cmd``
    (a command/reference that *fills* it) — never a secret value.
    """
    blocks: list[str] = []
    nondefault_gateway = (
        gateway.route_on != "turn" or gateway.sticky or gateway.sticky_cooldown
        or gateway.retries != 2 or gateway.breaker_threshold != 5 or gateway.breaker_cooldown != 30.0
        or gateway.failover != "same-tier"
    )
    if nondefault_gateway:
        lines = ["[gateway]"]
        if gateway.route_on != "turn":
            lines.append(f'route_on = "{gateway.route_on}"')
        if gateway.sticky:
            lines.append("sticky = true")
        if gateway.sticky_cooldown:
            lines.append(f"sticky_cooldown = {gateway.sticky_cooldown}")
        if gateway.retries != 2:
            lines.append(f"retries = {gateway.retries}")
        if gateway.breaker_threshold != 5:
            lines.append(f"breaker_threshold = {gateway.breaker_threshold}")
        if gateway.breaker_cooldown != 30.0:
            lines.append(f"breaker_cooldown = {round(gateway.breaker_cooldown, 6)!r}")
        if gateway.failover != "same-tier":
            lines.append(f'failover = "{gateway.failover}"')
        blocks.append("\n".join(lines))
    if gateway.budget is not None:  # emitted as its own [gateway.budget] sub-table
        b = gateway.budget
        lines = ["[gateway.budget]", f"limit = {round(b.limit, 6)!r}"]
        if b.window != "day":
            lines.append(f'window = "{b.window}"')
        if b.on_breach != "degrade":
            lines.append(f'on_breach = "{b.on_breach}"')
        blocks.append("\n".join(lines))
    for name, model in gateway.models.items():
        lines = [
            f"[gateway.models.{name}]",
            f'base_url = "{model.base_url}"',
            f'model = "{model.model}"',
        ]
        if model.api_key_env:
            lines.append(f'api_key_env = "{model.api_key_env}"')
        if model.api_key_cmd:  # a command/reference, not a secret — safe to round-trip
            lines.append(f'api_key_cmd = "{model.api_key_cmd}"')
        if model.cost_per_1k is not None:
            lines.append(f"cost_per_1k = {round(model.cost_per_1k, 6)!r}")
        if model.fallbacks:
            rendered = ", ".join(f'"{f}"' for f in model.fallbacks)
            lines.append(f"fallbacks = [{rendered}]")
        if model.context_window is not None:
            lines.append(f"context_window = {model.context_window}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _message_text(message: dict) -> str | None:
    """Text of one OpenAI-style message — plain string or array-of-parts — or None."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(parts) if parts else None
    return None


def extract_prompt(messages: object, *, route_on: str = "turn") -> str:
    """Deterministically join the chat-message text the router should score.

    ``route_on`` selects the scope (WF-ADR-0021), so a multi-turn chat does not
    drift toward cloud as its transcript (and the assistant's own replies) grows:

    - ``"turn"`` (default): the system message(s) plus the latest user message —
      the standing instructions and the new ask. Stable across turns.
    - ``"last_user"``: the latest user message only.
    - ``"user"``: every user message (excludes system and assistant).
    - ``"all"``: every message, all roles (legacy; the score ratchets upward over
      a conversation).

    Falls back to the last message when role-filtering finds nothing (e.g. a
    role-less or assistant-only payload), so the router never scores an empty
    string and silently routes local. Handles string and array-of-parts content.
    """
    if not isinstance(messages, list):
        return ""
    typed = [m for m in messages if isinstance(m, dict)]

    if route_on == "all":
        chosen: list[dict] = typed
    elif route_on == "user":
        chosen = [m for m in typed if m.get("role") == "user"]
    elif route_on == "last_user":
        last = next((m for m in reversed(typed) if m.get("role") == "user"), None)
        chosen = [last] if last is not None else []
    else:  # "turn" (default): standing system context + the new ask
        systems = [m for m in typed if m.get("role") == "system"]
        last = next((m for m in reversed(typed) if m.get("role") == "user"), None)
        chosen = systems + ([last] if last is not None else [])

    if not chosen and typed and route_on != "all":
        chosen = [typed[-1]]

    return "\n".join(t for t in (_message_text(m) for m in chosen) if t is not None)


# Per-request override transport (WF-ADR-0011). These are pure and offline: they
# only move which threshold/decision applies, never invoke a model.
THRESHOLD_HEADER = "x-wayfinder-threshold"
_AUTO = "auto"  # the OpenAI `model` sentinel meaning "Wayfinder decides"
_PREFER_LOW = "prefer-local"
_PREFER_HIGH = "prefer-hosted"  # canonical high-end directive (v0.1.3+)
_PREFER_HIGH_ALIASES = ("prefer-cloud",)  # back-compat: shipped in v0.1.2, still resolves


class BadOverride(Exception):
    """A per-request override was supplied but is malformed or not applicable."""


def resolve_pin(model_field: object, routing: RoutingConfig, gateway: GatewayConfig) -> str | None:
    """Resolve an explicit endpoint pin from the OpenAI ``model`` field, or ``None``.

    ``auto``, an empty value, or any string that is neither a configured endpoint
    name nor a ``prefer-*`` directive returns ``None`` — the request asks Wayfinder
    to score and decide (kept tolerant so ordinary OpenAI ``model`` ids pass
    through). ``prefer-local`` / ``prefer-hosted`` resolve to the low / high end of
    the configured router (its first / last tier's model); ``prefer-cloud`` is a
    back-compat alias of ``prefer-hosted``. They apply only to a tiered/binary
    router — a classifier has no ordered ladder, so ``prefer-*`` falls through to
    scoring there.
    """
    if not isinstance(model_field, str):
        return None
    name = model_field.strip()
    if not name or name == _AUTO:
        return None
    if routing.classifier is None and routing.tiers:
        if name == _PREFER_LOW:
            return routing.tiers[0].model
        if name == _PREFER_HIGH or name in _PREFER_HIGH_ALIASES:
            return routing.tiers[-1].model
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


# Two more per-request overrides (WF-ADR-0011), mirroring the threshold header: they
# let a client (e.g. the demo's settings) move the routing scope / latch for one
# request without touching server config. Still pure and offline.
ROUTE_ON_HEADER = "x-wayfinder-route-on"
STICKY_HEADER = "x-wayfinder-sticky"
STICKY_COOLDOWN_HEADER = "x-wayfinder-sticky-cooldown"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def parse_route_on_header(value: str | None) -> str | None:
    """Parse ``X-Wayfinder-Route-On`` into a scope, or ``None`` when absent.

    Raises :class:`BadOverride` for an unknown scope.
    """
    if value is None or not value.strip():
        return None
    scope = value.strip().lower()
    if scope not in ROUTE_ON_SCOPES:
        raise BadOverride(
            f"{ROUTE_ON_HEADER} must be one of {', '.join(ROUTE_ON_SCOPES)}, got {value!r}"
        )
    return scope


def resolve_sticky(value: str | None, default: bool) -> bool:
    """Resolve the conversation-latch flag from ``X-Wayfinder-Sticky``, else the config default."""
    if value is None or not value.strip():
        return default
    token = value.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise BadOverride(f"{STICKY_HEADER} must be true or false, got {value!r}")


def resolve_sticky_cooldown(value: str | None, default: int) -> int:
    """Resolve the latch cool-down (calm turns to release) from the header, else the default.

    ``0`` means the latch never decays (monotonic). Raises :class:`BadOverride` for a
    non-integer or negative value.
    """
    if value is None or not value.strip():
        return default
    try:
        cooldown = int(value.strip())
    except ValueError as exc:
        raise BadOverride(
            f"{STICKY_COOLDOWN_HEADER} must be a non-negative integer, got {value!r}"
        ) from exc
    if cooldown < 0:
        raise BadOverride(f"{STICKY_COOLDOWN_HEADER} must be >= 0, got {cooldown}")
    return cooldown


# In-demo scoring overrides (WF-ADR-0023): a request body field that tunes the
# scoring *function* (feature weights + lexicon terms) for this request only. Unlike
# the header overrides above (which move which threshold/scope/latch applies), this
# changes how the score is computed — but the scorer stays pure: the gateway only
# chooses the config it hands over (WF-ADR-0001). Opt-in, additive, never forwarded
# upstream. Production tuning is still `calibrate`; this is the demo's live knob.
TUNING_FIELD = "wayfinder_tuning"
_MAX_LEXICON_TERMS = 2000


def apply_scoring_overrides(routing: RoutingConfig, override: object) -> RoutingConfig:
    """Return a ``RoutingConfig`` variant with per-request weight/lexicon tuning applied.

    ``override`` is the parsed ``wayfinder_tuning`` body field: an optional partial
    ``weights`` map (merged over the configured weights) and an optional ``lexicon``
    with ``reasoning_terms`` / ``constraint_terms`` lists (which replace those sets).
    Returns ``routing`` unchanged when absent; raises :class:`BadOverride` on malformed
    input so the demo gets a clear 400.
    """
    if override is None:
        return routing
    if not isinstance(override, dict):
        raise BadOverride(f"{TUNING_FIELD} must be an object")
    weights = dict(routing.weights)
    raw_weights = override.get("weights")
    if raw_weights is not None:
        if not isinstance(raw_weights, dict):
            raise BadOverride(f"{TUNING_FIELD}.weights must be an object")
        for name, value in raw_weights.items():
            if name not in weights:
                raise BadOverride(f"{TUNING_FIELD}.weights: unknown feature {name!r}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise BadOverride(f"{TUNING_FIELD}.weights.{name} must be a non-negative number")
            weights[name] = float(value)
    lexicon = routing.lexicon
    raw_lexicon = override.get("lexicon")
    if raw_lexicon is not None:
        if not isinstance(raw_lexicon, dict):
            raise BadOverride(f"{TUNING_FIELD}.lexicon must be an object")
        changes: dict[str, frozenset[str]] = {}
        for key in ("reasoning_terms", "constraint_terms"):
            if key not in raw_lexicon:
                continue
            terms = raw_lexicon[key]
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} must be a list of strings")
            cleaned = frozenset(t.strip().lower() for t in terms if t.strip())
            if len(cleaned) > _MAX_LEXICON_TERMS:
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} exceeds {_MAX_LEXICON_TERMS} terms")
            changes[key] = cleaned
        if changes:
            lexicon = replace(routing.lexicon, **changes)
    return replace(routing, weights=weights, lexicon=lexicon)


def _tier_rank(model: str, tiers: tuple[Tier, ...]) -> int:
    """Index of ``model`` in the ordered tier ladder, or -1 if it is not a tier."""
    for i, tier in enumerate(tiers):
        if tier.model == model:
            return i
    return -1


def conversation_high_water(
    messages: object, routing: RoutingConfig, tiers: tuple[Tier, ...], *, cooldown: int = 0
) -> str | None:
    """The tier the conversation latches to (WF-ADR-0022).

    Each user turn is scored on its own (with the standing system context), so this
    is computed from per-turn tiers — a *max over turns*, not a sum, so it does not
    inflate with conversation length the way concatenating the transcript would.

    With ``cooldown == 0`` the latch is monotonic: the highest tier any turn reached,
    and it never steps down. With ``cooldown == N`` (N >= 1) the latch *decays*: after
    ``N`` consecutive turns below the current latch, it steps down to that lower tier —
    so a chat that goes hard then stays light drifts back toward local. Returns the
    tier's model name, or ``None`` when there are no user turns to score.
    """
    if not isinstance(messages, list):
        return None
    systems = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    ranks: list[int] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = extract_prompt(systems + [message], route_on="turn")
            model = recommend_tier(score_complexity(text, config=routing).score, tiers)
            ranks.append(max(0, _tier_rank(model, tiers)))
    if not ranks:
        return None
    # Walk the turns oldest->newest: a turn at or above the latch holds (and resets the
    # calm counter); a turn below it counts as calm, and once `cooldown` calm turns
    # accumulate the latch steps down to that turn's tier.
    latched, calm = 0, 0
    for rank in ranks:
        if rank >= latched:
            latched, calm = rank, 0
        else:
            calm += 1
            if cooldown and calm >= cooldown:
                latched, calm = rank, 0
    return tiers[latched].model


def forward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> tuple[int, bytes, str]:
    """POST ``json_body`` to ``url``; return ``(status, content, content_type)``.

    The synchronous forwarder used by :func:`invoke_model` (the onboarding A/B
    caller, which runs outside the async server). Isolated so tests can substitute
    it without a real upstream.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    response = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    return response.status_code, response.content, response.headers.get(
        "content-type", "application/json"
    )


async def aforward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> tuple[int, bytes, str]:
    """Async, non-streaming forward used by the server; returns the buffered reply.

    Transport failures (timeout, connection refused) raise :class:`UpstreamError`
    so the handler can return an OpenAI-shaped error instead of a bare 500.
    Isolated so tests can substitute it without a real upstream.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc
    return response.status_code, response.content, response.headers.get(
        "content-type", "application/json"
    )


async def aforward_stream(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> AsyncIterator[bytes]:
    """Async generator relaying an upstream Server-Sent-Events stream chunk by chunk.

    Transport failures raise :class:`UpstreamError`, which the handler turns into a
    terminal SSE error event. Isolated so tests can substitute it.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=json_body) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc


def invoke_messages(
    model: GatewayModel, messages: list[dict], timeout: float = _DEFAULT_TIMEOUT
) -> str:
    """Run a full OpenAI-style ``messages`` conversation through one upstream (BYO key).

    The multi-turn relay behind :func:`invoke_model`; the terminal chat
    (WF-DESIGN-0001) uses it in-process to send conversation history and get a real
    reply, reusing the gateway's exact forward path (key from the environment,
    OpenAI-compatible ``/chat/completions``). Reuses :func:`forward_request`, so
    tests substitute the network the same way.
    """
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    body = {"model": model.model, "messages": list(messages)}
    url = model.base_url.rstrip("/") + "/chat/completions"
    status, content, _ = forward_request(url, headers, body, timeout)
    if status >= 400:
        raise RuntimeError(f"{model.model} upstream returned {status}: {content[:200]!r}")
    try:
        data = json.loads(content)
        return str(data["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{model.model} returned an unexpected response shape: {exc}") from exc


def invoke_model(model: GatewayModel, prompt: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Run a single ``prompt`` turn through one upstream and return its text (BYO key).

    The single-prompt call the onboarding A/B harness uses; delegates to
    :func:`invoke_messages` so the relay lives in one place.
    """
    return invoke_messages(model, [{"role": "user", "content": prompt}], timeout)


def parse_sse_deltas(lines: Iterable[str]) -> Iterator[str]:
    """Yield assistant text deltas from OpenAI-style SSE ``data:`` lines (pure; testable)."""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
            delta = obj["choices"][0]["delta"].get("content")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            continue
        if delta:
            yield str(delta)


def _first_choice_text(response: object) -> str:
    """The assistant text of a non-streaming chat completion, or "" — for token estimates."""
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    return ""


def stream_messages(
    model: GatewayModel, messages: list[dict], timeout: float = _DEFAULT_TIMEOUT
) -> Iterator[str]:
    """Stream assistant text deltas from one upstream over SSE (sync; BYO key).

    The streaming counterpart to :func:`invoke_messages` for the terminal chat — sends
    ``stream: true`` and yields ``delta.content`` chunks as they arrive, reusing the same
    key/URL handling. Raises :class:`UpstreamError` on transport failure.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    body = {"model": model.model, "messages": list(messages), "stream": True}
    url = model.base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.stream("POST", url, headers=headers, json=body, timeout=timeout) as response:
            if response.status_code >= 400:
                response.read()
                raise RuntimeError(f"{model.model} upstream returned {response.status_code}")
            yield from parse_sse_deltas(response.iter_lines())
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc


def _resolve_timeout() -> float:
    """The upstream timeout in seconds, from ``WAYFINDER_ROUTER_TIMEOUT`` or the default."""
    raw = os.environ.get(_TIMEOUT_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("ignoring invalid %s=%r", _TIMEOUT_ENV, raw)
    return _DEFAULT_TIMEOUT


class _ConfigHolder:
    """Caches routing + gateway config, reloading when ``wayfinder-router.toml`` changes.

    Lets a recalibration (CLI, cron, or UI) take effect on the running gateway
    with no restart: each request checks the config file's mtime and re-reads only
    when it moved. A malformed mid-flight write keeps the last-good config (the
    marker advances so it is not retried every request), is logged rather than
    failing serving silently, and increments the reload-failure metric.
    """

    def __init__(
        self, start_dir: str, *, on_reload_failure: Callable[[], None] | None = None
    ) -> None:
        self.start_dir = start_dir
        self._on_reload_failure = on_reload_failure
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
            except WayfinderConfigError as exc:
                # Keep last-good config; the marker advanced so we do not thrash.
                logger.warning("config reload failed, keeping last-good config: %s", exc)
                if self._on_reload_failure is not None:
                    self._on_reload_failure()
        return self._routing, self._gateway


def build_app(
    start_dir: str = ".", *, dry_run: bool = False, timeout: float | None = None
) -> FastAPI:
    """Build the FastAPI gateway app; config hot-reloads on ``wayfinder-router.toml`` change.

    ``dry_run`` makes ``/v1/chat/completions`` return the routing decision without
    calling any upstream — try the router with no backends. ``timeout`` overrides the
    upstream timeout (else ``WAYFINDER_ROUTER_TIMEOUT`` or 60s).
    """
    try:
        from fastapi import Body, FastAPI, Header, Response
        from fastapi.responses import (
            HTMLResponse,
            JSONResponse,
            PlainTextResponse,
            StreamingResponse,
        )
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc

    from . import __version__  # local import: avoids a circular import at module load

    metrics = Metrics(__version__)
    holder = _ConfigHolder(start_dir, on_reload_failure=metrics.record_reload_failure)
    request_timeout = timeout if timeout is not None else _resolve_timeout()
    feedback_token = os.environ.get(_FEEDBACK_TOKEN_ENV)
    recent: deque[dict] = deque(maxlen=_RECENT_MAX)  # decision metadata only, no prompt text

    # Savings ledger (WF-DESIGN-0007): per-day realized/baseline/savings from routing
    # decisions x a price table. Persisted best-effort so the report survives restarts.
    savings_path = os.environ.get(_SAVINGS_FILE_ENV) or str(Path(start_dir) / "wayfinder-savings.json")
    try:
        ledger = pricing.SavingsLedger.load(savings_path)
    except (OSError, ValueError):
        ledger = pricing.SavingsLedger()
    _last_save = [0.0]  # debounce cell for disk snapshots

    def _persist_savings() -> None:
        now = time.time()
        if now - _last_save[0] < _SAVINGS_SAVE_INTERVAL:
            return
        _last_save[0] = now
        try:
            ledger.save(savings_path)
        except OSError as exc:  # best-effort; never break a request
            logger.warning("could not persist savings ledger to %s: %s", savings_path, exc)

    def _record_turn(
        entry: dict, chosen: str, decision: object, gw: GatewayConfig,
        response: object, prompt_text: str, completion_text: str,
    ) -> None:
        """Cost the turn from token usage x the price table; record it (no model call)."""
        model_costs = {n: m.cost_per_1k for n, m in gw.models.items()}
        tiers = getattr(decision, "tiers", None) or ()
        ladder = [t.model for t in tiers] or list(gw.models)
        costs, priced = pricing.price_table(model_costs, ladder)
        ledger.priced = priced
        pt, ct, estimated = pricing.usage_tokens(
            response, prompt_text=prompt_text, completion_text=completion_text
        )
        tc = pricing.turn_cost(chosen, pt, ct, costs, estimated=estimated)
        ledger.record(tc)
        metrics.observe_cost(tc.realized, tc.baseline)
        entry["cost"] = {  # metadata only — dollars and token counts, never prompt text
            "realized": tc.realized, "baseline": tc.baseline, "saved": tc.savings,
            "tokens": tc.prompt_tokens + tc.completion_tokens,
            "unit": "usd" if priced else "relative", "estimated": estimated,
        }
        _persist_savings()

    app = FastAPI(title="wayfinder-router-gateway")

    # Startup diagnostics: surface the misconfigurations that otherwise only show up
    # as a confusing first-request failure.
    _, gw0 = holder.current()
    metrics.set_model_costs(
        {name: model.cost_per_1k for name, model in gw0.models.items()
         if model.cost_per_1k is not None}
    )
    # Fill any api_key_cmd-backed keys from the user's secret store into the process
    # environment, in memory only (WF-DESIGN-0006), before the readiness check below.
    from . import bootstrap

    for name, reason in bootstrap.resolve_keys(gw0.models).items():
        logger.warning("gateway model '%s': could not resolve key — %s", name, reason)
    for name, model in gw0.models.items():
        if model.api_key_env and not os.environ.get(model.api_key_env):
            logger.warning("gateway model '%s' references unset env var %s", name, model.api_key_env)
    if not gw0.models and not dry_run:
        logger.warning(
            "no [gateway.models] configured; requests will fail until you add an endpoint "
            "(or run with --dry-run to see routing decisions without backends)"
        )
    if feedback_token is None:
        logger.info(
            "/v1/feedback is unauthenticated; set %s to require a bearer token", _FEEDBACK_TOKEN_ENV
        )

    # Reliability (WF-ADR-0031): one circuit breaker for the gateway's lifetime; thresholds
    # come from the initial config so this runtime state survives routing/cost hot-reloads.
    breaker = reliability.CircuitBreaker(
        threshold=gw0.breaker_threshold, cooldown=gw0.breaker_cooldown
    )

    def _auth_headers(model: GatewayModel) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if model.api_key_env:
            key = os.environ.get(model.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _deliver(
        plan: list[str], gw: GatewayConfig, body: dict, request_id: str
    ) -> tuple[str | None, int, bytes, str]:
        """Try each target in ``plan`` with bounded retries; return the one that served.

        Same-tier failover + retry + circuit breaker (WF-ADR-0031): on a transport error or
        a 429/5xx, back off and retry; on exhaustion, fall to the next configured endpoint;
        an ordinary 4xx is the client's and is returned as-is. Never re-scores the prompt.
        Returns ``(served_name|None, status, content, content_type)`` — ``None`` if all failed.
        """
        last_error = "no upstream available"
        for name in plan:
            model = gw.models[name]
            headers = _auth_headers(model)
            forward_body = {**body, "model": model.model}
            url = model.base_url.rstrip("/") + "/chat/completions"
            delays = reliability.retry_delays(gw.retries)
            for attempt in range(gw.retries + 1):
                started = time.perf_counter()
                try:
                    status, content, ctype = await aforward_request(
                        url, headers, forward_body, request_timeout
                    )
                except UpstreamError as exc:  # transport failure — always retryable
                    last_error = str(exc) or exc.__class__.__name__
                    metrics.observe_upstream_error(name)
                    if attempt < gw.retries:
                        await asyncio.sleep(delays[attempt])
                    continue
                if not reliability.is_retryable(status):  # 2xx or a client 4xx — done
                    metrics.observe_upstream(name, time.perf_counter() - started)
                    breaker.record(name, True)
                    return name, status, content, ctype
                last_error = f"upstream returned {status}"  # 429/5xx — retry/fall back
                metrics.observe_upstream_error(name)
                if attempt < gw.retries:
                    await asyncio.sleep(delays[attempt])
            breaker.record(name, False)  # every attempt on this target failed
            logger.warning("request %s: target '%s' exhausted (%s)", request_id, name, last_error)
        body_json = json.dumps(
            {"error": {"message": last_error, "type": "wayfinder_router_upstream_error"}}
        ).encode()
        return None, 502, body_json, "application/json"

    def _missing_keys(gw: GatewayConfig) -> list[str]:
        return sorted(
            name
            for name, model in gw.models.items()
            if model.api_key_env and not os.environ.get(model.api_key_env)
        )

    @app.get("/healthz")
    def healthz() -> dict:
        _, gw = holder.current()
        missing = _missing_keys(gw)
        body: dict = {"status": "degraded" if missing else "ok", "models": sorted(gw.models)}
        if missing:
            body["missing_keys"] = missing
        return body

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint() -> Response:
        """Prometheus text exposition of routing metrics (WF-ADR-0018).

        Metadata only (model / mode labels), never prompt text; a pure read of
        in-memory counters, off the scored path — no key, no model call, no network.
        """
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/v1/models")
    @app.get("/models")  # path tolerance: clients pointed at the bare host (no /v1 prefix)
    def list_models() -> dict:
        """Advertise the selectable routing options as an OpenAI-compatible list.

        Pure and offline like ``/healthz``: reads the current config only — no key,
        no model call, no network — so any OpenAI client auto-populates its model
        dropdown with the routing directives and configured endpoints instead of a
        hand-written list (WF-ADR-0012). ``prefer-*`` appears only for a
        tiered/binary router; a classifier has no ordered ladder to lean on.
        """
        routing, gw = holder.current()
        ids = [_AUTO]
        if routing.classifier is None and routing.tiers:
            ids += [_PREFER_LOW, _PREFER_HIGH]
        ids += list(gw.models)
        return {
            "object": "list",
            "data": [
                {"id": mid, "object": "model", "created": 0, "owned_by": "wayfinder"}
                for mid in ids
            ],
        }

    @app.get("/v1/savings")
    @app.get("/savings")  # path tolerance, like /v1/models
    def savings_report(period: str = "all") -> dict:
        """Per-period realized / baseline / savings from routing decisions (WF-DESIGN-0007).

        A pure read of the in-memory ledger — token counts x a price table, metadata only,
        no prompt text, no model call. ``period`` is ``today`` | ``7d`` | ``30d`` | ``all``.
        ``saved`` is "vs always-frontier"; figures are dollars when ``cost_per_1k`` is
        configured (``priced: true``), else relative units. ``price_table_version`` pins the
        current prices so a number is auditable.
        """
        days = {"today": 1, "7d": 7, "30d": 30, "all": None}.get(period, None)
        report = ledger.period(days=days)
        routing, gw = holder.current()
        model_costs = {n: m.cost_per_1k for n, m in gw.models.items()}
        ladder = [t.model for t in (routing.tiers or ())] or list(gw.models)
        costs, _ = pricing.price_table(model_costs, ladder)
        report["price_table_version"] = pricing.table_version(costs)
        return report

    @app.get("/router/recent")
    def router_recent(limit: int = 50) -> dict:
        """Read-only view of recent routing decisions (WF-ADR-0014).

        Metadata only — model, score, mode, request id, timestamp — never prompt
        text. The visibility half of the control surface: see *that* routing is
        happening and where, without inspecting per-request headers. Pure and offline.
        """
        items = list(recent)
        by_model: dict[str, int] = {}
        for entry in items:
            by_model[entry["model"]] = by_model.get(entry["model"], 0) + 1
        clamped = max(1, min(limit, _RECENT_MAX))
        return {"total": len(items), "by_model": by_model, "recent": items[-clamped:][::-1]}

    @app.get("/router", response_class=HTMLResponse)
    def router_dashboard() -> str:
        """A tiny self-contained dashboard that polls /router/recent."""
        return _DASHBOARD_HTML

    @app.get("/demo", response_class=HTMLResponse)
    def demo_page() -> str:
        """The decision-first chat demo (WF-ADR-0020): shows the routing decision,
        the score and why, and the cost saved, with a live threshold slider. Pairs
        with ``--dry-run`` for a keyless demo. Self-contained; no build, no CDN."""
        return _DEMO_HTML

    @app.get("/router/profiles")
    def lexicon_profiles() -> dict:
        """Stock lexicon profiles (WF-ADR-0024) the demo can load to seed the term lists.
        Static, read-only metadata — no model call, no prompt text."""
        return {"profiles": [p.to_dict() for p in PROFILES]}

    @app.get("/router/models")
    def router_models() -> dict:
        """Read-only view of the configured endpoints and whether each model's key is
        present (WF-ADR-0025). Returns only the env-var *name* and a boolean — never a
        secret. Keys live in the environment: set the named var and restart."""
        _, gw = holder.current()
        models = [
            {
                "name": name,
                "endpoint": m.base_url,
                "model": m.model,
                "api_key_env": m.api_key_env,
                "key_ok": m.api_key_env is None or bool(os.environ.get(m.api_key_env)),
            }
            for name, m in gw.models.items()
        ]
        return {"models": models, "dry_run": dry_run}

    @app.post("/router/config", response_class=PlainTextResponse)
    def export_config(body: dict = Body(default={})) -> Response:  # noqa: B008 - FastAPI default
        """Render the configured router as `[routing]` TOML, with any `wayfinder_tuning`
        body applied (WF-ADR-0023) — the demo's "Export config" so a tuned setup becomes
        a real, paste-able config. Pure: builds and serializes a config, no model call."""
        routing, _ = holder.current()
        try:
            tuned = apply_scoring_overrides(routing, body.get(TUNING_FIELD, body or None))
        except BadOverride as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
            )
        return PlainTextResponse(dump_routing_toml(tuned), media_type="text/plain; charset=utf-8")

    @app.post("/v1/feedback")
    def feedback(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> object:
        # Steady-state escalate loop: the caller records which model was good
        # enough for a prompt; the label feeds the next recalibration. Writing the
        # label log is guarded by an optional bearer token to prevent poisoning.
        if feedback_token is not None and authorization != f"Bearer {feedback_token}":
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        raw_text, raw_label = body.get("text"), body.get("label")
        if not isinstance(raw_text, str) or not raw_text:
            return JSONResponse(status_code=400, content={"error": "missing 'text'"})
        if not isinstance(raw_label, str) or not raw_label:
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        record_label(str(Path(start_dir) / DEFAULT_LOG), raw_text, raw_label)
        return {"ok": True}

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")  # path tolerance: base_url set without the /v1 prefix
    async def chat_completions(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        x_wayfinder_threshold: str | None = Header(default=None),
        x_wayfinder_route_on: str | None = Header(default=None),
        x_wayfinder_sticky: str | None = Header(default=None),
        x_wayfinder_sticky_cooldown: str | None = Header(default=None),
        x_wayfinder_debug: str | None = Header(default=None),
        x_wayfinder_failover: str | None = Header(default=None),
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        routing, gw = holder.current()

        def _reject(exc: BadOverride) -> JSONResponse:
            logger.info("request %s rejected: %s", request_id, exc)
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
                headers={"x-wayfinder-router-request-id": request_id},
            )

        # Resolve scope/latch overrides + any per-request scoring tuning before scoring.
        # The tuning field is popped so it is never forwarded to the upstream model.
        tuning = body.pop(TUNING_FIELD, None)
        try:
            route_on = parse_route_on_header(x_wayfinder_route_on) or gw.route_on
            sticky = resolve_sticky(x_wayfinder_sticky, gw.sticky)
            cooldown = resolve_sticky_cooldown(x_wayfinder_sticky_cooldown, gw.sticky_cooldown)
            routing = apply_scoring_overrides(routing, tuning)
        except BadOverride as exc:
            return _reject(exc)

        # Score once (always reported); a per-request override only changes which
        # endpoint the score routes to, never how it is computed (WF-ADR-0011). The
        # scoring time is the decision-latency metric (WF-ADR-0018).
        score_started = time.perf_counter()
        messages = body.get("messages")
        decision = score_complexity(extract_prompt(messages, route_on=route_on), config=routing)
        decision_seconds = time.perf_counter() - score_started

        pin = resolve_pin(body.get("model"), routing, gw)
        if pin is not None:
            chosen, mode = pin, "pinned"
        else:
            try:
                threshold = parse_threshold_header(x_wayfinder_threshold)
                if threshold is not None:
                    effective_tiers = threshold_tiers(routing, threshold)
                    chosen, mode = recommend_tier(decision.score, effective_tiers), "threshold-override"
                else:
                    effective_tiers = routing.tiers
                    chosen, mode = decision.recommendation, "scored"
                # Conversation latch (WF-ADR-0022): escalate to the highest tier any single
                # turn in this chat has needed, so a hard conversation stays on the big model.
                if sticky and routing.classifier is None and len(effective_tiers) >= 2:
                    latched = conversation_high_water(
                        messages, routing, effective_tiers, cooldown=cooldown
                    )
                    if latched is not None and _tier_rank(latched, effective_tiers) > _tier_rank(
                        chosen, effective_tiers
                    ):
                        chosen, mode = latched, "sticky"
            except BadOverride as exc:
                return _reject(exc)

        # Budget enforcement (WF-ROADMAP-0006): a spend cap on the realized cost in the
        # configured window. Only meaningful with real costs (``priced``); a relative-unit
        # demo has no dollars to cap. On breach we either degrade to the cheapest tier — the
        # failover ``degrade`` primitive (WF-ADR-0031), which never raises cost — or block
        # with 402. This changes only *delivery*; the scored decision above is untouched.
        budget_state: str | None = None
        budget = gw.budget
        if budget is not None and ledger.priced and ledger.spent(budget.window) >= budget.limit:
            if budget.on_breach == "block":
                logger.info(
                    "request %s blocked: %s budget of %s reached",
                    request_id, budget.window, budget.limit,
                )
                return JSONResponse(
                    status_code=402,
                    content={"error": {
                        "message": f"{budget.window} budget of {budget.limit} reached",
                        "type": "wayfinder_router_budget_exhausted",
                    }},
                    headers={
                        "x-wayfinder-router-request-id": request_id,
                        "x-wayfinder-router-budget": "blocked",
                    },
                )
            # degrade: route to the cheapest tier (lowest min_score). A no-op if the chosen
            # tier is already the cheapest, or in classifier mode (no tier ladder to descend).
            tiers_sorted = sorted(decision.tiers or (), key=lambda t: t.min_score)
            cheapest = tiers_sorted[0].model if tiers_sorted else None
            if cheapest is not None and cheapest != chosen:
                chosen, mode = cheapest, "budget-degraded"
            budget_state = "degraded"

        wf_headers = {
            "x-wayfinder-router-model": chosen,
            "x-wayfinder-router-score": f"{decision.score:.2f}",
            "x-wayfinder-router-mode": mode,
            "x-wayfinder-router-request-id": request_id,
        }
        if budget_state is not None:
            wf_headers["x-wayfinder-router-budget"] = budget_state
        logger.info(
            "request %s -> %s (score %.2f, mode %s)", request_id, chosen, decision.score, mode
        )
        entry = {  # mutable: cost fields (metadata only) are filled in after the upstream replies
            "request_id": request_id,
            "model": chosen,
            "score": round(decision.score, 2),
            "mode": mode,
            "ts": time.time(),
        }
        recent.append(entry)
        # Full prompt text, used only as a token-count fallback when the upstream omits `usage`.
        prompt_all = extract_prompt(messages, route_on="all")
        metrics.observe_decision(chosen, mode, decision_seconds)
        # Opt-in: surface the decision in the response so a client can show it
        # (default stays byte-clean for strict clients). The headers always carry it.
        debug = (x_wayfinder_debug or "").strip().lower() in ("1", "true", "yes")

        # The decision payload for the demo UI / debug clients. Built ONLY here and in the
        # debug/dry-run branches below — explain_score never runs on the scored relay path
        # (WF-ADR-0001/0020), so the default response stays byte-clean.
        def _cost_block() -> dict:
            wc = int(decision.features.get("word_count", 0))
            costs: dict[str, float] = {
                name: model.cost_per_1k
                for name, model in gw.models.items()
                if model.cost_per_1k is not None
            }
            if decision.tiers:
                for tier in decision.tiers:
                    if tier.cost is not None:
                        costs.setdefault(tier.model, tier.cost)
            estimated = not costs
            if estimated:
                # No cost metadata configured (e.g. a keyless dry-run demo): fall back to
                # the benchmark's relative units across the tier ladder (cheapest 0.2 ..
                # dearest 1.0) so the saved-vs-cloud story still renders. Clearly flagged.
                ladder = [t.model for t in (decision.tiers or ())] or [chosen]
                lo, hi = 0.2, 1.0
                step = (hi - lo) / max(1, len(ladder) - 1)
                costs = {m: round(lo + i * step, 3) for i, m in enumerate(ladder)}
            scale = wc / 1000.0
            chosen_per1k = costs.get(chosen, max(costs.values()))
            baseline_per1k = max(costs.values())  # always-route-to-the-dearest = "always-cloud"
            per_call = round(chosen_per1k * scale, 6)
            baseline = round(baseline_per1k * scale, 6)
            return {
                "per_call": per_call,
                "baseline": baseline,
                "saved": round(baseline - per_call, 6),
                "unit": "relative units / 1k words" if estimated else "$ / 1k words",
                "estimated": estimated,
                "word_count": wc,
            }

        def _explain_payload() -> dict:
            return {
                "model": chosen,
                "score": round(decision.score, 2),
                "mode": mode,
                "request_id": request_id,
                "features": dict(decision.features),
                "contributions": [fc.to_dict() for fc in explain_score(decision.features, routing.weights)],
                "tiers": (
                    [
                        {"min_score": t.min_score, "model": t.model}
                        | ({"cost": t.cost} if t.cost is not None else {})
                        for t in decision.tiers
                    ]
                    if decision.tiers
                    else None
                ),
                "cost": _cost_block(),
            }

        if dry_run:
            return JSONResponse(
                status_code=200,
                content={"wayfinder": {**_explain_payload(), "dry_run": True}},
                headers=wf_headers,
            )

        target = gw.models.get(chosen)
        if target is None:
            logger.error("request %s: no endpoint configured for model '%s'", request_id, chosen)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"no gateway endpoint configured for model '{chosen}'",
                        "type": "wayfinder_router_misconfigured",
                    }
                },
                headers=wf_headers,
            )
        # Delivery plan (WF-ADR-0031): the chosen tier's endpoint, its same-tier fallbacks,
        # then cross-tier candidates per the failover policy — minus any whose breaker is
        # open or that fail the pre-call check. The scored decision is unchanged either way.
        policy = (
            x_wayfinder_failover
            if x_wayfinder_failover in reliability.FAILOVER_POLICIES
            else gw.failover
        )
        ladder = [t.model for t in sorted(decision.tiers or (), key=lambda t: t.min_score)]
        candidates = [*target.fallbacks, *reliability.failover_candidates(chosen, ladder, policy)]
        prompt_estimate = pricing.estimate_tokens(prompt_all)

        def _precall_ok(name: str) -> bool:  # skip a target whose window can't fit the prompt
            model = gw.models.get(name)
            return model is None or reliability.precheck_ok(prompt_estimate, model.context_window)

        plan = reliability.delivery_plan(chosen, candidates, breaker, allow=_precall_ok)
        if not plan:  # chosen and every fallback are tripped, cooling down, or too small
            logger.warning("request %s: no available upstream for '%s'", request_id, chosen)
            return JSONResponse(
                status_code=503,
                content={"error": {
                    "message": f"no available upstream for '{chosen}' (cooling down or context too small)",
                    "type": "wayfinder_router_circuit_open",
                }},
                headers=wf_headers,
            )

        def _served_headers(served: str) -> dict[str, str]:
            out = {**wf_headers, "x-wayfinder-router-served-by": served}
            if served != chosen:
                out["x-wayfinder-router-failover"] = "true"
            return out

        if body.get("stream") is True:
            served = plan[0]  # breaker-aware target; streaming attempts once (WF-ADR-0031)
            smodel = gw.models[served]
            headers = _auth_headers(smodel)
            forward_body = {**body, "model": smodel.model}
            url = smodel.base_url.rstrip("/") + "/chat/completions"

            async def sse() -> AsyncIterator[bytes]:
                upstream_started = time.perf_counter()
                streamed: list[str] = []  # decoded chunks, to estimate completion tokens
                try:
                    async for chunk in aforward_stream(url, headers, forward_body, request_timeout):
                        yield chunk
                        streamed.append(chunk.decode("utf-8", "ignore"))
                    metrics.observe_upstream(served, time.perf_counter() - upstream_started)
                    breaker.record(served, True)
                    # No upstream `usage` over SSE by default, so estimate from the streamed text.
                    completion_text = "".join(parse_sse_deltas("".join(streamed).splitlines()))
                    _record_turn(entry, served, decision, gw, None, prompt_all, completion_text)
                    if debug:
                        meta = json.dumps(_explain_payload())
                        yield f"event: wayfinder\ndata: {meta}\n\n".encode()
                except UpstreamError as exc:
                    metrics.observe_upstream_error(served)
                    breaker.record(served, False)
                    logger.warning("request %s upstream stream error: %s", request_id, exc)
                    err = json.dumps(
                        {"error": {"message": str(exc), "type": "wayfinder_router_upstream_error"}}
                    )
                    yield f"data: {err}\n\n".encode()
                    yield b"data: [DONE]\n\n"

            return StreamingResponse(
                sse(), media_type="text/event-stream", headers=_served_headers(served)
            )

        served_by, status, content, content_type = await _deliver(plan, gw, body, request_id)
        if served_by is None:  # every endpoint failed
            return Response(
                content=content, status_code=status, media_type=content_type, headers=wf_headers
            )
        response_obj: object = None
        if content and "json" in content_type:
            try:
                response_obj = json.loads(content)
            except json.JSONDecodeError:
                response_obj = None
        if status < 400:  # record realized cost & savings, attributed to the target that served
            _record_turn(
                entry, served_by, decision, gw, response_obj,
                prompt_all, _first_choice_text(response_obj),
            )
        if debug and isinstance(response_obj, dict):
            response_obj["wayfinder"] = _explain_payload()
            content = json.dumps(response_obj).encode()
        return Response(
            content=content, status_code=status, media_type=content_type,
            headers=_served_headers(served_by),
        )

    return app


def run(  # pragma: no cover
    start_dir: str = ".",
    host: str = "127.0.0.1",
    port: int = 8088,
    *,
    dry_run: bool = False,
    timeout: float | None = None,
) -> None:
    """Serve the gateway with uvicorn (the `wayfinder-router serve` command)."""
    try:
        import uvicorn
    except ImportError as exc:
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    uvicorn.run(build_app(start_dir, dry_run=dry_run, timeout=timeout), host=host, port=port)
