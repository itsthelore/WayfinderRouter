"""Spec-first contract tests for wayfinder_router.store (WF-DESIGN-0013 §1; contracts 2-3).

Written from the design before implementation. Pins RecordStore.rebuild: it rebuilds the shard
indexes from the immutable segment logs across `workers` processes with no shared writer, returns
a RebuildReport whose fields are sane, produces query answers byte-identical to a serial rebuild
and to the pre-wipe store, is deterministic, and enforces the keyword-only `index_fields`/`workers`
contract via TypeError on positional abuse.

Ambiguities resolved to the strictest reading (raised at the checkpoint):
- rebuild reconstructs only the derived shards + MANIFEST from the log; the segment .log files
  (and therefore every Location's segment_id/offset/length/seq) are unchanged, so a post-rebuild
  query must return Locations equal to the pre-wipe query, not merely equal seq sets.
- RebuildReport.records is read as the total records indexed; .segments as the segment count;
  .workers as the requested worker count; .seconds as a non-negative wall-time float.
- multiprocessing determinism is asserted as identical query answers for workers=1 vs workers>1
  (no test actually spawns and inspects processes; that is the implementation's concern).
"""

from __future__ import annotations

import os
import shutil

import pytest

from wayfinder_router import store
from wayfinder_router.store import RebuildReport, RecordStore

INDEX_FIELDS = ("team", "route")


def _populate(root: str, n: int = 10) -> list:
    """Build a multi-segment store (small bounds set by the caller) and return the query answer."""
    st = RecordStore(root, index_fields=INDEX_FIELDS)
    locs = []
    for i in range(n):
        team = "finance" if i % 2 == 0 else "platform"
        route = "cloud" if i % 3 == 0 else "local"
        locs.append(st.append(f"r{i}".encode(), keys={"team": team, "route": route}))
    st.flush()
    st.close()
    return locs


def _index_dbs(root: str) -> list[str]:
    idx = os.path.join(root, "index")
    return [os.path.join(idx, f) for f in os.listdir(idx) if f.endswith(".db")]


def _finance_seqs(root: str) -> list[int]:
    st = RecordStore(root, index_fields=INDEX_FIELDS)
    try:
        return [loc.seq for loc in st.query(equals={"team": "finance"}, limit=1000)]
    finally:
        st.close()


def _finance_locs(root: str) -> list:
    st = RecordStore(root, index_fields=INDEX_FIELDS)
    try:
        return list(st.query(equals={"team": "finance"}, limit=1000))
    finally:
        st.close()


# --- RebuildReport field sanity ---------------------------------------------------------
def test_rebuild_report_fields_are_sane(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    root = os.path.join(str(tmp_path), "gov")
    _populate(root, n=10)
    report = RecordStore.rebuild(root, index_fields=INDEX_FIELDS, workers=2)
    assert isinstance(report, RebuildReport)
    assert report.records == 10
    assert report.segments >= 4               # 10 records / 3-per-segment -> at least 4 segments
    assert report.workers == 2                # echoes the requested worker count
    assert report.seconds >= 0.0


# --- rebuild restores a wiped index -----------------------------------------------------
def test_rebuild_restores_query_after_index_wipe(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    root = os.path.join(str(tmp_path), "gov")
    _populate(root, n=12)
    before = _finance_locs(root)
    for db in _index_dbs(root):
        os.remove(db)                         # destroy the derived shards
    RecordStore.rebuild(root, index_fields=INDEX_FIELDS, workers=4)
    after = _finance_locs(root)
    assert after == before                    # identical Locations, rebuilt from the log


# --- parallel == serial -----------------------------------------------------------------
def test_parallel_rebuild_matches_serial(tmp_path, monkeypatch) -> None:
    """rebuild(workers=N) must reproduce the same index (same query answers) as workers=1; the
    two roots are independent copies of the same populated log."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    base = os.path.join(str(tmp_path), "gov")
    _populate(base, n=15)
    serial_root = os.path.join(str(tmp_path), "serial")
    parallel_root = os.path.join(str(tmp_path), "parallel")
    shutil.copytree(base, serial_root)
    shutil.copytree(base, parallel_root)
    for db in _index_dbs(serial_root) + _index_dbs(parallel_root):
        os.remove(db)
    RecordStore.rebuild(serial_root, index_fields=INDEX_FIELDS, workers=1)
    RecordStore.rebuild(parallel_root, index_fields=INDEX_FIELDS, workers=4)
    assert _finance_locs(parallel_root) == _finance_locs(serial_root)


def test_rebuild_is_deterministic_across_repeats(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 4)
    base = os.path.join(str(tmp_path), "gov")
    _populate(base, n=13)
    first = os.path.join(str(tmp_path), "first")
    second = os.path.join(str(tmp_path), "second")
    shutil.copytree(base, first)
    shutil.copytree(base, second)
    r1 = RecordStore.rebuild(first, index_fields=INDEX_FIELDS, workers=2)
    r2 = RecordStore.rebuild(second, index_fields=INDEX_FIELDS, workers=2)
    assert r1.records == r2.records and r1.segments == r2.segments
    assert _finance_locs(first) == _finance_locs(second)


def test_rebuild_matches_prewipe_answers(tmp_path, monkeypatch) -> None:
    """The whole point: rebuilding indexes yields the same query answers the live store gave."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 5)
    root = os.path.join(str(tmp_path), "gov")
    _populate(root, n=11)
    before = _finance_seqs(root)
    for db in _index_dbs(root):
        os.remove(db)
    RecordStore.rebuild(root, index_fields=INDEX_FIELDS, workers=3)
    assert _finance_seqs(root) == before


def test_rebuild_recovers_from_corrupt_shards(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    root = os.path.join(str(tmp_path), "gov")
    _populate(root, n=9)
    before = _finance_seqs(root)
    for db in _index_dbs(root):
        with open(db, "r+b") as fh:
            fh.seek(0)
            fh.write(b"garbage-not-sqlite\x00\x00\x00")
    RecordStore.rebuild(root, index_fields=INDEX_FIELDS, workers=2)
    assert _finance_seqs(root) == before


def test_rebuild_preserves_high_water_seq(tmp_path, monkeypatch) -> None:
    """MANIFEST is rewritten from the segments, so high_water_seq is unchanged by a rebuild."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    root = os.path.join(str(tmp_path), "gov")
    locs = _populate(root, n=10)
    for db in _index_dbs(root):
        os.remove(db)
    RecordStore.rebuild(root, index_fields=INDEX_FIELDS, workers=2)
    st = RecordStore(root, index_fields=INDEX_FIELDS)
    try:
        assert st.high_water_seq() == locs[-1].seq
    finally:
        st.close()


# --- keyword-only contract --------------------------------------------------------------
def test_rebuild_index_fields_and_workers_are_keyword_only(tmp_path) -> None:
    """`index_fields` and `workers` are keyword-only on rebuild; positional abuse raises TypeError
    before any work is done."""
    root = os.path.join(str(tmp_path), "gov")
    _populate(root, n=3)
    with pytest.raises(TypeError):
        RecordStore.rebuild(root, INDEX_FIELDS)          # positional index_fields rejected
    with pytest.raises(TypeError):
        RecordStore.rebuild(root, INDEX_FIELDS, 4)       # positional workers rejected too
