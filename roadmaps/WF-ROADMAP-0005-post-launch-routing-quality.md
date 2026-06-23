---
schema_version: 1
id: WF-ROADMAP-0005
type: roadmap
tags: [post-launch, routing, confidence, calibration, demo, gateway, hardening]
---

# Roadmap: Post-launch — routing honesty, the calibration moat, and production readiness

## Status

Planned

## Context

The Show HN / launch shipped the product as *visible and runnable*: the default-install
terminal chat (WF-ADR-0029), the decision-first `/why`, the cost tally, forced routing, and a
deterministic, offline, zero-model-call router (WF-ADR-0001). The launch also surfaces the
predictable critique — a short, structurally-flat prompt that is semantically hard has no
structural tell, so the router can miss it — and a wave of first-time deployers.

This roadmap answers both **without compromising the core invariant** (no model call to route;
stdlib-only deterministic scorer). It turns the honest weakness into an explicit, tunable
signal; deepens the one real moat (calibrate on *your* traffic, measured); rides the launch
with a zero-install demo; and hardens the gateway for people who now want to run it. Every
initiative is additive — the base wheel and the offline core are never touched — and each
ships independently so the cheap, timely wins land first.

## Outcomes

- The router **says when it is unsure** instead of guessing with flat confidence — the
  credible reply to "it misses hard short prompts".
- "Tune it on your own traffic" becomes a **single, cross-validated command** with an honest
  lift report and drift detection — the moat made real and trustworthy.
- A **link-and-go demo** that costs ~$0 and survives a front-page spike, live during the
  launch window.
- The gateway is **safe to deploy**: it fails over, retries, caches, authenticates, and is
  observable.

## Initiatives

Sequenced by timeliness and leverage; each is independent.

1. **Hosted zero-install demo — execute WF-DESIGN-0002 (now).** Port the deterministic scorer
   to the browser (JS port gated by a golden parity corpus in CI; Pyodide as fallback), wire
   it to the existing `decide()` seam in `demo.html`, and deploy static files to a CDN. Most
   of the design work is done; this is execution, and it is most valuable *while the launch is
   hot*. ~1–2 days, low risk (decision-only, no keys/cost).

2. **Decision confidence & abstention — WF-DESIGN-0003.** Emit a per-decision confidence and
   reason (threshold margin + signal-sufficiency), surfaced in the gateway headers, `/why`,
   the CLI, and the demo, plus an `on_low_confidence = flag | escalate | keep-local` policy.
   Deterministic, no model call, additive. The small, high-leverage feature that turns the
   main critique into a feature. ~2–3 days, low risk.

3. **One-command calibration loop — WF-DESIGN-0004.** `wayfinder calibrate --from logs.jsonl`
   → fitted config + a k-fold cross-validated lift report against honest baselines and the
   oracle, recommending the lexical cues only when they generalise out-of-fold; plus a `drift`
   check. The flagship differentiator. ~1 week, medium risk (label-acquisition UX).

4. **Gateway hardening (epic, parallel).** Failover (model unreachable → escalate, logged),
   retries + per-model circuit breaker, an optional response cache (hash of
   messages+model+params, never across keys/users), bearer-token auth + simple rate limit, and
   richer metrics/tracing (extend `/metrics`; optional OpenTelemetry). Splits into 3–4
   independently-shippable PRs; two decisions warrant their own ADRs — **the failover policy**
   (when may we escalate *against* the routing decision?) and **cache correctness** (isolation
   across keys/users). ~1–2 weeks total.

## Success Measures

- **Confidence is meaningful**: on the benchmark, low-confidence turns concentrate the routing
  errors (confidence correlates with correctness).
- **Calibration is honest**: a worked repo example shows measurable lift on sample logs (or
  honestly shows none), cross-validated; the lexical-on recommendation fires only when it
  generalises.
- **Demo survives the spike**: the network-cut parity test passes (offline → identical
  decisions); it serves the launch at ~$0.
- **Gateway is resilient**: a chaos test (kill the local backend mid-stream) yields graceful
  escalation with no dropped request.

## Assumptions

- The launch produces real feedback that should *re-order* these — comments vote on priority.
- The deterministic, offline, no-model-call core remains the identity; any semantic-signal
  work stays explicitly opt-in (off by default), held to the same blind-eval honesty.
- Deployers want self-hosted resilience, not a hosted control plane.

## Risks

- **Brand erosion**: a "semantic confidence" feature that quietly adds a model call would break
  the core promise — kept out of the default path by design.
- **Calibration label quality**: garbage labels yield confident-but-wrong fits; mitigated by
  cross-validation, honest baselines, and a good log→label converter.
- **Scope creep on hardening**: the gateway epic could sprawl; mitigated by shipping
  failover + metrics first and treating cache/auth as separate, optional PRs.

## Related Decisions

- WF-ADR-0001 (deterministic, offline, no-model-call core — the invariant every initiative
  preserves)
- WF-ADR-0002 / WF-ADR-0003 (scored / multi-tier routing — what confidence and calibration
  build on)
- WF-ADR-0004 (the OpenAI-compatible gateway — what Initiative 4 hardens)
- WF-DESIGN-0002 (static serverless demo — Initiative 1 executes it)
- WF-DESIGN-0003 (decision confidence & abstention — Initiative 2)
- WF-DESIGN-0004 (one-command calibration loop — Initiative 3)
- WF-DESIGN-0005 (structural feature audit — measures which features earn their default
  weight; feeds the calibration defaults and the honest README framing)
- WF-DESIGN-0006 (friendlier, safer key experience — resolve keys from a keychain / password
  manager and a plain-English status check; a companion to Initiative 4's gateway hardening)
