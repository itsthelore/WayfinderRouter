"""Optional OpenAI-compatible routing gateway (WF-ADR-0004).

This is the impure layer: it holds bring-your-own keys and calls upstream models.
It ships behind the ``wayfinder-router[gateway]`` extra; ``fastapi`` / ``uvicorn`` /
``httpx`` are imported lazily so the deterministic core stays dependency-free.

A client points its OpenAI-compatible ``base_url`` at this gateway. For each request
the gateway scores the prompt with the pure core, maps the recommended model name to a
configured upstream, and forwards the call with the user's key. Keys are read from the
environment at request time and never appear in ``wayfinder-router.toml``, in the scored
path, or in any test fixture.

Streaming is first-class (WF-ADR-0013); the forward path is async so concurrent requests
do not block one another. Upstream transport failures surface as an OpenAI-shaped
``wayfinder_router_upstream_error`` rather than a bare 500, every response carries an
``x-wayfinder-router-request-id``, and the timeout is configurable
(``WAYFINDER_ROUTER_TIMEOUT``).

A request may steer the decision per call through OpenAI-compatible channels
(WF-ADR-0011): the ``model`` field is a routing directive (``auto`` scores, an endpoint
name pins, ``prefer-local`` / ``prefer-hosted`` pick a tier end), and an
``X-Wayfinder-Threshold`` header re-decides at a binary cut. These only move *which*
decision applies; they never add inference, so the WF-ADR-0001/0004 boundary holds.

The score is a *structural* proxy (length, headings, lists, code, links), not a verdict on
semantic difficulty: a short but hard prompt scores low. Calibrate the threshold on your own
traffic; the default is only a starting point.

Every response carries the decision signal (``x-wayfinder-router-model`` / ``-score`` /
``-mode`` / ``-request-id``); ``GET /router`` shows recent decisions and
``X-Wayfinder-Debug: true`` surfaces the decision in the body (WF-ADR-0014). ``GET /metrics``
exposes the same decisions as Prometheus counters/histograms — metadata only, never prompt
text (WF-ADR-0018). ``GET /v1/models`` advertises the selectable options (WF-ADR-0012).

Structure note (WF-ADR-0043 rebuild): the request pipeline's long-lived state lives in a
single ``_GatewayRuntime`` container built once in :func:`build_app`; the per-stage work
(delivery, cost accounting, decision explanation) is module-level free functions that take
that container, so the route handlers stay thin. The monkeypatch/import seams
(``forward_request``, ``aforward_request``, ``aforward_stream``, ``explain_score``,
``invoke_messages``, ``stream_messages``, ``parse_sse_deltas``, the pure decision helpers)
keep their exact names and are called by bare name through the module namespace so a
request-time ``setattr`` on this module takes effect.

Endpoints are declared under ``[gateway.models.<name>]`` in ``wayfinder-router.toml``. A
local, keyless endpoint needs only ``base_url`` and ``model``; a hosted one adds
``api_key_env``, the *name* of the environment variable that holds its key (never the key
itself)::

    [gateway.models.local]
    base_url = "http://localhost:11434/v1"
    model = "llama3.2"

    [gateway.models.cloud]
    base_url = "https://api.example.com/v1"
    model = "big-model"
    api_key_env = "EXAMPLE_API_KEY"
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import os
import threading
import time
import tomllib
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import anthropic_adapter, cache, pricing, ratelimit, reliability, vkeys
from .complexity import (
    ComplexityScore,
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

if TYPE_CHECKING:  # type-only; the runtime imports these lazily inside build_app
    from fastapi import FastAPI, Response

# Re-export the config error into this namespace (tests call gateway.WayfinderConfigError).
__all__ = ["WayfinderConfigError"]

logger = logging.getLogger("wayfinder_router.gateway")

_INSTALL_HINT = "the gateway needs its extra: pip install 'wayfinder-router[gateway]'"
_TIMEOUT_ENV = "WAYFINDER_ROUTER_TIMEOUT"
_FEEDBACK_TOKEN_ENV = "WAYFINDER_ROUTER_FEEDBACK_TOKEN"
_SAVINGS_FILE_ENV = "WAYFINDER_ROUTER_SAVINGS_FILE"  # persist the savings ledger here (WF-DESIGN-0007)
_SAVINGS_SAVE_INTERVAL = 5.0  # seconds; debounce best-effort disk snapshots
_DEFAULT_TIMEOUT = 60.0
_RECENT_MAX = 200  # routing decisions kept in memory for /router (metadata only)

# A tiny, self-contained "is routing working?" dashboard (WF-ADR-0014). No CDN, no build
# step, no prompt text — it polls /router/recent (decision metadata only).
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

# The decision-first chat demo (WF-ADR-0020). The markup is the canonical package-data file
# ``wayfinder_router/demo.html``; it is read once here so the page stays a single
# self-contained asset (no build, no CDN, system fonts only). Reading a data file via
# importlib.resources is allowed under the import-light rule (no heavy dep pulled in).
_DEMO_HTML = (importlib.resources.files("wayfinder_router") / "demo.html").read_text(
    encoding="utf-8"
)

# --- metrics (WF-ADR-0018) --------------------------------------------------
# Prometheus histogram bucket bounds, in seconds. Decision latency is a text scan with no
# model call, so its buckets are sub-millisecond; upstream latency spans a round-trip.
_DECISION_BUCKETS = (0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05)
_UPSTREAM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _new_hist(bounds: tuple[float, ...]) -> dict:
    return {"bounds": bounds, "counts": [0] * len(bounds), "sum": 0.0, "count": 0}


def _observe(hist: dict, value: float) -> None:
    # Cumulative-per-bound: a bound's count is every observation at or below it, so the
    # rendered buckets are already non-decreasing and +Inf equals the total.
    hist["sum"] += value
    hist["count"] += 1
    for i, bound in enumerate(hist["bounds"]):
        if value <= bound:
            hist["counts"][i] += 1


def _label_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_histogram(name: str, hist: dict, label_pairs: str = "") -> list[str]:
    """Bucket / sum / count exposition lines for one histogram (WF-ADR-0018).

    ``label_pairs`` (e.g. ``model="x"``) is threaded into every sample; the ``le`` label is
    appended after it. Buckets are cumulative, so the final ``+Inf`` bucket equals the count.
    """
    lead = f"{label_pairs}," if label_pairs else ""
    braces = f"{{{label_pairs}}}" if label_pairs else ""
    rows = [
        f'{name}_bucket{{{lead}le="{bound:g}"}} {count}'
        for bound, count in zip(hist["bounds"], hist["counts"], strict=True)
    ]
    rows.append(f'{name}_bucket{{{lead}le="+Inf"}} {hist["count"]}')
    rows.append(f"{name}_sum{braces} {hist['sum']:g}")
    rows.append(f"{name}_count{braces} {hist['count']}")
    return rows


class Metrics:
    """In-memory gateway metrics rendered in the Prometheus text format (WF-ADR-0018).

    Metadata only — ``model`` / ``mode`` / ``limit`` / ``key`` / ``version`` labels, never
    prompt text. A lock guards the counter dicts so a concurrent ``/metrics`` render cannot
    500 on a dict mutated mid-iteration; the exposition bytes are unchanged by it. Counters
    reset on restart, as Prometheus expects.
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
        self.cache_hits = 0  # exact-match cache hits (WF-ADR-0033)
        self.cache_misses = 0  # cacheable requests that missed
        self.cache_avoided_cost = 0.0  # cost a hit avoided (chosen-tier cost)
        self.rate_limited: dict[str, int] = {}  # 429s by tripped limit (WF-ADR-0034)
        self.key_requests: dict[str, int] = {}  # requests by virtual-key id (WF-ADR-0035)
        self._lock = threading.Lock()

    def set_model_costs(self, costs: dict[str, float]) -> None:
        """Record per-model cost metadata to surface as a gauge (informational)."""
        with self._lock:
            self.model_costs = dict(costs)

    def observe_cost(self, realized: float, baseline: float) -> None:
        """Accumulate realized spend and the always-frontier baseline (WF-DESIGN-0007)."""
        with self._lock:
            self.realized_cost = round(self.realized_cost + realized, 6)
            self.baseline_cost = round(self.baseline_cost + baseline, 6)

    def observe_decision(self, model: str, mode: str, seconds: float) -> None:
        with self._lock:
            key = (model, mode)
            self.requests[key] = self.requests.get(key, 0) + 1
            _observe(self.decision, seconds)

    def observe_upstream(self, model: str, seconds: float) -> None:
        with self._lock:
            hist = self.upstream.get(model)
            if hist is None:
                hist = self.upstream[model] = _new_hist(_UPSTREAM_BUCKETS)
            _observe(hist, seconds)

    def observe_upstream_error(self, model: str) -> None:
        with self._lock:
            self.upstream_errors[model] = self.upstream_errors.get(model, 0) + 1

    def observe_cache_hit(self, avoided_cost: float) -> None:
        """A cache hit served a stored answer; record the upstream cost it avoided."""
        with self._lock:
            self.cache_hits += 1
            self.cache_avoided_cost = round(self.cache_avoided_cost + max(0.0, avoided_cost), 6)

    def observe_cache_miss(self) -> None:
        with self._lock:
            self.cache_misses += 1

    def observe_rate_limited(self, limit: str) -> None:
        """A request was rejected with 429 by the ``rpm`` or ``tpm`` cap (WF-ADR-0034)."""
        with self._lock:
            self.rate_limited[limit] = self.rate_limited.get(limit, 0) + 1

    def observe_key_request(self, key_id: str) -> None:
        """An authenticated request was attributed to a virtual key (WF-ADR-0035)."""
        with self._lock:
            self.key_requests[key_id] = self.key_requests.get(key_id, 0) + 1

    def record_reload_failure(self) -> None:
        with self._lock:
            self.reload_failures += 1

    def render(self) -> str:
        with self._lock:
            return self._render_locked()

    def _render_locked(self) -> str:
        out: list[str] = []
        esc = _label_escape

        def family(name: str, help_text: str, kind: str, samples: Iterable[str] = ()) -> None:
            # One metric family: its HELP/TYPE header followed by zero or more sample lines.
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} {kind}")
            out.extend(samples)

        family(
            "wayfinder_router_build_info",
            "Build information.",
            "gauge",
            [f'wayfinder_router_build_info{{version="{esc(self.version)}"}} 1'],
        )
        family(
            "wayfinder_router_requests_total",
            "Routed requests by model and mode.",
            "counter",
            [
                f'wayfinder_router_requests_total{{model="{esc(model)}",mode="{esc(mode)}"}} {n}'
                for (model, mode), n in sorted(self.requests.items())
            ],
        )
        family(
            "wayfinder_router_upstream_errors_total",
            "Upstream transport failures by model.",
            "counter",
            [
                f'wayfinder_router_upstream_errors_total{{model="{esc(model)}"}} {n}'
                for model, n in sorted(self.upstream_errors.items())
            ],
        )
        family(
            "wayfinder_router_cache_hits_total",
            "Exact-match response cache hits (WF-ADR-0033).",
            "counter",
            [f"wayfinder_router_cache_hits_total {self.cache_hits}"],
        )
        family(
            "wayfinder_router_cache_misses_total",
            "Cacheable requests that missed the cache.",
            "counter",
            [f"wayfinder_router_cache_misses_total {self.cache_misses}"],
        )
        family(
            "wayfinder_router_cache_avoided_cost_total",
            "Upstream cost avoided by cache hits "
            "(chosen-tier cost; distinct from routing savings vs always-frontier).",
            "counter",
            [f"wayfinder_router_cache_avoided_cost_total {self.cache_avoided_cost:g}"],
        )
        family(
            "wayfinder_router_rate_limited_total",
            "Requests rejected with 429 by limit (WF-ADR-0034).",
            "counter",
            [
                f'wayfinder_router_rate_limited_total{{limit="{esc(limit)}"}} {n}'
                for limit, n in sorted(self.rate_limited.items())
            ],
        )
        if self.key_requests:  # only surfaces once a virtual key has been attributed
            family(
                "wayfinder_router_key_requests_total",
                "Requests by virtual-key id (WF-ADR-0035).",
                "counter",
                [
                    f'wayfinder_router_key_requests_total{{key="{esc(key_id)}"}} {n}'
                    for key_id, n in sorted(self.key_requests.items())
                ],
            )
        family(
            "wayfinder_router_config_reload_failures_total",
            "Config reloads that failed and kept the last-good config.",
            "counter",
            [f"wayfinder_router_config_reload_failures_total {self.reload_failures}"],
        )
        if self.model_costs:  # only when at least one model declares a cost
            family(
                "wayfinder_router_model_cost_per_1k",
                "Configured per-1k-token cost by model (informational, WF-ADR-0017).",
                "gauge",
                [
                    f'wayfinder_router_model_cost_per_1k{{model="{esc(model)}"}} {cost:g}'
                    for model, cost in sorted(self.model_costs.items())
                ],
            )
        family(
            "wayfinder_router_realized_cost_total",
            "Cumulative realized spend on the chosen tier "
            "(USD, or relative units when no cost_per_1k is configured; WF-DESIGN-0007).",
            "counter",
            [f"wayfinder_router_realized_cost_total {self.realized_cost:g}"],
        )
        family(
            "wayfinder_router_baseline_cost_total",
            "Cumulative cost had every request gone to the dearest tier "
            "(the always-frontier counterfactual).",
            "counter",
            [f"wayfinder_router_baseline_cost_total {self.baseline_cost:g}"],
        )
        savings = round(self.baseline_cost - self.realized_cost, 6)
        family(
            "wayfinder_router_savings_cost_total",
            "Cumulative savings vs always-frontier (baseline minus realized).",
            "counter",
            [f"wayfinder_router_savings_cost_total {savings:g}"],
        )
        family(
            "wayfinder_router_decision_latency_seconds",
            "Time to score a prompt and pick a model (no model call).",
            "histogram",
            _render_histogram("wayfinder_router_decision_latency_seconds", self.decision),
        )
        family(
            "wayfinder_router_upstream_latency_seconds",
            "Upstream model round-trip time by model.",
            "histogram",
        )
        for model, hist in sorted(self.upstream.items()):
            out += _render_histogram(
                "wayfinder_router_upstream_latency_seconds", hist, f'model="{esc(model)}"'
            )
        return "\n".join(out) + "\n"


