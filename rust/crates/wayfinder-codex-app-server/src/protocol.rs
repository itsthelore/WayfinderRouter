use std::collections::VecDeque;
use std::path::Path;
use std::time::Duration;

use serde_json::{Value, json};

use crate::{
    AppServerTransport, CodexAppServerError, MAX_JSONL_LINE_BYTES, MAX_NOTIFICATION_QUEUE,
    MAX_PENDING_REQUESTS, MAX_RESPONSE_BYTES, SUPPORTED_HELPER_VERSION,
};

const READ_BUFFER_BYTES: usize = 8_192;

#[derive(Debug)]
pub(crate) struct Notification {
    pub method: String,
    pub params: Value,
}

pub(crate) struct Session {
    transport: Box<dyn AppServerTransport>,
    decoder: JsonlDecoder,
    notifications: VecDeque<Notification>,
    next_id: u64,
    pending: usize,
    initialized: bool,
}

impl Session {
    pub async fn connect(
        transport: Box<dyn AppServerTransport>,
        client_version: &str,
        expected_codex_home: &Path,
        timeout: Duration,
    ) -> Result<Self, CodexAppServerError> {
        let mut session = Self {
            transport,
            decoder: JsonlDecoder::default(),
            notifications: VecDeque::new(),
            next_id: 1,
            pending: 0,
            initialized: false,
        };
        let initialize_result = session
            .request_inner(
                "initialize",
                json!({
                    "clientInfo": {
                        "name": "wayfinder",
                        "title": "Wayfinder",
                        "version": client_version,
                    }
                }),
                timeout,
                true,
            )
            .await?;
        validate_initialize_result(&initialize_result, expected_codex_home)?;
        session
            .send_notification("initialized", json!({}), timeout)
            .await?;
        session.initialized = true;
        Ok(session)
    }

