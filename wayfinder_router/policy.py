"""Compiled-once policy engine — deterministic total order over rules (WF-DESIGN-0013 §3).

Pure, offline, no model call (WF-ADR-0001): the governance spine (WF-ROADMAP-0012, the
enforcement layer over the detectors of WF-ROADMAP-0011 §1) turns a set of rules and a
request's already-resolved signals into one reproducible decision — a verb, a route, a
block/redact/throttle disposition, and a policy hash stamped on ``x-wayfinder-policy`` and
into every ``AuditRecord``. The verb vocabulary is the gateway's own (``gateway.py``) plus
the content verbs of WF-ROADMAP-0011 §1.

This module imports nothing from ``wayfinder_router``: ``PolicyContext`` carries only plain
``str``/``frozenset[str]`` names (never ``identity.Principal`` or ``detectors.DetectorHit``),
so ``policy`` stays independent and lazily reachable (never eagerly imported by the package
``__init__``). Stdlib only.

Total order, ties impossible by construction: rules sort ascending by ``order_key =
(priority, id)``; ``id`` is unique within a policy (duplicate → ``PolicyError`` at compile),
so ``order_key`` is a strict total order and no two rules ever compare equal. The terminal
rule is the applied rule minimizing ``(VERB_RANK[verb], order_key)`` — the second component
alone already breaks every tie, so the argmin is unique regardless of verb collisions.

Compile-once discipline (WF-DESIGN-0013 §3, Gate 1): all sorting, hashing, and the candidate
prefilter index are built in ``compile_policy``/``policy_from_toml`` at load/hot-reload only.
``evaluate`` recompiles nothing, re-sorts nothing, and allocates only the returned decision
and a bounded candidate set. The prefilter buckets each rule under its most-selective
membership dimension (pure-wildcard and score-only/``*_all``-only rules stay in an
always-scan bucket); it is a pure *superset* filter — every surviving candidate is confirmed
by ``MatchCondition.matches``, so the emitted ``applied_rules`` is byte-identical to a linear
walk over the sorted rules, but the walked set stays bounded by request fanout rather than
total rule count.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

POLICY_SCHEMA_VERSION: int = 1

# The gateway's existing verb vocabulary plus the content verbs (WF-ROADMAP-0011 §1).
VERBS: tuple[str, ...] = (
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

# Terminal-verb precedence, HIGH → LOW: the terminal rule minimizes (VERB_RANK[verb], order_key).
VERB_PRECEDENCE: tuple[str, ...] = (
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

# O(1) precedence lookup built once — never call VERB_PRECEDENCE.index in the hot path.
VERB_RANK: dict[str, int] = {verb: i for i, verb in enumerate(VERB_PRECEDENCE)}

# Verbs that rewrite the route (to args["target"]) or block the request, by *terminal* verb only.
_ROUTE_VERBS: frozenset[str] = frozenset({"pin", "clamp", "degrade"})
_BLOCK_VERBS: frozenset[str] = frozenset({"block", "deny"})

# Single-value membership dimensions usable as a prefilter discriminator (ctx supplies one value).
_SCALAR_DIMS: tuple[str, ...] = (
    "identity_ids",
    "identity_kinds",
    "teams",
    "models",
    "routes",
)
# Set-membership (ANY) dimensions usable as a discriminator (ctx supplies a set of values).
_ANY_DIMS: tuple[str, ...] = ("tags_any", "detectors_any")
# Dimensions that can seed the index; a rule wildcard on all of these is an always-scan rule.
_INDEXED_DIMS: tuple[str, ...] = _SCALAR_DIMS + _ANY_DIMS

# The MatchCondition frozenset-valued clauses, in canonical order (drives TOML parse + hashing).
_MATCH_SET_FIELDS: tuple[str, ...] = (
    "identity_ids",
    "identity_kinds",
    "teams",
    "tags_any",
    "tags_all",
    "models",
    "routes",
    "detectors_any",
    "detectors_all",
)


class PolicyError(Exception):
    """Raised for any policy schema, verb, or duplicate-id violation at compile time."""


@dataclass(frozen=True)
class MatchCondition:
    """Conjunction of match clauses; an empty set / None clause is an absent wildcard."""

    identity_ids: frozenset[str] = frozenset()
    identity_kinds: frozenset[str] = frozenset()
    teams: frozenset[str] = frozenset()
    tags_any: frozenset[str] = frozenset()
    tags_all: frozenset[str] = frozenset()
    models: frozenset[str] = frozenset()
    routes: frozenset[str] = frozenset()
    detectors_any: frozenset[str] = frozenset()
    detectors_all: frozenset[str] = frozenset()
    score_min: float | None = None
    score_max: float | None = None

    def matches(self, ctx: PolicyContext) -> bool:
        """Whether every present clause matches the context (AND across clauses)."""
        # Each clause is skipped when wildcard; a None team can never satisfy a present clause.
        if self.identity_ids and ctx.identity_id not in self.identity_ids:
            return False
        if self.identity_kinds and ctx.identity_kind not in self.identity_kinds:
            return False
        if self.teams and ctx.team not in self.teams:
            return False
        if self.tags_any and not (ctx.tags & self.tags_any):
            return False
        if self.tags_all and not (self.tags_all <= ctx.tags):
            return False
        if self.models and ctx.model not in self.models:
            return False
        if self.routes and ctx.route not in self.routes:
            return False
        if self.detectors_any and not (ctx.detector_names & self.detectors_any):
            return False
        if self.detectors_all and not (self.detectors_all <= ctx.detector_names):
            return False
        if self.score_min is not None and ctx.score < self.score_min:
            return False
        if self.score_max is not None and ctx.score > self.score_max:
            return False
        return True


@dataclass(frozen=True)
class Rule:
    """A single policy rule; ``id`` is unique within a policy, enforced at compile."""

    id: str
    priority: int
    enabled: bool
    match: MatchCondition
    verb: str
    # A plain dict keeps Rule usable via args.get(); Rule is never hashed nor set-membered.
    args: Mapping[str, str]

    def order_key(self) -> tuple[int, str]:
        """Return the strict total-order key (priority, id) — ties impossible."""
        return (self.priority, self.id)


@dataclass(frozen=True)
class PolicyContext:
    """A request's already-resolved signals — plain names only, no wayfinder imports."""

    identity_id: str
    identity_kind: str
    team: str | None
    tags: frozenset[str]
    vkey_id: str | None
    model: str
    route: str
    score: float
    detector_names: frozenset[str]


