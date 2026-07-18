# Wayfinder Native macOS App

This directory contains the shipping native macOS product. Wayfinder Desktop is a thin SwiftUI and
AppKit client over its bundled Rust gateway; the standalone Python distribution remains available
for compatibility and delegated commands during the migration period.

## Inspection Summary

- The bundled Rust implementation lives under `rust/crates/`; `wayfinder-gateway` owns native
  routing and OpenAI-compatible delivery.
- The Python package remains the compatibility implementation and owns explicitly delegated
  commands until the later removal decision.
- The macOS app never computes an authoritative route, calls a provider directly, or owns provider
  secrets. It consumes the bundled gateway contract.
- `clients/desktop` is the retained legacy Tauri client, not the current native product.
- WF-ADR-0042 and WF-ROADMAP-0012 define the accepted native shell and Chat contract.

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
decision, and never scores, contacts a provider directly, or owns credentials. The chronological
transcript stays complete and thread-first; a quiet receipt selects the turn while provider, mode,
score, explanation, and signal detail live in the persistent, collapsible routing inspector on the
right. Navigator search and route filters never remove messages from the transcript. WF-ROADMAP-0012
governs its delivery and fidelity gate.

## Integration Strategy

Recommended path:

1. Keep the native app behind the `WayfinderClient` protocol.
2. Use `GatewayWayfinderClient` as the real source-of-truth integration for menu-bar status, routing mix, and savings data.
3. Keep `LocalWayfinderClient` as a prototype/degraded preview only unless a parity test is added against the Python golden corpus.
4. Avoid storing provider keys in the app. Follow the existing gateway and Keychain pattern from the Tauri client.

The first UI patch ran with `MockWayfinderClient` so the menu-bar, chat, and settings surfaces could be shaped without bootstrapping the gateway. The native menu now starts with `AppDelegate(client: GatewayWayfinderClient())` in `Sources/WayfinderMacApp/WayfinderMacMain.swift`.

## Release Boundaries

- The Swift preview scorer is non-authoritative and remains limited to deterministic previews.
- The release script assembles and signs the production `.app`; public distribution still requires
  the real Developer ID identity, notarization, stapling, and the physical-Mac evidence matrix in
  `Packaging/RELEASE.md`.
- Provider-key writes and reads stay behind the existing narrow Keychain and authenticated XPC
  boundaries. Chat does not broaden either broker.
- The bundled gateway remains the routing source of truth, matching WF-ADR-0042.

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
