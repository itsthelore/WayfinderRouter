---
schema_version: 1
id: WF-ADR-0043
type: decision
tags: [rebuild, testing, parity, methodology, core]
---

# WF-ADR-0043: Rebuild the package under a frozen examiner

## Status

Accepted

## Category

Technical

## Context

The test suite, the packaging guards, and the JS parity gate (WF-ADR-0042)
have grown into a complete external specification of wayfinder-router:
604 tests pin CLI bytes and exit codes, gateway endpoints and headers,
Prometheus exposition, SSE event ordering, TOML round-trips, monkeypatch
seams, and the scorer's exact numerics. WF-ROADMAP-0010 exploits that: a
ground-up re-derivation of the implementation, examined by the suite it
must satisfy, measured by a before/after evidence protocol.

Doing that safely needs ground rules — what is frozen, what is spec, what
counts as done — recorded here so the rebuild is reviewable against a
stated method rather than ad-hoc judgement.

## Decision

1. **The examiner is frozen.** `tests/`, `pyproject.toml`, `benchmarks/`,
   `tools/`, `clients/`, fixtures, and golden files do not change on the
   rebuild branch. At completion, `git diff` against the base for those
   paths is empty.
2. **The legacy source is reference spec, not donor code.** Rebuilt modules
   are genuinely re-derived: their own structure, naming, and prose; fully
   typed; comments state constraints rather than mechanics. User-visible
   bytes (messages, formats, orderings, exit codes) are contract and are
   preserved exactly.
3. **Scoring numerics are a frozen specification.** The rebuilt scorer must
   reproduce the legacy scorer's outputs exactly — weights, feature order,
   summation order, rounding, and line-splitting semantics — verified by
   regenerating the parity corpus byte-identically and passing the JS
   parity check (WF-ADR-0042). Implementation may change; numbers may not.
4. **In-place, module-at-a-time replacement.** Each module is replaced in
   dependency order while the rest of the package keeps the suite
   importable and the editable install valid. Test-imported private names
   and monkeypatched module attributes are part of the public contract for
   this purpose and must survive.
5. **Acceptance is absolute and central.** The rebuild is done only when
   the full suite passes twice consecutively from a clean state, together
   with lint, type-check, and parity gates, run centrally — per-module
   green runs are evidence, never acceptance.
6. **Evidence brackets the rebuild.** Benchmarks, work counts, complexity
   and documentation censuses are captured on the untouched tree before any
   rebuild commit and re-captured with the identical harness after; the
   rewrite's reality is demonstrated by per-file similarity against the
   legacy source alongside the byte-identical external surface.

## Consequences

- Positive: the rebuild is falsifiable — every requirement above maps to a
  rerunnable command, so "done" is a measurement, not a claim.
- Positive: the suite's blind spots get surfaced explicitly (whatever the
  rewrite can change without a test noticing is, by definition, unpinned
  surface) and feed WF-ROADMAP-0010's hardening backlog.
- Negative: contract-pinned means improvement-limited — behavior, defaults,
  and numerics cannot improve on this branch; those gains are staged behind
  later ADRs.
- Risk: timing-sensitive tests (the TUI's threaded worker) can flake under
  load; the method treats any intermittent failure as a rebuild bug first
  and requires quiet-machine reruns before accepting it as environmental.

## Alternatives Considered

- **Parallel package (`wayfinder_router2`) then switch.** Rejected: the
  suite and the editable install reference the package by name; a switch
  moment would be a big-bang integration, defeating incremental examination.
- **Loosening the examiner where tests over-pin internals.** Rejected for
  this branch: changing tests while rebuilding against them destroys the
  experiment's validity. Over-pinned seams are recorded and proposed as
  follow-up work instead.
- **Improving routing quality during the rebuild.** Rejected: quality
  changes alter scored outputs, which the parity gate exists to catch.
  Quality work is measured in config-space and staged behind new ADRs.

## Success Measures

- Full suite green twice consecutively from clean state; ruff, mypy clean.
- `python tools/golden.py` byte-identical regeneration; JS parity green.
- Frozen paths show an empty diff against the base branch.
- Per-file similarity report shows substantive re-derivation of every
  rebuilt module.

## Related

- WF-ROADMAP-0010 (the rebuild and 10x program this method serves)
- WF-ADR-0042 (JS decision-core parity — the numeric freeze)
- WF-ADR-0001 (standalone deterministic core — unchanged invariant)
- WF-ADR-0015 (benchmark methodology — evidence harness foundations)
