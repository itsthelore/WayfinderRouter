"""Wayfinder's own configuration — `wayfinder.toml`, no RAC dependency.

Wayfinder owns its config namespace. It never reads RAC's `.rac/config.yaml`
(WF-ADR-0001). The decision boundary (threshold and feature weights) lives in a
`wayfinder.toml` discovered by walking up from a starting directory, parsed with
the standard-library `tomllib`. Determinism is preserved: the config is a
committed file, so the same input plus the same file yields the same answer.

Config shape (`wayfinder.toml`)::

    [routing]
    threshold = 0.6
    weights = { word_count = 4.0, list_item_count = 2.5 }
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .complexity import DEFAULT_WEIGHTS, FEATURE_ORDER, RoutingConfig

CONFIG_FILE = "wayfinder.toml"
# Convenience override for one-off runs without editing the file.
THRESHOLD_ENV = "WAYFINDER_THRESHOLD"


class WayfinderConfigError(Exception):
    """A `wayfinder.toml` exists but is malformed (a usage error, never ignored)."""


def find_config_file(start_dir: str) -> Path | None:
    """The nearest ``wayfinder.toml`` at or above ``start_dir``, or None."""
    current = Path(start_dir).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def load_routing_config(start_dir: str = ".") -> RoutingConfig:
    """Read the routing boundary from the nearest ``wayfinder.toml``.

    Returns the defaults when there is no config file or no ``[routing]`` table.
    ``WAYFINDER_THRESHOLD`` in the environment overrides the file's threshold.
    Malformed shapes raise :class:`WayfinderConfigError` — config is never
    silently ignored.
    """
    threshold = RoutingConfig().threshold
    weights = dict(DEFAULT_WEIGHTS)

    config_path = find_config_file(start_dir)
    if config_path is not None:
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise WayfinderConfigError(f"cannot read {config_path}: {exc}") from exc
        routing = data.get("routing")
        if routing is not None:
            if not isinstance(routing, dict):
                raise WayfinderConfigError(f"{config_path}: '[routing]' must be a table")
            threshold = _parse_threshold(config_path, routing.get("threshold"), threshold)
            weights = _parse_weights(config_path, routing.get("weights"), weights)

    threshold = _apply_env_threshold(threshold)
    return RoutingConfig(threshold=threshold, weights=weights)


def _parse_threshold(config_path: Path, value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise WayfinderConfigError(
            f"{config_path}: 'routing.threshold' must be a number between 0.0 and 1.0"
        )
    return float(value)


def _parse_weights(config_path: Path, value: object, base: dict[str, float]) -> dict[str, float]:
    weights = dict(base)
    if value is None:
        return weights
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{config_path}: 'routing.weights' must be a table")
    for name, weight in value.items():
        if name not in FEATURE_ORDER:
            raise WayfinderConfigError(
                f"{config_path}: 'routing.weights.{name}' is not a known feature "
                f"(one of {', '.join(FEATURE_ORDER)})"
            )
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise WayfinderConfigError(
                f"{config_path}: 'routing.weights.{name}' must be a non-negative number"
            )
        weights[name] = float(weight)
    return weights


def _apply_env_threshold(default: float) -> float:
    raw = os.environ.get(THRESHOLD_ENV)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise WayfinderConfigError(f"{THRESHOLD_ENV} must be a number, got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise WayfinderConfigError(f"{THRESHOLD_ENV} must be between 0.0 and 1.0, got {value}")
    return value
