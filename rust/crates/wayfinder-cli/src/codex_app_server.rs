//! Process-owned adapter for the optional managed ChatGPT provider.
//!
//! This module deliberately does not search `PATH` or reuse the user's normal
//! Codex home. An enabled provider gets one explicitly discovered helper, a
//! Wayfinder-owned credential store, and an empty Wayfinder-owned workspace.

use std::env;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};

#[cfg(target_os = "macos")]
use std::process::{Command, Stdio};

use thiserror::Error;
use tokio::sync::Mutex as AsyncMutex;
use wayfinder_codex_app_server::{
    AccountSnapshot, AccountStatus, CodexAppServerError, CodexAppServerManager, LoginMethod,
    LoginStart, ModelInfo, RuntimeConfig, RuntimeLimits,
};
use wayfinder_gateway::codex_control::{
    CodexAccountState, CodexConnectedAccount, CodexControl, CodexControlError, CodexControlFuture,
    CodexLoginFlow, CodexModel,
};

#[cfg(debug_assertions)]
const DEVELOPMENT_HELPER_ENV: &str = "WAYFINDER_CODEX_HELPER";
const USER_HOME_ENV: &str = "HOME";
#[cfg(debug_assertions)]
const BUNDLED_HELPER_NAME: &str = "codex";
const CHATGPT_HELPER: &str = "/Applications/ChatGPT.app/Contents/Resources/codex";
#[cfg(target_os = "macos")]
const CHATGPT_APP: &str = "/Applications/ChatGPT.app";
#[cfg(any(target_os = "macos", test))]
const CHATGPT_TEAM_IDENTIFIER: &str = "2DC432GLL2";
#[cfg(target_os = "macos")]
const CHATGPT_APP_REQUIREMENT: &str = "anchor apple generic and certificate leaf[subject.OU] = \"2DC432GLL2\" and identifier \"com.openai.codex\"";
#[cfg(target_os = "macos")]
const CHATGPT_HELPER_REQUIREMENT: &str = "anchor apple generic and certificate leaf[subject.OU] = \"2DC432GLL2\" and identifier \"codex\"";
const PRIVATE_DIRECTORY_MODE: u32 = 0o700;
const PRIVATE_FILE_MODE: u32 = 0o600;
const STORE_MARKER: &str = ".wayfinder-codex-store";
const STORE_MARKER_CONTENTS: &[u8] = b"wayfinder-codex-store-v1\n";

#[cfg(unix)]
type OwnerId = u32;
#[cfg(not(unix))]
type OwnerId = ();

/// Sanitized startup failure for the optional managed ChatGPT runtime.
///
/// No variant retains or displays a user-controlled path or an underlying I/O
/// diagnostic. The owning process can report these values without disclosing
/// account-store locations.
#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub(crate) enum CodexAdapterSetupError {
    /// No approved helper exists at any of the bounded discovery locations.
    #[error("the managed ChatGPT helper is unavailable")]
    HelperUnavailable,
    /// An explicitly selected helper is not an absolute executable file.
    #[error("the configured managed ChatGPT helper is invalid")]
    InvalidHelper,
    /// The ordinary user home cannot be resolved.
    #[error("the Wayfinder account-store location is unavailable")]
    UserHomeUnavailable,
    /// The override attempts to reuse the ordinary Codex account store.
    #[error("the Wayfinder account store must be isolated from ordinary Codex state")]
    SharedCodexHome,
    /// Private storage could not be created or verified.
    #[error("the private Wayfinder account store cannot be prepared")]
    PrivateStorageUnavailable,
    /// The dedicated general-chat workspace contains unexpected content.
    #[error("the private Wayfinder chat workspace is not empty")]
    WorkspaceNotEmpty,
    /// The process manager rejected its bounded configuration.
    #[error("the managed ChatGPT runtime cannot be initialized")]
    RuntimeUnavailable,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct RuntimePaths {
    helper: PathBuf,
    codex_home: PathBuf,
    workspace: PathBuf,
}

/// Cloneable process adapter shared by account control and provider delivery.
///
/// The manager remains the only object that can reach the child process. The
/// pending-login cache contains only the runtime's non-secret operation id,
/// browser/device URL, and user code so repeated account reads can retain the
/// normalized awaiting state.
#[derive(Clone)]
pub(crate) struct CodexAppServerAdapter {
    manager: CodexAppServerManager,
    pending_login: Arc<Mutex<Option<LoginStart>>>,
    control_operation: Arc<AsyncMutex<()>>,
}

/// Truthful loopback control used when an explicitly configured provider
/// cannot prepare its helper or isolated store at process startup.
///
/// This keeps account discovery available without making the gateway depend on
/// optional provider readiness. Mutations fail because no operation was
/// performed; in particular logout never claims to have cleared Codex-owned
/// credentials when the helper could not be reached.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct UnavailableCodexControl;

impl UnavailableCodexControl {
    /// Construct a stateless degraded control.
    #[must_use]
    pub(crate) const fn new() -> Self {
        Self
    }
}

impl CodexControl for UnavailableCodexControl {
    fn account(&self) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async { Ok(CodexAccountState::Unavailable) })
    }

    fn models(&self) -> CodexControlFuture<'_, Vec<CodexModel>> {
        Box::pin(async { Err(sanitized_control_error()) })
    }

    fn login(&self, _flow: CodexLoginFlow) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async { Err(sanitized_control_error()) })
    }

    fn cancel_login<'a>(&'a self, _login_id: &'a str) -> CodexControlFuture<'a, CodexAccountState> {
        Box::pin(async { Err(sanitized_control_error()) })
    }

    fn logout(&self) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async { Err(sanitized_control_error()) })
    }
}

