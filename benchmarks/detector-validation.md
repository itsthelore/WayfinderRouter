# Detector validation: how good are the deterministic secret/PII detectors?

WF-ROADMAP-0011's policy engine gates traffic on detectors — `block` a prompt carrying a
credential, `redact` a card number, `warn` on an email. Those verbs are only as
trustworthy as the detectors behind them, and the whole bet (shared with
WF-ROADMAP-0008's `pii_patterns`) is that *deterministic* regex detection has usable
precision/recall — no model, no egress (WF-ADR-0001, WF-ADR-0043). This benchmark
measures that bet before the policy engine is built, in the `blind-eval.md` register:
whatever the numbers are, they're published, and they decide which verb each detector can
safely drive.

## Method

A reference detector set (`benchmarks/detectors.py`) — email, US SSN, AWS access key,
GitHub PAT, Slack token, PEM private-key header, credit card (regex **plus a Luhn
checksum**), and a high-entropy-hex proxy — runs over a labeled corpus
(`benchmarks/detector-corpus.jsonl`) where each item carries the secret/PII types it
actually contains. For each detector, `benchmarks/detector_validation.py` computes
**precision** (of what fired, how much was real), **recall** (of the real secrets, how
much fired), and F1, plus micro (pooled) and macro (mean-of-detectors) rollups.

The corpus is built to be adversarial on purpose: alongside true positives it contains
**lookalikes** (an invoice number shaped `ddd-dd-dddd`, a git SHA, a Luhn-valid tracking
number) that *should not* be flagged, and **format variants** (an undashed SSN, a
dotted card number, an obfuscated `jane [at] example [dot] com`) that a real secret hides
in. Those are where precision and recall break, which is the point. No item contains a
real credential — keys use vendors' documented example values, cards use published test
numbers, SSNs use invalid ranges.

One honest wrinkle a secrets benchmark cannot avoid: **it can't commit literal
live-looking provider tokens**, because secret-scanning push protection (rightly) blocks
them. So the distinctive-token positives (AWS keys, GitHub PATs, Slack tokens) are
assembled from fragments at runtime in `benchmarks/detector_corpus.py` — the committed
source never holds a contiguous token — while everything else stays in the reviewable
`detector-corpus.jsonl`. The two sources are one corpus (`full_corpus`); a test asserts
the JSONL contains no scanner-matchable prefix.

The harness core is pure and dependency-free; its arithmetic is golden-tested with
planted items whose confusion counts are hand-checkable
(`tests/test_detector_validation.py`). Same corpus + same detectors → byte-identical
output.

## Running it

```sh
python -m benchmarks.detector_validation \
    --corpus benchmarks/detector-corpus.jsonl \
    --out benchmarks/detector-validation-results.md \
    --out-json benchmarks/detector-validation-results.json
```

## Results

Over the 49-item reference corpus (full table:
[`detector-validation-results.md`](detector-validation-results.md), machine copy
[`.json`](detector-validation-results.json)):

| detector | precision | recall | F1 |
| --- | --- | --- | --- |
| aws_access_key | 1.000 | 1.000 | 1.000 |
| github_pat | 1.000 | 1.000 | 1.000 |
| slack_token | 1.000 | 1.000 | 1.000 |
| private_key | 1.000 | 1.000 | 1.000 |
| email | 0.875 | 0.875 | 0.875 |
| credit_card | 0.833 | 0.833 | 0.833 |
| us_ssn | 0.600 | 0.600 | 0.600 |
| high_entropy_hex | 0.500 | 1.000 | 0.667 |

Micro precision 0.812 / recall 0.867; macro precision 0.851 / recall 0.914. The shape is
the finding, and it splits cleanly into three tiers:

- **Distinctive-token detectors are effectively solved by regex.** AWS keys, GitHub PATs,
  Slack tokens, and PEM headers carry a unique prefix, so precision *and* recall are 1.000
  — a `AKIA…` string is a key and nothing else is. These are safe to drive a hard `block`.
- **Structured-but-ambiguous PII trades precision away.** `us_ssn` lands at 0.600
  precision: any `ddd-dd-dddd` string trips it, so an invoice number and a part number
  read as SSNs (false positives), while an undashed `123456789` SSN is missed (false
  negative). `high_entropy_hex` has perfect recall but 0.500 precision: a git SHA and an
  md5 digest look exactly like a secret. These detectors **must not drive a hard `block`**
  — over-blocking legitimate traffic is the failure mode — and belong on `redact`/`warn`/
  `log`, or need a context check before they escalate.
- **Format-flexible detectors lose a little to variants.** `email` (0.875/0.875) and
  `credit_card` (0.833/0.833, Luhn included) miss dotted/obfuscated forms and false-fire on
  a retina-image filename and a Luhn-valid tracking number — middling, verb-dependent.

**What it means for the roadmap.** (1) Per-detector precision maps directly to the policy
verb a detector may safely drive — this benchmark is the input to WF-ROADMAP-0011
Initiative 1's verb mapping, and the per-release gate should be a **precision floor for
`block`-eligibility** (distinctive-token detectors clear it; SSN/hex do not). (2) It
empirically confirms the roadmap's "no detection-completeness guarantee" non-goal — recall
is not 1.000 for the flexible detectors, by construction. (3) Detection stays deterministic
and local (WF-ADR-0001/0043); the honest answer to the hard classes is verb choice and
on-traffic tuning, not an ML classifier in the request path.

*Caveat, stated plainly:* this is a small, hand-built corpus (49 items) — the numbers are
**illustrative floors that exercise the failure modes, not population estimates**, and the
external validation below revises two of them sharply. The detectors here are a reference
set under `benchmarks/`, the empirical starting point for Initiative 1 — not yet a product
component.

## External validation

The corpus above is ours — we wrote both the lookalikes and the detectors — so the numbers
can flatter. These two checks re-measure against data and rulesets **we did not author**,
and the result is a useful correction.

### PII detectors vs AI4Privacy (`ai4privacy_validation.py`)

Same detectors, same meter, run over
[AI4Privacy pii-masking-200k](https://huggingface.co/datasets/ai4privacy/pii-masking-200k)
— 43,501 English records with independently-labeled PII spans
([`ai4privacy-validation-results.md`](ai4privacy-validation-results.md)):

| detector | precision | recall | hand-built was |
| --- | --- | --- | --- |
| email | **0.997** | **1.000** | 0.875 / 0.875 |
| us_ssn | 1.000 | **0.255** | 0.600 / 0.600 |
| credit_card | **0.142** | 0.129 | 0.833 / 0.833 |

- **email is externally confirmed excellent** — 4,043 emails, 12 false positives, 0
  misses across 43k records. This one is genuinely `block`-eligible.
- **us_ssn is perfectly precise but US-format-only.** Recall falls to 0.255 because just
  ~29% of AI4Privacy's SSN values are `ddd-dd-dddd` (many are undashed or not even valid
  9-digit US SSNs). Honest reading: it is a *US-format* SSN detector, reliable where it
  fires, and must not be sold as international coverage.
- **credit_card fails here, and the two halves need separating.** The precision collapse to
  0.142 is **real**: `(?:\d[ -]?){13,19}` + Luhn fires on account numbers and IBANs that
  are 13–19 digits and pass Luhn by chance. The recall of 0.129 is partly an **artifact** —
  only 11% of AI4Privacy's synthetic "card" values are Luhn-valid at all (real cards always
  are), so the Luhn gate correctly rejects the other 89%; recall on genuine cards would be
  higher. Either way, the precision failure alone disqualifies `credit_card` from any
  auto-`block` until it is tightened.

