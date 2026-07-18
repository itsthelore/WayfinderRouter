//! Bounded client for a managed Codex app-server child process.
//!
//! The public API deliberately exposes managed ChatGPT authentication only.
//! Tokens, Codex auth files, API-key login, and experimental external-token
//! authentication never cross this crate boundary.

use std::future::Future;
use std::path::{Component, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::Duration;
use std::time::Instant;

#[cfg(target_os = "macos")]
use std::process::{Command as StdCommand, Stdio};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use thiserror::Error;
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

mod process;
mod protocol;

use protocol::{Notification, Session};

pub const MAX_JSONL_LINE_BYTES: usize = 65_536;
pub const MAX_PENDING_REQUESTS: usize = 16;
pub const MAX_NOTIFICATION_QUEUE: usize = 128;
pub const MAX_TRANSCRIPT_BYTES: usize = 1_048_576;
pub const MAX_RESPONSE_BYTES: usize = 65_536;
pub const MAX_STREAM_DELTA_ESCAPED_BYTES: usize = 61_440;
pub const MAX_MODELS: usize = 128;
pub const MAX_MODEL_PAGES: usize = 8;
pub const MAX_RUNTIME_RESTARTS: usize = 2;
pub const SUPPORTED_HELPER_VERSION: &str = "0.145.0-alpha.18";
pub const MAX_IDENTIFIER_BYTES: usize = 128;
pub const MAX_INSTRUCTIONS_BYTES: usize = 32_768;

const GENERAL_CHAT_INSTRUCTIONS: &str = "You are the text-only assistant inside Wayfinder Chat. Answer the user's request directly. Never use tools, execute commands, inspect or modify files, browse, invoke apps or plugins, delegate to agents, request approvals, or perform computer actions. If a request would require any such action, explain that this chat surface cannot perform it.";

#[derive(Clone, Debug)]
pub struct RuntimeConfig {
    pub helper_path: PathBuf,
    pub codex_home: PathBuf,
    pub workspace: PathBuf,
    pub client_version: String,
    pub limits: RuntimeLimits,
}

#[derive(Clone, Copy, Debug)]
pub struct RuntimeLimits {
    pub request_timeout: Duration,
    pub login_timeout: Duration,
    pub turn_idle_timeout: Duration,
    pub interrupt_grace: Duration,
    pub shutdown_timeout: Duration,
}

impl Default for RuntimeLimits {
    fn default() -> Self {
        Self {
            request_timeout: Duration::from_secs(10),
            login_timeout: Duration::from_secs(300),
            turn_idle_timeout: Duration::from_secs(120),
            interrupt_grace: Duration::from_secs(3),
            shutdown_timeout: Duration::from_secs(3),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountStatus {
    SignedOut,
    AwaitingBrowser,
    AwaitingDeviceCode,
    Connected,
    ReauthRequired,
    Unavailable,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountSnapshot {
    pub status: AccountStatus,
    pub email: Option<String>,
    pub plan_type: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LoginMethod {
    Browser,
    DeviceCode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LoginStart {
    Browser {
        login_id: String,
        auth_url: String,
    },
    DeviceCode {
        login_id: String,
        verification_url: String,
        user_code: String,
    },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ModelInfo {
    pub id: String,
    pub display_name: String,
    pub description: String,
    pub is_default: bool,
    pub hidden: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRole {
    System,
    Developer,
    User,
    Assistant,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ChatMessage {
    pub role: ChatRole,
    pub content: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ChatEvent {
    Delta(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChatResponse {
    /// The authoritative text from the completed agent-message item.
    pub content: String,
    pub thread_id: String,
    pub turn_id: String,
}

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
pub enum CodexAppServerError {
    #[error("Codex app-server configuration is invalid")]
    InvalidConfiguration,
    #[error("Codex app-server is unavailable")]
    RuntimeUnavailable,
    #[error("Codex app-server request is invalid")]
    InvalidRequest,
    #[error("Codex app-server request exceeded its bound")]
    RequestTooLarge,
    #[error("Codex app-server response exceeded its bound")]
    ResponseTooLarge,
    #[error("Codex app-server protocol message is malformed")]
    MalformedProtocol,
    #[error("Codex app-server protocol line exceeded its bound")]
    LineTooLarge,
    #[error("Codex app-server protocol response did not correlate")]
    CorrelationFailed,
    #[error("Codex app-server notification queue is full")]
    NotificationQueueFull,
    #[error("Codex app-server request timed out")]
    TimedOut,
    #[error("Codex app-server rejected the request")]
    RequestRejected,
    #[error("Codex app-server is already serving a turn")]
    Busy,
    #[error("ChatGPT authentication is required")]
    AuthenticationRequired,
    #[error("ChatGPT usage limit has been reached")]
    UsageLimitReached,
    #[error("ChatGPT authentication mode is unsupported")]
    UnsupportedAuthentication,
    #[error("ChatGPT login failed")]
    LoginFailed,
    #[error("ChatGPT login was cancelled")]
    LoginCancelled,
    #[error("Requested Codex model is unavailable")]
    ModelUnavailable,
    #[error("Codex app-server attempted a forbidden action")]
    ForbiddenAction,
    #[error("Codex app-server turn failed")]
    TurnFailed,
    #[error("Codex app-server turn was interrupted")]
    Interrupted,
    #[error("Codex app-server closed its protocol stream")]
    EndOfStream,
    #[error("Codex credential store permissions are insecure")]
    InsecureCredentialStore,
}

pub type TransportFuture<'a, T> =
    Pin<Box<dyn Future<Output = Result<T, CodexAppServerError>> + Send + 'a>>;

/// Injectable byte transport. Reads may return arbitrary JSONL fragments.
pub trait AppServerTransport: Send {
    fn write<'a>(&'a mut self, bytes: &'a [u8]) -> TransportFuture<'a, ()>;
    fn read<'a>(&'a mut self, buffer: &'a mut [u8]) -> TransportFuture<'a, usize>;
    fn terminate<'a>(&'a mut self) -> TransportFuture<'a, ()>;
}

pub trait AppServerTransportFactory: Send + Sync {
    fn spawn<'a>(
        &'a self,
        config: &'a RuntimeConfig,
    ) -> TransportFuture<'a, Box<dyn AppServerTransport>>;
}

#[derive(Clone)]
pub struct CodexAppServerManager {
    inner: Arc<ManagerInner>,
}

struct ManagerInner {
    config: RuntimeConfig,
    factory: Arc<dyn AppServerTransportFactory>,
    session: Mutex<Option<Session>>,
    active_cancel: std::sync::Mutex<Option<CancellationToken>>,
    pending_login: std::sync::Mutex<Option<PendingLogin>>,
    login_failed: AtomicBool,
    session_started: AtomicBool,
    restart_count: AtomicUsize,
    cancelled_session_restart: AtomicBool,
}

#[derive(Clone, Debug)]
struct PendingLogin {
    login_id: String,
    method: LoginMethod,
    started_at: Instant,
}

impl CodexAppServerManager {
    pub fn new(config: RuntimeConfig) -> Result<Self, CodexAppServerError> {
        Self::with_factory(config, Arc::new(SystemProcessFactory))
    }

    pub fn with_factory(
        config: RuntimeConfig,
        factory: Arc<dyn AppServerTransportFactory>,
    ) -> Result<Self, CodexAppServerError> {
        validate_config(&config)?;
        Ok(Self {
            inner: Arc::new(ManagerInner {
                config,
                factory,
                session: Mutex::new(None),
                active_cancel: std::sync::Mutex::new(None),
                pending_login: std::sync::Mutex::new(None),
                login_failed: AtomicBool::new(false),
                session_started: AtomicBool::new(false),
                restart_count: AtomicUsize::new(0),
                cancelled_session_restart: AtomicBool::new(false),
            }),
        })
    }

    pub async fn account(&self) -> Result<AccountSnapshot, CodexAppServerError> {
        if let Some(login_id) = self.take_expired_login()? {
            let _ = self
                .rpc(
                    "account/login/cancel",
                    json!({ "loginId": login_id }),
                    self.inner.config.limits.request_timeout,
                )
                .await;
            return Err(CodexAppServerError::LoginFailed);
        }
        if self.inner.login_failed.swap(false, Ordering::AcqRel) {
            return Err(CodexAppServerError::LoginFailed);
        }
        let response = match self
            .rpc(
                "account/read",
                json!({ "refreshToken": false }),
                self.inner.config.limits.request_timeout,
            )
            .await
        {
            Ok(response) => response,
            Err(CodexAppServerError::AuthenticationRequired) => {
                return Ok(AccountSnapshot {
                    status: AccountStatus::ReauthRequired,
                    email: None,
                    plan_type: None,
                });
            }
            Err(
                CodexAppServerError::RuntimeUnavailable
                | CodexAppServerError::EndOfStream
                | CodexAppServerError::TimedOut,
            ) => {
                return Ok(AccountSnapshot {
                    status: AccountStatus::Unavailable,
                    email: None,
                    plan_type: None,
                });
            }
            Err(error) => return Err(error),
        };
        if self.inner.login_failed.swap(false, Ordering::AcqRel) {
            return Err(CodexAppServerError::LoginFailed);
        }
        parse_account(&response, self.pending_login()?, &self.inner.config)
    }

    pub async fn models(&self) -> Result<Vec<ModelInfo>, CodexAppServerError> {
        let mut models = Vec::new();
        let mut cursor: Option<String> = None;
        for _ in 0..MAX_MODEL_PAGES {
            let response = self
                .rpc(
                    "model/list",
                    json!({
                        "cursor": cursor,
                        "includeHidden": false,
                        "limit": 100,
                    }),
                    self.inner.config.limits.request_timeout,
                )
                .await?;
            let data = response
                .get("data")
                .and_then(Value::as_array)
                .ok_or(CodexAppServerError::MalformedProtocol)?;
            for item in data {
                if models.len() >= MAX_MODELS {
                    return Err(CodexAppServerError::ResponseTooLarge);
                }
                models.push(parse_model(item)?);
            }
            cursor = response
                .get("nextCursor")
                .and_then(Value::as_str)
                .map(str::to_owned);
            match cursor.as_deref() {
                None => return Ok(models),
                Some(value) if value.is_empty() || value.len() > MAX_IDENTIFIER_BYTES => {
                    return Err(CodexAppServerError::MalformedProtocol);
                }
                Some(_) => {}
            }
        }
        Err(CodexAppServerError::ResponseTooLarge)
    }

    pub async fn start_login(
        &self,
        method: LoginMethod,
    ) -> Result<LoginStart, CodexAppServerError> {
        if self.pending_login()?.is_some() {
            return Err(CodexAppServerError::Busy);
        }
        let params = match method {
            LoginMethod::Browser => json!({
                "type": "chatgpt",
                "useHostedLoginSuccessPage": true,
                "appBrand": "chatgpt",
            }),
            LoginMethod::DeviceCode => json!({ "type": "chatgptDeviceCode" }),
        };
        let response = self
            .rpc(
                "account/login/start",
                params,
                self.inner.config.limits.request_timeout,
            )
            .await?;
        let response_type = bounded_string(&response, "type", 64)?;
        let login_id = bounded_string(&response, "loginId", MAX_IDENTIFIER_BYTES)?;
        let start = match (method, response_type.as_str()) {
            (LoginMethod::Browser, "chatgpt") => LoginStart::Browser {
                login_id: login_id.clone(),
                auth_url: bounded_https_url(&response, "authUrl")?,
            },
            (LoginMethod::DeviceCode, "chatgptDeviceCode") => LoginStart::DeviceCode {
                login_id: login_id.clone(),
                verification_url: bounded_https_url(&response, "verificationUrl")?,
                user_code: bounded_string(&response, "userCode", 64)?,
            },
            _ => return Err(CodexAppServerError::UnsupportedAuthentication),
        };
        let mut pending = self
            .inner
            .pending_login
            .lock()
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
        *pending = Some(PendingLogin {
            login_id,
            method,
            started_at: Instant::now(),
        });
        self.inner.login_failed.store(false, Ordering::Release);
        Ok(start)
    }

    pub async fn cancel_login(&self, login_id: &str) -> Result<(), CodexAppServerError> {
        validate_identifier(login_id)?;
        if self
            .pending_login()?
            .as_ref()
            .map(|pending| pending.login_id.as_str())
            != Some(login_id)
        {
            return Err(CodexAppServerError::RequestRejected);
        }
        let response = self
            .rpc(
                "account/login/cancel",
                json!({ "loginId": login_id }),
                self.inner.config.limits.request_timeout,
            )
            .await?;
        match bounded_string(&response, "status", 32)?.as_str() {
            "canceled" => {}
            "notFound" => return Err(CodexAppServerError::RequestRejected),
            _ => return Err(CodexAppServerError::MalformedProtocol),
        }
        let mut pending = self
            .inner
            .pending_login
            .lock()
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
        if pending.as_ref().map(|value| value.login_id.as_str()) == Some(login_id) {
            *pending = None;
        }
        Ok(())
    }

    pub async fn logout(&self) -> Result<(), CodexAppServerError> {
        let _ = self
            .rpc(
                "account/logout",
                json!({}),
                self.inner.config.limits.request_timeout,
            )
            .await?;
        let mut pending = self
            .inner
            .pending_login
            .lock()
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
        *pending = None;
        self.inner.login_failed.store(false, Ordering::Release);
        Ok(())
    }

    pub async fn chat<F>(
        &self,
        request: ChatRequest,
        cancel: CancellationToken,
        mut on_event: F,
    ) -> Result<ChatResponse, CodexAppServerError>
    where
        F: FnMut(ChatEvent) -> Result<(), CodexAppServerError> + Send,
    {
        let prepared = prepare_chat(&request)?;
        let active_cancel = cancel.child_token();
        {
            let mut active = self
                .inner
                .active_cancel
                .lock()
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            if active.is_some() {
                return Err(CodexAppServerError::Busy);
            }
            *active = Some(active_cancel.clone());
        }
        let _active_guard = ActiveTurnGuard {
            inner: Arc::clone(&self.inner),
        };
        if active_cancel.is_cancelled() {
            return Err(CodexAppServerError::Interrupted);
        }

        let models = self.models().await?;
        if !models.iter().any(|model| model.id == request.model) {
            return Err(CodexAppServerError::ModelUnavailable);
        }
        if active_cancel.is_cancelled() {
            return Err(CodexAppServerError::Interrupted);
        }

        let mut slot = tokio::time::timeout(
            self.inner.config.limits.request_timeout,
            self.inner.session.lock(),
        )
        .await
        .map_err(|_| CodexAppServerError::TimedOut)?;
        let mut cancellation_guard = SessionCancellationGuard::new(Arc::clone(&self.inner));
        if let Err(error) = self.ensure_session(&mut slot).await {
            cancellation_guard.disarm();
            return Err(error);
        }
        let Some(mut session) = slot.take() else {
            cancellation_guard.disarm();
            return Err(CodexAppServerError::RuntimeUnavailable);
        };
        let mut active_turn: Option<(String, String)> = None;
        let mut terminal_seen = false;
        let outcome = async {
            let thread_response = session
            .request(
                "thread/start",
                json!({
                    "model": request.model,
                    "cwd": self.inner.config.workspace,
                    "approvalPolicy": "never",
                    "ephemeral": true,
                    "baseInstructions": GENERAL_CHAT_INSTRUCTIONS,
                    "developerInstructions": prepared.instructions,
                    "config": {
                        "approval_policy": "never",
                        "default_permissions": "wayfinder-chat",
                        "web_search": "disabled",
                        "features": {
                            "apps": false,
                            "auth_elicitation": false,
                            "browser_use": false,
                            "browser_use_external": false,
                            "browser_use_full_cdp_access": false,
                            "code_mode": false,
                            "code_mode_host": false,
                            "computer_use": false,
                            "goals": false,
                            "guardian_approval": false,
                            "hooks": false,
                            "image_generation": false,
                            "in_app_browser": false,
                            "memories": false,
                            "mentions_v2": false,
                            "multi_agent": false,
                            "remote_plugin": false,
                            "plugins": false,
                            "plugin_sharing": false,
                            "request_permissions_tool": false,
                            "shell_snapshot": false,
                            "shell_tool": false,
                            "skill_search": false,
                            "skill_mcp_dependency_install": false,
                            "tool_call_mcp_elicitation": false,
                            "unified_exec": false,
                            "network_proxy": false,
                            "workspace_dependencies": false
                        }
                    }
                }),
                self.inner.config.limits.request_timeout,
            )
            .await?;
            validate_thread_start(&thread_response, &self.inner.config, &request.model)?;
            let thread_id = nested_identifier(&thread_response, &["thread", "id"])?;

            if !prepared.history.is_empty() {
                let _ = session
                    .request(
                        "thread/inject_items",
                        json!({ "threadId": thread_id, "items": prepared.history }),
                        self.inner.config.limits.request_timeout,
                    )
                    .await?;
            }
            let turn_response = session
                .request(
                    "turn/start",
                    json!({
                        "threadId": thread_id,
                        "input": [{ "type": "text", "text": prepared.prompt }],
                        "model": request.model,
                        "cwd": self.inner.config.workspace,
                        "approvalPolicy": "never"
                    }),
                    self.inner.config.limits.request_timeout,
                )
                .await?;
            let turn_id = nested_identifier(&turn_response, &["turn", "id"])?;
            active_turn = Some((thread_id.clone(), turn_id.clone()));

            let mut delta_bytes = 0_usize;
            let mut final_content: Option<String> = None;
            let mut terminal_error: Option<CodexAppServerError> = None;
            if active_cancel.is_cancelled() {
                return Err(CodexAppServerError::Interrupted);
            }
            loop {
                let notification = tokio::select! {
                    () = active_cancel.cancelled() => {
                        return Err(CodexAppServerError::Interrupted);
                    }
                    result = session.next_notification(self.inner.config.limits.turn_idle_timeout) => result?,
                };
                self.apply_notification(&notification)?;
                match notification.method.as_str() {
                    "item/agentMessage/delta" => {
                        ensure_event_identity(&notification.params, &thread_id, &turn_id)?;
                        let delta =
                            bounded_string(&notification.params, "delta", MAX_RESPONSE_BYTES)?;
                        validate_stream_delta_size(&delta)?;
                        delta_bytes = delta_bytes
                            .checked_add(delta.len())
                            .ok_or(CodexAppServerError::ResponseTooLarge)?;
                        if delta_bytes > MAX_RESPONSE_BYTES {
                            return Err(CodexAppServerError::ResponseTooLarge);
                        }
                        on_event(ChatEvent::Delta(delta))?;
                    }
                    "item/started" | "item/completed" => {
                        ensure_event_identity(&notification.params, &thread_id, &turn_id)?;
                        let item = notification
                            .params
                            .get("item")
                            .ok_or(CodexAppServerError::MalformedProtocol)?;
                        let item_type = item
                            .get("type")
                            .and_then(Value::as_str)
                            .ok_or(CodexAppServerError::MalformedProtocol)?;
                        if !matches!(item_type, "userMessage" | "agentMessage" | "reasoning") {
                            return Err(CodexAppServerError::ForbiddenAction);
                        }
                        if notification.method == "item/completed" && item_type == "agentMessage" {
                            let text = bounded_string(item, "text", MAX_RESPONSE_BYTES)?;
                            final_content = Some(text);
                        }
                    }
                    "turn/completed" => {
                        let event_thread = bounded_string(
                            &notification.params,
                            "threadId",
                            MAX_IDENTIFIER_BYTES,
                        )?;
                        if event_thread != thread_id {
                            return Err(CodexAppServerError::CorrelationFailed);
                        }
                        let turn = notification
                            .params
                            .get("turn")
                            .ok_or(CodexAppServerError::MalformedProtocol)?;
                        let event_turn = bounded_string(turn, "id", MAX_IDENTIFIER_BYTES)?;
                        if event_turn != turn_id {
                            return Err(CodexAppServerError::CorrelationFailed);
                        }
                        terminal_seen = true;
                        return match turn.get("status").and_then(Value::as_str) {
                            Some("completed") => {
                                let content = final_content
                                    .ok_or(CodexAppServerError::MalformedProtocol)?;
                                Ok(ChatResponse {
                                    content,
                                    thread_id,
                                    turn_id,
                                })
                            }
                            Some("interrupted") => Err(CodexAppServerError::Interrupted),
                            Some("failed") => Err(
                                terminal_error.unwrap_or(CodexAppServerError::TurnFailed),
                            ),
                            _ => Err(CodexAppServerError::MalformedProtocol),
                        };
                    }
                    "error" => {
                        ensure_event_identity(&notification.params, &thread_id, &turn_id)?;
                        let _will_retry = notification
                            .params
                            .get("willRetry")
                            .and_then(Value::as_bool)
                            .ok_or(CodexAppServerError::MalformedProtocol)?;
                        let error = notification
                            .params
                            .get("error")
                            .and_then(Value::as_object)
                            .ok_or(CodexAppServerError::MalformedProtocol)?;
                        terminal_error = Some(classify_turn_error(error));
                    }
                    method if benign_notification(method) => {}
                    _ => return Err(CodexAppServerError::ForbiddenAction),
                }
            }
        }
        .await;

        if let Err(error) = &outcome {
            let already_terminal = terminal_seen;
            let confirmed = if let Some((thread_id, turn_id)) = active_turn.as_ref()
                && !already_terminal
            {
                interrupt_turn_and_confirm(
                    &mut session,
                    thread_id,
                    turn_id,
                    self.inner.config.limits.interrupt_grace,
                )
                .await
            } else {
                true
            };
            if is_fatal_error(error) || !confirmed {
                let _ = session
                    .terminate(self.inner.config.limits.shutdown_timeout)
                    .await;
            } else {
                *slot = Some(session);
            }
        } else {
            *slot = Some(session);
        }
        cancellation_guard.disarm();
        outcome
    }

    pub fn interrupt_active_turn(&self) {
        if let Ok(guard) = self.inner.active_cancel.lock()
            && let Some(cancel) = guard.as_ref()
        {
            cancel.cancel();
        }
    }

    pub async fn shutdown(&self) -> Result<(), CodexAppServerError> {
        self.interrupt_active_turn();
        let lock_timeout = self
            .inner
            .config
            .limits
            .shutdown_timeout
            .saturating_add(self.inner.config.limits.interrupt_grace);
        let mut slot = tokio::time::timeout(lock_timeout, self.inner.session.lock())
            .await
            .map_err(|_| CodexAppServerError::TimedOut)?;
        if let Some(mut session) = slot.take() {
            session
                .terminate(self.inner.config.limits.shutdown_timeout)
                .await?;
        }
        Ok(())
    }

    async fn rpc(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, CodexAppServerError> {
        let mut slot = tokio::time::timeout(
            self.inner.config.limits.request_timeout,
            self.inner.session.lock(),
        )
        .await
        .map_err(|_| CodexAppServerError::TimedOut)?;
        let mut cancellation_guard = SessionCancellationGuard::new(Arc::clone(&self.inner));
        if let Err(error) = self.ensure_session(&mut slot).await {
            cancellation_guard.disarm();
            return Err(error);
        }
        let Some(mut session) = slot.take() else {
            cancellation_guard.disarm();
            return Err(CodexAppServerError::RuntimeUnavailable);
        };
        let result = session.request(method, params, timeout).await;
        let notifications = session.drain_notifications();
        for notification in &notifications {
            if let Err(error) = self.apply_notification(notification) {
                let _ = session
                    .terminate(self.inner.config.limits.shutdown_timeout)
                    .await;
                cancellation_guard.disarm();
                return Err(error);
            }
        }
        if result.as_ref().err().is_some_and(is_fatal_error) {
            let _ = session
                .terminate(self.inner.config.limits.shutdown_timeout)
                .await;
        } else {
            *slot = Some(session);
        }
        cancellation_guard.disarm();
        result
    }

    async fn ensure_session(&self, slot: &mut Option<Session>) -> Result<(), CodexAppServerError> {
        if slot.is_none() {
            if self.inner.session_started.swap(true, Ordering::AcqRel) {
                let cancelled = self
                    .inner
                    .cancelled_session_restart
                    .swap(false, Ordering::AcqRel);
                if !cancelled {
                    let restart = self.inner.restart_count.fetch_add(1, Ordering::AcqRel);
                    if restart >= MAX_RUNTIME_RESTARTS {
                        return Err(CodexAppServerError::RuntimeUnavailable);
                    }
                }
            }
            let session = tokio::time::timeout(self.inner.config.limits.request_timeout, async {
                let transport = self.inner.factory.spawn(&self.inner.config).await?;
                Session::connect(
                    transport,
                    &self.inner.config.client_version,
                    &self.inner.config.codex_home,
                    self.inner.config.limits.request_timeout,
                )
                .await
            })
            .await
            .map_err(|_| CodexAppServerError::TimedOut)??;
            *slot = Some(session);
        }
        Ok(())
    }

    fn pending_login(&self) -> Result<Option<PendingLogin>, CodexAppServerError> {
        self.inner
            .pending_login
            .lock()
            .map(|value| value.clone())
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)
    }

    fn take_expired_login(&self) -> Result<Option<String>, CodexAppServerError> {
        let mut pending = self
            .inner
            .pending_login
            .lock()
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
        let expired = pending.as_ref().is_some_and(|login| {
            login.started_at.elapsed() >= self.inner.config.limits.login_timeout
        });
        if expired {
            Ok(pending.take().map(|login| login.login_id))
        } else {
            Ok(None)
        }
    }

    fn apply_notification(&self, notification: &Notification) -> Result<(), CodexAppServerError> {
        match notification.method.as_str() {
            "account/login/completed" => {
                let login_id = notification.params.get("loginId").and_then(Value::as_str);
                let success = notification
                    .params
                    .get("success")
                    .and_then(Value::as_bool)
                    .ok_or(CodexAppServerError::MalformedProtocol)?;
                let mut pending = self
                    .inner
                    .pending_login
                    .lock()
                    .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
                if pending.as_ref().map(|value| value.login_id.as_str()) == login_id {
                    if success {
                        verify_auth_store_permissions(&self.inner.config)?;
                    } else {
                        self.inner.login_failed.store(true, Ordering::Release);
                    }
                    *pending = None;
                }
            }
            "account/updated" => match notification.params.get("authMode") {
                Some(Value::String(mode)) if mode == "chatgpt" => {
                    verify_auth_store_permissions(&self.inner.config)?;
                    let mut pending = self
                        .inner
                        .pending_login
                        .lock()
                        .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
                    *pending = None;
                }
                Some(Value::Null) => {}
                Some(Value::String(_)) => {
                    return Err(CodexAppServerError::UnsupportedAuthentication);
                }
                Some(_) | None => return Err(CodexAppServerError::MalformedProtocol),
            },
            _ => {}
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemProcessFactory;

impl AppServerTransportFactory for SystemProcessFactory {
    fn spawn<'a>(
        &'a self,
        config: &'a RuntimeConfig,
    ) -> TransportFuture<'a, Box<dyn AppServerTransport>> {
        process::spawn_process(config)
    }
}

fn validate_config(config: &RuntimeConfig) -> Result<(), CodexAppServerError> {
    if config.helper_path.as_os_str().is_empty()
        || config.codex_home.as_os_str().is_empty()
        || config.workspace.as_os_str().is_empty()
        || !config.helper_path.is_absolute()
        || !config.codex_home.is_absolute()
        || !config.workspace.is_absolute()
        || config
            .codex_home
            .components()
            .any(|component| component == Component::ParentDir)
        || config
            .workspace
            .components()
            .any(|component| component == Component::ParentDir)
        || config.workspace == config.codex_home
        || !config.workspace.starts_with(&config.codex_home)
        || config.client_version.is_empty()
        || config.client_version.len() > 64
        || config.client_version.chars().any(char::is_control)
        || config.limits.request_timeout.is_zero()
        || config.limits.request_timeout > Duration::from_secs(60)
        || config.limits.login_timeout.is_zero()
        || config.limits.login_timeout > Duration::from_secs(600)
        || config.limits.turn_idle_timeout.is_zero()
        || config.limits.turn_idle_timeout > Duration::from_secs(300)
        || config.limits.interrupt_grace.is_zero()
        || config.limits.interrupt_grace > Duration::from_secs(10)
        || config.limits.shutdown_timeout.is_zero()
        || config.limits.shutdown_timeout > Duration::from_secs(10)
    {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    Ok(())
}

struct ActiveTurnGuard {
    inner: Arc<ManagerInner>,
}

impl Drop for ActiveTurnGuard {
    fn drop(&mut self) {
        if let Ok(mut active) = self.inner.active_cancel.lock() {
            *active = None;
        }
    }
}

struct SessionCancellationGuard {
    inner: Arc<ManagerInner>,
    armed: bool,
}

impl SessionCancellationGuard {
    fn new(inner: Arc<ManagerInner>) -> Self {
        Self { inner, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for SessionCancellationGuard {
    fn drop(&mut self) {
        if self.armed {
            self.inner
                .cancelled_session_restart
                .store(true, Ordering::Release);
        }
    }
}

struct PreparedChat {
    instructions: String,
    history: Vec<Value>,
    prompt: String,
}

fn prepare_chat(request: &ChatRequest) -> Result<PreparedChat, CodexAppServerError> {
    validate_identifier(&request.model)?;
    if request.messages.is_empty() || request.messages.len() > 128 {
        return Err(CodexAppServerError::InvalidRequest);
    }
    if request.messages.last().map(|message| message.role) != Some(ChatRole::User) {
        return Err(CodexAppServerError::InvalidRequest);
    }

    let mut transcript_bytes = 0_usize;
    let mut instructions = String::new();
    let mut history = Vec::new();
    let last_index = request.messages.len() - 1;
    for (index, message) in request.messages.iter().enumerate() {
        if message.content.is_empty() {
            return Err(CodexAppServerError::InvalidRequest);
        }
        transcript_bytes = transcript_bytes
            .checked_add(message.content.len())
            .ok_or(CodexAppServerError::RequestTooLarge)?;
        if transcript_bytes > MAX_TRANSCRIPT_BYTES {
            return Err(CodexAppServerError::RequestTooLarge);
        }
        if index == last_index {
            continue;
        }
        match message.role {
            ChatRole::System | ChatRole::Developer => {
                if !instructions.is_empty() {
                    instructions.push_str("\n\n");
                }
                instructions.push_str(match message.role {
                    ChatRole::System => "System context:\n",
                    ChatRole::Developer => "Developer context:\n",
                    ChatRole::User | ChatRole::Assistant => {
                        return Err(CodexAppServerError::InvalidRequest);
                    }
                });
                instructions.push_str(&message.content);
                if instructions.len() > MAX_INSTRUCTIONS_BYTES {
                    return Err(CodexAppServerError::RequestTooLarge);
                }
            }
            ChatRole::User => history.push(json!({
                "type": "message",
                "role": "user",
                "content": [{ "type": "input_text", "text": message.content }]
            })),
            ChatRole::Assistant => history.push(json!({
                "type": "message",
                "role": "assistant",
                "content": [{ "type": "output_text", "text": message.content }]
            })),
        }
    }
    let prompt = request
        .messages
        .last()
        .map(|message| message.content.clone())
        .ok_or(CodexAppServerError::InvalidRequest)?;
    Ok(PreparedChat {
        instructions,
        history,
        prompt,
    })
}

fn parse_account(
    response: &Value,
    pending: Option<PendingLogin>,
    config: &RuntimeConfig,
) -> Result<AccountSnapshot, CodexAppServerError> {
    match response.get("requiresOpenaiAuth").and_then(Value::as_bool) {
        Some(true) => {}
        Some(false) => return Err(CodexAppServerError::UnsupportedAuthentication),
        None => return Err(CodexAppServerError::MalformedProtocol),
    }
    match response.get("account") {
        Some(Value::Null) | None => {
            let status = match pending.map(|value| value.method) {
                Some(LoginMethod::Browser) => AccountStatus::AwaitingBrowser,
                Some(LoginMethod::DeviceCode) => AccountStatus::AwaitingDeviceCode,
                None => AccountStatus::SignedOut,
            };
            Ok(AccountSnapshot {
                status,
                email: None,
                plan_type: None,
            })
        }
        Some(account) => {
            let account_type = bounded_string(account, "type", 64)?;
            if account_type != "chatgpt" {
                return Err(CodexAppServerError::UnsupportedAuthentication);
            }
            verify_auth_store_permissions(config)?;
            let email = bounded_optional_string(account, "email", 320)?;
            let plan_type = Some(bounded_string(account, "planType", 128)?);
            Ok(AccountSnapshot {
                status: AccountStatus::Connected,
                email,
                plan_type,
            })
        }
    }
}

fn parse_model(value: &Value) -> Result<ModelInfo, CodexAppServerError> {
    let model = value
        .get("model")
        .and_then(Value::as_str)
        .filter(|model| !model.is_empty())
        .or_else(|| value.get("id").and_then(Value::as_str))
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if model.len() > MAX_IDENTIFIER_BYTES {
        return Err(CodexAppServerError::ResponseTooLarge);
    }
    Ok(ModelInfo {
        id: model.to_owned(),
        display_name: bounded_string(value, "displayName", 160)?,
        description: bounded_string(value, "description", 1_024)?,
        is_default: value
            .get("isDefault")
            .and_then(Value::as_bool)
            .ok_or(CodexAppServerError::MalformedProtocol)?,
        hidden: value
            .get("hidden")
            .and_then(Value::as_bool)
            .ok_or(CodexAppServerError::MalformedProtocol)?,
    })
}

fn validate_thread_start(
    response: &Value,
    config: &RuntimeConfig,
    requested_model: &str,
) -> Result<(), CodexAppServerError> {
    if response.get("approvalPolicy").and_then(Value::as_str) != Some("never")
        || response.get("model").and_then(Value::as_str) != Some(requested_model)
        || response.get("modelProvider").and_then(Value::as_str) != Some("openai")
    {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let thread = response
        .get("thread")
        .and_then(Value::as_object)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if thread.get("ephemeral").and_then(Value::as_bool) != Some(true) {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let returned_cwd = response
        .get("cwd")
        .and_then(Value::as_str)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let returned_cwd = std::fs::canonicalize(returned_cwd)
        .map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    let expected_cwd = std::fs::canonicalize(&config.workspace)
        .map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    if returned_cwd != expected_cwd {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let profile = response
        .get("activePermissionProfile")
        .and_then(Value::as_object)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if profile.get("id").and_then(Value::as_str) != Some("wayfinder-chat")
        || !profile.get("extends").is_none_or(Value::is_null)
    {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let roots = response
        .get("runtimeWorkspaceRoots")
        .and_then(Value::as_array)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if roots.len() != 1 {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    let root = roots[0]
        .as_str()
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    let root =
        std::fs::canonicalize(root).map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    if root != expected_cwd {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let sandbox = response
        .get("sandbox")
        .and_then(Value::as_object)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if sandbox.get("type").and_then(Value::as_str) != Some("readOnly")
        || sandbox.get("networkAccess").and_then(Value::as_bool) != Some(false)
    {
        return Err(CodexAppServerError::InvalidConfiguration);
    }

    let instruction_sources = response
        .get("instructionSources")
        .and_then(Value::as_array)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if !instruction_sources.is_empty() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    Ok(())
}

fn bounded_string(
    value: &Value,
    field: &str,
    maximum: usize,
) -> Result<String, CodexAppServerError> {
    let string = value
        .get(field)
        .and_then(Value::as_str)
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    if string.is_empty() || string.len() > maximum {
        return Err(CodexAppServerError::ResponseTooLarge);
    }
    Ok(string.to_owned())
}

fn bounded_optional_string(
    value: &Value,
    field: &str,
    maximum: usize,
) -> Result<Option<String>, CodexAppServerError> {
    match value.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(string)) if string.len() <= maximum => Ok(Some(string.clone())),
        Some(Value::String(_)) => Err(CodexAppServerError::ResponseTooLarge),
        Some(_) => Err(CodexAppServerError::MalformedProtocol),
    }
}

fn bounded_https_url(value: &Value, field: &str) -> Result<String, CodexAppServerError> {
    let url = bounded_string(value, field, 2_048)?;
    if !url.starts_with("https://") {
        return Err(CodexAppServerError::MalformedProtocol);
    }
    Ok(url)
}

fn validate_stream_delta_size(delta: &str) -> Result<(), CodexAppServerError> {
    let encoded = serde_json::to_vec(&json!({ "delta": delta }))
        .map_err(|_| CodexAppServerError::MalformedProtocol)?;
    if encoded.len() > MAX_STREAM_DELTA_ESCAPED_BYTES {
        return Err(CodexAppServerError::ResponseTooLarge);
    }
    Ok(())
}

fn nested_identifier(value: &Value, path: &[&str]) -> Result<String, CodexAppServerError> {
    let mut current = value;
    for part in path {
        current = current
            .get(*part)
            .ok_or(CodexAppServerError::MalformedProtocol)?;
    }
    let identifier = current
        .as_str()
        .ok_or(CodexAppServerError::MalformedProtocol)?;
    validate_identifier(identifier)?;
    Ok(identifier.to_owned())
}

fn validate_identifier(identifier: &str) -> Result<(), CodexAppServerError> {
    if identifier.is_empty()
        || identifier.len() > MAX_IDENTIFIER_BYTES
        || identifier.chars().any(char::is_control)
    {
        return Err(CodexAppServerError::InvalidRequest);
    }
    Ok(())
}

fn ensure_event_identity(
    params: &Value,
    thread_id: &str,
    turn_id: &str,
) -> Result<(), CodexAppServerError> {
    let event_thread = bounded_string(params, "threadId", MAX_IDENTIFIER_BYTES)?;
    let event_turn = bounded_string(params, "turnId", MAX_IDENTIFIER_BYTES)?;
    if event_thread != thread_id || event_turn != turn_id {
        return Err(CodexAppServerError::CorrelationFailed);
    }
    Ok(())
}

fn benign_notification(method: &str) -> bool {
    matches!(
        method,
        "thread/started"
            | "turn/started"
            | "thread/tokenUsage/updated"
            | "account/login/completed"
            | "account/updated"
            | "account/rateLimits/updated"
    )
}

fn classify_turn_error(error: &serde_json::Map<String, Value>) -> CodexAppServerError {
    match error.get("codexErrorInfo").and_then(Value::as_str) {
        Some("unauthorized") => CodexAppServerError::AuthenticationRequired,
        Some("usageLimitExceeded") | Some("sessionBudgetExceeded") => {
            CodexAppServerError::UsageLimitReached
        }
        _ => CodexAppServerError::TurnFailed,
    }
}

async fn interrupt_turn_and_confirm(
    session: &mut Session,
    thread_id: &str,
    turn_id: &str,
    grace: Duration,
) -> bool {
    let result = tokio::time::timeout(grace, async {
        let _ = session
            .request(
                "turn/interrupt",
                json!({ "threadId": thread_id, "turnId": turn_id }),
                grace,
            )
            .await?;
        loop {
            let notification = session.next_notification(grace).await?;
            if notification.method != "turn/completed" {
                continue;
            }
            let event_thread =
                bounded_string(&notification.params, "threadId", MAX_IDENTIFIER_BYTES)?;
            let turn = notification
                .params
                .get("turn")
                .ok_or(CodexAppServerError::MalformedProtocol)?;
            let event_turn = bounded_string(turn, "id", MAX_IDENTIFIER_BYTES)?;
            let status = turn
                .get("status")
                .and_then(Value::as_str)
                .ok_or(CodexAppServerError::MalformedProtocol)?;
            if event_thread == thread_id && event_turn == turn_id && status == "interrupted" {
                return Ok::<(), CodexAppServerError>(());
            }
        }
    })
    .await;
    matches!(result, Ok(Ok(())))
}

fn is_fatal_error(error: &CodexAppServerError) -> bool {
    matches!(
        error,
        CodexAppServerError::InvalidConfiguration
            | CodexAppServerError::RuntimeUnavailable
            | CodexAppServerError::MalformedProtocol
            | CodexAppServerError::LineTooLarge
            | CodexAppServerError::ResponseTooLarge
            | CodexAppServerError::CorrelationFailed
            | CodexAppServerError::NotificationQueueFull
            | CodexAppServerError::TimedOut
            | CodexAppServerError::ForbiddenAction
            | CodexAppServerError::UnsupportedAuthentication
            | CodexAppServerError::EndOfStream
            | CodexAppServerError::InsecureCredentialStore
    )
}

#[cfg(unix)]
fn verify_auth_store_permissions(config: &RuntimeConfig) -> Result<(), CodexAppServerError> {
    let expected_owner =
        process::effective_owner().map_err(|_| CodexAppServerError::InsecureCredentialStore)?;
    let home_metadata = std::fs::symlink_metadata(&config.codex_home)
        .map_err(|_| CodexAppServerError::InsecureCredentialStore)?;
    let auth_file = config.codex_home.join("auth.json");
    let auth_metadata = std::fs::symlink_metadata(&auth_file)
        .map_err(|_| CodexAppServerError::InsecureCredentialStore)?;
    verify_auth_store_metadata(&home_metadata, &auth_metadata, expected_owner)?;
    verify_auth_path_has_no_acl(&config.codex_home)?;
    verify_auth_path_has_no_acl(&auth_file)?;
    Ok(())
}

#[cfg(unix)]
fn verify_auth_store_metadata(
    home_metadata: &std::fs::Metadata,
    auth_metadata: &std::fs::Metadata,
    expected_owner: u32,
) -> Result<(), CodexAppServerError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    if !home_metadata.is_dir()
        || home_metadata.file_type().is_symlink()
        || home_metadata.permissions().mode() & 0o777 != 0o700
        || home_metadata.uid() != expected_owner
    {
        return Err(CodexAppServerError::InsecureCredentialStore);
    }
    if !auth_metadata.is_file()
        || auth_metadata.file_type().is_symlink()
        || auth_metadata.permissions().mode() & 0o777 != 0o600
        || auth_metadata.nlink() != 1
        || auth_metadata.uid() != expected_owner
    {
        return Err(CodexAppServerError::InsecureCredentialStore);
    }
    Ok(())
}

#[cfg(not(unix))]
fn verify_auth_store_permissions(_config: &RuntimeConfig) -> Result<(), CodexAppServerError> {
    Ok(())
}

#[cfg(target_os = "macos")]
fn verify_auth_path_has_no_acl(path: &std::path::Path) -> Result<(), CodexAppServerError> {
    let output = StdCommand::new("/bin/ls")
        .arg("-lde")
        .arg(path)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| CodexAppServerError::InsecureCredentialStore)?;
    let has_acl = String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .next()
        .is_none_or(|token| token.contains('+'));
    if !output.status.success() || has_acl {
        return Err(CodexAppServerError::InsecureCredentialStore);
    }
    Ok(())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn verify_auth_path_has_no_acl(_path: &std::path::Path) -> Result<(), CodexAppServerError> {
    Ok(())
}
