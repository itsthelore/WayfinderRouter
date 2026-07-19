---
schema_version: 1
id: WF-ROADMAP-0014
type: roadmap
tags: [rust, migration, gateway, router, compatibility, macos, security, packaging]
---

# Roadmap: migrate Wayfinder's router and gateway to a parity-gated Rust helper

## Status

In progress. Discovery and architecture are complete; Rust is not the default backend.

> Desktop v0.1.0 release amendment (WF-ROADMAP-0015): the native desktop product explicitly embeds
> and selects the Rust gateway, but only as a thin arm64 helper on Apple Silicon. This does not select
> Rust as the default for standalone, Homebrew, container, or PyPI installations and does not permit
> Python removal. Universal/Intel distribution remains a later migration gate.

## Objective

Deliver a production-ready Rust implementation of Wayfinder's deterministic router, local gateway,
provider translation, operational controls, CLI/service surface, and signed macOS helper while
keeping the Python implementation runnable until every compatibility, security, packaging, and
rollback gate passes.

The authoritative inventory and risk register are
`docs/rust-migration-capability-matrix.md`. The workspace and helper architecture are accepted in
WF-ADR-0045; the native process/credential/update boundary is WF-DESIGN-0016.

## Invariants

- The scored decision path is offline, deterministic, keyless, and free of network/process/UI
  dependencies.
- SwiftUI/AppKit owns native UI, consent, and Apple lifecycle. The gateway remains the source of
  truth for health, routes, models, readiness, and provider delivery.
- Existing configuration is never overwritten implicitly. Explicit mutations are lossless for
  content outside their owned field/table and conflict rather than clobber a newer human edit.
- Secret values never enter argv, files, logs, metrics, telemetry, crash reports, fixtures,
  snapshots, serializable Rust state, or config.
- Offline is the only mode allowed to claim no egress, and only when the delivery closure is proven
  local.
- Python and Rust do not both send the same real provider request for comparison.
- Python removal and Rust-default selection are separate reviewed decisions.

## Delivery phases

### Phase 0 — discovery and accepted architecture

Deliver:

- repository/worktree baseline;
- complete capability matrix and hidden-contract inventory;
- risk register and bounded workflow split;
- Rust workspace/helper ADR;
- signed-helper/XPC/update design;
- baseline scorer/Swift verification evidence.

Exit:

- no broad Rust implementation predates the behavioral contract;
- dirty user changes are named and preserved;
- unresolved behavior/security decisions are explicit rather than silently guessed.

Status: complete.

### Phase 1 — deterministic kernel and differential harness

Status: complete. Core routing, semantic routing config, document-preserving supported mutation,
the initial `route` CLI, 29 checked-in decision vectors, 32 config vectors, and deterministic
synthetic subprocess differentials pass. Strict ascending tier order is the accepted product
policy because it matches the current Python parser; legacy sorting is migration-only.

Deliver:

- `wayfinder-core` with feature extraction, scalar score, tiers, classifier inference, decision
  schema, and explanations;
- `wayfinder-config` routing semantic parser and document-preserving mutation foundation;
- initial `wayfinder-cli route` surface;
- generated JSON vectors and a harness that runs identical inputs through Python and Rust;
- boundary/property tests for Unicode, line endings, fences, frontmatter, regexes, float rounding,
  inclusive cuts, classifier ties, and invalid config shapes.

Exit:

- raw features, rounded score, recommendation, explanation, and decision JSON match Python;
- valid/invalid routing config outcomes match the accepted tier-order policy;
- `cargo fmt`, warning-denied clippy, unit/doc tests pass;
- existing files are read-only throughout differential tests.

### Phase 2 — bounded HTTP compatibility skeleton

Status: complete. The bounded Axum surface passes the 20-case Python HTTP fixture corpus; the
real-process loopback harness covers aliases, malformed input, last-good hot reload, and SIGTERM.
Drain timeout and cancellation are deterministic under a paused Tokio clock without sockets.

