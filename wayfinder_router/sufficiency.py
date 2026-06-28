"""Trust gates for judge-generated labels (WF-ADR-0037).

An automated judge (:mod:`wayfinder_router.judge`) lets the calibration label faucet run
without a human — but a mislabeled corpus does not produce a bad *eval number*, it
produces a routing config that silently sends real traffic to the wrong tier. So a config
minted from judge labels is **untrusted until it clears these gates**, which measure label
quality directly and are *judge-agnostic* (they work the same for a heuristic or an LLM
judge). This is the same honesty bar calibration already holds itself to (WF-DESIGN-0004:
cross-validated lift over honest baselines), specialized to a judge-sourced label set.

Three gates:

1. **Agreement vs a human gold set** — Cohen's κ between the judge's labels and a small
   hand-labeled set on the same prompts. Below the floor (default 0.6, "substantial") the
   judge disagrees with humans too often to trust; the caller refuses to emit a config and
   prints the confusion matrix.
2. **Out-of-fold lift** — k-fold cross-validated accuracy of the *resulting* config must
   beat the majority-class baseline. If the labels carry no routable signal that
   generalizes, the cut is fitting noise.
3. **Degenerate-collapse** — both arms must be meaningfully represented (not ~all one
   label), or the sweep is trivial and threshold calibration's two-label contract is moot.

Pure and offline: no model call lives here (it consumes labels the judge already produced),
so it unit-tests like ``calibrate.py``. It reuses ``calibrate_threshold`` / ``recommend_tier``
for the CV fit rather than reimplementing the sweep.
"""

from __future__ import annotations

from dataclasses import dataclass

from .calibrate import CalibrationError, Sample, calibrate_threshold
from .complexity import Tier, recommend_tier

