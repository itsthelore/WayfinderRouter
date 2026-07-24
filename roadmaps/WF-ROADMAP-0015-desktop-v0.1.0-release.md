---
schema_version: 1
id: WF-ROADMAP-0015
type: roadmap
tags: [desktop, macos, release, semver, apple-silicon, signing, notarization, chat, chatgpt]
---

# Roadmap: release Wayfinder Desktop v0.1.0 for Apple Silicon

## Status

Accepted.

## Decision summary

Wayfinder Desktop v0.1.0 ships as an **Apple Silicon-only** native macOS application with its
arm64 Rust gateway embedded inside the signed app. It retains the macOS 14 minimum deployment
target and uses the desktop SemVer tag `desktop-v0.1.0`. The embedded Rust gateway is part of that
product and reports the desktop version.

The release includes focused native Chat, Apple Foundation Models on eligible systems, and the
opt-in ChatGPT-authenticated destination from WF-DESIGN-0018. ChatGPT authentication depends on the
separately installed, correctly signed ChatGPT app. Wayfinder v0.1.0 does **not** redistribute or
bundle the Codex executable and does not claim that this provider is self-contained. Release
discovery rejects development overrides, sibling executables, unsigned code, the wrong signing
identity, and incompatible runtime versions.

Signing in remains configuration rather than routing: it does not change `Automatic`, provider
defaults, route ladders, or the credential broker. On an eligible, never-configured Apple Silicon
Mac, Setup preselects Apple Local only after a live `available` response and still requires user
confirmation. Existing configurations and route ladders remain unchanged, and Chat opens on
`Automatic`.

The v0.1.0 distribution artifact is a Developer ID-signed, notarized, stapled `Wayfinder.app` in a
ZIP published directly on GitHub with a SHA-256 checksum. It is not a Mac App Store package. A DMG,
Homebrew cask, automatic updater, App Sandbox/App Store submission lane, bundled Codex runtime,
Intel slice, and universal artifact are follow-up release work.

## Release invariants

- Every executable in the distributed app reports exactly `arm64`; a fat or x86_64 binary fails the
  build even if it also contains an arm64 slice.
- `Packaging/DESKTOP_VERSION`, every bundle version, the embedded gateway version, and the signed
  helper manifest agree on `0.1.0`; Apple build numbers remain monotonically increasing.
- The manifest and runtime capability handshake agree on `target_architecture = "arm64"`.
- Nested code is signed before its containing bundle. All production components have the same
  non-empty Team ID, hardened runtime, and only their accepted entitlements.
- The final archive is recreated after stapling and verified after extraction. Verification covers
  architecture, versions, manifest/capabilities, signatures, notarization, Gatekeeper, and checksum.
- The release preserves config, routing history, ledger/cache state, and Keychain items across
  installation, replacement, failed promotion, and rollback.
- Apple on-device inference calls the native `FoundationModels` framework inside the authenticated
  Swift XPC service; no Apple-model CLI or subprocess inference path is shipped.
- The desktop workflow and its `desktop-v*` tags publish only native desktop artifacts.

## Delivery phases

### Phase 0 — encode the release contract

- Amend current desktop, helper, setup, and Rust migration documents so Apple Silicon is the only
  v0.1.0 platform claim and Intel/universal evidence is explicitly deferred.
- State that ChatGPT account routing ships through a separately installed verified ChatGPT app and
  that no Codex executable is bundled in v0.1.0.
- Keep the accepted availability-gated Apple new-setup preference and narrow credential brokers
  unchanged.

**Exit:** no current release-facing document claims that v0.1.0 is universal, Intel-tested, or
self-contained for ChatGPT authentication.

### Phase 1 — make architecture and packaging executable

- Build only `aarch64-apple-darwin` Rust and arm64 Swift products in the production bundle script.
- Reject any final executable whose exact Mach-O architecture list is not `arm64`.
- Bind `target_architecture` into the signed helper manifest and runtime capability check.
- Add a reusable final-bundle verifier and deterministic negative coverage for fat/x86_64 output.
- Recreate the distribution ZIP after stapling and emit its SHA-256 checksum.

