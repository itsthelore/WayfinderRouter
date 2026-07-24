# Wayfinder benchmark archive

> The executable benchmark harness was retired with the Rust-only cutover
> (WF-ADR-0046). The datasets and reports below are preserved as historical
> migration evidence; their old commands are not current product instructions.

A small, **deterministic, offline** evaluation of prompt-complexity routers, with
metrics aligned to the routing literature ([RouteLLM](https://www.lmsys.org/blog/2024-07-01-routellm/),
[RouterArena](https://arxiv.org/abs/2510.00202), [RouterBench](https://arxiv.org/pdf/2403.12031)).
It needs no network and no API keys — anyone can reproduce it (WF-ADR-0015).

```bash
python -m benchmarks.run            # or: make benchmark
python -m benchmarks.run mydata.jsonl
```

Committed sample output: [`results.md`](results.md).

## What it measures

A router is a pure function `prompt -> "local" | "cloud"` — the whole interface these
benchmarks evaluate. Each dataset row carries **per-model correctness labels** (did the
weak/`local` and strong/`cloud` model get this prompt right?) and a difficulty tag; the
labels are the oracle, so no model is called to produce the numbers.

| metric | meaning |
| --- | --- |
| **quality** | mean correctness of the chosen model (the strong model is the ceiling) |
| **cost** | mean relative cost of the chosen model (`local` 0.2, `cloud` 1.0) |
| **→ cloud** | call fraction to the strong model |
| **PGR** | performance gap recovered: `(quality − local_only) / (cloud_only − local_only)`; `0` = always-local, `1` = always-cloud ([RouteLLM's metric](https://www.lmsys.org/blog/2024-07-01-routellm/)) |
| **cost saved** | versus always-cloud |
| **decide µs** | per-prompt decision latency — for a structural router this is a text scan, no model call (machine-dependent) |

A single threshold is one point on a router's **cost-quality curve**; the report sweeps
the threshold to show the whole curve, and picks the cost-aware *knee* (maximising
`PGR × cost_savings`) as the headline operating point.

## How to read the sample results (honestly)

On the shipped 24-prompt illustrative set:

- **The short-but-hard prompts are the floor.** Six `hard-short` prompts (e.g.
  "Prove √2 is irrational") score ~0 structurally — *indistinguishable from
  `easy-short`* — so **no threshold can route them to cloud** without sending the easy
  ones too. Structural routing cannot recover them; this is the documented limitation.
- **Structure is recovered.** Wayfinder routes every `*-structured` prompt (including
  short ones with code/tables) to cloud, recovering ~60% of the quality gap at ~37% cost
  savings, deciding in tens of microseconds.
- **A tuned length baseline is competitive — and on this mix slightly ahead.** Raw word
  count captures most of what structure does here (several `hard-short` prompts are long
  enough in words to trip a low cut). Wayfinder's structural features add value
  specifically on *short-but-structured* prompts; on short-prose-heavy traffic, length
  alone does much of the job. **This is a small illustrative set — not a general claim.**
- **Nothing matches the oracle.** That gap is the price of deciding offline without
  reading the answer.

The point of the harness is honest, reproducible methodology you run on **your** traffic
(or a real public set), not a leaderboard win. Wayfinder's edge is *where* it sits in the
trade space: deterministic, offline, **zero model-call** to decide, calibratable to your
data — not "highest PGR on someone else's mix".

## Using a real dataset

The shipped set is illustrative. For general numbers, point the harness at a public set
with per-model labels — [RouterBench](https://arxiv.org/pdf/2403.12031) or
[RouterArena](https://github.com/RouteWorks/RouterArena) — converted to one JSON object
per line:

```json
{"prompt": "...", "difficulty": "...", "label": {"local": 0, "cloud": 1}}
```

`difficulty` is free-form (used only for the per-bucket breakdown); `label` is the
ground-truth correctness of each model.

The same graded table also validates the **sufficiency judge** (WF-ADR-0037): because
RouterBench stores each model's *response text* next to its grade, `HeuristicJudge` can
be replayed over real answer pairs and scored against grades it never saw — κ, accuracy,
abstention rate, per-comparator and per-family. Method and how to run:
[`judge-validation.md`](judge-validation.md) (`python -m benchmarks.judge_validation`).

## Validating the secret/PII detectors

A separate benchmark scores the deterministic detectors the governance-plane policy
engine will gate on (WF-ROADMAP-0011): a reference detector set is measured for
precision/recall/F1 on a labeled, adversarial corpus, so each detector's numbers decide
which policy verb it can safely drive (a distinctive-token detector can `block`; a
low-precision one must not). Method and how to run:
[`detector-validation.md`](detector-validation.md)
(`python -m benchmarks.detector_validation`).

## Adding a router (including the ones we can't run here)

A router is one function; add it to `routers.py` and to the table in `run.py`. The
**learned/hosted** routers below need a model call or API access, so they are not run in
this offline harness — implement an adapter and run it on the same dataset to compare
apples-to-apples:

```python
def routellm_adapter(prompt: str) -> str:        # requires the RouteLLM checkpoint + inference
    return "cloud" if my_routellm.route(prompt) == "strong" else "local"
```

## How Wayfinder compares to other routers

Qualitative, with each tool's **own published** numbers and their provenance. **These
cross-tool numbers come from different datasets and model pairs and are NOT directly
comparable** — the only apples-to-apples comparison is within this harness on one dataset.

| router | decides by | model call to decide? | offline / self-host | calibrate on your data | published result (provenance) |
| --- | --- | --- | --- | --- | --- |
| **Wayfinder** | deterministic structural score | **no** (~tens of µs) | **yes** | **yes** (`calibrate`) | this harness, illustrative set: PGR 0.60 at 37% cost savings |
| [RouteLLM](https://www.lmsys.org/blog/2024-07-01-routellm/) (LMSYS) | trained classifier on preference data | yes (router inference) | yes (open weights) | retrain | ~95% of GPT-4 quality at **45–85% fewer GPT-4 calls** on MT-Bench/MMLU/GSM8K ([LMSYS, 2024](https://www.lmsys.org/blog/2024-07-01-routellm/)) |
| NotDiamond | learned, hosted | yes (API) | no | via platform | vendor-reported |
| Martian | learned, hosted | yes (API) | no | — | commercial |
| OpenRouter (Auto) | NotDiamond-powered | yes (API) | no | — | hosted SaaS ([docs](https://openrouter.ai/blog/insights/model-routing/)) |
| [LiteLLM](https://github.com/BerriAI/litellm) | config / rules (not complexity) | no | yes | n/a | a multi-provider proxy, not a complexity router |
| semantic-router (Aurelio) | embedding similarity to routes | yes (embed) | yes | define routes | task routing, not local-vs-cloud complexity |

**The differentiator** is the combination, not any single metric: Wayfinder is the only
one of these that decides **offline, deterministically, with no model call**, and that you
**calibrate on your own data** — which is exactly why its decision latency is microseconds
rather than a model inference, and why its results are reproducible byte-for-byte.

## Sources

- [RouteLLM: An Open-Source Framework for Cost-Effective LLM Routing (LMSYS, 2024)](https://www.lmsys.org/blog/2024-07-01-routellm/)
- [RouterArena: An Open Platform for Comprehensive Comparison of LLM Routers (arXiv 2510.00202)](https://arxiv.org/abs/2510.00202)
- [RouterBench: A Benchmark for Multi-LLM Routing (arXiv 2403.12031)](https://arxiv.org/pdf/2403.12031)
