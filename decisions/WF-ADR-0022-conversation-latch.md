---
schema_version: 1
id: WF-ADR-0022
type: decision
tags: [gateway, routing, chat, multi-turn, escalation]
---

# WF-ADR-0022: Conversation Latch (Sticky-Auto)

## Status

Accepted

## Category

Technical

## Context

WF-ADR-0021 fixed the *drift* failure — scoring the whole transcript pushed every long chat
toward cloud — by scoring the current turn (system + latest user message). That left the opposite,
inherent failure of a structural router exposed: a **short but semantically hard follow-up** scores
low and routes cheap. "Now prove that's lossless." or "And in the general case?" carry no
structural weight, so the scorer — which reads structure, not meaning — sends them to the small
model even though the conversation is plainly in deep water.

A structural scorer cannot, on its own, tell that a terse follow-up is hard; that needs the meaning
(a model call) we deliberately keep out of the path (WF-ADR-0001). But there is deterministic
signal we are currently throwing away: **the conversation's own earlier turns.** If an earlier turn
in this chat was structurally heavy enough to route cloud, the follow-ups almost certainly belong
on the same model — they are continuing that hard thread. The cross-turn-consistency lever that
already exists is manual pinning (WF-ADR-0011); the gap is an automatic version of it.

## Decision

Add an opt-in **conversation latch**: route by the highest tier *any single turn* in the
conversation has needed, so a chat that goes hard stays on the big model.

- Config flag `[gateway] sticky` (default `false`), with a per-request override header
  `X-Wayfinder-Sticky: true|false` mirroring the threshold header (WF-ADR-0011).
- `conversation_high_water(messages, routing, tiers)` scores **each user turn independently** (with
  the standing system context) and returns the highest tier reached. It is a *max over turns*, not
  a sum, so — unlike the old whole-transcript scoring — it does not inflate with conversation
  length: fifty trivial turns stay local; one hard turn latches cloud.
- When the latch raises the tier above what the current turn alone would pick, the decision routes
  to the latched tier and reports `mode = "sticky"`. The reported `score` stays the *current*
  turn's, so the "why" breakdown remains honest (a low score with a `sticky` mode is exactly the
  story: this message is trivial, but the conversation already crossed over).
- Applies only to a tiered/binary router (`classifier is None`, ≥2 tiers) and never overrides an
  explicit pin — a pin is the operator's deliberate choice.

A per-request `X-Wayfinder-Route-On` header was added alongside, so a client (notably the demo's
settings panel) can move the scope and the latch for one request without touching server config.

We chose a **monotonic latch** (escalate and stay) over a windowed or decaying scheme for the first
cut: it is the simplest fully-deterministic rule, trivially explainable ("this chat went cloud at
turn 3 and stayed"), and matches the intent — keep a hard conversation on the capable model. A
cool-down window is possible future work if "stays cloud forever after one hard turn" proves too
sticky in practice.

## Consequences

### Positive

- Closes the common case of the gap: an ongoing hard conversation keeps its follow-ups on the big
  model, automatically, with no model call and no server-side conversation state — the transcript
  the client already sends *is* the state.
- Deterministic, bounded (max over turns), and explainable; off by default, so nothing changes for
  anyone who does not opt in.
- Gives the demo a real, visible setting to toggle — the routing decision changes live.

### Negative

- It does **not** close the cold-start case: if the *very first* message is short-but-hard, there
  is no earlier turn to latch from and it still routes cheap. That residue is structural and is
  covered only by opt-in lexical signals (WF-ADR-0016) or by pinning.
- "Escalate and stay" can over-route a chat that had one hard question then many trivial ones; the
  cost is bounded by being opt-in, and a cool-down is left as future work.
- Re-scoring every user turn per request is O(turns) scorer calls — negligible (microseconds each,
  chats are short), but it is no longer a single score per request.

### Risks

- Surprise for operators who enable it without expecting the stay-on-cloud behaviour. Mitigated by
  the default-off flag, the `sticky` mode label in the headers/decision payload, and the docs.

## Alternatives Considered

- **Lexical/keyword difficulty signals to catch the hard follow-up directly.** Already shipped and
  off by default; the double-blind test showed they detect an author's vocabulary, not difficulty
  (WF-ADR-0016). Complementary, not a replacement.
- **A model-as-judge to read the follow-up's meaning.** Off-ethos for the serving path (a model
  call per request to decide — the cost the router exists to avoid); see the FAQ.
- **Server-side conversation state keyed by a conversation id.** Rejected: adds memory and a store
  to a component whose whole value is being stateless and deterministic. The client-sent transcript
  already carries the history we need.
- **Windowed / decaying stickiness.** Deferred; monotonic latch first for simplicity.

## Related Decisions

- WF-ADR-0021 (per-turn scoping, which this builds on), WF-ADR-0011 (per-request override /
  manual pinning, the lever this automates), WF-ADR-0016 (lexical signals, the other half of the
  gap), WF-ADR-0001 (no model/state in the scored path), WF-ADR-0020 (the demo whose settings
  panel exposes this)
- roadmaps/v0.2.0-wayfinder-chat.md
