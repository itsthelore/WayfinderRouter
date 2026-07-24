use std::error::Error;

use axum::body::{Body, to_bytes};
use http::{Request, StatusCode};
use serde_json::{Value, json};
use tower::ServiceExt;
use wayfinder_gateway::{AppState, build_router};
use wayfinder_routing_core::{RoutingConfig, score_complexity};

#[tokio::test]
async fn embedded_core_and_gateway_return_identical_decisions() -> Result<(), Box<dyn Error>> {
    let routing = RoutingConfig::binary(0.5);
    let state = AppState::new(routing.clone(), Vec::new(), false, "2026.7.0");
    let prompts = [
        "hi",
        "Prove the theorem under exactly these constraints. Derive the invariant and explain why it prevents concurrency deadlock.",
    ];

    for prompt in prompts {
        let embedded = score_complexity(prompt, &routing)?;
        let request = Request::builder()
            .method("POST")
            .uri("/v1/chat/completions")
            .header("content-type", "application/json")
            .body(Body::from(serde_json::to_vec(&json!({
                "model": "auto",
                "messages": [{"role": "user", "content": prompt}]
            }))?))?;
        let response = build_router(state.clone()).oneshot(request).await?;
        assert_eq!(response.status(), StatusCode::OK, "{prompt:?}");
        let body = to_bytes(response.into_body(), 1024 * 1024).await?;
        let gateway: Value = serde_json::from_slice(&body)?;
        let decision = gateway
            .get("wayfinder")
            .ok_or("gateway response omitted decision")?;

        assert_eq!(decision["score"], json!(embedded.score), "{prompt:?} score");
        assert_eq!(
            decision["model"],
            json!(embedded.recommendation),
            "{prompt:?} recommendation"
        );
        assert_eq!(
            decision["features"],
            serde_json::to_value(embedded.features)?,
            "{prompt:?} features"
        );
    }

    Ok(())
}
