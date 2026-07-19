---
schema_version: 1
id: WF-DESIGN-0016
type: design
tags: [macos, rust, helper, launchd, xpc, keychain, signing, update, rollback]
---

# WF-DESIGN-0016: Signed Rust helper integration for Wayfinder.app

## Status

Implemented for the Apple Silicon desktop path; Developer ID release verification remains pending.
Universal/Intel distribution is deferred beyond Desktop v0.1.0 by WF-ROADMAP-0015.

## Context

The native Swift app treats the gateway as the source of truth and the per-user launchd job as its
owner. It probes loopback HTTP, renders gateway facts, invokes fixed CLI/config/service verbs, and
manages Keychain writes. When this design was accepted, the Python gateway was discovered outside
the app and there was no production bundle or signed helper path. The implemented desktop topology
now embeds an arm64 Rust gateway and two narrow Swift XPC services inside the production app;
Developer ID signing, notarization, and clean-machine release evidence remain the open release gate.

WF-ADR-0045 selects one Rust `wayfinder-router` executable as the eventual nested helper while
retaining Python as an explicit fallback through the parity period. This design makes the process,
credential, update, and rollback boundaries concrete without moving routing or provider delivery
into Swift.

The UI contracts remain those already accepted for the native menu-bar utility: gateway state is
explicit, Chat remains governed by the current release policy, readiness means configured/key-ready
rather than provider uptime, and offline is the only state allowed to promise no egress.

## Bundle and runtime topology

```text
Wayfinder.app
  Contents/MacOS/WayfinderMac                      SwiftUI/AppKit UI
  Contents/Helpers/WayfinderGateway.app
    Contents/MacOS/wayfinder-router                arm64 Rust gateway for v0.1.0
    Contents/XPCServices/
      com.wayfinder.CredentialBroker.xpc           Swift/Security.framework broker
      com.wayfinder.FoundationModelBroker.xpc      Swift/FoundationModels broker
  Contents/Resources/wayfinder-helper.json         version/capability manifest

launchd user domain
  com.wayfinder-router.gateway                     owns the Rust helper
  com.wayfinder.CredentialBroker                   on-demand XPC service
  com.wayfinder.FoundationModelBroker              on-demand XPC service

public local data plane
  http://127.0.0.1:8088                            existing gateway API
```

The app and gateway are separate processes. Closing or crashing the app does not stop routing;
crashing the helper does not crash the app. launchd applies `RunAtLoad` and `KeepAlive` to the
gateway and remains its only supervisor.

## Ownership

| Concern | Owner |
|---|---|
| Menu bar, Settings, setup, consent, accessibility, Keychain creation/deletion | Swift app / Swift XPC broker |
| Routing, config validation, provider calls, streaming, cache, budgets, limits, virtual keys, health/readiness APIs | Rust helper |
| Process lifetime/restart | launchd |
| Config and service mutation | fixed `wayfinder-router` CLI verbs |
| Public integration protocol | loopback HTTP |
| Credential value transport to the helper | authenticated XPC only for the bundled production path |
| Apple Foundation Models availability and inference | separate authenticated Foundation Models XPC service |
| Signing/notarization/update promotion | release tooling, outside both runtime processes |

Swift does not write TOML or launchd plists and does not spawn/supervise `serve` directly. Rust does
not display consent UI or independently create Keychain items.

## Helper discovery and backend selection

During migration, backend selection is explicit:

1. A development/test selector chooses `python` or `rust`; absence retains the current Python
   behavior until the default gate is separately accepted.
2. The selected executable must answer `wayfinder-router capabilities --json` with a versioned
   schema containing implementation, package version, build identifier, supported commands,
   config schema range, wire-contract version, target architecture, and credential mechanisms.
3. The app compares that result with the signed `wayfinder-helper.json` manifest and its own minimum
   contract. A mismatch is a visible setup/repair failure, never a silent fallback to another
   executable found on `PATH`.
4. `service install` writes the selected absolute executable path into the LaunchAgent. It refuses
   to replace a loaded job owned by another implementation unless the user explicitly requests a
   backend switch.
5. Health/build information identifies the running implementation so the app never reports the
   bundled Rust helper while a Homebrew/Python service owns the port.

Finder's restricted `PATH` is not part of production discovery. The nested helper path is derived
from the verified app bundle; Homebrew/Python discovery is retained only for the explicit legacy
or development selection.

