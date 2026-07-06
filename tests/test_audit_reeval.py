"""Spec-first contract tests for incremental audit re-evaluation.

Pins WF-DESIGN-0013 §2 (Incremental re-eval API — log-size-independent) and Contracts
invariant #6 (Re-eval boundedness): ``reeval(changeset=[...])`` reads exactly the changeset
records (asserted via a counting store shim); result count == changeset size; and both
``changeset=`` and ``match=`` modes are bounded by the matched set, not by total N.

Ambiguities resolved to the strictest reading (noted per test):
- The counting shim wraps a real ``RecordStore`` (§2: ``AuditLog(root, *, store=...)``) and
  counts only record *materializations* (``read`` / ``read_at``). append/query/scan pass
  through un-counted; the counter is reset immediately before ``reeval`` so only re-eval's
  reads are measured. Boundedness = materialized reads equal the matched-set size.
- ``ReevalResult.changed`` is defined (§2) as ``before.route != after.route or
  before.verb != after.verb``. The pinned fixtures drive every flip through ``route`` (the
  unambiguously stored field) so the assertions do not depend on how ``before.verb`` is
  reconstructed from the record's ``verbs``/``rule``.
- Log-size independence is asserted at the two sizes the design names (100 vs 10,000 total
  records): identical answers and identical materialized-read counts for a fixed changeset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from wayfinder_router.audit import (
    AUDIT_INDEX_FIELDS,
    AUDIT_SCHEMA_VERSION,
    AuditLog,
    AuditRecord,
    ReevalResult,
)
from wayfinder_router.policy import (
    CompiledPolicy,
    MatchCondition,
    Rule,
    compile_policy,
)
from wayfinder_router.store import Location, RecordStore

SMALL = 100
BIG = 10_000


class CountingStore:
    """A RecordStore-compatible proxy that counts record materializations (read/read_at).

    Everything except read/read_at is forwarded verbatim to the wrapped store, so the
    AuditLog above it is unaware of the shim. ``reads`` isolates re-eval's data access
    from setup appends/queries, letting the test assert O(changeset), not O(total N).
    """

    def __init__(self, inner: RecordStore) -> None:
        self._inner = inner
        self.reads = 0

    def read(self, seq: int) -> bytes | None:
        self.reads += 1
        return self._inner.read(seq)

    def read_at(self, loc: Location) -> bytes:
        self.reads += 1
        return self._inner.read_at(loc)

    def __getattr__(self, name: str) -> Any:  # forward append/query/scan/flush/close/etc.
        return getattr(self._inner, name)


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
    """Pins identity 'alice' to 'cloud-approved' — flips any record whose stored route is 'cloud'."""
    rule = Rule(
        id="alice-pin",
        priority=10,
        enabled=True,
        match=MatchCondition(identity_ids=frozenset({"alice"})),
        verb="pin",
        args={"target": "cloud-approved"},
    )
    return compile_policy([rule], policy_id="org-baseline")


def _empty_policy() -> CompiledPolicy:
    """A policy with no rules — evaluate() always defaults to verb='route' at ctx.route."""
    return compile_policy([], policy_id="org-baseline")


def _counting_log(tmp_path: Path) -> tuple[AuditLog, CountingStore]:
    inner = RecordStore(str(tmp_path), index_fields=AUDIT_INDEX_FIELDS)
    proxy = CountingStore(inner)
    return AuditLog(str(tmp_path), store=proxy), proxy


# --- changeset: exact result count + seq correspondence -------------------------------
def test_reeval_changeset_yields_exactly_changeset_size(tmp_path: Path) -> None:
    """Contract #6: reeval(changeset=[...]) yields exactly one ReevalResult per requested seq."""
    log = AuditLog(str(tmp_path))
    seqs = [log.append(_record(identity_id="alice")) for _ in range(8)]
    changeset = [seqs[1], seqs[4], seqs[6]]
    results = list(log.reeval(policy=_pin_alice_policy(), changeset=changeset))
    assert len(results) == len(changeset)
    assert all(isinstance(r, ReevalResult) for r in results)


def test_reeval_changeset_result_seqs_match_the_input(tmp_path: Path) -> None:
    """§2: the yielded ReevalResults correspond exactly to the changeset seqs (no more, no fewer)."""
    log = AuditLog(str(tmp_path))
    seqs = [log.append(_record()) for _ in range(6)]
    changeset = [seqs[0], seqs[2], seqs[5]]
    results = list(log.reeval(policy=_empty_policy(), changeset=changeset))
    assert {r.seq for r in results} == set(changeset)


# --- changed semantics ----------------------------------------------------------------
def test_reeval_changed_true_when_route_flips(tmp_path: Path) -> None:
    """§2: changed is True when the new policy moves the route away from the record's stored route."""
    log = AuditLog(str(tmp_path))
    # stored decision routed to 'cloud'; the pin policy now re-routes alice to 'cloud-approved'.
    seq = log.append(_record(identity_id="alice", route="cloud", route_pre_policy="cloud",
                             verbs=("route",), rule=None))
    (res,) = list(log.reeval(policy=_pin_alice_policy(), changeset=[seq]))
    assert res.changed is True
    assert res.before.route == "cloud" and res.after.route == "cloud-approved"


