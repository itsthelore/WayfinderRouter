---
schema_version: 1
id: WF-ADR-0021
type: decision
tags: [gateway, routing, chat, multi-turn, scoring]
---

# WF-ADR-0021: Score the Current Turn, Not the Whole Transcript

## Status

Accepted

## Category

Technical

## Context

The gateway scores a chat request by joining message text and handing the string to the
deterministic scorer (WF-ADR-0001). Until now `extract_prompt` concatenated **every** message —
every role, including the model's own prior replies — into one blob:

```python
for message in messages:
    parts.append(message["content"])
return "\n".join(parts)
```

In a multi-turn chat the scored text therefore grows with the conversation. Each turn re-scores
the entire back-scroll, the assistant's (usually long, list/heading/code-heavy) replies inflate
the score, and the structural score ratchets upward turn over turn regardless of what the *new*
user message asks. The demo recording made it visible: the same migration prompt scored `0.24` as
turn two and `0.57` as turn three. The production failure is worse than cosmetic — a trivial
follow-up (`"thanks!"`, `"and in French?"`) after a heavy exchange routes to the expensive model,
because the accumulated transcript is heavy. It also contradicted the FAQ, which claimed Wayfinder
"does not model a chat getting harder over turns" while the code did exactly that.

This is a question of *what text we score*, not how — so it lives entirely in the impure gateway
layer; the stdlib-only scorer is untouched (WF-ADR-0001).

## Decision

Score the **current turn** by default, and make the scope an explicit `[gateway]` knob,
`route_on`, with four values:

- **`turn`** *(default)* — the system message(s) plus the latest user message. The standing
  instructions and the new ask; stable across turns, so the score does not drift with conversation
  length, and the assistant's own output is never fed back into the routing decision.
- **`last_user`** — the latest user message only (drops the system prompt too).
- **`user`** — every user message, excluding system and assistant (keeps history; still grows
  slowly with a long chat).
- **`all`** — every message, all roles (the legacy behaviour, preserved for anyone who wants it).

`extract_prompt(messages, *, route_on="turn")` implements the scopes and falls back to the last
message when role-filtering finds nothing (a role-less or assistant-only payload), so the router
never scores an empty string and silently routes local. `route_on` round-trips through
`dump_gateway_toml` so recalibration preserves a non-default scope.

We chose `turn` (system + latest user) over `last_user` because the system prompt is where chat
apps put persistent task framing ("you are a DBA; produce migration runbooks…"); it sets the
difficulty of every turn and, being constant across the conversation, does not reintroduce drift.

## Consequences

### Positive

- The decision reflects what *this* turn asks for; the score stops drifting toward cloud as a
  conversation lengthens, and a cheap follow-up stays cheap.
- Deterministic and explainable as before — the "why" breakdown now describes the turn, not the
  transcript.
- Backward compatible: `route_on = "all"` restores the old behaviour exactly; single-turn requests
  (the common case and all existing fixtures) are unchanged, since system+latest-user of a
  one-message request is that message.

### Negative

- A short-but-semantically-hard follow-up ("now prove that's optimal") scores low and routes cheap.
  That is inherent to a structural router (it reads structure, not meaning), not a regression — and
  the complementary lever is conversation pinning (the `model` field / `X-Wayfinder-Threshold`,
  WF-ADR-0011), with a sticky-`auto` mode left as possible future work.
- One more configuration surface to document and test.

### Risks

- A non-standard client that omits roles would, under the default, score only its last message
  rather than the whole payload. Mitigated by the last-message fallback and by `route_on = "all"`
  for callers that genuinely want every part scored.

## Alternatives Considered

- **Keep scoring the whole transcript (`all` as default).** Rejected: this *is* the drift.
- **`last_user` as default.** Simpler, but misses a heavy task spec that lives in the system
  prompt; offered as a scope, not the default.
- **A conversation-state / "getting harder over turns" model.** Out of scope and off-ethos — it
  would require memory and heuristics the deterministic core deliberately avoids. Pinning covers
  the "keep a hard chat on the big model" need without it.

## Related Decisions

- WF-ADR-0001 (no model/derived state in the scored path; this only selects the string),
  WF-ADR-0011 (per-request override / pinning, the cross-turn-consistency lever),
  WF-ADR-0004 (the gateway), WF-ADR-0020 (the demo UI that surfaced the drift)
- roadmaps/v0.2.0-wayfinder-chat.md (the chat surface this serves)
