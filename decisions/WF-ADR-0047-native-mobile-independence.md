---
schema_version: 1
id: WF-ADR-0047
type: decision
status: accepted
date: 2026-07-24
tags: [ios, ipados, native, mobile, pairing, routing, privacy]
---

# iPhone and iPad are independent Wayfinder products; pairing is optional

## Context

Wayfinder Desktop v0.1.0 established a native macOS Chat experience over a
bundled Rust gateway. A companion-only mobile app would inherit the Mac's
availability, trust, and lifecycle without giving an iPhone or iPad an
independently useful product.

Mobile devices can legitimately execute work in two domains without a Mac:

- Apple's on-device Foundation Models framework when the current device reports
  it available; and
- approved hosted providers called directly with a Keychain-held API key or an
  official provider account flow.

A trusted Mac can still contribute larger local models and desktop-only account
helpers, but that is an extension of the eligible destination set rather than
the foundation of mobile Wayfinder.

## Decision

Wayfinder v0.2.0 ships as a fully native iPhone and iPad application. It embeds
the authoritative deterministic routing core, owns local threads and
configuration, executes approved providers, and remains useful when every Mac
is off.

The product has three execution domains:

1. **On this device** — execution occurs on the current iPhone or iPad.
2. **Direct cloud** — the current device contacts an approved hosted provider.
3. **Paired Wayfinder host** — an optional trusted Mac or self-hosted Wayfinder
   runtime contributes destinations through a bounded provider adapter.

Pairing is deferred from the v0.2.0 release gate to v0.2.1. It is represented as
a provider and never owns the mobile app's settings, credentials, conversations,
or identity.

## Product invariants

- First launch leads with standalone use. Connecting a Mac is secondary.
- Chat, routing, direct provider setup, Apple on-device execution, and thread
  retention never require a Mac or a localhost gateway.
- Connecting an account or adding a key publishes eligible destinations but
  never silently changes `Automatic` or rewrites a user route.
- A paired host joins a route only after explicit per-host opt-in.
- Removing or powering off a paired host cannot prevent launch, thread access,
  direct cloud execution, or on-device execution.
- Existing macOS behavior remains gateway-first. Mobile work does not broaden
  the macOS credential broker or replace its ChatGPT/Codex helper boundary.

## Execution-boundary truth

Every destination declares one current content-execution boundary:

- `on-device` — prompt content remains on the current iPhone or iPad;
- `local-network` — a trusted nearby Wayfinder device receives the request;
- `hosted` — a cloud provider receives the request.

Transport and execution are recorded separately for paired hosts. A request
sent to a Mac and then to ChatGPT is **hosted via the Mac**. A request sent to a
Mac-local Qwen instance is **Mac local over the local network**. Neither is
described as on-device on the phone.

The runtime enforces three user postures:

| Posture | Eligible boundaries |
| --- | --- |
| On-Device Only | `on-device` |
| Local Devices | `on-device`, `local-network` |
| Hosted Allowed | all |

No failure may cross a denied boundary. A pinned destination fails specifically
rather than silently switching.

## Platform experience

After onboarding, iPhone launches into a `TabView` with separate navigation
stacks for Chat, Threads, Destinations, and Settings. iPad uses an adaptive
`NavigationSplitView` with transcript and composer as the primary surface and
an optional route inspector.

The macOS window hierarchy is not copied into a smaller display. Standard Apple
navigation, forms, lists, sheets, typography, accessibility, and platform
materials take precedence over custom chrome.

## Consequences

### Positive

- Mobile Wayfinder has a clear value proposition even with no Mac or signal.
- A Mac can add capabilities without becoming a synchronization or identity
  authority.
- Privacy language follows actual prompt movement.
- The same deterministic product signature applies across Apple devices.

### Negative

- iOS must own provider execution, credentials, persistence, and lifecycle
  behavior rather than reusing the desktop HTTP process shape.
- Direct providers may require native Swift adapters even when desktop delivery
  remains implemented in Rust.
- Pairing requires a separate authenticated protocol and revocation model.

## Rejected alternatives

### Companion-only iOS client

Rejected because onboarding, routing, Chat, and retention would all fail when a
Mac is unavailable.

### Internal localhost gateway on iOS

Rejected because it preserves a desktop deployment shape rather than a mobile
product boundary. iOS embeds the routing library and calls provider adapters
directly.

### Mac as the mobile configuration authority

Rejected because pairing must remain optional and revocable. Mobile
configuration and threads are locally owned.

## Related

- WF-ADR-0048 — shared routing core and Apple embedding
- WF-DESIGN-0019 — provider, authentication, and credential contract
- WF-ROADMAP-0016 — native mobile v0.2 delivery
- `docs/apple-platform-capability-matrix.md`
