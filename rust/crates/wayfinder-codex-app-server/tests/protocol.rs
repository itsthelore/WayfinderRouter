use std::collections::VecDeque;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use tokio_util::sync::CancellationToken;
use wayfinder_codex_app_server::{
    AccountStatus, AppServerTransport, AppServerTransportFactory, ChatEvent, ChatMessage,
    ChatRequest, ChatRole, CodexAppServerError, CodexAppServerManager, LoginMethod, LoginStart,
    MAX_JSONL_LINE_BYTES, MAX_NOTIFICATION_QUEUE, MAX_RUNTIME_RESTARTS, RuntimeConfig,
    RuntimeLimits, TransportFuture,
};

static TEST_COUNTER: AtomicUsize = AtomicUsize::new(0);
const WORKSPACE_MARKER: &str = "__WAYFINDER_TEST_WORKSPACE__";

type TestResult = Result<(), Box<dyn std::error::Error>>;
type ManagerHarness = (
    CodexAppServerManager,
    Arc<Mutex<Vec<Vec<u8>>>>,
    RuntimeConfig,
);
type TerminatingManagerHarness = (
    CodexAppServerManager,
    Arc<Mutex<Vec<Vec<u8>>>>,
    RuntimeConfig,
    Arc<Mutex<bool>>,
);

#[derive(Clone)]
struct ScriptedFactory {
    transport: Arc<Mutex<Option<ScriptedTransport>>>,
}

#[derive(Clone)]
struct RestartingFactory {
    spawns: Arc<AtomicUsize>,
}

#[derive(Clone, Copy)]
struct HangingFactory;

impl AppServerTransportFactory for ScriptedFactory {
    fn spawn<'a>(
        &'a self,
        _config: &'a RuntimeConfig,
    ) -> TransportFuture<'a, Box<dyn AppServerTransport>> {
        Box::pin(async move {
            let mut transport = self
                .transport
                .lock()
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            transport
                .take()
                .map(|transport| Box::new(transport) as Box<dyn AppServerTransport>)
                .ok_or(CodexAppServerError::RuntimeUnavailable)
        })
    }
}

impl AppServerTransportFactory for RestartingFactory {
    fn spawn<'a>(
        &'a self,
        config: &'a RuntimeConfig,
    ) -> TransportFuture<'a, Box<dyn AppServerTransport>> {
        Box::pin(async move {
            self.spawns.fetch_add(1, Ordering::Relaxed);
            Ok(Box::new(ScriptedTransport {
                reads: VecDeque::from([initialize_response(config)]),
                interrupt_reads: VecDeque::new(),
                writes: Arc::new(Mutex::new(Vec::new())),
                terminated: Arc::new(Mutex::new(false)),
                block_when_empty: false,
            }) as Box<dyn AppServerTransport>)
        })
    }
}

impl AppServerTransportFactory for HangingFactory {
    fn spawn<'a>(
        &'a self,
        _config: &'a RuntimeConfig,
    ) -> TransportFuture<'a, Box<dyn AppServerTransport>> {
        Box::pin(std::future::pending())
    }
}

struct ScriptedTransport {
    reads: VecDeque<Vec<u8>>,
    interrupt_reads: VecDeque<Vec<u8>>,
    writes: Arc<Mutex<Vec<Vec<u8>>>>,
    terminated: Arc<Mutex<bool>>,
    block_when_empty: bool,
}

impl AppServerTransport for ScriptedTransport {
    fn write<'a>(&'a mut self, bytes: &'a [u8]) -> TransportFuture<'a, ()> {
        Box::pin(async move {
            {
                self.writes
                    .lock()
                    .map_err(|_| CodexAppServerError::RuntimeUnavailable)?
                    .push(bytes.to_vec());
            }
            if serde_json::from_slice::<Value>(bytes)
                .ok()
                .and_then(|value| {
                    value
                        .get("method")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .as_deref()
                == Some("turn/interrupt")
            {
                self.reads.append(&mut self.interrupt_reads);
            }
            Ok(())
        })
    }

    fn read<'a>(&'a mut self, buffer: &'a mut [u8]) -> TransportFuture<'a, usize> {
        Box::pin(async move {
            let Some(mut bytes) = self.reads.pop_front() else {
                if self.block_when_empty {
                    std::future::pending::<()>().await;
                }
                return Ok(0);
            };
            let count = bytes.len().min(buffer.len());
            buffer[..count].copy_from_slice(&bytes[..count]);
            if count < bytes.len() {
                let remainder = bytes.split_off(count);
                self.reads.push_front(remainder);
            }
            Ok(count)
        })
    }

    fn terminate<'a>(&'a mut self) -> TransportFuture<'a, ()> {
        Box::pin(async move {
            *self
                .terminated
                .lock()
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)? = true;
            Ok(())
        })
    }
}

