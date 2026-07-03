"""Deterministic exact-match response cache for the gateway (WF-ROADMAP-0006 #10).

Pure, offline, and never calls a model (WF-ADR-0001): when an identical request repeats, a
stored completion is replayed instead of forwarding upstream — an instant, free repeat. The
key is a SHA-256 over the normalized request (so the prompt itself is never retained, only its
digest); the value is the exact response bytes the client already received. The store is
in-memory only, bounded by an LRU entry count, a byte ceiling, and a TTL, and is off by
default — turning it on is a deliberate opt-in to holding response bodies in memory
(WF-ADR-0033). No FastAPI/httpx import lives here, so this unit-tests like ``reliability.py``
and ``pricing.py`` with an injectable clock.

The module ships one concrete ``ResponseCache`` plus pure gate functions; the
``get``/``put``/``clear`` contract is the seam a future disk or Redis backend would honor, so no
speculative abstraction is added now (matching ``CircuitBreaker`` / ``SavingsLedger``).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DEFAULT_TTL = 300.0  # seconds an entry stays fresh before it is treated as stale (0 = no expiry)
DEFAULT_MAX_ENTRIES = 1024  # LRU bound on the number of cached responses
DEFAULT_MAX_BYTES = 64 * 1024 * 1024  # hard memory ceiling for cached bodies (64 MiB)

# Fields removed before hashing. ``model`` is the inbound routing directive — the served upstream
# id is folded in separately instead — and ``stream`` is a transport choice that does not change
# the answer. Everything else in the body is keyed verbatim, so any field that alters the
# completion (temperature, max_tokens, stop, response_format, n, ...) splits the key.
EXCLUDED_KEY_FIELDS = frozenset({"model", "stream"})


def cache_key(served_model: str, body: Mapping) -> str:
    """SHA-256 over the served upstream model id plus a canonical projection of ``body``.

    Deterministic: requests that differ only in key ordering, whitespace, ``model``, or ``stream``
    collapse to one digest, while any content-affecting field splits it. The served model id is
    folded in under ``"m"`` so two routing names resolving to the same upstream share an entry and
    a different upstream never replays another's answer. Every serialization argument here —
    ``sort_keys``, the compact separators, ``ensure_ascii=False`` and ``default=str`` — affects the
    digest and must be preserved.
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
    """Whether a request is safe to serve from or store in an exact-match cache.

    Conservative on purpose: only contractually deterministic requests qualify, so a cache hit can
    never differ from a fresh call in a way the caller asked for. The sampling guards compare
    against the deterministic point — ``temperature != 0``, ``top_p != 1``, ``n != 1`` — so those
    exact values (and an absent field) pass and only off-point values reject. ``tools`` /
    ``tool_choice`` / ``logit_bias`` use a truthiness check (an empty container is not disqualifying).
    Multimodal / array message content is rejected: every message must be a dict with string content.
    """
    if body.get("stream") is True:
        return False
    # Sampling knobs are cacheable only at their deterministic setting; an absent field is fine,
    # while any off-point value forfeits reproducibility and disqualifies the request.
    for knob, deterministic in (("temperature", 0), ("top_p", 1), ("n", 1)):
        value = body.get(knob)
        if value is not None and value != deterministic:
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
    """Whether an upstream reply is a complete, genuine success that is safe to store.

    Guards the cardinal footgun: many OpenAI-compatible upstreams return HTTP 200 with an
    error-shaped or empty body on overload — storing that would replay a poisoned "success" to
    every identical request until it expires. Requires a real 200 JSON completion (``"json"`` is a
    substring match on the content type), no top-level ``error``, a non-empty ``choices`` list whose
    first entry is a dict with a message that has non-empty string content and no ``tool_calls``
    (defense in depth — tools are already gated out by :func:`is_cacheable`).
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
    """One stored completion: the bytes to replay plus the token counts a hit reports as avoided.

    ``body`` is the raw upstream response bytes, replayed verbatim with its ``content_type``. The
    token counts ride along so a hit can report the cost it avoided without re-tokenizing; they are
    never re-billed to the savings ledger (a hit is free — WF-ADR-0033).
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

    An ``OrderedDict`` gives O(1) LRU touch and eviction. A lock guards all mutation (the event loop
    is single-threaded, but tests and any future thread may touch it — same posture as
    ``SavingsLedger``). While ``enabled`` is false every operation is a cheap no-op and nothing is
    retained. The store is bounded by BOTH ``max_entries`` and ``max_bytes``: eviction drops the
    least-recently-used entries until it is under both ceilings.
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
        """Return a fresh entry (marking it most-recently-used) or ``None``, counting hit/miss.

        A disabled cache returns ``None`` before locking and counts NO miss. A present but expired
        entry is dropped lazily and counts a miss. The TTL boundary is ``>=`` — an entry reaching
        exactly ``ttl`` seconds old is expired — and ``ttl <= 0`` disables expiry entirely.
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
        """Insert or refresh an entry, then evict LRU until under both ceilings (no-op if disabled).

        A single body larger than ``max_bytes`` is never stored (it could never fit); it returns
        before touching the store. On overwrite the old body's length is subtracted before the new
        length is added, so the byte accounting stays exact.
        """
        if not self.enabled or self.max_entries <= 0 or self.max_bytes <= 0:
            return
        size = len(entry.body)
        if size > self.max_bytes:
            return
        with self._lock:
            if key in self._store:
                self._bytes -= len(self._store[key].body)
            self._store[key] = entry
            self._store.move_to_end(key)
            self._bytes += size
            self._evict_locked()

    def clear(self) -> None:
        """Drop every entry, purging all retained bodies."""
        with self._lock:
            self._store.clear()
            self._bytes = 0

    def reconfigure(self, *, enabled: bool, max_entries: int, max_bytes: int, ttl: float) -> None:
        """Apply hot-reloaded config to the long-lived instance.

        Disabling purges all retained bodies immediately (the privacy guarantee — turning the cache
        off does not leave completions in memory until TTL). Shrinking a ceiling evicts to fit. The
        cumulative hit/miss counters are preserved across every reconfigure.
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
        """Current entry count and byte total, plus lifetime hit and miss tallies, for introspection."""
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
        # Evict least-recently-used first, looping until under BOTH the entry and byte ceilings.
        while self._store and (len(self._store) > self.max_entries or self._bytes > self.max_bytes):
            _, entry = self._store.popitem(last=False)
            self._bytes -= len(entry.body)
