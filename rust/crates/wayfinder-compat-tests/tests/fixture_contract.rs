use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;

use serde::Deserialize;

const MIGRATION_GOLDEN: &str = include_str!("../fixtures/migration-golden.json");
const SHARED_GOLDEN: &str = include_str!("../../../../clients/shared/test/golden.json");
const ROUTING_BOUNDARIES: &str = include_str!("../fixtures/routing-boundaries.json");

const FEATURE_NAMES: [&str; 11] = [
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
];

#[derive(Debug, Deserialize)]
struct GoldenCase {
    name: String,
    text: String,
    score: f64,
    recommendation: String,
    features: BTreeMap<String, usize>,
}

#[derive(Debug, Deserialize)]
struct BoundaryCase {
    name: String,
    text: String,
    config: BoundaryConfig,
    score: f64,
    recommendation: String,
    features: BTreeMap<String, usize>,
}

#[derive(Debug, Deserialize)]
struct BoundaryConfig {
    mode: String,
    threshold: Option<f64>,
    tiers: Option<Vec<TierFixture>>,
    models: Option<Vec<String>>,
    intercepts: Option<Vec<f64>>,
    weights: Option<BTreeMap<String, Vec<f64>>>,
}

#[derive(Debug, Deserialize)]
struct TierFixture {
    min_score: f64,
    model: String,
}

#[test]
fn copied_migration_golden_is_the_shared_corpus_without_normalization() -> Result<(), Box<dyn Error>>
{
    let copied: serde_json::Value = serde_json::from_str(MIGRATION_GOLDEN)?;
    let shared: serde_json::Value = serde_json::from_str(SHARED_GOLDEN)?;

    assert_eq!(copied, shared);
    Ok(())
}

#[test]
fn migration_golden_has_the_expected_21_case_contract() -> Result<(), Box<dyn Error>> {
    let cases: Vec<GoldenCase> = serde_json::from_str(MIGRATION_GOLDEN)?;
    let expected_names = [
        "empty",
        "blank_whitespace",
        "short_easy",
        "question",
        "headings_lists",
        "code_fence",
        "unterminated_fence",
        "tilde_fence",
        "table",
        "links",
        "math_symbols",
        "reasoning_terms",
        "constraints",
        "frontmatter",
        "crlf_endings",
        "long_400plus",
        "emoji_cjk",
        "rounding_a",
        "rounding_b",
        "rtl_arabic",
        "nbsp_whitespace",
    ];
    let names: Vec<&str> = cases.iter().map(|case| case.name.as_str()).collect();

    assert_eq!(names, expected_names);
    for case in &cases {
        assert_feature_contract(&case.features);
        assert!((0.0..=1.0).contains(&case.score));
        assert!(!case.recommendation.is_empty());
        let _ = case.text.len();
    }
    Ok(())
}

#[test]
fn generated_boundary_vectors_are_dimensionally_valid() -> Result<(), Box<dyn Error>> {
    let cases: Vec<BoundaryCase> = serde_json::from_str(ROUTING_BOUNDARIES)?;
    let unique_names: BTreeSet<&str> = cases.iter().map(|case| case.name.as_str()).collect();

    assert_eq!(cases.len(), 8);
    assert_eq!(unique_names.len(), cases.len());
    for case in &cases {
        assert_feature_contract(&case.features);
        assert!((0.0..=1.0).contains(&case.score));
        assert!(!case.recommendation.is_empty());
        let _ = case.text.len();
        match case.config.mode.as_str() {
            "binary" => {
                let threshold = case.config.threshold.ok_or("binary threshold missing")?;
                assert!((0.0..=1.0).contains(&threshold));
            }
            "tiers" => {
                let tiers = case.config.tiers.as_deref().ok_or("tiers missing")?;
                assert!(!tiers.is_empty());
                assert_eq!(tiers.first().map(|tier| tier.min_score), Some(0.0));
                assert!(tiers.iter().all(|tier| !tier.model.is_empty()));
            }
            "classifier" => {
                let models = case.config.models.as_deref().ok_or("models missing")?;
                let intercepts = case
                    .config
                    .intercepts
                    .as_deref()
                    .ok_or("intercepts missing")?;
                let weights = case.config.weights.as_ref().ok_or("weights missing")?;
                assert_eq!(models.len(), intercepts.len());
                assert!(models.len() >= 2);
                assert!(weights.values().all(|values| values.len() == models.len()));
            }
            mode => return Err(format!("unknown boundary mode: {mode}").into()),
        }
    }
    Ok(())
}

fn assert_feature_contract(features: &BTreeMap<String, usize>) {
    let actual: BTreeSet<&str> = features.keys().map(String::as_str).collect();
    let expected: BTreeSet<&str> = FEATURE_NAMES.into_iter().collect();
    assert_eq!(actual, expected);
}
