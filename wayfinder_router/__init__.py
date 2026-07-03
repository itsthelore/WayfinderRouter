"""Wayfinder — a deterministic, offline prompt-complexity router.

Give it a prompt; get back a reproducible structural complexity score and a
model recommendation. The router never calls a model — inference stays with the
caller. Everything here is self-contained, with no runtime dependency on RAC.

Routing has two deterministic shapes, both fixed once the config is: ordered
score *tiers* (the familiar binary local/cloud split is just the two-tier case)
and a fitted multinomial *classifier*. The offline ``calibrate`` step turns a
labeled dataset into either shape.

    from wayfinder_router import score_complexity, RoutingConfig

    result = score_complexity(prompt_text, config=RoutingConfig.binary(threshold=0.7))
    if result.recommendation == "cloud":
        ...

This façade re-exports the stable public surface eagerly. That is deliberate:
``recalibrate`` names both a submodule *and* a re-exported function, so a lazy
``__getattr__`` would leave the package attribute pointing at the wrong one. The
eager imports below are all import-light (no rich/textual/fastapi), keeping
``import wayfinder_router`` cheap enough for embedding.
"""

from __future__ import annotations

__version__ = "2026.7.0"

# Scoring, feature extraction, and the routing-config value types.
from .complexity import (
    DEFAULT_LEXICON,
    ClassifierModel,
    ComplexityScore,
    FeatureContribution,
    Lexicon,
    RoutingConfig,
    Tier,
    explain_score,
    extract_features,
    normalized_features,
    scalar_score,
    score_complexity,
)

# Reading, writing, and parsing wayfinder-router.toml.
from .config import (
    WayfinderConfigError,
    dump_routing_toml,
    load_routing_config,
    routing_config_from_toml,
)

# Offline calibration: dataset -> config fragment, plus the cost/quality sweep.
from .calibrate import (
    CalibrationError,
    CalibrationResult,
    Sample,
    calibrate,
    load_dataset,
    parse_dataset,
    sweep_curve,
)

# The label faucet: append-only feedback, interactive onboarding, re-fitting.
from .feedback import read_labels, record_label
from .onboard import OnboardSummary, run_onboarding
from .recalibrate import RecalibrationResult, recalibrate

# Automated sufficiency judging and the trust gates that vet it (WF-ADR-0037).
from .judge import HeuristicJudge, Judge, Verdict, as_onboard_judge
from .sufficiency import (
    GateReport,
    cohens_kappa,
    cross_validated_accuracy,
    evaluate,
)

# Public surface, in the pinned contract order (WF-ADR-0043).
__all__ = [
    "__version__",
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
    "Lexicon",
    "DEFAULT_LEXICON",
    "load_routing_config",
    "routing_config_from_toml",
    "dump_routing_toml",
    "WayfinderConfigError",
    "calibrate",
    "sweep_curve",
    "load_dataset",
    "parse_dataset",
    "Sample",
    "CalibrationResult",
    "CalibrationError",
    "record_label",
    "read_labels",
    "run_onboarding",
    "OnboardSummary",
    "recalibrate",
    "RecalibrationResult",
    "Judge",
    "Verdict",
    "HeuristicJudge",
    "as_onboard_judge",
    "evaluate",
    "GateReport",
    "cohens_kappa",
    "cross_validated_accuracy",
]
