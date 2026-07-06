"""Append-only segmented record store — the durable governance substrate (WF-DESIGN-0013 §1).

The single-node spine WF-ROADMAP-0012 builds behind WF-ADR-0001's frozen constitution needs a
persistent audit/decision log with partitioned indexes: sub-millisecond appends, flat 1M->10M
queries, and a parallel cold rebuild. This module is that substrate — stdlib only (`mmap`,
`sqlite3`, `struct`, `zlib`, `os`, `json`, `multiprocessing`), offline, deterministic, no model
call. It is **not** imported by `wayfinder_router/__init__.py`; like `bootstrap`, the gateway
reaches it lazily, so `import wayfinder_router` stays UI/So-stack free (the packaging contract).

Design: an append-only segmented log is the source of truth (each record framed with a 24-byte
`<IQdI` header + payload, crc32 over the payload), paired with one SQLite (WAL) index shard per
segment carrying only the indexable projection (seq, ts, seg/off/len, and the declared index
fields). The log additionally stores each record's index-field `keys` as a crc-guarded blob after
the payload, so a lost or corrupt shard is fully rebuildable from the log alone — the log is truth,
the shard is derived.

Durability contract (in prose): an append is a *buffered* `write()` plus one autocommitted shard
INSERT — no per-record fsync in the default "buffered" mode, which is why append is sub-ms. A
record is durable once `flush()` returns, `DEFAULT_FSYNC_BYTES` of un-fsynced payload has triggered
an automatic barrier, or `close()` completes; `durability="strict"` fsyncs every append. On a hard
crash, buffered mode may lose the last `< fsync_bytes` of appends — an explicit, documented trade
for the sub-ms budget — but the log is **never left torn**: recovery scans the live segment to
physical EOF, truncates at the first short/crc-bad frame, and reconciles the derived shard to it.
"""

from __future__ import annotations

import json
import mmap
import multiprocessing as mp
import os
import sqlite3
import struct
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, NamedTuple

SCHEMA_VERSION: int = 1
# Sealing bounds and the auto-barrier threshold are module globals read AT CALL TIME (never
# snapshot into instance state) so tests may monkeypatch them before or after construction.
SEGMENT_MAX_RECORDS: int = 1_000_000
SEGMENT_MAX_BYTES: int = 512 * 1024 * 1024
DEFAULT_FSYNC_BYTES: int = 4 * 1024 * 1024

_MMAP_CACHE_CAP: int = 64  # bounded LRU of sealed-segment mmaps — caps open fds / address space
_SHARD_CACHE_PAGES: int = -2000  # ~2 MiB page cache per shard (negative = KiB) — bounds RSS

# The wire frame: fixed 24-byte little-endian header + payload. crc is over the payload only.
_HEADER = struct.Struct("<IQdI")  # length, seq, ts_wall, crc32
# Trailing keys section (not part of `length`/`Location.length`): its own length + crc so a torn
# keys tail is detected just like a torn payload. Lets the log rebuild the shard by itself.
_KEYHDR = struct.Struct("<II")  # klen, kcrc


class StoreError(Exception):
    """Base error for the record store (e.g. an equals key that is not an index field)."""


class SegmentCorruptError(StoreError):
    """A frame's payload failed crc verification; subclasses StoreError so both are catchable."""


@dataclass(frozen=True)
class Location:
    """A record's physical address; value-equal so query/rebuild reconstruct it faithfully."""

    segment_id: int
    offset: int  # byte offset of the frame header within the segment file
    length: int  # payload length, excludes the 24-byte header
    seq: int
    ts_wall: float


@dataclass(frozen=True)
class RebuildReport:
    """Summary of a parallel rebuild; `workers` echoes the requested count, not spawned procs."""

    segments: int
    records: int
    seconds: float
    workers: int


class _Frame(NamedTuple):
    """One decoded log frame plus the byte offset of the next frame (its end)."""

    seq: int
    ts: float
    off: int
    length: int
    payload: bytes
    keys: dict[str, Any]
    end: int


@dataclass
class _SegMeta:
    """Per-segment bounds used to route reads and prune query fan-out; the live one is mutable."""

    seg_id: int
    seq_lo: int
    seq_hi: int
    ts_lo: float
    ts_hi: float
    records: int
    nbytes: int
    sealed: bool


