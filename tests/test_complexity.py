"""Tests for the deterministic scorer, tiers, and the classifier runtime."""

from __future__ import annotations

from wayfinder_router.complexity import (
    DEFAULT_THRESHOLD,
    FEATURE_ORDER,
    extract_features,
    recommend_tier,
    scalar_score,
    strip_frontmatter,
)

from wayfinder_router import (
    ClassifierModel,
    ComplexityScore,
    RoutingConfig,
    Tier,
    score_complexity,
)

TRIVIAL = "Say hello."

COMPLEX = """# Build the reporting pipeline

## Context

We need a deterministic batch pipeline that ingests events and emits a daily
report, with retries and backfill, across three environments.

## Steps

- Parse the input manifest
- Validate every row against the schema
- Deduplicate by event id
- Aggregate per day
- Render the report
- Upload the artifact
- Notify the channel

## Reference

See [the spec](https://example.com/spec) and [the schema](https://example.com/schema).

## Example

```python
def pipeline(rows):
    return aggregate(dedupe(validate(rows)))
```

| Field | Type |
| --- | --- |
| id | string |
| ts | int |
"""

BODY = "# Task\n\nDo the thing.\n\n## Steps\n\n- one\n- two\n"
WITH_FRONTMATTER = "---\nschema_version: 1\nid: WF-TEST-01\ntype: prompt\n---\n" + BODY


# --- scorer -----------------------------------------------------------------


def test_score_is_deterministic_and_bounded():
    a = score_complexity(COMPLEX)
    b = score_complexity(COMPLEX)
    assert a.to_dict() == b.to_dict()
    assert 0.0 <= a.score <= 1.0


def test_complex_prompt_scores_higher_than_trivial():
    assert score_complexity(COMPLEX).score > score_complexity(TRIVIAL).score


def test_trivial_prompt_routes_local_by_default():
    result = score_complexity(TRIVIAL)
    assert isinstance(result, ComplexityScore)
    assert result.recommendation == "local"
    assert result.mode == "tiered"


def test_default_to_dict_is_versioned_contract():
    payload = score_complexity(COMPLEX).to_dict()
    assert payload["schema_version"] == "3"
    assert payload["mode"] == "tiered"
    assert set(payload["features"]) == set(FEATURE_ORDER)
    assert [t["model"] for t in payload["tiers"]] == ["local", "cloud"]


def test_frontmatter_is_stripped_so_artifact_equals_its_body():
    assert extract_features(WITH_FRONTMATTER) == extract_features(BODY)
    assert strip_frontmatter(WITH_FRONTMATTER) == BODY


def test_unterminated_frontmatter_is_left_in_place():
    text = "---\nstill going\nno closer here\n"
    assert strip_frontmatter(text) == text


def test_code_fence_contents_are_not_counted_as_structure():
    features = extract_features("```\n## Not a heading\n- not a list\n| a | b |\n```\n")
    assert features["heading_count"] == 0
    assert features["list_item_count"] == 0
    assert features["table_row_count"] == 0
    assert features["code_block_count"] == 1


# --- lexical difficulty signals (WF-ADR-0016) -------------------------------


def test_reasoning_terms_are_counted_case_insensitively():
    # "prove" and "irrational" are both in the curated reasoning lexicon.
    assert extract_features("Prove that the square root of 2 is irrational.")[
        "reasoning_term_count"
    ] == 2
    assert extract_features("PROVE THE THEOREM")["reasoning_term_count"] == 2


def test_reasoning_terms_match_whole_words_not_substrings():
    # "approve" / "proverbial" must not trip the "prove" term.
    assert extract_features("approve the proverbial change")["reasoning_term_count"] == 0


def test_math_symbols_count_glyphs_and_latex_tokens():
    # LaTeX-ish backslash tokens: \int, \le, \frac.
    assert extract_features(r"Show that $\int x\,dx \le 5$ and \frac{1}{2}.")[
        "math_symbol_count"
    ] == 3
    # Unicode math/logic glyphs: ∑, ∫, ≤.
    assert extract_features("Bound it by ∑ and ∫ where x ≤ y.")["math_symbol_count"] == 3