DEFAULT_KAPPA_FLOOR = 0.6  # "substantial" agreement (Landis & Koch); below it, refuse.
DEFAULT_CV_FOLDS = 5
DEFAULT_MIN_LIFT = 0.0  # the resulting config must strictly beat the majority baseline.
DEFAULT_DEGENERATE_FRACTION = 0.95  # one label dominating beyond this is a degenerate set.


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's κ for a list of ``(judge_label, gold_label)`` pairs.

    κ = (p_o − p_e) / (1 − p_e): observed agreement corrected for chance. ``1.0`` is
    perfect, ``0.0`` is chance-level, negative is worse than chance. When one label
    saturates both sides (p_e = 1) κ is undefined; we return ``1.0`` iff agreement is also
    perfect, else ``0.0`` (no information beyond the constant prediction).
    """
    n = len(pairs)
    if n == 0:
        return 0.0
    labels = sorted({label for pair in pairs for label in pair})
    observed = sum(1 for a, b in pairs if a == b) / n
    expected = 0.0
    for label in labels:
        p_judge = sum(1 for a, _ in pairs if a == label) / n
        p_gold = sum(1 for _, b in pairs if b == label) / n
        expected += p_judge * p_gold
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def confusion_matrix(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """``matrix[judge_label][gold_label] -> count`` over the ``(judge, gold)`` pairs."""
    labels = sorted({label for pair in pairs for label in pair})
    matrix = {row: {col: 0 for col in labels} for row in labels}
    for judge_label, gold_label in pairs:
        matrix[judge_label][gold_label] += 1
    return matrix


def majority_baseline(samples: list[Sample]) -> float:
    """Accuracy of always predicting the most common label (the floor a fit must beat)."""
    if not samples:
        return 0.0
    counts: dict[str, int] = {}
    for s in samples:
        counts[s.label] = counts.get(s.label, 0) + 1
    return max(counts.values()) / len(samples)


def cross_validated_accuracy(samples: list[Sample], *, k: int = DEFAULT_CV_FOLDS) -> float:
    """Mean out-of-fold accuracy of a threshold fit, by deterministic k-fold CV.

    Each fold is held out, a cut is fit on the rest with ``calibrate_threshold`` (accuracy
    objective), and the held-out fold is scored with ``recommend_tier`` — so the number
    reflects how the labels *generalize*, not how well a cut memorizes them. Folds whose
    training split lacks both labels are skipped (a cut needs two). Returns ``0.0`` when no
    fold is usable.
    """
    n = len(samples)
    if n < 2:
        return 0.0
    k = min(k, n)
    folds = [samples[i::k] for i in range(k)]  # stride partition — deterministic, no RNG.
    accuracies: list[float] = []
    for i in range(k):
        test = folds[i]
        train = [s for j in range(k) if j != i for s in folds[j]]
        if not test or not train:
            continue
        try:
            result = calibrate_threshold(train, objective="accuracy")
        except CalibrationError:
            continue  # the training split had only one label — not a usable fold.
        threshold = result.summary["threshold"]
        low, high = result.summary["models"]
        tiers = (Tier(0.0, low), Tier(threshold, high))
        correct = sum(1 for s in test if recommend_tier(s.score, tiers) == s.label)
        accuracies.append(correct / len(test))
    if not accuracies:
        return 0.0
    return sum(accuracies) / len(accuracies)


@dataclass(frozen=True)
class GateReport:
    """The verdict on whether a judge-labeled set is trustworthy enough to mint a config."""

    kappa: float
    kappa_floor: float
    n_gold: int
    gold_abstained: int
    confusion: dict[str, dict[str, int]]
    cv_accuracy: float
    majority_baseline: float
    lift: float
    label_counts: dict[str, int]
    degenerate: bool
    passed: bool
    failures: tuple[str, ...]

    def render(self) -> str:
        """A human-readable summary of every gate (stderr / refusal message)."""
        lines = [
            f"judge-vs-gold kappa: {self.kappa:.2f} (floor {self.kappa_floor:.2f}, "
            f"n={self.n_gold}, abstained={self.gold_abstained})",
            f"out-of-fold accuracy: {self.cv_accuracy:.2f} vs majority baseline "
            f"{self.majority_baseline:.2f} (lift {self.lift:+.2f})",
            f"label distribution: {self.label_counts}",
        ]
        if self.confusion:
            lines.append("confusion (rows=judge, cols=gold):")
            cols = sorted({c for row in self.confusion.values() for c in row})
            lines.append("            " + "  ".join(f"{c:>10}" for c in cols))
            for row in sorted(self.confusion):
                cells = "  ".join(f"{self.confusion[row].get(c, 0):>10}" for c in cols)
                lines.append(f"{row:>10}  {cells}")
        verdict = "PASS" if self.passed else "REFUSED"
        lines.append(f"trust gates: {verdict}")
        for failure in self.failures:
            lines.append(f"  - {failure}")
        return "\n".join(lines)


def evaluate(
    gold_pairs: list[tuple[str, str]],
    samples: list[Sample],
    *,
    kappa_floor: float = DEFAULT_KAPPA_FLOOR,
    min_lift: float = DEFAULT_MIN_LIFT,
    k: int = DEFAULT_CV_FOLDS,
    gold_abstained: int = 0,
    degenerate_fraction: float = DEFAULT_DEGENERATE_FRACTION,
) -> GateReport:
    """Run all three gates and return a :class:`GateReport` (``passed`` is the verdict).

    ``gold_pairs`` are ``(judge_label, gold_label)`` for prompts the judge did *not* abstain
    on (abstentions are excluded from κ but counted in ``gold_abstained``). ``samples`` are
    the labeled rows the resulting config would be fit on.
    """
    kappa = cohens_kappa(gold_pairs)
    confusion = confusion_matrix(gold_pairs)
    label_counts: dict[str, int] = {}
    for s in samples:
        label_counts[s.label] = label_counts.get(s.label, 0) + 1
    majority = majority_baseline(samples)
    cv_accuracy = cross_validated_accuracy(samples, k=k)
    lift = cv_accuracy - majority
    degenerate = len(label_counts) < 2 or majority > degenerate_fraction

    failures: list[str] = []
    if not gold_pairs:
        failures.append("no gold agreement measured — pass a human-labeled --gold set")
    elif kappa < kappa_floor:
        failures.append(f"judge-vs-gold kappa {kappa:.2f} < floor {kappa_floor:.2f}")
    if degenerate:
        failures.append(
            "labels degenerate — need both arms meaningfully represented, not ~all one arm"
        )
    elif lift <= min_lift:
        failures.append(
            f"no out-of-fold lift — cv accuracy {cv_accuracy:.2f} does not beat "
            f"majority baseline {majority:.2f}"
        )

    return GateReport(
        kappa=kappa,
        kappa_floor=kappa_floor,
        n_gold=len(gold_pairs),
        gold_abstained=gold_abstained,
        confusion=confusion,
        cv_accuracy=cv_accuracy,
        majority_baseline=majority,
        lift=lift,
        label_counts=label_counts,
        degenerate=degenerate,
        passed=not failures,
        failures=tuple(failures),
    )
