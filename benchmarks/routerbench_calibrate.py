"""Held-out calibration evaluation — does fitting the router on a labeled bootstrap help?

The headline numbers in ``benchmarks/routerbench-results.md`` are *in-sample*: the
threshold/knee was swept on the same rows it reports, so they overstate. This driver redoes
the evaluation **leakage-free** — calibrate on a train split, score once on a held-out test
split — and answers three concrete questions on real RouterBench labels:

  1. Does calibrating (the shipped threshold / cost-quality / classifier modes, or the
     lexical opt-in) beat the structural default and the random/length baselines on *unseen*
     rows?
  2. Is ~20 labeled prompts enough, or does any signal only emerge at N in the hundreds?
     (``--curve``)
  3. Does per-domain calibration (science / maths / general …) beat one global config?

Everything is deterministic and offline (WF-ADR-0001): no model is called; labels are the
oracle. The metric that matters is **skill = PGR - frac_cloud** — a router routing fraction
f to cloud recovers PGR = f by chance, so skill > 0 means it beats chance at choosing *which*
prompts to escalate (see ``benchmarks/routerbench_lexical.py``).

Run:
    python -m benchmarks.routerbench_calibrate benchmarks/routerbench.jsonl
    python -m benchmarks.routerbench_calibrate benchmarks/routerbench.jsonl --curve
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from benchmarks import harness
from benchmarks.blind_eval import OPTED_IN_WEIGHTS
from benchmarks.harness import Metrics, Row, evaluate, evaluate_oracle, knee, load_dataset, sweep
from benchmarks.routers import CLOUD, LOCAL, deterministic_random, length_threshold
from benchmarks.split import split_rows, train_order
from wayfinder_router.calibrate import CalibrationError, Sample, calibrate
from wayfinder_router.complexity import (
    DEFAULT_WEIGHTS,
    extract_features,
    recommend_tier,
    scalar_score,
)
from wayfinder_router.config import routing_config_from_toml

_THRESHOLDS = [round(i * 0.01, 2) for i in range(0, 61)]  # 0.00 .. 0.60
_WORD_CUTS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0, 120.0, 200.0]
_SALTS = ["a", "b", "c", "d", "e"]  # K independent splits for variance
_CURVE_N = [10, 20, 50, 100, 500, 2000]

# --- feature/score caches (extract_features is the only expensive step; memoize by prompt) ---
_FEATS: dict[str, dict[str, int]] = {}


def feats(prompt: str) -> dict[str, int]:
    cached = _FEATS.get(prompt)
    if cached is None:
        cached = extract_features(prompt)
        _FEATS[prompt] = cached
    return cached


# === labels & samples ===

def oracle_label(row: Row) -> str:
    """The model calibration should learn to pick — the same rule as harness.evaluate_oracle:
    route local whenever its graded score is at least cloud's (same-or-better quality, cheaper),
    else cloud. Correct for fractional scores (maximises quality, not 'any nonzero local wins')."""
    return LOCAL if row.label[LOCAL] >= row.label[CLOUD] else CLOUD


def to_samples(rows: list[Row], *, weights: dict[str, float] | None = None) -> list[Sample]:
    """Calibration samples with the oracle label. ``score`` uses ``weights`` (default weights
    unless given); the classifier ignores it, threshold mode sweeps on it."""
    w = weights or DEFAULT_WEIGHTS
    return [Sample(features=feats(r.prompt), label=oracle_label(r), score=scalar_score(feats(r.prompt), w))
            for r in rows]


def arm_costs(rows: list[Row]) -> dict[str, float]:
    """Mean real per-call cost of each arm, keyed by the oracle label — for cost-quality."""
    n = len(rows)
    return {
        LOCAL: sum((r.cost or harness.COST)[LOCAL] for r in rows) / n,
        CLOUD: sum((r.cost or harness.COST)[CLOUD] for r in rows) / n,
    }


# === routers ===

def config_router(cfg) -> harness.Router:
    """A harness Router from a RoutingConfig — mirrors score_complexity but reads the feature
    cache, so it uses the exact shipped scoring primitives (scalar_score / recommend_tier /
    classifier.predict) without re-extracting features."""
    if cfg.classifier is not None:
        return lambda p: cfg.classifier.predict(feats(p))
    return lambda p: recommend_tier(scalar_score(feats(p), cfg.weights), cfg.tiers)


def weighted_binary(weights: dict[str, float]):
    """make(t) -> Router that routes cloud when the ``weights``-scored prompt >= t."""
    return lambda t: (lambda p: CLOUD if scalar_score(feats(p), weights) >= t else LOCAL)


def router_from_result(result) -> harness.Router:
    """Round-trip a CalibrationResult through the shipped TOML parser into a Router — exactly
    the path a user deploys."""
    return config_router(routing_config_from_toml(result.toml))


def _skill(m: Metrics) -> float:
    return m.pgr - m.frac_cloud


# === one held-out split: calibrate on train, score on test ===

def _knee_router(train: list[Row], make) -> harness.Router:
    """Pick the cost-aware knee threshold on TRAIN, return the router at that threshold."""
    t, _ = knee(sweep(train, make, _THRESHOLDS))
    return make(t)


def evaluate_configs(train: list[Row], test: list[Row]) -> dict[str, Metrics | None]:
    """Every config selected on ``train``, scored once on ``test``. None == calibration skipped
    (e.g. a one-class train sample)."""
    out: dict[str, Metrics | None] = {}

    # Baselines (no calibration).
    out["oracle"] = evaluate_oracle(test)
    out["always-cloud"] = evaluate("c", lambda p: CLOUD, test)
    out["always-local"] = evaluate("l", lambda p: LOCAL, test)
    out["random"] = evaluate("r", deterministic_random, test)
    out["length (knee)"] = evaluate(
        "len", _knee_router(train, lambda w: (lambda p: length_threshold(p, int(w)))), test
    )

    # Knee baselines over fixed weights (the in-sample numbers, now held-out).
    out["wf structural (knee)"] = evaluate("s", _knee_router(train, weighted_binary(DEFAULT_WEIGHTS)), test)
    out["wf lexical-on (knee)"] = evaluate("x", _knee_router(train, weighted_binary(OPTED_IN_WEIGHTS)), test)

    # Shipped calibration tool outputs.
    samples = to_samples(train)
    for name, kwargs in (
        ("calibrate threshold", {"mode": "threshold"}),
        ("calibrate cost-quality", {"mode": "threshold", "objective": "cost-quality",
                                    "costs": arm_costs(train), "target_savings": 0.4}),
        ("calibrate classifier", {"mode": "classifier"}),
    ):
        try:
            result = calibrate(samples, **kwargs)
            out[name] = evaluate(name, router_from_result(result), test)
        except CalibrationError:
            out[name] = None
    return out


def _agg(values: list[float]) -> tuple[float, float]:
    return statistics.fmean(values), (statistics.pstdev(values) if len(values) > 1 else 0.0)


def held_out_table(rows: list[Row]) -> None:
    print(f"\n=== Held-out evaluation (K={len(_SALTS)} splits, 50/50, calibrate on train, "
          f"score on test) ===")
    print("skill = PGR - frac_cloud (>0 beats chance). Each cell is mean ± stdev over splits.\n")

    runs = [evaluate_configs(*split_rows(rows, test_frac=0.5, salt=s)) for s in _SALTS]
    names = list(runs[0].keys())

    print(f"| {'config':24} | {'PGR':>12} | {'→cloud':>12} | {'skill':>14} | {'cost saved':>10} |")
    print(f"| {'-'*24} | {'-'*12:>12} | {'-'*12:>12} | {'-'*14:>14} | {'-'*10:>10} |")
    for name in names:
        cells = [r[name] for r in runs]
        present = [m for m in cells if m is not None]
        if not present:
            print(f"| {name:24} | {'skipped (one-class train)':>54} |")
            continue
        pgr_m, _ = _agg([m.pgr for m in present])
        fc_m, _ = _agg([m.frac_cloud for m in present])
        sk_m, sk_sd = _agg([_skill(m) for m in present])
        cs_m, _ = _agg([m.cost_savings for m in present])
        skipped = len(cells) - len(present)
        note = f" (skipped {skipped}/{len(cells)})" if skipped else ""
        print(f"| {name:24} | {pgr_m:>12.3f} | {fc_m:>11.0%} | {sk_m:>+8.3f}±{sk_sd:<4.3f} | "
              f"{cs_m:>9.0%} |{note}")

    base = statistics.fmean([_skill(r["wf structural (knee)"]) for r in runs])
    print(f"\nreference: structural-default held-out skill = {base:+.3f}  "
          f"(random ~ 0.000 by construction).")


# === learning curve: is ~20 prompts enough? ===

def learning_curve(rows: list[Row]) -> None:
    print(f"\n=== Learning curve — held-out skill vs train size N (K={len(_SALTS)} splits) ===")
    print("classifier & threshold calibrated on the first N of a stable-shuffled train pool,\n"
          "scored on a fixed 30% test split. 'skipped' = train sample had only one class.\n")
    print(f"| {'N':>5} | {'mode':12} | {'test skill (mean±sd)':>22} | {'calibrated/K':>12} | "
          f"{'train local%':>12} |")
    print(f"| {'-'*5:>5} | {'-'*12} | {'-'*22:>22} | {'-'*12:>12} | {'-'*12:>12} |")

    pools = []
    for s in _SALTS:
        train, test = split_rows(rows, test_frac=0.3, salt=s)
        pools.append((train_order(train, salt=s), test))

    for n in _CURVE_N:
        for mode in ("threshold", "classifier"):
            skills: list[float] = []
            calibrated = 0
            local_fracs: list[float] = []
            for pool, test in pools:
                sub = pool[:n]
                local_fracs.append(sum(oracle_label(r) == LOCAL for r in sub) / len(sub))
                try:
                    result = calibrate(to_samples(sub), mode=mode)
                except CalibrationError:
                    continue
                calibrated += 1
                skills.append(_skill(evaluate("x", router_from_result(result), test)))
            lf_m, _ = _agg(local_fracs)
            if skills:
                m, sd = _agg(skills)
                cell = f"{m:+.3f} ± {sd:.3f}"
            else:
                cell = "— (all skipped)"
            print(f"| {n:>5} | {mode:12} | {cell:>22} | {calibrated:>6}/{len(pools):<5} | "
                  f"{lf_m:>11.0%} |")


# === per-domain: does science/maths/general specialization help? ===

_DOMAIN_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("code", ("mbpp", "humaneval", "code-llama", "leetcode")),
    ("math", ("grade-school-math", "mathematics", "algebra", "remainder-theorem", "mtbench-math")),
    ("multilingual", ("chinese",)),
    ("science", ("physics", "chemistry", "biology", "astronomy", "computer-science", "medicine",
                 "medical", "anatomy", "genetics", "nutrition", "clinical", "engineering",
                 "machine-learning", "econometrics", "statistics", "virology", "conceptual")),
    ("commonsense", ("hellaswag", "winogrande", "arc-", "moral-scenarios", "openbook")),
    ("humanities", ("history", "prehistory", "-law", "jurisprudence", "philosophy", "religion",
                    "logical-fallacies")),
]


def domain_of(eval_name: str) -> str:
    """Deterministic grouping of a RouterBench eval_name bucket into a domain (auditable via
    the coverage print). Falls back to 'general' (mmlu social-science, misc, summarization …)."""
    low = eval_name.lower()
    for domain, keys in _DOMAIN_RULES:
        if any(k in low for k in keys):
            return domain
    return "general"


def per_domain(rows: list[Row]) -> None:
    print("\n=== Per-domain calibration (global config vs a config fit on each domain) ===")
    by_domain: dict[str, list[Row]] = {}
    for r in rows:
        by_domain.setdefault(domain_of(r.difficulty), []).append(r)

    # Coverage (auditable): rows per domain.
    cov = "  ".join(f"{d}:{len(rs)}" for d, rs in sorted(by_domain.items()))
    print(f"coverage — {cov}\n")

    # Global config: one classifier fit on the whole train split, sliced per domain on test.
    train, test = split_rows(rows, test_frac=0.5, salt="domain")
    try:
        global_router = router_from_result(calibrate(to_samples(train), mode="classifier"))
    except CalibrationError:
        global_router = None
    test_by_domain: dict[str, list[Row]] = {}
    for r in test:
        test_by_domain.setdefault(domain_of(r.difficulty), []).append(r)

    print(f"| {'domain':14} | {'n test':>7} | {'global skill':>12} | {'per-domain skill':>16} | "
          f"{'Δ':>8} | {'dom train n':>11} |")
    print(f"| {'-'*14} | {'-'*7:>7} | {'-'*12:>12} | {'-'*16:>16} | {'-'*8:>8} | {'-'*11:>11} |")
    for domain in sorted(by_domain):
        dom_test = test_by_domain.get(domain, [])
        if not dom_test:
            continue
        g = _skill(evaluate("g", global_router, dom_test)) if global_router else None
        # Per-domain: independent split of this domain's rows (own salt → no leakage).
        d_train, d_test = split_rows(by_domain[domain], test_frac=0.5, salt=f"dom-{domain}")
        try:
            d_router = router_from_result(calibrate(to_samples(d_train), mode="classifier"))
            d = _skill(evaluate("d", d_router, d_test))
        except CalibrationError:
            d = None
        gs = f"{g:+.3f}" if g is not None else "—"
        ds = f"{d:+.3f}" if d is not None else "skip"
        delta = f"{d - g:+.3f}" if (g is not None and d is not None) else "—"
        print(f"| {domain:14} | {len(dom_test):>7} | {gs:>12} | {ds:>16} | {delta:>8} | "
              f"{len(d_train):>11} |")


# === hand-authored seed bootstrap: do a user's own ~20 prompts transfer? ===

def seed_transfer(rows: list[Row]) -> None:
    seed_path = Path(__file__).parent / "seed" / "domain-seed.jsonl"
    if not seed_path.exists():
        print(f"\n(seed set {seed_path} not found — skipping seed-transfer check)")
        return
    print("\n=== Hand-authored seed bootstrap → tested on ALL RouterBench (cross-distribution) ===")
    seed_samples: list[Sample] = []
    counts = {LOCAL: 0, CLOUD: 0}
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        label = o["label"]
        counts[label] = counts.get(label, 0) + 1
        f = feats(o["prompt"])
        seed_samples.append(Sample(features=f, label=label, score=scalar_score(f, DEFAULT_WEIGHTS)))
    print(f"seed: {len(seed_samples)} prompts ({counts.get(LOCAL,0)} local / {counts.get(CLOUD,0)} cloud); "
          f"the seed prompts are disjoint from RouterBench, so all 36k rows are valid test.\n")

    print(f"| {'seed-calibrated config':24} | {'PGR':>7} | {'→cloud':>7} | {'skill':>8} | {'cost saved':>10} |")
    print(f"| {'-'*24} | {'-'*7:>7} | {'-'*7:>7} | {'-'*8:>8} | {'-'*10:>10} |")
    for name, mode in (("threshold", "threshold"), ("classifier", "classifier")):
        try:
            result = calibrate(seed_samples, mode=mode)
            m = evaluate(name, router_from_result(result), rows)
            print(f"| seed {name:19} | {m.pgr:>7.3f} | {m.frac_cloud:>6.0%} | {_skill(m):>+8.3f} | "
                  f"{m.cost_savings:>9.0%} |")
        except CalibrationError as exc:
            print(f"| seed {name:19} | skipped: {exc}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    curve = "--curve" in argv
    paths = [a for a in argv if not a.startswith("--")]
    path = Path(paths[0]) if paths else Path(__file__).parent / "routerbench.jsonl"
    rows = load_dataset(path)
    # Warm the feature cache once over every prompt (the only expensive step).
    for r in rows:
        feats(r.prompt)
    print(f"set: {path.name} — {len(rows)} prompts ({len({r.prompt for r in rows})} unique)")

    if curve:
        learning_curve(rows)
    else:
        held_out_table(rows)
        per_domain(rows)
        seed_transfer(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
