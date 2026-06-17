---
schema_version: 1
id: WF-ADR-0005
type: decision
tags: [ui, explain, calibration, configure]
---

# WF-ADR-0005: Calibration / Explain / Configure UI

## Status

Accepted

## Category

Architecture

## Context

The router is a structural *proxy*, and tuning it — picking a threshold, weights,
or tiers, and understanding *why* a prompt routed where — is iterative. The CLI
and JSON serve scripts and agents, but a human calibrating the cut wants to see a
prompt's score broken down, sweep a threshold against labeled data, and edit the
config with immediate feedback.

A design council established the shape: this surface is **for calibration,
explainability, and configuration — never for routing**. Routing stays headless
(library / CLI / gateway, WF-ADR-0004); a UI on the routing path would only add
latency and operational weight. So the UI is a *consumer* of the deterministic
core, not a new place where logic lives.

## Decision

Add a local **calibration / explain / configure UI** as a thin consumer of the
core, plus the small core additions it (and the CLI) need.

- **Thin consumer.** The UI calls `score_complexity`, `explain_score`,
  `calibrate`, `load_routing_config`, and `dump_routing_toml`; it reimplements
  nothing. If scoring changes, the UI follows for free (the same engine/thin-client
  split RAC uses).
- **Local web app.** A FastAPI backend exposes JSON endpoints wrapping the core; a
  single no-build HTML/JS page is served inline (no Node toolchain, no static-asset
  build). It binds localhost. Ships behind the `wayfinder[ui]` extra
  (`fastapi`/`uvicorn`), lazily imported, so the base package keeps
  `dependencies = []`. The `wayfinder ui` command runs it.
- **Three core additions, pure and independently useful:**
  - `explain_score(features, weights)` → per-feature contribution to the score
    (`weight × normalized / Σweights`), also surfaced as `route --explain`.
  - `sweep_curve(samples)` → the full `(threshold, accuracy)` curve, for the
    calibrate chart.
  - `dump_routing_toml(config)` → a deterministic config writer that round-trips
    through `load_routing_config`, for the configure surface's save.
- **Three screens, all built.** Explain/Playground (paste a prompt → score,
  recommendation, tier ladder, contribution bars, live threshold slider);
  Calibrate (paste a labeled JSONL → run a mode → accuracy, the threshold-sweep
  curve, and the config fragment, with "send to Configure"); Configure (edit
  `wayfinder.toml` with live validation through the real loaders, then save). The
  text-free parsers the latter two rest on — `parse_dataset` and
  `routing_config_from_toml` — are also pure core additions, so a pasted draft is
  validated exactly as a real file is.
- **It never routes or invokes.** No model call. The one exception, a future "test
  through the gateway" action, goes through the WF-ADR-0004 gateway layer with a
  BYO key — opt-in and separate.
- **Secrets stay out.** The configure screen shows a gateway model's `api_key_env`
  (the variable *name*), never the secret value; the key-never-in-the-file
  invariant (WF-ADR-0004) holds.

This **extends** WF-ADR-0004 (which kept the UI off the routing path) rather than
conflicting with it: the UI is calibration/explain/configure only.

## Consequences

### Positive

- Calibration and "why did this route here" become visible and interactive,
  without putting anything new on the hot path.
- The core additions stand on their own (`route --explain`, a sweep API, a config
  writer) even for users who never open the UI.
- Determinism is untouched: the UI changes nothing about the runtime.

### Negative

- A web UI is a maintenance surface (the inline page, the endpoints); kept minimal
  and no-build to contain it.
- Save writes the whole `wayfinder.toml`; calibrate "applies" by sending its
  fragment to the Configure editor for review rather than writing blindly, so a
  human merges it with any existing gateway config.

## Alternatives Considered

### A terminal UI (Textual), like RAC's explorer

#### Disadvantages

- The weighting view, the contribution bars, and a sweep chart are weak in a
  terminal; the web app fits the calibration job better. (A TUI remains a possible
  later alternative.)

### Put scoring/calibration logic in the UI backend

#### Disadvantages

- Duplicates the engine and risks drift; the UI must stay a consumer of the core.

### A UI on the routing path

#### Disadvantages

- Routing is a headless per-request concern; a UI there adds latency for no gain
  (WF-ADR-0004).

## Success Measures

- The UI computes nothing itself: every number it shows comes from a core
  function, proven by testing those functions directly.
- `explain_score` contributions sum to the score; `dump_routing_toml` round-trips;
  the UI's `/api/score` returns the same result as the library.
- No secret ever appears in the UI or in `wayfinder.toml`.

## Related

- Extends WF-ADR-0004 (invocation/gateway boundary); builds on WF-ADR-0002 (tiers)
  and WF-ADR-0003 (calibration)
