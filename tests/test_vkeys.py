"""Tests for virtual API key hashing/verification/minting (WF-ADR-0035)."""

from __future__ import annotations

from wayfinder_router import vkeys


def test_hash_is_stable_and_hex():
    h = vkeys.hash_key("wf-abc")
    assert h == vkeys.hash_key("wf-abc") and len(h) == 64
    assert h != vkeys.hash_key("wf-abd")


def test_verify_constant_time_match():
    h = vkeys.hash_key("secret-key")
    assert vkeys.verify("secret-key", h)
    assert vkeys.verify("secret-key", h.upper())  # stored hash case-insensitive
    assert not vkeys.verify("wrong", h)


def test_match_returns_id_or_none():
    keys = {"team-a": vkeys.hash_key("ka"), "team-b": vkeys.hash_key("kb")}
    assert vkeys.match("ka", keys) == "team-a"
    assert vkeys.match("kb", keys) == "team-b"
    assert vkeys.match("nope", keys) is None
    assert vkeys.match("", keys) is None


def test_match_checks_every_key_without_early_return(monkeypatch):
    # Matching must be constant over the configured set: it compares against every key with
    # hmac.compare_digest and does not short-circuit on the first hit — otherwise a timing
    # side-channel could leak which (if any) key was close.
    calls = {"n": 0}
    real = vkeys.hmac.compare_digest

    def counting(a, b):
        calls["n"] += 1
        return real(a, b)

    monkeypatch.setattr(vkeys.hmac, "compare_digest", counting)
    # The FIRST key matches; a short-circuiting match would call compare_digest once, not three times.
    hashes = {"a": vkeys.hash_key("ka"), "b": vkeys.hash_key("kb"), "c": vkeys.hash_key("kc")}
    assert vkeys.match("ka", hashes) == "a"
    assert calls["n"] == 3  # compared against all three -> no early return, and compare_digest is used


def test_extract_bearer():
    assert vkeys.extract_bearer("Bearer wf-xyz") == "wf-xyz"
    assert vkeys.extract_bearer("bearer wf-xyz") == "wf-xyz"
    assert vkeys.extract_bearer("wf-raw") == "wf-raw"  # bare token tolerated
    assert vkeys.extract_bearer(None) is None
    assert vkeys.extract_bearer("Bearer ") is None


def test_generate_roundtrips_through_verify():
    plaintext, h = vkeys.generate()
    assert plaintext.startswith("wf-")
    assert vkeys.verify(plaintext, h)
    other, _ = vkeys.generate()
    assert other != plaintext  # cryptographically random, distinct
