---
schema_version: 1
id: WF-DESIGN-0018
type: design
tags: [gateway, rust, macos, codex, chatgpt, oauth, provider, chat]
---

# Design: bounded Codex app-server provider with managed ChatGPT authentication

## Status

Accepted

> Desktop v0.1.0 release amendment (WF-ROADMAP-0015): the provider ships as an opt-in destination
> that depends on the separately installed, correctly signed ChatGPT app at
> `/Applications/ChatGPT.app`. Wayfinder v0.1.0 does not bundle or redistribute Codex and does not
> claim this provider is self-contained. Release discovery ignores development overrides and rejects
> colocated or sibling executables; only the fixed ChatGPT-app runtime may pass the production trust
> checks. A bundled runtime remains possible only through a later reviewed packaging decision.
>
> Mobile amendment (WF-ROADMAP-0016): ChatGPT/Codex is not a native iOS account provider. OpenAI
> Platform remains a separate direct API-key provider. Until an official permitted mobile account
> execution contract exists, iPhone and iPad may access this destination only through an explicitly
> paired Mac, where it is labelled `hosted via <Mac>`. No Codex credential or helper moves to iOS.

## Summary

Wayfinder adds an explicit `codex-app-server` gateway provider for people who want to use the
models available through their ChatGPT Codex entitlement without supplying an OpenAI Platform API
key. The existing `openai-compatible` provider remains the direct API-key route. These are distinct
products, authentication contracts, and billing paths; neither silently substitutes for the other.

The native app remains a thin gateway client. It displays normalized account state and starts or
cancels managed sign-in through a loopback-only gateway control surface. Swift never receives an
access token, reads Codex auth files, launches the Codex helper, or expands the Wayfinder credential
broker. The Rust gateway owns one bounded Codex app-server process and translates its text response
events into Wayfinder's existing OpenAI-compatible response contract.

Signing in is configuration, not a routing decision. It does not add the provider to an existing
route ladder, make it the preferred hosted route, or change the desktop Chat destination from
`Automatic`.

## Provider configuration

The provider is opt-in and keyless from Wayfinder's perspective:

```toml
[gateway.models.chatgpt]
provider = "codex-app-server"
model = "gpt-5.6-sol"
context_window = 1050000
```

For `codex-app-server` models:

- `model` is required and must be present in the runtime `model/list` response before delivery;
- `base_url`, `api_key_env`, `api_key_cmd`, and native `tier` are invalid;
- the model is always hosted and is excluded while gateway offline mode is active;
- no dollar-cost estimate is invented for ChatGPT subscription usage;
- logged-out, expired, usage-limited, and runtime-unavailable are provider readiness states, not
  missing API keys.

## Runtime boundary

The gateway launches a version-compatible `codex app-server --listen stdio://` child only when a
Codex model or account operation is used. Development builds may use an explicitly configured or
colocated helper. Release builds accept only the separately verified helper installed with the
ChatGPT app; an executable placed beside the gateway is never sufficient production trust.

Desktop v0.1.0 deliberately ships that external dependency: the app does not contain Codex and the
provider is unavailable when the verified ChatGPT installation is absent or incompatible. A future
self-contained Wayfinder build would require a separate reviewed decision covering licensing,
pinning, architecture, nested signing, and recorded version/digest verification; it is not a v0.1.0
release gate.

The child receives:

- a Wayfinder-owned `CODEX_HOME` under Application Support, separate from `~/.codex`;
- Codex-managed `chatgpt` browser or device-code authentication only;
- no `chatgptAuthTokens`, API-key login, token import, or auth-file reuse;
- a Wayfinder-owned empty workspace rather than the user's project or home directory;
- analytics disabled;
- shell, unified execution, browser, apps, plugins, multi-agent, computer-use, hooks, skills,
  workspace dependency, and other tool-bearing features disabled;
- a deny-by-default permission profile limited to the minimum runtime paths and the empty chat
  workspace, with sandboxed tool network access disabled;
- a fixed general-chat base instruction that forbids tools and filesystem activity in addition to
  the enforceable runtime restrictions.

Codex owns token persistence and refresh. The first implementation uses its documented file store
inside the isolated `CODEX_HOME`, creates the directory with owner-only permissions, and verifies
credential-file permissions after login. Wayfinder never deserializes or logs that file.

## Bounded protocol

The child protocol is newline-delimited JSON using the documented app-server JSON-RPC shape. The
gateway must enforce:

- one `initialize` / `initialized` handshake per child;
- monotonically allocated request ids and exact response correlation;
- finite request, login, turn-idle, and shutdown deadlines;
- maximum JSONL line, pending-request, notification-queue, transcript, response, and stderr-tail
  sizes;
- one active inference turn in v0.1.0; a concurrent turn reports bounded Busy without affecting
  circuit-breaker health;
- `turn/interrupt` on downstream cancellation, followed by a finite kill grace period;
- sanitized errors on malformed messages, protocol skew, process EOF, timeout, or restart;
- a finite restart budget with no retry for signed-out or re-authentication-required failures.

