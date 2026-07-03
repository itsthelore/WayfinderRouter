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

Run on `routerbench_0shot.pkl` (36,497 graded prompts, mistral-7b-chat vs gpt-4-1106-preview).
The full table is in [`judge-validation-results.md`](judge-validation-results.md) (machine copy:
[`.json`](judge-validation-results.json)). Headline:

| | value |
| --- | --- |
| abstention | **99.3%** (36,239 / 36,497) — the judge decides only 258 prompts |
| κ, absolute gold (decided rows) | **0.048** — far below the 0.6 "substantial" floor |
| κ, relative gold (decided rows) | **0.038** |
| accuracy on decided rows | 0.562 absolute / 0.760 relative |

**Read honestly, this is a negative result, and a useful one: the standalone `HeuristicJudge`
does not clear its own trust gate on RouterBench.** That is exactly the outcome WF-ROADMAP-0010
Initiative 2 is designed for — automated verdicts are gated behind a human-gold κ floor and refuse
to render a flip verdict below it. This benchmark now *empirically* justifies that gate on a public
36k-prompt set, rather than assuming it. Three things drive the number, and each is a concrete
design input:

- **The 99.3% abstention is mostly a format mismatch, not wise caution.** RouterBench is dominated
  by multiple-choice families (MMLU, HellaSwag, WinoGrande, ARC — well over half the rows), whose
  graded "response" is a single token: gpt-4 answers `['C']`, mistral answers `['\nA']`. The
  judge's stub filter (`min_answer_chars = 16`) reads *both* terse answers as non-answers and
  abstains — this accounts for 27,369 of the abstentions (both responses under 16 chars). The
  judge was built for free-text sufficiency (`judge.py`: "strongest on verifiable/structured
  prompts and silent on open-ended prose"), and multiple-choice letters are neither the free text
  it judges nor the traffic the evidence engine will actually see (production is chat/code/prose).
  So the headline abstention over-states how often the judge would punt on real traffic.
- **Where it does decide, the `refusal` comparator misfires — and it drives the decisions.** Of
  258 decided rows, 235 come from the refusal rule, at 0.536 accuracy (near chance), biased hard
  toward "sufficient" (the judge says sufficient on 240 of 258). Root cause is the same format
  mismatch inverted: when the frontier arm's answer is *terse but correct* (`['C']`), the "dear
  arm empty → the cheap arm was enough" branch fires and wrongly rules the cheap arm sufficient.
  This is a specific, fixable bug: a short answer with no refusal marker is not a refusal. The
  `agreement` (4 rows, 1.00) and `similarity` (19 rows, 0.789) comparators are trustworthy but
  rarely decisive on this data.
- **The κ/accuracy gap is the class-imbalance signature.** Relative-gold accuracy is a respectable
  0.760 (and 0.916 on `consensus_summary`, a free-text family), but κ stays ~0.04 because a
  near-constant "sufficient" prediction earns little chance-corrected credit. Accuracy alone would
  flatter the judge; κ is the honest meter, which is why it is the gate.

This partly refutes the shape pre-registered above the fold (I predicted *high abstention +
high-κ-when-deciding*; reality is *high abstention + low-κ-when-deciding*, because the refusal rule
misfires on terse frontier answers). Leaving that prediction visible is the point.

**What it means for the roadmap.** (1) The human-gold + κ-floor gate (WF-ROADMAP-0010 §2) is
load-bearing and now empirically justified — do not let automated verdicts stand alone. (2) The
judge needs a format-aware fix before shadow-mode judging of real traffic: separate "empty/refused"
from "terse", so a short correct answer stops reading as a refusal (Initiative 6 tuning, tracked).
(3) An `LLMJudge` drop-in via the existing `Judge` protocol is the likely path for open-ended prose,
as `judge.py` predicted. None of this changes the decision path (WF-ADR-0001): the judge is
calibration/evidence-time only.

*Reproduce:* the RouterBench pickle is not redistributable in-repo; fetch it once (command above)
and re-run. Same pickle + same flags → byte-identical tables.
