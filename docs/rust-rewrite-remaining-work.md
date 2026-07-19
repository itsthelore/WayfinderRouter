# Rust Rewrite: Remaining Work

Status updated: 2026-07-19

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
- Native Rust `route`, `serve`, `service`, `capabilities`, `app-setup-init`, and Desktop-owned
  `config read-routing` / `config apply-routing`; explicit Python delegation for the remaining
  coexistence commands and config actions.
- Swift credential-broker and Foundation Models XPC services, bounded macOS Rust XPC clients, helper
  manifest/capability validation, bundled-helper discovery, and arm64 release packaging.
- Native Desktop Chat and the opt-in isolated ChatGPT account provider through verified
  `/Applications/ChatGPT.app`.

Desktop v0.1.0 explicitly selects the verified embedded Rust gateway. The standalone migration
metadata remains conservative and reports `gateway_ready: false`; Python remains runnable and is not
removed.

## P0 — finish the Apple Silicon Desktop v0.1.0 release gate

The provider, authenticated XPC boundaries, architecture/version handshake, embedded arm64 app,
launchd coexistence, and deterministic ad-hoc bundle verification are implemented.

1. Build with the real Developer ID Application identity, notarize, staple, and verify the final
   extracted ZIP.
2. Run clean-account Apple Silicon install, interrupted setup, restart, login/logout, manual
   replacement, failure-triggered rollback, Keychain preservation, and app-closed gateway tests.
3. Re-run Apple Foundation Models delivery and ChatGPT/Sol isolation evidence against that exact
   signed production artifact.
4. Complete the native fidelity and accessibility checklist with no open P0/P1 findings.

Desktop v0.1.0 has no automatic updater. Replacement and rollback operate on a complete verified app
bundle and never delete config, history, ledger/cache state, or Keychain items.

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
