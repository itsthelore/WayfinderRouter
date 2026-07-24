# Wayfinder

Wayfinder is a local-first model router and native macOS chat app. It scores each
request locally, chooses a configured destination, and keeps delivery policy
separate from the application making the request.

The production router is implemented in Rust. Wayfinder Desktop embeds that
router inside the signed application bundle; no Python runtime, package, or
fallback is required.

## Products

### Wayfinder Desktop

The native Swift macOS app provides:

- conversation-first Chat with locally persisted history;
- automatic or pinned model selection;
- Apple Foundation Models delivery on eligible Apple Silicon Macs;
- opt-in ChatGPT account routing through a separately verified provider;
- OpenAI-compatible and Anthropic-compatible local gateway endpoints;
- native setup, connection, routing, privacy, and diagnostic surfaces.

Desktop releases use SemVer and `desktop-v*` tags. See
[`macos/WayfinderMac/Packaging/RELEASE.md`](macos/WayfinderMac/Packaging/RELEASE.md).

### Rust gateway

The Rust workspace contains the deterministic scoring core, configuration
parser, provider clients, bounded HTTP gateway, service integration, native XPC
clients, and command-line helper.

Build it with:

```sh
cargo build \
  --manifest-path rust/Cargo.toml \
  --package wayfinder-cli \
  --bin wayfinder-router \
  --locked
```

Then run:

```sh
rust/target/debug/wayfinder-router route "Summarise this request"
rust/target/debug/wayfinder-router serve --host 127.0.0.1 --port 8088
```

The gateway exposes:

- OpenAI-compatible: `http://127.0.0.1:8088/v1`
- Anthropic-compatible: `http://127.0.0.1:8088`
- Health: `http://127.0.0.1:8088/healthz`

The scored decision remains offline, deterministic, and keyless. Credentials
are resolved only for delivery after the route is chosen.

## Container

```sh
docker build -t wayfinder-router .
docker run --rm -p 8088:8088 \
  -v "$PWD/wayfinder-router.toml:/data/wayfinder-router.toml:ro" \
  wayfinder-router
```

The image is built from the Rust workspace and contains only the native gateway
plus its runtime certificates.

## Verification

```sh
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked
cargo clippy --manifest-path rust/Cargo.toml \
  --workspace --all-targets --all-features --locked -- -D warnings
swift test --package-path macos/WayfinderMac
node clients/shared/test/parity.mjs
```

## Repository map

```text
rust/                    native router, gateway, providers, and service crates
macos/WayfinderMac/      native Swift macOS app and release packaging
clients/                 retained thin-client contract code and fixtures
decisions/               architecture decisions
designs/                 product and interaction contracts
roadmaps/                delivery plans and closeout records
docs/                    operational and release documentation
```

Wayfinder is licensed under Apache-2.0.
