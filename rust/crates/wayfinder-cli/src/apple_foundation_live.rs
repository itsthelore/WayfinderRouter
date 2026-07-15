//! Explicitly gated, content-free live smoke test for the signed Apple XPC path.

use std::io::Write;
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
use std::time::Duration;

use serde_json::json;
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
use wayfinder_apple_foundation_xpc::{
    Availability, FoundationModelsClient, FoundationModelsXpcError, GenerateRequest, Message,
    MessageRole, StreamEventKind,
};

use crate::{EXIT_CONFIG, EXIT_OK, EXIT_USAGE};

const LIVE_GATE_ENV: &str = "WAYFINDER_RUN_APPLE_FOUNDATION_LIVE";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
const FIXED_PROMPT: &str = "Reply with one short sentence confirming the test is ready.";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
const CANCELLATION_PROMPT: &str =
    "Write a detailed numbered explanation with at least one hundred separate points.";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

pub(crate) fn run(arguments: &[String], stdout: &mut dyn Write, stderr: &mut dyn Write) -> i32 {
    run_with_gate(
        arguments,
        std::env::var(LIVE_GATE_ENV).as_deref() == Ok("1"),
        stdout,
        stderr,
    )
}

fn run_with_gate(
    arguments: &[String],
    enabled: bool,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> i32 {
    if arguments == ["-h"] || arguments == ["--help"] {
        write_line(
            stdout,
            "usage: wayfinder-router apple-foundation-live-smoke --json",
        );
        return EXIT_OK;
    }
    if arguments != ["--json"] {
        write_line(
            stderr,
            "wayfinder-router: apple-foundation-live-smoke requires --json",
        );
        return EXIT_USAGE;
    }
    if !enabled {
        write_line(
            stderr,
            "wayfinder-router: live Apple Foundation Models smoke test is disabled",
        );
        return EXIT_CONFIG;
    }
    match run_live() {
        Ok(report) => {
            write_line(stdout, &report.to_string());
            EXIT_OK
        }
        Err(error) => {
            write_line(
                stderr,
                &json!({
                    "schema_version": "1",
                    "provider": "apple-foundation-models",
                    "completed": false,
                    "stage": error.stage(),
                    "error": error.code(),
                })
                .to_string(),
            );
            EXIT_CONFIG
        }
    }
}

fn run_live() -> Result<serde_json::Value, LiveSmokeError> {
    #[cfg(not(all(target_os = "macos", target_arch = "aarch64")))]
    return Err(LiveSmokeError::UnsupportedPlatform);

    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        let client = FoundationModelsClient::default();
        let availability_id = request_id("availability");
        let availability = client
            .availability(&availability_id)
            .map_err(|error| LiveSmokeError::Xpc("availability", error))?;
        if availability != Availability::Available {
            return Err(LiveSmokeError::Availability(availability));
        }

        let buffered_request = request("buffered", FIXED_PROMPT);
        let buffered = client
            .generate(&buffered_request)
            .map_err(|error| LiveSmokeError::Xpc("buffered", error))?;
        if buffered.content.is_empty() {
            return Err(LiveSmokeError::EmptyResponse);
        }

        let stream_request = request("stream", FIXED_PROMPT);
        let mut stream_events = 0_usize;
        let mut stream_bytes = 0_usize;
        client
            .stream(&stream_request, |event| {
                stream_events = stream_events.saturating_add(1);
                if event.kind == StreamEventKind::Chunk {
                    stream_bytes =
                        stream_bytes.saturating_add(event.content.as_deref().map_or(0, str::len));
                }
                Ok(())
            })
            .map_err(|error| LiveSmokeError::Xpc("streaming", error))?;
        if stream_events < 2 || stream_bytes == 0 {
            return Err(LiveSmokeError::EmptyResponse);
        }

        let cancellation_observed = run_cancellation()?;
        Ok(json!({
            "schema_version": "1",
            "provider": "apple-foundation-models",
            "completed": true,
            "availability": "available",
            "buffered": {
                "completed": true,
                "response_bytes": buffered.content.len(),
            },
            "streaming": {
                "completed": true,
                "events": stream_events,
                "response_bytes": stream_bytes,
            },
            "cancellation": {
                "requested": true,
                "observed": cancellation_observed,
            },
        }))
    }
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn run_cancellation() -> Result<bool, LiveSmokeError> {
    let request = request("cancel", CANCELLATION_PROMPT);
    let request_id = request.request_id.clone();
    let control = FoundationModelsClient::default();
    control
        .cancel(&request_id)
        .map_err(|error| LiveSmokeError::Xpc("cancellation", error))?;
    match FoundationModelsClient::default().stream(&request, |_| Ok(())) {
        Err(FoundationModelsXpcError::Cancelled) => Ok(true),
        Ok(()) => Err(LiveSmokeError::CancellationCompleted),
        Err(error) => Err(LiveSmokeError::Xpc("cancellation", error)),
    }
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn request(label: &str, prompt: &str) -> GenerateRequest {
    GenerateRequest {
        request_id: request_id(label),
        instructions: Some("Return plain text only.".to_owned()),
        messages: vec![Message {
            role: MessageRole::User,
            content: prompt.to_owned(),
        }],
        timeout: REQUEST_TIMEOUT,
    }
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn request_id(label: &str) -> String {
    format!("live-{label}-{}", uuid::Uuid::new_v4())
}

#[derive(Clone, Copy, Debug)]
enum LiveSmokeError {
    #[cfg(not(all(target_os = "macos", target_arch = "aarch64")))]
    UnsupportedPlatform,
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    Availability(Availability),
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    Xpc(&'static str, FoundationModelsXpcError),
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    EmptyResponse,
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    CancellationCompleted,
}

impl LiveSmokeError {
    const fn stage(self) -> &'static str {
        match self {
            #[cfg(not(all(target_os = "macos", target_arch = "aarch64")))]
            Self::UnsupportedPlatform => "platform",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(_) => "availability",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(stage, _) => stage,
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::EmptyResponse => "response",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::CancellationCompleted => "cancellation",
        }
    }

    const fn code(self) -> &'static str {
        match self {
            #[cfg(not(all(target_os = "macos", target_arch = "aarch64")))]
            Self::UnsupportedPlatform => "unsupported-platform",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::Available) => "unexpected-availability",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::DeviceNotEligible) => "device-not-eligible",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::AppleIntelligenceNotEnabled) => {
                "apple-intelligence-not-enabled"
            }
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::ModelNotReady) => "model-not-ready",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::Unsupported) => "unsupported",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Availability(Availability::Unavailable) => "unavailable",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::UnsupportedPlatform) => "xpc-unsupported",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::InvalidRequest) => "xpc-invalid-request",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::RequestTooLarge) => "xpc-request-too-large",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::MalformedResponse) => "xpc-malformed-response",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::ResponseTooLarge) => "xpc-response-too-large",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::UnsupportedVersion) => "xpc-version-skew",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::Unavailable) => "xpc-unavailable",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::TimedOut) => "xpc-timed-out",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::Cancelled) => "xpc-cancelled",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::Denied) => "xpc-denied",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::GenerationFailed) => "generation-failed",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::Xpc(_, FoundationModelsXpcError::InvalidStream) => "invalid-stream",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::EmptyResponse => "empty-response",
            #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
            Self::CancellationCompleted => "cancellation-completed-before-observed",
        }
    }
}

fn write_line(writer: &mut dyn Write, value: &str) {
    let _ = writeln!(writer, "{value}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_smoke_is_disabled_without_exact_gate() {
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        let exit = run_with_gate(&["--json".to_owned()], false, &mut stdout, &mut stderr);
        assert_eq!(exit, EXIT_CONFIG);
        assert!(stdout.is_empty());
        assert_eq!(
            String::from_utf8_lossy(&stderr),
            "wayfinder-router: live Apple Foundation Models smoke test is disabled\n"
        );
    }

    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    #[test]
    fn errors_are_stable_and_content_free() {
        assert_eq!(LiveSmokeError::EmptyResponse.code(), "empty-response");
        assert_eq!(
            LiveSmokeError::Xpc("availability", FoundationModelsXpcError::Denied).code(),
            "xpc-denied"
        );
        assert!(!format!("{:?}", LiveSmokeError::EmptyResponse).contains("prompt-content"));
    }
}