impl CodexAppServerAdapter {
    /// Wrap a configured manager without starting its lazy child process.
    #[must_use]
    pub(crate) fn new(manager: CodexAppServerManager) -> Self {
        Self {
            manager,
            pending_login: Arc::new(Mutex::new(None)),
            control_operation: Arc::new(AsyncMutex::new(())),
        }
    }

    /// Clone the bounded manager for the separate delivery adapter.
    #[must_use]
    pub(crate) fn manager(&self) -> CodexAppServerManager {
        self.manager.clone()
    }

    fn pending_login(&self) -> Result<Option<LoginStart>, CodexControlError> {
        self.pending_login
            .lock()
            .map(|pending| pending.clone())
            .map_err(|_| sanitized_control_error())
    }

    fn replace_pending_login(&self, login: Option<LoginStart>) -> Result<(), CodexControlError> {
        let mut pending = self
            .pending_login
            .lock()
            .map_err(|_| sanitized_control_error())?;
        *pending = login;
        Ok(())
    }
}

impl CodexControl for CodexAppServerAdapter {
    fn account(&self) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async move {
            let _operation = self.control_operation.lock().await;
            let snapshot = self
                .manager
                .account()
                .await
                .map_err(map_runtime_control_error)?;
            let pending = self.pending_login()?;
            let state = normalize_account(snapshot, pending.as_ref());
            if !matches!(
                state,
                CodexAccountState::AwaitingBrowser { .. }
                    | CodexAccountState::AwaitingDeviceCode { .. }
            ) {
                self.replace_pending_login(None)?;
            }
            Ok(state)
        })
    }

    fn models(&self) -> CodexControlFuture<'_, Vec<CodexModel>> {
        Box::pin(async move {
            let models = self
                .manager
                .models()
                .await
                .map_err(map_runtime_control_error)?;
            Ok(normalize_models(models))
        })
    }

    fn login(&self, flow: CodexLoginFlow) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async move {
            let _operation = self.control_operation.lock().await;
            let method = match flow {
                CodexLoginFlow::Browser => LoginMethod::Browser,
                CodexLoginFlow::DeviceCode => LoginMethod::DeviceCode,
            };
            let login = self
                .manager
                .start_login(method)
                .await
                .map_err(map_runtime_control_error)?;
            let state = normalize_login(&login);
            self.replace_pending_login(Some(login))?;
            Ok(state)
        })
    }

    fn cancel_login<'a>(&'a self, login_id: &'a str) -> CodexControlFuture<'a, CodexAccountState> {
        Box::pin(async move {
            let _operation = self.control_operation.lock().await;
            if self.pending_login()?.as_ref().map(login_start_id) != Some(login_id) {
                return Err(sanitized_control_error());
            }
            self.manager
                .cancel_login(login_id)
                .await
                .map_err(map_runtime_control_error)?;
            self.replace_pending_login(None)?;
            Ok(CodexAccountState::SignedOut)
        })
    }

    fn logout(&self) -> CodexControlFuture<'_, CodexAccountState> {
        Box::pin(async move {
            let _operation = self.control_operation.lock().await;
            self.manager
                .logout()
                .await
                .map_err(map_runtime_control_error)?;
            self.replace_pending_login(None)?;
            Ok(CodexAccountState::SignedOut)
        })
    }
}

/// Build the optional runtime only when configuration contains a Codex model.
///
/// The disabled path returns before helper discovery, home resolution, or any
/// directory mutation. The manager itself remains lazy after construction.
pub(crate) fn build_codex_app_server_adapter(
    provider_configured: bool,
    client_version: &str,
) -> Result<Option<CodexAppServerAdapter>, CodexAdapterSetupError> {
    if !provider_configured {
        return Ok(None);
    }
    build_codex_app_server_adapter_with(true, client_version, &DiscoveryInputs::from_process())
}

fn build_codex_app_server_adapter_with(
    provider_configured: bool,
    client_version: &str,
    inputs: &DiscoveryInputs,
) -> Result<Option<CodexAppServerAdapter>, CodexAdapterSetupError> {
    let Some(paths) = prepare_runtime_paths(provider_configured, inputs)? else {
        return Ok(None);
    };
    let manager = CodexAppServerManager::new(RuntimeConfig {
        helper_path: paths.helper,
        codex_home: paths.codex_home,
        workspace: paths.workspace,
        client_version: client_version.to_owned(),
        limits: RuntimeLimits::default(),
    })
    .map_err(|_| CodexAdapterSetupError::RuntimeUnavailable)?;
    Ok(Some(CodexAppServerAdapter::new(manager)))
}

#[derive(Clone, Debug)]
struct DiscoveryInputs {
    explicit_helper: Option<PathBuf>,
    user_home: Option<PathBuf>,
    current_executable: Option<PathBuf>,
    chatgpt_helper: PathBuf,
    chatgpt_helper_trust: fn(&Path) -> bool,
}

impl DiscoveryInputs {
    fn from_process() -> Self {
        Self {
            explicit_helper: development_helper_override(),
            user_home: nonempty_env_path(USER_HOME_ENV),
            current_executable: env::current_exe().ok(),
            chatgpt_helper: PathBuf::from(CHATGPT_HELPER),
            chatgpt_helper_trust: chatgpt_helper_is_trusted,
        }
    }
}

/// Return the process helper override only in development builds.
///
/// Release builds do not read the override environment variable at all, so a
/// production Wayfinder process can discover a helper only from the separately
/// verified ChatGPT installation. Bundled release discovery stays disabled
/// until packaging can pin and verify the nested helper's identity and digest.
fn development_helper_override() -> Option<PathBuf> {
    development_helper_override_from(|name| env::var_os(name))
}

