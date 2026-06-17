# wayfinder

A deterministic prompt-complexity router. Hand it a prompt, get back a
reproducible structural complexity score and a recommendation:

> route this prompt to your **local** model, or to the **cloud** model?

It is a **standalone** tool. It calls no model, needs no API key, makes no
network request, and has **zero dependency on RAC** — it is pure text scanning
plus a threshold. The recommendation is a fact you act on; Wayfinder stops there,
and the caller runs inference.

## Why deterministic

The obvious way to route by complexity is to ask a model how complex the prompt
is — an LLM-as-judge router. That is non-deterministic, costs a model call to
decide whether to make a model call, and cannot be reproduced or tested.
Wayfinder takes the opposite stance: it scores *structure* — length, headings,
instruction steps, links, code blocks, tables — combines the signals into a
bounded `0.0–1.0` score, and compares that to a threshold you control. Same
prompt and same threshold always give the same answer.

The score is a **structural proxy**, not a verdict on difficulty: whether it
tracks "this prompt needs the cloud model" is your calibration, which is exactly
why the threshold is yours to set.

## Run it (offline, no install)

```bash
cd wayfinder
echo "Summarise this paragraph in one sentence." | python -m wayfinder.cli route -
make route PROMPT=path/to/prompt.md
```

```text
Recommended Model: local
Complexity Score: 0.00  (mode: tiered)

Tiers:
  >= 0.00  local <-
  >= 0.50  cloud

Contributing Features:
  Word Count: 6
  ...
```

JSON for machine consumers (an agent reads this and routes to its own model):

```bash
wayfinder route prompt.md --json
```

```json
{
  "schema_version": "2",
  "score": 0.66,
  "recommendation": "cloud",
  "mode": "tiered",
  "features": { "word_count": 545, "heading_count": 12, "...": 0 },
  "tiers": [{ "min_score": 0.0, "model": "local" }, { "min_score": 0.5, "model": "cloud" }]
}
```

## Install

```bash
pip install -e .              # the `wayfinder` command on PATH (zero dependencies)
pip install -e ".[gateway]"   # plus the OpenAI-compatible routing gateway
pip install -e ".[ui]"        # plus the local calibration/explain/configure UI
pip install -e ".[dev]"       # plus the test runner
```

## Configure routing

Wayfinder reads its **own** config — never RAC's `.rac/`. Drop a `wayfinder.toml`
anywhere at or above where you run it. Three modes, in precedence order
(classifier > tiers > threshold); `weights` (the scalar-score weights) apply to
any of them.

**Binary** (the default) — one cut:

```toml
[routing]
threshold = 0.6
weights = { word_count = 4.0, list_item_count = 2.5 }
```

`--threshold N` overrides it for one run; `WAYFINDER_THRESHOLD` overrides via the
environment.

**Tiered** (WF-ADR-0002) — ordered score bands route to any number of models:

```toml
[[routing.tiers]]
min_score = 0.0
model = "llama-3b"
[[routing.tiers]]
min_score = 0.3
model = "llama-70b"
[[routing.tiers]]
min_score = 0.6
model = "claude-cloud"
```

**Classifier** (WF-ADR-0003) — a fitted multinomial-logistic model; `argmax` over
per-model linear scores. Usually produced by `calibrate`, not hand-written.

## Calibrate from data

The cut is a *proxy*; calibrate it against your traffic. `wayfinder calibrate`
reads a labeled JSONL dataset (`{"text": ..., "label": ...}`) and emits a config
fragment — offline, deterministic, and it never calls a model (labels come from
your own oracle):

```bash
wayfinder calibrate data.jsonl --mode threshold              # sweep the binary cut
wayfinder calibrate data.jsonl --mode tiers                  # ordinal multi-model
wayfinder calibrate data.jsonl --mode classifier --out wayfinder.toml
```

The emitted fragment drops straight into `wayfinder.toml`; the summary (accuracy,
chosen breakpoints) is printed to stderr. The classifier is fit by deterministic
L2-regularized Newton/IRLS — pure Python, converging in a handful of iterations.

## Route with your own key (gateway)

To actually *route* — score the prompt, then call the chosen model with your own
key — run the OpenAI-compatible gateway (WF-ADR-0004). Your existing client points
its `base_url` at Wayfinder; no application code changes.

