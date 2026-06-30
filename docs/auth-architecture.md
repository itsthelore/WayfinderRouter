# Authentication & user-owned model access

> Status: **design** (target ADR: WF-ADR-0043). This documents the intended architecture; the current
> shipping mechanism is the `api_key` mode described under [Today](#today). Everything else is the
> planned generalization.

## Principle

Wayfinder routes; **the user owns model access, model choice, credentials, spend, and risk.** This is not
a new stance — it is [WF-ADR-0001](../decisions/WF-ADR-0001-standalone-deterministic-router.md) (the scored
decision is offline, with no model call/key/network) and WF-ADR-0004 (bring-your-own-key) made explicit and
generalized. Wayfinder never owns or proxies model *spend*; it only authenticates the **upstream** call on
the user's behalf, using a credential the user controls.

The design goal: support many ways to authenticate an upstream tier — API keys, OAuth sign-in, cloud
identity, GitHub tokens, IAM roles, local endpoints, MCP — **without coupling the gateway to any one
provider, and without the decision core ever seeing a credential.**

## Today

A model tier authenticates with a static key, resolved at request time and sent as `Authorization: Bearer`:

```toml
[gateway.models.cloud]
base_url   = "https://api.openai.com/v1"
model      = "gpt-4o"
api_key_env = "OPENAI_API_KEY"          # env-var NAME only; the secret is never in config
api_key_cmd = "security find-generic-password -w -s OPENAI_API_KEY"  # optional: secret-store fetch
```

Keys live in the environment (or a secret store via `api_key_cmd`, [WF-DESIGN-0006](../designs/WF-DESIGN-0006-key-experience.md));
the config names only the variable; `wayfinder-router doctor` reports whether each resolves. This is the
`api_key` credential mode — the baseline the rest of the design generalizes from.

## The seam

A single **credential-provider** point in the gateway request path resolves a tier's credential into the
upstream auth — replacing today's hard-coded "read `api_key_env` → Bearer":

```python
class Credential(Protocol):
    provider: str            # "openai" | "anthropic" | "google" | "github_models" | "azure_openai" | "aws_bedrock" | "local" | "mcp"
    mode: str                # see Credential modes
    def is_available(self) -> bool: ...
    def headers(self) -> dict[str, str]: ...   # e.g. {"Authorization": "Bearer …"} | {"x-api-key": …}
    def base_url(self) -> str | None: ...        # override when the upstream surface differs
    def billing_boundary(self) -> str: ...       # where usage is charged / limited
    def is_interactive_only(self) -> bool: ...
    def is_ci_safe(self) -> bool: ...
```

The execution layer never cares whether the credential came from an env key, an OAuth session, a GitHub
token, an IAM role, or a local endpoint. **The scored decision still never reads a credential — this is all
invocation-layer (WF-ADR-0001 unchanged).**

## Credential modes (vendors are adapters under these)

```
CredentialMode                         Provider adapter
  ├── api_key                            ├── openai
  ├── browser_oauth      (PKCE)          ├── anthropic
  ├── device_code_oauth  (RFC 8628)      ├── google
  ├── pat_token                          ├── github_models
  ├── service_account                    ├── azure_openai
  ├── cloud_identity     (ADC)           ├── aws_bedrock
  ├── managed_identity   (Entra)         ├── local
  ├── iam_role           (SigV4)         └── mcp
  ├── actions_token      (GITHUB_TOKEN)
  └── local_endpoint
```

## Config & metadata model

Non-secret metadata lives in `wayfinder-router.toml`; **secrets never do** — they come from an env var, the
OS keychain (`api_key_cmd`), cloud identity, a provider-native store, or a CI secret store. Today's
`api_key_env`/`api_key_cmd` remain valid as shorthand for `mode = "api_key"`; richer modes use an `[auth]`
table:

```toml
[gateway.models.azure.auth]
mode = "managed_identity"        # Microsoft Entra ID; no key in config

[gateway.models.gh.auth]
mode = "actions_token"           # GitHub Models via $GITHUB_TOKEN

[gateway.models.codex.auth]
mode = "browser_oauth"           # OpenAI "Sign in with ChatGPT" (local interactive only)
```

Every resolved credential declares: `provider`, `mode`, `source`, `billing_boundary`, `ci_safe`,
`interactive_only` — surfaced by `wayfinder-router auth status`, `doctor`, `/healthz`, and `/router/models`.

## Provider adapter shapes (the OpenAI-compatible lever)

Wayfinder forwards an OpenAI-compatible request, so adapters fall into three shapes:

1. **Native OpenAI-compatible — a credential swap only.** OpenAI / Anthropic API keys, **Azure OpenAI**
   (Entra bearer → its OpenAI-compatible endpoint), **GitHub Models** (a GitHub token → its
   OpenAI-compatible endpoint), and **local** (Ollama / LM Studio / vLLM / LocalAI). These are pure
   `headers()` / `base_url()` — the easy, high-value cases.
2. **Different surface — needs a protocol adapter** (like the existing Anthropic `/v1/messages` adapter):
   OpenAI **ChatGPT/Codex sign-in** (the Codex *Responses* backend), native **Vertex AI** / **Bedrock** wire
   formats. Build an adapter, or compose (below).
3. **Compose — zero new code, works today.** Point a tier's `base_url` at **LiteLLM** (or any
   OpenAI-compatible enterprise proxy) with a virtual key in `api_key` mode; LiteLLM performs the hard
   provider auth (Entra / SigV4 / ADC) downstream. This is the pragmatic path for AWS SigV4 and anything
   exotic, and matches Wayfinder's "compose *with* provider gateways" positioning.

