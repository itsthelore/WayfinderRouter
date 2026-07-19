use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;

#[cfg(unix)]
use std::process::Command as StdCommand;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

use crate::{AppServerTransport, CodexAppServerError, RuntimeConfig, TransportFuture};

const MAX_STDERR_TAIL_BYTES: usize = 16_384;

#[cfg(unix)]
type OwnerId = u32;
#[cfg(not(unix))]
type OwnerId = ();

pub(crate) fn spawn_process(
    config: &RuntimeConfig,
) -> TransportFuture<'_, Box<dyn AppServerTransport>> {
    Box::pin(async move {
        let owner = prepare_runtime(config)?;

        let mut command = Command::new(&config.helper_path);
        let temporary_directory = config.codex_home.join("runtime-tmp");
        create_private_directory(&temporary_directory, owner)?;
        command
            .arg("app-server")
            .arg("--strict-config")
            .arg("--listen")
            .arg("stdio://")
            .current_dir(&config.workspace)
            .env_clear()
            .env("HOME", &config.codex_home)
            .env("CODEX_HOME", &config.codex_home)
            .env("TMPDIR", temporary_directory)
            .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
            .env("LANG", "en_US.UTF-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        let mut child = command
            .spawn()
            .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
        let stdin = child
            .stdin
            .take()
            .ok_or(CodexAppServerError::RuntimeUnavailable)?;
        let stdout = child
            .stdout
            .take()
            .ok_or(CodexAppServerError::RuntimeUnavailable)?;
        let stderr = child
            .stderr
            .take()
            .ok_or(CodexAppServerError::RuntimeUnavailable)?;
        let stderr_tail = Arc::new(Mutex::new(VecDeque::new()));
        let tail_writer = Arc::clone(&stderr_tail);
        tokio::spawn(async move {
            let mut stderr = stderr;
            let mut buffer = [0_u8; 1_024];
            loop {
                let count = match stderr.read(&mut buffer).await {
                    Ok(0) | Err(_) => return,
                    Ok(count) => count,
                };
                let mut tail = tail_writer.lock().await;
                for byte in &buffer[..count] {
                    if tail.len() == MAX_STDERR_TAIL_BYTES {
                        let _ = tail.pop_front();
                    }
                    tail.push_back(*byte);
                }
            }
        });

        Ok(Box::new(ProcessTransport {
            child,
            stdin,
            stdout,
            _stderr_tail: stderr_tail,
        }) as Box<dyn AppServerTransport>)
    })
}

fn prepare_runtime(config: &RuntimeConfig) -> Result<OwnerId, CodexAppServerError> {
    let owner = effective_owner()?;
    prepare_runtime_for_owner(config, owner)?;
    Ok(owner)
}

fn prepare_runtime_for_owner(
    config: &RuntimeConfig,
    owner: OwnerId,
) -> Result<(), CodexAppServerError> {
    verify_regular_executable(&config.helper_path)?;
    create_private_directory(&config.codex_home, owner)?;
    create_private_directory(&config.workspace, owner)?;
    verify_empty_directory(&config.workspace)?;
    verify_private_file_or_absent(&config.codex_home.join("auth.json"), owner)?;
    let config_path = config.codex_home.join("config.toml");
    prepare_isolated_config(&config_path, owner)?;
    Ok(())
}

fn isolated_config() -> &'static str {
    r#"forced_login_method = "chatgpt"
cli_auth_credentials_store = "file"
approval_policy = "never"
default_permissions = "wayfinder-chat"
web_search = "disabled"
check_for_update_on_startup = false

[analytics]
enabled = false

[history]
persistence = "none"

[features]
apps = false
auth_elicitation = false
browser_use = false
browser_use_external = false
browser_use_full_cdp_access = false
chronicle = false
code_mode = false
code_mode_host = false
code_mode_only = false
computer_use = false
deferred_executor = false
enable_fanout = false
enable_mcp_apps = false
exec_permission_approvals = false
external_migration = false
goals = false
guardian_approval = false
hooks = false
image_generation = false
in_app_browser = false
memories = false
mentions_v2 = false
multi_agent = false
multi_agent_v2 = false
remote_plugin = false
plugins = false
plugin_sharing = false
shell_tool = false
skill_search = false
shell_snapshot = false
unified_exec = false
network_proxy = false
request_permissions_tool = false
skill_mcp_dependency_install = false
standalone_web_search = false
tool_call_mcp_elicitation = false
tool_suggest = false
workspace_dependencies = false

[permissions.wayfinder-chat]
description = "Wayfinder text-only chat: read the empty isolated workspace and nothing else."

