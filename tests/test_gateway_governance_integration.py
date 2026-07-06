"""Spec-first integration tests for the gateway governance stage (WF-DESIGN-0013).

These are written FROM the design before a line of the stage exists, so the file
``py_compile``s but cannot RUN yet (``wayfinder_router.policy``/``audit``/
``identity``/``detectors`` are absent). Additive-only per WF-ADR-0044: no existing
test is touched.

Pinned to WF-DESIGN-0013:
  * §6 Gateway integration points — the single new stage sits AFTER the per-key
    allowlist clamp (gateway.py:2023-2027) and BEFORE ``wf_headers`` is built
    (gateway.py:2029); it is a literal no-op when ``[policy]`` is absent/disabled;
    audit-append happens when ``[audit]`` is enabled; the metrics endpoint gains
    additive counters ONLY when governance is active.
  * §3 Policy verbs/decision — VERBS/VERB_PRECEDENCE; deny/block -> structured 403;
    throttle -> 429; pin/clamp/degrade -> route mutation (args["target"]);
    redact -> forwarded-body rewrite; warn/log -> headers/audit only;
    ``PolicyDecision.to_headers()`` -> x-wayfinder-policy / -policy-rule / -policy-verb.
  * §5 Identity — ``VirtualKey.tags`` ("team:<x>"/"kind:<x>") gets its consumer;
    exactly one principal always; ``ANONYMOUS`` when keys are unconfigured.
  * §2 Audit — ``AuditRecord`` captures the final post-policy ``route`` AND the
    preserved ``route_pre_policy``; ``wayfinder_router.audit.AuditLog`` reads it back.

Contract invariants asserted (Contracts §): #8 (policy verb outcomes),
#10 (identity totality), #11 (zero-regression covenant), plus §6's stage-ordering
and additive-metrics rules and §2's metadata-only route/route_pre_policy capture.

Idioms are lifted verbatim from tests/test_gateway.py: a TOML config written to
``tmp_path / "wayfinder-router.toml"``, an app built with ``gateway.build_app(
start_dir=...)`` wrapped in ``TestClient``, and the upstream forward seam
``gateway.aforward_request`` monkeypatched as a MODULE ATTRIBUTE (the redact tests
capture the outbound ``json_body`` through it), virtual keys minted with
``vkeys.generate()``, and ``/metrics`` scanned line-by-line.
"""

from __future__ import annotations

import json

import pytest

# Skip the whole module cleanly if the gateway extra is not installed (mirrors test_gateway.py).
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from wayfinder_router import gateway, vkeys  # noqa: E402

# --- shared fixtures / config builders (test_gateway.py idiom) ----------------

TRIVIAL = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
COMPLEX_TEXT = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(14))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n| a | b |\n| - | - |\n"
)
COMPLEX = {"model": "auto", "messages": [{"role": "user", "content": COMPLEX_TEXT}]}

# The AWS example access-key id (byte-identical to benchmarks/detectors.py's fixture) — a
# credential-shaped token that trips the ``aws_access_key`` detector without being a live secret.
AWS_EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"
PLANTED_EMAIL = "alice@example.com"
PLANTED_SSN = "123-45-6789"

# Two priced tiers (local 0.0 / cloud 1.0), threshold 0.2 — TRIVIAL scores local, COMPLEX cloud.
_MODELS = (
    "[routing]\nthreshold = 0.2\n\n"
    '[gateway.models.local]\nbase_url = "http://local.test/v1"\nmodel = "m-local"\ncost_per_1k = 0.0\n\n'
    '[gateway.models.cloud]\nbase_url = "http://cloud.test/v1"\nmodel = "m-cloud"\ncost_per_1k = 1.0\n'
)

# Policy header names produced by PolicyDecision.to_headers() (§3).
H_POLICY = "x-wayfinder-policy"        # == CompiledPolicy.policy_hash (12-hex)
H_RULE = "x-wayfinder-policy-rule"     # deciding (terminal) rule id
H_VERB = "x-wayfinder-policy-verb"     # terminal verb