class GatewayUnavailable(Exception):
    """The gateway extra (fastapi / uvicorn / httpx) is not installed."""


class UpstreamError(Exception):
    """An upstream call failed at the transport level (timeout, connection)."""


class BadOverride(Exception):
    """A per-request override was supplied but is malformed or not applicable."""


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

    On breach the gateway degrades to the cheapest tier (never raising cost) or blocks the
    request with HTTP 402. Enforced only when the price table is real (``priced``): a
    relative-unit demo has no dollars to cap. The cap changes *delivery*, not the decision.
    """

    limit: float  # spend ceiling in the ledger's unit, over ``window``
    window: str = "day"  # "day" | "month" | "all"
    on_breach: str = "degrade"  # "degrade" (to cheapest tier) | "block" (402)


@dataclass(frozen=True)
class CacheConfig:
    """Exact-match response cache settings (WF-ADR-0033); OFF by default.

    Bounded by an LRU entry count, a byte ceiling, and a TTL. It never changes the scored
    decision — it only replays a stored answer for an identical, deterministic request.
    """

    enabled: bool = False
    ttl: float = cache.DEFAULT_TTL  # seconds an entry is served before it is stale (0 = no expiry)
    max_entries: int = cache.DEFAULT_MAX_ENTRIES  # LRU bound on the number of cached responses
    max_bytes: int = cache.DEFAULT_MAX_BYTES  # hard memory ceiling for cached bodies


@dataclass(frozen=True)
class RateLimit:
    """Rate-limit settings (WF-ADR-0034); caps rpm and/or tpm over a fixed window (429 on breach).

    At least one of ``rpm`` / ``tpm`` is set when the block is present.
    """

    rpm: int | None = None
    tpm: int | None = None
    window: float = ratelimit.DEFAULT_WINDOW  # seconds in a window (default 60)


@dataclass(frozen=True)
class VirtualKey:
    """A gateway-issued credential (WF-ADR-0035): a stored hash plus optional scope/attribution.

    ``hash`` is the SHA-256 hex of the key (the plaintext is never stored). ``budget`` /
    ``rate_limit``, when set, apply that key's own cap on top of any gateway-wide one (the
    stricter wins). ``models`` is an optional allowlist (empty = unrestricted). Virtual keys
    gate the *gateway*; provider keys still come from the environment (WF-ADR-0004).
    """

    hash: str
    tags: tuple[str, ...] = ()
    budget: Budget | None = None
    rate_limit: RateLimit | None = None
    models: tuple[str, ...] = ()  # allowlist of permitted model names; empty = any


@dataclass(frozen=True)
class GatewayConfig:
    """Maps recommended model names to upstream endpoints (from `[gateway.models]`).

    ``route_on`` selects which part of a multi-turn chat the router scores (WF-ADR-0021).
    ``sticky`` latches a conversation to the highest tier any turn needed (WF-ADR-0022);
    ``sticky_cooldown`` is the number of calm turns after which the latch decays (``0`` =
    never). Field order matters: :func:`gateway_config_from_toml` constructs positionally.
    """

    models: dict[str, GatewayModel] = field(default_factory=dict)
    route_on: str = "turn"
    sticky: bool = False
    sticky_cooldown: int = 0
    # In-message routing override (WF-ADR-0036): a recognized "/directive" at the start of the
    # latest user message pins the route. Off by default.
    slash_directives: bool = False
    # Reliability (WF-ADR-0031): bounded retries, per-target circuit breaker, cross-tier
    # failover policy ("same-tier" default / "degrade" cheaper / "escalate" dearer).
    retries: int = 2
    breaker_threshold: int = 5
    breaker_cooldown: float = 30.0
    failover: str = "same-tier"
    # Offline-first (WF-ADR-0039): deliver to the cheapest/local tier, skip dearer tiers; the
    # scored decision is unchanged. Off by default; also settable via X-Wayfinder-Offline.
    offline: bool = False
    # Optional spend cap / response cache / rate limit / virtual keys. ``None`` / empty = off.
    budget: Budget | None = None
    cache: CacheConfig | None = None
    rate_limit: RateLimit | None = None
    keys: dict[str, VirtualKey] = field(default_factory=dict)


# Which chat-message text the router scores (WF-ADR-0001); this only chooses that string so a
# multi-turn chat does not drift toward cloud as the transcript grows.
ROUTE_ON_SCOPES = ("turn", "last_user", "user", "all")

# Budget windows and breach behaviours (WF-ROADMAP-0006).
BUDGET_WINDOWS = ("day", "month", "all")
BUDGET_BREACH = ("degrade", "block")


# --- config loading / validation / dumping ----------------------------------

# TOML scalars arrive as a small set of shapes, so the table validators below lean on a
# handful of reusable guards. Python treats ``bool`` as an ``int`` subtype; every numeric
# guard excludes it up front so a bare ``true`` is never accepted as the number 1.
def _is_positive_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0


def _is_nonnegative_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0


def _is_positive_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _is_nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(_nonempty_str(item) for item in value)


def _as_str(value: object) -> str:
    # Narrowing shim for the type checker: callers validate the shape first.
    assert isinstance(value, str)
    return value


def _as_number(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


def _fail(where: str, detail: str) -> WayfinderConfigError:
    """Build a location-prefixed config error (``<file>: <detail>``)."""
    return WayfinderConfigError(f"{where}: {detail}")


def load_gateway_config(start_dir: str = ".") -> GatewayConfig:
    """Locate and parse the gateway config, searching upward from ``start_dir``.

    A missing file yields the empty default; an unreadable one surfaces as a config error.
    """
    path = find_config_file(start_dir)
    if path is None:
        return GatewayConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {path}: {exc}") from exc
    return gateway_config_from_toml(text, where=str(path))


def _budget_from_toml(raw: object, where: str) -> Budget | None:
    """Validate a spend-cap table (WF-ROADMAP-0006); serves both gateway-wide and per-key use."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail(where, "'[gateway.budget]' must be a table")
    limit = raw.get("limit")
    if not _is_positive_number(limit):
        raise _fail(where, "'gateway.budget.limit' must be a positive number")
    window = raw.get("window", "day")
    if window not in BUDGET_WINDOWS:
        raise _fail(where, f"'gateway.budget.window' must be one of {', '.join(BUDGET_WINDOWS)}")
    on_breach = raw.get("on_breach", "degrade")
    if on_breach not in BUDGET_BREACH:
        raise _fail(where, f"'gateway.budget.on_breach' must be one of {', '.join(BUDGET_BREACH)}")
    return Budget(limit=_as_number(limit), window=window, on_breach=on_breach)


