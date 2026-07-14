---
schema_version: 1
id: WF-ADR-0045
type: decision
tags: [rust, gateway, router, helper, macos, compatibility, security, packaging]
---

# WF-ADR-0045: Rewrite the router and gateway as one signed Rust helper, behind compatibility gates

## Status

Accepted for staged implementation; Rust is not yet accepted as the default backend.

## Category

Architecture

## Context

The Python package now contains three different kinds of responsibility:

1. a pure deterministic router and its offline calibration/evidence tools;
2. an asynchronous local HTTP gateway with provider translation and operational state; and
3. CLI, service-manager, and packaging glue used by headless installs and the native macOS app.

The native app is already constitutionally a thin client over an always-on launchd-owned gateway
(WF-ADR-0038/0042/0044). Python remains a good reference implementation and distribution path, but
it is a poor fit for a small universal helper nested, signed, updated, and crash-isolated inside a
macOS application. The migration must not trade that packaging improvement for behavioral drift or
weaker secret handling.

Repository discovery is recorded in `docs/rust-migration-capability-matrix.md`. It identifies a
14-command CLI, the OpenAI and Anthropic HTTP/streaming surfaces, configuration and persistence
formats, operational controls, and native client schemas that must remain runnable side by side.
It also identifies unsafe current behavior—unbounded frames, unchecked streaming status,
shell-based key commands, redirect/proxy ambiguity, and an offline mode that does not prove the
selected endpoint is local. Compatibility does not require perpetuating a vulnerability silently;
intentional hardening must be named, tested, and migrated.

## Decision

### 1. One distributable helper binary, multiple internal crates

Ship one executable named `wayfinder-router`. It retains the current command/subcommand surface,
including `serve` and `service`, instead of adding a separately named gateway daemon.

The executable is assembled from a Rust workspace:

```text
rust/
  Cargo.toml
  crates/
    wayfinder-core/
    wayfinder-config/
    wayfinder-providers/
    wayfinder-gateway/
    wayfinder-service/
    wayfinder-cli/
    wayfinder-compat-tests/
```

- `wayfinder-core` is pure and deterministic: feature extraction, scoring, tier/classifier
  decisions, explanations, and later calibration/judging/pricing primitives. It cannot depend on
  Tokio, an HTTP client/server, process execution, Keychain code, or provider secrets.
- `wayfinder-config` owns semantic TOML validation, environment/discovery precedence, and a
  lossless document representation for explicit mutations. Parsed runtime state is immutable;
  serializable state contains secret references only, never values.
- `wayfinder-providers` owns outbound clients, error translation, retry primitives, and explicit
  OpenAI/Anthropic streaming state machines. It does not make routing decisions.
- `wayfinder-gateway` owns HTTP handlers, policy precedence, hot reload, limits, cache, ledger,
  metrics, virtual keys, and lifecycle. It consumes the core and providers through typed seams.
- `wayfinder-service` renders and manages launchd/systemd-user units and contains the client side
  of approved secret-resolution/control seams. It does not write units unless a CLI operation
  explicitly asks it to.
- `wayfinder-cli` is the sole binary crate and the sole config/service author. Library crates
  expose typed operations so compatibility tests need not parse presentation strings internally.
- `wayfinder-compat-tests` is never shipped. It drives Python and Rust subprocesses and local fake
  providers with identical fixtures.

One binary preserves the existing console-script name, launchd `ProgramArguments`, rollback path,
and user mental model. It also gives Wayfinder.app one nested code object to sign and update. The
crate boundaries still prevent the gateway or Apple integration from contaminating the pure core.

### 2. Tokio, Axum, Reqwest, Serde, and typed errors

- Tokio is the asynchronous runtime. Use only the required runtime, net, time, signal, sync,
  process, and test features.
- Axum is the HTTP layer. Its typed extractors/responses and Tower service model fit the existing
  endpoint contract while providing composable body limits, timeouts, tracing, and testable
  in-process services.
- Reqwest is the outbound provider client. A small number of long-lived clients are constructed
  with explicit connect/read/total timeouts, redirects disabled, automatic proxy use disabled for
  the native helper, and bounded decoding/streaming behavior.
- Serde plus `serde_json` and TOML tooling own wire/config conversion. Config mutation uses a
  document-preserving representation; semantic structs alone must never be serialized over an
  existing user file.
- Crate-local error enums use `thiserror`; public gateway failures convert once into explicit
  compatibility envelopes. User/network input is not handled with `unwrap`, `expect`, indexing
  that can panic, or assertion-only validation.

Dependency versions are locked in the committed `Cargo.lock`; CI and release builds use
`--locked`. Default features are disabled where practical and every enabled feature is reviewed.

### 3. Immutable runtime snapshots and last-good hot reload

Routing and gateway configuration parse into one immutable, internally consistent runtime
snapshot. Requests clone a cheap shared pointer to a snapshot and never observe half a reload.
Reload performs read → parse → validate → build dependent state → atomic publish. Failure increments
the reload metric and retains the last-good snapshot. The compatibility rule that one bad mtime is
not retried forever is preserved unless an explicit later decision changes it.

