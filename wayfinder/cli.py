"""The `wayfinder` CLI.

    wayfinder route <prompt-file | ->  [--threshold N] [--json]
    wayfinder calibrate <dataset.jsonl> [--mode threshold|tiers|classifier]
                                        [--models a,b,c] [--out wayfinder.toml]

`route` scores a prompt and recommends a model — read-only and offline, it never
invokes a model. `calibrate` turns a labeled dataset into a `wayfinder.toml`
fragment (printed to stdout, or written with `--out`); a one-line summary goes to
stderr. Exit codes: ``0`` success, ``1`` malformed config / calibration error,
``2`` usage error (file not found, bad argument).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .calibrate import CalibrationError, calibrate, load_dataset
from .complexity import ComplexityScore, RoutingConfig, binary_tiers, score_complexity
from .config import WayfinderConfigError, load_routing_config

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_USAGE = 2


def _render_human(result: ComplexityScore) -> str:
    lines = [
        f"Recommended Model: {result.recommendation}",
        f"Complexity Score: {result.score:.2f}  (mode: {result.mode})",
    ]
    if result.tiers is not None:
        lines.append("")
        lines.append("Tiers:")
        for tier in result.tiers:
            marker = " <-" if tier.model == result.recommendation else ""
            lines.append(f"  >= {tier.min_score:.2f}  {tier.model}{marker}")
    if result.models is not None:
        lines.append("")
        lines.append("Candidates: " + ", ".join(result.models))
    lines.append("")
    lines.append("Contributing Features:")
    for name, value in result.features.items():
        lines.append(f"  {name.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def _route(text: str, *, start_dir: str, threshold: float | None) -> ComplexityScore:
    config = load_routing_config(start_dir)
    if threshold is not None:
        # An explicit per-run cut forces the binary local/cloud router.
        config = RoutingConfig(weights=config.weights, tiers=binary_tiers(threshold))
    return score_complexity(text, config=config)


def _cmd_route(args: argparse.Namespace) -> int:
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
            result = _route(
                path.read_text(encoding="utf-8"),
                start_dir=str(path.parent),
                threshold=args.threshold,
            )
    except WayfinderConfigError as exc:
        print(f"wayfinder: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_render_human(result))
    return EXIT_OK


def _cmd_calibrate(args: argparse.Namespace) -> int:
    if not Path(args.dataset).is_file():
        print(f"wayfinder: file not found: {args.dataset}", file=sys.stderr)
        return EXIT_USAGE
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    try:
        samples = load_dataset(args.dataset)
        result = calibrate(
            samples,
            args.mode,
            models_order=models,
            iterations=args.iterations,
            learning_rate=args.learning_rate,
        )
    except CalibrationError as exc:
        print(f"wayfinder: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.out:
        Path(args.out).write_text(result.toml, encoding="utf-8")
        print(f"wayfinder: wrote {args.out}", file=sys.stderr)
    else:
        print(result.toml)
    summary = ", ".join(f"{k}={v}" for k, v in result.summary.items())
    print(f"wayfinder: {summary}", file=sys.stderr)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayfinder",
        description="Deterministic prompt-complexity router.",
    )
    parser.add_argument("--version", action="version", version=f"wayfinder {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_route = sub.add_parser("route", help="Score a prompt and recommend a model.")
    p_route.add_argument("prompt", help="A prompt file, or '-' to read the prompt from stdin.")
    p_route.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Force a binary local/cloud cut (0.0-1.0) for this run, overriding config.",
    )
    p_route.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable text."
    )
    p_route.set_defaults(func=_cmd_route)

    p_cal = sub.add_parser(
        "calibrate", help="Turn a labeled JSONL dataset into a wayfinder.toml fragment."
    )
    p_cal.add_argument("dataset", help="JSONL file of {\"text\": ..., \"label\": ...} rows.")
    p_cal.add_argument(
        "--mode",
        choices=["threshold", "tiers", "classifier"],
        default="threshold",
        help="Calibration mode (default: threshold).",
    )
    p_cal.add_argument(
        "--models",
        default=None,
        help="Comma-separated model order for tiers/classifier (default: by mean score).",
    )
    p_cal.add_argument(
        "--out", default=None, help="Write the config fragment here instead of stdout."
    )
    p_cal.add_argument(
        "--iterations", type=int, default=300, help="Classifier fit iterations (default: 300)."
    )
    p_cal.add_argument(
        "--learning-rate", type=float, default=0.5, help="Classifier learning rate (default: 0.5)."
    )
    p_cal.set_defaults(func=_cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
