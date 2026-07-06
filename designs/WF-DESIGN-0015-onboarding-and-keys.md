---
schema_version: 1
id: WF-DESIGN-0015
type: design
tags: [desktop, macos, onboarding, keys, keychain, privacy, shortcut, settings, first-run]
---

# WF-DESIGN-0015: Onboarding & Keys — First-Run Scaffold, Keychain, Privacy, Rebind

## Status

Accepted

> The interaction design for WF-ROADMAP-0009 Phase 4, built on WF-ADR-0044's config seam.
> WF-DESIGN-0014 (the flat-list popover) deferred all of this to its "Later" section; this
> document is where it lands. Depends on two gateway enablers (PR #68, cherry-picked):
> `serve/service --config` + `WAYFINDER_CONFIG`, and decision-only replies with no models.

## The one journey this designs

Install app → open popover (first-run) → pick a preset → one click → a routed gateway →
degraded header says a key is missing (the line is the link) → Settings → Keys → paste →
routed for real.
No terminal, no TOML, no key in any file — and every step is the gateway's own machinery
(WF-ADR-0044): the app never authors config and never keeps a key.

## First-run (FirstRunView)

The single install CTA becomes a **preset picker** (three radio rows: hybrid — recommended,
keyless local Ollama → Anthropic cloud; openai — two cost tiers; gemini — two cost tiers;
summaries mirror `bootstrap.PRESETS`) plus one **"Set up routing"** CTA. The click runs, in
order:

1. `wayfinder-router init --preset <p> --keychain --path
   ~/Library/Application Support/Wayfinder/wayfinder-router.toml` — an existing file is kept
   (init's "already exists" is treated as success; the app never `--force`s over user edits).
2. `wayfinder-router service uninstall` (best-effort) → `service install` — the install argv
   *always* bakes `--config <that path>` into the unit. The uninstall step is load-bearing:
   re-installing over a loaded agent leaves launchd's **old** job spec running (`bootstrap`
   fails, the legacy `load` no-ops, the probe passes), so new ProgramArguments only apply
   across an uninstall/install cycle. This also silently upgrades Phase-3-era installs whose
   units lack `--config`.

Exit is organic: the next healthz poll sees the gateway and the popover flips to the usage list
— decision-only or degraded until a key lands. The scorer demo (LocalMirror) stays on the
first-run surface: the very first thing the app shows remains a real decision, keyless.

## Keys (Settings → Keys)

Driven entirely by `/router/models` (already returns `{name, model, api_key_env, key_ok}` —
env-var *names* and booleans, never secrets; `/healthz`'s `missing_keys` are model names and
stay display-only). Per keyed model: a FormRow with the model, its `$ENV_VAR`, key present/
missing, a password input + Save, and Remove when present. Keyless models don't appear.

Mechanics (WF-ADR-0044 §2): Save hands the key to Rust once — it never persists in JS state;
the input clears on success — and Rust feeds `add-generic-password -U -s wayfinder-router -a
<ENV_VAR> -T /usr/bin/security -w "<key>"` to `/usr/bin/security -i` over **stdin** (argv is
`ps`-visible). `-T /usr/bin/security` is the ACL that lets the headless launchd gateway read
the item back without a consent dialog nobody can see. Because `resolve_keys` runs only at
gateway startup, Save/Remove finish with `launchctl kickstart -k`; the UI refetches
`/router/models` with bounded retries until `key_ok` flips rather than one hopeful refetch.

Honesty note rendered under the rows: keys live in the macOS Keychain, read through the
`api_key_cmd` reference **scaffolded configs contain** — a hand-written config without that
line won't see them (the note says so and points at Gateway → Open in Finder).

The popover's degraded fix-it affordance is the **header's missing-keys line itself** —
"Missing cloud — add key…" renders as a click target deep-linking Settings → Keys via
`open_settings`'s whitelisted `section` param. It was briefly an "Add key…" action row;
maintainer review removed it (with the Open Dashboard / Open Logs rows, which moved into
Settings → Gateway) so the popover's action list stays behavior-only and never re-grows a
scattered menu next to its one "Settings…" door. The status that names the problem carries the
click. The deep-link applies on window *creation* only — an already-open Settings window is
focused, not re-routed (accepted limitation).

## Privacy (Settings → Privacy — the verify-lite panel)

Static Form rows stating exactly what WF-ADR-0042 §8 allows, nothing more: the decision is
computed on-device (deterministic, offline, keyless); prompts go only to the provider you route
to, under your own keys, from the local gateway; **offline mode is the only nothing-leaves
guarantee** (and where its one-click toggle lives); no telemetry, ever. The banned overclaim
("your data never leaves your machine") is asserted absent in tests, not just avoided.

## Shortcut rebind (Settings → General)

The static ⌥W row becomes a select over a fixed whitelist — ⌥W (default), ⌥⇧W, ⌃⌥W, ⌘⇧W.
**No ⌥Space**: it collides with common launchers (the roadmap's own note). Rust is stateless
and validates ids against the same whitelist (`shortcut_for`), doing unregister-all →
re-register with the one shared toggle handler; a registration failure (combo claimed
elsewhere) propagates and the select rolls back, showing the reason. The persisted settings
blob is the source of truth: the popover re-applies `settings.shortcut` on mount and on every
cross-window `storage` change. The roadmap's side-quest is also resolved: the old lib.rs
warning claiming the hotkey needs an Accessibility grant was false (RegisterEventHotKey needs
none) and is reworded.

## Verification & known limits

jsdom coverage: preset pick → scaffold arg, busy/error, Add-key row visibility + deep-link,
Keys list/save/remove/unreachable, Privacy claims (and the banned overclaim's absence),
shortcut persist/rollback, plus Rust unit tables for the keychain script builder (escaping +
rejection), the shortcut whitelist, and the install argv. **Not coverable in CI (Linux): the
real `/usr/bin/security -i` round-trip and the launchd uninstall→install flow — a real-Mac
smoke test is a required pre-release step (WF-ROADMAP-0009 Phase 5).**

## Related

- WF-ADR-0044 (the config seam all of this rides) · WF-ADR-0042 §5/§8 (key custody promise,
  privacy story) · WF-ADR-0004 (keys never in files/apps) · WF-DESIGN-0014 (the popover this
  extends; its "Later" list shrinks accordingly) · WF-ROADMAP-0009 Phase 4 (the plan item)
