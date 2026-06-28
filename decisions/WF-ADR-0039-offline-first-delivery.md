---
schema_version: 1
id: WF-ADR-0039
type: decision
tags: [gateway, offline, reliability, delivery, invocation, local-first]
---

# WF-ADR-0039: Offline-first delivery — serve the cheapest/local tier when there's no network

## Status

Accepted

## Category

Technical

## Context

WF-ADR-0031 gave the gateway a `degrade` failover policy (cloud→local, reusing the circuit breaker),
but it is purely **reactive**: a tier is only abandoned after retries + timeouts trip the breaker. On a
plane (or any no-connectivity moment) the *first* request to a cloud-scored prompt still pays the full
upstream timeout before degrading — a bad experience for the "LLM that keeps working offline" promise
(WF-ROADMAP-0007, the OS-level-routing vision). There is no way today to say, proactively, "there's no
network — just use the local model."

## Decision

1. **An explicit offline signal forces delivery to the cheapest tier.** A `[gateway] offline = true`
   config knob (off by default) and a per-request `X-Wayfinder-Offline: true` header make the gateway
   deliver to the **cheapest tier** (the bottom of the score ladder) and **skip dearer/cloud tiers
   entirely** — so no cloud call is attempted and nothing hangs on a timeout.

2. **Delivery-only; the decision is untouched (WF-ADR-0001).** The prompt is still scored and the
   chosen tier is still reported in `x-wayfinder-router-model`. Offline only changes *where the request
   is delivered*, reusing the existing `delivery_plan` + circuit breaker. A new
   `x-wayfinder-router-offline: true` response header marks the degrade; it is **not** reported as a
   failover (that label is reserved for reactive cloud-down).

3. **Explicit signal only; auto-detection deferred.** Offline is set by config or header, not by an
   active connectivity probe — automatic "am I online?" detection is flaky and not a clean primitive.
   Reactive cloud-down is already covered by the breaker/`degrade`; this adds the *proactive* path.

4. **Tiered routers.** Offline needs a cost ladder to pick "cheapest"; in classifier mode (no tier
   ladder) it is a no-op and delivery proceeds normally.

## Consequences

- **Wayfinder keeps working with no network** — the felt half of josalhor's Show HN ask — by serving
  the local tier instantly instead of timing out toward an unreachable cloud.
- **Doubles as a privacy / air-gapped mode**: `offline = true` pins delivery to the local tier
  regardless of score.
- **Unlocks the menu-bar "Offline" toggle** (WF-ADR-0040): the toggle flips `[gateway] offline`.
- **Reuses, not reinvents**: the same `degrade` primitive and breaker (WF-ADR-0031); a small,
  delivery-layer change.
- **Limitation**: an explicit signal, not auto-sensing; and it assumes the cheapest tier is the
  locally-reachable one (the normal local/cloud setup).

## Alternatives Considered

- **Rely only on reactive `degrade`.** Rejected for the proactive case: the first offline request still
  eats the full cloud timeout before the breaker opens.
- **Automatic connectivity detection (ping/DNS probe).** Deferred — flaky, racy, and platform-specific;
  an explicit signal is honest and predictable. Can layer on later.
- **A separate "local-only" config vs an "offline" header.** Folded into one knob + one header with the
  same meaning (deliver cheapest only), to avoid two names for one behavior.

## Success Measures

- With `[gateway] offline = true` (or the `X-Wayfinder-Offline` header), a prompt that *scores* cloud is
  **served by the cheapest/local tier**, the response carries `x-wayfinder-router-offline: true`, and no
  request is made to the dear tier (no timeout).
- Off by default: a cloud-scored prompt still routes cloud, with no offline header.
- The scored decision/header is unchanged; the deterministic core makes no model call (WF-ADR-0001).

## Related

- WF-ADR-0001 (deterministic, offline-decision core — untouched; this is delivery-layer)
- WF-ADR-0031 (failover policy — `degrade` is the primitive this reuses proactively)
- WF-ADR-0040 (macOS menu-bar "Offline" toggle flips this knob)
- WF-ROADMAP-0007 (local-LLM-service vision — Initiative 2)
