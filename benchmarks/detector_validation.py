"""Measure the reference detectors' precision/recall on a labeled corpus (WF-ROADMAP-0011 §6).

The governance plane's ``block``/``redact`` verbs are only as trustworthy as the
detectors behind them, so — before any of that is built — the detectors get the
``blind-eval.md`` treatment: run them over a corpus where each item is labeled with the
secret/PII types it actually contains, and report per-detector **precision** (of what
fired, how much was real), **recall** (of the real secrets, how much fired), and F1.

The honest finding this is built to surface: distinctive-prefix detectors
(aws/github/slack/private-key) are near-perfect, while the format-flexible ones
(email/ssn/credit-card) and the entropy proxy (high-entropy-hex) visibly trade precision
against recall — a lookalike invoice number reads as an SSN, a git SHA reads as a secret,
a dotted card number is missed. Regex detection is recall- *and* precision-imperfect by
construction; the tables say exactly how much, per detector.

The core (:func:`evaluate`) is pure and dependency-free so its arithmetic is golden-tested
(``tests/test_detector_validation.py``) with planted items whose confusion counts are
hand-checkable. Same corpus + same detectors -> byte-identical output.

    python -m benchmarks.detector_validation \
        --corpus benchmarks/detector-corpus.jsonl \
        --out benchmarks/detector-validation-results.md \
        --out-json benchmarks/detector-validation-results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from benchmarks.detector_corpus import TOKEN_ITEMS
from benchmarks.detectors import DETECTORS, Detector


@dataclass(frozen=True)
class CorpusItem:
    """One labeled text: ``labels`` are the detector names that *should* fire."""

    text: str
    labels: frozenset[str]


@dataclass
class Stats:
    """Confusion counts for one detector over the corpus, plus derived rates."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def load_corpus(path: str | Path) -> list[CorpusItem]:
    """Read the JSONL corpus; each line is ``{"text": ..., "labels": [...]}``."""
    items: list[CorpusItem] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            items.append(CorpusItem(row["text"], frozenset(row.get("labels", []))))
    return items


def full_corpus(path: str | Path) -> list[CorpusItem]:
    """The JSONL rows plus the runtime-assembled provider-token items (`detector_corpus`).

    The two sources exist only because a committed file cannot hold literal live-looking
    tokens (push protection blocks them); together they are the one corpus the benchmark
    scores.
    """
    extra = [CorpusItem(row["text"], frozenset(row.get("labels", []))) for row in TOKEN_ITEMS]
    return load_corpus(path) + extra


def evaluate(
    items: list[CorpusItem], detectors: tuple[Detector, ...] = DETECTORS
) -> dict[str, Stats]:
    """Per-detector confusion counts over the corpus. Pure and deterministic.

    For each detector D and item I: fired = D.detects(I.text), expected = D.name in
    I.labels. tp/fp/fn/tn follow directly. Detectors are independent — an item can be a
    true positive for one and a false positive for another.
    """
    stats = {d.name: Stats() for d in detectors}
    for item in items:
        for detector in detectors:
            fired = detector.detects(item.text)
            expected = detector.name in item.labels
            s = stats[detector.name]
            if fired and expected:
                s.tp += 1
            elif fired and not expected:
                s.fp += 1
            elif not fired and expected:
                s.fn += 1
            else:
                s.tn += 1
    return stats


def micro_macro(stats: dict[str, Stats]) -> dict[str, float]:
    """Corpus-level rollups: micro (pooled counts) and macro (mean of per-detector rates)."""
    tp = sum(s.tp for s in stats.values())
    fp = sum(s.fp for s in stats.values())
    fn = sum(s.fn for s in stats.values())
    micro = Stats(tp=tp, fp=fp, fn=fn)
    n = len(stats) or 1
    return {
        "micro_precision": micro.precision,
        "micro_recall": micro.recall,
        "micro_f1": micro.f1,
        "macro_precision": sum(s.precision for s in stats.values()) / n,
        "macro_recall": sum(s.recall for s in stats.values()) / n,
        "macro_f1": sum(s.f1 for s in stats.values()) / n,
    }


# ---------------------------------------------------------------------------- rendering


def render_markdown(stats: dict[str, Stats], *, corpus_size: int, source: str) -> str:
    """Deterministic markdown: same stats -> byte-identical text."""
    rollup = micro_macro(stats)
    lines = [
        "## Detector validation results",
        "",
        f"Reference detectors over `{source}` ({corpus_size} labeled items).",
        "",
        f"**Micro** (pooled): precision {rollup['micro_precision']:.3f}, "
        f"recall {rollup['micro_recall']:.3f}, F1 {rollup['micro_f1']:.3f}. "
        f"**Macro** (mean of detectors): precision {rollup['macro_precision']:.3f}, "
        f"recall {rollup['macro_recall']:.3f}, F1 {rollup['macro_f1']:.3f}.",
        "",
        "| detector | tp | fp | fn | tn | precision | recall | F1 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(stats):
        s = stats[name]
        lines.append(
            f"| {name} | {s.tp} | {s.fp} | {s.fn} | {s.tn} | "
            f"{s.precision:.3f} | {s.recall:.3f} | {s.f1:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def report_json(stats: dict[str, Stats]) -> dict:
    """Stable JSON: sorted detectors, plain types, plus the micro/macro rollup."""
    out: dict = {"detectors": {}, "rollup": micro_macro(stats)}
    for name in sorted(stats):
        s = stats[name]
        out["detectors"][name] = {
            "tp": s.tp, "fp": s.fp, "fn": s.fn, "tn": s.tn,
            "precision": s.precision, "recall": s.recall, "f1": s.f1,
        }
    return out


# ---------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score the reference detectors on a labeled corpus.")
    ap.add_argument("--corpus", default="benchmarks/detector-corpus.jsonl")
    ap.add_argument("--out", default=None, help="write markdown here (default: stdout)")
    ap.add_argument("--out-json", default=None, help="also write the machine-readable report")
    args = ap.parse_args(argv)

    items = full_corpus(args.corpus)
    stats = evaluate(items)
    markdown = render_markdown(stats, corpus_size=len(items), source=args.corpus)
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
