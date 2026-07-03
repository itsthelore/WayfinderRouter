"""Offline calibration — fit a routing config from a labeled prompt set.

Calibration is the empirical step that pins the structural proxy onto a concrete
routing decision. It runs *offline* over a labeled dataset and emits a
``wayfinder-router.toml`` fragment; the runtime itself stays deterministic and
model-free (WF-ADR-0003). Nothing here calls a model — the labels come from
whatever oracle the caller already trusts.

The three modes mirror the runtime router:

- ``threshold`` — binary: sweep the cut that best separates two labels and emit a
  two-tier (e.g. local/cloud) config.
- ``tiers`` — ordinal multi-class: order the models by mean score, sweep each
  adjacent breakpoint, and emit an N-tier config.
- ``classifier`` — fit a multinomial-logistic router over the normalized feature
  vector by deterministic pure-Python Newton/IRLS, and emit a classifier config.

Calibration and the runtime scalar score share a single feature transform
(``normalized_features``), so calibration never invents a scale the runtime does
not also use.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from .complexity import (
    ClassifierModel,
    DEFAULT_LEXICON,
    FEATURE_ORDER,
    Lexicon,
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
    """One labeled prompt: its extracted features, target label, and score.

    The field order — ``features, label, score`` — is contract: callers and
    helpers build samples positionally (re-scoring, for instance, emits
    ``Sample(f, l, s)``).
    """

    features: dict[str, int]
    label: str
    score: float


@dataclass
class CalibrationResult:
    """The emitted config fragment paired with a deterministic summary of the fit."""

    toml: str
    summary: dict


def parse_dataset(
    text: str, where: str = "<dataset>", *, lexicon: Lexicon = DEFAULT_LEXICON
) -> list[Sample]:
    """Parse a JSONL dataset of ``{"text": ..., "label": ...}`` rows from a string.

    The in-memory counterpart of :func:`load_dataset`, so the UI can calibrate
    pasted data with no file. Blank lines are ignored; each surviving row's prompt
    is scored to features once, with ``lexicon`` selecting the lexical triggers.
    """
    samples: list[Sample] = []
    for lineno, source in enumerate(text.splitlines(), start=1):
        stripped = source.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"{where}:{lineno}: invalid JSON: {exc}") from exc
        prompt = record.get("text")
        label = record.get("label")
        if not isinstance(prompt, str) or not isinstance(label, str) or not label:
            raise CalibrationError(
                f"{where}:{lineno}: each row needs string 'text' and non-empty 'label'"
            )
        features = extract_features(prompt, lexicon=lexicon)
        samples.append(Sample(features=features, label=label, score=_default_score(features)))
    if not samples:
        raise CalibrationError(f"{where}: no labeled rows found")
    return samples


def load_dataset(path: str, *, lexicon: Lexicon = DEFAULT_LEXICON) -> list[Sample]:
    """Read a JSONL dataset of ``{"text": ..., "label": ...}`` rows from a file.

    A thin wrapper over :func:`parse_dataset`. Read and decode failures (OSError,
    UnicodeDecodeError) propagate unwrapped — the CLI handles them separately — so,
    unlike the config loader, they are never turned into a ``CalibrationError``.
    """
    with open(path, encoding="utf-8") as handle:
        return parse_dataset(handle.read(), where=path, lexicon=lexicon)


def _default_score(features: dict[str, int]) -> float:
    # Calibration scores with the default weights; fitting weights is a separate
    # concern from finding the cut, which keeps threshold/tiers interpretable.
    from .complexity import DEFAULT_WEIGHTS

    return scalar_score(features, DEFAULT_WEIGHTS)


def _labels_by_mean_score(samples: list[Sample]) -> list[str]:
    """Distinct labels ordered by ascending mean structural score.

    Deterministic; a tie on the mean is broken by label name ascending, so the
    default ordering the whole pipeline leans on is reproducible.
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for s in samples:
        sums[s.label] = sums.get(s.label, 0.0) + s.score
        counts[s.label] = counts.get(s.label, 0) + 1
    means = {label: sums[label] / counts[label] for label in sums}
    return sorted(means, key=lambda label: (means[label], label))


