---
schema_version: 1
id: WF-ADR-0036
type: decision
tags: [gateway, routing, override, chat-ux, invocation]
---

# WF-ADR-0036: In-message slash routing directives (opt-in, prompt-parsed override)

## Status

Accepted

## Category

Technical

## Context

The gateway already supports per-request routing overrides — the OpenAI `model` field (`auto`, a
configured endpoint name, `prefer-local` / `prefer-hosted`) and per-request headers
(WF-ADR-0011). But many chat UIs give the user only a text box: they can't set the `model` field
or send headers. A user in Open WebUI, LibreChat, or Claude Code who wants *this* turn on a
specific tier has no inline way to ask. A **slash directive typed into the message** — `/local
refactor this` — fills that gap.

Two constraints frame the decision:

- **The deterministic core is sacred (WF-ADR-0001).** Parsing a directive out of the prompt is
  pure string handling — no model call. The complexity *score* is still computed offline; the
  directive only overrides which tier the scored decision is delivered to, exactly like a
  `model`-field pin. It is another explicit override channel, not a second router.
- **Don't eat legitimate prompts.** A user's message may legitimately start with `/` — a path
  (`/etc/hosts`), code, or a chat UI's own command (`/help`). The feature must never strip or
  reroute a message it wasn't explicitly meant to.

## Decision

1. **A recognized `/directive` at the very start of the latest user message overrides routing.**
   The token after the slash must be a *known* directive — a configured endpoint name, `prefer-local`
   / `prefer-hosted` (`prefer-cloud` alias), or `auto` (force scoring). It must be the first token,
   followed by whitespace or end-of-message. `/local refactor this` pins to `local`.

2. **Unknown ⇒ untouched.** If the token isn't a recognized directive, the message is left exactly
   as-is — not stripped, not rerouted. So `/etc/passwd`, `/help`, `/localhost`, or any ordinary
   slash-prefixed text passes through as normal prompt text. This is the safety guarantee.

3. **Opt-in, off by default.** Enabled with `[gateway] slash_directives = true`. A deployment that
   doesn't turn it on behaves exactly as before — no risk of reinterpreting slash-prefixed prompts.

4. **The directive is stripped before scoring and forwarding.** The upstream model never sees the
   `/directive`; the prompt is scored on the cleaned text too. So the directive influences delivery
   without polluting the conversation.

5. **Precedence: an explicit `model`-field pin wins.** When both are present, the API-level `model`
   pin takes precedence (least surprising for API clients); the slash directive applies when `model`
   is `auto`. A slash-routed turn reports `mode: slash-pinned`.

6. **Subject to the same downstream rules as any pin.** A slash pin is still clamped by a virtual
   key's model allowlist (WF-ADR-0035) — `/cloud` from a local-only key clamps to the nearest
   allowed tier — and still flows through budgets, failover, and the cache unchanged.

## Consequences

- **Wayfinder is steerable from any plain chat box** (and Claude Code via `/v1/messages`, since the
  directive lives in the message text), with no header or `model`-field control needed — a real
  daily-use win for chat-UI users.
- **Reuses `resolve_pin`** for the vocabulary, so the directives match the `model`-field ones; no
  new routing concepts.
- **Determinism preserved**: the score is unchanged; this is delivery-only, and testable as a pure
  function (`resolve_slash_directive`).
- **Risk — prompt mangling.** Mitigated by (2) known-directive-only + (3) off-by-default + (1)
  first-token-only matching; a non-directive slash message is never altered.
- **Limitation — first user-message token only.** No mid-prompt or multi-directive parsing; one
  directive, at the start, by design (keeps it unambiguous).

## Alternatives Considered

- **Greedily treat any leading `/word` as a directive** — would strip/reroute legitimate paths,
  code, and other tools' commands. Rejected for the known-directive-only rule.
- **On by default** — risks silently changing behavior for prompts that happen to start with a
  slash. Rejected; opt-in.
- **A separate control message / metadata field** — invisible to a plain chat box, which is the
  exact audience this serves. Rejected.
- **Slash directive overrides the `model`-field pin** — surprising for API clients that set `model`
  deliberately. Rejected; the API pin wins.

## Success Measures

- With `slash_directives = true`, `/local <complex prompt>` is served by `local` (`mode:
  slash-pinned`) and the upstream receives the prompt without the directive.
- `/etc/...`, `/help`, `/localhost`, and unknown `/foo` are passed through unchanged.
- Off by default: a slash-prefixed prompt is ordinary text until the flag is set.
- A slash pin is clamped by a virtual key's allowlist, just like a `model`-field pin.

## Related

- WF-ADR-0001 (deterministic, offline core — preserved; score unchanged)
- WF-ADR-0011 (per-request override via the `model` field / headers — this is the in-message channel)
- WF-ADR-0035 (virtual-key model allowlist — clamps a slash pin too)
