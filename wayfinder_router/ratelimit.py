"""Deterministic request/token rate limiting for the gateway (WF-ADR-0034, WF-ROADMAP-0006 #7).

Pure, offline counters — no model call, no network (WF-ADR-0001). A fixed-window limiter caps
requests per minute (RPM) and/or upstream tokens per minute (TPM); on breach the gateway returns
HTTP 429 so a runaway client can't flood an upstream or blow the blast radius. State is in-memory
per process by default, the clock is injectable, and a lock guards the counters. This unit-tests
like ``reliability.py``; no FastAPI/httpx import here.

Optionally (WF-DESIGN-0013 §7c, WF-ROADMAP-0012), when ``state_path`` is set the single scope's
window counters are best-effort persisted to a shared ``state.db`` on each mutating transition
(inside the lock) and reloaded on construction, so counts can survive a restart. Caps are never
persisted — they always come from the constructor/``reconfigure``. The guarantee is never-raise:
any ``sqlite3.Error``/``OSError`` degrades silently to pure in-memory operation. LIMITATION: the
persisted ``window_id`` is a raw ``clock()`` reading; ``time.monotonic`` restarts from an
arbitrary base across a real process restart, so a reloaded window is only meaningful relative to
the *same* clock (round-trips under an injected clock; undefined across a real monotonic restart).
This is documented, not solved (no wall-clock rebasing).

v1 is gateway-wide; per-key / per-session limits ride on virtual keys (WF-ROADMAP-0006 #5).
"""

from __future__ import annotations

import math
import sqlite3
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
    """Fixed-window RPM/TPM limiter; lock-guarded, clock injectable.

    A window is ``window`` seconds keyed by ``floor(now / window)`` (so windows roll
    deterministically and survive clock jumps via a monotonic clock). ``admit`` reserves a
    request slot — it increments the request count when it returns allowed — and ``add_tokens``
    records a served turn's upstream tokens against the current window. Either limit may be
    ``None`` (off); when both are ``None`` the limiter is inert and ``admit`` always allows.
    """

    rpm: int | None = None
    tpm: int | None = None
    window: float = DEFAULT_WINDOW
    clock: Callable[[], float] = time.monotonic
    state_path: str | None = field(default=None, kw_only=True)
    _window_id: int = -1
    _requests: int = 0
    _tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False, compare=False)
    _scope: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Best-effort open + reload of the persisted window row; degrade to in-memory."""
        if self.state_path is None:
            return
        try:
            conn = sqlite3.connect(self.state_path, isolation_level=None)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ratelimit ("
                "scope TEXT PRIMARY KEY, window_id INTEGER, requests INTEGER, tokens INTEGER)"
            )
            row = conn.execute(
                "SELECT window_id, requests, tokens FROM ratelimit WHERE scope = ?",
                (self._scope,),
            ).fetchone()
        except (sqlite3.Error, OSError):
            self._conn = None
            return
        self._conn = conn
        if row is not None:
            self._window_id, self._requests, self._tokens = int(row[0]), int(row[1]), int(row[2])

    def active(self) -> bool:
        """Whether any limit is configured (else the limiter is a no-op)."""
        return self.rpm is not None or self.tpm is not None

    def admit(self, now: float | None = None) -> RateResult:
        """Check the limits and, if within them, count this request as admitted.

        Returns ``allowed=False`` with the tripped limit and a ``retry_after`` (no increment) when
        a limit is already reached; otherwise increments the request count and allows.
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
            self._persist_locked()
            return RateResult(True)

    def add_tokens(self, n: int, now: float | None = None) -> None:
        """Record ``n`` upstream tokens for the current window (no-op unless a TPM cap is set)."""
        if self.tpm is None:
            return
        now = self.clock() if now is None else now
        with self._lock:
            self._roll_locked(now)
            self._tokens += max(0, int(n))
            self._persist_locked()

    def reconfigure(self, *, rpm: int | None, tpm: int | None, window: float) -> None:
        """Apply hot-reloaded limits to the long-lived instance (counts carry into the window)."""
        with self._lock:
            self.rpm = rpm
            self.tpm = tpm
            self.window = window

    def stats(self) -> dict[str, int]:
        """Current window's request and token counts (for introspection/tests)."""
        with self._lock:
            return {"requests": self._requests, "tokens": self._tokens}

    def snapshot(self, now: float | None = None) -> tuple[int, int, int] | None:
        """The request-rate dimension as ``(limit, remaining, reset_seconds)``, or ``None``.

        ``None`` when no RPM cap is set. ``remaining`` is the headroom left in the current window
        (after requests already admitted); ``reset_seconds`` is the time until the window rolls.
        Used for informational ``X-RateLimit-*`` response headers (WF-ADR-0034).
        """
        if self.rpm is None:
            return None
        now = self.clock() if now is None else now
        with self._lock:
            self._roll_locked(now)
            return self.rpm, max(0, self.rpm - self._requests), self._retry_after_locked(now)

    def _persist_locked(self) -> None:
        """Rewrite the single scope's window row; best-effort, never raises (call under lock).

        Caps are deliberately not persisted (§7c). ``reconfigure`` never calls this: it
        changes caps only, carries the in-window count, and does not re-roll.
        """
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO ratelimit(scope, window_id, requests, tokens) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET window_id=excluded.window_id, "
                "requests=excluded.requests, tokens=excluded.tokens",
                (self._scope, self._window_id, self._requests, self._tokens),
            )
        except (sqlite3.Error, OSError):
            self._conn = None

    def _roll_locked(self, now: float) -> None:
        wid = int(now // self.window)
        if wid != self._window_id:
            self._window_id = wid
            self._requests = 0
            self._tokens = 0

    def _retry_after_locked(self, now: float) -> int:
        remaining = (self._window_id + 1) * self.window - now
        return max(1, math.ceil(remaining))
