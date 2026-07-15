//! Buffered provider-delivery seam and OpenAI-compatible implementation.

use std::fmt;
use std::future::Future;
use std::net::IpAddr;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;

use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use http::StatusCode;
use serde_json::Value;
use thiserror::Error;
use wayfinder_apple_foundation_xpc::{
    Availability, FoundationModelsClient, FoundationModelsTransport, FoundationModelsXpcError,
    GenerateRequest, GenerateResponse, MAX_QUEUED_CHUNKS, Message, MessageRole, StreamEvent,
    StreamEventKind,
};
use wayfinder_config::gateway::ProviderKind;
use wayfinder_providers::openai_compat::{
    OpenAiEndpoint, OpenAiProviderClient, ProviderError, SecretValue,
};

use crate::ConfiguredModel;

/// Boxed cancellable delivery future.
pub type DeliveryFuture<'a> =
    Pin<Box<dyn Future<Output = Result<BufferedDeliveryResponse, DeliveryError>> + Send + 'a>>;

/// Cancellable provider bytes mapped to gateway delivery errors.
pub type DeliveryByteStream =
    Pin<Box<dyn Stream<Item = Result<Bytes, DeliveryError>> + Send + 'static>>;

/// Boxed future that establishes one upstream streaming response.
pub type StreamingDeliveryFuture<'a> =
    Pin<Box<dyn Future<Output = Result<StreamingDeliveryResponse, DeliveryError>> + Send + 'a>>;

/// One bounded buffered provider result. Debug never prints the body.
pub struct BufferedDeliveryResponse {
    status: StatusCode,
    content_type: String,
    body: Bytes,
}

impl BufferedDeliveryResponse {
    /// Construct a response from a test transport or provider adapter.
    #[must_use]
    pub fn new(status: StatusCode, content_type: impl Into<String>, body: Bytes) -> Self {
        Self {
            status,
            content_type: content_type.into(),
            body,
        }
    }

    /// Upstream status, including ordinary provider failures.
    #[must_use]
    pub const fn status(&self) -> StatusCode {
        self.status
    }

    /// Upstream media type.
    #[must_use]
    pub fn content_type(&self) -> &str {
        &self.content_type
    }

    /// Consume the result and return its bounded raw body.
    #[must_use]
    pub fn into_body(self) -> Bytes {
        self.body
    }
}

impl fmt::Debug for BufferedDeliveryResponse {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BufferedDeliveryResponse")
            .field("status", &self.status)
            .field("content_type", &self.content_type)
            .field("body_bytes", &self.body.len())
            .finish()
    }
}

/// One incremental provider response. Debug never consumes or reveals chunks.
pub struct StreamingDeliveryResponse {
    status: StatusCode,
    content_type: String,
    stream: DeliveryByteStream,
}

impl StreamingDeliveryResponse {
    /// Construct a response from a test transport or provider adapter.
    #[must_use]
    pub fn new(
        status: StatusCode,
        content_type: impl Into<String>,
        stream: DeliveryByteStream,
    ) -> Self {
        Self {
            status,
            content_type: content_type.into(),
            stream,
        }
    }

    /// Upstream status, retained even though the compatibility relay starts as HTTP 200.
    #[must_use]
    pub const fn status(&self) -> StatusCode {
        self.status
    }

    /// Upstream media type.
    #[must_use]
    pub fn content_type(&self) -> &str {
        &self.content_type
    }

    /// Consume and transfer the cancellable byte stream.
    #[must_use]
    pub fn into_stream(self) -> DeliveryByteStream {
        self.stream
    }
}

impl fmt::Debug for StreamingDeliveryResponse {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("StreamingDeliveryResponse")
            .field("status", &self.status)
            .field("content_type", &self.content_type)
            .field("stream", &"<cancellable bytes>")
            .finish()
    }
}

/// Sanitized credential-resolution failure.
#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum CredentialError {
    /// A configured reference had no current value.
    #[error("configured provider credential is unavailable")]
    Unavailable,
}

/// Provider delivery failure before an ordinary HTTP response exists.
#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum DeliveryError {
    /// Configured endpoint violated the explicit URL/origin policy.
    #[error("configured provider endpoint is invalid or unsafe")]
    InvalidEndpoint,
    /// A referenced provider credential could not be resolved.
    #[error("configured provider credential is unavailable")]
    CredentialUnavailable,
    /// Provider client or transport failed with a sanitized category.
    #[error(transparent)]
    Provider(ProviderError),
    /// Native Apple provider failed with a sanitized, planning-safe category.
    #[error(transparent)]
    Apple(AppleDeliveryError),
}

