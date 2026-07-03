"""Deterministic gateway reliability primitives (WF-ADR-0031, WF-DESIGN-0010).

Retry classification, a de-duplicated delivery plan, cross-tier failover ordering, and a
per-target circuit breaker — all derived from observed transport outcomes, with no model call and
no effect on the scored decision (WF-ADR-0001). These change *how a request is delivered*, never
*what was decided*. Everything is pure and clock/rng-injectable, so the gateway's forward path
stays testable without a network.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

# Upstream statuses worth retrying: rate limiting and transient server faults. An ordinary 4xx
# (bad request, auth) is the client's fault and is never retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Statuses that mean *this target is unusable* rather than *this request was bad*: a missing,
# expired, or forbidden upstream key. They are not retryable (retrying a bad key is pointless), but
# — unlike an ordinary client 4xx — they must count as a breaker failure so a stale key eventually
# opens the breaker and delivery degrades (WF-ADR-0031).
AUTH_FAILURE_STATUS = frozenset({401, 403, 407})


def is_retryable(status: int | None) -> bool:
    """Whether a forward attempt should be retried.

    ``None`` means a transport failure (timeout, connection refused) — always retryable. An HTTP
    status is retryable only for rate-limit / transient 5xx.
    """
    return status is None or status in RETRYABLE_STATUS


def is_auth_failure(status: int | None) -> bool:
    """Whether ``status`` means the target is unusable (bad/expired/forbidden key)."""
    return status in AUTH_FAILURE_STATUS


def retry_delays(
    retries: int,
    *,
    base: float = 0.2,
    cap: float = 5.0,
    rng: Callable[[], float] = random.random,
) -> list[float]:
    """Backoff delays (seconds) before each retry: exponential, capped, full-jitter.

    ``retries`` is the number of *re*-tries, so the schedule has exactly that many entries (the
    first attempt has no preceding delay; ``retries <= 0`` yields ``[]``). Each slot is
    ``min(cap, base * 2**i)`` and the jitter draws uniformly over ``[0, slot]`` via ``rng`` (injected
    for deterministic tests). Delays are rounded to 6dp.
    """
    delays: list[float] = []
    for i in range(max(0, retries)):
        slot = min(cap, base * (2**i))
        delays.append(round(slot * rng(), 6))
    return delays


@dataclass
class CircuitBreaker:
    """Per-target breaker: open after ``threshold`` consecutive failures, probe after ``cooldown``.

    Pure bookkeeping over success/failure outcomes — no model call. ``clock`` is injectable so
    cooldown is testable; state is in-memory and per process (a shared store for multi-process
    deployments is a deliberate later option, WF-ADR-0031). A lock guards the
    read-modify-write in ``record`` and the read in ``allow`` so concurrent forward attempts to the
    same target can't corrupt the failure count.
    """

    threshold: int = 5
    cooldown: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _fails: dict[str, int] = field(default_factory=dict, repr=False)
    _opened_at: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def allow(self, target: str) -> bool:
        """True if ``target`` may be tried now — closed, or cooldown elapsed (a half-open probe).

        The cooldown boundary is ``>=``: when exactly ``cooldown`` seconds have passed since the
        breaker opened, a single probe is allowed through.
        """
        with self._lock:
            opened = self._opened_at.get(target)
            if opened is None:
                return True
            return self.clock() - opened >= self.cooldown

    def is_open(self, target: str) -> bool:
        """True while ``target`` is tripped and still cooling down (the inverse of :meth:`allow`)."""
        return not self.allow(target)

    def record(self, target: str, ok: bool) -> None:
        """Fold one attempt's outcome in: success fully closes the breaker; a failure may open it.

        Success clears both the failure count and any open timestamp. A failure increments the count
        and, once it reaches ``threshold``, (re)opens the breaker and restarts the cooldown from now
        — so a failed half-open probe (count already at/over threshold) reopens immediately.
        """
        with self._lock:
            if ok:
                self._fails.pop(target, None)
                self._opened_at.pop(target, None)
                return
            count = self._fails.get(target, 0) + 1
            self._fails[target] = count
            if count >= self.threshold:
                self._opened_at[target] = self.clock()


def delivery_plan(
    primary: str,
    fallbacks: Iterable[str],
    breaker: CircuitBreaker | None = None,
    allow: Callable[[str], bool] | None = None,
) -> list[str]:
    """Ordered, de-duplicated targets to try: the primary then its same-tier fallbacks.

    Duplicates are dropped keeping first-seen order. A target whose breaker is open (still cooling
    down), or that the ``allow`` predicate rejects (a deterministic pre-call check, e.g.
    context-window fit), is skipped. Order and identity come from config, never from the score
    (WF-ADR-0031: delivery, not decision). May be empty if every candidate is filtered — the caller
    then fails fast.
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


# Cross-tier failover policies (WF-ADR-0031). The default is conservative: stay on the chosen tier
# (only its configured alternate endpoints), changing neither cost nor answer quality. This tuple's
# order and content feed a user-visible gateway config-error message via ``", ".join(...)``.
FAILOVER_POLICIES = ("same-tier", "degrade", "escalate")


def failover_candidates(chosen: str, ladder: Iterable[str], policy: str) -> list[str]:
    """Cross-tier targets to try once same-tier endpoints are exhausted, per ``policy``.

    ``ladder`` lists tier model names cheapest -> dearest. ``degrade`` walks to cheaper tiers,
    nearest-cheaper first (the reversed lower slice); ``escalate`` walks to dearer tiers,
    nearest-dearer first (the natural upper slice). ``same-tier``, any other policy, or an off-ladder
    ``chosen`` yields nothing. Identity and order come from the ladder, not the score.
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

    ``None`` means no configured limit (always OK). Used to skip a target that would certainly fail
    on length before spending the call (WF-ADR-0031).
    """
    return context_window is None or estimated_tokens <= context_window
