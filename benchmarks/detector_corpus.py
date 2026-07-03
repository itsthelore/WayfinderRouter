"""Provider-token corpus items, assembled at runtime — the part a JSONL can't hold.

A secrets-detection benchmark hits an obvious wall: it cannot commit literal, live-looking
provider tokens, because secret-scanning push protection (rightly) blocks them. So the
distinctive-token positives (AWS keys, GitHub PATs, Slack tokens) are built here from
fragments — the source file never contains a contiguous token string, but the assembled
value is exactly what a detector must catch. Everything without a scanner-matchable
literal (emails, SSNs, cards, hex, clean text, lookalikes) stays in the reviewable
``detector-corpus.jsonl``.

None of these are real: the AWS key is AWS's documented example value, the others are
structurally-valid but meaningless placeholders.
"""

from __future__ import annotations

# Assembled so the prefix never appears contiguously in the committed source.
_AWS = "AK" + "IAIOSFODNN7EXAMPLE"
_GH = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
_SLACK = "xox" + "b-" + "240120480129-abcDEF123ghiJKL"

# ``{"text": ..., "labels": [...]}`` items, same shape as the JSONL rows.
TOKEN_ITEMS: list[dict] = [
    {"text": f"aws_access_key_id = {_AWS}", "labels": ["aws_access_key"]},
    {"text": f"Rotate the key {_AWS} before Friday's audit.", "labels": ["aws_access_key"]},
    {"text": f"export GH_TOKEN={_GH}", "labels": ["github_pat"]},
    {"text": f"CI uses {_GH} for release tags.", "labels": ["github_pat"]},
    {"text": f"SLACK_BOT_TOKEN={_SLACK}", "labels": ["slack_token"]},
    {"text": f"The bot authenticates with {_SLACK} nightly.", "labels": ["slack_token"]},
    {
        "text": f"Provision a bot: slack {_SLACK}, email ops@example.com.",
        "labels": ["slack_token", "email"],
    },
]
