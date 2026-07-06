"""Spec-first contract tests for audit append/get/query and replay.

Pins WF-DESIGN-0013 §2 (Audit / decision log) — the Query API and Replay API — and
Contracts invariant #5 (Audit replay determinism): ``replay(seq, policy=p)`` equals the
``PolicyDecision`` reconstructed from the record's stored signals, byte-for-byte, across
repeated calls and process restarts.

Ambiguities resolved to the strictest reading (noted per test):
- Pagination: §2 says ``next_after_seq`` is the last returned seq "when
  ``len(records) == limit``, else ``None``". The strict reading of the manifest's
  "None iff last page" is that phrasing — a *full* final page still returns a cursor,
  and the following query yields an empty page whose cursor is None. Both are asserted.
- ``ts_wall`` is stamped by the store on append (§2), so time-range tests inject a
  controllable clock via an explicit ``RecordStore(..., clock=...)`` passed as ``store=``.
- Replay reconstructs a ``PolicyContext`` from stored signals INCLUDING identity_kind,
  team, and tags (captured at decision time so replay never consults the current
  identity registry). ``model`` remains unstored, so the pinned replay fixtures still
  do not match on model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder_router.audit import (
    AUDIT_INDEX_FIELDS,
    AUDIT_SCHEMA_VERSION,
    AuditLog,
    AuditPage,
    AuditRecord,
    DetectorHit,
)
from wayfinder_router.policy import (
    CompiledPolicy,
    MatchCondition,
    Rule,
    compile_policy,
)
from wayfinder_router.store import RecordStore


class _Clock:
    """A controllable wall clock for time-range queries (mirrors test_cache._Clock)."""

    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _record(**overrides: Any) -> AuditRecord:
    """Build a valid AuditRecord (seq=0 pre-append); overrides replace individual fields."""
    fields: dict[str, Any] = dict(
        schema_version=AUDIT_SCHEMA_VERSION,
        seq=0,
        ts_wall=0.0,
        ts_mono=1.0,
        request_id="0123456789ab",
        identity_id="alice",
        identity_kind="human",
        team="finance",
        tags=("role:analyst",),
        vkey_id="team-finance",
        route="cloud",
        route_pre_policy="cloud",
        score=0.5,
        mode="scored",
        offline=False,
        budget_state=None,
        policy_id="org-baseline",
        policy_hash="abcdef012345",
        rule=None,
        verbs=("route",),
        detector_hits=(),
        prompt_tokens=10,
        completion_tokens=20,
        estimated=False,
        realized=0.001,
        baseline=0.010,
        saved=0.009,
        unit="usd",
        request_digest="0" * 64,
    )
    fields.update(overrides)
    return AuditRecord(**fields)


def _pin_alice_policy() -> CompiledPolicy:
    """A policy whose single rule pins identity 'alice' to 'cloud-approved' (matches on a stored signal)."""
    rule = Rule(
        id="alice-pin",
        priority=10,
        enabled=True,
        match=MatchCondition(identity_ids=frozenset({"alice"})),
        verb="pin",
        args={"target": "cloud-approved"},
    )
    return compile_policy([rule], policy_id="org-baseline")


def _block_on_detector_policy() -> CompiledPolicy:
    """A policy that blocks when an 'aws_access_key' detector hit is present in the record."""
    rule = Rule(
        id="block-secrets",
        priority=10,
        enabled=True,
        match=MatchCondition(detectors_any=frozenset({"aws_access_key"})),
        verb="block",
        args={"message": "credentials are not permitted"},
    )
    return compile_policy([rule], policy_id="org-baseline")


# --- append / get ---------------------------------------------------------------------
def test_append_returns_ascending_seq_and_get_round_trips(tmp_path: Path) -> None:
    """§2: append assigns a store seq; get(seq) returns the stamped record back."""
    log = AuditLog(str(tmp_path))
    s1 = log.append(_record(request_id="aaaaaaaaaaaa"))
    s2 = log.append(_record(request_id="bbbbbbbbbbbb"))
    assert s2 > s1
    got = log.get(s1)
    assert got is not None and got.seq == s1 and got.request_id == "aaaaaaaaaaaa"


def test_get_unknown_seq_returns_none(tmp_path: Path) -> None:
    """§2: get(seq) is None for a seq that was never appended."""
    log = AuditLog(str(tmp_path))
    log.append(_record())
    assert log.get(9_999_999) is None


# --- query filters --------------------------------------------------------------------
def test_query_filters_by_identity_id(tmp_path: Path) -> None:
    """§2: identity_id filter maps to store.query(equals=...) and returns only matches."""
    log = AuditLog(str(tmp_path))
    log.append(_record(identity_id="alice"))
    log.append(_record(identity_id="bob"))
    page = log.query(identity_id="alice")
    assert {r.identity_id for r in page.records} == {"alice"}


def test_query_filters_by_vkey_id(tmp_path: Path) -> None:
    """§2: vkey_id filter selects only records with that virtual-key id."""
    log = AuditLog(str(tmp_path))
    log.append(_record(vkey_id="k-a"))
    log.append(_record(vkey_id="k-b"))
    page = log.query(vkey_id="k-b")
    assert {r.vkey_id for r in page.records} == {"k-b"}


def test_query_filters_by_policy_id(tmp_path: Path) -> None:
    """§2: policy_id filter selects only records governed by that policy."""
    log = AuditLog(str(tmp_path))
    log.append(_record(policy_id="p-finance"))
    log.append(_record(policy_id="p-eng"))
    page = log.query(policy_id="p-finance")
    assert {r.policy_id for r in page.records} == {"p-finance"}


def test_query_filters_by_route(tmp_path: Path) -> None:
    """§2: route filter selects only records with that final chosen route."""
    log = AuditLog(str(tmp_path))
    log.append(_record(route="cloud"))
    log.append(_record(route="local"))
    page = log.query(route="local")
    assert {r.route for r in page.records} == {"local"}


def test_query_filters_by_time_range(tmp_path: Path) -> None:
    """§2: start_ts/end_ts bound the window; ts_wall is stamped by the store's clock on append."""
    clock = _Clock()
    store = RecordStore(str(tmp_path), index_fields=AUDIT_INDEX_FIELDS, clock=clock)
    log = AuditLog(str(tmp_path), store=store)
    seqs = []
    stamps = []
    for _ in range(5):
        seqs.append(log.append(_record()))
        stamps.append(clock())
        clock.advance(10.0)
    # window covers the middle three appends (inclusive lower / exclusive upper is impl-defined;
    # choose a window strictly inside the stamped bounds so only the middle three qualify).
    page = log.query(start_ts=stamps[1], end_ts=stamps[3] + 0.001)
    got = {r.seq for r in page.records}
    assert got == {seqs[1], seqs[2], seqs[3]}


