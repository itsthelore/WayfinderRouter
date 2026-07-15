---
schema_version: 1
id: WF-ROADMAP-0012
type: roadmap
tags: [desktop, macos, swiftui, menu-bar, settings, v1, ux, accessibility, chat]
---

# Roadmap: ship the native macOS v1 as a compact routing utility — and block Chat until it is ready

## Status

Proposed

## Decision summary

The native Swift app ships v1 as a **small, calm menu-bar utility with a compact native Settings
window**. It should feel closer to the macOS Wi-Fi popover and System Settings than to a dashboard:
short rows, system materials, hairline separators, restrained symbols, terse status, and deeper
explanation moved out of the glance surface.

**Chat is not a v1 feature.** Its entry may remain visible as one disabled row labelled
“Coming later,” but the app must not create, open, shortcut into, or otherwise expose the current
Chat window in a release build. Chat returns only after a separate UX milestone proves its
interaction model, gateway response/history contract, accessibility, and visual quality.

This roadmap intentionally changes three accepted assumptions that currently pull implementation
back toward the wrong result:

- WF-ROADMAP-0009 says “chat included in v1.” This roadmap supersedes that v1 scope decision.
- WF-DESIGN-0014 fixes the popover at 400×550 and describes a Chat drill-in. The compact native
  amendment replaces those layout and Chat requirements for the Swift app.
- WF-ADR-0042’s 400×550 amendment is no longer the native Swift target. The accessory-app,
  service-first, Keychain, privacy, and thin-client decisions remain unchanged.

No implementation phase starts until those documents are amended, so a future contributor cannot
follow the old contracts correctly and still recreate the oversized interface.

## Reference grammar

Use the supplied macOS Wi-Fi popover as the primary shape reference:

- one compact title/status row;
- optional section labels only where they improve scanning;
- stable 32–36 pt rows with one leading symbol slot at most;
- primary label left, concise value/state right;
- hairlines instead of cards or large rounded containers;
- system material, system typography, and semantic colors;
- a single Settings… exit at the bottom, with secondary app actions kept quiet;
- detail and diagnostics live in Settings, not in the transient popover.

This is a grammar reference, not a pixel-for-pixel copy. WAYFINDER keeps teal for local/interactive,
amber for cloud/degraded, neutral structure, and the rule that only offline mode guarantees nothing
leaves the machine.

## V1 information architecture

### Menu-bar popover

Top to bottom:

1. **Header** — “Wayfinder” and one terse overall state: Running, Degraded, Offline, Stopped,
   Checking, or Unreachable. No status capsule and no explanatory paragraph.
2. **Gateway row** — one line of secondary detail only when action is needed. Hosted-key/model
   diagnostics move to Settings.
3. **Routing row** — local/cloud composition, counts if available, and one compact 6 pt split bar.
4. **Endpoint Status row** — opens a compact native sibling submenu listing configured provider,
   model, route alias, and readiness.
5. **Chat row** — disabled in v1, no chevron, trailing “Coming later,” with a VoiceOver hint that
   the feature is unavailable in this release.
6. **Footer** — Settings… and Quit Wayfinder. Refresh remains a keyboard command and automatic
   refresh-on-open behavior, not a permanent row.

The popover uses a **340 pt target width** and intrinsic content height, clamped to a **420 pt
maximum** at the default text size. It never shows a half row and does not need a scroll view in
normal v1 states. Exact height comes from content, not a second hard-coded 550 pt canvas.

### Settings window

Settings is the deeper operational surface, but it still should not read like an admin dashboard.

- Target initial size: **700×520 pt**; minimum **620×460 pt**.
- Native `NavigationSplitView` or sidebar-style `List(selection:)`; 170–190 pt sidebar.
- Show only shipped sections. Do not list disabled General/About destinations with “Coming Soon.”
- Use `Form`, `Section`, `LabeledContent`, `Toggle`, `Picker`, and standard buttons before custom
  rows or panels.
- Put one short description under a section title, not under every value.
- Move endpoint details, route names, service lifecycle controls, and copy actions into Gateway.
- Keep routine status visible; place raw diagnostics and explanatory material behind a disclosure.
- Keys keeps provider selection, key status, save/remove, and the Keychain explanation; removal
  remains confirmed.
- Routing exposes only settings the UI can round-trip safely. Unsupported lexicons, classifier
  data, or tier costs remain read-only with an Open Config action.
- Privacy copy remains exact and compact; Help contains the longer “one gateway, many apps” story.

## Delivery phases

### Phase 0 — change the source of truth

Before Swift changes:

- Amend WF-ADR-0042: replace the native 400×550 requirement with intrinsic compact sizing and
  record Chat as post-v1.
- Amend WF-DESIGN-0014: add a native Swift compact-popover section, remove Chat from the v1
  component inventory, and record the Wi-Fi/System Settings reference grammar.
- Amend WF-ROADMAP-0009: mark “chat included in v1” superseded and link this roadmap.
- Update `macos/README.md` so its implemented-surface list distinguishes v1 shipping surfaces from
  blocked/post-v1 work.
- Add the supplied screenshot to a stable repo-owned design-reference location with provenance;
  do not leave the design target dependent on a clipboard path.

**Exit criterion:** searching the governing docs for “400×550” or “chat included in v1” cannot
lead an implementer to treat either as the current native Swift requirement.

### Phase 1 — block Chat at the capability boundary

Block the feature before polishing anything else:

- Introduce a single release availability policy, e.g. `AppFeature.chat = .blocked(reason:)`.
- Do not instantiate `ChatWindowController` when Chat is blocked.
- Remove/disable every route to `showChatWindow()`, including popover action, shortcuts, commands,
  tests, and future deep-link seams.
- Render the compact disabled Chat row from availability state; do not fake an active destination.
- Keep Chat source files in-tree for later work, but exclude them from v1 interaction and fidelity
  acceptance. Do not spend v1 polish time refining the current 1180 pt three-pane window.

**Acceptance:** automated tests prove a release configuration cannot create or show a Chat window;
keyboard navigation skips the disabled action; VoiceOver announces why it is unavailable.

### Phase 2 — rebuild the popover around native compact rows

- Replace the fixed-height root with measured SwiftUI fitting size plus a safe maximum in the
  narrow AppKit panel bridge.
- Collapse header and global health into one compact header row.
- Remove the separate Hosted row from the popover; retain hosted readiness inside Gateway
  Settings and use Degraded in the header when it needs attention.
- Replace 36 pt circular icon wells with a plain 16 pt symbol slot or no symbol for metric rows.
- Recut Routing to a compact native row component with shared alignment and typography.
- Remove the visible Refresh row; refresh on show/focus and keep Command-R working.
- Use one inset rule consistently; remove special 80 pt separator offsets.
- Keep transient-panel behavior: anchored placement, click-away dismissal, Escape, all Spaces,
  Light/Dark appearance, and no Dock presence.

**Acceptance:** at default text size the full popover is ≤340×420 pt, contains no cards, no clipped
rows, no scroll bar, and exposes all actions by pointer and keyboard.

### Phase 3 — rebuild Settings with native controls and less copy

- Replace the button-stack sidebar with a native selectable sidebar list.
- Move window-local selection/provider state out of global `AppState` and into the retained
  Settings scene/root.
- Reduce the initial/minimum window sizes to the targets above and verify restoration on reopen.
- Convert each shipped page to native Form/Section/LabeledContent structure.
- Split the oversized Gateway and Routing view files into focused section views and small state
  owners; services remain outside SwiftUI.
- Delete unused competing settings roots and placeholder destinations once references are proven
  absent.
- Apply a copy budget: labels ≤30 characters; ordinary explanations one sentence; diagnostic
  detail behind disclosure or Help.

**Acceptance:** every shipped settings task is reachable without scrolling at 700×520 where its
content reasonably fits; no disabled placeholder destinations; no card-grid layout; window and
selection behavior are stable across close/reopen.

### Phase 4 — state, copy, and failure polish

Exercise the actual product states rather than only the happy path:

- Checking, healthy, degraded/missing key, offline, stopped, not installed, unreachable, empty
  routing history, unpriced savings, and actionable error.
- The popover states what happened and where to fix it, but never repeats the same health message
  in header, row, and footer.
- Settings owns install/restart/key/config remediation. The popover links to Settings rather than
  becoming a diagnostics surface.
- No fixture/mock values appear in production, and unavailable data collapses cleanly.
- Privacy language is reviewed against WF-ADR-0042: only offline mode says nothing leaves the Mac.

**Acceptance:** a state matrix test covers rendering and available actions for every state; no
state changes panel width, clips the footer, or exposes Chat.

### Phase 5 — fidelity and v1 release gate

Build a screenshot-and-accessibility review loop before packaging:

- Capture reference screenshots for popover and each Settings page in Light and Dark mode.
- Review at default text size and one larger accessibility size; Increased Contrast and Reduce
  Transparency must remain usable.
- VoiceOver order follows visual order; selected sidebar rows announce selection; disabled Chat
  announces its reason; symbols do not duplicate labels.
- Full keyboard pass: open/close popover, move through rows, open Settings, change sections, edit
  supported settings, cancel destructive actions, quit.
- Reduced Motion removes nonessential transitions.
- Run clean SwiftPM build/tests, then build and launch the staged `.app` bundle on macOS 14 and the
  current macOS release.

**Release gate:** no P0/P1 findings in the native fidelity checklist; all state-matrix and service
tests green; signed/notarized packaging work may proceed. Chat quality is not part of this gate
because Chat is blocked by construction.

## File-level implementation map

- Popover composition and compact sizing:
  `UI/MenuBarPopover/WayfinderPopoverView.swift`, `RoutingSummarySection.swift`,
  `EndpointStatusRow.swift`, `PopoverActionRow.swift`, `MenuBar/PopoverController.swift`.
- Feature availability and application wiring:
  `WayfinderMacApp.swift`, `Windowing/ChatWindowController.swift`, and a small model under
  `Models/` or `Support/`.
- Settings scene and selection:
  `UI/Settings/SettingsWindow.swift`, `SettingsSidebar.swift`, page-specific Settings views, and
  `Windowing/SettingsWindowController.swift`.
- State truthfulness:
  `State/AppState.swift`, `Models/RoutingStats.swift`, gateway/service clients.
- Verification:
  `Tests/WayfinderMacTests/` plus repo-owned native reference screenshots and a concise
  `docs/desktop-fidelity.md` checklist.

## Explicit non-goals for this v1 pass

- Polishing or shipping the current Chat window.
- Redesigning the routing algorithm or gateway API.
- Adding a dashboard, charts, logs browser, onboarding wizard, or extra menu-bar metrics.
- Replacing the service-first lifecycle or moving routing/key ownership into the app.
- Adding custom visual effects where system material and controls already solve the problem.

## Chat unblocking milestone (post-v1)

Chat becomes eligible for implementation only after a separate plan answers and prototypes:

- whether it is a compact routing-inspection conversation or a general chat client;
- authoritative assistant reply decoding, decision-only states, and last-N-turn gateway history;
- session boundaries, clear/new semantics, persistence policy, and transcript limits;
- a responsive one-/two-pane layout that works below 1180 pt;
- composer behavior, streaming/stop, error/retry, selection/copy, and keyboard commands;
- Light/Dark, VoiceOver, Reduced Motion, and screenshot fidelity acceptance.

Until that milestone is accepted, “blocked” means technically unreachable—not merely labelled beta.

## Success measures

- A first-time user can identify health, local/cloud routing, and the Settings exit in under five
  seconds without reading explanatory prose.
- The popover fits within 340×420 pt at default sizing and never scrolls in a normal v1 state.
- The Settings window opens at 700×520 pt and uses native selection and form controls.
- Chat cannot be opened in a v1 build by mouse, keyboard, or code path.
- All supported states are screenshot-reviewed and automated where practical.
- The v1 fidelity checklist has no P0/P1 defects and the clean package test/build gate is green.

## Related

- WF-ADR-0042 (desktop architecture; layout/Chat scope to be amended)
- WF-DESIGN-0014 (flat-list direction; compact native amendment required)
- WF-ROADMAP-0009 (desktop delivery; v1 Chat scope superseded)
- WF-DESIGN-0015 (Settings/config seam)
