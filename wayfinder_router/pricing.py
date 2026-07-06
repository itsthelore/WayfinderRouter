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
import logging
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

CHARS_PER_TOKEN = 4  # the rough estimate the TUI uses too; anything derived is labelled ~

logger = logging.getLogger(__name__)


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
    b["by_key"] = {}  # per virtual-key attribution (WF-ADR-0035); empty when keys are unused
    return b


def _num(value: object, default: float | int) -> float | int:
    """``value`` if it is a real (non-bool) number, else ``default`` — for tolerant load."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return default


def _coerce_bucket(raw: Mapping) -> dict:
    """Normalize a persisted bucket to the current schema so reads can index it directly.

    Fills any missing top-level field from :func:`_empty_bucket` and each ``by_route`` / ``by_key``
    sub-stat from :func:`_empty_route`, keeping only numeric values. An older-schema file (missing a
    field added later, e.g. ``estimated_n``) or a partially-corrupted bucket then can't ``KeyError``
    a later stats query — persistence stays best-effort and never raises into the request path.
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
    target["n"] += 1
    target["realized"] = round(target["realized"] + tc.realized, 6)
    target["baseline"] = round(target["baseline"] + tc.baseline, 6)
    target["savings"] = round(target["savings"] + tc.savings, 6)
    target["tokens"] += tc.prompt_tokens + tc.completion_tokens