[permissions.wayfinder-chat.filesystem.":workspace_roots"]
"." = "read"

[permissions.wayfinder-chat.network]
enabled = false
"#
}

fn create_private_directory(path: &Path, owner: OwnerId) -> Result<(), CodexAppServerError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            verify_private_directory_metadata(path, &metadata, owner)?;
            return Ok(());
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(CodexAppServerError::RuntimeUnavailable),
    }
    fs::create_dir_all(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    let metadata =
        fs::symlink_metadata(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    set_private_directory_permissions(path)?;
    let metadata =
        fs::symlink_metadata(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    verify_private_directory_metadata(path, &metadata, owner)
}

fn verify_regular_executable(path: &Path) -> Result<(), CodexAppServerError> {
    if !path.is_absolute() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    let metadata =
        fs::symlink_metadata(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    let canonical = fs::canonicalize(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    if canonical != path {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    Ok(())
}

fn prepare_isolated_config(path: &Path, owner: OwnerId) -> Result<(), CodexAppServerError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            verify_private_file_metadata(path, &metadata, owner)?;
            let contents = fs::read(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            if contents != isolated_config().as_bytes() {
                return Err(CodexAppServerError::InvalidConfiguration);
            }
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options
                .open(path)
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            file.write_all(isolated_config().as_bytes())
                .and_then(|()| file.sync_all())
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            set_private_file_permissions(path)?;
            let metadata =
                fs::symlink_metadata(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            verify_private_file_metadata(path, &metadata, owner)
        }
        Err(_) => Err(CodexAppServerError::RuntimeUnavailable),
    }
}

fn verify_private_file_or_absent(path: &Path, owner: OwnerId) -> Result<(), CodexAppServerError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => verify_private_file_metadata(path, &metadata, owner),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(CodexAppServerError::RuntimeUnavailable),
    }
}

fn verify_private_directory_metadata(
    path: &Path,
    metadata: &fs::Metadata,
    owner: OwnerId,
) -> Result<(), CodexAppServerError> {
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o777 != 0o700 {
            return Err(CodexAppServerError::InvalidConfiguration);
        }
    }
    verify_owner(metadata, owner)?;
    verify_no_acl(path)
}

fn verify_private_file_metadata(
    path: &Path,
    metadata: &fs::Metadata,
    owner: OwnerId,
) -> Result<(), CodexAppServerError> {
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if metadata.permissions().mode() & 0o777 != 0o600 || metadata.nlink() != 1 {
            return Err(CodexAppServerError::InvalidConfiguration);
        }
    }
    verify_owner(metadata, owner)?;
    verify_no_acl(path)
}

fn verify_empty_directory(path: &Path) -> Result<(), CodexAppServerError> {
    let mut entries = fs::read_dir(path).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    if entries.next().is_some() {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn effective_owner() -> Result<OwnerId, CodexAppServerError> {
    let output = StdCommand::new("/usr/bin/id")
        .arg("-u")
        .env_clear()
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    if !output.status.success() {
        return Err(CodexAppServerError::RuntimeUnavailable);
    }
    parse_owner_id(&output.stdout)
}

#[cfg(unix)]
fn parse_owner_id(output: &[u8]) -> Result<OwnerId, CodexAppServerError> {
    if output.is_empty() || output.len() > 32 {
        return Err(CodexAppServerError::RuntimeUnavailable);
    }
    let text = std::str::from_utf8(output).map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
    let value = text.strip_suffix('\n').unwrap_or(text);
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(CodexAppServerError::RuntimeUnavailable);
    }
    value
        .parse::<OwnerId>()
        .map_err(|_| CodexAppServerError::RuntimeUnavailable)
}

#[cfg(not(unix))]
pub(crate) fn effective_owner() -> Result<OwnerId, CodexAppServerError> {
    Ok(())
}

#[cfg(test)]
fn private_directory_owner(path: &Path) -> Result<OwnerId, CodexAppServerError> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(CodexAppServerError::InvalidConfiguration);
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

fn verify_owner(metadata: &fs::Metadata, expected: OwnerId) -> Result<(), CodexAppServerError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != expected {
            return Err(CodexAppServerError::InvalidConfiguration);
        }
    }
    #[cfg(not(unix))]
    let _ = (metadata, expected);
    Ok(())
}

