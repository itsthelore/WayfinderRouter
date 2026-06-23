# Integration recipes

Wayfinder's gateway speaks the OpenAI API, so almost anything that takes a custom
`base_url` works with **one line of config** — no SDK, no plugin. Two rules cover
nearly every tool:

1. **Point `base_url` at the gateway** — `http://localhost:8088/v1` (or your host).
   The gateway is path-tolerant, so the bare host (`http://localhost:8088`, no `/v1`)
   also works.
2. **Use `auto` as the model** — that's the routing directive that means "let Wayfinder
   decide." (You can also pin `prefer-local` / `prefer-hosted`, or a configured endpoint
   name, per request.) The API key can be any non-empty string unless you've put auth in
   front of the gateway; Wayfinder injects the real upstream key from its own config.

The most portable setup is the canonical env pair, which a large fraction of frameworks
and CLIs read automatically:

```bash
export OPENAI_BASE_URL="http://localhost:8088/v1"
export OPENAI_API_KEY="unused"   # any non-empty value; the real key lives in the gateway
```

---

## Chat UIs

**Open WebUI** — Admin Settings → Connections → OpenAI → Add Connection, or via env:

```bash
OPENAI_API_BASE_URL="http://localhost:8088/v1"
OPENAI_API_KEY="unused"
```

**LibreChat** — add a custom endpoint in `librechat.yaml`:

```yaml
endpoints:
  custom:
    - name: "Wayfinder"
      baseURL: "http://localhost:8088/v1"
      apiKey: "unused"
      models:
        default: ["auto"]
        fetch: true   # auto-discover via /v1/models
```

**Jan** — Settings → Model Providers → "+", set Base URL `http://localhost:8088/v1`, API
key `unused`, format **OpenAI**, model `auto`.

**AnythingLLM** — LLM provider "Generic OpenAI": Base URL `http://localhost:8088/v1`,
API key `unused`, model `auto`.

---

## Editors / IDE assistants

**Continue.dev** — `config.yaml`:

```yaml
models:
  - name: Wayfinder
    provider: openai
    model: auto
    apiBase: http://localhost:8088/v1
    apiKey: unused
```

**Cline** — provider **OpenAI Compatible**: Base URL `http://localhost:8088/v1` (note: the
`/v1`, not the full `/chat/completions` path), API key `unused`, Model ID `auto`.

**Zed** — `settings.json`:

```json
{ "language_models": { "openai_compatible": { "Wayfinder": {
  "api_url": "http://localhost:8088/v1"
} } } }
```

**JetBrains AI Assistant** — Settings → Tools → AI Assistant → Providers & API keys → set a
custom OpenAI-compatible Base URL `http://localhost:8088/v1` and Test Connection.

> **Cursor / VS Code Copilot caveat.** Both honor a custom OpenAI base URL only for their
> **chat/plan** panels — autocomplete, inline edit, Composer, and "apply" stay on the
> vendor's own backend and cannot be routed through Wayfinder. Use them for chat; don't
> expect inline-completion traffic to flow through the gateway.

---

## Agent frameworks

**OpenAI SDK (Python / JS)** — set `base_url` / `baseURL`, or just the env pair above:

```python
client = openai.OpenAI(base_url="http://localhost:8088/v1", api_key="unused")
```

**LangChain** — `ChatOpenAI(model="auto", base_url="http://localhost:8088/v1", api_key="unused")`
(or the `OPENAI_BASE_URL` env var).

**LlamaIndex** — use `OpenAILike` (the base `OpenAI` class is pinned to GPT model names):

```python
from llama_index.llms.openai_like import OpenAILike
llm = OpenAILike(model="auto", api_base="http://localhost:8088/v1", api_key="unused")
```

**CrewAI** — `LLM(model="openai/auto", base_url="http://localhost:8088/v1", api_key="unused")`
(the `openai/` prefix is required).

**AutoGen** — custom endpoints need an explicit capability dict:

```python
OpenAIChatCompletionClient(
    model="auto", base_url="http://localhost:8088/v1", api_key="unused",
    model_info={"function_calling": True, "vision": False, "json_output": True, "family": "unknown"},
)
```

**OpenAI Agents SDK (Python)** — `set_default_openai_client(AsyncOpenAI(base_url="http://localhost:8088/v1", api_key="unused"))`, or the `OPENAI_BASE_URL` env var.

**Vercel AI SDK** — `createOpenAICompatible({ name: "wayfinder", baseURL: "http://localhost:8088/v1", apiKey: "unused" })` from `@ai-sdk/openai-compatible`.

---

## Terminal / CLI agents

**aider**:

```bash
export OPENAI_API_BASE="http://localhost:8088/v1"
export OPENAI_API_KEY="unused"
aider --model openai/auto
```

**GitHub Copilot CLI** (BYOK):

```bash
export COPILOT_PROVIDER_BASE_URL="http://localhost:8088/v1"
export COPILOT_PROVIDER_API_KEY="unused"
export COPILOT_MODEL="auto"
```

> **Claude Code caveat.** Claude Code speaks Anthropic's Messages API, not OpenAI Chat
> Completions, so `ANTHROPIC_BASE_URL` can't point at this gateway directly. A first-class
> `/v1/messages` (Anthropic-format) adapter is planned — see WF-ROADMAP-0006.

---

## Notes

- **Streaming** works end to end: send `stream: true` and the gateway relays Server-Sent
  Events as they arrive.
- **Tool calling / vision** depend on the *upstream* model you route to, not on Wayfinder —
  the gateway forwards your request body unchanged (plus the resolved model id).
- **Per-request overrides** travel as headers (e.g. `X-Wayfinder-Threshold`), so you can
  tune routing without changing client config.

See WF-DESIGN-0009 (integration recipes & OpenAI-compatibility) and WF-ROADMAP-0006 for the
roadmap this is part of.
