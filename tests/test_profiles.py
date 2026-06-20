"""Stock lexicon profiles (WF-ADR-0024)."""

from __future__ import annotations

from wayfinder_router.complexity import Lexicon, extract_features
from wayfinder_router.profiles import PROFILES, PROFILES_BY_ID


def test_profiles_are_well_formed():
    assert PROFILES
    ids = [p.id for p in PROFILES]
    assert len(ids) == len(set(ids))  # unique ids
    for p in PROFILES:
        assert p.source in {"curated", "mined"}
        assert p.name and p.note  # labelled, with provenance/quality note
        assert p.reasoning_terms  # every profile carries at least reasoning terms
        # single lowercase word tokens — the scorer tokenizes on words, so phrases
        # (with spaces) would silently never match.
        for term in (*p.reasoning_terms, *p.constraint_terms):
            assert term and term == term.lower() and " " not in term


def test_both_provenances_present_and_weak_mined_flagged():
    assert {"curated", "mined"} <= {p.source for p in PROFILES}
    # the math word-problem list is kept as an honest cautionary example
    assert "cautionary" in PROFILES_BY_ID["mined-math"].note.lower()


def test_curated_terms_actually_fire_through_the_scorer():
    prof = PROFILES_BY_ID["proofs-math"]
    lex = Lexicon(reasoning_terms=frozenset(prof.reasoning_terms))
    feats = extract_features("We prove the theorem by induction.", lexicon=lex)
    assert feats["reasoning_term_count"] >= 3


def test_to_dict_shape():
    d = PROFILES_BY_ID["law-compliance"].to_dict()
    assert set(d) == {"id", "name", "source", "reasoning_terms", "constraint_terms", "note"}
    assert isinstance(d["reasoning_terms"], list) and d["constraint_terms"]
