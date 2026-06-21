"""The `wayfinder-router` CLI.

    wayfinder-router route <prompt-file | ->  [--threshold N] [--json]
    wayfinder-router calibrate <dataset.jsonl> [--mode threshold|tiers|classifier]
                                        [--models a,b,c] [--out wayfinder-router.toml]

`route` scores a prompt and recommends a model — read-only and offline, it never
invokes a model. `calibrate` turns a labeled dataset into a `wayfinder-router.toml`
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
from .complexity import (
    ComplexityScore,
    RoutingConfig,
    binary_tiers,
    explain_score,
    score_complexity,
)
from .config import WayfinderConfigError, load_routing_config

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_USAGE = 2

# Seconds to wait before opening the browser for `chat`, so the server is listening first.
_CHAT_OPEN_DELAY = 1.0


def _render_human(result: ComplexityScore, weights: dict[str, float] | None = None) -> str:
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
    if weights is not None:
        # --explain: show each feature's share of the score (value, normalized,
        # weight, contribution), so the recommendation is auditable.
        lines += ["", "Score Breakdown (feature: value  norm x weight = contribution):"]
        for fc in explain_score(result.features, weights):
            lines.append(
                f"  {fc.name:<18} {fc.value:>5}  "
                f"{fc.normalized:.2f} x {fc.weight:<4g} = {fc.contribution:.3f}"
            )
    else:
        lines.append("")
        lines.append("Contributing Features:")
        for name, value in result.features.items():
            lines.append(f"  {name.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def _route(
    text: str, *, start_dir: str, threshold: float | None
) -> tuple[ComplexityScore, RoutingConfig]:
    config = load_routing_config(start_dir)
    if threshold is not None:
        # An explicit per-run cut forces the binary local/cloud router.
        config = RoutingConfig(weights=config.weights, tiers=binary_tiers(threshold))
    return score_complexity(text, config=config), config


def _cmd_route(args: argparse.Namespace) -> int:
    if args.threshold is not None and not 0.0 <= args.threshold <= 1.0:
        print("wayfinder-router: --threshold must be a number between 0.0 and 1.0", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.prompt == "-":
            result, config = _route(sys.stdin.read(), start_dir=".", threshold=args.threshold)
        else:
            path = Path(args.prompt)
            if not path.is_file():
                print(f"wayfinder-router: file not found: {args.prompt}", file=sys.stderr)
                return EXIT_USAGE
            result, config = _route(
                path.read_text(encoding="utf-8"),
                start_dir=str(path.parent),
                threshold=args.threshold,
            )
    except WayfinderConfigError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_render_human(result, weights=config.weights if args.explain else None))
    return EXIT_OK


def _parse_costs(raw: str | None) -> dict[str, float] | None:
    """Parse ``--costs local=0.2,cloud=1.0`` into a label->cost map (or None)."""
    if not raw:
        return None
    costs: dict[str, float] = {}
    for item in raw.split(","):
        label, _, value = item.partition("=")
        label = label.strip()
        if not label or not value.strip():
            raise CalibrationError(f"--costs must be label=number pairs, got {item!r}")
        try:
            costs[label] = float(value)
        except ValueError as exc:
            raise CalibrationError(f"--costs value for {label!r} must be a number") from exc
    return costs


def _parse_weights_arg(raw: str | None) -> dict[str, float] | None:
    """Parse ``--weights reasoning_term_count=5,math_symbol_count=3`` into a
    feature->weight map (or None). Feature names are validated by the calibrator."""
    if not raw:
        return None
    weights: dict[str, float] = {}
    for item in raw.split(","):
        name, _, value = item.partition("=")
        name = name.strip()
        if not name or not value.strip():
            raise CalibrationError(f"--weights must be feature=number pairs, got {item!r}")
        try:
            weights[name] = float(value)
        except ValueError as exc:
            raise CalibrationError(f"--weights value for {name!r} must be a number") from exc
    return weights


def _cmd_calibrate(args: argparse.Namespace) -> int:
    if not Path(args.dataset).is_file():
        print(f"wayfinder-router: file not found: {args.dataset}", file=sys.stderr)
        return EXIT_USAGE
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    try:
        costs = _parse_costs(args.costs)
        weights = _parse_weights_arg(args.weights)
        samples = load_dataset(args.dataset)
        result = calibrate(
            samples,
            args.mode,
            models_order=models,
            iterations=args.iterations,
            l2=args.l2,
            objective=args.objective,
            costs=costs,
            target_savings=args.target_savings,
            weights=weights,
        )
    except CalibrationError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.out:
        Path(args.out).write_text(result.toml, encoding="utf-8")
        print(f"wayfinder-router: wrote {args.out}", file=sys.stderr)
    else:
        print(result.toml)
    summary = ", ".join(f"{k}={v}" for k, v in result.summary.items())
    print(f"wayfinder-router: {summary}", file=sys.stderr)
    return EXIT_OK


def _cmd_recalibrate(args: argparse.Namespace) -> int:
    from .recalibrate import recalibrate

    try:
        result = recalibrate(args.log, args.out, mode=args.mode, min_labels=args.min_labels)
    except (CalibrationError, WayfinderConfigError) as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if not result.written:
        print(f"wayfinder-router: skipped — {result.reason}", file=sys.stderr)
        return EXIT_OK
    summary = ", ".join(f"{k}={v}" for k, v in (result.summary or {}).items())
    print(
        f"wayfinder-router: recalibrated {args.out} from {result.label_count} labels — {summary}",
        file=sys.stderr,
    )
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    from .gateway import GatewayUnavailable, run

    try:
        run(
            start_dir=".",
            host=args.host,
            port=args.port,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except GatewayUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _demo_url(host: str, port: int) -> str:
    """The browsable URL for the demo UI. A wildcard bind isn't navigable, so show loopback."""
    display = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{display}:{port}/demo"


