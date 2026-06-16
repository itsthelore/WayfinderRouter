"""The `wayfinder` CLI — score a prompt, recommend local or cloud.

Usage::

    wayfinder <prompt-file | ->  [--threshold N] [--json]

Reads a prompt from a file or stdin (``-``), scores its structural complexity,
and prints a local/cloud recommendation. Read-only and offline: it never invokes
a model. Exit codes: ``0`` a recommendation was produced (local or cloud is a
valid result), ``1`` malformed ``wayfinder.toml``, ``2`` file not found or
``--threshold`` out of range.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .complexity import FEATURE_ORDER, ComplexityScore, score_complexity
from .config import RoutingConfig, WayfinderConfigError, load_routing_config

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_USAGE = 2


def _render_human(result: ComplexityScore) -> str:
    lines = [
        f"Recommended Model: {result.recommendation.upper()}",
        f"Complexity Score: {result.score:.2f}  (threshold {result.threshold:.2f})",
        "",
        "Contributing Features:",
    ]
    lines.extend(
        f"  {name.replace('_', ' ').title()}: {result.features[name]}" for name in FEATURE_ORDER
    )
    return "\n".join(lines)


def _route(text: str, *, start_dir: str, threshold: float | None) -> ComplexityScore:
    config = load_routing_config(start_dir)
    if threshold is not None:
        config = RoutingConfig(threshold=threshold, weights=config.weights)
    return score_complexity(text, config=config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wayfinder",
        description="Score a prompt's complexity and recommend a local or cloud model.",
    )
    parser.add_argument("--version", action="version", version=f"wayfinder {__version__}")
    parser.add_argument("prompt", help="A prompt file, or '-' to read the prompt from stdin.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override the routing threshold (0.0-1.0) for this run "
        "(default: wayfinder.toml [routing] threshold, else 0.5).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable text."
    )
    args = parser.parse_args(argv)

    if args.threshold is not None and not 0.0 <= args.threshold <= 1.0:
        print("wayfinder: --threshold must be a number between 0.0 and 1.0", file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.prompt == "-":
            result = _route(sys.stdin.read(), start_dir=".", threshold=args.threshold)
        else:
            path = Path(args.prompt)
            if not path.is_file():
                print(f"wayfinder: file not found: {args.prompt}", file=sys.stderr)
                return EXIT_USAGE
            text = path.read_text(encoding="utf-8")
            result = _route(text, start_dir=str(path.parent), threshold=args.threshold)
    except WayfinderConfigError as exc:
        print(f"wayfinder: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_render_human(result))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
