---
schema_version: 1
id: WF-ADR-0020
type: decision
tags: [ui, gateway, demo, chat, routing-visibility]
---

# WF-ADR-0020: A Decision-First Demo UI, Not a Chat Fork (Yet)

## Status

Proposed

## Category

Technical

## Context

Wayfinder needs a front end people can *see*. The no-fork path already works: the gateway
is OpenAI-compatible, advertises routing options via `GET /v1/models` (WF-ADR-0012), and the
`model` field doubles as a per-conversation routing picker (`auto` / `prefer-local` /
`prefer-hosted`, WF-ADR-0011), so LibreChat or Open WebUI give a routing-mode dropdown for
free (`examples/`). The one thing that path cannot do is the thing that actually *sells* the
router: show the decision — the local-vs-cloud choice, the complexity score and why, the
cost saved — and let you move the cut live with a per-conversation threshold slider. That
requires UI code (WF-ADR-0010 scoped a `wayfinder-chat` fork of LibreChat for it).

A full LibreChat fork is a large, fast-moving React/Node + Mongo app; maintaining it means
perpetual rebases onto upstream for security and features — a heavy tail that contradicts
Wayfinder's whole "small, deterministic, boring on purpose, vendor-the-file" stance
(WF-ADR-0001). The desired end state is to land routing controls in **LibreChat proper** so
there is no fork to maintain. But that is a larger, slower effort, and we want something to
demo now.

## Decision

Build a **thin, decision-first demo UI** first; pursue upstreaming to LibreChat later. The
fork is explicitly *not* chosen now.

1. **A single self-contained page.** Vanilla HTML/CSS/JS, no build step, no Node, no new
   runtime dependency — served by the existing FastAPI gateway at a route (e.g. `GET /demo`),
   behind the gateway extra. It stays entirely in the impure gateway/UI layer; the
   stdlib-only deterministic core is untouched (WF-ADR-0001). It sits next to, not on top of,
   the operator console (WF-ADR-0005), which is a different surface (calibrate/explain/configure).
2. **Decision-first, by design.** The UI foregrounds what no general chat app shows: the
   chosen model, the structural score, the top contributing features ("why"), the per-call
   cost, and a running *saved-vs-always-cloud* tally — plus a live threshold slider that
   re-cuts the next message.
3. **Reuse the existing contract.** It calls `POST /v1/chat/completions` with `model: "auto"`
   and the slider's `X-Wayfinder-Threshold`, and reads the decision from the response headers
   already emitted (`x-wayfinder-router-model` / `-score` / `-mode` / `-request-id`). A keyless
   demo uses the gateway's `dry_run` decision mode (returns the routing decision with no
   upstream call), so it runs with no API keys or local model.
4. **The contract is the upstream spec.** The request/response shape the demo exercises is
   exactly the feature we would propose to LibreChat. Building the demo de-risks and specifies
   the upstream contribution instead of competing with it.
5. **Aesthetic: native-desktop, ChatGPT-desktop-like.** System font stack
   (`-apple-system`/SF), light + dark via `prefers-color-scheme`, generous whitespace, rounded
   cards, subtle borders/shadows, an inspector-style routing panel — a Mac-app feel, not a
   web-dashboard feel. The polish *is* part of the pitch.

**Deliberately out of scope** (and that omission is the argument for LibreChat-proper later,
not a fork): accounts, conversation persistence/history, multimodal, plugins, org features.

**Known contract gap to resolve in the build:** the gateway returns model/score/mode but not
the feature breakdown. Surfacing "why" needs a small *additive* extension — e.g. include the
`explain_score` contributions in the `dry_run` / `X-Wayfinder-Debug` response payload — never
a change to the scored path.

## Consequences

### Positive

- Demos the differentiator (the visible, deterministic decision) immediately, on-ethos, with
  zero new runtime deps and nothing to babysit.
- Produces the concrete API contract for the eventual LibreChat upstreaming, so that effort
  starts from a working reference rather than a blank page.
- Keeps the core boundary intact; the UI is a static asset on the optional gateway.

### Negative

- A second small UI surface to keep working (alongside the operator console), though it shares
  the gateway and the contract.
- A demo UI is not a product; it intentionally lacks the chat-app features users may expect.

### Risks

- Scope creep toward "just one more chat feature" until it quietly becomes the fork we avoided.
  Mitigation: keep the out-of-scope list above firm; richer chat features are the trigger to
  invest in upstreaming, not in growing the demo.
- The "why" extension could tempt putting explain data on the hot path. Mitigation: it is
  additive, opt-in (debug/dry-run), and never alters scoring (WF-ADR-0001).

## Alternatives Considered

### Fork LibreChat now (WF-ADR-0010)

A real chat app for free, but a perpetual rebase burden for what is, today, a slider and a
decision badge. Deferred until the demo proves the controls and we pursue them upstream.

### A React/Svelte standalone demo

More polished tooling, but a build step + Node dependency undercuts the "small, vendor-the-file"
pitch before the visitor even reaches the router. Rejected for the first cut.

### Reuse the operator console (WF-ADR-0005)

Different surface and audience (operator calibrate/explain/configure vs. an end-user chat demo).
Kept separate; they share the gateway and the explain primitives.

## Related Decisions

- WF-ADR-0010 (the `wayfinder-chat` fork this defers), WF-ADR-0011 (per-request override),
  WF-ADR-0012 (`/v1/models`), WF-ADR-0005 (operator UI), WF-ADR-0018 (gateway metrics/decision
  surface), WF-ADR-0001 (no model/UI in the scored path)
- roadmaps/v0.2.0-wayfinder-chat.md (the chat surface roadmap this slots under)
