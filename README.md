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
echo "Summarise this paragraph in one sentence." | python -m wayfinder.cli -
make route PROMPT=path/to/prompt.md
```

```text
Recommended Model: LOCAL
Complexity Score: 0.00  (threshold 0.50)

Contributing Features:
  Word Count: 6
  Heading Count: 0
  Max Heading Depth: 0
  List Item Count: 0
  Link Count: 0
  Code Block Count: 0
  Table Row Count: 0
```

JSON for machine consumers (an agent reads this and routes to its own model):

```bash
python -m wayfinder.cli prompt.md --json
```

```json
{
  "schema_version": "1",
  "score": 0.66,
  "recommendation": "cloud",
  "threshold": 0.5,
  "features": { "word_count": 545, "heading_count": 12, "...": 0 }
}
```

## Install

```bash
pip install -e .            # the `wayfinder` command on PATH
pip install -e ".[dev]"     # plus pytest
```

## Configure the cut

Wayfinder reads its **own** config — never RAC's `.rac/`. Drop a `wayfinder.toml`
anywhere at or above where you run it:

```toml
[routing]
threshold = 0.6
weights = { word_count = 4.0, list_item_count = 2.5 }
```

`--threshold N` overrides it for one run; `WAYFINDER_THRESHOLD` overrides it via
the environment.

## Python API

```python
from wayfinder import score_complexity, RoutingConfig

result = score_complexity(prompt_text, config=RoutingConfig(threshold=0.7))
print(result.recommendation, result.score, result.features)
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
  wayfinder/     the package: complexity scorer, own config loader, CLI
  tests/         scorer, config, and CLI coverage
  decisions/     ADRs grounding the tool's own choices (dogfooded)
```

## Test

```bash
pip install -e .[dev]   # or: pip install pytest
make test
```