/// Stable buffered Apple delivery failures. No variant retains request content.
#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum AppleDeliveryError {
    /// The configured native provider is not supported on this platform.
    #[error("Apple Foundation Models are unsupported on this platform")]
    Unsupported,
    /// Apple Intelligence or its model is temporarily not ready.
    #[error("Apple Foundation Models are not ready")]
    NotReady,
    /// The native provider or authenticated service is unavailable.
    #[error("Apple Foundation Models are unavailable")]
    Unavailable,
    /// The public request cannot be represented by the bounded native protocol.
    #[error("request is unsupported by Apple Foundation Models")]
    InvalidRequest,
    /// The native response violated the bounded protocol contract.
    #[error("Apple Foundation Models returned an invalid response")]
    InvalidResponse,
}

/// Resolve a configured secret reference for one request.
///
/// Implementations must return an owned redacted value and must not cache it in
/// serializable or loggable state. The blanket closure implementation lets the
/// CLI supply environment or authenticated-XPC boundaries without coupling the
/// HTTP handler to either mechanism.
pub trait CredentialSource: Send + Sync {
    /// Resolve one environment/broker reference; `None` denotes a keyless model.
    fn resolve(&self, reference: Option<&str>) -> Result<Option<SecretValue>, CredentialError>;
}

impl<F> CredentialSource for F
where
    F: Fn(Option<&str>) -> Result<Option<SecretValue>, CredentialError> + Send + Sync,
{
    fn resolve(&self, reference: Option<&str>) -> Result<Option<SecretValue>, CredentialError> {
        self(reference)
    }
}

/// Async delivery abstraction used by the gateway handler and fake providers.
pub trait BufferedDelivery: Send + Sync {
    /// Deliver one JSON request to exactly the selected configured model.
    fn send<'a>(&'a self, model: &'a ConfiguredModel, body: Value) -> DeliveryFuture<'a>;
}

impl<D> BufferedDelivery for Arc<D>
where
    D: BufferedDelivery + ?Sized,
{
    fn send<'a>(&'a self, model: &'a ConfiguredModel, body: Value) -> DeliveryFuture<'a> {
        (**self).send(model, body)
    }
}

/// Async delivery abstraction for one non-retried streaming attempt.
pub trait StreamingDelivery: Send + Sync {
    /// Establish a stream to exactly the selected configured model.
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        body: Value,
    ) -> StreamingDeliveryFuture<'a>;
}

impl<D> StreamingDelivery for Arc<D>
where
    D: StreamingDelivery + ?Sized,
{
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        body: Value,
    ) -> StreamingDeliveryFuture<'a> {
        (**self).send_stream(model, body)
    }
}

/// Narrow synchronous native seam implemented by the authenticated XPC client and test fakes.
pub trait AppleFoundationModelsService: Send + Sync {
    /// Query readiness before generation.
    fn availability(&self, request_id: &str) -> Result<Availability, FoundationModelsXpcError>;
    /// Generate one bounded buffered response.
    fn generate(
        &self,
        request: &GenerateRequest,
    ) -> Result<GenerateResponse, FoundationModelsXpcError>;
    /// Produce one ordered bounded native stream.
    fn stream(
        &self,
        request: &GenerateRequest,
        on_event: &mut dyn FnMut(StreamEvent) -> Result<(), FoundationModelsXpcError>,
    ) -> Result<(), FoundationModelsXpcError>;
    /// Idempotently cancel one native request.
    fn cancel(&self, request_id: &str) -> Result<(), FoundationModelsXpcError>;
}

impl<T> AppleFoundationModelsService for FoundationModelsClient<T>
where
    T: FoundationModelsTransport + Send + Sync,
{
    fn availability(&self, request_id: &str) -> Result<Availability, FoundationModelsXpcError> {
        self.availability(request_id)
    }

    fn generate(
        &self,
        request: &GenerateRequest,
    ) -> Result<GenerateResponse, FoundationModelsXpcError> {
        self.generate(request)
    }

    fn stream(
        &self,
        request: &GenerateRequest,
        on_event: &mut dyn FnMut(StreamEvent) -> Result<(), FoundationModelsXpcError>,
    ) -> Result<(), FoundationModelsXpcError> {
        self.stream(request, on_event)
    }

    fn cancel(&self, request_id: &str) -> Result<(), FoundationModelsXpcError> {
        self.cancel(request_id)
    }
}

/// Buffered Apple Foundation Models delivery over a bounded native service.
pub struct AppleFoundationModelDelivery<S, I> {
    service: Arc<S>,
    request_timeout: Duration,
    request_ids: I,
}

impl<S, I> AppleFoundationModelDelivery<S, I> {
    /// Bind a native service, finite timeout, and opaque request-ID source.
    #[must_use]
    pub const fn new(service: Arc<S>, request_timeout: Duration, request_ids: I) -> Self {
        Self {
            service,
            request_timeout,
            request_ids,
        }
    }
}

impl<S, I> fmt::Debug for AppleFoundationModelDelivery<S, I> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AppleFoundationModelDelivery")
            .field("service", &"<bounded native service>")
            .field("request_timeout", &self.request_timeout)
            .field("request_ids", &"<opaque source>")
            .finish()
    }
}

