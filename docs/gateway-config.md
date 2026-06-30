# Gateway configuration reference

The operator knobs for `wayfinder-router serve` — timeouts, observability, reliability
and failover, budget, cache, rate limiting, and virtual keys. The [README](../README.md)
covers the deploy architecture (gateway as a service or sidecar, the one-`base_url`
change); this is the settings reference.

Most settings live in `wayfinder-router.toml` under `[gateway]` (and its sub-tables);
a few are environment variables or `serve` flags, noted inline. The routing decision
itself stays deterministic and offline — none of these touch the scored path.

## Basics

| setting | effect |
| --- | --- |
| `WAYFINDER_ROUTER_TIMEOUT` / `serve --timeout` | upstream timeout in seconds (default 60) |
| `WAYFINDER_ROUTER_FEEDBACK_TOKEN` | when set, `/v1/feedback` requires `Authorization: Bearer <token>` |
| `serve --dry-run` | return routing decisions without calling any upstream |

## Observability

| setting | effect |
| --- | --- |
| `GET /healthz` | reports `degraded` and lists `missing_keys` when a configured `api_key_env` is unset |
| `GET /router` | read-only dashboard of recent decisions, with `X-Wayfinder-Debug: true` surfacing one in the body |
| `GET /v1/savings?period=today\|7d\|30d\|all` | realized vs always-frontier cost and the savings between them, per route (WF-DESIGN-0007) |
| `WAYFINDER_ROUTER_SAVINGS_FILE` | where the savings ledger is persisted (default `<config-dir>/wayfinder-savings.json`) |

## Reliability and failover

| setting | effect |
| --- | --- |
| `[gateway] retries` / `breaker_threshold` / `breaker_cooldown` | reliability: bounded retries on transport/`429`/`5xx`, and a per-target circuit breaker (WF-ADR-0031) |
| `[gateway] failover = same-tier\|degrade\|escalate` | on exhaustion, stay on the tier (default), fall to a cheaper one (never raises cost), or a dearer one (opt-in); per-request `X-Wayfinder-Failover` |
| `[gateway.models.<name>] fallbacks = [...]` / `context_window` | same-tier endpoints to try on failure; skip a target whose window can't fit the prompt. Responses carry `x-wayfinder-router-served-by` |

## Budget

| setting | effect |
| --- | --- |
| `[gateway.budget] limit` / `window = day\|month\|all` / `on_breach = degrade\|block` | spend cap: once `limit` realized cost is reached, degrade to the cheapest tier (default, never raises cost) or block with HTTP 402. Surfaced via `x-wayfinder-router-budget`; needs real `cost_per_1k` prices (WF-ADR-0032) |

## Cache

| setting | effect |
| --- | --- |
| `[gateway.cache] enabled` / `ttl` / `max_entries` / `max_bytes` | exact-match response cache: replay a stored answer for an identical deterministic request — instant, free repeats. Off by default; in-memory only; raise `max_bytes` (default 64 MiB) for more. A hit is free and surfaced via `x-wayfinder-router-cache: hit\|miss`; disabling purges it (WF-ADR-0033) |

## Rate limiting

| setting | effect |
| --- | --- |
| `[gateway.rate_limit] rpm` / `tpm` / `window` | cap requests-per-minute and/or upstream-tokens-per-minute over a fixed `window` (default 60s); on breach returns `429` with `Retry-After`. The outermost guardrail (checked before scoring); gateway-wide. Successful responses carry `X-RateLimit-Limit`/`-Remaining`/`-Reset` so clients can self-pace; surfaced via `x-wayfinder-router-rate-limit` and `wayfinder_router_rate_limited_total` (WF-ADR-0034) |

## Virtual API keys

| setting | effect |
| --- | --- |
| `[gateway.keys.<id>] hash` / `tags` / `models` (+ nested `budget` / `rate_limit`) | virtual API keys: when any is set, `/v1/*` requires a valid `Authorization: Bearer` token (else `401`). Mint with `wayfinder-router keys new`; only the SHA-256 hash is stored. Spend & **savings** are attributed per key (`by_key` in `/v1/savings`, `wayfinder_router_key_requests_total`); a key can carry its own budget/rate-limit (strictest wins) and a `models` allowlist (clamps to the nearest allowed tier) (WF-ADR-0035) |