#[cfg(debug_assertions)]
fn development_helper_override_from<F>(read_environment: F) -> Option<PathBuf>
where
    F: FnOnce(&str) -> Option<OsString>,
{
    read_environment(DEVELOPMENT_HELPER_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

#[cfg(not(debug_assertions))]
fn development_helper_override_from<F>(_read_environment: F) -> Option<PathBuf>
where
    F: FnOnce(&str) -> Option<OsString>,
{
    None
}

fn nonempty_env_path(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

/// Resolve and prepare the optional provider without touching the filesystem
/// when the provider is absent from configuration.
fn prepare_runtime_paths(
    provider_configured: bool,
    inputs: &DiscoveryInputs,
) -> Result<Option<RuntimePaths>, CodexAdapterSetupError> {
    if !provider_configured {
        return Ok(None);
    }

    let helper = discover_helper(inputs)?;
    let user_home = inputs
        .user_home
        .as_deref()
        .ok_or(CodexAdapterSetupError::UserHomeUnavailable)?;
    let codex_home = user_home
        .join("Library")
        .join("Application Support")
        .join("Wayfinder")
        .join("Codex");
    if !codex_home.is_absolute() {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    reject_shared_codex_home(&codex_home, user_home)?;
    reject_symlinks_below(user_home, &codex_home)?;
    let owner = user_home_owner(user_home)?;
    let workspace = codex_home.join("chat-workspace");
    prepare_owned_store(&codex_home, owner)?;
    reject_symlinks_below(user_home, &codex_home)?;
    prepare_private_directory(&workspace, owner)?;
    verify_empty_directory(&workspace)?;

    Ok(Some(RuntimePaths {
        helper,
        codex_home,
        workspace,
    }))
}

fn discover_helper(inputs: &DiscoveryInputs) -> Result<PathBuf, CodexAdapterSetupError> {
    if let Some(explicit) = inputs.explicit_helper.as_deref() {
        return if helper_is_executable(explicit) {
            Ok(explicit.to_path_buf())
        } else {
            Err(CodexAdapterSetupError::InvalidHelper)
        };
    }

    if let Some(bundled) = development_bundled_helper(inputs.current_executable.as_deref()) {
        return Ok(bundled);
    }

    if (inputs.chatgpt_helper_trust)(&inputs.chatgpt_helper) {
        return Ok(inputs.chatgpt_helper.clone());
    }

    Err(CodexAdapterSetupError::HelperUnavailable)
}

#[cfg(debug_assertions)]
fn development_bundled_helper(current_executable: Option<&Path>) -> Option<PathBuf> {
    current_executable
        .and_then(Path::parent)
        .map(|parent| parent.join(BUNDLED_HELPER_NAME))
        .filter(|path| helper_is_executable(path))
}

#[cfg(not(debug_assertions))]
fn development_bundled_helper(_current_executable: Option<&Path>) -> Option<PathBuf> {
    None
}

fn helper_is_executable(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return false;
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn chatgpt_helper_is_trusted(path: &Path) -> bool {
    if !helper_is_executable(path) {
        return false;
    }

    #[cfg(target_os = "macos")]
    {
        if path != Path::new(CHATGPT_HELPER) {
            return false;
        }
        let Ok(canonical) = fs::canonicalize(path) else {
            return false;
        };
        if canonical != path {
            return false;
        }
        let app = Path::new(CHATGPT_APP);
        let Ok(canonical_app) = fs::canonicalize(app) else {
            return false;
        };
        if canonical_app != app {
            return false;
        }

        if !codesign_satisfies(path, CHATGPT_HELPER_REQUIREMENT)
            || !codesign_satisfies(app, CHATGPT_APP_REQUIREMENT)
        {
            return false;
        }

        let Ok(identity) = Command::new("/usr/bin/codesign")
            .args(["--display", "--verbose=4"])
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .output()
        else {
            return false;
        };
        identity.status.success() && codesign_team_matches(&identity.stderr)
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = path;
        false
    }
}

#[cfg(target_os = "macos")]
fn codesign_satisfies(path: &Path, requirement: &str) -> bool {
    Command::new("/usr/bin/codesign")
        .args(["--verify", "--strict"])
        .arg(format!("-R={requirement}"))
        .arg(path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(any(target_os = "macos", test))]
fn codesign_team_matches(output: &[u8]) -> bool {
    String::from_utf8_lossy(output)
        .lines()
        .any(|line| line.trim().strip_prefix("TeamIdentifier=") == Some(CHATGPT_TEAM_IDENTIFIER))
}

fn reject_shared_codex_home(
    codex_home: &Path,
    user_home: &Path,
) -> Result<(), CodexAdapterSetupError> {
    if contains_parent_component(codex_home) {
        return Err(CodexAdapterSetupError::SharedCodexHome);
    }
    if contains_parent_component(user_home) {
        return Err(CodexAdapterSetupError::UserHomeUnavailable);
    }
    let ordinary_codex_home = user_home.join(".codex");
    if paths_overlap_as_ancestors(codex_home, &ordinary_codex_home) {
        return Err(CodexAdapterSetupError::SharedCodexHome);
    }

    let resolved_home = resolve_through_existing_ancestor(codex_home)?;
    let resolved_ordinary = resolve_through_existing_ancestor(&ordinary_codex_home)?;
    if paths_overlap_as_ancestors(&resolved_home, &resolved_ordinary) {
        Err(CodexAdapterSetupError::SharedCodexHome)
    } else {
        Ok(())
    }
}

fn contains_parent_component(path: &Path) -> bool {
    path.components()
        .any(|component| component == Component::ParentDir)
}

fn paths_overlap_as_ancestors(left: &Path, right: &Path) -> bool {
    left.starts_with(right) || right.starts_with(left)
}

fn reject_symlinks_below(root: &Path, path: &Path) -> Result<(), CodexAdapterSetupError> {
    let suffix = path
        .strip_prefix(root)
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    let mut current = root.to_path_buf();
    for component in suffix.components() {
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(_) => return Err(CodexAdapterSetupError::PrivateStorageUnavailable),
        }
    }
    Ok(())
}

/// Resolve every existing prefix while preserving a not-yet-created suffix.
/// This detects an override that reaches `~/.codex` through a differently
/// named symlink without requiring the final Wayfinder directory to exist.
fn resolve_through_existing_ancestor(path: &Path) -> Result<PathBuf, CodexAdapterSetupError> {
    let mut cursor = path;
    let mut suffix = Vec::<OsString>::new();
    loop {
        match fs::canonicalize(cursor) {
            Ok(mut resolved) => {
                for component in suffix.iter().rev() {
                    resolved.push(component);
                }
                return Ok(resolved);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let name = cursor
                    .file_name()
                    .ok_or(CodexAdapterSetupError::PrivateStorageUnavailable)?;
                suffix.push(name.to_os_string());
                cursor = cursor
                    .parent()
                    .ok_or(CodexAdapterSetupError::PrivateStorageUnavailable)?;
            }
            Err(_) => return Err(CodexAdapterSetupError::PrivateStorageUnavailable),
        }
    }
}

fn user_home_owner(path: &Path) -> Result<OwnerId, CodexAdapterSetupError> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| CodexAdapterSetupError::UserHomeUnavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(CodexAdapterSetupError::UserHomeUnavailable);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(metadata.uid())
    }
    #[cfg(not(unix))]
    {
        Ok(())
    }
}

fn verify_store_parent(path: &Path, owner: OwnerId) -> Result<(), CodexAdapterSetupError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    verify_owner(&metadata, owner)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o022 != 0 {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
    }
    verify_no_acl(path)
}

fn verify_owner(metadata: &fs::Metadata, expected: OwnerId) -> Result<(), CodexAdapterSetupError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != expected {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
    }
    #[cfg(not(unix))]
    let _ = (metadata, expected);
    Ok(())
}

#[cfg(target_os = "macos")]
fn verify_no_acl(path: &Path) -> Result<(), CodexAdapterSetupError> {
    let output = Command::new("/bin/ls")
        .arg("-lde")
        .arg(path)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    if !output.status.success() || permissions_token_has_acl(&output.stdout) {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn verify_no_acl(_path: &Path) -> Result<(), CodexAdapterSetupError> {
    Ok(())
}

#[cfg(any(target_os = "macos", test))]
fn permissions_token_has_acl(output: &[u8]) -> bool {
    String::from_utf8_lossy(output)
        .split_whitespace()
        .next()
        .is_none_or(|token| token.contains('+'))
}

fn prepare_private_directory(path: &Path, owner: OwnerId) -> Result<(), CodexAdapterSetupError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
        Ok(_) => return verify_private_directory(path, owner),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(path)
                .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
        }
        Err(_) => return Err(CodexAdapterSetupError::PrivateStorageUnavailable),
    }

    set_private_permissions(path, PRIVATE_DIRECTORY_MODE)?;
    verify_private_directory(path, owner)
}

