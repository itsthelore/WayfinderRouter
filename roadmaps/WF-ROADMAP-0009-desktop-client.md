---
schema_version: 1
id: WF-ROADMAP-0009
type: roadmap
tags: [desktop, macos, tauri, clients, distribution, signing, notarization, updater, shadcn]
---

# Roadmap: the Wayfinder Desktop client — from scaffold to signed, self-updating menu-bar app

## Status

Accepted

## Context

Branch `claude/desktop-tauri-app` already carries the first two slices of WF-ADR-0042: the
`clients/` npm workspace with `@wayfinder/shared` (wire client + byte-for-byte JS scorer behind a
blocking golden-parity CI job) and the Tauri v2 menu-bar shell (vibrant 360×480 popover, tray,
⌥W, hide-on-blur, single-instance). This roadmap takes it to a shippable app: the decision-first
popover (WF-DESIGN-0012), service-first lifecycle, Keychain-glue onboarding, and a signed, notarized,
auto-updating DMG — the distribution playbook adapted from the June study, minus its remote backend.

Decisions already made (WF-ADR-0042, confirmed with the maintainer): service-first (the app never
owns the gateway process), Keychain via `api_key_cmd`, chat included in v1, Tailwind v4 + macOS 14.0
minimum, no PyInstaller sidecar in v1, no Windows/Linux in v1, no telemetry ever.

Each phase lands as its own conventional-commit PR onto `main`; the Python suite must stay green and
untouched throughout (the entire tree lives under `clients/`, `decisions/`, `designs/`, `docs/`,
`.github/`).

## Outcomes

- A menu-bar app where the routing decision (● LOCAL / ◆ CLOUD, score, why) is glanceable and a chat
  turn is one ⌥W away — rendered, never computed, by the client (WF-ADR-0001).
- A first run that goes: install service → (optionally) key into Keychain → first routed turn — with
  no terminal required after the gateway package is installed.
- A signed, notarized, stapled universal DMG on a `desktop-v*` release lane with working in-place
  auto-update and an honest privacy panel.

## Initiatives

### Phase 0 — Hygiene *(prerequisite)*

Rebase `claude/desktop-tauri-app` onto `main` (currently ~12 behind; expect only CHANGELOG-adjacent
conflicts). Land the governing docs (WF-ADR-0042, WF-DESIGN-0012, this roadmap; WF-ADR-0027 marked
superseded). Drop the phantom `menubar_core.py` TODO from `decision.js` — recorded golden gateway
fixtures are the decision-render contract. Tighten the popover CSP from `null` to
`default-src 'self'; connect-src ipc: http://ipc.localhost http://127.0.0.1:8088` (+ Vite dev ws);
audit capabilities (grow per-command, never wildcard).

### Phase 1 — Design system

Tailwind v4 (`@tailwindcss/vite`, CSS-config) + shadcn init (new-york, css-variables) in
`clients/desktop`; vendor exactly the nine components; strip `dark:` utilities. Install the token
block from WF-DESIGN-0012 into `src/styles/globals.css` with the `@theme inline` mapping; add the
theme-lint test (no zinc/neutral survivors). Bump `minimumSystemVersion` to 14.0, Vite target to
`safari16.4`; ship the vibrancy corner-radius tweak (`Some(13.0)` + `#root` radius). Add
`types/wayfinder-shared.d.ts` — the UI's written contract with the untyped shared package.

### Phase 2 — The popover UI *(the big one)*

The component tree, hooks, views, and both state machines exactly per WF-DESIGN-0012. Fixtures
first: `tools/record-fixtures.mjs` records real gateway payloads (dry-run decisions ×4, healthz ×3,
one verbatim SSE transcript, savings) into `src/test/fixtures/`. Then bottom-up with tests:
`lib/appState.ts` reducer tables → leaf components (DecisionPill → ScoreReadout → WhyBars →
DecisionCard → StreamingMessage) → hooks (`useGatewayHealth` 15s+focus poll, `useTurn` over
`routeTurnStream`, `useCheapestModel`, `useSavings`, `useReducedMotion`) → views (`PopoverRoot`
switching all six gateway modes; `ChatView`, `UnreachableView`, `FirstRunView`) → motion + a11y
polish. Gate: vitest suite (incl. SSE-replay and a11y smoke) + theme lint + parity 18/18.

