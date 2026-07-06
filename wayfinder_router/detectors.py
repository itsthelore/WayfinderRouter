"""Product secret/PII detectors for the governance spine (WF-DESIGN-0013 §4).

Productizes the reference set under ``benchmarks/detectors.py`` with **byte-identical
patterns and validators**, so the frozen benchmark oracle (micro precision 0.812 / recall
0.867, ``benchmarks/detector-validation-results.md``) is reproduced *by construction* — the
product carries its own copies of the eight patterns and of ``_luhn_ok``/``_is_card``, and
does not import ``benchmarks`` (only the tests compare the two). Any pattern drift or flag
change would shift a corpus confusion count and break that reproduction, so the strings here
are frozen transcriptions, not fresh authoring (WF-ADR-0044, additive-only / spec-first).

A :class:`DetectorHit` is deliberately just ``name``, ``count``, and integer ``spans``: the
matched substring is never retained anywhere, so a hit structurally cannot carry secret text
— ``scan`` records only ``match.start()``/``match.end()`` offsets and ``detects`` returns a
bool. Pure stdlib, no network, no keys, no model, in keeping with the offline deterministic
core (WF-ADR-0001) and Wayfinder's own logic never calling out (WF-ADR-0043). Each detector
owns its compiled pattern; :class:`DetectorRegistry` runs the eight as independent
``finditer`` passes and never recompiles on the hot path.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass


def _luhn_ok(candidate: str) -> bool:
    """True iff the digits in ``candidate`` (13-19 of them) pass the Luhn checksum.

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
class DetectorHit:
    """A detector firing: its name, the validated-match count, and integer offset spans.

    Carries no matched text by design — ``spans`` are ``(start, end)`` character offsets, so
    the hit cannot leak the secret it found (Contracts invariant 4, WF-DESIGN-0013 §4).
    """

    name: str
    count: int
    spans: tuple[tuple[int, int], ...]


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

    def scan(self, text: str) -> DetectorHit | None:
        """Return a hit for the validated matches, or ``None`` when none survive validation.

        Spans are built from ``match.start()``/``match.end()`` of validated matches only; the
        matched substring is discarded immediately after the validator sees it.
        """
        spans = tuple(
            (match.start(), match.end())
            for match in self.pattern.finditer(text)
            if self.validator is None or self.validator(match.group())
        )
        if not spans:
            return None
        return DetectorHit(name=self.name, count=len(spans), spans=spans)


# The frozen product set — patterns and validators transcribed byte-identically from
# ``benchmarks/detectors.py`` (str patterns compiled with no explicit flags; a str pattern
# carries ``re.UNICODE`` automatically — do not add IGNORECASE or any other flag, which would
# leave ``pattern.pattern`` unchanged yet silently shift the frozen corpus counts). Compilation
# happens once, here at import; ``DetectorRegistry`` stores these objects and never recompiles.
DETECTORS: tuple[Detector, ...] = (
    Detector("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    Detector("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    Detector("aws_access_key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b")),
    Detector("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    Detector("slack_token", re.compile(r"\bxox[baprs]-\d{10,}-[A-Za-z0-9-]{10,}\b")),
    Detector(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    Detector(
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        validator=_is_card,
    ),
    Detector("high_entropy_hex", re.compile(r"\b[0-9a-f]{32,}\b")),
)

DETECTORS_BY_NAME: dict[str, Detector] = {d.name: d for d in DETECTORS}


class DetectorRegistry:
    """Runs a fixed set of detectors over a prompt and returns the firing hits.

    Stores the given already-compiled ``Detector`` objects (never recompiling on the hot
    ``scan`` path) and precomputes their sorted name order once at construction.
    """

    def __init__(self, detectors: Iterable[Detector]) -> None:
        """Store the detectors and precompute the stable sorted name order."""
        self._detectors: tuple[Detector, ...] = tuple(detectors)
        self._by_name: dict[str, Detector] = {d.name: d for d in self._detectors}
        self._names: tuple[str, ...] = tuple(sorted(self._by_name))

    def scan(self, text: str) -> tuple[DetectorHit, ...]:
        """Return the firing detectors' hits, sorted by detector name; ``()`` when none fire."""
        hits = []
        for name in self._names:
            hit = self._by_name[name].scan(text)
            if hit is not None:
                hits.append(hit)
        return tuple(hits)

    def names(self) -> tuple[str, ...]:
        """Return the registry's detector names as a stable sorted tuple."""
        return self._names

    @classmethod
    def default(cls) -> DetectorRegistry:
        """Return a registry over the frozen product ``DETECTORS`` set."""
        return cls(DETECTORS)
