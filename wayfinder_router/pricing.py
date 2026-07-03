"""Deterministic cost and savings accounting for the gateway (WF-DESIGN-0007).

This is pure, offline arithmetic layered on top of the routing decisions the gateway
already makes: it turns token counts plus a configured price table into a persisted,
per-period *savings* report — what each request actually cost on its chosen tier versus
what it would have cost had every request gone to the dearest ("frontier") tier. No model
is called, no key is used, nothing touches the network (WF-ADR-0001); the scored core never
imports this module.

Token counts come from the upstream ``usage`` block when present, otherwise from a rough
~4-chars/token estimate (and the turn is then flagged ``estimated``). When no real
``cost_per_1k`` metadata is configured, the table falls back to relative units (cheapest
0.2 .. dearest 1.0) and the report is flagged ``priced = False`` so the numbers are never
mistaken for dollars.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

# Rough character-to-token ratio used when an upstream omits ``usage``. The TUI shares this
# constant for its own estimate; anything derived from it is labelled approximate.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate a token count from character length (~4 chars/token).

    Constraint: an empty string is the only input that yields 0. Any non-empty text yields
    at least 1 (via ``max``), and longer text uses integer division — a floor, never a round
    or ceil. This differs deliberately from the TUI's own estimator, which returns 1 for "".
    """
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def price_table(
    model_costs: Mapping[str, float | None], tier_ladder: Iterable[str]
) -> tuple[dict[str, float], bool]:
    """Build ``({model: cost_per_1k}, priced)`` from configured costs, or a relative fallback.

    When any real cost is configured, only the non-``None`` costs make the table (``None``-cost
    models are dropped, not zeroed) and ``priced`` is ``True``. When every cost is ``None`` (or
    the mapping is empty) the table falls back to relative units laid across the tier ladder —
    cheapest 0.2 .. dearest 1.0 — and ``priced`` is ``False`` so the figures can't be read as
    dollars. An empty ladder falls back to the mapping's own key order; if that is empty too the
    result is an empty table.
    """
    real = {name: float(c) for name, c in model_costs.items() if c is not None}
    if real:
        return real, True
    ladder = list(tier_ladder) or list(model_costs)
    if not ladder:
        return {}, False
    lo, hi = 0.2, 1.0
    # Guard the divisor so a single-element ladder yields {only: 0.2} rather than dividing by 0.
    step = (hi - lo) / max(1, len(ladder) - 1)
    # Relative units are rounded to 3dp (unlike the 6dp used everywhere else in this module).
    return {m: round(lo + i * step, 3) for i, m in enumerate(ladder)}, False