impl<S, I> BufferedDelivery for AppleFoundationModelDelivery<S, I>
where
    S: AppleFoundationModelsService + 'static,
    I: Fn() -> String + Send + Sync,
{
    fn send<'a>(&'a self, model: &'a ConfiguredModel, body: Value) -> DeliveryFuture<'a> {
        let service = self.service.clone();
        let timeout = self.request_timeout;
        let request_id = (self.request_ids)();
        let provider_model = model.provider_model().to_owned();
        Box::pin(async move {
            if model.provider() != ProviderKind::AppleFoundationModels
                || provider_model != "system-default"
            {
                return Err(DeliveryError::Apple(AppleDeliveryError::InvalidRequest));
            }
            let request = apple_generate_request(&body, request_id.clone(), timeout, false)?;
            let generated = tokio::task::spawn_blocking(move || {
                let availability = service.availability(&request_id).map_err(map_xpc_error)?;
                map_availability(availability)?;
                service.generate(&request).map_err(map_xpc_error)
            })
            .await
            .map_err(|_| DeliveryError::Apple(AppleDeliveryError::Unavailable))??;
            let body = serde_json::to_vec(&serde_json::json!({
                "id": generated.request_id,
                "object": "chat.completion",
                "created": 0,
                "model": provider_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": generated.content},
                    "finish_reason": "stop"
                }]
            }))
            .map_err(|_| DeliveryError::Apple(AppleDeliveryError::InvalidResponse))?;
            Ok(BufferedDeliveryResponse::new(
                StatusCode::OK,
                "application/json",
                Bytes::from(body),
            ))
        })
    }
}

struct AppleDeliveryStream<S: AppleFoundationModelsService + 'static> {
    receiver: tokio::sync::mpsc::Receiver<(Result<Bytes, DeliveryError>, bool)>,
    service: Arc<S>,
    request_id: String,
    finished: bool,
}

impl<S> Stream for AppleDeliveryStream<S>
where
    S: AppleFoundationModelsService + 'static,
{
    type Item = Result<Bytes, DeliveryError>;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match self.receiver.poll_recv(context) {
            Poll::Ready(Some((item, terminal))) => {
                self.finished = terminal;
                Poll::Ready(Some(item))
            }
            Poll::Ready(None) => {
                self.finished = true;
                Poll::Ready(None)
            }
            Poll::Pending => Poll::Pending,
        }
    }
}

impl<S> Drop for AppleDeliveryStream<S>
where
    S: AppleFoundationModelsService + 'static,
{
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        let service = self.service.clone();
        let request_id = self.request_id.clone();
        if let Ok(runtime) = tokio::runtime::Handle::try_current() {
            runtime.spawn_blocking(move || {
                let _ = service.cancel(&request_id);
            });
        }
    }
}

impl<S, I> StreamingDelivery for AppleFoundationModelDelivery<S, I>
where
    S: AppleFoundationModelsService + 'static,
    I: Fn() -> String + Send + Sync,
{
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        body: Value,
    ) -> StreamingDeliveryFuture<'a> {
        let service = self.service.clone();
        let timeout = self.request_timeout;
        let request_id = (self.request_ids)();
        let provider_model = model.provider_model().to_owned();
        Box::pin(async move {
            if model.provider() != ProviderKind::AppleFoundationModels
                || provider_model != "system-default"
            {
                return Err(DeliveryError::Apple(AppleDeliveryError::InvalidRequest));
            }
            let request = apple_generate_request(&body, request_id.clone(), timeout, true)?;
            let availability_service = service.clone();
            let availability_request_id = request_id.clone();
            tokio::task::spawn_blocking(move || {
                let availability = availability_service
                    .availability(&availability_request_id)
                    .map_err(map_xpc_error)?;
                map_availability(availability)
            })
            .await
            .map_err(|_| DeliveryError::Apple(AppleDeliveryError::Unavailable))??;

            let (sender, receiver) = tokio::sync::mpsc::channel(MAX_QUEUED_CHUNKS);
            let worker_service = service.clone();
            let stream_model = provider_model.clone();
            tokio::task::spawn_blocking(move || {
                let mut terminal_event = None;
                let result = {
                    let mut on_event = |event: StreamEvent| {
                        if event.kind == StreamEventKind::Terminal {
                            terminal_event = Some(event);
                            return Ok(());
                        }
                        let (chunk, _) = apple_stream_chunk(&event, &stream_model)?;
                        sender
                            .blocking_send((Ok(chunk), false))
                            .map_err(|_| FoundationModelsXpcError::Cancelled)
                    };
                    worker_service.stream(&request, &mut on_event)
                };
                match (result, terminal_event) {
                    (Ok(()), Some(event)) => match apple_stream_chunk(&event, &stream_model) {
                        Ok(chunk) => {
                            let _ = sender.blocking_send((Ok(chunk.0), true));
                        }
                        Err(error) => {
                            let _ = sender.blocking_send((Err(map_xpc_error(error)), true));
                        }
                    },
                    (Ok(()), None) => {
                        let _ = sender.blocking_send((
                            Err(DeliveryError::Apple(AppleDeliveryError::InvalidResponse)),
                            true,
                        ));
                    }
                    (Err(error), _) => {
                        let _ = sender.blocking_send((Err(map_xpc_error(error)), true));
                    }
                }
            });
            Ok(StreamingDeliveryResponse::new(
                StatusCode::OK,
                "text/event-stream",
                Box::pin(AppleDeliveryStream {
                    receiver,
                    service,
                    request_id,
                    finished: false,
                }),
            ))
        })
    }
}