### Phase 3 — Gateway lifecycle & tray

Service-first: detect via `/healthz`, attach, never spawn. Tray menu Start/Stop/Install shells out
to `wayfinder-router service …` / `launchctl` — exact commands only, scoped in capabilities. The
tray **template icon** gains three health states (waypoint glyph: running/degraded/stopped), driven
by the webview's single healthz poller through one `set_tray_state` command. Rust commands land
individually: `set_tray_state`, `service_control`, `open_config`, `notify` (route-flip
notifications, off by default). Document the two-LaunchAgents split (app autostart vs gateway
service) in the app's help.

### Phase 4 — Onboarding & keys

FirstRunView flow: Install service (CTA) → scaffold config if none (`init --preset` shell-out) →
optional provider key → Keychain (`security add-generic-password`) with only an `api_key_cmd`
reference written to the gateway config (hot-reloaded; WF-ADR-0004 intact — the key never enters JS
state). Settings row: shortcut rebind (⌥W is rebindable for layouts/apps that claim it; verify whether the
Accessibility note in `lib.rs` is even true — hotkey registration shouldn't need it), launch-at-
login, and the **verify-lite privacy panel** with the honest claims (decision local/keyless; prompts
go only to your chosen provider under your keys; offline mode alone guarantees nothing leaves).

### Phase 5 — Distribution *(June-derived)*

`desktop-release.yml` on a macOS runner: signed **universal** app (cheap while there's no Python
sidecar), notarized + stapled; validation = `codesign --verify --deep --strict`, `spctl --assess`,
`xcrun stapler validate` on app **and** DMG. Updater: `tauri-plugin-updater` +
`bundle.createUpdaterArtifacts: true`; keypair via `tauri signer generate` — **private-key custody
in the password manager + CI secret from day one; loss permanently bricks auto-update**. Releases +
`latest.json` on this repo's `desktop-v*` tags (verified: matches neither `release.yml` glob — no
PyPI collision). Right-sized RC flow: `desktop-vX.Y.Z-rc.N` prereleases, promote re-signs from the
same commit. Generate `THIRD_PARTY_NOTICES` (cargo-about + npm license checker — distribution
creates attribution obligations). Write `docs/RELEASE-desktop.md` (mirrors RELEASE.md) and
`docs/desktop-fidelity.md` (both appearances, reduced-motion, VoiceOver, Gatekeeper relaunch,
update-in-place). Maintainer prerequisites, flagged: Apple Developer ID certificate + notarization
API key.

## Budgets

DMG ≤ 12 MB · popover toggle < 100 ms perceived · first decision paint < 250 ms on a healthy
gateway · idle RAM < 120 MB.

## Verification

Per PR: `cargo build && cargo clippy -- -D warnings` (src-tauri), `npm test -w @wayfinder/desktop`,
`npm run parity` (18/18), and the Python gate untouched (`ruff` / `mypy` / `python -m pytest -q`).
Live: gateway up → `npm run tauri dev` → walk all six gateway modes against the fidelity checklist.
Distribution: RC through CI → clean-machine DMG install → Gatekeeper-clean launch → next RC updates
in place → promote.

## Non-goals (v1)

Windows/Linux builds · PyInstaller sidecar (hardened-runtime + universal2 risks recorded in
WF-ADR-0042) · app-owned gateway supervisor · Mac App Store (uses `macOSPrivateApi`) ·
agent/assistant features · telemetry.

## Related

- WF-ADR-0042 (architecture) · WF-DESIGN-0012 (design contract) · WF-ADR-0038/0039 (service +
  offline surfaced) · WF-ADR-0020 (design language) · WF-ROADMAP-0007 (the OS-level-routing vision
  this is the visible face of)
