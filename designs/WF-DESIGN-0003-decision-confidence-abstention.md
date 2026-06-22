---
schema_version: 1
id: WF-DESIGN-0003
type: design
tags: [routing, confidence, abstention, gateway, tui, honesty]
---

# WF-DESIGN-0003: Decision Confidence & Abstention (deterministic, no model call)

## Status

Proposed

> Every routing decision should carry a **confidence** and a **reason**, computed offline
> with no model call, so the one honest weakness — a short, structurally-flat prompt that is
> nonetheless semantically hard — becomes an explicit, tunable signal instead of a silent,
> falsely-confident guess. Companion to the offline core (WF-ADR-0001) and the calibration
> loop (WF-DESIGN-0004), which can later fit the mapping.

## Context

The scorer maps structural features to a bounded `0.0–1.0` score and routes it against a
threshold (default `0.5`, `complexity.py`, WF-ADR-0001). Two regions are least reliable: turns
whose score sits **near the threshold**, and **very short / structurally-flat** prompts where
every feature is ~0 and the score is therefore ~0 regardless of how hard the prompt actually
is. Today all three of those — a confidently-easy prompt, a coin-flip-margin prompt, and a
"no structural signal at all" prompt — are reported with the same flat certainty.

The README is already candid that purely semantic difficulty ("what is the 100th prime
number?") has no structural tell, and a model-based router will win there. A model-based
router papers over its own uncertainty with a probability; Wayfinder can do **better and more
honestly** — surface *where structure is uninformative* — without spending a model call to do
it. This is the on-brand answer to the most common, most fair critique of the approach.

## User Need

An operator or user wants to (a) know *how much to trust* a given route, (b) set a policy for
the uncertain middle — escalate to cloud, stay local, or just flag it — and (c) see that
signal consistently wherever a decision appears: the gateway response, the terminal chat's
`/why`, the CLI, and the demo. It must stay deterministic, offline, and add no model call.

## Design

### A confidence and a reason, from features we already have

Combine two deterministic signals into a `0.0–1.0` confidence plus a categorical reason:

1. **Margin** — `|score − threshold|`, scaled by the local tier span, so a coin-flip-margin
   decision reads low and a clear one reads high.
2. **Signal sufficiency** — a penalty/flag when the total structural evidence is tiny (e.g.
   `word_count` below a small floor *and* no headings/lists/code/links): the score is ~0 not
   because the prompt is easy but because structure *cannot speak* to it.

The categorical reason is the honest part: `clear`, `near-threshold`, or
`low-signal (short / flat prompt)`. Confidence is derived purely from the existing feature
vector — no new inputs, no model call.

### Surfaces

- **Gateway**: response headers `x-wayfinder-router-confidence` (number) and
  `x-wayfinder-router-confidence-reason` (text), alongside the existing model/score headers.
- **Terminal chat**: `/why` and the decision line gain a confidence line, e.g.
  `confidence low · structure can't tell (short prompt)`.
- **CLI / JSON** (`route`, `--debug`) and the **demo** include the same fields. Decision-only
  paths (`--dry-run`, the static demo of WF-DESIGN-0002) show it too — it is pure scoring.

### Abstention policy

A config key and per-request override: `on_low_confidence = flag | escalate | keep-local`,
with a `--min-confidence` knob. Default is **`flag`** — annotate only, never silently change
the route — so the feature is additive and non-surprising. `escalate` is the
"when unsure, pay for the safe answer" policy; `keep-local` the "never escalate on a hunch"
one.

## Constraints

- **No model call; stdlib-only core** (WF-ADR-0001). Confidence is a pure function of the
  feature vector and the threshold.
- **Additive and backwards-compatible**: default routing behaviour is unchanged unless a
  policy is set; new headers are additive.
- Must hold for the binary and the multi-tier router (WF-ADR-0002/0003) — margin generalises
  to "distance to the nearest tier boundary".

## Rationale

Surfacing uncertainty is the honest, on-brand reply to semantic blindness: it does not pretend
structure sees everything; it says *when it can't*. A scalar plus a reason is more actionable
than a bare number, and a `flag`-by-default policy keeps the change safe. It also composes
with calibration (WF-DESIGN-0004), which can later fit the margin scaling and the sufficiency
floor to a user's own traffic.

## Alternatives

- **A local embedding/classifier for semantic confidence** — rejected as a default: it is a
  model call and breaks the core invariant. It belongs in the explicitly opt-in
  local-semantic tier idea, not here.
- **A calibrated logistic probability** — viable *after* WF-DESIGN-0004; this design is the
  deterministic, zero-calibration baseline that works out of the box.
- **Do nothing** — leaves the known, most-cited weakness silent and falsely confident.

## Accessibility

Confidence is always emitted as **text plus a reason**, never colour-only; the terminal line
reads literally (`confidence low · structure can't tell (short prompt)`) so it works in
no-colour terminals and screen readers. The header value is machine-readable.

## Open Questions

- Exact margin scaling and the short/flat thresholds — sensible defaults now, calibratable
  later (WF-DESIGN-0004).
- Header shape: a number plus reason (lean), versus buckets (high/med/low) — probably both.
- Whether `/btw` asides and forced routes (`/local`, `/cloud`) suppress the confidence line
  (forced routes have no "decision" to be unsure about).

## Success Measures

- On the benchmark, **low-confidence turns concentrate the routing errors** — confidence
  correlates with correctness. That is the proof it is meaningful, not decorative.
- A documented `on_low_confidence = escalate` example that measurably changes outcomes on a
  sample set (rescues a batch of structurally-flat-but-hard prompts).

## Related

WF-ADR-0001 (offline deterministic core), WF-ADR-0002/0003 (scored / multi-tier routing),
WF-DESIGN-0004 (calibration can fit the confidence mapping), WF-ROADMAP-0005.
