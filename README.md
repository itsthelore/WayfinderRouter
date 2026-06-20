<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner-dark.png">
  <img alt="Wayfinder" src="docs/banner-light.png" width="640">
</picture>

<p><strong>Deterministic prompt-complexity routing — send each prompt to your
local or cloud model, offline, with no model call to decide.</strong></p>

<p>
  <a href="#quickstart">Quickstart</a> ·
  <a href="benchmarks/README.md">Benchmark</a> ·
  <a href="#how-it-compares">How it compares</a> ·
  <a href="EXPLAINER.md">Explainer</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p>
  <a href="https://pypi.org/project/wayfinder-router/"><img src="https://img.shields.io/pypi/v/wayfinder-router.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/wayfinder-router/"><img src="https://img.shields.io/pypi/pyversions/wayfinder-router.svg" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/wayfinder-router.svg" alt="License"></a>
  <a href="https://github.com/itsthelore/wayfinder-router/actions/workflows/ci.yml"><img src="https://github.com/itsthelore/wayfinder-router/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://mypy-lang.org/"><img src="https://img.shields.io/badge/types-Mypy-blue.svg" alt="Typed"></a>
</p>

</div>

<table align="center">
<tr>
<td align="center"><b>No model call</b><br>to decide the route</td>
<td align="center"><b>Deterministic</b><br>and fully offline</td>
</tr>
<tr>
<td align="center"><b>Calibrate</b><br>on your own data</td>
<td align="center"><b>Bring your own key</b><br>self-hosted</td>
</tr>
</table>

Wayfinder reads the shape of a prompt — its length, headings, lists, and code —
plus difficulty cues in the wording, like proofs, math, and hard constraints, and
tells you whether to send it to your small local model or your big cloud one. It
decides in microseconds, runs offline, and never calls another model to make the
call. No API key, no network, no heavy dependencies. You get a score and a
recommendation; what you do with it is up to you.

Cheap prompts stay local, hard ones go to the expensive model, and you stop paying
frontier prices for "summarize this" and "fix my typo."

## How it compares

Most routers decide by calling a model: a trained classifier, an LLM judge, or a
hosted API. That adds latency, cost, and a little randomness to the exact step
that is meant to save you money. Wayfinder reads structure and wording instead, so
the decision is free and the same every time.

| router | decides by | model call? | self-host | calibrate |
| --- | --- | :-: | :-: | :-: |
| **Wayfinder** | deterministic structural score | **no** | **yes** | **yes** |
| RouteLLM | trained classifier (preference data) | yes | yes | retrain |
| NotDiamond / Martian | learned, hosted | yes | no | via platform |
| OpenRouter (Auto) | hosted auto-router | yes | no | — |
| LiteLLM | provider proxy (not complexity-routed) | no | yes | n/a |

Wayfinder is not chasing a top accuracy number. It is the one router you can run
offline, with zero model calls, and tune on your own traffic. By default it scores
prompt *structure* only. It can also read lexical cues (proofs, math, constraints),
but those ship **off by default**: a [double-blind test](benchmarks/blind-eval.md)
on independently-authored prompts showed the lexical lift does *not* generalize (it
catches ~20% of unseen hard prompts and loses to a plain word-count baseline), so
they are opt-in — raise their weights only if you've calibrated them to your own
traffic's vocabulary. A prompt whose difficulty is purely semantic — a subtle code
snippet, an innocent-looking "what is the 100th prime number?" — has no structural
tell, and a semantic router will beat it there. The edge that survives the blind
test is the one to lead with: a deterministic, sub-millisecond, offline routing
decision with no model call. The [benchmark](benchmarks/README.md) (`make benchmark`)
shows where it wins and where it loses, against honest baselines and a perfect
oracle. Point it at RouterBench or RouterArena for graded numbers.

New here, or weighing it up? The [FAQ](docs/faq.md) gives straight answers —
including where it loses (it's no better than random on RouterBench's short-but-hard
items) and why you'd still run it.

## Try the demo (no keys)

See the routing decision for yourself — no API keys, no models, nothing on the network:

```bash
pip install "wayfinder-router[gateway]"
wayfinder-router serve --dry-run
# open http://127.0.0.1:8088/demo
```

A small chat UI that, for every message, shows where it routed (local vs cloud), the
complexity score and *why* (the feature breakdown), and the cost saved vs always-cloud —
with a live threshold slider. `--dry-run` makes the routing decision without calling a
model, so you can poke at it with zero setup.

## Quickstart

Put Wayfinder in front of your models. Your app keeps speaking the OpenAI API; you
just change one `base_url`.

1. Describe your two models in `wayfinder-router.toml`:

   ```toml
   [routing]
   threshold = 0.5            # below -> local, at/above -> cloud

   [gateway.models.local]
   base_url = "http://localhost:11434/v1"
   model = "llama3.2"

   [gateway.models.cloud]
   base_url = "https://api.openai.com/v1"
   model = "gpt-4o"
   api_key_env = "OPENAI_API_KEY"   # read from this env var, never stored
   ```

2. Run the gateway:

   ```bash
   pip install "wayfinder-router[gateway]"
   export OPENAI_API_KEY=sk-...
   wayfinder-router serve --port 8088
   ```

3. Point your existing client at it. No code change:

   ```python
   client = openai.OpenAI(base_url="http://localhost:8088/v1", api_key="unused")
   client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "..."}])
   ```

