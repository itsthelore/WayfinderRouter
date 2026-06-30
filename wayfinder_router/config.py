"""Wayfinder's own configuration — `wayfinder-router.toml`, no RAC dependency.

Wayfinder owns its config namespace. It never reads RAC's `.rac/config.yaml`
(WF-ADR-0001). The routing boundary lives in a `wayfinder-router.toml` discovered by
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
    DEFAULT_LEXICON,
    DEFAULT_WEIGHTS,
    FEATURE_ORDER,
    ClassifierModel,
    Lexicon,
    RoutingConfig,
    Tier,
    binary_tiers,
)

# A sane cap so a config can't smuggle a pathological term list (WF-ADR-0019 risk).
_MAX_LEXICON_TERMS = 2000

CONFIG_FILE = "wayfinder-router.toml"
# An explicit path to the config file, overriding the cwd walk-up. Lets a launchd-spawned gateway
# (whose cwd is unpredictable) and the desktop app agree on one well-known file — e.g.
# ~/Library/Application Support/Wayfinder/wayfinder-router.toml (WF-ADR-0042). `serve --config PATH`
# sets it.
CONFIG_PATH_ENV = "WAYFINDER_CONFIG"
# Convenience override for one-off runs of the binary router without editing the
# file. Ignored when explicit tiers or a classifier are configured.
THRESHOLD_ENV = "WAYFINDER_ROUTER_THRESHOLD"


class WayfinderConfigError(Exception):
    """A `wayfinder-router.toml` exists but is malformed (a usage error, never ignored)."""


def find_config_file(start_dir: str) -> Path | None:
    """The config file to load: an explicit ``WAYFINDER_CONFIG`` override, else the nearest
    ``wayfinder-router.toml`` at or above ``start_dir``, else None.

    The override is absolute: when ``WAYFINDER_CONFIG`` is set but the file is missing, the result
    is ``None`` (a clear "your configured file isn't there"), never a silent walk-up to some other
    config that happens to be above the cwd.
    """
    override = os.environ.get(CONFIG_PATH_ENV)
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    current = Path(start_dir).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def routing_config_from_toml(text: str, where: str = CONFIG_FILE) -> RoutingConfig:
    """Parse a :class:`RoutingConfig` from ``wayfinder-router.toml`` text.

    The pure, file-free parser shared by :func:`load_routing_config` and the
    config-editing UI, so a posted draft is validated exactly as a real file is.
    Malformed shapes raise :class:`WayfinderConfigError`.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WayfinderConfigError(f"{where}: invalid TOML: {exc}") from exc
    section = data.get("routing")
    if section is not None and not isinstance(section, dict):
        raise WayfinderConfigError(f"{where}: '[routing]' must be a table")
    routing = section or {}
    weights = _parse_weights(where, routing.get("weights"))
    lexicon = _parse_lexicon(where, routing["lexicon"]) if "lexicon" in routing else DEFAULT_LEXICON

    if "classifier" in routing:
        classifier = _parse_classifier(where, routing["classifier"])
        return RoutingConfig(weights=weights, classifier=classifier, lexicon=lexicon)
    if "tiers" in routing:
        return RoutingConfig(weights=weights, tiers=_parse_tiers(where, routing["tiers"]), lexicon=lexicon)

    threshold = _parse_threshold(where, routing.get("threshold"), _DEFAULT_THRESHOLD)
    threshold = _apply_env_threshold(threshold)
    return RoutingConfig(weights=weights, tiers=binary_tiers(threshold), lexicon=lexicon)


def load_routing_config(start_dir: str = ".") -> RoutingConfig:
    """Read the routing config from the nearest ``wayfinder-router.toml`` (or defaults).

    Malformed shapes raise :class:`WayfinderConfigError` — config is never
    silently ignored.
    """
    config_path = find_config_file(start_dir)
    if config_path is None:
        threshold = _apply_env_threshold(_DEFAULT_THRESHOLD)
        return RoutingConfig(tiers=binary_tiers(threshold))
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {config_path}: {exc}") from exc
    return routing_config_from_toml(text, where=str(config_path))


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


