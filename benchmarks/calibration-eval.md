# Held-out calibration evaluation — can a labeled bootstrap (or per-domain lexicon) improve routing?

Reproduce:

```bash
python -m benchmarks.routerbench_calibrate benchmarks/routerbench.jsonl           # held-out table + per-domain + seed
python -m benchmarks.routerbench_calibrate benchmarks/routerbench.jsonl --curve   # learning curve
```

Everything is deterministic and offline (WF-ADR-0001): no model is called; the graded
RouterBench labels are the oracle. The fix this doc makes over `routerbench-results.md` is
**leakage-free evaluation** — calibrate on a train split, score once on a held-out test
split, threshold/knee selected on train only. (The published results swept the knee on the
same rows they reported, so they were in-sample/optimistic.) Splits are by a stable FNV-1a
hash of the *prompt* (duplicates can't straddle the split); see `benchmarks/split.py`.

The metric is **skill = PGR − frac_cloud**: a router that sends fraction *f* to cloud
recovers PGR = *f* by chance, so skill > 0 means it beats chance at choosing *which* prompts
to escalate. Pair: `mistralai/mistral-7b-chat` (local) vs `gpt-4-1106-preview` (cloud).

## 1. Held-out comparison (K=5 splits, 50/50; mean ± stdev)

| config | PGR | → cloud | **skill** | cost saved |
| --- | --: | --: | --: | --: |
| oracle (ceiling) | 1.070 | 59% | **+0.482**±0.002 | 39% |
| random (the zero line) | 0.502 | 50% | +0.003±0.003 | 49% |
| length-threshold (knee) | 1.000 | 100% | +0.000±0.000 | 0% |
| **wf structural (knee)** — *shipped default* | 0.225 | 26% | **−0.038**±0.003 | 50% |
| **wf lexical-on (knee)** — *opt-in weights* | 0.483 | 43% | **+0.057**±0.003 | 61% |
| calibrate threshold (accuracy) | 0.968 | 95% | +0.018±0.001 | 6% |
| calibrate cost-quality (save≈0.4) | 0.542 | 58% | −0.041±0.001 | 17% |
| calibrate classifier (11 feats) | 0.974 | 96% | +0.016±0.001 | 7% |

Readings, held-out and honest:

- **The shipped structural default still loses to random** (skill −0.038) — the in-sample
  finding survives the leakage fix. Calibrating it does not save it.
- **The accuracy-objective calibration collapses toward always-cloud.** Because the oracle
  label is cloud-skewed (gpt-4 wins ~70% of rows), the most *accurate* cut/classifier just
  routes 95–96% to cloud: high PGR (0.97), but only 6–7% cost saved and skill ≈ +0.017. It
  maximises the wrong thing for a cost router. The cost-quality objective, forced to save,
  drops *below* random (−0.041) — placing a savings-constrained cut on the structural score,
  which is anti-correlated with the frontier gap, is worse than chance.
- **The one real, deployable win is the lexical opt-in at the cost-aware knee: skill +0.057
  at 61% cost saved.** It beats random and the structural default by ~+0.095 skill, and
  crucially it **survives held-out** (in-sample it was +0.039 — held-out it is *higher*, so
  it was not leakage). It recovers ~12% of the oracle's skill headroom. This is the
  deterministic, replicable improvement the evidence supports.

The takeaway: the lever that helps is the **lexical features** (math/reasoning vocabulary),
deployed with the **knee objective** (balance quality and cost) — not the accuracy-objective
calibration, which degenerates to always-cloud on skewed labels.

## 2. Learning curve — is ~20 prompts enough?

Calibrate on the first N of a stable-shuffled train pool; score on a fixed 30% test split;
K=5 splits.

| N | threshold skill | classifier skill |
| --: | --: | --: |
| 10 | +0.007 ± 0.030 | +0.016 ± 0.042 |
| 20 | +0.015 ± 0.029 | **−0.010** ± 0.013 |
| 50 | +0.003 ± 0.010 | +0.029 ± 0.040 |
| 100 | +0.005 ± 0.007 | +0.010 ± 0.006 |
| 500 | +0.009 ± 0.007 | +0.014 ± 0.001 |
| 2000 | +0.013 ± 0.004 | +0.015 ± 0.001 |

**No — ~20 prompts is not enough.** At N ≤ 50 the standard deviation across splits is as
large as (or larger than) the mean, and the sign is unstable (the classifier is *negative*
at N=20). Skill only settles — to a small **+0.013–0.015** — at N in the hundreds to
thousands. (Labels here are ~40% local, so 20 random prompts usually contain both classes;
the limiter is variance, not one-class failures.) A 20-prompt bootstrap cannot reliably
measure, let alone capture, a +0.01–0.06 effect.

## 3. Per-domain — does a science / maths / general split help?

One global classifier (fit on the whole train split, sliced per domain on test) vs a config
fit on each domain independently (own leakage-free split). RouterBench buckets grouped by
`domain_of` (coverage printed by the driver).

| domain | n test | global skill | per-domain skill | Δ (per-domain − global) |
| --- | --: | --: | --: | --: |
| general | 3157 | +0.046 | **+0.132** | **+0.085** |
| humanities | 1697 | −0.003 | +0.029 | +0.031 |
| multilingual | 405 | +0.265 | +0.267 | +0.003 |
| science | 1828 | +0.002 | +0.004 | +0.001 |
| commonsense | 6824 | +0.000 | +0.000 | −0.000 |
| code | 214 | +0.000 | −0.018 | −0.018 |
| math | 4154 | +0.000 | **−0.052** | **−0.052** |

**Mixed — not a clear win.** Per-domain calibration helps *general* (+0.085) and *humanities*
(+0.031) but *hurts* math (−0.052) and code (−0.018), and does nothing for the rest; the
mean Δ is ≈ 0. Two honest mechanisms: (a) the global classifier itself routes ~96% to cloud,
so its per-domain skill is mostly ~0 (always-cloud has zero skill by construction); (b)
within math/code every prompt already carries the structural/lexical signature, so a
within-domain cut can't discriminate and a smaller per-domain train set adds variance. The
standout is **multilingual** (Chinese tasks): skill +0.265 either way — those prompts are
structurally/charset-distinct and gpt-4's edge on them is large and *predictable*, the rare
case where structure genuinely tracks difficulty.

## 4. Hand-authored seed bootstrap (cross-distribution)

A 36-prompt domain-tagged seed (`benchmarks/seed/domain-seed.jsonl`, by-construction
easy→local / hard→cloud), calibrated and tested on all of RouterBench (the seed is disjoint,
so no leakage):

| seed-calibrated | PGR | → cloud | skill | cost saved |
| --- | --: | --: | --: | --: |
| threshold | 0.997 | 99% | +0.005 | 1% |
| classifier | 0.985 | 97% | +0.016 | 7% |

A user's own ~36 prompts, calibrated with the accuracy objective, transfer to the same
**collapse-to-always-cloud** behaviour (97–99% cloud, ~1–7% saved, skill ≈ +0.01). The
bootstrap doesn't transfer a useful cost-quality operating point — same lesson as §1.

## Reading

On real RouterBench labels, **calibrating the shipped router on a labeled bootstrap does not
meaningfully improve cost-aware routing**, and **~20 prompts is far too few** to even measure
the effect (variance swamps a ~+0.01 signal until N reaches the hundreds). The accuracy
objective collapses to always-cloud on skewed labels; the cost-quality objective on the
structural score drops below random. **Per-domain specialization is mixed** — it helps
general/humanities, hurts math/code, nets ~zero.

The one deterministic, replicable, held-out improvement over the shipped structural default
is **opting into the lexical features at the cost-aware knee** (skill −0.038 → +0.057, 61%
cost saved) — and a user can deploy exactly that today via `wayfinder-router.toml`
(`[routing] weights = {…}` + a threshold near the knee). That is consistent with the
project's standing position: the lexical signals are real *when difficulty lives in the
vocabulary* (RouterBench is math/reasoning-heavy), so they ship off-by-default and are opt-in
and calibrated to your traffic. The edge that needs no bootstrap remains the deterministic,
offline, sub-millisecond decision.
