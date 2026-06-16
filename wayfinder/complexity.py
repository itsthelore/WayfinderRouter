"""Deterministic prompt-complexity scoring and local/cloud routing.

Scores a prompt's *structural* complexity and maps it to a ``local`` / ``cloud``
routing recommendation against a threshold. Pure and offline: it reads only
structural signals from the text — length, headings, steps, links, code blocks,
tables — with no model, key, or network. The result is a *fact* (like a
classifier's confidence), never a semantic verdict about how hard the prompt is,
and Wayfinder never invokes a model: the caller maps the recommendation onto its
own configured endpoints and runs inference.

The scoring shape — each feature contributes a weighted, saturating amount toward
the total weight, normalized to ``0.0 – 1.0`` — is heritage from RAC's
deterministic ``classification.py`` (``points / ceiling``), but Wayfinder carries
no dependency on RAC (WF-ADR-0001). A leading YAML frontmatter block is stripped
first, so a stored prompt artifact and the same prompt on stdin score identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Below this score the prompt is recommended for the local model; at or above it,
# the cloud model. The cut is the user's to calibrate (``[routing] threshold`` in
# ``wayfinder.toml``); this is the zero-config default.
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

# Relative importance of each feature. Length and step count dominate because
# they track how much the prompt asks the model to hold and do.
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
# contribution saturates, so one very large signal cannot dominate the score.
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
class RoutingConfig:
    """The routing decision boundary — threshold plus feature weights.

    Defaults give a working zero-config surface; ``wayfinder.toml`` may override
    the threshold (and, optionally, individual weights) so a team calibrates the
    cut to its own local/cloud capability.
    """

    threshold: float = DEFAULT_THRESHOLD
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


DEFAULT_CONFIG = RoutingConfig()


@dataclass
class ComplexityScore:
    """A prompt's structural complexity and its routing recommendation.

    ``to_dict`` is the stable JSON contract: the score, the recommendation, the
    threshold it was compared against, and the raw feature values behind it, so
    the recommendation is explainable rather than opaque.
    """

    score: float  # 0.0 – 1.0, rounded to 2dp
    recommendation: str  # "local" | "cloud"
    threshold: float
    features: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "schema_version": "1",
            "score": self.score,
            "recommendation": self.recommendation,
            "threshold": self.threshold,
            "features": dict(self.features),
        }


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


def score_complexity(text: str, *, config: RoutingConfig = DEFAULT_CONFIG) -> ComplexityScore:
    """Score ``text`` and recommend ``local`` or ``cloud`` against the threshold.

    The score normalizes the weighted, saturating feature contributions to
    ``0.0 – 1.0``. The rounded score is compared to the threshold, so the
    reported score and recommendation are always consistent.
    """
    features = extract_features(text)
    total_weight = sum(config.weights.values())
    if total_weight:
        accumulated = sum(
            config.weights.get(name, 0.0) * min(features[name] / SATURATION[name], 1.0)
            for name in FEATURE_ORDER
        )
        score = round(accumulated / total_weight, 2)
    else:
        score = 0.0
    recommendation = "cloud" if score >= config.threshold else "local"
    return ComplexityScore(
        score=score,
        recommendation=recommendation,
        threshold=config.threshold,
        features=features,
    )
