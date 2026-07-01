---
schema_version: 1
id: WF-DESIGN-0012
type: design
tags: [desktop, macos, popover, shadcn, tailwind, vibrancy, design-system, a11y, motion]
---

# WF-DESIGN-0012: The Desktop Popover — Design Contract (Tokens, Components, States, Motion)

## Status

Accepted

> The aesthetic and UX contract for the 360×480 menu-bar popover (WF-ADR-0042): shadcn/ui themed
> with the canonical Wayfinder tokens over native vibrancy, a decision-first hierarchy (the route
> is the hero, chat is the verb, status is ambient), two pure state machines, one easing curve.
> The reference for restraint is June's native-calm feel; the palette and hierarchy are our own
> (WF-ADR-0020). Nothing here scores or decides — every component renders what the gateway said.

## Context

The popover is Wayfinder's first bespoke GUI. The design goal is an **instrument, not an app**: it
should read like a native macOS popover that happens to show a routing decision beautifully, not a
web page in a borderless window. The raw materials exist — `clients/shared/src/theme.js` carries the
canonical light/dark tokens (mirrored from `demo.html`, WF-ADR-0020), `decision.js` the render
helpers, `gateway.js` the wire client — and the shell (vibrancy, tray, ⌥Space, hide-on-blur) is
built. This document is the contract the React implementation is reviewed against.

## Platform & toolchain

- **Tailwind v4** (CSS-configured, `@tailwindcss/vite`) + **shadcn/ui** (new-york, css-variables) in
  `clients/desktop`. Tailwind v4's Safari floor (16.4) is why the bundle minimum is **macOS 14.0**
  (WF-ADR-0042 §6); Vite `build.target` rises to `safari16.4`.
- Dark mode is **`prefers-color-scheme` only** — a native popover follows the system; no toggle, no
  `.dark` class. Vendored shadcn components must not carry `dark:` utilities; the variables flip.
- A **theme-lint test** greps `components/ui/**` for raw zinc/neutral values and fails CI if the
  shadcn defaults survive theming.

## Tokens → slots (from `theme.js`, verbatim values)

Layering over the NSVisualEffectView `Popover` material:

| Layer | Surfaces | Treatment |
|---|---|---|
| L0 | native material | never fully painted over; `body { background: transparent }` |
| L1 | app wash: header, composer footer | translucent `--background` — light `rgba(255,255,255,.62)`, dark `rgba(30,30,32,.55)`; hairline separators |
| L2 | content: bubbles, decision card, tooltips | near-solid `--card` (elev @ α .92) / `--popover` (α .96); **all body/muted text lives here** |

Contrast policy: 4.5:1 on L2; L1 carries only ≥13px semibold (brand/status labels). One shell tweak
ships with this design: `apply_vibrancy(..., Some(13.0))` + `#root { border-radius: 13px; overflow:
hidden }` so material and CSS corners coincide.

Key slot assignments (light / dark):

| Slot | Token | Values | Meaning |
|---|---|---|---|
| `--primary` | `accent` | `#10a37f` / `#19c8a4` | **teal = brand + LOCAL + interactive** (send, focus ring, switch-on) |
| `--accent` | `accentWeak` | `#eaf6f2` / `#15302a` | hover washes, local pill fill |
| `--accent-foreground` | derived | `#0b7a5f` / `#2ad9b4` | AA-safe teal text on `accentWeak` (raw teal fails at ~2.7:1) |
| `--route-cloud` | `cloud` | `#bd6a13` / `#e0a25c` | **amber = CLOUD route accent only, never interaction** — amber on screen always means "money left the machine" |
| `--route-cloud-weak` | `cloudWeak` | `#fbf0e3` / `#332610` | cloud pill fill |
| `--card` | `elev` @ .92 | `rgba(255,255,255,.92)` / `rgba(42,42,45,.92)` | L2 surfaces |
| `--secondary` | `user` | `#f4f4f5` / `#2d2d31` | user bubble, secondary buttons |
| `--muted-foreground` | `muted` | `#6b6b78` / `#9a9aa6` | timestamps, badges |
| `--border` / `--input` | `line` / `lineStrong` | per theme.js | hairlines / control borders |
| `--ring` | `accent` | teal | focus rings |
| `--track` | `track` | `#ececed` / `#39393d` | why-bar rails |
| `--destructive` | added | `#c03d2e` / `#e5715f` | errors (theme.js has none; matched temperature) |
| radii | `radiusSm`/`radius`/`pill` | 13 / 18 / 999px | controls & bubbles / hero cards / pills |

