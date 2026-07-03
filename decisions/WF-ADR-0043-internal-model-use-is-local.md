---
schema_version: 1
id: WF-ADR-0043
type: decision
tags: [judge, privacy, offline, air-gapped, local-first, governance, deterministic, invocation]
---

# WF-ADR-0043: Wayfinder's own model use, if any, is local and in-container

## Status

Accepted

## Category

Technical

## Context

The RouterBench judge-validation benchmark (WF-ROADMAP-0010 §6) showed the deterministic
`HeuristicJudge` cannot assess open-ended prose or catch shared-wrongness — a text comparator sees
whether two answers *agree*, not whether they are *correct*. The obvious temptation is to reach for
an LLM-backed judge, and `judge.py` even described one as a "planned drop-in." The same temptation
recurs elsewhere: WF-ROADMAP-0011's governance plane contemplates an off-path advisory content
scanner behind the same `Judge`-style seam.

Each time this comes up it is re-litigated from scratch, and each time it collides with the same
promises: the decision path makes no model call (WF-ADR-0001); the gateway runs offline and
air-gapped (WF-ADR-0039); and the governance-plane pitch is explicitly *"prompts never leave the
building, and it can prove what it did."* An LLM judge that calls an external provider would drag a
key, a network dependency, per-judgment cost, and — worst — **egress of the organization's prompts
and responses to a third-party model** into machinery that is supposed to have none of that. That
is off-brand for the router and self-refuting for the governance plane.

This ADR settles the question once, as a standing constraint every future feature inherits, so it
is not re-argued per initiative.

## Decision

1. **Proxied traffic is out of scope; this governs Wayfinder's *own* intelligence.** The data plane
   forwards a user's request to whatever upstream the user configured — which may be an external
   provider; routing traffic to cloud models is the product's job (WF-ADR-0004). This ADR is not
   about that. It governs the functions where **Wayfinder itself forms a judgment**: the routing
   decision, the sufficiency judge (WF-ADR-0037), evidence judging (WF-ROADMAP-0010), and any
   future advisory scanner (WF-ROADMAP-0011).

2. **The routing decision uses no model at all.** Restating WF-ADR-0001: the per-request decision is
   offline, deterministic, and keyless. Nothing below weakens this.

3. **Any Wayfinder-internal function that would use a model is deferred by default, and local-only
   if ever adopted.** The standing signal for sufficiency/quality is the deterministic heuristic
   judge plus a human-labelled gold sample; where the heuristic cannot tell, coverage is reported,
   not guessed. If a model-backed judge or scanner is ever added via the `Judge`-style seam, it
   **must run as a local model co-located in the Wayfinder deployment (the same container/host) —
   no external key, no network egress, no call to a third-party model provider.** A "private" hosted
   endpoint or a BYO-key cloud model does not qualify: from the organization's perspective that is
   still egress and still a key.

4. **Such components stay opt-in, labelled, and off the decision path.** Their default is *absent*.
   When present they run at calibration/evidence time or as an off-path advisory only — never in the
   blocking request path — and they are explicitly surfaced, never silent.

5. **Provenance is recorded.** A model-backed component stamps its version into the labels/records it
   produces, as `HeuristicJudge` already does (WF-ADR-0037), so a config or audit trail shows exactly
   what produced a judgment.

## Consequences

- **The offline / air-gapped guarantee (WF-ADR-0039) survives the growth of auxiliary intelligence.**
  An operator can run Wayfinder with no outbound path for its own functions, model-backed judge
  installed or not.
- **The governance-plane trust story holds** (WF-ROADMAP-0011): the organization's prompts never
  leave its infrastructure for Wayfinder to judge or scan them, so "nothing leaves the building"
  stays literally true.
- **New features inherit the rule** instead of re-deriving it; "add an LLM judge/scanner" now has a
  settled answer (deferred; local-only if ever).
- **Cost/'infra shape** is honest: a local judge model is an infrastructure resource the operator
  opts into and runs, not a hidden metered egress to a provider.
- **Limitation**: local models are heavier to run and may be weaker than a frontier judge; the
  standing deterministic-heuristic-plus-human-gold path is designed to make the model-backed judge
  optional, not necessary.

## Alternatives Considered

- **An external-provider LLM judge/scanner drop-in** (the `judge.py` "planned drop-in" as originally
  worded). Rejected: a key, network egress, and per-judgment cost in machinery meant to have none,
  and it sends the org's prompts to a third party to "protect"/grade them — fatal to WF-ADR-0039 and
  the governance-plane pitch.
- **Ban model-backed auxiliaries entirely.** Rejected as too absolute: a *local, in-container* model
  preserves every guarantee and may genuinely help open-ended-prose judging someday. The line is
  *local vs egress*, not *model vs no-model*, for these off-path functions.
- **A "private" cloud endpoint or BYO-key cloud judge.** Rejected: still egress and still a key from
  the organization's point of view; it fails the same test an external provider does.
- **Say nothing and decide per feature.** Rejected: that is what produced the re-litigation and the
  overreaching "planned drop-in" language this ADR corrects.

## Success Measures

- No function through which Wayfinder forms its own judgment calls an external model provider; a
  grep of `wayfinder_router` finds no external-model SDK imported for internal intelligence.
- If a model-backed judge or scanner ever ships, it runs in-container with no egress, is off by
  default, sits off the request path, and stamps its version into what it produces.
- With such a component installed, the WF-ADR-0039 offline/air-gapped guarantees still hold end to
  end (no outbound connection for Wayfinder's own functions).
- The routing decision remains model-call-free (WF-ADR-0001 CI guard stays green).

## Related

- WF-ADR-0001 (deterministic, offline, model-call-free decision — the invariant this extends to
  Wayfinder's auxiliary intelligence)
- WF-ADR-0004 (the gateway proxies user traffic to configured upstreams — the data plane this ADR
  explicitly does *not* govern)
- WF-ADR-0037 (the automated sufficiency judge and its `Judge` seam + version provenance)
- WF-ADR-0039 (offline-first / air-gapped delivery — the guarantee this preserves)
- WF-ROADMAP-0008 (PII detectors — deterministic regex, the first detector set)
- WF-ROADMAP-0010 (the evidence engine — formalizes its "no LLM-as-judge, local-only if ever" non-goal)
- WF-ROADMAP-0011 (the governance plane — its off-path advisory scanner inherits this constraint)