def _client(tmp_path, monkeypatch, config, *, clock=None):
    """A TestClient over ``config`` whose upstream returns 200; captures the last forwarded body.

    ``captured["body"]`` is the outbound ``json_body`` handed to ``aforward_request`` — the copy the
    redact verb rewrites (§6). ``captured["forwards"]`` counts upstream calls.
    """
    (tmp_path / "wayfinder-router.toml").write_text(config, encoding="utf-8")
    captured: dict = {"forwards": 0}

    async def fake_aforward(url, headers, json_body, timeout=60.0):
        captured["forwards"] += 1
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json_body
        body = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10},
            "object": "chat.completion",
        }
        return 200, json.dumps(body).encode(), "application/json"

    monkeypatch.setattr(gateway, "aforward_request", fake_aforward)
    kwargs = {"start_dir": str(tmp_path)}
    if clock is not None:
        kwargs["clock"] = clock
    return TestClient(gateway.build_app(**kwargs)), captured


def _policy(*rules: str, enabled: bool = True, policy_id: str = "org-baseline") -> str:
    """A ``[policy]`` header table + one ``[policy.rules.<name>]`` table per rule fragment."""
    head = f"[policy]\nenabled = {'true' if enabled else 'false'}\nid = \"{policy_id}\"\n\n"
    return head + "\n\n".join(rules) + "\n\n"


def _audit(dir_path) -> str:
    return f'[audit]\nenabled = true\ndir = "{dir_path}"\ndurability = "buffered"\n\n'


def _forwarded_text(captured: dict) -> str:
    """The concatenated message content actually forwarded upstream."""
    body = captured.get("body") or {}
    return json.dumps(body.get("messages", []))


def _metric_present(tc, name: str) -> bool:
    return any(line.startswith(name) for line in tc.get("/metrics").text.splitlines())


# ============================================================================
# (1) Zero-regression covenant (Contract #11): tables absent => today's behavior.
# ============================================================================

def test_no_governance_tables_is_byte_identical_and_no_policy_metrics(tmp_path, monkeypatch):
    tc, captured = _client(tmp_path, monkeypatch, _MODELS)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)
    assert resp.status_code == 200
    # The scored decision surface is exactly today's (no new stage ran).
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers["x-wayfinder-router-mode"] == "scored"
    # No governance headers leak onto a request that configured no policy.
    assert H_POLICY not in resp.headers
    assert H_RULE not in resp.headers
    assert H_VERB not in resp.headers
    # And the metrics endpoint gains none of the additive counters when governance is inactive.
    metrics = tc.get("/metrics").text
    for name in (
        "wayfinder_router_policy_evaluations_total",
        "wayfinder_router_policy_verb_total",
        "wayfinder_router_policy_blocks_total",
        "wayfinder_router_detector_hits_total",
        "wayfinder_router_audit_appends_total",
        "wayfinder_router_identity_attributions_total",
    ):
        assert name not in metrics


# ============================================================================
# (2)/(3) block & deny -> structured 403 (§3; Contract #8).
# ============================================================================

def test_block_verb_returns_structured_403_with_message(tmp_path, monkeypatch):
    rule = (
        "[policy.rules.block-all]\n"
        "priority = 10\n"
        'verb = "block"\n'
        'message = "Requests containing credentials are not permitted."\n'
    )
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "Requests containing credentials are not permitted."
    assert resp.headers[H_VERB] == "block"
    assert captured["forwards"] == 0  # a blocked request never reaches an upstream


def test_deny_verb_returns_403_with_distinct_verb_header(tmp_path, monkeypatch):
    rule = (
        "[policy.rules.deny-all]\n"
        "priority = 5\n"
        'verb = "deny"\n'
        'message = "Denied by org policy."\n'
    )
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 403
    # deny and block both 403, but the recorded verb is distinct (§3 BlockOutcome.verb).
    assert resp.headers[H_VERB] == "deny"
    assert captured["forwards"] == 0


# ============================================================================
# (4) pin / clamp / degrade -> route mutation visible via x-wayfinder-router-model (§3/§6).
# ============================================================================

def test_pin_verb_mutates_route_visible_in_model_header(tmp_path, monkeypatch):
    rule = "[policy.rules.pin-cloud]\npriority = 30\nverb = \"pin\"\ntarget = \"cloud\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # scores local; policy pins cloud
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # route mutated to args["target"]
    assert resp.headers[H_VERB] == "pin"
    assert resp.headers["x-wayfinder-router-mode"]  # a mode is still reported after the mutation
    assert captured["url"] == "http://cloud.test/v1/chat/completions"  # delivered to the pinned tier


