"""Spec-first contract tests for wayfinder_router.policy — compilation & total order.

Source of truth: WF-DESIGN-0013 §3 (Policy engine — wayfinder_router/policy.py) and
Contracts invariant 7 ("Policy total order"). Written from the design only; the module
does not exist yet, so these fail-first tests pin the contract.

Contracts pinned here:
  - compile_policy rejects duplicate rule ids -> PolicyError (id is unique within a policy).
  - CompiledPolicy.rules excludes disabled rules and is pre-sorted ascending by
    order_key = (priority, id); order_key is a strict total order (ties impossible).
  - policy_hash == sha256(canonical(sorted enabled rules))[:12]: exactly 12 lowercase-hex
    chars, stable across input-order permutations of the same rule set (canonicalized),
    changes when a rule changes, and excludes disabled rules from the digest.
  - Compile-once: evaluate performs no recompilation — CompiledPolicy is frozen/immutable
    and repeated evaluate calls on the same context return equal decisions.

Strictest-reading choices (noted): "excludes disabled rules" is tested both by the
CompiledPolicy.rules membership AND by policy_hash invariance when a disabled rule is
added/removed. "compile-once" is asserted via frozen-instance immutability plus
repeated-call determinism, per the design's minimum bar.
"""

from __future__ import annotations

import dataclasses
import string
from typing import Mapping

import pytest

from wayfinder_router.policy import (
    POLICY_SCHEMA_VERSION,
    VERB_PRECEDENCE,
    VERBS,
    BlockOutcome,
    CompiledPolicy,
    MatchCondition,
    PolicyContext,
    PolicyDecision,
    PolicyError,
    Rule,
    compile_policy,
    policy_from_toml,
)

_HEX = set(string.hexdigits.lower())


def _rule(
    rule_id: str,
    *,
    priority: int = 100,
    enabled: bool = True,
    verb: str = "warn",
    match: MatchCondition | None = None,
    args: Mapping[str, str] | None = None,
) -> Rule:
    """Build a Rule with wildcard match unless overridden (design §3 field order)."""
    return Rule(
        id=rule_id,
        priority=priority,
        enabled=enabled,
        match=match if match is not None else MatchCondition(),
        verb=verb,
        args=dict(args or {}),
    )


def _ctx(**over) -> PolicyContext:
    base = dict(
        identity_id="anonymous",
        identity_kind="anonymous",
        team=None,
        tags=frozenset(),
        vkey_id=None,
        model="gpt",
        route="local",
        score=0.5,
        detector_names=frozenset(),
    )
    base.update(over)
    return PolicyContext(**base)


# --- module constants -------------------------------------------------------


def test_schema_version_and_verb_vocabulary() -> None:
    """POLICY_SCHEMA_VERSION == 1 and VERBS is the design's frozen vocabulary."""
    assert POLICY_SCHEMA_VERSION == 1
    assert VERBS == (
        "route",
        "pin",
        "degrade",
        "throttle",
        "clamp",
        "redact",
        "warn",
        "log",
        "block",
        "deny",
    )
    # VERB_PRECEDENCE is a permutation of VERBS, HIGH->LOW terminal precedence.
    assert set(VERB_PRECEDENCE) == set(VERBS)
    assert VERB_PRECEDENCE == (
        "deny",
        "block",
        "clamp",
        "degrade",
        "pin",
        "throttle",
        "redact",
        "warn",
        "log",
        "route",
    )


# --- duplicate id rejection -------------------------------------------------


def test_duplicate_rule_id_raises_policy_error() -> None:
    """Contract 7: duplicate rule id within a policy -> PolicyError."""
    with pytest.raises(PolicyError):
        compile_policy([_rule("dup", priority=1), _rule("dup", priority=2)])


# --- disabled exclusion & sort order ----------------------------------------


def test_disabled_rules_excluded_from_compiled_rules() -> None:
    """Disabled rules never appear in CompiledPolicy.rules."""
    compiled = compile_policy(
        [_rule("on", enabled=True), _rule("off", enabled=False)]
    )
    ids = [r.id for r in compiled.rules]
    assert ids == ["on"]


