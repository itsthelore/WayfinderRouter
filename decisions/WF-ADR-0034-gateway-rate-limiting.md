---
schema_version: 1
id: WF-ADR-0034
type: decision
tags: [gateway, rate-limit, reliability, throughput, invocation]
---

# WF-ADR-0034: Gateway Rate Limiting (RPM/TPM, fixed-window, invocation-layer)

## Status

Accepted

## Category

Technical

## Context

Budgets cap *cost* (WF-ADR-0032) and the response cache dedupes *repeats* (WF-ADR-0033). The
third production guardrail is capping *volume*: a runaway client, a retry storm, or a misbehaving
agent can flood an upstream — exhausting its rate limits, spiking latency for everyone, and
enlarging the blast radius of a single bad actor. WF-ROADMAP-0006 item #7 calls for configurable
rate limiting (RPM/TPM, per key/session). This completes the guardrails trilogy.

Two constraints frame the decision, as with the other guardrails:

- **The deterministic core is sacred (WF-ADR-0001).** Rate limiting is pure counting on the
  invocation layer — no model call, no network. It gates *whether* a request is served, never how
  the prompt is scored.
- **Per-key limits need an identity boundary.** "Per key/session" requires virtual keys
  (WF-ROADMAP-0006 #5), which do not exist yet. So v1 is gateway-wide, exactly as budgets shipped
  gateway-wide before per-key attribution.

## Decision

1. **A fixed-window limiter caps RPM and/or TPM; on breach the gateway returns HTTP 429.** A
   window is `window` seconds (default 60) keyed by `floor(now / window)` over a monotonic clock,
   so windows roll deterministically and survive clock jumps. `rpm` caps admitted requests per
   window; `tpm` caps the upstream tokens served per window. At least one is set when the block is
   present; either may be omitted.

2. **429, not degrade.** Unlike a budget breach (which degrades to a cheaper tier), a rate breach
   returns `429 Too Many Requests` with a `Retry-After` header (seconds until the window rolls),
   an `x-wayfinder-router-rate-limit: rpm|tpm` header naming the cap that tripped, and an
   OpenAI-shaped `wayfinder_router_rate_limited` error. Degrading wouldn't help: the concern is
   request/token *volume*, and the cheaper tier is just as floodable. A hard stop is the point.

3. **The outermost guardrail.** The admission check runs first — before scoring, budget, cache,
   and delivery — so a flood is rejected with minimal work. A request that is admitted counts
   immediately against RPM; a served turn's upstream tokens are added to the window after delivery
   (non-streaming and streaming alike).

4. **A cache hit counts toward RPM but not TPM.** A hit is still a request (it consumes a request
   slot — request-volume protection holds regardless of caching), but it makes no upstream call,
   so it contributes no tokens to the TPM window. TPM measures upstream throughput.

5. **Gateway-wide in v1; one long-lived limiter.** Its window counters survive config hot-reloads
   (like the circuit breaker), and the limits track reloaded config. Per-key / per-session limits
   ride on virtual keys (WF-ROADMAP-0006 #5).

6. **Configured under `[gateway.rate_limit]`** (`rpm`, `tpm`, `window`); absent means no limit.
   `rpm`/`tpm` are positive integers; `window` is a positive number; the block round-trips through
   `dump_gateway_toml` and hot-reloads.

## Consequences

- **Upstream protection and blast-radius containment** without touching the decision path — and
  testable deterministically with an injected clock (no real waiting).
- **Completes the guardrails trilogy**: cost cap (budgets) + repeat dedupe (cache) + volume cap
  (rate limit), all on the invocation layer, all reusing the `[gateway.*]` config pattern.
- **A 429 surfaces `Retry-After`** so well-behaved clients back off; `/metrics` gains
  `wayfinder_router_rate_limited_total{limit=…}`. Successful responses also carry informational
  `X-RateLimit-Limit` / `-Remaining` / `-Reset` (the tightest applicable request cap), so clients
  can self-pace before a breach (added in v2026.6.8).
- **Risk — a fixed window allows a 2× burst at the boundary** (the classic fixed-window edge). A
  sliding window or token bucket is a deliberate later refinement; fixed-window is the simplest
  deterministic counter and matches the savings ledger's bucket style.
- **Limitation — per process.** The limiter is in-memory per worker; a multi-process deployment
  caps per worker, not globally (consistent with the breaker, budget, and cache).
- **Limitation — gateway-wide only** until virtual keys add the per-key boundary.

## Alternatives Considered

- **Degrade on a rate breach** (like budgets) — the cheaper tier is equally floodable, so it
  doesn't protect the thing at risk. Rejected; rate limiting hard-stops with 429.
- **Sliding window / token bucket in v1** — smoother (no boundary burst) but more state and
  trickier to reason about. Deferred; fixed-window is the correct-enough, deterministic v1.
- **Per-request header overrides** — like the cache's force-refresh, an unauthenticated control on
  the open chat endpoint. Deferred until virtual keys add auth.
- **Delegating to an upstream proxy / nginx** — splits the guardrail from the router and concedes
  a table-stakes control; rejected, as in WF-ADR-0031/0032/0033.

## Success Measures

- With `[gateway.rate_limit] rpm = N`, the (N+1)th request in a window gets a 429 with a
  `Retry-After`, and the window rolls cleanly afterward — with an **identical scored decision** for
  admitted requests.
- With `tpm = N`, requests are admitted until the window's served tokens reach N, then 429.
- A cache hit consumes a request slot but adds no tokens to the TPM window.
- No `[gateway.rate_limit]` block ⇒ unlimited (no behavior change).

## Related

- WF-ADR-0001 (deterministic, offline, no-model-call core — preserved by rule 1)
- WF-ADR-0032 (budgets — cost cap) / WF-ADR-0033 (response cache — repeat dedupe): the other two guardrails
- WF-ADR-0031 (circuit breaker — the other in-memory, per-process runtime-state primitive)
- WF-ROADMAP-0006 (item #7 rate limiting; item #5 virtual keys, which unlock per-key limits)
