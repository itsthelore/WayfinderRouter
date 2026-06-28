"""Automated sufficiency judges for offline calibration (WF-ADR-0037).

The onboarding loop (:mod:`wayfinder_router.onboard`) already collects per-prompt
*sufficiency* labels — "was the cheaper arm good enough to skip the dearer one?" —
through an **injected** judge callable; until now the only judge that existed was the
interactive human at the terminal. This module supplies *automated* judges for that
same seam, so the A/B label faucet can run without a person in the loop.

The judging is offline / calibration-time only. The two arm calls happen in the
invocation layer (the gateway invoker, bring-your-own key) and the judging here is
pure text comparison — **no model, key, or network is touched on the decision path**,
so the deterministic core (WF-ADR-0001) is untouched. The emitted labels flow into the
existing ``calibrate`` pipeline, which already turns ``{text, label}`` rows into a
routing config; this module only *produces the labels*.

The seam is the :class:`Judge` protocol returning a **tri-state** :class:`Verdict`:
sufficient (route the cheap arm), insufficient (route the dear arm), or *abstain*
(``sufficient is None`` — the judge has no grounds, so the prompt is skipped and **no
label is recorded**, never a third label, which would break threshold calibration's
two-label contract). :class:`HeuristicJudge` is a pure, deterministic ensemble of text
comparators — free and replayable, golden-testable like ``cache.py``. An LLM-backed
judge is a planned drop-in via the same protocol; nothing here imports FastAPI/httpx.

A heuristic over free text is a deliberately weak proxy for "good enough" — it abstains
whenever it cannot tell, and its labels are only trusted after the
:mod:`wayfinder_router.sufficiency` gates (agreement vs a human gold set, out-of-fold
lift) clear. Abstaining is always preferred to guessing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol, runtime_checkable

# Lowercased substrings that mark a refusal / non-answer. Conservative on purpose: a
# marker only fires the refusal comparator, which then compares the two arms — a hit on
# *both* arms abstains (the prompt is the problem, not the routing).
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

# A response shorter than this (stripped) is treated as a stub, not an answer.
DEFAULT_MIN_ANSWER_CHARS = 16
# difflib ratio at/above which the two answers are "the same answer" -> cheap sufficient.
DEFAULT_SIMILARITY_SUFFICIENT = 0.8


@dataclass(frozen=True)
class Verdict:
    """One judge decision about a (cheap, expensive) answer pair.

    ``sufficient`` is tri-state: ``True`` (the cheap arm was good enough — route it),
    ``False`` (the cheap arm fell short — route the dear arm), or ``None`` (**abstain**:
    the judge has no grounds; the prompt is skipped and no label is recorded).
    ``comparator`` names the rule that decided, and ``reason`` is human-readable; both
    are metadata for the audit log and are never themselves persisted as a label.
    """

    sufficient: bool | None
    reason: str
    comparator: str


@runtime_checkable
class Judge(Protocol):
    """The pluggable judge seam: map a (prompt, cheap, expensive) triple to a verdict.

    ``version`` is stamped into provenance so a config records which judge produced its
    labels. :class:`HeuristicJudge` implements this today; an ``LLMJudge`` is a planned
    drop-in — both offline, neither on the decision path.
    """

    @property
    def version(self) -> str: ...

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict: ...


def _normalize(text: str) -> str:
    """Lowercase and collapse all whitespace runs to single spaces, stripped."""
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class HeuristicJudge:
    """A pure, deterministic sufficiency judge — an ordered ensemble of text comparators.

    Comparators run in a fixed order; the first decisive one wins, so the verdict is a
    deterministic function of the two responses (re-runnable from a saved comparison log
    with no network, unlike an LLM judge). The rules, in order:

    1. **refusal / error / stub** — an empty, too-short, or refusal-shaped answer. If the
       cheap arm is a non-answer but the dear arm answered → *insufficient*; if the dear
       arm is the non-answer but the cheap arm answered → *sufficient*; if **both** are
       non-answers → *abstain* (the prompt, not the routing, is the problem).
    2. **agreement** — identical after normalization → *sufficient* (the cheap arm
       produced the same answer, so the dear arm added nothing).
    3. **similarity** — difflib ratio ≥ ``similarity_sufficient`` → *sufficient*.
    4. otherwise → *abstain* (the answers genuinely diverge and a heuristic cannot
       adjudicate which is better; that is the honest "can't tell").

    This abstains often — by design. It is strongest on verifiable/structured prompts and
    silent on open-ended prose, which is exactly why the :mod:`sufficiency` gates are
    mandatory before its labels are trusted.
    """

    similarity_sufficient: float = DEFAULT_SIMILARITY_SUFFICIENT
    min_answer_chars: int = DEFAULT_MIN_ANSWER_CHARS
    refusal_markers: tuple[str, ...] = DEFAULT_REFUSAL_MARKERS
    version: str = "heuristic-1"

    def _is_non_answer(self, normalized: str) -> bool:
        if not normalized or len(normalized) < self.min_answer_chars:
            return True
        return any(marker in normalized for marker in self.refusal_markers)

    def judge(self, prompt: str, cheap: str, expensive: str) -> Verdict:
        c_norm = _normalize(cheap)
        e_norm = _normalize(expensive)

        # 1. refusal / error / stub.
        cheap_bad = self._is_non_answer(c_norm)
        exp_bad = self._is_non_answer(e_norm)
        if cheap_bad and exp_bad:
            return Verdict(None, "both arms refused or returned no answer", "refusal")
        if cheap_bad:
            return Verdict(False, "cheap arm refused/empty while the dear arm answered", "refusal")
        if exp_bad:
            return Verdict(True, "dear arm refused/empty while the cheap arm answered", "refusal")

        # 2. agreement.
        if c_norm == e_norm:
            return Verdict(True, "answers identical after normalization", "agreement")

        # 3. similarity.
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

    Returns ``cheap_arm`` on a *sufficient* verdict, ``expensive_arm`` on *insufficient*,
    and ``None`` on *abstain* (the loop then skips the prompt without recording a label).
    ``on_verdict`` (optional) is called with ``(prompt, outputs, verdict)`` for every
    decision — used to write the governed comparison audit log without coupling the judge
    to any I/O.
    """

    def _fn(prompt: str, outputs: dict) -> str | None:
        verdict = judge.judge(prompt, outputs[cheap_arm], outputs[expensive_arm])
        if on_verdict is not None:
            on_verdict(prompt, outputs, verdict)
        if verdict.sufficient is True:
            return cheap_arm
        if verdict.sufficient is False:
            return expensive_arm
        return None

    return _fn