/// Buffered dispatcher that keeps native and OpenAI-compatible providers distinct.
pub struct BufferedProviderDelivery<O, A> {
    openai: O,
    apple: A,
}

impl<O, A> BufferedProviderDelivery<O, A> {
    /// Bind provider-specific buffered implementations.
    #[must_use]
    pub const fn new(openai: O, apple: A) -> Self {
        Self { openai, apple }
    }
}

impl<O, A> BufferedDelivery for BufferedProviderDelivery<O, A>
where
    O: BufferedDelivery,
    A: BufferedDelivery,
{
    fn send<'a>(&'a self, model: &'a ConfiguredModel, body: Value) -> DeliveryFuture<'a> {
        match model.provider() {
            ProviderKind::OpenAiCompatible => self.openai.send(model, body),
            ProviderKind::AppleFoundationModels => self.apple.send(model, body),
        }
    }
}

impl<O, A> StreamingDelivery for BufferedProviderDelivery<O, A>
where
    O: StreamingDelivery,
    A: StreamingDelivery,
{
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        body: Value,
    ) -> StreamingDeliveryFuture<'a> {
        match model.provider() {
            ProviderKind::OpenAiCompatible => self.openai.send_stream(model, body),
            ProviderKind::AppleFoundationModels => self.apple.send_stream(model, body),
        }
    }
}

fn apple_generate_request(
    body: &Value,
    request_id: String,
    timeout: Duration,
    streaming: bool,
) -> Result<GenerateRequest, DeliveryError> {
    let object = body
        .as_object()
        .ok_or(DeliveryError::Apple(AppleDeliveryError::InvalidRequest))?;
    if (object.get("stream").and_then(Value::as_bool) == Some(true)) != streaming
        || ["tools", "tool_choice", "response_format", "logprobs"]
            .iter()
            .any(|field| object.contains_key(*field))
        || object
            .get("n")
            .and_then(Value::as_u64)
            .is_some_and(|n| n != 1)
    {
        return Err(DeliveryError::Apple(AppleDeliveryError::InvalidRequest));
    }
    let raw_messages = object
        .get("messages")
        .and_then(Value::as_array)
        .ok_or(DeliveryError::Apple(AppleDeliveryError::InvalidRequest))?;
    let mut instructions = Vec::new();
    let mut messages = Vec::new();
    for raw in raw_messages {
        let role = raw
            .get("role")
            .and_then(Value::as_str)
            .ok_or(DeliveryError::Apple(AppleDeliveryError::InvalidRequest))?;
        let content = raw
            .get("content")
            .and_then(Value::as_str)
            .ok_or(DeliveryError::Apple(AppleDeliveryError::InvalidRequest))?;
        match role {
            "system" | "developer" => instructions.push(content),
            "user" => messages.push(Message {
                role: MessageRole::User,
                content: content.to_owned(),
            }),
            "assistant" => messages.push(Message {
                role: MessageRole::Assistant,
                content: content.to_owned(),
            }),
            _ => return Err(DeliveryError::Apple(AppleDeliveryError::InvalidRequest)),
        }
    }
    Ok(GenerateRequest {
        request_id,
        instructions: (!instructions.is_empty()).then(|| instructions.join("\n\n")),
        messages,
        timeout,
    })
}