## Launch, restart, and shutdown

- Setup or an explicit backend switch invokes the selected CLI's service verb. The CLI renders and
  validates the unit, writes it atomically, bootstraps it, and verifies the exact label/program.
- Normal app launch only probes launchd plus `/healthz`; it does not bootstrap a second process.
- Restart uses launchd `kickstart -k` through the fixed service seam.
- Uninstall boots out the job and removes only the unit it owns. It does not delete config,
  ledgers, cache policy, feedback, or Keychain items.
- On SIGTERM the helper stops accepting connections, cancels/drains requests for a bounded period,
  flushes permitted metadata state, and exits. launchd may then restart it according to the unit.
- Interrupted setup is reassessed from observable facts. No UI claims that already-completed
  external mutations were rolled back.

## Public data plane

Loopback HTTP remains unchanged so Wayfinder.app and every existing OpenAI/Anthropic-compatible
client use the same gateway:

- `/healthz` is the source of overall configured/key-ready/offline state.
- `/router/models` is the source of endpoint configuration and key presence.
- `/router/recent` and `/v1/savings` are the source of routing/savings presentation.
- `/v1/chat/completions` and `/v1/messages` remain the invocation surfaces.

The UI does not infer provider uptime. The helper binds to loopback by default. A network-exposed
bind is explicit and visibly degraded/risky in native Settings.

## Credential protocol

### References

Config contains a reference, never a value. The production macOS reference identifies:

- protocol version;
- Keychain service (`wayfinder-router` for compatibility);
- account/environment identifier;
- optional access-group identifier; and
- no provider secret material.

Legacy `api_key_env` and `api_key_cmd` remain loadable during migration and are never rewritten
implicitly.

### Broker

`com.wayfinder.CredentialBroker.xpc` is a small Swift service using Security.framework. It is
launchd-on-demand and can resolve a key while the menu-bar UI is closed.

For each connection the broker:

1. obtains the caller's audit token;
2. validates the Rust helper's code-signing identity, Team ID, bundle/helper identifier, and
   designated requirement;
3. rejects unsigned, ad-hoc, differently versioned, or non-helper callers;
4. accepts only the fixed resolve operation and a bounded reference payload;
5. performs `SecItemCopyMatching` for that exact service/account/access group;
6. returns opaque bytes in one bounded in-memory reply; and
7. emits only reference-safe error codes such as missing, denied, locked, incompatible, or
   unavailable.

The protocol has no enumerate, arbitrary query, write, delete, shell, or config operation. The
helper cannot ask the broker to read an arbitrary Keychain class or account outside the Wayfinder
namespace.

### Secret lifetime

The Rust client immediately wraps reply bytes in a non-serializable redacted secret type. It never
logs the XPC payload, inserts it into argv/environment/persisted state, or includes it in provider
errors. It attaches the value only to the exact validated provider origin and clears owned bytes on
drop where possible.

Swift setup uses `SecureField` only long enough to write the Keychain item and clears it after the
operation. Secret values never enter persistent Swift state, accessibility values, pasteboard,
unified logging, crash breadcrumbs, screenshots, or setup restoration.

### Failure and rotation

- Missing/locked/denied credentials make the endpoint not key-ready; they do not imply provider
  downtime.
- Rotation writes the Keychain item, clears the UI value, and explicitly restarts or tells the
  helper to invalidate its in-memory secret cache through a non-secret control operation.
- XPC failure does not fall back to passing a key over HTTP, argv, a temp file, or shell output.
- Headless non-app installs continue to use environment/legacy reference mechanisms until an
  equivalent approved secret-store integration is configured.

## Signing and Desktop v0.1.0 arm64 artifact order

Desktop v0.1.0 release production follows this order:

1. Build and test the Rust helper for `aarch64-apple-darwin` with
   `MACOSX_DEPLOYMENT_TARGET=14.0`.
2. Verify the thin helper, including dependency/audit/license gates and target-specific smoke tests.
3. Build the Swift app and both XPC services for arm64.
4. Embed the arm64 helper and XPC services at their stable paths inside the containing gateway app.
5. Verify every final executable reports exactly arm64 and that expected build/version/architecture
   metadata agrees.
