"""Offline calibration — turn labeled prompts into a routing config.

Calibration is the empirical step that maps the structural proxy onto a real
decision. It runs *offline* on a labeled dataset and emits a ``wayfinder-router.toml``
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


# Default relative per-call costs when the caller gives none (WF-ADR-0017): the
# benchmark's units — the cheap/low arm at 0.2, the expensive/high arm at 1.0.
_DEFAULT_COST_LOW = 0.2
_DEFAULT_COST_HIGH = 1.0


def calibrate_threshold(
    samples: list[Sample],
    *,
    objective: str = "accuracy",
    costs: dict[str, float] | None = None,
    target_savings: float | None = None,
    weights: dict[str, float] | None = None,
) -> CalibrationResult:
    """Binary calibration: sweep the local/cloud-style cut between two labels.

    ``objective="accuracy"`` (the default) picks the most accurate cut.
    ``objective="knee"`` (WF-ADR-0017) picks the *cost-aware knee* — the cut that
    maximizes quality-recovered × cost-saved, with no savings target to guess. On
    skewed labels (one model usually right) the accuracy objective collapses to
    always-routing-high; the knee balances quality and cost on its own.
    ``objective="cost-quality"`` picks the most accurate cut that still reaches
    ``target_savings`` against always-routing-high. All cost objectives take per-arm
    ``costs`` (defaulting to the benchmark's 0.2 / 1.0 units). Cost only moves where
    the cut is placed; the scored path is untouched.

    ``weights`` re-scores the prompts with custom feature weights (e.g. the lexical
    opt-in) before sweeping, and emits them alongside the cut, so the result is a
    complete, deployable config rather than a cut over the default structural score.
    """
    if weights is not None:
        samples = [Sample(s.features, s.label, scalar_score(s.features, weights)) for s in samples]
    prefix = _weights_block(weights)
    labels = _labels_by_mean_score(samples)
    if len(labels) != 2:
        raise CalibrationError(
            f"threshold mode needs exactly two labels, found {len(labels)}: {labels}"
        )
    # Cost objectives route the *expensive* arm above the cut, so order arms by cost
    # (not by mean score, which can tie/invert under custom weights and flip the
    # savings direction); accuracy is symmetric and keeps the mean-score order.
    if objective == "knee":
        cost_low, cost_high, low, high = _cost_ordered_arms(labels, costs)
        scored = [(s.score, s.label == high) for s in samples]
        threshold, accuracy, savings, recall = _sweep_cut_knee(scored, cost_low, cost_high)
        tiers = (Tier(0.0, low, cost_low), Tier(threshold, high, cost_high))
        return CalibrationResult(
            toml=prefix + _tiers_toml(tiers),
            summary={"mode": "threshold", "objective": "knee", "threshold": threshold,
                     "models": [low, high], "accuracy": round(accuracy, 4),
                     "quality_recovered": round(recall, 4), "cost_savings": round(savings, 4),
                     "samples": len(samples)},
        )
    if objective == "cost-quality":
        if target_savings is None:
            raise CalibrationError("cost-quality objective needs a target_savings")
        cost_low, cost_high, low, high = _cost_ordered_arms(labels, costs)
        scored = [(s.score, s.label == high) for s in samples]
        threshold, accuracy, savings = _sweep_cut_cost_aware(
            scored, cost_low, cost_high, target_savings
        )
        tiers = (Tier(0.0, low, cost_low), Tier(threshold, high, cost_high))
        return CalibrationResult(
            toml=prefix + _tiers_toml(tiers),
            summary={"mode": "threshold", "objective": "cost-quality",
                     "threshold": threshold, "models": [low, high],
                     "accuracy": round(accuracy, 4), "cost_savings": round(savings, 4),
                     "target_savings": round(float(target_savings), 4),
                     "samples": len(samples)},
        )
    if objective != "accuracy":
        raise CalibrationError(f"unknown objective: {objective!r}")
    low, high = labels
    scored = [(s.score, s.label == high) for s in samples]
    threshold, accuracy = _sweep_cut(scored)
    tiers = (Tier(0.0, low), Tier(threshold, high))
    return CalibrationResult(
        toml=prefix + _tiers_toml(tiers),
        summary={"mode": "threshold", "threshold": threshold, "models": [low, high],
                 "accuracy": round(accuracy, 4), "samples": len(samples)},
    )


def _cost_ordered_arms(
    labels: list[str], costs: dict[str, float] | None
) -> tuple[float, float, str, str]:
    """``(cost_low, cost_high, low, high)`` for a cost objective: the cheap arm is routed
    below the cut, the expensive arm above it. With explicit ``costs`` the arms are ordered
    by cost (robust to score ties/inversions); with the default units the mean-score order
    is kept (the low-score arm is assumed cheap, as before)."""
    if costs is None:
        low, high = labels
        return _DEFAULT_COST_LOW, _DEFAULT_COST_HIGH, low, high
    a, b = labels
    missing = [label for label in (a, b) if label not in costs]
    if missing:
        raise CalibrationError(
            f"--costs must give a cost for each label; missing: {', '.join(missing)}"
        )
    low, high = (a, b) if float(costs[a]) <= float(costs[b]) else (b, a)
    cost_low, cost_high = float(costs[low]), float(costs[high])
    if cost_high <= 0:
        raise CalibrationError(f"the high-cost arm ('{high}') must have a positive cost")
    return cost_low, cost_high, low, high


def _savings_at(
    scored: list[tuple[float, bool]], cut: float, cost_low: float, cost_high: float
) -> float:
    total = len(scored)
    n_high = sum(1 for score, _ in scored if score >= cut)
    mean_cost = (n_high * cost_high + (total - n_high) * cost_low) / total
    return (cost_high - mean_cost) / cost_high


def _sweep_cut_cost_aware(
    scored: list[tuple[float, bool]], cost_low: float, cost_high: float,
    target_savings: float,
) -> tuple[float, float, float]:
    """Most accurate cut whose cost savings reach ``target_savings``.

    Raising the cut routes more calls to the cheap arm (more savings — the curve
    is monotone in the cut), so the feasible set is the cuts at or above the
    savings target; among them this maximises accuracy, ties broken to the median
    cut (a stable central choice). Raises when even all-low cannot reach it.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    feasible: list[tuple[float, float]] = []  # (accuracy, cut)
    best_savings = 0.0
    for cut in candidates:
        savings = _savings_at(scored, cut, cost_low, cost_high)
        best_savings = max(best_savings, savings)
        if savings + 1e-9 >= target_savings:
            correct = sum(1 for score, is_high in scored if (score >= cut) == is_high)
            feasible.append((correct / total, cut))
    if not feasible:
        raise CalibrationError(
            f"no cut reaches target savings {target_savings:.2f}; "
            f"the most achievable is {best_savings:.2f}"
        )
    best_acc = max(acc for acc, _ in feasible)
    best_cuts = sorted(cut for acc, cut in feasible if acc == best_acc)
    chosen = best_cuts[len(best_cuts) // 2]
    return chosen, best_acc, _savings_at(scored, chosen, cost_low, cost_high)


def _sweep_cut_knee(
    scored: list[tuple[float, bool]], cost_low: float, cost_high: float
) -> tuple[float, float, float, float]:
    """The cost-aware knee: the cut maximizing quality-recovered × cost-saved.

    ``quality recovered`` is the recall of the high arm — the fraction of prompts that
    belong on the strong/expensive model that the cut actually routes there: 1.0 when
    routing everything high, 0.0 when routing everything low. ``cost saved`` is the
    saving vs always-high: 0.0 when routing everything high, maximal when routing
    everything low. Their product is 0 at both ends and peaks at the efficient knee, so
    — unlike the accuracy objective, which collapses to always-high when one model is
    usually right — it trades quality against cost on its own, no ``target_savings`` to
    guess. This mirrors the benchmark knee (WF-ADR-0015); ties break to the median cut.

    Returns ``(threshold, accuracy, savings, recall)``.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    n_high = sum(1 for _, is_high in scored if is_high)
    best_obj = -1.0
    best_cuts: list[float] = []
    for cut in candidates:
        recall = sum(1 for score, is_high in scored if is_high and score >= cut) / n_high
        obj = recall * _savings_at(scored, cut, cost_low, cost_high)
        if obj > best_obj:
            best_obj, best_cuts = obj, [cut]
        elif obj == best_obj:
            best_cuts.append(cut)
    chosen = best_cuts[len(best_cuts) // 2]
    accuracy = sum(1 for score, is_high in scored if (score >= chosen) == is_high) / total
    recall = sum(1 for score, is_high in scored if is_high and score >= chosen) / n_high
    return chosen, accuracy, _savings_at(scored, chosen, cost_low, cost_high), recall


def calibrate_tiers(
    samples: list[Sample], models_order: list[str] | None = None,
    *, weights: dict[str, float] | None = None,
) -> CalibrationResult:
    """Ordinal multi-class calibration: order models, sweep each breakpoint.

    ``weights`` re-scores the prompts with custom feature weights and emits them with the
    breakpoints (as in :func:`calibrate_threshold`)."""
    if weights is not None:
        samples = [Sample(s.features, s.label, scalar_score(s.features, weights)) for s in samples]
    score_with = (lambda f: scalar_score(f, weights)) if weights is not None else _default_score
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
    accuracy = _accuracy(samples, lambda f: recommend_tier(score_with(f), tiers_tuple))
    return CalibrationResult(
        toml=_weights_block(weights) + _tiers_toml(tiers_tuple),
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
    The feature space is tiny (a dozen features x a few classes), so this
    converges in a handful of iterations regardless of dataset size (WF-ADR-0003).
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


def _weights_block(weights: dict[str, float] | None) -> str:
    """A ``[routing]`` weights table for the non-default weights, or '' when there are
    none — so a calibrated cut over custom (e.g. lexical) weights emits a complete config.
    Sorted for byte-stable output."""
    if not weights:
        return ""
    from .complexity import DEFAULT_WEIGHTS

    diff = {name: w for name, w in weights.items() if DEFAULT_WEIGHTS.get(name) != w}
    if not diff:
        return ""
    inner = ", ".join(f"{name} = {_fmt(diff[name])}" for name in sorted(diff))
    return f"[routing]\nweights = {{ {inner} }}\n\n"


def _tiers_toml(tiers: tuple[Tier, ...]) -> str:
    blocks = []
    for tier in tiers:
        block = (
            "[[routing.tiers]]\n"
            f"min_score = {_fmt(tier.min_score)}\n"
            f'model = "{tier.model}"\n'
        )
        if tier.cost is not None:
            block += f"cost = {_fmt(tier.cost)}\n"
        blocks.append(block)
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
    objective: str = "accuracy",
    costs: dict[str, float] | None = None,
    target_savings: float | None = None,
    weights: dict[str, float] | None = None,
) -> CalibrationResult:
    """Dispatch to the requested calibration mode.

    The cost-aware objective (WF-ADR-0017) is scoped to ``threshold`` mode in v1 —
    the binary cut is where a savings target is well defined. ``weights`` (custom
    feature weights, e.g. the lexical opt-in) applies to the score-based modes
    (threshold, tiers); the classifier fits its own weights and ignores it.
    """
    if objective != "accuracy" and mode != "threshold":
        raise CalibrationError(
            f"objective {objective!r} is only available in threshold mode"
        )
    if mode == "threshold":
        return calibrate_threshold(
            samples, objective=objective, costs=costs, target_savings=target_savings,
            weights=weights,
        )
    if mode == "tiers":
        return calibrate_tiers(samples, models_order=models_order, weights=weights)
    if mode == "classifier":
        return fit_classifier(samples, models_order=models_order, iterations=iterations, l2=l2)
    raise CalibrationError(f"unknown calibration mode: {mode!r}")
