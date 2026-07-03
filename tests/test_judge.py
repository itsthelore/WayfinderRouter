"""Tests for the automated sufficiency judge (WF-ADR-0037).

The judge is a pure, deterministic function of two response strings, so it tests with no
model and no network — like ``test_cache.py`` / ``test_reliability.py``.
"""

from __future__ import annotations

from wayfinder_router import HeuristicJudge, Judge, Verdict, as_onboard_judge

# Long enough to clear the 16-char stub gate; distinct enough to exercise each branch.
PARIS = "The capital of France is Paris."
PARIS_BANG = "The capital of France is Paris!"
CELL = "The mitochondria is the powerhouse of the cell, an organelle."


def test_empty_cheap_is_insufficient():
    v = HeuristicJudge().judge("q", "", PARIS)
    assert v.sufficient is False
    assert v.comparator == "refusal"


def test_short_answer_is_not_a_refusal():
    # A terse but real answer must not read as a refusal (the misfire the RouterBench
    # judge-validation surfaced). "42" vs a long divergent answer -> the heuristic can't
    # tell, so it abstains rather than wrongly ruling the cheap arm insufficient.
    v = HeuristicJudge().judge("q", "42", PARIS)
    assert v.sufficient is None
    assert v.comparator == "divergence"


def test_matching_short_answers_are_sufficient():
    # Both arms give the same multiple-choice letter -> exact agreement -> the cheap arm
    # was enough. This is trustworthy signal the old length filter threw away.
    v = HeuristicJudge().judge("q", "C", "C")
    assert v.sufficient is True
    assert v.comparator == "agreement"


def test_terse_dear_answer_is_not_a_refusal():
    # A terse-but-real frontier answer (e.g. a letter) must not trip "dear arm empty ->
    # cheap was sufficient". With a long cheap answer and a short dear one, the judge
    # cannot adjudicate and abstains -- it does not falsely rule sufficient.
    v = HeuristicJudge().judge("q", CELL, "C")
    assert v.sufficient is None
    assert v.comparator != "refusal"


def test_short_similar_answers_do_not_trigger_similarity():
    # "cat" vs "car" are lexically close but semantically different; on short answers
    # fuzzy similarity is unreliable, so it is gated off and the judge abstains.
    v = HeuristicJudge().judge("q", "cat", "car")
    assert v.sufficient is None
    assert v.comparator == "divergence"


def test_refusal_cheap_is_insufficient():
    v = HeuristicJudge().judge("q", "I can't help with that, sorry.", PARIS)
    assert v.sufficient is False
    assert v.comparator == "refusal"


def test_both_non_answers_abstains():
    v = HeuristicJudge().judge("q", "", "   ")
    assert v.sufficient is None
    assert v.comparator == "refusal"


def test_dear_arm_failed_but_cheap_answered_is_sufficient():
    v = HeuristicJudge().judge("q", PARIS, "I'm unable to answer that.")
    assert v.sufficient is True
    assert v.comparator == "refusal"


def test_identical_answers_are_sufficient():
    v = HeuristicJudge().judge("q", PARIS, PARIS)
    assert v.sufficient is True
    assert v.comparator == "agreement"


def test_near_identical_answers_are_sufficient_by_similarity():
    v = HeuristicJudge().judge("q", PARIS, PARIS_BANG)
    assert v.sufficient is True
    assert v.comparator == "similarity"


def test_divergent_answers_abstain():
    v = HeuristicJudge().judge("q", PARIS, CELL)
    assert v.sufficient is None
    assert v.comparator == "divergence"


def test_judge_is_deterministic():
    j = HeuristicJudge()
    assert j.judge("q", PARIS, CELL) == j.judge("q", PARIS, CELL)


def test_heuristic_judge_satisfies_the_protocol():
    assert isinstance(HeuristicJudge(), Judge)
    assert HeuristicJudge().version == "heuristic-2"


class _FixedJudge:
    version = "fixed"

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict:
        return self._verdict


def test_adapter_maps_sufficient_to_cheap_arm():
    fn = as_onboard_judge(_FixedJudge(Verdict(True, "", "x")), "local", "cloud")
    assert fn("p", {"local": "a", "cloud": "b"}) == "local"


def test_adapter_maps_insufficient_to_expensive_arm():
    fn = as_onboard_judge(_FixedJudge(Verdict(False, "", "x")), "local", "cloud")
    assert fn("p", {"local": "a", "cloud": "b"}) == "cloud"


def test_adapter_maps_abstain_to_none():
    fn = as_onboard_judge(_FixedJudge(Verdict(None, "", "x")), "local", "cloud")
    assert fn("p", {"local": "a", "cloud": "b"}) is None


def test_adapter_invokes_on_verdict_callback():
    seen = []
    fn = as_onboard_judge(
        _FixedJudge(Verdict(True, "why", "x")), "local", "cloud",
        on_verdict=lambda prompt, outputs, verdict: seen.append((prompt, verdict.reason)),
    )
    fn("p", {"local": "a", "cloud": "b"})
    assert seen == [("p", "why")]
