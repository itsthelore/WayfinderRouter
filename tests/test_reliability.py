"""Tests for the deterministic gateway reliability primitives (WF-ADR-0031)."""

from __future__ import annotations

from wayfinder_router import reliability


def test_is_retryable_transport_and_transient_only():
    assert reliability.is_retryable(None) is True  # transport failure
    for status in (429, 500, 502, 503, 504):
        assert reliability.is_retryable(status) is True
    for status in (200, 400, 401, 403, 404, 422):
        assert reliability.is_retryable(status) is False  # client errors fail fast


def test_retry_delays_exponential_capped_with_injected_jitter():
    full = reliability.retry_delays(4, base=0.2, cap=1.0, rng=lambda: 1.0)
    assert full == [0.2, 0.4, 0.8, 1.0]  # 1.6 capped to 1.0; full jitter = the slot
    assert reliability.retry_delays(3, base=0.2, rng=lambda: 0.0) == [0.0, 0.0, 0.0]
    assert reliability.retry_delays(0) == []  # no retries -> no delays


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_breaker_opens_after_threshold_then_probes_after_cooldown():
    clock = _Clock()
    cb = reliability.CircuitBreaker(threshold=3, cooldown=30.0, clock=clock)
    assert cb.allow("cloud") is True
    cb.record("cloud", False)
    cb.record("cloud", False)
    assert cb.allow("cloud") is True  # 2 < 3, still closed
    cb.record("cloud", False)
    assert cb.is_open("cloud") is True  # tripped at the threshold
    clock.t = 29.0
    assert cb.allow("cloud") is False  # still cooling down
    clock.t = 30.0
    assert cb.allow("cloud") is True  # cooldown elapsed -> half-open probe


def test_breaker_success_closes_and_probe_failure_reopens():
    clock = _Clock()
    cb = reliability.CircuitBreaker(threshold=2, cooldown=10.0, clock=clock)
    cb.record("x", False)
    cb.record("x", False)
    assert cb.is_open("x")
    clock.t = 10.0
    assert cb.allow("x")  # half-open
    cb.record("x", False)  # probe fails -> reopen, cooldown restarts from now
    assert cb.is_open("x")
    clock.t = 20.0
    assert cb.allow("x")
    cb.record("x", True)  # probe succeeds -> closed
    assert cb.allow("x") and cb.is_open("x") is False


def test_breaker_is_per_target():
    cb = reliability.CircuitBreaker(threshold=1)
    cb.record("a", False)
    assert cb.is_open("a") and cb.allow("b")  # only "a" tripped


def test_delivery_plan_orders_dedups_and_drops_open_targets():
    cb = reliability.CircuitBreaker(threshold=1, cooldown=999.0)
    # No breaker: primary then fallbacks, de-duplicated.
    assert reliability.delivery_plan("cloud", ["cloud", "cloud-2", "local"]) == [
        "cloud", "cloud-2", "local",
    ]
    cb.record("cloud", False)  # trip the primary
    assert reliability.delivery_plan("cloud", ["cloud-2"], cb) == ["cloud-2"]  # primary dropped


def test_delivery_plan_empty_when_all_open():
    cb = reliability.CircuitBreaker(threshold=1, cooldown=999.0)
    cb.record("cloud", False)
    cb.record("cloud-2", False)
    assert reliability.delivery_plan("cloud", ["cloud-2"], cb) == []  # caller fails fast
