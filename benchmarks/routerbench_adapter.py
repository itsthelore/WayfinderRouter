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

Schema notes (the published RouterBench release, `withmartian/routerbench`):
  * The pickles ship as pandas DataFrames. The *wide* tables (`routerbench_0shot.pkl`,
    `routerbench_5shot.pkl`) carry, per model, a bare-named score column (e.g.
    ``"gpt-4-1106-preview"``), a ``"<model>|model_response"`` text column, and a
    ``"<model>|total_cost"`` column. The *raw* table (`routerbench_raw.pkl`) is the
    same data in long form — one row per (sample, model) with ``model_name`` /
    ``performance`` / ``cost`` columns — which this adapter pivots to the wide shape.
  * The score column is the *bare* model id (no score keyword), so column discovery
    keys on the keyword first and falls back to the bare model hit.
  * Each ``prompt`` cell is the string repr of a list of parts
    (``"['instruction', 'question']"``); the real prompt text is reconstructed by
    parsing it back and joining the parts.

Usage:
    # local pickle (wide or raw long form) — point --dataset at the file:
    python -m benchmarks.routerbench_adapter \
        --dataset benchmarks/../data/routerbench_0shot.pkl \
        --local  mistral-7b \
        --cloud  gpt-4 \
        --out    benchmarks/routerbench.jsonl
    python -m benchmarks.run benchmarks/routerbench.jsonl

    # or straight from the Hub (needs `pip install datasets` and network):
    python -m benchmarks.routerbench_adapter --dataset withmartian/routerbench ...
"""
from __future__ import annotations

import argparse
import ast
import json
import sys

# Columns of the raw (long-form) RouterBench table that mark it as needing a pivot.
_LONG_FORMAT_COLS = {"model_name", "performance", "cost"}
_SCORE_KEYWORDS = ("perf", "score", "correct", "accuracy", "quality")


def _find_columns(columns: list[str], model: str) -> tuple[str | None, str | None]:
    """Best-effort: the score and cost columns for a model, by substring + keyword.

    RouterBench stores a model's graded score under the *bare* model id, with the
    response/cost columns suffixed ``|model_response`` / ``|total_cost``. So the cost
    column is the model hit containing "cost", and the score column is a hit with a
    score keyword if present, otherwise the bare hit (no ``|`` suffix).
    """
    hits = [c for c in columns if model.lower() in c.lower()]
    cost = next((c for c in hits if "cost" in c.lower()), None)
    score = next((c for c in hits if any(k in c.lower() for k in _SCORE_KEYWORDS)), None)
    if score is None:
        bare = [c for c in hits if "|" not in c]
        score = bare[0] if bare else None
    return score, cost


def _prompt_text(value: object) -> str:
    """Reconstruct the prompt text the model saw.

    RouterBench stores each prompt as the string repr of a list of parts, e.g.
    ``"['please write a title for: ', 'In this study ...']"``. Parse it back and join
    the parts with newlines; fall back to the raw string if it is not a list literal.
    """
    if not isinstance(value, str):
        return str(value)
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value
    if isinstance(parsed, (list, tuple)):
        return "\n".join(str(part) for part in parsed)
    return str(parsed)


def _resolve_model(model_names: list[str], substring: str, side: str) -> str:
    """Resolve a model substring to exactly one model_name in the long-form table."""
    hits = sorted({m for m in model_names if substring.lower() in m.lower()})
    if len(hits) != 1:
        raise SystemExit(
            f"--{side} {substring!r} matched {hits or 'no models'}; "
            f"need exactly one of {sorted(model_names)}"
        )
    return hits[0]


def _pivot_long(df, local_sub: str, cloud_sub: str):
    """Pivot the raw long-form RouterBench table (one row per sample x model) into the
    wide per-model schema for just the two requested models, so the rest of the adapter
    is identical whether the source was a wide or a raw pickle."""
    names = df["model_name"].unique().tolist()
    local = _resolve_model(names, local_sub, "local")
    cloud = _resolve_model(names, cloud_sub, "cloud")
    wide = None
    for name in (local, cloud):
        side = df[df["model_name"] == name][
            ["sample_id", "prompt", "eval_name", "performance", "cost"]
        ].rename(columns={"performance": name, "cost": f"{name}|total_cost"})
        if wide is None:
            wide = side
        else:
            wide = wide.merge(
                side[["sample_id", name, f"{name}|total_cost"]], on="sample_id", how="inner"
            )
    return wide


def _is_number(value: object) -> bool:
    """True for a real, non-missing numeric label/cost (rejects None and NaN)."""
    return value is not None and value == value  # NaN != NaN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert RouterBench to harness JSONL.")
    ap.add_argument("--dataset", default="withmartian/routerbench",
                    help="Hub id, or a path to a local .pkl (wide or raw long form)")
    ap.add_argument("--split", default="train")
    ap.add_argument("--local", required=True, help="substring of the small/cheap model column")
    ap.add_argument("--cloud", required=True, help="substring of the frontier model column")
    ap.add_argument("--prompt-col", default="prompt")
    ap.add_argument("--task-col", default="eval_name", help="source-task column, used as the bucket tag")
    ap.add_argument("--out", default="benchmarks/routerbench.jsonl")
    args = ap.parse_args(argv)

    if args.dataset.endswith((".pkl", ".pickle")):
        try:
            import pandas as pd
        except ImportError:
            print("install pandas to read a local RouterBench pickle:  pip install pandas",
                  file=sys.stderr)
            return 2
        df = pd.read_pickle(args.dataset)
        if _LONG_FORMAT_COLS <= set(df.columns):
            df = _pivot_long(df, args.local, args.cloud)
        columns = list(df.columns)
        rows: object = df.to_dict("records")
    else:
        try:
            from datasets import load_dataset
        except ImportError:
            print("install the extra first:  pip install datasets", file=sys.stderr)
            return 2
        ds = load_dataset(args.dataset, split=args.split)
        columns = list(ds.features)
        rows = ds

    ls, lc = _find_columns(columns, args.local)
    cs, cc = _find_columns(columns, args.cloud)
    if not all([ls, lc, cs, cc]):
        print("could not resolve model columns. available columns:", file=sys.stderr)
        for c in columns:
            print("  ", c, file=sys.stderr)
        print(f"\nresolved -> local score={ls} cost={lc} ; cloud score={cs} cost={cc}", file=sys.stderr)
        return 1

    n = skipped = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            ls_v, cs_v = row[ls], row[cs]
            lc_v, cc_v = row[lc], row[cc]
            if not all(_is_number(v) for v in (ls_v, cs_v, lc_v, cc_v)):
                skipped += 1  # a missing graded score/cost can't be a clean label — skip, don't guess
                continue
            f.write(json.dumps({
                "prompt": _prompt_text(row[args.prompt_col]),
                "difficulty": str(row.get(args.task_col, "?")),
                "label": {"local": float(ls_v), "cloud": float(cs_v)},
                "cost": {"local": float(lc_v), "cloud": float(cc_v)},
            }) + "\n")
            n += 1
    suffix = f"  (skipped {skipped} rows with a missing score/cost)" if skipped else ""
    print(f"wrote {n} rows to {args.out}  (local={ls}/{lc}, cloud={cs}/{cc}){suffix}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