Route accent flows through a `data-route="local|cloud"` attribute setting `--route-accent`; children
style against `var(--route-accent)`. The theme.js black `btn` token is deliberately **not**
`--primary` (that is the web demo's button); the popover's one primary action is teal.

## Component inventory

Vendored shadcn (exactly these; a 360×480 popover has no room for dialogs/sheets/menus): `button
badge card scroll-area separator switch tooltip skeleton textarea`. ScrollArea matters — native
scrollbars over vibrancy read wrong; the overlay scrollbar with a `--border` thumb reads native.

Custom components (consuming `@wayfinder/shared` helpers; file tree in WF-ROADMAP-0009):

- **DecisionPill** — glyph (fixed 1ch slot) + uppercase route + model in mono; `routeGlyph`/
  `routeLabel`/`routeKind`; crossfades on route flip, never reflows; glyph `aria-hidden` with
  sr-only "routed locally / to cloud".
- **ScoreReadout** — the hero number: `formatScore` at 22px mono `tabular-nums` + a thin `--track`
  rail whose fill = score in `--route-accent`. A rail, not a dial — at 360px a dial is ornament.
- **WhyBars** — `topContributions(d, 4)`: 11px label, share-width bar, mono value right-aligned;
  skeleton rows until the enriched decision lands.
- **DecisionCard** — the hero (L2, 18px radius): Pill + ScoreReadout, `routingBadge` sub-line
  (" · decision only", " · offline" come free), WhyBars behind a disclosure.
- **StreamingMessage** — append-only assistant bubble (no per-token animation), soft caret while
  streaming, `aria-busy`, text selectable; replaced by OnboardingCard when `decisionOnly`.
- **StatusDot** — 7px, teal ok / amber degraded / muted unreachable; `role="status"`; tooltip lists
  `missing_keys` verbatim.
- **OfflineToggle** — Switch; healthz `offline: true` renders on+disabled ("offline by config"),
  else toggles a client preference adding `X-Wayfinder-Offline: 1` per turn.
- **SavingsGlance** — "saved $0.42 today" from `/v1/savings`; hidden unless `priced && n > 0`
  (never "0 relative units").
- **Composer** — Textarea 1→4 rows, Enter sends / Shift+Enter newline, teal send; Stop aborts via
  the wire client's existing `signal`.
- **OnboardingCard** — the `decisionOnly`/first-run nudge: "Wayfinder scored this turn — connect a
  model to get replies" + copyable snippet.

## State machines (pure, table-tested, in `lib/appState.ts`)

**Gateway mode** (from `/healthz` polling + `localStorage["wf.seenGateway"]`):

| Mode | Trigger | View |
|---|---|---|
| healthy | `status:"ok"` | ChatView |
| degraded | `missing_keys` non-empty | ChatView + amber banner (env names verbatim) |
| decision-only | per-response `decision_only`/`dry_run` | DecisionCard full; OnboardingCard replaces reply |
| offline | healthz `offline:true` or local toggle | quiet "offline — routing to cheapest tier" chip |
| unreachable | healthz rejects, `seenGateway` set | embedded `scorer.js` preview, unmissably framed "local mirror — start the gateway" |
| first-run | healthz rejects, `seenGateway` unset | brand hero + install-service CTA + live scorer demo |

**Turn machine** (owned by `useTurn`): idle → streaming (headers decision paints early via
`decisionFromHeaders` + cheapest-model cache) → done (trailing `event: wayfinder` enriches — zero
layout shift) → error (decision persists even when the reply fails: **the decision is the product**).

## Motion

One easing — `cubic-bezier(0.2, 0, 0, 1)` — durations 80/160/240/400ms as CSS vars, zeroed by
`prefers-reduced-motion` centrally. Popover show: opacity + 4px rise, 160ms, re-triggered on window
focus (the window hides, never unmounts). Route flip: 180ms crossfade, fixed glyph slot. Score: no
digit tweening — 120ms dip-and-swap; rail width 240ms. WhyBars: width-in with 30ms stagger, first
reveal only. Streaming text: none per token (jank at 30+ tok/s); 1s caret pulse. Banners animate
height above the scroll region — never push the composer mid-typing.

## Typography & density

theme.js stacks verbatim: `ui-sans-serif/-apple-system/"SF Pro Text"…` for prose; `ui-monospace/
"SF Mono"…` for scores, model names, env names, commands — anything machine-true. Four sizes only:
22/600 mono tabular (score) · 15/590 (titles) · 13/400 lh 1.45 (body) · 11/500 uppercase tracked
(labels). 4px grid; 14px window padding; 44px header; composer min 52px; hairlines everywhere —
vibrancy + hairlines, no heavy borders.

## Accessibility

Composer autofocus on every window show; DOM-order focus; 2px teal ring offset 2. Esc closes the
why-disclosure first, then hides the window (mirrors hide-on-blur). Glyphs `aria-hidden` with
sr-only route text composed from `routeKind` + `formatScore`. WhyBars as `<ul aria-label="top
scoring factors">`, rows read "word count, 41% of score". One polite live-region announcement on
completion ("reply finished, routed locally") — never a live region on the token stream.

## Testing the contract

Vitest + jsdom + Testing Library. **Golden fixtures recorded from the real gateway**
(`tools/record-fixtures.mjs`, dry-run decisions local/cloud/decision-only/offline, healthz
ok/degraded/offline, a verbatim SSE transcript, savings) are the decision-render contract — they
replace the never-landed `menubar_core.py` parity idea. Table-driven component tests per fixture;
reducer tables for both machines; SSE replay through `useTurn` asserting the two `onDecision`
calls. Not tested in jsdom (manual checklist instead, `docs/desktop-fidelity.md`): vibrancy
compositing, tray, media-query flips, scroll physics, VoiceOver.

## Related

- WF-ADR-0042 (the architecture this designs the face of) · WF-ADR-0020 (palette + decision-first
  hierarchy inherited) · WF-ADR-0039 (the offline mode the toggle surfaces) · WF-ROADMAP-0009
  (delivery phases and budgets)
