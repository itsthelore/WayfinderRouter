"""Spec-first contract tests for wayfinder_router.store (WF-DESIGN-0013 §1; contract 2).

Written from the design before implementation. Pins the durability & crash-recovery contract:
the flush() barrier makes records durable; buffered vs strict durability; a torn tail (short
frame or crc-bad frame) marks end-of-valid-log and is truncated on reopen while every earlier
flushed record survives; records past a bad crc are NEVER returned; a missing or integrity-failed
shard is dropped and rebuilt from its segment (the log is truth, the shard is derived); and frames
in the live segment whose seq exceeds the live shard's MAX(seq) are re-INSERTed on open.

Ambiguities resolved to the strictest reading (raised at the checkpoint):
- "crash" is simulated by abandoning a store (never calling flush/close) or by post-hoc byte
  edits to the on-disk segment/shard files; no real process kill is used.
- The design does not promise buffered mode *loses* un-flushed records (only that it *may*), so
  no test asserts loss in buffered mode; only flush()/strict durability guarantees are asserted.
- The live shard table is named `records` with a `seq` column per the design's shard schema; the
  re-insert-from-log test manipulates that table directly.
"""

from __future__ import annotations

import os
import sqlite3
import struct


from wayfinder_router import store
from wayfinder_router.store import RecordStore

HEADER = struct.Struct("<IQdI")  # length, seq, ts_wall, crc32


def _root(tmp_path) -> str:
    return os.path.join(str(tmp_path), "gov")


def _segment_path(root: str, seg_id: int = 0) -> str:
    return os.path.join(root, "segments", f"{seg_id:06d}.log")


def _index_paths(root: str) -> list[str]:
    idx = os.path.join(root, "index")
    return [os.path.join(idx, f) for f in os.listdir(idx) if f.endswith(".db")]


def _seqs(locs) -> list[int]:
    return [loc.seq for loc in locs]


# --- flush barrier durability -----------------------------------------------------------
def test_flush_makes_records_durable_across_reopen(tmp_path) -> None:
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("team",))
    locs = [st.append(f"r{i}".encode(), keys={"team": "finance"}) for i in range(5)]
    st.flush()
    st.close()
    st2 = RecordStore(root, index_fields=("team",))
    assert st2.high_water_seq() == locs[-1].seq
    for loc in locs:
        assert st2.read(loc.seq) == st2.read_at(loc)
    got = st2.query(equals={"team": "finance"})
    assert _seqs(got) == _seqs(locs)


def test_strict_durability_persists_each_append_without_flush(tmp_path) -> None:
    """durability="strict" fsyncs every append, so a reopen (crash-sim: first store abandoned,
    never flushed/closed) still recovers every appended record."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=(), durability="strict")
    locs = [st.append(f"s{i}".encode(), keys={}) for i in range(4)]
    # deliberately no flush()/close() on `st` — simulate a crash right after the last append
    st2 = RecordStore(root, index_fields=())
    assert st2.high_water_seq() == locs[-1].seq
    assert list(st2.scan()) == [(loc.seq, f"s{i}".encode()) for i, loc in enumerate(locs)]


def test_buffered_is_the_default_durability(tmp_path) -> None:
    """Default durability is "buffered" (sub-ms append); flush() is the barrier that persists."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=())
    loc = st.append(b"buffered", keys={})
    st.flush()
    st.close()
    assert RecordStore(root, index_fields=()).read(loc.seq) == b"buffered"


# --- torn tail: short/partial frame -----------------------------------------------------
def test_torn_partial_frame_is_truncated_on_reopen(tmp_path) -> None:
    """A partial frame appended after the last good record (a torn write) is dropped on reopen;
    every earlier flushed record survives and the file is truncated back to the valid length."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=())
    locs = [st.append(f"g{i}".encode(), keys={}) for i in range(4)]
    st.flush()
    st.close()
    seg = _segment_path(root)
    good_size = os.path.getsize(seg)
    with open(seg, "ab") as fh:
        fh.write(b"\x0c\x00\x00\x00\x99\x99")  # a truncated header (claims a frame, no payload)
    st2 = RecordStore(root, index_fields=())
    assert st2.high_water_seq() == locs[-1].seq          # torn tail ignored
    assert [s for s, _ in st2.scan()] == _seqs(locs)     # all flushed records survive
    assert os.path.getsize(seg) == good_size             # torn bytes truncated away


def test_append_after_recovery_continues_seq(tmp_path) -> None:
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=())
    locs = [st.append(f"g{i}".encode(), keys={}) for i in range(3)]
    st.flush()
    st.close()
    with open(_segment_path(root), "ab") as fh:
        fh.write(b"\xff\xff\xff")  # torn tail
    st2 = RecordStore(root, index_fields=())
    new = st2.append(b"after", keys={})
    assert new.seq == locs[-1].seq + 1  # recovery restored the append cursor
    assert st2.read(new.seq) == b"after"


# --- torn tail: crc mismatch, and records-past-bad-crc never returned --------------------
def test_crc_bad_frame_truncates_and_hides_everything_after(tmp_path) -> None:
    """Contract 2: a crc mismatch marks end-of-valid-log. The corrupted frame and every frame
    after it are dropped on reopen and are never returned by read/scan/query."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("team",))
    locs = [st.append(f"r{i}".encode(), keys={"team": "t"}) for i in range(6)]
    st.flush()
    st.close()
    victim = locs[2]  # corrupt the 3rd frame's payload -> crc no longer matches
    with open(_segment_path(root), "r+b") as fh:
        fh.seek(victim.offset + HEADER.size)
        orig = fh.read(1)
        fh.seek(victim.offset + HEADER.size)
        fh.write(bytes([orig[0] ^ 0xFF]))
    st2 = RecordStore(root, index_fields=("team",))
    survivors = _seqs(locs[:2])
    assert st2.high_water_seq() == survivors[-1]
    assert [s for s, _ in st2.scan()] == survivors
    for loc in locs[2:]:
        assert st2.read(loc.seq) is None                    # past bad crc -> never returned
    assert _seqs(st2.query(equals={"team": "t"})) == survivors


