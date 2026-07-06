"""Spec-first contract tests for wayfinder_router.store (WF-DESIGN-0013 §1; contracts 3, 2).

Written from the design before implementation. Pins the partitioned query surface:
equals-on-index-field fan-out with AND across clauses, time-range windowing, after_seq/limit
pagination, seq-ascending ordering (len <= limit), the StoreError when an equals key is not in
index_fields, None key values stored as SQL NULL and never matched by an equals filter, scan
ranges, cross-segment fan-out merged by seq, and the size-invariant result-identity invariant
(contract 3) exercised as a scaled proxy for the 1M-vs-10M correctness gate.

Ambiguities resolved to the strictest reading (raised at the checkpoint):
- start_ts/end_ts boundary inclusivity is unspecified; the time-range tests place records
  strictly inside/outside the window so the assertion holds under any reasonable inclusivity,
  and no exact-boundary equality is asserted.
- after_seq is read as an *exclusive* lower bound (return seq > after_seq), matching the design's
  pagination note that next_after_seq is the last returned seq.
- The 1M-vs-10M identity is asserted at small scale: growing the count of non-matching padding
  records must not change the answer to a fixed equals-query (result identity is size-invariant).
"""

from __future__ import annotations

import os

import pytest

from wayfinder_router import store
from wayfinder_router.store import RecordStore, StoreError


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _root(tmp_path, name: str = "gov") -> str:
    return os.path.join(str(tmp_path), name)


def _seqs(locs) -> list[int]:
    return [loc.seq for loc in locs]


# --- equals filtering -------------------------------------------------------------------
def test_equals_matches_only_the_indexed_value(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("team",))
    fin = [st.append(f"f{i}".encode(), keys={"team": "finance"}) for i in range(3)]
    st.append(b"p", keys={"team": "platform"})
    got = st.query(equals={"team": "finance"})
    assert _seqs(got) == _seqs(fin)


def test_equals_key_not_in_index_fields_raises_store_error(tmp_path) -> None:
    """`equals` keys must be a subset of index_fields, else StoreError (not SegmentCorruptError)."""
    st = RecordStore(_root(tmp_path), index_fields=("team",))
    st.append(b"x", keys={"team": "finance"})
    with pytest.raises(StoreError):
        st.query(equals={"route": "cloud"})  # route was never declared as an index field


def test_multiple_equals_clauses_are_anded(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("team", "route"))
    hit = st.append(b"hit", keys={"team": "finance", "route": "cloud"})
    st.append(b"miss1", keys={"team": "finance", "route": "local"})
    st.append(b"miss2", keys={"team": "platform", "route": "cloud"})
    got = st.query(equals={"team": "finance", "route": "cloud"})
    assert _seqs(got) == [hit.seq]  # both clauses must hold (AND)


def test_empty_equals_returns_all_records(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("team",))
    locs = [st.append(f"r{i}".encode(), keys={"team": "t"}) for i in range(4)]
    assert _seqs(st.query()) == _seqs(locs)


# --- None -> SQL NULL, never matched ----------------------------------------------------
def test_none_key_stored_as_null_and_never_matched(tmp_path) -> None:
    """`keys` may carry None values (stored as SQL NULL) which no equals filter ever matches; the
    record is still retrievable by seq."""
    st = RecordStore(_root(tmp_path), index_fields=("team",))
    named = st.append(b"named", keys={"team": "finance"})
    anon = st.append(b"anon", keys={"team": None})
    got = st.query(equals={"team": "finance"})
    assert anon.seq not in _seqs(got)          # NULL never matched by an equals value
    assert named.seq in _seqs(got)
    assert st.read(anon.seq) == b"anon"         # but still stored and readable


# --- time-range fan-out -----------------------------------------------------------------
def test_time_range_selects_records_inside_window(tmp_path) -> None:
    """Records placed at distinct ts_wall via the injected clock; the window's interior is
    returned and the exterior is excluded (boundaries avoided by construction)."""
    clock = _Clock()
    st = RecordStore(_root(tmp_path), index_fields=(), clock=clock)
    locs = {}
    for t in (100.0, 200.0, 300.0, 400.0, 500.0):
        clock.t = t
        locs[t] = st.append(f"t{t}".encode(), keys={})
    got = st.query(start_ts=250.0, end_ts=450.0)
    assert _seqs(got) == [locs[300.0].seq, locs[400.0].seq]


