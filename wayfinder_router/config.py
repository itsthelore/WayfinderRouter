"""Wayfinder's self-owned routing configuration (``wayfinder-router.toml``).

Wayfinder keeps its own configuration namespace and never reaches into RAC's
``.rac/config.yaml`` (WF-ADR-0001). The routing boundary is described by a
``wayfinder-router.toml`` file that is found by walking up from a starting
directory and parsed with the standard-library ``tomllib``. Because the file is
committed alongside the code, the same inputs always yield the same routing
answer — determinism is preserved.

Exactly one routing mode is live at a time, chosen in this precedence order:

    [routing.classifier]            # multinomial-logistic router (WF-ADR-0003)
    [[routing.tiers]]               # ordered score bands (WF-ADR-0002)
    [routing] threshold = 0.6       # the binary local/cloud cut (the default)

A ``weights`` table (the scalar-score coefficients) is optional and layers onto
whichever mode is active, e.g.::

    [routing]
    threshold = 0.55
    weights = { word_count = 3.0, code_block_count = 2.0 }
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .complexity import (
    DEFAULT_LEXICON,
    DEFAULT_THRESHOLD as _DEFAULT_THRESHOLD,
    DEFAULT_WEIGHTS,
    FEATURE_ORDER,
    ClassifierModel,
    Lexicon,
    RoutingConfig,
    Tier,
    binary_tiers,
)

# A config must not be able to smuggle in a pathologically large term list
# (WF-ADR-0019 risk), so each lexicon family is capped. The cap is interpolated
# into the matching error text, which makes it behavioural contract rather than a
# bare guard.
_MAX_LEXICON_TERMS = 2000

CONFIG_FILE = "wayfinder-router.toml"
# A one-shot escape hatch for running the binary router at a different cut without
# touching the file. It is consulted only on the threshold path; an explicit tiers
# ladder or a classifier ignores it.
THRESHOLD_ENV = "WAYFINDER_ROUTER_THRESHOLD"


class WayfinderConfigError(Exception):
    """A ``wayfinder-router.toml`` was found but is malformed.

    Always surfaced to the caller as a usage error; a broken config is never
    silently ignored.
    """


def _is_real_number(value: object) -> bool:
    """True for a genuine int or float, excluding ``bool`` (an ``int`` subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unit_number(value: object, message: str) -> float:
    """Coerce ``value`` to a float in ``[0.0, 1.0]`` or raise ``message``."""
    # bool is rejected explicitly (it is an int subclass) before the range test.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise WayfinderConfigError(message)
    return float(value)


def _non_negative_number(value: object, message: str) -> float:
    """Coerce ``value`` to a non-negative float or raise ``message``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise WayfinderConfigError(message)
    return float(value)


def find_config_file(start_dir: str) -> Path | None:
    """Locate the nearest ``wayfinder-router.toml`` at or above ``start_dir``.

    The start directory is resolved, then ``self`` and each ascending parent are
    probed in turn; the first directory holding the file wins, else ``None``. The
    walk terminates on its own — the filesystem root has no parent.
    """
    origin = Path(start_dir).resolve()
    for folder in (origin, *origin.parents):
        candidate = folder / CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def routing_config_from_toml(text: str, where: str = CONFIG_FILE) -> RoutingConfig:
    """Parse ``wayfinder-router.toml`` text into a :class:`RoutingConfig`.

    The pure, file-free core that :func:`load_routing_config` and the config editor
    both call, so a draft typed into the UI is validated on exactly the path a
    committed file takes. ``where`` labels the source in every diagnostic. Any
    malformed shape raises :class:`WayfinderConfigError`.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WayfinderConfigError(f"{where}: invalid TOML: {exc}") from exc

    section = data.get("routing")
    if section is not None and not isinstance(section, dict):
        raise WayfinderConfigError(f"{where}: '[routing]' must be a table")
    routing = section or {}

    # Weights and the lexicon are orthogonal to the routing mode: they layer onto
    # whichever of classifier/tiers/threshold wins below.
    weights = _parse_weights(where, routing.get("weights"))
    if "lexicon" in routing:
        lexicon = _parse_lexicon(where, routing["lexicon"])
    else:
        lexicon = DEFAULT_LEXICON

    # Precedence is classifier > tiers > the binary threshold default.
    if "classifier" in routing:
        classifier = _parse_classifier(where, routing["classifier"])
        return RoutingConfig(weights=weights, classifier=classifier, lexicon=lexicon)
    if "tiers" in routing:
        tiers = _parse_tiers(where, routing["tiers"])
        return RoutingConfig(weights=weights, tiers=tiers, lexicon=lexicon)

    threshold = _apply_env_threshold(
        _parse_threshold(where, routing.get("threshold"), _DEFAULT_THRESHOLD)
    )
    return RoutingConfig(weights=weights, tiers=binary_tiers(threshold), lexicon=lexicon)


def load_routing_config(start_dir: str = ".") -> RoutingConfig:
    """Load the routing config from the nearest ``wayfinder-router.toml``.

    With no file on the walk-up, the zero-config binary router is returned — the
    environment threshold override still applies. A read failure is wrapped as
    :class:`WayfinderConfigError`; a readable file is handed to
    :func:`routing_config_from_toml`.
    """
    config_path = find_config_file(start_dir)
    if config_path is None:
        return RoutingConfig(tiers=binary_tiers(_apply_env_threshold(_DEFAULT_THRESHOLD)))
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WayfinderConfigError(f"cannot read {config_path}: {exc}") from exc
    return routing_config_from_toml(text, where=str(config_path))


def _parse_threshold(where: str, value: object, default: float) -> float:
    if value is None:
        return default
    return _unit_number(value, f"{where}: 'routing.threshold' must be a number in 0.0-1.0")


def _parse_weights(where: str, value: object) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if value is None:
        return weights
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: 'routing.weights' must be a table")
    for name, given in value.items():
        if name not in FEATURE_ORDER:
            raise WayfinderConfigError(
                f"{where}: 'routing.weights.{name}' is not a known feature "
                f"(one of {', '.join(FEATURE_ORDER)})"
            )
        weights[name] = _non_negative_number(
            given, f"{where}: 'routing.weights.{name}' must be a non-negative number"
        )
    return weights


def _term_list(where: str, label: str, value: object) -> frozenset[str]:
    if not isinstance(value, list) or any(
        not isinstance(term, str) or not term.strip() for term in value
    ):
        raise WayfinderConfigError(f"{where}: '{label}' must be a list of non-empty strings")
    if len(value) > _MAX_LEXICON_TERMS:
        raise WayfinderConfigError(f"{where}: '{label}' has more than {_MAX_LEXICON_TERMS} terms")
    # Fold to lower-case so custom terms match the scanner, which lower-cases the
    # prompt before it counts.
    return frozenset(term.strip().lower() for term in value)


def _parse_lexicon(where: str, value: object) -> Lexicon:
    """Parse the ``[routing.lexicon]`` trigger-word overrides (WF-ADR-0019).

    Either family (``reasoning_terms``, ``constraint_terms``) may be omitted to
    keep its built-in default; an unrecognised key is rejected so a typo cannot
    quietly switch a family off.
    """
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: '[routing.lexicon]' must be a table")
    known = {"reasoning_terms", "constraint_terms"}
    unknown = set(value) - known
    if unknown:
        raise WayfinderConfigError(
            f"{where}: unknown 'routing.lexicon' keys: {', '.join(sorted(unknown))} "
            f"(known: {', '.join(sorted(known))})"
        )
    families = {
        family: _term_list(where, f"routing.lexicon.{family}", value[family])
        for family in known
        if family in value
    }
    # Omitted families fall through to the Lexicon dataclass defaults.
    return Lexicon(**families)


def _parse_tier_entry(where: str, entry: object) -> Tier:
    if not isinstance(entry, dict):
        raise WayfinderConfigError(f"{where}: each '[[routing.tiers]]' must be a table")
    min_score = _unit_number(
        entry.get("min_score"), f"{where}: tier 'min_score' must be a number in 0.0-1.0"
    )
    model = entry.get("model")
    if not isinstance(model, str) or not model:
        raise WayfinderConfigError(f"{where}: tier 'model' must be a non-empty string")
    raw_cost = entry.get("cost")
    cost = (
        None
        if raw_cost is None
        else _non_negative_number(raw_cost, f"{where}: tier 'cost' must be a non-negative number")
    )
    return Tier(min_score, model, cost)


def _parse_tiers(where: str, value: object) -> tuple[Tier, ...]:
    if not isinstance(value, list) or not value:
        raise WayfinderConfigError(f"{where}: 'routing.tiers' must be a non-empty array of tables")
    tiers = [_parse_tier_entry(where, entry) for entry in value]
    # Order by min_score, then assert the ladder invariants: it must start at 0.0
    # and rise strictly — the strict ``<=`` pass is what rejects duplicate scores.
    tiers.sort(key=lambda tier: tier.min_score)
    if tiers[0].min_score != 0.0:
        raise WayfinderConfigError(f"{where}: the first tier must have min_score = 0.0")
    for lower, upper in zip(tiers, tiers[1:], strict=False):
        if upper.min_score <= lower.min_score:
            raise WayfinderConfigError(
                f"{where}: tier 'min_score' values must be strictly ascending"
            )
    return tuple(tiers)


def _valid_model_names(models: list) -> bool:
    # 2+ entries, each a non-empty string, no duplicates. The all-strings check
    # runs before ``set(models)`` so a non-hashable entry can't raise mid-check.
    return (
        len(models) >= 2
        and all(isinstance(m, str) and m for m in models)
        and len(set(models)) == len(models)
    )


def _parse_classifier(where: str, value: object) -> ClassifierModel:
    if not isinstance(value, dict):
        raise WayfinderConfigError(f"{where}: '[routing.classifier]' must be a table")
    models = value.get("models")
    if not isinstance(models, list) or not _valid_model_names(models):
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

    # Give every known feature a vector — the supplied one, or a zero vector when
    # omitted — and only then reject any leftover key. Filling before checking is
    # contract.
    weights: dict[str, tuple[float, ...]] = {
        name: (
            _number_vector(where, f"routing.classifier.weights.{name}", raw_weights[name], count)
            if name in raw_weights
            else (0.0,) * count
        )
        for name in FEATURE_ORDER
    }
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
        or any(not _is_real_number(v) for v in value)
    ):
        raise WayfinderConfigError(f"{where}: '{label}' must be a list of {count} numbers")
    return tuple(float(v) for v in value)