def _cache_from_toml(raw: object, where: str) -> CacheConfig | None:
    """Validate the response-cache table (WF-ADR-0033); absent leaves the cache off."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail(where, "'[gateway.cache]' must be a table")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _fail(where, "'gateway.cache.enabled' must be a boolean")
    ttl = raw.get("ttl", cache.DEFAULT_TTL)
    if not _is_nonnegative_number(ttl):
        raise _fail(where, "'gateway.cache.ttl' must be a non-negative number")
    max_entries = raw.get("max_entries", cache.DEFAULT_MAX_ENTRIES)
    if not _is_positive_int(max_entries):
        raise _fail(where, "'gateway.cache.max_entries' must be a positive integer")
    max_bytes = raw.get("max_bytes", cache.DEFAULT_MAX_BYTES)
    if not _is_positive_int(max_bytes):
        raise _fail(where, "'gateway.cache.max_bytes' must be a positive integer")
    return CacheConfig(
        enabled=enabled, ttl=float(ttl), max_entries=max_entries, max_bytes=max_bytes
    )


def _rate_limit_from_toml(raw: object, where: str) -> RateLimit | None:
    """Validate a rate-limit table (WF-ADR-0034); shared by gateway-wide and per-key limits."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail(where, "'[gateway.rate_limit]' must be a table")
    table = raw

    def _optional_rate(field: str) -> int | None:
        value = table.get(field)
        if value is None:
            return None
        if not _is_positive_int(value):
            raise _fail(where, f"'gateway.rate_limit.{field}' must be a positive integer")
        return value

    rpm = _optional_rate("rpm")
    tpm = _optional_rate("tpm")
    if rpm is None and tpm is None:
        raise _fail(where, "'[gateway.rate_limit]' must set 'rpm' and/or 'tpm'")
    window = raw.get("window", ratelimit.DEFAULT_WINDOW)
    if not _is_positive_number(window):
        raise _fail(where, "'gateway.rate_limit.window' must be a positive number")
    return RateLimit(rpm=rpm, tpm=tpm, window=float(window))


def _is_sha256_hex(value: object) -> bool:
    """True only for a 64-character hex string — the shape of a SHA-256 digest."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value)


def _keys_from_toml(raw: object, where: str) -> dict[str, VirtualKey]:
    """Parse and validate ``[gateway.keys.<id>]`` tables (WF-ADR-0035).

    Each key stores a SHA-256 ``hash`` (never plaintext) and may carry ``tags`` plus its own
    nested ``budget`` / ``rate_limit`` (validated by the gateway-wide helpers).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _fail(where, "'[gateway.keys]' must be a table")
    parsed: dict[str, VirtualKey] = {}
    for kid, entry in raw.items():
        prefix = f"gateway.keys.{kid}"
        if not isinstance(entry, dict):
            raise _fail(where, f"'[{prefix}]' must be a table")
        digest = entry.get("hash")
        if not _is_sha256_hex(digest):
            raise _fail(
                where,
                f"'{prefix}.hash' must be a 64-char SHA-256 hex digest "
                "(mint a key with `wayfinder-router keys new`)",
            )
        tags = entry.get("tags", [])
        if not _is_string_list(tags):
            raise _fail(where, f"'{prefix}.tags' must be a list of strings")
        allowlist = entry.get("models", [])
        if not _is_string_list(allowlist):
            raise _fail(where, f"'{prefix}.models' must be a list of model names")
        scope = f"{where} [gateway.keys.{kid}]"
        parsed[kid] = VirtualKey(
            hash=str(digest).lower(),  # already validated as a 64-char hex string
            tags=tuple(tags),
            budget=_budget_from_toml(entry.get("budget"), scope),
            rate_limit=_rate_limit_from_toml(entry.get("rate_limit"), scope),
            models=tuple(allowlist),
        )
    return parsed


def _model_from_toml(name: str, entry: object, where: str) -> GatewayModel:
    """Validate one ``[gateway.models.<name>]`` endpoint table into a :class:`GatewayModel`."""
    prefix = f"gateway.models.{name}"
    if not isinstance(entry, dict):
        raise _fail(where, f"'[{prefix}]' must be a table")
    base_url = entry.get("base_url")
    if not _nonempty_str(base_url):
        raise _fail(where, f"'{prefix}.base_url' must be a string")
    model = entry.get("model")
    if not _nonempty_str(model):
        raise _fail(where, f"'{prefix}.model' must be a string")
    api_key_env = entry.get("api_key_env")
    if api_key_env is not None and not _nonempty_str(api_key_env):
        raise _fail(where, f"'{prefix}.api_key_env' must be a non-empty string")
    api_key_cmd = entry.get("api_key_cmd")
    if api_key_cmd is not None and not _nonempty_str(api_key_cmd):
        raise _fail(where, f"'{prefix}.api_key_cmd' must be a non-empty string")
    if api_key_cmd is not None and api_key_env is None:
        # api_key_cmd only produces a value; api_key_env names where that value lands.
        raise _fail(
            where,
            f"'{prefix}.api_key_cmd' needs 'api_key_env' to name the variable it fills",
        )
    cost_per_1k = entry.get("cost_per_1k")
    if cost_per_1k is not None and not _is_nonnegative_number(cost_per_1k):
        raise _fail(where, f"'{prefix}.cost_per_1k' must be a non-negative number")
    fallbacks = entry.get("fallbacks", [])
    if not _is_string_list(fallbacks):
        raise _fail(where, f"'{prefix}.fallbacks' must be a list of model names")
    context_window = entry.get("context_window")
    if context_window is not None and not _is_positive_int(context_window):
        raise _fail(where, f"'{prefix}.context_window' must be a positive integer")
    return GatewayModel(
        base_url=_as_str(base_url),
        model=_as_str(model),
        api_key_env=api_key_env,
        api_key_cmd=api_key_cmd,
        cost_per_1k=float(cost_per_1k) if cost_per_1k is not None else None,
        fallbacks=tuple(fallbacks),
        context_window=context_window,
    )


def gateway_config_from_toml(text: str, where: str = "wayfinder-router.toml") -> GatewayConfig:
    """Parse a :class:`GatewayConfig` straight from TOML text, no filesystem access."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WayfinderConfigError(f"{where}: invalid TOML: {exc}") from exc
    section = data.get("gateway")
    if section is None:
        return GatewayConfig()
    if not isinstance(section, dict):
        raise _fail(where, "'[gateway]' must be a table")

    def _enum(name: str, default: str, allowed: tuple[str, ...]) -> str:
        chosen = section.get(name, default)
        if chosen not in allowed:
            raise _fail(where, f"'gateway.{name}' must be one of {', '.join(allowed)}")
        return chosen

    def _flag(name: str) -> bool:
        chosen = section.get(name, False)
        if not isinstance(chosen, bool):
            raise _fail(where, f"'gateway.{name}' must be a boolean")
        return chosen

    def _int(name: str, default: int, *, positive: bool) -> int:
        chosen = section.get(name, default)
        if positive and not _is_positive_int(chosen):
            raise _fail(where, f"'gateway.{name}' must be a positive integer")
        if not positive and not _is_nonnegative_int(chosen):
            raise _fail(where, f"'gateway.{name}' must be a non-negative integer")
        return chosen

    route_on = _enum("route_on", "turn", ROUTE_ON_SCOPES)
    sticky = _flag("sticky")
    cooldown = _int("sticky_cooldown", 0, positive=False)
    slash_directives = _flag("slash_directives")
    offline = _flag("offline")
    retries = _int("retries", 2, positive=False)
    breaker_threshold = _int("breaker_threshold", 5, positive=True)
    breaker_cooldown = section.get("breaker_cooldown", 30.0)
    if not _is_nonnegative_number(breaker_cooldown):
        raise _fail(where, "'gateway.breaker_cooldown' must be a non-negative number")
    failover = _enum("failover", "same-tier", reliability.FAILOVER_POLICIES)

    budget = _budget_from_toml(section.get("budget"), where)
    cache_cfg = _cache_from_toml(section.get("cache"), where)
    rate_limit = _rate_limit_from_toml(section.get("rate_limit"), where)
    keys = _keys_from_toml(section.get("keys"), where)

    raw_models = section.get("models") or {}
    if not isinstance(raw_models, dict):
        raise _fail(where, "'[gateway.models]' must be a table")
    models = {name: _model_from_toml(name, entry, where) for name, entry in raw_models.items()}

    # Cross-reference checks: every fallback and key allowlist must name a defined endpoint.
    for name, gm in models.items():
        for fb in gm.fallbacks:
            if fb == name:
                raise _fail(where, f"'gateway.models.{name}.fallbacks' cannot include itself")
            if fb not in models:
                raise _fail(where, f"'gateway.models.{name}.fallbacks' names unknown model '{fb}'")
    for kid, vk in keys.items():
        for allowed_model in vk.models:
            if allowed_model not in models:
                raise _fail(
                    where, f"'gateway.keys.{kid}.models' names unknown model '{allowed_model}'"
                )

    return GatewayConfig(
        models=models,
        route_on=route_on,
        sticky=sticky,
        sticky_cooldown=cooldown,
        slash_directives=slash_directives,
        offline=offline,
        retries=retries,
        breaker_threshold=breaker_threshold,
        breaker_cooldown=breaker_cooldown,
        failover=failover,
        budget=budget,
        cache=cache_cfg,
        rate_limit=rate_limit,
        keys=keys,
    )


def _emit_budget(header: str, b: Budget) -> str:
    """Render a budget sub-table under ``header`` (defaults omitted)."""
    lines = [f"[{header}]", f"limit = {round(b.limit, 6)!r}"]
    if b.window != "day":
        lines.append(f'window = "{b.window}"')
    if b.on_breach != "degrade":
        lines.append(f'on_breach = "{b.on_breach}"')
    return "\n".join(lines)


def _emit_rate_limit(header: str, rl: RateLimit) -> str:
    """Render a rate-limit sub-table under ``header`` (only the set caps appear)."""
    lines = [f"[{header}]"]
    if rl.rpm is not None:
        lines.append(f"rpm = {rl.rpm}")
    if rl.tpm is not None:
        lines.append(f"tpm = {rl.tpm}")
    if rl.window != ratelimit.DEFAULT_WINDOW:
        lines.append(f"window = {round(rl.window, 6)!r}")
    return "\n".join(lines)


def dump_gateway_toml(gateway: GatewayConfig) -> str:
    """Render a :class:`GatewayConfig` as ``[gateway.*]`` TOML, omitting fields left at default.

    Recalibration uses this to keep the endpoint map intact while it rewrites the routing
    section. Only the env-var *name* (``api_key_env``) and the *reference* that fills it
    (``api_key_cmd``) are written — never the secret. The result round-trips back through
    :func:`gateway_config_from_toml`.
    """
    blocks: list[str] = []

    # [gateway]: emit only when at least one scalar strays from its default.
    head = ["[gateway]"]
    if gateway.route_on != "turn":
        head.append(f'route_on = "{gateway.route_on}"')
    if gateway.sticky:
        head.append("sticky = true")
    if gateway.sticky_cooldown:
        head.append(f"sticky_cooldown = {gateway.sticky_cooldown}")
    if gateway.slash_directives:
        head.append("slash_directives = true")
    if gateway.offline:
        head.append("offline = true")
    if gateway.retries != 2:
        head.append(f"retries = {gateway.retries}")
    if gateway.breaker_threshold != 5:
        head.append(f"breaker_threshold = {gateway.breaker_threshold}")
    if gateway.breaker_cooldown != 30.0:
        head.append(f"breaker_cooldown = {round(gateway.breaker_cooldown, 6)!r}")
    if gateway.failover != "same-tier":
        head.append(f'failover = "{gateway.failover}"')
    if len(head) > 1:
        blocks.append("\n".join(head))

    if gateway.budget is not None:
        blocks.append(_emit_budget("gateway.budget", gateway.budget))
    if gateway.cache is not None:
        c = gateway.cache
        cache_lines = ["[gateway.cache]", f"enabled = {str(c.enabled).lower()}"]
        if c.ttl != cache.DEFAULT_TTL:
            cache_lines.append(f"ttl = {round(c.ttl, 6)!r}")
        if c.max_entries != cache.DEFAULT_MAX_ENTRIES:
            cache_lines.append(f"max_entries = {c.max_entries}")
        if c.max_bytes != cache.DEFAULT_MAX_BYTES:
            cache_lines.append(f"max_bytes = {c.max_bytes}")
        blocks.append("\n".join(cache_lines))
    if gateway.rate_limit is not None:
        blocks.append(_emit_rate_limit("gateway.rate_limit", gateway.rate_limit))

    for kid, vk in gateway.keys.items():
        key_lines = [f"[gateway.keys.{kid}]", f'hash = "{vk.hash}"']
        if vk.tags:
            key_lines.append("tags = [" + ", ".join(f'"{t}"' for t in vk.tags) + "]")
        if vk.models:
            key_lines.append("models = [" + ", ".join(f'"{m}"' for m in vk.models) + "]")
        blocks.append("\n".join(key_lines))
        if vk.budget is not None:
            blocks.append(_emit_budget(f"gateway.keys.{kid}.budget", vk.budget))
        if vk.rate_limit is not None:
            blocks.append(_emit_rate_limit(f"gateway.keys.{kid}.rate_limit", vk.rate_limit))

    for name, model in gateway.models.items():
        model_lines = [
            f"[gateway.models.{name}]",
            f'base_url = "{model.base_url}"',
            f'model = "{model.model}"',
        ]
        if model.api_key_env:
            model_lines.append(f'api_key_env = "{model.api_key_env}"')
        if model.api_key_cmd:  # a reference that fills the env var — safe to persist
            model_lines.append(f'api_key_cmd = "{model.api_key_cmd}"')
        if model.cost_per_1k is not None:
            model_lines.append(f"cost_per_1k = {round(model.cost_per_1k, 6)!r}")
        if model.fallbacks:
            joined = ", ".join(f'"{f}"' for f in model.fallbacks)
            model_lines.append(f"fallbacks = [{joined}]")
        if model.context_window is not None:
            model_lines.append(f"context_window = {model.context_window}")
        blocks.append("\n".join(model_lines))
    return "\n\n".join(blocks)


# --- pure decision / scoping helpers (also imported by tui / cli / ui / recalibrate) ---


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

    ``route_on`` selects the scope (WF-ADR-0021) so a multi-turn chat does not drift toward
    cloud as its transcript grows: ``"turn"`` (default) = the system message(s) plus the
    latest user message; ``"last_user"`` = the latest user message only; ``"user"`` = every
    user message; ``"all"`` = every message (legacy, ratchets upward).

    Falls back to the last message when role-filtering finds nothing (except ``"all"``), so
    the router never scores an empty string. Non-list input returns ``""``. Handles string
    and array-of-parts content.
    """
    if not isinstance(messages, list):
        return ""
    turns = [m for m in messages if isinstance(m, dict)]

    def _latest_user() -> list[dict]:
        found = next((m for m in reversed(turns) if m.get("role") == "user"), None)
        return [found] if found is not None else []

    if route_on == "all":
        picked: list[dict] = turns
    elif route_on == "user":
        picked = [m for m in turns if m.get("role") == "user"]
    elif route_on == "last_user":
        picked = _latest_user()
    else:  # "turn" (default): any standing system context plus the newest user ask
        picked = [m for m in turns if m.get("role") == "system"] + _latest_user()

    if not picked and turns and route_on != "all":  # never hand the scorer an empty string
        picked = [turns[-1]]

    rendered = (_message_text(m) for m in picked)
    return "\n".join(text for text in rendered if text is not None)