fn manager_with(reads: Vec<Vec<u8>>) -> Result<ManagerHarness, Box<dyn std::error::Error>> {
    let (manager, writes, config, _) = manager_with_termination(reads)?;
    Ok((manager, writes, config))
}

fn manager_with_termination(
    reads: Vec<Vec<u8>>,
) -> Result<TerminatingManagerHarness, Box<dyn std::error::Error>> {
    manager_with_transport(reads, false)
}

fn manager_with_transport(
    reads: Vec<Vec<u8>>,
    block_when_empty: bool,
) -> Result<TerminatingManagerHarness, Box<dyn std::error::Error>> {
    manager_with_interrupt_transport(reads, Vec::new(), block_when_empty)
}

fn manager_with_interrupt_transport(
    reads: Vec<Vec<u8>>,
    interrupt_reads: Vec<Vec<u8>>,
    block_when_empty: bool,
) -> Result<TerminatingManagerHarness, Box<dyn std::error::Error>> {
    let mut config = test_config()?;
    if block_when_empty {
        config.limits.turn_idle_timeout = Duration::from_secs(5);
    }
    let mut all_reads = vec![initialize_response(&config)];
    all_reads.extend(reads);
    let workspace = config.workspace.to_string_lossy();
    for read in &mut all_reads {
        replace_marker(read, WORKSPACE_MARKER.as_bytes(), workspace.as_bytes());
    }
    let writes = Arc::new(Mutex::new(Vec::new()));
    let terminated = Arc::new(Mutex::new(false));
    let factory = ScriptedFactory {
        transport: Arc::new(Mutex::new(Some(ScriptedTransport {
            reads: all_reads.into(),
            interrupt_reads: interrupt_reads.into(),
            writes: Arc::clone(&writes),
            terminated: Arc::clone(&terminated),
            block_when_empty,
        }))),
    };
    let manager = CodexAppServerManager::with_factory(config.clone(), Arc::new(factory))?;
    Ok((manager, writes, config, terminated))
}

fn test_config() -> Result<RuntimeConfig, Box<dyn std::error::Error>> {
    let number = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!(
        "wayfinder-codex-app-server-test-{}-{number}",
        std::process::id()
    ));
    let codex_home = root.join("codex-home");
    let workspace = codex_home.join("workspace");
    fs::create_dir_all(&codex_home)?;
    fs::create_dir_all(&workspace)?;
    fs::write(codex_home.join("auth.json"), b"{}")?;
    make_private(&codex_home)?;
    make_private(&workspace)?;
    make_private_file(&codex_home.join("auth.json"))?;
    Ok(RuntimeConfig {
        helper_path: PathBuf::from("/usr/bin/false"),
        codex_home,
        workspace,
        client_version: "0.1.0-test".to_owned(),
        limits: RuntimeLimits {
            request_timeout: Duration::from_millis(100),
            login_timeout: Duration::from_millis(100),
            turn_idle_timeout: Duration::from_millis(100),
            interrupt_grace: Duration::from_millis(100),
            shutdown_timeout: Duration::from_millis(100),
        },
    })
}

