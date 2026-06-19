"""Adapt RouterBench's pre-computed outcomes into the benchmark harness's shape.

RouterBench (Hu et al. 2024; Martian) is a table of *already-run* inference outcomes:
for each prompt and each candidate LLM it records a response, a quality/performance
score, and a dollar cost. That means any router can be scored fully offline — no API
keys, no inference — which is the whole reason it fits Wayfinder (WF-ADR-0001).

This reduces the multi-model table to Wayfinder's binary local-vs-cloud problem: pick a
cheap/small model as `local` and a frontier model as `cloud`, and emit per-prompt rows
with their real graded quality as the label and their real per-call cost. The harness's
`quality`/`PGR` already handle fractional labels; `harness.evaluate` reads per-row
`cost` when present (added with this adapter) instead of the flat 0.2/1.0 default.

Schema note: RouterBench's exact column names depend on the release. This adapter
discovers per-model score/cost columns by substring match and prints what it finds, so
you point it at the two models you want without hard-coding field names.

Usage (in an environment with the dataset downloaded and `datasets` installed):
    pip install datasets
    python -m benchmarks.routerbench_adapter \
        --dataset withmartian/routerbench \
        --local  mistral-7b \
        --cloud  gpt-4 \
        --out    benchmarks/routerbench.jsonl
    python -m benchmarks.run benchmarks/routerbench.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys


def _find_columns(columns: list[str], model: str) -> tuple[str | None, str | None]:
    """Best-effort: the score and cost columns for a model, by substring + keyword."""
    hits = [c for c in columns if model.lower() in c.lower()]
    score = next((c for c in hits if any(k in c.lower()
                  for k in ("perf", "score", "correct", "accuracy", "quality"))), None)
    cost = next((c for c in hits if "cost" in c.lower()), None)
    return score, cost


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert RouterBench to harness JSONL.")
    ap.add_argument("--dataset", default="withmartian/routerbench")
    ap.add_argument("--split", default="train")
    ap.add_argument("--local", required=True, help="substring of the small/cheap model column")
    ap.add_argument("--cloud", required=True, help="substring of the frontier model column")
    ap.add_argument("--prompt-col", default="prompt")
    ap.add_argument("--task-col", default="eval_name", help="source-task column, used as the bucket tag")
    ap.add_argument("--out", default="benchmarks/routerbench.jsonl")
    args = ap.parse_args(argv)

    try:
        from datasets import load_dataset
    except ImportError:
        print("install the extra first:  pip install datasets", file=sys.stderr)
        return 2

    ds = load_dataset(args.dataset, split=args.split)
    columns = list(ds.features)
    ls, lc = _find_columns(columns, args.local)
    cs, cc = _find_columns(columns, args.cloud)
    if not all([ls, lc, cs, cc]):
        print("could not resolve model columns. available columns:", file=sys.stderr)
        for c in columns:
            print("  ", c, file=sys.stderr)
        print(f"\nresolved -> local score={ls} cost={lc} ; cloud score={cs} cost={cc}", file=sys.stderr)
        return 1

    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for row in ds:
            f.write(json.dumps({
                "prompt": row[args.prompt_col],
                "difficulty": str(row.get(args.task_col, "?")),
                "label": {"local": float(row[ls]), "cloud": float(row[cs])},
                "cost": {"local": float(row[lc]), "cloud": float(row[cc])},
            }) + "\n")
            n += 1
    print(f"wrote {n} rows to {args.out}  (local={ls}/{lc}, cloud={cs}/{cc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
