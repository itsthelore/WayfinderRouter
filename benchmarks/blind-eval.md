# Double-blind evaluation — does the lexical signal generalize?

Reproduce with `python -m benchmarks.blind_eval`.

## Why this test exists

The canonical benchmark (`dataset.jsonl`) and the router were written by the same
person. That is a real bias: a prompt authored by someone who knows the lexical
lexicon is more likely to *contain* the lexicon, so the lexical signals (WF-ADR-0016)
can look stronger than they are. To measure the bias, the **frozen** scorer — no
peeking, no re-tuning after seeing results — is evaluated against prompts written by
an independent author given only a scorer-blind brief: "easy" vs "hard" in
human-difficulty terms, a plain/structured form tag, and *no* hint of which words or
structures the scorer rewards.

`blind/openai-cross-provider.jsonl` is one such set (154 prompts, 60 easy / 94 hard),
authored by a different provider's model (OpenAI) from that brief. Labels are *by
construction* (easy → `{local:1, cloud:1}`, hard → `{local:0, cloud:1}`); that is the
acknowledged weak link, replaced by real graded labels via
`routerbench_adapter.py` once a RouterBench pull is reachable. By-construction labels
still answer the one question here: does the signal *separate* independently-authored
hard prompts from easy ones, or did it only ever separate the author's own?

## Result (cross-provider, frozen scorer)

| measure | value | reading |
| --- | --: | --- |
| hard prompts with **any** lexical signal | **20 / 94 ≈ 21%** | the curated vocabulary rarely appears in an independent author's hard prompts |
| lexical PGR at a realistic cut (t=0.10) | **0.16** | recovers little of the quality gap |
| length-only baseline PGR (word count ≥ 10) | **0.81** | a dumb length rule beats it on this set |
| lexical margin over the length baseline | **−0.32 PGR** | the structural/lexical signal *loses* to length |
| easy prompts wrongly routed cloud at the knee | **17 / 60 ≈ 28%** | structure read as difficulty (mostly easy-but-structured) |

The in-house blind set (independent agent author, same brief) gave the same shape:
lexical lift over a structure-only control of **+0.01 PGR**, a **−0.07** margin to the
length baseline, and a **~20%** catch-rate on hard prompts. Two independent authors
and a different provider agree.

## Honest conclusion

On the author's own prompts the lexical signals lift the cost-aware knee from
**PGR 0.80 → 0.93** (see `results.md`). That lift **does not generalize**: under
independent-author and cross-provider blind tests it collapses to a ~21% catch-rate
and loses to a word-count baseline. The headline lexical "win" was largely an artifact
of the same person authoring both the router and the test prompts.

What this does *not* retract: the lexical features are cheap, deterministic, and
harmless as one opt-in signal among several, and they remain useful on traffic that
actually uses that vocabulary (you calibrate on your own data — that is the product).
What it retracts is any claim that they make Wayfinder a good *semantic* difficulty
detector in general.

The edge that **does** survive a blind test is the one this router was built on:
the routing decision is pure-Python, sub-millisecond, offline, and deterministic —
no model call to decide. That is the axis to lead with, and the axis a learned or
LLM-judge router cannot match. For real graded accuracy/cost numbers, point the
harness at RouterBench / RouterArena (`routerbench_adapter.py`).
