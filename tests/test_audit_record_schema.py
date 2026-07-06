"""Spec-first contract tests for the audit decision-record schema.

Pins WF-DESIGN-0013 §2 (Audit / decision log — ``wayfinder_router/audit.py``) and
Contracts invariant #4 (Audit metadata-only): no ``AuditRecord`` field, and no bytes
written by ``AuditLog.append``, may contain prompt text or matched detector text;
``DetectorHit`` carries only ``name``/``count``/``spans:int``.

Ambiguities resolved to the strictest reading (noted per test):
- The metadata-only scan is applied to *every* file the store writes under ``root``
  (segment logs AND the sqlite index shards), not only the segment ``.log`` bytes,
  since a leak into the index would be just as much a violation.
- ``request_digest`` is treated as a lower-case 64-hex sha256 string; a well-formed
  record must satisfy that shape (§2 AuditRecord.request_digest).
- ``mode`` is stored/round-tripped as an opaque ``str`` — ``from_json`` performs no
  vocabulary validation on it (§2 says the vocabulary is a documented set but the
  field type is ``str``; the strict reading is "accept any str, reject nothing").
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from wayfinder_router.audit import (
    AUDIT_INDEX_FIELDS,
    AUDIT_SCHEMA_VERSION,
    AuditError,
    AuditLog,
    AuditPage,
    AuditRecord,
    AuditSchemaError,
    DetectorHit,
    ReevalResult,
)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

# A planted secret and a fake SSN — must never survive into the on-disk payload.
_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_SSN = "123-45-6789"
_PROMPT = f"my aws key is {_AWS_KEY} and ssn {_SSN}"


def _digest(body: dict[str, Any]) -> str:
    """A canonical sha256 hex over a request body (never stores the body itself)."""
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _record(**overrides: Any) -> AuditRecord:
    """Build a fully-populated AuditRecord; overrides replace individual fields."""
    fields: dict[str, Any] = dict(
        schema_version=AUDIT_SCHEMA_VERSION,
        seq=0,
        ts_wall=1_700_000_000.5,
        ts_mono=12.25,
        request_id="0123456789ab",
        identity_id="alice",
        identity_kind="human",
        team="finance",
        tags=("role:analyst",),
        vkey_id="team-finance",
        route="cloud-approved",
        route_pre_policy="cloud",
        score=0.42,
        mode="scored",
        offline=False,
        budget_state=None,
        policy_id="org-baseline",
        policy_hash="abcdef012345",
        rule="finance-pin",
        verbs=("route", "pin"),
        detector_hits=(
            DetectorHit(name="email", count=2, spans=((0, 5), (10, 20))),
            DetectorHit(name="us_ssn", count=1, spans=((30, 41),)),
        ),
        prompt_tokens=17,
        completion_tokens=33,
        estimated=True,
        realized=0.0012,
        baseline=0.0100,
        saved=0.0088,
        unit="usd",
        request_digest=_digest({"model": "auto", "messages": [{"role": "user", "content": "hi"}]}),
    )
    fields.update(overrides)
    return AuditRecord(**fields)


def _canonical_bytes(record: AuditRecord) -> bytes:
    """The exact append canonicalization from §2 (sorted keys, compact, ensure_ascii=False)."""
    return json.dumps(
        record.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# --- constants ------------------------------------------------------------------------
def test_schema_version_constant_is_one() -> None:
    """§2: AUDIT_SCHEMA_VERSION is the pinned integer schema id."""
    assert AUDIT_SCHEMA_VERSION == 1


def test_audit_index_fields_are_the_declared_dimensions() -> None:
    """§2: AUDIT_INDEX_FIELDS is exactly the four filterable dimensions, in order."""
    assert AUDIT_INDEX_FIELDS == ("identity_id", "vkey_id", "policy_id", "route")


# --- to_json / from_json round-trip ---------------------------------------------------
def test_to_json_from_json_round_trips_every_field() -> None:
    """§2: to_json/from_json is a lossless round-trip over the whole record surface."""
    rec = _record()
    restored = AuditRecord.from_json(rec.to_json())
    assert restored == rec


def test_to_json_survives_canonical_json_encode_decode() -> None:
    """§2 append canonicalization: to_json yields JSON-native types that decode back equal."""
    rec = _record()
    reloaded = AuditRecord.from_json(json.loads(_canonical_bytes(rec).decode("utf-8")))
    assert reloaded == rec


def test_detector_hits_round_trip_preserves_tuple_spans() -> None:
    """§2/§4: detector_hits restore as DetectorHit with int-pair tuple spans (not lists)."""
    rec = _record(detector_hits=(DetectorHit(name="aws_access_key", count=1, spans=((3, 23),)),))
    restored = AuditRecord.from_json(rec.to_json())
    hit = restored.detector_hits[0]
    assert isinstance(restored.detector_hits, tuple)
    assert hit == DetectorHit(name="aws_access_key", count=1, spans=((3, 23),))
    assert isinstance(hit.spans, tuple) and isinstance(hit.spans[0], tuple)


def test_optional_fields_round_trip_when_none() -> None:
    """§2: nullable fields (vkey_id, team, budget_state, policy_id, policy_hash, rule) survive as None."""
    rec = _record(vkey_id=None, team=None, budget_state=None, policy_id=None, policy_hash=None, rule=None)
    assert AuditRecord.from_json(rec.to_json()) == rec


# --- schema_version gate --------------------------------------------------------------
def test_from_json_rejects_mismatched_schema_version() -> None:
    """§2: from_json raises AuditSchemaError on a version it cannot read."""
    data = _record().to_json()
    data["schema_version"] = AUDIT_SCHEMA_VERSION + 999
    try:
        AuditRecord.from_json(data)
    except AuditSchemaError:
        pass
    else:  # pragma: no cover - contract failure
        raise AssertionError("from_json accepted a mismatched schema_version")


def test_audit_schema_error_is_an_audit_error() -> None:
    """§2: AuditSchemaError is a subclass of AuditError (catchable as the family type)."""
    assert issubclass(AuditSchemaError, AuditError)


def test_from_json_accepts_matching_schema_version() -> None:
    """§2: the current AUDIT_SCHEMA_VERSION is accepted without raising."""
    assert AuditRecord.from_json(_record().to_json()).schema_version == AUDIT_SCHEMA_VERSION


# --- metadata-only invariant (Contract #4) --------------------------------------------
def test_appended_payload_never_contains_prompt_or_matched_text(tmp_path: Path) -> None:
    """Contract #4: append writes metadata only — a planted secret is absent from every on-disk byte.

    Strictest reading: scan *all* files the store persists under root (segment logs and
    index shards), not just the segment file. The record is constructed exactly as the
    gateway would — a sha256 request_digest plus detector spans (char offsets), never the
    matched substring — so the only way the secret could appear on disk is a leak.
    """
    aws_at = _PROMPT.index(_AWS_KEY)
    ssn_at = _PROMPT.index(_SSN)
    rec = _record(
        request_digest=_digest({"messages": [{"role": "user", "content": _PROMPT}]}),
        detector_hits=(
            DetectorHit(name="aws_access_key", count=1, spans=((aws_at, aws_at + len(_AWS_KEY)),)),
            DetectorHit(name="us_ssn", count=1, spans=((ssn_at, ssn_at + len(_SSN)),)),
        ),
    )

    log = AuditLog(str(tmp_path))
    log.append(rec)
    log.flush()
    log.close()

    blob = b"".join(p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file())
    assert _AWS_KEY.encode() not in blob
    assert _SSN.encode() not in blob
    assert b"my aws key is" not in blob  # no fragment of the prompt text survives


def test_record_fields_carry_no_free_text_slot() -> None:
    """Contract #4: the AuditRecord schema exposes no field that could hold raw prompt/match text."""
    names = {f.name for f in dataclasses.fields(AuditRecord)}
    for forbidden in ("prompt", "content", "text", "body", "messages", "match", "matched"):
        assert forbidden not in names


