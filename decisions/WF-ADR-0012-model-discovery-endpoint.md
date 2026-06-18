---
schema_version: 1
id: WF-ADR-0012
type: decision
tags: [gateway, discovery, openai-compatible, models, boundary]
---

# WF-ADR-0012: Model Discovery Endpoint

## Status

Accepted

## Category

Architecture

## Context

The per-request override (WF-ADR-0011) made the OpenAI `model` field a routing
directive: `auto`, a configured endpoint name, or `prefer-local` / `prefer-hosted`.
Those directives are the controls a client — especially a chat UI — exposes to a
user. But the gateway had no way to *advertise* them: it served
`/v1/chat/completions` and `/v1/feedback`, not the `/v1/models` listing that
OpenAI-compatible clients call to populate a model picker.

The practical cost showed up the moment we wrote the chat-UI recipes (the
`examples/` integration configs). Every client had to be told the directive names
by hand — LibreChat needed `fetch: false` plus a literal `default` list, and Open
WebUI needed the names typed into a "Model IDs" field. A client that fetched
`/v1/models` got a 404 and an empty dropdown. The directives existed but were not
discoverable, so each integration re-encoded them and drifted from the source.

The constraint is the same boundary as the rest of the gateway (WF-ADR-0001/0004):
discovery must not introduce a model call, a key read, or any provider logic. It is
a read of local configuration, nothing more.

## Decision

The gateway serves **`GET /v1/models`**, an OpenAI-compatible model list
synthesised from the current routing + gateway configuration. It is pure and
offline — it reads config only (via the same hot-reload-aware holder as every other
route), makes no model call, reads no key, and touches no network, exactly like
`/healthz`.

The list advertises the selectable routing options as model ids:

- `auto` — always present (the scored default).
- `prefer-local` / `prefer-hosted` — present **only for a tiered/binary router**.
  A classifier has no ordered low/high ladder for `prefer-*` to resolve against
  (WF-ADR-0011 amendment), so the directives are omitted rather than advertised as
  something that would not pin.
- each configured `[gateway.models]` endpoint name — the concrete pins.

Each entry is the minimal OpenAI model object: `{id, object: "model", created: 0,
owned_by: "wayfinder"}`. `created` is a fixed `0` rather than a wall-clock time so
the response stays deterministic, matching Wayfinder's reproducibility stance.

The renamed-away alias `prefer-cloud` (WF-ADR-0011 amendment) still *resolves* for
back-compatibility but is **not** advertised, so new clients surface only the
canonical `prefer-hosted`.

## Consequences

### Positive

- A chat UI or any OpenAI-compatible client auto-discovers the routing modes and
  configured endpoints; the `examples/` configs drop their hand-written lists
  (`fetch: true` for LibreChat, auto-discovery for Open WebUI).
- The directive vocabulary has one source of truth — the running config — instead
  of being re-encoded per integration.
- Discovery sits firmly inside the boundary: it is a config read, adding no model
  call, key read, or provider logic to the core.

### Negative

- The gateway now owns a third public route, so the directive-naming decisions
  (WF-ADR-0011) are part of a wire contract clients can read and depend on.

### Risks

- A client treats `created: 0` as a real timestamp and sorts oddly. Mitigation: the
  field is required by the schema; a fixed value is valid and the list order is
  already meaningful (auto, directives, endpoints).

## Alternatives Considered

### No discovery endpoint — keep hand-written lists

Leave clients to declare the directives themselves.

#### Disadvantages

- Every integration re-encodes the directive names and drifts from the source; a
  client that fetches `/v1/models` gets a 404 and an empty picker. Discovery is the
  one thing that makes the override ergonomic in a stock UI.

### Advertise `prefer-*` unconditionally, including under a classifier

List the `prefer-*` directives regardless of routing mode.

#### Disadvantages

- Under a classifier `prefer-*` does not pin (it has no ordered ladder), so
  advertising it would offer a control that silently falls through to scoring.
  Omitting it keeps the published list honest about what each id does.

## Success Measures

- An OpenAI-compatible client populates its model selector from `GET /v1/models`
  with `auto`, the `prefer-*` directives (tiered router), and the configured
  endpoint names — no hand-written list.
- The endpoint adds no model call, key read, or network to the core, and reflects
  config hot-reload like the other routes.
- A classifier config omits the `prefer-*` ids; the `prefer-cloud` alias is absent
  from the list while still resolving on a request.

## Related Decisions

- WF-ADR-0011 (the per-request override whose directives this endpoint advertises)
- WF-ADR-0004 (the gateway and the invocation boundary this read stays inside)
- WF-ADR-0001 (the deterministic, key-free core discovery must not disturb)
- WF-ADR-0002 / WF-ADR-0003 (tiers and the classifier, which decide whether
  `prefer-*` is advertised)
