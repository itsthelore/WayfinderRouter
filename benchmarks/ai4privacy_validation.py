"""Validate the PII detectors against AI4Privacy — an *external* labeled corpus.

The hand-built `detector-corpus.jsonl` demonstrates the detectors' failure modes, but its
numbers are ours: we wrote both the lookalikes and the detectors. This runs the same
detectors and the same meter (`detector_validation.evaluate`) over
[AI4Privacy pii-masking-200k](https://huggingface.co/datasets/ai4privacy/pii-masking-200k)
— ~200k synthetic-but-independently-labeled records with per-span PII types — so the
precision/recall for the PII detectors comes from data we did not author.

Only the three detectors that correspond to an AI4Privacy label are scored
(email / us_ssn / credit_card); the secret detectors (aws/github/slack/…) are not PII and
are grounded separately against the gitleaks/detect-secrets rulesets
(`benchmarks/gitleaks_crosscheck.py`). AI4Privacy is multilingual and templated, so this
is a genuine out-of-distribution test — in particular the US-format `us_ssn` regex meets
SSN values that may not be US-formatted, which is exactly the kind of recall gap worth
measuring rather than assuming.

    python -m benchmarks.ai4privacy_validation \
        --out benchmarks/ai4privacy-validation-results.md \
        --out-json benchmarks/ai4privacy-validation-results.json

The dataset is downloaded (and cached) by the `datasets` library at run time; nothing from
it is committed. Same dataset revision + same detectors -> identical numbers.
"""

from __future__ import annotations

import argparse
import json
import sys

from benchmarks.detector_validation import (
    CorpusItem,
    evaluate,
    render_markdown,
    report_json,
)
from benchmarks.detectors import DETECTORS_BY_NAME

# AI4Privacy PII label -> our detector name. Only the overlap is scored.
LABEL_MAP: dict[str, str] = {
    "EMAIL": "email",
    "SSN": "us_ssn",
    "CREDITCARDNUMBER": "credit_card",
}
PII_DETECTORS = tuple(DETECTORS_BY_NAME[n] for n in sorted(set(LABEL_MAP.values())))


def items_from_records(records) -> list[CorpusItem]:
    """Map AI4Privacy records to CorpusItems: text + the mapped PII types it contains.

    ``records`` is any iterable of dicts with ``source_text`` and ``privacy_mask``
    (a list of ``{"label": ...}``) — the AI4Privacy schema. Pure, so it is unit-tested
    with planted records and no network.
    """
    items: list[CorpusItem] = []
    for row in records:
        labels = {
            LABEL_MAP[span["label"]]
            for span in row["privacy_mask"]
            if span["label"] in LABEL_MAP
        }
        items.append(CorpusItem(row["source_text"], frozenset(labels)))
    return items


def load_items(*, language: str = "en", limit: int | None = None) -> list[CorpusItem]:
    """Download AI4Privacy and build CorpusItems for the given language (order-stable)."""
    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit("install the extra:  pip install datasets") from None
    ds = load_dataset("ai4privacy/pii-masking-200k", split="train")
    rows = (r for r in ds if r["language"] == language)
    items = items_from_records(rows)
    return items[:limit] if limit else items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score the PII detectors against AI4Privacy.")
    ap.add_argument("--language", default="en")
    ap.add_argument("--limit", type=int, default=None, help="cap items (deterministic prefix)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)

    items = load_items(language=args.language, limit=args.limit)
    stats = evaluate(items, PII_DETECTORS)
    source = f"ai4privacy/pii-masking-200k ({args.language}, {len(items)} records)"
    markdown = render_markdown(stats, corpus_size=len(items), source=source)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(markdown)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(report_json(stats), f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {args.out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
