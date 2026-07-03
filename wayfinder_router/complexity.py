"""Deterministic prompt-complexity scoring and model routing.

This module turns a prompt into a bounded ``0.0-1.0`` structural score and a
model recommendation, using nothing but the text itself. It is pure and offline:
no model is invoked, no key is read, no network is touched. Wayfinder only
*recommends* a tier; the caller runs inference. The score is a measured fact
about the prompt's shape and vocabulary, not a semantic judgement.

Structural signals (length, headings, list steps, links, fenced code, tables)
are joined by opt-in lexical signals (hard-reasoning vocabulary, math glyphs,
constraint markers, question marks) so that a short-but-hard prompt can be told
apart from a short-easy one (WF-ADR-0016). The lexical weights ship at zero: they
are computed and reported but do not move the default score until a deployment
calibrates them on its own traffic.

Two deterministic routing modes share the same normalized feature vector:

- **Tiered** (default): the weighted features collapse to one scalar score and
  ascending score bands map it to a model. The binary local/cloud router is the
  two-band special case.
- **Classifier**: a fitted multinomial-logistic model scores every candidate and
  ``argmax`` selects one (WF-ADR-0002, WF-ADR-0003).

A leading ``---`` YAML frontmatter block is stripped before scoring, so a stored
prompt artifact and the same prompt piped on stdin score identically.

The numeric behaviour here is a byte-for-byte cross-language contract (the Python
golden corpus is mirrored in JavaScript). Constants, regexes, the two distinct
summation orders in :func:`scalar_score`, and every rounding site are frozen
semantics: change any of them and cross-language parity breaks.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Default cut for the binary local/cloud router: at or above it routes to cloud,
# below it stays local. Deployments calibrate this; this is the zero-config value.
DEFAULT_THRESHOLD = 0.5

# The scored features, in their canonical order. This single tuple fixes the
# feature-dict key order, the classifier's summation order, the explain order,
# and the normalized-vector order all at once.
FEATURE_ORDER = (
    "word_count",
    "heading_count",
    "max_heading_depth",
    "list_item_count",
    "link_count",
    "code_block_count",
    "table_row_count",
    "reasoning_term_count",
    "math_symbol_count",
    "constraint_term_count",
    "question_count",
)

# Per-feature weights in the scalar score. Length and step count dominate; the
# four lexical features ship at 0.0 (measured and reported, but off by default).
# A keyword lexicon detects an author's vocabulary rather than difficulty in
# general (WF-ADR-0016), so raising these weights is an opt-in a deployment makes
# after calibrating on its own labels.
#
# This dict's INSERTION ORDER is a parity contract: ``sum(weights.values())`` in
# scalar_score/explain_score adds in exactly this order, which differs from
# FEATURE_ORDER. Float addition is non-associative, so both orders are load-bearing.
DEFAULT_WEIGHTS: dict[str, float] = {
    "word_count": 3.0,
    "list_item_count": 2.0,
    "heading_count": 1.5,
    "code_block_count": 1.5,
    "table_row_count": 1.0,
    "link_count": 1.0,
    "max_heading_depth": 1.0,
    "reasoning_term_count": 0.0,  # opt-in lexical signal
    "math_symbol_count": 0.0,  # opt-in lexical signal
    "constraint_term_count": 0.0,  # opt-in lexical signal
    "question_count": 0.0,  # opt-in lexical signal
}

# The feature value at which a feature reaches its full contribution; beyond it
# the contribution saturates so no single large signal can dominate. This is also
# the classifier's feature normalization, keeping every value inside 0.0-1.0.
SATURATION: dict[str, float] = {
    "word_count": 400.0,
    "heading_count": 8.0,
    "max_heading_depth": 4.0,
    "list_item_count": 15.0,
    "link_count": 10.0,
    "code_block_count": 4.0,
    "table_row_count": 12.0,
    "reasoning_term_count": 2.0,
    "math_symbol_count": 6.0,
    "constraint_term_count": 3.0,
    "question_count": 3.0,
}

# Structural line matchers. Each is anchored via re.match (start of the line).
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")  # group(1) length == heading depth
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")  # counted per line via findall

# Built-in lexical vocabularies (WF-ADR-0016): hard-reasoning verbs/concepts and
# multi-constraint markers. Both are overridable per deployment via Lexicon.
_REASONING_TERMS = frozenset({
    "prove", "proof", "proofs", "proven", "derive", "derives", "derivation",
    "theorem", "theorems", "lemma", "lemmas", "corollary", "axiom", "axioms",
    "irrational", "undecidable", "undecidability", "decidable", "infinitely",
    "asymptotic", "complexity", "invariant", "invariants", "concurrency",
    "concurrent", "deadlock", "induction", "contradiction", "optimal",
    "optimality", "optimize", "optimise", "minimise", "minimize", "maximise",
    "maximize", "recurrence", "halting", "eigenvalue", "eigenvalues", "integral",
    "derivative", "polynomial", "prime", "primes", "modulo", "isomorphism",
    "monotonic", "bijection", "injective", "surjective", "combinatorial",
})
_CONSTRAINT_TERMS = frozenset({
    "must", "without", "only", "ensure", "exactly", "guarantee", "constraint",
    "constraints", "subject", "preserving", "preserve",
})

# The scorer's own tokenizer (also imported by benchmarks/mine_lexicon.py):
# an ASCII-letter word, allowing internal apostrophes and hyphens.
_WORD_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z'\-]*")

# Math/logic signal: any of a fixed glyph set, or a LaTeX-ish ``\`` + ASCII
# letters token. The glyph class is exact — it contains N-ARY SUMMATION (U+2211)
# and GREEK CAPITAL SIGMA (U+03A3) but NOT N-ARY PRODUCT (U+220F); only GREEK
# CAPITAL PI (U+03A0). Applied to the raw body (not lowercased).
_MATH_SYMBOL_RE = re.compile(r"[∑∫√≤≥≠≈∞∂∈∉∀∃⊆⊂∪∩∇±×÷πθλμσΣΠ]|\\[a-zA-Z]+")

_FRONTMATTER_DELIMITER = "---"
_FRONTMATTER_CLOSERS = ("---", "...")


@dataclass(frozen=True)
class Lexicon:
    """Overridable trigger words behind the reasoning/constraint features.

    The two term sets default to the built-ins but may be replaced per deployment
    so that "calibrate on your own traffic" reaches the vocabulary too. Math
    symbols and the question count are built-in and are not part of a lexicon.
    Frozen and hashable so it can live inside a frozen :class:`RoutingConfig`.
    """

    reasoning_terms: frozenset[str] = _REASONING_TERMS
    constraint_terms: frozenset[str] = _CONSTRAINT_TERMS


DEFAULT_LEXICON = Lexicon()


@dataclass(frozen=True)
class Tier:
    """One band of the tiered router: use ``model`` when the score reaches
    ``min_score``. The first tier of a config has ``min_score`` 0.0.

    ``cost`` is optional per-call cost metadata (WF-ADR-0017) consumed by
    cost-aware calibration and surfaced in dashboards; it never enters scoring.
    """

    min_score: float
    model: str
    cost: float | None = None


@dataclass(frozen=True)
class ClassifierModel:
    """A fitted multinomial-logistic router over the normalized feature vector.

    ``weights[feature]`` is a per-model vector aligned with ``models``; the
    recommendation is the ``argmax`` of ``intercept + Σ weight·feature``. Pure
    linear algebra at inference — no training and no model call.
    """

    models: tuple[str, ...]
    weights: dict[str, tuple[float, ...]]
    intercepts: tuple[float, ...]

    def logits(self, features: dict[str, int]) -> list[float]:
        """Per-model linear scores; features summed in FEATURE_ORDER (contract)."""
        x = normalized_features(features)
        zero = (0.0,) * len(self.models)
        out: list[float] = []
        for c in range(len(self.models)):
            z = self.intercepts[c]
            for name in FEATURE_ORDER:
                z += self.weights.get(name, zero)[c] * x[name]
            out.append(z)
        return out

    def predict(self, features: dict[str, int]) -> str:
        """The argmax model, with a deterministic first-index tie-break."""
        logits = self.logits(features)
        best = 0
        for c in range(1, len(logits)):
            if logits[c] > logits[best]:  # strict > keeps the earliest on ties
                best = c
        return self.models[best]


# The zero-config two-tier router: the binary local/cloud cut at DEFAULT_THRESHOLD.
DEFAULT_TIERS: tuple[Tier, ...] = (Tier(0.0, "local"), Tier(DEFAULT_THRESHOLD, "cloud"))


def binary_tiers(threshold: float = DEFAULT_THRESHOLD) -> tuple[Tier, ...]:
    """The two-tier local/cloud router at ``threshold`` (score >= threshold => cloud)."""
    return (Tier(0.0, "local"), Tier(threshold, "cloud"))


@dataclass(frozen=True)
class RoutingConfig:
    """The routing decision boundary.

    ``weights`` always drive the scalar score. Exactly one recommendation mode is
    active: the ``classifier`` when present, otherwise the ``tiers`` bands. The
    defaults produce the zero-config binary local/cloud router.
    """

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    tiers: tuple[Tier, ...] = DEFAULT_TIERS
    classifier: ClassifierModel | None = None
    lexicon: Lexicon = DEFAULT_LEXICON

    @classmethod
    def binary(
        cls,
        threshold: float = DEFAULT_THRESHOLD,
        weights: dict[str, float] | None = None,
        lexicon: Lexicon | None = None,
    ) -> RoutingConfig:
        """A binary local/cloud config at ``threshold`` (ergonomic constructor)."""
        return cls(
            weights=dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS),
            tiers=binary_tiers(threshold),
            lexicon=lexicon if lexicon is not None else DEFAULT_LEXICON,
        )


DEFAULT_CONFIG = RoutingConfig()


@dataclass
class ComplexityScore:
    """A prompt's structural score together with its routing recommendation.

    :meth:`to_dict` is the stable JSON contract (schema version 3, the string
    ``"3"``): score, recommended model, active mode, raw feature values, and the
    boundary that was used (tiers in tiered mode, the model list in classifier
    mode). It is not frozen because callers build it incrementally.
    """

    score: float  # 0.0-1.0, rounded to 2dp
    recommendation: str  # chosen model name
    mode: str  # "tiered" | "classifier"
    features: dict[str, int]
    tiers: tuple[Tier, ...] | None = None  # present in tiered mode only
    models: tuple[str, ...] | None = None  # present in classifier mode only

    def to_dict(self) -> dict:
        payload: dict = {
            "schema_version": "3",
            "score": self.score,
            "recommendation": self.recommendation,
            "mode": self.mode,
            "features": dict(self.features),  # copy: input mutation cannot leak
        }
        if self.tiers is not None:
            payload["tiers"] = [
                {"min_score": t.min_score, "model": t.model}
                | ({"cost": t.cost} if t.cost is not None else {})
                for t in self.tiers
            ]
        if self.models is not None:
            payload["models"] = list(self.models)
        return payload


def strip_frontmatter(text: str) -> str:
    """Return ``text`` with a leading ``---`` YAML frontmatter block removed.

    Only a block opening on the very first line counts. Splitting is on ``\\n``
    alone (so ``\\r`` stays on line ends), and an unterminated block is left in
    place so the whole text is still scored.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() in _FRONTMATTER_CLOSERS:
            return "\n".join(lines[i + 1 :])
    return text


