---
schema_version: 1
id: WF-ROADMAP-0012
type: roadmap
tags: [governance, audit, policy, identity, scale, on-disk, single-node, deterministic, evidence]
---

# Roadmap: simplify, harden, and build the single-node governance spine

## Status

Done — both movements shipped and gated (WF-ADR-0045); see §Measured outcomes and §Residual
below. Two gate bars were missed and are recorded there with causes and remediation
projections, per §Verification's no-narrowing rule.

## Context

WF-ROADMAP-0011 names the destination — Wayfinder as the policy enforcement point an
organization's AI traffic flows through — and WF-ROADMAP-0010 sets the evidentiary standard:
every claim rerunnable, every miss reported. This roadmap is the exercise that connects them,
in two movements on one node.

**Movement A — simplify and operationally harden what exists.** The gateway's stateful
surfaces are scale-fragile by construction: the exact-match response cache lives in process
RAM (WF-ADR-0033), the feedback store is a JSONL read wholesale on every access
(WF-ADR-0006), and the rate limiter, circuit breaker, spend budgets, and savings ledger all
keep per-process in-memory state (WF-ADR-0034, WF-ADR-0031, WF-ADR-0032). Movement A moves
these onto on-disk bounded structures **behind unchanged contracts**: every currently
observable behavior is preserved, and WF-ADR-0001's constitution — offline, deterministic,
sub-millisecond, no model call, no data egress — remains frozen.

**Movement B — the governance spine.** The pieces WF-ROADMAP-0011 requires but which do not
exist in the package today, built spec-first: a persistent on-disk audit/decision log with
partitioned indexes, hot-path policy evaluation (route + compiled-once PII/secret detectors +
policy verbs + identity attribution) inside a sub-millisecond budget, and held-out
decision-quality evidence.

## Constraint ledger

Two regimes, precisely separated:

- **Existing surfaces** (all of Movement A; every touchpoint Movement B modifies): current
  observable behavior and WF-ADR-0001 are frozen. Frozen paths: `tests/`, `pyproject.toml`,
  `conftest.py`, `benchmarks/`, `clients/`, `tools/`, `examples/`, `docs/` (except the
  additive style handoff), `README.md`, and existing files under `decisions/` and
  `roadmaps/`. The JS parity numerics (`clients/shared/src/scorer.js` vs the Python scorer)
  are frozen byte-for-byte.
- **Net-new surfaces** (audit log, policy engine, identity model): no existing behavior to
  freeze. Contract tests are written spec-first from the design, approved by a human before
  any builder builds to them.
- **Examiner extension protocol:** existing test files are never modified. New test files
  are additive-only and land in exactly two ways: the Phase-0.5 human-approved batch, and one
  human-approved batch per Movement-B supersession bundle (tests presented with the bundle's
  ADR and measured harness win, approved as a unit).
- Sharding, external services, and shipping content out of process are prohibited. The only
  sanctioned index is a persistent on-disk one (mmap or embedded single-file KV), one node.

## Outcomes

The unit of scale is the audit/decision log, measured as a curve at 100k → 1M → 10M records
on the reference node (4 vCPU / 15 GB RAM / ~30 GB NVMe). The claim is invariance: the
fitted scaling exponent across the curve, cold-cache, curve legs in randomized order.

