"""Offline calibration — turn labeled prompts into a routing config.

Calibration is the empirical step that maps the structural proxy onto a real
decision. It runs *offline* on a labeled dataset and emits a ``wayfinder.toml``
fragment; the runtime stays deterministic and free (WF-ADR-0003). Nothing here
touches a model — labels come from whatever oracle the caller already has.

Three modes, matching the runtime:

- ``threshold`` — binary: sweep the cut that best separates two labels, emit a
  two-tier (e.g. local/cloud) config.
- ``tiers`` — ordinal multi-class: order the models by mean score and sweep each
  adjacent breakpoint, emit an N-tier config.
- ``classifier`` — fit a multinomial-logistic model over the normalized feature
  vector (pure-Python gradient descent, deterministic), emit a classifier config.

The classifier and the scalar score share one feature transform
(``normalized_features``), so calibration never invents a scale the runtime does
not also use.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from .complexity import (
    FEATURE_ORDER,
    ClassifierModel,
    Tier,
    extract_features,
    normalized_features,
    recommend_tier,
    scalar_score,
)


class CalibrationError(Exception):
    """The calibration dataset or request is malformed (a usage error)."""


@dataclass
class Sample:
    """One labeled prompt: its extracted features and the target model label."""

    features: dict[str, int]
    label: str
    score: float


@dataclass
class CalibrationResult:
    """The emitted config fragment plus a deterministic summary of the fit."""

    toml: str
    summary: dict


def parse_dataset(text: str, where: str = "<dataset>") -> list[Sample]:
    """Parse a JSONL dataset of ``{"text": ..., "label": ...}`` rows from a string.

    The in-memory counterpart of :func:`load_dataset`, so the UI can calibrate
    pasted data without a file. Each row's prompt is scored to features once.
    """
    samples: list[Sample] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"{where}:{lineno}: invalid JSON: {exc}") from exc
        prompt = row.get("text")
        label = row.get("label")
        if not isinstance(prompt, str) or not isinstance(label, str) or not label:
            raise CalibrationError(
                f"{where}:{lineno}: each row needs string 'text' and non-empty 'label'"
            )
        features = extract_features(prompt)
        samples.append(Sample(features=features, label=label, score=_default_score(features)))
    if not samples:
        raise CalibrationError(f"{where}: no labeled rows found")
    return samples


def load_dataset(path: str) -> list[Sample]:
    """Read a JSONL dataset of ``{"text": ..., "label": ...}`` rows from a file.

    Each row's prompt is scored to features once; the label is the model the row
    should route to (for ``threshold`` mode, the two labels are the two arms).
    """
    with open(path, encoding="utf-8") as handle:
        return parse_dataset(handle.read(), where=path)


def _default_score(features: dict[str, int]) -> float:
    # Calibration scores with the default weights; weight-fitting is a separate
    # concern from finding the cut, and keeps threshold/tiers modes interpretable.
    from .complexity import DEFAULT_WEIGHTS

    return scalar_score(features, DEFAULT_WEIGHTS)


def _labels_by_mean_score(samples: list[Sample]) -> list[str]:
    """Distinct labels ordered by ascending mean structural score (deterministic;
    ties broken by label name)."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for s in samples:
        sums[s.label] = sums.get(s.label, 0.0) + s.score
        counts[s.label] = counts.get(s.label, 0) + 1
    means = {label: sums[label] / counts[label] for label in sums}
    return sorted(means, key=lambda label: (means[label], label))


