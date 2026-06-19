# Double-blind evaluation — why the lexical signals ship off by default

Reproduce with `python -m benchmarks.blind_eval`.

## Why this test exists

The canonical benchmark (`dataset.jsonl`) and the router were written by the same
person. That is a real bias: a prompt authored by someone who knows the scorer is
likelier to *contain* what the scorer rewards, so a signal can look stronger than it
is. To measure the bias, the **frozen** scorer — no peeking, no re-tuning after seeing
results — is evaluated against prompts written by an independent author given only a
scorer-blind brief: "easy" vs "hard" in human-difficulty terms, a plain/structured
form tag, and *no* hint of which words or structures score high.

`blind/openai-cross-provider.jsonl` is one such set (154 prompts, 60 easy / 94 hard),
authored by a different provider's model (OpenAI) from that brief. Labels are *by
construction* (easy → `{local:1, cloud:1}`, hard → `{local:0, cloud:1}`); that is the
acknowledged weak link, replaced by real graded labels via `routerbench_adapter.py`
once a RouterBench pull is reachable. By-construction labels still answer the one
question here: does a signal *separate* independently-authored hard prompts from easy
ones, or did it only ever separate the author's own?

## What it found about the lexical signals

v0.2.0 trialed lexical difficulty signals (WF-ADR-0016) — reasoning terms, math
symbols, constraint markers — to catch short-but-hard prompts that carry no structural
tell. On the author's own prompts they lifted the cost-aware knee from
**PGR 0.80 → 0.93**. With the lexical weights turned on and the scorer frozen against
the independent set:

| measure | value | reading |
| --- | --: | --- |
| hard prompts with **any** lexical signal | **20 / 94 ≈ 21%** | the curated vocabulary rarely appears in an independent author's hard prompts |
| opted-in lexical PGR at a realistic cut (t=0.10) | **0.16** | recovers little of the quality gap |
| length-only baseline PGR (word count ≥ 10) | **0.81** | a dumb length rule beats it |
| opted-in lexical margin over the length baseline | **−0.32 PGR** | the lexical signal *loses* to length |

The in-house blind set (independent agent author, same brief) agreed: a **+0.01** lift
over a structure-only control, a **−0.07** margin to the length baseline, and the same
**~20%** catch-rate. Two independent authors and a different provider concurred: the
lexical lift was an artifact of one person authoring both the router and the test
prompts. A curated keyword lexicon detects an *author's vocabulary*, not difficulty in
general.

## The decision: opt-in, off by default

The short-hard gap is real — structural scoring alone can't see it — but a keyword
lexicon doesn't close it in a way that generalizes, and turned on globally it *adds*
false positives (easy prompts that happen to use a listed word route to cloud). So the
lexical features ship **computed and reported, but at weight 0.0** — they do nothing
until a user who knows their own traffic's vocabulary raises the weights in their
routing config and calibrates. The default scorer is purely structural.

On the same independent set the structural default is honest about its own limit: at a
realistic cut (t=0.10) it routes 0% to cloud — a short prompt, however hard, has almost
no structural signal. That is the documented limit (see `results.md`): structural
scoring predicts *structural* heaviness — long, multi-step, formatted prompts — not
semantic difficulty. Wayfinder is for traffic where those correlate, calibrated on your
own labels.

## Real-label cross-check (RouterArena)

The blind sets above use *by-construction* labels. RouterArena's published
`cached_results/` give the stronger test: **real, externally graded** `score` and real
`inference_cost` per model per prompt, reachable offline. `benchmarks/routerarena_adapter.py`
joins two models on 809 shared prompts across 78 benchmark families (AIME, MMLU-Pro,
LiveCodeBench, PubMedQA, …), with a weak model as `local` (claude-3-haiku, 0.52) and a
stronger one as `cloud` (gemini-2.0-flash, 0.69 — a real 0.17 quality gap).

On those real labels, Wayfinder's structural router still does not separate hard from
easy: at its cost-aware knee it recovers **PGR 0.52**, *below* the plain length baseline
(**0.68**) and even a hair under stable-random (0.56). Same verdict as the by-construction
blind sets, now on real graded outcomes: structural scoring predicts structural heaviness,
not difficulty. (The three cached models are all small-tier and the cheapest is also the
most accurate, so the cost axis is degenerate here — for a true cheap-weak vs
strong-expensive frontier, see RouterBench below.)

## Real-label cross-check (RouterBench) — the frontier gap

RouterArena's cost axis was degenerate; RouterBench (Hu et al. 2024) is not.
`routerbench_adapter.py` reduces its 0-shot graded table (36,497 real prompts, 11 models)
to `mistralai/mistral-7b-chat` as `local` (score 0.31, $0.000046/call) vs
`gpt-4-1106-preview` as `cloud` (0.78, $0.003293/call) — a real **0.475 quality gap at a
~72× cost ratio**. The fair metric is **skill = PGR − frac_cloud** (a router routing
fraction *f* to cloud recovers PGR = *f* at random, so skill > 0 means it beats chance at
picking *which* prompts to escalate). Full numbers in `routerbench-results.md`.

| router | PGR | → cloud | skill | reading |
| --- | --: | --: | --: | --- |
| stable-random | 0.50 | 50% | +0.00 | the zero-skill line |
| length-threshold (knee) | 0.44 | 51% | −0.07 | a dumb length rule is at random |
| **wayfinder, structural (knee)** | **0.22** | **26%** | **−0.04** | the shipped default is *below* random |

The shipped structural router does **not** beat length or random on real frontier labels:
mean skill across the realistic operating band is **−0.049**. The cause is direct — across
the 86 task buckets, the correlation between a bucket's frontier gap and how often
Wayfinder escalates it is **−0.51**: the biggest wins (gap ≈ +0.7) are short
multiple-choice questions (mmlu-moral-scenarios, college-biology) it escalates 0% of the
time, while long, smaller-gap history passages draw 80%+. Structural heaviness mildly
*anti*-correlates with difficulty here. Same verdict as every blind set.

**The one surprise — the lexical opt-in helps here.** Turning the lexical signals on
(`OPTED_IN_WEIGHTS`) lifts mean skill from **−0.049 to +0.039** — from below-random to
above it, at *every* matched cloud fraction. This does not overturn the off-by-default
decision; it confirms its logic. RouterBench is math/reasoning/STEM-heavy, where the
frontier model's edge is largest *and* whose prompts naturally carry the lexicon's
vocabulary ("prove", "derive", math notation). The blind sets showed the lexicon fails on
independently-authored prose where hardness is *not* in those words; RouterBench shows it
helps when it *is*. Both are the same rule: **opt in and calibrate the lexical weights only
when your own traffic's difficulty lives in its vocabulary.** RouterBench is the favorable
case, and even there it recovers a fraction of the oracle.

## The edge that survives a blind test

The decision is pure-Python, sub-millisecond, offline, and deterministic — no model
call to decide. That is the axis to lead with, and the axis a learned or LLM-judge
router cannot match. For real graded accuracy/cost numbers, point the harness at
RouterBench / RouterArena (`routerbench_adapter.py`).
