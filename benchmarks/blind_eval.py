"""Double-blind evaluation of the frozen scorer on independently-authored prompts.

The canonical benchmark (``dataset.jsonl``) was authored by the same person who wrote
the router, so it can flatter the lexical signals: a prompt written by someone who
knows the lexicon is more likely to contain the lexicon. To measure that bias we
evaluate the *frozen* scorer — no peeking, no re-tuning after seeing the results —
against a prompt set written by an independent author who was given only a
scorer-blind, human-difficulty brief ("easy" vs "hard", with a plain/structured form
tag) and no hint of which words or structures the scorer rewards.

``benchmarks/blind/openai-cross-provider.jsonl`` is one such set, authored by a
different provider's model (OpenAI) from that brief. Labels are *by construction* —
easy -> both models right ``{local:1, cloud:1}``; hard -> only the strong model right
``{local:0, cloud:1}`` — which is the acknowledged weak link until real graded labels
(RouterBench) replace them; see ``benchmarks/routerbench_adapter.py``. By-construction
labels still answer the question this test is for: does the structural/lexical signal
*separate* independently-authored hard prompts from easy ones, or did it only ever
separate the author's own?

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
from wayfinder_router import complexity as C

LEXICAL = ("reasoning_term_count", "math_symbol_count", "constraint_term_count")
DEFAULT_SET = Path(__file__).parent / "blind" / "openai-cross-provider.jsonl"
GRID = [round(x / 100, 2) for x in range(0, 101)]


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


def _lexical_router(t: float):
    return lambda p: score_complexity(p, config=RoutingConfig.binary(threshold=t)).recommendation


def _structure_router(weights: dict[str, float], t: float):
    cfg = RoutingConfig.binary(threshold=t)
    cfg = RoutingConfig(tiers=cfg.tiers, weights=weights)
    return lambda p: score_complexity(p, config=cfg).recommendation


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT_SET
    rows, meta = load(path)
    easy = [r for r, m in zip(rows, meta, strict=True) if m[0] == "easy"]
    hard = [r for r, m in zip(rows, meta, strict=True) if m[0] == "hard"]
    print(f"set: {path.name} — {len(rows)} prompts ({len(easy)} easy / {len(hard)} hard)\n")

    structure_weights = dict(C.DEFAULT_WEIGHTS)
    for k in (*LEXICAL, "question_count"):
        structure_weights[k] = 0.0

    lex_pts = harness.sweep(rows, _lexical_router, GRID)
    lt, lm = harness.knee(lex_pts)
    str_pts = harness.sweep(rows, lambda t: _structure_router(structure_weights, t), GRID)
    st, sm = harness.knee(str_pts)
    len_pts = harness.sweep(
        rows, lambda w: (lambda p: length_threshold(p, int(w))), [5, 10, 15, 20, 30, 50, 80, 120]
    )
    lw, lbm = harness.knee(len_pts)

    print("cost-aware knee (objective = PGR x cost_savings):")
    print(f"  lexical-on      t={lt:<5}  PGR={lm.pgr:.3f}  saved={lm.cost_savings:.3f}")
    print(f"  structure-only  t={st:<5}  PGR={sm.pgr:.3f}  saved={sm.cost_savings:.3f}")
    print(f"  length-only     w={int(lw):<5}  PGR={lbm.pgr:.3f}  saved={lbm.cost_savings:.3f}")
    print(f"  lexical lift over structure-only : {lm.pgr - sm.pgr:+.3f} PGR")
    print(f"  lexical margin over length base  : {lm.pgr - lbm.pgr:+.3f} PGR\n")

    # At a realistic low cut, how much of the gap does the lexical signal actually catch?
    at10 = harness.evaluate("t=0.10", _lexical_router(0.10), rows)
    print(f"lexical at a fixed t=0.10: PGR={at10.pgr:.3f}  -> cloud {at10.frac_cloud:.0%}")

    def fired(p: str) -> int:
        f = C.extract_features(p)
        return sum(f[k] for k in LEXICAL)

    caught = sum(1 for r in hard if fired(r.prompt) > 0)
    print(f"hard prompts with any lexical signal: {caught}/{len(hard)} = {caught/len(hard):.0%}")
    easy_fp = sum(
        1
        for r in easy
        if score_complexity(r.prompt, config=RoutingConfig.binary(threshold=lt)).recommendation
        == "cloud"
    )
    print(f"easy prompts routed cloud at the knee: {easy_fp}/{len(easy)} = {easy_fp/len(easy):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
