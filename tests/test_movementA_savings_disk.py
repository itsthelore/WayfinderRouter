"""Spec-first contract tests for the disk-backed ``SavingsLedger`` / ``Budget`` (Movement A).

Pins WF-DESIGN-0013 §7(d) "On-disk ``SavingsLedger`` / ``Budget`` day buckets" under **Contract
invariant 12**. The unchanged contract is exactly what ``tests/test_pricing.py`` (WF-DESIGN-0007)
pins: ``record`` accumulates into a per-day bucket (with per-route counts, ``estimated_n``, and
optional ``vkey`` attribution, WF-ADR-0035); ``period`` returns the report dict shape; ``totals``
returns all-time realized/baseline/saved; ``spent`` answers the current day/month/all window
(the number ``Budget`` enforcement reads); ``save``/``load``/``to_dict``/``from_dict`` round-trip;
``.days`` is bounded to ``max_days`` (old buckets pruned). §7d backs ``days`` with SQLite
(``buckets(day, scope, route, n, realized, baseline, savings, tokens, estimated_n)``) — ``record``
upserts, ``spent``/``period`` aggregate by SQL — while every return dict stays byte-identical.

``Budget`` (``wayfinder_router.gateway.Budget``) is a frozen ``(limit, window, on_breach)`` cap
whose ONLY ledger dependency is ``ledger.spent(window, vkey=...)`` (gateway.py:1982-1986). This
suite therefore proves "Budget enforcement unchanged" by asserting the disk ledger's ``spent()``
crosses the ``limit`` threshold identically to the in-RAM ledger — without importing the gateway
module (which pulls FastAPI), keeping the in-RAM parity halves runnable in isolation.

CHECKPOINT QUESTIONS (construction-surface assumptions — approve before a builder builds):
  1. SURFACE: pinned as a keyword-only ``db_path=<path>`` on the ``SavingsLedger`` dataclass
     selecting the SQLite backend; default (absent) keeps the current in-RAM dict + JSON snapshot.
     Is ``db_path=`` the chosen selector (vs. a ``backend="disk"`` field or a subclass)?
  2. ``.days`` OBSERVABILITY: ``tests/test_pricing.py`` asserts ``sorted(led.days) == [...]`` after
     pruning. The disk backend must therefore still expose ``.days`` as a mapping keyed by ISO day
     (a view over the ``buckets`` table). Confirm ``.days`` remains an observable day->bucket
     mapping rather than being replaced by an opaque handle.
  3. ``from_dict`` / ``load`` LANDING: pinned that both accept an optional keyword-only
     ``db_path=`` so a JSON snapshot (or the ``test_pricing`` old/partial bucket) can be
     materialized ONTO disk; absent ``db_path`` they build the in-RAM ledger as today. Confirm
     ``from_dict(raw, db_path=...)`` / ``load(path, db_path=...)`` is the seam.
  4. ``save`` TARGET: pinned that ``save(json_path)`` still writes the JSON snapshot (independent
     of ``db_path``), so the existing snapshot format is preserved for round-trip. Confirm.
"""

from __future__ import annotations

from datetime import date


from wayfinder_router import pricing


def _disk(tmp_path, name="ledger.db", **kw) -> "pricing.SavingsLedger":
    kw.setdefault("priced", True)
    return pricing.SavingsLedger(db_path=str(tmp_path / name), **kw)  # CHECKPOINT 1


# --- record / period / by_route parity (test_pricing through the disk path) -------------
def test_records_period_and_by_route(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 0.009}
    day = date(2026, 6, 23)
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=day)
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=True), when=day)
    rep = led.period(today=day)
    assert rep["requests"] == 2
    assert rep["estimated_requests"] == 1          # estimated_n attribution preserved
    assert rep["unit"] == "usd" and rep["priced"] is True
    assert rep["realized"] == 0.009 and rep["baseline"] == 0.018
    assert rep["saved"] == 0.009 and rep["saved_pct"] == 50.0
    assert rep["by_route"]["local"]["saved"] == 0.009
    assert rep["by_route"]["cloud"]["saved"] == 0.0


def test_period_window_filters_old_days(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 1.0}
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=date(2026, 6, 1))
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=date(2026, 6, 23))
    today = date(2026, 6, 23)
    assert led.period(days=1, today=today)["requests"] == 1     # only today
    assert led.period(days=30, today=today)["requests"] == 2    # both within 30d
    assert led.period(today=today)["requests"] == 2             # all-time


def test_prunes_to_max_days(tmp_path):
    led = _disk(tmp_path, max_days=2)
    costs = {"a": 1.0}
    for d in (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)):
        led.record(pricing.turn_cost("a", 1000, 0, costs, estimated=False), when=d)
    assert sorted(led.days) == ["2026-06-02", "2026-06-03"]     # oldest dropped (CHECKPOINT 2)


def test_relative_units_flagged_not_priced(tmp_path):
    led = _disk(tmp_path, priced=False)
    costs, _ = pricing.price_table({"local": None, "cloud": None}, ["local", "cloud"])
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=True), when=date(2026, 6, 23))
    rep = led.period(today=date(2026, 6, 23))
    assert rep["priced"] is False and rep["unit"] == "relative"
    assert rep["saved"] == 0.8                                   # 1.0 baseline - 0.2 local


