"""Wayfinder's own configuration — `wayfinder.toml`, no RAC dependency.

Wayfinder owns its config namespace. It never reads RAC's `.rac/config.yaml`
(WF-ADR-0001). The routing boundary lives in a `wayfinder.toml` discovered by
walking up from a starting directory, parsed with the standard-library
`tomllib`. Determinism is preserved: the config is a committed file, so the same
input plus the same file yields the same answer.

Exactly one routing mode is active, in precedence order:

    [routing.classifier]            # multinomial-logistic router (WF-ADR-0003)
    [[routing.tiers]]               # ordered score bands (WF-ADR-0002)
    [routing] threshold = 0.6       # the binary local/cloud cut (the default)

`weights` (the scalar-score weights) may be set alongside any mode::

    [routing]
    threshold = 0.6
    weights = { word_count = 4.0, list_item_count = 2.5 }
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .complexity import DEFAULT_THRESHOLD as _DEFAULT_THRESHOLD
from .complexity import (
    DEFAULT_WEIGHTS,
    FEATURE_ORDER,
    ClassifierModel,
    RoutingConfig,
    Tier,
    binary_tiers,
)

CONFIG_FILE = "wayfinder.toml"
# Convenience override for one-off runs of the binary router without editing the
# file. Ignored when explicit tiers or a classifier are configured.
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
    """Read the routing config from the nearest ``wayfinder.toml`` (or defaults).

    Malformed shapes raise :class:`WayfinderConfigError` — config is never
    silently ignored.
    """
    config_path = find_config_file(start_dir)
    routing: dict = {}
    if config_path is not None:
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise WayfinderConfigError(f"cannot read {config_path}: {exc}") from exc
        section = data.get("routing")
        if section is not None and not isinstance(section, dict):
            raise WayfinderConfigError(f"{config_path}: '[routing]' must be a table")
        routing = section or {}

    where = str(config_path) if config_path else CONFIG_FILE
    weights = _parse_weights(where, routing.get("weights"))

    if "classifier" in routing:
        classifier = _parse_classifier(where, routing["classifier"])
        return RoutingConfig(weights=weights, classifier=classifier)
    if "tiers" in routing:
        return RoutingConfig(weights=weights, tiers=_parse_tiers(where, routing["tiers"]))

    threshold = _parse_threshold(where, routing.get("threshold"), _DEFAULT_THRESHOLD)
    threshold = _apply_env_threshold(threshold)
    return RoutingConfig(weights=weights, tiers=binary_tiers(threshold))


def _parse_threshold(where: str, value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise WayfinderConfigError(f"{where}: 'routing.threshold' must be a number in 0.0-1.0")
    return float(value)


def _parse_weights(where: str, value: object) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if value is None:
        return weights
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: 'routing.weights' must be a table")
    for name, weight in value.items():
        if name not in FEATURE_ORDER:
            raise WayfinderConfigError(
                f"{where}: 'routing.weights.{name}' is not a known feature "
                f"(one of {', '.join(FEATURE_ORDER)})"
            )
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise WayfinderConfigError(
                f"{where}: 'routing.weights.{name}' must be a non-negative number"
            )
        weights[name] = float(weight)
    return weights


def _parse_tiers(where: str, value: object) -> tuple[Tier, ...]:
    if not isinstance(value, list) or not value:
        raise WayfinderConfigError(f"{where}: 'routing.tiers' must be a non-empty array of tables")
    tiers: list[Tier] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise WayfinderConfigError(f"{where}: each '[[routing.tiers]]' must be a table")
        min_score = entry.get("min_score")
        model = entry.get("model")
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0.0 <= min_score <= 1.0
        ):
            raise WayfinderConfigError(f"{where}: tier 'min_score' must be a number in 0.0-1.0")
        if not isinstance(model, str) or not model:
            raise WayfinderConfigError(f"{where}: tier 'model' must be a non-empty string")
        tiers.append(Tier(float(min_score), model))
    tiers.sort(key=lambda t: t.min_score)
    if tiers[0].min_score != 0.0:
        raise WayfinderConfigError(f"{where}: the first tier must have min_score = 0.0")
    for earlier, later in zip(tiers, tiers[1:], strict=False):
        if later.min_score <= earlier.min_score:
            raise WayfinderConfigError(
                f"{where}: tier 'min_score' values must be strictly ascending"
            )
    return tuple(tiers)


def _parse_classifier(where: str, value: object) -> ClassifierModel:
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: '[routing.classifier]' must be a table")
    models = value.get("models")
    if (
        not isinstance(models, list)
        or len(models) < 2
        or not all(isinstance(m, str) and m for m in models)
        or len(set(models)) != len(models)
    ):
        raise WayfinderConfigError(
            f"{where}: 'routing.classifier.models' must be 2+ unique non-empty strings"
        )
    count = len(models)
    intercepts = _number_vector(
        where, "routing.classifier.intercepts", value.get("intercepts"), count
    )
    raw_weights = value.get("weights")
    if not isinstance(raw_weights, dict):
        raise WayfinderConfigError(f"{where}: '[routing.classifier.weights]' must be a table")
    weights: dict[str, tuple[float, ...]] = {}
    for name in FEATURE_ORDER:
        if name in raw_weights:
            weights[name] = _number_vector(
                where, f"routing.classifier.weights.{name}", raw_weights[name], count
            )
        else:
            weights[name] = (0.0,) * count
    for name in raw_weights:
        if name not in FEATURE_ORDER:
            raise WayfinderConfigError(
                f"{where}: 'routing.classifier.weights.{name}' is not a known feature"
            )
    return ClassifierModel(models=tuple(models), weights=weights, intercepts=intercepts)


def _number_vector(where: str, label: str, value: object, count: int) -> tuple[float, ...]:
    if (
        not isinstance(value, list)
        or len(value) != count
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)
    ):
        raise WayfinderConfigError(f"{where}: '{label}' must be a list of {count} numbers")
    return tuple(float(v) for v in value)


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
