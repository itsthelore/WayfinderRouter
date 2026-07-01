"""Tests for the deterministic cost & savings engine (WF-DESIGN-0007)."""

from __future__ import annotations

from datetime import date

from wayfinder_router import pricing


def test_estimate_tokens_rough_and_empty():
    assert pricing.estimate_tokens("") == 0
    assert pricing.estimate_tokens("a") == 1  # floored to at least 1 for non-empty
    assert pricing.estimate_tokens("x" * 40) == 10


def test_price_table_uses_real_costs_when_present():
    costs, priced = pricing.price_table({"local": 0.0, "cloud": 0.009}, ["local", "cloud"])
    assert priced is True
    assert costs == {"local": 0.0, "cloud": 0.009}


def test_price_table_falls_back_to_relative_units():
    costs, priced = pricing.price_table({"local": None, "cloud": None}, ["local", "cloud"])
    assert priced is False
    assert costs == {"local": 0.2, "cloud": 1.0}  # cheapest 0.2 .. dearest 1.0


def test_price_table_empty():
    assert pricing.price_table({}, []) == ({}, False)


def test_table_version_stable_and_sensitive():
    a = pricing.table_version({"local": 0.0, "cloud": 0.009})
    b = pricing.table_version({"cloud": 0.009, "local": 0.0})  # order-independent
    c = pricing.table_version({"local": 0.0, "cloud": 0.01})
    assert a == b and a != c and len(a) == 12


def test_usage_tokens_prefers_upstream_usage():
    resp = {"usage": {"prompt_tokens": 120, "completion_tokens": 30}}
    assert pricing.usage_tokens(resp, prompt_text="ignored") == (120, 30, False)


def test_usage_tokens_splits_total_when_parts_missing():
    resp = {"usage": {"prompt_tokens": 100, "total_tokens": 130}}
    assert pricing.usage_tokens(resp) == (100, 30, False)


def test_usage_tokens_estimates_when_absent():
    pt, ct, estimated = pricing.usage_tokens({}, prompt_text="x" * 40, completion_text="y" * 80)
    assert (pt, ct, estimated) == (10, 20, True)


def test_turn_cost_savings_vs_dearest_baseline():
    costs = {"local": 0.0, "cloud": 0.009}
    tc = pricing.turn_cost("local", 1000, 0, costs, estimated=False)
    assert tc.realized == 0.0
    assert tc.baseline == 0.009  # always-frontier would have paid the dear rate
    assert tc.savings == 0.009


def test_turn_cost_negative_savings_on_escalation_is_kept_honest():
    costs = {"local": 0.0, "cloud": 0.009}
    # An escalated turn routed to cloud saves nothing vs the cloud baseline.
    tc = pricing.turn_cost("cloud", 1000, 1000, costs, estimated=True)
    assert tc.realized == tc.baseline == 0.018
    assert tc.savings == 0.0
    assert tc.estimated is True


def test_turn_cost_explicit_baseline_model():
    costs = {"small": 0.001, "mid": 0.005, "large": 0.02}
    tc = pricing.turn_cost("small", 2000, 0, costs, estimated=False, baseline="mid")
    assert tc.baseline == 0.01  # vs the named mid baseline, not the dearest large
    assert tc.savings == 0.008


def test_ledger_records_period_and_by_route():
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 0.009}
    day = date(2026, 6, 23)
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=day)
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=True), when=day)

    rep = led.period(today=day)
    assert rep["requests"] == 2
    assert rep["estimated_requests"] == 1
    assert rep["unit"] == "usd" and rep["priced"] is True
    assert rep["realized"] == 0.009  # 0 (local) + 0.009 (cloud)
    assert rep["baseline"] == 0.018  # both at dearest
    assert rep["saved"] == 0.009
    assert rep["saved_pct"] == 50.0
    assert rep["by_route"]["local"]["saved"] == 0.009
    assert rep["by_route"]["cloud"]["saved"] == 0.0


