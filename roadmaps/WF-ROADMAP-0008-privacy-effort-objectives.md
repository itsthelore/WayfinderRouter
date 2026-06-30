---
schema_version: 1
id: WF-ROADMAP-0008
type: roadmap
tags: [routing, privacy, pii, reasoning-effort, objectives, calibration, offline, deterministic]
---

# Roadmap: privacy-, effort-, and objective-aware routing (three deterministic features, on Wayfinder's terms)

## Status

Proposed

## Context

The vLLM Semantic Router (SR) comparison surfaced three capabilities worth adopting — but SR reaches
them with ML classifiers and an Envoy control plane. Wayfinder can reach the *useful* subset of each
**deterministically**, reusing machinery it already has, without giving up the offline, model-call-free
decision core (WF-ADR-0001):

- SR forces sensitive prompts to a safe tier with a classifier. Wayfinder can do the high-value part —
  **route prompts containing secrets/PII to the local/private tier** — with a deterministic regex pass.
- SR is beginning to vary *reasoning effort* per request. Wayfinder can make **reasoning effort a
  routing output** by attaching it to the score-banded tier and injecting it at delivery time.
- SR routes by *intent/objective*. Wayfinder's `calibrate` already optimises for cost objectives
  (`knee`, `cost-quality`); this makes the **chosen objective first-class and recorded**, and adds a
  latency objective.

All three are **additive** and land in layers Wayfinder already owns. The per-request routing decision
stays offline, deterministic, and keyless throughout (WF-ADR-0001). This roadmap maps the achievable
slice and the seams; the ADRs and implementation follow when the work is scheduled (ship 1 → 2 → 3,
each its own CalVer point release).

## Outcomes

- A prompt that contains a detectable secret or PII is **forced to the local/private tier**, regardless
  of its complexity score — "your secrets never leave the machine," computed offline.
- A route can carry a **reasoning-effort level** (think/no-think, budget) that is a deterministic
  function of the score-band and injected into the upstream request body — the decision is unchanged.
- A shipped config **records what it was optimised for** (cost | latency | accuracy), so the choice is
  inspectable and survives recalibration; a new `latency` objective joins the existing cost objectives.

## Initiatives

### 1. Deterministic privacy / PII-aware routing — *priority*

A deterministic regex/heuristic pass detects secrets and PII (emails, SSNs, API-key shapes, cards, …);
when any fires, the prompt is forced to the cheapest/local tier. This is a **security gate**, so —
unlike the lexical signals (WF-ADR-0016), which ship off by default — it ships **on by default**.

- **Patterns on `Lexicon`** (`wayfinder_router/complexity.py`, `Lexicon` dataclass): a new
  `pii_patterns` field with built-in defaults, **overridable per deployment** (a shop adds its internal
  token format). `Lexicon` is already the user-tunable trigger seam (WF-ADR-0019) and is frozen/hashable.
- **Detection rides the existing fence loop** in `extract_features` (`complexity.py:282`), which already
  tracks `in_fence` so code-block contents don't masquerade as structure. *(Open design call: do we scan
  **inside** code fences for PII? Leaked keys often live in code blocks — lean yes, even though
  structural scanning skips fences.)*
- **A gate, not a weight, in `score_complexity`.** Keep the true structural score (honest and
  explainable) but override `recommendation` to the first/cheapest tier and set a new
  `ComplexityScore.forced_by = "pii"`. Do **not** overload `mode` (the documented `"tiered" | "classifier"`
  contract); bump `to_dict` `schema_version` 3 → 4, emitting `forced_by` only when set.
- **Gateway surfaces it** (`wayfinder_router/gateway.py`): add a response header `X-Wayfinder-Forced: pii`
  and carry `forced_by` in the dry-run `{wayfinder: {…}}` debug payload, so a client shows "routed local —
  secret detected" rather than a misleading low score. The recommendation already drives delivery via the
  existing `degrade`/offline-first primitive (WF-ADR-0039) — no new delivery logic.

### 2. Reasoning-effort as a routing output

Because tiers are score-bands, attaching an effort level to a tier makes effort a **deterministic
function of the score** — a genuine routing output with no new decision machinery.

- **`Tier` gains an optional effort** (`complexity.py`, `Tier` dataclass): `reasoning_effort: str | None`,
  additive, defaults to no-op. The score selects the tier offline, so the effort is score-driven and the
  decision stays offline.
