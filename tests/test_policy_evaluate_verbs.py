"""Spec-first contract tests for wayfinder_router.policy — match & verb evaluation.

Source of truth: WF-DESIGN-0013 §3 (MatchCondition / evaluate / verb outcomes) and
Contracts invariant 8 ("Policy verb outcomes"). Written from the design only.

Contracts pinned here:
  - MatchCondition: every field defaults to empty set / None = wildcard; a present clause
    must match; clauses are AND-ed. Sets match by membership; tags_any = ANY, tags_all =
    subset; detectors_any / detectors_all likewise; score_min/max are inclusive bounds;
    identity_ids/kinds/teams/models/routes match by membership.
  - Terminal rule = unique argmin over applied rules of (VERB_PRECEDENCE.index(verb),
    order_key). A lower-priority rule with a higher-precedence verb beats a higher-priority
    lower-precedence one.
  - Verb outcomes (Contract 8): deny/block -> BlockOutcome status 403 + message + verb
    recorded; pin/clamp/degrade -> route = args['target']; redact -> redactions = sorted
    detector names accumulated across ALL applied redact rules (even non-terminal);
    throttle -> throttle=True; warn/log -> no route change (headers only); no match ->
    verb='route', rule=None, route=ctx.route.
  - applied_rules and verbs are emitted in total-order (order_key) sequence.
  - to_headers() emits x-wayfinder-policy, x-wayfinder-policy-rule, x-wayfinder-policy-verb.

Strictest-reading choices (noted):
  - "a redact rule's target detector names" is read as the detector names the rule matches
    on (detectors_any ∪ detectors_all) that are actually present in ctx.detector_names.
    Test cases are constructed so the rule's targeted detectors == the present detectors,
    which satisfies both the declared-names and present-intersection readings.
  - BlockOutcome.status is 403 for both block and deny; the distinct verb is recorded in
    BlockOutcome.verb (design §3 BlockOutcome note).
"""

from __future__ import annotations

from typing import Mapping

import pytest

from wayfinder_router.policy import (
    VERB_PRECEDENCE,
    BlockOutcome,
    CompiledPolicy,
    MatchCondition,
    PolicyContext,
    PolicyDecision,
    Rule,
    compile_policy,
)


def _rule(
    rule_id: str,
    *,
    priority: int = 100,
    verb: str = "warn",
    match: MatchCondition | None = None,
    args: Mapping[str, str] | None = None,
    enabled: bool = True,
) -> Rule:
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
        identity_id="alice",
        identity_kind="human",
        team="finance",
        tags=frozenset({"role:analyst"}),
        vkey_id="k1",
        model="gpt-4",
        route="local",
        score=0.5,
        detector_names=frozenset(),
    )
    base.update(over)
    return PolicyContext(**base)


def _matches(match: MatchCondition, ctx: PolicyContext) -> bool:
    return match.matches(ctx)


# --- MatchCondition wildcard & AND ------------------------------------------


def test_empty_match_is_wildcard() -> None:
    """An all-default MatchCondition matches every context."""
    assert _matches(MatchCondition(), _ctx())
    assert _matches(MatchCondition(), _ctx(identity_kind="service", score=0.0))


def test_present_clauses_are_anded() -> None:
    """Multiple present clauses must ALL match (logical AND across clauses)."""
    m = MatchCondition(teams=frozenset({"finance"}), models=frozenset({"gpt-4"}))
    assert _matches(m, _ctx(team="finance", model="gpt-4"))
    assert not _matches(m, _ctx(team="finance", model="other"))
    assert not _matches(m, _ctx(team="legal", model="gpt-4"))


# --- set membership clauses -------------------------------------------------


def test_identity_ids_membership() -> None:
    """identity_ids matches by membership of ctx.identity_id."""
    m = MatchCondition(identity_ids=frozenset({"alice", "bob"}))
    assert _matches(m, _ctx(identity_id="bob"))
    assert not _matches(m, _ctx(identity_id="carol"))


def test_identity_kinds_membership() -> None:
    """identity_kinds matches by membership of ctx.identity_kind."""
    m = MatchCondition(identity_kinds=frozenset({"agent", "service"}))
    assert _matches(m, _ctx(identity_kind="agent"))
    assert not _matches(m, _ctx(identity_kind="human"))


def test_teams_membership() -> None:
    """teams matches by membership of ctx.team (None team never matches a present clause)."""
    m = MatchCondition(teams=frozenset({"finance"}))
    assert _matches(m, _ctx(team="finance"))
    assert not _matches(m, _ctx(team=None))


