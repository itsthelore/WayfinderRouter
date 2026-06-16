"""Wayfinder — a deterministic prompt-complexity router.

A standalone, offline tool: hand it a prompt, get a reproducible structural
complexity score and a ``local`` / ``cloud`` recommendation against a threshold.
It never invokes a model — the caller runs inference. No dependency on RAC.

    from wayfinder import score_complexity, RoutingConfig

    result = score_complexity(prompt_text, config=RoutingConfig(threshold=0.7))
    if result.recommendation == "cloud":
        ...
"""

from __future__ import annotations

from .complexity import (
    ComplexityScore,
    RoutingConfig,
    extract_features,
    score_complexity,
)
from .config import WayfinderConfigError, load_routing_config

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "score_complexity",
    "extract_features",
    "ComplexityScore",
    "RoutingConfig",
    "load_routing_config",
    "WayfinderConfigError",
]