def test_rules_presorted_ascending_by_priority_then_id() -> None:
    """CompiledPolicy.rules is pre-sorted ascending by order_key = (priority, id)."""
    compiled = compile_policy(
        [
            _rule("b", priority=10),
            _rule("a", priority=10),
            _rule("z", priority=5),
            _rule("m", priority=20),
        ]
    )
    assert [(r.priority, r.id) for r in compiled.rules] == [
        (5, "z"),
        (10, "a"),
        (10, "b"),
        (20, "m"),
    ]
    assert isinstance(compiled.rules, tuple)


def test_order_key_is_priority_id_tuple() -> None:
    """Rule.order_key() == (priority, id) — the strict total order, ties impossible."""
    r = _rule("xyz", priority=42)
    assert r.order_key() == (42, "xyz")


def test_compile_policy_id_defaults_to_default() -> None:
    """compile_policy policy_id defaults to 'default' and is otherwise honored."""
    assert compile_policy([_rule("a")]).policy_id == "default"
    assert compile_policy([_rule("a")], policy_id="org-x").policy_id == "org-x"


def test_empty_ruleset_compiles() -> None:
    """A policy with no rules compiles to an empty, still-hashed CompiledPolicy."""
    compiled = compile_policy([])
    assert compiled.rules == ()
    assert len(compiled.policy_hash) == 12


# --- policy_hash contract ---------------------------------------------------


def test_policy_hash_is_twelve_lowercase_hex() -> None:
    """policy_hash == sha256(...)[:12]: 12 lowercase-hex chars."""
    h = compile_policy([_rule("a"), _rule("b")]).policy_hash
    assert len(h) == 12
    assert set(h) <= _HEX


def test_policy_hash_stable_across_input_order_permutations() -> None:
    """Contract 7: policy_hash is invariant to input-order permutations (canonicalized)."""
    rules = [_rule("a", priority=1), _rule("b", priority=2), _rule("c", priority=3)]
    h1 = compile_policy(rules).policy_hash
    h2 = compile_policy(list(reversed(rules))).policy_hash
    h3 = compile_policy([rules[1], rules[2], rules[0]]).policy_hash
    assert h1 == h2 == h3


def test_policy_hash_changes_when_a_rule_changes() -> None:
    """policy_hash changes when any enabled rule's content changes."""
    base = compile_policy([_rule("a", verb="warn")]).policy_hash
    changed_verb = compile_policy([_rule("a", verb="log")]).policy_hash
    changed_prio = compile_policy([_rule("a", verb="warn", priority=7)]).policy_hash
    assert base != changed_verb
    assert base != changed_prio


def test_policy_hash_excludes_disabled_rules() -> None:
    """Adding/removing a disabled rule leaves policy_hash unchanged (digest = enabled only)."""
    without = compile_policy([_rule("a")]).policy_hash
    with_disabled = compile_policy(
        [_rule("a"), _rule("ghost", enabled=False)]
    ).policy_hash
    assert without == with_disabled


# --- compile-once / immutability --------------------------------------------


def test_compiled_policy_is_frozen() -> None:
    """CompiledPolicy is an immutable frozen dataclass (no post-compile mutation)."""
    compiled = compile_policy([_rule("a")])
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        compiled.policy_hash = "0000deadbeef"  # type: ignore[misc]


def test_evaluate_is_repeatable_and_recompiles_nothing() -> None:
    """Compile-once: repeated evaluate calls on one context return equal decisions."""
    compiled = compile_policy(
        [_rule("pin-it", verb="pin", args={"target": "cloud"})]
    )
    ctx = _ctx()
    first = compiled.evaluate(ctx)
    second = compiled.evaluate(ctx)
    assert isinstance(first, PolicyDecision)
    assert first == second
    # Immutable inputs are not re-sorted or mutated by evaluation.
    assert compiled.evaluate(ctx).rule == first.rule


def test_policy_error_is_an_exception_type() -> None:
    """PolicyError, BlockOutcome, and policy_from_toml are importable per the API block."""
    assert issubclass(PolicyError, Exception)
    assert BlockOutcome is not None
    assert callable(policy_from_toml)
