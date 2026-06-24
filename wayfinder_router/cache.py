"""Deterministic exact-match response cache for the gateway (WF-ROADMAP-0006 #10).

Pure, offline, no model call (WF-ADR-0001): an identical request replays a stored completion
instead of forwarding upstream — an instant, free repeat. The key is a SHA-256 of the
normalized request (so the prompt itself is never stored, only its digest); the value is the
completion bytes the client already received. The store is **in-memory only**, bounded by an
LRU entry count, a byte ceiling, and a TTL, and is **off by default** — enabling it is a
deliberate opt-in to retaining response bodies in memory (WF-ADR-0033, mirroring the opt-in
posture WF-DESIGN-0008 set for body capture). No FastAPI/httpx import here, so this unit-tests
like ``reliability.py`` / ``pricing.py``; the clock is injectable.

The module is intentionally a single concrete ``ResponseCache`` plus pure helpers — the
``get``/``put``/``clear`` contract is the seam a future disk/Redis backend would honor, so no
speculative abstraction ships now (matching the ``CircuitBreaker``/``SavingsLedger`` precedent).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DEFAULT_TTL = 300.0  # seconds an entry is served before it is considered stale (0 = no expiry)
DEFAULT_MAX_ENTRIES = 1024  # LRU bound on the number of cached responses
DEFAULT_MAX_BYTES = 64 * 1024 * 1024  # hard memory ceiling for cached bodies (64 MiB)

# Fields dropped before hashing: ``model`` is the inbound routing directive (we key on the
# *served* upstream id instead, passed separately), and ``stream`` is a transport choice that
# does not change the answer. Everything else in the body is keyed verbatim, so any field that
# affects the completion (temperature, max_tokens, stop, response_format, n, …) splits the key.
EXCLUDED_KEY_FIELDS = frozenset({"model", "stream"})


def cache_key(served_model: str, body: Mapping) -> str:
    """SHA-256 over the served upstream model id + a canonical projection of ``body``.

    Pure and deterministic: identical requests (modulo key/whitespace ordering) produce the
    same digest; the served model id is folded in so two routing names that resolve to the same
    upstream share an entry, and a different upstream never replays another's answer. The raw
    prompt is hashed, never stored.
    """
    projected = {k: v for k, v in body.items() if k not in EXCLUDED_KEY_FIELDS}
    blob = json.dumps(
        {"m": served_model, "b": projected},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def is_cacheable(body: Mapping) -> bool:
    """Whether a request is safe to serve from / store in an exact-match cache.

    Conservative by design: only *contractually deterministic* requests qualify, so a hit can
    never differ from a fresh call in a way the caller asked for. Excludes streaming, sampling
    (``temperature``/``top_p``/``n`` away from the deterministic point), ``seed`` (not a
    cross-version guarantee), tool calls (replaying a stale ``tool_call`` into an agent loop is
    a hazard), a non-empty ``logit_bias``, and multimodal/array message content.
    """
    if body.get("stream") is True:
        return False
    temperature = body.get("temperature")
    if temperature is not None and temperature != 0:
        return False
    top_p = body.get("top_p")
    if top_p is not None and top_p != 1:
        return False
    n = body.get("n")
    if n is not None and n != 1:
        return False
    if body.get("seed") is not None:
        return False
    if body.get("tools") or body.get("tool_choice"):
        return False
    if body.get("logit_bias"):
        return False
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    return all(isinstance(m, dict) and isinstance(m.get("content"), str) for m in messages)


def is_storable(status: int, content_type: str, response: object) -> bool:
    """Whether an upstream response is a safe, complete success worth caching.

    Guards the cardinal footgun: many OpenAI-compatible upstreams return **HTTP 200 with an
    error-shaped or empty body** on overload. Storing such a body would replay a poisoned
    "success" to every identical request until it expires. Requires a real 200 JSON completion
    with non-empty string content and no ``tool_calls`` (defense in depth — tools are already
    gated out by :func:`is_cacheable`).
    """
    if status != 200 or "json" not in content_type:
        return False
    if not isinstance(response, dict) or response.get("error") is not None:
        return False
    choices = response.get("choices")
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        return False
    message = choices[0].get("message")
    if not isinstance(message, dict) or message.get("tool_calls"):
        return False
    content = message.get("content")
    return isinstance(content, str) and content != ""


@dataclass(frozen=True)
class CachedResponse:
    """One stored completion: the bytes to replay plus the token counts a hit reports as saved.

    ``body`` is the raw upstream response bytes (replayed verbatim, with its ``content_type``).
    The token counts are kept so a hit can report the cost it *avoided* without a re-tokenize;
    they are never re-billed to the savings ledger (a hit is free — WF-ADR-0033).
    """

    status: int
    content_type: str
    body: bytes
    prompt_tokens: int
    completion_tokens: int
    estimated: bool
    stored_at: float


@dataclass
class ResponseCache:
    """In-memory LRU + byte-ceiling + TTL exact-match cache; lock-guarded, clock injectable.

    ``OrderedDict`` gives O(1) LRU. A lock guards mutation (the event loop is single-threaded,
    but tests and any future thread may touch it — the same posture as ``SavingsLedger``). When
    ``enabled`` is false every operation is a cheap no-op and nothing is retained. Bounded by
    BOTH ``max_entries`` and ``max_bytes``: eviction drops the least-recently-used entries until
    the cache is under both ceilings.
    """

    enabled: bool = False
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_bytes: int = DEFAULT_MAX_BYTES
    ttl: float = DEFAULT_TTL
    clock: Callable[[], float] = time.monotonic
    hits: int = 0
    misses: int = 0
    _store: "OrderedDict[str, CachedResponse]" = field(default_factory=OrderedDict, repr=False)
    _bytes: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def get(self, key: str) -> CachedResponse | None:
        """Return a fresh entry (and mark it most-recently-used), or ``None``; counts hit/miss.

        A disabled cache always misses. Expired entries are dropped lazily on lookup.
        """
        if not self.enabled:
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            if self.ttl > 0 and self.clock() - entry.stored_at >= self.ttl:
                self._drop_locked(key)
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, key: str, entry: CachedResponse) -> None:
        """Insert/refresh an entry, then evict LRU until under both ceilings (no-op if disabled)."""
        if not self.enabled or self.max_entries <= 0 or self.max_bytes <= 0:
            return
        size = len(entry.body)
        if size > self.max_bytes:  # a single entry too large to ever fit — never store it
            return
        with self._lock:
            if key in self._store:
                self._bytes -= len(self._store[key].body)
            self._store[key] = entry
            self._store.move_to_end(key)
            self._bytes += size
            self._evict_locked()

    def clear(self) -> None:
        """Drop every entry (purges all retained bodies)."""
        with self._lock:
            self._store.clear()
            self._bytes = 0

    def reconfigure(self, *, enabled: bool, max_entries: int, max_bytes: int, ttl: float) -> None:
        """Apply hot-reloaded config to the long-lived instance.

        Disabling **purges** all retained bodies immediately (the privacy guarantee — turning
        the cache off does not leave completions sitting in memory until TTL). Shrinking the
        ceilings evicts to fit. Unrelated changes keep the warm cache and the hit/miss counters.
        """
        with self._lock:
            self.enabled = enabled
            self.max_entries = max_entries
            self.max_bytes = max_bytes
            self.ttl = ttl
            if not enabled:
                self._store.clear()
                self._bytes = 0
            else:
                self._evict_locked()

    def stats(self) -> dict[str, int]:
        """Entry count, byte size, and cumulative hits/misses (for introspection)."""
        with self._lock:
            return {
                "entries": len(self._store),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
            }

    def _drop_locked(self, key: str) -> None:
        entry = self._store.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry.body)

    def _evict_locked(self) -> None:
        while self._store and (len(self._store) > self.max_entries or self._bytes > self.max_bytes):
            _, entry = self._store.popitem(last=False)  # least-recently-used
            self._bytes -= len(entry.body)
