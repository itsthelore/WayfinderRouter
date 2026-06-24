"""Tests for the deterministic RPM/TPM rate limiter (WF-ADR-0034)."""

from __future__ import annotations

from wayfinder_router import ratelimit


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_inert_without_any_limit():
    rl = ratelimit.RateLimiter()
    assert not rl.active()
    for _ in range(1000):
        assert rl.admit(now=0.0).allowed  # no cap configured -> always allowed


def test_rpm_admits_then_denies_with_retry_after():
    rl = ratelimit.RateLimiter(rpm=2, window=60.0)
    assert rl.admit(now=0.0).allowed
    assert rl.admit(now=0.0).allowed
    denied = rl.admit(now=0.0)
    assert not denied.allowed and denied.limit == "rpm"
    assert denied.retry_after == 60  # full window remains at t=0


def test_rpm_window_rolls():
    clock = _Clock()
    rl = ratelimit.RateLimiter(rpm=1, window=60.0, clock=clock)
    assert rl.admit().allowed
    assert not rl.admit().allowed  # second request in the same window
    clock.advance(60.0)  # next window
    assert rl.admit().allowed
    assert rl.stats()["requests"] == 1  # counter reset on roll


def test_tpm_denies_once_tokens_exceed():
    rl = ratelimit.RateLimiter(tpm=100, window=60.0)
    assert rl.admit(now=0.0).allowed  # tokens 0 < 100
    rl.add_tokens(100, now=0.0)
    denied = rl.admit(now=0.0)
    assert not denied.allowed and denied.limit == "tpm"


def test_add_tokens_is_noop_without_tpm():
    rl = ratelimit.RateLimiter(rpm=5)
    rl.add_tokens(999, now=0.0)
    assert rl.stats()["tokens"] == 0  # no TPM cap -> tokens are not tracked


def test_reconfigure_raises_the_cap():
    rl = ratelimit.RateLimiter(rpm=1, window=60.0)
    assert rl.admit(now=0.0).allowed
    assert not rl.admit(now=0.0).allowed
    rl.reconfigure(rpm=5, tpm=None, window=60.0)
    assert rl.admit(now=0.0).allowed  # the in-window count carries, but the higher cap admits


def test_rpm_and_tpm_together():
    clock = _Clock()
    rl = ratelimit.RateLimiter(rpm=10, tpm=50, window=60.0, clock=clock)
    assert rl.admit().allowed
    rl.add_tokens(50)
    d = rl.admit()
    assert not d.allowed and d.limit == "tpm"  # tpm trips before rpm here
    clock.advance(60.0)
    assert rl.admit().allowed and rl.stats()["tokens"] == 0
