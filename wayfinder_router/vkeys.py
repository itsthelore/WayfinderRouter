"""Virtual API keys for the gateway — hashing, verification, minting (WF-ADR-0035).

Pure, offline credential handling (WF-ADR-0001): a virtual key is a gateway-issued bearer token
that authenticates a caller and attributes their spend/savings — it is NOT a provider key (those
still come from the environment, WF-ADR-0004). The gateway stores only a **SHA-256 hash** of each
key, never the plaintext, so a leaked config exposes no usable credential. Matching is
constant-time. No FastAPI/httpx import here; unit-tests like ``reliability.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping

KEY_PREFIX = "wf"  # minted keys look like "wf-<token>", easy to spot in logs/configs


def hash_key(presented: str) -> str:
    """The SHA-256 hex digest of a key — what the gateway stores and compares against."""
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()


def verify(presented: str, expected_hash: str) -> bool:
    """Constant-time check that ``presented`` hashes to ``expected_hash``."""
    return hmac.compare_digest(hash_key(presented), expected_hash.strip().lower())


def match(presented: str | None, hashes: Mapping[str, str]) -> str | None:
    """Return the id of the configured key whose hash matches ``presented``, or ``None``.

    Compares against every entry with a constant-time digest check so a non-match doesn't leak
    timing about which (if any) key was close.
    """
    if not presented:
        return None
    digest = hash_key(presented)
    found: str | None = None
    for key_id, expected in hashes.items():
        if hmac.compare_digest(digest, expected.strip().lower()):
            found = key_id
    return found


def extract_bearer(authorization: str | None) -> str | None:
    """Pull the token from an ``Authorization`` header — ``Bearer <token>`` or a bare token."""
    if not authorization:
        return None
    value = authorization.strip()
    lower = value.lower()
    if lower == "bearer" or lower.startswith("bearer "):
        return value[6:].strip() or None  # token after the scheme, or None if empty
    return value or None


def generate(prefix: str = KEY_PREFIX) -> tuple[str, str]:
    """Mint a new random virtual key; return ``(plaintext, hash)``.

    The plaintext is shown to the operator once (to hand to a team); only the hash goes in the
    config. Uses ``secrets`` for cryptographic randomness.
    """
    token = secrets.token_urlsafe(32)
    plaintext = f"{prefix}-{token}"
    return plaintext, hash_key(plaintext)
