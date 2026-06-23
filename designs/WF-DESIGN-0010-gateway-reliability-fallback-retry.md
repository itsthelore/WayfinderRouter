---
schema_version: 1
id: WF-DESIGN-0010
type: design
tags: [reliability, gateway, fallback, retry, circuit-breaker, invocation]
---

# WF-DESIGN-0010: Gateway Reliability — Fallback, Retry, Circuit Breaker

## Status

Proposed

> Make the gateway safe to depend on daily: when the chosen upstream times out, rate-limits, or
> 5xxs, transparently retry with backoff and fall back to an alternate, with a per-target cooldown
> and a success/failure circuit breaker. Crucially, this is an **invocation-layer** concern — it
> changes *delivery*, never the *scored decision* (WF-ADR-0001). No model call.

## Context

Wayfinder scores a prompt and forwards the request to one chosen tier. If that upstream errors,
the request fails — there is no failover today. Every adjacent gateway treats resilience as table
stakes: LiteLLM (ordered `fallbacks`, `num_retries`, `request_timeout`, `allowed_fails` +
`cooldown_time`, a production circuit breaker, and deterministic pre-call context-window checks),
OpenRouter (ordered model fallbacks), Cloudflare and Vercel (retries + provider failover). Daily
*production* reliance is impossible without this; it is a prerequisite, not a luxury.

The subtlety for Wayfinder: failover must not become a second, hidden router. The complexity score
and tier choice remain the deterministic decision; reliability logic only decides *what to do when
the chosen delivery fails*.

## User Need

An operator/agent builder wants requests to succeed through transient upstream failures — without
manual intervention, without the routing decision becoming non-deterministic, and without silently
ballooning cost.

## Design

### Retries (same target)

On a retryable transport error (timeout, connection error, HTTP 429, HTTP 5xx), retry the chosen
upstream up to a bounded count with exponential backoff + jitter. Non-retryable errors (4xx other
than 429) fail fast. Streaming: only retry before the first byte is forwarded; a mid-stream failure
is surfaced as a terminal SSE error (consistent with the existing upstream-error shape).

### Fallback (alternate target)

Each tier may declare ordered fallback candidates. When retries on the primary are exhausted, try
the next candidate in order. The fallback set is configuration, not a re-score — the decision that
"this prompt is tier T" stands; only the *endpoint serving tier T* changes.

A **failover policy** governs whether fallback may cross tiers (e.g. escalate to a pricier tier, or
degrade to the cheap/local one):

- `same-tier` (default): only fall back to alternate endpoints for the chosen tier.
- `degrade`: on exhaustion, fall back to the cheaper/local tier (keeps serving, never raises cost).
- `escalate`: on exhaustion, allow the next-stronger tier (raises cost — opt-in).

Because crossing tiers is cost- and trust-relevant, the policy is explicit and conservative by
default, and integrates with budgets (WF-ROADMAP-0006 item 6): a degrade-on-breach budget and a
degrade-on-failure policy are the same mechanism. This cross-tier policy likely warrants its own
ADR when built.

### Cooldown & circuit breaker

Track per-target success/failure counts. After N consecutive failures, open the breaker for that
target (CLOSED → OPEN), skip it for a cooldown window, then probe (HALF-OPEN) before closing.
Purely a function of observed transport outcomes — no model call, no scoring.

### Deterministic pre-call checks

Before dispatching, run cheap offline pre-flight checks (e.g. an estimated token/context-length
check against the target's known limit, mirroring LiteLLM's `enable_pre_call_checks`) and reroute
deterministically if the chosen target can't serve the request — avoiding a guaranteed failure.

### Surfaces

Configuration under `[gateway]` (retry/backoff bounds, fallback order, failover policy, breaker
thresholds). Reuse and extend the existing upstream-error metrics and decision logging so failovers
are observable (WF-DESIGN-0008); record which target ultimately served the request.

## Constraints

- **No model call; the scored decision is never recomputed** (WF-ADR-0001) — reliability acts only
  on transport outcomes.
- **Conservative by default:** `same-tier` failover; cross-tier `escalate` is opt-in because it
  raises cost; `degrade` never raises cost.
- **Honest accounting:** cost/savings (WF-DESIGN-0007) bill the target that actually served the
  request, including after fallback.
- **Self-hosted, deterministic** state (counts, breakers); resilient to its own backing store.

## Rationale

Resilience is the price of admission for daily production use, and it is squarely in the invocation
layer where Wayfinder is free to add value without touching the deterministic core. The
`degrade`-to-local policy is also distinctively Wayfinder: a router that already knows a cheaper
tier can keep serving through an outage where a single-target proxy would simply fail.

## Alternatives

- **Leave failover to an upstream proxy (LiteLLM in front/behind)** — pushes complexity onto users
  and concedes a table-stakes feature; a router that can't survive a provider hiccup won't be
  trusted in production.
- **Always escalate on failure** — simplest, but silently raises cost and can stampede a pricier
  provider; rejected as a default in favour of explicit policy.

## Accessibility

Configuration and behaviour are documented in plain text; failover events appear in logs/metrics
with machine-readable fields. No GUI required.

## Open Questions

- The default failover policy and whether cross-tier failover needs its own ADR (proposed: yes).
- Mid-stream failure handling for SSE (retry boundary at first byte vs buffering).
- Where breaker/cooldown state lives for multi-process deployments (in-memory vs shared store).
- Interaction with budgets (item 6) and rate limits (item 7) — shared "degrade to local" primitive.

## Success Measures

- A chaos test (kill the chosen upstream mid-request) yields graceful fallback with no dropped
  request and correct cost attribution to the serving target.
- Configurable, bounded retries/backoff demonstrably ride out transient 429/5xx bursts.
- The scored decision for a given prompt+threshold remains identical regardless of failover (the
  determinism invariant holds).

## Related

WF-ADR-0001 (deterministic core; decision never recomputed), WF-ADR-0004 (gateway/invocation layer),
WF-DESIGN-0007 (cost attribution to the serving target), WF-DESIGN-0008 (observability of failovers),
WF-ROADMAP-0006 (item 4; and items 6/7 for the shared degrade/limit primitives).