def test_records_past_bad_crc_absent_from_query(tmp_path) -> None:
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("k",))
    locs = [st.append(f"r{i}".encode(), keys={"k": "v"}) for i in range(5)]
    st.flush()
    st.close()
    with open(_segment_path(root), "r+b") as fh:
        fh.seek(locs[1].offset + HEADER.size)
        b = fh.read(1)
        fh.seek(locs[1].offset + HEADER.size)
        fh.write(bytes([b[0] ^ 0x01]))
    st2 = RecordStore(root, index_fields=("k",))
    assert _seqs(st2.query(equals={"k": "v"})) == [locs[0].seq]


# --- shard drop / rebuild on missing or integrity failure -------------------------------
def test_missing_shard_is_rebuilt_from_segment_on_open(tmp_path) -> None:
    """If a shard DB is missing on open, it is dropped-and-rebuilt from its segment (log is the
    source of truth); the query index is fully restored."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("team",))
    locs = [st.append(f"r{i}".encode(), keys={"team": "finance"}) for i in range(4)]
    st.flush()
    st.close()
    for db in _index_paths(root):
        os.remove(db)  # obliterate the derived index
    st2 = RecordStore(root, index_fields=("team",))
    assert _seqs(st2.query(equals={"team": "finance"})) == _seqs(locs)


def test_integrity_failed_shard_is_dropped_and_rebuilt(tmp_path) -> None:
    """A shard whose PRAGMA integrity_check fails (here: overwritten with garbage) is dropped and
    rebuilt from the segment on open."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("route",))
    locs = [st.append(f"r{i}".encode(), keys={"route": "cloud"}) for i in range(4)]
    st.flush()
    st.close()
    for db in _index_paths(root):
        with open(db, "r+b") as fh:
            fh.seek(0)
            fh.write(b"not-a-sqlite-database-header\x00\x00\x00\x00")  # corrupt the file
    st2 = RecordStore(root, index_fields=("route",))
    assert _seqs(st2.query(equals={"route": "cloud"})) == _seqs(locs)


def test_frames_past_shard_max_seq_are_reinserted(tmp_path) -> None:
    """Crash-recovery contract: any live-segment frame whose seq exceeds the live shard's
    MAX(seq) is re-INSERTed on open (the log is truth, the shard is derived). Simulated by
    deleting tail rows from the shard directly, then reopening."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=("team",))
    locs = [st.append(f"r{i}".encode(), keys={"team": "finance"}) for i in range(5)]
    st.flush()
    st.close()
    cut = locs[2].seq  # drop the last two rows from the shard (but they remain in the log)
    for db in _index_paths(root):
        con = sqlite3.connect(db)
        con.execute("DELETE FROM records WHERE seq > ?", (cut,))
        con.commit()
        con.close()
    st2 = RecordStore(root, index_fields=("team",))
    assert _seqs(st2.query(equals={"team": "finance"})) == _seqs(locs)  # tail rows restored


# --- sealed segments verified cheaply, not fully re-scanned -----------------------------
def test_reopen_recovers_after_seal_across_segments(tmp_path, monkeypatch) -> None:
    """With small bounds forcing multiple segments, a torn tail on the *live* segment is
    truncated while earlier sealed segments (verified by record count + tail crc) are preserved."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=())
    locs = [st.append(f"r{i}".encode(), keys={}) for i in range(7)]
    st.flush()
    st.close()
    live_seg = max(loc.segment_id for loc in locs)
    with open(_segment_path(root, live_seg), "ab") as fh:
        fh.write(b"\x08\x00")  # torn partial header on the live segment
    st2 = RecordStore(root, index_fields=())
    assert st2.high_water_seq() == locs[-1].seq
    assert [s for s, _ in st2.scan()] == _seqs(locs)


def test_flush_is_idempotent_barrier(tmp_path) -> None:
    """Repeated flush() calls are safe no-op barriers; durability is preserved."""
    root = _root(tmp_path)
    st = RecordStore(root, index_fields=())
    loc = st.append(b"x", keys={})
    st.flush()
    st.flush()
    st.close()
    assert RecordStore(root, index_fields=()).read(loc.seq) == b"x"
