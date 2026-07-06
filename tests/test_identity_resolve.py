"""Spec-first contract tests for ``wayfinder_router.identity`` (WF-DESIGN-0013 §5).

Written FROM the design before the module exists (additive-only, WF-ADR-0044). The module
under test does not exist yet, so this file errors at collection until it is built — the
intended spec-first state.

Contracts pinned (WF-DESIGN-0013 §5 / invariant 10 "Identity totality"):
  - "exactly one principal, always": ``resolve`` returns exactly one ``Principal`` for
    every input combination.
  - ``vkey_id is None`` -> ``ANONYMOUS`` (the singleton; id "anonymous", kind "anonymous").
  - a ``vkey_id`` mapping to a configured ``[identity.principals.<id>]`` wins over
    synthesis.
  - an unmapped ``vkey_id`` synthesizes via ``principal_from_vkey``: id == vkey_id, kind
    from a ``kind:<x>`` tag (default "human"), team from a ``team:<x>`` tag, residual tags
    kept. So ``["team:x","kind:agent","role:analyst"]`` -> ``Principal(id=vkey_id,
    kind="agent", team="x", tags=("role:analyst",))``.
  - ``IdentityRegistry.__init__`` rejects a duplicate principal id with ``IdentityError``.
  - ``IdentityRegistry.from_toml`` parses the design's §5 example TOML.
  - ``Principal`` is a frozen dataclass (hashable).

Strictest-reading resolutions (design silent on exact enforcement point):
  - "invalid kind -> IdentityError": ``Principal`` is a plain frozen dataclass and does not
    self-validate its ``kind`` (the design only annotates "in IDENTITY_KINDS"). The
    strictest defensible boundary is the *registry*: ``IdentityRegistry`` construction and
    ``from_toml`` reject a principal whose kind is not in ``IDENTITY_KINDS``. Both are
    asserted; the bare ``Principal(...)`` constructor is not assumed to raise.
"""

from __future__ import annotations

import dataclasses

import pytest

from wayfinder_router.identity import (
    ANONYMOUS,
    ANONYMOUS_ID,
    IDENTITY_KINDS,
    IdentityError,
    IdentityRegistry,
    Principal,
    principal_from_vkey,
)

# The design §5 example TOML, embedded verbatim.
EXAMPLE_TOML = """
[identity]
enabled = true

[identity.principals.alice]
kind = "human"
team = "finance"
tags = ["role:analyst"]

[identity.principals.nightly-agent]
kind = "agent"
team = "platform"
"""

# Cover every combination of vkey_id class x tag class for the totality invariant.
_VKEY_IDS = (None, "alice", "unmapped-key")
_TAG_SETS = ((), ("team:x", "kind:agent", "role:analyst"), ("kind:service",), ("role:x",))


def _registry() -> IdentityRegistry:
    return IdentityRegistry(
        [
            Principal(id="alice", kind="human", team="finance", tags=("role:analyst",)),
            Principal(id="nightly-agent", kind="agent", team="platform"),
        ]
    )


# ------------------------------------------------------------------------- totality


@pytest.mark.parametrize("vkey_id", _VKEY_IDS)
@pytest.mark.parametrize("tags", _TAG_SETS)
def test_resolve_returns_exactly_one_principal_for_every_input(vkey_id, tags):
    # Invariant 10: resolve is total — always exactly one Principal, never None/raise.
    result = _registry().resolve(vkey_id=vkey_id, vkey_tags=tags)
    assert isinstance(result, Principal)
    assert result.kind in IDENTITY_KINDS


def test_identity_kinds_vocabulary():
    assert IDENTITY_KINDS == ("human", "agent", "service", "anonymous")


# ------------------------------------------------------------------------ anonymous


def test_none_vkey_resolves_to_the_anonymous_singleton():
    # Invariant 10: vkey_id=None -> ANONYMOUS (identity), regardless of any tags supplied.
    reg = _registry()
    p = reg.resolve(vkey_id=None)
    assert p is ANONYMOUS
    assert p.id == "anonymous" == ANONYMOUS_ID
    assert p.kind == "anonymous"
    # Tags do not rescue a None vkey from anonymity.
    assert reg.resolve(vkey_id=None, vkey_tags=("team:x", "kind:agent")) is ANONYMOUS


