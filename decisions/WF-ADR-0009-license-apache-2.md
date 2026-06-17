---
schema_version: 1
id: WF-ADR-0009
type: decision
tags: [license, packaging, adoption]
---

# WF-ADR-0009: License — Apache 2.0

## Status

Accepted

## Category

Product

## Context

Wayfinder was scaffolded under MIT. As it heads toward pilot and likely enterprise
use — and may grow commercial extensions later — the licence choice should be
deliberate. Both MIT and Apache 2.0 are permissive and either would work; the
question is which serves adoption best.

The substantive difference: MIT is ~3 lines and grants copyright permissions only,
with no patent language. Apache 2.0 is also permissive but adds an **explicit
patent licence** from contributors to users, a **patent-retaliation** clause
(asserting patents in the software terminates the grant), an explicit **trademark**
non-grant, and `NOTICE`/attribution mechanics.

## Decision

License Wayfinder under the **Apache License, Version 2.0**.

- Replace the MIT `LICENSE` with the full Apache 2.0 text; set
  `license = { text = "Apache-2.0" }` in `pyproject.toml`; add a `NOTICE` file.
- The explicit patent grant is the deciding factor: corporate open-source review
  teams look for it, and it gives adopters and contributors patent clarity that
  MIT does not address — while remaining fully permissive.
- Wayfinder is independent of RAC (WF-ADR-0001), so this choice is its own; the
  sibling `decisiongrounding` being MIT imposes no constraint.

## Consequences

### Positive

- Lower legal friction for enterprise pilots and adopters (explicit patent licence
  + retaliation defence).
- Still permissive: no copyleft, free to embed and redistribute.

### Negative

- A longer licence file and `NOTICE`/attribution obligations for redistributors
  (standard Apache mechanics) versus MIT's three lines.

## Alternatives Considered

### MIT (the scaffolded default)

Maximal familiarity, minimal ceremony.

#### Disadvantages

- No explicit patent grant — the gap enterprise reviewers care about for a tool
  they route production traffic through.

### A copyleft licence (e.g. GPL/MPL)

#### Disadvantages

- Against the embed-anywhere, BYO-key posture; would deter the in-process library
  and gateway-sidecar use Wayfinder is designed for.

## Success Measures

- `LICENSE` is the Apache 2.0 text; `pyproject.toml` declares `Apache-2.0`; a
  `NOTICE` file is present.
- No remaining MIT references in the package metadata or licence files.