#[cfg(unix)]
fn make_private(path: &std::path::Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn make_private(_path: &std::path::Path) -> io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn make_private_file(path: &std::path::Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn make_private_file(_path: &std::path::Path) -> io::Result<()> {
    Ok(())
}

fn frame(value: Value) -> Vec<u8> {
    let mut bytes = value.to_string().into_bytes();
    bytes.push(b'\n');
    bytes
}

fn initialize_response(config: &RuntimeConfig) -> Vec<u8> {
    frame(json!({ "id": 1, "result": {
        "codexHome": config.codex_home,
        "platformFamily": if cfg!(target_os = "windows") { "windows" } else { "unix" },
        "platformOs": std::env::consts::OS,
        "userAgent": "wayfinder/0.145.0-alpha.18 (managed app-server)"
    } }))
}

fn model_response(id: u64) -> Vec<u8> {
    frame(json!({
        "id": id,
        "result": {
            "data": [{
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "displayName": "GPT-5.6 Sol",
                "description": "Frontier Codex model",
                "isDefault": true,
                "hidden": false
            }],
            "nextCursor": null
        }
    }))
}

fn thread_response(id: u64, thread_id: &str) -> Vec<u8> {
    frame(json!({ "id": id, "result": {
        "thread": { "id": thread_id, "ephemeral": true },
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": WORKSPACE_MARKER,
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "sandbox": { "type": "readOnly", "networkAccess": false },
        "activePermissionProfile": { "id": "wayfinder-chat", "extends": null },
        "runtimeWorkspaceRoots": [WORKSPACE_MARKER],
        "instructionSources": []
    } }))
}

fn replace_marker(bytes: &mut Vec<u8>, marker: &[u8], replacement: &[u8]) {
    while let Some(position) = bytes
        .windows(marker.len())
        .position(|window| window == marker)
    {
        bytes.splice(
            position..position + marker.len(),
            replacement.iter().copied(),
        );
    }
}

fn parse_writes(
    writes: &Arc<Mutex<Vec<Vec<u8>>>>,
) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
    let writes = writes.lock().map_err(|_| io::Error::other("write lock"))?;
    writes
        .iter()
        .map(|write| serde_json::from_slice(write).map_err(Into::into))
        .collect()
}

async fn wait_for_writes(
    writes: &Arc<Mutex<Vec<Vec<u8>>>>,
    expected: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            let count = writes
                .lock()
                .map_err(|_| io::Error::other("write lock"))?
                .len();
            if count >= expected {
                return Ok::<(), io::Error>(());
            }
            tokio::task::yield_now().await;
        }
    })
    .await??;
    Ok(())
}

#[tokio::test]
async fn fragmented_and_multiple_frames_remain_ordered() -> TestResult {
    let response = model_response(2);
    let split = 7;
    let mut second = response[split..].to_vec();
    second.extend(frame(json!({
        "method": "account/rateLimits/updated",
        "params": { "rateLimits": null }
    })));
    let (manager, writes, _) = manager_with(vec![response[..split].to_vec(), second])?;

    let models = manager.models().await?;

    assert_eq!(models.len(), 1);
    assert_eq!(models[0].id, "gpt-5.6-sol");
    let writes = parse_writes(&writes)?;
    assert_eq!(writes[0]["method"], "initialize");
    assert_eq!(writes[1]["method"], "initialized");
    assert_eq!(writes[2]["method"], "model/list");
    Ok(())
}

#[tokio::test]
async fn correlates_ids_across_interleaved_notifications() -> TestResult {
    let (manager, _, _) = manager_with(vec![
        frame(
            json!({ "method": "account/updated", "params": { "authMode": null, "planType": null } }),
        ),
        frame(json!({ "id": 2, "result": { "account": null, "requiresOpenaiAuth": true } })),
    ])?;

    let account = manager.account().await?;

    assert_eq!(account.status, AccountStatus::SignedOut);
    Ok(())
}

#[tokio::test]
async fn rejects_a_mismatched_response_id() -> TestResult {
    let (manager, _, _) = manager_with(vec![frame(
        json!({ "id": 99, "result": { "data": [], "nextCursor": null } }),
    )])?;

    let result = manager.models().await;

    assert_eq!(result, Err(CodexAppServerError::CorrelationFailed));
    Ok(())
}

