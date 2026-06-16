"""Deterministic prompt-complexity scoring and model routing.

Scores a prompt's *structural* complexity and maps it to a model recommendation.
Pure and offline: it reads only structural signals from the text — length,
headings, steps, links, code blocks, tables — with no model, key, or network. The
result is a *fact* (like a classifier's confidence), never a semantic verdict, and
Wayfinder never invokes a model: it recommends, the caller runs inference.

Two routing modes, both deterministic given the config:

- **Tiered** (default) — the structural features collapse to one bounded
  ``0.0–1.0`` score (the ``points / ceiling`` shape, heritage from RAC's
  ``classification.py``), and ordered score *bands* map the score to a model. The
  binary local/cloud router is just the two-tier case.
- **Classifier** — a fitted multinomial-logistic model gives each candidate model
  a linear score over the same normalized features; ``argmax`` picks one. This is
  the multi-signal / multi-model form (WF-ADR-0002, WF-ADR-0003).

A leading YAML frontmatter block is stripped first, so a stored prompt artifact
and the same prompt on stdin score identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Default cut for the binary local/cloud router: below it, local; at or above it,
# cloud. The cut is the user's to calibrate; this is the zero-config default.
DEFAULT_THRESHOLD = 0.5

# The weighted structural features, in stable report order. Each is a count
# scanned deterministically from the prompt body.
FEATURE_ORDER = (
    "word_count",
    "heading_count",
    "max_heading_depth",
    "list_item_count",
    "link_count",
    "code_block_count",
    "table_row_count",
)

# Relative importance of each feature in the scalar score. Length and step count
# dominate because they track how much the prompt asks the model to hold and do.
DEFAULT_WEIGHTS: dict[str, float] = {
    "word_count": 3.0,
    "list_item_count": 2.0,
    "heading_count": 1.5,
    "code_block_count": 1.5,
    "table_row_count": 1.0,
    "link_count": 1.0,
    "max_heading_depth": 1.0,
}

# The feature value at which a feature contributes its full weight. Beyond it the
# contribution saturates, so one very large signal cannot dominate. This is also
# the feature normalization used by the classifier, so values land in 0.0–1.0.
SATURATION: dict[str, float] = {
    "word_count": 400.0,
    "heading_count": 8.0,
    "max_heading_depth": 4.0,
    "list_item_count": 15.0,
    "link_count": 10.0,
    "code_block_count": 4.0,
    "table_row_count": 12.0,
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")

_FRONTMATTER_DELIMITER = "---"
_FRONTMATTER_CLOSERS = ("---", "...")


@dataclass(frozen=True)
class Tier:
    """One band of the tiered router: route to ``model`` when the score is at
    least ``min_score``. The first tier of a config has ``min_score`` 0.0."""

    min_score: float
    model: str


@dataclass(frozen=True)
class ClassifierModel:
    """A fitted multinomial-logistic router over the normalized feature vector.

    ``weights[feature]`` is a per-model vector aligned with ``models``; ``argmax``
    of ``intercept + Σ weight·feature`` over the models picks the recommendation.
    Pure linear algebra at inference — no training, no model call.
    """

    models: tuple[str, ...]
    weights: dict[str, tuple[float, ...]]
    intercepts: tuple[float, ...]

    def logits(self, features: dict[str, int]) -> list[float]:
        x = normalized_features(features)
        out = []
        for c in range(len(self.models)):
            z = self.intercepts[c]
            for name in FEATURE_ORDER:
                z += self.weights.get(name, (0.0,) * len(self.models))[c] * x[name]
            out.append(z)
        return out

    def predict(self, features: dict[str, int]) -> str:
        logits = self.logits(features)
        # argmax with a stable first-index tie-break (deterministic).
        best = 0
        for c in range(1, len(logits)):
            if logits[c] > logits[best]:
                best = c
        return self.models[best]


# Default two-tier router == the binary local/cloud cut at DEFAULT_THRESHOLD.
DEFAULT_TIERS: tuple[Tier, ...] = (Tier(0.0, "local"), Tier(DEFAULT_THRESHOLD, "cloud"))


def binary_tiers(threshold: float = DEFAULT_THRESHOLD) -> tuple[Tier, ...]:
    """The two-tier local/cloud router at ``threshold`` (score >= threshold = cloud)."""
    return (Tier(0.0, "local"), Tier(threshold, "cloud"))


@dataclass(frozen=True)
class RoutingConfig:
    """The routing decision boundary.

    ``weights`` drive the scalar score (always computed). Exactly one routing mode
    is active: ``classifier`` when set, otherwise the ``tiers`` bands. Defaults give
    the zero-config binary local/cloud router.
    """

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    tiers: tuple[Tier, ...] = DEFAULT_TIERS
    classifier: ClassifierModel | None = None

    @classmethod
    def binary(
        cls, threshold: float = DEFAULT_THRESHOLD, weights: dict[str, float] | None = None
    ) -> RoutingConfig:
        """A binary local/cloud config at ``threshold`` (ergonomic constructor)."""
        return cls(
            weights=dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS),
            tiers=binary_tiers(threshold),
        )


DEFAULT_CONFIG = RoutingConfig()


@dataclass
class ComplexityScore:
    """A prompt's structural score and its routing recommendation.

    ``to_dict`` is the stable JSON contract (schema_version 2): the score, the
    recommended model, the active mode, and the boundary used (tiers or the model
    list), plus the raw feature values — so the recommendation is explainable.
    """

    score: float  # 0.0 – 1.0, rounded to 2dp — the structural heaviness
    recommendation: str  # the chosen model name
    mode: str  # "tiered" | "classifier"
    features: dict[str, int]
    tiers: tuple[Tier, ...] | None = None  # set in tiered mode
    models: tuple[str, ...] | None = None  # set in classifier mode

    def to_dict(self) -> dict:
        payload: dict = {
            "schema_version": "2",
            "score": self.score,
            "recommendation": self.recommendation,
            "mode": self.mode,
            "features": dict(self.features),
        }
        if self.tiers is not None:
            payload["tiers"] = [{"min_score": t.min_score, "model": t.model} for t in self.tiers]
        if self.models is not None:
            payload["models"] = list(self.models)
        return payload


def strip_frontmatter(text: str) -> str:
    """Return ``text`` with a leading ``---`` YAML frontmatter block removed.

    A generic, self-contained reimplementation (Wayfinder depends on nothing):
    only a block starting on the very first line counts; an unterminated block is
    left in place so the whole text is still scored.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() in _FRONTMATTER_CLOSERS:
            return "\n".join(lines[i + 1 :])
    return text


