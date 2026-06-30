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

    from .gateway import GatewayUnavailable, load_gateway_config, run

    url = _demo_url(args.host, args.port)
    note = "  (dry-run: routing decisions only, no model calls)" if args.dry_run else ""
    print(f"wayfinder-router webchat → {url}{note}  (Ctrl-C to stop)")
    if not args.dry_run:  # first-run nudge: no models means decision-only replies
        try:
            if not load_gateway_config(".").models:
                print(
                    "note: no [gateway.models] configured — the demo shows routing decisions "
                    "only. Run `wayfinder-router init` to set up local + cloud.",
                    file=sys.stderr,
                )
        except WayfinderConfigError:
            pass
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
            dry_run=args.dry_run,
            stream=not args.no_stream,
            base_url=args.base_url,
        )
    except TUIUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _print_key_report(statuses: list) -> None:
    """Print a per-model key check: keyless, key set (✓), or named-but-unset (✗)."""
    print("models")
    for s in statuses:
        if s.env_var is None:
            key = "keyless ✓"
        elif s.ok:
            # After resolve_keys(), a command-filled key reads as set; note its source.
            key = f"{s.env_var} ✓ set" + (" (via command)" if s.cmd else "")
        else:
            key = f"{s.env_var} ✗ not set"
        print(f"  {s.name:<7} {s.model:<24} {s.base_url:<30} {key}")


def _print_key_remedies(missing: list[str]) -> None:
    """For each unset key: the plain `export`, plus an `api_key_cmd` for any secret
    tool found on PATH (so the key can live in a manager, never in a shell file)."""
    from . import bootstrap

    for var in missing:
        print(f'  export {var}="..."')
        for suggestion in bootstrap.suggest_key_commands(var):
            print(f'  · or store it safely and add:  api_key_cmd = "{suggestion}"')


def _summarize_routing(config: RoutingConfig) -> str:
    if config.classifier is not None:
        return f"classifier ({len(config.classifier.models)} models)"
    if not config.tiers:
        return "defaults"
    return " · ".join(f"{t.model} ≥{t.min_score:.2f}" for t in config.tiers)


