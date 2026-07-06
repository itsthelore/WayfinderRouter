"""Spec-first contract tests for the disk-backed ``ResponseCache`` (Movement A).

Pins WF-DESIGN-0013 §7(a) "Disk-backed ``ResponseCache``" and §6's ``[gateway.store]``
single knob (``backend="disk"``), against **Contract invariant 12** ("Movement A contract
preservation" — each disk backend passes the *existing* ``ResponseCache`` contract unmodified
plus a parametrized memory-vs-disk equivalence). Every behavior asserted here is the behavior
``tests/test_cache.py`` pins on the in-RAM ``ResponseCache`` (WF-ADR-0033), re-asserted through
the disk path: get/put/clear/reconfigure/stats parity, TTL expiry with an injected clock, LRU
eviction under ``max_entries`` AND ``max_bytes``, purge-on-disable (now proven by on-disk file
size, §7a: "``reconfigure(enabled=False)`` truncates both files"), and persistence across
reconstruction (the whole point of the disk backend).

Additive-only (WF-ADR-0044): a NEW ``DiskResponseCache`` class alongside the untouched
``ResponseCache``; nothing in ``cache.py`` or ``tests/test_cache.py`` is modified.

CHECKPOINT QUESTIONS (construction-surface assumptions — approve before a builder builds):
  1. SURFACE: the design fixes the *file format* (``cache/bodies.log`` + ``cache/index.db``) and
     the *selector* (``[gateway.store].backend="disk"``) but leaves the unit-level constructor
     unspecified. This suite pins the LEAST-INVASIVE reading: a concrete
     ``cache.DiskResponseCache`` dataclass carrying the SAME fields as ``ResponseCache``
     (``enabled``/``max_entries``/``max_bytes``/``ttl``/``clock``) PLUS one keyword-only
     ``dir=<path>`` naming the store root. Is ``DiskResponseCache`` the chosen surface, or is
     disk selection instead a factory / a ``backend=`` field on ``ResponseCache`` itself?
  2. DIR FIELD NAME: is the constructor keyword ``dir=``? (Tests locate the body log by globbing
     ``**/bodies.log`` under it, so a ``cache/`` subdir vs a flat layout are both tolerated — but
     the *keyword name* must be confirmed.)
  3. TRUNCATION SEMANTICS: §7a says disable "truncates both files". This suite asserts the body
     log drops to 0 bytes (strictest privacy reading). Is truncate-to-zero the intended purge, or
     is a logical delete (index rows cleared, body log compacted later) acceptable? The stricter
     zero-byte reading is pinned here.
  4. RECONSTRUCT: a fresh ``DiskResponseCache(dir=d, ...)`` over an existing ``d`` must reload the
     persisted entries (assumed — durability is the disk backend's reason to exist).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder_router import cache


class _Clock:
    """A controllable monotonic clock for TTL tests (mirrors tests/test_cache.py)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _entry(body: bytes = b"hello", pt: int = 3, ct: int = 5, at: float = 0.0) -> cache.CachedResponse:
    return cache.CachedResponse(
        status=200, content_type="application/json", body=body,
        prompt_tokens=pt, completion_tokens=ct, estimated=False, stored_at=at,
    )


def _disk(tmp_path, **kw) -> "cache.DiskResponseCache":
    """A disk cache rooted at ``tmp_path`` — the single construction seam (CHECKPOINT 1/2)."""
    kw.setdefault("enabled", True)
    kw.setdefault("ttl", 0)
    return cache.DiskResponseCache(dir=str(tmp_path), **kw)


def _body_log_size(tmp_path) -> int:
    """Size of the on-disk body segment (``bodies.log``), located by glob (CHECKPOINT 2)."""
    matches = list(Path(tmp_path).glob("**/bodies.log"))
    assert matches, "disk cache must materialize a bodies.log segment"
    return matches[0].stat().st_size


# --- get/put/clear/stats parity (mirrors test_cache.py through the disk path) ------------
def test_get_put_roundtrip_and_disabled_is_noop(tmp_path):
    c = _disk(tmp_path, enabled=False)  # ttl=0 -> no expiry
    c.put("k", _entry())
    assert c.get("k") is None  # disabled never stores or serves
    assert c.stats()["entries"] == 0
    c.enabled = True
    c.put("k", _entry())
    assert c.get("k") is not None


def test_stats_contract_shape(tmp_path):
    c = _disk(tmp_path)
    c.put("k", _entry(body=b"abcd"))
    c.get("k")
    c.get("nope")
    s = c.stats()
    assert set(s) == {"entries", "bytes", "hits", "misses"}
    assert s["entries"] == 1 and s["bytes"] == 4 and s["hits"] == 1 and s["misses"] == 1


def test_clear_drops_entries(tmp_path):
    c = _disk(tmp_path)
    c.put("k", _entry())
    c.clear()
    assert c.get("k") is None and c.stats()["entries"] == 0 and c.stats()["bytes"] == 0


