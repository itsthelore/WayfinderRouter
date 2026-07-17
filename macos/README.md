# Wayfinder Native macOS Prototype

This directory contains the first native macOS prototype for Wayfinder Router. It is additive: the Python package, gateway, shared TypeScript scorer, and Tauri client remain untouched.

## Inspection Summary

- The deterministic routing core lives in `wayfinder_router/complexity.py`.
- The CLI entry point is `wayfinder_router/cli.py`, especially `wayfinder-router route <prompt | -> --json --explain`.
- The local service and OpenAI-compatible gateway live in `wayfinder_router/gateway.py`.
- The current desktop app is `clients/desktop`: Tauri v2, Rust shell, React UI, and a thin-client contract over the gateway.
- `clients/shared/src/scorer.js` is a parity-gated JavaScript mirror of the Python scorer for degraded preview mode, not the primary router.
- Tests cover the Python scorer, gateway, Tauri-adjacent shared client behavior, fixtures, config, calibration, and service helpers.
- ADRs and design docs already point toward a menu-bar command surface, especially `decisions/WF-ADR-0042-desktop-menu-bar-client.md`, `designs/WF-DESIGN-0012-desktop-popover-design.md`, and `designs/WF-DESIGN-0014-flat-list-popover.md`.

## Native Layout

```text
macos/
  README.md
  WayfinderMac/
    Package.swift
    Sources/
      WayfinderMacApp/
        WayfinderMacMain.swift
      WayfinderMac/
        WayfinderMacApp.swift
        MenuBar/
        Windowing/
        UI/
          MenuBarPopover/
          Chat/
          Settings/
        State/
        Services/
        Models/
    Tests/
      WayfinderMacTests/
```

This is a Swift Package executable with a production bundle assembly path. It creates a native `NSStatusItem`,
opens a transient AppKit panel, hosts SwiftUI with `NSHostingController`, and opens native Settings
through an AppKit-owned `NSWindow`. Chat ships in v0.1.0 as a dedicated thin-client window over the
same gateway. `script/build_release_bundle.sh` assembles the app, universal Rust helper, credential-broker
XPC service, signed manifest, hardened-runtime signatures, and optional notarization/stapling.
`Packaging/RELEASE.md` defines the physical-Mac release and rollback evidence.

## Native v0.1.0 Surfaces

- Shipping v0.1.0: an accessory menu-bar app, routing/gateway and endpoint status, native
  Settings, service controls, routing configuration, provider-key management through the Keychain
  boundary, privacy, Help, About, and focused Chat through the bundled gateway.
- The compact popover has one enabled Chat row that opens a retained, reusable native window.
- Settings window: native sidebar, Keys screen, provider picker/form, existing key status row, Keychain info box.
- Service boundary: `WayfinderClient` supports `route(prompt:)`, `loadStats(range:)`, and `loadOverview()`. The app entrypoint uses `GatewayWayfinderClient` for live status/stat rendering; `MockWayfinderClient` remains available for previews and tests.

## Chat boundary

Chat is a shipping v0.1.0 surface, but it remains a thin client: it sends bounded conversation
history only to the gateway, renders the gateway's authoritative assistant reply and routing
decision, and never scores, contacts a provider directly, or owns credentials. WF-ROADMAP-0012
governs its delivery and fidelity gate.

## Integration Strategy

Recommended path:

1. Keep the native app behind the `WayfinderClient` protocol.
2. Use `GatewayWayfinderClient` as the real source-of-truth integration for menu-bar status, routing mix, and savings data.
3. Keep `LocalWayfinderClient` as a prototype/degraded preview only unless a parity test is added against the Python golden corpus.
4. Avoid storing provider keys in the app. Follow the existing gateway and Keychain pattern from the Tauri client.

The first UI patch ran with `MockWayfinderClient` so the menu-bar, chat, and settings surfaces could be shaped without bootstrapping the gateway. The native menu now starts with `AppDelegate(client: GatewayWayfinderClient())` in `Sources/WayfinderMacApp/WayfinderMacMain.swift`.

## Risks And Unknowns

- The Swift local scorer is a prototype mirror, not yet parity-gated against `wayfinder_router/complexity.py`.
- This Swift Package runs as a native executable, not yet a signed `.app` bundle.
- Global shortcut, launch-at-login, app icon assets, and real Keychain/provider writes are intentionally deferred.
- The eventual product architecture should keep the gateway as the routing source of truth, matching WF-ADR-0042.

## First Patch Plan

1. Add the Swift Package scaffold under `macos/WayfinderMac`.
2. Add AppKit menu-bar shell with `NSStatusItem` and `NSPopover`.
3. Add SwiftUI menu-bar popover with routing summary, saved summary, and menu-style actions.
4. Add separate native chat and settings windows.
5. Add `WayfinderClient` service boundary with local, mock, and gateway client implementations.
6. Add focused Swift tests for the deterministic local prototype scorer.

## Run

```bash
cd macos/WayfinderMac
./script/build_and_run.sh
```

The script builds and stages a local `dist/WayfinderMac.app` bundle before launch. Use
`--verify` to confirm the process starts, or `--debug`, `--logs`, and `--telemetry` for
focused diagnostics.
For repeatable native Chat-window visual QA, launch a staged app with `--args --open-chat` to use
the real gateway or `--args --preview-chat` to use an explicit deterministic preview client. Normal
launches remain menu-bar-only and always use the real gateway.

## Test

```bash
cd macos/WayfinderMac
swift test
```

If the managed environment blocks Swift's default module cache, use:

```bash
CLANG_MODULE_CACHE_PATH=/private/tmp/wayfinder-swift-module-cache swift test --disable-sandbox
```