6. Sign inner code first: Rust helper, XPC services, containing gateway app,
   frameworks/libraries, then the outer app with
   hardened runtime and the minimum required entitlements.
7. Verify the nested designated requirements and Keychain/XPC entitlement relationship.
8. Notarize, staple, and validate the app; checksum and verify the final ZIP after extraction.
9. Install the final extracted ZIP on a clean Apple Silicon Mac and exercise setup, headless
   credential read, gateway calls, app exit, helper crash/restart, rotation, replacement, and
   rollback.

The originally accepted universal target remains future work. Before claiming Intel or universal
support, build and verify a real x86_64 slice, assemble both architectures, and exercise the result
on physical Intel hardware; Rosetta is not Intel evidence.

## Replacement and rollback

Desktop v0.1.0 has no automatic updater. Manual replacement and release tooling operate on signed
app artifacts, never an independently downloaded helper executable:

1. download to staging and verify signature, notarization ticket, version policy, helper manifest,
   and the release's declared architecture (`arm64` for v0.1.0);
2. ask the current service seam to quiesce and boot out the selected job;
3. atomically promote the verified app bundle;
4. bootstrap the job from the new stable helper path;
5. wait with bounded backoff for launchd identity plus `/healthz` and capability agreement;
6. on failure, boot out the failed job, restore the previously verified bundle, bootstrap its
   helper, and report the rollback without altering user data.

Config schema changes must be backward-readable before promotion. No update may rewrite existing
config merely to make rollback impossible. Ledger/cache persisted formats are versioned and read
tolerantly; incompatible state is quarantined with an explicit diagnostic rather than overwritten.

## Crash and failure isolation

| Failure | Expected result |
|---|---|
| UI quits/crashes | Gateway continues under launchd; other clients are unaffected |
| Helper crashes | Connections fail clearly; launchd restarts it; app shows stopped/unreachable until health recovers |
| XPC broker unavailable | Keyed endpoints are not ready; keyless/offline local endpoints may continue; no insecure fallback |
| Config reload invalid | Last-good runtime remains; health/metrics expose reload failure without leaking contents |
| Provider stream stalls | Explicit timeout/cancellation closes only that request; helper remains healthy |
| Update promotion fails | Previous verified app/helper is restored; user config and Keychain stay intact |
| Legacy Python selected | Rust helper is not launched; app renders implementation/version truthfully |

## Verification matrix

Unit/contract tests:

- manifest/capability parsing and version skew;
- launchd unit ownership, absolute helper path, idempotent install/uninstall/status;
- XPC request allowlist, payload bounds, sanitized errors, caller-requirement validation helpers;
- no secret in argv/environment/logging/fixtures/snapshots/errors;
- native readiness wording and implementation identity;
- config and user-state byte preservation through switch/update/rollback.

Real signed-Mac tests for Desktop v0.1.0:

- Apple Silicon launch, health, streaming, cancellation, and restart;
- app closed while the on-demand XPC broker resolves a key;
- wrong-team, unsigned, copied, and stale helper rejected by the broker;
- locked Keychain, denied consent, missing item, rotation, and logout/login;
- bundled ↔ Python/Homebrew backend switch with one label/port owner;
- interrupted install/update and automatic rollback;
- `codesign --verify --deep --strict`, Gatekeeper assessment, stapler validation, and clean-user
  first-run recovery.

Physical Intel testing remains required before a later Intel or universal-support claim, not before
the Apple Silicon-only v0.1.0 release.

## Non-goals

- Running the gateway inside the Swift process.
- A general-purpose XPC control API.
- Provider credentials over loopback HTTP, CLI arguments, temp files, or config.
- Swift-authored TOML or launchd plists.
- Provider uptime probes presented as readiness.
- Removing the Python/Homebrew path before a separate reviewed decision.
- Claiming no egress merely because the lowest-cost tier was selected.

## Related

- WF-ADR-0038 (launchd-owned local service)
- WF-ADR-0039 (offline delivery and privacy claim)
- WF-ADR-0042 (thin native client)
- WF-ADR-0044 (CLI-owned config seam)
- WF-ADR-0045 (Rust workspace/helper architecture)
- WF-ROADMAP-0015 (Apple Silicon desktop v0.1.0 release contract)
- WF-DESIGN-0015 (current setup and Keychain design)
- `docs/rust-migration-capability-matrix.md`
