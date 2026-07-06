---
schema_version: 1
id: WF-DESIGN-0013
type: design
tags: [desktop, macos, popover, tray, meter, glance, settings, menu-bar]
---

# WF-DESIGN-0013: The Glance Pivot — Live Meter Tray, Glance Tiles, Settings

## Status

Accepted

> Amends WF-DESIGN-0012 (the popover design contract): the reachable-gateway surface becomes
> glance-first with chat one tab away, and the tray icon becomes a live meter. WF-DESIGN-0012
> remains authoritative for everything on the Chat tab — the decision hero, the no-reflow
> streaming contract, motion, and typography are unchanged. The information design here is
> inspired by [CodexBar](https://github.com/steipete/CodexBar) (MIT) — the *patterns* (a
> data-bearing menu-bar icon, dense per-source tiles, cadence presets) adopted onto Wayfinder's
> own data; no code ported.

## Context

The maintainer reviewed CodexBar and called its UX the target: the menu-bar icon itself carries
data, the popover answers everything at a glance, and the app has real preferences. Wayfinder's
ambient value maps cleanly — savings (`/v1/savings`), the local/cloud route split
(`/router/recent`), gateway health (`/healthz`) — so the popover becomes an instrument first and
a chat second, without a stack change (WF-ADR-0042 stands: Tauri v2, thin client, service-first,
never scores).

## The tray: a live meter, savings-forward

- The **title** carries the savings `$` only (unchanged; never a route).
- The running **W fills bottom-up with the local-routing share** of recent turns — the thesis
  ("watch Wayfinder keep you local") at a glance. The fill is a runtime **row-splice** of the two
  committed templates (hollow above the fill line, solid below), so the icon stays a pure
  black+alpha template the system tints; no new art per level.
- **Health outranks the meter**: degraded keeps its notched W, stopped stays hollow. A **12%
  visual floor** keeps a running-but-0% meter distinct from the hollow stopped W.
- The webview quantizes the share to **5% steps** before crossing IPC so poll noise never
  re-renders the icon. `null` share (no turns yet, no cheapest tier) renders the full solid W —
  exactly the pre-meter behaviour.

## The popover: glance-first, chat one tab away

A `Glance | Chat` segmented control (11px uppercase, teal active) under the frosted header.
**The inactive pane is hidden, never unmounted** — the composer draft and any streaming turn
survive tab flips, the same invariant as hide-on-blur. Unreachable/first-run remain full-surface
(no tabs).

**GlanceView tiles** (single column of L2 cards, 13px radius, 11px uppercase labels):

| Tile | Content | Empty state |
|---|---|---|
| routing | local/cloud share bars (route-accent colours) + counts, from `/router/recent` | "No turns yet" / skeletons while loading |
| saved | `saved $` at 22px mono tabular + `saved_pct`% vs always-frontier | "Savings show once priced turns land" (never "0 relative units") |
| gateway | status dot + endpoint + `missing_keys` verbatim in cloud-amber | — |

A **budget tile** (spend vs cap + window countdown, WF-ADR-0032) is designed but deferred: the
gateway exposes budget state only as a response header today. It lands when a readable HTTP
surface exists (its own gateway PR).

## Settings

A gear in the header opens a slide-over Settings surface (main tree stays mounted underneath):

- **Refresh cadence** presets — `auto` (15s, default; the checkpointed behaviour) / `manual` (no
  background interval; initial fetch + focus poll + event-driven refreshes stay) / `1m` / `5m` /
  `15m`. One preset drives all three feeds (healthz, savings, recent).
- **Notifications** — arms the transition-edge notifier (up↔down, ok↔degraded, keys
  appear/clear). Off by default; never fires on an unchanged poll or the token stream.
- **Launch at login** — the app's own agent via the autostart plugin; the gateway keeps its
  WF-ADR-0038 agent (docs/desktop-lifecycle.md).
- Persisted as `wf.settings.v1` in localStorage. Esc closes settings first (the dialog holds
  focus), then the window.
- This surface is Phase 4's home: the verify-lite privacy panel, ⌥W rebinding, and key
  management land as rows here.

## Later (recorded, not built)

Savings sparkline (client-side ring buffer of poll samples) · confetti on savings milestones ·
Homebrew cask + localization (Phase 5 distribution polish) · the budget tile (above).

## Related

- WF-DESIGN-0012 (the contract this amends; Chat tab unchanged) · WF-ADR-0042 (architecture,
  unchanged) · WF-ADR-0038/0039 (the service + offline mode surfaced) · WF-ADR-0032 (the budgets
  the deferred tile will surface) · WF-ROADMAP-0009 (delivery)
