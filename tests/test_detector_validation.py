"""Tests for the detector-validation benchmark (WF-ROADMAP-0011 §6).

Planted items with hand-checkable confusion counts prove the precision/recall arithmetic
before any real corpus number is trusted — the meter-first discipline of
``test_judge_validation.py`` and ``test_benchmarks.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from benchmarks.detector_corpus import TOKEN_ITEMS
from benchmarks.detector_validation import (
    CorpusItem,
    Stats,
    evaluate,
    full_corpus,
    micro_macro,
    render_markdown,
    report_json,
)
from benchmarks.detectors import DETECTORS_BY_NAME, Detector

CORPUS = Path(__file__).parent.parent / "benchmarks" / "detector-corpus.jsonl"

# A literal-match detector so planted fires are fully controlled.
FLAG = Detector("flag", re.compile(r"SECRET"))
MARK = Detector("mark", re.compile(r"MARK"))


def _planted() -> list[CorpusItem]:
    return [
        CorpusItem("this has SECRET inside", frozenset({"flag"})),   # tp for flag
        CorpusItem("SECRET but not labelled", frozenset()),          # fp for flag
        CorpusItem("labelled but absent", frozenset({"flag"})),      # fn for flag
        CorpusItem("clean text here", frozenset()),                  # tn for flag
    ]


def test_planted_confusion_counts():
    s = evaluate(_planted(), (FLAG,))["flag"]
    assert (s.tp, s.fp, s.fn, s.tn) == (1, 1, 1, 1)


def test_precision_recall_f1_math():
    s = Stats(tp=3, fp=1, fn=1, tn=5)
    assert s.precision == 0.75
    assert s.recall == 0.75
    assert s.f1 == 0.75
    empty = Stats()
    assert empty.precision == 0.0 and empty.recall == 0.0 and empty.f1 == 0.0


def test_micro_and_macro_rollup():
    items = [
        CorpusItem("SECRET MARK", frozenset({"flag", "mark"})),  # tp both
        CorpusItem("SECRET only", frozenset({"flag"})),          # tp flag; tn mark
        CorpusItem("MARK unlabelled", frozenset()),              # fp mark; tn flag
    ]
    stats = evaluate(items, (FLAG, MARK))
    assert (stats["flag"].tp, stats["flag"].fp) == (2, 0)
    assert (stats["mark"].tp, stats["mark"].fp) == (1, 1)
    roll = micro_macro(stats)
    # micro precision = pooled tp/(tp+fp) = 3/(3+1) = 0.75
    assert roll["micro_precision"] == 0.75
    # macro precision = mean(1.0, 0.5) = 0.75
    assert roll["macro_precision"] == 0.75
    # macro recall = mean(1.0, 1.0) = 1.0 (no false negatives here)
    assert roll["macro_recall"] == 1.0


def test_report_is_deterministic():
    items = _planted()
    a, b = evaluate(items, (FLAG,)), evaluate(items, (FLAG,))
    assert report_json(a) == report_json(b)
    md = render_markdown(a, corpus_size=len(items), source="planted")
    assert md == render_markdown(b, corpus_size=len(items), source="planted")
    assert "| flag | 1 | 1 | 1 | 1 | 0.500 | 0.500 | 0.500 |" in md


def test_luhn_validator_gates_credit_card():
    cc = DETECTORS_BY_NAME["credit_card"]
    assert cc.detects("card 4111 1111 1111 1111 on file")      # Luhn-valid test number
    assert not cc.detects("code 4111 1111 1111 1112 rejected")  # last digit breaks Luhn
    assert not cc.detects("dotted 4111.1111.1111.1111 missed")  # separators the regex skips


def test_distinctive_prefix_detectors_are_exact():
    aws = DETECTORS_BY_NAME["aws_access_key"]
    assert aws.detects("AKIAIOSFODNN7EXAMPLE")
    assert not aws.detects("the AKIA prefix alone")
    gh = DETECTORS_BY_NAME["github_pat"]
    assert gh.detects("ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert not gh.detects("ghp_short")


def test_reference_corpus_loads_and_labels_are_known():
    items = full_corpus(CORPUS)
    assert len(items) >= 45
    for item in items:
        for label in item.labels:
            assert label in DETECTORS_BY_NAME, f"unknown detector label: {label}"


def test_assembled_token_items_actually_fire():
    # The provider tokens are built from fragments (push protection blocks literals); the
    # assembled values must still trip their detectors, or the benchmark measures nothing.
    for row in TOKEN_ITEMS:
        item = CorpusItem(row["text"], frozenset(row["labels"]))
        for label in item.labels:
            assert DETECTORS_BY_NAME[label].detects(item.text), f"{label} missed its own positive"


def test_no_literal_provider_token_in_committed_corpus():
    # The JSONL must not contain a scanner-matchable provider prefix — that is the whole
    # reason the token items live in detector_corpus.py.
    raw = CORPUS.read_text(encoding="utf-8")
    for needle in ("xoxb-", "ghp_a", "AKIAI"):
        assert needle not in raw, f"committed corpus contains a live-looking token: {needle}"