def test_from_dict_tolerates_an_old_or_partial_bucket():
    # An older-schema bucket (missing a field added later, e.g. estimated_n) or a partially-corrupted
    # one must not KeyError the next stats query — persistence is best-effort, never raising into reads.
    raw = {
        "priced": True,
        "days": {
            "2026-06-23": {  # no estimated_n at top level; the by_route sub-stat omits most fields
                "n": 2, "realized": 0.009, "baseline": 0.018, "savings": 0.009, "tokens": 1000,
                "by_route": {"local": {"n": 2, "savings": 0.009}},
            }
        },
    }
    led = pricing.SavingsLedger.from_dict(raw)
    rep = led.period(today=date(2026, 6, 23))  # would KeyError before the coerce
    assert rep["requests"] == 2
    assert rep["estimated_requests"] == 0  # defaulted by the coerce
    assert rep["by_route"]["local"]["saved"] == 0.009


def test_ledger_period_window_filters_old_days():
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 1.0}
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=date(2026, 6, 1))
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=date(2026, 6, 23))
    today = date(2026, 6, 23)
    assert led.period(days=1, today=today)["requests"] == 1  # only today
    assert led.period(days=30, today=today)["requests"] == 2  # both within 30d
    assert led.period(today=today)["requests"] == 2  # all-time


def test_ledger_prunes_to_max_days():
    led = pricing.SavingsLedger(max_days=2, priced=True)
    costs = {"a": 1.0}
    for d in (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)):
        led.record(pricing.turn_cost("a", 1000, 0, costs, estimated=False), when=d)
    assert sorted(led.days) == ["2026-06-02", "2026-06-03"]  # oldest dropped


def test_ledger_relative_units_flagged_not_priced():
    led = pricing.SavingsLedger(priced=False)
    costs, priced = pricing.price_table({"local": None, "cloud": None}, ["local", "cloud"])
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=True), when=date(2026, 6, 23))
    rep = led.period(today=date(2026, 6, 23))
    assert rep["priced"] is False and rep["unit"] == "relative"
    assert rep["saved"] == 0.8  # 1.0 baseline - 0.2 local, in relative units


def test_ledger_totals_for_metrics():
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 0.01}
    led.record(pricing.turn_cost("local", 2000, 0, costs, estimated=False), when=date(2026, 6, 23))
    totals = led.totals()
    assert totals == {"realized": 0.0, "baseline": 0.02, "saved": 0.02}


def test_ledger_save_load_round_trip(tmp_path):
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 0.009}
    led.record(pricing.turn_cost("cloud", 1000, 500, costs, estimated=False), when=date(2026, 6, 23))
    path = str(tmp_path / "savings.json")
    led.save(path)
    back = pricing.SavingsLedger.load(path)
    assert back.priced is True
    assert back.period(today=date(2026, 6, 23)) == led.period(today=date(2026, 6, 23))


def test_ledger_spent_by_window():
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 0.01}
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 6, 10))
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 6, 23))
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=date(2026, 5, 31))
    today = date(2026, 6, 23)
    assert led.spent("day", today=today) == 0.01  # just today's bucket
    assert led.spent("month", today=today) == 0.02  # both June days, not May
    assert led.spent("all", today=today) == 0.03  # everything


def test_ledger_per_key_attribution_and_spend():
    led = pricing.SavingsLedger(priced=True)
    costs = {"local": 0.0, "cloud": 0.01}
    day = date(2026, 6, 24)
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="team-a")
    led.record(pricing.turn_cost("cloud", 1000, 0, costs, estimated=False), when=day, vkey="team-b")
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=False), when=day)  # unattributed
    assert led.spent("day", today=day) == 0.02  # all keys + unattributed
    assert led.spent("day", vkey="team-a", today=day) == 0.01  # just team-a
    assert led.spent("day", vkey="absent", today=day) == 0.0
    rep = led.period(today=day)
    assert rep["by_key"]["team-a"]["realized"] == 0.01
    assert rep["by_key"]["team-b"]["requests"] == 1
    assert "team-a" in rep["by_key"] and "team-b" in rep["by_key"]
