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

### Phase X — The 10x program

Phases E and Q are complete, green, and measured; Phase X stages the
improvements they exposed. It leads with the least flattering true finding:
**the contract-pinned rebuild did not move wall-clock, and the routing-quality
wins all require superseding a settled decision — none can ship silently.**

#### Measured baselines (the "before" every projection is falsifiable against)

Rebuild deltas (interleaved-median wall-clock, legacy vs rebuilt tree; full
tables in the Phase E evidence report):

| Metric | Legacy | Rebuilt | Delta | Why |
| --- | --- | --- | --- | --- |
| score (synthetic 2000) | ~0.100s | ~0.098s | ~0–2% | scan-bound; the 64.9% call-count cut doesn't reach the clock |
| score (real ×200) | ~0.116s | ~0.111s | ~0–4% | regex/string scanning dominates, not call overhead |
| `build_app` ×50 | ~0.829s | ~0.823s | ~0% | 94% is FastAPI/pydantic schema-gen, framework-inherent |
| config load ×1000 | ~0.134s | ~0.129s | ~0–4% | 74% is stdlib `tomllib.loads` |
| cold import | ~58ms | ~58ms | ~0% | eager `recalibrate→gateway` chain (~43ms, ~21ms asyncio) unchanged on this branch |

The rebuild cut profiled call-count 64.9% and added contract-safe locks to the
previously-unlocked `Metrics` dicts and `CircuitBreaker`; neither moved
wall-clock, because the hot path is character scanning and the locks are
uncontended single-threaded. That is the honest ceiling of a byte-frozen
rewrite.

Routing-quality baseline on `benchmarks/dataset.jsonl` (24 rows), shipped
default vs the operating points Phase Q measured:

| Config | threshold | PGR | quality | cloud | note |
| --- | --- | --- | --- | --- | --- |
| **shipped default** | 0.50 | **0.00** | 0.375 | **0%** | inert — byte-identical to always-local |
| structural knee | 0.02 | 0.60 | 0.75 | 54% | one number off the default |
| lexical-on knee | 0.11 | **0.80** | 0.875 | 50% | best point; rescues hard-short 0/6→6/6 |
| held-out calibration (5-fold) | — | 0.886 (sd 0.166) | — | — | direction only; 24-row caveat |

**24-row caveat (governs every quality projection below):** these prove
*direction and mechanism* — the 0.5 default is inert, a useful band exists at
t≈0.01–0.20, and lexical signals rescue the documented hard-short blind spot —
and are **nowhere near enough to set a shipped magnitude**. Held-out PGR carries
sd 0.16–0.31 on ~12-row splits; the in-sample lexical numbers are flattered by
the shared-author bias `benchmarks/blind_eval.py` exists to catch. Every "ship
it" gate below is the RouterBench-scale, held-out, cross-provider run, not these
24 rows.

#### What the rebuild delivered vs what needs a new ADR

The boundary is the point of this program:

- **Delivered by the contract-pinned rebuild (no ADR, already on the branch):**
  a fully re-derived, typed package; the frozen suite green twice; JS parity
  byte-identical; a 64.9% call-count reduction; and contract-safe locks on the
  `Metrics` counters and `CircuitBreaker` that were unlocked shared state
  reachable from the threaded server (FIX-OK — invisible to the serial suite,
  behavior-preserving).
- **Requires a new ADR (cannot ship on this branch, by construction):** every
  item below. Moving the default cut changes frozen bytes (WF-ADR-0043);
  turning lexical on changes scored bytes and reverses WF-ADR-0016; the
  single-pass scanner perturbs edge-case numerics; the lazy import facade
  reshapes the documented import graph and monkeypatch seams; multi-worker state
  changes the operational contract. None override an ADR silently.

#### Initiatives (sequenced by risk, lowest first)

