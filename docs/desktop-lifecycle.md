# Wayfinder Desktop — the two-LaunchAgents split

The desktop app and the gateway are **two separate things with two separate launch agents**. This
is deliberate (WF-ADR-0042 §4, "service-first"): the app never spawns or supervises the gateway, so
there is never a fight over `:8088`.

| | The **app** | The **gateway service** |
|---|---|---|
| What it is | `Wayfinder.app` — the menu-bar client | `wayfinder-router serve` — the local HTTP router |
| Launch agent | `tauri-plugin-autostart` ("launch at login", opt-in in Settings) | `com.wayfinder-router.gateway` (WF-ADR-0038) |
| Installed by | dragging the app / first run | the app's **Install service** button, or `wayfinder-router service install` |
| Endpoint | none — it's a client | `127.0.0.1:8088` |
| If it dies | tray disappears; relaunch the app | `KeepAlive` restarts it; the tray goes to the hollow **W** until it's back |

## How the app relates to the gateway

- **Detect, don't spawn.** The app polls `GET /healthz` every 15 s (and on focus). It *attaches* to
  whatever is answering — a launchd-managed service, or a gateway you started by hand in a terminal.
  It never launches its own copy.
- **The tray reflects, the service supervises.** The three-state **W** (running / degraded / stopped)
  is driven entirely by that healthz poll through the `set_tray_state` command. Start/Stop/Install in
  the tray menu shell out to `wayfinder-router service …` / `launchctl` — they ask the *service* to
  change state; the next poll reflects it. There is one source of tray truth.
- **Quitting the app leaves the gateway running.** They're independent. Stop the gateway from the
  tray menu (or `wayfinder-router service uninstall`) if you want it gone.

## Files

- App config (owned by the app): `~/Library/Application Support/Wayfinder/wayfinder-router.toml`
- Gateway logs: `~/Library/Logs/` (`wayfinder-router.log` / `.err.log`)
- Gateway launch agent: `~/Library/LaunchAgents/com.wayfinder-router.gateway.plist`
