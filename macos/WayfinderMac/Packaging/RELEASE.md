# Native helper release and rollback

The production artifact is `Wayfinder.app`; the nested Rust executable is never updated
independently. Python/Homebrew remains the rollback backend until the default/removal decisions.

## Build prerequisites

- Xcode/Command Line Tools supporting macOS 14 or later.
- Rust targets `aarch64-apple-darwin` and `x86_64-apple-darwin`.
- A Developer ID Application identity supplied as `CODESIGN_IDENTITY`.
- For notarization, a `notarytool` keychain profile supplied as `NOTARYTOOL_PROFILE`.

Run `script/build_release_bundle.sh`. It builds both thin Rust and Swift artifacts, verifies every
universal slice, assembles the gateway as the containing
`Contents/Helpers/WayfinderGateway.app` with both XPC services nested inside it, signs inner code
before outer code, verifies that all production components share one non-empty Team ID, and
performs strict signature verification. When a notarization profile is present it also
submits, staples, validates, and runs Gatekeeper assessment.

## Ownership and coexistence

- Exactly one job owns `com.wayfinder-router.gateway` and port 8088.
- Bundled Rust selection uses the verified absolute path under
  `Wayfinder.app/Contents/Helpers/WayfinderGateway.app/Contents/MacOS`; production discovery never
  falls back to `PATH`.
- A helper running from that bundled path requires `xpc-credential-broker-v1`; it does not fall
  back to environment or legacy command values when broker resolution fails.
- Python/Homebrew selection remains explicit. Switching first boots out the current job, verifies
  ownership, and then installs the selected backend through its `service` command.
- Uninstall never deletes configuration, savings state, cache policy, feedback, or Keychain items.

## Clean-machine matrix

Exercise on physical Apple Silicon and Intel Macs:

1. Verify `lipo -archs` reports both architectures for the app, helper, and XPC executable.
2. Run `codesign --verify --deep --strict`, `spctl --assess`, and `stapler validate`.
3. Install with no prior config; complete setup; verify launchd identity and `/healthz`.
4. Quit the UI and verify the helper continues and the on-demand broker resolves a key.
5. Test missing, locked, denied, rotated, and deleted Keychain items without secret output.
6. Kill the helper and verify bounded launchd restart; restart after login/logout.
7. Interrupt install after each external mutation and verify reassessment is idempotent.
8. Update from the prior signed version; verify config, ledger, and Keychain preservation.
9. Force failed health/capability agreement and verify restoration of the previous signed bundle.
10. Switch bundled Rust to and from Python/Homebrew and verify one label/port owner.
11. On an eligible macOS 26+ Apple Silicon Mac, verify Apple inference while the menu-bar UI is
    closed and verify copied, ad-hoc, and mismatched-Team components are rejected.

Release evidence must record hardware, OS version, artifact hash, signing identity, command output,
and observed state. An arm64 process under Rosetta is not Intel evidence.
