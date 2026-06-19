"""Run the benchmark and print a markdown report.

    python -m benchmarks.run                      # uses benchmarks/dataset.jsonl
    python -m benchmarks.run path/to/other.jsonl  # any RouterArena/RouterBench-shaped set

Deterministic and offline: routers that need a model call to decide (RouteLLM,
NotDiamond, …) are not run here — see benchmarks/README.md for adapters and their
published numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .harness import Metrics, Row, evaluate, evaluate_oracle, knee, load_dataset, sweep
from .routers import (
    always_cloud,
    always_local,
    deterministic_random,
    length_threshold,
    wayfinder,
)

_DEFAULT_DATASET = Path(__file__).parent / "dataset.jsonl"
_THRESHOLDS = [round(i * 0.01, 2) for i in range(0, 31)]  # 0.00 .. 0.30
_WORD_CUTS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0, 120.0]
_CURVE_POINTS = [0.0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]


def _row(m: Metrics) -> str:
    latency = f"{m.latency_us:.1f}" if m.latency_us else "~0"
    return (
        f"| {m.name} | {m.quality:.2f} | {m.cost:.2f} | {m.frac_cloud:.0%} | "
        f"{m.pgr:.2f} | {m.cost_savings:.0%} | {latency} |"
    )


def _table(results: list[Metrics]) -> str:
    head = (
        "| router | quality | cost | → cloud | PGR | cost saved | decide µs |\n"
        "| --- | --: | --: | --: | --: | --: | --: |"
    )
    return "\n".join([head, *(_row(m) for m in results)])


def _curve(points: list[tuple[float, Metrics]]) -> str:
    head = "| threshold | quality | cost | → cloud | PGR |\n| --: | --: | --: | --: | --: |"
    lines = [head]
    wanted = {v for v in _CURVE_POINTS}
    for value, m in points:
        if value in wanted:
            lines.append(
                f"| {value:.2f} | {m.quality:.2f} | {m.cost:.2f} | {m.frac_cloud:.0%} | {m.pgr:.2f} |"
            )
    return "\n".join(lines)


def _by_difficulty(rows: list[Row], threshold: float) -> str:
    buckets: dict[str, list[Row]] = {}
    for row in rows:
        buckets.setdefault(row.difficulty or "?", []).append(row)
    lines = ["| difficulty | n | accuracy | → cloud |", "| --- | --: | --: | --: |"]
    for name in sorted(buckets):
        bucket = buckets[name]
        choices = [wayfinder(r.prompt, threshold) for r in bucket]
        acc = sum(r.label[c] for r, c in zip(bucket, choices, strict=True)) / len(bucket)
        cloud = sum(c == "cloud" for c in choices) / len(bucket)
        lines.append(f"| {name} | {len(bucket)} | {acc:.2f} | {cloud:.0%} |")
    return "\n".join(lines)


def run(dataset: Path) -> str:
    rows = load_dataset(dataset)

    wf_points = sweep(rows, lambda t: (lambda p: wayfinder(p, t)), _THRESHOLDS)
    wf_t, _ = knee(wf_points)
    len_points = sweep(rows, lambda w: (lambda p: length_threshold(p, int(w))), _WORD_CUTS)
    len_w, _ = knee(len_points)

    results = [
        evaluate_oracle(rows),
        evaluate("always-cloud (strong only)", always_cloud, rows),
        evaluate("always-local (weak only)", always_local, rows),
        evaluate("random (stable)", deterministic_random, rows),
        evaluate(f"length-threshold (cost-aware, ≥{int(len_w)} words)",
                 lambda p: length_threshold(p, int(len_w)), rows),
        evaluate("wayfinder (default 0.5)", wayfinder, rows, measure_latency=True),
        evaluate(f"wayfinder (cost-aware, t={wf_t:.2f})",
                 lambda p: wayfinder(p, wf_t), rows, measure_latency=True),
    ]

    return (
        f"# Benchmark results — `{dataset.name}` ({len(rows)} prompts)\n\n"
        "Deterministic and offline; reproduce with `python -m benchmarks.run`. "
        "`quality` = mean correctness of the chosen model; `PGR` = performance gap recovered "
        "(0 = always-local, 1 = always-cloud); `cost saved` is vs always-cloud; `decide µs` is the "
        "per-prompt decision latency (no model call, machine-dependent).\n\n"
        + _table(results)
        + "\n\n## Wayfinder cost-quality curve (threshold sweep)\n\n"
        + _curve(wf_points)
        + f"\n\n## Wayfinder at the cost-aware knee (t={wf_t:.2f}), by difficulty\n\n"
        + _by_difficulty(rows, wf_t)
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    dataset = Path(args[0]) if args else _DEFAULT_DATASET
    print(run(dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