def test_constraint_and_question_markers_are_counted():
    f = extract_features("It must run without locks, only once. Done? Sure?")
    assert f["constraint_term_count"] == 3  # must, without, only
    assert f["question_count"] == 2


def test_lexical_signals_lift_a_short_hard_prompt_over_a_short_easy_one():
    # The documented benchmark hole: a short, structureless but hard prompt used to
    # score ~0 and route local. The lexical signals separate it from short-easy.
    easy = score_complexity("What is the capital of France?")
    hard = score_complexity("Prove that the square root of 2 is irrational.")
    assert easy.score == 0.0
    assert hard.score > easy.score
    # At a low cost-aware cut the hard prompt routes up; the easy one stays local.
    assert score_complexity(
        "Prove that the square root of 2 is irrational.",
        config=RoutingConfig.binary(threshold=0.1),
    ).recommendation == "cloud"
    assert score_complexity(
        "What is the capital of France?", config=RoutingConfig.binary(threshold=0.1)
    ).recommendation == "local"


def test_question_marks_alone_do_not_raise_the_score_by_default():
    # question_count ships at weight 0.0 — an interrogative is not, by itself, hard.
    assert score_complexity("Is it? Really? You sure? Truly?").score == 0.0


# --- tiers ------------------------------------------------------------------


def test_binary_recommendation_flips_at_the_threshold():
    score = score_complexity(COMPLEX).score
    assert score > 0.0
    at = score_complexity(COMPLEX, config=RoutingConfig.binary(threshold=score))
    assert at.recommendation == "cloud"
    above = score_complexity(COMPLEX, config=RoutingConfig.binary(threshold=min(1.0, score + 0.01)))
    assert above.recommendation == "local"


def test_recommend_tier_picks_the_highest_band_reached():
    tiers = (Tier(0.0, "small"), Tier(0.3, "medium"), Tier(0.6, "large"))
    assert recommend_tier(0.0, tiers) == "small"
    assert recommend_tier(0.29, tiers) == "small"
    assert recommend_tier(0.3, tiers) == "medium"
    assert recommend_tier(0.59, tiers) == "medium"
    assert recommend_tier(0.6, tiers) == "large"
    assert recommend_tier(1.0, tiers) == "large"


def test_three_tier_routing_via_score_complexity():
    tiers = (Tier(0.0, "small"), Tier(0.3, "medium"), Tier(0.6, "large"))
    result = score_complexity(COMPLEX, config=RoutingConfig(tiers=tiers))
    assert result.mode == "tiered"
    assert result.recommendation in {"small", "medium", "large"}
    assert result.recommendation == recommend_tier(result.score, tiers)


# --- classifier -------------------------------------------------------------


def test_classifier_argmax_is_deterministic_and_explainable():
    # "big" wins only when word_count saturates; otherwise the intercept favors "small".
    clf = ClassifierModel(
        models=("small", "big"),
        weights={name: (0.0, 0.0) for name in FEATURE_ORDER} | {"word_count": (0.0, 5.0)},
        intercepts=(1.0, 0.0),
    )
    cfg = RoutingConfig(classifier=clf)
    assert score_complexity(TRIVIAL, config=cfg).recommendation == "small"
    big = score_complexity(COMPLEX, config=cfg)
    assert big.recommendation == "big"
    assert big.mode == "classifier"
    assert big.to_dict()["models"] == ["small", "big"]


def test_scalar_score_matches_default_threshold_default():
    # The scalar score is still reported in classifier mode (informational).
    features = extract_features(TRIVIAL)
    assert scalar_score(features, RoutingConfig().weights) == 0.0
    assert RoutingConfig().tiers[1].min_score == DEFAULT_THRESHOLD