#[tokio::test]
async fn exposes_browser_pending_then_cancels_login() -> TestResult {
    let (manager, writes, _) = manager_with(vec![
        frame(json!({ "id": 2, "result": {
            "type": "chatgpt", "loginId": "login-1", "authUrl": "https://chatgpt.com/login"
        } })),
        frame(json!({ "id": 3, "result": { "account": null, "requiresOpenaiAuth": true } })),
        frame(json!({ "id": 4, "result": { "status": "canceled" } })),
        frame(json!({ "id": 5, "result": { "account": null, "requiresOpenaiAuth": true } })),
    ])?;

    let login = manager.start_login(LoginMethod::Browser).await?;
    assert_eq!(
        login,
        LoginStart::Browser {
            login_id: "login-1".to_owned(),
            auth_url: "https://chatgpt.com/login".to_owned(),
        }
    );
    assert_eq!(
        manager.account().await?.status,
        AccountStatus::AwaitingBrowser
    );
    manager.cancel_login("login-1").await?;
    assert_eq!(manager.account().await?.status, AccountStatus::SignedOut);

    let writes = parse_writes(&writes)?;
    assert_eq!(writes[2]["params"]["type"], "chatgpt");
    assert!(
        writes
            .iter()
            .all(|write| write.to_string().find("chatgptAuthTokens").is_none())
    );
    Ok(())
}

#[tokio::test]
async fn login_cancellation_requires_the_current_id_and_confirmed_status() -> TestResult {
    let (manager, writes, _) = manager_with(vec![
        frame(json!({ "id": 2, "result": {
            "type": "chatgpt", "loginId": "login-current",
            "authUrl": "https://chatgpt.com/login"
        } })),
        frame(json!({ "id": 3, "result": { "status": "notFound" } })),
        frame(json!({ "id": 4, "result": {
            "account": null, "requiresOpenaiAuth": true
        } })),
    ])?;
    let _ = manager.start_login(LoginMethod::Browser).await?;

    assert_eq!(
        manager.cancel_login("login-stale").await,
        Err(CodexAppServerError::RequestRejected)
    );
    assert_eq!(
        manager.cancel_login("login-current").await,
        Err(CodexAppServerError::RequestRejected)
    );
    assert_eq!(
        manager.account().await?.status,
        AccountStatus::AwaitingBrowser
    );
    let writes = parse_writes(&writes)?;
    let cancel_requests = writes
        .iter()
        .filter(|write| write["method"] == "account/login/cancel")
        .collect::<Vec<_>>();
    assert_eq!(cancel_requests.len(), 1);
    assert_eq!(cancel_requests[0]["params"]["loginId"], "login-current");
    Ok(())
}

#[tokio::test]
async fn exposes_connected_chatgpt_account() -> TestResult {
    let (manager, _, _) = manager_with(vec![frame(json!({ "id": 2, "result": {
            "account": { "type": "chatgpt", "email": "user@example.com", "planType": "pro" },
            "requiresOpenaiAuth": true
        } }))])?;

    let account = manager.account().await?;

    assert_eq!(account.status, AccountStatus::Connected);
    assert_eq!(account.email.as_deref(), Some("user@example.com"));
    assert_eq!(account.plan_type.as_deref(), Some("pro"));
    Ok(())
}

#[cfg(unix)]
#[tokio::test]
async fn connected_account_rejects_a_hard_linked_auth_store() -> TestResult {
    let (manager, _, config) = manager_with(vec![frame(json!({ "id": 2, "result": {
            "account": { "type": "chatgpt", "email": "user@example.com", "planType": "pro" },
            "requiresOpenaiAuth": true
        } }))])?;
    fs::hard_link(
        config.codex_home.join("auth.json"),
        config.codex_home.join("auth-copy.json"),
    )?;

    assert_eq!(
        manager.account().await,
        Err(CodexAppServerError::InsecureCredentialStore)
    );
    Ok(())
}

#[tokio::test]
async fn account_contract_rejects_non_openai_or_unknown_auth_modes() -> TestResult {
    let (account, _, _) = manager_with(vec![frame(json!({ "id": 2, "result": {
        "account": null,
        "requiresOpenaiAuth": false
    } }))])?;
    assert_eq!(
        account.account().await,
        Err(CodexAppServerError::UnsupportedAuthentication)
    );

    let (notification, _, _) = manager_with(vec![
        frame(json!({ "method": "account/updated", "params": {
            "authMode": "externalTokens"
        } })),
        frame(json!({ "id": 2, "result": {
            "account": null,
            "requiresOpenaiAuth": true
        } })),
    ])?;
    assert_eq!(
        notification.account().await,
        Err(CodexAppServerError::UnsupportedAuthentication)
    );
    Ok(())
}

