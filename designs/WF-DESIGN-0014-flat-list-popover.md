---
schema_version: 1
id: WF-DESIGN-0014
type: design
tags: [desktop, macos, popover, tray, settings, menu-bar, codexbar, flat-list]
---

# WF-DESIGN-0014: The Popover Is a Flat Native Menu, Not a Card Grid

## Status

Accepted

> Supersedes the layout portions of WF-DESIGN-0012 and all of WF-DESIGN-0013: both described
> (and WF-DESIGN-0013 built) an 18px-radius card/tile grid. That shape was never CodexBar's own
> — it was this project's invention *inspired by* CodexBar's concepts (a data-bearing tray icon,
> dense tiles, cadence presets) rather than a mirror of CodexBar's actual DOM. This document
> corrects that: it mirrors [CodexBar](https://github.com/steipete/CodexBar) (MIT licensed) row
> for row, verified against its real screenshots (cited throughout), and re-composes Wayfinder's
> existing data (unchanged: `lib/appState.ts`, the hooks, `lib/gateway.ts`/`ipc.ts`) into that
> shape. WF-DESIGN-0012's tokens, motion primitives, and state machines remain authoritative
> except where this document explicitly overrides them (typography and the hero-card component
> inventory, both below). Credit: CodexBar's layout is mirrored deliberately and disclosedly,
> per its MIT license — no CodexBar code is vendored, only its arrangement.

## Context

The maintainer's brief was specific: build the UI CodexBar actually has, with Wayfinder's colors
dropped in — not a new UI "inspired by" CodexBar's ideas. The prior pass (WF-DESIGN-0013)
mis-read that brief: it kept the original card-grid instinct (`Card` tiles, 18px-radius hero,
segmented `Glance | Chat` tab pills) and only borrowed CodexBar's *concepts* (a meter tray icon,
density, cadence presets). This document was written after actually fetching and viewing
CodexBar's screenshots (`docs/codexbar.png`, `docs/screenshots/current-merged-menu-redacted.png`,
`docs/screenshots/clawrouter-usage.png`, `docs/screenshots/clawrouter-settings.png`,
`docs/screenshots/cost-chart-yaxis-labels.png` in the CodexBar repo) and reading its README
rather than guessing from the name. Two things fell out of actually looking:

1. **It is a flat native menu list, not a grid.** No cards, no tiles, no rounded containers
   floating in padding. Top to bottom: a detail header, repeated metric sections (bold label →
   thin progress bar with a knob at the fill point → left/right value line → an optional gray
   insight line), hairline dividers between every section, plain icon+label action rows, and a
   footer menu in exact NSMenu style (icon, label, right-aligned ⌘-shortcut). Colour appears only
   in bar fills and the tab-strip's active state; every label, value, and insight line is neutral
   gray/black text on the vibrancy material.
2. **The multi-provider tab-strip in the main screenshot does not apply to Wayfinder.** That
   strip (icon+label pills, one per coding-tool provider: Codex/Claude/Cursor/Droid/Gemini/
   Copilot) exists because CodexBar aggregates several independent tools. CodexBar's own
   **ClawRouter** provider — a router that sits in front of several LLMs and tracks a budget,
   the closest existing thing to what Wayfinder *is* — has no tab-strip in its own popover
   (`clawrouter-usage.png`): it opens directly on a header and its metric sections, because it
   is one entity, not an aggregator. Wayfinder is a ClawRouter, not a CodexBar shell. The popover
   in this document follows the ClawRouter screenshot as the near-literal template, per the task
   brief, not the multi-provider outer chrome.

A second open question — whether the tray icon should show a literal numeric percentage instead
of the row-splice fill meter WF-DESIGN-0013 built — was resolved with the maintainer before any
tray code was touched. Reading CodexBar's own README ("the menu bar icon is a tiny usage
meter... dynamic bar icons") confirmed the icon is itself a bar-fill meter, not digit text; the
settings row that prompted the question ("choose which window drives the menu bar percent")
exists because CodexBar tracks several independent quotas and needs a picker. Wayfinder has one
natural fill metric (local-routing share) and no second one worth exposing a picker for yet, so
the decision was: **keep the fill-meter mechanism from WF-DESIGN-0013 (`src-tauri/src/{commands,
tray}.rs`, `lib/meter.ts`), drop nothing there, and do not add a metric picker.** That code was
already correct in kind; only the popover DOM around it was wrong.

