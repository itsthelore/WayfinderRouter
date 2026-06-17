---
schema_version: 1
id: WF-ADR-0007
type: decision
tags: [recalibration, feedback, gateway, hot-reload]
---

# WF-ADR-0007: Scheduled Recalibration and Hot-Reload

## Status

Accepted

## Category

Architecture

## Context

WF-ADR-0006 recorded "scheduled recalibration" as a forward direction: the
feedback log keeps growing (from `wayfinder onboard` and the gateway's
`/v1/feedback`), so the routing config should be re-fit from it periodically and
the running gateway should pick up the new config. Two gaps stood in the way: the
gateway loads its config **once at startup** (so a re-fit would not take effect
without a restart), and there was no single command to re-fit.

The judging of trade-offs (CLI/cron vs an in-gateway auto-trigger vs a UI button)
is the substance of this decision.

## Decision

Add a deterministic recalibration unit, two human-/operator-driven triggers, and
gateway hot-reload. **No automatic recalibration runs inside the serving process.**

- **The recalibrate unit** (`recalibrate(log_path, config_path, mode, min_labels)`)
  reads the whole label log and re-fits via the existing `load_dataset` +
  `calibrate` path — the deterministic batch replay WF-ADR-0006 described. It is a
  no-op below `min_labels`. It rewrites **only the `[routing]` section** and
  **preserves the `[gateway]` section** (the endpoint mapping and `api_key_env`
  *names* — never a secret), with a `# recalibrated from feedback: …` header for
  traceability. Pure orchestration; no model call.
- **Triggers:** `wayfinder recalibrate` (the schedulable unit — run it from cron,
  a systemd timer, or a k8s CronJob) and a **UI "Recalibrate & save" button**.
  Both just write `wayfinder.toml`. Both keep a human or an operator in the loop.
- **Gateway hot-reload:** the gateway caches its config and re-reads
  `wayfinder.toml` when the file's mtime changes, so any recalibration takes effect
  live with no restart. A malformed mid-flight write keeps the last-good config
  (the mtime marker advances so it is not retried every request) rather than
  failing serving.
- **Rejected: an in-gateway auto-recalibrate** (re-fit every N feedback events
  inside the request path).

## Consequences

### Positive

- The collect → calibrate → route loop now closes without a restart: re-fit
  offline (CLI/cron) or with one click (UI), and the gateway picks it up.
- Recalibration stays **out of the serving path**: a fit can't add request latency
  or take the gateway down, and the gateway remains a pure forwarder (WF-ADR-0004).
- Deterministic and safe under multiple replicas — each reads the same committed
  `wayfinder.toml`; nothing is re-fit independently per instance.

### Negative

- Time-based cadence means a lag between new feedback and a re-fit (the operator
  owns the cadence). The UI button covers the "do it now" case.
- Hot-reload stats the config file per request (sub-millisecond; negligible
  against a forwarded model call).

## Alternatives Considered

### In-gateway auto-recalibrate every N feedback events

Re-fit inside `/v1/feedback` when the label count crosses a threshold.

#### Disadvantages

- Runs a fit in the serving process (latency/availability risk), is **not
  multi-instance safe** (each replica re-fits independently and they diverge), and
  silently shifts the router with no human in the loop. Rejected for the same
  determinism/purity reasons as the rest of the design.

### Restart the gateway to pick up a new config

#### Disadvantages

- Operationally heavy and drops in-flight requests; mtime hot-reload is free and
  live.

## Success Measures

- `wayfinder recalibrate` on a balanced log writes a config that routes the
  labeled prompts the labeled way, with the `[gateway]` section intact and no
  secret in the file.
- A running gateway routes the new way on the next request after the config file
  changes, with no restart; a malformed write keeps it serving the last-good config.
- No recalibration path runs inside a request handler.

## Related

- Extends WF-ADR-0006 (feedback loop); builds on WF-ADR-0003 (calibration) and
  WF-ADR-0004 (gateway). The UI button extends WF-ADR-0005.
