# Wayfinder Rust migration workspace

This is the additive, parity-gated Rust implementation selected by WF-ADR-0045. Desktop v0.1.0
explicitly selects this gateway as the arm64 helper embedded in `Wayfinder.app`. The independently
distributed router keeps Python as its compatibility oracle and retained fallback. The
`gateway_ready: false` handshake remains conservative cross-distribution migration metadata; it
does not override the Desktop product's explicit verified-helper selection.

## Verified checkpoint (2026-07-11)

- `wayfinder-core` implements deterministic feature extraction, scoring, explanations, tiers,
  classifier inference, schema-version-3 decisions, and all ten stock lexicon profiles.
- `wayfinder-config` implements routing and gateway TOML discovery, parsing, validation, emission,
  and line-preserving supported mutations. Strict ascending tier order matches the current Python
  product contract; legacy sorting remains available only for explicit migration tooling.
- `wayfinder-gateway` provides bounded Axum health, metrics, model/profile/recent/savings/config,
  dry-run/decision-only chat, and buffered OpenAI-compatible delivery. Request-scoped tuning,
  route scope, sticky routing, slash directives, offline fail-closed delivery, prompt-free recent
  state, in-memory cost accounting, constant-time virtual-key authentication, model allowlists,
  global/per-key RPM and TPM, global/per-key spend budgets, rate and budget headers, key
  attribution, the opt-in bounded exact-response cache, and buffered retry/fallback/circuit-breaker
  delivery are covered. Buffered Anthropic `/v1/messages` and `/messages` requests reuse that same
  chat path. Request-atomic last-good config reload, bounded graceful drain, and real-process HTTP
  lifecycle coverage complete the Phase 2 skeleton. Phase 3 adds pre-commit upstream status
  handling, pre-first-byte plan failover, cancellation-safe OpenAI SSE, and incremental Anthropic
  Messages SSE translation. Several operational policies are not yet fully parity-gated, so this is
  not a replacement gateway.
- `wayfinder-providers` provides a hardened Reqwest buffered client, bounded SSE decoding,
  reliability policy primitives, and buffered plus streaming Anthropic translation. Streaming
  translation is tested as a state machine but is not yet connected to the HTTP transport.
- `wayfinder-service` provides deterministic pricing/ledger logic, bounded legacy command-secret
  resolution, and byte-compatible launchd/systemd unit rendering.
- `wayfinder-cli` implements `route`, real `serve`, `service install|uninstall|status`, the bounded
  Desktop setup/config operations, and the versioned capability handshake. `config read-routing`
  and `config apply-routing` are native; other coexistence config actions remain Python-delegated.
  Environment credentials take precedence over bounded startup `api_key_cmd` compatibility values;
  neither path serializes or logs the secret.
- `wayfinder-compat-tests` currently passes ten integration tests covering 21 Python golden
  prompts, eight routing boundaries, 32 routing-config cases, 74 gateway-config cases, 20 ordered
  HTTP exchanges, and byte-exact service-unit output. The seeded differential runner has also
  passed 506 generated prompts.

Focused checkpoint evidence:

```sh
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets \
  --all-features --locked --offline -- -D warnings
cargo test --manifest-path rust/Cargo.toml -p wayfinder-compat-tests \
  --all-features --locked --offline
cargo test --manifest-path rust/Cargo.toml -p wayfinder-providers \
  --locked --offline anthropic::tests
cargo audit --file rust/Cargo.lock --no-fetch
```

The complete workspace suite contains temporary-loopback tests and therefore needs permission to
bind local sockets in restricted environments. `cargo-audit` is installed and the current lockfile
has no known advisory; `cargo-deny` and the x86_64 macOS target are still unavailable locally.

## Standalone migration remains parity-gated

The broader standalone migration still requires retained/delegated CLI decisions and complete
cross-platform evidence before Python removal. Desktop v0.1.0 has a separate Apple Silicon-only
release gate in WF-ROADMAP-0015; x86_64 and universal packaging are future claims, not blockers for
that release.