@dataclass(frozen=True)
class BlockOutcome:
    """A structured 403 for block/deny; the distinct originating verb is recorded."""

    status: int
    message: str
    verb: str


@dataclass(frozen=True)
class PolicyDecision:
    """The reproducible outcome of evaluating one context against a compiled policy."""

    policy_hash: str
    rule: str | None
    verb: str
    route: str
    verbs: tuple[str, ...]
    applied_rules: tuple[str, ...]
    block: BlockOutcome | None
    redactions: tuple[str, ...]
    throttle: bool

    def to_headers(self) -> dict[str, str]:
        """Emit the three policy headers; a None terminal rule is rendered as an empty string."""
        return {
            "x-wayfinder-policy": self.policy_hash,
            "x-wayfinder-policy-rule": self.rule if self.rule is not None else "",
            "x-wayfinder-policy-verb": self.verb,
        }


@dataclass(frozen=True)
class CompiledPolicy:
    """An immutable, pre-sorted, pre-indexed policy ready for allocation-lean evaluation."""

    policy_id: str
    policy_hash: str
    rules: tuple[Rule, ...]
    # Auxiliary compile-time prefilter: value -> rule indices, plus always-scan indices. These
    # are a superset filter only; matches() confirms, so observable semantics equal a linear walk.
    _index: dict[tuple[str, str], tuple[int, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _always: tuple[int, ...] = field(default=(), repr=False, compare=False)

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        """Evaluate ctx against the sorted rules; return the deterministic decision."""
        # Gather a bounded candidate superset from the prefilter, then confirm with matches().
        candidates: set[int] = set(self._always)
        index = self._index
        for dim, value in (
            ("identity_ids", ctx.identity_id),
            ("identity_kinds", ctx.identity_kind),
            ("models", ctx.model),
            ("routes", ctx.route),
        ):
            hit = index.get((dim, value))
            if hit:
                candidates.update(hit)
        if ctx.team is not None:
            hit = index.get(("teams", ctx.team))
            if hit:
                candidates.update(hit)
        for tag in ctx.tags:
            hit = index.get(("tags_any", tag))
            if hit:
                candidates.update(hit)
        for detector in ctx.detector_names:
            hit = index.get(("detectors_any", detector))
            if hit:
                candidates.update(hit)

        # sorted() over int indices restores order_key order (rules is already order_key-sorted).
        applied = [
            self.rules[i]
            for i in sorted(candidates)
            if self.rules[i].match.matches(ctx)
        ]

        if not applied:
            return PolicyDecision(
                policy_hash=self.policy_hash,
                rule=None,
                verb="route",
                route=ctx.route,
                verbs=(),
                applied_rules=(),
                block=None,
                redactions=(),
                throttle=False,
            )

        # Terminal = unique argmin; (priority, id) alone is already a strict total order.
        terminal = min(
            applied, key=lambda r: (VERB_RANK[r.verb], r.priority, r.id)
        )

        # Content verbs accumulate independently of the terminal choice.
        redacted: set[str] = set()
        throttle = False
        for rule in applied:
            if rule.verb == "redact":
                redacted |= (
                    rule.match.detectors_any | rule.match.detectors_all
                ) & ctx.detector_names
            elif rule.verb == "throttle":
                throttle = True

        # Route and block are set by the terminal verb only.
        if terminal.verb in _ROUTE_VERBS:
            route = terminal.args.get("target", ctx.route)
        else:
            route = ctx.route
        block: BlockOutcome | None = None
        if terminal.verb in _BLOCK_VERBS:
            block = BlockOutcome(
                status=403,
                message=terminal.args.get("message", ""),
                verb=terminal.verb,
            )

        return PolicyDecision(
            policy_hash=self.policy_hash,
            rule=terminal.id,
            verb=terminal.verb,
            route=route,
            verbs=tuple(r.verb for r in applied),
            applied_rules=tuple(r.id for r in applied),
            block=block,
            redactions=tuple(sorted(redacted)),
            throttle=throttle,
        )


def compile_policy(
    rules: Iterable[Rule], *, policy_id: str = "default"
) -> CompiledPolicy:
    """Compile rules into a sorted, hashed, indexed policy; reject duplicate ids."""
    seen: set[str] = set()
    enabled: list[Rule] = []
    for rule in rules:
        if rule.id in seen:
            raise PolicyError(f"duplicate rule id: {rule.id!r}")
        seen.add(rule.id)
        if rule.verb not in VERBS:
            raise PolicyError(f"rule {rule.id!r}: unknown verb {rule.verb!r}")
        if rule.enabled:
            enabled.append(rule)
    sorted_rules = tuple(sorted(enabled, key=Rule.order_key))
    policy_hash = _policy_hash(sorted_rules)
    index, always = _build_index(sorted_rules)
    return CompiledPolicy(
        policy_id=policy_id,
        policy_hash=policy_hash,
        rules=sorted_rules,
        _index=index,
        _always=always,
    )


def policy_from_toml(text: str) -> CompiledPolicy:
    """Parse the [policy]/[policy.rules.<id>] TOML into an active CompiledPolicy or raise."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"invalid policy TOML: {exc}") from exc
    policy = data.get("policy")
    if not isinstance(policy, dict):
        raise PolicyError("no [policy] table: the policy stage is inactive")
    # [policy].enabled defaults false and gates compilation — an inactive policy never compiles.
    if policy.get("enabled", False) is not True:
        raise PolicyError("[policy].enabled is false or absent: the policy stage is inactive")
    policy_id = str(policy.get("id", "default"))
    rules_table = policy.get("rules", {})
    if not isinstance(rules_table, dict):
        raise PolicyError("[policy.rules] must be a table of rule tables")
    rules = [
        _rule_from_table(str(rule_id), table)
        for rule_id, table in rules_table.items()
    ]
    return compile_policy(rules, policy_id=policy_id)


def _rule_from_table(rule_id: str, table: object) -> Rule:
    """Build one Rule from its TOML table; every error names the offending rule id."""
    if not isinstance(table, dict):
        raise PolicyError(f"rule {rule_id!r}: expected a table")
    if "verb" not in table:
        raise PolicyError(f"rule {rule_id!r}: missing required 'verb' key")
    verb = table["verb"]
    if not isinstance(verb, str) or verb not in VERBS:
        raise PolicyError(f"rule {rule_id!r}: unknown verb {verb!r}")
    priority = table.get("priority", 100)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise PolicyError(
            f"rule {rule_id!r}: priority must be int, got {type(priority).__name__}"
        )
    enabled = table.get("enabled", True)
    if not isinstance(enabled, bool):
        raise PolicyError(
            f"rule {rule_id!r}: enabled must be bool, got {type(enabled).__name__}"
        )
    args: dict[str, str] = {}
    for key in ("target", "message"):
        if key in table:
            value = table[key]
            if not isinstance(value, str):
                raise PolicyError(
                    f"rule {rule_id!r}: {key} must be str, got {type(value).__name__}"
                )
            args[key] = value
    match = _match_from_table(rule_id, table)
    return Rule(
        id=rule_id,
        priority=priority,
        enabled=enabled,
        match=match,
        verb=verb,
        args=args,
    )


def _match_from_table(rule_id: str, table: Mapping[str, object]) -> MatchCondition:
    """Build a MatchCondition from a rule table's match-field keys, validating types."""
    kwargs: dict[str, object] = {}
    for name in _MATCH_SET_FIELDS:
        if name in table:
            value = table[name]
            if not isinstance(value, (list, tuple)) or not all(
                isinstance(item, str) for item in value
            ):
                raise PolicyError(f"rule {rule_id!r}: {name} must be a list of strings")
            kwargs[name] = frozenset(value)
    for name in ("score_min", "score_max"):
        if name in table:
            value = table[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PolicyError(f"rule {rule_id!r}: {name} must be a number")
            kwargs[name] = float(value)
    return MatchCondition(**kwargs)  # type: ignore[arg-type]


def _build_index(
    sorted_rules: tuple[Rule, ...],
) -> tuple[dict[tuple[str, str], tuple[int, ...]], tuple[int, ...]]:
    """Build the superset prefilter: (dim, value) -> rule indices, plus always-scan indices."""
    buckets: dict[tuple[str, str], list[int]] = {}
    always: list[int] = []
    for i, rule in enumerate(sorted_rules):
        discriminator = _discriminator(rule.match)
        if discriminator is None:
            always.append(i)
            continue
        dim, values = discriminator
        for value in values:
            buckets.setdefault((dim, value), []).append(i)
    index = {key: tuple(indices) for key, indices in buckets.items()}
    return index, tuple(always)


def _discriminator(match: MatchCondition) -> tuple[str, frozenset[str]] | None:
    """Pick a rule's smallest present membership clause, or None if it must always be scanned."""
    # A rule wildcard on every membership dimension (or bounded only by score_*/tags_all/
    # detectors_all) has no prefilter key and must be an unconditional candidate every request.
    best_name: str | None = None
    best_size = 0
    for name in _INDEXED_DIMS:
        values: frozenset[str] = getattr(match, name)
        if values and (best_name is None or len(values) < best_size):
            best_name = name
            best_size = len(values)
    if best_name is None:
        return None
    return best_name, getattr(match, best_name)


def _policy_hash(sorted_rules: tuple[Rule, ...]) -> str:
    """Return sha256 over the canonical, order-invariant, disabled-excluded rules[:12]."""
    payload = "\n".join(_canon_rule(rule) for rule in sorted_rules).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _canon_rule(rule: Rule) -> str:
    """Canonical per-rule JSON with every collection sorted — no iteration-order dependence."""
    match = rule.match
    obj = {
        "id": rule.id,
        "priority": rule.priority,
        "verb": rule.verb,
        "args": dict(sorted(rule.args.items())),
        "match": {
            "identity_ids": sorted(match.identity_ids),
            "identity_kinds": sorted(match.identity_kinds),
            "teams": sorted(match.teams),
            "tags_any": sorted(match.tags_any),
            "tags_all": sorted(match.tags_all),
            "models": sorted(match.models),
            "routes": sorted(match.routes),
            "detectors_any": sorted(match.detectors_any),
            "detectors_all": sorted(match.detectors_all),
            "score_min": match.score_min,
            "score_max": match.score_max,
        },
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