def _term_list(where: str, label: str, value: object) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(t, str) or not t.strip() for t in value):
        raise WayfinderConfigError(f"{where}: '{label}' must be a list of non-empty strings")
    if len(value) > _MAX_LEXICON_TERMS:
        raise WayfinderConfigError(f"{where}: '{label}' has more than {_MAX_LEXICON_TERMS} terms")
    return frozenset(t.strip().lower() for t in value)


def _parse_lexicon(where: str, value: object) -> Lexicon:
    """Parse ``[routing.lexicon]`` — custom trigger words (WF-ADR-0019). Either key may
    be omitted to keep its built-in default; terms are lower-cased to match the scanner."""
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: '[routing.lexicon]' must be a table")
    known = {"reasoning_terms", "constraint_terms"}
    unknown = set(value) - known
    if unknown:
        raise WayfinderConfigError(
            f"{where}: unknown 'routing.lexicon' keys: {', '.join(sorted(unknown))} "
            f"(known: {', '.join(sorted(known))})"
        )
    kwargs = {
        key: _term_list(where, f"routing.lexicon.{key}", value[key])
        for key in known
        if key in value
    }
    return Lexicon(**kwargs)


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
        cost = entry.get("cost")
        if cost is not None and (
            isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0
        ):
            raise WayfinderConfigError(f"{where}: tier 'cost' must be a non-negative number")
        tiers.append(Tier(float(min_score), model, float(cost) if cost is not None else None))
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


def _fmt_num(value: float) -> str:
    return repr(round(float(value), 6))


def _dump_tier(tier: Tier) -> str:
    lines = [
        "[[routing.tiers]]",
        f"min_score = {_fmt_num(tier.min_score)}",
        f'model = "{tier.model}"',
    ]
    if tier.cost is not None:
        lines.append(f"cost = {_fmt_num(tier.cost)}")
    return "\n".join(lines)


def dump_routing_toml(config: RoutingConfig) -> str:
    """Serialize a :class:`RoutingConfig` back to a ``wayfinder-router.toml`` fragment.

    The deterministic round-trip for the Configure surface: the output loads back
    through :func:`load_routing_config` to the same config. Non-default weights are
    emitted; the active mode (classifier or tiers) is emitted in full.
    """
    blocks: list[str] = []
    if dict(config.weights) != dict(DEFAULT_WEIGHTS):
        items = ", ".join(f"{name} = {_fmt_num(config.weights[name])}" for name in FEATURE_ORDER)
        blocks.append("[routing]\nweights = { " + items + " }")
    if config.lexicon != DEFAULT_LEXICON:
        lines = ["[routing.lexicon]"]
        for key, terms, default in (
            ("reasoning_terms", config.lexicon.reasoning_terms, DEFAULT_LEXICON.reasoning_terms),
            ("constraint_terms", config.lexicon.constraint_terms, DEFAULT_LEXICON.constraint_terms),
        ):
            if terms != default:  # emit only the overridden set, sorted for byte-stability
                lines.append(f"{key} = [" + ", ".join(f'"{t}"' for t in sorted(terms)) + "]")
        blocks.append("\n".join(lines))
    if config.classifier is not None:
        clf = config.classifier
        models = ", ".join(f'"{m}"' for m in clf.models)
        intercepts = ", ".join(_fmt_num(b) for b in clf.intercepts)
        lines = [
            "[routing.classifier]",
            f"models = [{models}]",
            f"intercepts = [{intercepts}]",
            "",
            "[routing.classifier.weights]",
        ]
        for name in FEATURE_ORDER:
            lines.append(f"{name} = [" + ", ".join(_fmt_num(w) for w in clf.weights[name]) + "]")
        blocks.append("\n".join(lines))
    else:
        blocks.append("\n\n".join(_dump_tier(t) for t in config.tiers))
    return "\n\n".join(blocks) + "\n"


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
