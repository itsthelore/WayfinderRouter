---
schema_version: 1
id: WF-ADR-0042
type: decision
tags: [desktop, macos, tauri, menu-bar, clients, distribution, packaging, keys, offline]
---

# WF-ADR-0042: The Desktop App is a Thin Menu-Bar Client Over the Gateway (One Backend, Many Clients)

## Status

Accepted

> Supersedes WF-ADR-0027 (the thin `/demo`-URL wrapper). The spike that ADR deferred to was run;
> the outcome is bigger than a URL wrapper and different in kind: a real menu-bar app, but one that
> is architecturally forbidden from becoming a second router. This ADR is the constitution for
> `clients/` — everything in it renders decisions; nothing in it makes them.
>
> Amendment (popover size, WF-DESIGN-0014): §3's 360×480 popover is revised to **400×720**. The
> flat-list mirror of CodexBar's actual layout needs more canvas than the original spec allowed —
> at 360×480 CodexBar's own spacing/type scale read as cramped and low-contrast next to the
> reference (verified by rendering both side by side), and the height is sized so the whole menu
> — header, both metric sections, every action row, the footer — fits with no half-clipped row
> (the full list measures ~712px). The shell mechanics are unchanged: still one borderless,
> vibrant, hide-on-blur popover; `position_bottom_center` in `lib.rs` already reads the window's
> live size rather than a hardcoded constant, so only `tauri.conf.json` moves.

## Category

Technical

## Context

2026.7.0 made the gateway an always-on local service (WF-ADR-0038) with offline-first delivery
(WF-ADR-0039). What's missing is a *face*: a way to see, at a glance, that routing is on, where the
last turn went and why, and to use it — without a terminal. The natural macOS shape for an always-on
router is a **menu-bar app**: ambient status in the tray, a popover for the decision and a chat turn,
gone when you click away.

The danger is equally natural: desktop apps grow. A bespoke client that re-implements scoring, holds
keys, or grows its own delivery logic would fork the product's one non-negotiable — the routing
decision is computed **offline, deterministically, with no model call and no key** (WF-ADR-0001), and
provider keys live in the environment/secret store, never in an app or a file (WF-ADR-0004).

We studied June (`open-software-network/os-june`), a mature Tauri v2 desktop AI assistant, as a
reference. Its *craft* is worth adopting — signed/notarized universal builds, a disciplined
RC→promote release train, an auto-updater with hard key custody, restrained native-feeling design.
Its *architecture* is the opposite of ours and is explicitly rejected: June's model calls go to a
mandatory remote backend that holds the provider keys (made trustworthy with TEE attestation).
Wayfinder needs none of that machinery because nothing leaves the machine to make a decision — the
honest statement of our posture is an app that *provably does less*.

## Decision

1. **One backend, many thin clients.** The desktop app is a renderer over the local gateway's
   loopback HTTP API (`/v1/chat/completions` + `X-Wayfinder-Debug`, `/healthz`, `/router/models`,
   `/v1/savings`). It never scores, never forwards to a provider, never holds a key. The shared wire
   client and render helpers live in `clients/shared` (`@wayfinder/shared`) and are the only way
   clients consume decisions.

2. **The embedded JS scorer is a parity-gated degraded mode, not a second source of truth.**
   `clients/shared/src/scorer.js` is a byte-for-byte port of `wayfinder_router/complexity`, trusted
   only while the golden-corpus parity job (`tools/golden.py` → `clients/shared/test/parity.mjs`,
   blocking in CI) is green. It runs **only** when the gateway is unreachable, and its output is
   always framed as a preview ("local mirror — start the gateway"), never as a routed decision.

3. **Tauri v2, menu-bar accessory shell.** No Dock icon; a template tray icon with three health
   states; one borderless 360×480 vibrancy popover (hide-on-blur, state preserved); ⌥Space toggle
   (rebindable); single-instance. The webview reaches the gateway by direct loopback fetch — not
   Rust IPC — so the Rust command surface and capabilities stay minimal and auditable.

4. **Service-first lifecycle — the app never owns the gateway process.** Detect via `/healthz` and
   attach. When nothing is running, the primary CTA is one-click **Install the service**, shelling
   out to the existing `wayfinder-router service install` (WF-ADR-0038); Start/Stop go through
   `launchctl`/the `service` verbs. Two launch agents, two jobs: `tauri-plugin-autostart` starts the
   *app* at login; the WF-ADR-0038 agent starts the *gateway*. No app-owned supervisor, no
   dual-supervisor fight over `:8088`.

5. **Keys go to the Keychain via `api_key_cmd` — the app is glue, not a keeper.** First-run may
   scaffold a config (shell-out to `init --preset`) and accept a provider key, but the key is written
   straight to the macOS Keychain (`security add-generic-password`) and only an
   `api_key_cmd = "security find-generic-password -w …"` reference lands in the gateway config —
   the existing WF-ADR-0004 secret-store seam. The key never enters the webview's state, the app's
   storage, or any file.

