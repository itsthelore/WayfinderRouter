"""Onboarding harness — collect local-vs-hosted judgments into the label log.

The A/B onboarding loop: for each sample prompt, run the arms (e.g. a local and a
hosted model), let the user judge which was good enough, and record that judgment
as a label. Enough labels and ``calibrate`` produces a routing config — after
which you route automatically (WF-ADR-0006).

The model-running and the judging are *injected*, so the loop is pure and testable
without a model or a terminal; the CLI supplies the real model calls (the gateway
invoker, with a bring-your-own key) and the interactive prompt. Lives in the
invocation layer; the deterministic core is untouched.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .feedback import record_label

RunModel = Callable[[str, str], str]  # (arm, prompt) -> output text
Judge = Callable[[str, dict], str]  # (prompt, {arm: output}) -> chosen arm


@dataclass
class OnboardSummary:
    """How many prompts were judged, and the label distribution."""

    judged: int = 0
    label_counts: dict[str, int] = field(default_factory=dict)


def run_onboarding(
    prompts: Iterable[str],
    arms: list[str],
    run_model: RunModel,
    judge: Judge,
    log_path: str,
) -> OnboardSummary:
    """Run the A/B onboarding loop, recording one label per prompt.

    Each prompt is run through every arm (the A/B comparison the user sees);
    ``judge`` returns the arm that was good enough, which is recorded as the label.
    """
    if len(arms) < 2:
        raise ValueError("onboarding needs at least two arms (e.g. a local and a hosted model)")
    summary = OnboardSummary()
    for prompt in prompts:
        outputs = {arm: run_model(arm, prompt) for arm in arms}
        label = judge(prompt, outputs)
        if label not in arms:
            raise ValueError(f"judge returned an unknown arm: {label!r}")
        record_label(log_path, prompt, label)
        summary.judged += 1
        summary.label_counts[label] = summary.label_counts.get(label, 0) + 1
    return summary
