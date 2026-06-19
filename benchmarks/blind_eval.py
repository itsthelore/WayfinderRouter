"""Double-blind evaluation of the frozen scorer on independently-authored prompts.

The canonical benchmark (``dataset.jsonl``) and the router share an author, which can
flatter the scorer: prompts written by someone who knows what the scorer rewards are
likelier to contain those signals. To measure that bias we evaluate the *frozen*
scorer — no peeking, no re-tuning after seeing results — against prompts written by an
independent author given only a scorer-blind brief ("easy" vs "hard" in human terms,
a plain/structured form tag) and no hint of which words or structures score high.

``blind/openai-cross-provider.jsonl`` is one such set (154 prompts), authored by a
different provider's model (OpenAI) from that brief. Labels are *by construction* —
easy -> ``{local:1, cloud:1}``; hard -> ``{local:0, cloud:1}`` — the acknowledged weak
link, replaced by real graded labels via ``routerbench_adapter.py`` once a RouterBench
pull is reachable. By-construction labels still answer the one question this test is
for: does a signal *separate* independently-authored hard prompts from easy ones, or
did it only ever separate the author's own?

This is the test that sent the lexical signals (WF-ADR-0016) to *opt-in, off by
default*: even with the lexical weights turned on, the curated lexicon caught only
~20% of independently-authored hard prompts and lost to a word-count baseline. The
harness compares the shipped default (structural-only) against an opted-in lexical
config and a length baseline; see ``blind-eval.md``.

Run:  python -m benchmarks.blind_eval [path-to-jsonl]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmarks import harness
from benchmarks.harness import Row
from benchmarks.routers import length_threshold
from wayfinder_router import RoutingConfig, score_complexity
from wayfinder_router.complexity import DEFAULT_WEIGHTS, extract_features

DEFAULT_SET = Path(__file__).parent / "blind" / "openai-cross-provider.jsonl"
GRID = [round(x / 100, 2) for x in range(0, 101)]
LEXICAL = ("reasoning_term_count", "math_symbol_count", "constraint_term_count")
# An opted-in lexical config: the weights a user calibrates on if they enable the
# lexical signals (the v0.1.x trial defaults), vs the shipped 0.0 (off).
OPTED_IN_WEIGHTS = dict(DEFAULT_WEIGHTS) | {
    "reasoning_term_count": 5.0,
    "math_symbol_count": 3.0,
    "constraint_term_count": 1.5,
}


def load(path: Path) -> tuple[list[Row], list[tuple[str, str]]]:
    rows: list[Row] = []
    meta: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        diff, form = o["difficulty"], o.get("form", "?")
        label = {"local": 1, "cloud": 1} if diff == "easy" else {"local": 0, "cloud": 1}
        rows.append(Row(prompt=o["prompt"], difficulty=f"{diff}-{form}", label=label))
        meta.append((diff, form))
    return rows, meta


def _router(t: float, weights: dict[str, float] | None = None):
    cfg = RoutingConfig.binary(threshold=t, weights=weights)
    return lambda p: score_complexity(p, config=cfg).recommendation


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT_SET
    rows, meta = load(path)
    hard = [r for r, m in zip(rows, meta, strict=True) if m[0] == "hard"]
    print(f"set: {path.name} — {len(rows)} prompts "
          f"({len(rows) - len(hard)} easy / {len(hard)} hard)\n")

    def knee(weights: dict[str, float] | None):
        return harness.knee(harness.sweep(rows, lambda t: _router(t, weights), GRID))

    dt, dm = knee(None)  # shipped default: lexical off
    ot, om = knee(OPTED_IN_WEIGHTS)  # opted-in: lexical on
    len_pts = harness.sweep(
        rows, lambda w: (lambda p: length_threshold(p, int(w))), [5, 10, 15, 20, 30, 50, 80, 120]
    )
    lw, lbm = harness.knee(len_pts)

    print("cost-aware knee (objective = PGR x cost_savings):")
    print(f"  default (lexical off)   t={dt:<5}  PGR={dm.pgr:.3f}  saved={dm.cost_savings:.3f}")
    print(f"  opted-in (lexical on)   t={ot:<5}  PGR={om.pgr:.3f}  saved={om.cost_savings:.3f}")
    print(f"  length-only             w={int(lw):<5}  PGR={lbm.pgr:.3f}  saved={lbm.cost_savings:.3f}")
    print(f"  opted-in lexical margin over the length baseline : {om.pgr - lbm.pgr:+.3f} PGR\n")

    fires = sum(1 for r in hard if sum(extract_features(r.prompt)[k] for k in LEXICAL) > 0)
    print(f"hard prompts with any lexical signal: {fires}/{len(hard)} = {fires/len(hard):.0%}")
    om10 = harness.evaluate("t=0.10", _router(0.10, OPTED_IN_WEIGHTS), rows)
    print(f"opted-in lexical at a fixed t=0.10: PGR={om10.pgr:.3f}  -> cloud {om10.frac_cloud:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