Deliver:

- Tokio/Axum lifecycle with loopback default, request limits, structured redacted errors, and
  graceful SIGINT/SIGTERM shutdown;
- health, model discovery/status, profiles, recent, metrics, savings, and dry-run/zero-model chat;
- hot-reload snapshots retaining last-good state;
- local in-process and subprocess HTTP contract harness.

Exit:

- status/body/header/ordering/alias fixtures match Python except documented intentional hardening;
- malformed and oversized input cannot panic or allocate without bound;
- cancellation and drain deadlines are deterministic under paused clocks/fake clients.

### Phase 3 — providers and streaming

Status: complete. OpenAI-compatible transport enforces explicit origin, no redirects/proxies,
finite deadlines, and bounded buffered bodies. OpenAI and Anthropic streams are incremental and
bounded; upstream establishment/status precedes a successful downstream commit, ordered-plan
failover is limited to pre-first-byte transport/retryable failures, and downstream cancellation
drops upstream without accounting or breaker failure. The unsupported Anthropic cloud URL was
removed from the default hybrid preset in favor of an actually OpenAI-compatible OpenAI endpoint.

Deliver:

- OpenAI-compatible outbound client with explicit origin, redirect/proxy, timeout, and credential
  policy;
- incoming Anthropic Messages request/response adapter;
- explicit OpenAI and Anthropic SSE state machines;
- fake providers covering fragmented/malformed frames, upstream HTTP errors, stalls, disconnects,
  backpressure, parallel tools, missing terminators, and pre-first-byte failover;
- native Anthropic upstream decision/implementation or explicit supported-preset correction.

Exit:

- buffered and streaming event sequences pass differential fixtures;
- no upstream error is mislabeled as a successful 200 stream;
- downstream disconnect cancels upstream work and leaves breaker/accounting state defined;
- every frame, accumulator, queue, and timeout is bounded.

### Phase 4 — operational controls and persistence

Deliver:

- retries/backoff/circuit breaker/delivery planning;
- offline-locality enforcement;
- exact-match bounded cache;
- pricing/savings ledger with tolerant versioned persistence;
- global and per-key budgets/rate limits;
- virtual-key constant-time authentication and allowlists;
- Prometheus metrics and recent metadata with bounded/cardinality-safe labels;
- concurrency, clock, persistence-corruption, and secret/prompt-redaction tests.

Exit:

- every operations row in the capability matrix has differential or intentional-change evidence;
- concurrent requests cannot violate documented synchronization guarantees;
- corrupt state is quarantined or rejected without losing the last good file;
- no body/secret appears in default observability.

### Phase 5 — CLI and service compatibility

Status: complete for the coexistence period. `route`, `serve`, `service`, and `capabilities` are
native Rust commands. `calibrate`, `recalibrate`, `webchat`, `ui`, `chat`, `onboard`, `judge`,
`init`, `doctor`, `config`, and `keys` are explicitly delegated to
`python3 -m wayfinder_router.cli`, with inherited standard streams and exit status. The capability
handshake reports native and delegated ownership separately; subprocess differentials cover every
delegated help contract and a representative parse failure. Python remains installed by design
until the later removal decision.

Deliver:

- all current commands/options/exit codes/stdout/stderr contracts, or an explicitly documented
  retained-Python delegation for UI/TUI commands during coexistence;
- presets, doctor/readiness, keys, feedback/recalibration/onboarding/judging;
- launchd and systemd-user unit goldens plus idempotent/recoverable manager adapters;
- machine-readable capabilities/status/errors for the native app;
- subprocess differential tests rather than only in-process command tests.

Exit:

- command matrix passes on supported platforms;
- no service/config operation mutates external state during `--print`/dry-run tests;
- backend switch and rollback preserve config/user state and leave one label/port owner.

