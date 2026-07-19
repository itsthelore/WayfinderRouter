---
schema_version: 1
id: WF-DESIGN-0017
type: design
tags: [macos, foundation-models, xpc, rust, provider, offline, privacy]
---

# WF-DESIGN-0017: Apple Foundation Models provider boundary

## Status

Implemented as an availability-gated provider. On an eligible, never-configured Apple Silicon Mac,
Setup preselects the Apple Local preset only after a live `available` response. This is not a global
route default: existing configuration and route ladders remain unchanged, and Chat still opens on
`Automatic`.

## Decision

WAYFINDER will represent Apple's on-device system model as the typed provider kind
`apple-foundation-models`, with stable configured model name `system-default`. It is not an
OpenAI-compatible endpoint and has no URL or credential field.

The Rust gateway remains the only owner of routing, delivery planning, public HTTP translation,
offline closure, budgets, limits, accounting, cache, breaker state, health, cancellation, and
model discovery. A separately named Swift XPC inference service owns only Foundation Models
framework availability and generation calls. The credential broker remains a separate service and
protocol.

```text
Rust gateway
  -> typed AppleFoundationModelDelivery
  -> bounded com.wayfinder.FoundationModelBroker XPC v1
  -> Swift FoundationModelBroker.xpc
  -> SystemLanguageModel.default / LanguageModelSession
```

Both XPC services authenticate the bundled helper's signing identity. Closing the menu-bar app
does not stop either the launchd-owned gateway or on-demand inference service.

## Availability and bootstrap

Availability is a runtime fact, not an OS-version inference. The native service maps
`SystemLanguageModel.default.availability` into these stable categories:

- `available`;
- `device-not-eligible`;
- `apple-intelligence-not-enabled`;
- `model-not-ready`;
- `unsupported` for older OS or builds without the framework; and
- `unavailable` for unknown future or sanitized service failures.

New setup may offer and preselect `apple-local` only after an `available` response, and the user must
still confirm configuration. Existing configuration is never rewritten implicitly. Device
ineligibility retains Ollama/manual local setup. Model-not-ready is temporary readiness state, not
provider downtime. Non-macOS and macOS 14–15 continue without this provider.

## Configuration identity

The document-preserving config layer will accept the semantic shape:

```toml
[gateway.models.apple-local]
provider = "apple-foundation-models"
model = "system-default"
tier = "local"
```

Apple's internal model version is deliberately absent. Explicit preset mutation must preserve
all unrelated bytes and be covered by valid and invalid compatibility fixtures. Existing presets
do not change until the signed-platform exit gate passes.

## XPC protocol v1

Every request contains protocol version `1` and an opaque, non-empty request ID of at most 128 UTF-8
bytes. The protocol exposes only `availability`, `generate`, `stream`, and idempotent `cancel`.
It never accepts credentials, paths, shell commands, environment variables, Keychain references,
provider URLs, or configuration documents.

The implementation must define and test finite maxima for instructions, message count, each content
part, total normalized input, tool count/schema bytes, queued requests, generated chunk, accumulated
response, response event count, and deadline. Unsupported OpenAI or Anthropic generation parameters
are rejected explicitly. Version skew and malformed payloads fail closed with sanitized errors.

Streaming establishes XPC and model availability before committing downstream HTTP 200 headers.
Events are ordered and have exactly one terminal result. Cancellation is propagated to the native
session. Delivery may fail over only before the first generated output byte and never retries
generated content.

## Offline and failure semantics

`offline = true` permits Apple delivery only when every member of the selected delivery plan is
proven local. An XPC failure, model-not-ready state, timeout, or crash never permits cloud fallback
under offline mode. User cancellation is not a breaker failure. Availability and not-ready errors
are distinct from provider-down failures.

## Privacy and observability

Product language says **on-device** for this provider. A broader no-egress claim is permitted only
when the gateway proves the full offline delivery closure.

Prompt text, responses, tool arguments, generated content, and XPC payloads never enter unified
logging, Rust tracing, metrics, crash breadcrumbs, persisted state, snapshots, debug output, or
accessibility labels. Bounded provider kind, availability category, latency, usage counts,
cancellation, and sanitized error class are permitted. The inference service does not persist
session transcripts.

## Rollout gate

The provider's implementation gates cover buffered and streaming public API fixtures, cancellation,
bounds, accounting, breaker/cache/offline semantics, and signed caller authentication. Desktop
v0.1.0 still requires final signed app-closed inference and clean Apple Silicon release evidence.
The accepted setup preference is limited to never-configured Macs with confirmed live availability;
Ollama/manual local setup remains supported, existing configuration is preserved, and no global
`Automatic` route is changed. This decision does not remove the standalone Python distribution.

## Related

- WF-ROADMAP-0014
- WF-DESIGN-0016
- WF-ADR-0039
- `docs/apple-foundation-models-handoff.md`
- `docs/rust-migration-capability-matrix.md`
