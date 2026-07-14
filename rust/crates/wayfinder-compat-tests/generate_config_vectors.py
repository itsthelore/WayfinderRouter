#!/usr/bin/env python3
"""Generate Python-authoritative routing TOML compatibility vectors.

Run from the repository root:

    python3 rust/crates/wayfinder-compat-tests/generate_config_vectors.py

The JSON written to stdout is checked in as ``fixtures/routing-config.json``.
Every case isolates ``WAYFINDER_ROUTER_THRESHOLD`` so generation does not depend
on the invoking shell. Invalid cases preserve Python's full diagnostic as audit
evidence while Rust parity asserts the accept/reject outcome.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from wayfinder_router.complexity import FEATURE_ORDER, RoutingConfig, score_complexity  # noqa: E402
from wayfinder_router.config import (  # noqa: E402
    THRESHOLD_ENV,
    WayfinderConfigError,
    routing_config_from_toml,
)

WHERE = "compat-vector"
HEADING_LIST_PROMPT = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"
PROBES = (
    ("empty", ""),
    ("headings_lists", HEADING_LIST_PROMPT),
    ("lexicon", "Differential must be exactly correct?"),
    ("classifier_tie", ("word " * 80).strip()),
    ("classifier_above", ("word " * 81).strip()),
)


@contextmanager
def isolated_threshold_environment(value: str | None) -> Iterator[None]:
    previous = os.environ.pop(THRESHOLD_ENV, None)
    if value is not None:
        os.environ[THRESHOLD_ENV] = value
    try:
        yield
    finally:
        os.environ.pop(THRESHOLD_ENV, None)
        if previous is not None:
            os.environ[THRESHOLD_ENV] = previous


def summarize(config: RoutingConfig) -> dict[str, Any]:
    classifier = config.classifier
    return {
        "mode": "classifier" if classifier is not None else "tiered",
        "weights": {name: config.weights[name] for name in FEATURE_ORDER},
        "lexicon": {
            "reasoning_terms": sorted(config.lexicon.reasoning_terms),
            "constraint_terms": sorted(config.lexicon.constraint_terms),
        },
        "tiers": (
            [
                {
                    "min_score": tier.min_score,
                    "model": tier.model,
                    "cost": tier.cost,
                }
                for tier in config.tiers
            ]
            if classifier is None
            else None
        ),
        "classifier": (
            {
                "models": list(classifier.models),
                "intercepts": list(classifier.intercepts),
                "weights": {
                    name: list(classifier.weights[name]) for name in FEATURE_ORDER
                },
            }
            if classifier is not None
            else None
        ),
        "decisions": [
            decision_summary(name, text, config) for name, text in PROBES
        ],
    }


def decision_summary(name: str, text: str, config: RoutingConfig) -> dict[str, Any]:
    result = score_complexity(text, config=config)
    return {
        "name": name,
        "score": result.score,
        "recommendation": result.recommendation,
        "mode": result.mode,
    }


def generate_case(specification: dict[str, Any]) -> dict[str, Any]:
    name = specification["name"]
    text = specification["toml"]
    threshold_environment = specification.get("threshold_environment")
    result = {
        "name": name,
        "toml": text,
        "threshold_environment": threshold_environment,
    }
    with isolated_threshold_environment(threshold_environment):
        try:
            config = routing_config_from_toml(text, where=WHERE)
        except WayfinderConfigError as error:
            result["outcome"] = {
                "status": "invalid",
                "python_error": str(error),
            }
        else:
            result["outcome"] = {
                "status": "valid",
                "summary": summarize(config),
            }
    return result


CASES: tuple[dict[str, Any], ...] = (
    {"name": "defaults_with_threshold_and_environment_omitted", "toml": ""},
    {
        "name": "explicit_threshold_with_environment_omitted",
        "toml": "[routing]\nthreshold = 0.8\n",
    },
    {
        "name": "environment_overrides_configured_threshold",
        "toml": "[routing]\nthreshold = 0.8\n",
        "threshold_environment": "0.2",
    },
    {
        "name": "empty_environment_keeps_configured_threshold",
        "toml": "[routing]\nthreshold = 0.8\n",
        "threshold_environment": "",
    },
    {
        "name": "weights_merge_over_defaults",
        "toml": (
            "[routing]\n"
            "threshold = 0.1\n"
            "weights = { word_count = 9.0, reasoning_term_count = 5.0 }\n"
        ),
    },
    {
        "name": "partial_lexicon_override_is_lowercased",
        "toml": (
            "[routing]\n"
            "threshold = 0.1\n"
            "weights = { reasoning_term_count = 8.0 }\n\n"
            "[routing.lexicon]\n"
            'reasoning_terms = ["Differential", "contraindication"]\n'
        ),
    },
    {
        "name": "ordered_tiers_with_optional_cost",
        "toml": (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\ncost = 0.2\n\n"
            "[[routing.tiers]]\nmin_score = 0.15\nmodel = \"medium\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.6\nmodel = \"large\"\ncost = 1.0\n"
        ),
    },
    {
        "name": "sparse_classifier_weights_default_to_zero",
        "toml": (
            "[routing.classifier]\n"
            'models = ["small", "big"]\n'
            "intercepts = [1.0, 0.0]\n\n"
            "[routing.classifier.weights]\n"
            "word_count = [0.0, 5.0]\n"
        ),
    },
    {
        "name": "classifier_takes_precedence_over_tiers",
        "toml": (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"unused\"\n\n"
            "[routing.classifier]\n"
            'models = ["first", "second"]\n'
            "intercepts = [0.0, 0.0]\n\n"
            "[routing.classifier.weights]\n"
        ),
    },
    {
        "name": "malformed_toml_is_rejected",
        "toml": "[routing\nthreshold = 0.2\n",
    },
    {"name": "routing_scalar_is_rejected", "toml": "routing = 1\n"},
    {
        "name": "boolean_threshold_is_rejected",
        "toml": "[routing]\nthreshold = true\n",
    },
    {
        "name": "out_of_range_threshold_is_rejected",
        "toml": "[routing]\nthreshold = 1.1\n",
    },
    {
        "name": "invalid_environment_threshold_is_rejected",
        "toml": "[routing]\nthreshold = 0.8\n",
        "threshold_environment": "not-a-number",
    },
    {
        "name": "weights_scalar_is_rejected",
        "toml": "[routing]\nweights = 1\n",
    },
    {
        "name": "unknown_weight_is_rejected",
        "toml": "[routing]\nweights = { surprise = 1.0 }\n",
    },
    {
        "name": "negative_weight_is_rejected",
        "toml": "[routing]\nweights = { word_count = -1.0 }\n",
    },
    {
        "name": "lexicon_scalar_is_rejected",
        "toml": "[routing]\nlexicon = 1\n",
    },
    {
        "name": "unknown_lexicon_family_is_rejected",
        "toml": "[routing.lexicon]\nunknown_family = [\"x\"]\n",
    },
    {
        "name": "empty_lexicon_term_is_rejected",
        "toml": "[routing.lexicon]\nreasoning_terms = [\"ok\", \"\"]\n",
    },
    {
        "name": "empty_tiers_are_rejected",
        "toml": "[routing]\ntiers = []\n",
    },
    {
        "name": "tier_without_zero_start_is_rejected",
        "toml": "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"medium\"\n",
    },
    {
        "name": "duplicate_tier_boundaries_are_rejected",
        "toml": (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"one\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"two\"\n"
        ),
    },
    {
        "name": "descending_tiers_are_rejected_by_current_python",
        "toml": (
            "[[routing.tiers]]\nmin_score = 0.6\nmodel = \"large\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"medium\"\n"
        ),
    },
    {
        "name": "empty_tier_model_is_rejected",
        "toml": "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"\"\n",
    },
    {
        "name": "negative_tier_cost_is_rejected",
        "toml": (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\ncost = -1.0\n"
        ),
    },
    {
        "name": "classifier_scalar_is_rejected",
        "toml": "[routing]\nclassifier = 1\n",
    },
    {
        "name": "duplicate_classifier_models_are_rejected",
        "toml": (
            "[routing.classifier]\nmodels = [\"same\", \"same\"]\n"
            "intercepts = [0.0, 0.0]\n\n[routing.classifier.weights]\n"
        ),
    },
    {
        "name": "classifier_intercept_shape_is_rejected",
        "toml": (
            "[routing.classifier]\nmodels = [\"one\", \"two\"]\n"
            "intercepts = [0.0]\n\n[routing.classifier.weights]\n"
        ),
    },
    {
        "name": "classifier_weights_table_is_required",
        "toml": (
            "[routing.classifier]\nmodels = [\"one\", \"two\"]\n"
            "intercepts = [0.0, 0.0]\n"
        ),
    },
    {
        "name": "unknown_classifier_feature_is_rejected",
        "toml": (
            "[routing.classifier]\nmodels = [\"one\", \"two\"]\n"
            "intercepts = [0.0, 0.0]\n\n[routing.classifier.weights]\n"
            "surprise = [0.0, 1.0]\n"
        ),
    },
    {
        "name": "classifier_weight_shape_is_rejected",
        "toml": (
            "[routing.classifier]\nmodels = [\"one\", \"two\"]\n"
            "intercepts = [0.0, 0.0]\n\n[routing.classifier.weights]\n"
            "word_count = [1.0]\n"
        ),
    },
)


print(json.dumps([generate_case(case) for case in CASES], ensure_ascii=False, indent=1))