# Per-request override transport (WF-ADR-0011). These are pure and offline: they only move
# which threshold/decision applies, never invoke a model.
THRESHOLD_HEADER = "x-wayfinder-threshold"
_AUTO = "auto"  # the OpenAI `model` sentinel meaning "Wayfinder decides"
_PREFER_LOW = "prefer-local"
_PREFER_HIGH = "prefer-hosted"  # canonical high-end directive (v0.1.3+)
_PREFER_HIGH_ALIASES = ("prefer-cloud",)  # back-compat: shipped in v0.1.2, still resolves


def resolve_pin(model_field: object, routing: RoutingConfig, gateway: GatewayConfig) -> str | None:
    """Resolve an explicit endpoint pin from the OpenAI ``model`` field, or ``None``.

    ``auto`` / empty / any unrecognized string returns ``None`` (ordinary OpenAI ids pass
    through to scoring). ``prefer-local`` / ``prefer-hosted`` (and the ``prefer-cloud`` alias)
    resolve to the low / high end of a *tiered/binary* router; under a classifier they fall
    through to scoring. A configured endpoint name pins to that endpoint.
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


def resolve_slash_directive(
    messages: object, routing: RoutingConfig, gateway: GatewayConfig
) -> tuple[str | None, list | None]:
    """Detect a ``/directive`` at the start of the latest string-content user message (WF-ADR-0036).

    The token after the slash must be recognized (a configured endpoint, ``prefer-local`` /
    ``prefer-hosted``, or ``auto`` to force scoring); anything else (a path, a UI's ``/help``,
    code) is left untouched. Returns ``(pin, cleaned_messages)``: ``pin`` is the resolved
    endpoint (``None`` for ``/auto`` or no match), ``cleaned_messages`` is a copy with the
    directive stripped (``None`` when nothing was recognized). Pure; no model call.
    """
    if not isinstance(messages, list):
        return None, None
    idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):  # newest first
        entry = messages[i]
        if (
            isinstance(entry, dict)
            and entry.get("role") == "user"
            and isinstance(entry.get("content"), str)
        ):
            idx = i
            break
    if idx is None:
        return None, None
    head = messages[idx]["content"].lstrip()
    if not head.startswith("/"):
        return None, None
    parts = head[1:].split(None, 1)  # the directive ends at the first run of whitespace
    if not parts:
        return None, None
    token = parts[0]
    remainder = parts[1] if len(parts) > 1 else ""
    if token == _AUTO:
        pin: str | None = None  # /auto forces scoring but is still a recognized directive
    else:
        pin = resolve_pin(token, routing, gateway)
        if pin is None:  # unrecognized leading token — leave the transcript untouched
            return None, None
    rewritten = list(messages)
    rewritten[idx] = {**messages[idx], "content": remainder}
    return pin, rewritten


def parse_threshold_header(value: str | None) -> float | None:
    """Parse ``X-Wayfinder-Threshold`` into a ``0.0``–``1.0`` cut, or ``None``.

    Raises :class:`BadOverride` when present but not a number in range.
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

    Only well-defined for a binary (two-tier) router; a classifier or multi-tier router has
    no single cut to move, so this raises :class:`BadOverride`.
    """
    if routing.classifier is not None or len(routing.tiers) != 2:
        raise BadOverride(
            f"{THRESHOLD_HEADER} applies only to a binary (two-tier) router; this "
            "gateway is configured for classifier or multi-tier routing"
        )
    return (Tier(0.0, routing.tiers[0].model), Tier(threshold, routing.tiers[1].model))


# Two more per-request overrides (WF-ADR-0011): move the routing scope / latch for one request
# without touching server config. Still pure and offline.
ROUTE_ON_HEADER = "x-wayfinder-route-on"
STICKY_HEADER = "x-wayfinder-sticky"
STICKY_COOLDOWN_HEADER = "x-wayfinder-sticky-cooldown"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def parse_route_on_header(value: str | None) -> str | None:
    """Read ``X-Wayfinder-Route-On`` as a routing scope; ``None`` when the header is absent.

    An unrecognized scope is a :class:`BadOverride`.
    """
    if not (value and value.strip()):
        return None
    scope = value.strip().lower()
    if scope not in ROUTE_ON_SCOPES:
        raise BadOverride(
            f"{ROUTE_ON_HEADER} must be one of {', '.join(ROUTE_ON_SCOPES)}, got {value!r}"
        )
    return scope


def resolve_sticky(value: str | None, default: bool) -> bool:
    """Read the latch flag from ``X-Wayfinder-Sticky``, or fall back to the config default."""
    if not (value and value.strip()):
        return default
    token = value.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise BadOverride(f"{STICKY_HEADER} must be true or false, got {value!r}")


def resolve_sticky_cooldown(value: str | None, default: int) -> int:
    """Resolve the latch cool-down (calm turns to release) from the header, else the default.

    ``0`` means the latch never decays. Raises :class:`BadOverride` for a non-integer or
    negative value.
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


# In-demo scoring overrides (WF-ADR-0023): a request-body field that tunes the scoring
# *function* (feature weights + lexicon terms) for this request only. The scorer stays pure —
# the gateway only chooses the config it hands over. Opt-in, additive, never forwarded upstream.
TUNING_FIELD = "wayfinder_tuning"
_MAX_LEXICON_TERMS = 2000


