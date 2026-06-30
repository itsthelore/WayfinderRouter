---
schema_version: 1
id: WF-ROADMAP-0007
type: roadmap
tags: [vision, infrastructure, service, offline, local-first, gateway]
---

# Roadmap: Wayfinder as your machine's local LLM service (the near-term slice of OS-level routing)

## Status

In progress

## Context

A Show HN comment captured a real desire: **LLM routing at the OS level, like mobile data** — pay
for the inference infrastructure once, let the *device* route every app's queries appropriately, and
keep working when the network is gone. The full version (transparent OS-wide interception, kernel /
network-stack integration, carrier-style cross-device billing) is genuinely aspirational. But most
of the *felt* experience is near-term, because Wayfinder is already a local, OpenAI-compatible
endpoint that any app points at with one `base_url`, holds your provider keys in one place, and
decides local-vs-cloud **offline, with no model call**.

This roadmap delivers the achievable slice and draws an explicit line around what is not in it.

## Outcomes

- A single local endpoint, **always on**, that every OpenAI-compatible app on the machine shares —
  set up once, and the machine routes each app's LLM calls (cheap/local vs expensive/cloud).
- That endpoint **keeps working without internet**: when the network or the cloud tier is
  unreachable, it serves from the local model instead of hanging or failing.
- The deterministic, offline routing **decision** is unchanged throughout (WF-ADR-0001) — these are
  deployment and delivery features in the invocation layer.

## Initiatives

1. **Local service (WF-ADR-0038) — macOS first.** `wayfinder-router service install/uninstall/status`
   registers the gateway as a launchd LaunchAgent (macOS, primary) or systemd user unit (Linux,
   fast-follow) so it auto-starts at login on a stable `127.0.0.1:8088` and restarts if it exits.
   Apps set `OPENAI_BASE_URL` there once. This is the "set up once, every app shares it" piece —
   mostly packaging, low risk.

2. **Offline-first delivery (WF-ADR-0039) — fast-follow.** An `offline` config knob and an
   `X-Wayfinder-Offline` request header force delivery to the cheapest/local tier, reusing the
   existing `degrade` failover + circuit breaker, so a request never hangs on a cloud timeout when
   there's no network. The scored decision is still computed and reported; only delivery degrades.

## Non-goals (explicit, for this roadmap)

- **Transparent OS-wide interception** of all LLM traffic without apps opting in (per-app `base_url`
  is still required) — that needs per-OS network/TLS hooks.
- **Kernel / network-stack integration**, or a system-wide transparent proxy.
- **Carrier-style cross-device / shared-account billing.**
- **Automatic offline *detection*** (active connectivity probing) — offline is an *explicit* signal
  (config or header) in v1; reactive cloud-down already degrades via the breaker.
- **Windows native service** — guidance only in v1 (run `serve` directly).

## Success Measures

- After `service install` on macOS, the gateway is running at login with no terminal open, and any
  OpenAI-compatible app pointed at `127.0.0.1:8088/v1` is routed — keys configured once, shared.
- With `offline` set (or the header sent), a request is served from the local tier with **no cloud
  call and no timeout**; the scored decision and its headers are unchanged.
- The deterministic core stays offline and model-call-free (the standing WF-ADR-0001 CI guards pass).

## Related Decisions

- WF-ADR-0001 (deterministic, offline, no-model-call core — preserved)
- WF-ADR-0004 (the OpenAI-compatible gateway + BYO-key model)
- WF-ADR-0031 (failover policy — `degrade` is the primitive offline-first reuses)
- WF-ADR-0038 (local service surface — Initiative 1)
- WF-ADR-0039 (offline-first delivery — Initiative 2)
