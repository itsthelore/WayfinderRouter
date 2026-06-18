---
schema_version: 1
id: WF-ADR-0011
type: decision
tags: [gateway, override, transport, openai-compatible, boundary]
---

# WF-ADR-0011: Per-Request Routing Override Transport

## Status

Accepted

## Category

Architecture

## Context

The gateway (WF-ADR-0004) routes every request off the server's
`wayfinder-router.toml`: it scores the prompt, maps the recommended name to a
configured upstream, and forwards. The decision boundary is a property of the
*deployment*, identical for every caller.

That is the right default, but it cannot serve a per-call intent: "this prompt
is trivial, keep it local regardless of score", "I am willing to pay for cloud
on this one", or — the motivating case (WF-ADR-0010) — a chat client whose user
moves a routing slider or picks a routing mode per conversation. WF-ADR-0010
records that the chat fork's controls require the router to "accept a per-request
override … a threshold/model-pair in the request body or a header, or the
model-name mapping". This ADR fixes that transport so the gateway, the chat
fork, and any other client agree on one wire contract.

The hard constraint is the boundary WF-ADR-0001/0004 draws: the scored path is
deterministic, key-free, and never invokes a model. An override must move *which
threshold or decision applies* to a request — it must not introduce inference,
provider selection, or any new model call into the core. It also must not break
OpenAI compatibility: ordinary clients send a `model` field and arbitrary
headers, and those must keep working unchanged.

## Decision

A request may steer its own routing through two OpenAI-compatible channels,
evaluated in a fixed precedence. The score is always computed and reported; an
override only changes which configured endpoint the request is forwarded to.

- **The `model` field is a routing directive.** It is already on every
  OpenAI-style request and was previously ignored. Now:
  - `auto`, an empty value, or any value that is **not** a recognized directive
    means "Wayfinder decides" — score per config, today's behavior. Unrecognized
    values (an ordinary model id like `gpt-4o`) fall through to scoring rather
    than erroring, preserving OpenAI compatibility.
  - An exact configured endpoint name (a key of `[gateway.models]`) **pins** the
    request to that endpoint, bypassing the score-based recommendation.
  - `prefer-local` / `prefer-cloud` pin to the **low / high end** of the
    configured router (its first / last tier's model), giving a mode-agnostic
    "lean cheap / lean capable" control without naming a concrete endpoint.
- **An `X-Wayfinder-Threshold` request header re-decides the binary cut.** A
  number in `0.0`–`1.0` re-runs the decision for that one request at that cut,
  reusing the configured scoring weights. It is well-defined only for a binary
  (two-tier) router; against a classifier or a multi-tier router — which have no
  single cut to move — it is a `400` (`wayfinder_router_bad_override`), as is a
  malformed or out-of-range value.
- **Precedence: an explicit `model` pin wins over the threshold header, which
  wins over the scored default.** Naming an endpoint (directly or via
  `prefer-*`) is the most specific intent, so it takes priority; if the caller
  leaves `model` at `auto`, the threshold header applies; with neither, the
  gateway scores and decides.
- **The decision signal is extended, not changed.** Every response still carries
  `x-wayfinder-router-model` (the chosen endpoint) and `x-wayfinder-router-score`
  (the structural score, always computed even when pinned), and now also
  `x-wayfinder-router-mode` (`scored` / `pinned` / `threshold-override`) so a
  caller can see *why* a request went where it did.

The boundary holds: every override path still scores deterministically with the
pure core and forwards through the existing key-bearing gateway path. No override
adds a model call, reads a credential, or selects a provider in the core.

## Consequences

### Positive

- A chat client (WF-ADR-0010) can offer a per-conversation routing mode and a
  threshold slider over plain OpenAI-compatible transport — a model selector for
  `auto` / `prefer-*` / a pinned endpoint, and a custom header for the slider —
  with no bespoke protocol.
- The override is fully transparent and testable: it changes the *target*, never
  the *scoring*, so the deterministic core and its golden tests are untouched.
- Existing callers are unaffected — an unrecognized `model` and any unrelated
  header behave exactly as before.

### Negative

- The `model` field now carries two meanings (a directive vs. a passthrough id);
  the rule is documented and kept tolerant, but it is a small overload of a
  standard field.
- A second override channel (a header) exists alongside the field, requiring a
  stated precedence.

### Risks

- A caller pins to an endpoint that is not configured and gets the existing
  `wayfinder_router_misconfigured` `500`. Mitigation: that is the same clear,
  already-tested error the scored path raises for a missing endpoint.
- A future contributor reads the override as license to add per-request model
  invocation or provider logic. Mitigation: this ADR scopes the override to
  threshold/decision selection only; the core still has no SDK or network import.

## Alternatives Considered

### A bespoke override body (a `wayfinder` object in the JSON payload)

Carry the override as a custom field in the request body.

#### Disadvantages

- Many OpenAI clients reject or strip unknown body fields, and it is invisible to
  a plain model selector. The standard `model` field plus a header ride through
  unchanged.

### Threshold only, no model directive

Expose just the `X-Wayfinder-Threshold` header.

#### Disadvantages

- A header is awkward to drive from a chat UI's model dropdown, and gives no way
  to pin a concrete endpoint. The `model` directive is the natural control for
  client UIs; the header complements it for fine threshold control.

### Reject unrecognized `model` values

Treat any `model` that is not a directive as an error.

#### Disadvantages

- Breaks OpenAI compatibility — real clients send concrete model ids. Falling
  through to scoring keeps the gateway a drop-in proxy.

## Success Measures

- An OpenAI-style client routes a single request by setting `model` to a
  configured endpoint, `prefer-local`, or `prefer-cloud`, or by sending an
  `X-Wayfinder-Threshold` header, with no application code change.
- The scored default and every existing gateway test are unchanged; the override
  paths add no model call, key read, or provider logic to the core.
- A response's `x-wayfinder-router-mode` reports `scored`, `pinned`, or
  `threshold-override` matching the channel that was used.

## Related Decisions

- WF-ADR-0004 (the gateway and the invocation boundary this override rides on)
- WF-ADR-0001 (the deterministic, key-free core the override must not disturb)
- WF-ADR-0010 (the chat fork whose per-conversation controls need this override)
- WF-ADR-0002 (tiers — the low/high ends `prefer-*` resolve to)