**Amendment: the tray shape is now a signpost, not a W.** The maintainer asked for a shape in
that spirit (a two-arm signpost). Its point data is traced from lucide-react's own
"signpost-big" icon (ISC licensed; lucide is already this app's icon library — HelpTip, the
send button, etc.) rather than hand-drawn, so the proportions are a real icon's, not an
approximation. A first hand-drawn cut, and a first pass at porting it, both rendered "running"
as a formless blob — a uniformly thick stroke over the whole shape merges the two signs into
one mass at 22px. The fix: the post and ground stay plain strokes at two widths (thick for
running/degraded, thin for the hollow stopped state — the same trick the old W used), but the
two signs are filled *polygons*, so they stay legible as distinct pennants at every state.
`meter_image`'s row-splice needed the post/ground to vary too, not just the signs — it needs
every row band to differ between the running and stopped source images, since the
local-routing-share fill can land anywhere. Every semantic this section already covers is
untouched: three health states, never colour (macOS tints the template), the fill-meter
row-splice, no percentage picker.

**Amendment: degraded is one solid sign, one hollow — not a notch.** The first cut chipped a
small triangular wedge from a sign's tip to mark "degraded"; at 22px it read as noise, not
damage — too subtle to register at a glance. Degraded now renders one sign fully solid and the
other as a thin outline (post/ground stay at the running weight, since the gateway is still
up): "half up, half down" reads instantly, where the notch didn't.

## What ports unchanged

Everything data/logic-shaped, because none of it renders anything — WF-ADR-0001 (the client never
scores) and WF-ADR-0042 (rendered, not computed) apply regardless of DOM shape:

- `hooks/*`, `lib/appState.ts` (both state machines), `lib/gateway.ts`, `lib/ipc.ts`,
  `lib/settings.ts`, `lib/scorerPreview.ts`, `lib/meter.ts`.
- The recorded golden fixtures and `tools/record-fixtures.mjs` — the decision-render contract.
- `src-tauri/src/{commands,service,tray}.rs` — the tray meter, the service-first lifecycle, the
  fixed open-target/notify commands. `lib.rs` gains one addition (below): the Settings window.
- The nine vendored shadcn primitives and the token/motion contract in `globals.css`
  (WF-DESIGN-0012's "Tokens → slots" and "Motion" sections stand as written).

## What this document changes

### Typography (amends WF-DESIGN-0012)

CodexBar's list has no giant hero numeral anywhere — every number (`$1,515`, `2% used`, `100%
left`) is plain inline text, sized like the rest of its row. The 22px mono tabular "hero score"
and the rounded-hero (18px) card are retired. A first pass at the new scale under-shot the
reference badly — 13px labels, a 4px rail with no knob, and a nearly-invisible track all read as
a compact sidebar next to CodexBar's own generously-sized, high-contrast list. That was caught by
rendering both side by side (screenshots, not just reading the fixture data) and corrected. The
scale that shipped, all on the body/mono stacks already in `globals.css`:

| Use | Size/weight |
|---|---|
| Header name ("Wayfinder" / "Chat") | 19px, 700 |
| Section labels (Routing/Saved), action-row labels, footer labels, the decision route+model line | 16px, 700 (metric/decision labels) or 400 (action/footer row text) — normal case, not uppercase-tracked; CodexBar's "Session"/"Weekly"/"Routing" labels are plain sentence case |
| Body values (bar left/right values, Saved's Cost-style lines, header subtext/health) | 14px, 400 |
| Muted secondary (insight lines) / footer shortcuts | 13px, 400, muted |
| Numerals inside rows (scores, dollar amounts, counts) | `font-mono tabular-nums`, inherits the row's size — never its own larger size |

**Hierarchy comes from darkness, not size** (third correction, against the maintainer's high-res
reference): the size scale is deliberately narrow — the reference's own — and emphasis is carried
by foreground-vs-muted. The rules: a metric row's **left value and Cost-style body lines are dark
foreground**; only the **right value ("Resets in …" slot), insight lines, and header subtext are
muted**. The header is two explicit rows — bold name alone on line one; subtext left + health
label right sharing line two's baseline (the reference's "Updated just now … Max" pair). Dividers
are **inset** to the content padding (`mx-5`), never full-bleed. A nonzero bar fill never renders
below a **12px pill** (`min-width`); a genuine zero stays an empty track, like the reference's
"Sonnet 0% used". Saved renders **two Cost-style lines** — `Today: …` and `Last 30 days: …` (a
second `/v1/savings?period=30d` feed) — each shown only when its period is priced with real
savings.

11px uppercase-tracked labels (WF-DESIGN-0012's fourth scale rung) survive only for the
tab-strip-free popover's total absence — there is no more segmented-control pill needing that
treatment.

**Bars, tokens, and the popover canvas** also needed correction after the same side-by-side check:
- Bars are 6px thick (was a 4px rail). An intermediate pass also copied CodexBar's slider-thumb
  knob; maintainer review removed it again and recut the bar forms entirely — see the deviation
  note below.
- `--track` and `--border` (`globals.css`) were both too pale to read against the popover's
  vibrancy tint — bars looked broken (empty) rather than "a channel with a small fill," and
  dividers didn't separate sections. Both are darkened (light: `--track` `#ececed`→`#dcdce1`,
  `--border` `#ececef`→`#e2e2e7`; dark: `--track` `#39393d`→`#48484e`, `--border` alpha
  `.08`→`.12`). `--track` is now used solely by `Bar` (WhyBars/ScoreReadout, its old consumers,
  are retired), so strengthening the token directly was safe.