Protocol tests use an in-memory/scripted transport and do not require an OpenAI account. Live tests
are a separate release gate.

## Chat-completions translation

Codex app-server is an agent runtime, not a generic OpenAI API bearer-token endpoint. The adapter
therefore accepts only the narrow subset Wayfinder Chat needs:

- text-only `system`, `developer`, `user`, and `assistant` messages;
- exactly one generated choice;
- buffered and streaming text output;
- no client tools, `tool_choice`, structured response formats, log probabilities, audio, or
  unsupported multimodal message parts.

Each gateway request creates an ephemeral Codex thread. System/developer content becomes bounded
thread instructions; completed prior messages are injected as model-visible history; the final user
message starts the turn. Only final assistant text enters the transcript. Any command, file-change,
tool, approval, or other agent-action item fails the request closed and interrupts the turn.

Streaming agent-message deltas are translated into OpenAI-compatible SSE chunks. The authoritative
completed agent item supplies the buffered answer. `turn/completed` is required before success;
`failed` and `interrupted` remain distinct terminal states.

## Local account API

The gateway exposes a normalized account surface only when its listener is bound to a literal
loopback address:

- `GET /router/codex/account`
- `GET /router/codex/models`
- `POST /router/codex/login`
- `POST /router/codex/login/cancel`
- `POST /router/codex/logout`

Every request requires `X-Wayfinder-Local-Control: 1`; mutating calls require JSON. The gateway does
not emit CORS headers. The custom header provides a browser-CSRF boundary, while literal-loopback
gating prevents these controls from appearing on a network-exposed gateway. The API returns only
normalized runtime/account/model data, login ids, browser/device URLs, user codes, and sanitized
errors. It never returns tokens or auth-file locations.

## Native macOS contract

Settings gains an **Accounts** section distinct from **Keys**. ChatGPT account states are Checking,
Signed Out, Awaiting Browser, Awaiting Device Code, Connected, Re-authentication Required,
Unavailable, and Failed. Connected state may display bounded email, plan, and model-catalog data.
Sign-out is confirmed. OpenAI Platform key controls remain unchanged under Keys.

The Chat composer gains a compact destination menu:

- `Automatic — Wayfinder chooses` remains selected by default;
- explicit configured route aliases are available;
- a ChatGPT route is advertised only when its configured model appears in `model/list`;
- a pinned ChatGPT route never silently falls back when signed out or unavailable;
- destination changes are disabled during an active response;
- account identity and plan stay in Settings, while route/provider/model/access metadata stays in
  the existing right-hand routing inspector.

## Privacy and security claims

ChatGPT-authenticated requests leave the Mac and follow the signed-in ChatGPT workspace's policies.
Offline mode disables this provider. Wayfinder does not claim these calls are private/local and does
not equate ChatGPT subscription authentication with general OpenAI API access.

Release acceptance requires adversarial live evidence against the exact helper build supplied by
the supported ChatGPT app: prompts must not read outside the empty workspace, execute commands,
mutate files, access tools, or make sandboxed network calls. If that cannot be proven, the adapter
does not ship as an ordinary Chat provider and must instead become an explicit Codex-agent surface
with its own activity and approval UX.

## Explicit non-goals

- Replacing the API-key OpenAI provider or changing any existing provider default.
- Reading, importing, forwarding, or refreshing OAuth tokens in Wayfinder code.
- Extending `WayfinderCredentialBroker` or sharing `~/.codex` state.
- Exposing Codex tools, approvals, filesystem actions, plans, or agent activity in v0.1.0 Chat.
- Treating a ChatGPT subscription as authorization for arbitrary OpenAI REST API calls.
- Bundling or redistributing an unpinned helper, or silently accepting incompatible app-server
  schemas.
- Claiming that Desktop v0.1.0 includes Codex or works with this provider when the separately
  installed verified ChatGPT app is absent.

## Verification

Deterministic coverage must include configuration rejection/round-tripping, fragmented and
oversized JSONL, handshake ordering, request-id correlation, auth transitions, login cancellation,
model discovery, tool-item fail-closed behavior, ordered deltas, authoritative final output,
interrupt, concurrent-turn Busy handling without circuit-breaker poisoning, timeout, process
EOF/restart, release rejection of unverified sibling helpers, loopback/control-header enforcement,
offline exclusion, and Swift account/destination state transitions.

The signed-app release gate additionally covers login, refresh, logout, sleep/wake, helper signing
and architecture, cancellation, Sol delivery, a missing/expired account, and the adversarial
isolation cases above.

## References

- OpenAI Codex app-server protocol and auth: https://learn.chatgpt.com/docs/app-server
- OpenAI authentication boundary: https://learn.chatgpt.com/docs/auth#openai-authentication
- OpenAI permission profiles: https://learn.chatgpt.com/docs/permissions
- WF-ADR-0042 (one backend, thin native client)
- WF-ROADMAP-0012 (focused native Chat and routing inspector)
- WF-ROADMAP-0015 (Apple Silicon desktop v0.1.0 release contract)
- WF-ROADMAP-0016 (standalone native mobile and optional paired-host contract)
