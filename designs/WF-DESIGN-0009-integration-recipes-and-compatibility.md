---
schema_version: 1
id: WF-DESIGN-0009
type: design
tags: [integrations, surfaces, compatibility, onboarding, gateway, openai-compatible]
---

# WF-DESIGN-0009: Integration Recipes & OpenAI-Compatibility Hardening

## Status

Proposed

> Make "point the tool you already use at Wayfinder" a sub-minute, first-try experience. Two
> parts: (1) a **recipe pack** — copy-paste config for each common client — and (2) **gateway
> compatibility hardening** so the common breakage points (capability advertising, unexpected
> params, path handling) never bite. Pure docs + gateway hygiene; no model call (WF-ADR-0001).

## Context

A custom `base_url` is a genuine one-line swap across the surfaces developers and agent builders
live in: chat UIs (Open WebUI, LibreChat, AnythingLLM, Jan), editors (Continue, Cline, Zed,
JetBrains AI, and the chat panels of Cursor / VS Code Copilot BYOK), agent frameworks (LangChain,
LlamaIndex via `OpenAILike`, CrewAI, AutoGen, the OpenAI Agents SDK, the Vercel AI SDK), and CLIs
(aider, Copilot CLI). Since the always-on gateway is the habit surface, the fastest path to daily
use is removing every gram of friction from that swap — and time-to-first-value in the first
session is the leading indicator of whether a tool is ever adopted.

But "OpenAI-compatible" hides sharp edges, confirmed from each tool's docs:

- Several clients **require tool-calling + streaming** and refuse models that don't advertise them
  (VS Code Copilot, Copilot CLI); AutoGen requires an explicit `model_info` capability dict.
- Many clients **auto-discover** models from `GET /v1/models` (Open WebUI, LibreChat, Cline).
- Compatibility shims exist precisely because endpoints choke on unexpected fields (LibreChat's
  `dropParams`; LangChain disabling `stream_usage` against non-OpenAI base URLs).
- **Path handling** is inconsistent: some clients want `…/v1`, some the full
  `…/v1/chat/completions`; JetBrains has rewritten the path to `/v1/` (bug LLM-22911).
- `OPENAI_BASE_URL` + `OPENAI_API_KEY` env vars cover a large fraction of frameworks/CLIs at once.

## User Need

A developer or agent builder wants to drop Wayfinder in front of their existing tool, paste one
config, and have it work on the first request — without debugging "model not found", "model
doesn't support tools", or a mangled URL path.

## Design

### Recipe pack (docs)

A `docs/integrations/` section (and a short README pointer) with a copy-paste snippet per tool,
grouped: chat UIs, editors, agent frameworks, CLIs. Each recipe states the exact field (`apiBase`,
`baseURL`, `OPENAI_BASE_URL`, env var, etc.), whether to use `…/v1` or the full path, and any
caveats. Notable caveats to document honestly:

- **Cursor / VS Code Copilot inline completions stay on vendor infrastructure** — the custom
  `base_url` is honored only for the chat/plan panel. Set expectations; don't promise capture of
  inline traffic.
- **AutoGen needs a `model_info` dict**; provide a ready-made one per routed model.
- **Claude Code is Anthropic-Messages-native** — not an OpenAI swap; point to the planned adapter
  (WF-ROADMAP-0006 item 11) rather than a base_url recipe.

Optionally, `wayfinder-router doctor`/`init` can emit the recipe for a chosen tool, so the snippet
is generated with the user's actual port/model names.

### Compatibility hardening (gateway)

- **Capability-aware `/v1/models`:** advertise per routed name whether it supports tool calling,
  streaming, vision, etc., so capability-gating clients accept it. (`auto`, `prefer-local`,
  `prefer-hosted`, and configured endpoints are already listed; add capability flags.)
- **Liberal params (`dropParams`-style):** ignore/pass-through unknown request fields rather than
  erroring, and always return OpenAI-shaped `usage` so token/cost accounting downstream works.
- **Path tolerance:** accept both `…/v1` and the full `…/v1/chat/completions`, and tolerate an
  injected/duplicated `/v1` segment, eliminating a class of silent misconfigurations.
- **Document the canonical env pair** (`OPENAI_BASE_URL` + `OPENAI_API_KEY`) front and centre.

All of this is deterministic request hygiene; nothing scores or calls a model.

## Constraints

- **No model call** (WF-ADR-0001); this is documentation plus request-parsing tolerance.
- **Stay a faithful OpenAI-compatible proxy** (WF-ADR-0004): be liberal in what is accepted,
  strict and standard in what is emitted (OpenAI-shaped responses, `usage`, `/v1/models`).
- **Honest about limits:** where a surface can't be captured (Cursor/Copilot inline), say so.

## Rationale

The recipe pack is the cheapest possible work for the biggest activation lift, and compatibility
hardening converts "OpenAI-compatible (mostly)" into "works first try," which is what turns a
one-time trial into the configured default a developer then uses on every call.

## Alternatives

- **Build per-tool plugins/extensions** — higher touch and higher maintenance; unnecessary when a
  documented base_url swap plus a robust gateway covers nearly everything. Reserve bespoke work for
  the genuine gaps (the Anthropic adapter, WF-ROADMAP-0006 item 11).
- **Do nothing beyond the existing README** — leaves the sharp edges (capabilities, params, paths)
  to bite during the critical first session.

## Accessibility

Recipes are plain markdown with copy-paste blocks; no GUI step. `/v1/models` output is
machine-readable and standard.

## Open Questions

- Which tools warrant first-class recipes at launch vs a "generic OpenAI-compatible" template.
- Whether `doctor` should generate per-tool snippets, and how to keep recipes from going stale as
  tools change.
- Exact capability schema to expose in `/v1/models` (mirror OpenAI's where one exists).

## Success Measures

- A new user completes a drop-in into their tool of choice in under a minute, first try.
- Drop in support tickets / issues of the "model not found" / "doesn't support tools" / "bad path"
  class to ~zero for documented tools.
- Measurable activation lift (first routed request in the first session).

## Related

WF-ADR-0004 (OpenAI-compatible gateway), WF-ADR-0001 (no model call), WF-ROADMAP-0006 (item 1, and
item 11 for the Anthropic adapter), the existing README "Works with any OpenAI-compatible API"
section (the provider/upstream complement to this client-side work).
