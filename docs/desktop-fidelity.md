# Desktop popover — fidelity checklist & six-mode walk

The manual counterpart to the vitest suite. jsdom can assert structure, roles, and the decision
contract, but not vibrancy compositing, the tray, media-query flips, scroll physics, or VoiceOver —
those are checked here, by hand, against [WF-DESIGN-0012](../designs/WF-DESIGN-0012-desktop-popover-design.md).
Run this after each phase that touches the popover.

## Running it

```sh
cd clients/desktop
npm run tauri -w @wayfinder/desktop -- dev     # or: npm install first
```

Summon with the tray icon or **⌥W** — the popover appears bottom-centre of the display your cursor
is on. It hides on blur (click away) and on the next ⌥W; the draft survives because the window is
hidden, not destroyed.

> The gateway lives on `127.0.0.1:8088`. Install it with `pip install -e ".[gateway]"` then
> `wayfinder-router serve` (foreground, easy to stop) or `wayfinder-router service install` (the
> real launchd path). A `wayfinder-router.toml` in the working directory configures tiers/models.

## The six gateway modes — how to force each, what to see

| Mode | Force it | Expect (WF-DESIGN-0012) |
|---|---|---|
| **healthy** | `serve` with models + keys set | green dot; ChatView; a turn paints the decision hero **before the first token**, streams the reply below a fixed hero, then the why-bars fill on the trailing event **without the hero moving** |
| **degraded** | config a model with `api_key_env = "SOME_UNSET_VAR"` | amber dot; amber banner naming the unset var **verbatim** (mono); still sends turns |
| **decision-only** | live `serve` with tiers but **no `[gateway.models]`** (needs the gateway from PR #68) | full decision hero; the reply slot is the OnboardingCard ("connect a model") — the decision is real, the reply is withheld |
| **offline** | `[gateway] offline = true`, **or** flip the popover's offline switch | quiet "offline — routing to the cheapest tier" chip; the switch is teal-on and **disabled** when it's config-owned, toggleable when it's your preference |
| **unreachable** | let the app see the gateway, then stop it (`Ctrl-C` / `service stop`) | UnreachableView; a **Start Wayfinder** CTA; the LocalMirror still previews decisions as you type, unmissably labelled "local mirror" |
| **first-run** | never-seen state: in devtools `localStorage.removeItem('wf.seenGateway')`, quit the gateway, reopen | brand hero; **Install the Wayfinder service** CTA; the live scorer demo shows a real decision keyless — never a dead screen |

The parity-gated local mirror (unreachable/first-run) only appears in a build where the JS↔Python
scorer parity check is green (`VITE_PARITY_OK`; the dev/build scripts set it, the CI parity job is
the enforcement). Without it the surface says "decisions unavailable" rather than risk a drifted
local score.

## The no-reflow decision paint (the nail)

Send a turn on a healthy gateway and watch the hero:

- [ ] the route pill + score appear from the **response headers**, before any reply text
- [ ] the reply streams **below** a hero that does not move
- [ ] opening **› why** reveals skeleton rows that fill on the trailing `event: wayfinder` — the
      hero's height/position never changes as the "why" lands
- [ ] on a reply *error*, the decision stays put ("the decision is the product")

## Appearance, motion, a11y (not testable in jsdom)

- [ ] **Vibrancy**: the popover is the native NSVisualEffectView material, body transparent; the
      13px CSS corners coincide with the material corners (no square bleed)
- [ ] **Light + dark**: flip System Settings → Appearance with the popover open — the palette flips
      (teal LOCAL, amber CLOUD) with **no zinc/grey anywhere**; no flash of the wrong theme
- [ ] **Tray**: no Dock icon; the tray title shows the savings `$` only; left-click toggles the
      popover, right-click opens the service menu (Start / Stop / Install · Open
      dashboard/config/logs · Quit)
- [ ] **Tray state (the W)**: the monochrome W changes shape with health — solid (running),
      notched (degraded), thin outline (stopped/unreachable); it tints with the menu-bar
      appearance and never shows colour
- [ ] **Service control**: with the gateway stopped, first-run **Install the Wayfinder service** /
      unreachable **Start Wayfinder** (and the tray menu items) shell out to `wayfinder-router
      service …` / `launchctl`; the next healthz poll flips the mode. If `wayfinder-router` isn't
      on the resolver's paths, the CTA shows "install the gateway first" rather than failing silently
- [ ] **Reduced motion**: System Settings → Accessibility → Reduce Motion on — the score dip-swap,
      rail fill, and why-bar stagger are stilled (durations zeroed centrally)
- [ ] **Scroll**: a long reply scrolls inside the content region with the overlay scrollbar; the
      composer and header stay pinned; banners animate above the scroll region, never pushing the
      composer mid-type
- [ ] **VoiceOver** (⌘F5): the route reads as "routed locally/to cloud, score 0.NN"; the why-bars
      read "word count, 41% of score"; completion announces once ("reply finished, routed locally")
- [ ] **Keyboard**: composer autofocus on open; Enter sends, Shift+Enter newlines; a 2px teal focus
      ring, offset

## Tray & service control (Phase 3)

The tray is a monochrome **template W** (system-tinted for the menu-bar appearance — never teal),
driven by the webview's single `/healthz` poll through `set_tray_state`. Full reference:
[`docs/desktop-lifecycle.md`](desktop-lifecycle.md).

- **Three W states** — filled = **running** (`status:"ok"`), notched = **degraded** (`missing_keys`),
  hollow = **stopped** (unreachable). Force by starting/stopping the service (below) and watching the
  W flip on the next poll. The tray **title** shows the savings `$` only — never a route.
- **Left-click** toggles the popover (summons bottom-center); **right-click** opens the native menu.
- **Service control from the menu** — `Start gateway` / `Stop gateway` / `Install service…` shell out
  to `launchctl` / `wayfinder-router service …` (exact argv only; no arbitrary shell). Walk:
  1. With no service: menu → **Install service…** → grant nothing (no key needed) → the W goes
     filled and the popover flips to a chat surface within ~15 s (next poll).
  2. **Stop gateway** → W goes hollow, popover → *Wayfinder isn't running* (local-mirror preview).
  3. **Start gateway** → W filled again, chat returns. Quitting the app leaves the gateway serving.
- **Open dashboard / config / logs** open `:8088/router`, `~/Library/Application Support/Wayfinder/`,
  and `~/Library/Logs/` respectively — three fixed targets, never a webview-supplied path.
- **Notifications** (edge-only: up↔down, ok↔degraded) are wired but **off by default**; the Settings
  toggle lands in Phase 4. (Dep-free via `osascript` for now, so the sender attribution is generic —
  app-attributed notifications are a later dependency decision.)

## Two launch agents (by design)

Two independent LaunchAgents, two jobs — don't conflate them:

- **The app** starts at login via `tauri-plugin-autostart` (a LaunchAgent for *Wayfinder.app*).
- **The gateway** starts at login via the WF-ADR-0038 agent (`com.wayfinder-router.gateway`),
  installed by `wayfinder-router service install`.

The app never spawns or supervises the gateway (WF-ADR-0042 §4) — it detects `/healthz` and
attaches, and the tray/CTAs ask the *service* to start/stop. So the app can be quit with the
gateway still serving every other client on `:8088`, and vice-versa.

## Known-open (land with later phases)

- Esc closes the why-disclosure then hides the window, and re-focus-the-composer-on-show — both
  need a small window→webview event; folded into the Phase 4 settings/keyboard pass. Today
  autofocus covers first open.
- The **Install** CTA runs `service install` today; the guided first-run onboarding around it
  (provider key → Keychain, config scaffold) is **Phase 4**.