fn prepare_owned_store(path: &Path, owner: OwnerId) -> Result<(), CodexAdapterSetupError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
        Ok(_) => {
            verify_private_directory(path, owner)?;
            verify_store_marker(path, owner)?;
            return Ok(());
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(CodexAdapterSetupError::PrivateStorageUnavailable),
    }

    let parent = path
        .parent()
        .ok_or(CodexAdapterSetupError::PrivateStorageUnavailable)?;
    fs::create_dir_all(parent).map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    verify_store_parent(parent, owner)?;

    let stage = parent.join(format!(
        ".wayfinder-codex-store-stage-{}",
        uuid::Uuid::new_v4()
    ));
    fs::create_dir(&stage).map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    let result = (|| {
        set_private_permissions(&stage, PRIVATE_DIRECTORY_MODE)?;
        verify_private_directory(&stage, owner)?;
        create_store_marker(&stage)?;
        verify_store_marker(&stage, owner)?;
        fs::rename(&stage, path).map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
        verify_private_directory(path, owner)?;
        verify_store_marker(path, owner)
    })();
    if stage.exists() {
        let _ = fs::remove_dir_all(&stage);
    }
    result
}

fn create_store_marker(path: &Path) -> Result<(), CodexAdapterSetupError> {
    let marker = path.join(STORE_MARKER);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(PRIVATE_FILE_MODE);
    }
    let mut file = options
        .open(&marker)
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    file.write_all(STORE_MARKER_CONTENTS)
        .and_then(|()| file.sync_all())
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    set_private_permissions(&marker, PRIVATE_FILE_MODE)
}

fn verify_store_marker(path: &Path, owner: OwnerId) -> Result<(), CodexAdapterSetupError> {
    let marker = path.join(STORE_MARKER);
    let metadata = fs::symlink_metadata(&marker)
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
    }
    verify_owner(&metadata, owner)?;
    verify_private_mode(&metadata, PRIVATE_FILE_MODE)?;
    verify_no_acl(&marker)?;
    let contents =
        fs::read(marker).map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    if contents != STORE_MARKER_CONTENTS {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    Ok(())
}

fn verify_private_directory(path: &Path, owner: OwnerId) -> Result<(), CodexAdapterSetupError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
    }
    verify_owner(&metadata, owner)?;
    verify_private_mode(&metadata, PRIVATE_DIRECTORY_MODE)?;
    verify_no_acl(path)
}

fn set_private_permissions(path: &Path, mode: u32) -> Result<(), CodexAdapterSetupError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))
            .map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    }
    #[cfg(not(unix))]
    let _ = (path, mode);
    Ok(())
}

fn verify_private_mode(
    metadata: &fs::Metadata,
    expected: u32,
) -> Result<(), CodexAdapterSetupError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o777 != expected {
            return Err(CodexAdapterSetupError::PrivateStorageUnavailable);
        }
    }
    #[cfg(not(unix))]
    let _ = (metadata, expected);

    Ok(())
}