fn apple_stream_chunk(
    event: &StreamEvent,
    provider_model: &str,
) -> Result<(Bytes, bool), FoundationModelsXpcError> {
    let data = match event.kind {
        StreamEventKind::Chunk => serde_json::json!({
            "id": event.request_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": provider_model,
            "choices": [{
                "index": 0,
                "delta": {"content": event.content.as_deref().unwrap_or_default()},
                "finish_reason": Value::Null
            }]
        }),
        StreamEventKind::Terminal => serde_json::json!({
            "id": event.request_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": provider_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }),
    };
    let encoded =
        serde_json::to_string(&data).map_err(|_| FoundationModelsXpcError::MalformedResponse)?;
    let terminal = event.kind == StreamEventKind::Terminal;
    let suffix = if terminal {
        "\n\ndata: [DONE]\n\n"
    } else {
        "\n\n"
    };
    Ok((Bytes::from(format!("data: {encoded}{suffix}")), terminal))
}

fn map_availability(availability: Availability) -> Result<(), DeliveryError> {
    match availability {
        Availability::Available => Ok(()),
        Availability::ModelNotReady | Availability::AppleIntelligenceNotEnabled => {
            Err(DeliveryError::Apple(AppleDeliveryError::NotReady))
        }
        Availability::Unsupported | Availability::DeviceNotEligible => {
            Err(DeliveryError::Apple(AppleDeliveryError::Unsupported))
        }
        Availability::Unavailable => Err(DeliveryError::Apple(AppleDeliveryError::Unavailable)),
    }
}

fn map_xpc_error(error: FoundationModelsXpcError) -> DeliveryError {
    let class = match error {
        FoundationModelsXpcError::UnsupportedPlatform => AppleDeliveryError::Unsupported,
        FoundationModelsXpcError::InvalidRequest
        | FoundationModelsXpcError::RequestTooLarge
        | FoundationModelsXpcError::UnsupportedVersion => AppleDeliveryError::InvalidRequest,
        FoundationModelsXpcError::MalformedResponse
        | FoundationModelsXpcError::ResponseTooLarge
        | FoundationModelsXpcError::InvalidStream => AppleDeliveryError::InvalidResponse,
        FoundationModelsXpcError::Unavailable
        | FoundationModelsXpcError::TimedOut
        | FoundationModelsXpcError::Cancelled
        | FoundationModelsXpcError::Denied
        | FoundationModelsXpcError::GenerationFailed => AppleDeliveryError::Unavailable,
    };
    DeliveryError::Apple(class)
}

/// Reqwest-backed OpenAI-compatible delivery with per-request credential resolution.
pub struct OpenAiCompatibleDelivery<S> {
    client: OpenAiProviderClient,
    credentials: S,
}

impl<S> OpenAiCompatibleDelivery<S> {
    /// Bind an already policy-constrained provider client to a credential source.
    #[must_use]
    pub const fn new(client: OpenAiProviderClient, credentials: S) -> Self {
        Self {
            client,
            credentials,
        }
    }
}

impl<S> fmt::Debug for OpenAiCompatibleDelivery<S> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("OpenAiCompatibleDelivery")
            .field("client", &self.client)
            .field("credentials", &"[REDACTED SOURCE]")
            .finish()
    }
}

impl<S> BufferedDelivery for OpenAiCompatibleDelivery<S>
where
    S: CredentialSource,
{
    fn send<'a>(&'a self, model: &'a ConfiguredModel, mut body: Value) -> DeliveryFuture<'a> {
        Box::pin(async move {
            let endpoint = OpenAiEndpoint::parse(model.endpoint())
                .map_err(|_| DeliveryError::InvalidEndpoint)?;
            let credential = self
                .credentials
                .resolve(model.api_key_env())
                .map_err(|_| DeliveryError::CredentialUnavailable)?;
            if model.api_key_env().is_some() && credential.is_none() {
                return Err(DeliveryError::CredentialUnavailable);
            }
            let Some(body_object) = body.as_object_mut() else {
                return Err(DeliveryError::Provider(ProviderError::Transport));
            };
            body_object.insert(
                "model".to_owned(),
                Value::String(model.provider_model().to_owned()),
            );
            let response = self
                .client
                .send_buffered(&endpoint, &body, credential.as_ref())
                .await
                .map_err(DeliveryError::Provider)?;
            let status = response.status();
            let content_type = response.content_type().to_owned();
            let body = response.into_body();
            Ok(BufferedDeliveryResponse::new(status, content_type, body))
        })
    }
}

impl<S> StreamingDelivery for OpenAiCompatibleDelivery<S>
where
    S: CredentialSource,
{
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        mut body: Value,
    ) -> StreamingDeliveryFuture<'a> {
        Box::pin(async move {
            let endpoint = OpenAiEndpoint::parse(model.endpoint())
                .map_err(|_| DeliveryError::InvalidEndpoint)?;
            let credential = self
                .credentials
                .resolve(model.api_key_env())
                .map_err(|_| DeliveryError::CredentialUnavailable)?;
            if model.api_key_env().is_some() && credential.is_none() {
                return Err(DeliveryError::CredentialUnavailable);
            }
            let Some(body_object) = body.as_object_mut() else {
                return Err(DeliveryError::Provider(ProviderError::Transport));
            };
            body_object.insert(
                "model".to_owned(),
                Value::String(model.provider_model().to_owned()),
            );
            body_object.insert("stream".to_owned(), Value::Bool(true));
            let response = self
                .client
                .send_stream(&endpoint, &body, credential.as_ref())
                .await
                .map_err(DeliveryError::Provider)?;
            let status = response.status();
            let content_type = response.content_type().to_owned();
            let stream = response
                .into_stream()
                .map(|chunk| chunk.map_err(DeliveryError::Provider));
            Ok(StreamingDeliveryResponse::new(
                status,
                content_type,
                Box::pin(stream),
            ))
        })
    }
}

