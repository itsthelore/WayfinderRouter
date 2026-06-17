"""Wayfinder — a deterministic prompt-complexity router.

A standalone, offline tool: hand it a prompt, get a reproducible structural
complexity score and a model recommendation. It never invokes a model — the
caller runs inference. No dependency on RAC.

Two routing modes, both deterministic given the config: ordered score *tiers*
(the binary local/cloud router is the two-tier case) and a fitted multinomial
*classifier*. Offline ``calibrate`` turns a labeled dataset into a config.

    from wayfinder import score_complexity, RoutingConfig

    result = score_complexity(prompt_text, config=RoutingConfig.binary(threshold=0.7))
    if result.recommendation == "cloud":
        ...
"""

from __future__ import annotations

from .calibrate import (
    CalibrationError,
    CalibrationResult,
    Sample,
    calibrate,
    load_dataset,
    parse_dataset,
    sweep_curve,
)
from .complexity import (
    ClassifierModel,
    ComplexityScore,
    FeatureContribution,
    RoutingConfig,
    Tier,
    explain_score,
    extract_features,
    normalized_features,
    scalar_score,
    score_complexity,
)
from .config import (
    WayfinderConfigError,
    dump_routing_toml,
    load_routing_config,
    routing_config_from_toml,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Scoring / routing.
    "score_complexity",
    "scalar_score",
    "extract_features",
    "normalized_features",
    "explain_score",
    "ComplexityScore",
    "FeatureContribution",
    "RoutingConfig",
    "Tier",
    "ClassifierModel",
    # Config.
    "load_routing_config",
    "routing_config_from_toml",
    "dump_routing_toml",
    "WayfinderConfigError",
    # Calibration.
    "calibrate",
    "sweep_curve",
    "load_dataset",
    "parse_dataset",
    "Sample",
    "CalibrationResult",
    "CalibrationError",
]
