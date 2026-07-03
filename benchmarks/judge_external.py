"""External, cross-dataset, baseline-anchored validation of the sufficiency judge.

`judge_validation.py` scores one judge on one RouterBench pair. This widens the check the
three ways an honest external validation should:

* **multiple model pairs** on RouterBench (a wide and a narrow capability gap), so the
  result isn't a single-pair fluke;
* a **second, independent dataset** (RouterArena, different authors, fetched over GitHub),
  so agreement isn't an artifact of one corpus;
* **baseline judges** (always-sufficient, exact-match-only) alongside `HeuristicJudge`, so
  the κ has something to be measured against rather than floating free.

Deterministic given the same RouterBench pickle and the same RouterArena revision.

    python -m benchmarks.judge_external --dataset data/routerbench_0shot.pkl \
        --out benchmarks/judge-external-results.md
"""

from __future__ import annotations

import argparse
import sys

from benchmarks.judge_validation import (
    AlwaysSufficientJudge,
    ExactMatchJudge,
    _load_rows,
    load_routerarena_rows,
    validate,
)
from wayfinder_router.judge import HeuristicJudge

JUDGES = (HeuristicJudge(), ExactMatchJudge(), AlwaysSufficientJudge())

# RouterBench pairs: a wide gap, a mid gap, and a narrow gap, all vs gpt-4.
ROUTERBENCH_PAIRS = (("mistral-7b", "gpt-4"), ("llama-2-70b", "gpt-4"), ("gpt-3.5-turbo", "gpt-4"))
# RouterArena pair (independent dataset).
ROUTERARENA_PAIRS = (("claude-3-haiku-20240307", "gemini-2.0-flash-001"),)


def _row(dataset: str, pair: str, judge_version: str, rows: list) -> str:
    report = validate(rows, judge=next(j for j in JUDGES if j.version == judge_version))["overall"]
    a, rel = report.gold["absolute"], report.gold["relative"]
    return (
        f"| {dataset} | {pair} | {judge_version} | {report.n} | {report.decided} | "
        f"{report.abstention_rate:.1%} | {a.kappa:.3f} | {rel.kappa:.3f} |"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-dataset, multi-pair, baseline judge validation.")
    ap.add_argument("--dataset", default="data/routerbench_0shot.pkl", help="RouterBench pickle")
    ap.add_argument("--limit", type=int, default=None, help="cap rows per pair (speed)")
    ap.add_argument("--skip-routerarena", action="store_true", help="RouterBench only (no network)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    lines = [
        "## Judge validation — cross-dataset, multi-pair, vs baselines",
        "",
        "| dataset | pair | judge | n | decided | abstain % | κ abs | κ rel |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for local, cloud in ROUTERBENCH_PAIRS:
        rows, _ = _load_rows(args.dataset, local, cloud, "prompt", "eval_name")
        if args.limit:
            rows = rows[: args.limit]
        for judge in JUDGES:
            lines.append(_row("RouterBench", f"{local} vs {cloud}", judge.version, rows))
        print(f"RouterBench {local} vs {cloud}: {len(rows)} rows", file=sys.stderr)

    if not args.skip_routerarena:
        for local, cloud in ROUTERARENA_PAIRS:
            try:
                rows = load_routerarena_rows(local, cloud, limit=args.limit)
            except OSError as e:
                print(f"RouterArena fetch failed ({e}); skipping", file=sys.stderr)
                continue
            for judge in JUDGES:
                lines.append(_row("RouterArena", f"{local[:14]} vs {cloud[:14]}", judge.version, rows))
            print(f"RouterArena {local} vs {cloud}: {len(rows)} rows", file=sys.stderr)

    markdown = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