def test_models_and_routes_membership() -> None:
    """models matches ctx.model; routes matches ctx.route (route_pre_policy)."""
    assert _matches(MatchCondition(models=frozenset({"gpt-4"})), _ctx(model="gpt-4"))
    assert not _matches(MatchCondition(models=frozenset({"gpt-4"})), _ctx(model="x"))
    assert _matches(MatchCondition(routes=frozenset({"cloud"})), _ctx(route="cloud"))
    assert not _matches(MatchCondition(routes=frozenset({"cloud"})), _ctx(route="local"))


# --- tags any/all -----------------------------------------------------------


def test_tags_any_is_membership() -> None:
    """tags_any matches when the principal has ANY of the listed tags."""
    m = MatchCondition(tags_any=frozenset({"role:admin", "role:analyst"}))
    assert _matches(m, _ctx(tags=frozenset({"role:analyst"})))
    assert not _matches(m, _ctx(tags=frozenset({"role:intern"})))


def test_tags_all_is_subset() -> None:
    """tags_all matches only when the principal has ALL listed tags (subset)."""
    m = MatchCondition(tags_all=frozenset({"a", "b"}))
    assert _matches(m, _ctx(tags=frozenset({"a", "b", "c"})))
    assert not _matches(m, _ctx(tags=frozenset({"a"})))


# --- detectors any/all ------------------------------------------------------


def test_detectors_any_is_membership() -> None:
    """detectors_any matches on a hit for ANY of the named detectors."""
    m = MatchCondition(detectors_any=frozenset({"email", "us_ssn"}))
    assert _matches(m, _ctx(detector_names=frozenset({"email"})))
    assert not _matches(m, _ctx(detector_names=frozenset({"credit_card"})))


def test_detectors_all_is_subset() -> None:
    """detectors_all matches only when ALL named detectors fired (subset)."""
    m = MatchCondition(detectors_all=frozenset({"email", "us_ssn"}))
    assert _matches(m, _ctx(detector_names=frozenset({"email", "us_ssn", "x"})))
    assert not _matches(m, _ctx(detector_names=frozenset({"email"})))


# --- score bounds (inclusive) -----------------------------------------------


def test_score_min_is_inclusive() -> None:
    """score_min is an inclusive lower bound."""
    m = MatchCondition(score_min=0.5)
    assert _matches(m, _ctx(score=0.5))
    assert _matches(m, _ctx(score=0.6))
    assert not _matches(m, _ctx(score=0.4))


def test_score_max_is_inclusive() -> None:
    """score_max is an inclusive upper bound."""
    m = MatchCondition(score_max=0.5)
    assert _matches(m, _ctx(score=0.5))
    assert _matches(m, _ctx(score=0.4))
    assert not _matches(m, _ctx(score=0.6))


# --- terminal argmin precedence ---------------------------------------------


def test_terminal_is_argmin_of_precedence_then_order_key() -> None:
    """A lower-priority rule with a higher-precedence verb wins the terminal decision.

    warn-first (priority 10) is evaluated earlier, but pin (higher VERB_PRECEDENCE)
    at priority 90 is the terminal rule — argmin of (precedence_index, order_key).
    """
    compiled = compile_policy(
        [
            _rule("a", priority=10, verb="warn"),
            _rule("b", priority=90, verb="pin", args={"target": "cloud"}),
        ]
    )
    d = compiled.evaluate(_ctx())
    assert VERB_PRECEDENCE.index("pin") < VERB_PRECEDENCE.index("warn")
    assert d.rule == "b"
    assert d.verb == "pin"
    assert d.route == "cloud"


def test_applied_rules_and_verbs_in_total_order() -> None:
    """applied_rules and verbs are emitted in (priority, id) total-order sequence."""
    compiled = compile_policy(
        [
            _rule("a", priority=10, verb="warn"),
            _rule("b", priority=90, verb="pin", args={"target": "cloud"}),
            _rule("c", priority=20, verb="log"),
        ]
    )
    d = compiled.evaluate(_ctx())
    assert d.applied_rules == ("a", "c", "b")
    assert d.verbs == ("warn", "log", "pin")


# --- verb outcomes (Contract 8) ---------------------------------------------


def test_block_verb_yields_block_outcome_403() -> None:
    """block -> BlockOutcome(status=403, message=..., verb='block')."""
    compiled = compile_policy(
        [_rule("blk", verb="block", args={"message": "no secrets"})]
    )
    d = compiled.evaluate(_ctx())
    assert d.verb == "block"
    assert isinstance(d.block, BlockOutcome)
    assert d.block.status == 403
    assert d.block.message == "no secrets"
    assert d.block.verb == "block"


