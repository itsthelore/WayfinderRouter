"""Characterization tests for Movement A's stateful surfaces (WF-DESIGN-0013 §7).

Additive-only (WF-ADR-0044): this file pins EXISTING behaviors of the gateway's
scale-fragile in-RAM surfaces (ResponseCache, feedback log, RateLimiter, CircuitBreaker,
SavingsLedger) that the current test suite does NOT assert, but that the upcoming disk
backends (§7 a-d: SQLite index + body log for the cache, a JSONL sidecar for feedback,
a best-effort ``state.db`` for limiter/breaker, SQLite day-buckets for the ledger) could
silently change. Every test here passes against the current tree and pins a gap no
existing test pins (each docstring names the gap). Pure stdlib + pytest, injectable
clocks, ``tmp_path`` — the repo idiom.
"""

from __future__ import annotations

from datetime import date

import pytest
from wayfinder_router import cache, feedback, pricing, ratelimit, reliability


class _Clock:
    """A settable monotonic clock (mirrors test_reliability / test_ratelimit style)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _entry(body: bytes = b"hello", at: float = 0.0) -> cache.CachedResponse:
    return cache.CachedResponse(
        status=200,
        content_type="application/json",
        body=body,
        prompt_tokens=3,
        completion_tokens=5,
        estimated=False,
        stored_at=at,
    )


# =============================================================================
# cache.py — ResponseCache byte accounting, dual ceilings, counter durability
# =============================================================================
def test_put_replace_same_key_reaccounts_bytes_exactly() -> None:
    """GAP: byte-accounting exactness on a same-key put-replace of a DIFFERENT size.

    Existing tests only put distinct keys. The disk index (§7a: ``sum(len)`` over rows)
    must, on upsert, subtract the old body length and add the new — never leak the old
    size. Here 100 -> 250 bytes under one key must leave exactly one entry of 250 bytes.
    """
    c = cache.ResponseCache(enabled=True, max_entries=10, max_bytes=10**9, ttl=0)
    c.put("k", _entry(body=b"x" * 100))
    assert c.stats()["bytes"] == 100
    c.put("k", _entry(body=b"y" * 250))  # replace under the SAME key
    s = c.stats()
    assert s["entries"] == 1 and s["bytes"] == 250  # not 350 — old size reclaimed


def test_eviction_obeys_both_ceilings_at_once() -> None:
    """GAP: the byte ceiling evicts even while the entry-count ceiling is not exceeded.

    Existing tests trip count OR bytes in isolation. With entries==max_entries (not over)
    but the byte sum over the ceiling, ``_evict_locked`` must still drop LRU rows — the
    disk backend's "``max_entries`` AND ``max_bytes``" bound (§7a) must hold jointly.
    """
    c = cache.ResponseCache(enabled=True, max_entries=3, max_bytes=250, ttl=0)
    c.put("a", _entry(body=b"a" * 100))
    c.put("b", _entry(body=b"b" * 100))
    c.put("c", _entry(body=b"c" * 100))  # entries==3 (at cap), bytes==300 (>250)
    s = c.stats()
    assert s["entries"] == 2 and s["bytes"] == 200  # byte ceiling forced one eviction
    assert c.get("a") is None  # the LRU is the casualty, not b/c


@pytest.mark.parametrize(("max_entries", "max_bytes"), [(0, 10**9), (10, 0)])
def test_zero_ceiling_stores_nothing(max_entries: int, max_bytes: int) -> None:
    """GAP: a non-positive ``max_entries`` or ``max_bytes`` makes ``put`` a hard no-op.

    Never asserted today. A disk backend configured with a zero bound must likewise
    persist nothing (no orphaned index row / body-log append).
    """
    c = cache.ResponseCache(enabled=True, max_entries=max_entries, max_bytes=max_bytes, ttl=0)
    c.put("k", _entry())
    assert c.stats()["entries"] == 0 and c.get("k") is None


def test_hit_miss_counters_survive_unrelated_reconfigure() -> None:
    """GAP: reconfigure that leaves the cache enabled preserves cumulative hits/misses.

    The docstring promises "unrelated changes keep ... the hit/miss counters"; no test
    pins it. A disk backend that rebuilds its index on reconfigure must not zero the
    (separately tracked) hit/miss tallies.
    """
    c = cache.ResponseCache(enabled=True, max_entries=8, max_bytes=10**9, ttl=0)
    c.put("k", _entry())
    c.get("k")  # hit
    c.get("absent")  # miss
    c.reconfigure(enabled=True, max_entries=4, max_bytes=10**9, ttl=123.0)
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1  # counters carried across reconfigure


def test_clear_resets_bytes_but_keeps_counters() -> None:
    """GAP: ``clear`` purges entries+bytes yet leaves cumulative hit/miss counters intact.

    Existing tests never combine clear with the counters. A disk backend truncating its
    body log on ``clear`` must reset the byte tally to 0 without touching hits/misses.
    """
    c = cache.ResponseCache(enabled=True, ttl=0)
    c.put("k", _entry(body=b"z" * 40))
    c.get("k")  # hit
    c.get("absent")  # miss
    c.clear()
    s = c.stats()
    assert s["entries"] == 0 and s["bytes"] == 0  # bodies purged, byte tally reset
    assert s["hits"] == 1 and s["misses"] == 1  # lifetime counters untouched by clear


def test_disabled_get_does_not_count_a_miss() -> None:
    """GAP: a lookup on a DISABLED cache returns None WITHOUT incrementing ``misses``.

    ``get`` short-circuits before the miss bookkeeping when disabled. A disk backend must
    reproduce this: an off cache is inert, not a miss-counting probe.
    """
    c = cache.ResponseCache(enabled=False, ttl=0)
    assert c.get("anything") is None
    assert c.stats()["misses"] == 0  # disabled != miss


def test_cache_key_default_str_fallback_is_deterministic() -> None:
    """GAP: ``cache_key`` folds a non-JSON-native body value via ``default=str`` (no raise).

    ``json.dumps(..., default=str)`` lets an exotic value (here a ``date``) be keyed instead
    of raising TypeError. Never exercised today. Two identical bodies must yield the same
    64-hex digest, and a differing exotic value must split the key — so the disk index's
    ``key TEXT PRIMARY KEY`` stays a stable, collision-free string.
    """
    body = {"messages": [{"role": "user", "content": "hi"}], "meta": date(2026, 1, 1)}
    k1 = cache.cache_key("m-cloud", body)
    k2 = cache.cache_key("m-cloud", dict(body))
    assert k1 == k2 and len(k1) == 64  # default=str did not raise and is deterministic
    other = {"messages": [{"role": "user", "content": "hi"}], "meta": date(2026, 1, 2)}
    assert cache.cache_key("m-cloud", other) != k1  # a differing exotic value splits the key


def test_is_cacheable_deterministic_point_float_and_int_edges() -> None:
    """GAP: the exact determinism-gate edges — 0.0 vs None temperature, top_p 1.0, n==0.

    Existing tests use ``temperature: 0`` (int) and reject 0.7 / n:2. Here: float ``0.0``
    and ``top_p: 1.0`` are the deterministic point (accepted), an omitted param is accepted,
    and ``n: 0`` is rejected (``!= 1``) — pinning precisely which requests are eligible to
    ever enter a rehoused cache.
    """
    msgs = [{"role": "user", "content": "hi"}]
    assert cache.is_cacheable({"messages": msgs, "temperature": 0.0, "top_p": 1.0, "n": 1})
    assert cache.is_cacheable({"messages": msgs, "temperature": None, "top_p": None})
    assert not cache.is_cacheable({"messages": msgs, "n": 0})  # n != 1 -> rejected
    assert not cache.is_cacheable({"messages": msgs, "temperature": -0.5})  # any != 0 rejected


# =============================================================================
# feedback.py — read tolerance and on-disk byte format
# =============================================================================
def test_read_labels_skips_blank_and_whitespace_lines_and_strips(tmp_path) -> None:
    """GAP: ``read_labels`` skips blank / whitespace-only lines and strips padded JSON.

    Round-trip tests only write clean lines. The §7b sidecar-paged reader must reproduce
    this exact tolerance: interleaved empty lines are dropped, order is preserved, and a
    JSON line with surrounding whitespace still parses.
    """
    log = tmp_path / "fb.jsonl"
    log.write_text(
        '{"text": "a", "label": "x"}\n'
        "\n"
        "   \n"
        '{"text": "b", "label": "y"}\n'
        "\t\n"
        '  {"text": "c", "label": "z"}  \n',
        encoding="utf-8",
    )
    assert feedback.read_labels(str(log)) == [
        {"text": "a", "label": "x"},
        {"text": "b", "label": "y"},
        {"text": "c", "label": "z"},
    ]


def test_record_label_writes_raw_utf8_not_ascii_escapes(tmp_path) -> None:
    """GAP: ``record_label`` appends with ``ensure_ascii=False`` — raw UTF-8, not \\uXXXX.

    The on-disk byte format is load-bearing: §7b keeps the JSONL "verbatim" and offsets a
    sidecar against it, so the exact bytes (literal non-ASCII, no escape expansion) must be
    preserved. Never asserted at the byte level today.
    """
    log = tmp_path / "fb.jsonl"
    feedback.record_label(str(log), "café ☃", "local")  # café ☃
    raw = log.read_text(encoding="utf-8")
    assert "café ☃" in raw  # literal codepoints on disk
    assert "\\u" not in raw  # not ASCII-escaped
    assert feedback.read_labels(str(log)) == [{"text": "café ☃", "label": "local"}]


@pytest.mark.parametrize(("text", "label"), [(123, "x"), ("hi", 456)])
def test_record_label_rejects_non_string_fields(tmp_path, text, label) -> None:
    """GAP: non-string (but truthy) ``text``/``label`` raise ValueError.

    Existing parametrization covers empty-string and ``None`` only. A non-string int must
    also be refused so the append path (and its rehoused index) never records a malformed
    row.
    """
    with pytest.raises(ValueError):
        feedback.record_label(str(tmp_path / "fb.jsonl"), text, label)


# =============================================================================
# ratelimit.py — admit/deny accounting, retry_after arithmetic, token clamping
# =============================================================================
def test_denied_admit_does_not_increment_the_request_count() -> None:
    """GAP: a denied ``admit`` leaves the request count exactly at the cap (no increment).

    Existing tests observe the boolean denial but never assert the count stops climbing.
    The persistent-limiter row (§7c ``ratelimit.requests``) must not be bumped by rejected
    calls, or admission math drifts across a restart.
    """
    rl = ratelimit.RateLimiter(rpm=1, window=60.0)
    assert rl.admit(now=0.0).allowed
    assert not rl.admit(now=0.0).allowed
    assert not rl.admit(now=0.0).allowed  # repeated denials
    assert rl.stats()["requests"] == 1  # count pinned at the cap, never beyond


def test_retry_after_is_ceil_to_window_end_with_floor_of_one() -> None:
    """GAP: ``retry_after`` = ceil(seconds to window roll), never below 1.

    Existing test only checks the whole-window value (60 at t=0). A fractional clock must
    round UP, and the final fractional second must still report 1 — the exact ``Retry-After``
    the rehoused limiter emits.
    """
    clock = _Clock()
    rl = ratelimit.RateLimiter(rpm=1, window=60.0, clock=clock)
    clock.t = 0.0
    assert rl.admit().allowed  # fill window 0
    clock.t = 30.0
    assert rl.admit().retry_after == 30  # exactly 30s remain
    clock.t = 59.5
    assert rl.admit().retry_after == 1  # ceil(0.5) -> 1
    clock.t = 59.999
    assert rl.admit().retry_after == 1  # floor of 1 near the boundary


def test_add_tokens_clamps_negative_and_truncates_float() -> None:
    """GAP: ``add_tokens`` records ``max(0, int(n))`` — negatives add 0, floats truncate.

    Never asserted. A rehoused token counter must fold exactly the same clamped integer, or
    a spurious negative/float would corrupt the persisted TPM tally.
    """
    rl = ratelimit.RateLimiter(tpm=100, window=60.0)
    rl.add_tokens(-50, now=0.0)  # clamped to 0
    rl.add_tokens(5, now=0.0)
    rl.add_tokens(3.9, now=0.0)  # int(3.9) == 3
    assert rl.stats()["tokens"] == 8  # 0 + 5 + 3


def test_add_tokens_rolls_the_window_before_recording() -> None:
    """GAP: ``add_tokens`` in a new window resets the counter before adding (rolls first).

    ``add_tokens`` calls ``_roll_locked`` too — tokens from a prior window do not bleed into
    the next. The §7c per-window row must be keyed by ``window_id`` so a token write in a
    fresh window starts from 0.
    """
    clock = _Clock()
    rl = ratelimit.RateLimiter(tpm=100, window=60.0, clock=clock)
    rl.add_tokens(40)  # window 0
    clock.advance(60.0)
    rl.add_tokens(40)  # window 1 — must reset, not accumulate to 80
    assert rl.stats()["tokens"] == 40


def test_snapshot_remaining_never_negative_after_cap_lowered() -> None:
    """GAP: ``snapshot`` remaining is clamped to 0 when in-window count exceeds a new cap.

    A hot reconfigure that lowers ``rpm`` below the already-admitted count must report
    ``remaining == 0`` (``max(0, ...)``), not a negative header. The persisted count (§7c)
    can legitimately exceed a freshly-lowered cap.
    """
    rl = ratelimit.RateLimiter(rpm=5, window=60.0, clock=lambda: 0.0)
    for _ in range(3):
        assert rl.admit().allowed  # requests == 3
    rl.reconfigure(rpm=2, tpm=None, window=60.0)  # cap now below the count
    snap = rl.snapshot()
    assert snap is not None and snap[0] == 2 and snap[1] == 0  # remaining floored at 0


# =============================================================================
# reliability.py — auth-failure classification, breaker counter reset, backoff
# =============================================================================
def test_is_auth_failure_matrix() -> None:
    """GAP: ``is_auth_failure`` is entirely unpinned by the current suite.

    It classifies 401/403/407 as "target unusable" (a breaker failure, not retryable) and
    everything else — including transport ``None`` and ordinary 4xx/5xx — as not an auth
    failure. The persistent breaker (§7c) counts these as failures, so the classifier is
    load-bearing.
    """
    for status in (401, 403, 407):
        assert reliability.is_auth_failure(status) is True
    for status in (400, 404, 429, 500, 502, 200, None):
        assert reliability.is_auth_failure(status) is False


def test_breaker_success_resets_the_failure_counter() -> None:
    """GAP: a success zeroes the consecutive-fail counter, so reopening needs a FULL threshold.

    Existing tests show a probe-success closing an open breaker, but never that the fail
    COUNT is reset — i.e. that a single later failure does not immediately reopen. The §7c
    ``breaker.fails`` row must be cleared on success, not merely the ``opened_at`` marker.
    """
    clock = _Clock()  # frozen at 0.0; cooldown large so 'open' stays open
    cb = reliability.CircuitBreaker(threshold=2, cooldown=100.0, clock=clock)
    cb.record("t", False)
    cb.record("t", False)
    assert cb.is_open("t")  # opened at the threshold
    cb.record("t", True)  # success: clears BOTH fails and opened_at
    assert cb.allow("t") and not cb.is_open("t")
    cb.record("t", False)  # a single fresh failure...
    assert cb.allow("t")  # ...does NOT reopen — the counter restarted from 0
    cb.record("t", False)  # a full threshold of new failures reopens
    assert cb.is_open("t")


def test_retry_delays_negative_is_empty_and_jitter_stays_in_slot() -> None:
    """GAP: negative ``retries`` yields ``[]`` and mid-range jitter scales the slot exactly.

    Existing tests cover 0 retries and the jitter extremes (rng 0.0 / 1.0) only. A negative
    count (via ``max(0, retries)``) is empty, and an intermediate rng lands each delay in
    ``[0, slot]`` rounded to 6 dp — the backoff schedule is pure and rehousing-independent
    but unpinned at these points.
    """
    assert reliability.retry_delays(-3) == []
    got = reliability.retry_delays(2, base=0.2, cap=5.0, rng=lambda: 0.5)
    assert got == [0.1, 0.2]  # 0.2*0.5, 0.4*0.5 — within [0, slot]


# =============================================================================
# pricing.py — SavingsLedger aggregation, pruning, persistence, per-key windows
# =============================================================================
def _costs() -> dict[str, float]:
    return {"local": 0.0, "cloud": 0.01}


def test_period_saved_pct_is_zero_when_baseline_is_zero() -> None:
    """GAP: ``saved_pct`` guards against a zero baseline (no ZeroDivisionError -> 0.0).

    Every existing period test has a non-zero baseline. A period of only free turns (dearest
    cost 0) must report ``saved_pct == 0.0`` — the SQL-aggregated §7d report must reproduce
    the divide-by-zero guard.
    """
    led = pricing.SavingsLedger(priced=True)
    tc = pricing.turn_cost("local", 1000, 0, {"local": 0.0}, estimated=False)  # dearest == 0
    led.record(tc, when=date(2026, 6, 23))
    rep = led.period(today=date(2026, 6, 23))
    assert rep["baseline"] == 0.0 and rep["saved"] == 0.0 and rep["saved_pct"] == 0.0


def test_turn_cost_clamps_negative_token_counts() -> None:
    """GAP: ``turn_cost`` clamps negative prompt/completion counts to 0 before pricing.

    Never asserted. A malformed negative usage must not produce negative cost or tokens —
    the value that feeds the (rehoused) ledger row is ``max(0, ...)`` on both axes.
    """
    tc = pricing.turn_cost("local", -50, -10, {"local": 1.0, "cloud": 2.0}, estimated=False)
    assert tc.prompt_tokens == 0 and tc.completion_tokens == 0
    assert tc.realized == 0.0 and tc.baseline == 0.0 and tc.savings == 0.0


def test_period_by_route_and_by_key_are_sorted() -> None:
    """GAP: ``period`` emits ``by_route`` / ``by_key`` in sorted-key order.

    The summary comprehensions iterate ``sorted(...)``; no test pins the ordering. A SQL
    ``GROUP BY`` in §7d would need an explicit ``ORDER BY`` to match this — pin the contract
    so a route/key insertion order can't leak through.
    """
    led = pricing.SavingsLedger(priced=True)
    costs = {"alpha": 0.0, "zebra": 0.02, "mid": 0.01}
    day = date(2026, 6, 23)
    led.record(pricing.turn_cost("zebra", 1000, 0, costs, estimated=False), when=day, vkey="z-key")
    led.record(pricing.turn_cost("alpha", 1000, 0, costs, estimated=False), when=day, vkey="a-key")
    rep = led.period(today=day)
    assert list(rep["by_route"]) == ["alpha", "zebra"]
    assert list(rep["by_key"]) == ["a-key", "z-key"]


def test_totals_sums_realized_and_baseline_across_days() -> None:
    """GAP: ``totals`` aggregates over ALL day buckets (multi-day), not just one.

    Existing totals test records a single day. The §7d ``totals`` (feeding the ``/metrics``
    counters) must ``SUM`` every retained day and derive ``saved = baseline - realized``.
    """
    led = pricing.SavingsLedger(priced=True)
    led.record(pricing.turn_cost("local", 1000, 0, _costs(), estimated=False), when=date(2026, 6, 1))
    led.record(pricing.turn_cost("local", 1000, 0, _costs(), estimated=False), when=date(2026, 6, 2))
    assert led.totals() == {"realized": 0.0, "baseline": 0.02, "saved": 0.02}


def test_estimated_n_aggregates_across_days_in_period() -> None:
    """GAP: ``estimated_requests`` sums the ``estimated_n`` of every in-window day.

    Existing test counts estimated turns within a single day. A multi-day period must add
    each bucket's ``estimated_n`` — the §7d per-row ``estimated_n`` column, aggregated.
    """
    led = pricing.SavingsLedger(priced=True)
    led.record(pricing.turn_cost("cloud", 1000, 0, _costs(), estimated=True), when=date(2026, 6, 1))
    led.record(pricing.turn_cost("cloud", 1000, 0, _costs(), estimated=False), when=date(2026, 6, 2))
    rep = led.period(today=date(2026, 6, 2))
    assert rep["requests"] == 2 and rep["estimated_requests"] == 1


def test_to_from_dict_round_trips_unpriced_and_custom_max_days() -> None:
    """GAP: ``to_dict``/``from_dict`` preserve ``priced=False`` and a non-default ``max_days``.

    The existing save/load test uses ``priced=True`` and the default cap. The §7d
    persistence contract must round-trip the relative-unit flag and the retention bound, and
    reproduce the identical period report.
    """
    led = pricing.SavingsLedger(max_days=9, priced=False)
    costs, _ = pricing.price_table({"local": None, "cloud": None}, ["local", "cloud"])
    led.record(pricing.turn_cost("local", 1000, 0, costs, estimated=True), when=date(2026, 6, 23))
    back = pricing.SavingsLedger.from_dict(led.to_dict())
    assert back.max_days == 9 and back.priced is False
    assert back.period(today=date(2026, 6, 23)) == led.period(today=date(2026, 6, 23))
    assert back.period(today=date(2026, 6, 23))["unit"] == "relative"


def test_spent_per_key_across_month_and_all_windows() -> None:
    """GAP: ``spent`` with a ``vkey`` honors the ``month``/``all`` windows (and absent keys -> 0).

    The existing per-key test only checks the ``day`` window. §7d must SUM a single key's
    realized cost across the calendar month and all-time, and yield 0.0 for an unknown key.
    """
    led = pricing.SavingsLedger(priced=True)
    led.record(pricing.turn_cost("cloud", 1000, 0, _costs(), estimated=False),
               when=date(2026, 6, 10), vkey="team-a")
    led.record(pricing.turn_cost("cloud", 1000, 0, _costs(), estimated=False),
               when=date(2026, 6, 20), vkey="team-a")
    led.record(pricing.turn_cost("cloud", 1000, 0, _costs(), estimated=False),
               when=date(2026, 5, 31), vkey="team-a")  # prior month
    today = date(2026, 6, 20)
    assert led.spent("month", vkey="team-a", today=today) == 0.02  # two June days
    assert led.spent("all", vkey="team-a", today=today) == 0.03  # every day
    assert led.spent("month", vkey="absent", today=today) == 0.0  # unknown key