# --- ordering + pagination ------------------------------------------------------------
def test_query_returns_seq_ascending(tmp_path: Path) -> None:
    """§2: query ordering is always seq-ascending (== append / wall-clock order)."""
    log = AuditLog(str(tmp_path))
    for _ in range(6):
        log.append(_record())
    page = log.query()
    seqs = [r.seq for r in page.records]
    assert seqs == sorted(seqs)


def test_full_page_returns_last_seq_cursor_then_empty_last_page(tmp_path: Path) -> None:
    """§2: len(records)==limit ⇒ next_after_seq == last seq; the next query is the empty last page.

    Strict reading of "None iff last page": a full final page still yields a cursor, and
    only a page with len<limit (here, the trailing empty page) reports next_after_seq=None.
    """
    log = AuditLog(str(tmp_path))
    seqs = [log.append(_record()) for _ in range(4)]
    p1: AuditPage = log.query(limit=2)
    assert len(p1.records) == 2 and p1.next_after_seq == p1.records[-1].seq
    p2 = log.query(after_seq=p1.next_after_seq, limit=2)
    assert [r.seq for r in p2.records] == seqs[2:4]
    assert p2.next_after_seq == p2.records[-1].seq  # full page ⇒ cursor present
    p3 = log.query(after_seq=p2.next_after_seq, limit=2)
    assert p3.records == () and p3.next_after_seq is None  # short/empty page ⇒ last page


def test_partial_last_page_has_none_cursor(tmp_path: Path) -> None:
    """§2: len(records) < limit ⇒ next_after_seq is None (the page is genuinely last)."""
    log = AuditLog(str(tmp_path))
    for _ in range(3):
        log.append(_record())
    p1 = log.query(limit=2)
    assert len(p1.records) == 2 and p1.next_after_seq == p1.records[-1].seq
    p2 = log.query(after_seq=p1.next_after_seq, limit=2)
    assert len(p2.records) == 1 and p2.next_after_seq is None


# --- replay determinism (Contract #5) -------------------------------------------------
def test_replay_is_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    """Contract #5: replay(seq, policy) is byte-identical across repeated calls (pure evaluation)."""
    log = AuditLog(str(tmp_path))
    seq = log.append(_record(identity_id="alice", route_pre_policy="cloud"))
    policy = _pin_alice_policy()
    first = log.replay(seq, policy=policy)
    second = log.replay(seq, policy=policy)
    assert first == second
    assert first.route == "cloud-approved" and first.rule == "alice-pin"  # the pin rule fired


def test_replay_is_deterministic_across_close_and_reopen(tmp_path: Path) -> None:
    """Contract #5: replay is identical after a close()+reopen of the AuditLog (same seq, same policy)."""
    log = AuditLog(str(tmp_path))
    seq = log.append(_record(identity_id="alice", route_pre_policy="cloud"))
    policy = _pin_alice_policy()
    before = log.replay(seq, policy=policy)
    log.close()

    reopened = AuditLog(str(tmp_path))
    after = reopened.replay(seq, policy=policy)
    assert after == before


def test_replay_uses_stored_detector_hits_never_rescans_text(tmp_path: Path) -> None:
    """Contract #5 / §2 honest-scope: replay decides from the record's stored detector hits alone.

    The record carries a detector_hit for text that no longer exists anywhere (no prompt is
    stored, by constraint). A block rule keyed on that detector name must still fire on replay,
    proving replay consumes stored signals rather than re-scanning any text.
    """
    log = AuditLog(str(tmp_path))
    rec = _record(
        identity_id="mallory",
        detector_hits=(DetectorHit(name="aws_access_key", count=1, spans=((0, 20),)),),
    )
    seq = log.append(rec)
    decision = log.replay(seq, policy=_block_on_detector_policy())
    assert decision.verb == "block" and decision.rule == "block-secrets"
    assert decision.block is not None and decision.block.status == 403


def test_replay_no_match_defaults_to_route_of_stored_pre_policy(tmp_path: Path) -> None:
    """§2/§3: when no rule matches, replay yields verb='route' at the record's route_pre_policy."""
    log = AuditLog(str(tmp_path))
    seq = log.append(_record(identity_id="nobody", route_pre_policy="local"))
    decision = log.replay(seq, policy=_pin_alice_policy())  # alice-pin cannot match 'nobody'
    assert decision.verb == "route" and decision.rule is None and decision.route == "local"