State with a lifecycle beyond the config snapshot—cache, breaker, rate windows, metrics, and
ledger—is held in bounded, separately synchronized components. Reconfiguration is explicit per
component; a new snapshot cannot silently reset accounting or authentication state.

### 4. Streaming is a state machine with bounded backpressure and cancellation

Provider streams are parsed incrementally by explicit state machines. The implementation must:

- check the upstream HTTP status before committing a downstream 200 stream;
- bound request bodies, buffered response bodies, SSE line/event size, tool-call accumulation,
  channel depth, and accounting buffers;
- tolerate fragmented frames and the currently supported missing-terminator cases;
- apply backpressure rather than spawning unbounded producers;
- cancel the upstream request when the downstream disconnects;
- permit retry/failover only before a downstream byte is committed;
- close and account for cancellation, timeout, malformed input, and graceful shutdown explicitly.

The gateway stops accepting new work on SIGTERM/SIGINT, signals active requests, drains them for a
bounded deadline, flushes permitted metadata state, and exits. Launchd remains the supervisor.

### 5. Secret references are typed; secret values are non-serializable and redacted

Serializable configuration can contain only a reference such as an environment-variable name,
legacy command reference, or macOS broker/keychain identifier. A resolved secret value:

- has redacted `Debug` and `Display`;
- does not implement serialization;
- is never a CLI argument, metric, tracing field, error string, fixture, snapshot, crash breadcrumb,
  or persisted Rust field;
- is cleared from owned memory on drop where the platform and optimizer permit;
- is attached only to the exact configured outbound origin.

Existing `api_key_env` and `api_key_cmd` configurations continue to load. Environment values keep
precedence. Legacy commands are isolated behind a compatibility resolver with a fixed timeout,
bounded stdout/stderr, sanitized errors, and no logging of output. Existing files are never
rewritten to a new secret mechanism implicitly.

For the production app, Swift remains responsible for Security.framework, Keychain consent, and
credential creation. The preferred future read seam is a launchd-on-demand Swift XPC credential
broker that authenticates the signed Rust helper by audit token/designated requirement and returns
only the requested secret over an in-memory reply. A direct Rust Security.framework bridge is not
selected by this ADR; adopting one would require its own unsafe/entitlement review. Loopback HTTP
and CLI arguments are never credential transport.

### 6. Loopback HTTP remains the public data plane; private control stays narrow

The local OpenAI/Anthropic-compatible HTTP surface remains the source of truth for health, routes,
models, readiness, streaming, and operational read APIs. The native app continues to use it.

Configuration and service mutation remain fixed CLI verbs so they work when the gateway is down.
Machine consumers gain versioned JSON/capability output and typed error codes without removing the
existing human stdout/stderr contract. A general HTTP config-write API or an in-process Swift/Rust
router bridge is not added.

### 7. Network policy is explicit and offline claims are enforceable

Provider URLs must be syntactically valid `http` or `https` origins without userinfo or fragments.
Redirects are disabled so credentials cannot be forwarded to a second origin. The configured base
URL remains an operator-controlled destination—private and loopback endpoints are legitimate for
local models—but request-derived URLs cannot select an arbitrary destination.

Offline mode may say “nothing leaves this machine” only when every possible delivery target and
fallback is proven local (loopback or a separately accepted local transport). If that proof fails,
configuration validation or the request fails safely; “cheapest” alone is insufficient. This is an
intentional hardening of the current implementation and requires migration notes and differential
tests that distinguish meaningful rejection from accidental incompatibility.

The server binds to loopback by default. A non-loopback bind remains explicit and must surface a
warning; release policy may require gateway authentication for it after a separate compatibility
decision.

### 8. macOS uses a separately running, universal, signed nested helper

Wayfinder.app remains SwiftUI/AppKit. The Rust executable is nested at a stable bundle path, signed
as nested code before the containing app, and run as the per-user launchd service. App exit does not
stop it; helper failure does not crash the UI process.

Release builds compile `aarch64-apple-darwin` and `x86_64-apple-darwin` with the app's macOS 14
deployment target, verify each slice independently, combine them as a universal Mach-O, then sign,
notarize, staple, and validate the outer artifact. Update and rollback boot out the selected job,
atomically promote a verified helper/app, and re-bootstrap it. They never delete user config,
ledger, cache policy, or Keychain items implicitly.

Bundled, Homebrew, and legacy Python installations share a capability/version handshake and an
explicit backend selection during migration. Only one implementation may own
`com.wayfinder-router.gateway` and port 8088 at a time. Development selection is explicit; Rust
does not become default merely because a binary is present.

### 9. Compatibility is executable and meaningful differences are never normalized away

