"""A reference secret/PII detector set — deterministic regex, for WF-ROADMAP-0011 §6.

The governance plane's policy engine (WF-ROADMAP-0011 Initiative 1) rests on the
assumption that *deterministic* detectors have usable precision/recall on secrets and
PII — the same bet WF-ROADMAP-0008's ``pii_patterns`` makes. Detectors do not exist in
the product yet, so this module is a **reference set living under ``benchmarks/``**, not
a product commitment: enough real detector shapes to measure the assumption honestly
(``benchmarks/detector_validation.py``) and hand Initiative 1 an empirical floor to
start from, in the ``blind-eval.md`` register.

Each :class:`Detector` is a name, a compiled pattern, and an optional per-match
``validator`` (e.g. the Luhn checksum for card numbers) — so a detector can be *pattern
plus a cheap deterministic check*, never a model. Pure stdlib, no network, no keys, in
keeping with the offline core (WF-ADR-0001) and WF-ADR-0043 (Wayfinder's own logic never
calls out).

Nothing here contains a real credential: keys use vendors' documented example values
(e.g. ``AKIAIOSFODNN7EXAMPLE``), cards use published test numbers, SSNs use invalid
ranges, and the private-key example is a bare header.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


def _luhn_ok(candidate: str) -> bool:
    """True iff the digits in ``candidate`` (13–19 of them) pass the Luhn checksum.

    The check is what separates a card number from any 16-digit string, so it is the
    detector's precision lever — and its limit: a Luhn-valid number in a non-card context
    still passes (an honest false positive the benchmark reports).
    """
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_card(candidate: str) -> bool:
    """Luhn-valid *and* a recognized card-issuer (IIN) prefix + length.

    Requiring a real issuer prefix is the precision fix the AI4Privacy run demanded
    (``benchmarks/detector-validation.md``): account numbers and IBANs pass Luhn by chance
    but rarely start with a Visa/Mastercard/Amex/Discover/UnionPay prefix, so the prefix
    check drops the false positives Luhn alone let through.
    """
    if not _luhn_ok(candidate):
        return False
    n = "".join(c for c in candidate if c.isdigit())
    ln = len(n)
    if n[0] == "4" and ln in (13, 16, 19):                                   # Visa
        return True
    if n[:2] in {"34", "37"} and ln == 15:                                   # Amex
        return True
    if (51 <= int(n[:2]) <= 55 or 2221 <= int(n[:4]) <= 2720) and ln == 16:  # Mastercard
        return True
    if (n[:4] == "6011" or n[:2] == "65" or 644 <= int(n[:3]) <= 649) and ln == 16:  # Discover
        return True
    if n[:2] == "62" and 16 <= ln <= 19:                                     # UnionPay
        return True
    return False


@dataclass(frozen=True)
class Detector:
    """A named pattern plus an optional deterministic per-match validator."""

    name: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None

    def detects(self, text: str) -> bool:
        """True iff any match survives the validator — i.e. the text trips this detector."""
        for match in self.pattern.finditer(text):
            if self.validator is None or self.validator(match.group()):
                return True
        return False


# The reference set. Distinctive-prefix detectors (aws/github/slack/private_key) should
# score high precision *and* recall; the format-flexible ones (email/ssn/credit_card) and
# the entropy proxy (high_entropy_hex) are where precision/recall visibly trade off — the
# point the benchmark exists to quantify.
DETECTORS: tuple[Detector, ...] = (
    Detector("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # us_ssn is deliberately US-format-only (ddd-dd-dddd): perfect precision, and it does
    # not claim to catch international/undashed IDs (AI4Privacy recall reflects that).
    Detector("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # aws widened to the gitleaks prefix set + base32 charset: also catches ASIA/ABIA/ACCA/
    # A3T temp & STS keys, not just long-term AKIA keys (gitleaks cross-check).
    Detector("aws_access_key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b")),
    Detector("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    # slack tightened to the digit-led structure real tokens have (xox?-<digits>-<body>),
    # so it no longer fires on a loose xox?- prefix followed by arbitrary text.
    Detector("slack_token", re.compile(r"\bxox[baprs]-\d{10,}-[A-Za-z0-9-]{10,}\b")),
    # private_key stays header-based on purpose: a DLP gate wants to catch a pasted key even
    # when truncated, so it favors recall. That makes it a warn/redact-tier signal (a header
    # quoted in prose is a rare false positive), not an auto-block one.
    Detector(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    Detector(
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        validator=_is_card,
    ),
    # high_entropy_hex cannot distinguish a secret from a hash (both are high-entropy hex):
    # a git SHA / md5 trips it, so it is an advisory (log-tier) signal, never a block.
    Detector("high_entropy_hex", re.compile(r"\b[0-9a-f]{32,}\b")),
)

DETECTORS_BY_NAME: dict[str, Detector] = {d.name: d for d in DETECTORS}
