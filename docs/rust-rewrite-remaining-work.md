# Rust Rewrite: Remaining Work

Status snapshot: 2026-07-13

This is the ordered execution backlog for reaching full Python parity with a Rust helper natively
embedded in Wayfinder's Swift macOS app. Authoritative contracts remain:

- [`rust-migration-capability-matrix.md`](rust-migration-capability-matrix.md)
- [`WF-ROADMAP-0014-rust-gateway-migration.md`](../roadmaps/WF-ROADMAP-0014-rust-gateway-migration.md)
- [`WF-DESIGN-0016-signed-rust-helper-integration.md`](../designs/WF-DESIGN-0016-signed-rust-helper-integration.md)
- [`apple-foundation-models-handoff.md`](apple-foundation-models-handoff.md)

## Implemented baseline

- Deterministic Rust routing/configuration kernel and compatibility fixtures.
- Bounded Axum gateway and public health/models/profiles/recent/metrics/savings surfaces.
- Buffered and streaming OpenAI-compatible delivery plus inbound Anthropic translation.
- Retry/failover/circuit breakers, offline-locality enforcement, exact cache, budgets, rate limits,
  virtual keys, bounded metrics, recent metadata, and last-good reload.
- Versioned atomic savings persistence with last-good recovery and corruption quarantine.
- Native Rust `route`, `serve`, `service`, and `capabilities`; explicit Python delegation for the
  remaining coexistence commands.
- Swift credential-broker XPC service, bounded macOS Rust XPC client, helper manifest models,
  bundled-helper discovery, and arm64/universal packaging inputs.

Rust remains non-default and reports `gateway_ready: false`.

## P0 — complete the Apple Silicon embedded product path

1. Implement the Apple Foundation Models provider described in
   [`apple-foundation-models-handoff.md`](apple-foundation-models-handoff.md), capability-detected
   on macOS 26 rather than assumed on every Mac.
2. Run authenticated end-to-end credential-broker resolution with a signed arm64 helper and XPC
   service; test missing, denied, locked, rotation, app-closed, wrong-caller, and service-crash cases.
3. Make app startup/setup execute the helper capability command and verify implementation, version,
   commands, config schema, wire contract, architecture, and credential mechanisms against the
   signed manifest. Mismatch must be a visible repair state, never PATH fallback.
4. Build the complete arm64 `.app`, embed the helper and XPC services at stable paths, sign inner
   code before the app with hardened runtime, and validate designated requirements.
5. Finish launchd backend switching so Python/Homebrew and bundled Rust cannot simultaneously own
   `com.wayfinder-router.gateway` or port 8088.
6. Implement signed app update and automatic rollback: stage, verify, quiesce, promote, bootstrap,
   health/capability check, restore previous bundle on failure, and preserve config/ledger/Keychain.
7. Run clean-account Apple Silicon install, interrupted install, restart, login/logout, update,
   forced rollback, Keychain preservation, and app-closed gateway tests.

## P0 — close behavioral and security parity

1. Extend Python/Rust HTTP differentials across buffered/streaming success and every structured
   error/header/alias path, using fake providers only.
2. Complete URL and credential-forwarding evidence: redirect, proxy, DNS rebinding/private-network,
   IPv4/IPv6 loopback, SSRF, userinfo, query/fragment, and origin changes.
3. Prove explicit bounds for request bodies, decoded SSE frames, native-XPC messages, queues,
   accumulators, tool calls, concurrent requests, response buffers, and every timeout.
4. Add sustained cancellation/race/contention tests covering downstream disconnect, provider stall,
   hot reload, accounting, cache, rate limits, budgets, breakers, and shutdown.
5. Complete restart/clock/concurrent-request state tests for savings, budgets, limits, cache, and
   breakers. Document that savings survives restart while cache, rate windows, recent metadata,
   in-memory secrets, and breaker state are deliberately ephemeral unless an accepted contract says
   otherwise.
6. Verify no prompt, secret, key, credential command, response body, tool argument, or XPC payload
   appears in logs, metrics, debug output, argv, environment mutation, persisted state,
   accessibility, crash output, fixtures, or snapshots.
7. Install and run `cargo-deny`; retain `cargo audit`, dependency, license, and secret-scan policy
   evidence.

## P1 — finish CLI and service parity

The coexistence delegation is valid for opt-in testing but is not full Rust/Python removal parity.

