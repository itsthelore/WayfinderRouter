"""Spec-first contract tests for wayfinder_router.policy — policy_from_toml.

Source of truth: WF-DESIGN-0013 §3 (TOML representation) and Contracts invariants 7-8.
Written from the design only. Follows the tests/test_config.py idiom: plain pytest, the
design's TOML embedded verbatim, and error assertions via pytest.raises with the offending
rule id asserted to appear in the message.

Contracts pinned here:
  - policy_from_toml parses the design's example [policy]/[policy.rules.<id>] tables into a
    CompiledPolicy whose rule ids are the table names.
  - Defaults: priority=100, enabled=true (per design §3).
  - Verb args (target, message) are read from the rule table's flat keys; match fields use
    the MatchCondition field names (e.g. detectors_any, teams).
  - Unknown verb -> PolicyError; bad schema/type -> PolicyError whose message names the
    offending rule id.

Strictest-reading choice (noted, per task): [policy] defaults enabled=false, and the design
states "absent/false => policy stage skipped entirely". Since CompiledPolicy has no
`enabled` field and a disabled/absent policy must never be evaluated, the strictest sensible
contract is that policy_from_toml REFUSES to compile a disabled or absent policy table and
raises PolicyError — compiling one would be a caller misuse. The enabled/absent gate is the
config layer's responsibility (governance_active); policy_from_toml only ever yields an
active policy. Both the absent-table and enabled=false cases are pinned to PolicyError.
"""

from __future__ import annotations

import pytest

from wayfinder_router.policy import (
    CompiledPolicy,
    PolicyError,
    Rule,
    policy_from_toml,
)

# The design §3 example, embedded verbatim.
EXAMPLE_TOML = """\
[policy]
enabled = true
id = "org-baseline"

[policy.rules.block-secrets]
priority = 10
enabled = true
verb = "block"
message = "Requests containing credentials are not permitted."
detectors_any = ["aws_access_key", "github_pat", "slack_token", "private_key"]

[policy.rules.redact-pii]
priority = 20
verb = "redact"
detectors_any = ["email", "us_ssn", "credit_card"]

[policy.rules.finance-pin]
priority = 30
verb = "pin"
target = "cloud-approved"
teams = ["finance"]
"""


def _by_id(compiled: CompiledPolicy) -> dict[str, Rule]:
    return {r.id: r for r in compiled.rules}


# --- parsing the design example ---------------------------------------------


def test_parses_example_toml_into_compiled_policy() -> None:
    """policy_from_toml compiles the design's example verbatim; policy_id from [policy].id."""
    compiled = policy_from_toml(EXAMPLE_TOML)
    assert isinstance(compiled, CompiledPolicy)
    assert compiled.policy_id == "org-baseline"
    assert {r.id for r in compiled.rules} == {
        "block-secrets",
        "redact-pii",
        "finance-pin",
    }


def test_rule_ids_are_table_names_and_presorted() -> None:
    """Rule ids are the [policy.rules.<id>] table names; rules pre-sorted by (priority, id)."""
    compiled = policy_from_toml(EXAMPLE_TOML)
    assert [r.id for r in compiled.rules] == [
        "block-secrets",  # priority 10
        "redact-pii",  # priority 20
        "finance-pin",  # priority 30
    ]


def test_verbs_parsed_from_tables() -> None:
    """Each rule's verb is read from its table's `verb` key."""
    rules = _by_id(policy_from_toml(EXAMPLE_TOML))
    assert rules["block-secrets"].verb == "block"
    assert rules["redact-pii"].verb == "redact"
    assert rules["finance-pin"].verb == "pin"


# --- defaults ---------------------------------------------------------------


def test_priority_defaults_to_100() -> None:
    """A rule without `priority` defaults to 100."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.only]\nverb = "warn"\n'
    )
    rules = _by_id(policy_from_toml(toml))
    assert rules["only"].priority == 100


def test_enabled_defaults_to_true() -> None:
    """A rule without `enabled` defaults to enabled=true (so it appears in compiled rules)."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.only]\nverb = "warn"\n'
    )
    compiled = policy_from_toml(toml)
    assert [r.id for r in compiled.rules] == ["only"]
    assert compiled.rules[0].enabled is True


def test_disabled_rule_excluded_from_compiled() -> None:
    """A rule with enabled=false is dropped from CompiledPolicy.rules."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.on]\nverb = "warn"\n\n'
        '[policy.rules.off]\nenabled = false\nverb = "log"\n'
    )
    compiled = policy_from_toml(toml)
    assert [r.id for r in compiled.rules] == ["on"]


# --- verb args & match fields -----------------------------------------------


def test_verb_args_from_flat_keys() -> None:
    """Verb args target/message are read from the rule table's flat keys."""
    rules = _by_id(policy_from_toml(EXAMPLE_TOML))
    assert rules["finance-pin"].args.get("target") == "cloud-approved"
    assert (
        rules["block-secrets"].args.get("message")
        == "Requests containing credentials are not permitted."
    )


def test_match_fields_use_condition_field_names() -> None:
    """Match fields (detectors_any, teams) populate the MatchCondition by field name."""
    rules = _by_id(policy_from_toml(EXAMPLE_TOML))
    assert rules["block-secrets"].match.detectors_any == frozenset(
        {"aws_access_key", "github_pat", "slack_token", "private_key"}
    )
    assert rules["redact-pii"].match.detectors_any == frozenset(
        {"email", "us_ssn", "credit_card"}
    )
    assert rules["finance-pin"].match.teams == frozenset({"finance"})


# --- error types ------------------------------------------------------------


def test_unknown_verb_raises_policy_error() -> None:
    """An unknown verb (not in VERBS) -> PolicyError."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.bad]\nverb = "obliterate"\n'
    )
    with pytest.raises(PolicyError):
        policy_from_toml(toml)


def test_bad_type_raises_policy_error_naming_the_rule() -> None:
    """A bad field type -> PolicyError whose message names the offending rule id."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.wonky]\nverb = "warn"\npriority = "high"\n'
    )
    with pytest.raises(PolicyError) as excinfo:
        policy_from_toml(toml)
    assert "wonky" in str(excinfo.value)


def test_missing_verb_raises_policy_error_naming_the_rule() -> None:
    """A rule table with no `verb` key -> PolicyError naming the offending rule."""
    toml = (
        '[policy]\nenabled = true\nid = "p"\n\n'
        '[policy.rules.verbless]\npriority = 10\n'
    )
    with pytest.raises(PolicyError) as excinfo:
        policy_from_toml(toml)
    assert "verbless" in str(excinfo.value)


# --- disabled / absent policy table (strictest-reading contract) ------------


def test_absent_policy_table_raises_policy_error() -> None:
    """Strict contract: no [policy] table -> PolicyError (nothing active to compile)."""
    with pytest.raises(PolicyError):
        policy_from_toml("[routing]\nthreshold = 0.5\n")


def test_disabled_policy_table_raises_policy_error() -> None:
    """Strict contract: [policy] enabled=false (the default) -> PolicyError; the stage is
    skipped at the config layer, never compiled here."""
    toml = (
        '[policy]\nid = "p"\n\n'  # enabled omitted => default false
        '[policy.rules.r]\nverb = "warn"\n'
    )
    with pytest.raises(PolicyError):
        policy_from_toml(toml)
