"""Spec-first contract tests for ``wayfinder_router.detectors`` (WF-DESIGN-0013 §4).

These tests are written FROM the design before the module exists (additive-only per
WF-ADR-0044). The module under test does not exist yet, so this file will error at
collection until it is built — that is the intended spec-first state.

Contracts pinned here (WF-DESIGN-0013):
  - §4 "Product detectors": ``Detector.scan`` returns ``None`` on no validated match,
    else a ``DetectorHit`` whose ``count`` is the number of *validated* matches and whose
    ``spans`` are ``(start, end)`` integer pairs taken from ``match.start()/end()`` only —
    "the matched substring is never retained ... there is no text field".
  - §4: the product ``DETECTORS`` are "byte-identical patterns and validators" to
    ``benchmarks/detectors.py`` — for every benchmark detector name there is a product
    ``Detector`` with an identical ``pattern.pattern`` string; ``detects`` has "identical
    semantics to benchmarks.Detector".
  - §4 API: ``Detector.pattern`` is typed ``re.Pattern[str]`` (compiled once in the
    ``DetectorRegistry.__init__``); ``DetectorRegistry.scan`` returns hits "sorted by
    name; only firing detectors"; ``DetectorRegistry.default()`` is "the frozen product
    set".
  - Contracts invariant 4: ``DetectorHit`` carries only ``name/count/spans:int``.

Strictest-reading resolutions (noted where the design is silent):
  - "constructing with a pattern string should not be possible per design — patterns are
    re.Pattern": the design types ``pattern`` as ``re.Pattern[str]`` but a frozen
    dataclass does not itself reject a bad type, so the enforceable invariant asserted
    here is that every shipped product ``Detector.pattern`` *is* an ``re.Pattern``
    instance (compiled, not a raw string) — i.e. compiled-once holds for the product set.
  - "documented example values" for ``detects`` parity are drawn from the benchmark
    module's own docstrings and from the committed ``tests/test_detector_validation.py``
    (e.g. ``AKIAIOSFODNN7EXAMPLE``, ``4111 1111 1111 1111``).
"""

from __future__ import annotations

import dataclasses
import re

import benchmarks.detectors as bench
from wayfinder_router.detectors import (
    DETECTORS,
    DETECTORS_BY_NAME,
    Detector,
    DetectorHit,
    DetectorRegistry,
)

# Documented example values (benchmark docstrings + committed detector tests). Each is a
# text that trips exactly the named detector; the negatives trip nothing.
DOCUMENTED = {
    "email": ("reach me at analyst@example.com please", "no address here"),
    "us_ssn": ("ssn 123-45-6789 on file", "digits 12345 6789 only"),
    "aws_access_key": ("key AKIAIOSFODNN7EXAMPLE rotated", "the AKIA prefix alone"),
    "github_pat": (
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789 leaked",
        "ghp_short token",
    ),
    "slack_token": (
        "token xoxb-1234567890-abcdefghij posted",
        "loose xoxb- prefix then words",
    ),
    "private_key": ("-----BEGIN RSA PRIVATE KEY-----", "BEGIN PRIVATE nothing"),
    "credit_card": ("card 4111 1111 1111 1111 on file", "code 4111 1111 1111 1112 bad"),
    "high_entropy_hex": (("a" * 40) + " sha", "short abc123 hex"),
}


def _find(name: str) -> Detector:
    return DETECTORS_BY_NAME[name]


# --------------------------------------------------------------------------- DetectorHit


def test_detector_hit_has_no_text_field():
    # Contracts inv. 4 / §4: a DetectorHit is exactly (name, count, spans) — no text field
    # can exist, so it structurally cannot carry secret text.
    field_names = {f.name for f in dataclasses.fields(DetectorHit)}
    assert field_names == {"name", "count", "spans"}
    assert "text" not in field_names and "match" not in field_names


def test_scan_returns_none_on_no_validated_match():
    # §4: scan returns None when nothing (that survives the validator) matches.
    assert _find("email").scan("no address at all here") is None
    # credit_card: regex matches a digit run but the Luhn/IIN validator rejects it.
    assert _find("credit_card").scan("code 4111 1111 1111 1112 rejected") is None


def test_scan_count_is_number_of_validated_matches():
    # §4: count == number of validated matches. Two distinct e-mails -> count 2.
    hit = _find("email").scan("a@x.com and b@y.org")
    assert isinstance(hit, DetectorHit) and hit.name == "email" and hit.count == 2
    # One valid card among prose -> count 1.
    one = _find("credit_card").scan("pay with 4111 1111 1111 1111 today")
    assert one is not None and one.count == 1


