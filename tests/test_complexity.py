"""Tests for the deterministic complexity scorer."""

from __future__ import annotations

from wayfinder.complexity import (
    DEFAULT_THRESHOLD,
    FEATURE_ORDER,
    extract_features,
    strip_frontmatter,
)

from wayfinder import ComplexityScore, RoutingConfig, score_complexity

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
    assert result.threshold == DEFAULT_THRESHOLD


def test_features_cover_the_declared_order():
    assert set(score_complexity(COMPLEX).features) == set(FEATURE_ORDER)


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


def test_recommendation_flips_at_the_threshold():
    score = score_complexity(COMPLEX).score
    assert score > 0.0
    at = score_complexity(COMPLEX, config=RoutingConfig(threshold=score))
    assert at.recommendation == "cloud"
    above = score_complexity(COMPLEX, config=RoutingConfig(threshold=min(1.0, score + 0.01)))
    assert above.recommendation == "local"


def test_score_and_recommendation_are_consistent():
    for text in (TRIVIAL, COMPLEX):
        result = score_complexity(text)
        expected = "cloud" if result.score >= result.threshold else "local"
        assert result.recommendation == expected