def test_clamp_verb_mutates_route(tmp_path, monkeypatch):
    rule = "[policy.rules.clamp-cloud]\npriority = 30\nverb = \"clamp\"\ntarget = \"cloud\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers[H_VERB] == "clamp"


def test_degrade_verb_mutates_route(tmp_path, monkeypatch):
    rule = "[policy.rules.degrade-local]\npriority = 30\nverb = \"degrade\"\ntarget = \"local\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # scores cloud; policy degrades to local
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"
    assert resp.headers[H_VERB] == "degrade"
    assert captured["url"] == "http://local.test/v1/chat/completions"


# ============================================================================
# (5) throttle -> 429 (§3).
# ============================================================================

def test_throttle_verb_returns_429(tmp_path, monkeypatch):
    rule = "[policy.rules.throttle-all]\npriority = 40\nverb = \"throttle\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 429
    assert resp.headers[H_VERB] == "throttle"
    assert captured["forwards"] == 0


# ============================================================================
# (6) redact -> rewrite the FORWARDED copy only; original prompt untouched (§3/§6).
# ============================================================================

def test_redact_rewrites_forwarded_copy_only(tmp_path, monkeypatch):
    rule = (
        "[policy.rules.redact-pii]\n"
        "priority = 20\n"
        'verb = "redact"\n'
        'detectors_any = ["email", "us_ssn"]\n'
    )
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": f"contact {PLANTED_EMAIL} ssn {PLANTED_SSN}"}],
    }
    resp = tc.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    # The outbound copy handed to the forward seam is scrubbed of the planted secrets...
    forwarded = _forwarded_text(captured)
    assert PLANTED_EMAIL not in forwarded
    assert PLANTED_SSN not in forwarded
    assert captured["forwards"] == 1
    # ...while the request body the client posted (our local object) is untouched.
    assert PLANTED_EMAIL in payload["messages"][0]["content"]
    assert PLANTED_SSN in payload["messages"][0]["content"]
    # The decision/policy headers ride along on a redacted (but delivered) request.
    assert resp.headers[H_VERB] == "redact"
    assert resp.headers[H_POLICY]


# ============================================================================
# (9) detector-triggered rule -> a planted AWS example key trips detectors_any and blocks (§4/§3).
# ============================================================================

def test_detector_triggered_rule_blocks_on_aws_example_key(tmp_path, monkeypatch):
    rule = (
        "[policy.rules.block-secrets]\n"
        "priority = 10\n"
        'verb = "block"\n'
        'message = "Requests containing credentials are not permitted."\n'
        'detectors_any = ["aws_access_key", "github_pat", "slack_token", "private_key"]\n'
    )
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    # A benign prompt does not trip the detector -> the rule does not match -> forwarded normally.
    ok = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert ok.status_code == 200
    assert captured["forwards"] == 1
    # A prompt carrying the AWS example key trips ``aws_access_key`` -> block -> 403.
    secret = {"model": "auto", "messages": [{"role": "user", "content": f"key={AWS_EXAMPLE_KEY}"}]}
    blocked = tc.post("/v1/chat/completions", json=secret)
    assert blocked.status_code == 403
    assert blocked.headers[H_VERB] == "block"
    assert captured["forwards"] == 1  # the blocked request added no upstream call


# ============================================================================
# (7) identity attribution — team:/kind: vkey tags resolve; anonymous when unconfigured (§5; #10).
# ============================================================================

