---
schema_version: 1
id: WF-ADR-0004
type: decision
tags: [invocation, gateway, byo-key, boundary]
---

# WF-ADR-0004: Invocation Layer and the OpenAI-Compatible Gateway

## Status

Accepted

## Category

Architecture

## Context

WF-ADR-0001 made Wayfinder a pure recommender: it scores a prompt and names a
model, but never invokes one — no key, no network. That purity is the integrity
asset (deterministic, golden-tested, reproducible).

But the natural user request is "weigh the prompt, then actually route it to the
chosen model with my own key." Done naively — folding inference into the scorer —
this would poison the property that makes Wayfinder trustworthy: the scored path
would become side-effectful, key-bearing, and non-deterministic.

A design council (product, architecture, integration, calibration seats) reviewed
the surfaces and converged: deliver routing-with-invocation **headless**, as a
separate layer, with an OpenAI-compatible gateway as the primary surface — not a
UI. The UI, if ever built, is for calibration/explainability only and is never on
the routing path.

## Decision

Add bring-your-own-key (BYO) invocation as a **separate, optional layer**, leaving
the deterministic core untouched.

- **The core stays pure.** `complexity`, `config`, and `calibrate` import no model
  SDK, read no key, and make no network call. Their golden tests are unchanged.
- **Gateway (primary surface).** An OpenAI-compatible HTTP proxy
  (`POST /v1/chat/completions`) extracts the prompt, scores it with the core,
  maps the recommended model name to a configured upstream endpoint, and forwards
  the request with the user's key. Existing OpenAI-style clients point their
  `base_url` at it and route transparently — no application code changes. This is
  the established pattern (LiteLLM / RouteLLM / OpenRouter).
- **In-process invoker (secondary).** A thin helper for Python callers who prefer
  not to run a proxy; same boundary, keys supplied by the caller.
- **Keys never enter the core or the config file.** `wayfinder.toml` may map a
  model name to an upstream `base_url` and the *name of the env var* that holds
  the key (`api_key_env`); the secret itself is read from the environment at
  request time, only inside the gateway. No secret is ever in `wayfinder.toml`,
  in the scored path, or in any golden test.
- **The impure layer ships behind an extra.** `wayfinder[gateway]` pulls
  `fastapi`/`uvicorn`/`httpx`, lazily imported with a clear install hint when
  absent. The base package keeps `dependencies = []` (WF-ADR-0001).
- **No UI is required for routing.** Routing is headless (gateway/library/CLI). A
  UI is out of scope here and, if added later, serves calibration and
  explainability only.

This **amends** WF-ADR-0001 (scopes "never invokes" to the core) rather than
superseding it; the core's guarantee is unchanged, the prohibition is clarified.

## Consequences

### Positive

- "Route by weight with my own key" works with zero application changes, the way
  users already expect from comparable gateways.
- The deterministic core keeps every property that makes it trustworthy; the
  non-deterministic, key-bearing code is isolated and separately tested.
- Aligns with RAC ADR-035 (user owns credentials and inference): Wayfinder
  supplies the routing decision; the user's key and endpoints do the inference.

### Negative

- A second test class appears (network/secret handling) — kept entirely out of
  the core's golden path.
- The optional extra adds heavier dependencies (fastapi/httpx) for those who run
  the gateway; the base install stays light.

### Risks

- A future contributor folds invocation into the core "for convenience".
  Mitigation: this ADR draws the line; crossing it requires superseding it, and
  the core has no SDK/network imports to prove it.
- Streaming responses add complexity; the initial gateway relays upstream
  responses and treats full streaming support as a follow-up.

## Alternatives Considered

### Fold invocation into the scorer (one component scores and calls)

#### Disadvantages

- Makes the scored path side-effectful and key-bearing; destroys determinism and
  testability — the whole point of WF-ADR-0001.

### Stay a pure recommender; never invoke

#### Disadvantages

- Leaves every user to wire "name → endpoint + key → call" themselves; the
  gateway is exactly the reusable, transparent piece they would otherwise rebuild.

### A UI as the routing surface

#### Disadvantages

- Routing is a headless, per-request concern; a UI on the routing path adds
  latency and operational weight for no benefit. A UI's real value is offline
  calibration/explainability.

## Success Measures

- The core (`complexity`/`config`/`calibrate`) has no import of an HTTP/model SDK
  and no key access; its golden tests are unchanged.
- An OpenAI-style client pointed at the gateway routes to the recommended
  upstream with the user's key, with no application code change.
- No secret ever appears in `wayfinder.toml` or any test fixture.

## Related

- Amends WF-ADR-0001 · builds on WF-ADR-0002 (tiers) and WF-ADR-0003 (calibration)
- RAC ADR-035 (user-managed credentials), ADR-068 (routing boundary)
