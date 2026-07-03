---
schema_version: 1
id: WF-ADR-0044
type: decision
tags: [scoring, calibration, onboarding, cli, doctor, quality, default]
---

# WF-ADR-0044: Move the zero-config default off the inert 0.5 cut

## Status

Proposed

## Category

Technical

## Context

The single least flattering finding of WF-ROADMAP-0010's Phase Q is that the
shipped zero-config router does not route. `DEFAULT_THRESHOLD = 0.5`
(`complexity.py`) is off the top of the operating curve: on
`benchmarks/dataset.jsonl` (24 rows) the default cut sends **0%** of prompts to
cloud, recovers **0.00** of the local→cloud quality gap (PGR), and is
**byte-for-byte identical to always-local**. A trivial "≥10 words → cloud"
length rule beats it (PGR 0.67). This is not conservatism; it is inertness.

The cause is arithmetic, not a bug. `scalar_score` is `Σ(weight·normalized) /
Σweights`, and the default weights sum to 11.0, so any one structural feature
contributes at most `weight/11`. To clear 0.5 a prompt must saturate word_count
*and* pile on headings, lists, and code fences — a long, heavily formatted
document. Ordinary chat and coding prompts live in the 0.00–0.20 band. The
entire useful operating range on this mix is `t ≈ 0.01–0.20`; the knee (max
PGR × cost_savings) sits at **t = 0.02** with PGR 0.60. The default is ~0.48
above that knee.

The `init` presets already know better — every preset writes `threshold = 0.08`
(or `min_score = 0.08`), near the knee. So there are effectively two defaults:
the *generated* config routes, the *library zero-config* default and every
hand-written README example teach the dead 0.5. A user who runs `init` is
quietly rescued; a user who reads the docs and hand-writes a config, or who
kicks the tires with `route`/`chat --dry-run`/the webchat demo, sees `● LOCAL`
on nearly everything and concludes the product never routes.

Under WF-ADR-0043 the 0.5 default is frozen contract — the rebuild had to
reproduce it byte-identically and could not touch it. Changing it is therefore
a decision that supersedes settled bytes, which is what this ADR is for.

## Decision

Give the zero-config experience a cut that actually routes, without pretending
24 rows license a shipped magic number. Two mechanisms, sequenced:

1. **Init-time calibration against a bundled mini-dataset (primary).** Ship a
   small labeled routing dataset *inside the package* (today the only labeled
   data lives in the unshipped `benchmarks/` tree) and have `init` run
   `calibrate --objective knee` against it, writing the derived knee threshold
   in place of the hard-coded 0.08. Offer it interactively: "detected knee =
   0.04; write it? [Y/n]". The generated config becomes calibrated rather than
   guessed, and the recommended cut is single-sourced instead of duplicated
   across three preset TOML literals.

2. **A defensible shipped default and a doctor self-check (secondary).**
   Either move the *library* `DEFAULT_THRESHOLD` off 0.5 to a value inside the
   measured useful band, or — the softer path — keep the library constant and
   have `doctor`/`route` surface the inertness: `doctor` scores the bundled
   mini-dataset with the user's actual config and warns when cloud-share is 0%
   or 100% ("routing: cloud share 0% on the built-in probe — your threshold
   (0.50) may be inert; run `calibrate`, suggested knee ≈ 0.04"). This closes
   the "doctor passes clean on a dead router" gap without moving a frozen byte.

The library `DEFAULT_THRESHOLD` constant, the `route`/`doctor` "≥0.50 cloud"
strings, and the preset TOML bytes are all pinned by tests; this ADR owns
whichever of those it changes and re-baselines the corresponding golden
assertions. It does **not** change scored numerics — the scorer is untouched;
only the *chosen operating point* and first-run bytes move — so it is
independent of the parity/scorer freeze (that is WF-ADR-0045's territory).

## Consequences

- **Positive.** The out-of-box product does its one job. Projected default-path
  routing quality on `benchmarks/dataset.jsonl` moves from **PGR 0.00 → ~0.60**
  at the structural knee (t≈0.02), quality **0.375 → 0.75**, cloud-share
  **0% → ~54%**, at the cost of dropping cost-savings from 0.80 (which is the
  savings of never routing) to ~0.37. Falsifiable against the harness (see
  Success Measures).
- **Positive.** `doctor` gains a routing-quality verdict, so a dead cut is
  caught on first run instead of never.
- **Negative / honest.** `init`-time calibration re-routes traffic on upgrade
  for anyone who regenerates a config; a moved library default re-routes
  everyone on `pip install -U`. This is a behavior change and needs a minor
  version bump and a loud changelog, not a silent default swap.
- **Negative.** A calibrated default is only as good as the bundled dataset.
  Shipping a mini-dataset that shares an author with the scorer risks the same
  self-flattering bias WF-ADR-0016's double-blind exposed; the bundled set must
  be small, documented, and understood as a *sane starting cut*, not a
  guarantee.
- **Small-sample caveat (governing).** 24 rows can prove the 0.5 default is
  inert (deterministic, reproduced exactly) and that a useful band exists — it
  **cannot** set a trustworthy shipped number. Held-out calibration on these
  rows carries PGR sd 0.16–0.31 on ~12-row test splits. The projected 0.60 is a
  direction and a mechanism, not a confidence-bounded magnitude; a shippable
  default needs the RouterBench-scale, held-out, cross-provider evaluation the
  `calibrate` driver is already built for.

## Alternatives Considered

- **Leave 0.5 and rely on `init` alone.** Rejected: the default a user is most
  likely to *read* (README, `route` sample) is the broken one; the one they
  *generate* is good. Fixing only the generated path leaves the documented path
  teaching an inert value the project's own benchmark refutes.
- **Hard-code a new magic threshold (e.g. 0.05) as the library default.**
  Rejected as the primary path: it trades one unjustified constant for another.
  Calibration against a bundled set at least derives the cut from data and
  single-sources it. A moved constant is acceptable only as the secondary,
  version-bumped story with a defensible number behind it.
- **Ship a classifier by default.** Rejected here: it additionally changes the
  emitted config shape and collapses into the WF-ADR-0045 supersede (it fits its
  own feature weights). Out of scope for a threshold/onboarding ADR.

## Success Measures

- A config generated by the new `init` path scores **PGR ≥ 0.55** on
  `benchmarks/dataset.jsonl` (vs 0.00 for a 0.5 config); fail the item if
  PGR < 0.40. Prove by running the harness before/after:
  `python -m benchmarks.run` on the generated config vs a 0.5 config.
- A config with `threshold = 0.5` produces a non-empty `doctor` inert-cut
  warning; a knee config produces none. Binary and testable.
- No scored decision changes: `python tools/golden.py` byte-identical, JS
  parity green (this ADR must not perturb the scorer).
- The bundled mini-dataset ships in the wheel (`pyproject.toml` packages) and a
  user can reproduce the knee from an install.

## Related

- WF-ADR-0043 (froze the 0.5 default and the `route`/`doctor` bytes this
  supersedes)
- WF-ADR-0017 (cost-aware `calibrate --objective knee` — the machinery reused)
- WF-ADR-0016 (lexical-off — the self-flattering-dataset lesson this heeds)
- WF-ADR-0045 (versioned scorer / lexical default — the quality lever this ADR
  deliberately does *not* pull, to keep the scorer frozen)
- WF-ROADMAP-0010 (Phase Q measurements and the Phase X staging)
