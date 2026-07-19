# Wayfinder Desktop v0.1.0 fidelity checklist

Status: release gate; incomplete until every required row has dated evidence and no open P0/P1
finding. Governing contract: WF-ROADMAP-0012 and WF-ROADMAP-0015.

This checklist reviews the final staged or signed app, not SwiftUI previews. Record screenshots in a
release-specific evidence folder without credentials, account tokens, private prompts, or unrelated
desktop content.

## Run metadata

| Field | Value |
|---|---|
| Date/time | Pending |
| Commit | Pending |
| Desktop version/build | Pending |
| Artifact SHA-256 | Pending |
| Mac model / architecture | Pending / arm64 |
| macOS version | Pending |
| Appearance and accessibility configuration | Pending |
| Reviewer | Pending |

## Severity and completion

- **P0:** blocks use, risks data/credential exposure, or violates the one-gateway boundary.
- **P1:** broken primary workflow, inaccessible required action, clipping, misleading state, or lost
  recovery path. Blocks release.
- **P2:** material polish or secondary-workflow defect. Record with an owner and disposition.
- Mark a row complete only after observing it in the final app. Automated coverage may support but
  does not replace visual, keyboard, or assistive-technology observation.

## Menu-bar popover

- [ ] Default healthy state fits within the 340 pt target width and 420 pt maximum height without a
  partial row, clipping, or a normal-state scroll bar.
- [ ] Checking, degraded, offline, stopped, not installed, unreachable, and setup-incomplete states
  preserve Settings and Quit and do not duplicate the same diagnosis.
- [ ] Endpoint Status opens and closes predictably; click-away, Escape, focus restoration, all Spaces,
  and no-Dock behavior match the transient-panel contract.
- [ ] Chat opens one retained dedicated window and never appears as a second setup or popover screen.
- [ ] Pointer and keyboard paths expose every action; Command-R refresh remains available without a
  permanent Refresh row.

## Settings and setup

- [ ] The window opens at the accepted compact size, restores selection, and uses native sidebar/form
  behavior without disabled placeholder destinations.
- [ ] Gateway install/start/stop/restart, routing, Offline, Keys, Accounts, Privacy, Help, and About
  show truthful current state and an actionable recovery path.
- [ ] Setup handles new, existing, deferred, interrupted, repaired, and already-complete states
  without editing TOML in Swift or deleting existing configuration.
- [ ] On an eligible never-configured Mac, Apple Local is preselected only after a live `available`
  response and still requires confirmation; existing configurations are never silently rewritten.
- [ ] ChatGPT account states cover Checking, Signed Out, browser/device flow, Connected,
  Re-authentication Required, Unavailable, Failed, cancellation, and confirmed logout.
- [ ] Account identity remains in Settings; provider/model/route detail remains in Chat's inspector.

## Chat

- [ ] The complete chronological transcript and composer remain primary at the default and minimum
  supported window widths.
- [ ] Navigator search/filter changes only navigator results; they never remove, reorder, or
  implicitly reselect transcript turns.
- [ ] Selecting an older turn scrolls to it and updates the inspector; later stream fragments do not
  steal the selection or auto-scroll unless the user is following the latest turn near the bottom.
- [ ] The right inspector owns provider, model, mode, score, explanation, and signals; completed turns
  show only the quiet routing receipt inline.
- [ ] Pending, decision-only, streaming, complete, stopped, failed, Busy, offline, and unavailable
  turns remain selectable and show truthful delivery and routing states.
- [ ] Return sends, Shift-Return inserts a newline, Stop is immediate, Retry is contextual, New Chat
  resets the bounded in-memory session, and copy/selection behave natively.
- [ ] Collapsing either side pane keeps the transcript usable and leaves routing information
  discoverable.
- [ ] Automatic remains the initial destination. Explicit ChatGPT selection never silently falls
  back when signed out, unavailable, or excluded by Offline mode.

## Appearance and accessibility matrix

Repeat the required surfaces in each row:

| Configuration | Popover | Settings/setup | Chat | Result/evidence |
|---|---:|---:|---:|---|
| Light, default text | [ ] | [ ] | [ ] | Pending |
| Dark, default text | [ ] | [ ] | [ ] | Pending |
| Larger accessibility text | [ ] | [ ] | [ ] | Pending |
| Increased Contrast | [ ] | [ ] | [ ] | Pending |
| Reduce Transparency | [ ] | [ ] | [ ] | Pending |
| Reduce Motion | [ ] | [ ] | [ ] | Pending |

For every configuration:

- [ ] Text, controls, focus rings, selection, local teal, degraded/cloud amber, and destructive states
  retain adequate contrast and do not rely on color alone.
- [ ] Content reflows or scrolls intentionally; no label truncates a required distinction and no
  action is clipped.
- [ ] Reduced Motion removes nonessential transitions without hiding state changes.
- [ ] Reduce Transparency replaces material with a readable opaque-enough surface.

## VoiceOver and keyboard

- [ ] VoiceOver order follows visual order in popover, Settings/setup, transcript, composer,
  navigator, and inspector.
- [ ] Selected rows announce selection; symbols do not duplicate labels; status text is concise and
  does not expose secret/account-file data.
- [ ] Chat, Settings, setup recovery, destructive confirmation, Stop, Retry, pane toggles, search,
  and destination selection are operable without a pointer.
- [ ] Focus remains visible and returns to the initiating surface after closing a menu, sheet, setup
  window, Settings window, Chat window, or transient panel.

## Findings

| ID | Severity | Surface/state | Finding | Owner | Disposition / verification |
|---|---|---|---|---|---|
| — | — | — | No findings recorded yet | — | Pending review |

## Release sign-off

- [ ] All required configurations were exercised against the final release candidate.
- [ ] Screenshot references are attached and contain no private information.
- [ ] Automated Swift/streaming/service tests are green for the reviewed commit.
- [ ] No P0/P1 finding remains open.
- [ ] Any accepted P2 is documented in release notes with an owner and follow-up.