#[cfg(target_os = "macos")]
fn verify_no_acl(path: &Path) -> Result<(), CodexAppServerError> {
    let output = StdCommand::new("/bin/ls")
        .arg("-lde")
        .arg(path)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| CodexAppServerError::InvalidConfiguration)?;
    if !output.status.success() || permissions_token_has_acl(&output.stdout) {
        return Err(CodexAppServerError::InvalidConfiguration);
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn verify_no_acl(_path: &Path) -> Result<(), CodexAppServerError> {
    Ok(())
}

#[cfg(any(target_os = "macos", test))]
fn permissions_token_has_acl(output: &[u8]) -> bool {
    String::from_utf8_lossy(output)
        .split_whitespace()
        .next()
        .is_none_or(|token| token.contains('+'))
}

#[cfg(unix)]
fn set_private_directory_permissions(path: &Path) -> Result<(), CodexAppServerError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|_| CodexAppServerError::RuntimeUnavailable)
}

#[cfg(not(unix))]
fn set_private_directory_permissions(_path: &Path) -> Result<(), CodexAppServerError> {
    Ok(())
}

#[cfg(unix)]
fn set_private_file_permissions(path: &Path) -> Result<(), CodexAppServerError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|_| CodexAppServerError::RuntimeUnavailable)
}

#[cfg(not(unix))]
fn set_private_file_permissions(_path: &Path) -> Result<(), CodexAppServerError> {
    Ok(())
}

struct ProcessTransport {
    child: Child,
    stdin: ChildStdin,
    stdout: ChildStdout,
    _stderr_tail: Arc<Mutex<VecDeque<u8>>>,
}

impl AppServerTransport for ProcessTransport {
    fn write<'a>(&'a mut self, bytes: &'a [u8]) -> TransportFuture<'a, ()> {
        Box::pin(async move {
            self.stdin
                .write_all(bytes)
                .await
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
            self.stdin
                .flush()
                .await
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)
        })
    }

    fn read<'a>(&'a mut self, buffer: &'a mut [u8]) -> TransportFuture<'a, usize> {
        Box::pin(async move {
            self.stdout
                .read(buffer)
                .await
                .map_err(|_| CodexAppServerError::RuntimeUnavailable)
        })
    }

    fn terminate(&mut self) -> TransportFuture<'_, ()> {
        Box::pin(async move {
            match self.child.try_wait() {
                Ok(Some(_)) => Ok(()),
                Ok(None) => {
                    self.child
                        .start_kill()
                        .map_err(|_| CodexAppServerError::RuntimeUnavailable)?;
                    self.child
                        .wait()
                        .await
                        .map(|_| ())
                        .map_err(|_| CodexAppServerError::RuntimeUnavailable)
                }
                Err(_) => Err(CodexAppServerError::RuntimeUnavailable),
            }
        })
    }
}

impl Drop for ProcessTransport {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

#[cfg(all(test, unix))]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    static TEST_COUNTER: AtomicUsize = AtomicUsize::new(0);

    struct TestDirectory {
        path: std::path::PathBuf,
    }

    impl TestDirectory {
        fn new(name: &str) -> Result<Self, Box<dyn std::error::Error>> {
            let number = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "wayfinder-codex-process-{name}-{}-{number}",
                std::process::id()
            ));
            fs::create_dir_all(&path)?;
            Ok(Self { path })
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn existing_wrong_mode_directory_is_rejected_without_chmod()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("directory-mode")?;
        fs::set_permissions(&root.path, fs::Permissions::from_mode(0o755))?;
        let owner = private_directory_owner(&root.path)?;

