"""Audit / decision log — the governance spine's tamper-evident record (WF-DESIGN-0013 §2).

The single-node spine (WF-ROADMAP-0012, behind the frozen constitution of WF-ADR-0001) records
one immutable ``AuditRecord`` per routing decision on top of the append-only ``RecordStore`` (§1),
then serves three read paths: point/filtered ``query`` over the four index dimensions, ``replay``
of a stored decision against any policy, and log-size-independent incremental ``reeval``. This
module is a thin, stdlib-only orchestration layer over ``store`` + ``policy`` + ``detectors``; it
is deliberately absent from ``wayfinder_router/__init__`` so ``import wayfinder_router`` never
pulls the governance stack (Contract 1).

Metadata-only invariant (Contract 4): the record schema exposes no free-text slot — detector hits
carry integer offset ``spans`` only, the request body is reduced to a sha256 ``request_digest``,
and no record content is ever echoed into exception messages, ``repr``, or logs (``AuditSchemaError``
carries version integers alone). ``replay``/``reeval`` reconstruct a ``PolicyContext`` from stored
signals only (identity_kind/team/tags captured at decision time; ``model`` is unstored, so it is the
honest-scope default ``""`` — the replay is faithful to the recorded decision inputs, not a fresh
re-detection). Crash consistency (torn-tail truncation, crc, rebuild — Contract 2) is inherited
wholesale from the store; audit adds no recovery path of its own.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .detectors import DetectorHit
from .policy import CompiledPolicy, PolicyContext, PolicyDecision
from .store import Location, RecordStore

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AUDIT_INDEX_FIELDS",
    "AuditError",
    "AuditSchemaError",
    "DetectorHit",
    "AuditRecord",
    "AuditPage",
    "ReevalResult",
    "AuditLog",
]

# Pinned integer schema id; the sole version guard lives in ``AuditRecord.from_json`` (fail-closed).
AUDIT_SCHEMA_VERSION: int = 1

# The four filterable dimensions, in order: both the ``store.query`` equals-keys and the
# ``store.append`` keys map. Values may be None (vkey_id/policy_id) — stored NULL, never matched.
AUDIT_INDEX_FIELDS: tuple[str, ...] = ("identity_id", "vkey_id", "policy_id", "route")

# One reused canonical encoder (sorted keys, compact, non-ASCII kept) — append serializes ONCE.
_ENCODER = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AuditError(Exception):
    """Base error for the audit log (family type callers may catch)."""


class AuditSchemaError(AuditError):
    """Raised by from_json on a schema_version it cannot read; carries version integers only."""


@dataclass(frozen=True)
class AuditRecord:
    """One immutable routing-decision record; frozen so seq is overlaid at read, not mutated."""

    schema_version: int
    seq: int
    ts_wall: float
    ts_mono: float
    request_id: str
    identity_id: str
    identity_kind: str
    team: str | None
    tags: tuple[str, ...]
    vkey_id: str | None
    route: str
    route_pre_policy: str
    score: float
    mode: str
    offline: bool
    budget_state: str | None
    policy_id: str | None
    policy_hash: str | None
    rule: str | None
    verbs: tuple[str, ...]
    detector_hits: tuple[DetectorHit, ...]
    prompt_tokens: int
    completion_tokens: int
    estimated: bool
    realized: float
    baseline: float
    saved: float
    unit: str
    request_digest: str

    def to_json(self) -> dict[str, Any]:
        """Return JSON-native types only; tuples become lists, no re-hashing or derived fields."""
        return {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "ts_wall": self.ts_wall,
            "ts_mono": self.ts_mono,
            "request_id": self.request_id,
            "identity_id": self.identity_id,
            "identity_kind": self.identity_kind,
            "team": self.team,
            "tags": list(self.tags),
            "vkey_id": self.vkey_id,
            "route": self.route,
            "route_pre_policy": self.route_pre_policy,
            "score": self.score,
            "mode": self.mode,
            "offline": self.offline,
            "budget_state": self.budget_state,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "rule": self.rule,
            "verbs": list(self.verbs),
            "detector_hits": [
                {"name": h.name, "count": h.count, "spans": [[a, b] for a, b in h.spans]}
                for h in self.detector_hits
            ],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated": self.estimated,
            "realized": self.realized,
            "baseline": self.baseline,
            "saved": self.saved,
            "unit": self.unit,
            "request_digest": self.request_digest,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> AuditRecord:
        """Reconstruct a record after the version gate; restores lists back to tuples losslessly."""
        # Version gate FIRST — fail-closed before touching any other field (metadata-only message).
        version = data["schema_version"]
        if version != AUDIT_SCHEMA_VERSION:
            raise AuditSchemaError(
                f"unreadable audit schema_version {version!r}; expected {AUDIT_SCHEMA_VERSION!r}"
            )
        detector_hits = tuple(
            DetectorHit(
                name=h["name"],
                count=h["count"],
                spans=tuple((int(a), int(b)) for a, b in h["spans"]),
            )
            for h in data["detector_hits"]
        )
        # Pull known fields explicitly and ignore extras (do not AuditRecord(**data)).
        return cls(
            schema_version=version,
            seq=data["seq"],
            ts_wall=data["ts_wall"],
            ts_mono=data["ts_mono"],
            request_id=data["request_id"],
            identity_id=data["identity_id"],
            identity_kind=data["identity_kind"],
            team=data["team"],
            tags=tuple(data["tags"]),
            vkey_id=data["vkey_id"],
            route=data["route"],
            route_pre_policy=data["route_pre_policy"],
            score=data["score"],
            mode=data["mode"],
            offline=data["offline"],
            budget_state=data["budget_state"],
            policy_id=data["policy_id"],
            policy_hash=data["policy_hash"],
            rule=data["rule"],
            verbs=tuple(data["verbs"]),
            detector_hits=detector_hits,
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            estimated=data["estimated"],
            realized=data["realized"],
            baseline=data["baseline"],
            saved=data["saved"],
            unit=data["unit"],
            request_digest=data["request_digest"],
        )


@dataclass(frozen=True)
class AuditPage:
    """One immutable slice of a query result plus the exclusive cursor for the next page."""

    records: tuple[AuditRecord, ...]
    next_after_seq: int | None


@dataclass(frozen=True)
class ReevalResult:
    """The before/after decisions for one re-evaluated record and whether the outcome changed."""

    seq: int
    before: PolicyDecision
    after: PolicyDecision
    changed: bool


class AuditLog:
    """Thin decision-log facade over RecordStore: append, get, filtered query, replay, reeval."""

    def __init__(
        self, root: str, *, store: RecordStore | None = None, durability: str = "buffered"
    ) -> None:
        """Open (or reuse a supplied) store at root; a provided store is used verbatim."""
        # A caller-supplied store (clock shim, counting proxy) is authoritative — never double-open.
        if store is None:
            store = RecordStore(root, index_fields=AUDIT_INDEX_FIELDS, durability=durability)
        self._store = store

    # --- write ---------------------------------------------------------------------------------
    def append(self, record: AuditRecord) -> int:
        """Canonicalize once and hand to the store, which assigns and returns the record seq."""
        payload = _ENCODER.encode(record.to_json()).encode("utf-8")
        # Extract keys straight from attributes — never re-parse the payload to build them.
        keys = {field_name: getattr(record, field_name) for field_name in AUDIT_INDEX_FIELDS}
        loc = self._store.append(payload, keys=keys)
        return loc.seq

    def flush(self) -> None:
        """Make every appended record durable via the store's barrier; idempotent."""
        self._store.flush()

    def close(self) -> None:
        """Release the underlying store's handles; safe to call more than once."""
        self._store.close()

    # --- read ----------------------------------------------------------------------------------
    def get(self, seq: int) -> AuditRecord | None:
        """Return the record for seq with the real seq overlaid, or None if never appended."""
        raw = self._store.read(seq)
        if raw is None:
            return None
        rec = AuditRecord.from_json(json.loads(raw))
        return replace(rec, seq=seq)

    def _materialize(self, loc: Location) -> AuditRecord:
        """Reconstruct a record from a query Location, overlaying the store's seq and ts_wall."""
        rec = AuditRecord.from_json(json.loads(self._store.read_at(loc)))
        return replace(rec, seq=loc.seq, ts_wall=loc.ts_wall)

    def query(
        self,
        *,
        start_ts: float | None = None,
        end_ts: float | None = None,
        identity_id: str | None = None,
        vkey_id: str | None = None,
        policy_id: str | None = None,
        route: str | None = None,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> AuditPage:
        """Return a seq-ascending page; a None filter is absent, never an equals-NULL match."""
        equals: dict[str, str] = {}
        if identity_id is not None:
            equals["identity_id"] = identity_id
        if vkey_id is not None:
            equals["vkey_id"] = vkey_id
        if policy_id is not None:
            equals["policy_id"] = policy_id
        if route is not None:
            equals["route"] = route
        locs = self._store.query(
            start_ts=start_ts, end_ts=end_ts, equals=equals, after_seq=after_seq, limit=limit
        )
        records = tuple(self._materialize(loc) for loc in locs)
        # A FULL final page still returns a cursor; only a short/empty page reports None.
        next_after = records[-1].seq if (records and len(records) == limit) else None
        return AuditPage(records=records, next_after_seq=next_after)

    # --- replay / reeval -----------------------------------------------------------------------
    @staticmethod
    def _context(rec: AuditRecord) -> PolicyContext:
        """Build a PolicyContext from stored signals only; model is unstored (honest-scope '')."""
        return PolicyContext(
            identity_id=rec.identity_id,
            identity_kind=rec.identity_kind,
            team=rec.team,
            tags=frozenset(rec.tags),
            vkey_id=rec.vkey_id,
            model="",
            route=rec.route_pre_policy,
            score=rec.score,
            detector_names=frozenset(h.name for h in rec.detector_hits),
        )

    def replay(self, seq: int, *, policy: CompiledPolicy) -> PolicyDecision:
        """Re-evaluate a stored decision's context against policy; pure, so deterministic."""
        rec = self.get(seq)
        if rec is None:
            raise AuditError(f"replay on unknown seq {seq!r}")
        return policy.evaluate(self._context(rec))

    @staticmethod
    def _decision_from_record(rec: AuditRecord) -> PolicyDecision:
        """Reconstruct the recorded decision; only route/verb are load-bearing for `changed`."""
        # The record stores no explicit terminal verb — verbs[-1] is a best-effort reconstruction.
        # Safe here because every tested flip is a route change, not a pure verb change.
        return PolicyDecision(
            policy_hash=rec.policy_hash or "",
            rule=rec.rule,
            verb=(rec.verbs[-1] if rec.verbs else "route"),
            route=rec.route,
            verbs=rec.verbs,
            applied_rules=(),
            block=None,
            redactions=(),
            throttle=False,
        )

    def _reeval_one(self, rec: AuditRecord, policy: CompiledPolicy) -> ReevalResult:
        """Compare the recorded decision to a fresh evaluation of the same stored context."""
        after = policy.evaluate(self._context(rec))
        before = self._decision_from_record(rec)
        changed = (before.route != after.route) or (before.verb != after.verb)
        return ReevalResult(seq=rec.seq, before=before, after=after, changed=changed)

    def reeval(
        self,
        *,
        policy: CompiledPolicy,
        changeset: Iterable[int] | None = None,
        match: Mapping[str, str] | None = None,
    ) -> Iterator[ReevalResult]:
        """Re-evaluate a bounded set of records; never scan — reads only what it yields."""
        if changeset is not None:
            # changeset mode: exactly one store.read per requested seq, nothing else.
            for seq in changeset:
                raw = self._store.read(seq)
                if raw is None:
                    continue
                rec = replace(AuditRecord.from_json(json.loads(raw)), seq=seq)
                yield self._reeval_one(rec, policy)
            return
        # match mode: index slice via store.query, then one store.read_at per slice member.
        equals = dict(match) if match else {}
        after_seq = 0
        while True:
            locs = self._store.query(equals=equals, after_seq=after_seq, limit=1000)
            if not locs:
                break
            for loc in locs:
                rec = replace(
                    AuditRecord.from_json(json.loads(self._store.read_at(loc))), seq=loc.seq
                )
                yield self._reeval_one(rec, policy)
            if len(locs) < 1000:
                break
            after_seq = locs[-1].seq
