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
[`.json`](judge-validation-results.json)). The benchmark surfaced a bug, we fixed it, and re-ran —
the whole point of building the meter first. Both readings are kept below.

### Initial run — `heuristic-1` (a useful negative result)

| | value |
| --- | --- |
| abstention | **99.3%** (36,239 / 36,497) — decides only 258 prompts |
| κ, absolute gold (decided) | **0.048** — far below the 0.6 floor |
| κ, relative gold (decided) | **0.038** |
| accuracy (decided) | 0.562 absolute / 0.760 relative |

The standalone judge did not clear its own trust gate — exactly the outcome WF-ROADMAP-0010 §2
gates against. Two mechanisms, both traced to one root cause: the judge treated *any* short
response as a non-answer.

- **The 99.3% abstention was mostly a format mismatch.** RouterBench is dominated by
  multiple-choice families (MMLU, HellaSwag, WinoGrande, ARC — over half the rows) whose graded
  "response" is a single token: gpt-4 answers `['C']`, mistral `['\nA']`. The stub filter
  (`min_answer_chars = 16`) read *both* terse answers as non-answers and abstained — 27,369 rows
  (both responses under 16 chars).
- **Where it decided, the `refusal` comparator misfired** — 235 of 258 decisions, at 0.536
  accuracy (near chance), biased to "sufficient": when the frontier answer was *terse but correct*
  (`['C']`), the "dear arm empty → cheap was enough" branch fired and wrongly ruled cheap
  sufficient. A short answer with no refusal marker is not a refusal.

(This refuted the shape pre-registered above the fold — I predicted *high abstention + high-κ-when-
deciding*; reality was *low-κ-when-deciding*, because of the misfire. The prediction is left visible.)

### After the fix — `heuristic-2`

The fix (this branch, `wayfinder_router/judge.py`): length is no longer a non-answer signal — only
emptiness or a refusal marker is — and fuzzy `similarity` is gated to answers ≥ `min_answer_chars`
(on short strings a one-token difference dominates the ratio). Version bumped `heuristic-1 →
heuristic-2` so provenance records which judge produced a label.

| | heuristic-1 | heuristic-2 |
| --- | --- | --- |
| decided | 258 | **2,811** (11× coverage) |
| abstention | 99.3% | 92.3% |
| `refusal` misfires | 235 @ 0.536 | **2** (eliminated) |
| relative-gold accuracy (decided) | 0.760 | **0.999** |
| relative-gold κ (decided) | 0.038 | **0.333** (none → fair) |
| absolute-gold κ (decided) | 0.048 | −0.001 |

Read honestly, the fix does exactly what it should — and exposes the judge's real boundary:

- **On the question that matters, it is now reliable where it decides.** The evidence engine asks
  the *relative* question — "would routing cheap have lost anything versus the frontier arm?" On
  that reading the fixed judge is near-perfect on decided rows (0.999 accuracy, 2,806/2,810
  correct), because agreement between the two arms almost tautologically means routing cheap lost
  nothing. Coverage is up 11× and the misfire is gone.
