# Wayfinder Rust workspace

Rust is Wayfinder's sole production router and gateway runtime under
WF-ADR-0046. This workspace contains:

- `wayfinder-core` — deterministic feature extraction, scoring, tiers, and
  explanations;
- `wayfinder-config` — typed routing/gateway configuration and preserved
  mutations;
- `wayfinder-providers` — bounded provider clients and streaming translation;
- `wayfinder-gateway` — Axum data plane, policy, reliability, limits, metrics,
  and provider orchestration;
- `wayfinder-service` — service units, pricing/ledger logic, and secret seams;
- `wayfinder-cli` — the `wayfinder-router` executable;
- macOS XPC clients for credentials and Apple Foundation Models;
- checked fixtures retained as immutable migration evidence.

The runtime never launches or delegates to Python. Unsupported legacy commands
fail closed.

## Build and test

```sh
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked
cargo clippy --manifest-path rust/Cargo.toml \
  --workspace --all-targets --all-features --locked -- -D warnings
```

Build the executable:

```sh
cargo build \
  --manifest-path rust/Cargo.toml \
  --package wayfinder-cli \
  --bin wayfinder-router \
  --locked
```

## Apple-platform direction

Wayfinder Desktop continues to embed `wayfinder-router` as a separately running
signed helper. Native iPhone and iPad v0.2 does not run that executable or an
internal HTTP server.

WF-ADR-0048 extracts a pure `wayfinder-routing-core` and typed runtime contracts
from this workspace. The gateway and generated Swift bridge will consume the
same core and golden fixtures. The pure core may not perform filesystem,
Keychain, process, provider, HTTP-server, UI, or Apple-framework work.

The extraction, bridge/XCFramework, provider execution choice, iOS shell, auth,
Apple model, and pairing remain separate pull requests under
WF-ROADMAP-0016.

## Governing documents

- `decisions/WF-ADR-0046-rust-only-runtime.md`
- `decisions/WF-ADR-0048-shared-routing-core-apple-embedding.md`
- `roadmaps/WF-ROADMAP-0014-rust-gateway-migration.md`
- `roadmaps/WF-ROADMAP-0016-native-mobile-v0.2.md`
- `docs/apple-platform-capability-matrix.md`
