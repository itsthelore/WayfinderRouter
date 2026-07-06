"""Spec-first contract tests for persistent RateLimiter / CircuitBreaker state (Movement A).

Pins WF-DESIGN-0013 §7(c) "Persistent rate-limiter / breaker state" under **Contract invariant
12**. The unchanged contracts are exactly what ``tests/test_ratelimit.py`` (WF-ADR-0034) and
``tests/test_reliability.py`` (WF-ADR-0031) pin: ``RateLimiter.admit`` fixed-window
admit/deny/``retry_after`` and window roll on ``floor(now/window)``, ``add_tokens``/``stats``/
``snapshot``; ``CircuitBreaker.allow``/``is_open``/``record`` opening at ``threshold`` consecutive
failures and half-open reopen after ``cooldown``. §7c backs both with a single best-effort
``state.db`` written on each transition inside the existing lock and **reloaded on construction**,
so counters and opened breakers survive a restart — with the SAME "best-effort, never raise into
the request path" posture as ``SavingsLedger.save/load``.

Injected clocks are used throughout (as the existing suites do): monotonic wall values are not
meaningful across a real process restart, so persistence is exercised within one process by
reconstructing against a controlled clock — which is the honest unit-level proof of the
reload-restores-state contract.

CHECKPOINT QUESTIONS (construction-surface assumptions — approve before a builder builds):
  1. SURFACE: pinned as a keyword-only ``state_path=<path>`` field on BOTH the ``RateLimiter`` and
     ``CircuitBreaker`` dataclasses; the row is (re)loaded in ``__post_init__`` and rewritten on
     each transition inside the existing lock. Is a per-object ``state_path=`` the chosen seam, or
     a shared ``StateStore`` handle injected instead? Least-invasive per-object path is assumed.
  2. SHARED vs SEPARATE DB: §7c describes ONE ``state.db`` with a ``ratelimit`` table and a
     ``breaker`` table. These tests give each object its own ``state_path`` for isolation; a
     shared file with two tables must behave identically. Confirm the file may be shared.
  3. RATELIMIT SCOPE KEY: the ``ratelimit`` table is keyed by ``scope``; v1 is gateway-wide (one
     scope). Assumed a single implicit scope per ``RateLimiter`` — confirm no scope kwarg is
     required at construction.
  4. NEVER-RAISE DEGRADE: pinned STRICTEST reading of "best-effort" — a corrupt or unwritable
     ``state.db`` is swallowed silently and the limiter/breaker degrades to pure in-memory (fresh
     empty state), never raising on construction OR on any transition. Confirm silent degrade
     (vs. a logged warning that still never raises).
"""

from __future__ import annotations

from pathlib import Path

from wayfinder_router import ratelimit, reliability


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _state(tmp_path, name="state.db") -> str:
    return str(tmp_path / name)


# --- RateLimiter: contract unchanged through the disk path ------------------------------
def test_ratelimit_admit_deny_retry_after_unchanged(tmp_path):
    rl = ratelimit.RateLimiter(rpm=2, window=60.0, state_path=_state(tmp_path))
    assert rl.admit(now=0.0).allowed
    assert rl.admit(now=0.0).allowed
    denied = rl.admit(now=0.0)
    assert not denied.allowed and denied.limit == "rpm" and denied.retry_after == 60


def test_ratelimit_window_rolls_through_disk(tmp_path):
    clock = _Clock()
    rl = ratelimit.RateLimiter(rpm=1, window=60.0, clock=clock, state_path=_state(tmp_path))
    assert rl.admit().allowed
    assert not rl.admit().allowed          # second request in the same window
    clock.advance(60.0)                     # next window (floor(now/window) rolls)
    assert rl.admit().allowed
    assert rl.stats()["requests"] == 1      # counter reset on roll


# --- RateLimiter: window counts survive reconstruction ----------------------------------
def test_ratelimit_request_count_survives_reconstruct(tmp_path):
    p = _state(tmp_path)
    rl = ratelimit.RateLimiter(rpm=3, window=60.0, clock=lambda: 0.0, state_path=p)
    rl.admit(now=0.0)
    rl.admit(now=0.0)                        # two requests reserved in window 0
    rl2 = ratelimit.RateLimiter(rpm=3, window=60.0, clock=lambda: 0.0, state_path=p)
    assert rl2.stats()["requests"] == 2      # reloaded on construction
    assert rl2.admit(now=0.0).allowed        # third admits
    assert not rl2.admit(now=0.0).allowed    # fourth denied -> count truly restored