- Action-row icons are `lucide-react` line icons (a new, minimal dependency — tree-shaken, only
  the ~6 icons actually imported end up in the bundle), not the unicode glyphs (`↗ ⚙ ▤`) the first
  pass used, and every row has one now, including the Offline mode toggle (`WifiOff`, replaced by
  a checkmark when on) — the first pass left it icon-less, breaking the rows' shared left edge.
- **The popover grew from 360×480 to 400×550** (WF-ADR-0042 amended). CodexBar's own popover reads
  spacious at a canvas Wayfinder's original fixed size couldn't match without cramming; widening
  it was a deliberate call (confirmed with the maintainer, since it touches an existing ADR), not
  a silent scope-creep. The height is sized to the measured full menu (~547px) so no action row
  ever renders half-clipped behind the scroll edge — a menu with a cut-off row reads as broken.
  `position_bottom_center` in `lib.rs` reads the window's live size, so only `tauri.conf.json`'s
  two numbers changed.

### Component inventory (replaces WF-DESIGN-0012's card-based list)

- **MenuHeader** — line 1: bold name (left) + health text (right, neutral: "Running" /
  "Degraded" / "Offline" / "Unreachable" — never colour, matching CodexBar's "Max" tier badge
  being plain gray). Line 2: "Updated {relative}" subtext, replaced by "Missing `{ENV_VAR}`"
  when degraded (same slot, higher-priority content — mirrors the header's single freshness-line
  convention rather than adding a second banner element).
- **MetricRow** — bold label, an *optional* bar, a left/right value line (`"Resets in …"` only
  when a real reset window exists — routing has none, so its right value is a plain count
  instead of a fabricated countdown), and an optional muted insight line. Two instances:
  **Routing** (a stacked local/cloud **SplitBar** — one 6px track, a teal segment and an amber
  segment proportional to the gateway's own counts; insight line
  `"Routed: local: {n} · cloud: {n}"`, the literal Wayfinder swap of ClawRouter's "Routed
  providers: anthropic: 2 · google-gemini: 2 · openai: 2") and **Saved** (no bar — one plain
  value line `"Today: {saved} · {pct}% vs always-cloud"`, the form CodexBar's own bar-less Cost
  section uses).

