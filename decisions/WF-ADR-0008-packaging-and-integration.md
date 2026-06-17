---
schema_version: 1
id: WF-ADR-0008
type: decision
tags: [packaging, integration, gateway, deployment]
---

# WF-ADR-0008: Packaging and Integration Surfaces

## Status

Accepted

## Category

Product

## Context

A fair question: does Wayfinder only work when someone types a prompt into the
CLI? The CLI, the onboarding flow, and the UI are all operator/bootstrap surfaces.
For Wayfinder to be useful, prompts have to be routed where they *already flow* —
inside applications, agent frameworks, and IDE assistants — not re-entered by hand.

The mechanism for that already exists (the OpenAI-compatible gateway, WF-ADR-0004),
but the packaging to make it deployable, and the integration story, were never
written down.

## Decision

State the integration surfaces and package the gateway for deployment.

- **The gateway is the primary integration surface.** Any client that speaks the
  OpenAI API points its `base_url` at Wayfinder and routes transparently, with no
  application code change: an app using the OpenAI SDK, agent frameworks
  (LangChain/LlamaIndex), IDE assistants that accept a custom endpoint (Cursor,
  Continue), or a gateway like LiteLLM. Wayfinder scores the incoming prompt and
  forwards to the chosen model with the user's key.
- **The library is the in-process surface.** `from wayfinder import
  score_complexity` for apps that own their model calls and want the recommendation
  without a network hop.
- **The CLI / onboarding / UI are operator/bootstrap surfaces**, not the request
  path: they calibrate and inspect the config that the gateway and library then use.
- **Deployment:** ship a container that runs `wayfinder serve` as a sidecar or
  small service, plus a compose example that persists `wayfinder.toml` and the
  feedback log on a volume and shows the `recalibrate` one-shot (the CronJob analog).
- **Feedback wiring is the host surface's responsibility.** The host (app, IDE,
  chat) decides how to surface a 👍/👎 and POSTs it to `/v1/feedback`; Wayfinder
  provides the endpoint and the recording, not the judgment UI.
- **Secrets stay in the environment** (the gateway model's `api_key_env`), never in
  the image, the config file, or the scored path.
- **Framework-specific adapters** (a LangChain/LiteLLM-style hook) are recorded as
  *future*: the gateway already covers those clients via `base_url`, so an adapter
  is a convenience, not a requirement.

## Consequences

### Positive

- The honest answer to "how does this reach real surfaces" is concrete: a
  container in front of OpenAI-compatible traffic, or the library embedded in-process.
- The base package stays zero-dependency; only the deployed gateway pulls the
  `[gateway]` extra.
- The feedback loop reaches production: the host app wires 👍/👎, the gateway
  records it, recalibration (WF-ADR-0007) closes the loop.

### Negative

- The host app must do a little work to wire feedback (a thumbs control → one POST);
  Wayfinder deliberately does not own that UI.
- No turnkey framework adapters yet — integration is via `base_url` until one is built.

## Alternatives Considered

### Framework adapters as the primary integration

Ship LangChain/LlamaIndex hooks first.

#### Disadvantages

- Per-framework maintenance, and the gateway already serves every OpenAI-compatible
  client through one surface. Adapters are additive, not foundational.

### A hosted/SaaS routing service

#### Disadvantages

- Out of scope and against the self-hostable, BYO-key posture; the container is the
  deployment unit, run by the user.

## Success Measures

- `docker build` produces an image that serves the gateway; an OpenAI SDK client
  pointed at it routes with no app code change.
- The README shows, end to end, pointing a client, wiring `/v1/feedback`, and
  scheduling `recalibrate`.
- No secret appears in the image or the config; keys are read from the environment.

## Related

- Builds on WF-ADR-0004 (gateway) and WF-ADR-0007 (recalibration); the bootstrap
  surfaces are WF-ADR-0005 (UI) and WF-ADR-0006 (onboarding).