- **Provider-neutral injection at delivery.** OpenAI (`reasoning_effort: low|medium|high`) and Anthropic
  (`thinking: {type, budget_tokens}`) disagree on schema, so the router must not hard-code either. Add
  `upstream_params: dict | None` to `GatewayModel` (`gateway.py`): an arbitrary per-endpoint request-body
  passthrough; the tier's `reasoning_effort`, when set, populates it for the matched arm.
- **Inject at *both* `forward_body` sites** — non-stream (`gateway.py:1604`) and stream
  (`gateway.py:2198`). Applying the merge at only one silently diverges streaming from non-streaming. This
  is the easy-to-miss bug. *(Open design call: on a body-vs-`upstream_params` key collision, does the
  caller's request body or the config win? Decide and document.)*
- Delivery-layer only; the scored decision path is untouched (WF-ADR-0001 holds trivially).

### 3. Routing objective made explicit (cost | latency | accuracy)

Mostly surfacing what `calibrate` already computes (`calibrate.py`, `calibrate_threshold` already supports
`objective` = `accuracy` | `knee` | `cost-quality` with per-arm `costs`/`target_savings`, WF-ADR-0017).

- **Record the objective in the shipped config.** Add `objective` and an optional `objective_metadata`
  to `RoutingConfig` (`complexity.py`) as **metadata only** — never enters the per-request decision.
  Round-trip it in the TOML loader/dumper and preserve it through `recalibrate()` (`recalibrate.py`), so a
  re-cut keeps the same objective. The dashboard/menu-bar can then label "calibrated for: cost (target 30%)".
- **Add a `latency` objective** to the sweep — the one genuinely new bit: per-arm latency numbers
  (config-provided, mirroring how `costs` are supplied) and a `--target-latency`. Reuses the existing
  `sweep_curve` scaffolding.
- **CLI:** expose `--objective {accuracy,knee,cost-quality,latency}` (+ `--target-latency`) on the
  existing `calibrate` command; no new command.

## Non-goals (explicit, for this roadmap)

- **An ML classifier on semantic intent** (SR's core). Wayfinder's signals stay deterministic
  keyword/regex scans; an opt-in `ClassifierModel` mode already exists for those who want a fitted model,
  and that — not these three features — is the thing that would match SR's classifier (tracked separately).
- **A "privacy" *calibration* objective.** Privacy is Initiative 1 (the PII gate) plus offline-first
  (WF-ADR-0039), not a degenerate always-local sweep. Objectives stay cost | latency | accuracy.
- **A guarantee that PII detection is complete.** Regex detection is recall-imperfect; it is a
  deterministic guardrail, documented honestly in the spirit of `benchmarks/blind-eval.md`, not a promise.
- **Any change to the offline decision contract.** No feature adds a model call to the routing decision;
  Feature 2 is delivery-only, Features 1 and 3 are pure scoring/calibration.

## Success Measures

- A prompt with a detectable secret routes local with `forced_by="pii"` and an `X-Wayfinder-Forced: pii`
  header; a prompt with none is scored and routed byte-identically to today (regression guard).
- A configured `Tier.reasoning_effort` (or `GatewayModel.upstream_params`) is injected identically on the
  streaming and non-streaming paths; an unset config forwards byte-identical request bodies.
- `calibrate --objective {…}` records the objective in the emitted config, it survives `recalibrate`, and
  the `latency` objective honours `--target-latency`.
- The standing WF-ADR-0001 CI guards stay green — the decision is still offline and model-call-free.

## Related Decisions

- WF-ADR-0001 (deterministic, offline, no-model-call core — preserved by all three)
- WF-ADR-0004 (invocation layer + OpenAI-compatible gateway — where effort is injected)
- WF-ADR-0016 (lexical difficulty signals — the opt-in precedent; PII differs: it's a security gate, on by default)
- WF-ADR-0017 (cost-aware routing and calibration — the objective machinery Initiative 3 surfaces)
- WF-ADR-0019 (the trigger lexicon is configuration, not code — where `pii_patterns` lives)
- WF-ADR-0039 (offline-first delivery — the `degrade` primitive the PII gate reuses to land local)
- New ADRs to be authored when the work is scheduled: PII gate, reasoning-effort injection, recorded objective.