def test_scan_spans_are_int_pairs_from_match_offsets():
    # §4: spans are (start, end) int pairs built from match.start()/end() ONLY. They must
    # equal the offsets the identical benchmark pattern produces (validated matches).
    text = "mails a@x.com then b@y.org end"
    hit = _find("email").scan(text)
    assert hit is not None
    expected = tuple(
        (m.start(), m.end()) for m in bench.DETECTORS_BY_NAME["email"].pattern.finditer(text)
    )
    assert hit.spans == expected
    for start, end in hit.spans:
        assert type(start) is int and type(end) is int and start < end


# ---------------------------------------------------------------- parity with benchmarks


def test_detects_semantics_identical_to_benchmarks():
    # §4: product Detector.detects is identical to benchmarks.Detector.detects on the same
    # inputs. Compared on the benchmark's own documented positive/negative example values.
    for name, (positive, negative) in DOCUMENTED.items():
        prod, ref = _find(name), bench.DETECTORS_BY_NAME[name]
        assert prod.detects(positive) is ref.detects(positive) is True, name
        assert prod.detects(negative) is ref.detects(negative) is False, name


def test_pattern_parity_with_benchmarks():
    # §4 / Contracts inv. 9: byte-identical patterns — every benchmark detector name has a
    # product Detector with an identical pattern.pattern string. Same set of names, too.
    assert set(DETECTORS_BY_NAME) == set(bench.DETECTORS_BY_NAME)
    for name, ref in bench.DETECTORS_BY_NAME.items():
        assert _find(name).pattern.pattern == ref.pattern.pattern, name


def test_all_product_patterns_are_compiled_re_patterns():
    # §4 API: pattern is re.Pattern[str] — compiled once, never a raw string.
    for det in DETECTORS:
        assert isinstance(det.pattern, re.Pattern), det.name


def test_detectors_by_name_maps_every_detector():
    assert DETECTORS_BY_NAME == {d.name: d for d in DETECTORS}
    assert len(DETECTORS_BY_NAME) == len(DETECTORS)


# ------------------------------------------------------------------- DetectorRegistry


def test_registry_scan_sorted_by_name_only_firing():
    # §4: DetectorRegistry.scan returns hits sorted by name, only firing detectors.
    reg = DetectorRegistry.default()
    text = "mail me a@x.com or use card 4111 1111 1111 1111 now"
    hits = reg.scan(text)
    names = [h.name for h in hits]
    assert names == sorted(names)                       # sorted by name
    assert set(names) == {"credit_card", "email"}       # only firing detectors included
    assert all(isinstance(h, DetectorHit) for h in hits)


def test_registry_scan_excludes_non_firing():
    reg = DetectorRegistry.default()
    hits = reg.scan("just an email a@x.com and nothing else")
    assert [h.name for h in hits] == ["email"]
    assert reg.scan("completely clean text") == ()


def test_registry_default_is_exactly_the_frozen_product_set():
    # §4: default() "contains exactly the frozen product set".
    reg = DetectorRegistry.default()
    assert set(reg.names()) == set(DETECTORS_BY_NAME)
    assert reg.names() == tuple(sorted(reg.names()))     # names() is a stable sorted tuple


def test_registry_compiles_once_reuses_pattern_objects():
    # §4: "patterns compiled once here" — constructing a registry from the product
    # Detectors reuses their already-compiled pattern objects (no per-scan recompile).
    reg = DetectorRegistry(DETECTORS)
    text = "a@x.com"
    reg.scan(text)
    reg.scan(text)
    for det in DETECTORS:
        assert isinstance(det.pattern, re.Pattern)
        assert det.pattern is DETECTORS_BY_NAME[det.name].pattern  # same identity, not recompiled


# ------------------------------------------------------------------ specific validators


def test_credit_card_validator_is_luhn_plus_iin():
    # §4: credit_card validator is Luhn + IIN issuer prefix/length. Documented test cards.
    cc = _find("credit_card")
    assert cc.detects("card 4111 1111 1111 1111 here")       # Visa, Luhn-valid
    assert cc.detects("amex 378282246310005 on file")        # Amex 15-digit (37 prefix)
    assert not cc.detects("code 4111 1111 1111 1112 bad")     # last digit breaks Luhn
    assert not cc.detects("dotted 4111.1111.1111.1111 miss")  # separators the regex skips
    # Validator parity with the benchmark's _is_card on the same candidates.
    for cand in ("4111111111111111", "378282246310005", "4111111111111112", "1234567890123456"):
        assert bench._is_card(cand) is cc.validator(cand)


def test_high_entropy_hex_present_and_advisory():
    # §4: high_entropy_hex is part of the frozen set; it fires on 32+ lowercase hex.
    assert "high_entropy_hex" in DETECTORS_BY_NAME
    heh = _find("high_entropy_hex")
    assert heh.detects("a" * 32)
    assert not heh.detects("a" * 31)