    pub async fn request(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, CodexAppServerError> {
        if !self.initialized {
            return Err(CodexAppServerError::MalformedProtocol);
        }
        self.request_inner(method, params, timeout, false).await
    }

    async fn request_inner(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
        initialize: bool,
    ) -> Result<Value, CodexAppServerError> {
        if self.pending >= MAX_PENDING_REQUESTS || (!initialize && !self.initialized) {
            return Err(CodexAppServerError::MalformedProtocol);
        }
        let id = self.next_id;
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or(CodexAppServerError::MalformedProtocol)?;
        let request = json!({ "method": method, "id": id, "params": params });
        self.pending += 1;
        let result = tokio::time::timeout(timeout, async {
            self.send_value(&request).await?;
            self.wait_for_response(id, timeout).await
        })
        .await;
        self.pending = self.pending.saturating_sub(1);
        match result {
            Ok(result) => result,
            Err(_) => Err(CodexAppServerError::TimedOut),
        }
    }

    async fn wait_for_response(
        &mut self,
        expected_id: u64,
        timeout: Duration,
    ) -> Result<Value, CodexAppServerError> {
        loop {
            match self.read_message().await? {
                WireMessage::Response { id, result, error } => {
                    if id != expected_id {
                        return Err(CodexAppServerError::CorrelationFailed);
                    }
                    return match (result, error) {
                        (Some(result), None) => Ok(result),
                        (None, Some(error)) => Err(classify_remote_error(&error)),
                        _ => Err(CodexAppServerError::MalformedProtocol),
                    };
                }
                WireMessage::Notification(notification) => self.queue(notification)?,
                WireMessage::ServerRequest { id } => {
                    self.deny_server_request(id, timeout).await?;
                    return Err(CodexAppServerError::ForbiddenAction);
                }
            }
        }
    }

    pub async fn next_notification(
        &mut self,
        idle_timeout: Duration,
    ) -> Result<Notification, CodexAppServerError> {
        if let Some(notification) = self.notifications.pop_front() {
            return Ok(notification);
        }
        let message = tokio::time::timeout(idle_timeout, self.read_message())
            .await
            .map_err(|_| CodexAppServerError::TimedOut)??;
        match message {
            WireMessage::Notification(notification) => Ok(notification),
            WireMessage::Response { .. } => Err(CodexAppServerError::CorrelationFailed),
            WireMessage::ServerRequest { id } => {
                self.deny_server_request(id, idle_timeout).await?;
                Err(CodexAppServerError::ForbiddenAction)
            }
        }
    }

    pub fn drain_notifications(&mut self) -> Vec<Notification> {
        self.notifications.drain(..).collect()
    }

    pub async fn terminate(&mut self, timeout: Duration) -> Result<(), CodexAppServerError> {
        tokio::time::timeout(timeout, self.transport.terminate())
            .await
            .map_err(|_| CodexAppServerError::TimedOut)??;
        Ok(())
    }

    async fn send_notification(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<(), CodexAppServerError> {
        tokio::time::timeout(
            timeout,
            self.send_value(&json!({ "method": method, "params": params })),
        )
        .await
        .map_err(|_| CodexAppServerError::TimedOut)?
    }

    async fn deny_server_request(
        &mut self,
        id: Value,
        timeout: Duration,
    ) -> Result<(), CodexAppServerError> {
        tokio::time::timeout(
            timeout,
            self.send_value(&json!({
                "id": id,
                "error": { "code": -32601, "message": "Unsupported by this client" }
            })),
        )
        .await
        .map_err(|_| CodexAppServerError::TimedOut)?
    }

    async fn send_value(&mut self, value: &Value) -> Result<(), CodexAppServerError> {
        let mut encoded =
            serde_json::to_vec(value).map_err(|_| CodexAppServerError::MalformedProtocol)?;
        if encoded.len() > MAX_JSONL_LINE_BYTES {
            return Err(CodexAppServerError::RequestTooLarge);
        }
        encoded.push(b'\n');
        self.transport.write(&encoded).await
    }

    fn queue(&mut self, notification: Notification) -> Result<(), CodexAppServerError> {
        if self.notifications.len() >= MAX_NOTIFICATION_QUEUE {
            return Err(CodexAppServerError::NotificationQueueFull);
        }
        self.notifications.push_back(notification);
        Ok(())
    }

    async fn read_message(&mut self) -> Result<WireMessage, CodexAppServerError> {
        loop {
            if let Some(line) = self.decoder.next_line()? {
                if line.len() > MAX_RESPONSE_BYTES && line.len() <= MAX_JSONL_LINE_BYTES {
                    return Err(CodexAppServerError::ResponseTooLarge);
                }
                let value: Value = serde_json::from_slice(&line)
                    .map_err(|_| CodexAppServerError::MalformedProtocol)?;
                return classify_message(value);
            }
            let mut read_buffer = [0_u8; READ_BUFFER_BYTES];
            let count = self.transport.read(&mut read_buffer).await?;
            if count == 0 {
                return Err(CodexAppServerError::EndOfStream);
            }
            self.decoder.push(&read_buffer[..count])?;
        }
    }
}

fn validate_initialize_result(
    result: &Value,
    expected_codex_home: &Path,
) -> Result<(), CodexAppServerError> {
    let codex_home = result
        .get("codexHome")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty() && path.len() <= 4_096)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let returned_home =
        std::fs::canonicalize(codex_home).map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    let expected_home = std::fs::canonicalize(expected_codex_home)
        .map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    if returned_home != expected_home {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let family = result
        .get("platformFamily")
        .and_then(Value::as_str)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let platform_os = result
        .get("platformOs")
        .and_then(Value::as_str)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let expected_family = if cfg!(target_os = "windows") {
        "windows"
    } else {
        "unix"
    };
    if family != expected_family || platform_os != std::env::consts::OS {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let user_agent = result
        .get("userAgent")
        .and_then(Value::as_str)
        .filter(|value| {
            let expected = format!("wayfinder/{SUPPORTED_HELPER_VERSION}");
            !value.is_empty()
                && value.len() <= 256
                && value.strip_prefix(&expected).is_some_and(|suffix| {
                    suffix.is_empty() || suffix.chars().next().is_some_and(char::is_whitespace)
                })
        })
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if user_agent.chars().any(char::is_control) {
        return Err(CodexAppServerError::MalformedProtocol);
    }
    Ok(())
}

fn classify_remote_error(error: &Value) -> CodexAppServerError {
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_ascii_lowercase();
    if message.contains("unauthor")
        || message.contains("authentication")
        || message.contains("sign in")
        || message.contains("login")
    {
        CodexAppServerError::AuthenticationRequired
    } else {
        CodexAppServerError::RequestRejected
    }
}

enum WireMessage {
    Response {
        id: u64,
        result: Option<Value>,
        error: Option<Value>,
    },
    Notification(Notification),
    ServerRequest {
        id: Value,
    },
}

fn classify_message(value: Value) -> Result<WireMessage, CodexAppServerError> {
    let object = value
        .as_object()
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let id = object.get("id");
    let method = object.get("method").and_then(Value::as_str);
    match (id, method) {
        (Some(id), Some(_)) => Ok(WireMessage::ServerRequest { id: id.clone() }),
        (Some(id), None) => {
            let id = id.as_u64().ok_or(CodexAppServerError::CorrelationFailed)?;
            Ok(WireMessage::Response {
                id,
                result: object.get("result").cloned(),
                error: object.get("error").cloned(),
            })
        }
        (None, Some(method)) => Ok(WireMessage::Notification(Notification {
            method: method.to_owned(),
            params: object.get("params").cloned().unwrap_or_else(|| json!({})),
        })),
        (None, None) => Err(CodexAppServerError::MalformedProtocol),
    }
}

#[derive(Default)]
struct JsonlDecoder {
    buffer: Vec<u8>,
}

impl JsonlDecoder {
    fn push(&mut self, bytes: &[u8]) -> Result<(), CodexAppServerError> {
        let new_len = self
            .buffer
            .len()
            .checked_add(bytes.len())
            .ok_or(CodexAppServerError::LineTooLarge)?;
        if new_len > MAX_JSONL_LINE_BYTES && !bytes.contains(&b'\n') {
            return Err(CodexAppServerError::LineTooLarge);
        }
        self.buffer.extend_from_slice(bytes);
        if let Some(position) = self.buffer.iter().position(|byte| *byte == b'\n')
            && position > MAX_JSONL_LINE_BYTES
        {
            return Err(CodexAppServerError::LineTooLarge);
        }
        Ok(())
    }

    fn next_line(&mut self) -> Result<Option<Vec<u8>>, CodexAppServerError> {
        let Some(position) = self.buffer.iter().position(|byte| *byte == b'\n') else {
            if self.buffer.len() > MAX_JSONL_LINE_BYTES {
                return Err(CodexAppServerError::LineTooLarge);
            }
            return Ok(None);
        };
        if position > MAX_JSONL_LINE_BYTES {
            return Err(CodexAppServerError::LineTooLarge);
        }
        let mut line = self.buffer.drain(..=position).collect::<Vec<_>>();
        let _ = line.pop();
        if line.last() == Some(&b'\r') {
            let _ = line.pop();
        }
        if line.is_empty() {
            return Err(CodexAppServerError::MalformedProtocol);
        }
        Ok(Some(line))
    }
}
