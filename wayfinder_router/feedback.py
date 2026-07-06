"""Append-only label log — the feedback faucet that feeds calibration (WF-ADR-0006).

Each recorded judgment is a ``{"text", "label"}`` JSON line: the prompt and the
model that was good enough for it. That is exactly the dataset
``wayfinder-router calibrate`` (and :func:`~wayfinder_router.load_dataset`) consume, so feedback
turns straight into a routing config with no new calibration logic — the loop is
collect judgments -> calibrate -> route automatically.

Pure file IO; no model call lives here. Recalibration reads the whole log (the
deterministic batch replay), so the same log always yields the same config.

Indexed paging (WF-DESIGN-0013 §7b, WF-ROADMAP-0012): the JSONL log stays a verbatim
full-replay dataset — :func:`read_labels` with no kwargs is byte-identical to the
pre-index reader and independent of any sidecar. Additively, :func:`record_label`
maintains a sibling ``<log_path>.idx`` of ``struct``-packed ``(offset, length)`` pairs
(one per recorded line) so a *paged* ``read_labels(log, offset=.., limit=..)`` is
O(page) rather than O(file). The sidecar is best-effort: it is never consulted by the
wholesale read, and a missing or stale sidecar (detected by byte-coverage) is rebuilt
transparently on the next paged read, so results never depend on its presence. Only
lines written by :func:`record_label` are indexed; blank/whitespace lines and any
externally appended content are not, which makes the trailing bytes fail the coverage
check and force one (correct, safe) rebuild scan — correctness over a wasted scan.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

DEFAULT_LOG = "wayfinder-router-feedback.jsonl"

# Sidecar record layout: little-endian (arbitrary-but-fixed so it never silently
# changes) pair of unsigned 64-bit ints — byte offset of the raw line start and its
# byte length excluding the trailing newline. 64-bit offsets keep the unbounded
# append-only log from ever overflowing the index.
_IDX_FORMAT = "<QQ"
ENTRY_SIZE = 16  # struct.calcsize(_IDX_FORMAT)


def record_label(log_path: str, text: str, label: str) -> None:
    """Append one ``{"text", "label"}`` judgment to the log (creating it).

    Also appends the line's ``(offset, length)`` to the ``<log_path>.idx`` sidecar,
    log-line-first so a torn write leaves the log longer than the sidecar (detected as
    stale, then rebuilt) rather than a sidecar pointing past EOF. Best-effort: a sidecar
    write failure is swallowed and self-heals on the next read.
    """
    if not isinstance(text, str) or not text:
        raise ValueError("feedback needs a non-empty prompt text")
    if not isinstance(label, str) or not label:
        raise ValueError("feedback needs a non-empty label")
    line = json.dumps({"text": text, "label": label}, ensure_ascii=False)
    # Offset must be the true byte position of the new line's start. Read it from the
    # current file size (never a running in-process counter) so externally inserted
    # bytes can never desync the sidecar; never a text-mode tell() (opaque for multibyte).
    try:
        offset = os.stat(log_path).st_size
    except OSError:
        offset = 0
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    length = len(line.encode("utf-8"))
    try:
        with open(log_path + ".idx", "ab") as idx:
            idx.write(struct.pack(_IDX_FORMAT, offset, length))
    except OSError:
        pass


def read_labels(log_path: str, *, offset: int = 0, limit: int | None = None) -> list[dict]:
    """Read recorded judgments in append order; ``[]`` when the log is absent.

    With no kwargs (``offset=0, limit=None``) this is a full wholesale read — every
    judgment, blank/whitespace lines skipped and ``.strip()``ed before decode — kept
    byte-identical to the pre-index reader and independent of the sidecar (calibration
    replays the whole log, WF-ADR-0006). The keyword-only ``offset``/``limit`` select a
    ``whole[offset : offset+limit]`` page served from the ``<log_path>.idx`` sidecar
    (rebuilt if missing/stale); it falls back to a wholesale slice if the sidecar cannot
    be read or rebuilt, so a paged read never raises on a degraded sidecar.
    """
    path = Path(log_path)
    if not path.is_file():
        return []
    if offset == 0 and limit is None:
        return _read_whole(path)
    if limit is not None and limit <= 0:
        return []
    if offset < 0:
        offset = 0
    entries = _load_or_rebuild_index(path)
    end = offset + limit if limit is not None else None
    if entries is None:
        return _read_whole(path)[offset:end]
    page = entries[offset:end]
    if not page:
        return []
    try:
        rows: list[dict] = []
        with open(path, "rb") as handle:
            for off, length in page:
                handle.seek(off)
                raw = handle.read(length)
                rows.append(json.loads(raw.decode("utf-8").strip()))
        return rows
    except OSError:
        return _read_whole(path)[offset:end]


def _read_whole(path: Path) -> list[dict]:
    """Decode every non-blank line of the log, in append order (the wholesale read)."""
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _load_or_rebuild_index(path: Path) -> list[tuple[int, int]] | None:
    """Return the sidecar's ``(offset, length)`` entries, rebuilding when missing/stale.

    ``None`` on an unreadable log (the caller then serves a wholesale slice). An empty
    log yields ``[]``. Rebuild alignment with the wholesale read is exact — both skip
    blank lines in the same order.
    """
    try:
        log_size = path.stat().st_size
    except OSError:
        return None
    idx_path = Path(str(path) + ".idx")
    if log_size == 0:
        return []
    if _index_is_fresh(idx_path, log_size):
        try:
            data = idx_path.read_bytes()
        except OSError:
            data = None
        if data is not None and len(data) % ENTRY_SIZE == 0:
            return [
                _unpack_entry(data[i : i + ENTRY_SIZE])
                for i in range(0, len(data), ENTRY_SIZE)
            ]
    try:
        entries = _rebuild_index(path)
    except OSError:
        return None
    try:
        _write_index(idx_path, entries)
    except OSError:
        pass  # best-effort persist; the freshly scanned entries still serve this read
    return entries


def _index_is_fresh(idx_path: Path, log_size: int) -> bool:
    """O(1) staleness check: exists, a positive multiple of ENTRY_SIZE, coverage to EOF.

    Coverage means the last entry ends exactly at the log's final newline
    (``offset + length + 1 == log_size``); every ``record_label`` line ends in ``"\\n"``.
    Anything else (missing, ragged size, empty over a non-empty log, short coverage)
    forces a rebuild.
    """
    try:
        size = idx_path.stat().st_size
    except OSError:
        return False
    if size == 0 or size % ENTRY_SIZE != 0:
        return False
    try:
        with open(idx_path, "rb") as handle:
            handle.seek(size - ENTRY_SIZE)
            last = handle.read(ENTRY_SIZE)
    except OSError:
        return False
    if len(last) != ENTRY_SIZE:
        return False
    off, length = _unpack_entry(last)
    return off + length + 1 == log_size


def _rebuild_index(path: Path) -> list[tuple[int, int]]:
    """Scan the log once, emitting ``(line_start, byte_len_without_newline)`` per line."""
    entries: list[tuple[int, int]] = []
    pos = 0
    with open(path, "rb") as handle:
        for raw in handle:
            body = raw[:-1] if raw.endswith(b"\n") else raw
            if body.strip():
                entries.append((pos, len(body)))
            pos += len(raw)
    return entries


def _write_index(idx_path: Path, entries: list[tuple[int, int]]) -> None:
    """Overwrite the sidecar atomically via a tmp-and-rename."""
    data = b"".join(struct.pack(_IDX_FORMAT, off, length) for off, length in entries)
    tmp_path = str(idx_path) + ".tmp"
    with open(tmp_path, "wb") as handle:
        handle.write(data)
    os.replace(tmp_path, idx_path)


def _unpack_entry(chunk: bytes) -> tuple[int, int]:
    off, length = struct.unpack(_IDX_FORMAT, chunk)
    return int(off), int(length)