def test_ratelimit_tokens_survive_reconstruct(tmp_path):
    p = _state(tmp_path)
    rl = ratelimit.RateLimiter(tpm=100, window=60.0, clock=lambda: 0.0, state_path=p)
    rl.add_tokens(80, now=0.0)
    rl2 = ratelimit.RateLimiter(tpm=100, window=60.0, clock=lambda: 0.0, state_path=p)
    assert rl2.stats()["tokens"] == 80
    rl2.add_tokens(20, now=0.0)
    assert not rl2.admit(now=0.0).allowed and rl2.admit(now=0.0).limit == "tpm"


def test_ratelimit_snapshot_parity_through_disk(tmp_path):
    rl = ratelimit.RateLimiter(rpm=5, window=60.0, clock=lambda: 0.0, state_path=_state(tmp_path))
    assert rl.snapshot() == (5, 5, 60)
    rl.admit()
    rl.admit()
    assert rl.snapshot() == (5, 3, 60)       # two consumed -> 3 remaining


# --- CircuitBreaker: contract unchanged + opened breaker survives reconstruction --------
def test_breaker_opens_after_threshold_unchanged(tmp_path):
    clock = _Clock()
    cb = reliability.CircuitBreaker(threshold=3, cooldown=30.0, clock=clock, state_path=_state(tmp_path))
    cb.record("cloud", False)
    cb.record("cloud", False)
    assert cb.allow("cloud") is True         # 2 < 3, still closed
    cb.record("cloud", False)
    assert cb.is_open("cloud") is True        # tripped at the threshold
    clock.t = 29.0
    assert cb.allow("cloud") is False         # still cooling down
    clock.t = 30.0
    assert cb.allow("cloud") is True          # cooldown elapsed -> half-open probe


def test_breaker_open_state_survives_reconstruct(tmp_path):
    p = _state(tmp_path)
    clock = _Clock()
    cb = reliability.CircuitBreaker(threshold=2, cooldown=10.0, clock=clock, state_path=p)
    cb.record("x", False)
    cb.record("x", False)                     # opened_at stamped at clock.t == 0.0
    assert cb.is_open("x")
    cb2 = reliability.CircuitBreaker(threshold=2, cooldown=10.0, clock=clock, state_path=p)
    assert cb2.is_open("x")                    # opened_at reloaded on construction
    clock.t = 10.0
    assert cb2.allow("x")                      # cooldown math intact across the reload (half-open)


def test_breaker_reopen_semantics_through_disk(tmp_path):
    clock = _Clock()
    cb = reliability.CircuitBreaker(threshold=2, cooldown=10.0, clock=clock, state_path=_state(tmp_path))
    cb.record("x", False)
    cb.record("x", False)
    assert cb.is_open("x")
    clock.t = 10.0
    assert cb.allow("x")                       # half-open probe
    cb.record("x", False)                      # probe fails -> reopen, cooldown restarts from now
    assert cb.is_open("x")
    clock.t = 20.0
    cb.record("x", True)                       # success closes the breaker
    assert cb.allow("x") and cb.is_open("x") is False


# --- best-effort, never raise into the request path (CHECKPOINT 4) ----------------------
def test_corrupt_state_degrades_to_memory_silently(tmp_path):
    p = _state(tmp_path)
    Path(p).write_bytes(b"this is not a sqlite database")  # planted corruption
    # Construction must not raise; it degrades to fresh in-memory state.
    rl = ratelimit.RateLimiter(rpm=2, window=60.0, clock=lambda: 0.0, state_path=p)
    assert rl.stats()["requests"] == 0
    assert rl.admit(now=0.0).allowed           # operates normally in-memory
    cb = reliability.CircuitBreaker(threshold=1, clock=lambda: 0.0, state_path=p)
    assert cb.allow("a") is True


def test_unwritable_state_never_raises_on_transition(tmp_path):
    # A state_path that can never be opened as a DB (it is a directory) must be swallowed:
    # construction and every transition degrade to in-memory and never raise.
    bad_dir = tmp_path / "state_is_a_dir"
    bad_dir.mkdir()
    p = str(bad_dir)
    rl = ratelimit.RateLimiter(rpm=1, window=60.0, clock=lambda: 0.0, state_path=p)
    assert rl.admit(now=0.0).allowed
    assert not rl.admit(now=0.0).allowed        # in-memory limiting still correct
    cb = reliability.CircuitBreaker(threshold=1, clock=lambda: 0.0, state_path=p)
    cb.record("t", False)                        # transition write fails silently
    assert cb.is_open("t")                        # in-memory breaker still correct
