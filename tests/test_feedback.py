"""Tests for the append-only label log (WF-ADR-0006)."""

from __future__ import annotations

import pytest
from wayfinder.calibrate import calibrate, load_dataset

from wayfinder import read_labels, record_label

COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)


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


def test_log_is_directly_a_calibrate_dataset(tmp_path):
    # The whole point: feedback turns into a routing config with no new logic.
    log = str(tmp_path / "fb.jsonl")
    for _ in range(4):
        record_label(log, "hi", "local")
    for _ in range(4):
        record_label(log, COMPLEX, "cloud")
    result = calibrate(load_dataset(log), "threshold")
    assert result.summary["accuracy"] == 1.0


@pytest.mark.parametrize(("text", "label"), [("", "local"), ("hi", ""), ("hi", None)])
def test_record_rejects_empty(tmp_path, text, label):
    with pytest.raises(ValueError):
        record_label(str(tmp_path / "fb.jsonl"), text, label)
