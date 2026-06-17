"""Recalibration — re-fit the routing config from the feedback log (WF-ADR-0007).

The deterministic batch replay WF-ADR-0006 recorded: read the whole label log,
calibrate, and write the routing section of ``wayfinder.toml`` — preserving the
``[gateway]`` section (the endpoint mapping and its ``api_key_env`` names, never a
secret) so the running gateway keeps working. The gateway hot-reloads the new file.

Pure orchestration over existing pieces (``read_labels`` + ``load_dataset`` +
``calibrate`` + ``dump_gateway_toml``); no model call lives here. Triggered by
``wayfinder recalibrate`` (CLI / cron) or the UI button — never automatically inside
the serving process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .calibrate import calibrate, load_dataset
from .feedback import read_labels
from .gateway import GatewayConfig, dump_gateway_toml, gateway_config_from_toml

DEFAULT_MIN_LABELS = 2


@dataclass
class RecalibrationResult:
    """The outcome of a recalibration run."""

    written: bool
    label_count: int
    summary: dict | None = None
    toml: str | None = None
    reason: str | None = None  # why it was skipped, when ``written`` is False


def recalibrate(
    log_path: str,
    config_path: str,
    mode: str = "threshold",
    min_labels: int = DEFAULT_MIN_LABELS,
) -> RecalibrationResult:
    """Re-fit the routing config in ``config_path`` from the labels in ``log_path``.

    A no-op (no write) when the log holds fewer than ``min_labels`` rows, so a
    scheduled run or a button click on a near-empty log is safe. May raise
    :class:`~wayfinder.CalibrationError` (e.g. ``threshold`` mode needs both arms
    represented) or :class:`~wayfinder.WayfinderConfigError` (a malformed existing
    config) — callers report those.
    """
    rows = read_labels(log_path)
    if len(rows) < min_labels:
        return RecalibrationResult(
            written=False,
            label_count=len(rows),
            reason=f"need >= {min_labels} labels, have {len(rows)}",
        )

    result = calibrate(load_dataset(log_path), mode)

    config_file = Path(config_path)
    gateway = GatewayConfig()
    if config_file.is_file():
        gateway = gateway_config_from_toml(
            config_file.read_text(encoding="utf-8"), where=str(config_file)
        )

    summary_bits = ", ".join(f"{k}={v}" for k, v in result.summary.items())
    parts = [f"# recalibrated from feedback: {summary_bits}", result.toml.rstrip("\n")]
    if gateway.models:
        parts.append(dump_gateway_toml(gateway))
    text = "\n\n".join(parts) + "\n"
    config_file.write_text(text, encoding="utf-8")

    return RecalibrationResult(
        written=True, label_count=len(rows), summary=result.summary, toml=text
    )