def test_time_range_open_ended_bounds(tmp_path) -> None:
    clock = _Clock()
    st = RecordStore(_root(tmp_path), index_fields=(), clock=clock)
    locs = {}
    for t in (10.0, 20.0, 30.0):
        clock.t = t
        locs[t] = st.append(f"t{t}".encode(), keys={})
    # end_ts only: everything strictly before 25 -> the 10 and 20 records
    early = st.query(end_ts=25.0)
    assert _seqs(early) == [locs[10.0].seq, locs[20.0].seq]
    # start_ts only: everything strictly after 15 -> the 20 and 30 records
    late = st.query(start_ts=15.0)
    assert _seqs(late) == [locs[20.0].seq, locs[30.0].seq]


# --- pagination -------------------------------------------------------------------------
def test_after_seq_is_exclusive_lower_bound(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("k",))
    locs = [st.append(f"r{i}".encode(), keys={"k": "v"}) for i in range(6)]
    page = st.query(equals={"k": "v"}, after_seq=locs[2].seq)
    assert all(s > locs[2].seq for s in _seqs(page))
    assert _seqs(page) == _seqs(locs[3:])


def test_limit_bounds_page_size_and_paginates(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("k",))
    locs = [st.append(f"r{i}".encode(), keys={"k": "v"}) for i in range(10)]
    first = st.query(equals={"k": "v"}, limit=4)
    assert len(first) == 4
    assert _seqs(first) == _seqs(locs[:4])
    nxt = st.query(equals={"k": "v"}, after_seq=first[-1].seq, limit=4)
    assert _seqs(nxt) == _seqs(locs[4:8])


def test_query_results_are_seq_ascending(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=("k",))
    for i in range(8):
        st.append(f"r{i}".encode(), keys={"k": "v"})
    seqs = _seqs(st.query(equals={"k": "v"}, limit=1000))
    assert seqs == sorted(seqs)


# --- cross-segment fan-out (merged by seq) ----------------------------------------------
def test_query_fans_out_across_sealed_segments_merged_by_seq(tmp_path, monkeypatch) -> None:
    """With small bounds forcing several shards, a filtered query probes every overlapping shard
    and merges results by seq into one ascending, limit-bounded page."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    st = RecordStore(_root(tmp_path), index_fields=("team",))
    fin = []
    for i in range(10):
        team = "finance" if i % 2 == 0 else "platform"
        loc = st.append(f"r{i}".encode(), keys={"team": team})
        if team == "finance":
            fin.append(loc)
    got = st.query(equals={"team": "finance"}, limit=1000)
    assert _seqs(got) == _seqs(fin)            # spans >1 segment, still ascending + complete


# --- scan ranges ------------------------------------------------------------------------
def test_scan_range_returns_contiguous_ascending_run(tmp_path) -> None:
    st = RecordStore(_root(tmp_path), index_fields=())
    locs = [st.append(f"r{i}".encode(), keys={}) for i in range(6)]
    lo, hi = locs[1].seq, locs[4].seq
    seqs = [s for s, _ in st.scan(start_seq=lo, end_seq=hi)]
    assert seqs == sorted(seqs)
    assert all(lo <= s < hi for s in seqs)     # half-open [lo, hi) per the noted reading


# --- size-invariant result identity (contract 3, scaled proxy) --------------------------
@pytest.mark.parametrize("pad", [10, 200])
def test_query_result_is_size_invariant(tmp_path, pad: int) -> None:
    """Contract 3 correctness gate: the answer to a fixed equals-query is identical no matter how
    many non-matching records the store also holds. The five 'hit' records are appended first, so
    their Locations (segment/offset/seq) are stable, then padded with `pad` misses; the query
    result must be byte-for-byte identical across pad sizes."""
    root = _root(tmp_path, f"gov{pad}")
    st = RecordStore(root, index_fields=("tag",))
    hits = [st.append(f"h{i}".encode(), keys={"tag": "hit"}) for i in range(5)]
    for i in range(pad):
        st.append(f"m{i}".encode(), keys={"tag": "miss"})
    got = st.query(equals={"tag": "hit"}, limit=1000)
    assert list(got) == list(hits)  # identical Locations regardless of the miss-record volume
