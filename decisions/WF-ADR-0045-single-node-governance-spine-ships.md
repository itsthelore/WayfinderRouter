---
schema_version: 1
id: WF-ADR-0045
type: decision
tags: [governance, audit, policy, detectors, identity, store, on-disk, single-node, movement-a, movement-b, sqlite, evidence]
---

# WF-ADR-0045: The single-node governance spine ships; on-disk backends amend the in-memory posture

## Status

Accepted

## Category

Technical

## Context

WF-ROADMAP-0012 anchored a two-movement run: rehouse the gateway's scale-fragile in-RAM surfaces
onto on-disk bounded structures behind unchanged contracts (Movement A), then build the governance
spine — record store, audit log, policy engine, productized detectors, principal identity — that
WF-ROADMAP-0011 needs on one node (Movement B). WF-DESIGN-0013 specified every contract concretely
enough that the failing tests were written first (5f271fc lands the checkpoint-approved suites;
7de93c3 pins the pre-existing behavior of the surfaces being rehoused).

Several settled ADRs describe the surfaces being rehoused as in-memory *by design*: the failover
breaker (WF-ADR-0031, "state is in-memory, per process"), the spend ledger (WF-ADR-0032,
"persisted best-effort"), the response cache (WF-ADR-0033, "**in-memory only** (no disk tier in
v1)"), the rate limiter (WF-ADR-0034, per-process fixed windows), and the feedback loop
(WF-ADR-0006), whose JSONL read path was measured reading the whole log per access. Those
postures were correct for a laptop-scale router; the governance destination makes the gateway a
long-lived, audited enforcement point where "the log is the product." Both movements are now
built, measured, and merged on the run branch; this ADR records the shipped decisions and amends
the named postures on the record, so the corpus does not claim "in-memory only" for surfaces that
now have a disk tier.

## Decision

1. **Movement A: the stateful surfaces gain opt-in on-disk backends; contracts and defaults do
   not change.** `SavingsLedger` (keyword-only `db_path=` selects a SQLite `buckets` table),
   the response cache's body tier, feedback paging, and limiter/breaker state (53a90d1, 8f9f18b)
   are disk-backed *when configured* and byte-identical to the prior in-RAM behavior when not.
   This **amends** WF-ADR-0031/0032/0033/0034 and WF-ADR-0006's read path: their in-memory
   posture is now the *default tier*, not the only tier. Every other clause of those ADRs
   (determinism, opt-in, bounded, fail-open, no plaintext prompt storage in the cache) stands.

2. **Movement B: the governance spine ships as specified in WF-DESIGN-0013.** An append-only
   segmented record store with partitioned SQLite indexes (`store.py`), a metadata-only audit
   log with replay and bounded incremental re-eval (`audit.py`), a compiled-once policy engine
   with a deterministic total order over rules (`policy.py`), the detector set productized from
   the benchmark harnesses (`detectors.py`), and the principal identity model that gives
   `VirtualKey.tags` its consumer (`identity.py`), wired into the request path as a single
   gateway stage (686af96).

3. **Inactive means absent.** Governance activates only via explicit config (`[audit].enabled`,
   a compiled policy); an unconfigured gateway takes no new imports on the hot path and its
   `/metrics` output is byte-identical to the pre-run tree. This is the zero-regression covenant
   of WF-DESIGN-0013, held throughout: the scoring path's instruction-count output hash is
   byte-identical before and after, goldens unchanged, JS parity green, 906 tests green.

4. **The constitution is untouched.** WF-ADR-0001 (deterministic, offline, keyless decision),
   WF-ADR-0039 (offline-first), and WF-ADR-0043 (no external model for Wayfinder's own
   judgment) are inherited unchanged; the spine adds no network dependency and no model call.

5. **Measured outcomes are part of the decision record, misses included.** On the reference node
   (4 vCPU / 15 GB): hot-path policy eval worst-cell p99 434 µs with flat scaling to 10k
   policies / 100k identities; audit-query exponent 0.136 across 100k→10M but **10M p99 165 ms
   vs the 100 ms bar (miss)**; incremental re-eval log-size-independent (warm ratio 0.974);
   cold rebuild 104 s/1M at 10M scale but **132 s vs 120 s at 1M (miss — parallelism is
   segment-bounded and a 1M log has two segments)**; peak RSS 7.6 GB; detector and routing
   quality gates passed against the frozen in-repo oracles. Remediation paths live in
   WF-ROADMAP-0012 §Residual.

## Consequences

- **WF-ROADMAP-0011's trust story gets its load-bearing floor**: on one node, policy evaluation
  is sub-millisecond on the hot path, every decision is replayable from an append-only log, and
  the whole spine runs offline.
- **The corpus stays truthful**: WF-ADR-0031/0032/0033/0034/0006 are amended here rather than
  silently contradicted; readers of WF-ADR-0033's "in-memory only" clause land on this ADR via
  the Related links.
- **Operators opt in deliberately**: nothing changes for existing deployments; the disk tier and
  the governance stage are configuration, not defaults.
- **Two bars are honestly unmet** (audit-query p99 at 10M; rebuild wall at exactly 1M) with
  measured causes and falsifiable remediation projections in the roadmap's residual list — they
  are scheduled work, not open questions.
- **Limitation**: single-node, single-writer by design; multi-node coordination remains out of
  scope (WF-ROADMAP-0011 phases it later).

## Alternatives Considered

- **Supersede WF-ADR-0031–0034 with rewritten ADRs.** Rejected: every decision in them except
  the storage tier survives intact; amendment-by-reference keeps the history legible instead of
  re-litigating settled scope.
- **Make the disk tier the default.** Rejected: the laptop-scale defaults are load-bearing for
  the existing user base, and the zero-regression covenant was the run's spine; defaults can
  move in a later release with their own evidence.
- **An external store (Postgres, Redis) for the spine.** Rejected per WF-DESIGN-0013's constraint
  ledger: single node, no new infrastructure dependency, offline-first (WF-ADR-0039); segmented
  files + SQLite indexes meet the measured bars with stdlib-only machinery.
- **Hold the ADR until the two missed bars are green.** Rejected: the corpus records what
  shipped, and what shipped includes two documented misses; hiding them until fixed would make
  the evidence report and the decision record disagree.

## Success Measures

- The gate battery in the run's evidence report is rerunnable from the committed harnesses and
  reproduces the table (within box noise) on the reference-node shape.
- A gateway with no governance config serves `/metrics` byte-identical to the pre-run tree and
  imports none of `store/audit/policy/detectors/identity` on the request path.
- The residual items carry falsifiable projections; the 10M p99 and 1M rebuild bars are either
  met by the projected remediations or the projections are recorded as refuted.
- Grep finds no surviving unqualified "in-memory only" claim about the amended surfaces that
  does not resolve, via Related links, to this amendment.

## Related

- WF-ROADMAP-0012 (the run anchor; targets, measured outcomes, and residual list)
- WF-DESIGN-0013 (the contract-level specification both movements were built and gated against)
- WF-ADR-0044 (the fleet-run method the run was executed under)
- WF-ADR-0031, WF-ADR-0032, WF-ADR-0033, WF-ADR-0034, WF-ADR-0006 (amended: in-memory posture
  becomes the default tier, not the only tier)
- WF-ADR-0035 (virtual keys — `VirtualKey.tags` gains its consumer in the identity model)
- WF-ADR-0001, WF-ADR-0039, WF-ADR-0043 (the constitution the spine inherits unchanged)
- WF-ROADMAP-0011 (the governance-plane destination this is the single-node floor of)