The compatibility harness runs the same routing/config/CLI/HTTP/stream/state fixtures against both
backends. Normalization is limited to timestamps, generated request IDs, selected free ports, and
temporary paths. Statuses, headers, modes, ordering, error codes, stream event order, config
preservation, and readiness meaning are not nondeterminism.

Shadow comparison never duplicates a real provider call. It uses recorded streams, fake local
providers, or compares the pure decision before a single selected backend performs delivery.

Rust default selection and Python removal are separate future decisions. The default gate includes
the full capability matrix, Python and Rust tests, formatting/clippy/audit/deny, Swift tests,
clean-machine setup, local/hosted/hybrid/offline flows, streaming/cancellation, universal signed
artifacts on real Apple Silicon and Intel Macs, and a tested rollback.

## Consequences

### Positive

- One small helper fits the existing service-first/native-thin-client architecture and is easier to
  sign, update, isolate, and roll back than an embedded Python runtime.
- Pure routing remains independently testable and cannot accidentally acquire network, process, or
  secret dependencies.
- Axum/Tower and Tokio make body limits, cancellation, graceful shutdown, and in-process HTTP
  contract tests explicit rather than incidental framework behavior.
- Typed secret and config seams remove values from serializable/loggable state while preserving
  legacy references.
- Python remains a live oracle and fallback until evidence, not code volume, clears each gate.

### Negative

- During migration the repository carries Python, Swift, JavaScript, and Rust implementations and
  a substantial differential test matrix.
- A one-binary CLI means optional UI/TUI commands either need Rust implementations or an explicit
  coexistence/delegation story before full CLI parity.
- The XPC broker, universal artifact, signing, notarization, update, and real-Intel gates require
  Apple-specific release work beyond a normal Cargo build.
- Security bounds and truthful offline enforcement intentionally reject some currently accepted
  pathological or misleading configurations; each needs a written migration path.

### Risks

- Numeric or regex drift can change routing at a boundary. Golden vectors include raw features,
  rounded score, explanation, and recommendation across Python and Rust.
- Lossless TOML mutation can still race a human edit. Writes use identity/mtime checks and atomic
  replacement; conflict returns an error rather than overwriting newer content.
- XPC peer authentication and Keychain access groups can fail only in signed/headless contexts.
  Real-Mac tests are release gates, not inferred from unit tests.
- Process-local budgets/limits remain insufficient for a multi-worker server. The native helper is
  one process; shared distributed state is a separate architecture decision.

## Alternatives considered

### Separate `wayfinder-gateway` and `wayfinder-router` executables

This gives each binary a smaller command surface, but doubles nested signing/update/discovery,
changes current launchd arguments, complicates rollback, and offers no isolation benefit because
launchd already runs the gateway out of process. Rejected for the production distribution; crate
separation supplies the code boundary.

### Run the Rust gateway inside the Swift UI process

This removes one process but couples gateway uptime and provider crashes to a menu-bar UI, breaks
the accepted launchd service ownership, and makes every non-native client depend on the app being
open. Rejected.

### Hyper directly instead of Axum

Hyper can implement the surface and is already under both Axum and Reqwest, but recreating routing,
extraction, response conversion, body limits, and Tower integration increases compatibility and
security code without a product benefit. Rejected unless a measured release-size or latency gate
later demonstrates a material problem.

### A C ABI linking the Rust core into Swift

The gateway is the source of truth and the native app is a thin client. An in-process decision ABI
would create another lifecycle and parity surface without serving headless clients. Rejected.

### Put provider secrets in environment variables injected by the app

Environment values are inherited at process launch and are awkward to rotate; an app-owned launch
path would also compete with launchd. Provider secrets are never placed in CLI arguments or files,
and production credential brokering stays a narrow authenticated seam. Rejected.

### Delete Python as each module is ported

This removes the differential oracle and rollback path before parity is proven. Rejected. Python
removal requires a separate reviewed decision after Rust is the demonstrated default.

## Verification gates

```text
cargo fmt --check --manifest-path rust/Cargo.toml
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --all-features -- -D warnings
cargo test --manifest-path rust/Cargo.toml --workspace --all-features
cargo test --manifest-path rust/Cargo.toml --doc --workspace
cargo audit --file rust/Cargo.lock
cargo deny --manifest-path rust/Cargo.toml check
python -m pytest -q
python compatibility tests
swift test (macos/WayfinderMac)
universal release build + codesign/spctl/stapler verification
```

The exact commands evolve with the workspace, but none of the named gate classes may be silently
omitted from a default-readiness recommendation.

## Related

- WF-ADR-0001 (pure deterministic core)
- WF-ADR-0004 (gateway and provider-key boundary)
- WF-ADR-0013 (streaming and hardening)
- WF-ADR-0031 through WF-ADR-0035 (failover, budgets, cache, limits, virtual keys)
- WF-ADR-0038/0039 (service and offline delivery)
- WF-ADR-0042/0044 (thin native client and CLI-owned configuration)
- `docs/rust-migration-capability-matrix.md`
