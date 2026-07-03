"""Tests for the judge-validation benchmark (WF-ROADMAP-0010 §6).

The planted fixtures make every statistic hand-computable, so the harness's arithmetic
is proven before any real RouterBench data is trusted to it — the same discipline as
``test_benchmarks.py``: validate the meter, then read the measurement.
"""

from __future__ import annotations

from benchmarks.judge_validation import (
    INSUFFICIENT,
    SUFFICIENT,
    JudgeRow,
    render_markdown,
    report_json,
    validate,
)

# A long divergent pair the similarity comparator cannot adjudicate -> abstain.
_DIVERGENT_A = "The mitochondria is the powerhouse of the cell and produces ATP for energy."
_DIVERGENT_B = "Paris has been the capital of France since the late tenth century, roughly."
_REFUSAL = "I cannot help with that request at all, unfortunately."
_ANSWER = "The answer to the question is forty-two, computed directly."


def _agreement_row(local_score: float, bucket: str = "math") -> JudgeRow:
    """Identical texts -> the 'agreement' comparator -> judge says sufficient."""
    return JudgeRow(
        prompt="p", cheap_text=_ANSWER, expensive_text=_ANSWER,
        local_score=local_score, cloud_score=1.0, bucket=bucket,
    )


def _planted_rows() -> list[JudgeRow]:
    rows: list[JudgeRow] = []
    # 6 correct sufficients: identical answers, local graded correct.
    rows += [_agreement_row(1.0) for _ in range(6)]
    # 2 correct insufficients: cheap arm refuses, dear arm answers, local graded wrong.
    rows += [
        JudgeRow(prompt="p", cheap_text=_REFUSAL, expensive_text=_ANSWER,
                 local_score=0.0, cloud_score=1.0, bucket="qa")
        for _ in range(2)
    ]
    # 1 wrong sufficient (absolute gold): dear arm refuses so the judge says sufficient,
    # but local was graded wrong. Cloud also scored 0.0, so the *relative* gold says
    # sufficient — this row is exactly the absolute/relative divergence case.
    rows.append(
        JudgeRow(prompt="p", cheap_text=_ANSWER, expensive_text=_REFUSAL,
                 local_score=0.0, cloud_score=0.0, bucket="qa")
    )
    # 1 wrong sufficient under both golds: identical answers but local graded wrong.
    rows.append(_agreement_row(0.0, bucket="qa"))
    # 2 abstentions: divergent answers the heuristic cannot adjudicate.
    rows += [
        JudgeRow(prompt="p", cheap_text=_DIVERGENT_A, expensive_text=_DIVERGENT_B,
                 local_score=1.0, cloud_score=1.0, bucket="math")
        for _ in range(2)
    ]
    return rows


def test_planted_counts_and_abstention():
    overall = validate(_planted_rows())["overall"]
    assert overall.n == 12
    assert overall.abstained == 2
    assert overall.decided == 10
    assert overall.abstention_rate == 2 / 12
    assert overall.by_comparator == {"agreement": 7, "refusal": 3, "divergence": 2}


def test_planted_kappa_matches_hand_computation():
    # Decided pairs vs absolute gold: judge sufficient 8x (6 gold-sufficient, 2 not),
    # judge insufficient 2x (both gold-insufficient). observed = 0.8;
    # expected = 0.8*0.6 + 0.2*0.4 = 0.56; kappa = 0.24/0.44 = 6/11.
    stats = validate(_planted_rows())["overall"].gold["absolute"]
    assert stats.n == 10
    assert stats.accuracy == 0.8
    assert abs(stats.kappa - 6 / 11) < 1e-12
    assert stats.confusion == {
        SUFFICIENT: {SUFFICIENT: 6, INSUFFICIENT: 2},
        INSUFFICIENT: {SUFFICIENT: 0, INSUFFICIENT: 2},
    }


def test_relative_gold_forgives_the_shared_miss():
    # The dear-arm-refusal row scores 0.0 on both arms: wrong under absolute gold,
    # right under relative gold ("routing cheap lost nothing").
    reports = validate(_planted_rows())
    absolute = reports["overall"].gold["absolute"]
    relative = reports["overall"].gold["relative"]
    assert absolute.accuracy == 0.8
    assert relative.accuracy == 0.9


def test_buckets_partition_overall():
    reports = validate(_planted_rows())
    assert set(reports) == {"overall", "math", "qa"}
    assert reports["math"].n + reports["qa"].n == reports["overall"].n
    assert reports["math"].abstained == 2 and reports["qa"].abstained == 0


def test_perfect_judge_scores_kappa_one():
    rows = [_agreement_row(1.0)] * 5 + [
        JudgeRow(prompt="p", cheap_text=_REFUSAL, expensive_text=_ANSWER,
                 local_score=0.0, cloud_score=1.0)
        for _ in range(5)
    ]
    stats = validate(rows)["overall"].gold["absolute"]
    assert stats.kappa == 1.0 and stats.accuracy == 1.0


def test_report_is_deterministic():
    rows = _planted_rows()
    first, second = validate(rows), validate(rows)
    assert report_json(first) == report_json(second)
    md = render_markdown(first, judge_version="heuristic-1", gold_threshold=0.5, source="planted")
    assert md == render_markdown(second, judge_version="heuristic-1", gold_threshold=0.5,
                                 source="planted")
    assert "| overall | 12 | 16.7% |" in md
    assert "0.545" in md  # 6/11 rendered at 3 places


def test_comparator_accuracy_table():
    overall = validate(_planted_rows())["overall"]
    # agreement decided 7 (6 right), refusal decided 3 (2 right), divergence never decides.
    assert overall.comparator_hits["agreement"] == [6, 7]
    assert overall.comparator_hits["refusal"] == [2, 3]
    assert "divergence" not in overall.comparator_hits