def _fmt_num(value: float) -> str:
    # Byte-stable float formatting for emitted TOML: round to 6 dp, then repr. The
    # float() cast is deliberate — an int-valued field must still emit "1.0"/"0.0",
    # not "1"/"0" — and is intentionally NOT shared with calibrate._fmt, which has
    # no cast; unifying the two would change this module's output bytes.
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


def _dump_weights(weights: dict[str, float]) -> str:
    # Always in canonical FEATURE_ORDER, rendered as a single inline table.
    inline = ", ".join(f"{name} = {_fmt_num(weights[name])}" for name in FEATURE_ORDER)
    return "[routing]\nweights = { " + inline + " }"


def _dump_lexicon(lexicon: Lexicon) -> str:
    lines = ["[routing.lexicon]"]
    families = (
        ("reasoning_terms", lexicon.reasoning_terms, DEFAULT_LEXICON.reasoning_terms),
        ("constraint_terms", lexicon.constraint_terms, DEFAULT_LEXICON.constraint_terms),
    )
    # One line per overridden family only; terms sorted for byte stability.
    for key, terms, default in families:
        if terms != default:
            lines.append(f"{key} = [" + ", ".join(f'"{t}"' for t in sorted(terms)) + "]")
    return "\n".join(lines)


def _dump_classifier(clf: ClassifierModel) -> str:
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
    return "\n".join(lines)


