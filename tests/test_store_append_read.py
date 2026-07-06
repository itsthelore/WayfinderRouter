"""Spec-first contract tests for wayfinder_router.store (WF-DESIGN-0013 §1; contracts 2-3).

Written from the design before implementation. Pins the framed append/read/read_at/scan
round-trip, the on-disk 24-byte frame layout (struct "<IQdI" + payload, crc32 over payload),
Location field semantics, high_water_seq, keyword-only `keys`, injected-clock ts_wall, and
segment sealing at the module SEGMENT_MAX_* bounds (overridden via monkeypatch because the
design fixes them as module constants with no constructor override).

Ambiguities resolved to the strictest reading (raised at the checkpoint):
- seq origin is unspecified; tests assert only that high_water_seq == the last assigned seq,
  that consecutive appends increment seq by 1, and that a fresh empty store reports
  high_water_seq() == 0. No absolute first-seq value is asserted.
- scan(end_seq=...) inclusivity is unspecified; read as the Pythonic half-open [start_seq,
  end_seq) and noted where asserted.
- read_at over a corrupted frame is read as raising SegmentCorruptError (read_at is typed to
  return bytes, not Optional), and is exercised on a *sealed* segment so that open-time recovery
  (which only tail-crc-checks sealed segments) does not truncate the planted corruption first.
"""

from __future__ import annotations

import os
import struct
import zlib

import pytest

from wayfinder_router import store
from wayfinder_router.store import Location, RecordStore, SegmentCorruptError

HEADER = struct.Struct("<IQdI")  # length, seq, ts_wall, crc32 -> 24 bytes (little-endian)


class _Clock:
    """Deterministic fake wall clock (mirrors test_cache/_Clock); never sleeps."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _store(tmp_path, *, index_fields: tuple[str, ...] = (), **kw) -> RecordStore:
    root = os.path.join(str(tmp_path), "gov")
    return RecordStore(root, index_fields=index_fields, **kw)


def _segment_path(root: str, seg_id: int) -> str:
    return os.path.join(root, "segments", f"{seg_id:06d}.log")


# --- frame layout + Location correctness ------------------------------------------------
def test_append_returns_location_with_spec_fields(tmp_path) -> None:
    """Location.length is the payload length (excludes the 24-byte header); ts_wall is the
    clock value at append; offset is the frame-header byte offset within the segment file."""
    clock = _Clock(1234.5)
    st = _store(tmp_path, clock=clock)
    payload = b"the-record-bytes"
    loc = st.append(payload, keys={})
    assert isinstance(loc, Location)
    assert loc.length == len(payload)      # payload length, header excluded
    assert loc.ts_wall == 1234.5           # injected clock, not time.time()
    assert loc.segment_id == 0             # first segment
    assert loc.offset >= 0


def test_on_disk_frame_matches_the_struct_and_crc(tmp_path) -> None:
    """Pins the wire frame: struct.pack("<IQdI", length, seq, ts_wall, crc32) + payload, with
    length == len(payload) and crc32 == zlib.crc32(payload)."""
    clock = _Clock(42.0)
    st = _store(tmp_path, clock=clock)
    payload = b"abcdEFGH0123"
    loc = st.append(payload, keys={})
    st.flush()
    raw = open(_segment_path(os.path.join(str(tmp_path), "gov"), loc.segment_id), "rb").read()
    length, seq, ts_wall, crc = HEADER.unpack(raw[loc.offset:loc.offset + HEADER.size])
    assert length == len(payload)
    assert seq == loc.seq
    assert ts_wall == 42.0
    assert crc == (zlib.crc32(payload) & 0xFFFFFFFF)
    assert raw[loc.offset + HEADER.size:loc.offset + HEADER.size + length] == payload


def test_read_and_read_at_round_trip_payload(tmp_path) -> None:
    st = _store(tmp_path)
    loc = st.append(b"hello-world", keys={})
    assert st.read(loc.seq) == b"hello-world"
    assert st.read_at(loc) == b"hello-world"


def test_read_unknown_seq_returns_none(tmp_path) -> None:
    """read(seq) is documented to return None for an unknown seq (never raise)."""
    st = _store(tmp_path)
    loc = st.append(b"x", keys={})
    assert st.read(loc.seq + 9999) is None


def test_empty_and_binary_payloads_round_trip(tmp_path) -> None:
    """Framing is length-prefixed, so a zero-length payload and header-colliding bytes survive."""
    st = _store(tmp_path)
    empty = st.append(b"", keys={})
    assert empty.length == 0
    assert st.read_at(empty) == b""
    hostile = HEADER.pack(7, 7, 7.0, 7) + b"\x00\xff\x99"  # bytes that look like a frame
    loc = st.append(hostile, keys={})
    assert st.read_at(loc) == hostile


# --- seq / high_water_seq ---------------------------------------------------------------
def test_seq_increments_by_one_and_high_water_tracks_last(tmp_path) -> None:
    st = _store(tmp_path)
    locs = [st.append(f"r{i}".encode(), keys={}) for i in range(5)]
    for prev, cur in zip(locs, locs[1:]):
        assert cur.seq == prev.seq + 1
    assert st.high_water_seq() == locs[-1].seq


def test_high_water_seq_zero_on_empty_store(tmp_path) -> None:
    """Strictest reading: nothing appended => high_water_seq() == 0."""
    st = _store(tmp_path)
    assert st.high_water_seq() == 0


# --- scan -------------------------------------------------------------------------------
def test_scan_yields_all_in_seq_ascending_order(tmp_path) -> None:
    st = _store(tmp_path)
    payloads = [f"p{i}".encode() for i in range(6)]
    locs = [st.append(p, keys={}) for p in payloads]
    scanned = list(st.scan())
    assert [s for s, _ in scanned] == [loc.seq for loc in locs]  # ascending, complete
    assert [b for _, b in scanned] == payloads


def test_scan_start_seq_is_inclusive_lower_bound(tmp_path) -> None:
    st = _store(tmp_path)
    locs = [st.append(f"p{i}".encode(), keys={}) for i in range(6)]
    mid = locs[3].seq
    seqs = [s for s, _ in st.scan(start_seq=mid)]
    assert min(seqs) == mid and seqs == sorted(seqs)
    assert all(s >= mid for s in seqs)


def test_scan_end_seq_is_exclusive_upper_bound(tmp_path) -> None:
    """Ambiguity: end_seq inclusivity is unspecified; asserted as half-open [start, end)."""
    st = _store(tmp_path)
    locs = [st.append(f"p{i}".encode(), keys={}) for i in range(6)]
    end = locs[4].seq
    seqs = [s for s, _ in st.scan(end_seq=end)]
    assert all(s < end for s in seqs)


# --- injected clock ---------------------------------------------------------------------
def test_injected_clock_stamps_ts_wall_and_is_nondecreasing(tmp_path) -> None:
    clock = _Clock(100.0)
    st = _store(tmp_path, clock=clock)
    a = st.append(b"a", keys={})
    clock.advance(5.0)
    b = st.append(b"b", keys={})
    assert a.ts_wall == 100.0 and b.ts_wall == 105.0
    assert b.ts_wall >= a.ts_wall


# --- keyword-only contract --------------------------------------------------------------
def test_append_keys_is_keyword_only(tmp_path) -> None:
    """`keys` is marked keyword-only in the API block; positional abuse must raise TypeError."""
    st = _store(tmp_path)
    with pytest.raises(TypeError):
        st.append(b"payload", {"team": "finance"})  # positional keys is rejected


# --- segment sealing (module constants overridden via monkeypatch; no ctor override) ----
def test_segment_seals_at_max_records_and_opens_next(tmp_path, monkeypatch) -> None:
    """The design fixes SEGMENT_MAX_RECORDS as a module constant with no constructor knob, so
    the bound is lowered by monkeypatching the module before the store is built."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    st = _store(tmp_path)
    locs = [st.append(f"r{i}".encode(), keys={}) for i in range(7)]
    seg_ids = [loc.segment_id for loc in locs]
    assert seg_ids[0] == 0
    assert max(seg_ids) >= 2                    # at least three segments for 7 records / 3
    assert seg_ids == sorted(seg_ids)           # segment ids are non-decreasing with append
    assert st.high_water_seq() == locs[-1].seq  # seq is continuous across the seal


