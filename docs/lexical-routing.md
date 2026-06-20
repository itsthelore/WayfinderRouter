# Lexical routing — the opt-in recipe that beats the structural default

Wayfinder's default scorer is **structural only** (length, headings, lists, code, tables).
On real frontier traffic that default does not beat stable-random: against RouterBench
(`mistralai/mistral-7b-chat` as `local` vs `gpt-4-1106-preview` as `cloud`) its cost-aware
knee recovers a *negative* skill — see [`../benchmarks/routerbench-results.md`](../benchmarks/routerbench-results.md).

The one deterministic, **held-out** improvement is to opt in to the lexical features
(reasoning / math / constraint vocabulary) and cut at the cost-aware knee. Measured
leakage-free (calibrate on train, score on held-out test;
[`../benchmarks/calibration-eval.md`](../benchmarks/calibration-eval.md)):

| router | skill (PGR − frac_cloud) | cost saved |
| --- | --: | --: |
| structural default (knee) | **−0.038** (loses to random) | 50% |
| **lexical opt-in (knee)** | **+0.057** (beats random) | 61% |

`skill` is the honest metric: a router that sends fraction *f* of prompts to cloud recovers
`PGR = f` by chance, so only `skill = PGR − frac_cloud > 0` means it ranks *which* prompts
need the strong model better than a coin flip.

## The recipe

Copy [`../examples/wayfinder-router.lexical.toml`](../examples/wayfinder-router.lexical.toml)
to `wayfinder-router.toml`:

```toml
[routing]
threshold = 0.09
weights = { reasoning_term_count = 5.0, math_symbol_count = 3.0, constraint_term_count = 1.5 }
```

Only the lexical weights are raised; the loader keeps the structural defaults, so this is
the shipped scorer with the lexicon switched on. Nothing else changes — the decision is
still pure, offline, sub-millisecond, with no model call (WF-ADR-0001).

## When it helps (and when it does not)

Lexical signals detect a **vocabulary**, not difficulty in general. They help only when your
traffic's hardness is *expressed in words the lexicon scans* — proofs, math notation,
multi-constraint instructions. RouterBench is math/reasoning-heavy, which is exactly that
case. On independently-authored prose where hardness is *not* in those words, the lexicon
fires on ~20% of hard prompts and loses to a length baseline
([`../benchmarks/blind-eval.md`](../benchmarks/blind-eval.md)) — which is why it ships **off
by default** (WF-ADR-0016). Turn it on only if your traffic looks like the former.

Two things the evidence is clear about:

- **`0.09` is RouterBench's knee, not a universal constant.** Recalibrate it to your traffic.
- **A ~20-prompt bootstrap is not enough** to find a stable cut: skill is noise-dominated
  until you have a few hundred labeled prompts (see the learning curve in
  `calibration-eval.md`). Treat 20 prompts as a smoke test, not a calibration.

## Recalibrate the threshold to your traffic

Label a representative sample of *your* prompts (`{"text": ..., "label": "local"|"cloud"}` —
the model each prompt should have gone to), then let the shipped calibrator place the cut:

```bash
wayfinder-router calibrate your-data.jsonl --mode threshold --out wayfinder-router.toml
```

Then add the lexical `weights` line from the recipe to the emitted `[routing]` block. To
bootstrap a labeled set interactively, start from the domain-tagged starter prompts in
[`../benchmarks/seed/domain-seed.jsonl`](../benchmarks/seed/domain-seed.jsonl) (science /
maths / general / code / commonsense) and judge each arm:

```bash
wayfinder-router onboard your-prompts.jsonl --arms local,cloud --calibrate > wayfinder-router.toml
```

Aim for a few hundred labels before trusting the cut; recalibrate as your traffic drifts
(`wayfinder-router recalibrate`).

## Verify it on your data

Point the benchmark harness at your labeled set to see the held-out skill for the structural
default vs the lexical opt-in before you deploy:

```bash
python -m benchmarks.routerbench_calibrate your-data.jsonl
```

It reports both, plus the random / length / oracle reference lines, all leakage-free.
