"""Deterministic cost & savings accounting for the gateway (WF-DESIGN-0007).

Pure, offline arithmetic over token counts and a configured price table — no model
call, no key, no network (WF-ADR-0001). Turns the routing decisions the gateway already
makes into a persisted, per-period **savings** report: what each request cost on the
chosen tier versus what it would have cost always routing to the dearest ("frontier")
tier.

This lives in the invocation/observability layer; the scored core never imports it.
Token counts come from the upstream ``usage`` when present, else a ~4-chars/token
estimate (then the turn is flagged ``estimated``). When no real ``cost_per_1k`` metadata
is configured the table falls back to relative units (cheapest 0.2 .. dearest 1.0,
mirroring the demo), and the report is flagged ``priced = false`` so the figures are
never mistaken for dollars.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

CHARS_PER_TOKEN = 4  # the rough estimate the TUI uses too; anything derived is labelled ~


def estimate_tokens(text: str) -> int:
    """A rough token count (~4 chars/token) for when an upstream omits ``usage``."""
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def price_table(
    model_costs: Mapping[str, float | None], tier_ladder: Iterable[str]
) -> tuple[dict[str, float], bool]:
    """``({model: cost_per_1k}, priced)`` from configured costs, or a relative fallback.

    ``priced`` is ``False`` when no real cost metadata is configured: then costs are
    relative units laid across the tier ladder (cheapest ``0.2`` .. dearest ``1.0``), the
    same fallback the demo's cost block uses so the saved-vs-frontier story still renders.
    """
    real = {name: float(c) for name, c in model_costs.items() if c is not None}
    if real:
        return real, True
    ladder = list(tier_ladder) or list(model_costs)
    if not ladder:
        return {}, False
    lo, hi = 0.2, 1.0
    step = (hi - lo) / max(1, len(ladder) - 1)
    return {m: round(lo + i * step, 3) for i, m in enumerate(ladder)}, False


def table_version(costs: Mapping[str, float]) -> str:
    """A short, stable fingerprint of the price table, so a saved report is auditable."""
    blob = json.dumps({k: costs[k] for k in sorted(costs)}, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def usage_tokens(
    response: object, *, prompt_text: str = "", completion_text: str = ""
) -> tuple[int, int, bool]:
    """``(prompt_tokens, completion_tokens, estimated)`` — prefer the upstream ``usage``.

    Falls back to a ~4-chars/token estimate of the given prompt/completion text and sets
    ``estimated = True`` so the report can show which figures are exact.
    """
    if isinstance(response, Mapping):
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(pt, int) and isinstance(ct, int):
                return pt, ct, False
            total = usage.get("total_tokens")
            if isinstance(total, int):
                known = pt if isinstance(pt, int) else 0
                return known, max(0, total - known), False
    return estimate_tokens(prompt_text), estimate_tokens(completion_text), True


@dataclass(frozen=True)
class TurnCost:
    """One request's realized/baseline/savings, from token counts × the price table."""

    route: str
    realized: float
    baseline: float
    savings: float
    prompt_tokens: int
    completion_tokens: int
    estimated: bool  # tokens were estimated (no upstream usage)


def turn_cost(
    route: str,
    prompt_tokens: int,
    completion_tokens: int,
    costs: Mapping[str, float],
    *,
    estimated: bool,
    baseline: str | None = None,
) -> TurnCost:
    """Cost of one turn on ``route`` and the counterfactual on the baseline (dearest) tier.

    ``baseline`` names the "always-frontier" reference model; default is the dearest
    configured tier. Savings is ``baseline − realized`` (kept honest — may be negative on
    an escalated turn). Pure arithmetic; no model call.
    """
    total_k = (max(0, prompt_tokens) + max(0, completion_tokens)) / 1000.0
    dearest = max(costs.values()) if costs else 0.0
    baseline_per1k = costs.get(baseline, dearest) if baseline is not None else dearest
    chosen_per1k = costs.get(route, dearest)
    realized = round(chosen_per1k * total_k, 6)
    base = round(baseline_per1k * total_k, 6)
    return TurnCost(
        route=route,
        realized=realized,
        baseline=base,
        savings=round(base - realized, 6),
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        estimated=estimated,
    )


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _empty_route() -> dict:
    return {"n": 0, "realized": 0.0, "baseline": 0.0, "savings": 0.0, "tokens": 0}


def _empty_bucket() -> dict:
    b = _empty_route()
    b["estimated_n"] = 0
    b["by_route"] = {}
    return b


def _accumulate(target: dict, tc: TurnCost) -> None:
    target["n"] += 1
    target["realized"] = round(target["realized"] + tc.realized, 6)
    target["baseline"] = round(target["baseline"] + tc.baseline, 6)
    target["savings"] = round(target["savings"] + tc.savings, 6)
    target["tokens"] += tc.prompt_tokens + tc.completion_tokens


