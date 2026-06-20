"""Mine a trigger lexicon from labeled prompts — data-driven words, not a hand-curated list.

The configurable lexicon (WF-ADR-0019) lets a deployment supply its own trigger words; the
honest way to choose them is to *mine them from your own labels* rather than guess. This is
the deferred "learn the lexicon" alternative from that ADR, kept as an offline analysis tool
(promoting it to a shipped `calibrate` capability would warrant its own ADR).

Method (deterministic): split leakage-free, then on the TRAIN split rank each word by how
much more often it appears in cloud-labeled prompts than local-labeled ones — a smoothed
log-odds — keeping terms with enough support. Build a `Lexicon` from the top terms, calibrate
the cost-aware knee with it, and score on the HELD-OUT test split, against the built-in
lexicon. Words and metrics depend only on the data, the fixed salt, and the fixed knobs.

Run:  python -m benchmarks.mine_lexicon benchmarks/routerbench.jsonl
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

from benchmarks.harness import Row, evaluate, evaluate_oracle, load_dataset
from benchmarks.routerbench_calibrate import arm_costs, domain_of, oracle_label
from benchmarks.split import split_rows
from wayfinder_router import RoutingConfig, score_complexity
from wayfinder_router.calibrate import Sample, calibrate
from wayfinder_router.complexity import (
    DEFAULT_LEXICON,
    DEFAULT_WEIGHTS,
    Lexicon,
    binary_tiers,
)
from wayfinder_router.complexity import _WORD_TOKEN_RE as _TOKEN  # the scorer's own tokenizer
from wayfinder_router.config import dump_routing_toml

LOCAL, CLOUD = "local", "cloud"
_TOP_K = 40
_MIN_SUPPORT = 25       # a term must appear in at least this many cloud prompts to qualify
_MIN_LEN = 3            # drop 1-2 char tokens (mostly function words / noise)
_REASONING_WEIGHT = 5.0  # weight on reasoning_term_count for the isolated lexicon comparison
# A few function words that survive the length filter; log-odds demotes most, this is belt-and-braces.
_STOP = frozenset("the and for are was were has had have not you your with this that from "
                  "what which when where who whom how why into out over under than then".split())


def _doc_terms(prompt: str) -> set[str]:
    """The distinct lower-cased word tokens of a prompt (the scorer's tokenizer)."""
    return {t for t in _TOKEN.findall(prompt.lower()) if len(t) >= _MIN_LEN and t not in _STOP}


def mine_terms(rows: list[Row], *, top_k: int = _TOP_K, min_support: int = _MIN_SUPPORT) -> list[str]:
    """Top words by smoothed log-odds of appearing in cloud-labeled vs local-labeled prompts.

    Deterministic: ranked by (score desc, term asc). A term needs ``min_support`` cloud docs."""
    df_cloud: Counter[str] = Counter()
    df_local: Counter[str] = Counter()
    n_cloud = n_local = 0
    for r in rows:
        terms = _doc_terms(r.prompt)
        if oracle_label(r) == CLOUD:
            n_cloud += 1
            df_cloud.update(terms)
        else:
            n_local += 1
            df_local.update(terms)
    if not n_cloud or not n_local:
        return []
    scored: list[tuple[float, str]] = []
    for term, c in df_cloud.items():
        if c < min_support:
            continue
        p_cloud = (c + 1) / (n_cloud + 2)
        p_local = (df_local.get(term, 0) + 1) / (n_local + 2)
        scored.append((math.log(p_cloud / p_local), term))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [term for _, term in scored[:top_k]]


def _samples(rows: list[Row], lexicon: Lexicon) -> list[Sample]:
    from wayfinder_router.complexity import extract_features
    return [Sample(extract_features(r.prompt, lexicon=lexicon), oracle_label(r), 0.0) for r in rows]


def _calibrate_and_eval(train: list[Row], test: list[Row], lexicon: Lexicon):
    """Knee-calibrate (reasoning-only weight) with ``lexicon`` on train; score on test.

    Returns (config, test Metrics). Math/constraint stay at 0.0 so the only signal is the
    reasoning vocabulary — an apples-to-apples test of the word list itself."""
    weights = dict(DEFAULT_WEIGHTS, reasoning_term_count=_REASONING_WEIGHT)
    res = calibrate(_samples(train, lexicon), "threshold", objective="knee",
                    costs=arm_costs(train), weights=weights)
    cfg = RoutingConfig(weights=weights, tiers=binary_tiers(res.summary["threshold"]), lexicon=lexicon)
    metrics = evaluate("x", lambda p: score_complexity(p, config=cfg).recommendation, test)
    return cfg, metrics


def _skill(m) -> float:
    return m.pgr - m.frac_cloud


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else Path(__file__).parent / "routerbench.jsonl"
    rows = load_dataset(path)
    train, test = split_rows(rows, test_frac=0.5, salt="mine")
    print(f"set: {path.name} — {len(rows)} prompts (train {len(train)} / test {len(test)})\n")

    mined = mine_terms(train)
    print(f"=== Top {len(mined)} cloud-signal terms mined from the TRAIN split (log-odds) ===")
    print("  " + ", ".join(mined) + "\n")

    print("=== Per-domain mined terms (top 8 each) — the 'subject-matter-expertise' lexicons ===")
    by_domain: dict[str, list[Row]] = {}
    for r in train:
        by_domain.setdefault(domain_of(r.difficulty), []).append(r)
    for domain in sorted(by_domain):
        terms = mine_terms(by_domain[domain], top_k=8, min_support=8)
        print(f"  {domain:13s} {', '.join(terms) if terms else '(too few rows)'}")

    print("\n=== Held-out skill: built-in vs mined reasoning lexicon (reasoning-only weight) ===")
    print("(same weight, knee-calibrated on train, scored on test; only the word list differs)\n")
    print(f"  {'lexicon':22s} {'PGR':>7} {'->cloud':>8} {'skill':>8} {'saved':>7}")
    oracle = evaluate_oracle(test)
    print(f"  {'oracle (ceiling)':22s} {oracle.pgr:>7.3f} {oracle.frac_cloud:>7.0%} "
          f"{_skill(oracle):>+8.3f} {oracle.cost_savings:>6.0%}")
    builtin_cfg, builtin_m = _calibrate_and_eval(train, test, DEFAULT_LEXICON)
    print(f"  {'built-in reasoning':22s} {builtin_m.pgr:>7.3f} {builtin_m.frac_cloud:>7.0%} "
          f"{_skill(builtin_m):>+8.3f} {builtin_m.cost_savings:>6.0%}")
    mined_cfg, mined_m = _calibrate_and_eval(train, test, Lexicon(reasoning_terms=frozenset(mined)))
    print(f"  {'mined reasoning':22s} {mined_m.pgr:>7.3f} {mined_m.frac_cloud:>7.0%} "
          f"{_skill(mined_m):>+8.3f} {mined_m.cost_savings:>6.0%}")
    print(f"\n  mined − built-in: {_skill(mined_m) - _skill(builtin_m):+.3f} skill")

    print("\n=== Deployable config from the mined lexicon (drop into wayfinder-router.toml) ===\n")
    print(dump_routing_toml(mined_cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
