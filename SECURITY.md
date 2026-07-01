# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — don't open a public issue or pull request.

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/itsthelore/wayfinder-router/security/advisories/new)**
(the repo's *Security → Advisories → Report a vulnerability*). We'll acknowledge within a few days and
keep you posted as we work on a fix.

## Supported versions

Wayfinder follows CalVer and ships fixes on the latest release. Please reproduce against the most recent
version before reporting.

## Scope & design posture

A few properties are load-bearing, and reports that undermine them are especially welcome:

- **The scored decision path is offline, deterministic, and keyless** (WF-ADR-0001) — it makes no model
  call, opens no network connection, and reads no credential to route a prompt.
- **Provider keys are read from the environment at request time** and are never written to config, logs,
  or disk (WF-ADR-0004). Virtual gateway keys are stored only as SHA-256 hashes, never in plaintext.
- Prompt text is never logged or persisted by the decision or metrics paths.

Out of scope: problems that require a deployment you control to be misconfigured (for example, exposing
the gateway to an untrusted network with no auth), and vulnerabilities in the upstream providers you point
the gateway at.
