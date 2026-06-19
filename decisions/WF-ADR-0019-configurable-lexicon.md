---
schema_version: 1
id: WF-ADR-0019
type: decision
tags: [scoring, lexicon, config, ui, calibration]
---

# WF-ADR-0019: The Trigger Lexicon Is Configuration, Not Code

## Status

Proposed

## Category

Technical

## Context

WF-ADR-0016 added lexical difficulty features (`reasoning_term_count`,
`constraint_term_count`, plus math/question scans). Its amendment shipped them at
**weight 0.0** — opt-in, off by default — because a cross-provider double-blind test
(`benchmarks/blind-eval.md`) showed a curated keyword list detects the *author's*
vocabulary, not difficulty in general: it fired on only ~20% of independently authored
hard prompts and lost to a length baseline.

That makes the opt-in only half usable. The **weights** are already configuration —
`routing.weights.reasoning_term_count` in the TOML, parsed by `config.py:_parse_weights`
and round-tripped by `dump_routing_toml`. But the **lexicon itself** is hardcoded
frozensets in `complexity.py` (`_REASONING_TERMS`, `_CONSTRAINT_TERMS`). A user can turn
the dial up, but cannot change *which words* turn it. So "calibrate to your own traffic"
is impossible past reweighting a vocabulary that is, by construction, the author's.

The honest resolution to a vocabulary-specific signal is to let each deployment curate
its own vocabulary. The words must therefore become data the user owns, not constants
in the library.

## Decision

Move the trigger lexicon from module-level constants into `RoutingConfig`, defaulting
to today's built-in sets, and thread it through scoring, config, and the UI.

1. **Lexicon as config data.** `RoutingConfig` gains a `lexicon` — the reasoning and
   constraint term sets — defaulting to the current built-ins (a new
   `DEFAULT_LEXICON`). A `[routing.lexicon]` TOML table (`reasoning_terms = [...]`,
   `constraint_terms = [...]`) is parsed by a new `_parse_lexicon` sibling to
   `_parse_weights`, validated (lists of non-empty strings, lower-cased, a sane size
   cap), and emitted by `dump_routing_toml`.
2. **Threaded into scoring, one extraction path.** `extract_features` gains an optional
   `lexicon` argument defaulting to `DEFAULT_LEXICON`; `score_complexity` passes
   `config`'s lexicon down. The function stays pure and deterministic — the lexicon is
   injected, not read from a global. Raw counts and `FEATURE_ORDER` are unchanged.
3. **Scope: word lexicons only.** Only `reasoning_terms` and `constraint_terms` (curated
   vocabulary) become configurable. `math_symbol_count` (a Unicode/LaTeX regex) and
   `question_count` (`?` count) are not vocabulary a user curates and stay in code.
4. **Off by default is unchanged.** The default lexicon is the built-in set *and* the
   default weights stay 0.0. Configuring words does nothing until the user also raises
   the weight — opt-in is preserved end to end.
5. **UI (WF-ADR-0005).** The operator console's **Configure** screen edits the TOML, so
   lexicon editing is available the moment it is in the schema. The high-value
   affordance lands in the **Explain/Playground** screen: paste a prompt, highlight
   *which* words fired, add/remove a word, and watch the score move — writing back to
   `[routing.lexicon]`. Managing vocabulary is an operator task; it lives in the console,
   not the per-conversation chat slider.
6. **Honest framing guardrail.** Every surface frames this as "trigger words for *your*
   traffic — Wayfinder counts these; you decide they signal complexity," never as a
   general difficulty detector. The blind-test caveat (`benchmarks/blind-eval.md`) is
   linked from the screen.

The JSON output contract (`to_dict`, `schema_version "3"`) is **unchanged** — same
feature keys, same shape. Only the *config* contract grows, additively.

Stays inside the WF-ADR-0001 boundary: a user wordlist is still pure text, scanned
deterministically, offline, with no model call.

## Consequences

### Positive

- The opt-in becomes genuinely usable: a deployment curates the vocabulary that signals
  difficulty *in its domain*, which is the only form of the feature the double-blind
  test supports.
- The feature's weakness (vocabulary-specificity) becomes a UI affordance (curate your
  own), and the decision stays fully inspectable and deterministic.
- "Which words fired" closes the visibility loop (WF-ADR-0014) and makes the signal
  debuggable.

### Negative

- The config contract grows and `extract_features` gains a parameter (additive; the
  default preserves current behavior and every existing call site).
- A user lexicon is unbounded text input; mitigated by validation (string lists,
  lower-cased, length and size caps) and the WF-ADR-0001 reading that artifact/config
  content is untrusted and bounded at parse time.

### Risks

- Re-implying difficulty detection. Mitigation: the framing guardrail and the linked
  blind-test caveat; weights stay 0.0 by default.
- A pathologically large lexicon slowing the scan. Mitigation: a size cap and the
  existing per-decision latency budget; the scan is a set membership test per token.

## Alternatives Considered

### Extend-only (append to the built-ins, cannot remove)

Smaller change, but it keeps the author's vocabulary baked in — the exact thing that did
not generalize. Rejected: it does not deliver "calibrate to your own traffic."

### Keep the lexicon in code, ship better defaults

There is no universal hard-word list; the blind test is the evidence. Rejected.

### Learn the lexicon from labeled data during `calibrate`

Tempting and future-compatible, but it is a separate, larger capability (term selection
from a corpus) and risks overfitting a small label set. Out of scope here; this ADR only
makes the lexicon *editable*. A later ADR may add *learning* it.

## Related Decisions

- WF-ADR-0016 (lexical signals; amended to opt-in/off by default)
- WF-ADR-0005 (local UI surface)
- WF-ADR-0014 (routing visibility surface)
- WF-ADR-0001 (no-model-call boundary)