def apply_scoring_overrides(routing: RoutingConfig, override: object) -> RoutingConfig:
    """Apply per-request scoring tuning (WF-ADR-0023), returning a fresh ``RoutingConfig``.

    ``override`` is the request body's ``wayfinder_tuning`` field. A ``weights`` map is merged
    over the configured feature weights; a ``lexicon`` replaces the ``reasoning_terms`` /
    ``constraint_terms`` sets. Absent input returns ``routing`` untouched (the configured
    weights and lexicon are never mutated in place). Malformed input raises :class:`BadOverride`.
    """
    if override is None:
        return routing
    if not isinstance(override, dict):
        raise BadOverride(f"{TUNING_FIELD} must be an object")

    weights = dict(routing.weights)
    weight_overrides = override.get("weights")
    if weight_overrides is not None:
        if not isinstance(weight_overrides, dict):
            raise BadOverride(f"{TUNING_FIELD}.weights must be an object")
        for name, value in weight_overrides.items():
            if name not in weights:
                raise BadOverride(f"{TUNING_FIELD}.weights: unknown feature {name!r}")
            if not _is_nonnegative_number(value):
                raise BadOverride(f"{TUNING_FIELD}.weights.{name} must be a non-negative number")
            weights[name] = float(value)

    lexicon = routing.lexicon
    lexicon_override = override.get("lexicon")
    if lexicon_override is not None:
        if not isinstance(lexicon_override, dict):
            raise BadOverride(f"{TUNING_FIELD}.lexicon must be an object")
        replacements: dict[str, frozenset[str]] = {}
        for key in ("reasoning_terms", "constraint_terms"):
            if key not in lexicon_override:
                continue
            terms = lexicon_override[key]
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} must be a list of strings")
            normalized = frozenset(t.strip().lower() for t in terms if t.strip())
            if len(normalized) > _MAX_LEXICON_TERMS:
                raise BadOverride(f"{TUNING_FIELD}.lexicon.{key} exceeds {_MAX_LEXICON_TERMS} terms")
            replacements[key] = normalized
        if replacements:
            lexicon = replace(routing.lexicon, **replacements)
    return replace(routing, weights=weights, lexicon=lexicon)


def _tier_rank(model: str, tiers: tuple[Tier, ...]) -> int:
    """Position of ``model`` in the tier ladder, or ``-1`` when it names no tier."""
    return next((i for i, tier in enumerate(tiers) if tier.model == model), -1)


def conversation_high_water(
    messages: object, routing: RoutingConfig, tiers: tuple[Tier, ...], *, cooldown: int = 0
) -> str | None:
    """The tier the conversation latches to (WF-ADR-0022) — a *max over turns*, not a sum.

    Each user turn is scored on its own (with standing system context), so the latch does not
    inflate with conversation length. ``cooldown == 0`` is monotonic (never steps down);
    ``cooldown == N`` (N >= 1) decays: after N consecutive turns below the latch, it steps
    down to that lower tier. Returns the tier's model name, or ``None`` with no user turns.
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
    # Walk oldest->newest: a turn at/above the latch holds and resets the calm counter; a turn
    # below counts as calm, and once `cooldown` calm turns accumulate the latch steps down.
    latched, calm = 0, 0
    for rank in ranks:
        if rank >= latched:
            latched, calm = rank, 0
        else:
            calm += 1
            if cooldown and calm >= cooldown:
                latched, calm = rank, 0
    return tiers[latched].model


def _clamp_to_allowed(chosen: str, ladder: list[str], allowed: frozenset[str]) -> str:
    """The allowed model nearest ``chosen`` in the tier ``ladder`` (preferring not to raise cost).

    For a virtual key's model allowlist (WF-ADR-0035): if ``chosen`` is not permitted, route to
    the highest allowed tier at or below it (cheaper), else the cheapest allowed tier above it.
    Falls back to a stable allowed model when the ladder does not position ``chosen`` (e.g.
    classifier mode). Pure; no model call.
    """
    if not allowed or chosen in allowed:
        return chosen
    in_ladder = [m for m in ladder if m in allowed]
    if not in_ladder:
        return sorted(allowed)[0]  # no tier ordering to clamp along; stable and deterministic
    if chosen in ladder:
        ci = ladder.index(chosen)
        below = [m for m in in_ladder if ladder.index(m) <= ci]
        return below[-1] if below else in_ladder[0]
    return in_ladder[0]


# --- upstream callers / SSE (monkeypatch seams; called by bare name via the module namespace) ---


def forward_request(
    url: str, headers: dict[str, str], json_body: dict, timeout: float = _DEFAULT_TIMEOUT
) -> tuple[int, bytes, str]:
    """POST ``json_body`` to ``url``; return ``(status, content, content_type)`` (sync).

    Used by :func:`invoke_model` (the onboarding A/B caller, off the async server). Isolated so
    tests can substitute it without a real upstream.
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

    Transport failures raise :class:`UpstreamError` so the handler returns an OpenAI-shaped
    error instead of a bare 500. Isolated so tests can substitute it.
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
    """Async generator relaying an upstream SSE stream chunk by chunk.

    Transport failures raise :class:`UpstreamError`, which the handler turns into a terminal
    SSE error event. Isolated so tests can substitute it.
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

    The multi-turn relay behind :func:`invoke_model`; the terminal chat uses it in-process.
    Reuses :func:`forward_request`, so tests substitute the network the same way.
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
    """Run a single ``prompt`` turn through one upstream and return its text (BYO key)."""
    return invoke_messages(model, [{"role": "user", "content": prompt}], timeout)


def parse_sse_deltas(lines: Iterable[str]) -> Iterator[str]:
    """Yield assistant text deltas from OpenAI-style SSE ``data:`` frames (pure; testable)."""
    for line in lines:
        if not line.startswith("data:"):  # an empty line also fails this and is skipped
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            frame = json.loads(payload)
            delta = frame["choices"][0]["delta"].get("content")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            continue
        if delta:
            yield str(delta)


def _first_choice_text(response: object) -> str:
    """Assistant text of a non-streaming completion (``""`` when absent) — for token estimates."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        return ""
    message = choices[0].get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return ""


def stream_messages(
    model: GatewayModel, messages: list[dict], timeout: float = _DEFAULT_TIMEOUT
) -> Iterator[str]:
    """Stream assistant text deltas from one upstream over SSE (sync; BYO key).

    The streaming counterpart to :func:`invoke_messages` for the terminal chat. Raises
    :class:`UpstreamError` on transport failure.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - only reachable without the gateway extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        secret = os.environ.get(model.api_key_env)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
    payload = {"model": model.model, "messages": list(messages), "stream": True}
    endpoint = model.base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.stream("POST", endpoint, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise RuntimeError(f"{model.model} upstream returned {resp.status_code}")
            yield from parse_sse_deltas(resp.iter_lines())
    except httpx.HTTPError as exc:
        raise UpstreamError(str(exc) or exc.__class__.__name__) from exc


def _resolve_timeout() -> float:
    """Upstream timeout in seconds: ``WAYFINDER_ROUTER_TIMEOUT`` when set and valid, else default."""
    raw = os.environ.get(_TIMEOUT_ENV)
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        logger.warning("ignoring invalid %s=%r", _TIMEOUT_ENV, raw)
        return _DEFAULT_TIMEOUT


class _ConfigHolder:
    """Caches routing + gateway config, reloading when ``wayfinder-router.toml`` changes.

    Lets a recalibration take effect on the running gateway with no restart: each request
    checks the config file's mtime and re-reads only when it moved. A malformed mid-flight
    write keeps the last-good config (the marker still advances so it is not retried every
    request), is logged, and increments the reload-failure metric. A lock guards the
    check-and-swap so a concurrent reload cannot publish a half-updated pair.
    """

    def __init__(
        self, start_dir: str, *, on_reload_failure: Callable[[], None] | None = None
    ) -> None:
        self.start_dir = start_dir
        self._on_reload_failure = on_reload_failure
        self._routing = load_routing_config(start_dir)
        self._gateway = load_gateway_config(start_dir)
        self._mtime = self._mtime_now()
        self._lock = threading.Lock()

    def _mtime_now(self) -> float | None:
        path = find_config_file(self.start_dir)
        if path is None:
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def current(self) -> tuple[RoutingConfig, GatewayConfig]:
        with self._lock:
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


# --- request-pipeline runtime + stage helpers (module-level so the handlers stay thin) ---


@dataclass
class _GatewayRuntime:
    """Long-lived per-app state, built once in :func:`build_app` and threaded to the stage
    helpers. Holds no request-scoped data; the counters/ledger/breaker/limiters survive config
    hot-reloads deliberately (runtime state, not config)."""

    start_dir: str
    dry_run: bool
    clock: Callable[[], float]
    request_timeout: float
    feedback_token: str | None
    savings_path: str
    metrics: Metrics
    holder: _ConfigHolder
    ledger: pricing.SavingsLedger
    breaker: reliability.CircuitBreaker
    response_cache: cache.ResponseCache
    rate_limiter: ratelimit.RateLimiter
    key_limiters: dict[str, ratelimit.RateLimiter]
    recent: deque[dict]
    last_save: list[float]  # one-cell debounce for best-effort disk snapshots


class _BudgetBlocked(Exception):
    """Internal signal: a hard budget breach that must return HTTP 402."""

    def __init__(self, window: str, limit: float) -> None:
        super().__init__(f"{window} budget of {limit} reached")
        self.window = window
        self.limit = limit


def _price_table(gw: GatewayConfig, decision: object) -> tuple[dict[str, float], bool]:
    """The cost table for this turn's tier ladder (``{model: cost_per_1k}``, priced?)."""
    model_costs = {n: m.cost_per_1k for n, m in gw.models.items()}
    tiers = getattr(decision, "tiers", None) or ()
    ladder = [t.model for t in tiers] or list(gw.models)
    return pricing.price_table(model_costs, ladder)


def _auth_headers(model: GatewayModel) -> dict[str, str]:
    """Forward headers for one upstream; the provider key comes from the environment."""
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _missing_keys(gw: GatewayConfig) -> list[str]:
    return sorted(
        name
        for name, model in gw.models.items()
        if model.api_key_env and not os.environ.get(model.api_key_env)
    )


def _persist_savings(rt: _GatewayRuntime) -> None:
    """Best-effort, debounced snapshot of the savings ledger to disk (never breaks a request)."""
    now = time.time()
    if now - rt.last_save[0] < _SAVINGS_SAVE_INTERVAL:
        return
    rt.last_save[0] = now
    try:
        rt.ledger.save(rt.savings_path)
    except OSError as exc:
        logger.warning("could not persist savings ledger to %s: %s", rt.savings_path, exc)


def _record_turn(
    rt: _GatewayRuntime, entry: dict, chosen: str, decision: object, gw: GatewayConfig,
    response: object, prompt_text: str, completion_text: str, vkey: str | None = None,
) -> tuple[int, int, bool]:
    """Cost the turn from token usage x the price table; record it (no model call).

    ``vkey`` attributes the turn to a virtual key in the ledger (WF-ADR-0035). Returns
    ``(prompt_tokens, completion_tokens, estimated)`` so a caller (e.g. the response cache)
    can reuse the counts without re-tokenizing.
    """
    costs, priced = _price_table(gw, decision)
    rt.ledger.priced = priced
    pt, ct, estimated = pricing.usage_tokens(
        response, prompt_text=prompt_text, completion_text=completion_text
    )
    tc = pricing.turn_cost(chosen, pt, ct, costs, estimated=estimated)
    rt.ledger.record(tc, vkey=vkey)
    rt.metrics.observe_cost(tc.realized, tc.baseline)
    entry["cost"] = {  # metadata only — dollars and token counts, never prompt text
        "realized": tc.realized, "baseline": tc.baseline, "saved": tc.savings,
        "tokens": tc.prompt_tokens + tc.completion_tokens,
        "unit": "usd" if priced else "relative", "estimated": estimated,
    }
    _persist_savings(rt)
    return pt, ct, estimated