def test_anonymous_constant_shape():
    assert ANONYMOUS.id == ANONYMOUS_ID == "anonymous"
    assert ANONYMOUS.kind == "anonymous"
    assert ANONYMOUS.team is None and ANONYMOUS.tags == ()


# ------------------------------------------------------------ configured vs synthesized


def test_configured_principal_lookup_wins_over_synthesis():
    # §5 rule 2 over rule 3: a mapped vkey returns the configured Principal even when tags
    # would have synthesized something different (here: configured human/finance, not the
    # agent/eng the tags describe).
    reg = _registry()
    p = reg.resolve(vkey_id="alice", vkey_tags=("kind:agent", "team:eng", "role:ignored"))
    assert p == Principal(id="alice", kind="human", team="finance", tags=("role:analyst",))
    assert p.kind == "human" and p.team == "finance"


def test_unmapped_vkey_synthesizes_from_tags():
    # Invariant 10 / §5 rule 3: unmapped vkey -> team:/kind: consumed, residual tags kept.
    reg = _registry()
    p = reg.resolve(
        vkey_id="k-123", vkey_tags=("team:x", "kind:agent", "role:analyst")
    )
    assert p == Principal(id="k-123", kind="agent", team="x", tags=("role:analyst",))
    assert p.id == "k-123" and p.kind == "agent" and p.team == "x"
    assert p.tags == ("role:analyst",)  # team:/kind: consumed, not left in residual tags


def test_kind_defaults_to_human_when_no_kind_tag():
    # §5 rule 3: kind default is "human" when no kind:<x> tag is present.
    p = _registry().resolve(vkey_id="k-9", vkey_tags=("team:y",))
    assert p.kind == "human" and p.team == "y" and p.tags == ()


def test_principal_from_vkey_direct():
    # The tag-convention consumer, called directly (§5).
    p = principal_from_vkey("svc-1", ("kind:service", "team:ops", "scope:read"))
    assert p == Principal(id="svc-1", kind="service", team="ops", tags=("scope:read",))
    bare = principal_from_vkey("bare", ())
    assert bare == Principal(id="bare", kind="human", team=None, tags=())


# ------------------------------------------------------------------------- registry


def test_duplicate_principal_id_raises_identity_error():
    # §5: "by-id map; duplicate id -> IdentityError".
    with pytest.raises(IdentityError):
        IdentityRegistry(
            [Principal(id="dup", kind="human"), Principal(id="dup", kind="agent")]
        )


def test_get_returns_configured_principal_or_none():
    reg = _registry()
    assert reg.get("alice").id == "alice"
    assert reg.get("nightly-agent").kind == "agent"
    assert reg.get("absent") is None


def test_from_toml_parses_the_design_example_verbatim():
    # §5: from_toml parses the [identity.principals.<id>] tables into Principals.
    reg = IdentityRegistry.from_toml(EXAMPLE_TOML)
    assert reg.get("alice") == Principal(
        id="alice", kind="human", team="finance", tags=("role:analyst",)
    )
    assert reg.get("nightly-agent") == Principal(
        id="nightly-agent", kind="agent", team="platform", tags=()
    )
    # And the parsed registry resolves a configured key to that principal.
    assert reg.resolve(vkey_id="alice").team == "finance"


def test_invalid_kind_is_rejected_at_the_registry_boundary():
    # Strictest reading: a kind outside IDENTITY_KINDS is an IdentityError — via from_toml
    # and via direct registry construction.
    bad_toml = """
[identity]
enabled = true

[identity.principals.bot]
kind = "robot"
"""
    with pytest.raises(IdentityError):
        IdentityRegistry.from_toml(bad_toml)
    with pytest.raises(IdentityError):
        IdentityRegistry([Principal(id="bot", kind="robot")])


def test_principal_is_frozen_and_hashable():
    p = Principal(id="alice", kind="human", team="finance", tags=("role:analyst",))
    assert hash(p) == hash(
        Principal(id="alice", kind="human", team="finance", tags=("role:analyst",))
    )
    assert {p, ANONYMOUS}  # usable as set members
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.kind = "agent"  # type: ignore[misc]
