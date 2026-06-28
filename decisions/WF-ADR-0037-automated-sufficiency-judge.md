---
schema_version: 1
id: WF-ADR-0037
type: decision
tags: [calibration, labeling, judge, offline, invocation, trust]
---

# WF-ADR-0037: Automated sufficiency judge for the calibration label faucet (offline, gated)

## Status

Accepted

## Category

Technical

## Context

Calibration turns labeled prompts into a routing config (`calibrate`), and the onboarding loop
(`onboard.run_onboarding`, WF-ADR-0006) already collects those labels by running each prompt
through two arms and asking an **injected** judge which arm was good enough — recording the
answer through the `{text, label}` feedback faucet. Until now the only judge that existed was the
interactive human at the terminal (`Is 'local' good enough? [y/N]`). The open question
WF-DESIGN-0004 named — *label acquisition is the hard part* — is precisely this: where do labels
come from at scale, without a person in the loop?

A user asked: route the same query to multiple models, compare the responses, and "generate a
heuristic from there." That is exactly the loop above with the human judge **automated** — and
two constraints frame it:

- **The deterministic core is sacred (WF-ADR-0001).** Comparing responses requires *calling*
  models, which must never enter the decision path. But the onboarding/calibration loop is
  already offline / calibration-time, in the invocation layer (it holds keys, calls upstreams).
  An automated judge lives there too. The runtime routing decision stays byte-identical and
  model-free; this only produces labels that the offline `calibrate` step consumes.
- **A bad label is not a bad eval number — it is degraded production routing.** The config a
  judge mints is consumed by `recalibrate` → the live `wayfinder-router.toml`. A judge that
  mislabels (e.g. two arms that confidently agree on a wrong answer → "cheap sufficient") silently
  biases the cut toward under-routing. So judge labels cannot be trusted on faith.

The routing question is the narrow **binary sufficiency** one — *was the cheaper arm good enough to
skip the dearer one?* — not a subjective "which answer is better", because that binary is the only
thing the two-arm threshold sweep consumes (a `label`).

## Decision

1. **A pluggable `Judge` seam, automating the existing onboarding judge.** `judge.Judge` is a
   protocol mapping `(prompt, cheap, expensive)` to a tri-state `Verdict` — *sufficient* (route the
   cheap arm), *insufficient* (route the dear arm), or **abstain** (`sufficient is None`). The
   onboarding loop is generalized so a judge may return `None`: the prompt is **skipped and no
   label recorded**, never a third "abstain" label (which would break threshold calibration's
   exactly-two-labels contract). The human judge is unchanged (it never abstains).

2. **v1 judge is a deterministic heuristic ensemble (`HeuristicJudge`).** Ordered comparators,
   first decisive wins: refusal/error/stub detection, normalized agreement, lexical similarity;
   otherwise *abstain*. It is free, pure, and **replayable** from a saved comparison log with no
   re-calling — which an LLM judge cannot be. A heuristic over free text is a deliberately weak
   proxy; it abstains whenever it cannot tell, and never guesses.

3. **Judge labels are untrusted until they clear mandatory, judge-agnostic gates
   (`sufficiency.evaluate`).** Before `wayfinder-router judge` emits a config it must pass:
   (a) **agreement vs a human gold set** — Cohen's κ ≥ a floor (default 0.6); (b) **out-of-fold
   lift** — k-fold cross-validated accuracy of the resulting config beats the majority baseline;
   (c) **degenerate-collapse** — both arms meaningfully represented. On any failure the CLI prints
   the confusion matrix and **refuses to emit a config** (non-zero exit); the collected labels are
   still recorded as data. This is the same honesty bar WF-DESIGN-0004 sets for calibration,
   specialized to a judge-sourced label set; the gates are identical for a heuristic or a future
   LLM judge.

