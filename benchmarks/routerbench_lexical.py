"""Skill-over-random and lexical opt-in check for a RouterBench (or any) harness set.

Two honest cross-checks the headline ``benchmarks.run`` table does not make explicit:

  * **Skill over random.** A router that routes fraction ``f`` of prompts to the strong
    model recovers, *in expectation if it chose at random*, exactly ``PGR = f``. So a
    structural router's real skill is ``PGR - frac_cloud``: positive only if its ranking
    of *which* prompts need the frontier model beats chance. PGR alone flatters any
    router that simply routes more to cloud.

  * **Lexical opt-in (WF-ADR-0016).** Re-runs Wayfinder with the lexical signals turned
    on (the ``OPTED_IN_WEIGHTS`` a user would calibrate) to test whether, on real graded
    labels, the keyword lexicon adds skill over the shipped structural-only default.

The structural score is independent of the threshold, so it is computed once per prompt
and the threshold is swept as a cheap comparison — the routers handed to ``harness`` are
pure dict lookups, so every metric still comes from the one audited harness.

Run:  python -m benchmarks.routerbench_lexical benchmarks/routerbench.jsonl
"""
from __future__ import annotations

import sys
from pathlib import Path

from benchmarks import harness
from benchmarks.blind_eval import OPTED_IN_WEIGHTS
from benchmarks.harness import Metrics
from benchmarks.routers import CLOUD, LOCAL, deterministic_random, length_threshold
from wayfinder_router.complexity import DEFAULT_WEIGHTS, extract_features, scalar_score

_THRESHOLDS = [round(i * 0.01, 2) for i in range(0, 51)]  # 0.00 .. 0.50
_WORD_CUTS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0, 120.0, 200.0]


def _score_lookup(prompts: set[str], weights: dict[str, float]) -> dict[str, float]:
    """The structural score of each unique prompt under ``weights`` (computed once)."""
    return {p: scalar_score(extract_features(p), weights) for p in prompts}


def _router(scores: dict[str, float], threshold: float):
    return lambda p: CLOUD if scores[p] >= threshold else LOCAL


def _skill(m: Metrics) -> float:
    """PGR lift over routing the same cloud fraction at random (expectation = frac_cloud)."""
    return m.pgr - m.frac_cloud


def _line(label: str, knob: str, m: Metrics) -> str:
    return (f"  {label:24s} {knob:>10s}  PGR={m.pgr:+.3f}  frac_cloud={m.frac_cloud:.3f}  "
            f"skill(PGR-frac)={_skill(m):+.3f}  saved={m.cost_savings:.3f}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else Path(__file__).parent / "routerbench.jsonl"
    rows = harness.load_dataset(path)
    prompts = {r.prompt for r in rows}
    print(f"set: {path.name} — {len(rows)} prompts ({len(prompts)} unique)\n")

    rnd = harness.evaluate("random", deterministic_random, rows)
    print(_line("random (stable)", "-", rnd))

    len_pts = harness.sweep(rows, lambda w: (lambda p: length_threshold(p, int(w))), _WORD_CUTS)
    lw, lm = harness.knee(len_pts)
    print(_line("length-threshold", f">={int(lw)}w", lm))

    default_scores = _score_lookup(prompts, dict(DEFAULT_WEIGHTS))
    dt_pts = harness.sweep(rows, lambda t: _router(default_scores, t), _THRESHOLDS)
    dt, dm = harness.knee(dt_pts)
    print(_line("wayfinder (structural)", f"t={dt:.2f}", dm))

    lexical_scores = _score_lookup(prompts, OPTED_IN_WEIGHTS)
    ot_pts = harness.sweep(rows, lambda t: _router(lexical_scores, t), _THRESHOLDS)
    ot, om = harness.knee(ot_pts)
    print(_line("wayfinder (lexical on)", f"t={ot:.2f}", om))

    print(f"\n  lexical opt-in margin over structural default : "
          f"{om.pgr - dm.pgr:+.3f} PGR, {_skill(om) - _skill(dm):+.3f} skill")
    print(f"  structural default skill over random          : {_skill(dm):+.3f} "
          f"({'beats' if _skill(dm) > 0 else 'does NOT beat'} chance selection)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