**Deviation from CodexBar's bar form (maintainer review).** CodexBar's fill-with-knob bars carry
quota semantics — "N% of a limit used, resets in T" — and Wayfinder has no quotas. Rendered in
that form the rows misread: a half-full Routing bar looked like something half-done, and a 29%
Saved bar looked like a budget being consumed. So the bars were recut by what each stat *is*:
the route split is a **composition**, so it renders as a stacked two-segment SplitBar (teal
local / amber cloud — the one place row colour is spent, per the route-accent rule); savings is
**cost-like**, so it renders as a plain text line exactly like CodexBar's own Cost section,
which is bar-less too; and the complexity score (chat sub-screen) keeps a plain fill `Bar`
because a 0..1 score genuinely is a meter. The slider-thumb knob is gone everywhere — a thumb
reads as a draggable control, and none of these are.
- **ActionRow** — icon + label, optional trailing checkmark or chevron (Chat, see below) — the
  row *shape* is CodexBar's "Add Account…" / "Usage Dashboard" grammar, but Wayfinder's set is
  deliberately **one row**: Chat (chevron — pushes a full-screen sub-view, see below). Three
  maintainer reviews shaped this: first "Open Config" was cut ("Config" and "Settings" read as
  synonyms as sibling menu entries), then Open Dashboard / Open Logs / Add key… followed for
  the same reason — every open-something and fix-something action lives inside Settings
  (Gateway and Keys sections; WF-DESIGN-0015) — and finally the Offline row moved to the
  **header switch** once offline became the gateway's global mode (flipped via
  `config set gateway.offline`, WF-ADR-0044): a machine-wide mode belongs beside the
  machine-wide status it changes, not in the action list. The degraded fix-it affordance is
  the header's missing-keys line itself, not a row.
- **FooterMenuItem** — icon, label, right-aligned real `⌘`-shortcut (wired to an actual
  `keydown` listener, not a decorative label): Refresh (`⌘R`), Settings… (`⌘,`), Quit Wayfinder
  (`⌘Q`). CodexBar's fourth footer row, "About CodexBar", is deliberately **not** built —
  Wayfinder has no About panel to open yet, and a dead menu row is worse than a shorter footer;
  it is recorded under Later.
- **Divider** — one hairline `<Separator>` between every section, exactly as the reference (no
  vertical rhythm relies on padding alone).
- **HelpTip (the help layer)** — labels and statuses stay terse; what they *mean* lives
  behind a small muted **(?)** button that opens a compact panel on click (Radix popover).
  Help appears only when explicitly asked for — hover does nothing, labels stay plain. Three
  triggers, four topics: one in the header status cluster (per-state status copy — what
  "Degraded" is and where to fix it — plus, when the switch renders, the machine-wide Offline
  line), and one beside each of the Routing and Saved labels via `MetricRow`'s `help` prop.
  Rules: **one short sentence per idea** (a first cut shipped as multi-sentence hover
  tooltips and was rejected as too verbose); copy keeps to WF-ADR-0042 §8's allowed claims
  ("nothing leaves this Mac" is said only of offline mode); a panel never carries an *action*
  (actions stay in Settings and the header link); the trigger is a real button, so the copy
  is keyboard-reachable.

`DecisionPill`, `ScoreReadout`, `WhyBars`, `DecisionCard`, `FrostedHeader`, `GlanceView` are
retired as named components; their *data* (route/score/why/health/savings) is re-homed into
MenuHeader/MetricRow/ActionRow/the Chat sub-screen below. `StreamingMessage`, `Composer`,
`OfflineToggle`, `OnboardingCard`, `StatusDot`, `SavingsGlance`, `LocalMirror` survive as
components but are restyled flat (no `Card` wrapper, hairlines instead of rounded borders).

### Chat has no CodexBar analogue — here is the disclosed extrapolation

CodexBar's tracked tools have no chat surface, so there is nothing to mirror for Wayfinder's
WF-ADR-0042 §1 chat requirement. Rather than resurrect a tab-strip that only makes sense for
multi-provider aggregation (see Context), Chat is reached via an **ActionRow with a trailing `›`
chevron** — the same disclosure affordance CodexBar's own "Cost" section uses in the main
screenshot to push into a detail view. Tapping it replaces the flat list with the chat surface
(composer, transcript, the decision rendered as one more MetricRow-shaped block: bold route+model
line, the score bar, why-rows as label/bar/value rows behind a disclosure — same visual grammar,
not a floating card); the header swaps its right side for a `‹` back control. This keeps
decision-first hierarchy (WF-ADR-0020): the decision is still the first thing shown, above the
reply, just typographically consistent with the rest of the popover instead of a 22px hero digit.
Unreachable/first-run keep their WF-DESIGN-0013 invariant: full-surface takeover, no header list.

