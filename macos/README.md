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

This is a Swift Package executable for the first prototype. It creates a native `NSStatusItem`, opens a transient `NSPopover`, hosts SwiftUI with `NSHostingController`, and opens separate native chat/settings windows through AppKit-owned `NSWindow` instances. A later distribution patch can wrap the same core into an Xcode `.app` target with signing, app bundle metadata, and assets.

## Implemented Native Surfaces

- Menu-bar popover: routing split, saved summary, running toggle, refresh/settings/chat/quit rows.
- Wayfinder Chat window: routed conversation examples, compact route cards, why disclosure, bottom composer.
- Settings window: native sidebar, Keys screen, provider picker/form, existing key status row, Keychain info box.
- Service boundary: `WayfinderClient` supports `route(prompt:)` and `loadStats(range:)`, with `MockWayfinderClient` powering the UI by default.

## Integration Strategy

Recommended path:

1. Keep the native app behind the `WayfinderClient` protocol.
2. Use `GatewayWayfinderClient` as the real source-of-truth integration once the local gateway lifecycle is ready.
3. Keep `LocalWayfinderClient` as a prototype/degraded preview only unless a parity test is added against the Python golden corpus.
4. Avoid storing provider keys in the app. Follow the existing gateway and Keychain pattern from the Tauri client.

The first UI patch runs with `MockWayfinderClient` so the menu-bar, chat, and settings surfaces can be shaped without bootstrapping the gateway. To switch to the gateway later, initialize `AppDelegate(client: GatewayWayfinderClient())` in `Sources/WayfinderMacApp/WayfinderMacMain.swift`.

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
swift run WayfinderMac
```

## Test

```bash
cd macos/WayfinderMac
swift test
```

If the managed environment blocks Swift's default module cache, use:

```bash
CLANG_MODULE_CACHE_PATH=/private/tmp/wayfinder-swift-module-cache swift test --disable-sandbox
```