fn verify_empty_directory(path: &Path) -> Result<(), CodexAdapterSetupError> {
    let mut entries =
        fs::read_dir(path).map_err(|_| CodexAdapterSetupError::PrivateStorageUnavailable)?;
    match entries.next() {
        None => Ok(()),
        Some(Ok(_)) => Err(CodexAdapterSetupError::WorkspaceNotEmpty),
        Some(Err(_)) => Err(CodexAdapterSetupError::PrivateStorageUnavailable),
    }
}

fn normalize_account(
    snapshot: AccountSnapshot,
    pending_login: Option<&LoginStart>,
) -> CodexAccountState {
    match snapshot.status {
        AccountStatus::SignedOut => CodexAccountState::SignedOut,
        AccountStatus::AwaitingBrowser => match pending_login {
            Some(LoginStart::Browser { login_id, auth_url }) => {
                CodexAccountState::AwaitingBrowser {
                    login_id: login_id.clone(),
                    url: auth_url.clone(),
                }
            }
            _ => CodexAccountState::Unavailable,
        },
        AccountStatus::AwaitingDeviceCode => match pending_login {
            Some(LoginStart::DeviceCode {
                login_id,
                verification_url,
                user_code,
            }) => CodexAccountState::AwaitingDeviceCode {
                login_id: login_id.clone(),
                url: verification_url.clone(),
                user_code: user_code.clone(),
            },
            _ => CodexAccountState::Unavailable,
        },
        AccountStatus::Connected => CodexAccountState::Connected(CodexConnectedAccount {
            email: snapshot.email.filter(|value| !value.is_empty()),
            plan: snapshot.plan_type.filter(|value| !value.is_empty()),
        }),
        AccountStatus::ReauthRequired => CodexAccountState::ReauthenticationRequired,
        AccountStatus::Unavailable => CodexAccountState::Unavailable,
    }
}

fn normalize_login(login: &LoginStart) -> CodexAccountState {
    match login {
        LoginStart::Browser { login_id, auth_url } => CodexAccountState::AwaitingBrowser {
            login_id: login_id.clone(),
            url: auth_url.clone(),
        },
        LoginStart::DeviceCode {
            login_id,
            verification_url,
            user_code,
        } => CodexAccountState::AwaitingDeviceCode {
            login_id: login_id.clone(),
            url: verification_url.clone(),
            user_code: user_code.clone(),
        },
    }
}

fn login_start_id(login: &LoginStart) -> &str {
    match login {
        LoginStart::Browser { login_id, .. } | LoginStart::DeviceCode { login_id, .. } => login_id,
    }
}

fn normalize_models(models: Vec<ModelInfo>) -> Vec<CodexModel> {
    models
        .into_iter()
        .filter(|model| !model.hidden)
        .map(|model| CodexModel {
            id: model.id,
            display_name: (!model.display_name.is_empty()).then_some(model.display_name),
        })
        .collect()
}

fn map_runtime_control_error(_error: CodexAppServerError) -> CodexControlError {
    sanitized_control_error()
}