**Exit:** ad-hoc CI packaging and the production release path both prove a thin arm64 app, and
architecture mismatch fails before promotion.

### Phase 2 — add desktop CI and release automation

- Run Swift tests and release builds on native GitHub-hosted Apple Silicon runners.
- Test the macOS 14 deployment floor and the current macOS 26 release environment.
- Keep signing secrets in a protected `desktop-release` environment and import them into an
  ephemeral keychain only for `desktop-v*` release jobs.
- Verify the tag against `Packaging/DESKTOP_VERSION`, then build, sign, notarize, staple, extract,
  re-verify, checksum, and publish the archive.
- Upload unsigned/ad-hoc CI artifacts only as build evidence; never present them as distributable.

**Exit:** ordinary PRs cannot access release credentials, malformed desktop tags fail closed, and a
valid protected tag can produce the independently versioned native desktop artifact.

### Phase 3 — close fidelity and live-provider evidence

- Complete the WF-ROADMAP-0012 Light/Dark, larger text, Increased Contrast, Reduce Transparency,
  Reduce Motion, VoiceOver, keyboard, selection, streaming, cancellation, and failure-state sweep.
- Verify setup, upgrade, reinstall, service restart, app-closed gateway operation, Keychain
  missing/locked/denied/rotated cases, and rollback on a clean Apple Silicon account.
- On an eligible macOS 26+ Mac, prove Apple Foundation Models inference, cancellation, app-closed
  delivery, and rejection of copied, ad-hoc, or wrong-Team components.
- Against the exact supported ChatGPT/Codex build, prove login, refresh, logout, model discovery, Sol
  delivery, cancellation, sleep/wake recovery, missing/expired account handling, and the adversarial
  no-tools/no-filesystem/no-extra-network isolation contract.
- Record hardware, OS, app and helper versions, signing identity, artifact hash, commands, and
  observed outcomes without secrets or account tokens.

**Exit:** the fidelity checklist has no P0/P1 findings and both optional provider paths have signed
live evidence against the final production topology.

### Phase 4 — publish desktop-v0.1.0

- Move the desktop entries from `Unreleased` into a dated `Desktop v0.1.0` release record.
- Create the protected `desktop-v0.1.0` tag from reviewed `main`.
- Publish the final ZIP, checksum, release notes, supported-platform statement, install/rollback
  steps, and known external ChatGPT-app dependency.
- Install the downloaded release archive—not the build directory—and repeat Gatekeeper, version,
  architecture, health, Apple, and ChatGPT smoke checks.

**Exit:** the downloadable artifact is byte-identified, Gatekeeper-clean, reproducible from the tag,
and honest about its supported platform and external provider dependency.

## Explicit exclusions

- Intel or universal macOS support.
- Bundling or redistributing Codex, importing ChatGPT tokens, or reading `~/.codex`.
- Making ChatGPT an automatic/default route, changing Chat's `Automatic` destination, or applying
  the Apple Local setup preset to an existing configuration.
- Expanding `WayfinderCredentialBroker` or moving gateway/provider ownership into Swift.
- A DMG, Homebrew cask, Sparkle/Tauri updater, Mac App Store package, or automatic promotion.
- Standalone package-manager distribution; any future channel needs its own native support contract.
- iOS implementation; that begins as a separate roadmap after the desktop release gate closes.

## Verification record

Repository evidence lives in:

- `docs/desktop-fidelity.md` — native interaction and accessibility checklist;
- `docs/desktop-release-evidence.md` — signed artifact, clean-machine, and provider evidence;
- `macos/WayfinderMac/Packaging/RELEASE.md` — operator build, verification, and rollback procedure;
- `.github/workflows/desktop-ci.yml` and `.github/workflows/desktop-release.yml` — automated gates.

## Related

- WF-ROADMAP-0012 — native desktop v0.1.0 UX and Chat contract
- WF-DESIGN-0016 — signed Rust helper integration
- WF-DESIGN-0017 — Apple Foundation Models provider
- WF-DESIGN-0018 — ChatGPT-authenticated Codex app-server provider
- WF-ADR-0042 — thin native desktop client over one gateway
- WF-ADR-0045 — Rust gateway/helper architecture
