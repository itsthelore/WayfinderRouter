"""Router implementations for the benchmark harness (WF-ADR-0015).

A router is just a pure function ``prompt -> "local" | "cloud"`` — the whole
interface RouteLLM / RouterArena evaluate — so dropping in another router (including
a commercial or trained one, behind an adapter) is a one-function change. The
routers here are the ones that run *offline and deterministically* in CI. Adapters
for hosted/trained routers (RouteLLM, NotDiamond, …) are documented in
``benchmarks/README.md``; they require API access and so are not run here.
"""

from __future__ import annotations

from wayfinder_router import RoutingConfig, score_complexity

LOCAL = "local"
CLOUD = "cloud"


def always_local(prompt: str) -> str:
    """Lower cost bound, lower quality bound (the weak model only)."""
    return LOCAL


def always_cloud(prompt: str) -> str:
    """Upper quality bound, upper cost bound (the strong model only)."""
    return CLOUD


def length_threshold(prompt: str, words: int = 120) -> str:
    """Naive baseline: route by raw word count alone, ignoring structure.

    The control that isolates what Wayfinder's structural features add over length.
    """
    return CLOUD if len(prompt.split()) >= words else LOCAL


def deterministic_random(prompt: str) -> str:
    """A reproducible 'random' baseline: a stable FNV-1a hash of the prompt picks a side.

    Stable across runs and processes (unlike the builtin ``hash`` with a random seed).
    """
    h = 14695981039346656037
    for byte in prompt.encode("utf-8"):
        h = ((h ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return CLOUD if h & 1 else LOCAL


def wayfinder(prompt: str, threshold: float = 0.5) -> str:
    """Wayfinder's deterministic structural router at ``threshold`` (no model call)."""
    return score_complexity(prompt, config=RoutingConfig.binary(threshold=threshold)).recommendation