        assert_eq!(
            create_private_directory(&root.path, owner),
            Err(CodexAppServerError::InvalidConfiguration)
        );
        assert_eq!(
            fs::metadata(&root.path)?.permissions().mode() & 0o777,
            0o755
        );
        Ok(())
    }

    #[test]
    fn existing_non_wayfinder_config_is_rejected_without_overwrite()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("config-preservation")?;
        let path = root.path.join("config.toml");
        fs::write(&path, b"personal = true\n")?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
        let owner = private_directory_owner(&root.path)?;

        assert_eq!(
            prepare_isolated_config(&path, owner),
            Err(CodexAppServerError::InvalidConfiguration)
        );
        assert_eq!(fs::read(path)?, b"personal = true\n");
        Ok(())
    }

    #[test]
    fn hard_linked_private_file_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;

        let root = TestDirectory::new("hard-link")?;
        let source = root.path.join("source");
        let linked = root.path.join("linked");
        fs::write(&source, b"secret")?;
        fs::set_permissions(&source, fs::Permissions::from_mode(0o600))?;
        fs::hard_link(&source, &linked)?;

        let metadata = fs::symlink_metadata(linked)?;
        let owner = private_directory_owner(&root.path)?;
        assert_eq!(
            verify_private_file_metadata(&root.path.join("linked"), &metadata, owner),
            Err(CodexAppServerError::InvalidConfiguration)
        );
        Ok(())
    }

    #[test]
    fn acl_detection_is_fail_closed() {
        assert!(!permissions_token_has_acl(
            b"-rw------- 1 user staff 2 Jul 18 00:00 auth.json\n"
        ));
        assert!(permissions_token_has_acl(
            b"-rw-------+ 1 user staff 2 Jul 18 00:00 auth.json\n"
        ));
        assert!(permissions_token_has_acl(b""));
    }

    #[test]
    fn private_metadata_rejects_a_different_owner() -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::MetadataExt;

        let root = TestDirectory::new("owner")?;
        let metadata = fs::metadata(&root.path)?;
        assert_eq!(
            verify_owner(&metadata, metadata.uid().wrapping_add(1)),
            Err(CodexAppServerError::InvalidConfiguration)
        );
        Ok(())
    }

    #[test]
    fn effective_owner_parser_is_strict_and_bounded() {
        assert_eq!(parse_owner_id(b"501\n"), Ok(501));
        assert_eq!(parse_owner_id(b"0"), Ok(0));
        for invalid in [
            b"".as_slice(),
            b"501 \n".as_slice(),
            b" 501\n".as_slice(),
            b"-1\n".as_slice(),
            b"501\n\n".as_slice(),
            b"4294967296\n".as_slice(),
            &[0xff],
        ] {
            assert_eq!(
                parse_owner_id(invalid),
                Err(CodexAppServerError::RuntimeUnavailable)
            );
        }
    }

    #[test]
    fn runtime_artifacts_are_anchored_to_the_process_owner()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};

        let root = TestDirectory::new("runtime-owner")?;
        fs::set_permissions(&root.path, fs::Permissions::from_mode(0o700))?;
        let owner = fs::metadata(&root.path)?.uid();
        assert_eq!(effective_owner()?, owner);

        let auth_path = root.path.join("auth.json");
        fs::write(&auth_path, b"{}")?;
        fs::set_permissions(&auth_path, fs::Permissions::from_mode(0o600))?;
        let config = RuntimeConfig {
            helper_path: fs::canonicalize(std::env::current_exe()?)?,
            codex_home: root.path.clone(),
            workspace: root.path.join("workspace"),
            client_version: "test".to_owned(),
            limits: crate::RuntimeLimits::default(),
        };

        prepare_runtime_for_owner(&config, owner)?;
        let home_metadata = fs::symlink_metadata(&config.codex_home)?;
        let auth_metadata = fs::symlink_metadata(&auth_path)?;
        assert_eq!(
            crate::verify_auth_store_metadata(&home_metadata, &auth_metadata, owner),
            Ok(())
        );
        assert_eq!(
            crate::verify_auth_store_metadata(
                &home_metadata,
                &auth_metadata,
                owner.wrapping_add(1)
            ),
            Err(CodexAppServerError::InsecureCredentialStore)
        );
        for (path, mode) in [
            (config.codex_home.clone(), 0o700),
            (config.workspace.clone(), 0o700),
            (config.codex_home.join("config.toml"), 0o600),
            (auth_path, 0o600),
        ] {
            let metadata = fs::symlink_metadata(&path)?;
            assert_eq!(metadata.uid(), owner, "unexpected owner for {path:?}");
            assert_eq!(
                metadata.permissions().mode() & 0o777,
                mode,
                "unexpected mode for {path:?}"
            );
        }

        assert_eq!(
            prepare_runtime_for_owner(&config, owner.wrapping_add(1)),
            Err(CodexAppServerError::InvalidConfiguration)
        );
        Ok(())
    }

    #[test]
    fn isolated_config_keeps_auth_permissions_network_and_tools_closed() {
        let config = isolated_config();
        for required in [
            "forced_login_method = \"chatgpt\"",
            "approval_policy = \"never\"",
            "default_permissions = \"wayfinder-chat\"",
            "skill_search = false",
            "shell_tool = false",
            "unified_exec = false",
            "plugins = false",
            "workspace_dependencies = false",
            "[permissions.wayfinder-chat.network]\nenabled = false",
        ] {
            assert!(
                config.contains(required),
                "missing isolated config: {required}"
            );
        }
        assert!(!config.contains("chatgptAuthTokens"));
        assert!(!config.contains("api_key"));
        assert!(!config.contains("= true"));
    }
}