def extract_features(text: str) -> dict[str, int]:
    """Scan structural feature counts from a prompt body, frontmatter stripped.

    Pure and deterministic. Lines inside fenced code blocks are not scanned for
    headings, lists, tables, or links (the fence itself is counted as a code
    block), so a code sample's contents do not masquerade as structure.
    """
    body = strip_frontmatter(text)

    word_count = len(body.split())
    heading_count = 0
    max_heading_depth = 0
    list_item_count = 0
    table_row_count = 0
    code_block_count = 0
    link_count = 0

    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            if not in_fence:
                code_block_count += 1
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            heading_count += 1
            max_heading_depth = max(max_heading_depth, len(heading.group(1)))
        elif _LIST_RE.match(line):
            list_item_count += 1
        elif _TABLE_ROW_RE.match(line):
            table_row_count += 1
        link_count += len(_LINK_RE.findall(line))

    return {
        "word_count": word_count,
        "heading_count": heading_count,
        "max_heading_depth": max_heading_depth,
        "list_item_count": list_item_count,
        "link_count": link_count,
        "code_block_count": code_block_count,
        "table_row_count": table_row_count,
    }


def normalized_features(features: dict[str, int]) -> dict[str, float]:
    """Each feature saturated into ``0.0–1.0`` (value / saturation, capped at 1).

    The shared feature transform: the scalar score and the classifier both read
    this, so a feature's scale is defined in exactly one place (``SATURATION``).
    """
    return {name: min(features[name] / SATURATION[name], 1.0) for name in FEATURE_ORDER}


def scalar_score(features: dict[str, int], weights: dict[str, float]) -> float:
    """The bounded ``0.0–1.0`` structural score: weighted, saturating, normalized.

    Computed the same way RAC's ``classification.py`` normalizes fit
    (``points / ceiling``), rounded to 2dp so the reported score is stable.
    """
    norm = normalized_features(features)
    total_weight = sum(weights.values())
    if not total_weight:
        return 0.0
    accumulated = sum(weights.get(name, 0.0) * norm[name] for name in FEATURE_ORDER)
    return round(accumulated / total_weight, 2)


def recommend_tier(score: float, tiers: tuple[Tier, ...]) -> str:
    """The model of the highest tier whose ``min_score`` the score reaches.

    ``tiers`` are ascending by ``min_score`` with a 0.0 first tier, so the binary
    case (``score >= threshold`` routes up) is preserved exactly.
    """
    chosen = tiers[0].model
    for tier in tiers:
        if score >= tier.min_score:
            chosen = tier.model
        else:
            break
    return chosen


def score_complexity(text: str, *, config: RoutingConfig = DEFAULT_CONFIG) -> ComplexityScore:
    """Score ``text`` and recommend a model. The score is always reported; the
    recommendation comes from the classifier when configured, else the tiers."""
    features = extract_features(text)
    score = scalar_score(features, config.weights)
    if config.classifier is not None:
        return ComplexityScore(
            score=score,
            recommendation=config.classifier.predict(features),
            mode="classifier",
            features=features,
            models=config.classifier.models,
        )
    return ComplexityScore(
        score=score,
        recommendation=recommend_tier(score, config.tiers),
        mode="tiered",
        features=features,
        tiers=config.tiers,
    )
