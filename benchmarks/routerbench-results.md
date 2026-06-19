# RouterBench real-label results — does structural routing predict difficulty?

The honest, externally-graded verdict for Wayfinder's deterministic structural router,
run against **RouterBench** (Hu et al. 2024; Martian) — real prompts, real per-model
graded scores, real per-call dollar costs, with a genuine cheap-weak-vs-frontier gap.
Everything here is offline and deterministic: no model is called to make a routing
decision (WF-ADR-0001); the graded labels are the oracle.

Reproduce:

```bash
pip install pandas datasets           # to read the RouterBench pickles
# download routerbench_0shot.pkl from huggingface.co/datasets/withmartian/routerbench
python -m benchmarks.routerbench_adapter \
    --dataset data/routerbench_0shot.pkl --local mistral-7b --cloud gpt-4 \
    --out benchmarks/routerbench.jsonl
python -m benchmarks.run benchmarks/routerbench.jsonl          # headline table
python -m benchmarks.routerbench_lexical benchmarks/routerbench.jsonl   # skill + lexical opt-in
```

## Setup

**File.** RouterBench ships three pandas pickles. `routerbench_0shot.pkl` (36,497 rows)
is the canonical **0-shot** graded table and the primary set here — its prompts are real
user-length questions, the right input for a structural router. `routerbench_raw.pkl`
(1.2 GB, 401,467 rows) turned out to be the **5-shot** data in long form (one row per
sample × model); verified byte-for-byte equal to `routerbench_5shot.pkl` after pivoting,
and used below only as a cross-check. (5-shot prompts are few-shot-padded, which inflates
every structural signal — see the caveat at the end.)

**Model pair.** Of the 11 models, the cost-quality landscape is a real gradient (unlike
RouterArena's degenerate cached tier, where the cheapest model was also the best):

| role | model | mean score | mean cost / call |
| --- | --- | --: | --: |
| `local` (cheap-weak) | `mistralai/mistral-7b-chat` | 0.306 | $0.000046 |
| `cloud` (frontier) | `gpt-4-1106-preview` | 0.781 | $0.003293 |

A real **0.475 quality gap** at a **~72× cost ratio** — the cost axis is finally
meaningful. Scores are mostly binary correctness with ~21% fractional (partial credit);
the harness handles fractional labels.

**Metrics.** `quality` = mean graded score of the chosen model; `PGR` = performance gap
recovered (0 = always-local, 1 = always-cloud); `cost saved` is vs always-cloud; `decide
µs` is the per-prompt decision latency (pure Python, no model call). The cost-aware knee
maximises `PGR × cost_savings`.

**The fair metric — skill over random.** A router that sends fraction *f* of prompts to
cloud recovers, *if it chose at random*, exactly `PGR = f` in expectation. So a router's
real skill is **`skill = PGR − frac_cloud`**: positive only if its ranking of *which*
prompts need the frontier model beats chance. PGR alone flatters any router that simply
routes more to cloud, so skill is the number that answers the question.

## Headline (0-shot, 36,497 prompts)

> **In-sample caveat (knees tuned on the test set).** The threshold/knee rows below are
> selected on the *same* rows they report — an optimistic, in-sample estimate. A
> leakage-free re-run (calibrate on a train split, score on held-out test) is in
> [`calibration-eval.md`](calibration-eval.md): the structural knee's skill holds at
> **−0.038** held-out (the finding survives), and the lexical opt-in's skill holds at
> **+0.057** (so it was real, not leakage).

| router | quality | cost/call | → cloud | PGR | skill | cost saved | decide µs |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (upper bound, not a router) | 0.81 | 2.00e-03 | 59% | 1.07 | **+0.48** | 39% | ~0 |
| always-cloud (strong only) | 0.78 | 3.29e-03 | 100% | 1.00 | 0.00 | 0% | ~0 |
| always-local (weak only) | 0.31 | 4.57e-05 | 0% | 0.00 | 0.00 | 99% | ~0 |
| random (stable) | 0.54 | 1.67e-03 | 50% | 0.50 | +0.00 | 49% | ~0 |
| length-threshold (knee, ≥120 words) | 0.52 | 2.62e-03 | 51% | 0.44 | −0.07 | 20% | ~0 |
| **wayfinder (structural, knee t=0.14)** | 0.41 | 1.64e-03 | 26% | 0.22 | **−0.04** | 50% | 59.0 |
| wayfinder (structural, default 0.5) | 0.31 | 4.57e-05 | 0% | 0.00 | 0.00 | 99% | 58.8 |

**The question — does the structural router beat length and stable-random? No.** At its
cost-aware knee Wayfinder's structural router recovers PGR 0.22 at 26% cloud — a **skill
of −0.04**, *below* the zero-skill random line. The plain length baseline is no better
(skill −0.07). Both sit within a few points of random; neither extracts real signal. Only
the oracle has meaningful positive skill. Same verdict as every prior test (by-construction
blind sets, RouterArena) — now on a real frontier cost-quality gap.

This holds across the whole curve, not just the knee — mean skill over the realistic
operating band (frac_cloud ∈ [0.1, 0.7]) is **−0.049** for the structural router: it is
*consistently slightly worse than random* at picking which prompts to escalate.

## Why: structure is anti-correlated with the frontier gap

The mechanism is direct. Across the 86 task buckets, the n-weighted correlation between a
bucket's **frontier gap** (how much gpt-4 beats mistral-7b) and **how often Wayfinder
escalates it** is **−0.512**. Wayfinder routes to cloud the structurally heavy buckets
(long history passages, multi-part prompts) and keeps local the short ones — but the
short multiple-choice buckets are exactly where the frontier model's edge is largest:

| bucket | n | local | cloud | gap | wf → cloud |
| --- | --: | --: | --: | --: | --: |
| mmlu-moral-scenarios | 895 | 0.00 | 0.75 | +0.75 | **0%** |
| mmlu-college-biology | 144 | 0.25 | 0.94 | +0.69 | **0%** |
| mmlu-high-school-government | 193 | 0.28 | 0.97 | +0.69 | **0%** |
| mmlu-astronomy | 152 | 0.27 | 0.93 | +0.66 | **0%** |
| … (most high-gap buckets are short MC) | | | | | **0%** |
| mmlu-high-school-us-history | 204 | 0.45 | 0.96 | +0.51 | 86% |
| mmlu-high-school-world-history | 237 | 0.43 | 0.92 | +0.50 | 82% |
| grade-school-math | 7450 | 0.41 | 0.66 | +0.25 | 64% |
| abstract2title | 254 | 1.00 | 1.00 | +0.00 | 27% |

The biggest opportunities (gap ≈ +0.7, short questions) get 0% escalation; long buckets
with a *smaller* gap get 80%+; `abstract2title`, where both models already score 1.00, still
draws 27% of the cloud budget. Structural heaviness predicts structural heaviness, not
semantic difficulty — and on this set the two mildly anti-correlate.

## Step 5 — the lexical opt-in: a real-label surprise

The lexical signals (WF-ADR-0016: reasoning terms, math symbols, constraint markers) ship
**off by default** because by-construction blind tests showed a curated lexicon detects an
author's vocabulary, not difficulty (see `blind-eval.md`). Re-running with them turned on
(`OPTED_IN_WEIGHTS`: reasoning 5.0 / math 3.0 / constraint 1.5) **contradicts that on
RouterBench's real 0-shot labels** — modestly but consistently:

| config | knee | PGR | frac_cloud | skill | mean skill, frac∈[0.1,0.7] |
| --- | --- | --: | --: | --: | --: |
| wayfinder structural (default) | t=0.14 | 0.224 | 0.262 | −0.038 | **−0.049** |
| wayfinder lexical on (opt-in) | t=0.09 | 0.481 | 0.426 | +0.055 | **+0.039** |

The lexical opt-in flips the router from below-random to **above** random (+0.093 skill at
the knee), and it dominates the structural default at *every* matched cloud fraction
(0.10 → 0.70), so it is not a knee artifact. The reason is honest and specific: RouterBench
is dominated by math/reasoning/STEM benchmarks where the frontier model's edge is largest
*and* whose prompts naturally contain the lexicon's vocabulary (math notation, "prove",
"derive", "modulo"). That is exactly the case the lexicon is built for.