4. **Provenance over reproducibility.** The arm responses are not bit-reproducible, so a minted
   config carries a comment banner stamping *what judged what* — judge version, prompt/gold file
   hashes, the κ/lift that passed, and the tool version — extending `recalibrate`'s
   `# recalibrated from feedback:` convention. The label-derivation is not replayable; the banner
   makes it auditable instead.

5. **The comparison/response store is governed (WF-DESIGN-0008).** Saving raw prompts+responses
   (`--save-comparisons`) is **off by default** and written only on request — it is a response-body
   store, held to the same opt-in posture as the response cache (WF-ADR-0033). The `{text, label}`
   feedback log is already-consented label data (WF-ADR-0014) and is unaffected.

6. **Shadow/live-traffic collection is deferred.** Sourcing comparisons from real gateway traffic
   (an async fan-out + a persistent body store) is a separate, heavier design (real-dollar cost on
   production traffic, a persistent privacy surface) and is **not** in v1. The offline batch path —
   real prompts replayed from the feedback/gateway logs — gives representative inputs without it.

## Consequences

- **The calibration moat gets its label faucet.** "Tune it on your own traffic" no longer needs a
  human grading every prompt; the loop is `judge → (gated) calibrate → route`, automated.
- **Determinism preserved.** No model call enters the decision path; the judge and gates are pure
  and offline, testable like `calibrate.py` / `cache.py`.
- **An LLM judge is a clean drop-in** via the same `Judge` protocol and the same gates — no rewrite.
- **Honest by construction.** The heuristic judge abstains on the hard, open-ended prompts (its
  weakness), and the κ + lift gates refuse to mint a config the labels don't support — so a weak
  judge fails loudly rather than quietly degrading routing.
- **Limitation — coverage skew.** A heuristic judge labels mostly verifiable/short prompts and
  abstains on prose; the abstain rate is reported so the coverage gap is visible. An LLM judge
  (later) addresses it.

## Alternatives Considered

- **A new parallel collection pipeline (`collect.py` / `judge_runner.py`).** Rejected — it would
  fork a copy of `onboard.run_onboarding` and its model-injection seam, which already do exactly
  this. The judge slots into the existing loop.
- **Emit labels with no trust gate (or a soft warning only).** Rejected — an ungated config from a
  weak judge silently degrades live routing; the gate must *refuse*, matching the house style.
- **An LLM judge in v1.** Deferred — non-deterministic (not replayable), costs hundreds of calls
  plus a gold set, and is a drop-in later through the seam; the deterministic judge proves the
  pipeline first.
- **Live gateway shadow mode in v1.** Deferred — real-dollar async fan-out on production traffic +
  a persistent response-body store is its own WF-DESIGN-0008-scale effort.
- **Judge "which answer is better" (quality ranking).** Rejected — subjective and not what the
  two-arm sweep consumes; the binary sufficiency question is the right, tractable target.

## Success Measures

- `wayfinder-router judge prompts.jsonl --gold gold.jsonl` records sufficiency labels and, **only
  when the κ + lift + degeneracy gates pass**, emits a calibrated config with a provenance banner;
  on gate failure it prints the confusion matrix and exits non-zero without a config.
- The deterministic core is untouched: the runtime decision path makes no model call, and the
  "RAC absent / model-free decision" CI guards still pass.
- The judge is deterministic and replayable; the gates reproduce the same verdict given the same
  labels.

## Related

- WF-ADR-0001 (deterministic, offline core — preserved; judging is offline, decision path unchanged)
- WF-ADR-0006 (collect judgments → calibrate → route; this automates the judge)
- WF-ADR-0007 (recalibrate from the feedback log — consumes the judge's labels unchanged)
- WF-DESIGN-0004 (one-command calibration loop; this is its label-acquisition piece, with the same
  cross-validated-lift honesty bar)
- WF-ADR-0033 / WF-DESIGN-0008 (response-body store posture — the comparison log inherits it)
- WF-ADR-0014 (metadata-only routing visibility; the `{text, label}` log is consented label data)
