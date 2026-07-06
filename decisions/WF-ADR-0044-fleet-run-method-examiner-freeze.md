---
schema_version: 1
id: WF-ADR-0044
type: decision
tags: [method, process, testing, evidence, governance, examiner-freeze, verification]
---

# WF-ADR-0044: Fleet-run method — examiner freeze, artifact gates, evidence protocol

## Status

Accepted

## Category

Process

## Context

WF-ROADMAP-0012 rebuilds the gateway's stateful surfaces and adds a net-new governance
spine in a single supervised run executed by a fleet of coding agents. Two failure modes
dominate such runs, both measured on this codebase: an agent can satisfy every test while
violating a process constraint the tests cannot see, and an agent's self-report about its
own artifact can be materially false. The method below exists to make both impossible to
miss, and it applies to this run and any future run of its kind against this repository.

## Decision

1. **The examiner is frozen.** Existing test files are never modified during a run. The
   suite — plus every repo file it reads (`README.md`, `docs/faq.md` and the files its
   links resolve to, `conftest.py`, `pyproject.toml`, `benchmarks/`, `clients/`, `tools/`,
   `examples/`) — is the specification, not the work product.
2. **Examiner extensions are additive, human-approved batches.** New test files land only
   as (a) one characterization/spec-first batch approved before building starts, and
   (b) one batch per supersession bundle, presented together with the bundle's ADR and its
   measured harness win. Nothing else may extend the examiner.
3. **Every constraint ships with its verification command.** A rule that cannot be checked
   by a command is explicitly labeled as human-verified conduct. Acceptance rows are
   pass/fail against a named command; measurements without a pass bar are labeled REPORT.
4. **Artifacts are gated, not just plans.** Before any merge commit, the orchestrator —
   never the building agent — re-runs the gates on the artifact itself: the full suite from
   a clean tree, lint, types, JS parity where scorer-adjacent, and a frozen-path diff
   against the recorded base commit of the run.
5. **Behavior claims trace to rerunnable evidence.** Performance is reported as fitted
   scaling exponents over a measured curve with a cold-cache protocol, never endpoint
   ratios alone; quality is reported held-out with recorded fold ids, never in-sample; a
   missed target is published with numbers and what would move it. Timing jobs run alone.
6. **Settled decisions are superseded on the record, never overridden silently.** Any
   change that conflicts with an accepted ADR ships as a superseding ADR naming its
   predecessor, carrying the measured number that justifies it.

## Consequences

- The suite's green state is necessary but never sufficient; acceptance is the gate
  battery, and the gate battery is rerunnable by anyone from the commands in the run's
  roadmap (WF-ROADMAP-0012 for this run).
- Human checkpoints exist exactly where the examiner is extended or scope is staged —
  nowhere else — so autonomous execution stays safe precisely because the spec cannot
  drift mid-run.
- Corpus artifacts produced by a run (roadmaps, ADRs, designs) are additive and take the
  next unused numbers at runtime; parallel work makes reserved-looking gaps untrustworthy.

## Related

- WF-ROADMAP-0012 — the run this method governs
- WF-ROADMAP-0010 — the evidence-engine standard this method operationalizes
- WF-ADR-0015 — benchmark methodology (held-out discipline this ADR generalizes)
