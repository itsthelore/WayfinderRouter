---
schema_version: 1
id: WF-ADR-0028
type: decision
tags: [packaging, distribution, cli, gateway, binary]
---

# WF-ADR-0028: A Standalone Single-File Binary for No-Python Users

## Status

Proposed

> Stub for WF-ROADMAP-0004 Initiative 3. The direction (a true no-Python single
> binary that bundles the gateway extra) is proposed; the specific builder
> (PyInstaller vs Nuitka) is deferred to a spike and recorded as an amendment.

## Category

Technical

## Context

WF-ROADMAP-0004 (Initiative 3) wants a single downloadable executable so people
with **no Python toolchain** can run `wayfinder-router chat` and reach the demo.
The base package is deliberately zero-dependency (WF-ADR-0001); only the deployed
gateway pulls the `[gateway]` extra (FastAPI/uvicorn, WF-ADR-0008). A binary
therefore has to bundle that extra and an ASGI server while leaving the base wheel
and the deterministic core untouched.

A key constraint clarifies the candidate set: **zipapp tools (shiv, pex) still
require a Python interpreter on the target machine**, so they do not satisfy the
"no Python" goal. A true standalone binary needs a freezer/compiler.

## Decision

Produce a **self-contained executable** that bundles the `[gateway]` extra and runs
`wayfinder-router chat`, built **per-OS in CI** (Linux/macOS/Windows) and attached
to the GitHub release (extending the existing tag-driven release workflow). The
deterministic core stays importable exactly as today; only the distribution
wrapper is new, and no secret is ever baked into the artifact (WF-ADR-0008).

The builder is **deferred to a spike**: compare **PyInstaller** (mature, broad
support, single-file `--onefile`) against **Nuitka** (compiles to C — smaller and
faster, more complex build). Current lean is **PyInstaller** for breadth and
simplicity, switching to Nuitka only if binary size proves unacceptable. The
outcome and the measured sizes will be recorded as an amendment here.

## Consequences

### Positive

- Non-Python users get a download-and-run Wayfinder; the demo reaches an audience
  the pip/uvx and container paths cannot.
- Release artifacts are produced automatically on tag, alongside the PyPI publish.

### Negative

- A per-OS build matrix to maintain.
- Binary size grows with the bundled ASGI stack (uvicorn/anyio/starlette).

### Risks

- **Bundling fragility.** Freezers can miss uvicorn/anyio dynamic imports.
  Mitigation: smoke-test the built binary in CI (`chat --dry-run` boots, `/demo`
  returns 200) on every target OS before attaching it to a release.
- **Bundle bloat.** Mitigation: measure; consider Nuitka or a slimmer ASGI server;
  the core stays importable without the gateway extra regardless.
- **Signing/AV friction.** Unsigned binaries trip macOS Gatekeeper / Windows
  SmartScreen. Mitigation: code-signing/notarization is explicitly a non-goal for
  the first cut (WF-ROADMAP-0004) and revisited on demand.

## Alternatives Considered

### shiv / pex (zipapp)

Simpler to build, but the produced zipapp **requires Python on the target**, which
fails the "no Python" goal. Usable only if the goal is relaxed to "Python present,"
which the container and pip/uvx paths already cover.

### Docker only

Initiative 2 already delivers a pull-and-run container, but it requires Docker — not
a fit for a non-technical desktop user who just wants an executable.

### Nuitka (kept as the spike's second candidate)

Compiles to C for a smaller, faster binary, but with a heavier, less forgiving build
than PyInstaller. Carried into the spike rather than rejected.

## Success Measures

- A downloaded binary runs `wayfinder-router chat` on each target OS with **no
  Python installed**, opening `/demo`.
- Binary size is measured and documented for the chosen builder.
- The binary is built and smoke-tested in CI and attached to the release on tag.

## Related

- WF-ROADMAP-0004 (Initiative 3 — the binary initiative this decides)
- WF-ADR-0008 (packaging and integration; the `[gateway]` extra and release path)
- WF-ADR-0020 (the `wayfinder-router chat` launcher the binary runs)
- WF-ADR-0004 (the OpenAI-compatible gateway being bundled)
- WF-ADR-0001 (the zero-dependency core preserved; only the wrapper is new)