fn sanitized_control_error() -> CodexControlError {
    CodexControlError::new("managed ChatGPT account operation failed")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(debug_assertions)]
    #[test]
    fn debug_builds_expose_only_nonempty_development_helper_overrides() {
        let expected = PathBuf::from("/tmp/wayfinder-development-codex");
        assert_eq!(
            development_helper_override_from(|name| {
                assert_eq!(name, DEVELOPMENT_HELPER_ENV);
                Some(expected.clone().into_os_string())
            }),
            Some(expected)
        );
        assert_eq!(
            development_helper_override_from(|_| Some(OsString::new())),
            None
        );
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn release_builds_disable_the_development_helper_override() {
        assert_eq!(
            development_helper_override_from(|_| -> Option<OsString> {
                panic!("release builds must not read WAYFINDER_CODEX_HELPER")
            }),
            None
        );
    }

    struct TestDirectory {
        path: PathBuf,
    }

    impl TestDirectory {
        fn new(label: &str) -> Result<Self, Box<dyn std::error::Error>> {
            let path = env::temp_dir().join(format!(
                "wayfinder-codex-adapter-{label}-{}",
                uuid::Uuid::new_v4()
            ));
            fs::create_dir_all(&path)?;
            fs::create_dir_all(path.join("home"))?;
            Ok(Self { path })
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    fn executable(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, b"test helper")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
        }
        Ok(())
    }

    fn inputs(root: &Path) -> DiscoveryInputs {
        DiscoveryInputs {
            explicit_helper: None,
            user_home: Some(root.join("home")),
            current_executable: None,
            chatgpt_helper: root.join("missing-chatgpt-codex"),
            chatgpt_helper_trust: helper_is_executable,
        }
    }

    fn reject_helper(_path: &Path) -> bool {
        false
    }

    #[test]
    fn explicit_helper_precedes_bundled_and_chatgpt_helpers()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("helper-precedence")?;
        let explicit = root.path.join("explicit/codex");
        let current_executable = root
            .path
            .join("Wayfinder.app/Contents/Helpers/wayfinder-router");
        let bundled = root.path.join("Wayfinder.app/Contents/Helpers/codex");
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&explicit)?;
        executable(&bundled)?;
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(explicit.clone());
        discovery.current_executable = Some(current_executable);
        discovery.chatgpt_helper = chatgpt;

        assert_eq!(discover_helper(&discovery)?, explicit);
        Ok(())
    }

    #[cfg(debug_assertions)]
    #[test]
    fn bundled_helper_precedes_installed_chatgpt_helper() -> Result<(), Box<dyn std::error::Error>>
    {
        let root = TestDirectory::new("bundled-precedence")?;
        let current_executable = root
            .path
            .join("Wayfinder.app/Contents/Helpers/wayfinder-router");
        let bundled = root.path.join("Wayfinder.app/Contents/Helpers/codex");
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&bundled)?;
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.current_executable = Some(current_executable);
        discovery.chatgpt_helper = chatgpt;

        assert_eq!(discover_helper(&discovery)?, bundled);
        Ok(())
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn release_builds_reject_unverified_siblings_and_use_only_the_trusted_fallback()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("release-helper-trust")?;
        let current_executable = root
            .path
            .join("Wayfinder.app/Contents/Helpers/wayfinder-router");
        let sibling = root.path.join("Wayfinder.app/Contents/Helpers/codex");
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&sibling)?;
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.current_executable = Some(current_executable);
        discovery.chatgpt_helper = chatgpt.clone();

        assert_eq!(discover_helper(&discovery)?, chatgpt);

        discovery.chatgpt_helper_trust = reject_helper;
        assert_eq!(
            discover_helper(&discovery),
            Err(CodexAdapterSetupError::HelperUnavailable)
        );
        Ok(())
    }

    #[test]
    fn invalid_explicit_helper_never_falls_through_to_an_implicit_helper()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("invalid-explicit")?;
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(PathBuf::from("relative-codex"));
        discovery.chatgpt_helper = chatgpt;

        assert_eq!(
            discover_helper(&discovery),
            Err(CodexAdapterSetupError::InvalidHelper)
        );
        Ok(())
    }

    #[test]
    fn installed_chatgpt_helper_is_the_only_implicit_fallback()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("chatgpt-fallback")?;
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.current_executable = Some(root.path.join("bin/wayfinder-router"));
        discovery.chatgpt_helper = chatgpt.clone();

        assert_eq!(discover_helper(&discovery)?, chatgpt);

        discovery.chatgpt_helper = root.path.join("missing");
        assert_eq!(
            discover_helper(&discovery),
            Err(CodexAdapterSetupError::HelperUnavailable)
        );
        Ok(())
    }

    #[test]
    fn untrusted_chatgpt_fallback_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("untrusted-chatgpt")?;
        let chatgpt = root.path.join("ChatGPT.app/Contents/Resources/codex");
        executable(&chatgpt)?;
        let mut discovery = inputs(&root.path);
        discovery.chatgpt_helper = chatgpt;
        discovery.chatgpt_helper_trust = reject_helper;

        assert_eq!(
            discover_helper(&discovery),
            Err(CodexAdapterSetupError::HelperUnavailable)
        );
        Ok(())
    }

    #[test]
    fn helper_symlinks_and_wrong_signing_teams_are_rejected()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("helper-trust")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&helper, root.path.join("codex-link"))?;
            assert!(!helper_is_executable(&root.path.join("codex-link")));
        }
        assert!(codesign_team_matches(
            b"Identifier=codex\nTeamIdentifier=2DC432GLL2\n"
        ));
        assert!(!codesign_team_matches(
            b"Identifier=codex\nTeamIdentifier=ATTACKER\n"
        ));
        Ok(())
    }

    #[test]
    fn acl_detection_is_fail_closed() {
        assert!(!permissions_token_has_acl(
            b"drwx------ 3 user staff 96 Jul 18 00:00 Codex\n"
        ));
        assert!(permissions_token_has_acl(
            b"drwx------+ 3 user staff 96 Jul 18 00:00 Codex\n"
        ));
        assert!(permissions_token_has_acl(b""));
    }

    #[cfg(unix)]
    #[test]
    fn owner_validation_rejects_a_different_uid() -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::MetadataExt;

        let root = TestDirectory::new("owner-check")?;
        let metadata = fs::metadata(root.path.join("home"))?;
        assert_eq!(
            verify_owner(&metadata, metadata.uid().wrapping_add(1)),
            Err(CodexAdapterSetupError::PrivateStorageUnavailable)
        );
        Ok(())
    }

    #[test]
    fn provider_not_configured_is_inert() -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("inert")?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(PathBuf::from("relative-invalid-helper"));
        let expected_home = root
            .path
            .join("home/Library/Application Support/Wayfinder/Codex");

        assert_eq!(prepare_runtime_paths(false, &discovery)?, None);
        assert!(build_codex_app_server_adapter(false, "").is_ok_and(|value| value.is_none()));
        assert!(!expected_home.exists());
        Ok(())
    }

    #[tokio::test]
    async fn unavailable_control_reports_truth_without_claiming_mutations()
    -> Result<(), Box<dyn std::error::Error>> {
        let control = UnavailableCodexControl::new();

        assert_eq!(control.account().await?, CodexAccountState::Unavailable);
        for result in [
            control
                .models()
                .await
                .map(|_| CodexAccountState::Unavailable),
            control.login(CodexLoginFlow::Browser).await,
            control.cancel_login("login-id").await,
            control.logout().await,
        ] {
            assert_eq!(
                result
                    .err()
                    .ok_or("degraded control unexpectedly reported success")?
                    .to_string(),
                "managed ChatGPT account operation failed"
            );
        }
        Ok(())
    }

    #[test]
    fn default_store_is_private_and_never_reuses_dot_codex()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("private-store")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let paths = prepare_runtime_paths(true, &discovery)?
            .ok_or("configured provider did not produce runtime paths")?;

        let ordinary_home = root.path.join("home/.codex");
        assert!(!paths.codex_home.starts_with(&ordinary_home));
        assert_eq!(
            paths.codex_home,
            root.path
                .join("home/Library/Application Support/Wayfinder/Codex")
        );
        assert_eq!(paths.workspace, paths.codex_home.join("chat-workspace"));
        assert_eq!(
            fs::read(paths.codex_home.join(STORE_MARKER))?,
            STORE_MARKER_CONTENTS
        );
        assert_eq!(
            prepare_runtime_paths(true, &discovery)?,
            Some(paths.clone())
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&paths.codex_home)?.permissions().mode() & 0o777,
                0o700
            );
            assert_eq!(
                fs::metadata(&paths.workspace)?.permissions().mode() & 0o777,
                0o700
            );
            assert_eq!(
                fs::metadata(paths.codex_home.join(STORE_MARKER))?
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
        Ok(())
    }

    #[test]
    fn an_orphaned_staging_directory_does_not_poison_store_creation()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("orphaned-stage")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let parent = root.path.join("home/Library/Application Support/Wayfinder");
        fs::create_dir_all(&parent)?;
        let orphan = parent.join(".wayfinder-codex-store-stage-interrupted");
        fs::create_dir(&orphan)?;
        fs::write(orphan.join("partial"), b"not a credential store")?;

        let paths = prepare_runtime_paths(true, &discovery)?
            .ok_or("configured provider did not produce runtime paths")?;

        assert!(orphan.exists());
        assert_eq!(
            fs::read(paths.codex_home.join(STORE_MARKER))?,
            STORE_MARKER_CONTENTS
        );
        Ok(())
    }

    #[test]
    fn shared_codex_locations_are_rejected() -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("shared-store")?;
        let user_home = root.path.join("home");

        assert_eq!(
            reject_shared_codex_home(&user_home.join(".codex/wayfinder"), &user_home),
            Err(CodexAdapterSetupError::SharedCodexHome)
        );
        assert_eq!(
            reject_shared_codex_home(&user_home, &user_home),
            Err(CodexAdapterSetupError::SharedCodexHome)
        );
        Ok(())
    }

    #[test]
    fn parent_components_cannot_hide_a_dot_codex_override() -> Result<(), Box<dyn std::error::Error>>
    {
        let root = TestDirectory::new("parent-store")?;
        let user_home = root.path.join("home");

        assert_eq!(
            reject_shared_codex_home(&user_home.join("Library/../.codex/wayfinder"), &user_home,),
            Err(CodexAdapterSetupError::SharedCodexHome)
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn symlink_alias_into_dot_codex_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::symlink;

        let root = TestDirectory::new("symlink-store")?;
        let user_home = root.path.join("home");
        let ordinary_home = user_home.join(".codex");
        fs::create_dir_all(&ordinary_home)?;
        let alias = root.path.join("isolated-looking-store");
        symlink(&ordinary_home, &alias)?;

        assert_eq!(
            reject_shared_codex_home(&alias.join("wayfinder"), &user_home),
            Err(CodexAdapterSetupError::SharedCodexHome)
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn symlink_inside_application_support_cannot_redirect_the_owned_store()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::symlink;

        let root = TestDirectory::new("application-support-symlink")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let application_support = root.path.join("home/Library/Application Support");
        let redirected = root.path.join("redirected-wayfinder");
        fs::create_dir_all(&application_support)?;
        fs::create_dir_all(&redirected)?;
        symlink(&redirected, application_support.join("Wayfinder"))?;

        assert_eq!(
            prepare_runtime_paths(true, &discovery),
            Err(CodexAdapterSetupError::PrivateStorageUnavailable)
        );
        assert!(!redirected.join("Codex").exists());
        Ok(())
    }

    #[test]
    fn nonempty_workspace_fails_closed() -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("workspace-content")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let codex_home = root
            .path
            .join("home/Library/Application Support/Wayfinder/Codex");
        let owner = user_home_owner(&root.path.join("home"))?;
        prepare_owned_store(&codex_home, owner)?;
        let workspace = codex_home.join("chat-workspace");
        prepare_private_directory(&workspace, owner)?;
        fs::write(workspace.join("unexpected.txt"), b"content")?;

        assert_eq!(
            prepare_runtime_paths(true, &discovery),
            Err(CodexAdapterSetupError::WorkspaceNotEmpty)
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn an_existing_unowned_store_fails_closed_without_chmod()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("unowned-store")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let codex_home = root
            .path
            .join("home/Library/Application Support/Wayfinder/Codex");
        fs::create_dir_all(&codex_home)?;
        fs::set_permissions(&codex_home, fs::Permissions::from_mode(0o755))?;

        assert_eq!(
            prepare_runtime_paths(true, &discovery),
            Err(CodexAdapterSetupError::PrivateStorageUnavailable)
        );
        assert_eq!(
            fs::metadata(codex_home)?.permissions().mode() & 0o777,
            0o755
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn an_existing_private_directory_still_requires_the_ownership_marker()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("unmarked-store")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let codex_home = root
            .path
            .join("home/Library/Application Support/Wayfinder/Codex");
        fs::create_dir_all(&codex_home)?;
        fs::set_permissions(&codex_home, fs::Permissions::from_mode(0o700))?;

        assert_eq!(
            prepare_runtime_paths(true, &discovery),
            Err(CodexAdapterSetupError::PrivateStorageUnavailable)
        );
        assert!(!codex_home.join(STORE_MARKER).exists());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn a_hard_link_cannot_impersonate_the_ownership_marker()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("hard-linked-marker")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);
        let codex_home = root
            .path
            .join("home/Library/Application Support/Wayfinder/Codex");
        fs::create_dir_all(&codex_home)?;
        fs::set_permissions(&codex_home, fs::Permissions::from_mode(0o700))?;
        let source = root.path.join("marker-source");
        fs::write(&source, STORE_MARKER_CONTENTS)?;
        fs::set_permissions(&source, fs::Permissions::from_mode(0o600))?;
        fs::hard_link(source, codex_home.join(STORE_MARKER))?;

        assert_eq!(
            prepare_runtime_paths(true, &discovery),
            Err(CodexAdapterSetupError::PrivateStorageUnavailable)
        );
        Ok(())
    }

    #[test]
    fn setup_errors_never_echo_sensitive_paths() {
        let sensitive = "/Users/private/account@example.com/token-store";
        for error in [
            CodexAdapterSetupError::HelperUnavailable,
            CodexAdapterSetupError::InvalidHelper,
            CodexAdapterSetupError::UserHomeUnavailable,
            CodexAdapterSetupError::SharedCodexHome,
            CodexAdapterSetupError::PrivateStorageUnavailable,
            CodexAdapterSetupError::WorkspaceNotEmpty,
            CodexAdapterSetupError::RuntimeUnavailable,
        ] {
            assert!(!error.to_string().contains(sensitive));
            assert!(!error.to_string().contains("account@example.com"));
        }
    }

    #[test]
    fn account_snapshots_map_to_the_normalized_gateway_states() {
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::SignedOut,
                    email: None,
                    plan_type: None,
                },
                None,
            ),
            CodexAccountState::SignedOut
        );
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::Connected,
                    email: Some("person@example.com".to_owned()),
                    plan_type: Some("plus".to_owned()),
                },
                None,
            ),
            CodexAccountState::Connected(CodexConnectedAccount {
                email: Some("person@example.com".to_owned()),
                plan: Some("plus".to_owned()),
            })
        );
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::ReauthRequired,
                    email: None,
                    plan_type: None,
                },
                None,
            ),
            CodexAccountState::ReauthenticationRequired
        );
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::Unavailable,
                    email: None,
                    plan_type: None,
                },
                None,
            ),
            CodexAccountState::Unavailable
        );
    }

    #[test]
    fn awaiting_account_snapshots_require_matching_cached_login_details() {
        let browser = LoginStart::Browser {
            login_id: "login-browser".to_owned(),
            auth_url: "https://example.test/browser".to_owned(),
        };
        let device = LoginStart::DeviceCode {
            login_id: "login-device".to_owned(),
            verification_url: "https://example.test/device".to_owned(),
            user_code: "ABCD-EFGH".to_owned(),
        };
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::AwaitingBrowser,
                    email: None,
                    plan_type: None,
                },
                Some(&browser),
            ),
            CodexAccountState::AwaitingBrowser {
                login_id: "login-browser".to_owned(),
                url: "https://example.test/browser".to_owned(),
            }
        );
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::AwaitingDeviceCode,
                    email: None,
                    plan_type: None,
                },
                Some(&device),
            ),
            CodexAccountState::AwaitingDeviceCode {
                login_id: "login-device".to_owned(),
                url: "https://example.test/device".to_owned(),
                user_code: "ABCD-EFGH".to_owned(),
            }
        );
        assert_eq!(
            normalize_account(
                AccountSnapshot {
                    status: AccountStatus::AwaitingBrowser,
                    email: None,
                    plan_type: None,
                },
                Some(&device),
            ),
            CodexAccountState::Unavailable
        );
    }

    #[test]
    fn login_starts_and_visible_models_map_without_runtime_only_fields() {
        let browser = LoginStart::Browser {
            login_id: "login-browser".to_owned(),
            auth_url: "https://example.test/browser".to_owned(),
        };
        assert_eq!(
            normalize_login(&browser),
            CodexAccountState::AwaitingBrowser {
                login_id: "login-browser".to_owned(),
                url: "https://example.test/browser".to_owned(),
            }
        );
        assert_eq!(
            normalize_models(vec![
                ModelInfo {
                    id: "gpt-5.6-sol".to_owned(),
                    display_name: "GPT-5.6 Sol".to_owned(),
                    description: "runtime-only description".to_owned(),
                    is_default: true,
                    hidden: false,
                },
                ModelInfo {
                    id: "hidden".to_owned(),
                    display_name: "Hidden".to_owned(),
                    description: String::new(),
                    is_default: false,
                    hidden: true,
                },
                ModelInfo {
                    id: "unnamed".to_owned(),
                    display_name: String::new(),
                    description: String::new(),
                    is_default: false,
                    hidden: false,
                },
            ]),
            vec![
                CodexModel {
                    id: "gpt-5.6-sol".to_owned(),
                    display_name: Some("GPT-5.6 Sol".to_owned()),
                },
                CodexModel {
                    id: "unnamed".to_owned(),
                    display_name: None,
                },
            ]
        );
    }

    #[test]
    fn runtime_errors_are_collapsed_to_one_sanitized_control_failure() {
        for error in [
            CodexAppServerError::InvalidConfiguration,
            CodexAppServerError::RuntimeUnavailable,
            CodexAppServerError::InvalidRequest,
            CodexAppServerError::RequestTooLarge,
            CodexAppServerError::ResponseTooLarge,
            CodexAppServerError::MalformedProtocol,
            CodexAppServerError::LineTooLarge,
            CodexAppServerError::CorrelationFailed,
            CodexAppServerError::NotificationQueueFull,
            CodexAppServerError::TimedOut,
            CodexAppServerError::RequestRejected,
            CodexAppServerError::Busy,
            CodexAppServerError::AuthenticationRequired,
            CodexAppServerError::UnsupportedAuthentication,
            CodexAppServerError::LoginFailed,
            CodexAppServerError::LoginCancelled,
            CodexAppServerError::ModelUnavailable,
            CodexAppServerError::ForbiddenAction,
            CodexAppServerError::TurnFailed,
            CodexAppServerError::Interrupted,
            CodexAppServerError::EndOfStream,
            CodexAppServerError::InsecureCredentialStore,
        ] {
            assert_eq!(
                map_runtime_control_error(error).to_string(),
                "managed ChatGPT account operation failed"
            );
        }
    }

    #[test]
    fn configured_constructor_builds_a_lazy_cloneable_adapter()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = TestDirectory::new("constructor")?;
        let helper = root.path.join("helper/codex");
        executable(&helper)?;
        let mut discovery = inputs(&root.path);
        discovery.explicit_helper = Some(helper);

        let adapter = build_codex_app_server_adapter_with(true, "0.1.0", &discovery)?
            .ok_or("configured provider did not produce an adapter")?;
        let _manager = adapter.manager();
        let _clone = adapter.clone();
        Ok(())
    }
}