def _sweep_cut(scored: list[tuple[float, bool]]) -> tuple[float, float]:
    """Best threshold separating ``(score, is_high)`` pairs by accuracy.

    Returns ``(threshold, accuracy)``. Rule: predict high when ``score >=
    threshold``. Candidate cuts are the observed scores plus 0.0; ties on accuracy
    break to the median candidate, a stable central choice.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    best_acc = -1.0
    best_cuts: list[float] = []
    for cut in candidates:
        correct = sum(1 for score, is_high in scored if (score >= cut) == is_high)
        acc = correct / total
        if acc > best_acc:
            best_acc = acc
            best_cuts = [cut]
        elif acc == best_acc:
            best_cuts.append(cut)
    return best_cuts[len(best_cuts) // 2], best_acc


def sweep_curve(samples: list[Sample]) -> list[tuple[float, float]]:
    """The full ``(threshold, accuracy)`` curve for two-label data.

    The chart behind threshold calibration: each candidate cut and the accuracy
    of "route high when score >= cut". Deterministic; raises for other than two
    labels. Reuses the same scoring as :func:`calibrate_threshold`.
    """
    labels = _labels_by_mean_score(samples)
    if len(labels) != 2:
        raise CalibrationError(
            f"sweep needs exactly two labels, found {len(labels)}: {labels}"
        )
    _, high = labels
    scored = [(s.score, s.label == high) for s in samples]
    total = len(scored)
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    return [
        (cut, sum(1 for score, is_high in scored if (score >= cut) == is_high) / total)
        for cut in candidates
    ]


def calibrate_threshold(samples: list[Sample]) -> CalibrationResult:
    """Binary calibration: sweep the local/cloud-style cut between two labels."""
    labels = _labels_by_mean_score(samples)
    if len(labels) != 2:
        raise CalibrationError(
            f"threshold mode needs exactly two labels, found {len(labels)}: {labels}"
        )
    low, high = labels
    scored = [(s.score, s.label == high) for s in samples]
    threshold, accuracy = _sweep_cut(scored)
    tiers = (Tier(0.0, low), Tier(threshold, high))
    return CalibrationResult(
        toml=_tiers_toml(tiers),
        summary={"mode": "threshold", "threshold": threshold, "models": [low, high],
                 "accuracy": round(accuracy, 4), "samples": len(samples)},
    )


def calibrate_tiers(
    samples: list[Sample], models_order: list[str] | None = None
) -> CalibrationResult:
    """Ordinal multi-class calibration: order models, sweep each breakpoint."""
    order = models_order or _labels_by_mean_score(samples)
    present = set(s.label for s in samples)
    if set(order) != present:
        raise CalibrationError(f"--models {order} does not match dataset labels {sorted(present)}")
    if len(order) < 2:
        raise CalibrationError("tiers mode needs at least two labels")
    rank = {label: i for i, label in enumerate(order)}
    tiers = [Tier(0.0, order[0])]
    previous = 0.0
    for b in range(len(order) - 1):
        lo, hi = order[b], order[b + 1]
        pair = [(s.score, rank[s.label] >= b + 1) for s in samples if s.label in (lo, hi)]
        cut, _ = _sweep_cut(pair)
        cut = max(cut, previous)  # keep breakpoints non-decreasing
        tiers.append(Tier(cut, hi))
        previous = cut
    tiers_tuple = tuple(tiers)
    accuracy = _accuracy(samples, lambda f: recommend_tier(_default_score(f), tiers_tuple))
    return CalibrationResult(
        toml=_tiers_toml(tiers_tuple),
        summary={"mode": "tiers", "models": list(order),
                 "breakpoints": [t.min_score for t in tiers_tuple[1:]],
                 "accuracy": round(accuracy, 4), "samples": len(samples)},
    )


def fit_classifier(
    samples: list[Sample],
    models_order: list[str] | None = None,
    *,
    iterations: int = 100,
    l2: float = 0.01,
    tol: float = 1e-8,
) -> CalibrationResult:
    """Fit a multinomial-logistic router by L2-regularized Newton/IRLS.

    Deterministic: zero initialization, exact Newton steps from a gradient and
    Hessian accumulated in fixed data order, solved by Gaussian elimination with
    partial pivoting, stopped on a tolerance. The L2 term keeps the Hessian
    positive-definite (so the solve is well-posed even on perfectly separable
    data, where unregularized logistic weights diverge) and bounds the weights.
    The feature space is tiny (7 features x a few classes), so this converges in
    a handful of iterations regardless of dataset size (WF-ADR-0003).
    """
    order = models_order or _labels_by_mean_score(samples)
    present = set(s.label for s in samples)
    if set(order) != present:
        raise CalibrationError(f"--models {order} does not match dataset labels {sorted(present)}")
    if len(order) < 2:
        raise CalibrationError("classifier mode needs at least two labels")

    index = {label: i for i, label in enumerate(order)}
    feat_n = len(FEATURE_ORDER)
    class_n = len(order)
    # Augment each feature row with a constant 1.0 so the intercept is the last
    # parameter of every class; parameter p of class c lives at c * params + p.
    rows = [
        [normalized_features(s.features)[name] for name in FEATURE_ORDER] + [1.0] for s in samples
    ]
    targets = [index[s.label] for s in samples]
    params = feat_n + 1
    size = class_n * params

    theta = [0.0] * size
    iterations_run = 0
    for _ in range(iterations):
        iterations_run += 1
        gradient = [0.0] * size
        hessian = [[0.0] * size for _ in range(size)]
        for row, target in zip(rows, targets, strict=True):
            logits = [_dot(theta[c * params : (c + 1) * params], row) for c in range(class_n)]
            probs = _softmax(logits)
            for c in range(class_n):
                resid = probs[c] - (1.0 if c == target else 0.0)
                base_c = c * params
                for j in range(params):
                    gradient[base_c + j] += resid * row[j]
                # Hessian block H[c, c'] = p_c (delta_cc' - p_c') x x^T.
                for d in range(class_n):
                    weight = probs[c] * ((1.0 if c == d else 0.0) - probs[d])
                    if weight == 0.0:
                        continue
                    base_d = d * params
                    for j in range(params):
                        wj = weight * row[j]
                        for k in range(params):
                            hessian[base_c + j][base_d + k] += wj * row[k]
        # L2 ridge: regularizes the gradient and the Hessian diagonal, keeping the
        # system positive-definite and invertible.
        for p in range(size):
            gradient[p] += l2 * theta[p]
            hessian[p][p] += l2
        step = _solve(hessian, gradient)
        for p in range(size):
            theta[p] -= step[p]
        if max(abs(s) for s in step) < tol:
            break

    weights_by_class = [theta[c * params : (c + 1) * params] for c in range(class_n)]
    classifier = ClassifierModel(
        models=tuple(order),
        weights={
            name: tuple(weights_by_class[c][i] for c in range(class_n))
            for i, name in enumerate(FEATURE_ORDER)
        },
        intercepts=tuple(weights_by_class[c][feat_n] for c in range(class_n)),
    )
    accuracy = _accuracy(samples, classifier.predict)
    return CalibrationResult(
        toml=_classifier_toml(classifier),
        summary={"mode": "classifier", "models": list(order), "iterations": iterations_run,
                 "accuracy": round(accuracy, 4), "samples": len(samples)},
    )


def _dot(weights: list[float], x: list[float]) -> float:
    return sum(w * xi for w, xi in zip(weights, x, strict=True))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve ``matrix @ x = vector`` by Gaussian elimination with partial pivoting.

    Deterministic (fixed pivot order, first-index tie-break). The matrix is the
    regularized Hessian, so it is positive-definite and a pivot is always found.
    """
    n = len(vector)
    # Work on an augmented copy so the inputs are untouched.
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        for r in range(col + 1, n):
            factor = aug[r][col] / pivot_val
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        acc = aug[row][n] - sum(aug[row][c] * solution[c] for c in range(row + 1, n))
        solution[row] = acc / aug[row][row]
    return solution