## The dual-auth path (the concrete first slice)

For OpenAI and Anthropic, two coexisting modes per provider — **API key (default, CI-safe) and account
sign-in (opt-in, local-interactive only):**

```toml
# OpenAI — API key (recommended default; CI/automation)
[gateway.models.openai.auth]
mode = "api_key"                 # billing: OpenAI Platform account; ci_safe: yes
env_var = "OPENAI_API_KEY"

# OpenAI — ChatGPT/Codex sign-in (local interactive only)
[gateway.models.openai_chatgpt.auth]
mode = "browser_oauth"           # billing: ChatGPT plan limits; ci_safe: NO; interactive_only: yes
```

Same shape for Anthropic (`api_key` ↔ `claude` sign-in). Critical honesty, baked into the docs and the
mode metadata:

- **API-key usage is billed separately from consumer chat subscriptions** — `OPENAI_API_KEY` is the metered
  Platform account, *not* your ChatGPT plan. Wayfinder must never imply otherwise.
- **Account sign-in is local-interactive only**, and routing a *consumer subscription* token through a
  third-party gateway is **ToS-gray to prohibited** — Anthropic explicitly bans third-party reuse of
  Pro/Max OAuth tokens (Feb 2026); OpenAI steers programmatic use to API keys. So `browser_oauth`/`claude`
  ship as a **flagged, opt-in, personal-use** mode — never the default, never CI, with a clear warning —
  and may break as providers change their flows. The gateway's backbone stays API keys + cloud identity.

## CLI

```bash
wayfinder-router auth login openai                 # default mode (api_key)
wayfinder-router auth login openai --mode chatgpt  # local-interactive sign-in
wayfinder-router auth login anthropic --mode api-key|claude
wayfinder-router auth login google   --mode oauth|api-key|adc|service-account
wayfinder-router auth login github   --mode pat|app|actions-token
wayfinder-router auth login azure    --mode api-key|entra|managed-identity
wayfinder-router auth login aws      --mode profile|iam-role|access-key
wayfinder-router auth login local    --provider ollama|lmstudio --base-url http://localhost:11434
wayfinder-router auth login mcp <server-name>      # RAC-style remote MCP auth (deferred)

wayfinder-router auth status [provider]            # provider · mode · source · billing · ci-safe · interactive-only
wayfinder-router auth logout [provider | --all]
```

These extend today's `init` / `doctor` (which already reports key resolution). Example `auth status`:

```
Provider: openai   Mode: api_key        Source: env:OPENAI_API_KEY   Billing: OpenAI Platform account   CI-safe: yes  Interactive-only: no
Provider: openai   Mode: chatgpt_oauth  Source: local token store    Billing: ChatGPT plan limits        CI-safe: no   Interactive-only: yes
Provider: local    Mode: local_endpoint Source: http://localhost:11434  Billing: your infrastructure     CI-safe: yes  Interactive-only: no
```

## Billing boundary & CI safety

Every mode declares **where usage is charged** and **whether it is CI-safe** — so users don't wrongly assume
their ChatGPT/Claude subscription covers API-key usage, and so automation doesn't pick an interactive mode.

| CI-safe | Not CI-safe (interactive-only) |
|---|---|
| `api_key` (env), GitHub Actions token, GitHub App token, Google ADC / service account, Azure managed identity, AWS IAM role, local endpoint, enterprise proxy | browser OAuth, ChatGPT sign-in, Claude sign-in, desktop-only token stores |

**Headless guardrail:** in a headless/CI context (`CI=true`, no TTY/browser, or `[gateway] interactive =
false`), the gateway **refuses an interactive-only mode** with a clear error rather than hanging on a
browser:

```
This credential mode is interactive-only and is not suitable for CI.
Use an API key, a service account, a GitHub token, managed identity, or an IAM role instead.
```

## OAuth patterns (for any sign-in mode)

- **Authorization Code + PKCE** (loopback redirect) → interactive local sign-in; the default.
- **Device Authorization Grant** (RFC 8628) → headless/SSH/container; phishable, so gate behind a flag.
- Store tokens in the **OS keychain** (macOS Keychain / Windows Credential Locker / Linux Secret Service),
  never plaintext; short-lived access tokens with refresh-token rotation + reuse detection.

## MCP (deferred; reframed for Wayfinder)

Wayfinder is a router, not a product-knowledge platform, so MCP is lower-priority here than for a knowledge
tool. Two future roles, planned for in the seam (`mode = "mcp_oauth"`) but not implemented now:
- **Wayfinder as MCP server** — expose routing/decision tools; production serving should require auth
  (`mcp serve --auth oauth|token|none`, default `none` for local only).
- **Wayfinder as MCP client** — route to MCP-authenticated model servers as another adapter.

## Implementation order

1. **Credential-provider seam** + `api_key` providers (env + `api_key_cmd`) reproducing today's behaviour
   byte-for-byte (regression-guarded).
2. **`[gateway.models.*.auth]` config** + validation + `dump_gateway_toml` round-trip + metadata in
   `auth status` / `doctor` / `/healthz` / `/router/models`.
3. **Local endpoints** formalized (`mode = local_endpoint`, keyless) — fits local-first positioning.
4. **GitHub Models** (`actions_token` / `pat`) — GitHub-native; OpenAI-compatible; matters for PR workflows.
5. **Compose-with-LiteLLM** documented (zero code) — unlocks cloud-IAM-backed tiers immediately.
6. **OpenAI ChatGPT/Codex sign-in** (`browser_oauth` + `device_code_oauth` + the Codex adapter) — flagged.
7. **Anthropic Claude sign-in** — flagged, with the explicit ToS caveat.
8. **Azure Entra / managed identity**, then **Google ADC / service account** (clean bearer flows).
9. **AWS IAM / SigV4** (native request-signing) — or keep delegating to LiteLLM / a Bedrock gateway.
10. **MCP OAuth** — once/if Wayfinder exposes an MCP surface.

Rationale: API keys work everywhere; local + GitHub Models + LiteLLM-composition give most of the matrix
with little new code; subscription sign-in is useful but never the foundation; enterprise cloud identity and
MCP come later.

## Documentation language

- **Don't** say: *"Use your ChatGPT subscription with Wayfinder."*
- **Do** say: *"Wayfinder supports user-owned model access. Depending on the provider and credential mode,
  usage may be billed through your API platform account, your cloud account, your GitHub organisation, your
  eligible subscription plan, your enterprise proxy, or your local infrastructure."*
- API keys: *"API-key usage is usually billed separately from consumer chat subscriptions — check your
  provider's billing and limits before enabling AI features."*
- Sign-in modes: *"Account sign-in is for local interactive use where the provider supports it; do not
  assume it works in CI/headless, and note that some providers prohibit third-party programmatic reuse of
  subscription tokens."*
- Local: *"Local endpoints avoid external model billing but require you to run and maintain the model
  infrastructure."*

## Acceptance criteria

A provider-neutral credential abstraction (1); a clear distinction between API keys, OAuth sessions, cloud
identity, local endpoints, and MCP (2); a per-mode billing boundary (3) and CI-safe flag (4); GitHub-native
workflows (5); local / OpenAI-compatible base-URL support (6); a future path for ChatGPT/Codex and Claude
sign-in (7) and for Google ADC / Azure managed identity / AWS IAM / MCP OAuth (8); honest billing docs (9);
and an ADR capturing the decision (10, WF-ADR-0043).

## Honest notes

- **Decision core untouched** — every mode is invocation-layer; the scored route stays offline,
  deterministic, and keyless (WF-ADR-0001).
- **The OpenAI-compatible front is the lever** — most modes are a credential swap on an OpenAI-compatible
  endpoint (OpenAI, Anthropic, Azure, GitHub Models, local). Only genuinely-different surfaces (ChatGPT-Codex,
  native Vertex/Bedrock) need an adapter — or compose via LiteLLM.
- **Subscription sign-in is a flagged opt-in, never the backbone** — ToS-gray (OpenAI) to banned (Anthropic)
  for third-party programmatic reuse, and provider-volatile. Ship it local-interactive-only with the
  billing/ToS warning.
- **Interactive sign-in's natural home is a local client** (the planned desktop app / TUI), not the headless
  gateway; the gateway's modes are the CI-safe ones (keys, cloud identity, tokens, local).