# --- DetectorHit shape (Contract #4) --------------------------------------------------
def test_detector_hit_has_exactly_name_count_spans_and_no_text() -> None:
    """§4/Contract #4: DetectorHit is name/count/spans only — there is NO text field."""
    names = {f.name for f in dataclasses.fields(DetectorHit)}
    assert names == {"name", "count", "spans"}
    assert "text" not in names and "match" not in names


def test_detector_hit_spans_are_int_pairs() -> None:
    """§4: spans are (start, end) int char-offset pairs — never the matched substring."""
    hit = DetectorHit(name="credit_card", count=2, spans=((0, 16), (40, 56)))
    assert all(isinstance(a, int) and isinstance(b, int) for a, b in hit.spans)


# --- opaque mode + digest shape -------------------------------------------------------
def test_mode_is_accepted_as_an_opaque_string() -> None:
    """§2: mode is a str; from_json round-trips an unknown/future mode without validation."""
    rec = _record(mode="some-future-mode")
    assert AuditRecord.from_json(rec.to_json()).mode == "some-future-mode"


def test_request_digest_is_a_64_hex_sha256_string() -> None:
    """§2: request_digest is the sha256 hex of the canonicalized body — 64 lower-case hex chars."""
    digest = _record().request_digest
    assert _HEX64.match(digest)
    assert len(digest) == 64


# --- re-exported symbols are wired ----------------------------------------------------
def test_audit_page_and_reeval_result_are_dataclasses() -> None:
    """§2: AuditPage and ReevalResult are frozen dataclasses re-exported from audit."""
    assert dataclasses.is_dataclass(AuditPage) and dataclasses.is_dataclass(ReevalResult)