This does **not** overturn the off-by-default decision — it is the first real-label
evidence *for* the "opt-in, calibrate to your own traffic" framing. The blind tests showed
the lexicon fails on independently-authored prose where hardness is *not* expressed in
those words; RouterBench shows it helps when hardness *is*. Both are the same rule: enable
and calibrate the lexical weights only when your traffic's difficulty lives in its
vocabulary. It still recovers far less than the oracle, and it remains a curated lexicon,
not a difficulty model.

## 5-shot cross-check (the `routerbench_raw.pkl` labels)

| router | quality | → cloud | PGR | skill | cost saved |
| --- | --: | --: | --: | --: | --: |
| oracle | 0.84 | 42% | 1.11 | +0.53 | 56% |
| random (stable) | 0.65 | 50% | 0.50 | −0.00 | 49% |
| length-threshold (knee, ≥120w) | 0.79 | 96% | 0.95 | +0.02 | 3% |
| wayfinder (structural, knee t=0.27) | 0.70 | 58% | 0.64 | **+0.06** | 35% |
| wayfinder (lexical on, knee t=0.22) | 0.70 | 59% | 0.65 | +0.06 | 35% |

On 5-shot the structural router shows positive skill (+0.06) and the lexical opt-in adds
nothing — but this is a **confound, not a vindication**: 5-shot prompts are padded with
few-shot examples, so prompt length becomes a proxy for *task family* (each family gets a
fixed scaffold), and task family correlates with difficulty. The tell is that the length
baseline *degenerates* here (its knee routes 96% to cloud — essentially always-cloud, 3%
saved), because nearly every padded prompt clears any word cut. The structural router is
reading the scaffolding, not the question. On 0-shot — real user-length prompts — that
crutch is gone and the structural signal collapses to below-random.

## Reading

On real, externally-graded RouterBench labels with a true cheap-weak-vs-frontier gap,
Wayfinder's **shipped structural router does not beat a plain length threshold or
stable-random** — at its cost-aware knee it recovers PGR 0.22 with a *negative* skill
(−0.04), and across the whole curve it is consistently a hair worse than random, because
structural heaviness is mildly *anti*-correlated (−0.51 by bucket) with where the frontier
model actually helps: the high-gap wins are short multiple-choice questions it never
escalates. This confirms the documented limit on the labels that matter most. The one
real surprise is step 5: contrary to the by-construction blind tests, the **opt-in lexical
signals add genuine skill here** (−0.049 → +0.039 mean), because RouterBench's difficulty
is math/reasoning-heavy and the lexicon targets exactly that — strengthening, not
overturning, the "opt-in and calibrate to your traffic" design. The edge that survives
every test remains what it always was: a deterministic, offline, ~0.06 ms decision with no
model in the scored path — not semantic accuracy from structure alone.
