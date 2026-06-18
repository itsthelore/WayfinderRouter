---
schema_version: 1
id: WF-ADR-0013
type: decision
tags: [gateway, streaming, async, observability, resilience, boundary]
---

# WF-ADR-0013: Streaming and Gateway Production Hardening

## Status

Accepted

## Category

Architecture

## Context

The gateway (WF-ADR-0004) was correct but only suitable for internal testing. Two
gaps blocked real adoption — especially the chat-client integrations the project
documents (WF-ADR-0010, the `examples/` recipes):

- **It did not stream.** The forward path did a blocking `httpx.post(...)` and
  returned the whole buffered body. A client sending `stream: true` got a stalled,
  all-at-once reply or a timeout — token-by-token rendering, the core chat UX, was
  broken. Because the handler was a synchronous `def` calling blocking I/O, the
  server also serialised under concurrent load.
- **It failed opaquely.** An upstream timeout or connection error bubbled to a bare
  `500` with a traceback (not an OpenAI-shaped error); there was no logging, no
  request id, no way to see *why* a request routed where in production; the upstream
  timeout was hardcoded; a config-reload failure was swallowed silently; and the
  `/v1/feedback` write — which feeds calibration — was unauthenticated, so anyone
  could poison the label log.

The hard constraint is unchanged: the *scored path* stays deterministic, key-free,
and free of any model call (WF-ADR-0001/0004). Hardening must touch only the
invocation layer, never the decision.

## Decision

The gateway forwards asynchronously and streams, and gains the operational surface a
deployable proxy needs. None of it changes how a prompt is scored.

- **Streaming is first-class.** A request with `stream: true` is forwarded with
  `httpx.AsyncClient.stream(...)` and relayed back as a `StreamingResponse`
  (`text/event-stream`), chunk by chunk. The non-streaming path is also async
  (`aforward_request`). The synchronous `forward_request` is retained only for
  `invoke_model` (the onboarding A/B caller, which runs outside the server).
- **Upstream failures are OpenAI-shaped.** A transport error becomes
  `UpstreamError` → a `502` with `type: wayfinder_router_upstream_error` (or, mid
  stream, a terminal SSE error event), not a bare `500`.
- **Observability.** Every response carries `x-wayfinder-router-request-id`; the
  gateway logs each routing decision, upstream errors, and — no longer silently —
  config-reload failures. `GET /healthz` reports `degraded` and lists `missing_keys`
  when a configured `api_key_env` is unset.
- **Configuration.** The upstream timeout is set by `WAYFINDER_ROUTER_TIMEOUT` or
  `serve --timeout` (default 60s). `serve --dry-run` returns the routing decision
  without calling any upstream, so the router can be tried with no backends.
- **The feedback write is guarded.** When `WAYFINDER_ROUTER_FEEDBACK_TOKEN` is set,
  `/v1/feedback` requires a matching `Authorization: Bearer` token; unset, it stays
  open (back-compat) but logs a warning at startup.

The boundary holds: scoring is still pure and deterministic; only the
already-impure forward path gained async, streaming, error wrapping, logging, and an
optional auth check.

## Consequences

### Positive

- The documented chat-client integrations (LibreChat, Open WebUI) actually work —
  tokens stream, and concurrent requests do not block one another.
- Upstream failures, missing keys, and stale config are visible instead of silent;
  a request id makes production routing debuggable.
- The label log can be protected against poisoning without breaking existing
  unauthenticated deployments.

### Negative

- The gateway now depends on async httpx and carries more surface (timeouts, auth,
  logging) than a minimal proxy.
- A second forwarder (sync `forward_request` for onboarding, async for the server)
  exists; the split is documented and small.

### Risks

- A streaming response cannot retroactively change its HTTP status once bytes have
  flowed, so a mid-stream upstream failure surfaces as a terminal SSE error event
  rather than an HTTP error code. Mitigation: the event uses the same
  `wayfinder_router_upstream_error` shape, and non-streaming requests still get a
  proper `502`.

## Alternatives Considered

### Buffer the stream and return it whole

Keep the blocking path and just forward `stream: true` upstream, returning the
buffered SSE body at the end.

#### Disadvantages

- Defeats the purpose: the client sees no progressive tokens, pays for a long stall,
  and may time out. Streaming has to be relayed incrementally to be useful.

### Require authentication on `/v1/feedback` always

Make the bearer token mandatory.

#### Disadvantages

- Breaks every existing unauthenticated deployment on upgrade. An opt-in token (with
  a startup warning when absent) hardens new deployments without a breaking change.

## Success Measures

- A `stream: true` request through the gateway yields incremental SSE chunks; a chat
  client renders tokens progressively.
- An upstream timeout or connection error returns `wayfinder_router_upstream_error`
  with a request id and a log line, not a bare `500`.
- `serve --dry-run` returns a routing decision with no backend configured; the scored
  path and every prior gateway test are unchanged.

## Related Decisions

- WF-ADR-0004 (the gateway and the invocation boundary this hardening stays inside)
- WF-ADR-0001 (the deterministic, key-free core the async/streaming path must not disturb)
- WF-ADR-0010 (the chat fork whose token streaming this unblocks)
- WF-ADR-0012 (the `/v1/models` discovery endpoint served alongside these routes)