def _softmax(logits: list[float]) -> list[float]:
    top = max(logits)
    exps = [math.exp(z - top) for z in logits]
    total = sum(exps)
    return [e / total for e in exps]


def _accuracy(samples: list[Sample], predict: Callable[[dict[str, int]], str]) -> float:
    correct = sum(1 for s in samples if predict(s.features) == s.label)
    return correct / len(samples)


def _fmt(value: float) -> str:
    # Stable, readable float formatting for emitted TOML (trim to 6 dp).
    return repr(round(value, 6))


def _tiers_toml(tiers: tuple[Tier, ...]) -> str:
    blocks = []
    for tier in tiers:
        blocks.append(
            "[[routing.tiers]]\n"
            f"min_score = {_fmt(tier.min_score)}\n"
            f'model = "{tier.model}"\n'
        )
    return "\n".join(blocks)


def _classifier_toml(clf: ClassifierModel) -> str:
    models = ", ".join(f'"{m}"' for m in clf.models)
    intercepts = ", ".join(_fmt(b) for b in clf.intercepts)
    lines = [
        "[routing.classifier]",
        f"models = [{models}]",
        f"intercepts = [{intercepts}]",
        "",
        "[routing.classifier.weights]",
    ]
    for name in FEATURE_ORDER:
        vector = ", ".join(_fmt(w) for w in clf.weights[name])
        lines.append(f"{name} = [{vector}]")
    return "\n".join(lines) + "\n"


def calibrate(
    samples: list[Sample],
    mode: str,
    *,
    models_order: list[str] | None = None,
    iterations: int = 100,
    l2: float = 0.01,
) -> CalibrationResult:
    """Dispatch to the requested calibration mode."""
    if mode == "threshold":
        return calibrate_threshold(samples)
    if mode == "tiers":
        return calibrate_tiers(samples, models_order=models_order)
    if mode == "classifier":
        return fit_classifier(samples, models_order=models_order, iterations=iterations, l2=l2)
    raise CalibrationError(f"unknown calibration mode: {mode!r}")