def _console_io():
    """Real terminal I/O for the wizard: prompts to stderr (so stdout stays pipeable),
    answers from stdin. EOF (piped/exhausted input) falls back to the default."""

    def say(message: str) -> None:
        print(message, file=sys.stderr)

    def ask(prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        sys.stderr.write(f"{prompt}{suffix}: ")
        sys.stderr.flush()
        try:
            line = input().strip()
        except EOFError:
            return default
        return line or default

    return ask, say


def _cmd_init(args: argparse.Namespace) -> int:
    from . import bootstrap
    from .gateway import gateway_config_from_toml

    if args.interactive:
        ask, say = _console_io()
        preset = bootstrap.run_init_wizard(ask, say)
    else:
        chosen = bootstrap.PRESETS.get(args.preset)
        if chosen is None:
            choices = ", ".join(sorted(bootstrap.PRESETS))
            print(
                f"wayfinder-router: unknown preset '{args.preset}' (choose: {choices})",
                file=sys.stderr,
            )
            return EXIT_USAGE
        preset = chosen

    config_text = bootstrap.render_config(preset)
    if args.print:
        sys.stdout.write(config_text)
        return EXIT_OK

    target = Path(args.path)
    if target.exists() and not args.force:
        print(
            f"wayfinder-router: {target} already exists — use --force to overwrite, "
            "or run `wayfinder-router doctor` to check it",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        target.write_text(config_text, encoding="utf-8")
    except OSError as exc:
        print(f"wayfinder-router: cannot write {target}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"✓ wrote {target}  (preset: {preset.name} — {preset.summary})")

    # .env.example holds env-var NAMES only — never a secret; don't clobber silently.
    if preset.env_vars:
        env_path = target.parent / ".env.example"
        if env_path.exists() and not args.force:
            print(f"· kept existing {env_path} (use --force to overwrite)")
        else:
            try:
                env_path.write_text(bootstrap.render_env_example(preset), encoding="utf-8")
                print(f"✓ wrote {env_path}  (env-var names only — no secrets)")
            except OSError as exc:
                print(f"wayfinder-router: cannot write {env_path}: {exc}", file=sys.stderr)

    statuses = bootstrap.key_status(gateway_config_from_toml(config_text).models)
    print()
    _print_key_report(statuses)
    print()
    missing = bootstrap.missing_keys(statuses)
    if missing:
        print("set your key(s) — read from the environment at request time, never stored:")
        _print_key_remedies(missing)
        print()
    print("next:  wayfinder-router chat        # or `wayfinder-router doctor` to re-check")
    return EXIT_OK


def _cmd_keys(args: argparse.Namespace) -> int:
    """Mint a virtual API key (WF-ADR-0035): prints the paste-able config block + the key once."""
    from . import vkeys

    if args.action != "new":  # only "new" today; choices guards this, kept for clarity
        return EXIT_USAGE
    plaintext, key_hash = vkeys.generate()
    lines = [f"[gateway.keys.{args.id}]", f'hash = "{key_hash}"']
    if args.tag:
        lines.append("tags = [" + ", ".join(f'"{t}"' for t in args.tag) + "]")
    print("# Paste into wayfinder-router.toml (only the hash is stored — never the key):")
    print("\n".join(lines))
    print("\n# Give this key to the caller; it is shown once and cannot be recovered:")
    print(plaintext)
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    from . import bootstrap
    from .config import find_config_file
    from .gateway import load_gateway_config

    path = find_config_file(args.dir)
    if path is None:
        print(
            "no wayfinder-router.toml found — run `wayfinder-router init` to create one",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        routing = load_routing_config(args.dir)
        gateway = load_gateway_config(args.dir)
    except WayfinderConfigError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print(f"config:  {path}")
    print(f"routing: {_summarize_routing(routing)}")
    if not gateway.models:
        print("models:  none configured — add [gateway.models] (see `wayfinder-router init`)")
        print("(chat / serve will show routing decisions only)")
        return EXIT_OK
    # Verify readiness for real: run any api_key_cmd so a key kept in a secret store
    # counts as present (WF-DESIGN-0006). In-memory only — nothing is written.
    cmd_errors = bootstrap.resolve_keys(gateway.models)
    statuses = bootstrap.key_status(gateway.models)
    print()
    _print_key_report(statuses)
    print()
    if cmd_errors:
        print("key command(s) failed:")
        for name, reason in sorted(cmd_errors.items()):
            print(f"  {name}: {reason}")
        print()
    missing = bootstrap.missing_keys(statuses)
    if missing:
        print("not ready — set the missing key(s):")
        _print_key_remedies(missing)
        return EXIT_CONFIG
    print("ready:  wayfinder-router chat")
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
    from . import bootstrap
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
    bootstrap.resolve_keys(gateway.models)  # fill keys from a secret store (WF-DESIGN-0006)
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


def _judge_provenance_banner(judge_version, args, sample_count, report) -> str:
    """A leading comment block stamping how a judge-minted config was derived (WF-ADR-0037).

    The derivation is not bit-reproducible (the arm responses are not), so the banner records
    *what judged what* — judge version, prompt/gold file hashes, the gates that passed, and
    the tool version — in place of a replay guarantee. Extends ``recalibrate``'s
    ``# recalibrated from feedback:`` comment convention.
    """
    import datetime
    import hashlib

    def _file_hash(path: str | None) -> str:
        if not path or not Path(path).is_file():
            return "none"
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]

    generated = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return "\n".join([
        "# wayfinder-router judge: trusted config (WF-ADR-0037)",
        f"# judge={judge_version} mode={args.mode} samples={sample_count}",
        f"# kappa={report.kappa:.2f} (floor {report.kappa_floor:.2f}, gold n={report.n_gold}) "
        f"cv_acc={report.cv_accuracy:.2f} baseline={report.majority_baseline:.2f} "
        f"lift={report.lift:+.2f}",
        f"# prompts={_file_hash(args.prompts)} gold={_file_hash(args.gold)} "
        f"tool={__version__} generated={generated}",
    ])


def _cmd_judge(args: argparse.Namespace) -> int:
    from . import bootstrap
    from .calibrate import load_dataset
    from .gateway import GatewayUnavailable, invoke_model, load_gateway_config
    from .judge import HeuristicJudge, as_onboard_judge
    from .onboard import run_onboarding
    from .sufficiency import evaluate

    if not Path(args.prompts).is_file():
        print(f"wayfinder-router: file not found: {args.prompts}", file=sys.stderr)
        return EXIT_USAGE
    if args.gold and not Path(args.gold).is_file():
        print(f"wayfinder-router: file not found: {args.gold}", file=sys.stderr)
        return EXIT_USAGE
    try:
        gateway = load_gateway_config(".")
    except WayfinderConfigError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    bootstrap.resolve_keys(gateway.models)  # fill keys from a secret store (WF-DESIGN-0006)
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(gateway.models)
    arms = arms[:2]
    if len(arms) < 2:
        print(
            "wayfinder-router: judge needs two gateway models in cheap,expensive order; "
            "configure [gateway.models.*] or pass --arms cheap,expensive",
            file=sys.stderr,
        )
        return EXIT_USAGE
    missing = [a for a in arms if a not in gateway.models]
    if missing:
        print(f"wayfinder-router: no [gateway.models] entry for: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    cheap, expensive = arms

    judge_impl = HeuristicJudge()

    def run_model(arm: str, prompt: str) -> str:
        return invoke_model(gateway.models[arm], prompt)

    # Optional comparison audit log: a response-body store, so off by default and only
    # written when explicitly requested (WF-DESIGN-0008 capture posture). Enables a future
    # deterministic re-judge from saved bodies with no re-calling.
    on_verdict = None
    if args.save_comparisons:
        comp_path = args.save_comparisons

        def on_verdict(prompt: str, outputs: dict, verdict) -> None:
            row = {
                "text": prompt,
                "cheap": {"arm": cheap, "model": gateway.models[cheap].model,
                          "response": outputs[cheap]},
                "expensive": {"arm": expensive, "model": gateway.models[expensive].model,
                              "response": outputs[expensive]},
                "verdict": {"sufficient": verdict.sufficient,
                            "comparator": verdict.comparator, "reason": verdict.reason},
                "judge_version": judge_impl.version,
            }
            with open(comp_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Gate 1 input: run the judge over the human-labeled gold set and pair its verdicts
    # against the human labels (abstentions are excluded from kappa, only counted).
    gold_pairs: list[tuple[str, str]] = []
    gold_abstained = 0
    try:
        if args.gold:
            from .feedback import read_labels

            for row in read_labels(args.gold):
                text, gold_label = row.get("text"), row.get("label")
                if not isinstance(text, str) or not isinstance(gold_label, str):
                    continue
                outputs = {arm: run_model(arm, text) for arm in arms}
                verdict = judge_impl.judge(text, outputs[cheap], outputs[expensive])
                if on_verdict is not None:
                    on_verdict(text, outputs, verdict)
                if verdict.sufficient is True:
                    gold_pairs.append((cheap, gold_label))
                elif verdict.sufficient is False:
                    gold_pairs.append((expensive, gold_label))
                else:
                    gold_abstained += 1

        # Main collection: judge every prompt, record non-abstained labels to the log.
        prompts = _load_prompts(args.prompts)
        if args.limit:
            prompts = prompts[: args.limit]
        judge_fn = as_onboard_judge(judge_impl, cheap, expensive, on_verdict=on_verdict)
        summary = run_onboarding(prompts, arms, run_model, judge_fn, args.log)
    except GatewayUnavailable as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(
        f"wayfinder-router: judged {summary.judged} prompts "
        f"({summary.abstained} abstained) -> {summary.label_counts}",
        file=sys.stderr,
    )
    print(f"wayfinder-router: labels appended to {args.log}", file=sys.stderr)

    try:
        samples = load_dataset(args.log)
    except CalibrationError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    report = evaluate(gold_pairs, samples, kappa_floor=args.kappa_floor,
                      k=args.folds, gold_abstained=gold_abstained)
    print(report.render(), file=sys.stderr)
    if not report.passed:
        print(
            "wayfinder-router: refusing to emit a config — trust gates failed "
            "(labels were still recorded to the log)",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    try:
        result = calibrate(samples, args.mode)
    except CalibrationError as exc:
        print(f"wayfinder-router: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    print(_judge_provenance_banner(judge_impl.version, args, len(samples), report))
    print(result.toml)
    summary_line = ", ".join(f"{k}={v}" for k, v in result.summary.items())
    print(f"wayfinder-router: {summary_line}", file=sys.stderr)
    return EXIT_OK


def _resolve_serve_args(host: str, port: int) -> list[str]:
    """ProgramArguments to launch the gateway: the installed console script, else ``python -m``."""
    import shutil

    exe = shutil.which("wayfinder-router")
    base = [exe] if exe else [sys.executable, "-m", "wayfinder_router.cli"]
    return [*base, "serve", "--host", host, "--port", str(port)]


def _probe_health(host: str, port: int) -> str:
    """A tolerant `/healthz` probe for `service status` (bypasses any HTTP proxy for localhost)."""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/healthz", timeout=1.5) as resp:
            return f"ok ({resp.status})" if resp.status == 200 else f"status {resp.status}"
    except Exception:
        return "unreachable (service not running?)"


def _cmd_service(args: argparse.Namespace) -> int:
    import os
    import shutil
    import subprocess

    from . import service

    plat = service.detect_platform()
    if plat == "other":
        print(
            "wayfinder-router: service supports macOS (launchd) and Linux (systemd user units); "
            "elsewhere run `wayfinder-router serve` yourself.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    program_args = _resolve_serve_args(args.host, args.port)
    endpoint = f"http://{args.host}:{args.port}/v1"
    # launchd does not expand ``~`` in StandardOutPath/StandardErrorPath — an unresolved tilde
    # makes it fail to open the log file and refuse to spawn (EX_CONFIG). Resolve the log dir to
    # an absolute path here, in the I/O layer, just as we resolve the program path with ``which``.
    mac_log_dir = os.path.expanduser("~/Library/Logs")
    if plat == "macos":
        unit_text = service.launchd_plist(program_args, log_dir=mac_log_dir)
        unit_file = service.agent_path()
        manager = shutil.which("launchctl")
    else:  # linux
        unit_text = service.systemd_unit(program_args)
        unit_file = service.systemd_unit_path()
        manager = shutil.which("systemctl")

    if args.action == "install":
        if args.print:
            print(unit_text)
            return EXIT_OK
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        unit_file.write_text(unit_text, encoding="utf-8")
        if plat == "macos" and manager:
            uid = os.getuid()
            os.makedirs(mac_log_dir, exist_ok=True)  # launchd needs the StandardOut/Err dir present
            loaded = subprocess.run(
                [manager, "bootstrap", f"gui/{uid}", str(unit_file)], capture_output=True, text=True
            )
            if loaded.returncode != 0:  # older macOS
                subprocess.run([manager, "load", "-w", str(unit_file)], capture_output=True, text=True)
            print(f"wayfinder-router: installed and loaded {unit_file}", file=sys.stderr)
        elif plat == "linux" and manager:
            subprocess.run([manager, "--user", "daemon-reload"], capture_output=True, text=True)
            subprocess.run(
                [manager, "--user", "enable", "--now", service.SYSTEMD_UNIT_NAME],
                capture_output=True, text=True,
            )
            print(f"wayfinder-router: installed and started {unit_file}", file=sys.stderr)
        else:
            hint = (
                f"launchctl bootstrap gui/$(id -u) {unit_file}"
                if plat == "macos"
                else f"systemctl --user enable --now {service.SYSTEMD_UNIT_NAME}"
            )
            print(f"wayfinder-router: wrote {unit_file}; start it with:\n  {hint}", file=sys.stderr)
        print(f"wayfinder-router: point your apps at OPENAI_BASE_URL={endpoint}", file=sys.stderr)
        return EXIT_OK

    if args.action == "uninstall":
        if plat == "macos" and manager and unit_file.is_file():
            uid = os.getuid()
            booted = subprocess.run(
                [manager, "bootout", f"gui/{uid}/{service.LAUNCHD_LABEL}"], capture_output=True, text=True
            )
            if booted.returncode != 0:
                subprocess.run([manager, "unload", "-w", str(unit_file)], capture_output=True, text=True)
        elif plat == "linux" and manager and unit_file.is_file():
            subprocess.run(
                [manager, "--user", "disable", "--now", service.SYSTEMD_UNIT_NAME],
                capture_output=True, text=True,
            )
        existed = unit_file.is_file()
        unit_file.unlink(missing_ok=True)
        print(
            f"wayfinder-router: removed {unit_file}"
            if existed
            else f"wayfinder-router: nothing to remove ({unit_file} not present)",
            file=sys.stderr,
        )
        return EXIT_OK

    # status
    installed = unit_file.is_file()
    print(f"unit file: {unit_file} ({'present' if installed else 'absent'})", file=sys.stderr)
    print(f"endpoint:  {endpoint}", file=sys.stderr)
    if manager and installed:
        if plat == "macos":
            uid = os.getuid()
            probe = subprocess.run(
                [manager, "print", f"gui/{uid}/{service.LAUNCHD_LABEL}"], capture_output=True, text=True
            )
            print(f"launchd:   {'loaded' if probe.returncode == 0 else 'not loaded'}", file=sys.stderr)
        else:
            probe = subprocess.run(
                [manager, "--user", "is-active", service.SYSTEMD_UNIT_NAME], capture_output=True, text=True
            )
            print(f"systemd:   {probe.stdout.strip() or 'unknown'}", file=sys.stderr)
    print(f"health:    {_probe_health(args.host, args.port)}", file=sys.stderr)
    if not installed:
        print(f"\ninstall with: wayfinder-router service install --port {args.port}", file=sys.stderr)
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

    p_service = sub.add_parser(
        "service",
        help="Run the gateway as an always-on local service (macOS launchd / Linux systemd).",
    )
    p_service.add_argument(
        "action", choices=["install", "uninstall", "status"], help="What to do."
    )
    p_service.add_argument("--host", default="127.0.0.1", help="Gateway host (default: 127.0.0.1).")
    p_service.add_argument("--port", type=int, default=8088, help="Gateway port (default: 8088).")
    p_service.add_argument(
        "--print", action="store_true",
        help="Print the generated unit file instead of installing it.",
    )
    p_service.set_defaults(func=_cmd_service)

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
    p_chat.add_argument(
        "--dry-run",
        action="store_true",
        help="Decision-only: never call a model, even if [gateway.models] are configured.",
    )
    p_chat.add_argument(
        "--no-stream", action="store_true", help="Wait for the full reply instead of streaming."
    )
    p_chat.add_argument(
        "--base-url",
        default=None,
        help="Talk to a running gateway over HTTP (e.g. http://host:8088) instead of in-process.",
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

    p_judge = sub.add_parser(
        "judge",
        help="Auto-label prompts by comparing two tiers, gated by trust checks (WF-ADR-0037).",
    )
    p_judge.add_argument(
        "prompts", help="A file of prompts: one per line, or JSONL {\"text\": ...}."
    )
    p_judge.add_argument(
        "--arms",
        default=None,
        help="Two gateway model names in cheap,expensive order (default: first two).",
    )
    p_judge.add_argument(
        "--gold",
        default=None,
        help="A human-labeled {\"text\",\"label\"} JSONL set; required to mint a trusted config "
             "(the kappa agreement gate).",
    )
    p_judge.add_argument(
        "--log", default="wayfinder-router-feedback.jsonl", help="Label log to append to."
    )
    p_judge.add_argument(
        "--mode",
        choices=["threshold", "tiers", "classifier"],
        default="threshold",
        help="Calibration mode for the emitted config (default: threshold).",
    )
    p_judge.add_argument(
        "--kappa-floor", type=float, default=0.6,
        help="Minimum judge-vs-gold Cohen's kappa to trust the labels (default: 0.6).",
    )
    p_judge.add_argument(
        "--folds", type=int, default=5, help="Cross-validation folds for the lift gate (default: 5)."
    )
    p_judge.add_argument(
        "--limit", type=int, default=None, help="Judge at most this many prompts (caps cost)."
    )
    p_judge.add_argument(
        "--save-comparisons", default=None,
        help="Also write a JSONL of prompts+responses+verdicts here (a response-body store; "
             "off by default).",
    )
    p_judge.set_defaults(func=_cmd_judge)

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

    p_init = sub.add_parser(
        "init",
        help="Scaffold a wayfinder-router.toml (+ .env.example) and check your keys.",
    )
    p_init.add_argument(
        "-i", "--interactive", action="store_true",
        help="Pick providers/models step by step (still never captures a secret).",
    )
    p_init.add_argument(
        "--preset", default="hybrid",
        help="Starter preset: hybrid (default, keyless local Ollama → Anthropic cloud), "
             "openai (gpt-4o-mini → gpt-4o), or gemini (gemini-2.5-flash → gemini-2.5-pro).",
    )
    p_init.add_argument(
        "--path", default="wayfinder-router.toml", help="Where to write the config (default: cwd)."
    )
    p_init.add_argument(
        "--force", action="store_true", help="Overwrite an existing config / .env.example."
    )
    p_init.add_argument(
        "--print", action="store_true", help="Print the config to stdout instead of writing files."
    )
    p_init.set_defaults(func=_cmd_init)

    p_doctor = sub.add_parser(
        "doctor",
        help="Check the nearest wayfinder-router.toml and whether each model's key is set.",
    )
    p_doctor.add_argument(
        "--dir", default=".", help="Where to start the search for wayfinder-router.toml."
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_keys = sub.add_parser(
        "keys", help="Mint a virtual API key for the gateway (WF-ADR-0035)."
    )
    p_keys.add_argument("action", choices=["new"], help="Action to perform (currently: new).")
    p_keys.add_argument(
        "--id", default="team-1", help="Key id for the [gateway.keys.<id>] block."
    )
    p_keys.add_argument(
        "--tag", action="append", default=[], help="Attribution tag (repeatable)."
    )
    p_keys.set_defaults(func=_cmd_keys)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
