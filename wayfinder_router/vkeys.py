"""Virtual API keys for the gateway: hashing, verification, and minting (WF-ADR-0035).

A virtual key is a gateway-issued bearer token that authenticates a caller — not a
provider key (those come from the environment, WF-ADR-0004). The gateway stores only a
SHA-256 hash, never the plaintext, and every comparison is constant-time. Pure and
offline (WF-ADR-0001): no FastAPI/httpx here.
"""

from __future__ import annotations

import hashlib
import hmac  # kept as a module attribute so tests can monkeypatch hmac.compare_digest
import secrets
from collections.abc import Mapping

# Minted keys look like "wf-<token>", making them easy to spot in logs and configs.
KEY_PREFIX = "wf"


def hash_key(presented: str) -> str:
    """Return the SHA-256 hex digest the gateway stores and compares against (64 lowercase hex)."""
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()


def verify(presented: str, expected_hash: str) -> bool:
    """Constant-time check that ``presented`` hashes to ``expected_hash``.

    The stored hash is stripped and lowercased, so verification is whitespace-tolerant and
    case-insensitive on the expected side; the presented plaintext is left untouched (its
    digest is already lowercase hex).
    """
    return hmac.compare_digest(hash_key(presented), expected_hash.strip().lower())


def match(presented: str | None, hashes: Mapping[str, str]) -> str | None:
    """Return the id of the configured key whose hash matches ``presented``, else ``None``.

    Compares against every entry with a constant-time digest check and never short-circuits,
    so a non-match leaks no timing about which (if any) configured key was close.
    """
    if not presented:  # None or "" — no work, no compares
        return None
    digest = hash_key(presented)  # computed once, reused for every candidate
    found: str | None = None
    for key_id, expected in hashes.items():
        # No break / early return: the whole set is always walked (last match wins).
        if hmac.compare_digest(digest, expected.strip().lower()):
            found = key_id
    return found


def extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization`` header: ``Bearer <token>`` or a bare token."""
    if not authorization:
        return None
    value = authorization.strip()
    lower = value.lower()
    # Scheme match is case-insensitive, but the token is sliced from the original-cased value.
    if lower == "bearer" or lower.startswith("bearer "):
        return value[6:].strip() or None  # len("bearer") == 6; empty token -> None
    return value or None


def generate(prefix: str = KEY_PREFIX) -> tuple[str, str]:
    """Mint a fresh random virtual key; return ``(plaintext, hash)``.

    The plaintext is shown to the operator once; only the hash belongs in the config.
    Randomness comes from :mod:`secrets`.
    """
    token = secrets.token_urlsafe(32)
    plaintext = f"{prefix}-{token}"
    return plaintext, hash_key(plaintext)
