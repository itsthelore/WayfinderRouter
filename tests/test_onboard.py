"""Tests for the onboarding harness (WF-ADR-0006).

The model-running and judging are injected, so the A/B loop is tested with fakes —
no model, no terminal.
"""

from __future__ import annotations

import pytest

from wayfinder import read_labels, run_onboarding


def test_onboarding_records_the_judged_arm_and_runs_every_arm(tmp_path):
    log = str(tmp_path / "fb.jsonl")
    ran: list[tuple[str, str]] = []

    def run_model(arm: str, prompt: str) -> str:
        ran.append((arm, prompt))
        return f"{arm}:{prompt}"

    def judge(prompt: str, outputs: dict) -> str:
        # A/B: the user always sees both arms' outputs.
        assert set(outputs) == {"local", "cloud"}
        return "local" if prompt == "easy" else "cloud"

    summary = run_onboarding(["easy", "hard"], ["local", "cloud"], run_model, judge, log)

    assert summary.judged == 2
    assert summary.label_counts == {"local": 1, "cloud": 1}
    assert read_labels(log) == [
        {"text": "easy", "label": "local"},
        {"text": "hard", "label": "cloud"},
    ]
    # Every arm ran for every prompt (the A/B comparison).
    assert set(ran) == {("local", "easy"), ("cloud", "easy"), ("local", "hard"), ("cloud", "hard")}


def test_onboarding_requires_two_arms(tmp_path):
    with pytest.raises(ValueError):
        run_onboarding(["x"], ["only"], lambda a, p: "", lambda p, o: "only", str(tmp_path / "f"))


def test_onboarding_rejects_unknown_judge_arm(tmp_path):
    with pytest.raises(ValueError):
        run_onboarding(["x"], ["a", "b"], lambda a, p: "", lambda p, o: "c", str(tmp_path / "f"))