**Amendment: Chat holds a session transcript.** The first cut was a single-turn probe — each
send wiped the previous turn and every request went out as a one-message conversation.
Maintainer review grew it into a session: settled turns collapse into compact **scrollback
rows** above the live turn (the prompt, dark, with a muted `›` marker; a one-line muted
routing decision — glyph, route, model; the reply, or its error line), and each send carries
the **last 8 settled turns as user/assistant history** (`historyFromTranscript`; turns without
a reply — errors, decision-only — contribute only their user line, never a fabricated answer).
The full decision hero (score bar, why rows) stays reserved for the live turn, keeping
decision-first hierarchy per turn without repeating the ornament 20 times. Boundaries: the
transcript is **in-memory only** (never persisted — quitting the app is the clear affordance),
capped at 20 turns of scrollback, and lives in the same pure turn reducer (a settled turn is
archived by the next SUBMIT — an aborted half-stream never is). The view auto-follows the
newest content (bottom sentinel, smooth scroll, instant under reduced motion). This stays a
routing-inspection surface, not a chat product: no sessions list, no editing, no persistence.

**Amendment: slash commands.** The Composer opens a small overlay menu — `SlashMenu` — the
moment its value is a single token starting with `/`, mirroring Claude's own composer rather
than inventing a new interaction: it lists commands filtered by prefix as you keep typing,
arrow keys move the highlight, Enter runs the highlighted command and clears the box, Escape
dismisses. It is a plain positioned list anchored above the textarea, not a modal — the
textarea never loses focus, and clicking an option fires on `mousedown` (before blur) for the
same reason. A space or newline after the first token exits slash-mode and the text becomes an
ordinary message, so `/notacommand` with no match sends literally. The command set is
deliberately small — `/clear` (resets the turn machine, same as if the session had never
started), `/offline` (flips the header's global switch — same handler, same
WF-ADR-0044-backed `config set gateway.offline` call, just reachable without leaving the
keyboard; absent if no toggle handler is wired), `/settings` (opens the native Settings
window) — because Chat stays a routing-inspection surface, not a general command palette:
every command here already exists as a header control or footer row, never a new capability
invented just for the composer.

### Settings is a separate native window (replaces the in-popover slide-over)

WF-DESIGN-0013's `SettingsView` slide-over is retired. Settings… now opens a real, resizable,
decorated `WebviewWindow` (`src-tauri/src/commands.rs::open_settings`, built on demand — not
declared in `tauri.conf.json` — so a closed window is simply rebuilt on the next open rather than
tracked as stale). Layout mirrors `clawrouter-settings.png`: a sidebar list on the left, a detail
pane on the right using Mac-native Form rows (bold label + gray description on the left, the
control flush right). Wayfinder's sidebar has two entries: **General** — cadence, notifications,
launch-at-login, and the (display-only, not yet rebindable) shortcut — and **Gateway** — the
loopback endpoint (read-only) and the one door to the gateway's own config file ("Open in
Finder"; the app opens it, never edits it — WF-ADR-0042/0004). The Gateway entry exists because
"Config" and "Settings" as sibling popover rows read as synonyms (maintainer review): app
preferences and router configuration *are* different things, but the UI must make that
distinction where the user is looking, not ask them to guess it from two near-identical menu
words. The sidebar is a real, data-driven list, so a third entry (Privacy, Keys) slots in
without restructuring when WF-ROADMAP-0009 Phase 4 lands them. No
provider search box (ClawRouter's search box searches *its* provider list; Wayfinder has nothing
to search yet) and no API key / Base URL rows (WF-ADR-0004's Keychain glue is still Phase 4, not
this pass) — both are recorded under Later rather than faked.

The Settings webview paints its own opaque background (`body[data-window="settings"]` in
`globals.css`) and drops the popover's 13px root radius — only the popover rides the
transparent-body-over-vibrancy treatment; a decorated window inheriting that transparent body
would render dark-mode text over the webview's default white. Every row maps to a real,
already-wired capability: cadence drives all three gateway polls (via a `storage`-event sync to
the popover window), notifications arm the transition-edge notifier, launch-at-login drives the
autostart plugin, and the shortcut row is explicitly display-only until rebinding lands.

## Amendment: Routing goes bar-only + a Today/7d/30d toggle; icons land everywhere