| Gate | Target | Check |
|---|---|---|
| Hot-path policy eval | p99 < 1 ms, p50 < 250 µs added; flat from 10→10,000 policies and 100→100,000 identities. Boundary: in-process timing of the policy-evaluation call (score + detect + verbs + attribution + audit-append), excluding HTTP/ASGI transport, which is reported separately | in-process percentile harness + load generator |
| Audit query / replay | p99 < 100 ms, p50 < 30 ms, flat 1M→10M records (exponent ≈ 0 within noise) | query harness across the size curve, cold-cache protocol |
| Incremental re-eval (~1,000-request changeset or one policy edit) | < 5 s, log-size-independent | re-eval harness at 1M and 10M |
| Cold full build / whole-log replay | ≤ ~2 min per 1M records, parallel across cores; the only path allowed to grow with N | timed build at each curve point |
| Memory | working-set RSS ≤ ⅔ node RAM (≤ ~10 GB); log + indexes + cache + ledger on disk; on-disk sizes reported | RSS sampling during each gate |
| Detector quality (held-out) | precision/recall ≥ published in-repo baselines (micro P 0.812 / R 0.867 per `benchmarks/detector-validation-results.md`; `ai4privacy-validation-results.md`; `gitleaks-crosscheck-results.md`); zero regression | rerun the repo's validation harnesses |
| Routing quality (held-out) | PGR floor 0.60 / stretch 0.80, measured only as: operating point selected on the train fold, evaluated once on the untouched test fold, fold ids recorded (`benchmarks/split.py` partitions by prompt hash) | split + blind eval with recorded fold ids |
| Legacy wall documented | the point where the in-RAM design bends or fails on the curve, recorded precisely (time / RSS / crash) | same harness against the pre-Movement tree |

Data fallback: external corpora (AI4Privacy, RouterBench) are egress-blocked in this
environment (CONNECT 403, policy denial — verified at run start). Detector quality is
therefore gated against the committed validation-results files as a frozen oracle on in-repo
fixtures; routing quality on the largest in-repo held-out corpus; the external reruns are
reported as an honest miss with the exact failure.

## Initiatives

1. Baseline evidence: full before-metric capture, scale-corpus generator, and the
   legacy-falls-over measurement against the untouched tree.
2. Examiner hardening: characterization tests for unpinned existing behavior; spec-first
   contract tests for the net-new surfaces; perf and quality harnesses as rerunnable gates
   (human checkpoint).
3. Movement A: response cache, feedback store, rate limiter, breaker, budgets, savings
   ledger onto on-disk bounded structures behind unchanged contracts.
4. Movement B: audit/decision log + partitioned indexes; policy engine (verbs, compiled
   detectors, identity attribution) inside the hot-path budget; supersession bundles with
   measured wins (human-approved per bundle).
5. After-evidence, residual plan (human checkpoint), and the published evidence report.

## Measured outcomes (2026-07-06, reference node 4 vCPU / 15 GB)

Every row below is the recorded verdict of the corresponding gate harness; the published
evidence report carries the rerun commands and raw verdict files.

| Gate | Measured | Verdict |
|---|---|---|
| Hot-path policy eval | worst cell p99 434 µs @ 10k policies (p50 ≤ 150 µs); slope 0.070 over policies, −0.006 over identities; RSS 459 MB | **Pass** |
| Audit query / replay | p99/p50: 88.1/0.31 ms @100k, 76.8/1.32 ms @1M, 165.1/12.6 ms @10M; fitted exponent 0.136 | **Pass at 100k/1M; miss at 10M p99** (165 vs 100 ms — cold full-page materialize floor ≈ 90 ms on this box; box I/O drifts several-fold across the day) |
| Incremental re-eval | warm 0.081 s @1M / 0.079 s @10M (ratio 0.974, log-size-independent); cold-page first pass 0.8 s / 3.1 s; reads == changeset pinned by counting proxy | **Pass** |
| Cold rebuild | 10M: 1044 s (bar 1200 s), 15 segments, 4 workers ⇒ 104 s per 1M; 1M: 132.4 s (bar 120 s) — a 1M log seals only 2 segments, so parallelism is segment-bounded (speedup 1.17×) | **Pass at 10M; narrow miss at 1M** |
| Memory | peak RSS 7.6 GB (audit gate @10M); 10M store ≈ 8.7 GB on disk | **Pass** |
| Detector quality | micro P 0.8125 / R 0.8667, per-detector no-regression, exact floats vs the frozen oracle | **Pass** |
| Routing quality | held-out test-fold PGR 1.0 (train-selected operating point, FNV-1a fold ids recorded, n_test = 8 — small-n caveat); 154-row blind eval PGR 0.957 supporting | **Pass on protocol; magnitude carries the small-n caveat** |
| Legacy wall | feedback read-wholesale 1.84 s/access @1M rows (linear); in-RAM ledger `period()` ≈ 630 ms @1M; in-RAM cache flat in latency but ≈ 520 MB RSS @1M entries | **Documented** |
| External corpora | huggingface.co CONNECT 403 (policy denial, verified twice) | **Honest miss (environmental)**; frozen in-repo oracle used per §Outcomes fallback |