```toml
# wayfinder.toml — map each routed model name to an upstream + a key env var.
[routing]
threshold = 0.6

[gateway.models.local]
base_url = "http://localhost:11434/v1"
model = "llama3.2"

[gateway.models.cloud]
base_url = "https://api.example.com/v1"
model = "big-model"
api_key_env = "EXAMPLE_API_KEY"   # the *name* of the env var; the secret is never in this file
```

```bash
pip install -e ".[gateway]"
export EXAMPLE_API_KEY=...     # read at request time, only inside the gateway
wayfinder serve --port 8088
```

```python
import openai
client = openai.OpenAI(base_url="http://localhost:8088/v1", api_key="unused")
client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "..."}])
# Wayfinder scores the prompt, forwards to local or cloud, and returns the response.
# Response headers carry x-wayfinder-model and x-wayfinder-score.
```

The gateway is the **only** part that touches keys or the network; the scorer,
config, and calibrator stay pure, offline, and deterministic. Keys are read from
the environment at request time and never enter `wayfinder.toml` or the scored
path.

## Learn from feedback (onboarding)

Don't guess the cut — *learn* it from your own judgment of local vs hosted output
(WF-ADR-0006). The loop is: **collect judgments → calibrate → route automatically.**

**Bootstrap with A/B onboarding.** For each sample prompt, `wayfinder onboard` runs
both arms and asks which was good enough; the answer is a label:

```bash
wayfinder onboard prompts.jsonl --arms local,cloud --calibrate > wayfinder.toml
```

The A/B comparison and the prompt go to stderr; `--calibrate` prints the resulting
config to stdout. Each judgment appends a `{"text", "label"}` line to a feedback
log — which *is* the `calibrate` dataset, so the log turns straight into a config.

**Keep it honest with steady-state feedback.** Once routing automatically, record
which model was actually good enough; the label feeds the next recalibration:

```bash
curl localhost:8088/v1/feedback -d '{"text": "...", "label": "cloud"}'
```

The judging runs models, so it lives in the gateway/invocation layer (BYO key); the
deterministic core is untouched and the label log carries no secrets.

## Explain & tune

To see *why* a prompt routed where, ask for the per-feature breakdown — each
feature's value, its normalized level, its weight, and its share of the score:

```bash
wayfinder route prompt.md --explain
```

For interactive tuning there's a local web UI (WF-ADR-0005) with three tabs:

- **Explain** — paste a prompt; see the score, tier ladder, and contribution bars,
  and drag a threshold slider to watch routing change live.
- **Calibrate** — paste a labeled JSONL dataset; run a mode; see accuracy, the
  threshold-sweep curve, and the resulting config fragment, then send it to
  Configure.
- **Configure** — edit `wayfinder.toml` with live validation (the real loaders)
  and save.
- **Onboard** — A/B a local vs hosted model on sample prompts in the browser,
  judge each, record labels, then calibrate from the log (needs `[gateway]` too,
  for the model calls).

```bash
pip install -e ".[ui]"
wayfinder ui --port 8099    # then open http://localhost:8099
```

The UI is a thin consumer of the same pure functions; it never calls a model, and
no secret ever appears in it (a gateway model names an `api_key_env`; the key
lives in the environment).

## Python API

```python
from wayfinder import score_complexity, RoutingConfig, explain_score

result = score_complexity(prompt_text, config=RoutingConfig.binary(threshold=0.7))
print(result.recommendation, result.score, result.features)
for fc in explain_score(result.features, RoutingConfig().weights):
    print(fc.name, fc.contribution)
```

## Heritage

Wayfinder began as the `rac route` exploration inside
[requirements-as-code](https://github.com/itsthelore/requirements-as-code), and
its scoring shape is inspired by RAC's deterministic `classification.py`
(`points / ceiling`). It was split out because routing is a runtime *inference*
concern, divergent from RAC/Lore's recorded-knowledge product line — a prompt
router should not require installing a requirements-as-code engine. The shipped
tool shares no runtime code with RAC; see `decisions/WF-ADR-0001`.

## Repository layout

```
wayfinder/
  wayfinder/     the package: complexity scorer, tiers + classifier, own config
                 loader + writer, offline calibration (Newton/IRLS), explain, the
                 feedback log + onboarding harness, CLI, and the optional
                 OpenAI-compatible gateway and local UI (impure layers, behind
                 their extras)
  tests/         scorer, config, calibration, explain, feedback, onboard, CLI,
                 gateway, and UI coverage
  decisions/     ADRs grounding the tool's own choices (dogfooded)
```

## Test

```bash
pip install -e .[dev]   # or: pip install pytest
make test
```