def extract_features(text: str, *, lexicon: Lexicon = DEFAULT_LEXICON) -> dict[str, int]:
    """Scan the frozen feature counts from a prompt body (frontmatter stripped).

    Pure and deterministic. Lines inside fenced code blocks are excluded from the
    heading/list/table/link scan (the fence line itself counts as one code block),
    so code samples do not masquerade as structure. ``lexicon`` (keyword-only)
    supplies the reasoning/constraint vocabularies.
    """
    body = strip_frontmatter(text)

    # Word count is a plain whitespace split (drops empty tokens) — distinct from
    # the splitlines() used below and the split("\n") used in strip_frontmatter.
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
                code_block_count += 1  # count opening toggles only
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
        # Link counting is unconditional per non-fence line: a link on a heading
        # or list line still counts.
        link_count += len(_LINK_RE.findall(line))

    # Lexical signals scan the whole (lowercased) body as prose. Counting via a
    # single Counter build then summing occurrences over the fixed-size lexicons
    # is exactly equal to per-token membership summation (integer occurrence
    # counts, order-independent) while doing prompt-length-independent lookups.
    counts = Counter(_WORD_TOKEN_RE.findall(body.lower()))
    reasoning_term_count = sum(counts[term] for term in lexicon.reasoning_terms)
    constraint_term_count = sum(counts[term] for term in lexicon.constraint_terms)

    math_symbol_count = len(_MATH_SYMBOL_RE.findall(body))  # raw body, not lowered
    question_count = body.count("?")

    return {
        "word_count": word_count,
        "heading_count": heading_count,
        "max_heading_depth": max_heading_depth,
        "list_item_count": list_item_count,
        "link_count": link_count,
        "code_block_count": code_block_count,
        "table_row_count": table_row_count,
        "reasoning_term_count": reasoning_term_count,
        "math_symbol_count": math_symbol_count,
        "constraint_term_count": constraint_term_count,
        "question_count": question_count,
    }


