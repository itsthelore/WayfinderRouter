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
# Function words that survive the length filter; log-odds demotes most, this is belt-and-braces.
_STOP = frozenset("the and for are was were has had have not you your with this that from "
                  "what which when where who whom how why into out over under than then".split())
# Instruction / boilerplate vocabulary that rides along with cloud-needed tasks (answer the
# question, provide the steps, …). Dropped only for the per-domain *expertise* lists so the
# domain's own vocabulary shows through — global mining keeps using just _STOP.
_BOILERPLATE = frozenset("""
answer answers question questions problem problems following follow provide given give using used use
based write written return returns output input solve find select choose option options correct incorrect
true false explain explanation step steps example examples format final number numbers list lists value
values check whether will would should could can may might must each between among about above below text
sentence word words name names letter letters line lines case cases set sets group groups type types part
parts please first second third next last also more most some many much very just only then there their
they them its his her our additional addition older current upper fails getting respond indicate addressing
assistant called include included according main clearly close free another less started trying obtain
provided provides resulting print user range sign signs subject described allows expected larger view acts
""".split())
_DOMAIN_STOP = _STOP | _BOILERPLATE


def _doc_terms(prompt: str, *, min_len: int = _MIN_LEN, stop: frozenset[str] = _STOP) -> set[str]:
    """The distinct lower-cased word tokens of a prompt (the scorer's tokenizer)."""
    return {t for t in _TOKEN.findall(prompt.lower()) if len(t) >= min_len and t not in stop}


def mine_terms(
    rows: list[Row], *, top_k: int = _TOP_K, min_support: int = _MIN_SUPPORT,
    min_len: int = _MIN_LEN, stop: frozenset[str] = _STOP, min_log_odds: float | None = None,
) -> list[str]:
    """Top words by smoothed log-odds of appearing in cloud-labeled vs local-labeled prompts.

    Deterministic: ranked by (score desc, term asc). A term needs ``min_support`` cloud docs;
    ``min_log_odds`` (when set) keeps only terms skewed at least that much toward cloud."""
    df_cloud: Counter[str] = Counter()
    df_local: Counter[str] = Counter()
    n_cloud = n_local = 0
    for r in rows:
        terms = _doc_terms(r.prompt, min_len=min_len, stop=stop)
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
        log_odds = math.log(((c + 1) / (n_cloud + 2)) / ((df_local.get(term, 0) + 1) / (n_local + 2)))
        if min_log_odds is not None and log_odds < min_log_odds:
            continue
        scored.append((log_odds, term))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [term for _, term in scored[:top_k]]


def mine_per_domain(
    rows: list[Row], *, top_k: int = 20, min_len: int = 4, min_log_odds: float = 0.6,
) -> dict[str, list[str]]:
    """Per-domain expertise term lists mined from labelled traffic.

    Groups by :func:`domain_of`, drops instruction boilerplate (``_DOMAIN_STOP``) so the
    domain's vocabulary shows through, and keeps only strongly cloud-skewed terms with
    support scaled to the domain's size. Domains that yield nothing (too few/too uniform
    prompts) are omitted. Deterministic."""
    by_domain: dict[str, list[Row]] = {}
    for r in rows:
        by_domain.setdefault(domain_of(r.difficulty), []).append(r)
    out: dict[str, list[str]] = {}
    for domain, dom_rows in by_domain.items():
        n_cloud = sum(1 for r in dom_rows if oracle_label(r) == CLOUD)
        min_support = max(10, n_cloud // 100)
        terms = mine_terms(dom_rows, top_k=top_k, min_support=min_support, min_len=min_len,
                           stop=_DOMAIN_STOP, min_log_odds=min_log_odds)
        if terms:
            out[domain] = terms
    return out


_DOMAIN_FILE_HEADER = """\
# Per-domain trigger-word lists mined from RouterBench labelled traffic (WF-ADR-0019).
#
# Each list is the vocabulary that, in RouterBench, appears far more in cloud-needed prompts
# than local-ok ones (deterministic smoothed log-odds on a held-out train split). These are
# STARTER templates, not a universal lexicon: copy the block for your domain into your
# wayfinder-router.toml as `[routing.lexicon] reasoning_terms = [...]`, raise the
# `reasoning_term_count` weight, and recalibrate the knee on your own data.
#
# Honest about quality: science / general / humanities give real subject-matter vocabulary;
# math / multilingual / commonsense skew to task-surface nouns (RouterBench's tasks there are
# word-problems / templated), and sparse domains (e.g. code) may be absent. The right move is
# to mine YOUR traffic: `python -m benchmarks.mine_lexicon your-data.jsonl --emit-domains out.toml`.
#
# Reproduce: python -m benchmarks.mine_lexicon benchmarks/routerbench.jsonl \\
#                --emit-domains benchmarks/seed/domain-lexicons.toml
"""


def _emit_domain_file(path: Path, per_domain: dict[str, list[str]]) -> None:
    blocks = [_DOMAIN_FILE_HEADER]
    for domain in sorted(per_domain):
        terms = ", ".join(f'"{t}"' for t in per_domain[domain])
        blocks.append(f"[{domain}]\nreasoning_terms = [{terms}]")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


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
    emit_to: Path | None = None
    if "--emit-domains" in argv:
        i = argv.index("--emit-domains")
        emit_to = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    path = Path(argv[0]) if argv else Path(__file__).parent / "routerbench.jsonl"
    rows = load_dataset(path)
    train, test = split_rows(rows, test_frac=0.5, salt="mine")
    print(f"set: {path.name} — {len(rows)} prompts (train {len(train)} / test {len(test)})\n")

    mined = mine_terms(train)
    print(f"=== Top {len(mined)} cloud-signal terms mined from the TRAIN split (log-odds) ===")
    print("  " + ", ".join(mined) + "\n")

    per_domain = mine_per_domain(train)
    print("=== Per-domain mined terms — the 'subject-matter-expertise' lexicons ===")
    for domain in sorted(per_domain):
        print(f"  {domain:13s} {', '.join(per_domain[domain])}")
    if emit_to is not None:
        _emit_domain_file(emit_to, per_domain)
        print(f"\nwrote per-domain lexicons to {emit_to}")
        return 0

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