def _key_limiter(rt: _GatewayRuntime, key_id: str, rl_cfg: RateLimit) -> ratelimit.RateLimiter:
    """The per-key limiter, created on first use and kept alive so its window counters persist."""
    lim = rt.key_limiters.get(key_id)
    if lim is None:
        lim = rt.key_limiters[key_id] = ratelimit.RateLimiter(
            rpm=rl_cfg.rpm, tpm=rl_cfg.tpm, window=rl_cfg.window, clock=rt.clock
        )
    else:
        lim.reconfigure(rpm=rl_cfg.rpm, tpm=rl_cfg.tpm, window=rl_cfg.window)
    return lim


def _resolve_route(
    body: dict, messages: object, decision: ComplexityScore, routing: RoutingConfig,
    gw: GatewayConfig, *, threshold_header: str | None, sticky: bool, cooldown: int,
    slash_pin: str | None,
) -> tuple[str, str]:
    """Pick ``(chosen, mode)`` from the (already computed) decision + per-request overrides.

    Mode precedence: API ``model`` pin > slash-pin > (threshold-override | scored, then the
    sticky latch may upgrade). The score is always computed by the caller regardless — an
    override only moves which endpoint it routes to, never how it is computed (WF-ADR-0011).
    Raises :class:`BadOverride` (threshold path only).
    """
    pin = resolve_pin(body.get("model"), routing, gw)
    if pin is not None:
        return pin, "pinned"
    if slash_pin is not None:
        return slash_pin, "slash-pinned"
    threshold = parse_threshold_header(threshold_header)
    if threshold is not None:
        effective_tiers = threshold_tiers(routing, threshold)
        chosen, mode = recommend_tier(decision.score, effective_tiers), "threshold-override"
    else:
        effective_tiers = routing.tiers
        chosen, mode = decision.recommendation, "scored"
    # Conversation latch (WF-ADR-0022): escalate to the highest tier any single turn needed.
    if sticky and routing.classifier is None and len(effective_tiers) >= 2:
        latched = conversation_high_water(messages, routing, effective_tiers, cooldown=cooldown)
        if latched is not None and _tier_rank(latched, effective_tiers) > _tier_rank(
            chosen, effective_tiers
        ):
            chosen, mode = latched, "sticky"
    return chosen, mode


def _apply_budget(
    rt: _GatewayRuntime, gw: GatewayConfig, decision: ComplexityScore, chosen: str, mode: str,
    *, key_id: str | None, key_cfg: VirtualKey | None, offline: bool,
) -> tuple[str, str, str | None]:
    """Enforce the gateway-wide and per-key spend caps (WF-ROADMAP-0006). Delivery only.

    Priced-ness comes from the *current* config (not the ledger's lagging flag) so a hot
    reload adding/removing ``cost_per_1k`` bites this very request. On a hard block (not
    offline) raises :class:`_BudgetBlocked`; on a degrade routes to the cheapest tier (offline
    already lands cheapest, so it never rewrites the reported decision). Returns
    ``(chosen, mode, budget_state)``.
    """
    budget_state: str | None = None
    _, budget_priced = _price_table(gw, decision)
    if not budget_priced:  # no real dollars to cap — a relative-unit demo is a no-op
        return chosen, mode, budget_state
    applicable: list[tuple[Budget, float]] = []
    if gw.budget is not None:
        applicable.append((gw.budget, rt.ledger.spent(gw.budget.window)))
    if key_cfg is not None and key_cfg.budget is not None and key_id is not None:
        applicable.append((key_cfg.budget, rt.ledger.spent(key_cfg.budget.window, vkey=key_id)))
    for bud, spent in applicable:
        if spent < bud.limit:
            continue
        if bud.on_breach == "block" and not offline:
            raise _BudgetBlocked(bud.window, bud.limit)
        budget_state = "degraded"
    if budget_state == "degraded" and not offline:
        tiers_sorted = sorted(decision.tiers or (), key=lambda t: t.min_score)
        cheapest = tiers_sorted[0].model if tiers_sorted else None
        if cheapest is not None and cheapest != chosen:
            chosen, mode = cheapest, "budget-degraded"
    return chosen, mode, budget_state


def _cost_block(decision: ComplexityScore, gw: GatewayConfig, chosen: str) -> dict:
    """The saved-vs-cloud cost summary for the demo / debug view (metadata only).

    Uses configured ``cost_per_1k`` when present; otherwise synthesises a relative ladder
    (cheapest 0.2 .. dearest 1.0) so the story still renders, clearly flagged ``estimated``.
    """
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


def _explain_payload(
    decision: ComplexityScore, gw: GatewayConfig, routing: RoutingConfig,
    *, chosen: str, mode: str, offline: bool, request_id: str,
) -> dict:
    """The decision payload for the demo UI / debug clients (WF-ADR-0020).

    The ONLY caller of :func:`explain_score` — it runs only on the debug / dry-run path, never
    on the scored relay path, so the default response stays byte-clean (WF-ADR-0001).
    """
    return {
        "model": chosen,
        "score": round(decision.score, 2),
        "mode": mode,
        "offline": offline,
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
        "cost": _cost_block(decision, gw, chosen),
    }


