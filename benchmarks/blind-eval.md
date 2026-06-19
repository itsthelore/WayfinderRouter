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
strong-expensive frontier, run RouterBench via `routerbench_adapter.py`.)

## The edge that survives a blind test

The decision is pure-Python, sub-millisecond, offline, and deterministic — no model
call to decide. That is the axis to lead with, and the axis a learned or LLM-judge
router cannot match. For real graded accuracy/cost numbers, point the harness at
RouterBench / RouterArena (`routerbench_adapter.py`).