def test_totals_for_metrics(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 0.01}
    led.record(pricing.turn_cost("local", 2000, 0, costs, estimated=False), when=date(2026, 6, 23))
    assert led.totals() == {"realized": 0.0, "baseline": 0.02, "saved": 0.02}


def test_spent_by_window(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 0.01}
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 6, 10))
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 6, 23))
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 5, 31))
    today = date(2026, 6, 23)
    assert led.spent("day", today=today) == 0.01     # just today's bucket
    assert led.spent("month", today=today) == 0.02   # both June days, not May
    assert led.spent("all", today=today) == 0.03     # everything


def test_per_key_attribution_and_spend(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 0.01}
    day = date(2026, 6, 24)
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="team-a")
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="team-b")
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=day)  # unattributed
    assert led.spent("day", today=day) == 0.02                  # all keys + unattributed
    assert led.spent("day", vkey="team-a", today=day) == 0.01   # scope="team-a"
    assert led.spent("day", vkey="absent", today=day) == 0.0
    rep = led.period(today=day)
    assert rep["by_key"]["team-a"]["realized"] == 0.01
    assert rep["by_key"]["team-b"]["requests"] == 1


# --- save/load + from_dict compatibility with an EXISTING JSON snapshot ------------------
def test_save_load_round_trip(tmp_path):
    led = _disk(tmp_path)
    costs = {"local": 0.0, "cloud": 0.009}
    led.record(pricing.turn_cost("cloud", 1000, 500, costs, estimated=False), when=date(2026, 6, 23))
    json_path = str(tmp_path / "savings.json")
    led.save(json_path)                                          # CHECKPOINT 4: JSON snapshot
    back = pricing.SavingsLedger.load(json_path, db_path=str(tmp_path / "reloaded.db"))  # CHECKPOINT 3
    assert back.priced is True
    assert back.period(today=date(2026, 6, 23)) == led.period(today=date(2026, 6, 23))


def test_from_dict_tolerates_an_old_or_partial_bucket(tmp_path):
    # The exact old/partial snapshot tests/test_pricing.py pins; the disk backend must coerce it
    # identically (no KeyError on the next stats query) when landed onto SQLite.
    raw = {
        "priced": True,
        "days": {
            "2026-06-23": {
                "n": 2, "realized": 0.009, "baseline": 0.018, "savings": 0.009, "tokens": 1000,
                "by_route": {"local": {"n": 2, "savings": 0.009}},
            }
        },
    }
    led = pricing.SavingsLedger.from_dict(raw, db_path=str(tmp_path / "from.db"))  # CHECKPOINT 3
    rep = led.period(today=date(2026, 6, 23))
    assert rep["requests"] == 2
    assert rep["estimated_requests"] == 0                        # defaulted by the coerce
    assert rep["by_route"]["local"]["saved"] == 0.009


def test_to_dict_shape_parity(tmp_path):
    led = _disk(tmp_path, max_days=42)
    led.record(pricing.turn_cost("cloud", 1000, 0, {"cloud": 0.01}, estimated=False), when=date(2026, 6, 23))
    d = led.to_dict()
    assert set(d) == {"max_days", "priced", "days"}
    assert d["max_days"] == 42 and d["priced"] is True
    assert d["days"]["2026-06-23"]["n"] == 1


# --- Budget enforcement via spent() unchanged (gateway.Budget reads only spent()) -------
def test_budget_threshold_via_spent_unchanged(tmp_path):
    # Budget(limit=L, window="day").on_breach fires when ledger.spent("day", vkey=...) >= L.
    # Proven disk-side without importing the gateway: the crossing point is identical to RAM.
    led = _disk(tmp_path)
    costs = {"cloud": 0.01}
    day = date(2026, 6, 23)
    limit = 0.02
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="k")
    assert led.spent("day", vkey="k", today=day) < limit         # under cap -> not enforced
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="k")
    assert led.spent("day", vkey="k", today=day) >= limit        # at cap -> breach path taken


# --- memory-vs-disk equivalence over one TurnCost sequence (Contract 12) ----------------
def _script(led):
    costs = {"local": 0.0, "cloud": 0.01}
    seq = [
        (pricing.turn_cost("local", 1000, 0, costs, estimated=False), date(2026, 6, 22), None),
        (pricing.turn_cost("cloud", 1000, 500, costs, estimated=True), date(2026, 6, 23), "team-a"),
        (pricing.turn_cost("cloud", 2000, 0, costs, estimated=False), date(2026, 6, 23), "team-b"),
        (pricing.turn_cost("local", 500, 500, costs, estimated=False), date(2026, 6, 23), "team-a"),
    ]
    for tc, when, vkey in seq:
        led.record(tc, when=when, vkey=vkey)
    today = date(2026, 6, 23)
    return {
        "period_all": led.period(today=today),
        "period_1d": led.period(days=1, today=today),
        "totals": led.totals(),
        "spent_day": led.spent("day", today=today),
        "spent_month": led.spent("month", today=today),
        "spent_key_a": led.spent("day", vkey="team-a", today=today),
        "days": sorted(led.days),
    }


def test_memory_and_disk_are_observably_identical(tmp_path):
    ram = pricing.SavingsLedger(priced=True)
    disk = _disk(tmp_path)
    assert _script(ram) == _script(disk)   # every report/total/spend/day view is byte-identical
