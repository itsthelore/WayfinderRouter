"""Validate the HeuristicJudge against RouterBench's graded labels (WF-ROADMAP-0010 §6).

The evidence engine's credibility rests entirely on the sufficiency judge: shadow-mode
comparisons are judged by :class:`wayfinder_router.judge.HeuristicJudge`, and the evidence
report's win/loss/tie tables are only as good as those verdicts. So the judge gets the
``blind-eval.md`` treatment: replay it over RouterBench's *already-graded* answer pairs —
for each prompt the table records every model's response text **and** its graded score —
and measure how often the judge's verdict agrees with the grades it never saw.

Two gold definitions are reported side by side, because "the cheap arm was good enough"
is genuinely ambiguous and the honest move is to quantify both readings:

* **absolute** — sufficient iff the local model's graded score clears a threshold
  (default 0.5): "the cheap answer was *correct*."
* **relative** — sufficient iff the local score is >= the cloud score: "routing cheap
  *lost nothing*" (when both arms are wrong, the cheap arm was not the mistake).

Abstentions are first-class and never folded into either side (the judge.py honesty
rule): κ and the confusion matrix are computed over decided rows only, and the
abstention rate is always reported next to them. Agreement is Cohen's κ via
:func:`wayfinder_router.sufficiency.cohens_kappa` — the same trust-gate arithmetic the
onboarding loop applies to human gold sets, with the same 0.6 "substantial" floor as
the reference line.

The core (:func:`validate`) is pure and pandas-free so its statistics are golden-tested
(``tests/test_judge_validation.py``) with planted fixtures whose κ is hand-computable —
the report machinery is validated before any real data is trusted to it. Only the CLI
touches pandas, to read the RouterBench pickle:

    # download routerbench_0shot.pkl from huggingface.co/datasets/withmartian/routerbench
    python -m benchmarks.judge_validation \
        --dataset data/routerbench_0shot.pkl --local mistral-7b --cloud gpt-4 \
        --out benchmarks/judge-validation-results.md

Everything is offline and deterministic: same pickle, same flags -> byte-identical
output (WF-ADR-0001's discipline applied to the meta-question of judging the judge).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from wayfinder_router.judge import HeuristicJudge, Judge, Verdict
from wayfinder_router.sufficiency import DEFAULT_KAPPA_FLOOR, cohens_kappa, confusion_matrix

from benchmarks.routerbench_adapter import _find_columns, _is_number, _prompt_text

SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"
GOLD_DEFINITIONS = ("absolute", "relative")

ROUTERARENA_RAW = "https://raw.githubusercontent.com/RouteWorks/RouterArena/main/cached_results/"


@dataclass(frozen=True)
class AlwaysSufficientJudge:
    """Baseline: always rules the cheap arm sufficient. Under class imbalance this scores
    high *accuracy* but κ ≈ 0 — the floor a real judge must beat to earn any trust."""

    version: str = "always-sufficient"

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict:
        return Verdict(True, "baseline: always sufficient", "baseline")


@dataclass(frozen=True)
class ExactMatchJudge:
    """Baseline: sufficient only when the two answers are identical after normalization,
    else abstain — the `HeuristicJudge` agreement rule alone, with nothing else."""

    version: str = "exact-match"

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict:
        if " ".join(cheap.lower().split()) == " ".join(expensive.lower().split()):
            return Verdict(True, "answers identical after normalization", "agreement")
        return Verdict(None, "no exact match; abstain", "divergence")


@dataclass(frozen=True)
class JudgeRow:
    """One graded RouterBench pair: both response texts plus both graded scores."""

    prompt: str
    cheap_text: str
    expensive_text: str
    local_score: float
    cloud_score: float
    bucket: str = "?"


@dataclass
class GoldStats:
    """Judge-vs-gold agreement over the decided (non-abstain) rows, for one gold rule."""

    pairs: list[tuple[str, str]] = field(default_factory=list)

    def add(self, judge_label: str, gold_label: str) -> None:
        self.pairs.append((judge_label, gold_label))

    @property
    def n(self) -> int:
        return len(self.pairs)

    @property
    def accuracy(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for a, b in self.pairs if a == b) / len(self.pairs)

    @property
    def kappa(self) -> float:
        return cohens_kappa(self.pairs)

    @property
    def confusion(self) -> dict[str, dict[str, int]]:
        return confusion_matrix(self.pairs)


@dataclass
class BucketReport:
    """Verdict counts and per-gold agreement for one bucket (eval family or 'overall')."""

    n: int = 0
    abstained: int = 0
    by_comparator: dict[str, int] = field(default_factory=dict)
    gold: dict[str, GoldStats] = field(
        default_factory=lambda: {name: GoldStats() for name in GOLD_DEFINITIONS}
    )
    # comparator -> (correct, decided) vs the *absolute* gold, for the rule-level table.
    comparator_hits: dict[str, list[int]] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return self.n - self.abstained

    @property
    def abstention_rate(self) -> float:
        return self.abstained / self.n if self.n else 0.0


def _gold_labels(row: JudgeRow, threshold: float) -> dict[str, str]:
    return {
        "absolute": SUFFICIENT if row.local_score >= threshold else INSUFFICIENT,
        "relative": SUFFICIENT if row.local_score >= row.cloud_score else INSUFFICIENT,
    }


def validate(
    rows: list[JudgeRow],
    *,
    judge: Judge | None = None,
    gold_threshold: float = 0.5,
) -> dict[str, BucketReport]:
    """Replay ``judge`` over graded pairs; return per-bucket reports plus ``"overall"``.

    Pure and deterministic: row order is preserved, abstentions are excluded from every
    agreement statistic and counted separately, and no randomness exists anywhere.
    """
    judge = judge if judge is not None else HeuristicJudge()
    reports: dict[str, BucketReport] = {"overall": BucketReport()}
    for row in rows:
        verdict = judge.judge(row.prompt, row.cheap_text, row.expensive_text)
        targets = [reports["overall"], reports.setdefault(row.bucket, BucketReport())]
        gold = _gold_labels(row, gold_threshold)
        for report in targets:
            report.n += 1
            report.by_comparator[verdict.comparator] = (
                report.by_comparator.get(verdict.comparator, 0) + 1
            )
            if verdict.sufficient is None:
                report.abstained += 1
                continue
            judge_label = SUFFICIENT if verdict.sufficient else INSUFFICIENT
            for name in GOLD_DEFINITIONS:
                report.gold[name].add(judge_label, gold[name])
            hits = report.comparator_hits.setdefault(verdict.comparator, [0, 0])
            hits[1] += 1
            if judge_label == gold["absolute"]:
                hits[0] += 1
    return reports


# ---------------------------------------------------------------------------- rendering


def _confusion_lines(stats: GoldStats) -> list[str]:
    matrix = stats.confusion
    labels = sorted({label for pair in stats.pairs for label in pair})
    lines = ["| judge \\ gold | " + " | ".join(labels) + " |"]
    lines.append("|" + " --- |" * (len(labels) + 1))
    for row_label in labels:
        cells = " | ".join(str(matrix.get(row_label, {}).get(col, 0)) for col in labels)
        lines.append(f"| {row_label} | {cells} |")
    return lines


def render_markdown(
    reports: dict[str, BucketReport],
    *,
    judge_version: str,
    gold_threshold: float,
    source: str,
) -> str:
    """Deterministic markdown: same reports -> byte-identical text."""
    overall = reports["overall"]
    lines = [
        "## Judge validation results",
        "",
        f"Judge `{judge_version}` replayed over `{source}` "
        f"(absolute gold threshold {gold_threshold:g}; κ floor "
        f"{DEFAULT_KAPPA_FLOOR:g} for reference).",
        "",
        f"**Overall:** n={overall.n}, decided={overall.decided}, "
        f"abstained={overall.abstained} ({overall.abstention_rate:.1%}).",
        "",
        "| bucket | n | abstain % | κ (absolute) | acc (absolute) | κ (relative) | acc (relative) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    ordered = ["overall"] + sorted(k for k in reports if k != "overall")
    for name in ordered:
        r = reports[name]
        a, rel = r.gold["absolute"], r.gold["relative"]
        lines.append(
            f"| {name} | {r.n} | {r.abstention_rate:.1%} | {a.kappa:.3f} | {a.accuracy:.3f} "
            f"| {rel.kappa:.3f} | {rel.accuracy:.3f} |"
        )
    lines += ["", "### Overall confusion (absolute gold, decided rows only)", ""]
    lines += _confusion_lines(overall.gold["absolute"])
    lines += [
        "",
        "### By comparator (decided rows, accuracy vs absolute gold)",
        "",
        "| comparator | fired | decided | accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for comparator in sorted(overall.by_comparator):
        fired = overall.by_comparator[comparator]
        correct, decided = overall.comparator_hits.get(comparator, [0, 0])
        acc = f"{correct / decided:.3f}" if decided else "—"
        lines.append(f"| {comparator} | {fired} | {decided} | {acc} |")
    lines.append("")
    return "\n".join(lines)


def report_json(reports: dict[str, BucketReport]) -> dict:
    """A stable JSON shape for machine consumption (sorted keys, plain types)."""
    out: dict = {}
    for name in sorted(reports):
        r = reports[name]
        out[name] = {
            "n": r.n,
            "decided": r.decided,
            "abstained": r.abstained,
            "abstention_rate": r.abstention_rate,
            "by_comparator": dict(sorted(r.by_comparator.items())),
            "gold": {
                g: {
                    "n": r.gold[g].n,
                    "kappa": r.gold[g].kappa,
                    "accuracy": r.gold[g].accuracy,
                    "confusion": r.gold[g].confusion,
                }
                for g in GOLD_DEFINITIONS
            },
        }
    return out


def load_routerarena_rows(local: str, cloud: str, *, limit: int | None = None) -> list[JudgeRow]:
    """Join two RouterArena ``cached_results/<model>.jsonl`` files into JudgeRows.

    RouterArena is a *second, independent* external source (RouteWorks, over GitHub, no
    HuggingFace): each record carries a real ``generated_answer`` and a graded ``score``,
    exactly the (text, grade) pair the judge needs. Same shape as the RouterBench loader,
    different provenance — so agreement across both is genuine cross-dataset validation.
    """
    import urllib.request

    def fetch(model: str) -> dict[str, dict]:
        raw = urllib.request.urlopen(ROUTERARENA_RAW + model + ".jsonl", timeout=90).read()
        text = raw.decode("utf-8")
        dec = json.JSONDecoder()
        out: dict[str, dict] = {}
        i, n = 0, len(text)
        while i < n:
            while i < n and text[i] in " \r\n\t":
                i += 1
            if i >= n:
                break
            obj, end = dec.raw_decode(text, i)
            out[obj["global_index"]] = obj
            i = end
        return out

    left, right = fetch(local), fetch(cloud)
    rows: list[JudgeRow] = []
    for g in sorted(set(left) & set(right)):
        lr, cr = left[g], right[g]
        rows.append(
            JudgeRow(
                prompt=str(lr.get("question", "")),
                cheap_text=str(lr.get("generated_answer", "")),
                expensive_text=str(cr.get("generated_answer", "")),
                local_score=float(lr["evaluation_result"]["score"]),
                cloud_score=float(cr["evaluation_result"]["score"]),
                bucket=g.rsplit("_", 1)[0],
            )
        )
        if limit and len(rows) >= limit:
            break
    return rows


# ---------------------------------------------------------------------------------- CLI


def _find_response_column(columns: list[str], model: str) -> str | None:
    hits = [c for c in columns if model.lower() in c.lower() and "model_response" in c.lower()]
    return hits[0] if hits else None


def _load_rows(
    dataset: str, local: str, cloud: str, prompt_col: str, task_col: str
) -> tuple[list[JudgeRow], int]:
    """Read a wide RouterBench pickle into JudgeRows; returns (rows, skipped)."""
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("install pandas to read a RouterBench pickle:  pip install pandas") from None
    df = pd.read_pickle(dataset)
    columns = list(df.columns)
    local_score, _ = _find_columns(columns, local)
    cloud_score, _ = _find_columns(columns, cloud)
    local_text = _find_response_column(columns, local)
    cloud_text = _find_response_column(columns, cloud)
    if not all([local_score, cloud_score, local_text, cloud_text]):
        print("could not resolve model columns. available columns:", file=sys.stderr)
        for c in columns:
            print("  ", c, file=sys.stderr)
        raise SystemExit(
            f"resolved -> local score={local_score} text={local_text} ; "
            f"cloud score={cloud_score} text={cloud_text}"
        )
    rows: list[JudgeRow] = []
    skipped = 0
    for record in df.to_dict("records"):
        ls_v, cs_v = record[local_score], record[cloud_score]
        lt_v, ct_v = record[local_text], record[cloud_text]
        if not (_is_number(ls_v) and _is_number(cs_v)) or not (
            isinstance(lt_v, str) and isinstance(ct_v, str)
        ):
            skipped += 1  # a missing grade or response text can't be judged — skip, don't guess
            continue
        rows.append(
            JudgeRow(
                prompt=_prompt_text(record.get(prompt_col, "")),
                cheap_text=lt_v,
                expensive_text=ct_v,
                local_score=float(ls_v),
                cloud_score=float(cs_v),
                bucket=str(record.get(task_col, "?")),
            )
        )
    return rows, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay HeuristicJudge over graded RouterBench pairs.")
    ap.add_argument("--dataset", required=True, help="path to a wide RouterBench .pkl")
    ap.add_argument("--local", required=True, help="substring of the small/cheap model column")
    ap.add_argument("--cloud", required=True, help="substring of the frontier model column")
    ap.add_argument("--prompt-col", default="prompt")
    ap.add_argument("--task-col", default="eval_name", help="bucket column for the per-family table")
    ap.add_argument("--gold-threshold", type=float, default=0.5,
                    help="absolute gold: local score >= this is 'sufficient'")
    ap.add_argument("--out", default=None, help="write the markdown report here (default: stdout)")
    ap.add_argument("--out-json", default=None, help="also write the machine-readable report here")
    args = ap.parse_args(argv)

    rows, skipped = _load_rows(args.dataset, args.local, args.cloud, args.prompt_col, args.task_col)
    judge = HeuristicJudge()
    reports = validate(rows, judge=judge, gold_threshold=args.gold_threshold)
    markdown = render_markdown(
        reports,
        judge_version=judge.version,
        gold_threshold=args.gold_threshold,
        source=f"{args.dataset} ({args.local} vs {args.cloud})",
    )
    if skipped:
        markdown += f"\n*Skipped {skipped} rows with a missing grade or response text.*\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(markdown)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(report_json(reports), f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {args.out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