def _cmd_webchat(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    from .gateway import GatewayUnavailable, run

    url = _demo_url(args.host, args.port)
    note = "  (dry-run: routing decisions only, no model calls)" if args.dry_run else ""
    print(f"wayfinder-router webchat → {url}{note}  (Ctrl-C to stop)")
    # uvicorn.run blocks, so open the browser from a short timer once the server is up.
    timer = None
    if not args.no_open:
        timer = threading.Timer(_CHAT_OPEN_DELAY, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        run(
            start_dir=".",
            host=args.host,
            port=args.port,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except GatewayUnavailable as exc:
        if timer is not None:
            timer.cancel()
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _cmd_ui(args: argparse.Namespace) -> int:
    from .ui import UIUnavailable, run_ui

    try:
        run_ui(start_dir=".", host=args.host, port=args.port)
    except UIUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _cmd_chat(args: argparse.Namespace) -> int:
    from .tui import TUIUnavailable, run_tui

    if args.threshold is not None and not 0.0 <= args.threshold <= 1.0:
        print("wayfinder-router: --threshold must be a number between 0.0 and 1.0", file=sys.stderr)
        return EXIT_USAGE
    try:
        run_tui(
            start_dir=".",
            theme=args.theme,
            show_why=args.why,
            threshold=args.threshold,
        )
    except TUIUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _load_prompts(path: str) -> list[str]:
    """One prompt per line: a JSON object with a ``text`` field, or raw text."""
    prompts: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("text"), str):
                prompts.append(row["text"])
                continue
        prompts.append(line)
    return prompts


def _cmd_onboard(args: argparse.Namespace) -> int:
    from .gateway import GatewayUnavailable, invoke_model, load_gateway_config
    from .onboard import run_onboarding

    if not Path(args.prompts).is_file():
        print(f"wayfinder-router: file not found: {args.prompts}", file=sys.stderr)
        return EXIT_USAGE
    try:
        gateway = load_gateway_config(".")
    except WayfinderConfigError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(gateway.models)
    arms = arms[:2]
    if len(arms) < 2:
        print(
            "wayfinder-router: onboard needs two gateway models (e.g. local and hosted); "
            "configure [gateway.models.*] or pass --arms local,cloud",
            file=sys.stderr,
        )
        return EXIT_USAGE
    missing = [a for a in arms if a not in gateway.models]
    if missing:
        print(f"wayfinder-router: no [gateway.models] entry for: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE

    primary, fallback = arms

    def run_model(arm: str, prompt: str) -> str:
        return invoke_model(gateway.models[arm], prompt)

    def judge(prompt: str, outputs: dict) -> str:
        # Interactive A/B goes to stderr so stdout stays clean for --calibrate.
        print(f"\n--- prompt ---\n{prompt}\n", file=sys.stderr)
        print(f"[{primary}]\n{outputs[primary]}\n", file=sys.stderr)
        print(f"[{fallback}]\n{outputs[fallback]}\n", file=sys.stderr)
        print(f"Is '{primary}' good enough? [y/N] ", end="", file=sys.stderr, flush=True)
        answer = input().strip().lower()
        return primary if answer in ("y", "yes") else fallback

    try:
        prompts = _load_prompts(args.prompts)
        summary = run_onboarding(prompts, arms, run_model, judge, args.log)
    except GatewayUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    counts = ", ".join(f"{k}={v}" for k, v in summary.label_counts.items())
    print(f"wayfinder-router: judged {summary.judged} prompts -> {counts}", file=sys.stderr)
    print(f"wayfinder-router: labels appended to {args.log}", file=sys.stderr)

    if args.calibrate:
        from .calibrate import calibrate, load_dataset

        result = calibrate(load_dataset(args.log), args.mode)
        print(result.toml)
        summary_line = ", ".join(f"{k}={v}" for k, v in result.summary.items())
        print(f"wayfinder-router: {summary_line}", file=sys.stderr)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayfinder-router",
        description="Deterministic prompt-complexity router.",
    )
    parser.add_argument("--version", action="version", version=f"wayfinder-router {__version__}")
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
    p_route.add_argument(
        "--explain",
        action="store_true",
        help="Show each feature's contribution to the score (human output only).",
    )
    p_route.set_defaults(func=_cmd_route)

    p_cal = sub.add_parser(
        "calibrate", help="Turn a labeled JSONL dataset into a wayfinder-router.toml fragment."
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
        "--iterations", type=int, default=100, help="Max Newton iterations (default: 100)."
    )
    p_cal.add_argument(
        "--l2", type=float, default=0.01, help="Classifier L2 regularization (default: 0.01)."
    )
    p_cal.add_argument(
        "--objective",
        choices=["accuracy", "knee", "cost-quality"],
        default="accuracy",
        help="threshold mode: maximize accuracy (default); 'knee' for the cost-aware "
        "knee (quality x savings, no target needed); or 'cost-quality' for accuracy at "
        "a --target-savings.",
    )
    p_cal.add_argument(
        "--target-savings",
        type=float,
        default=None,
        help="Cost saved vs always-routing-high to hold, 0.0-1.0 (cost-quality objective).",
    )
    p_cal.add_argument(
        "--costs",
        default=None,
        help="Per-arm cost for knee/cost-quality, e.g. local=0.2,cloud=1.0 (default: 0.2/1.0).",
    )
    p_cal.add_argument(
        "--weights",
        default=None,
        help="Custom feature weights to score with and emit (threshold/tiers), e.g. "
        "reasoning_term_count=5,math_symbol_count=3,constraint_term_count=1.5 (the lexical opt-in).",
    )
    p_cal.set_defaults(func=_cmd_calibrate)

    p_serve = sub.add_parser(
        "serve",
        help="Run the OpenAI-compatible routing gateway (needs the [gateway] extra).",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    p_serve.add_argument("--port", type=int, default=8088, help="Bind port (default: 8088).")
    p_serve.add_argument(
        "--dry-run",
        action="store_true",
        help="Return the routing decision without calling an upstream (no backends needed).",
    )
    p_serve.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Upstream request timeout in seconds (default: WAYFINDER_ROUTER_TIMEOUT or 60).",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_chat = sub.add_parser(
        "chat",
        help="Wayfinder terminal chat: decision-first routing in the terminal (needs the [tui] extra).",
    )
    p_chat.add_argument(
        "--theme",
        choices=["auto", "light", "dark"],
        default="auto",
        help="Colour theme (default: auto -> $WAYFINDER_THEME or dark).",
    )
    p_chat.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Force a binary local/cloud cut at this score (0.0-1.0).",
    )
    p_chat.add_argument(
        "--why", action="store_true", help="Expand the score breakdown on every turn."
    )
    p_chat.set_defaults(func=_cmd_chat)

    p_ui = sub.add_parser(
        "ui",
        help="Run the local calibration/explain/configure UI (needs the [ui] extra).",
    )
    p_ui.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    p_ui.add_argument("--port", type=int, default=8099, help="Bind port (default: 8099).")
    p_ui.set_defaults(func=_cmd_ui)

    p_webchat = sub.add_parser(
        "webchat",
        help="Launch the web chat UI (the gateway, opened at /demo; needs the [gateway] extra).",
    )
    p_webchat.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    p_webchat.add_argument("--port", type=int, default=8088, help="Bind port (default: 8088).")
    p_webchat.add_argument(
        "--dry-run",
        action="store_true",
        help="Show routing decisions without calling an upstream (no backends needed).",
    )
    p_webchat.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Upstream request timeout in seconds (default: WAYFINDER_ROUTER_TIMEOUT or 60).",
    )
    p_webchat.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the demo in a browser on startup.",
    )
    p_webchat.set_defaults(func=_cmd_webchat)

    p_onboard = sub.add_parser(
        "onboard",
        help="A/B local vs hosted on sample prompts to bootstrap labels (needs [gateway]).",
    )
    p_onboard.add_argument(
        "prompts", help="A file of prompts: one per line, or JSONL {\"text\": ...}."
    )
    p_onboard.add_argument(
        "--arms",
        default=None,
        help="Two gateway model names to compare, e.g. local,cloud (default: first two).",
    )
    p_onboard.add_argument(
        "--log", default="wayfinder-router-feedback.jsonl", help="Label log to append to."
    )
    p_onboard.add_argument(
        "--calibrate", action="store_true", help="Calibrate a config from the log when done."
    )
    p_onboard.add_argument(
        "--mode",
        choices=["threshold", "tiers", "classifier"],
        default="threshold",
        help="Calibration mode for --calibrate (default: threshold).",
    )
    p_onboard.set_defaults(func=_cmd_onboard)

    p_recal = sub.add_parser(
        "recalibrate",
        help="Re-fit the routing config from the feedback log (cron/CI-friendly).",
    )
    p_recal.add_argument(
        "--log", default="wayfinder-router-feedback.jsonl", help="Feedback label log to read."
    )
    p_recal.add_argument("--out", default="wayfinder-router.toml", help="Config file to update in place.")
    p_recal.add_argument(
        "--mode", choices=["threshold", "tiers", "classifier"], default="threshold"
    )
    p_recal.add_argument(
        "--min-labels", type=int, default=2, help="Skip (no write) below this many labels."
    )
    p_recal.set_defaults(func=_cmd_recalibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