**X1 — Calibrated default routing (WF-ADR-0044).** Lowest risk, highest
user-visible leverage. Move the zero-config experience off the inert 0.5 cut via
init-time calibration on a bundled mini-dataset plus a `doctor` inert-cut
self-check. Projected default-path **PGR 0.00 → ~0.60** (structural knee, t≈0.02)
with quality 0.375 → 0.75; falsifiable by `python -m benchmarks.run` on the
generated config. Touches no scored numeric — only the operating point and
first-run bytes — so it is independent of the parity freeze. Small-sample caveat
applies: 0.60 is a direction, a shipped number needs the held-out run.

**X2 — Versioned scorer + lexical default (WF-ADR-0045).** Higher risk: it
supersedes two settled decisions. A `schema_version` bump ("3"→"4") that ships
calibrated non-zero lexical weights as the default, superseding WF-ADR-0016's
off-by-default and the WF-ADR-0043 weight-pinning + JS byte-mirror (re-ported in
lockstep per version — a mixed Python-"4"/JS-"3" state is a hard parity failure
by design). Projected **PGR ~0.80** at t=0.11. **Stated tradeoff up front:** the
measured lexical-on config regresses hard-short-structured **1.00 → 0.25** while
rescuing hard-short 0/6 → 6/6 — a net win on 24 rows but not free, and each
bucket is a handful of prompts. Ships only if the "4" weights beat the length
baseline *held-out* on the shipping evaluation.

**X3 — Scorer + import performance (WF-ADR-0046).** The wall-clock work the
rebuild could not do, because the byte-safe versions buy nothing. (a) A
single-pass feature scanner behind the WF-ADR-0045 version bump (the rebuild
proved call-count cuts don't move the clock, so this must change the *scanning
algorithm*, collapsing the four whole-text passes): projected `score_complexity`
**~24.9µs → ~18–20µs/decision**; falsifier `python -m benchmarks.run` decide-µs
≤ ~20µs under `scorer_version = "4"` with a new-vs-old feature-dict diff over the
dataset. (b) A lazy scoring-only import facade: cold import **58ms → ~22ms**
(scoring-only ~8–10ms) by dropping the asyncio-dominated gateway subtree, noting
the `recalibrate` submodule/re-export collision that makes a naive PEP-562 shadow
unsafe; falsifier `python -X importtime -c "import wayfinder_router" 2>&1 | tail
-1` with `pytest -q tests/test_packaging.py tests/test_recalibrate.py` green.
Explicitly *not* chasing `build_app` (94% FastAPI) or config parse (74%
tomllib) — architecture-pinned, measured.

**X4 — Concurrency / multi-worker state hardening (WF-ADR pending).** The
quality-safety lens found that the `Metrics` dicts and `CircuitBreaker` were
unlocked shared state reachable from the threaded server — the rebuild already
added locks contract-safely (FIX-OK), so single-process thread-safety is closed
and should be pinned with a concurrency test so it cannot regress. **Unaddressed
and ADR-gated:** all in-memory state (rate limiter, breaker, spend budget,
savings ledger, metrics) is **per-process**, so `serve --workers N` silently
multiplies limits ~N× and `/metrics` reports one worker's view. A `StateBackend`
protocol (in-memory default; optional shared backend) behind limiter, breaker,
budget, and ledger changes the operational contract (limits become cluster-wide)
and adds an optional dependency — ADR-gated under WF-ADR-0031/0032/0034.
Falsifiable as a correctness invariant, not latency: with N workers and a shared
backend, admitted rpm == configured rpm ±ε (today ≈N×).

Sequencing rationale: X1 is the cheap user-visible unlock and ships the bundled
dataset X2/X3 reuse; X2 carries the real quality win and the real tradeoff; X3
is coupled to X2's version machinery; X4 is independent hardening whose cheap
half already landed and whose expensive half is gated on offering multi-worker
at all.

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
- WF-ADR-0044 (Phase X: calibrated default routing — off the inert 0.5 cut)
- WF-ADR-0045 (Phase X: versioned scorer + lexical default — supersedes 0016/0043)
- WF-ADR-0046 (Phase X: single-pass scanner + lazy import facade)
- WF-ROADMAP-0002 (core hardening — the arc this continues)