Easy prompts go local, hard ones go cloud, and every response carries
`x-wayfinder-router-model` and `x-wayfinder-router-score` so you can see where it
went. Want to steer one request? Pin it with `model="cloud"` / `prefer-local`, or
move the cut for a single call with an `X-Wayfinder-Threshold` header (see
[Steer a single request](#steer-a-single-request)).

Check it's working:

```bash
curl -s localhost:8088/healthz
# {"status":"ok","models":["cloud","local"]}

curl -s -D - -o /dev/null http://localhost:8088/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' \
  | grep -i x-wayfinder-router
# x-wayfinder-router-model: local
# x-wayfinder-router-score: 0.00
```

No backends yet? `wayfinder-router serve --dry-run` answers with the routing
decision instead of calling an upstream, so you can feel the routing in 30 seconds
before wiring up real models.

## Install

| command | what you get |
| --- | --- |
| `pip install "wayfinder-router[gateway]"` | the OpenAI-compatible routing gateway, the common case |
| `pip install wayfinder-router` | core only: scorer, CLI, and Python API, zero dependencies |
| `pip install "wayfinder-router[ui]"` | adds the local calibrate / explain / configure UI |
| `pip install "wayfinder-router[all]"` | gateway and UI together |

## How it works

Wayfinder sits behind whatever OpenAI-compatible client you already use. You point
that client's `base_url` at the gateway once, and from then on it is invisible. The
same client serves a request whether it routes local or hosted.

```text
  your client   (chat app, IDE, agent, or code)
       |
       v
  Wayfinder gateway   scores, picks a model
       |
       |-- low  -->  local    (Ollama, vLLM)
       |-- high -->  hosted   (OpenAI, any /v1)
       |
       v
  response returns via the same client,
  with x-wayfinder-router-* headers
```

A few things follow from this:

- **The interface in front is yours.** A chat GUI (Open WebUI, LibreChat), an IDE
  assistant with a custom endpoint (Cursor, Continue), an agent framework, or your
  own code on the OpenAI SDK. Want a chat window today? Put Open WebUI in front and
  point it at the gateway.
- **Local and hosted are backends, not apps.** The local model is just a server
  (Ollama, LM Studio, vLLM, llama.cpp) speaking OpenAI's `/v1`; the hosted one is
  the same shape. The user never switches UIs and usually never knows which model
  answered.
- **The score is computed, not a second opinion.** Asking a model how hard a
  prompt is would be slow, non-deterministic, and would cost a model call to decide
  whether to make a model call. Wayfinder scans the prompt instead — structure
  (length, headings, steps, links, code, tables) and difficulty cues in the wording
  (reasoning terms, math symbols, constraints) — into a `0.0`-`1.0` value and
  compares it to your threshold. Same prompt, same threshold, same answer. It is a
  proxy for difficulty, not a verdict, which is why the threshold is yours to tune.

Keys are read from the environment at request time and never touch the config file
or the scored path.

## Score a prompt from the CLI

```bash
echo "Summarise this paragraph in one sentence." | wayfinder-router route -
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

Add `--json` for machine consumers (an agent reads this and routes to its own
model):

```json
{
  "schema_version": "3",
  "score": 0.66,
  "recommendation": "cloud",
  "mode": "tiered",
  "features": { "word_count": 545, "heading_count": 12, "reasoning_term_count": 3, "...": 0 },
  "tiers": [{ "min_score": 0.0, "model": "local" }, { "min_score": 0.5, "model": "cloud" }]
}
```

## Configure routing

Wayfinder reads its own `wayfinder-router.toml`, found by walking up from where you
run it. There are three modes, in precedence order (classifier > tiers >
threshold); the scalar-score `weights` apply to any of them.

**Binary** (the default) is a single cut:

```toml
[routing]
threshold = 0.6
weights = { word_count = 4.0, list_item_count = 2.5 }
```

`--threshold N` overrides it for one run; `WAYFINDER_ROUTER_THRESHOLD` overrides it
from the environment.

To switch the lexical cues on, raise their `weights` and cut at the knee — the one
held-out improvement over the structural default on real frontier traffic (skill
−0.038 → +0.057, 61% cost saved on RouterBench). See
[`docs/lexical-routing.md`](docs/lexical-routing.md) and the ready-to-edit
[`examples/wayfinder-router.lexical.toml`](examples/wayfinder-router.lexical.toml);
recalibrate the threshold to your own traffic (a ~20-prompt bootstrap is only a smoke
test — see [`benchmarks/calibration-eval.md`](benchmarks/calibration-eval.md)).

**Tiered** routes ordered score bands to any number of models:

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

**Classifier** is a fitted multinomial-logistic model, `argmax` over per-model
linear scores. You usually generate it with `calibrate` rather than write it by
hand.

Each `[gateway.models.<name>]` block maps a routed name to an upstream `base_url`, a
`model`, and an optional `api_key_env` (the name of an environment variable, never
the secret itself). The gateway is the only part that touches keys or the network;
the scorer, config, and calibrator stay pure and offline.

## Calibrate on your data

The cut is a proxy, so tune it against your own traffic. `wayfinder-router
calibrate` reads a labeled JSONL dataset (`{"text": ..., "label": ...}`) and prints
a config fragment. It runs offline and never calls a model; the labels are your
ground truth.

```bash
wayfinder-router calibrate data.jsonl --mode threshold              # sweep the binary cut
wayfinder-router calibrate data.jsonl --mode tiers                  # ordinal multi-model
wayfinder-router calibrate data.jsonl --mode classifier --out wayfinder-router.toml
```

The fragment drops straight into `wayfinder-router.toml`; the accuracy and chosen
breakpoints print to stderr. The classifier is fit by deterministic L2-regularized
Newton/IRLS, pure Python, converging in a handful of iterations.

To pick a cut in cost terms instead of bare accuracy, use a cost-aware objective.
`--objective knee` chooses the cost-aware knee automatically (it maximizes
quality-recovered × cost-saved — no target to guess, and it can't collapse to
always-routing-to-the-expensive-model the way pure accuracy does on skewed labels);
`--objective cost-quality --target-savings X` instead holds a specific savings floor.
Add `--weights` to score with — and emit — custom feature weights, e.g. the lexical
opt-in, so the output is a complete, deployable config (see
[`docs/lexical-routing.md`](docs/lexical-routing.md)):

```bash
wayfinder-router calibrate data.jsonl --mode threshold --objective knee \
  --costs local=0.2,cloud=1.0 \
  --weights reasoning_term_count=5,math_symbol_count=3,constraint_term_count=1.5
```

Cost is metadata only — it shapes the calibrated cut and is reported on the
`/metrics` endpoint, but never enters a per-request decision, which stays
deterministic and free.

### Steer a single request

The deployment's config sets the default boundary, but a client can override the
decision for one request over plain OpenAI transport. An override only changes
where the request goes; the prompt is still scored, and nothing adds a model call.

- **The `model` field is a routing directive.** `auto` (or any normal model id)
  lets Wayfinder decide; a configured endpoint name (`local`, `cloud`) pins the
  request there; `prefer-local` / `prefer-hosted` pin to the low / high end of your
  router (`prefer-cloud` still works as an alias of `prefer-hosted`).
- **An `X-Wayfinder-Threshold` header re-cuts the decision** for that request, a
  number in `0.0`-`1.0` reusing your weights (binary routers only).

```python
# Pin one call to cloud regardless of score:
client.chat.completions.create(model="cloud", messages=[...])
# Or move the cut for one call (keep model="auto"):
client.chat.completions.create(
    model="auto", messages=[...], extra_headers={"X-Wayfinder-Threshold": "0.8"}
)
```

Each response adds `x-wayfinder-router-mode` (`scored` / `pinned` /
`threshold-override`) next to the `-model` and `-score` headers, so you can see
which channel decided the route.

## Drive it from a chat UI (no fork)

Because the `model` field is a routing directive, any OpenAI-compatible chat UI can
drive routing with no code change: the app's normal model dropdown becomes a
per-conversation routing picker (`auto` / `prefer-local` / `prefer-hosted` / a
pinned endpoint). The gateway lists these at `GET /v1/models`, so a UI discovers
them on its own.

- **LibreChat** — copy [`examples/librechat.yaml`](examples/librechat.yaml) and
  [`examples/docker-compose.override.yml`](examples/docker-compose.override.yml)
  into your checkout, run `docker compose up`, and pick the "Wayfinder" endpoint.
- **Open WebUI** — add an OpenAI connection pointing at the gateway; it
  auto-discovers the routing options.

See [`examples/`](examples/) for both. The one thing a stock UI can't express is a
live per-conversation threshold slider; that's what the `wayfinder-chat` fork adds,
and this no-fork path proves it out first.

## See where requests go

Wayfinder's controls are spread across the tools you already run, so it's easy not
to notice it working. Four surfaces show or steer routing:

| surface | what it shows | where |
| --- | --- | --- |
| Model dropdown | the routing picker (`auto` / `prefer-local` / `prefer-hosted` / a pinned endpoint) | your client, from `GET /v1/models` |
| Response headers | where each request went and why (`-model` / `-score` / `-mode` / `-request-id`) | every response |
| Debug body field | the decision inside the response body, opt-in | request header `X-Wayfinder-Debug: true` |
| Dashboard | recent decisions, per-model counts, scores — metadata only, never prompt text | `GET /router` (JSON at `/router/recent`) |

The dashboard is separate from the off-path `wayfinder-router ui` console, which is
for tuning, not production traffic.

## Learn from feedback

Don't guess the cut, learn it from your own judgment of local versus hosted output.
The loop is: collect judgments, calibrate, route automatically.

Bootstrap it with A/B onboarding. For each sample prompt, `wayfinder-router
onboard` runs both arms and asks which was good enough; the answer is a label:

```bash
wayfinder-router onboard prompts.jsonl --arms local,cloud --calibrate > wayfinder-router.toml
```

The comparison goes to stderr; `--calibrate` prints the resulting config to stdout.
Each judgment appends a `{"text", "label"}` line to a feedback log, which is itself
the `calibrate` dataset, so the log turns straight into a config.

Once you're routing automatically, keep it honest by recording which model was
actually good enough:

```bash
curl localhost:8088/v1/feedback -d '{"text": "...", "label": "cloud"}'
```

Then re-fit on a schedule from cron, a k8s CronJob, or a click in the UI.
Recalibration rewrites only the `[routing]` section and preserves your `[gateway]`
endpoints, and a running gateway hot-reloads the result with no restart:

```bash
wayfinder-router recalibrate                  # log -> calibrate -> write config
wayfinder-router recalibrate --min-labels 50  # no-op until you have enough signal
```

The judging runs models, so it lives in the gateway layer (with your key); the
scoring core stays untouched and the log carries no secrets.

## Deploy and integrate

The CLI, onboarding, and UI are for operators and bootstrapping. In production,
prompts flow through the gateway (transparent) or the library (in-process), so
routing happens where prompts already are.

Run the gateway as a service, sidecar or standalone:

```bash
docker build -t wayfinder-router . && docker run -p 8088:8088 -v "$PWD/data:/data" wayfinder-router
# or: docker compose up gateway   (see docker-compose.example.yml)
```

Point your existing client at it with no app change. Anything that speaks the
OpenAI API takes a `base_url`, including agent frameworks (LangChain, LlamaIndex),
IDE assistants with a custom endpoint (Cursor, Continue), and gateways like LiteLLM:

```python
client = openai.OpenAI(base_url="http://localhost:8088/v1", api_key="unused")
```

Wire feedback from wherever your users are. Your app, IDE, or chat shows a
thumbs-up or thumbs-down and posts the judgment; the next recalibration learns from
it:

```js
fetch("http://localhost:8088/v1/feedback", {
  method: "POST",
  body: JSON.stringify({ text: prompt, label: wasGoodEnough ? "local" : "cloud" }),
});
```

The gateway forwards asynchronously and streams: a request with `stream: true`
comes back as Server-Sent-Events, so chat clients render tokens as they arrive. An
upstream timeout or connection failure returns an OpenAI-shaped error instead of a
bare 500, every response carries a request id for tracing, and routing decisions
and reload failures are logged. The knobs:

| setting | effect |
| --- | --- |
| `WAYFINDER_ROUTER_TIMEOUT` / `serve --timeout` | upstream timeout in seconds (default 60) |
| `WAYFINDER_ROUTER_FEEDBACK_TOKEN` | when set, `/v1/feedback` requires `Authorization: Bearer <token>` |
| `serve --dry-run` | return routing decisions without calling any upstream |
| `GET /healthz` | reports `degraded` and lists `missing_keys` when a configured `api_key_env` is unset |
| `GET /router` | read-only dashboard of recent decisions, with `X-Wayfinder-Debug: true` surfacing one in the body |

## Explain and tune

To see why a prompt routed where it did, ask for the per-feature breakdown: each
feature's value, its normalized level, its weight, and its share of the score.

```bash
wayfinder-router route prompt.md --explain
```

For interactive tuning there's a local web UI:

- **Explain** — paste a prompt; see the score, the tier ladder, and contribution
  bars, and drag a threshold slider to watch routing change live.
- **Calibrate** — paste a labeled dataset, run a mode, and see accuracy, the sweep
  curve, and the resulting config fragment.
- **Configure** — edit `wayfinder-router.toml` with live validation and save.
- **Onboard** — A/B a local and a hosted model in the browser, judge each, and
  calibrate from the log (needs `[gateway]` for the model calls).

```bash
pip install "wayfinder-router[ui]"
wayfinder-router ui --port 8099    # then open http://localhost:8099
```

The UI is a thin wrapper over the same pure functions; it never calls a model, and
no secret appears in it.

## Python API

```python
from wayfinder_router import score_complexity, RoutingConfig, explain_score

result = score_complexity(prompt_text, config=RoutingConfig.binary(threshold=0.7))
print(result.recommendation, result.score, result.features)
for fc in explain_score(result.features, RoutingConfig().weights):
    print(fc.name, fc.contribution)
```

## Origin

Wayfinder started as a `route` experiment inside a larger requirements tool and was
split out because routing is a runtime concern, not a knowledge one: a prompt router
shouldn't make you install an engine you don't need. The result is a small,
dependency-free core that does one thing well.

## Repository layout

```
wayfinder-router/
  wayfinder_router/   the package: scorer, tiers + classifier, config loader/writer,
                      offline calibration (Newton/IRLS), explain, the feedback log and
                      onboarding harness, recalibration, CLI, and the optional gateway
                      and local UI (the impure layers, behind their extras)
  tests/              scorer, config, calibration, explain, feedback, onboard,
                      recalibrate, CLI, gateway, and UI coverage
  decisions/          design notes behind the tool's own choices
  docs/               the FAQ and the lexical-routing guide
  Dockerfile, docker-compose.example.yml   deploy the gateway as a service
```

## Test

```bash
pip install -e .[dev]   # or: pip install pytest
make test
```
