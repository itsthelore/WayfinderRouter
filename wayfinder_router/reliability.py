"""Deterministic gateway reliability primitives (WF-ADR-0031, WF-DESIGN-0010).

Retry classification, a same-tier delivery plan, and a per-target circuit breaker —
all computed from observed transport outcomes, with no model call and no effect on the
scored decision (WF-ADR-0001). These change *how a request is delivered*, never *what
was decided*. Pure and clock/rng-injectable so the gateway's forward path stays testable
without a network.
"""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# Upstream statuses worth retrying: rate limiting and transient server faults. Ordinary
# 4xx (bad request, auth) is the client's fault and is never retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Statuses that mean *this target is unusable* rather than *this request was bad*: a
# missing/expired/forbidden upstream key. They are not retryable (retrying a bad key is
# pointless), but — unlike an ordinary client 4xx — they must count as a breaker *failure*
# so a stale key eventually opens the breaker and delivery degrades (WF-ADR-0031).
AUTH_FAILURE_STATUS = frozenset({401, 403, 407})


def is_retryable(status: int | None) -> bool:
    """Whether a forward attempt should be retried.

    ``status is None`` means a transport failure (timeout, connection refused) — always
    retryable. An HTTP status is retryable only for rate-limit / transient 5xx.
    """
    return status is None or status in RETRYABLE_STATUS


def is_auth_failure(status: int | None) -> bool:
    """Whether ``status`` means the upstream target is unusable (bad/expired/forbidden key)."""
    return status in AUTH_FAILURE_STATUS


def retry_delays(
    retries: int,
    *,
    base: float = 0.2,
    cap: float = 5.0,
    rng: Callable[[], float] = random.random,
) -> list[float]:
    """Backoff delays (seconds) before each retry: exponential, capped, full-jitter.

    ``retries`` is the number of *re*-tries, so the schedule has that many entries (the
    first attempt has no preceding delay). Jitter is over ``[0, slot]`` via ``rng``,
    which is injectable for deterministic tests.
    """
    delays: list[float] = []
    for i in range(max(0, retries)):
        slot = min(cap, base * (2**i))
        delays.append(round(slot * rng(), 6))
    return delays


@dataclass
class CircuitBreaker:
    """Per-target breaker: open after ``threshold`` consecutive failures; probe after ``cooldown``.

    Pure bookkeeping over success/failure outcomes — no model call. ``clock`` is injectable
    (default ``time.monotonic``) so cooldown is testable. State is in-memory per process by
    default; optionally (WF-DESIGN-0013 §7c, WF-ROADMAP-0012), when ``state_path`` is set each
    target's ``(fails, opened_at)`` is best-effort persisted to a shared ``state.db`` on every
    ``record`` and reloaded on construction, so an open breaker can survive a restart. The
    guarantee is never-raise: any ``sqlite3.Error``/``OSError`` degrades silently to pure
    in-memory operation. LIMITATION: a persisted ``opened_at`` is a raw ``clock()`` reading;
    ``time.monotonic`` restarts from an arbitrary base across a real process restart, so a
    reloaded cooldown is only meaningful relative to the *same* clock (round-trips under an
    injected clock; undefined across a real monotonic restart). Documented, not solved.
    """

    threshold: int = 5
    cooldown: float = 30.0
    clock: Callable[[], float] = time.monotonic
    state_path: str | None = field(default=None, kw_only=True)
    _fails: dict[str, int] = field(default_factory=dict, repr=False)
    _opened_at: dict[str, float] = field(default_factory=dict, repr=False)
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Best-effort open + reload of persisted per-target state; degrade to in-memory."""
        if self.state_path is None:
            return
        try:
            conn = sqlite3.connect(self.state_path, isolation_level=None)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS breaker ("
                "target TEXT PRIMARY KEY, fails INTEGER, opened_at REAL)"
            )
            rows = conn.execute("SELECT target, fails, opened_at FROM breaker").fetchall()
        except (sqlite3.Error, OSError):
            self._conn = None
            return
        self._conn = conn
        for target, fails, opened_at in rows:
            self._fails[target] = int(fails)
            if opened_at is not None:  # NULL opened_at means the target is closed
                self._opened_at[target] = float(opened_at)

    def allow(self, target: str) -> bool:
        """True if ``target`` may be tried now — closed, or cooldown elapsed (half-open probe)."""
        opened = self._opened_at.get(target)
        if opened is None:
            return True
        return self.clock() - opened >= self.cooldown

    def is_open(self, target: str) -> bool:
        """True while ``target`` is tripped and still cooling down (the inverse of allow)."""
        return not self.allow(target)

    def record(self, target: str, ok: bool) -> None:
        """Fold one attempt's outcome in: success closes the breaker; failures may open it."""
        if ok:
            self._fails.pop(target, None)
            self._opened_at.pop(target, None)
            self._persist_success(target)
            return
        count = self._fails.get(target, 0) + 1
        self._fails[target] = count
        if count >= self.threshold:
            self._opened_at[target] = self.clock()  # (re)open, restart the cooldown
        self._persist_failure(target)

    def _persist_failure(self, target: str) -> None:
        """Rewrite one target's row after a failure; best-effort, never raises."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO breaker(target, fails, opened_at) VALUES (?, ?, ?) "
                "ON CONFLICT(target) DO UPDATE SET fails=excluded.fails, opened_at=excluded.opened_at",
                (target, self._fails[target], self._opened_at.get(target)),
            )
        except (sqlite3.Error, OSError):
            self._conn = None

    def _persist_success(self, target: str) -> None:
        """Clear one target's row after a success; best-effort, never raises."""
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM breaker WHERE target = ?", (target,))
        except (sqlite3.Error, OSError):
            self._conn = None