6. **Platform ruling: Tailwind v4 + macOS 14.0 minimum.** The UI stack is React 19 + Tailwind v4 +
   shadcn/ui themed with the canonical Wayfinder tokens (WF-DESIGN-0012). Tailwind v4 needs Safari
   16.4+; WKWebView tracks the OS, so the bundle minimum rises from 13.0 to **14.0** (macOS 13 has
   left Apple security support; June ships 14+ too). Windows/Linux are non-goals for v1 — the shared
   core stays portable, the shell does not.

7. **Distribution follows June's playbook, minus its backend.** Signed + notarized + stapled
   universal DMG; the Tauri updater with `createUpdaterArtifacts` and a keypair whose private key is
   custodied from day one (its loss permanently bricks auto-update); releases on a `desktop-v*` tag
   lane (verified: matches neither of `release.yml`'s globs, so it cannot fire the PyPI publish);
   right-sized RC→promote; generated `THIRD_PARTY_NOTICES`. The PyInstaller sidecar (bundling the
   gateway into the app) is **deferred**: PyInstaller's self-extraction interacts badly with the
   hardened runtime notarization requires, and universal2 Python builds are their own fight — the
   service-first model makes the sidecar unnecessary for v1.

8. **The privacy story is told honestly.** A "verify-lite" panel states exactly what is true: the
   decision is computed locally with no model call and no key in the app; prompts go only to the
   provider you route to, under your own keys; **offline mode** (WF-ADR-0039) is the only mode that
   guarantees nothing leaves — and it is a one-click toggle in the popover. No claim of "your data
   never leaves your machine" outside that mode. No telemetry, ever.

## Consequences

### Positive

- The decision core and key discipline survive contact with a GUI: the app *cannot* drift into a
  second router because it has no scoring path (except the parity-gated mirror) and no key storage.
- Ambient, glanceable proof that routing works — the product story WF-ROADMAP-0007 wants — with a
  chat surface good enough for daily use.
- The distribution craft (signing, updater, RC train) is inherited from a studied reference instead
  of discovered by trial and error.

### Negative

- A Rust/Node toolchain now lives beside the Python one (accepted in WF-ADR-0027's risks; contained
  to `clients/`).
- First-run on a machine with no Python/pipx requires installing the gateway before the app is
  useful — the cost of refusing the sidecar in v1.

### Risks

- **Parity drift.** The JS scorer silently diverging from Python would make the degraded mode lie.
  Mitigation: the parity job is blocking CI; the corpus regenerates from the real Python scorer on
  every run; divergence fails the build.
- **Shell-out surface.** `service install` / `launchctl` / `security` invocations are privileged
  glue. Mitigation: exact commands only, scoped in Tauri capabilities; no arbitrary shell.
- **Updater key custody.** One private key, permanent consequences. Mitigation: generated once,
  stored in the password manager + CI secret, documented in `docs/RELEASE-desktop.md`.

## Alternatives Considered

### A thin wrapper over `/demo` (WF-ADR-0027's proposal)

Cheapest, but it can't do the job: no tray presence, no service control, no offline toggle, no
first-run story — a browser tab in a costume. Superseded.

### June's architecture (remote backend holds keys, TEE attestation)

Rejected on principle: Wayfinder's differentiator is that no backend is *required* and no key ever
leaves the user's control. Adopting a mandatory remote backend — however well attested — would
delete the product's reason to exist.

### App-owned gateway supervisor (detect-then-spawn)

The app spawning and supervising the gateway duplicates WF-ADR-0038's launchd agent and creates the
two-supervisors-one-port failure mode. Rejected for v1; the service is the supervisor.

### Electron

A heavier runtime and bundle for no capability we need; Tauri's Rust shell + system webview fits a
menu-bar utility and keeps the binary small.

## Success Measures

- The app renders correct decisions with the gateway up, degraded, decision-only, offline, and
  unreachable — and in the last case is unmissably labelled a preview.
- `rg -i "api[_-]?key" clients/desktop/src` finds no key handling outside the Keychain glue command.
- A signed, notarized DMG installs on a clean macOS 14 machine, passes Gatekeeper, and updates
  in place from the previous release.
- The Python suite and the deterministic core are untouched by the entire `clients/` tree.

## Related

- WF-ADR-0027 (superseded — the URL-wrapper direction)
- WF-ADR-0001 / WF-ADR-0004 (the invariants this ADR exists to protect)
- WF-ADR-0038 (the service this app is a client of) · WF-ADR-0039 (the offline mode it surfaces)
- WF-ADR-0020 (the decision-first design language the popover inherits)
- WF-DESIGN-0012 (the popover design contract) · WF-ROADMAP-0009 (the delivery plan)
