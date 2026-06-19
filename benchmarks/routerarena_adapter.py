"""Adapt RouterArena's cached evaluation results into the benchmark harness's shape.

RouterArena (RouteWorks) publishes, in its GitHub repo, `cached_results/<model>.jsonl`:
for each prompt (`global_index`) and model, a real graded `score` and a real
`inference_cost`. That is the same offline, already-run structure as RouterBench — any
router can be scored with no API key and no inference — but it is reachable over
`raw.githubusercontent.com` (no HuggingFace egress needed).

This joins two model files on `global_index` and emits harness rows with the real
`score` as the per-model label and the benchmark family as the difficulty tag. Pick a
weaker model as `local` and a stronger one as `cloud`. Costs are a separate axis: the
cached `inference_cost` is real but, across the three small models cached in-repo, it
does not track quality (the most accurate model is also the cheapest), so by default
this emits the harness's role-based synthetic cost. Pass `--real-cost` to emit the
cached dollar cost instead and judge the cost axis on real numbers.

Usage:
    python -m benchmarks.routerarena_adapter \
        --local claude-3-haiku-20240307 --cloud gemini-2.0-flash-001 \
        --out benchmarks/routerarena.jsonl
    python -m benchmarks.run benchmarks/routerarena.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/RouteWorks/RouterArena/main/cached_results/"


def _fetch(model: str) -> dict[str, dict]:
    """Stream-parse `<model>.jsonl` (records are not strictly one-per-line)."""
    raw = urllib.request.urlopen(RAW + model + ".jsonl", timeout=60).read().decode("utf-8")
    dec = json.JSONDecoder()
    out: dict[str, dict] = {}
    i, n = 0, len(raw)
    while i < n:
        while i < n and raw[i] in " \r\n\t":
            i += 1
        if i >= n:
            break
        obj, end = dec.raw_decode(raw, i)
        out[obj["global_index"]] = obj
        i = end
    return out


def _family(global_index: str) -> str:
    return global_index.rsplit("_", 1)[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert RouterArena cached results to harness JSONL.")
    ap.add_argument("--local", required=True, help="weaker/cheaper model file stem (the 'local' arm)")
    ap.add_argument("--cloud", required=True, help="stronger model file stem (the 'cloud' arm)")
    ap.add_argument("--real-cost", action="store_true", help="emit cached inference_cost, not role cost")
    ap.add_argument("--out", default="benchmarks/routerarena.jsonl")
    args = ap.parse_args(argv)

    try:
        local, cloud = _fetch(args.local), _fetch(args.cloud)
    except OSError as e:  # network/egress
        print(f"fetch failed: {e}", file=sys.stderr)
        return 2

    common = sorted(set(local) & set(cloud))
    if not common:
        print("no shared global_index between the two models", file=sys.stderr)
        return 1

    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for g in common:
            lr, cr = local[g], cloud[g]
            row: dict = {
                "prompt": lr["question"],
                "difficulty": _family(g),
                "label": {
                    "local": float(lr["evaluation_result"]["score"]),
                    "cloud": float(cr["evaluation_result"]["score"]),
                },
            }
            if args.real_cost:
                row["cost"] = {
                    "local": float(lr["evaluation_result"]["inference_cost"]),
                    "cloud": float(cr["evaluation_result"]["inference_cost"]),
                }
            f.write(json.dumps(row) + "\n")
            n += 1
    print(f"wrote {n} rows to {args.out}  (local={args.local}, cloud={args.cloud})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
