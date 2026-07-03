"""Re-fit the routing config from the accumulated feedback log (WF-ADR-0007).

The batch counterpart to online serving: replay every recorded label, calibrate
a fresh routing section, and rewrite ``wayfinder-router.toml`` — while carrying the
existing ``[gateway]`` mapping (endpoints and their ``api_key_env`` names, never a
secret) through untouched, so the running gateway hot-reloads without losing its
wiring.

This is thin orchestration over pieces that already exist (``read_labels`` +
``load_dataset`` + ``calibrate`` + ``dump_gateway_toml``); no model is ever
called from here. It runs only when a human asks — ``wayfinder-router recalibrate``
(CLI or cron) or the UI button — never inside the serving loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .calibrate import calibrate, load_dataset
from .feedback import read_labels
from .gateway import GatewayConfig, dump_gateway_toml, gateway_config_from_toml

# A run on almost no data is noise, so hold off until at least this many labels
# have accumulated.
DEFAULT_MIN_LABELS = 2


@dataclass
class RecalibrationResult:
    """What one recalibration run did (or why it declined to do anything)."""

    written: bool
    label_count: int
    summary: dict | None = None
    toml: str | None = None
    reason: str | None = None  # populated instead of a write when skipped


def _load_gateway(config_path: Path) -> GatewayConfig:
    """Read just the ``[gateway]`` mapping from an existing config, if present.

    The routing section is discarded — we are about to replace it — but every
    gateway model (and its ``api_key_env``) is round-tripped through the dataclass
    so it survives the rewrite. A missing file yields an empty config.
    """
    if not config_path.is_file():
        return GatewayConfig()
    text = config_path.read_text(encoding="utf-8")
    return gateway_config_from_toml(text, where=str(config_path))


def _render(result_toml: str, summary: dict, gateway: GatewayConfig) -> str:
    """Assemble the new file: a provenance header, the fresh routing, the gateway.

    Deterministic by construction — no timestamps, no set iteration — so the same
    labels always produce byte-identical output.
    """
    trail = ", ".join(f"{key}={value}" for key, value in summary.items())
    sections = [f"# recalibrated from feedback: {trail}", result_toml.rstrip("\n")]
    if gateway.models:  # only re-emit a [gateway] block when there is one to keep
        sections.append(dump_gateway_toml(gateway))
    return "\n\n".join(sections) + "\n"


def recalibrate(
    log_path: str,
    config_path: str,
    mode: str = "threshold",
    min_labels: int = DEFAULT_MIN_LABELS,
) -> RecalibrationResult:
    """Re-fit the routing in ``config_path`` from the labels in ``log_path``.

    Declines silently (no write, a ``reason`` set) when fewer than ``min_labels``
    rows exist, so a cron tick or button press on a near-empty log is harmless.
    Beyond that gate, a calibration failure — e.g. ``threshold`` mode with only
    one arm represented — surfaces as a :class:`CalibrationError` for the caller,
    and a malformed existing config surfaces as a config error.
    """
    labels = read_labels(log_path)
    if len(labels) < min_labels:
        return RecalibrationResult(
            written=False,
            label_count=len(labels),
            reason=f"need >= {min_labels} labels, have {len(labels)}",
        )

    fit = calibrate(load_dataset(log_path), mode)

    destination = Path(config_path)
    gateway = _load_gateway(destination)
    text = _render(fit.toml, fit.summary, gateway)
    destination.write_text(text, encoding="utf-8")

    return RecalibrationResult(
        written=True,
        label_count=len(labels),
        summary=fit.summary,
        toml=text,
    )