#[tokio::test]
async fn failed_login_notification_is_not_treated_as_success() -> TestResult {
    let (manager, _, _) = manager_with(vec![
        frame(json!({ "id": 2, "result": {
            "type": "chatgptDeviceCode", "loginId": "login-2",
            "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "ABCD-1234"
        } })),
        frame(json!({ "method": "account/login/completed", "params": {
            "loginId": "login-2", "success": false, "error": "redacted upstream error"
        } })),
        frame(json!({ "id": 3, "result": { "account": null, "requiresOpenaiAuth": true } })),
    ])?;
    let _ = manager.start_login(LoginMethod::DeviceCode).await?;

    let result = manager.account().await;

    assert_eq!(result, Err(CodexAppServerError::LoginFailed));
    Ok(())
}

#[tokio::test]
async fn malformed_and_oversized_lines_fail_safely() -> TestResult {
    let (malformed, _, _) = manager_with(vec![b"not-json\n".to_vec()])?;
    assert_eq!(
        malformed.models().await,
        Err(CodexAppServerError::MalformedProtocol)
    );

    let oversized_line = vec![b'x'; MAX_JSONL_LINE_BYTES + 1];
    let (oversized, _, _) = manager_with(vec![oversized_line])?;
    assert_eq!(
        oversized.models().await,
        Err(CodexAppServerError::LineTooLarge)
    );
    Ok(())
}

#[tokio::test]
async fn notification_queue_has_a_hard_bound() -> TestResult {
    let mut reads = Vec::new();
    for _ in 0..=MAX_NOTIFICATION_QUEUE {
        reads.push(frame(json!({
            "method": "account/rateLimits/updated",
            "params": { "rateLimits": null }
        })));
    }
    reads.push(model_response(2));
    let (manager, _, _) = manager_with(reads)?;

    let result = manager.models().await;

    assert_eq!(result, Err(CodexAppServerError::NotificationQueueFull));
    Ok(())
}

#[tokio::test]
async fn server_requests_are_denied_and_fail_closed() -> TestResult {
    let (manager, writes, _) = manager_with(vec![frame(json!({
        "id": 55,
        "method": "tool/call",
        "params": { "secret": "must-not-be-reflected" }
    }))])?;

    assert_eq!(
        manager.models().await,
        Err(CodexAppServerError::ForbiddenAction)
    );
    let writes = parse_writes(&writes)?;
    let denial = writes
        .last()
        .ok_or_else(|| io::Error::other("missing server-request denial"))?;
    assert_eq!(denial["id"], 55);
    assert_eq!(denial["error"]["code"], -32601);
    assert!(denial.to_string().find("must-not-be-reflected").is_none());
    Ok(())
}