def test_second_segment_file_is_created_on_seal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 2)
    st = _store(tmp_path)
    for i in range(5):
        st.append(f"r{i}".encode(), keys={})
    st.flush()
    root = os.path.join(str(tmp_path), "gov")
    assert os.path.exists(_segment_path(root, 0))
    assert os.path.exists(_segment_path(root, 1))  # sealing rolled to the next segment file


# --- crc verification on read_at over a sealed (non-tail-rescanned) segment -------------
def test_read_at_raises_segment_corrupt_on_crc_mismatch(tmp_path, monkeypatch) -> None:
    """A byte flipped inside a *sealed* segment's non-tail frame is not caught by open-time
    recovery (sealed segments get only a tail-crc check per the recovery contract), so read_at
    must detect the crc mismatch itself and raise SegmentCorruptError."""
    monkeypatch.setattr(store, "SEGMENT_MAX_RECORDS", 3)
    st = _store(tmp_path)
    locs = [st.append(f"payload-{i}".encode(), keys={}) for i in range(6)]
    st.flush()
    st.close()
    victim = locs[1]                # a non-tail frame within sealed segment 0
    assert victim.segment_id == 0
    path = _segment_path(os.path.join(str(tmp_path), "gov"), 0)
    with open(path, "r+b") as fh:
        fh.seek(victim.offset + HEADER.size)   # first payload byte
        orig = fh.read(1)
        fh.seek(victim.offset + HEADER.size)
        fh.write(bytes([orig[0] ^ 0xFF]))      # corrupt payload -> crc no longer matches
    st2 = RecordStore(os.path.join(str(tmp_path), "gov"), index_fields=())
    with pytest.raises(SegmentCorruptError):
        st2.read_at(victim)