def delivery_plan(
    primary: str,
    fallbacks: Iterable[str],
    breaker: CircuitBreaker | None = None,
    allow: Callable[[str], bool] | None = None,
) -> list[str]:
    """Ordered, de-duplicated targets to try: the primary then its same-tier fallbacks.

    Targets whose breaker is open (still cooling down), or that ``allow`` rejects (a
    deterministic pre-call check, e.g. context-window fit), are dropped. Order and identity
    come from config, never from the score (WF-ADR-0031: delivery, not decision). May be
    empty if every candidate is filtered — the caller then fails fast.
    """
    plan: list[str] = []
    for target in (primary, *fallbacks):
        if target in plan:
            continue
        if breaker is not None and not breaker.allow(target):
            continue
        if allow is not None and not allow(target):
            continue
        plan.append(target)
    return plan


# Cross-tier failover policies (WF-ADR-0031). Default is conservative: stay on the chosen
# tier (only its configured alternate endpoints), changing neither cost nor answer quality.
FAILOVER_POLICIES = ("same-tier", "degrade", "escalate")


def failover_candidates(chosen: str, ladder: Iterable[str], policy: str) -> list[str]:
    """Cross-tier targets to try after same-tier endpoints are exhausted, per ``policy``.

    ``ladder`` is the tier model names cheapest→dearest. ``degrade`` walks to cheaper tiers
    (nearest-cheaper first; never raises cost); ``escalate`` walks to dearer tiers
    (nearest-dearer first; opt-in, raises cost); ``same-tier`` (and an off-ladder ``chosen``)
    yields nothing. Identity/order from the ladder, not the score.
    """
    seq = list(ladder)
    if policy not in ("degrade", "escalate") or chosen not in seq:
        return []
    idx = seq.index(chosen)
    if policy == "degrade":
        return seq[:idx][::-1]  # cheaper tiers, nearest first
    return seq[idx + 1:]  # escalate: dearer tiers, nearest first


def precheck_ok(estimated_tokens: int, context_window: int | None) -> bool:
    """A deterministic pre-call check: does the estimated prompt fit the target's window?

    ``None`` means no configured limit (always OK). Used to skip a target that would
    certainly fail on length before spending the call (WF-ADR-0031).
    """
    return context_window is None or estimated_tokens <= context_window