#[tokio::test]
async fn chat_injects_history_streams_ordered_deltas_and_uses_completed_text() -> TestResult {
    let (manager, writes, _) = manager_with(vec![
        model_response(2),
        thread_response(3, "thread-1"),
        frame(json!({ "id": 4, "result": {} })),
        frame(json!({ "id": 5, "result": { "turn": {
            "id": "turn-1", "status": "inProgress", "items": [], "error": null
        } } })),
        frame(json!({ "method": "item/agentMessage/delta", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "Hel"
        } })),
        frame(json!({ "method": "item/agentMessage/delta", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "lo"
        } })),
        frame(json!({ "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 1,
            "item": { "id": "item-1", "type": "agentMessage", "text": "Hello, authoritative." }
        } })),
        frame(json!({ "method": "turn/completed", "params": {
            "threadId": "thread-1", "turn": { "id": "turn-1", "status": "completed", "items": [] }
        } })),
    ])?;
    let deltas = Arc::new(Mutex::new(Vec::new()));
    let captured = Arc::clone(&deltas);

    let response = manager
        .chat(
            ChatRequest {
                model: "gpt-5.6-sol".to_owned(),
                messages: vec![
                    ChatMessage {
                        role: ChatRole::System,
                        content: "Be concise.".to_owned(),
                    },
                    ChatMessage {
                        role: ChatRole::User,
                        content: "Earlier question".to_owned(),
                    },
                    ChatMessage {
                        role: ChatRole::Assistant,
                        content: "Earlier answer".to_owned(),
                    },
                    ChatMessage {
                        role: ChatRole::User,
                        content: "Current question".to_owned(),
                    },
                ],
            },
            CancellationToken::new(),
            move |event| {
                let ChatEvent::Delta(delta) = event;
                captured
                    .lock()
                    .map_err(|_| CodexAppServerError::RuntimeUnavailable)?
                    .push(delta);
                Ok(())
            },
        )
        .await?;

    assert_eq!(response.content, "Hello, authoritative.");
    assert_eq!(
        *deltas.lock().map_err(|_| io::Error::other("delta lock"))?,
        vec!["Hel".to_owned(), "lo".to_owned()]
    );
    let writes = parse_writes(&writes)?;
    assert_eq!(writes[4]["method"], "thread/inject_items");
    assert_eq!(writes[4]["params"]["items"][0]["role"], "user");
    assert_eq!(writes[4]["params"]["items"][1]["role"], "assistant");
    assert!(
        writes[3]["params"]["developerInstructions"]
            .as_str()
            .is_some_and(|text| text.contains("Be concise."))
    );
    assert_eq!(writes[3]["params"]["approvalPolicy"], "never");
    assert_eq!(
        writes[3]["params"]["config"]["default_permissions"],
        "wayfinder-chat"
    );
    assert_eq!(
        writes[3]["params"]["config"]["features"]["skill_search"],
        false
    );
    assert_eq!(
        writes[3]["params"]["config"]["features"]["shell_tool"],
        false
    );
    assert!(writes[3]["params"].get("permissions").is_none());
    Ok(())
}

#[tokio::test]
async fn thread_start_requires_ephemeral_network_closed_profile_isolation() -> TestResult {
    let mut missing_ephemeral: Value =
        serde_json::from_slice(&thread_response(3, "thread-policy"))?;
    missing_ephemeral["result"]["thread"]
        .as_object_mut()
        .ok_or_else(|| io::Error::other("thread fixture"))?
        .remove("ephemeral");

    let mut missing_network: Value = serde_json::from_slice(&thread_response(3, "thread-policy"))?;
    missing_network["result"]["sandbox"]
        .as_object_mut()
        .ok_or_else(|| io::Error::other("sandbox fixture"))?
        .remove("networkAccess");

    let mut extended_profile: Value = serde_json::from_slice(&thread_response(3, "thread-policy"))?;
    extended_profile["result"]["activePermissionProfile"]["extends"] = json!("default");

    for response in [missing_ephemeral, missing_network, extended_profile] {
        let (manager, _, _, terminated) =
            manager_with_termination(vec![model_response(2), frame(response)])?;
        assert_eq!(
            manager
                .chat(simple_chat(), CancellationToken::new(), |_| Ok(()))
                .await,
            Err(CodexAppServerError::InvalidConfiguration)
        );
        assert!(
            *terminated
                .lock()
                .map_err(|_| io::Error::other("terminate lock"))?
        );
    }
    Ok(())
}

#[tokio::test]
async fn tool_activity_fails_closed_and_interrupts() -> TestResult {
    let (manager, writes, _) = manager_with(vec![
        model_response(2),
        thread_response(3, "thread-2"),
        frame(json!({ "id": 4, "result": { "turn": {
            "id": "turn-2", "status": "inProgress", "items": []
        } } })),
        frame(json!({ "method": "item/started", "params": {
            "threadId": "thread-2", "turnId": "turn-2",
            "item": { "id": "command-1", "type": "commandExecution" }
        } })),
        frame(json!({ "id": 5, "result": {} })),
    ])?;

    let result = manager
        .chat(simple_chat(), CancellationToken::new(), |_| Ok(()))
        .await;

    assert_eq!(result, Err(CodexAppServerError::ForbiddenAction));
    let writes = parse_writes(&writes)?;
    assert_eq!(
        writes.last().and_then(|write| write.get("method")),
        Some(&json!("turn/interrupt"))
    );
    Ok(())
}

