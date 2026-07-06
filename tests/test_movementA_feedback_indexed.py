"""Spec-first contract tests for the indexed/bounded feedback store (Movement A).

Pins WF-DESIGN-0013 §7(b) "Indexed/bounded feedback store" under **Contract invariant 12**.
The unchanged contract is exactly what ``tests/test_feedback.py`` pins on ``feedback.py``
(WF-ADR-0006): ``record_label(log_path, text, label)`` appends a ``{"text","label"}`` JSONL
line; ``read_labels(log_path)`` returns every judgment in append order, ``[]`` when the log is
absent, tolerating blank lines. §7b adds a ``<log_path>.idx`` sidecar of ``struct``-packed
``(offset, length)`` per line and a NEW keyword-only paging signature
``read_labels(log_path, *, offset=0, limit=None)`` — **additive**, so the existing no-kwarg call
stays byte-identical (WF-ADR-0044). The JSONL log is kept verbatim (calibration still reads it as
a full replay, WF-ADR-0006); the sidecar only makes reads O(page) instead of O(file).

CHECKPOINT QUESTIONS (construction-surface assumptions — approve before a builder builds):
  1. PAGING SIGNATURE: pinned as keyword-only ``read_labels(log_path, *, offset=0, limit=None)``
     on the SAME module function (not a new function name), so today's positional
     ``read_labels(log)`` is byte-identical. Confirmed as the surface?
  2. SIDECAR PATH: pinned as ``<log_path>.idx`` (sibling file, design-named). Confirmed?
  3. STALE/MISSING SIDECAR: the design says the sidecar is "kept" but does not fully pin recovery.
     This suite pins the STRICTEST reading — a missing OR stale (shorter-than-log) sidecar is
     rebuilt transparently on read, so results never depend on sidecar presence. Is transparent
     rebuild the intended behavior, or is a missing sidecar an error / a silent full-file
     fallback? Strict rebuild is assumed.
  4. record_label SIDE EFFECT: pinned that ``record_label`` appends one sidecar entry alongside
     the log line (so the sidecar tracks the log without a separate reindex step). Confirmed?
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder_router import read_labels, record_label
from wayfinder_router import feedback

COMPLEX = "# Plan\n\n## Steps\n\n" + "".join(f"- step {i}\n" for i in range(12))


# --- unchanged contract: record/read round-trip in append order (test_feedback parity) --
def test_record_and_read_round_trip(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    record_label(log, "hi", "local")
    record_label(log, COMPLEX, "cloud")
    assert read_labels(log) == [
        {"text": "hi", "label": "local"},
        {"text": COMPLEX, "label": "cloud"},
    ]


def test_read_absent_log_is_empty(tmp_path):
    assert read_labels(str(tmp_path / "nope.jsonl")) == []


def test_blank_line_tolerance(tmp_path):
    # The log may carry blank lines (partial write, manual edit); reads skip them, as today.
    log = Path(tmp_path / "fb.jsonl")
    record_label(str(log), "a", "local")
    with open(log, "a", encoding="utf-8") as h:
        h.write("\n   \n")  # blank + whitespace-only lines
    record_label(str(log), "b", "cloud")
    assert read_labels(str(log)) == [
        {"text": "a", "label": "local"},
        {"text": "b", "label": "cloud"},
    ]


# --- existing callers unchanged: the no-kwarg call is byte-identical (WF-ADR-0044) ------
def test_existing_no_kwarg_call_is_byte_identical(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    for i in range(5):
        record_label(log, f"t{i}", "local")
    # Wholesale read via the RAW log text (pre-sidecar semantics) must equal read_labels(log).
    import json
    raw = [
        json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert read_labels(log) == raw
    # And the default paged call (offset=0, limit=None) equals the wholesale read.
    assert feedback.read_labels(log, offset=0, limit=None) == read_labels(log)


# --- NEW paged reads match wholesale slices via the sidecar -----------------------------
def test_paged_reads_match_wholesale_slices(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    for i in range(10):
        record_label(log, f"t{i}", "local" if i % 2 else "cloud")
    whole = read_labels(log)
    assert feedback.read_labels(log, offset=2, limit=3) == whole[2:5]
    assert feedback.read_labels(log, offset=0, limit=4) == whole[0:4]
    assert feedback.read_labels(log, offset=7) == whole[7:]        # limit=None -> to end
    assert feedback.read_labels(log, offset=99, limit=5) == []     # past end -> empty page
    assert feedback.read_labels(log, offset=0, limit=0) == []      # zero-length page


# --- the sidecar exists and record_label maintains it (CHECKPOINT 4) --------------------
def test_record_label_maintains_sidecar(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    record_label(log, "a", "local")
    record_label(log, "b", "cloud")
    assert Path(log + ".idx").is_file()  # §7b: sidecar sibling <log_path>.idx (CHECKPOINT 2)


# --- strictest recovery: missing/stale sidecar is rebuilt transparently (CHECKPOINT 3) --
def test_missing_or_stale_sidecar_is_rebuilt_transparently(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    for i in range(6):
        record_label(log, f"t{i}", "local")
    whole = read_labels(log)
    # (a) sidecar deleted entirely -> a paged read still returns the correct slice.
    Path(log + ".idx").unlink()
    assert feedback.read_labels(log, offset=1, limit=2) == whole[1:3]
    # (b) sidecar truncated / stale (shorter than the log) -> rebuilt, read stays correct.
    Path(log + ".idx").write_bytes(b"")
    assert feedback.read_labels(log, offset=3, limit=2) == whole[3:5]
    assert read_labels(log) == whole  # wholesale read is unaffected by sidecar state


@pytest.mark.parametrize(("text", "label"), [("", "local"), ("hi", ""), ("hi", None)])
def test_record_rejects_empty(tmp_path, text, label):
    with pytest.raises(ValueError):
        record_label(str(tmp_path / "fb.jsonl"), text, label)