def test_reeval_changed_false_when_policy_reproduces_the_record(tmp_path: Path) -> None:
    """§2: changed is False when re-evaluation reproduces the recorded route and verb."""
    log = AuditLog(str(tmp_path))
    seq = log.append(_record(identity_id="bob", route="cloud", route_pre_policy="cloud",
                             verbs=("route",), rule=None))
    # empty policy => verb='route' at ctx.route=='cloud' == the record's stored decision.
    (res,) = list(log.reeval(policy=_empty_policy(), changeset=[seq]))
    assert res.changed is False
    assert res.after.route == "cloud" and res.after.verb == "route"


def test_reeval_mixed_changeset_flips_only_matching_records(tmp_path: Path) -> None:
    """§2: within one changeset, only records whose signals match the edited rule flip."""
    log = AuditLog(str(tmp_path))
    a = log.append(_record(identity_id="alice", route="cloud", route_pre_policy="cloud"))
    b = log.append(_record(identity_id="bob", route="cloud", route_pre_policy="cloud"))
    by_seq = {r.seq: r for r in log.reeval(policy=_pin_alice_policy(), changeset=[a, b])}
    assert by_seq[a].changed is True and by_seq[a].after.route == "cloud-approved"
    assert by_seq[b].changed is False and by_seq[b].after.route == "cloud"


# --- boundedness: changeset (Contract #6) ---------------------------------------------
def test_reeval_changeset_reads_exactly_changeset_size(tmp_path: Path) -> None:
    """Contract #6: reeval(changeset=[...]) materializes exactly len(changeset) records."""
    log, proxy = _counting_log(tmp_path)
    seqs = [log.append(_record(identity_id="alice")) for _ in range(SMALL)]
    changeset = seqs[10:15]  # five records out of a hundred
    proxy.reads = 0
    results = list(log.reeval(policy=_pin_alice_policy(), changeset=changeset))
    assert len(results) == len(changeset)
    assert proxy.reads == len(changeset)  # not O(total) — exactly the changeset


def test_reeval_changeset_reads_independent_of_total_log_size(tmp_path: Path) -> None:
    """Contract #6: the materialized-read count for a fixed changeset is the same at 100 and 10,000 total."""
    counts = []
    for total in (SMALL, BIG):
        root = tmp_path / f"n{total}"
        root.mkdir()
        log, proxy = _counting_log(root)
        seqs = [log.append(_record(identity_id="alice")) for _ in range(total)]
        changeset = seqs[5:10]
        proxy.reads = 0
        list(log.reeval(policy=_pin_alice_policy(), changeset=changeset))
        counts.append(proxy.reads)
    assert counts[0] == counts[1] == 5  # log-size-independent reads


# --- boundedness: match slice ---------------------------------------------------------
def test_reeval_match_touches_only_the_index_slice(tmp_path: Path) -> None:
    """§2: reeval(match={...}) materializes only records the index slice selects, not the whole log."""
    log, proxy = _counting_log(tmp_path)
    for _ in range(5):
        log.append(_record(policy_id="p-finance"))
    for _ in range(SMALL - 5):
        log.append(_record(policy_id="p-eng"))
    proxy.reads = 0
    results = list(log.reeval(policy=_pin_alice_policy(), match={"policy_id": "p-finance"}))
    assert len(results) == 5
    assert proxy.reads == 5  # only the matching index slice was read


def test_reeval_match_reads_independent_of_total_log_size(tmp_path: Path) -> None:
    """§2: a fixed match slice reads the same number of records at 100 vs 10,000 total."""
    counts = []
    for total in (SMALL, BIG):
        root = tmp_path / f"n{total}"
        root.mkdir()
        log, proxy = _counting_log(root)
        for _ in range(5):
            log.append(_record(policy_id="p-finance"))
        for _ in range(total - 5):
            log.append(_record(policy_id="p-eng"))
        proxy.reads = 0
        list(log.reeval(policy=_pin_alice_policy(), match={"policy_id": "p-finance"}))
        counts.append(proxy.reads)
    assert counts[0] == counts[1] == 5  # index slice, not a full scan


# --- answers independent of total size ------------------------------------------------
def test_reeval_answers_are_identical_at_100_and_10000_total(tmp_path: Path) -> None:
    """Contract #6 / §8: the same fixed changeset yields identical (changed, after.route) at any N."""
    answers = []
    for total in (SMALL, BIG):
        root = tmp_path / f"n{total}"
        root.mkdir()
        log = AuditLog(str(root))
        # five 'alice' records that the pin flips, interleaved into a large 'bob' background.
        alice_seqs = []
        for i in range(total):
            ident = "alice" if i < 5 else "bob"
            seq = log.append(_record(identity_id=ident, route="cloud", route_pre_policy="cloud"))
            if ident == "alice":
                alice_seqs.append(seq)
        results = list(log.reeval(policy=_pin_alice_policy(), changeset=alice_seqs))
        answers.append(tuple((r.changed, r.after.route) for r in results))
    assert answers[0] == answers[1]
    assert answers[0] == ((True, "cloud-approved"),) * 5