def _sweep_cut(scored: list[tuple[float, bool]]) -> tuple[float, float]:
    """Most accurate cut over ``(score, is_high)`` pairs.

    The rule is "predict high when ``score >= cut``". Candidate cuts are the
    distinct observed scores (rounded to 4 dp) plus 0.0, scanned in ascending
    order; on an accuracy tie the median candidate wins, a stable central pick.
    Returns ``(threshold, accuracy)``.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    best_acc = -1.0
    ties: list[float] = []
    for candidate in candidates:
        hits = sum(1 for score, is_high in scored if (score >= candidate) == is_high)
        acc = hits / total
        if acc > best_acc:
            best_acc = acc
            ties = [candidate]
        elif acc == best_acc:
            ties.append(candidate)
    return ties[len(ties) // 2], best_acc


def sweep_curve(samples: list[Sample]) -> list[tuple[float, float]]:
    """The full ``(threshold, accuracy)`` curve for two-label data.

    The chart behind threshold calibration: every candidate cut and the accuracy
    of "route high when ``score >= cut``". Deterministic, and it scores exactly as
    :func:`calibrate_threshold` does. Raises for anything but two labels.
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
    curve: list[tuple[float, float]] = []
    for candidate in candidates:
        hits = sum(1 for score, is_high in scored if (score >= candidate) == is_high)
        curve.append((candidate, hits / total))
    return curve


