"""Automated sufficiency judges for offline calibration (WF-ADR-0037).

The onboarding loop (:mod:`wayfinder_router.onboard`) collects per-prompt *sufficiency*
labels — "was the cheaper arm good enough to skip the dearer one?" — through an injected
judge callable. This module supplies automated judges for that seam so the label faucet can
run without a human at the terminal.

Judging is offline / calibration-time only and is pure text comparison: no model, key, or
network is touched on the decision path, so the deterministic core (WF-ADR-0001) stays
untouched. The seam is the :class:`Judge` protocol returning a tri-state :class:`Verdict` —
sufficient (route the cheap arm), insufficient (route the dear arm), or abstain
(``sufficient is None``, so the prompt is skipped and no label is recorded, preserving
threshold calibration's two-label contract). :class:`HeuristicJudge` is a deterministic
ensemble of text comparators; an LLM-backed judge is a planned drop-in via the same protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol, runtime_checkable

# Lowercased substrings that mark a refusal / non-answer. Order is preserved as contract.
# A marker only fires the refusal comparator, which still compares both arms — a hit on
# both arms abstains (the prompt is the problem, not the routing).
DEFAULT_REFUSAL_MARKERS: tuple[str, ...] = (
    "i can't help",
    "i cannot help",
    "i can't assist",
    "i cannot assist",
    "i'm unable to",
    "i am unable to",
    "i'm not able to",
    "i am not able to",
    "i'm sorry, but i can",
    "as an ai language model",
    "i cannot provide",
    "i can't provide",
)

# A normalized response shorter than this is treated as a stub, not an answer.
DEFAULT_MIN_ANSWER_CHARS = 16
# difflib ratio at/above which the two answers count as "the same answer" -> cheap sufficient.
DEFAULT_SIMILARITY_SUFFICIENT = 0.8


@dataclass(frozen=True)
class Verdict:
    """One judge decision about a (cheap, expensive) answer pair.

    ``sufficient`` is tri-state: ``True`` (cheap arm good enough — route it), ``False``
    (cheap arm fell short — route the dear arm), or ``None`` (abstain — no grounds, so the
    prompt is skipped and no label is recorded). ``comparator`` names the deciding rule and
    ``reason`` is human-readable audit metadata; neither is ever persisted as a label.
    """

    sufficient: bool | None
    reason: str
    comparator: str


@runtime_checkable
class Judge(Protocol):
    """The pluggable judge seam: map a (prompt, cheap, expensive) triple to a :class:`Verdict`.

    ``version`` is stamped into provenance so a config records which judge produced its
    labels. Declared ``@runtime_checkable`` so ``isinstance(obj, Judge)`` gates adapters.
    """

    @property
    def version(self) -> str: ...

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict: ...


def _normalize(text: str) -> str:
    """Lowercase and collapse every whitespace run to a single space, stripped."""
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class HeuristicJudge:
    """A pure, deterministic sufficiency judge — an ordered ensemble of text comparators.

    Comparators run in a fixed order and the first decisive rule wins, making the verdict a
    deterministic function of the two responses (replayable from a saved comparison log):

    1. **refusal / stub** — empty, too-short, or refusal-shaped answers. Only the cheap arm
       bad → insufficient; only the dear arm bad → sufficient; both bad → abstain.
    2. **agreement** — identical after normalization → sufficient.
    3. **similarity** — difflib ratio >= ``similarity_sufficient`` → sufficient.
    4. otherwise → abstain (the answers genuinely diverge; a heuristic can't adjudicate).

    Abstaining is preferred to guessing — the sufficiency gates must clear before its labels
    are trusted.
    """

    similarity_sufficient: float = DEFAULT_SIMILARITY_SUFFICIENT
    min_answer_chars: int = DEFAULT_MIN_ANSWER_CHARS
    refusal_markers: tuple[str, ...] = DEFAULT_REFUSAL_MARKERS
    version: str = "heuristic-1"

    def _is_non_answer(self, normalized: str) -> bool:
        """True when the normalized text is empty, below the stub gate, or refusal-shaped."""
        if not normalized or len(normalized) < self.min_answer_chars:
            return True
        return any(marker in normalized for marker in self.refusal_markers)

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict:
        c_norm = _normalize(cheap)
        e_norm = _normalize(expensive)

        # 1. refusal / stub — both-bad is checked before either single-bad case.
        cheap_bad = self._is_non_answer(c_norm)
        exp_bad = self._is_non_answer(e_norm)
        if cheap_bad and exp_bad:
            return Verdict(None, "both arms refused or returned no answer", "refusal")
        if cheap_bad:
            return Verdict(False, "cheap arm refused/empty while the dear arm answered", "refusal")
        if exp_bad:
            return Verdict(True, "dear arm refused/empty while the cheap arm answered", "refusal")

        # 2. agreement — identical after normalization.
        if c_norm == e_norm:
            return Verdict(True, "answers identical after normalization", "agreement")

        # 3. similarity — difflib ratio at/above the threshold.
        ratio = SequenceMatcher(None, c_norm, e_norm).ratio()
        if ratio >= self.similarity_sufficient:
            return Verdict(
                True, f"answers {ratio:.2f} similar (>= {self.similarity_sufficient:.2f})", "similarity"
            )

        # 4. can't tell — abstain rather than guess.
        return Verdict(
            None, f"answers diverge ({ratio:.2f} similar); heuristic cannot adjudicate", "divergence"
        )


# The onboard seam's judge callable: ``(prompt, {arm: output}) -> chosen arm | None``.
OnboardJudge = Callable[[str, dict], "str | None"]


def as_onboard_judge(
    judge: Judge,
    cheap_arm: str,
    expensive_arm: str,
    *,
    on_verdict: Callable[[str, dict, Verdict], None] | None = None,
) -> OnboardJudge:
    """Adapt a :class:`Judge` to the :func:`onboard.run_onboarding` judge callable.

    Returns ``cheap_arm`` on a sufficient verdict, ``expensive_arm`` on insufficient, and
    ``None`` on abstain. ``on_verdict`` (optional) is invoked with ``(prompt, outputs,
    verdict)`` for every decision — a seam for the audit log that keeps the judge free of I/O.
    """

    def _fn(prompt: str, outputs: dict) -> str | None:
        verdict = judge.judge(prompt, outputs[cheap_arm], outputs[expensive_arm])
        if on_verdict is not None:
            on_verdict(prompt, outputs, verdict)  # every verdict, abstains included
        # Tri-state on identity, never truthiness: None must not fall through to a branch.
        if verdict.sufficient is True:
            return cheap_arm
        if verdict.sufficient is False:
            return expensive_arm
        return None

    return _fn
