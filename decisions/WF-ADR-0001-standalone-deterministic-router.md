---
schema_version: 1
id: WF-ADR-0001
type: decision
tags: [foundation, routing, independence]
---

# WF-ADR-0001: Standalone Deterministic Router

## Status

Accepted

## Category

Architecture

## Context

Wayfinder began life as `rac route`, an exploration inside the
`requirements-as-code` (RAC) repository: a deterministic, structural
prompt-complexity scorer that recommends a local or cloud model. RAC's own
decisions concluded the capability did not belong there — routing is a runtime
*inference* concern, divergent from RAC/Lore's recorded-knowledge product line
(RAC ADR-069 records the split; RAC ADR-068 pins the routing boundary). Wayfinder
is the product that capability becomes.

The defining question for this repository is the dependency direction. A prompt
router that required users to install a requirements-as-code knowledge engine
would be absurd: the audiences barely overlap. So the foundational choice is
whether Wayfinder consumes RAC or stands alone.

## Decision

Wayfinder is a standalone, deterministic prompt router with **zero runtime
dependency on RAC**.

- Wayfinder does not import `rac`, depend on the `requirements-as-code` package,
  or read RAC's `.rac/` config namespace. It owns its own config file,
  `wayfinder.toml`.
- The scorer is pure, offline structural scanning of the prompt text (length,
  headings, instruction steps, links, code blocks, tables), normalized to a
  bounded `0.0–1.0` score and compared to a configurable threshold. Stdlib only;
  no model, key, or network.
- The two small things RAC provided are generic and reimplemented here, not
  borrowed: stripping a leading `---` frontmatter block (a ~17-line, import-free
  function) and a config-file walk-up (~10 lines, pointed at `wayfinder.toml`).
- The **core** recommends; it never invokes a model, selects a provider, reads a
  credential, or tokenizes per a vendor model. The caller runs inference. (This
  is RAC ADR-068's boundary, carried over intact.)

  *Amended (WF-ADR-0004):* this prohibition is scoped to the **deterministic
  core** (`complexity`, `config`, `calibrate`). A separate, optional invocation
  layer — an in-process invoker and an OpenAI-compatible gateway behind the
  `wayfinder[gateway]` extra — may hold a bring-your-own key and call the chosen
  model. Keys live only in that layer (never in `wayfinder.toml`, never in the
  scored path), so the core stays pure, offline, and golden-tested. See
  WF-ADR-0004.
- The score is a structural *proxy*, not a verdict on prompt difficulty or a
  guarantee of model capability; calibrating the threshold to real local/cloud
  capability is the caller's responsibility.

The relationship to RAC is heritage only: prototyped there, scoring shape
inspired by RAC's `classification.py` (`points / ceiling`).

## Consequences

### Positive

- Wayfinder installs and runs with no RAC present and no `.rac/` on the path —
  usable by anyone running local + cloud models.
- The router stays reproducible, testable, and free: no model call to decide
  whether to make a model call.
- A clean product boundary: Wayfinder's concerns never leak back into RAC, and
  RAC's knowledge model never constrains Wayfinder.

### Negative

- ~30 lines of generic utility (frontmatter strip, config walk-up) are
  reimplemented rather than shared. Accepted deliberately: sharing them would
  re-couple the audiences the split exists to separate, for trivial, stable code.

### Risks

- The structural score is mistaken for a capability verdict. Mitigation: output
  reports the contributing features and the threshold, and this ADR frames the
  score as a proxy the caller calibrates.

## Alternatives Considered

### Depend on the published `requirements-as-code`

Reuse RAC's `split_frontmatter` and config discovery via the published package.

#### Disadvantages

- Forces a prompt-routing audience to install a knowledge engine, contradicting
  the reason Wayfinder was split out at all (RAC ADR-069).

### Keep it as `rac route` inside RAC

Ship routing as a permanent RAC command.

#### Disadvantages

- Bloats a knowledge product with a runtime-inference concern and muddies RAC's
  "no inference in the core" claim — the reason RAC ADR-069 moved it out.

Full independence is selected.

## Success Measures

- `pip install wayfinder` and a single prompt yield a deterministic recommendation
  with no RAC installed and no `.rac/` directory anywhere on the path.
- The same prompt and threshold produce byte-identical output across runs and
  across the CLI and the Python API.
- No Wayfinder code path imports `rac`, reads `.rac/`, or invokes a model.