- **On the absolute question it remains near-useless — and that is a fundamental limit, not a
  regression.** Absolute-gold κ ≈ 0 both before and after (0.048 and −0.001 are both "no
  agreement"; the difference is noise). A text-comparison judge detects whether two answers
  *agree*, not whether they are *correct*: on 515 decided rows both arms agreed on the **same wrong
  answer**, and the judge cannot see it (it has essentially no "insufficient" prediction — 1 of
  2,811). This is the honest case for the human-gold gate and an `LLMJudge` drop-in, not something
  a heuristic can close.
- **κ is still below 0.6, even relative.** The near-constant "sufficient" prediction caps
  chance-corrected agreement under heavy class imbalance; 0.999 accuracy still only earns κ 0.333.
  κ is the honest meter, which is why it is the gate — accuracy alone would flatter the judge.
- **92.3% abstention is now correct conservatism, not a bug.** The remaining abstentions are
  open-ended prose families where text comparison genuinely cannot grade quality — the judge stays
  silent rather than guessing, bounding coverage, not honesty.

**What it means for the roadmap.** (1) The human-gold + κ-floor gate (WF-ROADMAP-0010 §2) is
load-bearing and now empirically justified on a public 36k set. (2) The `HeuristicJudge` is a
usable *relative-quality, verifiable-task* signal after the fix, and should be scoped to that — not
asked to assess correctness. (3) Open-ended prose and shared-wrongness are covered by the
**human-labelled gold sample**, not by reaching for an LLM: an LLM judge is deliberately deferred,
and if it is ever adopted it would run as a **local model inside the Wayfinder deployment** (no
external key, no egress) so the offline / "prompts never leave the building" guarantees hold. Where
the heuristic cannot tell, coverage is reported, not guessed. The decision path is untouched
throughout (WF-ADR-0001, evidence/calibration-time only).

*Reproduce:* the RouterBench pickle is not redistributable in-repo; fetch it once (command above)
and re-run. Same pickle + same flags → byte-identical tables.

## External validation — across pairs, a second dataset, and baselines

One pair on one dataset can flatter. `benchmarks/judge_external.py` widens the check to three
RouterBench model pairs (wide → narrow capability gap), a **second independent dataset**
(RouterArena, different authors, fetched over GitHub), and two **baseline judges** — so the κ has
something to be measured against ([`judge-external-results.md`](judge-external-results.md)):

| dataset | pair | judge | decided | abstain % | κ abs | κ rel |
| --- | --- | --- | --- | --- | --- | --- |
| RouterBench | mistral-7b vs gpt-4 | heuristic-2 | 2811 | 92.3% | −0.001 | **0.333** |
| RouterBench | mistral-7b vs gpt-4 | exact-match | 2790 | 92.4% | 0.000 | 1.000 |
| RouterBench | mistral-7b vs gpt-4 | always-sufficient | 36497 | 0.0% | 0.000 | 0.000 |
| RouterBench | llama-2-70b vs gpt-4 | heuristic-2 | 3565 | 90.2% | 0.004 | **0.400** |
| RouterBench | gpt-3.5-turbo vs gpt-4 | heuristic-2 | 12187 | 66.6% | 0.004 | **0.315** |
| RouterArena | claude-3-haiku vs gemini-2.0 | heuristic-2 | 16 | 98.0% | 0.000 | 0.000 |

Three findings, each of which tempers the single-pair result above:

- **Robust across RouterBench pairs, but only there.** Relative κ stays in the 0.31–0.40 "fair"
  band across all three pairs (and abstention falls as the gap narrows — two strong models agree
  more, so more rows are decidable). Absolute κ is ≈ 0 on every pair: the "can't judge correctness"
  limit is not a one-pair artifact.
- **It barely beats the trivial baseline.** `heuristic-2` decides 2811 rows where `exact-match`
  (the agreement rule *alone*) decides 2790 — nearly identical. Most of the judge's relative signal
  **is** exact agreement; the similarity/refusal comparators add little on this graded data.
  (`exact-match`'s κ_rel of 1.000 is a *degenerate* score — identical answers trivially satisfy
  "local ≥ cloud" — which is itself a caution that the relative gold rewards agreement-detection
  tautologically. `always-sufficient` sits at κ 0, the do-nothing floor `heuristic-2` clears.)
- **It does not transfer to RouterArena.** On an independent dataset of free-text answers, the
  heuristic decides just **16 of 809** rows (98% abstention) at κ 0 — essentially non-functional.
  Its whole mechanism (exact/near agreement) needs terse, comparable answers; RouterArena's prose
  gives it nothing to grab.

**Net, honestly:** the `HeuristicJudge` is a *narrow, dataset-sensitive* signal whose working part
is agreement on short answers. That is the strongest argument yet for WF-ROADMAP-0010 §2's design —
the automated judge is a coverage-extender behind a **human-labelled gold gate**, never a
standalone quality oracle — and for expecting near-total abstention on free-text production traffic
(honest, not harmful). It does not change the earlier per-pair reading; it bounds how far to trust
it.

*Reproduce:* `python -m benchmarks.judge_external --dataset data/routerbench_0shot.pkl` (RouterBench
pickle fetched once as above; RouterArena fetched from GitHub at run time).