The headline: **external data cut the hand-built us_ssn recall (0.60 → 0.26) and
credit_card precision (0.83 → 0.14) hard.** The self-authored corpus was optimistic exactly
where it mattered — which is the case for demanding external validation in the first place.

### Secret detectors vs gitleaks (`gitleaks_crosscheck.py`)

Secrets aren't PII, so the secret detectors are grounded against the community-standard
[gitleaks](https://github.com/gitleaks/gitleaks) ruleset — a per-pattern comparison
([`gitleaks-crosscheck-results.md`](gitleaks-crosscheck-results.md)):

- **github_pat matches the community standard exactly** — same regex, full agreement.
- **aws_access_key is narrower than gitleaks** (`AKIA` only vs `AKIA|ASIA|ABIA|ACCA|A3T`),
  so it misses AWS temporary/STS keys — a recall gap to close in Initiative 1.
- **private_key is broader than gitleaks** — it fires on a bare PEM *header* while gitleaks
  requires the key body, so it over-fires (a header in prose isn't a leaked key).
- **slack_token is a loose approximation** — it does not match gitleaks' structured
  `slack-bot-token` shape, so it will both miss and over-fire relative to the standard.
- `high_entropy_hex` has no direct gitleaks counterpart (gitleaks uses entropy-based
  generic detection) — consistent with its 0.500 precision above.

### Net guidance for Initiative 1

`email` is `block`-eligible (externally validated). `github_pat` is solid. `aws_access_key`
needs the broader prefix set, `private_key` a body check, `slack_token` the structured
pattern — concrete, community-grounded fixes. `us_ssn` is precise-but-US-only (`redact`/
`warn`, no international claim); `credit_card` is **not usable as-is** and must be tightened
before any verb. And the standing caveat holds even against external data: AI4Privacy is
synthetic and templated, not real traffic — the honest endpoint is still on-your-own-traffic
measurement, not any single public number.

*Reproduce:* `python -m benchmarks.ai4privacy_validation` (needs `pip install datasets`;
downloads the dataset, nothing committed) and `python -m benchmarks.gitleaks_crosscheck`
(fetches `config/gitleaks.toml`). Same dataset revision / ruleset → same numbers.