#[tokio::test]
async fn cancellation_interrupts_the_active_turn() -> TestResult {
    let (manager, writes, _, terminated) = manager_with_interrupt_transport(
        vec![
            model_response(2),
            thread_response(3, "thread-3"),
            frame(json!({ "id": 4, "result": { "turn": {
                "id": "turn-3", "status": "inProgress", "items": []
            } } })),
        ],
        vec![
            frame(json!({ "id": 5, "result": {} })),
            frame(json!({ "method": "turn/completed", "params": {
                "threadId": "thread-3", "turn": {
                    "id": "turn-3", "status": "interrupted", "items": []
                }
            } })),
        ],
        true,
    )?;
    let cancellation = CancellationToken::new();
    let worker_cancel = cancellation.clone();
    let worker_manager = manager.clone();
    let worker = tokio::spawn(async move {
        worker_manager
            .chat(simple_chat(), worker_cancel, |_| Ok(()))
            .await
    });
    wait_for_writes(&writes, 5).await?;
    cancellation.cancel();

    let result = worker.await?;

    assert_eq!(result, Err(CodexAppServerError::Interrupted));
    let writes = parse_writes(&writes)?;
    assert_eq!(
        writes.last().and_then(|write| write.get("method")),
        Some(&json!("turn/interrupt"))
    );
    assert!(
        !*terminated
            .lock()
            .map_err(|_| io::Error::other("terminate lock"))?
    );
    Ok(())
}

#[tokio::test]
async fn cancellation_without_a_correlated_terminal_kills_the_session() -> TestResult {
    let (manager, writes, _, terminated) = manager_with_transport(
        vec![
            model_response(2),
            thread_response(3, "thread-4"),
            frame(json!({ "id": 4, "result": { "turn": {
                "id": "turn-4", "status": "inProgress", "items": []
            } } })),
        ],
        true,
    )?;
    let cancellation = CancellationToken::new();
    let worker_cancel = cancellation.clone();
    let worker_manager = manager.clone();
    let worker = tokio::spawn(async move {
        worker_manager
            .chat(simple_chat(), worker_cancel, |_| Ok(()))
            .await
    });
    wait_for_writes(&writes, 5).await?;
    cancellation.cancel();

    assert_eq!(worker.await?, Err(CodexAppServerError::Interrupted));
    assert!(
        *terminated
            .lock()
            .map_err(|_| io::Error::other("terminate lock"))?
    );
    Ok(())
}

#[tokio::test]
async fn pre_cancelled_chat_never_starts_or_terminates_a_session() -> TestResult {
    let (manager, writes, _, terminated) = manager_with_termination(Vec::new())?;
    let cancellation = CancellationToken::new();
    cancellation.cancel();

    assert_eq!(
        manager.chat(simple_chat(), cancellation, |_| Ok(())).await,
        Err(CodexAppServerError::Interrupted)
    );
    assert!(
        writes
            .lock()
            .map_err(|_| io::Error::other("write lock"))?
            .is_empty()
    );
    assert!(
        !*terminated
            .lock()
            .map_err(|_| io::Error::other("terminate lock"))?
    );
    Ok(())
}

#[tokio::test]
async fn active_turn_rejects_second_chat_and_bounds_control_waits() -> TestResult {
    let (manager, writes, _, terminated) = manager_with_transport(
        vec![
            model_response(2),
            thread_response(3, "thread-busy"),
            frame(json!({ "id": 4, "result": { "turn": {
                "id": "turn-busy", "status": "inProgress", "items": []
            } } })),
        ],
        true,
    )?;
    let first_cancel = CancellationToken::new();
    let first_manager = manager.clone();
    let worker_cancel = first_cancel.clone();
    let first = tokio::spawn(async move {
        first_manager
            .chat(simple_chat(), worker_cancel, |_| Ok(()))
            .await
    });
    wait_for_writes(&writes, 5).await?;

    let second = tokio::time::timeout(
        Duration::from_millis(25),
        manager.chat(simple_chat(), CancellationToken::new(), |_| Ok(())),
    )
    .await?;
    assert_eq!(second, Err(CodexAppServerError::Busy));

    let models = tokio::time::timeout(Duration::from_millis(250), manager.models()).await?;
    assert_eq!(models, Err(CodexAppServerError::TimedOut));
    let account = tokio::time::timeout(Duration::from_millis(250), manager.account()).await??;
    assert_eq!(account.status, AccountStatus::Unavailable);

    first_cancel.cancel();
    assert_eq!(first.await?, Err(CodexAppServerError::Interrupted));
    assert!(
        *terminated
            .lock()
            .map_err(|_| io::Error::other("terminate lock"))?
    );
    Ok(())
}

