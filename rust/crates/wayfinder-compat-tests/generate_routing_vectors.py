#!/usr/bin/env python3
"""Generate Python-authoritative routing boundary vectors.

Run from the repository root:

    python3 rust/crates/wayfinder-compat-tests/generate_routing_vectors.py

The JSON written to stdout is checked in as ``fixtures/routing-boundaries.json``.
Keeping generation here makes the classifier and inclusive tier-boundary cases
reproducible without teaching the Rust test suite to invoke Python.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from wayfinder_router.complexity import (  # noqa: E402
    ClassifierModel,
    RoutingConfig,
    Tier,
    score_complexity,
)

HEADING_LIST_PROMPT = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"
WORD_CLASSIFIER = ClassifierModel(
    models=("small", "big"),
    weights={"word_count": (0.0, 5.0)},
    intercepts=(1.0, 0.0),
)


def binary_case(name: str, text: str, threshold: float) -> dict[str, Any]:
    config = RoutingConfig.binary(threshold=threshold)
    return case(
        name,
        text,
        config,
        {"mode": "binary", "threshold": threshold},
    )


def classifier_case(
    name: str, text: str, classifier: ClassifierModel
) -> dict[str, Any]:
    config = RoutingConfig(classifier=classifier)
    serialized_weights = {
        feature: list(weights) for feature, weights in classifier.weights.items()
    }
    return case(
        name,
        text,
        config,
        {
            "mode": "classifier",
            "models": list(classifier.models),
            "intercepts": list(classifier.intercepts),
            "weights": serialized_weights,
        },
    )


def case(
    name: str,
    text: str,
    config: RoutingConfig,
    serialized_config: dict[str, Any],
) -> dict[str, Any]:
    result = score_complexity(text, config=config)
    return {
        "name": name,
        "text": text,
        "config": serialized_config,
        "score": result.score,
        "recommendation": result.recommendation,
        "features": dict(result.features),
    }


def vectors() -> list[dict[str, Any]]:
    three_tiers = (
        Tier(0.0, "small"),
        Tier(0.15, "medium"),
        Tier(0.6, "large"),
    )
    tie_classifier = ClassifierModel(
        models=("first", "second", "third"),
        weights={},
        intercepts=(0.0, 0.0, 0.0),
    )
    return [
        binary_case("binary_zero_is_inclusive", "", 0.0),
        binary_case(
            "binary_score_equality_is_inclusive", HEADING_LIST_PROMPT, 0.15
        ),
        binary_case(
            "binary_cut_above_score_stays_lower", HEADING_LIST_PROMPT, 0.1500001
        ),
        case(
            "three_tier_score_equality_uses_higher_band",
            HEADING_LIST_PROMPT,
            RoutingConfig(tiers=three_tiers),
            {
                "mode": "tiers",
                "tiers": [
                    {"min_score": tier.min_score, "model": tier.model}
                    for tier in three_tiers
                ],
            },
        ),
        classifier_case(
            "classifier_equal_logits_use_first_model", "Say hello.", tie_classifier
        ),
        classifier_case(
            "classifier_feature_boundary_tie_uses_first_model",
            ("word " * 80).strip(),
            WORD_CLASSIFIER,
        ),
        classifier_case(
            "classifier_feature_above_boundary_uses_second_model",
            ("word " * 81).strip(),
            WORD_CLASSIFIER,
        ),
        classifier_case(
            "classifier_normalization_saturates",
            ("word " * 450).strip(),
            WORD_CLASSIFIER,
        ),
    ]


print(json.dumps(vectors(), ensure_ascii=False, indent=1))
