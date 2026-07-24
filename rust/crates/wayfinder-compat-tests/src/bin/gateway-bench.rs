//! Reproducible in-process dry-run gateway benchmark.
//!
//! This measures Axum routing, bounded body collection, JSON parsing, policy,
//! scoring, metrics/recent updates, and response serialization. It deliberately
//! excludes listener/syscall overhead and provider latency.

use std::error::Error;
use std::time::Instant;

use axum::body::{Body, to_bytes};
use http::{Request, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use tower::ServiceExt;
use wayfinder_gateway::{AppState, build_router};
use wayfinder_routing_core::RoutingConfig;

const MAX_OPERATIONS: usize = 2_000_000;
const RESPONSE_LIMIT: usize = 1024 * 1024;

#[derive(Debug, Deserialize)]
struct Fixture {
    name: String,
    text: String,
    score: f64,
    recommendation: String,
    features: FixtureFeatures,
}

#[derive(Debug, Deserialize)]
struct FixtureFeatures {
    word_count: u64,
}

struct Prepared {
    name: String,
    body: Vec<u8>,
    score: f64,
    recommendation: String,
    word_count: u64,
}

#[derive(Clone, Copy, Debug)]
struct Options {
    iterations: usize,
    warmup_iterations: usize,
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut options = Options {
        iterations: 2_000,
        warmup_iterations: 100,
    };
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        let target = match argument.as_str() {
            "--iterations" => &mut options.iterations,
            "--warmup-iterations" => &mut options.warmup_iterations,
            _ => return Err(format!("unknown argument: {argument}").into()),
        };
        let value = arguments
            .next()
            .ok_or_else(|| format!("{argument} requires a non-negative integer"))?;
        *target = value.parse()?;
    }
    if options.iterations == 0 {
        return Err("--iterations must be positive".into());
    }
    Ok(options)
}

fn prepare(fixtures: Vec<Fixture>) -> Result<Vec<Prepared>, Box<dyn Error>> {
    fixtures
        .into_iter()
        .map(|fixture| {
            let body = serde_json::to_vec(&json!({
                "model": "auto",
                "messages": [{"role": "user", "content": fixture.text}],
            }))?;
            Ok(Prepared {
                name: fixture.name,
                body,
                score: fixture.score,
                recommendation: fixture.recommendation,
                word_count: fixture.features.word_count,
            })
        })
        .collect()
}

async fn exercise(
    corpus: &[Prepared],
    iterations: usize,
    record_latency: bool,
) -> Result<(u64, Vec<u64>), Box<dyn Error>> {
    let state =
        AppState::new(RoutingConfig::default(), Vec::new(), false, "benchmark").with_dry_run(true);
    let router = build_router(state);
    let operations = iterations
        .checked_mul(corpus.len())
        .ok_or("benchmark operation count overflow")?;
    if operations > MAX_OPERATIONS {
        return Err(format!("benchmark is capped at {MAX_OPERATIONS} operations").into());
    }
    let mut latencies = if record_latency {
        Vec::with_capacity(operations)
    } else {
        Vec::new()
    };
    let mut checksum = 0_u64;
    for _ in 0..iterations {
        for fixture in corpus {
            let started = Instant::now();
            let request = Request::builder()
                .method("POST")
                .uri("/v1/chat/completions")
                .header("content-type", "application/json")
                .body(Body::from(fixture.body.clone()))?;
            let response = router.clone().oneshot(request).await?;
            if response.status() != StatusCode::OK {
                return Err(format!("{} returned {}", fixture.name, response.status()).into());
            }
            let body = to_bytes(response.into_body(), RESPONSE_LIMIT).await?;
            let payload: Value = serde_json::from_slice(&body)?;
            let wayfinder = payload
                .get("wayfinder")
                .and_then(Value::as_object)
                .ok_or_else(|| format!("{} omitted wayfinder response", fixture.name))?;
            let model = wayfinder.get("model").and_then(Value::as_str);
            let score = wayfinder.get("score").and_then(Value::as_f64);
            let word_count = wayfinder
                .get("features")
                .and_then(Value::as_object)
                .and_then(|features| features.get("word_count"))
                .and_then(Value::as_u64);
            if model != Some(fixture.recommendation.as_str())
                || score != Some(fixture.score)
                || word_count != Some(fixture.word_count)
            {
                return Err(format!(
                    "{} diverged: model={model:?} score={score:?} word_count={word_count:?}",
                    fixture.name
                )
                .into());
            }
            checksum = checksum
                .wrapping_mul(1_099_511_628_211)
                .wrapping_add(fixture.score.to_bits())
                .wrapping_add(fixture.word_count)
                .wrapping_add(fixture.recommendation.bytes().map(u64::from).sum::<u64>());
            if record_latency {
                latencies.push(u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX));
            }
        }
    }
    Ok((checksum, latencies))
}

fn percentile(sorted: &[u64], percentile: usize) -> u64 {
    let index = sorted.len().saturating_sub(1).saturating_mul(percentile) / 100;
    sorted[index]
}

fn micros(nanoseconds: u64) -> f64 {
    nanoseconds as f64 / 1_000.0
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    let fixtures: Vec<Fixture> =
        serde_json::from_str(include_str!("../../fixtures/migration-golden.json"))?;
    let corpus = prepare(fixtures)?;
    let _ = exercise(&corpus, options.warmup_iterations, false).await?;

    let started = Instant::now();
    let (checksum, mut latencies) = exercise(&corpus, options.iterations, true).await?;
    let elapsed = started.elapsed().as_secs_f64();
    latencies.sort_unstable();
    let operations = options.iterations.saturating_mul(corpus.len());
    println!(
        "{}",
        serde_json::to_string(&json!({
            "implementation": "rust",
            "benchmark": "gateway_dry_run_in_process",
            "corpus_cases": corpus.len(),
            "iterations": options.iterations,
            "warmup_iterations": options.warmup_iterations,
            "operations": operations,
            "elapsed_seconds": elapsed,
            "operations_per_second": operations as f64 / elapsed,
            "latency_microseconds": {
                "p50": micros(percentile(&latencies, 50)),
                "p95": micros(percentile(&latencies, 95)),
                "p99": micros(percentile(&latencies, 99)),
                "max": micros(*latencies.last().ok_or("empty latency sample")?),
            },
            "validated_checksum": checksum,
        }))?
    );
    Ok(())
}
