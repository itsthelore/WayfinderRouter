"""Deterministic exact-match response cache for the gateway (WF-ROADMAP-0006 #10).

Pure, offline, no model call (WF-ADR-0001): an identical request replays a stored completion
instead of forwarding upstream — an instant, free repeat. The key is a SHA-256 of the
normalized request (so the prompt itself is never stored, only its digest); the value is the
completion bytes the client already received. The default ``ResponseCache`` is **in-memory
only**; an opt-in ``DiskResponseCache`` (WF-DESIGN-0013 §7a, WF-ROADMAP-0012) persists behind the
same contract — an append-only body log plus a SQLite index — for survival across restart. Both
are bounded by an LRU entry count, a byte ceiling, and a TTL, and are **off by default** —
enabling either is a deliberate opt-in to retaining response bodies (WF-ADR-0033, mirroring the
opt-in posture WF-DESIGN-0008 set for body capture). No FastAPI/httpx import here, so this
unit-tests like ``reliability.py`` / ``pricing.py``; the clock is injectable.

The module stays two concrete classes plus pure helpers — the ``get``/``put``/``clear`` contract
is the seam the disk backend honors, so no speculative abstraction beyond it ships (matching the
``CircuitBreaker``/``SavingsLedger`` precedent).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path

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


# A length-prefix frame is all the body log needs — the index carries off/len, status, tokens,
# ttl and mru — so store.py's richer seq/ts/crc frame is over-spec here and is deliberately not
# imported (avoids coupling this scope's build to a sibling module, WF-ROADMAP-0012 §7).
_BODY_FRAME = struct.Struct("<I")


@dataclass
class DiskResponseCache:
    """Disk-backed exact-match cache with the ``ResponseCache`` contract (WF-DESIGN-0013 §7a).

    Bodies append to ``bodies.log`` (length-prefixed frames); a SQLite ``index.db`` carries the
    off/len slice plus status, tokens, TTL and an ``mru`` LRU rank. ``get`` slices the body by
    offset and verifies TTL; ``put`` appends then upserts and evicts under BOTH ceilings. Every
    write commits and flushes so a reconstructed instance sees durable rows without a ``close()``;
    ``reconfigure(enabled=False)`` truncates ``bodies.log`` to zero and clears the index — the
    privacy purge must not be resurrectable on reload (WF-ADR-0033). A lock serializes the single
    connection (``check_same_thread=False``); WAL + ``synchronous=NORMAL`` keep the append cheap
    and let a second live instance over the same dir coexist (WF-ADR-0032).
    """

    enabled: bool = False
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_bytes: int = DEFAULT_MAX_BYTES
    ttl: float = DEFAULT_TTL
    clock: Callable[[], float] = time.monotonic
    hits: int = 0
    misses: int = 0
    _: KW_ONLY
    dir: str = ""  # keyword-only store root; required in practice (the tests always pass dir=)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _conn: sqlite3.Connection = field(init=False, repr=False, compare=False)
    _log_path: str = field(default="", init=False, repr=False, compare=False)
    _mru: int = field(default=0, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root = Path(self.dir)
        root.mkdir(parents=True, exist_ok=True)
        self._log_path = str(root / "bodies.log")
        if not os.path.exists(self._log_path):
            open(self._log_path, "wb").close()
        self._conn = sqlite3.connect(str(root / "index.db"), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS entries(key TEXT PRIMARY KEY, off INTEGER, len INTEGER, "
            "status INTEGER, content_type TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, "
            "estimated INTEGER, stored_at REAL, mru INTEGER)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS entries_mru ON entries(mru)")
        self._conn.commit()
        # Continue the LRU ordering of any reloaded rows rather than colliding with their ranks.
        row = self._conn.execute("SELECT COALESCE(MAX(mru),0)+1 FROM entries").fetchone()
        self._mru = int(row[0])

    def get(self, key: str) -> CachedResponse | None:
        """Return a fresh entry (and mark it most-recently-used), or ``None``; counts hit/miss.

        A disabled cache always misses without bookkeeping. Expired entries drop lazily on lookup.
        """
        if not self.enabled:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT off,len,status,content_type,prompt_tokens,completion_tokens,estimated,"
                "stored_at FROM entries WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                self.misses += 1
                return None
            off, length, status, content_type, pt, ct, estimated, stored_at = row
            if self.ttl > 0 and self.clock() - stored_at >= self.ttl:
                self._conn.execute("DELETE FROM entries WHERE key=?", (key,))
                self._conn.commit()
                self.misses += 1
                return None
            self._mru += 1
            self._conn.execute("UPDATE entries SET mru=? WHERE key=?", (self._mru, key))
            self._conn.commit()
            self.hits += 1
            with open(self._log_path, "rb") as f:
                f.seek(off)
                body = f.read(length)
            return CachedResponse(
                status=int(status),
                content_type=str(content_type),
                body=body,
                prompt_tokens=int(pt),
                completion_tokens=int(ct),
                estimated=bool(estimated),
                stored_at=float(stored_at),
            )

    def put(self, key: str, entry: CachedResponse) -> None:
        """Append the body and upsert the index row, then evict LRU under both ceilings.

        A no-op when disabled or a ceiling is non-positive, or when a single body cannot ever fit.
        """
        if not self.enabled or self.max_entries <= 0 or self.max_bytes <= 0:
            return
        size = len(entry.body)
        if size > self.max_bytes:  # a single entry too large to ever fit — never store it
            return
        try:
            with self._lock:
                off, length = self._append_body_locked(entry.body)
                self._mru += 1
                self._conn.execute(
                    "INSERT INTO entries(key,off,len,status,content_type,prompt_tokens,"
                    "completion_tokens,estimated,stored_at,mru) VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET off=excluded.off,len=excluded.len,"
                    "status=excluded.status,content_type=excluded.content_type,"
                    "prompt_tokens=excluded.prompt_tokens,completion_tokens=excluded.completion_tokens,"
                    "estimated=excluded.estimated,stored_at=excluded.stored_at,mru=excluded.mru",
                    (
                        key, off, length, entry.status, entry.content_type, entry.prompt_tokens,
                        entry.completion_tokens, 1 if entry.estimated else 0, entry.stored_at,
                        self._mru,
                    ),
                )
                self._evict_locked()
                self._conn.commit()
        except (sqlite3.Error, OSError):  # pragma: no cover - defensive, never raise into requests
            pass

    def clear(self) -> None:
        """Drop every entry and truncate the body log (purges all retained bodies)."""
        with self._lock:
            self._conn.execute("DELETE FROM entries")
            self._conn.commit()
            open(self._log_path, "wb").close()

    def reconfigure(self, *, enabled: bool, max_entries: int, max_bytes: int, ttl: float) -> None:
        """Apply hot-reloaded config; disabling purges + truncates both files (the privacy guard).

        Shrinking the ceilings evicts to fit; unrelated changes keep the warm rows and counters.
        """
        with self._lock:
            self.enabled = enabled
            self.max_entries = max_entries
            self.max_bytes = max_bytes
            self.ttl = ttl
            if not enabled:
                self._conn.execute("DELETE FROM entries")
                self._conn.commit()
                open(self._log_path, "wb").close()  # zero the body file — no resurrection on reload
            else:
                self._evict_locked()
                self._conn.commit()

    def stats(self) -> dict[str, int]:
        """Entry count, logical live byte total (``SUM(len)``, not file size), and hits/misses."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(len),0) FROM entries"
            ).fetchone()
            return {
                "entries": int(row[0]),
                "bytes": int(row[1]),
                "hits": self.hits,
                "misses": self.misses,
            }

    def _append_body_locked(self, body: bytes) -> tuple[int, int]:
        # Append-only: a replace/eviction leaves the old bytes dead in the log (the index stops
        # referencing them); ``stats()`` counts only live SUM(len). Flush so a reopened instance
        # sees the bytes without depending on finalizer timing.
        frame = _BODY_FRAME.pack(len(body)) + body
        with open(self._log_path, "ab") as f:
            f.seek(0, os.SEEK_END)
            start = f.tell()
            f.write(frame)
            f.flush()
        return start + _BODY_FRAME.size, len(body)

    def _evict_locked(self) -> None:
        # Honor BOTH ceilings, dropping the least-recently-used index row until under each.
        while True:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(len),0) FROM entries"
            ).fetchone()
            count, total = int(row[0]), int(row[1])
            if count == 0 or (count <= self.max_entries and total <= self.max_bytes):
                return
            self._conn.execute("DELETE FROM entries WHERE mru=(SELECT MIN(mru) FROM entries)")