### Phase 6 — native helper, packaging, and release path

Status: Apple Silicon implementation path complete; signed-platform exit evidence remains pending. The
SwiftPM app now includes a fixed resolve-only Security.framework credential broker, a bounded
macOS-only Rust XPC client wired as the mandatory credential source for bundled helpers, typed helper
manifest/capability validation, bundled-helper-first discovery, production plist/entitlement
inputs, and an inner-to-outer build/sign/notarize pipeline. Desktop v0.1.0 requires a thin arm64
artifact plus physical Apple Silicon install/update/rollback evidence and release identities.
Universal assembly and physical Intel evidence remain required only before a later universal claim.

Deliver:

- arm64 nested helper for Desktop v0.1.0; universal arm64/x86_64 packaging is deferred;
- Swift XPC credential broker and authenticated Rust client, or a separately accepted alternative;
- stable helper manifest/capability/version handshake;
- production app target, entitlements, nested signing, notarization/stapling, update, rollback;
- Homebrew formula/cask coexistence contract and retained Python distribution;
- clean-machine scripts/checklists for setup, interrupted setup, restart, update, and rollback.

Exit:

- signed real-Mac Apple Silicon tests in WF-DESIGN-0016 pass for Desktop v0.1.0;
- physical Intel tests pass before any later Intel or universal-support claim;
- Swift tests remain green and the native app never computes routes/authors config/supervises the
  gateway;
- no secret appears in argv/logs/state/accessibility/crash output;
- existing configuration and Keychain items survive update and rollback.

### Phase 7 — reproducible evidence and Rust opt-in

Deliver:

- cold-start, idle-memory, decision-latency, HTTP-overhead, streaming-first-forward,
  concurrent-stream, and config-parse benchmarks against Python on the same machine/fixtures;
- warning-free release builds, rustfmt, clippy, tests/docs, audit, deny, secret scans, and dependency
  policy;
- Rust opt-in for tests/development plus shadow-safe pure-decision comparison;
- supported-platform, migration, Homebrew/container, release, and rollback documentation;
- final capability matrix with evidence links and remaining gaps.

Exit:

- Python and Rust gates both pass from clean environments;
- local-only, hosted, hybrid, missing-key, cache, budget, rate, vkey, offline, streaming, interrupted
  setup, restart, and rollback scenarios are demonstrated;
- remaining gaps are zero or accepted deprecations with migration notes.

### Phase 8 — default and eventual removal decisions

Rust becomes default only through a separate reviewed decision using Phase 7 evidence. Python
removal is a later, separate decision after real releases prove default operation and rollback.

## Parallel workflow ownership

One integration owner controls architecture, shared types, fixture schemas, compatibility policy,
and merge cadence. Parallel work is bounded to:

- routing/core and numeric vectors;
- config/document preservation and invalid corpus;
- HTTP schemas/lifecycle;
- providers/streaming state machines;
- operational state/security;
- CLI/service contracts;
- macOS packaging/credential broker;
- independent compatibility/security review.

Each workflow returns code, tests, fixtures, benchmark data, or a concrete review report. No
workflow may independently change a public schema, security posture, distribution owner, or
normalization rule.

## Progress reporting

Progress is reported as capability rows with passing evidence, not Rust line count. The readiness
summary always states:

- verified parity count and total;
- intentional changes and migration notes;
- Python/Rust/Swift/tooling command outcomes;
- signed-platform evidence actually run;
- remaining gaps and their severity;
- whether Rust is recommended for opt-in, default, or neither.

## Current recommendation

Proceed with the signed Apple Silicon desktop release gates in WF-ROADMAP-0015. The bundled Rust
gateway is selected only inside that desktop product; broader standalone default selection and
Python removal remain future reviewed decisions.

## Related

- WF-ADR-0045
- WF-DESIGN-0016
- WF-ROADMAP-0015
- `docs/rust-migration-capability-matrix.md`