Maintainer review of a live render: the Routing row's permanent left/right/insight text
("50% routed locally" / "2 turns" / "Routed: local: 1 · cloud: 1") is gone — the row is now
just the label, the `help` (?), a small **Today / 7d / 30d** period toggle (`headerRight`, a new
`MetricRow` slot), and the SplitBar. The same breakdown that used to render as permanent text
now renders in a **hover tooltip** over the bar (`components/ui/tooltip.tsx`, a vendored-but-
previously-unused shadcn/Radix primitive) — a deliberate, narrow exception to this document's
"hover does nothing" rule for `HelpTip`: that rule is about the *documentation* layer (what a
stat means), not a chart-style value readout, and the accessible name was never text-only
anyway — `SplitBar`'s `role="img"` `aria-label` already carried the identical breakdown for
screen readers, so removing the visible copy loses nothing there.

The toggle is real, not decorative: each position re-points the bar at a different `/v1/savings?
period=` report's `by_route` field (`today`/`7d`/`30d` — the gateway already returns this
day-bucketed local/cloud split; the frontend just hadn't read it before). This is a genuine
calendar-day window, unlike `/router/recent`'s fixed last-N-turns count, which now only backs
the tray meter and serves as a fallback if a report hasn't loaded `by_route` yet. No charting
library was adopted for this — a single two-segment proportion doesn't warrant one; `SplitBar`
stays exactly as it was, just re-fed.

Icons landed next to every section label (`MetricRow`'s new `icon` prop: `Route` for Routing,
`PiggyBank` for Saved) and every footer action (`FooterMenuItem`'s new `icon` prop: `RefreshCw`,
`Settings`, `LogOut`) — `FooterMenuItem`'s own doc comment already claimed "icon, label" from
this document's first pass; the code just hadn't caught up until now.

