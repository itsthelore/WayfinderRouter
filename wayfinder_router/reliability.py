"""Deterministic gateway reliability primitives (WF-ADR-0031, WF-DESIGN-0010).

Retry classification, a same-tier delivery plan, and a per-target circuit breaker —
all computed from observed transport outcomes, with no model call and no effect on the
scored decision (WF-ADR-0001). These change *how a request is delivered*, never *what
was decided*. Pure and clock/rng-injectable so the gateway's forward path stays testable
without a network.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# Upstream statuses worth retrying: rate limiting and transient server faults. Ordinary
# 4xx (bad request, auth) is the client's fault and is never retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable(status: int | None) -> bool:
    """Whether a forward attempt should be retried.

    ``status is None`` means a transport failure (timeout, connection refused) — always
    retryable. An HTTP status is retryable only for rate-limit / transient 5xx.
    """
    return status is None or status in RETRYABLE_STATUS


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
    (default ``time.monotonic``) so cooldown is testable. State is in-memory, per process;
    a shared store for multi-process deployments is a deliberate later option (WF-ADR-0031).
    """

    threshold: int = 5
    cooldown: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _fails: dict[str, int] = field(default_factory=dict, repr=False)
    _opened_at: dict[str, float] = field(default_factory=dict, repr=False)

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
            return
        count = self._fails.get(target, 0) + 1
        self._fails[target] = count
        if count >= self.threshold:
            self._opened_at[target] = self.clock()  # (re)open, restart the cooldown


def delivery_plan(
    primary: str, fallbacks: Iterable[str], breaker: CircuitBreaker | None = None
) -> list[str]:
    """Ordered, de-duplicated targets to try: the primary then its same-tier fallbacks.

    Targets whose breaker is open (still cooling down) are dropped. Order and identity come
    from config, never from the score (WF-ADR-0031: delivery, not decision). May be empty
    if every candidate is cooling down — the caller then fails fast.
    """
    plan: list[str] = []
    for target in (primary, *fallbacks):
        if target in plan:
            continue
        if breaker is not None and not breaker.allow(target):
            continue
        plan.append(target)
    return plan
