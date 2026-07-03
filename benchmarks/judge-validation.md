# Judge validation: does the HeuristicJudge agree with real grades?

The evidence engine proposed in WF-ROADMAP-0010 rests on one load-bearing assumption:
that `HeuristicJudge` (`wayfinder_router/judge.py`, WF-ADR-0037) — a deterministic
ensemble of text comparators that never sees a grade — agrees with *actual* answer
quality often enough to be worth trusting after the `sufficiency` gates. This benchmark
measures that assumption directly, before anything is built on it, in the same register
as `blind-eval.md`: whatever the number is, it gets published.

## Method

RouterBench (`withmartian/routerbench`) records, for every prompt and model, both the
**response text** and a **graded score** — so the judge can be replayed offline over
real (cheap, expensive) answer pairs and its verdicts compared against grades it never
saw. `benchmarks/judge_validation.py` does exactly that:

1. For each row, feed `(prompt, local_response, cloud_response)` to
   `HeuristicJudge.judge` → *sufficient* / *insufficient* / *abstain*.
2. Compare each **decided** verdict against two gold readings of "the cheap arm was
   good enough":
   - **absolute** — the local model's graded score clears a threshold (default 0.5):
     *the cheap answer was correct*;
   - **relative** — the local score ≥ the cloud score: *routing cheap lost nothing*
     (when both arms are wrong, the cheap arm was not the mistake).
3. Report, overall and per RouterBench eval family: Cohen's κ
   (`wayfinder_router.sufficiency.cohens_kappa`, floor 0.6 as the "substantial"
   reference line), accuracy, the confusion matrix, and — always alongside them —
   the **abstention rate**. Abstentions are never folded into either side; a judge
   that abstains often and decides well is useful, a judge that guesses is not.
4. Break results down **by comparator** (refusal / agreement / similarity), because the
   ensemble's rules have very different characters and a weak rule should be found,
   not averaged away.

The run is offline and deterministic: same pickle, same flags → byte-identical output.
The statistics themselves are golden-tested with planted fixtures whose κ is
hand-computable (`tests/test_judge_validation.py`) — the meter is validated before the
measurement is read.

## Running it

```sh
# once: download routerbench_0shot.pkl (36,497 graded prompts, 11 models)
# from huggingface.co/datasets/withmartian/routerbench into data/
python -m benchmarks.judge_validation \
    --dataset data/routerbench_0shot.pkl \
    --local mistral-7b --cloud gpt-4 \
    --out benchmarks/judge-validation-results.md \
    --out-json benchmarks/judge-validation-results.json
```

The model pair mirrors `routerbench-results.md` (mistral-7b as the local arm, gpt-4 as
the frontier arm) so the judge numbers sit next to the router numbers they would govern.
`--gold-threshold` adjusts the absolute reading; both golds are always reported.

## Results

**Pending the dataset.** The RouterBench pickles are not redistributable in this repo
and must be fetched once from the Hub (the harness environment this benchmark was built
in has no route to huggingface.co, so the first published table comes from the first
maintainer run of the command above). What ships today, deliberately in this order, is
the validated meter: the harness, its planted-fixture proofs, and this methodology.

What to look for when the table lands, stated in advance so the reading isn't fitted to
the result:

- **Overall κ vs the 0.6 floor** — the same bar `sufficiency.evaluate` holds human-gold
  agreement to. Below it, the evidence report's automated verdicts cannot be trusted
  standalone and WF-ROADMAP-0010's Initiative 2 leans harder on the human gold
  subsample; that is a design input, not a failure of the benchmark.
- **The abstention rate** — expected to be high (the judge is deliberately conservative;
  it decides on refusals, agreement, and near-agreement, and abstains on genuine
  divergence). High-abstention + high-κ-when-deciding is the *intended* shape: it bounds
  the evidence report's coverage, not its honesty.
- **Per-comparator accuracy** — if a single rule (e.g. `similarity`) is the weak one, it
  gets a tighter threshold or removal; the ensemble is tuned rule by rule, in public.
- **Per-family spread** — the judge should be strongest on verifiable/structured
  families and weakest on open-ended prose (the `judge.py` docstring's own prediction,
  now checkable).
