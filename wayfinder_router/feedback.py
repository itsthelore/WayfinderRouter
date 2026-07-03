"""Append-only judgment log that feeds calibration (WF-ADR-0006).

Every recorded judgment is a single ``{"text", "label"}`` JSON line: a prompt and
the model tier that was good enough for it. That JSONL shape is exactly what the
``calibrate`` pipeline and :func:`~wayfinder_router.load_dataset` consume, so feedback
becomes a routing config with no extra calibration logic. Pure file IO — no model
call lives here, and recalibration replays the whole log deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

# Consumers (ui.py, gateway.py) join this onto a start dir; the exact filename is contract.
DEFAULT_LOG = "wayfinder-router-feedback.jsonl"


def record_label(log_path: str, text: str, label: str) -> None:
    """Append one ``{"text", "label"}`` judgment line to ``log_path`` (created on demand)."""
    # isinstance guards are load-bearing, not decorative: callers may pass ``None`` for
    # ``label``, so a bare truthiness check would miss the type contract these errors promise.
    if not isinstance(text, str) or not text:
        raise ValueError("feedback needs a non-empty prompt text")
    if not isinstance(label, str) or not label:
        raise ValueError("feedback needs a non-empty label")
    # Dict key order (text, then label) and ensure_ascii=False are both contract:
    # unicode prompts must round-trip through the log verbatim.
    line = json.dumps({"text": text, "label": label}, ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_labels(log_path: str) -> list[dict]:
    """Return every recorded judgment in append order; ``[]`` when the log is absent."""
    path = Path(log_path)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:  # tolerate stray blank lines rather than failing the whole read
            rows.append(json.loads(stripped))
    return rows