Two more maintainer-review fixes bundled in: the Usage screen's scrollable content no longer
force-stretches (`flex-1`) to fill the fixed 400×640 window when the list is short — it now
hugs its own height so the footer follows directly after Saved, and any leftover space in the
fixed window collects below Quit Wayfinder instead of as a gap before Refresh. And the Chat row
(and the chat sub-screen's header) is now labelled **"Wayfinder Chat"**, not the bare "Chat".

## Amendment: the real wordmark replaces the plain-text "Wayfinder"

The two standalone brand moments — `MenuHeader`'s top line and `FirstRunView`'s hero — now
render `src/assets/wayfinder-wordmark.png` instead of bold text. The maintainer-supplied source
had its transparency flattened to a faint checkerboard pattern rather than a real alpha
channel (common when an image is exported/re-saved through a tool that doesn't preserve alpha);
it was reprocessed (near-white pixels keyed to transparent, cropped to content, downscaled to a
600px-wide retina-ready PNG, ~98KB) before landing in the repo. Every other "Wayfinder" in the
UI — "Wayfinder Chat", "Wayfinder scored this turn", "Wayfinder isn't running" — stays plain
text; those are sentences the word is part of, not the standalone brand mark.

## Amendment: adopting shadcn's newer `marker`/`item`/`button-group`/`native-select`/`message-scroller`

Stress-tested against a full Swift/CodexBar-fork rewrite (see the session's decision doc);
landed instead: keep building Wayfinder on Tauri+React, swap several hand-rolled bits onto
shadcn's own newer registry components rather than bespoke Tailwind. All vendored via
`npx shadcn add <name>` into `components/ui/`, same as `separator`/`popover`/`tooltip`/`switch`
already were.

- **`marker`** (pure radix-ui, no new deps) replaces the transcript's per-turn routing-decision
  line's ad-hoc spans, and fills a real gap: between a turn's submit and the headers-derived
  decision arriving, the chat screen used to render nothing below the scrollback — it now shows
  the prompt plus a "Routing…" marker for that (usually brief) window.
- **`item`** (radix-ui + `separator` regdep) — `FormRow` (and everything built on it: every
  Settings row, `KeyRow`, the Add Provider form) now composes
  `Item`/`ItemContent`/`ItemTitle`/`ItemDescription`/`ItemActions` instead of a hand-rolled div
  layout. Same visual result, one fewer bespoke layout primitive to maintain.
- **`button-group`** (button/separator regdeps) replaces the Routing period toggle's hand-rolled
  rounded-pill container with a real segmented control.
- **`native-select`** (no deps — a styled wrapper over a real `<select>`) replaces the two plain
  `<select>`s in Settings → General (refresh cadence, popover shortcut).
- **`message-scroller`** (the one genuinely new dependency here, `@shadcn/react` — confirmed
  with the maintainer before adding) replaces `ScrollArea` + a manual `scrollIntoView` effect in
  `ChatScreen`. Autoscroll-follow now lives in the primitive via a `scrollAnchor` on the live
  turn; it also brings a scroll-to-bottom button and a top/bottom fade
  (`scroll-fade-b` — the registry item ships the class name but not the CSS behind it, so it's
  defined in `globals.css` as a `mask-image` gradient).
- **Deliberately not adopted (yet):** the full `Sidebar` primitive for Settings' nav — it pulls
  in `Sheet` (mobile drawer), `Input`, `Skeleton`, and a `use-mobile` hook, none of which apply
  to a fixed, always-visible, 4-item desktop nav; the nav got `item`'s icon+label row treatment
  instead, not the full collapsible/responsive machinery.

One recurring gotcha worth recording: `npx shadcn add` syncs upstream copies of *shared*
registry deps too (`button.tsx` came along with both `item` and `message-scroller`), and the
upstream copies use literal `dark:` Tailwind variants — dead code here, since dark mode is
`prefers-color-scheme` only (`theme-lint.test.ts` catches this; it fired twice this pass, on
`button.tsx` and `native-select.tsx`). Every sync needs that check before landing.

## Amendment: the live decision is a prompt-analysis card; Usage gains a routing-time stat

A supplied product mockup gave the routing summary a richer grammar. It applies to the **live
turn only** — Wayfinder stores no prompt history (WF-ADR-0001/0042), so this is one card, not a
feed; settled turns still collapse to the compact scrollback marker.

`DecisionSummary` (the chat screen's live turn) becomes: the complexity score as a large numeral
in the route accent (`--primary` local / `--route-cloud` cloud) over the existing 0–1 `Bar`
(still `role="meter"`, `aria-label="complexity score"` — the meter is load-bearing for tests and
AT), a **route pill** (lucide `Monitor`/`Cloud` + "Route: Local|Cloud" on the accent-weak
background), a "Deterministic · No model call" caption (true by construction, WF-ADR-0001), and a
five-row **feature readout** — Word count, Lists, Code blocks, Structured sections, Lexical
signals — with a one-line **"Why:"** sentence. The feature rows and the why line need the
enriched debug payload (the header-only decision carries no `contributions`), so they skeleton
until it lands while the score, bar, and route paint from the headers. The prompt line gains a
copy button (webview clipboard, no Rust). Two pure, display-only helpers do the shaping in the
shared decision module — `featureRows` and `whyLine` — reading the contributions the gateway
already returned; **no scorer change, no parity impact, the client still never scores.**

The Usage screen gains a footer **stat strip** (mockup): the week's savings share beside an
**Avg routing time** — the median time to *decide* a route, read from `/router/recent`'s
`p50_decision_ms`. Both are plain text (no bar, no meter — the popover keeps its single `img`,
the route split). Sub-millisecond p50s read as "<1 ms" (a route is a table walk, not a model
call). The caption is "p50 over recent turns", **not** the mockup's "over last 7 days" — the
backend's window is the recent-ring (last ≤200 turns), and the doc renders the honest span.

## Later (recorded, not built)

A menu-bar-metric picker (only worth building if Wayfinder grows a second fill-worthy
percentage, e.g. a readable budget-used% once WF-ADR-0032's budget has a stable HTTP surface) —
its future home is the Display tab (WF-DESIGN-0015 amendment).

> Landed since this document: the Privacy/verify-lite panel, key management, and ⌥W rebinding
> shipped as WF-DESIGN-0015 on WF-ADR-0044's config seam; the About panel now lives there too, in
> the five-tab Settings restructure. The plain-text placeholder wordmark was replaced by the real
> brand wordmark (see the wordmark amendment above).

## Related

- WF-DESIGN-0012 (tokens/motion/state-machines this amends only in typography + component
  inventory) · WF-DESIGN-0013 (superseded — the card-grid "glance pivot") · WF-ADR-0042
  (architecture, unchanged) · WF-ADR-0020 (decision-first hierarchy, preserved) ·
  WF-ROADMAP-0009 (delivery)