Zero-regression covenant: 906 tests green (626 at baseline), scoring-path work-count output
hash byte-identical before/after, goldens byte-identical, JS parity 21/21, `/metrics`
byte-identical with governance unconfigured.

## Residual

Ordered by leverage; each perf item carries a falsifiable projection.

1. **Audit-query 10M p99 (165 → <100 ms).** The tail is the cold materialization of a full
   result page from ~200-byte records spread across segment pages. Projection: batching
   payload reads by segment offset (one ordered pass instead of per-record seeks) or a
   covering payload column in the shard index halves the cold tail; refuted if a prototype
   shows the floor is the page fault count itself, in which case the bar needs an explicit
   cold/warm split as the honest restatement.
2. **Rebuild wall at exactly 1M (132 → ≤120 s).** Cause is segment granularity, not
   throughput (10M leg proves 104 s/1M with full fan-out). Projection: sub-segment work
   units (shard-level rebuild tasks) restore ≥3-way parallelism at 2 segments and clear the
   bar; alternatively the bar is restated to per-1M throughput at fan-out, with the
   granularity bound documented.
3. **Cache-log compaction.** The disk cache's append log grows without a compaction pass;
   bounded today by the entry/byte ceilings but wasteful long-run. Design deferred from
   Movement A.
4. **`gateway.build_app` complexity (cc 179 → 205).** The governance stage wiring grew the
   builder despite the helper-extraction instruction; extract stage assembly into
   module-level helpers. No behavior change.
5. **Audit-log hot-reload close leak.** The reload path can leave the previous audit
   connection to the GC instead of closing it deterministically (builder note; benign
   single-digit fd count, but sloppy).
6. **Larger labeled routing corpus.** The PGR gate's n_test = 8 fold is honest but thin;
   when egress allows, rerun against RouterBench per §Outcomes and publish alongside the
   in-repo result.
7. **User-facing governance docs.** `docs/` was frozen for the run; the `[audit]`/policy
   TOML surface needs operator documentation as a follow-up docs change.

## Verification

Every gate row above names its harness; harness outputs live outside the repo. The full test
suite must pass twice consecutively from a clean tree at every integration boundary. A missed
target is reported with numbers and what would move it — never narrowed or faked. Flat means
a fitted exponent, not two lucky points; held-out means the test fold was never touched
during tuning.

## Non-goals

- Sharding, external datastores, or multi-node designs — one node, on-disk, is the point.
- Model-backed policy evaluation on the hot path (WF-ADR-0001, WF-ADR-0043 stand).
- Any change observable by the existing test suite on existing surfaces.
- Wall-clock speedup claims for the scoring path (the scan-bound profile is settled
  evidence; WF-ROADMAP-0010).

## Related

- WF-ROADMAP-0011 — the governance plane this spine serves
- WF-ROADMAP-0010 — the evidence standard this run is held to
- WF-ADR-0001 — the frozen constitution (offline, deterministic, sub-ms, no egress)
- WF-ADR-0006, WF-ADR-0031, WF-ADR-0032, WF-ADR-0033, WF-ADR-0034 — the stateful surfaces
  Movement A rehouses
- WF-ADR-0043 — internal model use, if any, is local (bounds the detector design)
