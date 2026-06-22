---
schema_version: 1
id: WF-DESIGN-0006
type: design
tags: [keys, security, onboarding, gateway, ux, secrets, cli]
---

# WF-DESIGN-0006: A Friendlier, Safer Key Experience (resolve from your keychain; plain-English status)

## Status

Proposed

> Borrow the *spirit* of OpenRouter's "save it to 1Password" tip, mirrored for a self-hosted
> tool: let Wayfinder **resolve** your API key from your OS keychain or a password manager at
> launch — so you never paste a raw `sk-…` into a shell or a plaintext file — and make first-run
> setup and status speak plain English. It **strengthens**, never weakens, the existing
> "keys are never stored, never in config" stance (WF-ADR-0001 / WF-ADR-0025).

## Context

Wayfinder is deliberately strict with secrets: a model config names only the *env-var name*
(`api_key_env = "ANTHROPIC_API_KEY"`); the gateway reads `os.environ[...]` at request time
(`gateway.py:786,842`) and the value is never written to config or disk. WF-ADR-0025 went
further and **refused to add key entry to the UI**, on purpose, adding only a *read-only*
status surface (`/healthz` `missing_keys`, a `key_ok` flag — `gateway.py:949-1056`).

Two gaps remain, both felt hardest by a brand-new user at first run:

- **Getting the key in is on the user.** The path today is `export ANTHROPIC_API_KEY=sk-…`,
  which tends to leave the raw secret in shell history and plaintext dotfiles (`README.md:158`)
  — the opposite of "somewhere safe."
- **Readiness is only legible to experts.** The single signal that a key is wired is a bare
  `missing_keys` list you have to `curl /healthz` to see, or a `key_ok` boolean in a JSON
  surface. A first-timer has no plain-English "are my keys ok?" answer.

OpenRouter's tip is a *browser* feature (their site issues a key; 1Password's extension offers
to save it). Wayfinder doesn't issue keys, so the exact moment doesn't translate — but its
mirror image does: instead of *saving* a key into a manager, **load** it *out* of the manager
the user already trusts.

## User Need

A first-time, non-expert user wants to (a) get their key into Wayfinder **without pasting a raw
secret into a plaintext shell file**, and (b) be told, in plain English, whether their keys are
wired up and exactly how to fix it if not. An experienced user who already keeps secrets in
1Password / the OS keychain wants Wayfinder to just read from there.

## Design

Two additive pieces, both holding the invariant that **Wayfinder never writes the secret**.

### 1. Resolve a key from any secret store (the resolver)

Add an optional, opt-in companion to `api_key_env`: an `api_key_cmd` — a command Wayfinder runs
at startup whose **stdout is the key**, loaded into the process environment for that variable,
in memory only, for the process lifetime. Because it is just a command, it works with every
secret store the user already has, with zero new dependencies:

```toml
[[gateway.models]]
name = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"          # still the source of truth
api_key_cmd = "op read op://Private/Anthropic/key"   # …or how to fetch it
```

- 1Password CLI — `op read "op://Private/Anthropic/key"`
- macOS Keychain — `security find-generic-password -w -s wayfinder-anthropic`
- Linux libsecret — `secret-tool lookup service anthropic`
- `pass`, `gopass`, `vault read …`, etc.

Precedence: an env var **already set** wins (CI, `export`); otherwise resolve via `api_key_cmd`.
Failures are surfaced in plain words ("couldn't resolve key for *anthropic*: command exited
non-zero"). The value is **never** written to config, disk, or logs (masked in all output).
Implemented with stdlib `subprocess` only — no vendor SDKs.

### 2. Friendlier setup + plain-English status (the doctor)

- **Setup wizard** (`bootstrap.py`): when it asks about a key, it *detects* what's on the
  machine (is `op` on `PATH`? is there an OS keychain?) and offers the matching `api_key_cmd`
  line plus a one-line "store your key like this" hint — instead of only asking for an env-var
  name and leaving the user to `export`.
- **`wayfinder-router doctor`**: prints, per model, whether the key resolves, in plain English —
  `Anthropic (ANTHROPIC_API_KEY): found ✓` / `missing ✗ — set it with `export …`, or store it
  in 1Password and add `api_key_cmd = …``. This is a human-readable front-end to the data the
  status surface already exposes (`missing_keys` / `key_ok`) — **no new security surface**, just
  legible.

## Constraints

- **Stays inside WF-ADR-0025 / 0001 / 0004**: Wayfinder *resolves/reads* keys, never *stores*
  them; nothing secret is written to config or disk; `doctor` is read-only status (no key-entry
  field), exactly the line WF-ADR-0025 drew.
- `api_key_cmd` runs a **user-specified** command with the user's own privileges — the wizard
  never invents or runs one without explicit confirmation; the trust model is documented.
- **Stdlib only** (`subprocess`); no vendor SDKs, no new base-wheel dependency. Rides the
  existing optional `[gateway]` extra.
- Resolved keys live in the process env for its lifetime only and are masked everywhere.

## Rationale

It delivers the screenshot's *intent* — "keep your key somewhere safe, don't lose it, don't
leave it lying around" — in the shape that fits a self-hosted CLI: read from the store you
already trust. It **reduces** plaintext secrets in dotfiles and shell history while being a
genuine first-run quality-of-life win. A command-based resolver is maximally compatible and
dependency-free, the same "vendor-the-file" minimalism the rest of the project follows.

## Alternatives

- **Bundle a native 1Password / per-vendor SDK** — rejected: vendor lock-in, new dependencies,
  and it helps only that one tool; a generic command covers every store with stdlib.
- **Let users type the key into the UI or store it in config** — rejected explicitly: that is
  the line WF-ADR-0025 drew; this design deliberately stays on the safe side of it.
- **A Wayfinder-managed encrypted keystore on disk** — rejected: then Wayfinder *is* storing
  secrets (master password, rotation, recovery) — scope and risk we don't want; defer to the
  user's existing manager.
- **Do nothing** — leaves the plaintext-`export` friction and the curl-only status.

## Accessibility

`doctor` output uses **words, not just glyphs or colour** ("found" / "missing", with the fix
spelled out), so it reads correctly in no-colour terminals and screen readers; the ✓/✗ are
decorative, never the only signal.

## Open Questions

- Command name and shape: `doctor` vs `keys` vs folding into the existing status surface.
- Should the wizard *offer to store* the key (run `op item create` / `security add-generic-
  password`)? Convenient, but it edges toward Wayfinder touching the secret — lean: print the
  exact command for the user to run, never handle the secret ourselves.
- Cache a resolved key for the session vs re-resolve per request (latency vs rotation freshness).
- Behaviour when the store prompts interactively at startup (e.g. 1Password biometric unlock) —
  timeout and a clear message.
- The `api_key_cmd` execution is security-relevant; when built it should graduate to an **ADR**
  amending WF-ADR-0025 ("resolve from external stores, still never store").

## Success Measures

- A user can go from "fresh clone + key already in 1Password" to a working gateway **without
  ever putting the raw key in a shell file** — documented end to end.
- `doctor` tells a misconfigured user exactly what is wrong and the one command that fixes it,
  with no need to `curl /healthz`.
- A test asserts the secret never appears in config, logs, or on disk and is masked in output.

## Related

WF-ADR-0001 (keys never in config), WF-ADR-0004 (gateway / invocation), WF-ADR-0025 (read-only
key status, no key entry — this extends it), WF-ADR-0006 (feedback & onboarding),
`bootstrap.py` (the setup wizard), WF-ROADMAP-0005 (a natural companion to the gateway-hardening
initiative).
