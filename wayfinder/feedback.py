"""Append-only label log — the feedback faucet that feeds calibration (WF-ADR-0006).

Each recorded judgment is a ``{"text", "label"}`` JSON line: the prompt and the
model that was good enough for it. That is exactly the dataset
``wayfinder calibrate`` (and :func:`~wayfinder.load_dataset`) consume, so feedback
turns straight into a routing config with no new calibration logic — the loop is
collect judgments -> calibrate -> route automatically.

Pure file IO; no model call lives here. Recalibration reads the whole log (the
deterministic batch replay), so the same log always yields the same config.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LOG = "wayfinder-feedback.jsonl"


def record_label(log_path: str, text: str, label: str) -> None:
    """Append one ``{"text", "label"}`` judgment to the log (creating it)."""
    if not isinstance(text, str) or not text:
        raise ValueError("feedback needs a non-empty prompt text")
    if not isinstance(label, str) or not label:
        raise ValueError("feedback needs a non-empty label")
    line = json.dumps({"text": text, "label": label}, ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_labels(log_path: str) -> list[dict]:
    """Read every recorded judgment, in append order; ``[]`` when the log is absent."""
    path = Path(log_path)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows
