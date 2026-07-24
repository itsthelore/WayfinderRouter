---
schema_version: 1
id: WF-DESIGN-0020
type: design
status: accepted-for-implementation
date: 2026-07-24
tags: [ios, ipados, swiftui, chat, navigation, routing, ux]
---

# Mobile thread-first chat shell

## Summary

Wayfinder mobile adopts the interaction hierarchy that makes the ChatGPT
mobile client effective: the conversation owns the screen, secondary
navigation stays out of the way, and the composer is always reachable. This is
an interaction benchmark, not a copy of another product's name, iconography,
assets, or provider controls.

Wayfinder's distinction remains visible through its green accent, deterministic
route receipts, execution-boundary language, and privacy control. Those
elements support the conversation instead of turning Chat into a routing
dashboard.

## iPhone contract

- Chat is the default and dominant screen.
- A leading button opens a slide-over drawer for the current conversation,
  Destinations, and Settings.
- There is no persistent bottom tab bar.
- New chat is always available in the top trailing position and drawer.
- The title exposes the current Automatic routing mode without presenting
  implementation detail as the page title.
- The empty state is short, centred, and useful. Suggestions may populate the
  composer but must not fabricate provider capability.
- The composer is attached with `safeAreaInset`, uses a neutral elevated
  surface, supports multiple lines, and keeps routing/privacy controls compact.
- Attachment affordance may be visible only when its unavailable state is
  explicit and accessible.

The implementation may retain a hidden `TabView` or equivalent state container
to preserve a separate `NavigationStack` per section. That is an implementation
detail, not visible navigation.

## iPad contract

iPad uses `NavigationSplitView` with the same content hierarchy:

- conversations and product destinations in the sidebar;
- transcript and composer in the detail;
- no default third routing column;
- route detail appears in a sheet or other transient presentation from the
  receipt.

Collapsing the split view must produce the iPhone navigation model rather than
compress three desktop columns into the available width.

## Transcript and routing receipts

User messages use a quiet trailing bubble. Assistant output, once provider
execution exists, reads as normal transcript content rather than a card.

Every terminal turn exposes a compact, tappable receipt:

```text
Ran on this iPhone · Apple On-Device
Ran in hosted cloud · OpenAI Platform
Tom's Mac -> Qwen 3 · Mac local
```

The detail presentation owns destination, execution boundary, routing tier,
score, reason codes, fallback truth, and bounded error recovery. The transcript
never reserves a permanent dashboard for those fields.

The current shell slice uses deterministic route previews only. It must say
that no provider was contacted and must not render a fabricated assistant
answer.

## Composer

The composer contains:

1. a multiline `Message Wayfinder` field;
2. a compact Automatic route label;
3. an on-demand privacy-posture menu;
4. an enabled send control only when the trimmed draft is non-empty.

The send action is currently labelled `Preview route` for accessibility because
provider execution is outside this slice. That label changes to `Send message`
when real execution lands.

The composer uses system materials and semantic colours. A strong permanent
green outline is prohibited; the accent belongs on active actions and routing
identity.

## Accessibility and state rules

- Drawer content is modal to assistive technology while open.
- Obscured page content is not focusable while the drawer is open.
- Icon-only controls have explicit labels and, where necessary, hints.
- Route receipt rows combine into one useful reading unit.
- Dynamic Type may expand the composer and rows without hiding send, privacy,
  or navigation actions.
- Interactive keyboard dismissal must not discard the draft.
- Starting a new chat clears only transient conversation state and returns to
  Chat.

## Out of scope

- provider execution or streaming;
- durable thread storage;
- account or credential setup;
- Apple Foundation Models execution;
- paired Mac support;
- downloadable assets or branded imitation;
- a permanent routing inspector on mobile.

## Acceptance

This shell is accepted when:

1. iPhone shows no persistent bottom navigation or routing dashboard;
2. the drawer reaches every existing section and dismisses by selection or
   scrim;
3. iPad exposes the same hierarchy through a native split view;
4. composer, suggestions, new chat, privacy selection, route preview, and
   receipt detail are functional;
5. preview copy never implies a provider responded;
6. source compiles for iOS and remote simulator checks pass.