def dump_routing_toml(config: RoutingConfig) -> str:
    """Serialize a :class:`RoutingConfig` back to a ``wayfinder-router.toml`` fragment.

    The deterministic inverse used by the Configure surface: the text parses back
    through :func:`routing_config_from_toml` to an equivalent config. Weights and
    lexicon families are emitted only when they diverge from the defaults; the
    active mode (classifier or tier ladder) is emitted in full. Blocks are joined
    by a blank line and the fragment ends in a newline.
    """
    blocks: list[str] = []
    if dict(config.weights) != dict(DEFAULT_WEIGHTS):
        blocks.append(_dump_weights(config.weights))
    if config.lexicon != DEFAULT_LEXICON:
        blocks.append(_dump_lexicon(config.lexicon))
    if config.classifier is not None:
        blocks.append(_dump_classifier(config.classifier))
    else:
        blocks.append("\n\n".join(_dump_tier(t) for t in config.tiers))
    return "\n\n".join(blocks) + "\n"


def _apply_env_threshold(default: float) -> float:
    """Overlay the ``WAYFINDER_ROUTER_THRESHOLD`` override onto ``default``.

    An unset variable or the exact empty string leaves ``default`` untouched.
    Anything else — whitespace included — is fed to ``float()`` and range-checked,
    so a stray value surfaces as an error rather than a silent fallback.
    """
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
