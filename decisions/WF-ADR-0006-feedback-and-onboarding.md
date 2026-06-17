---
schema_version: 1
id: WF-ADR-0006
type: decision
tags: [feedback, onboarding, calibration, invocation]
---

# WF-ADR-0006: Feedback Loop and Onboarding

## Status

Accepted

## Category

Architecture

## Context

A potential user asked, in effect: let me say whether the local model's output was
good enough, and if not try the hosted one — and once we learn what's best for a
kind of task, route that way automatically.

That is the keystone the product was missing. Calibration (WF-ADR-0003) always
needed *labeled* data — prompts tagged with which model was good enough — but
Wayfinder never provided a way to produce it; it only named the oracle (tests,
production signals, a judge). The user's request *is* that oracle: a human judgment
of local-vs-hosted, turned into labels, fed to `calibrate`, closing the loop
**collect judgments → calibrate → route automatically**.

The judging requires running models, so it belongs in the invocation layer
(WF-ADR-0004), not the deterministic core.

## Decision

Add a feedback loop that records local-vs-hosted judgments as labels and feeds the
existing calibration, with two collection modes and the core untouched.

- **The label log is the calibrate dataset.** Each judgment is a
  `{"text", "label"}` JSON line appended to a log — exactly the format
  `load_dataset` / `wayfinder calibrate` consume. So feedback turns into a routing
  config with **no new calibration logic**, and recalibration is a deterministic
  batch replay of the whole log (the direction WF-ADR-0003 recorded).
- **A/B onboarding (bounded, clean labels).** `wayfinder onboard` runs each sample
  prompt through two arms (a local and a hosted model), shows both outputs, and
  the user judges which was good enough; the chosen arm is the label. Running both
  costs 2× briefly but yields the strongest label — the user sees the counterfactual.
- **Steady-state escalate (cheap, ongoing).** The gateway exposes `/v1/feedback`
  `{text, label}`: after a routed result, the caller records which model was
  actually good enough. This keeps the config honest as traffic drifts.
- **The harness is injectable; the core is pure.** `run_onboarding` takes the
  model-call and the judgment as callables, so the loop is tested with fakes — no
  model, no terminal. The model invoker (`invoke_model`) and the gateway endpoint
  live in the invocation layer behind the `wayfinder[gateway]` extra; nothing in
  `complexity`/`config`/`calibrate` changes, and no model is called in the core.
- **Keys stay out.** Invocation reads a bring-your-own key from the environment
  (via the gateway model's `api_key_env`); no secret is in `wayfinder.toml`, the
  label log, or the scored path.

## Consequences

### Positive

- The product becomes self-improving: a few judgments bootstrap a trustworthy
  config, ongoing feedback maintains it, and routing is then automatic and free.
- Reuses everything: the label log feeds the unchanged `calibrate`, which emits a
  config through `dump_routing_toml` and routes through the existing tiers/classifier.
- The determinism and testability of the core are preserved; the model-running,
  non-deterministic part is isolated and faked in tests.

### Negative

- A/B onboarding doubles inference cost during the bounded onboarding window
  (accepted for label quality; steady-state escalate avoids it).
- Human "good enough?" judgments are subjective — fine for a user's *own* routing
  config; where a deterministic oracle exists (tests pass, exact match) it is a
  stronger label and the harness accepts any label source.

### Risks

- Label drift or a biased sample skews the fit. Mitigation: recalibration is a
  transparent batch replay of an inspectable log; re-running it is reproducible.

## Alternatives Considered

### Escalate-on-reject only (no A/B)

Run local, ask good-enough, run hosted only on rejection.

#### Disadvantages

- Cheaper but the label is weaker: when local is "fine" you never learn whether
  hosted was notably better. Kept as the *steady-state* mode, not the bootstrap.

### Bake judgment/feedback into the core

Have the scorer collect or act on feedback.

#### Disadvantages

- Puts model calls and mutable state in the deterministic, golden-tested path —
  the WF-ADR-0001/0004 line. Feedback is an invocation-layer concern.

### A continuous online learner (streaming SGD)

Update the config on every judgment.

#### Disadvantages

- Not reproducible (order-dependent). Batch replay over the log preserves the
  determinism guarantee.

## Forward direction (recorded)

- A **UI "Onboard" tab** over the same harness — paste prompts, A/B in the browser,
  judge with a click, then calibrate from the log — is **built** (extends
  WF-ADR-0005): it runs the arms through the gateway invoker and records via the
  pure feedback functions.
- **Scheduled recalibration** (not built): re-run `calibrate` over the log on a
  cadence and swap the config in, with a generation marker.

## Success Measures

- A handful of `wayfinder onboard` judgments produce a log that `calibrate` turns
  into a config which then routes those prompts the judged way.
- `/v1/feedback` appends a label that a later `calibrate` consumes unchanged.
- No feedback or onboarding path imports a model SDK into the core or writes a key
  to disk.

## Related

- Builds on WF-ADR-0003 (calibration) and WF-ADR-0004 (invocation/gateway);
  the UI tab extends WF-ADR-0005
