---
schema_version: 1
id: WF-DESIGN-0011
type: design
tags: [gateway, anthropic, claude-code, adapter, translation, surfaces, invocation]
---

# WF-DESIGN-0011: Claude Code Adapter — an Anthropic `/v1/messages` Translation Endpoint

## Status

Proposed

> Make Wayfinder a one-line `ANTHROPIC_BASE_URL` swap for Claude Code (and any
> Anthropic-Messages-native client) by adding a `POST /v1/messages` endpoint that
> **translates** the Anthropic Messages format to the OpenAI Chat Completions the gateway
> already speaks — request and response, non-streaming and streaming. Pure format
> translation: the routing decision, budget, and failover are the *existing* deterministic
> machinery, reused unchanged (WF-ADR-0001/0004). No model call in the adapter.

## Context

The gateway is OpenAI-compatible: it answers `POST /v1/chat/completions` and forwards to
OpenAI-shaped upstreams (WF-ADR-0004). That covers most tools via a `base_url` swap
(WF-DESIGN-0009). It does **not** cover clients that speak the **Anthropic Messages API** —
most importantly **Claude Code**, which talks to whatever `ANTHROPIC_BASE_URL` points at
using `POST /v1/messages`, with `x-api-key`/`anthropic-version` headers, server-sent-event
streaming, and tool use. Today there is no way to put Wayfinder in front of Claude Code; it
is the single highest-value surface that is *not* a `base_url` swap (WF-ROADMAP-0006 item 11).

The shape of the work is **translation, not routing**. Wayfinder already knows how to score
a prompt, pick a tier, enforce a budget, and fail over — all OpenAI-shaped. The adapter's job
is to (a) turn an inbound Anthropic request into the OpenAI body that machinery expects, and
(b) turn the OpenAI reply back into the Anthropic response the client expects. Both directions
are deterministic and offline; the adapter adds no scoring of its own.

## User Need

A developer wants to point Claude Code (or Cline, or the Anthropic SDK) at Wayfinder and have
their prompts routed across local/cloud tiers — saving cost, surviving outages, respecting a
budget — without changing the client. One environment variable: `ANTHROPIC_BASE_URL`.

## Design

### Surface

- `POST /v1/messages` (+ bare `/messages`, matching the existing path tolerance).
- Auth: accept Anthropic's `x-api-key` header in addition to `Authorization: Bearer` — Claude
  Code sends `x-api-key`. The gateway's own forwarding still uses each upstream's configured
  `api_key_env` (BYO key, WF-DESIGN-0006); the inbound `x-api-key` is **not** forwarded (it is
  an Anthropic key, meaningless to an OpenAI upstream). It may, optionally, gate access if a
  client token is configured — but v1 treats the gateway as the trust boundary, as the OpenAI
  endpoint already does.
- `anthropic-version` header is accepted and ignored (we translate a stable subset).

### Routing reuse (the core invariant)

The adapter does **not** re-implement scoring/budget/failover. It translates the request to an
OpenAI body and **delegates to the existing `/v1/chat/completions` handler**, then translates
that handler's response back. This means:

- The scored decision, `mode`, budget degrade/block, and failover are *identical* to the OpenAI
  path for the same prompt — there is exactly one router (WF-ADR-0001).
- All decision headers (`x-wayfinder-router-model`, `-score`, `-mode`, `-served-by`, `-budget`,
  `-request-id`) ride along on the `/v1/messages` response unchanged, so the adapter is as
  observable as the native endpoint.
- The inbound Anthropic `model` (e.g. `claude-opus-4-…`) maps to the OpenAI `model` field,
  where the existing `resolve_pin` treats an unconfigured name as `auto` → score-and-route. A
  client can still pin by sending a configured endpoint name or a `prefer-*` directive.

### Request translation (Anthropic → OpenAI)

| Anthropic Messages | OpenAI Chat Completions |
| --- | --- |
| `system` (string, or array of text blocks) | a leading `{"role":"system","content": …}` message |
| `messages[].role` `user`/`assistant` | same |
| `messages[].content` string | `content` string |
| `messages[].content[]` `text` block | OpenAI text part / joined text |
| `messages[].content[]` `tool_use` (assistant) | `tool_calls[]` (`id`, `function.name`, `function.arguments` = JSON-encoded `input`) |
| `messages[].content[]` `tool_result` (user) | a `{"role":"tool","tool_call_id":…,"content":…}` message |
| `tools[]` (`name`, `description`, `input_schema`) | `tools[]` (`type:function`, `function.{name,description,parameters}`) |
| `tool_choice` `auto`/`any`/`tool` | `tool_choice` `auto`/`required`/`{type:function,function:{name}}` |
| `max_tokens` (required) | `max_tokens` |
| `temperature`, `top_p` | same; `top_k` dropped (no OpenAI equivalent) |
| `stop_sequences` | `stop` |
| `stream` | `stream` |
| `metadata` | dropped |

### Response translation (OpenAI → Anthropic), non-streaming

OpenAI `choices[0]` → an Anthropic `message` object:

- `id` (prefixed `msg_…`), `type:"message"`, `role:"assistant"`, `model` (echo the request model).
- `content`: `[{type:"text", text}]` for `message.content`, plus a `{type:"tool_use", id, name,
  input}` block per `message.tool_calls[]` (arguments JSON-parsed into `input`).
- `stop_reason`: `stop`→`end_turn`, `length`→`max_tokens`, `tool_calls`→`tool_use`,
  `content_filter`→`end_turn`; `stop_sequence` carried when the finish was a stop string.
- `usage`: `prompt_tokens`→`input_tokens`, `completion_tokens`→`output_tokens`.

### Response translation (OpenAI SSE → Anthropic SSE), streaming

Claude Code streams. We consume the upstream OpenAI `data:` chunks and emit the Anthropic event
sequence:

```
message_start            → {message:{id,role,model,content:[],stop_reason:null,usage:{input_tokens,output_tokens:0}}}
content_block_start      → {index, content_block:{type:"text", text:""}}        (on first text delta)
content_block_delta      → {index, delta:{type:"text_delta", text}}             (per text delta)
content_block_start      → {index, content_block:{type:"tool_use", id, name, input:{}}}  (per tool call)
content_block_delta      → {index, delta:{type:"input_json_delta", partial_json}}         (tool args)
content_block_stop       → {index}                                              (closing each open block)
message_delta            → {delta:{stop_reason, stop_sequence}, usage:{output_tokens}}
message_stop
```

A periodic `ping` event is permitted and harmless. The translator tracks which content blocks
are open and closes them in order. Streaming fails over only before the first byte, exactly as
the OpenAI path (WF-ADR-0031) — inherited for free via delegation.

### Errors

A non-2xx from the core (bad override 400, misconfigured 500, circuit-open 503, budget 402) is
re-shaped to the Anthropic error envelope `{"type":"error","error":{"type","message"}}`, with the
status preserved and the decision headers retained. The OpenAI error `type` maps to the nearest
Anthropic error `type` (e.g. 400→`invalid_request_error`, 429→`rate_limit_error`,
5xx→`api_error`, 402→`invalid_request_error`).

## Scope

**In scope (v1):** text content, `system`, multi-turn `messages`, sampling params, `stop_sequences`,
streaming, token `usage`, `stop_reason` mapping, and **tool use** (request `tools`/`tool_choice`,
assistant `tool_use`, user `tool_result`, and streaming tool calls) — the subset Claude Code
actually exercises.

**Out of scope (v1), documented and degraded cleanly, not crashed:** image/document content blocks
(vision), extended thinking blocks, prompt-caching `cache_control`, the `count_tokens` endpoint, and
the `/v1/complete` legacy Text Completions API. Unknown content-block types are skipped (their text,
if any, still scores); unknown top-level fields are ignored. These are natural follow-ups.

## Determinism & the core invariant

The adapter is pure translation around the existing router. It performs **no** scoring and makes
**no** model call. A property test asserts that for the same logical prompt, `/v1/messages` and
`/v1/chat/completions` produce the **same** `x-wayfinder-router-*` decision headers. The translation
functions are pure (dict→dict, str-iter→str-iter) and unit-tested without a network.

## Testing

- **Pure translation** (no server): Anthropic→OpenAI request (system flattening, content blocks,
  tools, tool_result→tool message); OpenAI→Anthropic response (text, tool_use, stop_reason, usage);
  SSE transform (text deltas, tool-call deltas, block open/close ordering, message_delta usage).
- **Endpoint** (`TestClient`, fake upstream): non-streaming text round-trip; streaming event
  sequence; tool-call round-trip; decision-header parity with `/v1/chat/completions`; budget degrade
  surfaced; error re-shaping; `x-api-key` accepted.
- All offline, no keys (dry-run covers the keyless decision path).

## Alternatives Considered

- **Refactor the chat handler into a shared core and call it from both endpoints.** Cleaner in
  theory, but the handler is large and well-tested; delegating to it as-is adds the adapter with
  *zero* change to the proven path. Revisit if a third surface appears.
- **A standalone proxy process (LiteLLM-style) in front of the gateway.** Concedes the surface and
  splits routing/budget/accounting across two processes. Rejected — the translation belongs in the
  gateway that owns the decision.
- **Translate to an Anthropic-native upstream instead of OpenAI.** The gateway's upstream contract
  is OpenAI Chat Completions; an Anthropic-native backend is a separate (future) provider-adapter
  concern, not this endpoint's job.

## Related

- WF-ADR-0001 (deterministic, offline, no-model-call core — the adapter scores nothing)
- WF-ADR-0004 (the OpenAI-compatible gateway this wraps)
- WF-ADR-0031 (failover — inherited via delegation), WF-ADR-0032 (budgets — inherited likewise)
- WF-DESIGN-0006 (BYO key — upstream auth is unchanged; inbound `x-api-key` is not forwarded)
- WF-DESIGN-0009 (integration recipes — Claude Code joins the recipe set)
- WF-ROADMAP-0006 (item 11: the Claude Code adapter)
