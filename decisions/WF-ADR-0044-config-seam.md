---
schema_version: 1
id: WF-ADR-0044
type: decision
tags: [config, gateway, cli, desktop, keys, keychain, seam, onboarding]
---

# WF-ADR-0044: The Config Seam — the Gateway CLI Is the Only Config Author

## Status

Accepted

> Drafted with WF-ROADMAP-0009 Phase 4 (desktop onboarding & keys), whose first-run flow is the
> seam's first consumer. Originally Proposed pending the first real `config set` need — which
> arrived immediately: the desktop's offline toggle had to become **global** (the popover
> header's switch must mean machine-wide, not this-app's-chat-turns), and the only honest way
> to flip `[gateway] offline` for every client is through the seam. `wayfinder-router config
> set gateway.offline true|false [--path]` shipped as designed in §3: whitelisted keys only,
> line-preserving edits (`config.set_toml_bool` — every untouched line survives byte-for-byte),
> and the edited text must re-parse through the real config parsers before anything is written.
> A running gateway hot-reloads the change on its next request; no restart. The whitelist grows
> key by key — this is still not a general TOML editor.

## Category

Technical

## Context

The desktop app (WF-ADR-0042) opened an honest question the moment its Settings window gained a
"Gateway" section: should the app edit gateway configuration too, so people don't have to write
TOML? The temptation is real — onboarding currently ends at a text editor — but an app-side
config editor has three structural problems:

1. **Two writers, one file.** The gateway's config is a hand-editable file the gateway
   hot-reloads (mtime-based). An app that also writes it clobbers comments and formatting and
   races concurrent edits.
2. **Validation lives in Python.** The config schema and its semantics (tier ordering,
   `api_key_env`/`api_key_cmd` coupling, cost validation) are the gateway's. A GUI editor either
   duplicates that knowledge in the client — the drift problem the JS scorer needed a blocking
   parity gate to make safe (WF-ADR-0042 §2) — or writes blind and lets users discover errors at
   reload.
3. **It is the named failure mode.** WF-ADR-0042 exists to stop the app from growing into a
   second source of truth: "everything in it renders decisions; nothing in it makes them."

Meanwhile the actual onboarding pain has a narrower shape: scaffold a starter config, and get a
provider key somewhere safe that the gateway can read — without the user hand-editing anything
and without the key ever entering the app's state or any file (WF-ADR-0004).

## Decision

**The gateway CLI is the only author of gateway configuration.** Clients that want to create or
mutate config do it by shelling out to fixed, whitelisted gateway verbs — never by parsing or
writing TOML themselves.

1. **Creation: `init --preset … --keychain --path …`.** The desktop first-run scaffolds config
   by invoking the same `init` a human would, pointed at the app/service's shared well-known
   path (`~/Library/Application Support/Wayfinder/wayfinder-router.toml`, loaded via
   `service install --config` / `WAYFINDER_CONFIG`). The new `--keychain` flag makes the
   generated config's keyed models read their keys from the macOS Keychain via
   `api_key_cmd = "/usr/bin/security find-generic-password -s wayfinder-router -a <ENV_VAR> -w"`.
   The app writes zero TOML; the reference lands in config because the *gateway* put it there.

2. **Key material never crosses argv.** The desktop stores keys with `/usr/bin/security -i`,
   feeding the one-line `add-generic-password -U -s wayfinder-router -a <ENV_VAR>
   -T /usr/bin/security -w "<key>"` command over **stdin** — argv is `ps`-visible, stdin is not.
   `-T /usr/bin/security` is load-bearing: the gateway reads the item back through
   `/usr/bin/security` from a headless launchd context, and without that ACL entry every gateway
   restart would raise a Keychain consent dialog nobody can see. (This is also why a native
   SecItemAdd binding is the wrong tool: items it creates trust the calling app, not
   `/usr/bin/security`.) Inputs are strictly validated first — env-var names against
   `^[A-Z][A-Z0-9_]{0,63}$`, keys printable-ASCII, control-character-free, length-capped — so
   nothing quote-hostile reaches the `security -i` tokenizer.

3. **Mutation (future): a `config set` verb family, not an app editor.** When an editable knob
   genuinely needs a GUI (offline default, budget cap, threshold), the gateway grows
   `wayfinder-router config set <key> <value>` — validating against the real schema, editing
   format-preservingly, exiting non-zero with a human-readable reason on invalid input — and the
   app renders a form that shells the verb. Same pattern as `service install`: the CLI works
   while the gateway is down, which is exactly when onboarding-time config matters. A gateway
   HTTP mutation surface remains possible later but is not needed for v1 and would add attack
   surface to a currently read-only API.

4. **Operational caveat, accepted and documented: keys resolve at startup only.**
   `bootstrap.resolve_keys` runs `api_key_cmd` once at gateway startup; hot-reload re-reads
   config but does not re-run it. So key changes ride a service restart — the desktop's existing
   `launchctl kickstart -k` action — which the app performs automatically after storing a key.
   Re-resolving keys on hot-reload is listed as future work, not assumed.

## Consequences

### Positive

- Onboarding stops at a click instead of a text editor, and the thin-client invariant survives:
  the app still cannot author config or hold a key.
- One validator (the gateway), one writer per file-lifetime moment (the CLI verb the user or app
  invoked), no schema knowledge duplicated into TypeScript or Rust.
- The seam composes: every future knob is a CLI verb first, which also serves headless and
  scripted users, not just the desktop.

### Negative

- Shelling out means parsing exit codes/stderr for UX, which is clunkier than a typed API.
- The startup-only key resolution forces a restart into the add-key flow (brief, automated, but
  real).

### Risks

- **`security -i` quoting** is verified by unit tests over the pure script builder, but the CI
  gate is Linux; the real-Mac smoke test is a required pre-release step (WF-ROADMAP-0009
  Phase 5).
- **Whitelist discipline** is the seam's security boundary: presets, env-var names, and shortcut
  ids are validated in Rust before any process spawn. A new caller bypassing the whitelists
  would reopen the arbitrary-shell surface WF-ADR-0042 §4 closed.

## Alternatives Considered

### The app edits TOML directly (format-preserving, e.g. toml_edit)

Solves comment-clobbering but not two-writers or schema drift; permanently couples the Rust
client to the Python schema. Rejected — it is the exact slope WF-ADR-0042 guards.

### A gateway HTTP config-mutation API now

One honest owner, typed errors — but it adds a write surface to a loopback API that is currently
read-only (plus auth questions WF-ADR-0035's virtual keys only partially answer), and it cannot
serve the highest-value moment (first-run, when the gateway may not be running yet). Deferred,
not rejected forever.

### Keys in a Tauri-managed store / SecItemAdd

Breaks WF-ADR-0004's "the gateway reads keys from the environment/secret store it already
trusts" and the `-T /usr/bin/security` ACL problem above; the key would be readable by the app
forever after. Rejected.

## Related

- WF-ADR-0042 (the thin-client constitution this seam preserves) · WF-ADR-0004 (key custody) ·
  WF-ADR-0038 (the service the config rides) · WF-ROADMAP-0009 Phase 4 (first consumer) ·
  WF-DESIGN-0015 (the onboarding/keys surface built on this seam)