1. Expand subprocess differentials for every command's options, exit codes, stdout, stderr,
   malformed input, dry-run behavior, and filesystem/service-manager side effects.
2. Prove install/status/restart/uninstall, interrupted setup, explicit backend switch, and rollback
   are idempotent and preserve user state.
3. Either port or formally retain/deprecate each delegated workflow:
   `calibrate`, `recalibrate`, `init`, `doctor`, `config`, `keys`, `onboard`, and `judge`.
4. Replace Python `ui`, terminal `chat`, and `webchat` with accepted native Swift product surfaces
   or retain a separately shipped Python compatibility package. Do not silently remove them.
5. Resolve stale Python bootstrap assertions and preserve the corrected supported hybrid preset.
6. Make capability/status/error output sufficient for native setup, repair, switching, and rollback
   without Swift parsing human-readable text.

## P1 — hot reload and operational state

1. Reconfigure cache and rate-limit state across config snapshots under documented migration rules.
2. Decide and test breaker migration across threshold/cooldown/model changes; do not accidentally
   carry state to a different provider identity.
3. Preserve shared ledger, metrics, and recent-state continuity exactly where intended.
4. Test corrupt current and last-good ledger files, failed atomic writes, read-only directories,
   clock boundaries, concurrent recording/saving, and restart budget enforcement.
5. Surface sanitized persistence/reload diagnostics through health/metrics without leaking paths or
   contents beyond the accepted diagnostic contract.

## P1 — reproducible performance and release evidence

Benchmark Rust and Python on identical hardware and fixtures for:

- cold start and idle memory;
- routing and config parse/reload latency;
- buffered HTTP overhead;
- first streaming event and sustained streaming throughput;
- concurrent buffered/streaming requests and cancellation;
- Apple Foundation Models first response, sustained generation, cancellation, and service restart.

Then produce warning-free release builds and run Rust, Python, Swift, compatibility, security,
documentation, and packaging checks from a clean environment. Add explicit Rust opt-in and a
shadow-safe pure-decision comparison period; never duplicate real provider requests.

## P2 — broader distribution evidence

Apple Silicon is the immediate product lane. Before claiming the originally accepted universal
release gate:

1. Build and test a real `x86_64-apple-darwin` helper and Swift app/XPC slices.
2. Combine with `lipo` and verify metadata/capabilities match both thin artifacts.
3. Run install, streaming, cancellation, restart, update, rollback, and Keychain tests on physical
   Intel hardware; Rosetta is not Intel evidence.
4. Complete Developer ID signing, notarization, stapling, Gatekeeper validation, and clean-machine
   recovery for the distribution artifact.
5. Finalize Homebrew formula/cask and container coexistence while retaining the Python package as
   rollback until removal is separately approved.

## Phase 7 scenarios required before recommendation

Demonstrate and link evidence for:

- Apple Foundation Models available/not-ready/ineligible;
- Ollama/manual local-only;
- hosted-only and hybrid;
- offline and attempted remote delivery;
- missing, locked, denied, and rotated credentials;
- cache hit/miss/expiry;
- global and per-key budget/rate limits;
- virtual-key allowlists and attribution;
- buffered and streaming OpenAI/Anthropic clients;
- cancellation before and after first byte;
- config reload success/failure;
- restart with persistent savings/budget enforcement;
- interrupted setup/update and rollback;
- bundled Rust ↔ Python/Homebrew switching with one label/port owner.

Every capability-matrix row must have direct evidence or an accepted deprecation/migration note.

## Default and Python-removal policy

Rust default selection is a separate reviewed Phase 8 decision after Phase 7 evidence. Python
removal is later, after real releases prove default operation and rollback.

Until then:

- preserve Python implementation and packaging;
- keep `gateway_ready: false`;
- make Rust activation explicit and reversible;
- never let both backends own port 8088 or the launchd label;
- record intentional compatibility changes in an ADR and differential fixtures.

## Verification commands

From `wayfinder-router/rust`:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked --offline -- -D warnings
cargo test --workspace --locked --offline
cargo audit --no-fetch
```

From `wayfinder-router/macos/WayfinderMac`:

```bash
swift test
```

Two gateway listener lifecycle tests can fail with `PermissionDenied` in restricted sandboxes. Run
them on a host permitting loopback binding; do not count a sandbox skip as product evidence.
