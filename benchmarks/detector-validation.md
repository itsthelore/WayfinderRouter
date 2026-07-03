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
([`ai4privacy-validation-results.md`](ai4privacy-validation-results.md)). The first external
run exposed two real problems, which we then fixed (see *Acting on the findings* below);
current numbers:

| detector | precision | recall | first external run | hand-built |
| --- | --- | --- | --- | --- |
| email | **0.997** | **1.000** | 0.997 / 1.000 | 0.875 / 0.875 |
| us_ssn | 1.000 | 0.255 | 1.000 / 0.255 | 0.600 / 0.600 |
| credit_card | **0.474** | 0.025 | 0.142 / 0.129 | 0.833 / 0.833 |

- **email is externally confirmed excellent** — 4,043 emails, 12 false positives, 0 misses
  across 43k records. Genuinely `block`-eligible.
- **us_ssn is perfectly precise but US-format-only** — by design. Recall is 0.255 because
  only ~29% of AI4Privacy's SSN values are `ddd-dd-dddd` (many are undashed or not even
  valid 9-digit US SSNs). It is a *US-format* detector, reliable where it fires; we keep it
  US-specific rather than dilute its precision, and simply don't claim international coverage.
- **credit_card's precision problem was real and is now much reduced.** The first run's
  0.142 came from `(?:\d[ -]?){13,19}` + Luhn firing on account numbers and IBANs that pass
  Luhn by chance; adding an issuer-prefix (IIN) check lifted precision to **0.474** (false
  positives 1,990 → 72). Its AI4Privacy *recall* (0.129 → 0.025) is not a fair measure — only
  11% of the dataset's synthetic "cards" are even Luhn-valid and fewer carry a real issuer
  prefix, so both gates correctly reject them; real cards (always Luhn- and IIN-valid) are
  not what this recall reflects.

Micro precision across the PII detectors rose **0.71 → 0.98** after the fix.

### Secret detectors vs gitleaks (`gitleaks_crosscheck.py`)

Secrets aren't PII, so the secret detectors are grounded against the community-standard
[gitleaks](https://github.com/gitleaks/gitleaks) ruleset
([`gitleaks-crosscheck-results.md`](gitleaks-crosscheck-results.md)):

- **github_pat matches the community standard exactly** — same regex, full agreement.
- **aws_access_key now matches gitleaks** — broadened from `AKIA`-only to
  `AKIA|ASIA|ABIA|ACCA|A3T` with the base32 charset, so it catches AWS temporary/STS keys.
  Cross-check agreement went 2/3 → **3/3**.
- **slack_token was tightened** from a loose `xox?-<anything>` to the digit-led structure
  real tokens have (`xox?-<digits>-<body>`), dropping the loose false-fire; it still diverges
  from gitleaks' many per-type Slack rules and stays a `warn`-tier signal.
- **private_key stays header-based on purpose** — a DLP gate should catch a *pasted* key
  even when truncated, so it favors recall. That makes it `warn`/`redact`-tier (a header
  quoted in prose is a rare, tolerable false positive), not auto-`block`.
- `high_entropy_hex` has no gitleaks counterpart (gitleaks detects generic secrets by
  entropy) and cannot separate a secret from a git SHA / md5 — so it is a `log`-tier
  advisory signal only.

### Acting on the findings

The external runs drove four concrete changes to `benchmarks/detectors.py`, each re-measured
above: **credit_card** gained an IIN issuer-prefix check (precision 0.14 → 0.47);
**aws_access_key** was broadened to the gitleaks prefix set (agreement 2/3 → 3/3);
**slack_token** was tightened to a digit-led structure. And three detectors got explicit
*scoping* decisions rather than code changes, because their "fix" is a real precision/recall
trade, not a bug: **us_ssn** stays US-format-only (precise, not international),
**private_key** stays header-based (recall-favoring, `warn`-tier), **high_entropy_hex**
stays a `log`-tier advisory (a hash is indistinguishable from a secret by shape alone).

### Net guidance for Initiative 1

`email` and `github_pat` are `block`-eligible (externally validated / community-matched).
`aws_access_key` now matches the standard and can `block`. `credit_card` improved to 0.47
precision — `redact`/`warn`, not yet a clean `block`. `us_ssn` is precise-but-US-only
(`redact`/`warn`). `private_key` is `warn`/`redact`-tier (header-based by choice);
`high_entropy_hex` is `log`-only. The standing caveat holds even against external data:
AI4Privacy is synthetic and templated, not real traffic — the honest endpoint is still
on-your-own-traffic measurement, not any single public number.

*Reproduce:* `python -m benchmarks.ai4privacy_validation` (needs `pip install datasets`;
downloads the dataset, nothing committed) and `python -m benchmarks.gitleaks_crosscheck`
(fetches `config/gitleaks.toml`). Same dataset revision / ruleset → same numbers.