# --- path + framing helpers (the only places that touch the on-disk layout / header) ----------
def _segments_dir(root: str) -> str:
    return os.path.join(root, "segments")


def _index_dir(root: str) -> str:
    return os.path.join(root, "index")


def _segment_log_path(root: str, seg_id: int) -> str:
    return os.path.join(_segments_dir(root), f"{seg_id:06d}.log")


def _shard_db_path(root: str, seg_id: int) -> str:
    return os.path.join(_index_dir(root), f"{seg_id:06d}.db")


def _manifest_path(root: str) -> str:
    return os.path.join(root, "MANIFEST.json")


def _quote(ident: str) -> str:
    """Quote a SQL identifier so an arbitrary index-field name is a safe column name."""
    return '"' + ident.replace('"', '""') + '"'


def _pack_record(seq: int, ts: float, payload: bytes, keys: Mapping[str, Any]) -> bytes:
    """Frame one record: header(len,seq,ts,crc) + payload + crc-guarded keys blob."""
    crc = zlib.crc32(payload) & 0xFFFFFFFF  # masked defensively; Py3 crc32 is already unsigned
    header = _HEADER.pack(len(payload), seq, ts, crc)
    kb = json.dumps(dict(keys), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return header + payload + _KEYHDR.pack(len(kb), zlib.crc32(kb) & 0xFFFFFFFF) + kb


def _iter_valid_frames(buf: Any, buflen: int) -> Iterator[_Frame]:
    """Walk frames from byte 0, stopping at the first short or crc-bad frame (end of valid log)."""
    off = 0
    while off + _HEADER.size <= buflen:
        length, seq, ts, crc = _HEADER.unpack(bytes(buf[off : off + _HEADER.size]))
        pstart = off + _HEADER.size
        pend = pstart + length
        if pend + _KEYHDR.size > buflen:
            break  # torn: payload or keys header runs past physical EOF
        payload = bytes(buf[pstart:pend])
        if zlib.crc32(payload) & 0xFFFFFFFF != crc:
            break  # crc mismatch marks end-of-valid-log; this frame and all after it are dropped
        klen, kcrc = _KEYHDR.unpack(bytes(buf[pend : pend + _KEYHDR.size]))
        kstart = pend + _KEYHDR.size
        kend = kstart + klen
        if kend > buflen:
            break  # torn keys tail
        kb = bytes(buf[kstart:kend])
        if zlib.crc32(kb) & 0xFFFFFFFF != kcrc:
            break
        keys: dict[str, Any] = json.loads(kb.decode("utf-8")) if kb else {}
        yield _Frame(seq, ts, off, length, payload, keys, kend)
        off = kend


# --- shard (sqlite) helpers, shared by the store and the rebuild worker ------------------------
def _apply_pragmas(con: sqlite3.Connection) -> None:
    """WAL + synchronous=NORMAL keep the INSERT off the platter; a bounded cache caps RSS."""
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute(f"PRAGMA cache_size={_SHARD_CACHE_PAGES}")


def _create_shard_schema(con: sqlite3.Connection, index_fields: tuple[str, ...]) -> None:
    """Create the `records` table (seq PK, ts_wall, seg/off/len, one TEXT col per index field)."""
    extra = "".join(f", {_quote(f)} TEXT" for f in index_fields)
    con.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        "seq INTEGER PRIMARY KEY, ts_wall REAL, seg INTEGER, off INTEGER, len INTEGER" + extra + ")"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_records_ts ON records(ts_wall)")
    for f in index_fields:
        con.execute(
            f"CREATE INDEX IF NOT EXISTS {_quote('idx_records_' + f)} "
            f"ON records({_quote(f)})"
        )


def _build_insert_sql(index_fields: tuple[str, ...]) -> str:
    """Prepared INSERT covering the fixed columns plus each declared index field."""
    cols = ["seq", "ts_wall", "seg", "off", "len"] + [_quote(f) for f in index_fields]
    return f"INSERT OR REPLACE INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"


def _row_values(seg: int, fr: _Frame, index_fields: tuple[str, ...]) -> list[Any]:
    """Project a frame into the shard row values in the INSERT's column order."""
    return [fr.seq, fr.ts, seg, fr.off, fr.length] + [fr.keys.get(f) for f in index_fields]


def _shard_ok(dbpath: str) -> bool:
    """Whether a shard is a usable sqlite db with the `records` table (cheap integrity probe)."""
    if not os.path.exists(dbpath):
        return False
    try:
        con = sqlite3.connect(dbpath)
        try:
            con.execute("SELECT MAX(seq) FROM records").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False  # garbage file (not a database) or missing table => drop and rebuild
    return True


def _drop_shard_files(dbpath: str) -> None:
    """Remove a shard db and its stale WAL sidecars so a fresh build cannot replay old frames."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(dbpath + suffix)
        except FileNotFoundError:
            pass


def _rebuild_segment(root: str, seg_id: int, index_fields: tuple[str, ...]) -> int:
    """Drop and rebuild one segment's shard from its immutable log; picklable rebuild worker.

    Top-level (no closures, no shared connections) so `multiprocessing` can spawn it across
    disjoint segments with no shared writer; returns the number of records indexed.
    """
    dbpath = _shard_db_path(root, seg_id)
    _drop_shard_files(dbpath)
    with open(_segment_log_path(root, seg_id), "rb") as fh:
        data = fh.read()
    con = sqlite3.connect(dbpath, isolation_level=None)
    try:
        _apply_pragmas(con)
        _create_shard_schema(con, index_fields)
        insert = _build_insert_sql(index_fields)
        count = 0
        for fr in _iter_valid_frames(data, len(data)):
            con.execute(insert, _row_values(seg_id, fr, index_fields))
            count += 1
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return count


def _shard_aggregate(dbpath: str) -> tuple[int, int, float | None, float | None, int]:
    """Return (seq_lo, seq_hi, ts_lo, ts_hi, count) for a shard via one indexed aggregate."""
    con = sqlite3.connect(dbpath)
    try:
        row = con.execute(
            "SELECT COALESCE(MIN(seq),0), COALESCE(MAX(seq),0), MIN(ts_wall), MAX(ts_wall), "
            "COUNT(*) FROM records"
        ).fetchone()
    finally:
        con.close()
    return int(row[0]), int(row[1]), row[2], row[3], int(row[4])


def _write_manifest_atomic(root: str, data: dict[str, Any]) -> None:
    tmp = _manifest_path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, _manifest_path(root))


def _write_manifest_from_disk(root: str, index_fields: tuple[str, ...]) -> None:
    """Recompute MANIFEST from the shards after a rebuild (high_water is derived from the log)."""
    seg_ids = _segment_ids(root)
    segs: list[dict[str, Any]] = []
    high_water = 0
    for sid in seg_ids:
        seq_lo, seq_hi, ts_lo, ts_hi, count = _shard_aggregate(_shard_db_path(root, sid))
        high_water = max(high_water, seq_hi)
        segs.append(
            {
                "id": sid, "seq_lo": seq_lo, "seq_hi": seq_hi, "ts_lo": ts_lo, "ts_hi": ts_hi,
                "records": count, "bytes": os.path.getsize(_segment_log_path(root, sid)),
                "sealed": sid != seg_ids[-1] if seg_ids else True,
            }
        )
    _write_manifest_atomic(
        root,
        {"schema_version": SCHEMA_VERSION, "index_fields": list(index_fields),
         "segments": segs, "high_water_seq": high_water},
    )


def _segment_ids(root: str) -> list[int]:
    """Sorted ids of every on-disk segment log; the highest id is always the live segment."""
    seg_dir = _segments_dir(root)
    if not os.path.isdir(seg_dir):
        return []
    return sorted(int(f[:-4]) for f in os.listdir(seg_dir) if f.endswith(".log"))


class _MmapCache:
    """Bounded LRU of sealed-segment read-only mmaps; eviction closes the mmap and its fd."""

    def __init__(self, cap: int = _MMAP_CACHE_CAP) -> None:
        self._cap = cap
        self._items: "OrderedDict[int, tuple[int, mmap.mmap]]" = OrderedDict()

    def get(self, seg_id: int, path: str) -> mmap.mmap:
        """Return a cached whole-file mmap for a sealed segment, opening one if absent."""
        hit = self._items.get(seg_id)
        if hit is not None:
            self._items.move_to_end(seg_id)
            return hit[1]
        fd = os.open(path, os.O_RDONLY)
        mm = mmap.mmap(fd, 0, prot=mmap.PROT_READ)  # length 0 maps the whole (non-empty) file
        self._items[seg_id] = (fd, mm)
        if len(self._items) > self._cap:
            _, (old_fd, old_mm) = self._items.popitem(last=False)
            old_mm.close()
            os.close(old_fd)
        return mm

    def drop(self, seg_id: int) -> None:
        hit = self._items.pop(seg_id, None)
        if hit is not None:
            hit[1].close()
            os.close(hit[0])

    def close_all(self) -> None:
        for fd, mm in self._items.values():
            mm.close()
            os.close(fd)
        self._items.clear()


class RecordStore:
    """Append-only segmented log + partitioned SQLite index; sub-ms buffered append, flat query."""

    def __init__(
        self,
        root: str,
        *,
        index_fields: tuple[str, ...] = (),
        durability: str = "buffered",
        fsync_bytes: int = DEFAULT_FSYNC_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Open (recovering) or create a store at `root`; everything after `root` is keyword-only."""
        self._root = root
        self._index_fields = tuple(index_fields)
        self._durability = durability
        self._fsync_bytes = fsync_bytes
        self._clock = clock
        self._insert_sql = _build_insert_sql(self._index_fields)
        self._segments: dict[int, _SegMeta] = {}
        self._shards: dict[int, sqlite3.Connection] = {}
        self._mmap_cache = _MmapCache()
        self._live_log: BinaryIO | None = None
        self._live_shard: sqlite3.Connection | None = None
        self._live_seg_id = 0
        self._live_bytes = 0
        self._live_records = 0
        self._unfsynced = 0
        self._high_water = 0
        self._closed = False
        os.makedirs(_segments_dir(root), exist_ok=True)
        os.makedirs(_index_dir(root), exist_ok=True)
        self._recover()

    # --- open-time recovery -------------------------------------------------------------------
    def _recover(self) -> None:
        """Rebuild in-memory state from disk: sealed shards are cheap; the live log is truth."""
        seg_ids = _segment_ids(self._root)
        if not seg_ids:
            self._create_live_segment(0, at_pos=0)
            self._high_water = 0
            return
        for sid in seg_ids[:-1]:
            self._recover_sealed(sid)
        self._recover_live(seg_ids[-1])
        self._high_water = max((m.seq_hi for m in self._segments.values()), default=0)

    def _recover_sealed(self, seg_id: int) -> None:
        """Verify a sealed segment cheaply (shard aggregate), rebuilding the shard only if broken."""
        dbpath = _shard_db_path(self._root, seg_id)
        if not _shard_ok(dbpath):
            _rebuild_segment(self._root, seg_id, self._index_fields)  # log is truth
        seq_lo, seq_hi, ts_lo, ts_hi, count = _shard_aggregate(dbpath)
        self._shards[seg_id] = self._open_shard(dbpath, create=False)
        self._segments[seg_id] = _SegMeta(
            seg_id, seq_lo, seq_hi, ts_lo or 0.0, ts_hi or 0.0, count,
            os.path.getsize(_segment_log_path(self._root, seg_id)), sealed=True,
        )

    def _recover_live(self, seg_id: int) -> None:
        """Scan the live segment to physical EOF; truncate a torn tail and reconcile its shard.

        Correction #1: the live log is *always* scanned to EOF and defines high_water even when
        MANIFEST is stale or absent (a crash without flush/close leaves MANIFEST behind the log).
        """
        path = _segment_log_path(self._root, seg_id)
        with open(path, "rb") as fh:
            data = fh.read()
        frames = list(_iter_valid_frames(data, len(data)))
        valid_end = frames[-1].end if frames else 0
        if valid_end < len(data):
            with open(path, "r+b") as fh:
                fh.truncate(valid_end)
        live_max = frames[-1].seq if frames else 0

        dbpath = _shard_db_path(self._root, seg_id)
        if not _shard_ok(dbpath):
            _rebuild_segment(self._root, seg_id, self._index_fields)
        con = self._open_shard(dbpath, create=True)
        # Drop shard rows for now-truncated frames, then re-INSERT any log frame the shard trails.
        con.execute("DELETE FROM records WHERE seq > ?", (live_max,))
        shard_max = int(con.execute("SELECT COALESCE(MAX(seq),0) FROM records").fetchone()[0])
        for fr in frames:
            if fr.seq > shard_max:
                con.execute(self._insert_sql, _row_values(seg_id, fr, self._index_fields))

        self._shards[seg_id] = con
        self._live_shard = con
        self._live_seg_id = seg_id
        self._live_bytes = valid_end
        self._live_records = len(frames)
        self._unfsynced = 0
        self._live_log = open(path, "r+b")
        self._live_log.seek(valid_end)
        if frames:
            ts_vals = [fr.ts for fr in frames]
            self._segments[seg_id] = _SegMeta(
                seg_id, frames[0].seq, live_max, min(ts_vals), max(ts_vals),
                len(frames), valid_end, sealed=False,
            )
        else:
            self._segments[seg_id] = _SegMeta(seg_id, 0, 0, 0.0, 0.0, 0, valid_end, sealed=False)

    # --- shard / segment lifecycle ------------------------------------------------------------
    def _open_shard(self, dbpath: str, *, create: bool) -> sqlite3.Connection:
        """Open a shard in autocommit WAL mode so per-append INSERTs are visible same-session."""
        con = sqlite3.connect(dbpath, isolation_level=None, check_same_thread=False)
        _apply_pragmas(con)
        if create:
            _create_shard_schema(con, self._index_fields)
        return con

    def _create_live_segment(self, seg_id: int, *, at_pos: int) -> None:
        """Open a fresh live segment log + shard for appends starting at byte `at_pos`."""
        path = _segment_log_path(self._root, seg_id)
        if not os.path.exists(path):
            open(path, "wb").close()
        self._live_log = open(path, "r+b")
        self._live_log.seek(at_pos)
        self._live_seg_id = seg_id
        self._live_bytes = at_pos
        self._live_records = 0
        con = self._open_shard(_shard_db_path(self._root, seg_id), create=True)
        self._shards[seg_id] = con
        self._live_shard = con
        self._segments[seg_id] = _SegMeta(seg_id, 0, 0, 0.0, 0.0, 0, at_pos, sealed=False)

    def _seal_live(self) -> None:
        """Fsync + checkpoint the live segment and mark it sealed (immutable, read-only shard)."""
        self._barrier()
        assert self._live_shard is not None
        self._live_shard.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._segments[self._live_seg_id].sealed = True
        self._segments[self._live_seg_id].nbytes = self._live_bytes
        if self._live_log is not None:
            self._live_log.close()
            self._live_log = None

    # --- append -------------------------------------------------------------------------------
    def append(self, payload: bytes, *, keys: Mapping[str, str | int | None]) -> Location:
        """Frame and buffer-write one record; durability is deferred to a barrier (`keys` kw-only)."""
        if self._closed:
            raise StoreError("append on a closed store")
        # Seal when the CURRENT segment has reached a bound; the seal check reads the bare module
        # globals so a monkeypatched SEGMENT_MAX_* is honored at call time.
        if self._live_records >= SEGMENT_MAX_RECORDS or self._live_bytes >= SEGMENT_MAX_BYTES:
            self._seal_live()
            self._create_live_segment(self._live_seg_id + 1, at_pos=0)
        assert self._live_log is not None and self._live_shard is not None

        seq = self._high_water + 1
        ts = float(self._clock())
        record = _pack_record(seq, ts, payload, keys)
        off = self._live_bytes
        self._live_log.write(record)
        # Drain the Python buffer to the OS (one write syscall, NO fsync — barrier/durability
        # semantics unchanged, sub-ms posture intact): the on-disk live segment always ends at a
        # frame boundary, so a second RecordStore opened over this root while we are alive never
        # sees a mid-frame torn tail (and never truncates our file), and never misses records
        # hidden in this process's user-space buffer.
        self._live_log.flush()
        self._live_bytes += len(record)
        self._live_records += 1
        self._unfsynced += len(record)
        # One autocommitted INSERT: same-session queries observe it without a flush (correction #2).
        self._live_shard.execute(
            self._insert_sql,
            [seq, ts, self._live_seg_id, off, len(payload)]
            + [self._resolve_key(keys, f) for f in self._index_fields],
        )
        self._high_water = seq

        meta = self._segments[self._live_seg_id]
        if meta.records == 0:
            meta.seq_lo, meta.ts_lo, meta.ts_hi = seq, ts, ts
        else:
            meta.ts_lo, meta.ts_hi = min(meta.ts_lo, ts), max(meta.ts_hi, ts)
        meta.seq_hi, meta.records, meta.nbytes = seq, meta.records + 1, self._live_bytes

        if self._durability == "strict" or self._unfsynced >= DEFAULT_FSYNC_BYTES:
            self._barrier()
        return Location(self._live_seg_id, off, len(payload), seq, ts)

    @staticmethod
    def _resolve_key(keys: Mapping[str, Any], field: str) -> Any:
        return keys.get(field)

    # --- durability barrier -------------------------------------------------------------------
    def _barrier(self) -> None:
        """Flush + fsync the live log and checkpoint the live shard — the durability barrier."""
        if self._live_log is not None:
            self._live_log.flush()
            os.fsync(self._live_log.fileno())
        if self._live_shard is not None:
            self._live_shard.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._unfsynced = 0

    def flush(self) -> None:
        """Durability barrier: make every appended record durable; idempotent across calls."""
        self._barrier()
        self._write_manifest()

    def close(self) -> None:
        """Barrier, persist MANIFEST, and release every mmap / shard connection / file handle."""
        if self._closed:
            return
        self._barrier()
        self._write_manifest()
        if self._live_log is not None:
            self._live_log.close()
            self._live_log = None
        for con in self._shards.values():
            con.close()
        self._mmap_cache.close_all()
        self._closed = True

    def _write_manifest(self) -> None:
        segs = [
            {"id": m.seg_id, "seq_lo": m.seq_lo, "seq_hi": m.seq_hi, "ts_lo": m.ts_lo,
             "ts_hi": m.ts_hi, "records": m.records, "bytes": m.nbytes, "sealed": m.sealed}
            for m in sorted(self._segments.values(), key=lambda m: m.seg_id)
        ]
        _write_manifest_atomic(
            self._root,
            {"schema_version": SCHEMA_VERSION, "index_fields": list(self._index_fields),
             "segments": segs, "high_water_seq": self._high_water},
        )

    # --- reads --------------------------------------------------------------------------------
    def high_water_seq(self) -> int:
        """Return the last assigned seq, or 0 when nothing valid has been appended."""
        return self._high_water

    def _segment_bytes(self, seg_id: int, off: int, n: int) -> bytes:
        """Return `n` bytes at `off` in a segment; the live segment is flushed to the OS first."""
        if seg_id == self._live_seg_id and self._live_log is not None:
            self._live_log.flush()  # push the Python buffer to the OS so pread sees it (no fsync)
            return os.pread(self._live_log.fileno(), n, off)
        mm = self._mmap_cache.get(seg_id, _segment_log_path(self._root, seg_id))
        return bytes(mm[off : off + n])

    def _read_verified(self, seg_id: int, off: int, length: int) -> bytes:
        """Read and crc-verify a payload from the log; raise SegmentCorruptError on a bad frame."""
        header = self._segment_bytes(seg_id, off, _HEADER.size)
        if len(header) < _HEADER.size:
            raise SegmentCorruptError(f"truncated frame header in segment {seg_id} at offset {off}")
        hlen, _seq, _ts, crc = _HEADER.unpack(header)
        payload = self._segment_bytes(seg_id, off + _HEADER.size, hlen)
        if zlib.crc32(payload) & 0xFFFFFFFF != crc:
            raise SegmentCorruptError(f"crc mismatch in segment {seg_id} at offset {off}")
        return payload

    def read(self, seq: int) -> bytes | None:
        """Return the payload for `seq`, or None for an unknown seq (never raises for that)."""
        if seq <= 0:
            return None
        seg_id = self._locate_segment(seq)
        if seg_id is None:
            return None
        row = self._shards[seg_id].execute(
            "SELECT seg, off, len FROM records WHERE seq=?", (seq,)
        ).fetchone()
        if row is None:
            return None
        return self._read_verified(int(row[0]), int(row[1]), int(row[2]))

    def read_at(self, loc: Location) -> bytes:
        """Return the payload at `loc`, verifying its crc; raises SegmentCorruptError if corrupt."""
        return self._read_verified(loc.segment_id, loc.offset, loc.length)

    def _locate_segment(self, seq: int) -> int | None:
        """Find the segment whose contiguous seq range contains `seq` (append-order partitioning)."""
        for meta in self._segments.values():
            if meta.records and meta.seq_lo <= seq <= meta.seq_hi:
                return meta.seg_id
        return None

    def scan(
        self, *, start_seq: int = 0, end_seq: int | None = None
    ) -> Iterator[tuple[int, bytes]]:
        """Yield (seq, payload) from the segment logs in ascending seq over half-open [lo, hi)."""
        for seg_id in sorted(self._segments):
            meta = self._segments[seg_id]
            if meta.records == 0:
                continue
            buf, buflen = self._segment_view(seg_id)
            for fr in _iter_valid_frames(buf, buflen):
                if fr.seq < start_seq:
                    continue
                if end_seq is not None and fr.seq >= end_seq:
                    return  # seqs are globally ascending, so nothing further can qualify
                yield fr.seq, fr.payload

    def _segment_view(self, seg_id: int) -> tuple[Any, int]:
        """Return a (buffer, valid_length) view over a segment for whole-segment frame walks."""
        if seg_id == self._live_seg_id and self._live_log is not None:
            self._live_log.flush()
            data = os.pread(self._live_log.fileno(), self._live_bytes, 0)
            return data, len(data)
        mm = self._mmap_cache.get(seg_id, _segment_log_path(self._root, seg_id))
        return mm, len(mm)

    # --- query --------------------------------------------------------------------------------
    def query(
        self,
        *,
        start_ts: float | None = None,
        end_ts: float | None = None,
        equals: Mapping[str, str] = {},
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[Location]:
        """Return seq-ascending Locations matching the filters; `equals` keys must be index fields."""
        for key in equals:
            if key not in self._index_fields:
                raise StoreError(f"equals key {key!r} is not an index field {self._index_fields}")

        where = ["seq > ?"]
        params: list[Any] = [after_seq]
        if start_ts is not None:
            where.append("ts_wall >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("ts_wall <= ?")
            params.append(end_ts)
        for key, value in equals.items():
            where.append(f"{_quote(key)} = ?")
            params.append(value)
        sql = (
            "SELECT seq, ts_wall, seg, off, len FROM records WHERE "
            + " AND ".join(where)
            + " ORDER BY seq LIMIT ?"
        )

        rows: list[tuple[Any, ...]] = []
        for seg_id in sorted(self._segments):
            meta = self._segments[seg_id]
            if meta.records == 0:
                continue
            if start_ts is not None and meta.ts_hi < start_ts:
                continue  # prune shards whose time range cannot overlap the window (no full scan)
            if end_ts is not None and meta.ts_lo > end_ts:
                continue
            cur = self._shards[seg_id].execute(sql, params + [limit])
            rows.extend(cur.fetchall())
        rows.sort(key=lambda r: r[0])  # merge per-shard indexed results by seq
        return [
            Location(segment_id=int(r[2]), offset=int(r[3]), length=int(r[4]),
                     seq=int(r[0]), ts_wall=float(r[1]))
            for r in rows[:limit]
        ]

    # --- parallel rebuild ---------------------------------------------------------------------
    @classmethod
    def rebuild(
        cls, root: str, *, index_fields: tuple[str, ...], workers: int = 4
    ) -> RebuildReport:
        """Rebuild every shard from the immutable logs across `workers` processes; kw-only args."""
        start = time.perf_counter()
        seg_ids = _segment_ids(root)
        if workers <= 1 or len(seg_ids) <= 1:
            counts = [_rebuild_segment(root, sid, index_fields) for sid in seg_ids]
        else:
            # spawn context + a top-level worker => no inherited fds / connections cross the boundary
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=min(workers, len(seg_ids))) as pool:
                counts = pool.starmap(
                    _rebuild_segment, [(root, sid, index_fields) for sid in seg_ids]
                )
        _write_manifest_from_disk(root, index_fields)
        return RebuildReport(
            segments=len(seg_ids), records=sum(counts),
            seconds=max(0.0, time.perf_counter() - start), workers=workers,
        )