async def _deliver(
    rt: _GatewayRuntime, plan: list[str], gw: GatewayConfig, body: dict, request_id: str
) -> tuple[str | None, int, bytes, str]:
    """Try each target in ``plan`` with bounded retries; return the one that served.

    Same-tier failover + retry + circuit breaker (WF-ADR-0031): on a transport error or a
    429/5xx, back off and retry; on exhaustion fall to the next endpoint. An ordinary 4xx is
    the client's and is returned as-is; a 401/403 (auth failure) is returned but counts as a
    breaker failure so repeats degrade. ``(None, 502, ...)`` when every target failed.
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
                    url, headers, forward_body, rt.request_timeout
                )
            except UpstreamError as exc:  # transport failure — always retryable
                last_error = str(exc) or exc.__class__.__name__
                rt.metrics.observe_upstream_error(name)
                if attempt < gw.retries:
                    await asyncio.sleep(delays[attempt])
                continue
            if not reliability.is_retryable(status):  # 2xx or a non-retryable 4xx — done
                if reliability.is_auth_failure(status):
                    # A bad/expired upstream key makes the target unusable, not just this
                    # request bad: count it as a breaker failure so repeats degrade. Still
                    # returned so the client sees the auth error; retrying a bad key is pointless.
                    rt.metrics.observe_upstream_error(name)
                    rt.breaker.record(name, False)
                else:  # genuine 2xx or an ordinary client 4xx — the target is reachable
                    rt.metrics.observe_upstream(name, time.perf_counter() - started)
                    rt.breaker.record(name, True)
                return name, status, content, ctype
            last_error = f"upstream returned {status}"  # 429/5xx — retry/fall back
            rt.metrics.observe_upstream_error(name)
            if attempt < gw.retries:
                await asyncio.sleep(delays[attempt])
        rt.breaker.record(name, False)  # every attempt on this target failed
        logger.warning("request %s: target '%s' exhausted (%s)", request_id, name, last_error)
    body_json = json.dumps(
        {"error": {"message": last_error, "type": "wayfinder_router_upstream_error"}}
    ).encode()
    return None, 502, body_json, "application/json"


def build_app(
    start_dir: str = ".", *, dry_run: bool = False, timeout: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Build the FastAPI gateway app; config hot-reloads on ``wayfinder-router.toml`` change.

    ``dry_run`` makes ``/v1/chat/completions`` return the routing decision without calling any
    upstream. ``timeout`` overrides the upstream timeout (else ``WAYFINDER_ROUTER_TIMEOUT`` or
    60s). The heavy web deps are imported here, lazily, so importing this module stays light.
    """
    try:
        from fastapi import Body, FastAPI, Header, Response
        from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
    except ImportError as exc:  # pragma: no cover - only reachable without the gateway extra
        raise GatewayUnavailable(_INSTALL_HINT) from exc

    from . import __version__  # local import: sidesteps a circular import at module load time

    metrics = Metrics(__version__)
    holder = _ConfigHolder(start_dir, on_reload_failure=metrics.record_reload_failure)
    request_timeout = timeout if timeout is not None else _resolve_timeout()
    feedback_token = os.environ.get(_FEEDBACK_TOKEN_ENV)
    recent: deque[dict] = deque(maxlen=_RECENT_MAX)  # decision metadata only, no prompt text

    # Savings ledger (WF-DESIGN-0007): persisted best-effort so the report survives restarts.
    savings_path = os.environ.get(_SAVINGS_FILE_ENV) or str(Path(start_dir) / "wayfinder-savings.json")
    try:
        ledger = pricing.SavingsLedger.load(savings_path)
    except (OSError, ValueError):
        ledger = pricing.SavingsLedger()

    app = FastAPI(title="wayfinder-router-gateway")

    # Startup diagnostics: surface misconfigurations that otherwise only show up as a confusing
    # first-request failure.
    _, gw0 = holder.current()
    metrics.set_model_costs(
        {name: model.cost_per_1k for name, model in gw0.models.items()
         if model.cost_per_1k is not None}
    )
    # Fill any api_key_cmd-backed keys from the user's secret store into the process environment,
    # in memory only (WF-DESIGN-0006), before the readiness check below.
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

    # One circuit breaker / cache / limiter for the gateway's lifetime; thresholds come from the
    # initial config so this runtime state survives routing/cost hot-reloads (WF-ADR-0031/0033/0034).
    breaker = reliability.CircuitBreaker(
        threshold=gw0.breaker_threshold, cooldown=gw0.breaker_cooldown
    )
    _cache0 = gw0.cache or CacheConfig()
    response_cache = cache.ResponseCache(
        enabled=_cache0.enabled, ttl=_cache0.ttl,
        max_entries=_cache0.max_entries, max_bytes=_cache0.max_bytes,
    )
    _rl0 = gw0.rate_limit or RateLimit()
    rate_limiter = ratelimit.RateLimiter(rpm=_rl0.rpm, tpm=_rl0.tpm, window=_rl0.window, clock=clock)

    rt = _GatewayRuntime(
        start_dir=start_dir, dry_run=dry_run, clock=clock, request_timeout=request_timeout,
        feedback_token=feedback_token, savings_path=savings_path, metrics=metrics, holder=holder,
        ledger=ledger, breaker=breaker, response_cache=response_cache, rate_limiter=rate_limiter,
        key_limiters={}, recent=recent, last_save=[0.0],
    )

    @app.get("/healthz")
    def healthz() -> dict:
        _, gw = holder.current()
        missing = _missing_keys(gw)
        body: dict = {
            "status": "degraded" if missing else "ok",
            "models": sorted(gw.models),
            "offline": gw.offline,  # standing config knob; the per-request header is separate
        }
        if missing:
            body["missing_keys"] = missing
        return body

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint() -> Response:
        """Prometheus text exposition of routing metrics (WF-ADR-0018) — metadata only,
        a pure read of in-memory counters off the scored path (no key, no model call)."""
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/v1/models")
    @app.get("/models")  # path tolerance: clients pointed at the bare host (no /v1 prefix)
    def list_models() -> dict:
        """Advertise the selectable routing options as an OpenAI-compatible list (WF-ADR-0012).

        ``prefer-*`` appears only for a tiered/binary router; a classifier has no ordered ladder.
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

        A pure read of the in-memory ledger (token counts x a price table, metadata only).
        ``period`` is ``today`` | ``7d`` | ``30d`` | ``all`` (unknown -> all). ``price_table_version``
        pins the current prices; the ladder falls back to ``gw.models`` when the router has no tiers.
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
        """Read-only view of recent routing decisions (WF-ADR-0014) — most-recent-first,
        metadata only (model, score, mode, request id, timestamp), never prompt text."""
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
        """The decision-first chat demo (WF-ADR-0020); self-contained, no build, no CDN."""
        return _DEMO_HTML

    @app.get("/router/profiles")
    def lexicon_profiles() -> dict:
        """Stock lexicon profiles (WF-ADR-0024) the demo can load. Static, read-only metadata."""
        return {"profiles": [p.to_dict() for p in PROFILES]}

    @app.get("/router/models")
    def router_models() -> dict:
        """Read-only view of the configured endpoints and whether each model's key is present
        (WF-ADR-0025). Returns only the env-var *name* and a boolean — never a secret."""
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
        """Render the configured router as `[routing]` TOML, with any `wayfinder_tuning` body
        applied (WF-ADR-0023) — the demo's "Export config". Pure: no model call."""
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
        # Steady-state escalate loop: the caller records which model was good enough for a
        # prompt; the label feeds the next recalibration. Writing is guarded by an optional
        # bearer token (bare error envelope here, unlike the typed one elsewhere).
        if feedback_token is not None and authorization != f"Bearer {feedback_token}":
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        text_value = body.get("text")
        label_value = body.get("label")
        if not _nonempty_str(text_value):
            return JSONResponse(status_code=400, content={"error": "missing 'text'"})
        if not _nonempty_str(label_value):
            return JSONResponse(status_code=400, content={"error": "missing 'label'"})
        record_label(str(Path(start_dir) / DEFAULT_LOG), _as_str(text_value), _as_str(label_value))
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
        x_wayfinder_offline: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        routing, gw = holder.current()

        # Keep the long-lived cache/limiter in sync with hot-reloaded config; disabling the
        # cache purges retained bodies immediately (WF-ADR-0033).
        if gw.cache is not None:
            response_cache.reconfigure(
                enabled=gw.cache.enabled, ttl=gw.cache.ttl,
                max_entries=gw.cache.max_entries, max_bytes=gw.cache.max_bytes,
            )
        elif response_cache.enabled:
            response_cache.reconfigure(
                enabled=False, ttl=response_cache.ttl,
                max_entries=response_cache.max_entries, max_bytes=response_cache.max_bytes,
            )
        if gw.rate_limit is not None:
            rate_limiter.reconfigure(
                rpm=gw.rate_limit.rpm, tpm=gw.rate_limit.tpm, window=gw.rate_limit.window
            )
        elif rate_limiter.active():
            rate_limiter.reconfigure(rpm=None, tpm=None, window=rate_limiter.window)

        def _too_many(result: ratelimit.RateResult, scope: str) -> JSONResponse:
            metrics.observe_rate_limited(result.limit)
            logger.info("request %s rate-limited (%s%s)", request_id, result.limit, scope)
            return JSONResponse(
                status_code=429,
                content={"error": {
                    "message": f"{result.limit} rate limit exceeded",
                    "type": "wayfinder_router_rate_limited",
                }},
                headers={
                    "x-wayfinder-router-request-id": request_id,
                    "x-wayfinder-router-rate-limit": result.limit,
                    "Retry-After": str(result.retry_after),
                },
            )

        # Rate-limit admission (WF-ADR-0034/0035): the outermost guardrail — BEFORE auth, so an
        # unauthenticated flood is shed with one 429 instead of a per-request 401 (each a SHA-256
        # + constant-time compare). A cache hit still counts a request (RPM); only real upstream
        # calls count against TPM. The gateway-wide cap is enforced here; the key's own cap needs
        # the resolved key id, so it is checked just after auth.
        rl = rate_limiter.admit()
        if not rl.allowed:
            return _too_many(rl, "")

        # Virtual-key auth (WF-ADR-0035): required only when keys are configured (else the gateway
        # stays open, backward compatible). Provider keys are unaffected (still from the environment).
        key_id: str | None = None
        key_cfg: VirtualKey | None = None
        if gw.keys:
            configured_hashes = {kid: vk.hash for kid, vk in gw.keys.items()}
            key_id = vkeys.match(vkeys.extract_bearer(authorization), configured_hashes)
            if key_id is None:
                logger.info("request %s unauthorized (missing/invalid virtual key)", request_id)
                return JSONResponse(
                    status_code=401,
                    content={"error": {
                        "message": "missing or invalid API key",
                        "type": "wayfinder_router_unauthorized",
                    }},
                    headers={
                        "x-wayfinder-router-request-id": request_id,
                        "WWW-Authenticate": "Bearer",
                    },
                )
            key_cfg = gw.keys[key_id]
            metrics.observe_key_request(key_id)

        key_limiter: ratelimit.RateLimiter | None = None
        if key_id is not None and key_cfg is not None and key_cfg.rate_limit is not None:
            key_limiter = _key_limiter(rt, key_id, key_cfg.rate_limit)
            krl = key_limiter.admit()
            if not krl.allowed:
                return _too_many(krl, f" key={key_id}")

        def _reject(exc: BadOverride) -> JSONResponse:
            logger.info("request %s rejected: %s", request_id, exc)
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "wayfinder_router_bad_override"}},
                headers={"x-wayfinder-router-request-id": request_id},
            )

        # Resolve scope/latch overrides + any per-request scoring tuning before scoring. The
        # tuning field is popped so it is never forwarded to the upstream model.
        tuning = body.pop(TUNING_FIELD, None)
        try:
            route_on = parse_route_on_header(x_wayfinder_route_on) or gw.route_on
            sticky = resolve_sticky(x_wayfinder_sticky, gw.sticky)
            cooldown = resolve_sticky_cooldown(x_wayfinder_sticky_cooldown, gw.sticky_cooldown)
            routing = apply_scoring_overrides(routing, tuning)
        except BadOverride as exc:
            return _reject(exc)

        # Score once (always reported); an override only changes which endpoint it routes to.
        # The scoring time is the decision-latency metric (WF-ADR-0018).
        score_started = time.perf_counter()
        messages = body.get("messages")
        # In-message routing override (WF-ADR-0036): a recognized "/directive" at the start of the
        # latest user message pins the route and is stripped before scoring/forwarding.
        slash_pin: str | None = None
        if gw.slash_directives:
            slash_pin, cleaned = resolve_slash_directive(messages, routing, gw)
            if cleaned is not None:
                body["messages"] = cleaned
                messages = cleaned
        decision = score_complexity(extract_prompt(messages, route_on=route_on), config=routing)
        decision_seconds = time.perf_counter() - score_started

        # Choose the model + mode (pin > slash > threshold/scored, sticky may upgrade). Offline is
        # decided next, before BOTH the budget hard-block and the cache, so an offline request is
        # never rejected for spend it won't incur nor replays a dearer tier's cached answer.
        try:
            chosen, mode = _resolve_route(
                body, messages, decision, routing, gw,
                threshold_header=x_wayfinder_threshold, sticky=sticky, cooldown=cooldown,
                slash_pin=slash_pin,
            )
        except BadOverride as exc:
            return _reject(exc)

        offline = gw.offline or (x_wayfinder_offline or "").strip().lower() in ("1", "true", "yes")

        # Budget enforcement (WF-ROADMAP-0006): the strictest of the gateway-wide + per-key caps
        # wins; only meaningful with real costs. Changes only *delivery*; the decision is untouched.
        try:
            chosen, mode, budget_state = _apply_budget(
                rt, gw, decision, chosen, mode,
                key_id=key_id, key_cfg=key_cfg, offline=offline,
            )
        except _BudgetBlocked as exc:
            logger.info(
                "request %s blocked: %s budget of %s reached", request_id, exc.window, exc.limit
            )
            return JSONResponse(
                status_code=402,
                content={"error": {
                    "message": str(exc),
                    "type": "wayfinder_router_budget_exhausted",
                }},
                headers={
                    "x-wayfinder-router-request-id": request_id,
                    "x-wayfinder-router-budget": "blocked",
                },
            )

        # Per-key model allowlist (WF-ADR-0035): a key may only use its permitted models. Applied
        # last, so it is the final word on the route — clamp to the nearest allowed tier.
        if key_cfg is not None and key_cfg.models:
            ladder = [t.model for t in sorted(decision.tiers or (), key=lambda t: t.min_score)]
            clamped = _clamp_to_allowed(chosen, ladder, frozenset(key_cfg.models))
            if clamped != chosen:
                chosen, mode = clamped, "key-scoped"

        wf_headers = {
            "x-wayfinder-router-model": chosen,
            "x-wayfinder-router-score": f"{decision.score:.2f}",  # string, 2dp (distinct from round)
            "x-wayfinder-router-mode": mode,
            "x-wayfinder-router-request-id": request_id,
        }
        if budget_state is not None:
            wf_headers["x-wayfinder-router-budget"] = budget_state
        if offline:  # set once, so every path (cache hit, dry-run, delivery) carries the marker
            wf_headers["x-wayfinder-router-offline"] = "true"
        # Informational rate-limit headers (WF-ADR-0034): the tightest applicable cap by remaining
        # headroom (a tighter per-key cap wins over gateway-wide).
        rate_snaps = [
            s for s in (rate_limiter.snapshot(),
                        key_limiter.snapshot() if key_limiter is not None else None)
            if s is not None
        ]
        if rate_snaps:
            limit, remaining, reset = min(rate_snaps, key=lambda s: s[1])
            wf_headers["X-RateLimit-Limit"] = str(limit)
            wf_headers["X-RateLimit-Remaining"] = str(remaining)
            wf_headers["X-RateLimit-Reset"] = str(reset)
        logger.info(
            "request %s -> %s (score %.2f, mode %s)", request_id, chosen, decision.score, mode
        )
        entry = {  # mutable: cost fields (metadata only) are filled in after the upstream replies
            "request_id": request_id,
            "model": chosen,
            "score": round(decision.score, 2),  # float, 2dp (the header uses the string form)
            "mode": mode,
            "ts": time.time(),
        }
        if key_id is not None:  # attribution: which virtual key this turn belongs to
            entry["key"] = key_id
        recent.append(entry)
        # Full prompt text, used only as a token-count fallback when the upstream omits `usage`.
        prompt_all = extract_prompt(messages, route_on="all")
        metrics.observe_decision(chosen, mode, decision_seconds)
        debug = (x_wayfinder_debug or "").strip().lower() in ("1", "true", "yes")

        if dry_run:
            return JSONResponse(
                status_code=200,
                content={"wayfinder": {
                    **_explain_payload(
                        decision, gw, routing,
                        chosen=chosen, mode=mode, offline=offline, request_id=request_id,
                    ),
                    "dry_run": True,
                }},
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

        # Effective delivery model (WF-ADR-0039): offline serves the cheapest tier; else the scored
        # choice. The cache lookup and delivery plan both key off this, so an offline request never
        # replays a dearer tier's cached answer.
        ladder = [t.model for t in sorted(decision.tiers or (), key=lambda t: t.min_score)]
        deliver_from = ladder[0] if (offline and ladder) else chosen

        # Response cache (WF-ADR-0033): an exact-match, deterministic, non-streaming hit replays a
        # stored answer with no upstream call, no breaker effect, and no budget spend. Keyed on the
        # *served upstream model id*. Skipped for streaming, non-deterministic, tool, or debug requests.
        cache_state: str | None = None
        serve_target = gw.models.get(deliver_from)
        cacheable = (
            gw.cache is not None
            and gw.cache.enabled
            and not debug
            and serve_target is not None
            and body.get("stream") is not True
            and cache.is_cacheable(body)
        )
        if cacheable and serve_target is not None:
            stored = response_cache.get(cache.cache_key(serve_target.model, body))
            if stored is not None:
                costs, _ = _price_table(gw, decision)
                avoided = pricing.turn_cost(
                    deliver_from, stored.prompt_tokens, stored.completion_tokens,
                    costs, estimated=stored.estimated,
                ).realized
                metrics.observe_cache_hit(avoided)
                entry["cache"] = "hit"  # decision-feed metadata only — never the body
                logger.info("request %s cache hit (served-by %s)", request_id, deliver_from)
                hit_headers = {
                    **wf_headers,
                    "x-wayfinder-router-served-by": deliver_from,
                    "x-wayfinder-router-cache": "hit",
                }
                return Response(
                    content=stored.body,
                    status_code=stored.status,
                    media_type=stored.content_type,
                    headers=hit_headers,
                )
            metrics.observe_cache_miss()
            cache_state = "miss"

        # Delivery plan (WF-ADR-0031): the chosen tier's endpoint, its same-tier fallbacks, then
        # cross-tier candidates per policy — minus any whose breaker is open or that fail the
        # pre-call context check. Offline delivers to the cheapest tier only (never a dearer one).
        prompt_estimate = pricing.estimate_tokens(prompt_all)

        def _precall_ok(name: str) -> bool:  # skip a target whose window can't fit the prompt
            model = gw.models.get(name)
            return model is None or reliability.precheck_ok(prompt_estimate, model.context_window)

        if offline and ladder:
            own_fallbacks = gw.models[deliver_from].fallbacks if deliver_from in gw.models else ()
            plan = reliability.delivery_plan(deliver_from, own_fallbacks, breaker, allow=_precall_ok)
        else:
            policy = (
                x_wayfinder_failover
                if x_wayfinder_failover in reliability.FAILOVER_POLICIES
                else gw.failover
            )
            cross_tier = reliability.failover_candidates(chosen, ladder, policy)
            candidates = [*target.fallbacks, *cross_tier]
            plan = reliability.delivery_plan(chosen, candidates, breaker, allow=_precall_ok)
        if not plan:  # nothing left: every candidate is tripped, cooling down, or too small
            logger.warning("request %s: no available upstream for '%s'", request_id, chosen)
            circuit_open = {
                "message": f"no available upstream for '{chosen}' (cooling down or context too small)",
                "type": "wayfinder_router_circuit_open",
            }
            return JSONResponse(status_code=503, content={"error": circuit_open}, headers=wf_headers)

        def _served_headers(served: str) -> dict[str, str]:
            hdrs = {**wf_headers, "x-wayfinder-router-served-by": served}
            if served != chosen and not offline:  # an offline degrade is flagged separately
                hdrs["x-wayfinder-router-failover"] = "true"
            return hdrs

        if body.get("stream") is True:
            served = plan[0]  # first breaker-aware target; a stream is attempted once (WF-ADR-0031)
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
                    # No upstream `usage` over SSE by default — estimate from the streamed text.
                    completion_text = "".join(parse_sse_deltas("".join(streamed).splitlines()))
                    s_pt, s_ct, _ = _record_turn(
                        rt, entry, served, decision, gw, None, prompt_all, completion_text,
                        vkey=key_id,
                    )
                    rate_limiter.add_tokens(s_pt + s_ct)  # count served tokens toward TPM
                    if key_limiter is not None:
                        key_limiter.add_tokens(s_pt + s_ct)  # ...and the key's own TPM window
                    if debug:
                        meta = json.dumps(_explain_payload(
                            decision, gw, routing,
                            chosen=chosen, mode=mode, offline=offline, request_id=request_id,
                        ))
                        yield f"event: wayfinder\ndata: {meta}\n\n".encode()
                except UpstreamError as exc:
                    # HTTP 200 was already sent — the error is a terminal in-band SSE event.
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

        served_by, status, content, content_type = await _deliver(rt, plan, gw, body, request_id)
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
            pt, ct, estimated = _record_turn(
                rt, entry, served_by, decision, gw, response_obj,
                prompt_all, _first_choice_text(response_obj), vkey=key_id,
            )
            rate_limiter.add_tokens(pt + ct)  # count served tokens toward TPM (WF-ADR-0034)
            if key_limiter is not None:
                key_limiter.add_tokens(pt + ct)  # ...and the key's own TPM window
            # Store the raw success (captured BEFORE any debug mutation), keyed on the model that
            # actually served — so a failover turn populates the served model's key, not chosen's.
            if cache_state == "miss" and cache.is_storable(status, content_type, response_obj):
                response_cache.put(
                    cache.cache_key(gw.models[served_by].model, body),
                    cache.CachedResponse(
                        status=status, content_type=content_type, body=content,
                        prompt_tokens=pt, completion_tokens=ct, estimated=estimated,
                        stored_at=response_cache.clock(),
                    ),
                )
        if debug and isinstance(response_obj, dict):
            response_obj["wayfinder"] = _explain_payload(
                decision, gw, routing,
                chosen=chosen, mode=mode, offline=offline, request_id=request_id,
            )
            content = json.dumps(response_obj).encode()
        headers = _served_headers(served_by)
        if cache_state is not None:  # surface the miss (a hit returned earlier)
            headers = {**headers, "x-wayfinder-router-cache": cache_state}
        return Response(
            content=content, status_code=status, media_type=content_type, headers=headers,
        )

    @app.post("/v1/messages")
    @app.post("/messages")  # path tolerance, like /v1/chat/completions
    async def messages(  # noqa: B008 - FastAPI default
        body: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> Response:
        """Claude Code adapter (WF-DESIGN-0011): Anthropic Messages <-> OpenAI Chat Completions.

        Pure translation around the existing router — scores nothing, calls no model. The inbound
        Anthropic request is reshaped to an OpenAI body, delegated to :func:`chat_completions` (so
        routing/budget/failover are identical to the native endpoint), and the reply is reshaped
        back. No per-request header overrides ride this path, but virtual-key auth does.
        """
        raw_model = body.get("model")
        model_echo: str = raw_model if isinstance(raw_model, str) else ""
        openai_body = anthropic_adapter.anthropic_to_openai_request(body)
        prompt_text = extract_prompt(openai_body.get("messages"), route_on="all")
        input_estimate = pricing.estimate_tokens(prompt_text)

        inner = await chat_completions(
            body=openai_body,
            x_wayfinder_threshold=None,
            x_wayfinder_route_on=None,
            x_wayfinder_sticky=None,
            x_wayfinder_sticky_cooldown=None,
            x_wayfinder_debug=None,
            x_wayfinder_failover=None,
            x_wayfinder_offline=None,
            authorization=authorization,  # virtual-key auth applies to Claude Code too
        )
        request_id = inner.headers.get("x-wayfinder-router-request-id", "")
        message_id = f"msg_{request_id}" if request_id else "msg_unknown"
        # Only the decision headers cross the adapter; content-type/length are recomputed here.
        out_headers = {
            key: val for key, val in inner.headers.items() if key.lower().startswith("x-wayfinder")
        }

        if isinstance(inner, StreamingResponse):  # Claude Code streams by default
            translated = anthropic_adapter.messages_stream(
                inner.body_iterator,
                model=model_echo,
                message_id=message_id,
                input_tokens=input_estimate,
            )
            return StreamingResponse(translated, media_type="text/event-stream", headers=out_headers)

        raw = bytes(inner.body) if inner.body else b""
        status = inner.status_code
        try:
            parsed = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if status >= 400 or not isinstance(parsed, dict):
            # Prefer the most specific message: the upstream OpenAI error.message, then the raw
            # body (capped at 500 chars), then a generic fallback.
            message = "upstream error"
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                message = str(parsed["error"].get("message", message))
            elif raw:
                message = raw.decode("utf-8", "ignore")[:500]
            return JSONResponse(
                status_code=status,
                content=anthropic_adapter.anthropic_error(status, message),
                headers=out_headers,
            )
        if "choices" not in parsed:  # e.g. a dry-run decision payload — forward it verbatim
            return JSONResponse(status_code=status, content=parsed, headers=out_headers)
        anthropic_body = anthropic_adapter.openai_to_anthropic_response(
            parsed, model=model_echo, message_id=message_id, prompt_text=prompt_text
        )
        return JSONResponse(status_code=status, content=anthropic_body, headers=out_headers)

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
