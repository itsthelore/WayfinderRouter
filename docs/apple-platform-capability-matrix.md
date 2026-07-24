# Apple platform capability matrix

Status: Phase 0 contract, 2026-07-24

This matrix records product ownership and release claims. A capability marked
planned is not implemented merely because a platform API or desktop equivalent
exists.

| Capability | macOS v0.1 | iPhone/iPad v0.2.0 | Paired host v0.2.1 |
| --- | --- | --- | --- |
| Product independence | Shipping native app | Required; no Mac/gateway | Optional extension only |
| Authoritative routing | Bundled Rust gateway | Embedded shared Rust core | Mobile plans; host advertises candidates |
| Route algorithm in Swift | Forbidden | Forbidden | Forbidden |
| Conversation owner | macOS app | Mobile app | Mobile app; host keeps bounded request records |
| Configuration | Gateway TOML through bounded native seams | Typed native store/export | Host config remains host-owned |
| Provider API keys | macOS Keychain/XPC boundary | Device-only Keychain actor | Never transferred |
| Direct cloud execution | Rust gateway | Planned direct provider adapter | Host may contribute hosted destinations |
| Apple Foundation Models | macOS XPC service | In-process Swift actor | Mac advertises its own provider separately |
| OpenAI Platform | API key | API key | Optional hosted-via-Mac destination |
| ChatGPT/Codex account | Verified desktop runtime | Not native | Available only through approved paired host |
| Kimi/Moonshot API | Not yet | Planned API-key preset | May be host-provided |
| Kimi Code account | Not implemented | Blocked by WF-QUAL-0001 | Not inferred from host installation |
| OAuth authorization code | Not generalized | Planned with PKCE | No remote login control |
| OAuth device code | Codex helper-owned for desktop provider | Planned generic engine | No remote login control |
| On-Device Only posture | Desktop offline closure | Required runtime enforcement | Host excluded |
| Local Devices posture | Not applicable | Planned | Allows opted-in local-network destinations |
| Hosted Allowed posture | Gateway policy | Planned | May include cloud-via-Mac |
| Streaming/cancellation | Shipping gateway contract | Release gate | Request reconciliation gate |
| Background completion | Gateway may outlive UI | Not promised for direct mobile | Host-owned request may reconcile |
| Thread persistence | Native macOS store | Native durable mobile store | Not host-owned |
| Local discovery | Not a client feature | Deferred to v0.2.1 | Bonjour plus manual endpoint |
| Pairing/trust | Future host service | Deferred to v0.2.1 | Revocable device credential |
| Remote account control | Loopback desktop UI only | Not permitted | Not permitted |
| Tools/filesystem/shell | Not exposed by Chat | Explicit non-goal | Explicitly denied |

## Deployment floors

- macOS remains 14 or later.
- iOS and iPadOS begin at 18 or later.
- Foundation Models is independently gated by compile-time API availability and
  the live `SystemLanguageModel` availability state. The base mobile app does
  not require a Foundation Models-capable OS or device.

## Execution-boundary examples

| Destination | Boundary rendered on iPhone/iPad |
| --- | --- |
| Apple On-Device on this iPhone | On device |
| OpenAI Platform called by iPhone | Hosted |
| Qwen running locally on Tom's Mac | Local network; Mac local |
| ChatGPT through Tom's Mac | Hosted via Tom's Mac |

## Governing documents

- WF-ADR-0047 — product independence and optional pairing
- WF-ADR-0048 — routing-core ownership and embedding
- WF-DESIGN-0019 — provider/auth/credential framework
- WF-ROADMAP-0016 — delivery and acceptance gates
- WF-DESIGN-0017 — current macOS Foundation Models topology
- WF-DESIGN-0018 — current macOS ChatGPT/Codex topology
