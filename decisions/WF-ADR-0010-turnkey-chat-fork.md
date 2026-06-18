---
schema_version: 1
id: WF-ADR-0010
type: decision
tags: [product, topology, chat, librechat]
---

# WF-ADR-0010: `wayfinder-chat`, a LibreChat Fork for the Turnkey Chat Experience

## Status

Proposed

## Category

Product

## Context

Wayfinder Router is deliberately UI-less middleware: a transparent
OpenAI-compatible gateway you place *behind* any client (WF-ADR-0004), over a
deterministic, zero-dependency core (WF-ADR-0001), with an operator-only tuning
console (WF-ADR-0005). The pitch is "bring your own client; routing is
invisible." That serves **power users** who already run a chat app, IDE
assistant, or agent and just want local/cloud routing slotted underneath.

It does not serve a second audience: **casual / hobbyist users** who want one
thing they can install and *chat with*, where local-vs-cloud routing — and the
controls for it — are built in. Today they must stand up a chat UI themselves
(Open WebUI, LibreChat, …) and point it at the gateway — the path the README's
"Where Wayfinder sits" section describes. That assembly is exactly the friction
this audience will not push through.

The tempting shortcut is to add a chat window to `wayfinder-router` itself. That
would invert its identity — turning a router you compose behind anything into a
chat application that competes with mature chat UIs — and load the lean router
with a large, ongoing UI surface (sessions, history, streaming, auth, settings).
Building such a UI from scratch reinvents a solved problem.

## Decision

The turnkey chat experience ships as **`wayfinder-chat`: a separate repository
that is a fork of [LibreChat](https://github.com/danny-avila/LibreChat) (MIT) and
*depends on* `wayfinder-router`** — not a fork of the router itself. The two
products are maintained and used independently:

- **`wayfinder-router`** stays the transparent, bring-your-own-client router for
  power users: UI-less, zero-dependency core, composed behind any
  OpenAI-compatible client. It is unchanged by this decision.
- **`wayfinder-chat`** forks LibreChat (chosen over Open WebUI for its permissive
  **MIT** licence and clean redistribution), preconfigured in front of the
  Wayfinder gateway, giving hobbyists chat + routing in a single install.

### What is configuration vs. what needs the fork

LibreChat's extension points cover the *integration* but not the *routing UX* —
which is what makes this a source fork rather than a thin distribution:

- **Configuration only (no source changes).** Pointing LibreChat at the gateway is
  a stock *custom endpoint* in `librechat.yaml` (an OpenAI-compatible `baseURL` +
  a `models` list). A **coarse routing control** also needs no fork: expose model
  names like `auto` / `prefer-local` / `prefer-cloud` in that `models` list (they
  populate the model selector) and have the gateway map each to a threshold.
- **Requires the fork (LibreChat source changes).** A true **routing-threshold
  control in the settings panel** — LibreChat's `customParams` only tunes the
  range/default of *existing* parameters and cannot define a new UI control — and
  **surfacing the routing decision** in the chat (the `x-wayfinder-router-*`
  model/score; LibreChat does not expose response headers in the UI).
- **Requires a `wayfinder-router` change (router side, not the fork).** For any
  per-conversation control to take effect, the gateway must accept a **per-request
  override** — a threshold/model-pair in the request body or a header, or the
  model-name mapping above. Today it routes off `wayfinder-router.toml`.

This permits a **phased build**: a config-only v0 (a custom endpoint plus the
`auto` / `prefer-*` model control) proves the experience with no fork at all; the
fork follows only for the polished threshold-in-settings and the decision display.

This is **v0.2.0-and-beyond, exploratory.** It is recorded to fix the direction
(a `wayfinder-chat` LibreChat fork, visible routing controls, a separate repo, two
audiences). The precise integration shape and a scoped roadmap are deferred until
the work is scheduled, and should be confirmed by a spike against LibreChat's
client (its settings/parameters components and request path), since that extension
surface evolves.

## Consequences

### Positive

- Serves both audiences without compromise: the router stays transparent and lean
  for power users; hobbyists get a one-install chat + routing product.
- Reuses a mature, MIT-licensed chat UI rather than building or maintaining one —
  far less surface area than a from-scratch UI.
- The fork can be adopted or ignored independently; `wayfinder-router`'s identity
  (WF-ADR-0001/0004/0005) is preserved intact.

### Negative

- A second product in the Wayfinder line to position, brand, and maintain — and a
  fork that tracks upstream LibreChat.
- The routing UX — a threshold control in the settings panel and showing the
  `x-wayfinder-router-*` decision — is a **source-level change to LibreChat's
  client**, not a config or plugin hook, so it is the part of the fork that must be
  re-reconciled when tracking upstream.
- `wayfinder-router` gains a small per-request-override feature so the chat UI's
  control actually changes routing — a router-side change, separate from the fork.

### Risks

- Fork-maintenance burden: staying current with LibreChat while carrying the
  source-level routing UX. Mitigation: keep the change thin and localised to a
  small set of client components, and lean on configuration (the custom endpoint
  and the `auto`/`prefer-*` model names) for everything that does not strictly
  need source.
- Brand confusion between the router and the chat product. Mitigation: clear
  naming and positioning ("the router" vs "the turnkey app"); the README "Where
  Wayfinder sits" section already frames the relationship.
- LibreChat licence/health drift. Mitigation: MIT today; revisit if upstream
  relicenses — the reason Open WebUI was not chosen.

## Alternatives Considered

### Build the chat window into `wayfinder-router`

Add a chat UI to the existing package and repository.

#### Disadvantages

- Inverts the transparent-router identity (WF-ADR-0004) and competes with mature
  chat UIs; loads the lean router with a large UI surface and dependencies that
  power users who bring their own client do not want.

### Fork or bundle Open WebUI instead of LibreChat

#### Disadvantages

- Open WebUI's licence has tightened (branding/usage clauses), complicating a
  rebrandable fork and redistribution. LibreChat's MIT licence is cleaner for a
  bundled product.

### Pure configuration bundle (no fork)

Ship only a `librechat.yaml` plus a compose recipe — a custom endpoint pointed at
the gateway, with `auto` / `prefer-*` model names — and no LibreChat source
changes.

#### Disadvantages

- Gives only coarse, model-selector routing control; it cannot present a real
  threshold slider or show the routing decision in the chat. Adopted as the **v0
  phase** rather than rejected outright — it is the starting point, with the fork
  layered on when the richer UX is wanted.

### Build a chat UI from scratch

#### Disadvantages

- Reinvents a solved problem (auth, sessions, streaming, rendering, settings) at
  high, ongoing cost with no differentiation.

## Success Measures

- A hobbyist installs one thing and chats, with local/cloud routing working and
  its threshold/controls adjustable in the UI — no manual gateway-plus-client
  assembly.
- `wayfinder-router` remains UI-less and unchanged in identity; the two products
  install and run independently.
- The chat product's routing customisation stays a thin, maintainable layer over
  upstream LibreChat.

## Related Decisions

- WF-ADR-0001 (standalone deterministic router — the identity preserved here)
- WF-ADR-0004 (invocation and gateway — the transparent-proxy boundary the fork builds on)
- WF-ADR-0005 (the operator UI — distinct from the end-user chat surface)
- WF-ADR-0009 (Apache-2.0 for `wayfinder-router`; the fork must reconcile this with LibreChat's MIT)