def test_deny_verb_yields_block_outcome_403_with_deny_recorded() -> None:
    """deny -> BlockOutcome(status=403, verb='deny') — distinct verb recorded."""
    compiled = compile_policy(
        [_rule("dny", verb="deny", args={"message": "denied"})]
    )
    d = compiled.evaluate(_ctx())
    assert d.verb == "deny"
    assert isinstance(d.block, BlockOutcome)
    assert d.block.status == 403
    assert d.block.verb == "deny"


@pytest.mark.parametrize("verb", ["pin", "clamp", "degrade"])
def test_route_mutating_verbs_set_route_to_target(verb: str) -> None:
    """pin/clamp/degrade mutate route to args['target']."""
    compiled = compile_policy(
        [_rule("r", verb=verb, args={"target": "cloud-approved"})]
    )
    d = compiled.evaluate(_ctx(route="local"))
    assert d.verb == verb
    assert d.route == "cloud-approved"
    assert d.block is None


def test_no_block_outcome_for_non_block_terminal() -> None:
    """block is set iff terminal verb in {block, deny}."""
    compiled = compile_policy([_rule("p", verb="pin", args={"target": "c"})])
    assert compiled.evaluate(_ctx()).block is None


def test_redact_accumulates_sorted_across_all_applied_rules() -> None:
    """redactions = sorted detector names from EVERY applied redact rule, even non-terminal.

    A terminal pin coexists with two non-terminal redact rules; redactions still gather
    both rules' targeted (and present) detectors, sorted.
    """
    compiled = compile_policy(
        [
            _rule("pin", priority=5, verb="pin", args={"target": "cloud"}),
            _rule(
                "r1",
                priority=10,
                verb="redact",
                match=MatchCondition(detectors_any=frozenset({"email"})),
            ),
            _rule(
                "r2",
                priority=20,
                verb="redact",
                match=MatchCondition(detectors_any=frozenset({"credit_card"})),
            ),
        ]
    )
    d = compiled.evaluate(_ctx(detector_names=frozenset({"email", "credit_card"})))
    assert d.verb == "pin"  # pin outranks redact in VERB_PRECEDENCE
    assert d.redactions == ("credit_card", "email")  # sorted, both accumulated
    assert list(d.redactions) == sorted(d.redactions)


def test_throttle_verb_sets_throttle_flag() -> None:
    """throttle -> throttle=True."""
    compiled = compile_policy([_rule("t", verb="throttle")])
    d = compiled.evaluate(_ctx())
    assert d.throttle is True


def test_no_throttle_flag_when_absent() -> None:
    """throttle stays False when no throttle rule applied."""
    compiled = compile_policy([_rule("w", verb="warn")])
    assert compiled.evaluate(_ctx()).throttle is False


@pytest.mark.parametrize("verb", ["warn", "log"])
def test_warn_and_log_do_not_change_route(verb: str) -> None:
    """warn/log are headers/audit only — no route change, no block."""
    compiled = compile_policy([_rule("x", verb=verb)])
    d = compiled.evaluate(_ctx(route="local"))
    assert d.verb == verb
    assert d.route == "local"
    assert d.block is None
    assert d.redactions == ()


def test_no_match_default_decision() -> None:
    """No rule matches -> verb='route', rule=None, route=ctx.route, no block/redactions."""
    compiled = compile_policy(
        [_rule("nope", verb="pin", args={"target": "cloud"},
               match=MatchCondition(teams=frozenset({"legal"})))]
    )
    d = compiled.evaluate(_ctx(team="finance"))
    assert d.rule is None
    assert d.verb == "route"
    assert d.route == "local"
    assert d.block is None
    assert d.redactions == ()
    assert d.applied_rules == ()
    assert d.verbs == ()


# --- headers ----------------------------------------------------------------


def test_to_headers_emits_the_three_policy_headers() -> None:
    """to_headers() emits x-wayfinder-policy/-rule/-verb reflecting the decision."""
    compiled = compile_policy(
        [_rule("b", verb="pin", args={"target": "cloud"})]
    )
    d = compiled.evaluate(_ctx())
    headers = d.to_headers()
    assert set(headers) >= {
        "x-wayfinder-policy",
        "x-wayfinder-policy-rule",
        "x-wayfinder-policy-verb",
    }
    assert headers["x-wayfinder-policy"] == d.policy_hash
    assert headers["x-wayfinder-policy-rule"] == "b"
    assert headers["x-wayfinder-policy-verb"] == "pin"
    assert all(isinstance(v, str) for v in headers.values())


def test_decision_carries_compiled_policy_hash() -> None:
    """PolicyDecision.policy_hash == the CompiledPolicy's 12-hex hash."""
    compiled: CompiledPolicy = compile_policy([_rule("a", verb="warn")])
    d = compiled.evaluate(_ctx())
    assert d.policy_hash == compiled.policy_hash
    assert len(d.policy_hash) == 12