@dataclass
class SavingsLedger:
    """Daily-bucket accumulator of realized/baseline/savings + per-route counts.

    In-memory, bounded to ``max_days`` (old buckets are dropped). A lock guards updates so
    a best-effort disk snapshot stays internally consistent; the gateway's event loop is
    single-threaded, but persistence and tests may touch it from elsewhere. ``priced``
    records whether the figures are dollars (real ``cost_per_1k``) or relative units.
    """

    max_days: int = 400
    priced: bool = True
    days: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, tc: TurnCost, *, when: date | None = None) -> None:
        key = (when or _utc_today()).isoformat()
        with self._lock:
            bucket = self.days.setdefault(key, _empty_bucket())
            _accumulate(bucket, tc)
            if tc.estimated:
                bucket["estimated_n"] += 1
            route = bucket["by_route"].setdefault(tc.route, _empty_route())
            _accumulate(route, tc)
            self._prune_locked()

    def _prune_locked(self) -> None:
        if len(self.days) > self.max_days:
            for key in sorted(self.days)[: len(self.days) - self.max_days]:
                del self.days[key]

    def period(self, days: int | None = None, *, today: date | None = None) -> dict:
        """Aggregate the last ``days`` buckets (``None`` = all-time) into a report dict."""
        today = today or _utc_today()
        with self._lock:
            keys = sorted(self.days)
            if days is not None:
                cutoff = today.toordinal() - (days - 1)
                keys = [k for k in keys if date.fromisoformat(k).toordinal() >= cutoff]
            agg = _empty_bucket()
            for key in keys:
                bucket = self.days[key]
                for f in ("n", "realized", "baseline", "savings", "tokens", "estimated_n"):
                    agg[f] = round(agg[f] + bucket[f], 6) if isinstance(agg[f], float) else agg[f] + bucket[f]
                for route, rstats in bucket["by_route"].items():
                    tgt = agg["by_route"].setdefault(route, _empty_route())
                    for f in ("n", "realized", "baseline", "savings", "tokens"):
                        tgt[f] = round(tgt[f] + rstats[f], 6) if isinstance(tgt[f], float) else tgt[f] + rstats[f]
            return self._summary(agg, days)

    def _summary(self, agg: dict, days: int | None) -> dict:
        saved = agg["savings"]
        baseline = agg["baseline"]
        pct = round(100.0 * saved / baseline, 1) if baseline else 0.0
        return {
            "period_days": days,
            "unit": "usd" if self.priced else "relative",
            "priced": self.priced,
            "requests": agg["n"],
            "estimated_requests": agg["estimated_n"],
            "tokens": agg["tokens"],
            "realized": round(agg["realized"], 6),
            "baseline": round(agg["baseline"], 6),
            "saved": round(saved, 6),
            "saved_pct": pct,
            "by_route": {
                route: {
                    "requests": r["n"],
                    "realized": round(r["realized"], 6),
                    "baseline": round(r["baseline"], 6),
                    "saved": round(r["savings"], 6),
                    "tokens": r["tokens"],
                }
                for route, r in sorted(agg["by_route"].items())
            },
        }

    def totals(self) -> dict[str, float]:
        """All-time realized/baseline/saved — for the ``/metrics`` counters."""
        with self._lock:
            r = sum(b["realized"] for b in self.days.values())
            b = sum(d["baseline"] for d in self.days.values())
            return {"realized": round(r, 6), "baseline": round(b, 6), "saved": round(b - r, 6)}

    def spent(self, window: str = "day", *, today: date | None = None) -> float:
        """Realized spend in the current ``window`` — for budget enforcement (WF-ROADMAP-0006).

        ``"day"`` is today's UTC bucket; ``"month"`` is the current calendar month; anything
        else is all-time. Meaningful only when ``priced`` (else the figures are relative units).
        """
        today = today or _utc_today()
        with self._lock:
            if window == "day":
                bucket = self.days.get(today.isoformat())
                return round(bucket["realized"], 6) if bucket else 0.0
            if window == "month":
                prefix = today.isoformat()[:7]  # YYYY-MM
                return round(
                    sum(b["realized"] for k, b in self.days.items() if k.startswith(prefix)), 6
                )
            return round(sum(b["realized"] for b in self.days.values()), 6)

    # --- persistence (best-effort; never raise into the request path) ---------
    def to_dict(self) -> dict:
        with self._lock:
            return {"max_days": self.max_days, "priced": self.priced, "days": self.days}

    @classmethod
    def from_dict(cls, data: Mapping) -> SavingsLedger:
        led = cls(max_days=int(data.get("max_days", 400)), priced=bool(data.get("priced", True)))
        days = data.get("days")
        if isinstance(days, dict):
            led.days = {str(k): v for k, v in days.items() if isinstance(v, dict)}
        return led

    def save(self, path: str) -> None:
        from pathlib import Path

        tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX — no half-written report

    @classmethod
    def load(cls, path: str) -> SavingsLedger:
        from pathlib import Path

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
