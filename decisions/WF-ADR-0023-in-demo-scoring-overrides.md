---
schema_version: 1
id: WF-ADR-0023
type: decision
tags: [gateway, demo, tuning, weights, lexicon, overrides]
---

# WF-ADR-0023: Per-Request Scoring Overrides for the Demo (Tune Weights & Lexicon Live)

## Status

Accepted

## Category

Technical

## Context

The demo could move *routing behaviour* live — threshold, scope (WF-ADR-0021), the latch and its
cool-down (WF-ADR-0022) — all via the header overrides of WF-ADR-0011, which deliberately only
change *which* threshold/scope/latch applies, never *how* the score is computed. But the two
knobs that actually shape the score — the per-feature **weights** and the **lexicon** (WF-ADR-0019,
shipped off by default) — were reachable only through the config file plus a restart, or via
`calibrate`. So the demo couldn't show the thing that makes the router *yours*: turning the lexical
signals on, watching a short, hard, structureless prompt ("Prove the halting problem is
undecidable.") flip from local to cloud — the one **cold-start** case the conversation latch
explicitly can't catch (no earlier turn to latch from).

The user asked for an in-demo "Advanced" tuning surface for these parameters.

## Decision

Add an **opt-in, per-request scoring override** carried in the chat request body, plus an **export**
endpoint, and surface both in the demo's settings as an "Advanced" accordion.

1. **Transport.** A `wayfinder_tuning` object in the `POST /v1/chat/completions` body (not a header —
   term lists are unbounded and ugly in headers): an optional partial `weights` map merged over the
   configured weights, and an optional `lexicon` (`reasoning_terms` / `constraint_terms`) replacing
   those sets. `apply_scoring_overrides()` builds a `RoutingConfig` variant with `dataclasses.replace`
   and the scorer runs against it. The field is **popped before the upstream relay**, so it never
   leaks to the model. Malformed input raises `BadOverride` → 400, like the other overrides.
2. **This changes scoring, by design — but not the scorer.** Unlike the WF-ADR-0011 header overrides,
   this alters the scoring *function* (weights/lexicon). The deterministic core is still untouched:
   the gateway only *chooses the config it hands over*; `score_complexity` remains a pure function of
   `(text, config)` (WF-ADR-0001). It is opt-in and additive — absent the field, nothing changes.
3. **Export, so tuning becomes real.** `POST /router/config` applies the same override to the live
   config and returns `[routing]` TOML via the existing `dump_routing_toml`, round-trip-parseable.
   The demo's "Export config" button shows it to paste into `wayfinder-router.toml`.
4. **The demo is for *finding* a setting, not running on it.** Live per-request tuning is an
   exploration affordance; the principled production path is still `calibrate` on labelled traffic
   (and the lexical caveats of WF-ADR-0016 stand). The accordion's copy says as much.

## Consequences

### Positive

- Closes the demo loop on the whole gap story: the latch (WF-ADR-0022) handles an *ongoing* hard
  chat; the lexical toggle here handles the *cold-start* short-hard prompt — the deterministic answer
  that needs no model and no prior turn.
- "Tune it for your traffic" is now visible and tactile: drag a weight, add a trigger word, watch the
  decision and the "why" recompute, then export a real config.
- The scored relay path and the core boundary are intact; the override is opt-in and never forwarded.

### Negative

- The OpenAI request body now carries an optional non-standard field. Mitigated by popping it before
  the relay and namespacing it (`wayfinder_tuning`); real upstreams never see it.
- A second, lower-friction way to set weights/lexicon (besides config + calibrate) — a tuning UI can
  tempt eyeballing values over calibrating them. Mitigated by the export-to-config framing and explicit
  copy that calibration is the real path.

### Risks

- Misuse as a production control plane (tuning per request in anger). It is stateless and additive, so
  the blast radius is one request; operators who want persistence use the exported config.

## Alternatives Considered

- **Headers for weights/lexicon.** Rejected: term lists and an 11-feature weight map don't belong in
  headers (size, escaping). A body field is the natural carrier and is trivially stripped before relay.
- **A separate tuning/playground endpoint only (no chat-path override).** Cleaner boundary, but then
  the demo's tuning wouldn't change the actual chat routing you're watching — which is the whole point.
  Kept the chat-path override *and* added the export endpoint for persistence.
- **Put tuning in the operator console (WF-ADR-0005) instead.** Valid and still the right home for a
  full configure/calibrate workflow; but the ask was to tune *in the chat demo* and see the effect on
  the live decision. The two can converge later (both render `dump_routing_toml`).

## Related Decisions

- WF-ADR-0011 (per-request overrides; this extends them from transport to scoring params),
  WF-ADR-0019 (the configurable lexicon this exposes), WF-ADR-0016 (why lexical ships off; the caveats
  that still apply), WF-ADR-0022 (the latch; lexical is the cold-start half of the same gap),
  WF-ADR-0020 (the demo), WF-ADR-0005 (operator console), WF-ADR-0001 (pure scorer)
- docs/lexical-routing.md, roadmaps/v0.2.0-wayfinder-chat.md