def table_version(costs: Mapping[str, float]) -> str:
    """A short, stable fingerprint of the price table so a saved report stays auditable.

    Keys are sorted before serialization, so the digest is order-independent but value-sensitive.
    The compact ``(",", ":")`` separators are load-bearing — any other whitespace changes the hash.
    """
    blob = json.dumps({k: costs[k] for k in sorted(costs)}, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def usage_tokens(
    response: object, *, prompt_text: str = "", completion_text: str = ""
) -> tuple[int, int, bool]:
    """Resolve ``(prompt_tokens, completion_tokens, estimated)``, preferring the upstream usage.

    An exact ``usage`` block wins and is reported with ``estimated = False``; the fallback text
    estimate is only used when no usable usage is present, and is flagged ``estimated = True``.
    The literal ``isinstance(x, int)`` checks are kept intentionally (``bool`` is an ``int``
    subclass, but no test probes that corner).
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
    """One request's realized, baseline, and savings figures, from token counts x price table."""

    route: str
    realized: float
    baseline: float
    savings: float
    prompt_tokens: int
    completion_tokens: int
    estimated: bool


def turn_cost(
    route: str,
    prompt_tokens: int,
    completion_tokens: int,
    costs: Mapping[str, float],
    *,
    estimated: bool,
    baseline: str | None = None,
) -> TurnCost:
    """Cost of one turn on ``route`` versus the counterfactual on the baseline (dearest) tier.

    ``baseline`` names the "always-frontier" reference model; when omitted it is the dearest
    configured tier, and an unknown ``route`` also falls back to the dearest cost. Savings is
    ``baseline - realized`` (kept honest — negative on an escalated turn). Every money figure is
    rounded to 6dp; stored token counts are clamped non-negative.
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
    # Module-level so it stays the natural monkeypatch seam for "what day is it".
    return datetime.now(timezone.utc).date()


def _empty_route() -> dict:
    return {"n": 0, "realized": 0.0, "baseline": 0.0, "savings": 0.0, "tokens": 0}


def _empty_bucket() -> dict:
    bucket = _empty_route()
    bucket["estimated_n"] = 0
    bucket["by_route"] = {}
    bucket["by_key"] = {}  # per virtual-key attribution (WF-ADR-0035); empty when keys unused
    return bucket


def _num(value: object, default: float | int) -> float | int:
    """Return ``value`` if it is a real (non-``bool``) number, else ``default`` — tolerant load."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return default


def _coerce_bucket(raw: Mapping) -> dict:
    """Normalize a persisted bucket to the current schema so a later read can index it directly.

    Every top-level field is filled from :func:`_empty_bucket` and every ``by_route`` / ``by_key``
    sub-stat from :func:`_empty_route`, keeping only numeric values. An older or partially corrupted
    file (e.g. missing ``estimated_n``) then cannot ``KeyError`` a later stats query — persistence
    stays best-effort and never raises into the request path.
    """
    bucket = _empty_bucket()
    for f in ("n", "realized", "baseline", "savings", "tokens", "estimated_n"):
        bucket[f] = _num(raw.get(f), bucket[f])
    for field_name in ("by_route", "by_key"):
        sub = raw.get(field_name)
        if isinstance(sub, Mapping):
            for name, rstats in sub.items():
                if isinstance(rstats, Mapping):
                    route = _empty_route()
                    for f in route:
                        route[f] = _num(rstats.get(f), route[f])
                    bucket[field_name][str(name)] = route
    return bucket


def _accumulate(target: dict, tc: TurnCost) -> None:
    # Money figures are re-rounded to 6dp at every step so a long run cannot drift the goldens.
    target["n"] += 1
    target["realized"] = round(target["realized"] + tc.realized, 6)
    target["baseline"] = round(target["baseline"] + tc.baseline, 6)
    target["savings"] = round(target["savings"] + tc.savings, 6)
    target["tokens"] += tc.prompt_tokens + tc.completion_tokens


@dataclass
class SavingsLedger:
    """Daily-bucket accumulator of realized / baseline / savings plus per-route and per-key counts.

    In-memory and bounded to ``max_days`` (oldest buckets pruned). A lock guards updates so a
    best-effort disk snapshot stays internally consistent even though the gateway's event loop is
    single-threaded. ``priced`` records whether the figures are dollars or relative units.
    """

    max_days: int = 400
    priced: bool = True
    days: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, tc: TurnCost, *, when: date | None = None, vkey: str | None = None) -> None:
        """Record a turn into its day bucket, optionally attributing it to a virtual key.

        ``vkey`` adds the turn to that key's per-day tally (WF-ADR-0035); ``None`` leaves the key
        tally untouched (no empty entry is created).
        """
        day = (when or _utc_today()).isoformat()
        with self._lock:
            bucket = self.days.setdefault(day, _empty_bucket())
            _accumulate(bucket, tc)
            if tc.estimated:
                bucket["estimated_n"] += 1
            route = bucket["by_route"].setdefault(tc.route, _empty_route())
            _accumulate(route, tc)
            if vkey is not None:
                kstats = bucket.setdefault("by_key", {}).setdefault(vkey, _empty_route())
                _accumulate(kstats, tc)
            self._prune_locked()

    def _prune_locked(self) -> None:
        # ISO date keys sort lexicographically == chronologically, so the smallest keys are oldest.
        if len(self.days) > self.max_days:
            for key in sorted(self.days)[: len(self.days) - self.max_days]:
                del self.days[key]

    def period(self, days: int | None = None, *, today: date | None = None) -> dict:
        """Aggregate the last ``days`` buckets (``None`` = all-time) into a report dict.

        The window is inclusive: ``days=1`` is today only, ``days=30`` the last 30 days including
        today. Floats are re-rounded to 6dp at each aggregation step.
        """
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
                    agg[f] = (
                        round(agg[f] + bucket[f], 6)
                        if isinstance(agg[f], float)
                        else agg[f] + bucket[f]
                    )
                for route, rstats in bucket["by_route"].items():
                    tgt = agg["by_route"].setdefault(route, _empty_route())
                    for f in ("n", "realized", "baseline", "savings", "tokens"):
                        tgt[f] = (
                            round(tgt[f] + rstats[f], 6)
                            if isinstance(tgt[f], float)
                            else tgt[f] + rstats[f]
                        )
                for vkey, kstats in bucket.get("by_key", {}).items():
                    tgt = agg["by_key"].setdefault(vkey, _empty_route())
                    for f in ("n", "realized", "baseline", "savings", "tokens"):
                        tgt[f] = (
                            round(tgt[f] + kstats[f], 6)
                            if isinstance(tgt[f], float)
                            else tgt[f] + kstats[f]
                        )
            return self._summary(agg, days)

    def _summary(self, agg: dict, days: int | None) -> dict:
        saved = agg["savings"]
        baseline = agg["baseline"]
        # saved_pct is the one figure at 1dp (all money figures are 6dp); 0.0 when no baseline.
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
            "by_key": {
                vkey: {
                    "requests": r["n"],
                    "realized": round(r["realized"], 6),
                    "baseline": round(r["baseline"], 6),
                    "saved": round(r["savings"], 6),
                    "tokens": r["tokens"],
                }
                for vkey, r in sorted(agg["by_key"].items())
            },
        }

    def totals(self) -> dict[str, float]:
        """All-time realized / baseline / saved — for the ``/metrics`` counters."""
        with self._lock:
            realized = sum(b["realized"] for b in self.days.values())
            baseline = sum(b["baseline"] for b in self.days.values())
            return {
                "realized": round(realized, 6),
                "baseline": round(baseline, 6),
                "saved": round(baseline - realized, 6),
            }

    def spent(
        self, window: str = "day", *, vkey: str | None = None, today: date | None = None
    ) -> float:
        """Realized spend in the current ``window`` — for budget enforcement (WF-ROADMAP-0006).

        ``"day"`` is today's UTC bucket; ``"month"`` is the current calendar month; anything else
        is all-time. With ``vkey`` set the figure is that key's spend only. Meaningful only when
        ``priced`` (otherwise the numbers are relative units).
        """
        today = today or _utc_today()

        def _realized(bucket: dict) -> float:
            if vkey is None:
                return bucket["realized"]
            return bucket.get("by_key", {}).get(vkey, {}).get("realized", 0.0)

        with self._lock:
            if window == "day":
                bucket = self.days.get(today.isoformat())
                return round(_realized(bucket), 6) if bucket else 0.0
            if window == "month":
                prefix = today.isoformat()[:7]  # YYYY-MM
                return round(
                    sum(_realized(b) for k, b in self.days.items() if k.startswith(prefix)), 6
                )
            return round(sum(_realized(b) for b in self.days.values()), 6)

    # --- persistence: best-effort; never raise into the request path ---------------------
    def to_dict(self) -> dict:
        with self._lock:
            return {"max_days": self.max_days, "priced": self.priced, "days": self.days}

    @classmethod
    def from_dict(cls, data: Mapping) -> SavingsLedger:
        led = cls(max_days=int(data.get("max_days", 400)), priced=bool(data.get("priced", True)))
        days = data.get("days")
        if isinstance(days, dict):
            # Rebuild only from Mapping-shaped buckets, each coerced so partial/old files load clean.
            led.days = {
                str(k): _coerce_bucket(v) for k, v in days.items() if isinstance(v, Mapping)
            }
        return led

    def save(self, path: str) -> None:
        from pathlib import Path

        tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX — no half-written report is ever visible

    @classmethod
    def load(cls, path: str) -> SavingsLedger:
        from pathlib import Path

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
