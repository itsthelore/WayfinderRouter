"""A/B onboarding loop that turns local-vs-hosted judgments into label-log rows.

Onboarding is how a fresh install acquires its first routing signal: replay a handful
of sample prompts through two candidate arms (typically a cheap local model and a more
capable hosted one), let a judge pick the arm that was good enough on each prompt, and
persist that pick as a label. Once enough labels accumulate, ``calibrate`` can derive a
routing config and the gateway starts routing on its own (WF-ADR-0006).

The model call and the judgment are both *injected*, which keeps this core a pure loop
over strings — no model, no keys, no terminal. The CLI wires in the real gateway invoker
and either an interactive prompt or an automated judge; this module stays deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .feedback import record_label

# Injected model call: given an arm name and a prompt, produce that arm's output text.
RunModel = Callable[[str, str], str]
# Injected judgment: given the prompt and the {arm: output} map, return the winning arm,
# or ``None`` to abstain. Abstention is load-bearing: a human judge always names an arm,
# but an automated judge (WF-ADR-0037) may decline when it has no grounds. The loop then
# skips the prompt so no third "abstain" pseudo-label ever leaks into the log — keeping
# calibration's two-label assumption intact.
Judge = Callable[[str, dict], "str | None"]


@dataclass
class OnboardSummary:
    """Tally of a run: prompts judged, prompts skipped, and the per-arm label counts."""

    judged: int = 0
    abstained: int = 0
    # default_factory (never a bare ``{}``) so every summary owns its own dict.
    label_counts: dict[str, int] = field(default_factory=dict)


def run_onboarding(
    prompts: Iterable[str],
    arms: list[str],
    run_model: RunModel,
    judge: Judge,
    log_path: str,
) -> OnboardSummary:
    """Run the A/B loop over ``prompts``, recording one label per judged prompt.

    Positional signature is contract — both CLI entry points call this by position.
    Every prompt is run through *every* arm before judging (the full A/B comparison),
    even when the judge goes on to abstain. The judge's returned arm becomes the recorded
    label; ``None`` skips the prompt (counted under ``abstained``, nothing logged). An arm
    the judge names that is not in ``arms`` is a hard error.
    """
    # At least two arms — there is nothing to compare with fewer. The upper bound is a
    # caller's concern (the CLI caps to 2); this loop happily accepts three or more.
    if len(arms) < 2:
        raise ValueError("onboarding needs at least two arms (e.g. a local and a hosted model)")

    summary = OnboardSummary()
    for prompt in prompts:
        # Build all arm outputs first, in arms order, so the judge sees the full set.
        outputs = {arm: run_model(arm, prompt) for arm in arms}
        label = judge(prompt, outputs)
        if label is None:
            # The abstain check precedes the unknown-arm check, so abstaining never raises.
            summary.abstained += 1
            continue
        if label not in arms:
            raise ValueError(f"judge returned an unknown arm: {label!r}")
        # record_label appends {"text", "label"} JSONL and raises on empty text/label;
        # we let that propagate rather than pre-validating the prompt here.
        record_label(log_path, prompt, label)
        summary.judged += 1
        summary.label_counts[label] = summary.label_counts.get(label, 0) + 1
    return summary
