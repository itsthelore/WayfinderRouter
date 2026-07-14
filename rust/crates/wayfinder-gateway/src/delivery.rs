//! Buffered provider-delivery seam and OpenAI-compatible implementation.

use std::fmt;
use std::future::Future;
use std::net::IpAddr;
use std::pin::Pin;

use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use http::StatusCode;
use serde_json::Value;
use thiserror::Error;
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

/// Async delivery abstraction for one non-retried streaming attempt.
pub trait StreamingDelivery: Send + Sync {
    /// Establish a stream to exactly the selected configured model.
    fn send_stream<'a>(
        &'a self,
        model: &'a ConfiguredModel,
        body: Value,
    ) -> StreamingDeliveryFuture<'a>;
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
    use super::*;

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
}
