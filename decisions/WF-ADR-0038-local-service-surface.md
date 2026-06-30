---
schema_version: 1
id: WF-ADR-0038
type: decision
tags: [gateway, packaging, service, daemon, macos, launchd, systemd, invocation]
---

# WF-ADR-0038: Run the gateway as an always-on local service (`service` command, macOS first)

## Status

Accepted

## Category

Technical

## Context

The gateway runs today only as a foreground `wayfinder-router serve` — you start it by hand and it
dies with the terminal. A Show HN comment asked for "LLM routing at the OS level, like mobile data":
pay for the inference infrastructure once and let the *machine* route every app's queries. Wayfinder
is already most of the way there — it's a local, OpenAI-compatible endpoint that any app points at
with one `base_url`, holds your provider keys in one place, and decides local-vs-cloud offline. The
missing piece for the "set it up once, every app shares it" experience is simply **keeping it
running**: an always-on local service on a stable `localhost` endpoint. This is the near-term,
localhost slice of WF-ROADMAP-0007 (the OS-level-routing vision); the truly OS-level parts
(transparent interception, kernel hooks) are out of scope there.

## Decision

1. **A `wayfinder-router service install | uninstall | status` command.** `install` registers the
   gateway with the OS service manager so it auto-starts at login on a stable endpoint
   (`127.0.0.1:8088` by default) and restarts if it exits; apps then set their `base_url` /
   `OPENAI_BASE_URL` there once and share it.

2. **macOS (launchd) is the primary target.** `install` writes a LaunchAgent plist to
   `~/Library/LaunchAgents/com.wayfinder-router.gateway.plist` with `RunAtLoad` + `KeepAlive`, and
   loads it via `launchctl bootstrap gui/$UID` (falling back to `launchctl load -w` on older macOS).
   `uninstall` boots it out and removes the plist; `status` reports the unit, the endpoint, and a
   `/healthz` probe. The **Linux systemd user unit** ships in the same module as the fast-follow.

3. **The unit generators are a pure module (`service.py`).** They only render text and resolve paths
   (no I/O), so they golden-test like `reliability.py` / `cache.py`; the CLI does the file writes and
   drives `launchctl` / `systemctl`. Live `launchctl` verification runs on macOS (the CI host is
   Linux), so `--print` and the golden tests are the CI coverage.

4. **Graceful when no manager is present.** If `launchctl` / `systemctl` is missing, `install` writes
   the unit and prints the one manual command to start it rather than failing. Windows is guidance
   only in v1 (`serve` directly). `--print` emits the unit without installing.

5. **Opt-in.** Nothing auto-installs; running as a service is an explicit choice. Provider keys stay
   in the gateway config (referenced by env-var name, never stored — WF-ADR-0004); the service just
   keeps the existing gateway running.

6. **Packaging only — the core is untouched.** This adds no routing behavior; the deterministic,
   offline decision (WF-ADR-0001) is unchanged. It reuses `run()`, `/healthz`, `GET /v1/models`, and
   the `[project.scripts]` console-script entry point.

## Consequences

- **Wayfinder becomes the machine's local LLM endpoint** — a "dial-tone" every OpenAI-compatible app
  on the box can share, which is the felt experience josalhor asked for, minus any OS-level magic.
- **Low risk**: it's file generation + a manager call; the gateway and decision path are unchanged.
- **Platform reality**: the live macOS path is verified on a Mac, not in CI; the plist text is
  golden-tested and `--print`-able everywhere.
- **Limitation**: it manages a single local instance per user session; multi-user/system-wide daemons
  and Windows services are deferred.

## Alternatives Considered

- **A `--daemon` flag on `serve` (self-daemonize).** Rejected — fragile across platforms and doesn't
  survive logout/reboot; the OS service managers already solve restart/at-login correctly.
- **Docker-only "run it as a container".** Already documented and still valid, but it isn't the
  laptop "always there for every app" experience the comment wanted.
- **Bundle a full menu-bar app.** Out of scope for near-term; the LaunchAgent gives the always-on
  behavior without a GUI.

## Success Measures

- On macOS, `wayfinder-router service install` makes `launchctl print gui/$UID/com.wayfinder-router.gateway`
  show the agent running and `curl 127.0.0.1:8088/healthz` answer after login, with no terminal open.
- `service install --print` emits a valid LaunchAgent plist (and a systemd unit on Linux); the
  generators are golden-tested.
- The deterministic decision path still makes no model call (WF-ADR-0001 guard unaffected).

## Related

- WF-ADR-0001 (deterministic, offline core — untouched; this is packaging)
- WF-ADR-0004 (the OpenAI-compatible gateway + BYO-key model this serves)
- WF-ROADMAP-0007 (local-LLM-service / OS-level-routing vision — this is its first slice)
- WF-ADR-0039 (offline-first delivery — the companion slice)