def test_identity_team_tag_resolves_and_team_rule_fires(tmp_path, monkeypatch):
    key, h = vkeys.generate()
    keys = (
        f'[gateway.keys.finance-key]\nhash = "{h}"\n'
        'tags = ["team:finance", "kind:service"]\n\n'
    )
    rule = (
        "[policy.rules.finance-pin]\n"
        "priority = 30\n"
        'verb = "pin"\n'
        'target = "cloud"\n'
        'teams = ["finance"]\n'
    )
    tc, captured = _client(
        tmp_path, monkeypatch, keys + _policy(rule) + _MODELS, clock=lambda: 1000.0
    )
    resp = tc.post(
        "/v1/chat/completions", json=TRIVIAL, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200
    # The "team:finance" tag resolves the principal's team, so the team-matched rule fires.
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers[H_RULE] == "finance-pin"


def test_anonymous_attribution_drives_kind_rule_when_keys_unconfigured(tmp_path, monkeypatch):
    rule = (
        "[policy.rules.anon-pin]\n"
        "priority = 30\n"
        'verb = "pin"\n'
        'target = "cloud"\n'
        'identity_kinds = ["anonymous"]\n'
    )
    # No [gateway.keys] table -> vkey_id is None -> resolve() returns ANONYMOUS (§5 case 1).
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"
    assert resp.headers[H_RULE] == "anon-pin"


# ============================================================================
# (8) audit append — the record's route/route_pre_policy/verbs/policy match the headers (§2).
# ============================================================================

def test_audit_append_record_matches_response_headers(tmp_path, monkeypatch):
    from wayfinder_router import audit  # lazily reachable (import contract, Contract #1)

    audit_dir = tmp_path / "gov"
    rule = "[policy.rules.pin-cloud]\npriority = 30\nverb = \"pin\"\ntarget = \"cloud\"\n"
    config = _audit(audit_dir) + _policy(rule) + _MODELS
    tc, captured = _client(tmp_path, monkeypatch, config)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # scores local, pins cloud
    assert resp.status_code == 200

    log = audit.AuditLog(str(audit_dir))
    try:
        page = log.query(limit=100)
    finally:
        log.close()
    assert page.records, "an audit record was appended on the request path"
    rec = page.records[-1]
    # The audit record carries the FINAL post-policy route AND preserves the pre-policy choice (§2).
    assert rec.route == resp.headers["x-wayfinder-router-model"] == "cloud"
    assert rec.route_pre_policy == "local"
    assert "pin" in rec.verbs
    assert rec.policy_hash == resp.headers[H_POLICY]
    assert rec.rule == resp.headers[H_RULE]
    assert rec.request_id == resp.headers["x-wayfinder-router-request-id"]


def test_audit_record_carries_identity_and_vkey_attribution(tmp_path, monkeypatch):
    from wayfinder_router import audit

    key, h = vkeys.generate()
    audit_dir = tmp_path / "gov"
    keys = f'[gateway.keys.team-a]\nhash = "{h}"\ntags = ["team:finance"]\n\n'
    rule = "[policy.rules.log-all]\npriority = 50\nverb = \"log\"\n"
    config = _audit(audit_dir) + keys + _policy(rule) + _MODELS
    tc, captured = _client(tmp_path, monkeypatch, config, clock=lambda: 1000.0)
    resp = tc.post(
        "/v1/chat/completions", json=TRIVIAL, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200

    log = audit.AuditLog(str(audit_dir))
    try:
        rec = log.query(limit=100).records[-1]
    finally:
        log.close()
    # VirtualKey.tags finally has a consumer: the vkey and its resolved principal are attributed.
    assert rec.vkey_id == "team-a"
    assert rec.identity_id != "anonymous"  # a configured key resolves to a real principal


def test_audit_absent_when_audit_table_disabled(tmp_path, monkeypatch):
    from wayfinder_router import audit

    audit_dir = tmp_path / "gov"
    # [policy] active but no [audit] table -> audit_active is false -> nothing is appended (§6).
    rule = "[policy.rules.pin-cloud]\npriority = 30\nverb = \"pin\"\ntarget = \"cloud\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # policy still ran
    # No audit store was created, and the audit-append counter never appears.
    assert not audit_dir.exists()
    assert not _metric_present(tc, "wayfinder_router_audit_appends_total")


# ============================================================================
# (10) additive metrics counters present when active (§6).
# ============================================================================

def test_additive_policy_metrics_present_when_active(tmp_path, monkeypatch):
    rule = "[policy.rules.pin-cloud]\npriority = 30\nverb = \"pin\"\ntarget = \"cloud\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    tc.post("/v1/chat/completions", json=TRIVIAL)
    metrics = tc.get("/metrics").text
    # The new counters are rendered only once governance has evaluated a request.
    assert "wayfinder_router_policy_evaluations_total" in metrics
    assert "wayfinder_router_policy_verb_total" in metrics
    assert "wayfinder_router_identity_attributions_total" in metrics


# ============================================================================
# (11) x-wayfinder-policy* headers carry policy_hash / rule / verb (§3 to_headers).
# ============================================================================

def test_policy_headers_carry_hash_rule_verb(tmp_path, monkeypatch):
    rule = "[policy.rules.pin-cloud]\npriority = 30\nverb = \"pin\"\ntarget = \"cloud\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)
    assert resp.status_code == 200
    # policy_hash is a 12-hex digest; the rule and verb name the terminal decision.
    policy_hash = resp.headers[H_POLICY]
    assert len(policy_hash) == 12 and all(c in "0123456789abcdef" for c in policy_hash)
    assert resp.headers[H_RULE] == "pin-cloud"
    assert resp.headers[H_VERB] == "pin"


# ============================================================================
# (12) stage ordering — the clamp runs BEFORE policy, so a route-matched rule sees the
#      POST-clamp route as route_pre_policy (§6; the design's exact reading).
# ============================================================================

def test_stage_ordering_clamp_before_policy_route_pre_policy_is_post_clamp(tmp_path, monkeypatch):
    from wayfinder_router import audit

    key, h = vkeys.generate()
    audit_dir = tmp_path / "gov"
    # The key's allowlist permits only ``local`` -> a COMPLEX (cloud-scored) request is clamped to
    # ``local`` (the current "final word on the route") BEFORE the policy stage runs.
    keys = f'[gateway.keys.team-a]\nhash = "{h}"\nmodels = ["local"]\n\n'
    # A rule that matches on route == "local" only fires if it sees the POST-clamp route
    # (route_pre_policy IS the post-clamp, pre-policy chosen route).
    rule = (
        "[policy.rules.route-local-pin]\n"
        "priority = 30\n"
        'verb = "pin"\n'
        'target = "cloud"\n'
        'routes = ["local"]\n'
    )
    config = _audit(audit_dir) + keys + _policy(rule) + _MODELS
    tc, captured = _client(tmp_path, monkeypatch, config, clock=lambda: 1000.0)
    resp = tc.post(
        "/v1/chat/completions", json=COMPLEX, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200
    # The rule fired -> it matched the clamped route "local" -> final route mutates to "cloud".
    assert resp.headers[H_RULE] == "route-local-pin"
    assert resp.headers["x-wayfinder-router-model"] == "cloud"

    log = audit.AuditLog(str(audit_dir))
    try:
        rec = log.query(limit=100).records[-1]
    finally:
        log.close()
    # The recorded pre-policy route is the post-clamp choice, not the scored "cloud".
    assert rec.route_pre_policy == "local"
    assert rec.route == "cloud"


# ============================================================================
# warn / log -> headers (and audit) only; the route is never mutated (§3).
# ============================================================================

def test_warn_verb_is_headers_only_route_unchanged(tmp_path, monkeypatch):
    rule = "[policy.rules.warn-all]\npriority = 40\nverb = \"warn\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # scores local
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"  # unchanged by a content verb
    assert resp.headers[H_VERB] == "warn"
    assert captured["forwards"] == 1


def test_log_verb_is_audit_and_headers_only_route_unchanged(tmp_path, monkeypatch):
    rule = "[policy.rules.log-all]\npriority = 50\nverb = \"log\"\n"
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=COMPLEX)  # scores cloud
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "cloud"  # route unchanged
    assert resp.headers[H_VERB] == "log"
    assert captured["forwards"] == 1


# ============================================================================
# no-match default — verb "route", no mutation, policy stage still stamps headers (§3).
# ============================================================================

def test_no_match_default_verb_is_route_with_no_mutation(tmp_path, monkeypatch):
    # A rule that cannot match this request (score band well above a TRIVIAL prompt's score).
    rule = (
        "[policy.rules.only-hard]\n"
        "priority = 30\n"
        'verb = "pin"\n'
        'target = "cloud"\n'
        "score_min = 0.99\n"
    )
    tc, captured = _client(tmp_path, monkeypatch, _policy(rule) + _MODELS)
    resp = tc.post("/v1/chat/completions", json=TRIVIAL)  # scores local, below score_min
    assert resp.status_code == 200
    assert resp.headers["x-wayfinder-router-model"] == "local"  # no rule matched -> route unchanged
    # to_headers() still emits with the default terminal verb "route" and no deciding rule.
    assert resp.headers[H_VERB] == "route"
    assert resp.headers.get(H_RULE, "") == ""
