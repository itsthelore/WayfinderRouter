"""Deterministic request/token rate limiting for the gateway (WF-ADR-0034, WF-ROADMAP-0006 #7).

Pure, offline counters — no model call, no network (WF-ADR-0001). The limiter caps requests per
minute (RPM) and/or upstream tokens per minute (TPM); on a breach the gateway returns HTTP 429 so
a runaway client can neither flood an upstream nor blow the blast radius. State is in-memory and
per process (like the circuit breaker), the clock is injectable, and a lock guards the counters.
This unit-tests like ``reliability.py``; no FastAPI/httpx import lives here.

v1 is gateway-wide; per-key / per-session limits ride on virtual keys (WF-ROADMAP-0006 #5).
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_WINDOW = 60.0  # seconds; RPM/TPM are per-minute by convention


@dataclass(frozen=True)
class RateResult:
    """The outcome of an admission check: whether to serve, and if not, why and for how long."""

    allowed: bool
    limit: str = ""  # "" when allowed, else the limit that tripped: "rpm" | "tpm"
    retry_after: int = 0  # seconds until the current window rolls (for the Retry-After header)


@dataclass
class RateLimiter:
    """RPM/TPM limiter over a FIXED window; lock-guarded, clock injectable.

    Despite the "per-minute rate" framing, this is a fixed window, not a sliding one: the window is
    ``window`` seconds keyed by ``floor(now / window)``, so a window rolls deterministically (and
    survives clock jumps under a monotonic clock) rather than tracking a rolling trailing minute.
    ``admit`` reserves a request slot (it increments only when it returns allowed); ``add_tokens``
    records a served turn's upstream tokens into the current window. Either cap may be ``None`` (off);
    when both are ``None`` the limiter is inert and ``admit`` always allows without reading the clock.
    """

    rpm: int | None = None
    tpm: int | None = None
    window: float = DEFAULT_WINDOW
    clock: Callable[[], float] = time.monotonic
    _window_id: int = -1
    _requests: int = 0
    _tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def active(self) -> bool:
        """Whether any limit is configured (otherwise the limiter is a no-op)."""
        return self.rpm is not None or self.tpm is not None

    def admit(self, now: float | None = None) -> RateResult:
        """Check the limits and, if within them, count this request as admitted.

        Returns ``allowed=False`` with the tripped limit and a ``retry_after`` (and NO increment)
        when a cap is already reached; otherwise increments the request count and allows. RPM is
        checked before TPM, but a TPM already at its cap still trips even when RPM has headroom.
        """
        if not self.active():
            return RateResult(True)
        now = self.clock() if now is None else now
        with self._lock:
            self._roll_locked(now)
            if self.rpm is not None and self._requests >= self.rpm:
                return RateResult(False, "rpm", self._retry_after_locked(now))
            if self.tpm is not None and self._tokens >= self.tpm:
                return RateResult(False, "tpm", self._retry_after_locked(now))
            self._requests += 1
            return RateResult(True)

    def add_tokens(self, n: int, now: float | None = None) -> None:
        """Record ``n`` upstream tokens for the current window (no-op unless a TPM cap is set)."""
        if self.tpm is None:
            return
        now = self.clock() if now is None else now
        with self._lock:
            self._roll_locked(now)
            self._tokens += max(0, int(n))

    def reconfigure(self, *, rpm: int | None, tpm: int | None, window: float) -> None:
        """Apply hot-reloaded limits to the long-lived instance.

        In-window counts carry over; the window is NOT reset — so raising a cap can immediately
        re-admit a client that was exhausted a moment ago.
        """
        with self._lock:
            self.rpm = rpm
            self.tpm = tpm
            self.window = window

    def stats(self) -> dict[str, int]:
        """The current window's request and token counts (for introspection/tests)."""
        with self._lock:
            return {"requests": self._requests, "tokens": self._tokens}

    def snapshot(self, now: float | None = None) -> tuple[int, int, int] | None:
        """The request-rate dimension as ``(limit, remaining, reset_seconds)``, or ``None``.

        ``None`` when no RPM cap is set (a TPM-only or uncapped limiter has no request dimension to
        report). ``remaining`` is the headroom left after requests already admitted; ``reset_seconds``
        is the time until the window rolls. The tuple order feeds the informational ``X-RateLimit-*``
        response headers (WF-ADR-0034), so ``(limit, remaining, reset)`` is a fixed contract.
        """
        if self.rpm is None:
            return None
        now = self.clock() if now is None else now
        with self._lock:
            self._roll_locked(now)
            return self.rpm, max(0, self.rpm - self._requests), self._retry_after_locked(now)

    def _roll_locked(self, now: float) -> None:
        # Fixed-window bucket keyed by floor(now/window); a new bucket zeroes both counters.
        wid = int(now // self.window)
        if wid != self._window_id:
            self._window_id = wid
            self._requests = 0
            self._tokens = 0

    def _retry_after_locked(self, now: float) -> int:
        # Time until this window's ceiling; ceil'd and floored at 1 so Retry-After is never 0.
        remaining = (self._window_id + 1) * self.window - now
        return max(1, math.ceil(remaining))
