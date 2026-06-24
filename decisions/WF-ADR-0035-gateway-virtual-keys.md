---
schema_version: 1
id: WF-ADR-0035
type: decision
tags: [gateway, auth, virtual-keys, attribution, budget, rate-limit, finops, invocation]
---

# WF-ADR-0035: Gateway Virtual Keys (hashed-in-config, auth + attribution + per-key scope)

## Status

Accepted

## Category

Technical

## Context

Budgets, the response cache, and rate limiting are all **gateway-wide** — they cap and attribute
the gateway as a whole. To become a team control plane (WF-ROADMAP-0006 #5), the gateway needs an
**identity layer**: scoped credentials issued to teams/apps, so spend *and savings* can be
attributed per caller and caps applied per caller. This is the Wave 2 keystone, and it is also the
auth boundary that per-request control headers (e.g. a cache force-refresh) were waiting on.

Two constraints frame the decision:

- **No secrets in config (WF-ADR-0004).** Wayfinder reads *provider* keys from the environment and
  never writes them to config or disk. A virtual key is a credential the gateway *issues* — it must
  not sit in config as plaintext either.
- **The deterministic core is sacred (WF-ADR-0001).** Auth, attribution, and per-key caps are pure
  invocation-layer concerns — no model call. They gate and label delivery; they never change how a
  prompt is scored.

A virtual key here authenticates access to the *gateway* and labels the request; Wayfinder still
resolves the real provider keys from the environment. Virtual keys are not provider keys.

## Decision

1. **A virtual key is a gateway-issued bearer token, stored only as a SHA-256 hash.** Config holds
   `[gateway.keys.<id>] hash = "<sha256>"` (64-hex) plus optional `tags`; the plaintext is shown
   once at mint time (`wayfinder-router keys new`) and never stored. An incoming `Authorization:
   Bearer <token>` is hashed and matched constant-time against the configured hashes. A leaked
   config exposes no usable credential.

2. **Auth is opt-in and backward-compatible.** When `[gateway.keys]` is non-empty, `/v1/*`
   (chat completions **and** the Anthropic `/v1/messages` adapter) requires a valid key — else
   `401 wayfinder_router_unauthorized` with `WWW-Authenticate: Bearer`. With no keys configured the
   gateway stays open, exactly as before.

3. **Attribution by key.** The resolved key id tags the request: it appears in the `/router/recent`
   decision feed (metadata only), increments `wayfinder_router_key_requests_total{key=…}`, and —
   the FinOps payoff — attributes the turn's realized/baseline/**savings** to that key in the
   ledger, surfaced under `by_key` in `/v1/savings`. Every competitor tracks per-key *cost*; none
   tracks per-key *savings*.

4. **Per-key scope reuses the existing guardrails.** A key may carry its own `[gateway.keys.<id>.budget]`
   and `[gateway.keys.<id>.rate_limit]`, validated by the same parsers as the gateway-wide ones.
   Both the gateway-wide cap and the key's cap apply; the **strictest wins** (a block beats a
   degrade; a request must pass both rate limiters). Per-key budgets read the key's own ledger
   spend; per-key rate limits use a per-key limiter whose window persists across requests.

5. **Hashed-in-config, not a runtime store, in v1.** Keys live in `wayfinder-router.toml` (hot-
   reloaded like the rest of the config) — deterministic, no database, no secret persisted. Runtime
   create/revoke via an admin API is a deliberate later option (WF-ROADMAP-0006 #14), not v1.

6. **Round-trips and hot-reloads.** `[gateway.keys.<id>]` (with nested budget/rate_limit) serializes
   through `dump_gateway_toml` and reloads live, consistent with every other `[gateway.*]` block.

## Consequences

- **The gateway becomes a team control plane**: issue scoped keys, attribute spend & savings per
  team, and cap each — on the invocation layer, with the scored decision untouched.
- **Reuses everything**: the budget and rate-limit machinery key cleanly by virtual key; the ledger
  gained a `by_key` dimension that powers both per-key budgets and per-key attribution.
- **Unlocks the auth boundary** deferred features (cache force-refresh header, per-key rate-limit
  headers) were waiting on.
- **Risk — a leaked config is safe** (only hashes), but a leaked *key* grants gateway access until
  removed; mitigated by easy rotation (mint a new key, swap the hash) and constant-time matching.
- **Limitation — per process.** Per-key limiters and the ledger are in-memory per worker, like the
  breaker/cache; a multi-process deployment scopes per worker until a shared store exists.
- **Limitation — no model allowlist, no runtime CRUD** in v1 (deferred). Auth covers all `/v1/*`
  uniformly; finer per-key route scoping is a follow-up.

## Alternatives Considered

- **Plaintext keys in config** — violates the no-secrets-in-config posture (WF-ADR-0004); a leaked
  config would leak live credentials. Rejected for hashed storage.
- **Env-referenced keys** (each key in its own env var) — works for one or two keys but doesn't
  scale to many teams and couples key rotation to process restarts. Hashed-in-config is simpler.
- **A runtime key store + admin API now** — more powerful (create/revoke without editing config)
  but needs persistence and an auth'd admin surface; deferred to WF-ROADMAP-0006 #14.
- **Delegating auth to an upstream proxy** — splits identity from attribution and the per-key caps;
  rejected, as with the other guardrails.

## Success Measures

- With `[gateway.keys]` configured, an unkeyed or wrong-key request gets `401`; a valid key gets
  `200` and is attributed in `/metrics` and `/v1/savings` `by_key`.
- A key's own budget/rate-limit enforces on top of the gateway-wide cap, strictest-wins.
- No `[gateway.keys]` ⇒ the gateway is open (no behavior change).
- The scored decision is identical with or without keys.

## Related

- WF-ADR-0001 (deterministic core — preserved) / WF-ADR-0004 (provider keys from env — unchanged)
- WF-ADR-0032 (budgets) / WF-ADR-0034 (rate limiting): the caps virtual keys scope per-key
- WF-ADR-0033 (cache): the force-refresh header this auth boundary unblocks
- WF-DESIGN-0007 (savings ledger — gained the `by_key` attribution dimension)
- WF-ROADMAP-0006 (item #5 virtual keys; item #14 runtime admin API)
