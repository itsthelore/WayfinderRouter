---
schema_version: 1
id: WF-ROADMAP-0010
type: roadmap
tags: [rebuild, core, gateway, tui, cli, evidence, benchmarks, quality]
---

# Roadmap: Ground-up rebuild under the frozen examiner, and the 10x program

## Status

In progress

## Context

Wayfinder's corpus has grown decision-by-decision from v0.1.0 to 2026.7.0:
23 modules, ~10.2k lines, 42 ADRs, and a 600+-test suite that pins the
product's user-visible behavior down to exact bytes — CLI text and exit
codes, Prometheus exposition lines, SSE event ordering, TOML round-trips,
and a JS decision core (WF-ADR-0042) that must mirror the Python scorer
numerically. That suite, together with the parity gate and the packaging
guards, now constitutes a complete external specification of the product.

This roadmap runs the experiment that specification makes possible: rebuild
the implementation from scratch with the tests as the examiner and the
legacy source as reference spec, measure honestly what a contract-pinned
rewrite can and cannot improve, and use what the rebuild teaches — plus a
measured routing-quality baseline — to stage the follow-on program: a
faster engine and a router that routes better, with every projected number
falsifiable against the repo's own benchmark harness (WF-ADR-0015).

Two goals name what "better" means here:

1. **Quicker.** Same outputs, less work: leaner scoring passes, a lighter
   per-request decision path, cheaper config loading, faster cold import.
   Where the profile shows the cost is architecture-pinned, the roadmap
   says so with numbers instead of claiming a speedup.
2. **Routes better.** The lexical difficulty signals (WF-ADR-0016) still
   ship at weight 0.0; calibration (WF-ADR-0003, WF-ADR-0017) and the
   configurable lexicon (WF-ADR-0019) exist but the stock experience does
   not use them. Turning measured quality into the default experience
   requires superseding settled decisions (notably WF-ADR-0042's
   byte-mirror pin), so those items are staged behind new ADRs — never
   changed silently.

## Outcomes

- The package is fully re-derived: every module rewritten, every test in
  the frozen suite passing, all repo gates green, and the JS parity corpus
  regenerating byte-identically — proving the rewrite preserved the
  contract while the source itself is demonstrably new.
- A before/after evidence base exists: interleaved wall-clock benchmarks on
  a deterministic synthetic corpus and the real dataset, instrumented call
  counts, complexity and documentation censuses, and per-file similarity —
  each traceable to a rerunnable harness.
- A measured routing-quality baseline (quality, PGR, cost savings on the
  benchmark dataset and seed corpora) plus config-level experiments that
  quantify the headroom in lexical signals and calibration.
- A staged 10x program whose initiatives carry measured or falsifiable
  projected numbers, with an explicit line between what a contract-pinned
  rewrite delivered and what requires superseding a settled decision.

## Initiatives

### Phase R — The rebuild (this branch)

Rebuild all 23 modules in dependency order under disjoint ownership, with
the frozen suite as the examiner. Method recorded in WF-ADR-0043. The
scoring numerics are treated as a frozen specification (WF-ADR-0042 parity
invariant); user-visible bytes are contract; internals are genuinely
re-derived and fully typed.

### Phase E — Evidence

Capture before/after: interleaved-median wall-clock on the synthetic and
real corpora, `sys.setprofile` work counts, cyclomatic/maintainability
censuses, docstring/comment censuses, and per-file similarity against the
legacy source. Publish the evidence report with the harness commands.

### Phase Q — Routing-quality experiments

Using only existing machinery (lexical config, mined lexicons, calibration
objectives), measure quality/cost deltas against the Phase-E baseline on
the benchmark harness. No shipped defaults change in this phase.

### Phase X — The 10x program (expanded at the end of this branch)

Initiatives staged from Phases E and Q, each with its measured or projected
numbers and, where a settled decision must be superseded, a new ADR.

## Budgets

The rebuild ships zero behavior change: no CHANGELOG entry, no version
bump. Evidence and experiments run offline on the repo's own harnesses.

## Verification

- `python -m pytest -q` twice consecutively clean from a clean state.
- `ruff check .`, `python -m mypy wayfinder_router` clean.
- `python tools/golden.py` output byte-identical to the committed corpus;
  `node clients/shared/test/parity.mjs` green.
- `git diff origin/main -- tests/ pyproject.toml` empty.
- Evidence claims each trace to a rerunnable harness command.

## Non-goals

- Changing any scored decision, default weight, or user-visible byte on
  this branch.
- Rewriting `benchmarks/`, `tools/`, or `clients/` — they are the examiner
  and stay frozen.
- Auto-adopting quality-experiment winners as defaults; that is Phase X,
  behind new ADRs.

## Related

- WF-ADR-0043 (rebuild method: frozen examiner, in-place replacement)
- WF-ADR-0042 (JS decision-core parity — the numeric freeze)
- WF-ADR-0015 (benchmark methodology — the quality harness)
- WF-ADR-0016 (lexical signals — shipped at weight 0.0, the quality headroom)
- WF-ADR-0003 / WF-ADR-0017 / WF-ADR-0019 (calibration and lexicon machinery)
- WF-ROADMAP-0002 (core hardening — the arc this continues)