/// Whether a validated endpoint is provably local without DNS resolution.
#[must_use]
pub fn endpoint_is_literal_loopback(endpoint: &str) -> bool {
    let Ok(endpoint) = OpenAiEndpoint::parse(endpoint) else {
        return false;
    };
    let Some(host) = endpoint.chat_completions_url().host_str() else {
        return false;
    };
    let address_host = host
        .strip_prefix('[')
        .and_then(|host| host.strip_suffix(']'))
        .unwrap_or(host);
    host.eq_ignore_ascii_case("localhost")
        || address_host
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::sync::{Condvar, Mutex};

    use wayfinder_config::gateway::ProviderTier;

    use super::*;

    #[derive(Debug)]
    struct FakeAppleService {
        availability: Availability,
        content: String,
        requests: Mutex<Vec<GenerateRequest>>,
        availability_calls: Mutex<Vec<String>>,
        stream_events: Mutex<VecDeque<Result<StreamEvent, FoundationModelsXpcError>>>,
        stall_stream: bool,
        cancelled: (Mutex<bool>, Condvar),
        cancel_calls: Mutex<Vec<String>>,
    }

    impl FakeAppleService {
        fn new(availability: Availability, content: &str) -> Self {
            Self {
                availability,
                content: content.to_owned(),
                requests: Mutex::new(Vec::new()),
                availability_calls: Mutex::new(Vec::new()),
                stream_events: Mutex::new(VecDeque::new()),
                stall_stream: false,
                cancelled: (Mutex::new(false), Condvar::new()),
                cancel_calls: Mutex::new(Vec::new()),
            }
        }

        fn with_stream(
            mut self,
            events: impl IntoIterator<Item = Result<StreamEvent, FoundationModelsXpcError>>,
        ) -> Self {
            self.stream_events = Mutex::new(events.into_iter().collect());
            self
        }

        fn with_stalled_stream(mut self) -> Self {
            self.stall_stream = true;
            self
        }
    }

    impl AppleFoundationModelsService for FakeAppleService {
        fn availability(&self, request_id: &str) -> Result<Availability, FoundationModelsXpcError> {
            self.availability_calls
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?
                .push(request_id.to_owned());
            Ok(self.availability)
        }

        fn generate(
            &self,
            request: &GenerateRequest,
        ) -> Result<GenerateResponse, FoundationModelsXpcError> {
            if self
                .availability_calls
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?
                .is_empty()
            {
                return Err(FoundationModelsXpcError::GenerationFailed);
            }
            self.requests
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?
                .push(request.clone());
            Ok(GenerateResponse {
                request_id: request.request_id.clone(),
                content: self.content.clone(),
            })
        }

        fn stream(
            &self,
            request: &GenerateRequest,
            on_event: &mut dyn FnMut(StreamEvent) -> Result<(), FoundationModelsXpcError>,
        ) -> Result<(), FoundationModelsXpcError> {
            self.requests
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?
                .push(request.clone());
            if self.stall_stream {
                let (cancelled, wake) = &self.cancelled;
                let mut cancelled = cancelled
                    .lock()
                    .map_err(|_| FoundationModelsXpcError::Unavailable)?;
                while !*cancelled {
                    cancelled = wake
                        .wait(cancelled)
                        .map_err(|_| FoundationModelsXpcError::Unavailable)?;
                }
                return Err(FoundationModelsXpcError::Cancelled);
            }
            let mut events = self
                .stream_events
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?;
            while let Some(event) = events.pop_front() {
                on_event(event?)?;
            }
            Ok(())
        }

        fn cancel(&self, request_id: &str) -> Result<(), FoundationModelsXpcError> {
            self.cancel_calls
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)?
                .push(request_id.to_owned());
            let (cancelled, wake) = &self.cancelled;
            *cancelled
                .lock()
                .map_err(|_| FoundationModelsXpcError::Unavailable)? = true;
            wake.notify_all();
            Ok(())
        }
    }

    fn apple_model() -> ConfiguredModel {
        ConfiguredModel::new("apple-local", "", "system-default", None, true).with_provider(
            ProviderKind::AppleFoundationModels,
            Some(ProviderTier::Local),
        )
    }

    #[test]
    fn loopback_proof_accepts_only_literal_local_hosts() {
        assert!(endpoint_is_literal_loopback("http://localhost:11434/v1"));
        assert!(endpoint_is_literal_loopback("http://127.0.0.1/v1"));
        assert!(endpoint_is_literal_loopback("http://[::1]:8080/v1"));
        assert!(!endpoint_is_literal_loopback("https://api.example.com/v1"));
        assert!(!endpoint_is_literal_loopback(
            "http://local-model.internal/v1"
        ));
        assert!(!endpoint_is_literal_loopback("file:///tmp/socket"));
    }

    #[test]
    fn buffered_response_debug_omits_body() {
        let response = BufferedDeliveryResponse::new(
            StatusCode::OK,
            "application/json",
            Bytes::from_static(b"prompt-or-provider-content"),
        );
        let rendered = format!("{response:?}");
        assert!(rendered.contains("body_bytes"));
        assert!(!rendered.contains("prompt-or-provider-content"));
    }

    #[tokio::test]
    async fn apple_buffered_delivery_checks_availability_translates_and_wraps_response()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(FakeAppleService::new(
            Availability::Available,
            "native answer",
        ));
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-fixed".to_owned()
            });
        let response = delivery
            .send(
                &apple_model(),
                serde_json::json!({
                    "model": "apple-local",
                    "messages": [
                        {"role": "system", "content": "be concise"},
                        {"role": "developer", "content": "use plain text"},
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "prior"},
                        {"role": "user", "content": "continue"}
                    ]
                }),
            )
            .await?;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.content_type(), "application/json");
        let body: Value = serde_json::from_slice(&response.into_body())?;
        assert_eq!(body["id"], "request-fixed");
        assert_eq!(body["model"], "system-default");
        assert_eq!(body["choices"][0]["message"]["content"], "native answer");

        let availability_calls = service
            .availability_calls
            .lock()
            .map_err(|_| std::io::Error::other("fake availability lock poisoned"))?;
        assert_eq!(availability_calls.as_slice(), ["request-fixed"]);
        drop(availability_calls);
        let requests = service
            .requests
            .lock()
            .map_err(|_| std::io::Error::other("fake request lock poisoned"))?;
        assert_eq!(requests.len(), 1);
        assert_eq!(
            requests[0].instructions.as_deref(),
            Some("be concise\n\nuse plain text")
        );
        assert_eq!(requests[0].messages.len(), 3);
        assert_eq!(requests[0].messages[0].role, MessageRole::User);
        assert_eq!(requests[0].messages[2].content, "continue");
        Ok(())
    }

    #[tokio::test]
    async fn apple_buffered_delivery_rejects_unsupported_request_before_native_call()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(FakeAppleService::new(Availability::Available, "unused"));
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-fixed".to_owned()
            });
        let result = delivery
            .send(
                &apple_model(),
                serde_json::json!({
                    "messages": [{"role": "user", "content": "secret prompt"}],
                    "tools": [{"type": "function"}]
                }),
            )
            .await;
        let Err(error) = result else {
            return Err("tools should be rejected before native delivery".into());
        };
        assert_eq!(
            error,
            DeliveryError::Apple(AppleDeliveryError::InvalidRequest)
        );
        assert!(format!("{error:?}").find("secret prompt").is_none());
        assert!(
            service
                .availability_calls
                .lock()
                .map_err(|_| std::io::Error::other("fake availability lock poisoned"))?
                .is_empty()
        );
        assert!(
            service
                .requests
                .lock()
                .map_err(|_| std::io::Error::other("fake request lock poisoned"))?
                .is_empty()
        );
        Ok(())
    }

    #[tokio::test]
    async fn apple_buffered_delivery_distinguishes_not_ready_without_generating()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(FakeAppleService::new(Availability::ModelNotReady, "unused"));
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-fixed".to_owned()
            });
        let result = delivery
            .send(
                &apple_model(),
                serde_json::json!({
                    "messages": [{"role": "user", "content": "hello"}]
                }),
            )
            .await;
        let Err(error) = result else {
            return Err("not-ready availability should stop before generation".into());
        };
        assert_eq!(error, DeliveryError::Apple(AppleDeliveryError::NotReady));
        assert!(
            service
                .requests
                .lock()
                .map_err(|_| std::io::Error::other("fake request lock poisoned"))?
                .is_empty()
        );
        Ok(())
    }

    #[tokio::test]
    async fn apple_streaming_checks_availability_and_translates_ordered_events()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(
            FakeAppleService::new(Availability::Available, "unused").with_stream([
                Ok(StreamEvent::chunk("request-stream", 0, "Hel")),
                Ok(StreamEvent::chunk("request-stream", 1, "lo")),
                Ok(StreamEvent::terminal("request-stream", 2)),
            ]),
        );
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-stream".to_owned()
            });
        let response = delivery
            .send_stream(
                &apple_model(),
                serde_json::json!({
                    "stream": true,
                    "messages": [{"role": "user", "content": "hello"}]
                }),
            )
            .await?;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.content_type(), "text/event-stream");
        let chunks = response.into_stream().collect::<Vec<_>>().await;
        let bytes = chunks.into_iter().collect::<Result<Vec<_>, _>>()?.concat();
        let body = String::from_utf8(bytes)?;
        assert!(body.contains(r#""content":"Hel""#));
        assert!(body.contains(r#""content":"lo""#));
        assert!(body.contains(r#""finish_reason":"stop""#));
        assert!(body.ends_with("data: [DONE]\n\n"));
        assert_eq!(
            service
                .availability_calls
                .lock()
                .map_err(|_| std::io::Error::other("fake availability lock poisoned"))?
                .as_slice(),
            ["request-stream"]
        );
        assert_eq!(
            service
                .requests
                .lock()
                .map_err(|_| std::io::Error::other("fake request lock poisoned"))?
                .len(),
            1
        );
        assert!(
            service
                .cancel_calls
                .lock()
                .map_err(|_| std::io::Error::other("fake cancel lock poisoned"))?
                .is_empty()
        );
        Ok(())
    }

    #[tokio::test]
    async fn apple_streaming_surfaces_post_chunk_protocol_failure_without_done()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(
            FakeAppleService::new(Availability::Available, "unused").with_stream([
                Ok(StreamEvent::chunk("request-stream", 0, "partial")),
                Err(FoundationModelsXpcError::InvalidStream),
            ]),
        );
        let delivery = AppleFoundationModelDelivery::new(service, Duration::from_secs(10), || {
            "request-stream".to_owned()
        });
        let response = delivery
            .send_stream(
                &apple_model(),
                serde_json::json!({
                    "stream": true,
                    "messages": [{"role": "user", "content": "hello"}]
                }),
            )
            .await?;
        let chunks = response.into_stream().collect::<Vec<_>>().await;
        assert_eq!(chunks.len(), 2);
        assert!(chunks[0].as_ref().is_ok_and(|chunk| {
            String::from_utf8_lossy(chunk).contains(r#""content":"partial""#)
        }));
        assert_eq!(
            chunks[1].as_ref().err(),
            Some(&DeliveryError::Apple(AppleDeliveryError::InvalidResponse))
        );
        Ok(())
    }

    #[tokio::test]
    async fn apple_streaming_rejects_missing_terminal_and_service_crash_without_done()
    -> Result<(), Box<dyn std::error::Error>> {
        for (failure, expected) in [
            (None, AppleDeliveryError::InvalidResponse),
            (
                Some(FoundationModelsXpcError::Unavailable),
                AppleDeliveryError::Unavailable,
            ),
        ] {
            let events = std::iter::once(Ok(StreamEvent::chunk("request-stream", 0, "partial")))
                .chain(failure.into_iter().map(Err));
            let service = Arc::new(
                FakeAppleService::new(Availability::Available, "unused").with_stream(events),
            );
            let delivery =
                AppleFoundationModelDelivery::new(service, Duration::from_secs(10), || {
                    "request-stream".to_owned()
                });
            let response = delivery
                .send_stream(
                    &apple_model(),
                    serde_json::json!({
                        "stream": true,
                        "messages": [{"role": "user", "content": "hello"}]
                    }),
                )
                .await?;
            let chunks = response.into_stream().collect::<Vec<_>>().await;
            assert_eq!(chunks.len(), 2);
            assert!(chunks.iter().all(|chunk| match chunk {
                Ok(bytes) => !String::from_utf8_lossy(bytes).contains("data: [DONE]"),
                Err(_) => true,
            }));
            assert_eq!(
                chunks[1].as_ref().err(),
                Some(&DeliveryError::Apple(expected))
            );
        }
        Ok(())
    }

    #[tokio::test]
    async fn apple_streaming_not_ready_fails_before_starting_session()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(FakeAppleService::new(Availability::ModelNotReady, "unused"));
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-stream".to_owned()
            });
        let result = delivery
            .send_stream(
                &apple_model(),
                serde_json::json!({
                    "stream": true,
                    "messages": [{"role": "user", "content": "hello"}]
                }),
            )
            .await;
        let Err(error) = result else {
            return Err("not-ready must fail before streaming response establishment".into());
        };
        assert_eq!(error, DeliveryError::Apple(AppleDeliveryError::NotReady));
        assert!(
            service
                .requests
                .lock()
                .map_err(|_| std::io::Error::other("fake request lock poisoned"))?
                .is_empty()
        );
        Ok(())
    }

    #[tokio::test]
    async fn dropping_apple_stream_cancels_stalled_native_session()
    -> Result<(), Box<dyn std::error::Error>> {
        let service = Arc::new(
            FakeAppleService::new(Availability::Available, "unused").with_stalled_stream(),
        );
        let delivery =
            AppleFoundationModelDelivery::new(service.clone(), Duration::from_secs(10), || {
                "request-stalled".to_owned()
            });
        let response = delivery
            .send_stream(
                &apple_model(),
                serde_json::json!({
                    "stream": true,
                    "messages": [{"role": "user", "content": "hello"}]
                }),
            )
            .await?;
        drop(response);
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if service
                    .cancel_calls
                    .lock()
                    .map(|calls| !calls.is_empty())
                    .unwrap_or(false)
                {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await?;
        assert_eq!(
            service
                .cancel_calls
                .lock()
                .map_err(|_| std::io::Error::other("fake cancel lock poisoned"))?
                .as_slice(),
            ["request-stalled"]
        );
        Ok(())
    }
}
