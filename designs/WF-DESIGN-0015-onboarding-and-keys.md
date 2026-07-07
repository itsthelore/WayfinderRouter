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
   Before calling it, the app probes `init --help` for the literal text `--keychain` — a
   capability check, not a version check (there is no released version number that reliably
   means "has the flag": it postdates the currently published tag, and the unreleased worktree
   that added it hadn't bumped `__version__` either). An installed CLI predating the flag fails
   fast with a plain "update with `pip install --upgrade wayfinder-router`" message instead of
   argparse's raw `unrecognized arguments: --keychain` surfacing straight into the UI.
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

## Amendment: Add Provider or Model (Settings → Keys)

A second entry point beside the keyed-model list: a "+ Add Provider or Model" button reveals a
form for registering an endpoint that isn't in config yet at all — the seam's `config add-model`
verb (WF-ADR-0044 amendment), not `config set`. Deliberately open-ended, not a fixed provider
enum ("anything OpenAI-compatible," per the maintainer): five quick-picks (Anthropic, OpenAI,
Google Gemini, Ollama, LM Studio) prefill base URL and, for the cloud three, the conventional
`$ENV_VAR` name; **Custom** starts blank for everything else (a HuggingFace-hosted endpoint, a
personal proxy, anything). No provider icons — names only, per the maintainer's steer.

The **name field is a freely-editable slug**, not tied to the provider identity — picking
Anthropic twice with different names (e.g. `anthropic`, `anthropic-fast`) and different Model
IDs registers two independent `[gateway.models.*]` entries, since the same provider can back
several models a user wants to compare or fall back between. Client-side validation mirrors the
CLI's own name pattern (`^[a-z][a-z0-9_-]{0,63}$`) so a bad name fails fast in the form rather
than round-tripping to the CLI first; the real validation (schema, collision, re-parse) still
happens in the gateway CLI per the seam's rule of one validator.

Local runners get a second assist: a narrow Rust-side loopback probe (`detect_local_providers`,
checking 127.0.0.1:11434 and :1234 — Ollama and LM Studio's default ports) runs when the form
opens and marks whichever quick-pick is actually live with a "•" and a caption, so the common
case (Ollama already running) needs no typing at all beyond the Model field. This runs in Rust,
not the webview, so the CSP's `connect-src` stays untouched (WF-ADR-0042).

Add shells `add_model` (base_url, model, and — only if the field is non-empty — an
`api_key_env`, which also implies `--keychain` on the CLI side); success closes the form and
refreshes the Keys list the same way Save/Remove already do. The new entry appears as an
ordinary `KeyRow` if it's keyed — no separate code path for entering its key, same
Keychain-via-stdin flow as every other model. **Registering an endpoint is not the same as
routing to it**: `config add-model` never touches `[[routing.tiers]]`, so a freshly added model
is keyable and usable by direct name or as a same-tier fallback, but won't receive
auto-routed traffic until a human places it in a tier by hand — the form doesn't claim otherwise.

## Privacy (Settings → Privacy — the verify-lite panel)

Static Form rows stating exactly what WF-ADR-0042 §8 allows, nothing more: the decision is
computed on-device (deterministic, offline, keyless); prompts go only to the provider you route
to, under your own keys, from the local gateway; **offline mode is the only nothing-leaves
guarantee** (its one-click toggle is the header switch, below); no telemetry, ever. The banned
overclaim
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

## Amendment: the offline switch is global (header, not action list)

The popover's per-app "Offline" action row is retired. Offline is a machine-wide mode — every
client of the gateway routes local while it's on — so the desktop app flips the config itself
through the seam's first mutation verb, `wayfinder-router config set gateway.offline
true|false --path <the app's config>` (WF-ADR-0044), and hot-reload applies it gateway-wide.
The control is a small switch in the popover header, on the status row beside the health label
it changes: flip → shell the verb → poll `/healthz` → the `offline` field confirms and the
label reads "Offline". The switch is disabled while that round-trip is pending, and its checked
state always renders healthz truth, never optimistic local state — an edit made in the TOML by
hand shows up on the next poll exactly the same way. The old per-turn
`X-Wayfinder-Offline` header path in the client is deleted along with the row.

## Amendment: Settings is a five-tab window; Keys becomes Providers

A supplied product mockup reworked Settings into a horizontal icon-tab strip — **General /
Providers / Display / Advanced / About** — replacing the four-item vertical sidebar this doc
first described. The tabs map as follows, and this amendment supersedes the "Keys" and "Privacy"
section descriptions above:

- **General** — unchanged: cadence, notifications, launch-at-login, the shortcut rebind.
- **Providers** — the old Keys section, rebuilt as a **master-detail pane** and absorbing the
  key experience entirely. The left list has one row per configured model (not just keyed ones —
  a keyless local model still appears) with a health dot (teal enabled+keyed / amber key-missing
  / grey disabled) and its share of the last 7 days' routing (`/v1/savings?period=7d`
  `by_route`). The right detail pane, for the selected model, shows read-only endpoint / model
  id / context window / route eligibility, and edits — all through the config seam
  (WF-ADR-0044), hot-reloaded with no restart: an **Enabled** switch (delivery-time only,
  WF-ADR-0001 — a disabled model is skipped at request time, never removed from the scored
  decision), a **Fallback** picker (same-tier failover, WF-ADR-0031; the list excludes the model
  itself), and a **routing-threshold** slider shown only for escalation tiers. The base tier's
  boundary is structural (its `min_score` is `0.0` and the gateway always rejects moving it), so
  it reads "Base tier" with no slider — more honest than a control that can only fail. The
  model's Keychain key row folds in unchanged. A **Test Connection** button probes the endpoint
  read-only (WF-ADR-0042 §3 exception, Rust-side because arbitrary hosts aren't in the webview
  CSP) and reports the result inline. The `+ Add Provider or Model` flow is the list's add
  affordance; there is no remove (no `config remove-model` verb exists — we don't fake one).
- **Display** — new: a "show savings in the menu bar" switch (a `trayShowSavings` setting,
  default on; when off the tray carries only the health/meter shape, no dollar figure) and an
  informational row stating appearance follows the system theme (`prefers-color-scheme` only,
  WF-DESIGN-0012 — rendered as fact, not a toggle).
- **Advanced** — the former Gateway section verbatim: endpoint, open config / dashboard / logs.
- **About** — new: the wordmark, the app version (`@tauri-apps/api/app` `getVersion()`, "—"
  outside Tauri), and the verify-lite privacy claims moved here verbatim from the old Privacy
  section (the banned-overclaim test still asserts "your data never leaves your machine" never
  appears).

Deep-link compatibility: the legacy section ids `keys` / `gateway` / `privacy` remap to
`providers` / `advanced` / `about` in both the webview (`initialSection`) and the Rust
`open_settings` whitelist, so an old deep-link from a not-yet-reloaded popover still lands.

## Verification & known limits

jsdom coverage: preset pick → scaffold arg, busy/error, Add-key row visibility + deep-link,
Providers master-detail (lists every model, base-tier-no-slider vs escalation-tier-slider,
enable switch → `setModelEnabled`, fallback select excludes self, key save/remove/unreachable,
seam-rejection surfacing, **Test Connection** success + transport-error inline, legacy
`keys`→Providers deep-link), Add Provider or Model (preset fill, freeform name reuse,
custom/keyless, client-side name validation, seam-rejection surfacing, detected-local-runner
badge, Cancel), the Display "show savings" toggle, About claims (and the banned overclaim's
absence), shortcut persist/rollback, plus Rust unit tables for the keychain script builder
(escaping + rejection), the shortcut whitelist, the `is_http_url` probe guard, and the install
argv. **Not coverable in CI (Linux): the real `/usr/bin/security -i`
round-trip and the launchd uninstall→install flow — a real-Mac smoke test is a required
pre-release step (WF-ROADMAP-0009 Phase 5).**

## Related

- WF-ADR-0044 (the config seam all of this rides) · WF-ADR-0042 §5/§8 (key custody promise,
  privacy story) · WF-ADR-0004 (keys never in files/apps) · WF-DESIGN-0014 (the popover this
  extends; its "Later" list shrinks accordingly) · WF-ROADMAP-0009 Phase 4 (the plan item)
