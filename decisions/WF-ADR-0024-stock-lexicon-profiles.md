---
schema_version: 1
id: WF-ADR-0024
type: decision
tags: [lexicon, profiles, tuning, demo, gateway]
---

# WF-ADR-0024: Stock Lexicon Profiles

## Status

Accepted

## Category

Technical

## Context

The lexical signals can now be tuned live in the demo (WF-ADR-0023), but the only starting
vocabularies we shipped were `benchmarks/seed/domain-lexicons.toml` — RouterBench-mined term lists
that live in the benchmark tree, are copied by hand, and are honestly *mixed quality* (science /
general / humanities surface real subject vocabulary; math / commonsense / multilingual surface
RouterBench's word-problem task nouns, not difficulty). The built-in `DEFAULT_LEXICON` is a decent
hand-authored reasoning/constraint set but ships off and isn't presented as a choosable starting
point. So a user tuning the lexicon faces a blank textarea, and the one set of "stock" lists isn't
packaged with the library or selectable anywhere.

## Decision

Ship a small set of **stock lexicon profiles** as packaged library data, each labelled by
provenance and quality, loadable in the demo to pre-fill the term lists.

1. **Packaged, not benchmark-coupled.** `wayfinder_router/profiles.py` defines a frozen
   `LexiconProfile` (`id`, `name`, `source`, `reasoning_terms`, `constraint_terms`, `note`) and a
   `PROFILES` tuple. No dependency on `benchmarks/`; the mined lists are embedded (with a reproduce
   command in the docstring). Terms are single lowercase word tokens, matching the scorer's tokenizer.
2. **Two honest provenances.**
   - `curated` — hand-authored, defensible domain vocabulary (Proofs & mathematics, Law &
     compliance, Code & infrastructure, Science & medicine). Each `note` says it is hand-authored and
     *unvalidated* — a head-start, not a validated router.
   - `mined` — the RouterBench domains, each `note` stating whether it is real subject vocabulary or
     (for math / commonsense / multilingual) task-surface vocabulary kept as a *cautionary example*.
3. **Read-only surface.** `GET /router/profiles` returns the profiles as JSON. The demo's Advanced
   panel adds a "starter profile" dropdown (grouped Curated / RouterBench-mined) that fills the
   reasoning/constraint term lists, turns the lexical signal on, and shows the profile's note — then
   you tune and Export config (WF-ADR-0023).
4. **Honest framing is load-bearing.** Profiles are starting points; the copy and every `note` repeat
   that you must calibrate on your own labels, and the WF-ADR-0016 caution (lexical detects vocabulary,
   not difficulty) still governs. We deliberately did *not* launder the weak mined lists into
   polished, unlabelled "profiles."

## Consequences

### Positive

- Tuning starts from a sensible, domain-relevant vocabulary instead of a blank box; the lexicon
  feature is finally discoverable and usable end-to-end (pick → tune → export).
- The mixed-quality mined lists become a teaching tool (you can *see* why "candy, sunday" is a bad
  signal) rather than a hidden trap.
- Stdlib-only, static data; no benchmark dependency at runtime; one read-only endpoint, off the
  scored path.

### Negative

- Curated profiles are unvalidated by construction — a reasonable head-start, but if treated as
  finished routers they will under/over-fire. Mitigated by the framing and the export-to-calibrate path.
- Another small surface to maintain as vocabularies drift.

### Risks

- Users ship a profile as-is without calibrating. Mitigated by per-profile notes, the Advanced copy,
  and lexical signals remaining off until explicitly weighted.

## Alternatives Considered

- **Mined-only (promote the seed file).** Real provenance, but several lists mislead; rejected as the
  sole offering.
- **Curated-only.** Clean but entirely unvalidated, against the project's show-the-numbers ethos;
  rejected as the sole offering. Chosen approach ships both, labelled.
- **Leave lexicons as copy-from-seed / mine-your-own.** The status quo; rejected because it leaves the
  lexicon undiscoverable and the demo's tuning starting from nothing.

## Related Decisions

- WF-ADR-0023 (live tuning + export this seeds), WF-ADR-0019 (configurable lexicon),
  WF-ADR-0016 (lexical ships off; the caveats that still apply), WF-ADR-0020 (the demo)
- benchmarks/seed/domain-lexicons.toml (the mined source), docs/lexical-routing.md