# Default relative per-call costs used when the caller supplies none (WF-ADR-0017):
# the benchmark's units, with the cheap/low arm at 0.2 and the pricey/high arm at 1.0.
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

    ``objective="accuracy"`` (the default) takes the most accurate cut.
    ``objective="knee"`` (WF-ADR-0017) takes the cost-aware knee — the cut that
    maximizes quality-recovered x cost-saved with no savings target to guess; where
    accuracy collapses to always-route-high on skewed labels, the knee still trades
    quality against cost. ``objective="cost-quality"`` takes the most accurate cut
    that still reaches ``target_savings`` versus always-route-high. Cost objectives
    read per-arm ``costs`` (defaulting to the 0.2/1.0 units).

    ``weights`` re-scores the prompts with custom feature weights before the sweep
    and emits them alongside the cut, so the result is a complete config rather
    than a cut over the bare default structural score.
    """
    # Note the guard is ``is not None``: an empty dict still re-scores (to all-zero
    # scores) even though _weights_block emits nothing for it — the two guards
    # deliberately disagree on ``{}``.
    if weights is not None:
        samples = [Sample(s.features, s.label, scalar_score(s.features, weights)) for s in samples]
    prefix = _weights_block(weights)

    labels = _labels_by_mean_score(samples)
    if len(labels) != 2:
        raise CalibrationError(
            f"threshold mode needs exactly two labels, found {len(labels)}: {labels}"
        )

    # Cost objectives route the pricey arm above the cut, so they order arms by cost
    # (robust to score ties/inversions under custom weights); accuracy is symmetric
    # and keeps the mean-score order. Each summary's key insertion order is
    # user-visible (the CLI joins summary.items()), so it is contract.
    if objective == "knee":
        cost_low, cost_high, low, high = _cost_ordered_arms(labels, costs)
        scored = [(s.score, s.label == high) for s in samples]
        threshold, accuracy, savings, recall = _sweep_cut_knee(scored, cost_low, cost_high)
        tiers = (Tier(0.0, low, cost_low), Tier(threshold, high, cost_high))
        return CalibrationResult(
            toml=prefix + _tiers_toml(tiers),
            summary={
                "mode": "threshold",
                "objective": "knee",
                "threshold": threshold,
                "models": [low, high],
                "accuracy": round(accuracy, 4),
                "quality_recovered": round(recall, 4),
                "cost_savings": round(savings, 4),
                "samples": len(samples),
            },
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
            summary={
                "mode": "threshold",
                "objective": "cost-quality",
                "threshold": threshold,
                "models": [low, high],
                "accuracy": round(accuracy, 4),
                "cost_savings": round(savings, 4),
                "target_savings": round(float(target_savings), 4),
                "samples": len(samples),
            },
        )

    if objective != "accuracy":
        raise CalibrationError(f"unknown objective: {objective!r}")

    # Accuracy path: no cost metadata, and no "objective" key in the summary.
    low, high = labels
    scored = [(s.score, s.label == high) for s in samples]
    threshold, accuracy = _sweep_cut(scored)
    tiers = (Tier(0.0, low), Tier(threshold, high))
    return CalibrationResult(
        toml=prefix + _tiers_toml(tiers),
        summary={
            "mode": "threshold",
            "threshold": threshold,
            "models": [low, high],
            "accuracy": round(accuracy, 4),
            "samples": len(samples),
        },
    )


def _cost_ordered_arms(
    labels: list[str], costs: dict[str, float] | None
) -> tuple[float, float, str, str]:
    """Return ``(cost_low, cost_high, low, high)`` for a cost objective.

    The cheap arm routes below the cut, the pricey arm above it. Without explicit
    ``costs`` the mean-score order is kept (the low-score arm is assumed cheap) and
    the default 0.2/1.0 units apply. With ``costs``, arms are ordered by cost so the
    savings direction is robust to score ties or inversions.
    """
    if costs is None:
        low, high = labels
        return _DEFAULT_COST_LOW, _DEFAULT_COST_HIGH, low, high
    a, b = labels
    missing = [label for label in (a, b) if label not in costs]
    if missing:
        raise CalibrationError(
            f"--costs must give a cost for each label; missing: {', '.join(missing)}"
        )
    # ``<=`` keeps ``a`` on the low side when the two costs tie, so ordering stays
    # deterministic.
    low, high = (a, b) if float(costs[a]) <= float(costs[b]) else (b, a)
    cost_low, cost_high = float(costs[low]), float(costs[high])
    if cost_high <= 0:
        raise CalibrationError(f"the high-cost arm ('{high}') must have a positive cost")
    return cost_low, cost_high, low, high


def _savings_at(
    scored: list[tuple[float, bool]], cut: float, cost_low: float, cost_high: float
) -> float:
    # Fraction of the always-high cost this cut saves; monotone increasing in cut.
    total = len(scored)
    n_high = sum(1 for score, _ in scored if score >= cut)
    mean_cost = (n_high * cost_high + (total - n_high) * cost_low) / total
    return (cost_high - mean_cost) / cost_high


def _sweep_cut_cost_aware(
    scored: list[tuple[float, bool]],
    cost_low: float,
    cost_high: float,
    target_savings: float,
) -> tuple[float, float, float]:
    """Most accurate cut whose savings reach ``target_savings``.

    Raising the cut routes more calls to the cheap arm (more savings — the curve is
    monotone), so the feasible set is the cuts at or above the target. A 1e-9
    epsilon lets a savings value landing exactly on the target still count as
    feasible. Among the feasible cuts accuracy is maximized, ties broken to the
    median. Raises when even all-low cannot reach the target.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    reachable: list[tuple[float, float]] = []  # (accuracy, cut)
    best_savings = 0.0
    for candidate in candidates:
        savings = _savings_at(scored, candidate, cost_low, cost_high)
        best_savings = max(best_savings, savings)
        if savings + 1e-9 >= target_savings:
            hits = sum(1 for score, is_high in scored if (score >= candidate) == is_high)
            reachable.append((hits / total, candidate))
    if not reachable:
        raise CalibrationError(
            f"no cut reaches target savings {target_savings:.2f}; "
            f"the most achievable is {best_savings:.2f}"
        )
    best_acc = max(acc for acc, _ in reachable)
    top_cuts = sorted(cut for acc, cut in reachable if acc == best_acc)
    chosen = top_cuts[len(top_cuts) // 2]
    return chosen, best_acc, _savings_at(scored, chosen, cost_low, cost_high)


def _sweep_cut_knee(
    scored: list[tuple[float, bool]], cost_low: float, cost_high: float
) -> tuple[float, float, float, float]:
    """The cost-aware knee: the cut maximizing quality-recovered x cost-saved.

    Quality recovered is the recall of the high arm (the share of high-belonging
    prompts actually routed high): 1.0 routing all high, 0.0 routing all low. Cost
    saved is the saving versus always-high: 0.0 routing all high, maximal routing
    all low. Their product is zero at both ends and peaks at the knee, so — unlike
    accuracy, which collapses to always-high when one model usually wins — it
    trades quality for cost on its own (WF-ADR-0015). Ties break to the median cut.
    Returns ``(threshold, accuracy, savings, recall)``.
    """
    candidates = sorted({0.0, *(round(score, 4) for score, _ in scored)})
    total = len(scored)
    n_high = sum(1 for _, is_high in scored if is_high)
    best_obj = -1.0
    ties: list[float] = []
    for candidate in candidates:
        recall = sum(1 for score, is_high in scored if is_high and score >= candidate) / n_high
        obj = recall * _savings_at(scored, candidate, cost_low, cost_high)
        if obj > best_obj:
            best_obj, ties = obj, [candidate]
        elif obj == best_obj:
            ties.append(candidate)
    chosen = ties[len(ties) // 2]
    accuracy = sum(1 for score, is_high in scored if (score >= chosen) == is_high) / total
    recall = sum(1 for score, is_high in scored if is_high and score >= chosen) / n_high
    return chosen, accuracy, _savings_at(scored, chosen, cost_low, cost_high), recall


def calibrate_tiers(
    samples: list[Sample],
    models_order: list[str] | None = None,
    *,
    weights: dict[str, float] | None = None,
) -> CalibrationResult:
    """Ordinal multi-class calibration: order the models, sweep each breakpoint.

    ``weights`` re-scores the prompts with custom feature weights and emits them
    with the breakpoints, exactly as :func:`calibrate_threshold` does.
    """
    if weights is not None:
        samples = [Sample(s.features, s.label, scalar_score(s.features, weights)) for s in samples]
    score_with = (lambda f: scalar_score(f, weights)) if weights is not None else _default_score
    order = models_order or _labels_by_mean_score(samples)
    present = {s.label for s in samples}
    if set(order) != present:
        raise CalibrationError(
            f"--models {order} does not match dataset labels {sorted(present)}"
        )
    if len(order) < 2:
        raise CalibrationError("tiers mode needs at least two labels")

    rank = {label: i for i, label in enumerate(order)}
    tiers = [Tier(0.0, order[0])]
    previous = 0.0
    for i in range(len(order) - 1):
        lo, hi = order[i], order[i + 1]
        pair = [(s.score, rank[s.label] >= i + 1) for s in samples if s.label in (lo, hi)]
        cut, _ = _sweep_cut(pair)
        cut = max(cut, previous)  # breakpoints stay non-decreasing
        tiers.append(Tier(cut, hi))
        previous = cut
    tiers_tuple = tuple(tiers)
    accuracy = _accuracy(samples, lambda f: recommend_tier(score_with(f), tiers_tuple))
    return CalibrationResult(
        toml=_weights_block(weights) + _tiers_toml(tiers_tuple),
        summary={
            "mode": "tiers",
            "models": list(order),
            "breakpoints": [t.min_score for t in tiers_tuple[1:]],
            "accuracy": round(accuracy, 4),
            "samples": len(samples),
        },
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

    Deterministic end to end: zero initialization, exact Newton steps built from a
    gradient and Hessian accumulated in fixed data order, solved by Gaussian
    elimination with partial pivoting, stopped on a tolerance. The L2 term keeps
    the Hessian positive-definite (so the solve stays well-posed even on perfectly
    separable data, where unregularized logistic weights run away) and bounds the
    weights. The feature space is tiny (a dozen features x a few classes), so this
    converges in a handful of iterations whatever the dataset size (WF-ADR-0003).
    """
    if l2 <= 0:  # the ridge is what holds the Hessian PD; 0 or negative can go singular
        raise CalibrationError(f"--l2 must be > 0 (got {l2}); it keeps the solve well-posed")
    order = models_order or _labels_by_mean_score(samples)
    present = {s.label for s in samples}
    if set(order) != present:
        raise CalibrationError(
            f"--models {order} does not match dataset labels {sorted(present)}"
        )
    if len(order) < 2:
        raise CalibrationError("classifier mode needs at least two labels")

    index = {label: i for i, label in enumerate(order)}
    feat_n = len(FEATURE_ORDER)
    class_n = len(order)
    # Each feature row carries a trailing constant 1.0, so the intercept is the last
    # parameter of every class; parameter p of class c lives at c * params + p.
    design = [
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
        for x, target in zip(design, targets, strict=True):
            logits = [_dot(theta[c * params : (c + 1) * params], x) for c in range(class_n)]
            prob = _softmax(logits)
            for c in range(class_n):
                residual = prob[c] - (1.0 if c == target else 0.0)
                offset_c = c * params
                for j in range(params):
                    gradient[offset_c + j] += residual * x[j]
                # Hessian block H[c, d] = p_c (delta_cd - p_d) x x^T.
                for d in range(class_n):
                    coeff = prob[c] * ((1.0 if c == d else 0.0) - prob[d])
                    if coeff == 0.0:
                        continue
                    offset_d = d * params
                    for j in range(params):
                        cx_j = coeff * x[j]
                        for k in range(params):
                            hessian[offset_c + j][offset_d + k] += cx_j * x[k]
        # L2 ridge on the gradient and the Hessian diagonal keeps the system PD.
        for p in range(size):
            gradient[p] += l2 * theta[p]
            hessian[p][p] += l2
        step = _solve(hessian, gradient)
        for p in range(size):
            theta[p] -= step[p]
        if max(abs(s) for s in step) < tol:
            break

    class_params = [theta[c * params : (c + 1) * params] for c in range(class_n)]
    classifier = ClassifierModel(
        models=tuple(order),
        weights={
            name: tuple(class_params[c][i] for c in range(class_n))
            for i, name in enumerate(FEATURE_ORDER)
        },
        intercepts=tuple(class_params[c][feat_n] for c in range(class_n)),
    )
    accuracy = _accuracy(samples, classifier.predict)
    return CalibrationResult(
        toml=_classifier_toml(classifier),
        summary={
            "mode": "classifier",
            "models": list(order),
            "iterations": iterations_run,
            "accuracy": round(accuracy, 4),
            "samples": len(samples),
        },
    )


def _dot(weights: list[float], x: list[float]) -> float:
    return sum(w * xi for w, xi in zip(weights, x, strict=True))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve ``matrix @ x = vector`` by Gaussian elimination with partial pivoting.

    Deterministic: a fixed pivot scan with a first-index tie-break (Python ``max``
    returns the first maximizer). The matrix is the regularized Hessian, hence
    positive-definite, so a nonzero pivot is always available.
    """
    n = len(vector)
    # Augment a copy so the caller's matrix and vector are left untouched.
    work = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for k in range(n):
        pivot = max(range(k, n), key=lambda r: abs(work[r][k]))
        if pivot != k:
            work[k], work[pivot] = work[pivot], work[k]
        pivot_value = work[k][k]
        for i in range(k + 1, n):
            factor = work[i][k] / pivot_value
            if factor == 0.0:
                continue
            for j in range(k, n + 1):
                work[i][j] -= factor * work[k][j]
    solution = [0.0] * n
    for i in range(n - 1, -1, -1):
        residual = work[i][n] - sum(work[i][j] * solution[j] for j in range(i + 1, n))
        solution[i] = residual / work[i][i]
    return solution


def _softmax(logits: list[float]) -> list[float]:
    # Shift by the max before exp for overflow safety, then normalize.
    ceiling = max(logits)
    exps = [math.exp(z - ceiling) for z in logits]
    total = sum(exps)
    return [e / total for e in exps]


def _accuracy(samples: list[Sample], predict: Callable[[dict[str, int]], str]) -> float:
    correct = sum(1 for s in samples if predict(s.features) == s.label)
    return correct / len(samples)


def _fmt(value: float) -> str:
    # Byte-stable float formatting for emitted TOML (6 dp round, then repr).
    # Intentionally NOT config._fmt_num, which adds a float() cast; keeping them
    # apart means unifying cannot change either module's int-valued output bytes.
    return repr(round(value, 6))


def _weights_block(weights: dict[str, float] | None) -> str:
    """A ``[routing]`` weights table for the non-default weights, or '' for none.

    So a cut calibrated over custom (e.g. lexical) weights emits a complete,
    deployable config. Falsy weights (``None`` or ``{}``) emit nothing; keys are
    sorted for byte stability.
    """
    if not weights:
        return ""
    from .complexity import DEFAULT_WEIGHTS

    diff = {
        name: value
        for name, value in weights.items()
        if DEFAULT_WEIGHTS.get(name) != value
    }
    if not diff:
        return ""
    inner = ", ".join(f"{name} = {_fmt(diff[name])}" for name in sorted(diff))
    return f"[routing]\nweights = {{ {inner} }}\n\n"


def _tiers_toml(tiers: tuple[Tier, ...]) -> str:
    blocks = []
    for tier in tiers:
        rows = [
            "[[routing.tiers]]",
            f"min_score = {_fmt(tier.min_score)}",
            f'model = "{tier.model}"',
        ]
        if tier.cost is not None:
            rows.append(f"cost = {_fmt(tier.cost)}")
        # A trailing newline per block means the "\n" join leaves a blank line
        # between successive tiers.
        blocks.append("\n".join(rows) + "\n")
    return "\n".join(blocks)


def _classifier_toml(clf: ClassifierModel) -> str:
    models = ", ".join(f'"{m}"' for m in clf.models)
    intercepts = ", ".join(_fmt(b) for b in clf.intercepts)
    weight_lines = (
        f"{name} = [{', '.join(_fmt(w) for w in clf.weights[name])}]" for name in FEATURE_ORDER
    )
    lines = [
        "[routing.classifier]",
        f"models = [{models}]",
        f"intercepts = [{intercepts}]",
        "",
        "[routing.classifier.weights]",
        *weight_lines,
    ]
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

    ``mode`` is positional-or-keyword; the rest are keyword-only. The cost-aware
    objectives (WF-ADR-0017) are scoped to ``threshold`` mode, the one place a
    savings target is well defined. ``weights`` (custom feature weights, e.g. the
    lexical opt-in) applies to the score-based modes (threshold, tiers); the
    classifier fits its own weights and ignores it.
    """
    if objective != "accuracy" and mode != "threshold":
        raise CalibrationError(
            f"objective {objective!r} is only available in threshold mode"
        )
    if mode == "threshold":
        return calibrate_threshold(
            samples,
            objective=objective,
            costs=costs,
            target_savings=target_savings,
            weights=weights,
        )
    if mode == "tiers":
        return calibrate_tiers(samples, models_order=models_order, weights=weights)
    if mode == "classifier":
        return fit_classifier(samples, models_order=models_order, iterations=iterations, l2=l2)
    raise CalibrationError(f"unknown calibration mode: {mode!r}")