# --- LRU eviction under BOTH ceilings (§7a: max_entries AND max_bytes) -------------------
def test_lru_eviction_by_entry_count(tmp_path):
    c = _disk(tmp_path, max_entries=2, max_bytes=10**9)
    c.put("a", _entry())
    c.put("b", _entry())
    assert c.get("a") is not None  # touch a -> most recently used
    c.put("c", _entry())  # over the count -> evict the LRU, now b
    assert c.get("b") is None
    assert c.get("a") is not None and c.get("c") is not None


def test_byte_ceiling_eviction_and_oversize_never_stored(tmp_path):
    c = _disk(tmp_path, max_entries=10**6, max_bytes=150)
    c.put("a", _entry(body=b"x" * 100))
    c.put("b", _entry(body=b"y" * 100))  # 200 > 150 -> evict a
    assert c.get("a") is None and c.get("b") is not None
    c.put("big", _entry(body=b"z" * 200))  # single entry over the ceiling -> never stored
    assert c.get("big") is None


# --- TTL expiry with an injected clock (identical to the in-RAM semantics) ---------------
def test_ttl_expiry_with_injected_clock(tmp_path):
    clock = _Clock()
    c = _disk(tmp_path, ttl=10.0, clock=clock)
    c.put("k", _entry(at=clock()))
    assert c.get("k") is not None
    clock.advance(10.0)  # reaches ttl -> expired (>= boundary, same as memory)
    assert c.get("k") is None
    assert c.stats()["entries"] == 0  # expired entry dropped lazily on lookup


# --- reconfigure: shrink evicts, disable PURGES + truncates the on-disk files -----------
def test_reconfigure_disable_purges_and_truncates_files(tmp_path):
    c = _disk(tmp_path, max_entries=3, max_bytes=10**9)
    for k in ("a", "b", "c"):
        c.put(k, _entry(body=b"payload"))
    assert _body_log_size(tmp_path) > 0  # bodies are on disk
    c.reconfigure(enabled=True, max_entries=1, max_bytes=10**9, ttl=300.0)
    assert c.stats()["entries"] == 1  # shrunk -> evicted down to the cap
    c.reconfigure(enabled=False, max_entries=1, max_bytes=10**9, ttl=300.0)
    assert c.stats()["entries"] == 0 and c.stats()["bytes"] == 0  # disable purges bodies
    assert _body_log_size(tmp_path) == 0  # §7a: disable truncates the body file (CHECKPOINT 3)


def test_hit_miss_counters(tmp_path):
    c = _disk(tmp_path)
    c.put("k", _entry())
    c.get("k")
    c.get("missing")
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1


# --- persistence across reconstruction (the disk backend's reason to exist) -------------
def test_entries_survive_reconstruction(tmp_path):
    c = _disk(tmp_path, ttl=0)
    c.put("k", _entry(body=b"durable", pt=7, ct=11))
    del c
    c2 = _disk(tmp_path, ttl=0)  # fresh instance over the same dir (CHECKPOINT 4)
    got = c2.get("k")
    assert got is not None and got.body == b"durable"
    assert got.prompt_tokens == 7 and got.completion_tokens == 11
    assert c2.stats()["entries"] == 1


def test_reconstruct_after_disable_stays_empty(tmp_path):
    c = _disk(tmp_path)
    c.put("k", _entry())
    c.reconfigure(enabled=False, max_entries=1, max_bytes=10**9, ttl=300.0)  # privacy purge
    c2 = _disk(tmp_path)  # reload must NOT resurrect purged bodies
    assert c2.get("k") is None and c2.stats()["entries"] == 0


# --- memory-vs-disk equivalence over one operation script (Contract 12) -----------------
def _script(c, clock):
    """A fixed op sequence; returns the observable trace + final stats for equivalence."""
    trace = []
    c.put("a", _entry(body=b"AAAA", at=clock()))
    c.put("b", _entry(body=b"BBBB", at=clock()))
    trace.append(c.get("a") is not None)      # hit
    trace.append(c.get("zzz") is None)        # miss
    c.put("c", _entry(body=b"CCCC", at=clock()))
    got = c.get("b")
    trace.append(None if got is None else got.body)  # body identity
    clock.advance(5.0)
    trace.append(c.get("a") is not None)      # still fresh (ttl=10)
    return trace, c.stats()


@pytest.mark.parametrize("backend", ["memory", "disk"])
def test_memory_and_disk_produce_identical_results(tmp_path, backend):
    clock = _Clock()
    common = dict(enabled=True, max_entries=10, max_bytes=10**9, ttl=10.0, clock=clock)
    if backend == "memory":
        c = cache.ResponseCache(**common)
    else:
        c = cache.DiskResponseCache(dir=str(tmp_path), **common)
    trace, stats = _script(c, clock)
    # The disk backend must be OBSERVABLY identical to the reference in-RAM implementation.
    assert trace == [True, True, b"BBBB", True]
    assert stats == {"entries": 3, "bytes": 12, "hits": 3, "misses": 1}
