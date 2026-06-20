---
schema_version: 1
id: WF-ADR-0025
type: decision
tags: [gateway, demo, keys, security, observability]
---

# WF-ADR-0025: Read-Only Models / Key-Status Surface (No Key Entry in the UI)

## Status

Accepted

## Category

Technical

## Context

Model API keys are configured by **environment variable**: `[gateway.models.*]` names an
`api_key_env`, and the gateway reads it at request time and sends `Authorization: Bearer …`. Keys
never live in the config file or the app — the example config says so explicitly, and it is a
deliberate security posture. The gap was *discoverability*: a user couldn't see which models are
wired or which keys are missing without curling `/healthz` (which reports a bare `missing_keys`
list). The question raised was whether the demo should let users *configure* keys.

## Decision

Do **not** add key entry to the UI; add a **read-only** status surface instead.

1. `GET /router/models` returns, per configured model: `name`, `endpoint` (base_url), upstream
   `model`, the `api_key_env` **name**, and a boolean `key_ok` (`True` when no key is required or the
   named env var is set). It returns the env-var *name* and a boolean only — **never the secret**.
2. The demo's Settings popover gains a read-only **Models** section: a dot per model (green = ready,
   amber = key missing), its endpoint host, and `ENV_VAR ✓ / · missing`. A `?` explains that keys
   live in the environment — set the named var and restart; they are never entered here.

Key *entry* in the browser was rejected: it would push a secret through the web app to be stored
server-side, which is exactly what the env-var posture avoids. Self-hosters set the env var; the UI
only reflects whether it is present.

## Consequences

### Positive

- The "what do I configure?" question is answerable in the UI: you see the missing env var's name
  and which endpoint needs it, without any secret leaving the environment.
- Pure and safe: a read-only view of config + `os.environ` presence, off the scored path, no secret
  in the response (covered by a test asserting the value never appears).

### Negative / Risks

- Not a setup wizard — you still set env vars and restart. That is the intended boundary, not a gap.
- `key_ok` only checks presence, not validity; a wrong key still 401s upstream at call time.

## Alternatives Considered

- **Editable key entry stored server-side.** Rejected: secrets through the web app, against the
  keys-in-env posture; the blast radius and persistence questions aren't worth the convenience.
- **Reuse `/healthz`.** It reports only names/`missing_keys`; the UI wants endpoints and per-model
  status, so a dedicated read-only endpoint is cleaner. `/healthz` stays the ops health check.
- **Docs only.** Helpful but undiscoverable in the moment; the status surface complements the docs.

## Related Decisions

- WF-ADR-0004 (the gateway / env-var keys), WF-ADR-0023 (Export config — the other "make it real"
  surface), WF-ADR-0020 (the demo), WF-ADR-0018 (`/healthz`, the ops check this complements)
