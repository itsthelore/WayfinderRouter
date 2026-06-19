---
schema_version: 1
id: WF-ROADMAP-0003
type: roadmap
tags: [v0.3.0, scoring, lexicon, config, ui]
---

# Roadmap: Configurable lexicon (v0.3.0)

## Status

Planned

## Context

v0.2.0 shipped the lexical difficulty features off by default (WF-ADR-0016 amendment):
a cross-provider double-blind test showed a curated keyword list detects the author's
vocabulary, not difficulty in general. The feature is opt-in, but only half usable — the
weights are configuration while the trigger words are hardcoded in `complexity.py`. A
deployment can dial the signal up but cannot change which words turn it.

WF-ADR-0019 decides the fix: the trigger lexicon becomes configuration the user owns,
editable from the operator console, so "calibrate on your own traffic" finally applies
to the words themselves — the only form of the feature the blind test supports.

## Outcomes

- The reasoning and constraint term lists live in `[routing.lexicon]`, default to the
  built-ins, and round-trip through the config loader and `dump_routing_toml`.
- The scorer reads the active lexicon from `RoutingConfig`; default behaviour (and the
  0.0 default weights) is unchanged.
- The operator UI lets a user see which words fired on a prompt and add/remove trigger
  words, with honest framing (your vocabulary, not a difficulty model).

## Initiatives

Sequenced so the contract lands before the UI consumes it.

### Initiative 1 — Lexicon as config (`wayfinder-router` v0.3.0, WF-ADR-0019)

`DEFAULT_LEXICON` plus a `lexicon` on `RoutingConfig`; `extract_features` takes an
optional lexicon (default = built-ins) and `score_complexity` threads `config`'s lexicon
through. A `_parse_lexicon` sibling to `_parse_weights` parses and validates
`[routing.lexicon]`; `dump_routing_toml` emits it. The JSON output contract is unchanged;
only the config contract grows, additively. Behaviour-preserving by default.

### Initiative 2 — Lexicon management in the console (`wayfinder-router` v0.3.0, WF-ADR-0019)

The **Configure** screen edits the lexicon as soon as it is in the schema. The
**Explain/Playground** screen highlights which words fired on a pasted prompt and lets
the user add/remove a word live, writing back to `[routing.lexicon]`. Read-only views
stay metadata-only (no prompt text persisted), consistent with WF-ADR-0014. The screen
links the `benchmarks/blind-eval.md` caveat so the framing stays honest.

## Constraints

- Inside the WF-ADR-0001 boundary: deterministic, offline, no model call. A user wordlist
  is untrusted, bounded config input — validated and size-capped at parse time.
- Off-by-default is preserved: default weights stay 0.0; configuring words alone changes
  nothing until a weight is raised.
- No new runtime dependency; the UI stays behind the `[ui]` extra.

## Related Decisions

- WF-ADR-0019 (the trigger lexicon is configuration, not code)
- WF-ADR-0016 (lexical signals, amended to opt-in)
- WF-ADR-0005 (local UI surface)
- WF-ADR-0014 (routing visibility surface)
