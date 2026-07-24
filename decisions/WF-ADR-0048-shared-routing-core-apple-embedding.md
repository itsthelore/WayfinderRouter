---
schema_version: 1
id: WF-ADR-0048
type: decision
status: accepted
date: 2026-07-24
tags: [rust, swift, ios, ipados, ffi, routing, providers, persistence]
---

# Share one pure Rust routing core and embed it natively on Apple platforms

## Context

WF-ADR-0046 made Rust Wayfinder's sole router and gateway runtime. The current
workspace still combines pure deterministic routing with host, HTTP-server,
provider, service, and macOS integration responsibilities.

iOS cannot run the macOS helper topology and should not launch an internal
loopback server merely to preserve it. Reimplementing score, thresholds,
exclusions, tie-breaks, or receipts in Swift would create a second routing
authority.

## Decision

Extract a platform-neutral Rust library that owns all authoritative route
planning. The gateway and the mobile bridge consume that same library and
shared fixtures.

Target ownership:

```text
rust/crates/
  wayfinder-routing-core/       pure deterministic planning and receipts
  wayfinder-runtime-contracts/  requests, destinations, errors, usage
  wayfinder-provider-core/      portable provider orchestration where proven
  wayfinder-apple-ffi/          generated Swift-facing bridge
  wayfinder-gateway/            Axum/server host consuming the same core
```

`wayfinder-routing-core` performs no filesystem, Keychain, process, UI,
HTTP-server, Apple-framework, or provider-secret work. Eligibility inputs are
typed destination snapshots supplied by the host runtime. It owns:

- compatibility exclusions and stable reason codes;
- score and threshold semantics;
- tier and ordered-route selection;
- stable tie-breaks and fallback planning;
- route explanations and compact receipts.

The mobile app calls the library in-process. The macOS product may remain a
thin client over its bundled gateway for v0.2.

## Bridge contract

Phase 0 tests generated UniFFI Swift bindings first. A narrow generated C ABI is
the fallback only if UniFFI fails measured device, simulator, concurrency,
cancellation, binary-size, or maintenance gates.

The runtime bridge remains small:

```swift
protocol WayfinderRoutingEngine: Sendable {
    func validate(configuration: RoutingConfiguration) async throws
    func score(_ request: RoutingRequest) async throws -> ComplexityResult
    func plan(
        _ request: RoutingRequest,
        candidates: [DestinationSnapshot]
    ) async throws -> RoutePlan
    func explain(_ plan: RoutePlan) async throws -> RouteExplanation
}
```

Do not hand-maintain a broad JSON-over-FFI runtime. JSON remains the canonical
portable fixture and debugging format. Provider streaming belongs to the
execution layer, not the pure scorer.

## Provider execution placement gate

The routing-core language is decided; provider execution placement is not.
Phase 0 compares:

1. shared Rust HTTP execution with a narrow Swift credential callback; and
2. Rust route planning with Swift `URLSession` provider execution.

The selected v0.2 path must demonstrate:

- correct cancellation before and during streaming;
- fragmented SSE and terminal-event behavior;
- system TLS and ATS compatibility;
- bounded memory and event queues;
- usable error classification;
- acceptable binary size and launch cost;
- no secret crossing an unnecessary language boundary.

Apple frameworks, Authentication Services, Keychain, and Apple on-device model
execution remain in Swift regardless of this result. A Swift provider adapter
is acceptable when it implements the shared contracts and fixtures.

## Accepted mobile foundation choices

- **Deployment floor:** iOS 18 and iPadOS 18. Foundation Models remains
  separately availability-gated to the OS/device states that actually support
  it.
- **Route configuration:** typed native data with a versioned export/import
  schema; mobile does not edit desktop TOML.
- **Provider catalog:** executable adapters are built in and reviewed. Remote
  signed metadata may update labels and model catalogs only after a separate
  schema/signature implementation.
- **Credential backup:** provider secrets use device-only Keychain
  accessibility by default and are not silently synchronized.
- **Mac adoption:** shared Apple packages are introduced incrementally after
  the mobile seams are proven; the existing macOS target is not moved first.

## Decisions still requiring measured Phase 0 artifacts

| Decision | Required artifact | Gate |
| --- | --- | --- |
| UniFFI vs generated C ABI | simulator and physical-device bridge spike | typed call, errors, concurrency, lifetime, size |
| Rust HTTP vs Swift `URLSession` | two execution spikes | streaming, cancellation, TLS, memory, size |
| SwiftData vs SQLite | persistence prototype | migration, deterministic tests, export/delete |

These are bounded choices inside the accepted architecture. None may change
mobile independence or introduce a Swift routing implementation.

## Parity and release gates

- Gateway and embedded calls consume the same golden corpus.
- Serialized decisions and reason codes are byte-identical where serialization
  applies.
- Simulator and physical-device architecture builds are required.
- Malformed/oversized input, version skew, concurrency, callback release, and
  cancellation races fail safely.
- Core extraction lands without provider or UI behavior changes.
- XCFramework/bridge work lands separately from the extraction.

## Implementation status

The first extraction boundary provides:

- `wayfinder-routing-core` as the renamed authoritative scorer and planner;
- `wayfinder-runtime-contracts` as secret-free, serializable host contracts;
- gateway and compatibility consumers compiled against the renamed core;
- hard eligibility filters that run before score-based tier selection;
- stable input-order selection and pre-output fallback planning within the
  recommended tier;
- a manifest-level dependency test that rejects unreviewed production
  dependencies.

The generated Swift bridge, XCFramework assembly, physical-device proof, and
provider-execution placement decision remain separate gated work.

## Consequences

The router remains one inspectable authority while each Apple host can use the
native lifecycle and security APIs appropriate to it. The cost is an explicit
FFI build/test lane and shared-contract discipline across Swift and Rust.

## Related

- WF-ADR-0046 — Rust-only runtime
- WF-ADR-0047 — native mobile independence
- WF-DESIGN-0019 — provider and authentication framework
- WF-ROADMAP-0016 — mobile delivery sequence