def normalized_features(features: dict[str, int]) -> dict[str, float]:
    """Each feature saturated into ``0.0-1.0`` (value / saturation, capped at 1.0).

    The shared transform read by both the scalar score and the classifier, so a
    feature's scale lives in exactly one place (:data:`SATURATION`).
    """
    return {name: min(features[name] / SATURATION[name], 1.0) for name in FEATURE_ORDER}


def scalar_score(features: dict[str, int], weights: dict[str, float]) -> float:
    """The bounded ``0.0-1.0`` structural score: weighted saturating average.

    The two summations use two different orders on purpose: ``total_weight`` adds
    in the ``weights`` dict's insertion order, while the numerator adds in
    FEATURE_ORDER. Both orders are the cross-language parity contract. The final
    quotient is rounded to 2dp once (Python round = round-half-to-even).
    """
    norm = normalized_features(features)
    total_weight = sum(weights.values())
    if not total_weight:
        return 0.0
    accumulated = sum(weights.get(name, 0.0) * norm[name] for name in FEATURE_ORDER)
    return round(accumulated / total_weight, 2)


def recommend_tier(score: float, tiers: tuple[Tier, ...]) -> str:
    """The model of the highest tier whose ``min_score`` the score reaches.

    ``tiers`` are ascending by ``min_score`` with a 0.0 first tier, so scanning
    upward and stopping at the first unmet band preserves the binary case exactly.
    """
    chosen = tiers[0].model
    for tier in tiers:
        if score >= tier.min_score:
            chosen = tier.model
        else:
            break
    return chosen