#[tokio::test]
async fn structured_turn_errors_keep_auth_and_usage_readiness_distinct() -> TestResult {
    for (codex_error_info, expected) in [
        ("unauthorized", CodexAppServerError::AuthenticationRequired),
        ("usageLimitExceeded", CodexAppServerError::UsageLimitReached),
    ] {
        let (manager, _, _) = manager_with(vec![
            model_response(2),
            thread_response(3, "thread-error"),
            frame(json!({ "id": 4, "result": { "turn": {
                "id": "turn-error", "status": "inProgress", "items": []
            } } })),
            frame(json!({ "method": "error", "params": {
                "threadId": "thread-error",
                "turnId": "turn-error",
                "willRetry": false,
                "error": {
                    "message": "sensitive upstream detail",
                    "codexErrorInfo": codex_error_info
                }
            } })),
            frame(json!({ "method": "turn/completed", "params": {
                "threadId": "thread-error", "turn": {
                    "id": "turn-error", "status": "failed", "items": []
                }
            } })),
        ])?;

        assert_eq!(
            manager
                .chat(simple_chat(), CancellationToken::new(), |_| Ok(()))
                .await,
            Err(expected)
        );
    }
    Ok(())
}

#[tokio::test]
async fn stream_delta_bound_applies_after_json_escaping() -> TestResult {
    let (manager, _, _) = manager_with(vec![
        model_response(2),
        thread_response(3, "thread-escaped"),
        frame(json!({ "id": 4, "result": { "turn": {
            "id": "turn-escaped", "status": "inProgress", "items": []
        } } })),
        frame(json!({ "method": "item/agentMessage/delta", "params": {
            "threadId": "thread-escaped",
            "turnId": "turn-escaped",
            "itemId": "item-escaped",
            "delta": "\\".repeat(31_000)
        } })),
    ])?;

    assert_eq!(
        manager
            .chat(simple_chat(), CancellationToken::new(), |_| Ok(()))
            .await,
        Err(CodexAppServerError::ResponseTooLarge)
    );
    Ok(())
}

#[tokio::test]
async fn eof_becomes_a_normalized_unavailable_account_state() -> TestResult {
    let (manager, _, _) = manager_with(Vec::new())?;

    let account = manager.account().await?;

    assert_eq!(account.status, AccountStatus::Unavailable);
    Ok(())
}

#[tokio::test]
async fn helper_spawn_is_bounded_by_the_request_deadline() -> TestResult {
    let manager = CodexAppServerManager::with_factory(test_config()?, Arc::new(HangingFactory))?;

    let result = tokio::time::timeout(Duration::from_millis(250), manager.models()).await?;

    assert_eq!(result, Err(CodexAppServerError::TimedOut));
    Ok(())
}

#[tokio::test]
async fn repeated_protocol_eof_exhausts_the_finite_restart_budget() -> TestResult {
    let config = test_config()?;
    let spawns = Arc::new(AtomicUsize::new(0));
    let manager = CodexAppServerManager::with_factory(
        config,
        Arc::new(RestartingFactory {
            spawns: Arc::clone(&spawns),
        }),
    )?;

    for _ in 0..6 {
        assert_eq!(manager.account().await?.status, AccountStatus::Unavailable);
    }
    assert_eq!(spawns.load(Ordering::Relaxed), MAX_RUNTIME_RESTARTS + 1);
    Ok(())
}

fn simple_chat() -> ChatRequest {
    ChatRequest {
        model: "gpt-5.6-sol".to_owned(),
        messages: vec![ChatMessage {
            role: ChatRole::User,
            content: "Hello".to_owned(),
        }],
    }
}
