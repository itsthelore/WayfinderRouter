---
schema_version: 1
id: WF-ROADMAP-0013
type: roadmap
tags: [desktop, macos, swiftui, onboarding, first-run, homebrew, service, keychain, accessibility]
---

# Roadmap: ship a guided native first-run setup for the Homebrew-installed macOS app

## Status

Proposed

## Decision summary

The native macOS app needs a focused, dismissible **Setup Assistant** for first run and recovery.
Homebrew installs the app and gateway tooling; the assistant configures this user’s routing,
credentials, and launch agent through the existing gateway CLI seams. A new user must not need to
discover terminal commands, edit TOML, or infer why a menu-bar icon says “Not Installed.”

This is not a permanent onboarding dashboard and it does not live inside the 340 pt popover. It is
a small native utility window that appears when setup is genuinely incomplete, can be reopened from
Settings, and disappears from the normal path after success.

The assistant preserves the existing product boundaries:

- the Homebrew cask installs `Wayfinder.app` and depends on the `wayfinder-router` formula;
- the gateway CLI remains the only config author (WF-ADR-0044);
- the service remains launchd-owned and the app never supervises it (WF-ADR-0042/0038);
- credentials go directly to Keychain and never persist in Swift state, argv, logs, or files;
- the gateway remains the source of truth for health, models, routes, and readiness;
- endpoint “Ready” means configured and key-ready, not verified provider uptime;
- only offline/local-only operation may claim that nothing leaves the machine.

## Outcomes

- `brew install --cask wayfinder` leads to a usable router without requiring further terminal work.
- A new user understands what Wayfinder does, chooses an appropriate routing preset, supplies only
  required credentials, and reaches a truthful ready/degraded state in a few minutes.
- Existing users and hand-authored configurations are detected and preserved.
- Partial setup, missing dependencies, service failures, and key failures are recoverable without
  reinstalling the app.
- The normal menu-bar popover remains a compact routing instrument, not an onboarding surface.

## Initiatives

- **Align product and packaging contracts:** reconcile the native-v1 onboarding non-goal, define
  the cask/formula dependency, and document which layer owns installation versus user setup.
- **Build a fact-based setup engine:** assess CLI, config, service, gateway, endpoint, and key state;
  plan only whitelisted gateway commands; and recover safely from partial completion.
- **Deliver the native Setup Assistant:** implement the guided six-step window, secure credential
  entry, progress, cancellation, retries, existing-config handling, and Settings recovery entry.
- **Integrate without bloating the popover:** expose only truthful setup status and one setup action
  while incomplete, then restore the accepted compact routing hierarchy after success.
- **Prove the clean-machine journey:** test Homebrew install, setup variants, accessibility,
  interruption/recovery, upgrades, uninstall behavior, and secret-handling boundaries on real Macs.

## User journey

### Entry conditions

On launch, classify setup from observable facts rather than one persisted “onboarded” boolean:

| Condition | Result |
|---|---|
| CLI missing | Setup Assistant → Tools Missing |
| Config missing and service absent | Setup Assistant → Welcome |
| Config exists, service absent | Setup Assistant → Existing Configuration |
| Service exists but is stopped | Normal popover shows Stopped; Gateway Settings offers Start/Repair |
| Gateway reachable, no endpoints | Setup Assistant → Choose Routing |
| Gateway reachable, missing keys | Normal app opens; Setup Assistant may resume at Credentials |
| Gateway healthy/offline | Normal app; assistant does not auto-open |
| User previously chose Set Up Later | Normal compact popover with `Set Up Wayfinder…` until setup succeeds |

Do not auto-open the assistant for a transient unreachable state after the gateway has previously
been healthy. That is an operational failure, not first run.

### Step 1 — Welcome

- Title: **Set up Wayfinder**.
- One-sentence explanation: “Wayfinder routes each request to a configured local or hosted model.”
- State that routing decisions are computed locally without a model call.
- Actions: **Continue**, **Use Existing Configuration…**, and **Set Up Later**.
- Do not show marketing panels, metrics, terminal commands, or decorative illustrations.

### Step 2 — Choose routing

Present native radio rows with a title, one-line behavior summary, and requirements:

1. **Hybrid — Recommended**: local endpoint plus a hosted fallback; requires the relevant local
   runtime and one hosted-provider key.
2. **Local only**: configured local endpoint; no provider key; explicitly eligible for the
   nothing-leaves-this-Mac claim when offline delivery is enforced.
3. **OpenAI**: hosted cost/capability tiers; requires an OpenAI key.
4. **Gemini**: hosted cost/capability tiers; requires a Gemini key.

Preset identifiers and descriptions must come from a gateway-owned machine-readable surface or a
versioned shared contract. The app must not independently recreate preset TOML or routing rules.

### Step 3 — Check requirements

- Resolve `wayfinder-router` using an injected resolver that checks the inherited `PATH`,
  `/opt/homebrew/bin`, and `/usr/local/bin`; accept only an executable file.
- Detect preset-specific local runtimes without installing or launching third-party software.
- For a missing Homebrew component, show one exact copyable command and **Check Again**.
- Never invoke `brew install`, request administrator credentials, or mutate Homebrew from the app.
- Hide this step when all requirements are met.

### Step 4 — Credentials

- Show only credentials required by the selected preset.
- Use `SecureField`, provider name, concise purpose, and an optional provider-console help link.
- Pass key material directly to the existing Keychain writer over stdin; clear the field after the
  operation completes.
- Never place secrets in process arguments, error descriptions, analytics, unified logs, crash
  breadcrumbs, pasteboard, persisted setup state, or accessibility values.
- Local-only setup skips this step.
- Support Back and Set Up Later without persisting entered key material.

### Step 5 — Configure and start

Run a single cancellable setup operation with explicit progress stages:

1. **Creating routing configuration**
   `wayfinder-router init --preset <id> --keychain --path <well-known-path>`.
2. **Updating the gateway service**
   best-effort `service uninstall`, then `service install --config <well-known-path>`.
3. **Saving credentials**
   write each required key to Keychain over stdin.
4. **Restarting the gateway**
   restart after key changes so command-resolved keys are loaded.
5. **Checking configuration**
   poll `/healthz` and `/router/models` with a bounded timeout and backoff.

An existing config is never overwritten. If `init` reports that the target exists, stop and offer
**Use Existing Configuration** or **Choose Another Location**; never add `--force` automatically.

The progress view shows the current stage and a determinate step count, not a fake percentage.
Cancel stops future stages but does not pretend already completed external mutations were rolled
back. Re-entry re-detects actual state and resumes safely.

### Step 6 — Result

Success shows:

- routing preset;
- gateway address;
- configured endpoint count;
- endpoint readiness summary;
- **Open Wayfinder** as the default action.

A degraded result is allowed when configuration succeeded but a key or local runtime is missing.
Name the exact remediation and offer **Open Keys**, **Check Again**, or **Open Gateway Settings**.
Do not label provider connectivity as verified.

## Normal popover behavior

The assistant must not enlarge or replace the normal popover hierarchy.

When setup is incomplete, the popover renders:

1. Header — Wayfinder / Not Set Up.
2. Routing — No routing history.
3. Endpoint Status — Unavailable or the truthful configured summary.
4. Disabled Chat — Coming later.
5. **Set Up Wayfinder…**.
6. Settings….
7. Quit Wayfinder.

After successful setup, `Set Up Wayfinder…` disappears and the accepted v1 hierarchy returns.
Settings → Gateway always offers **Run Setup Assistant…** as a recovery/reconfiguration action.

## Window and interaction contract

- Native SwiftUI window retained by a narrow AppKit controller, matching the existing Settings
  ownership pattern.
- Target size 560×460 pt; minimum 520×420 pt; resizable only when accessibility text requires it.
- One content column, one short description per step, standard controls, no cards or hero art.
- Bottom action row remains stable: Back on the left; Set Up Later where applicable; primary action
  on the right.
- Closing the window is equivalent to Set Up Later unless an external mutation is actively running;
  during mutation, closing requires a concise confirmation that completed steps remain applied.
- Restore the current step only when it is safe and contains no secret. Always recompute external
  state on reopen.
- Full keyboard operation, visible focus, VoiceOver step/title/progress announcements, Increased
  Contrast, Reduce Transparency, Reduce Motion, and larger accessibility text are release gates.

## Delivery phases

### Phase 0 — reconcile governing contracts and packaging

- Amend WF-ROADMAP-0012’s “no onboarding wizard” non-goal to prohibit onboarding inside the
  popover or a permanent dashboard, while explicitly allowing this focused Setup Assistant.
- Amend `macos/README.md` to list Setup Assistant as a shipping v1 surface.
- Amend WF-DESIGN-0015 for the native Swift/Homebrew flow and remove Tauri-specific assumptions
  where they are no longer authoritative.
- Add a Homebrew tap layout containing a formula and cask, or document the external tap repository
  and its versioning contract.
- Cask depends on the gateway formula; installation itself performs no user config mutation and
  does not start a service.

**Exit criterion:** docs agree on ownership, launch behavior, CLI discovery, and the distinction
between first run and operational failure.

### Phase 1 — pure setup state and command planning

- Add `SetupAssessment`, `SetupStep`, `SetupPreset`, `SetupProgress`, and `SetupFailure` value
  models.
- Add a pure transition reducer/state machine; views must not infer next steps ad hoc.
- Add `GatewayToolResolver` with injected filesystem/environment inputs.
- Add a `SetupCommandPlan` that emits fixed executable + argument arrays for approved presets only.
- Reject unknown preset IDs, non-executable tool paths, unsafe config paths, and unexpected key
  identifiers before spawning any process.
- Distinguish `neverConfigured`, `existingConfig`, `stopped`, `unreachableAfterSuccess`,
  `missingKeys`, and `healthy`.

**Tests:** table-test every assessment and transition, Apple Silicon/Intel paths, existing config,
cancel/re-entry, unknown presets, and unsafe inputs.

### Phase 2 — setup service orchestration

- Extend the service layer with async `assess`, `initialize`, `install`, `start/restart`, and
  bounded `verify` operations.
- Use the gateway CLI for config/service mutations; do not write TOML or launchd plists in Swift.
- Stream only sanitized stage/result events to UI state.
- Make uninstall/install re-entry idempotent and surface actionable stderr without secret values.
- Reuse the Keychain credential boundary and restart semantics already used by Keys Settings.
- Define cancellation points between external mutations and record no fictional rollback state.

**Tests:** injected process runner covers success, partial failure at every stage, cancellation,
existing config preservation, sanitized errors, bounded polling, and restart after key changes.

### Phase 3 — native Setup Assistant window

- Add `SetupWindowController`, `SetupAssistantView`, and focused step views under
  `UI/Setup/`.
- Retain window-scoped state in the setup root; keep services injected and global `AppState` free
  of transient form/secret state.
- Implement stable Back/primary action placement, progress, recovery actions, and safe close.
- Add Settings → Gateway → Run Setup Assistant….
- On first launch, present only after assessment resolves; never flash Welcome during Checking.
- If the user chooses Set Up Later, persist only that preference and continue honest assessment on
  every launch.

**Tests:** view-model/state tests for navigation, disabled actions, skipped credentials, retry,
set-up-later, and existing-config handling.

### Phase 4 — compact popover integration

- Add an explicit setup presentation model derived from setup assessment plus gateway truth.
- Show `Set Up Wayfinder…` only when setup is incomplete or explicitly deferred.
- Keep Chat technically blocked and retain the 340×420 pt sizing contract.
- Ensure setup state never removes Settings or Quit and never adds raw diagnostic rows.
- Refresh gateway/setup assessment when the assistant completes or closes.

**Tests:** measure incomplete, deferred, degraded, and complete popovers; prove no clipping, scroll,
Chat route, or stale setup action.

### Phase 5 — Homebrew and clean-machine release gate

- Build signed/notarized universal app artifacts and version-matched gateway formula artifacts.
- Test cask→formula dependency installation on clean Apple Silicon and Intel macOS systems.
- Test install, first run, Set Up Later, local-only, hosted preset, hybrid with missing runtime,
  existing config, interrupted setup, repair, upgrade, downgrade refusal, and uninstall/reinstall.
- Confirm formula upgrades retain stable executable resolution and do not orphan the launch agent.
- Confirm cask uninstall does not silently delete user config or Keychain entries; document explicit
  full-removal commands separately.
- Run `brew audit`/`brew install`/`brew uninstall` checks appropriate to the target tap.

**Release gate:** a clean user can install with one Homebrew command and finish setup without a
terminal; all failure states are recoverable; no secrets appear in argv/logs/state; no P0/P1 UX or
accessibility findings remain.

## File-level implementation map

- App launch and presentation: `WayfinderMacApp.swift`, `Windowing/SetupWindowController.swift`.
- Setup UI: `UI/Setup/SetupAssistantView.swift` and one focused view per step.
- Setup models/state: `Models/SetupAssessment.swift`, `State/SetupState.swift`.
- CLI/service orchestration: `Services/GatewayToolResolver.swift`,
  `Services/SetupService.swift`, existing `GatewayServiceController.swift`.
- Key handling: existing `KeychainCredentialStore.swift`; do not add a second credential writer.
- Popover integration: `Models/PopoverPresentation.swift`,
  `UI/MenuBarPopover/WayfinderPopoverView.swift`.
- Settings recovery entry: `UI/Settings/GatewaySettingsView.swift`.
- Packaging: Homebrew tap formula/cask definitions and the existing signed app release workflow.
- Verification: `Tests/WayfinderMacTests/` plus clean-machine setup screenshots/checklist.

## Success measures

- Median clean first run reaches configured gateway state in under three minutes, excluding
  third-party runtime downloads.
- No terminal command or TOML edit is required after the Homebrew install command.
- Local-only setup completes without requesting a hosted-provider key.
- Existing configs survive setup, repair, upgrade, and cask reinstall byte-for-byte unless the
  gateway CLI performs an explicit validated mutation requested by the user.
- Every external setup stage has an actionable failure and retry path.
- Zero secret material in persisted Swift state, argv, logs, crash reports, screenshots, or
  accessibility output.
- The normal popover remains within 340×420 pt and Setup Assistant remains a separate surface.

## Non-goals

- Installing Homebrew or invoking `brew install` from inside the app.
- Downloading or installing Ollama or other third-party runtimes.
- Editing TOML or launchd plists directly in Swift.
- Verifying provider uptime or billing/account validity during setup.
- A permanent welcome dashboard, account system, telemetry funnel, product tour, or Chat setup.
- Deleting existing configuration or Keychain credentials during ordinary cask uninstall.

## Risks

- Formula/cask version skew can leave the app expecting CLI capabilities that are absent. Mitigate
  with a machine-readable CLI capability/version probe and explicit minimum-version failure.
- Process orchestration can leave partial external state. Mitigate with fact-based reassessment,
  idempotent commands, bounded stages, and honest recovery instead of rollback claims.
- Keychain ACL behavior can differ in headless launchd contexts. Retain the real-Mac security smoke
  test required by WF-DESIGN-0015.
- Automatically opening a window from a menu-bar accessory app can feel intrusive. Limit it to
  genuine first run, honor Set Up Later, and never reopen it for transient outages.
- Preset definitions can drift from the Python gateway. Consume gateway-owned metadata or maintain
  a versioned shared contract; never duplicate routing configuration in Swift.

## Related

- WF-ROADMAP-0012 (native v1 UX and compact popover)
- WF-DESIGN-0015 (onboarding, Keychain, and config scaffold)
- WF-ADR-0042 (thin menu-bar client and service lifecycle)
- WF-ADR-0044 (gateway CLI as the only config author)
- WF-ADR-0038 (launchd service surface)