def score_complexity(text: str, *, config: RoutingConfig = DEFAULT_CONFIG) -> ComplexityScore:
    """Score ``text`` and recommend a model.

    The scalar score is always reported. The recommendation comes from the
    classifier when the config carries one (classifier mode, ``tiers`` left None),
    otherwise from the score bands (tiered mode, ``models`` left None).
    """
    features = extract_features(text, lexicon=config.lexicon)
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


@dataclass(frozen=True)
class FeatureContribution:
    """One feature's share of the scalar score — the "why" behind a recommendation.

    ``contribution`` is ``weight × normalized / Σweights`` computed per feature
    (division applied per feature, not once at the end), so summing the
    contributions reconstructs the unrounded score. Powers both ``route --explain``
    and the explain UI.
    """

    name: str
    value: int  # raw feature count
    normalized: float  # value / saturation, capped at 1.0, rounded to 4dp
    weight: float
    contribution: float  # share of the score, rounded to 4dp

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "normalized": self.normalized,
            "weight": self.weight,
            "contribution": self.contribution,
        }


def explain_score(
    features: dict[str, int], weights: dict[str, float]
) -> list[FeatureContribution]:
    """Break the scalar score into one :class:`FeatureContribution` per feature.

    Iterates FEATURE_ORDER; ``total_weight`` sums in the weights dict's insertion
    order. Both ``normalized`` and ``contribution`` are rounded to 4dp.
    """
    norm = normalized_features(features)
    total_weight = sum(weights.values())
    out: list[FeatureContribution] = []
    for name in FEATURE_ORDER:
        weight = weights.get(name, 0.0)
        contribution = (weight * norm[name] / total_weight) if total_weight else 0.0
        out.append(
            FeatureContribution(
                name=name,
                value=features[name],
                normalized=round(norm[name], 4),
                weight=weight,
                contribution=round(contribution, 4),
            )
        )
    return out