@dataclass
class SavingsLedger:
    """Daily-bucket accumulator of realized/baseline/savings + per-route counts.

    Bounded to ``max_days`` (old buckets are dropped). A lock guards updates so a best-effort
    disk snapshot stays internally consistent; the gateway's event loop is single-threaded, but
    persistence and tests may touch it from elsewhere. ``priced`` records whether the figures are
    dollars (real ``cost_per_1k``) or relative units. By default the buckets live in memory with a
    JSON snapshot; passing keyword-only ``db_path=`` backs them with a SQLite ``buckets`` table
    instead (WF-DESIGN-0013 §7d, WF-ROADMAP-0012) so queries stay flat against request count while
    every return dict remains byte-identical to the in-memory path.
    """

    max_days: int = 400
    priced: bool = True
    _days: dict[str, dict] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    db_path: str | None = field(default=None, kw_only=True)
    _conn: sqlite3.Connection | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # ``db_path`` selects the SQLite backing; absent keeps the in-memory dict (default posture).
        if self.db_path is not None:
            self._conn = self._open_db(self.db_path)

    @staticmethod
    def _open_db(path: str) -> sqlite3.Connection:
        # WAL + a single lock-serialized connection: committed rows survive reconstruction without
        # relying on ``close()``, and no fsync-per-record on the gateway hot path (WF-ADR-0032).
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS buckets("
            "day TEXT, scope TEXT, route TEXT, n INTEGER, realized REAL, baseline REAL, "
            "savings REAL, tokens INTEGER, estimated_n INTEGER, PRIMARY KEY(day, scope, route))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS buckets_day ON buckets(day)")
        conn.commit()
        return conn

    @property
    def days(self) -> dict[str, dict]:
        """The ISO-day -> bucket mapping (a live view; materialized from SQLite when disk-backed)."""
        if self.db_path is None:
            return self._days
        return self._materialize_days()

    @days.setter
    def days(self, value: dict[str, dict]) -> None:
        # Only the in-memory path assigns ``days`` wholesale (``from_dict``); disk rows are canonical.
        self._days = value

    def record(self, tc: TurnCost, *, when: date | None = None, vkey: str | None = None) -> None:
        """Record a turn into its day bucket; optionally attribute it to a virtual key.

        ``vkey`` adds the turn to that key's per-day tally (WF-ADR-0035), which powers per-key
        budgets and the per-key savings breakdown. ``None`` leaves the key tally untouched.
        """
        day = (when or _utc_today()).isoformat()
        if self._conn is not None:
            self._record_disk(day, tc, vkey)
            return
        with self._lock:
            bucket = self._days.setdefault(day, _empty_bucket())
            _accumulate(bucket, tc)
            if tc.estimated:
                bucket["estimated_n"] += 1
            route = bucket["by_route"].setdefault(tc.route, _empty_route())
            _accumulate(route, tc)
            if vkey is not None:
                kstats = bucket.setdefault("by_key", {}).setdefault(vkey, _empty_route())
                _accumulate(kstats, tc)
            self._prune_locked()

    def _record_disk(self, day: str, tc: TurnCost, vkey: str | None) -> None:
        # Best-effort: a SQLite/OS write error must never propagate into the request path.
        try:
            with self._lock:
                conn = self._conn
                assert conn is not None
                self._upsert_locked(conn, day, "", "", tc, 1 if tc.estimated else 0)
                self._upsert_locked(conn, day, "", tc.route, tc, 0)
                if vkey is not None:
                    self._upsert_locked(conn, day, vkey, "", tc, 0)
                self._prune_disk_locked(conn)
                conn.commit()
        except (sqlite3.Error, OSError):  # pragma: no cover - defensive, tests use valid paths
            logger.warning("SavingsLedger.record disk write failed", exc_info=True)

    @staticmethod
    def _upsert_locked(
        conn: sqlite3.Connection, day: str, scope: str, route: str, tc: TurnCost, est_add: int
    ) -> None:
        # ``scope=""``/``route=""`` is the day-total row persisted in its OWN right, so a partial
        # snapshot reports the stored top-level figures rather than a sum re-derived from by_route
        # rows (WF-ROADMAP-0012 audit correction). ``round(...,6)`` mirrors ``_accumulate`` step for
        # step so SQL-backed values equal the in-memory sequential-round.
        conn.execute(
            "INSERT INTO buckets(day,scope,route,n,realized,baseline,savings,tokens,estimated_n) "
            "VALUES(?,?,?,1,?,?,?,?,?) "
            "ON CONFLICT(day,scope,route) DO UPDATE SET n=n+1, "
            "realized=round(realized+excluded.realized,6), "
            "baseline=round(baseline+excluded.baseline,6), "
            "savings=round(savings+excluded.savings,6), "
            "tokens=tokens+excluded.tokens, estimated_n=estimated_n+excluded.estimated_n",
            (
                day, scope, route, tc.realized, tc.baseline, tc.savings,
                tc.prompt_tokens + tc.completion_tokens, est_add,
            ),
        )

    def _prune_locked(self) -> None:
        if len(self._days) > self.max_days:
            for key in sorted(self._days)[: len(self._days) - self.max_days]:
                del self._days[key]

    def _prune_disk_locked(self, conn: sqlite3.Connection) -> None:
        rows = [r[0] for r in conn.execute("SELECT DISTINCT day FROM buckets ORDER BY day")]
        if len(rows) > self.max_days:
            drop = rows[: len(rows) - self.max_days]
            conn.executemany("DELETE FROM buckets WHERE day=?", [(d,) for d in drop])

    def period(self, days: int | None = None, *, today: date | None = None) -> dict:
        """Aggregate the last ``days`` buckets (``None`` = all-time) into a report dict."""
        today = today or _utc_today()
        if self._conn is not None:
            return self._period_disk(days, today)
        with self._lock:
            keys = sorted(self._days)
            if days is not None:
                cutoff = today.toordinal() - (days - 1)
                keys = [k for k in keys if date.fromisoformat(k).toordinal() >= cutoff]
            agg = _empty_bucket()
            for key in keys:
                bucket = self._days[key]
                for f in ("n", "realized", "baseline", "savings", "tokens", "estimated_n"):
                    agg[f] = round(agg[f] + bucket[f], 6) if isinstance(agg[f], float) else agg[f] + bucket[f]
                for route, rstats in bucket["by_route"].items():
                    tgt = agg["by_route"].setdefault(route, _empty_route())
                    for f in ("n", "realized", "baseline", "savings", "tokens"):
                        tgt[f] = round(tgt[f] + rstats[f], 6) if isinstance(tgt[f], float) else tgt[f] + rstats[f]
                for vkey, kstats in bucket.get("by_key", {}).items():
                    tgt = agg["by_key"].setdefault(vkey, _empty_route())
                    for f in ("n", "realized", "baseline", "savings", "tokens"):
                        tgt[f] = round(tgt[f] + kstats[f], 6) if isinstance(tgt[f], float) else tgt[f] + kstats[f]
            return self._summary(agg, days)

    def _period_disk(self, days: int | None, today: date) -> dict:
        # Indexed SQL GROUP BY over the retained buckets: bounded by max_days x routes x keys, flat
        # against request count (the curve the in-memory O(buckets) loop could not hold).
        where = ""
        params: list[object] = []
        if days is not None:
            cutoff = date.fromordinal(today.toordinal() - (days - 1)).isoformat()
            where = " AND day>=?"
            params = [cutoff]
        with self._lock:
            conn = self._conn
            assert conn is not None
            total = conn.execute(
                "SELECT COALESCE(SUM(n),0),COALESCE(SUM(realized),0.0),COALESCE(SUM(baseline),0.0),"
                "COALESCE(SUM(savings),0.0),COALESCE(SUM(tokens),0),COALESCE(SUM(estimated_n),0) "
                "FROM buckets WHERE scope='' AND route=''" + where,
                params,
            ).fetchone()
            agg = _empty_bucket()
            agg["n"], agg["tokens"], agg["estimated_n"] = int(total[0]), int(total[4]), int(total[5])
            agg["realized"] = round(total[1], 6)
            agg["baseline"] = round(total[2], 6)
            agg["savings"] = round(total[3], 6)
            for row in conn.execute(
                "SELECT route,SUM(n),SUM(realized),SUM(baseline),SUM(savings),SUM(tokens) "
                "FROM buckets WHERE scope='' AND route<>''" + where + " GROUP BY route ORDER BY route",
                params,
            ):
                agg["by_route"][row[0]] = self._row_route(row)
            for row in conn.execute(
                "SELECT scope,SUM(n),SUM(realized),SUM(baseline),SUM(savings),SUM(tokens) "
                "FROM buckets WHERE scope<>''" + where + " GROUP BY scope ORDER BY scope",
                params,
            ):
                agg["by_key"][row[0]] = self._row_route(row)
            return self._summary(agg, days)

    @staticmethod
    def _row_route(row: tuple) -> dict:
        return {
            "n": int(row[1]),
            "realized": round(row[2], 6),
            "baseline": round(row[3], 6),
            "savings": round(row[4], 6),
            "tokens": int(row[5]),
        }

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
        """All-time realized/baseline/saved — for the ``/metrics`` counters."""
        if self._conn is not None:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COALESCE(SUM(realized),0.0),COALESCE(SUM(baseline),0.0) "
                    "FROM buckets WHERE scope='' AND route=''"
                ).fetchone()
                r, b = row[0], row[1]
                return {"realized": round(r, 6), "baseline": round(b, 6), "saved": round(b - r, 6)}
        with self._lock:
            r = sum(b["realized"] for b in self._days.values())
            b = sum(d["baseline"] for d in self._days.values())
            return {"realized": round(r, 6), "baseline": round(b, 6), "saved": round(b - r, 6)}

    def spent(
        self, window: str = "day", *, vkey: str | None = None, today: date | None = None
    ) -> float:
        """Realized spend in the current ``window`` — for budget enforcement (WF-ROADMAP-0006).

        ``"day"`` is today's UTC bucket; ``"month"`` is the current calendar month; anything
        else is all-time. With ``vkey`` set, returns only that virtual key's spend in the window
        (WF-ADR-0035). Meaningful only when ``priced`` (else the figures are relative units).
        """
        today = today or _utc_today()
        if self._conn is not None:
            # ``scope=""`` day-total row for the unattributed spend; ``scope=vkey`` for a key.
            scope = "" if vkey is None else vkey
            base = "SELECT COALESCE(SUM(realized),0.0) FROM buckets WHERE scope=? AND route=''"
            with self._lock:
                conn = self._conn
                if window == "day":
                    row = conn.execute(base + " AND day=?", (scope, today.isoformat())).fetchone()
                elif window == "month":
                    row = conn.execute(
                        base + " AND day LIKE ?", (scope, today.isoformat()[:7] + "%")
                    ).fetchone()
                else:
                    row = conn.execute(base, (scope,)).fetchone()
                return round(row[0], 6)

        def _realized(bucket: dict) -> float:
            if vkey is None:
                return bucket["realized"]
            return bucket.get("by_key", {}).get(vkey, {}).get("realized", 0.0)

        with self._lock:
            if window == "day":
                bucket = self._days.get(today.isoformat())
                return round(_realized(bucket), 6) if bucket else 0.0
            if window == "month":
                prefix = today.isoformat()[:7]  # YYYY-MM
                return round(
                    sum(_realized(b) for k, b in self._days.items() if k.startswith(prefix)), 6
                )
            return round(sum(_realized(b) for b in self._days.values()), 6)

    # --- persistence (best-effort; never raise into the request path) ---------
    def _materialize_days(self) -> dict[str, dict]:
        with self._lock:
            conn = self._conn
            assert conn is not None
            return self._read_days(conn)

    @staticmethod
    def _read_days(conn: sqlite3.Connection) -> dict[str, dict]:
        # Reconstruct the nested day-bucket shape from rows (day-total row + by_route + by_key) so
        # ``to_dict``/``.days`` stay byte-identical to the in-memory dict.
        result: dict[str, dict] = {}
        for day, n, r, b, s, t, e in conn.execute(
            "SELECT day,n,realized,baseline,savings,tokens,estimated_n "
            "FROM buckets WHERE scope='' AND route=''"
        ):
            bucket = _empty_bucket()
            bucket["n"], bucket["realized"], bucket["baseline"] = int(n), r, b
            bucket["savings"], bucket["tokens"], bucket["estimated_n"] = s, int(t), int(e)
            result[day] = bucket
        for day, route, n, r, b, s, t in conn.execute(
            "SELECT day,route,n,realized,baseline,savings,tokens "
            "FROM buckets WHERE scope='' AND route<>''"
        ):
            result.setdefault(day, _empty_bucket())["by_route"][route] = {
                "n": int(n), "realized": r, "baseline": b, "savings": s, "tokens": int(t)
            }
        for day, scope, n, r, b, s, t in conn.execute(
            "SELECT day,scope,n,realized,baseline,savings,tokens FROM buckets WHERE scope<>''"
        ):
            result.setdefault(day, _empty_bucket())["by_key"][scope] = {
                "n": int(n), "realized": r, "baseline": b, "savings": s, "tokens": int(t)
            }
        return result

    def to_dict(self) -> dict:
        if self._conn is not None:
            with self._lock:
                conn = self._conn
                return {
                    "max_days": self.max_days,
                    "priced": self.priced,
                    "days": self._read_days(conn),
                }
        with self._lock:
            return {"max_days": self.max_days, "priced": self.priced, "days": self._days}

    @classmethod
    def from_dict(cls, data: Mapping, *, db_path: str | None = None) -> SavingsLedger:
        led = cls(
            max_days=int(data.get("max_days", 400)),
            priced=bool(data.get("priced", True)),
            db_path=db_path,
        )
        raw_days = data.get("days")
        coerced: dict[str, dict] = {}
        if isinstance(raw_days, dict):
            coerced = {
                str(k): _coerce_bucket(v) for k, v in raw_days.items() if isinstance(v, Mapping)
            }
        if led._conn is None:
            led._days = coerced
            return led
        led._land_snapshot(coerced)
        return led

    def _land_snapshot(self, coerced: dict[str, dict]) -> None:
        # Land a JSON snapshot onto SQLite; the day-total row carries the stored top-level figures.
        try:
            with self._lock:
                conn = self._conn
                assert conn is not None
                for day, bucket in coerced.items():
                    self._insert_bucket_locked(conn, day, bucket)
                self._prune_disk_locked(conn)
                conn.commit()
        except (sqlite3.Error, OSError):  # pragma: no cover - defensive, tests use valid paths
            logger.warning("SavingsLedger.from_dict disk landing failed", exc_info=True)

    @staticmethod
    def _insert_bucket_locked(conn: sqlite3.Connection, day: str, bucket: dict) -> None:
        sql = (
            "INSERT OR REPLACE INTO buckets"
            "(day,scope,route,n,realized,baseline,savings,tokens,estimated_n) "
            "VALUES(?,?,?,?,?,?,?,?,?)"
        )
        conn.execute(
            sql,
            (
                day, "", "", bucket["n"], bucket["realized"], bucket["baseline"],
                bucket["savings"], bucket["tokens"], bucket["estimated_n"],
            ),
        )
        for route, r in bucket["by_route"].items():
            conn.execute(
                sql,
                (day, "", route, r["n"], r["realized"], r["baseline"], r["savings"], r["tokens"], 0),
            )
        for scope, r in bucket["by_key"].items():
            conn.execute(
                sql,
                (day, scope, "", r["n"], r["realized"], r["baseline"], r["savings"], r["tokens"], 0),
            )

    def save(self, path: str) -> None:
        from pathlib import Path

        tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX — no half-written report

    @classmethod
    def load(cls, path: str, *, db_path: str | None = None) -> SavingsLedger:
        from pathlib import Path

        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")), db_path=db_path
        )
