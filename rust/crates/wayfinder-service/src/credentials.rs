//! Bounded compatibility resolver for legacy `api_key_cmd` references.
//!
//! The command string is an existing configuration contract. Its output is
//! never included in an error or retained outside the redacted secret wrapper.

use std::process::Stdio;
use std::time::Duration;

use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::{Child, Command};
use wayfinder_providers::openai_compat::SecretValue;

/// Default command execution deadline.
pub const DEFAULT_COMMAND_TIMEOUT: Duration = Duration::from_secs(2);
/// Default per-stream output ceiling (64 KiB).
pub const DEFAULT_MAX_OUTPUT_BYTES: usize = 64 * 1_024;
/// Default configured command-reference ceiling (8 KiB).
pub const DEFAULT_MAX_COMMAND_BYTES: usize = 8 * 1_024;

/// Explicit process and memory limits for legacy resolution.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LegacyCommandLimits {
    /// Wall-clock deadline including process startup and pipe draining.
    pub timeout: Duration,
    /// Independent stdout and stderr byte ceiling.
    pub max_output_bytes: usize,
    /// Configured command string byte ceiling.
    pub max_command_bytes: usize,
}

impl Default for LegacyCommandLimits {
    fn default() -> Self {
        Self {
            timeout: DEFAULT_COMMAND_TIMEOUT,
            max_output_bytes: DEFAULT_MAX_OUTPUT_BYTES,
            max_command_bytes: DEFAULT_MAX_COMMAND_BYTES,
        }
    }
}

/// Sanitized resolver failure. No variant retains command or output text.
#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum LegacyCredentialError {
    /// One configured bound was zero.
    #[error("legacy credential resolver limits must be positive")]
    InvalidLimits,
    /// The configured reference exceeded the command bound.
    #[error("legacy credential command reference is too large")]
    CommandTooLarge,
    /// The platform shell process could not be started.
    #[error("legacy credential command could not be started")]
    SpawnFailed,
    /// The command exceeded its wall-clock deadline and was terminated.
    #[error("legacy credential command timed out")]
    TimedOut,
    /// stdout or stderr exceeded the independent memory bound.
    #[error("legacy credential command output exceeded its bound")]
    OutputTooLarge,
    /// Pipe I/O or process waiting failed.
    #[error("legacy credential command I/O failed")]
    ProcessIo,
    /// The command returned a non-success status.
    #[error("legacy credential command failed")]
    NonZeroExit,
    /// Successful output was empty after ASCII-whitespace trimming.
    #[error("legacy credential command returned an empty value")]
    EmptyOutput,
}

/// Run one legacy secret-reference command under strict bounds.
pub async fn resolve_legacy_command(
    command_reference: &str,
    limits: LegacyCommandLimits,
) -> Result<SecretValue, LegacyCredentialError> {
    validate_limits(command_reference, limits)?;
    let mut command = platform_command(command_reference);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = command
        .spawn()
        .map_err(|_| LegacyCredentialError::SpawnFailed)?;
    let stdout = child
        .stdout
        .take()
        .ok_or(LegacyCredentialError::ProcessIo)?;
    let stderr = child
        .stderr
        .take()
        .ok_or(LegacyCredentialError::ProcessIo)?;

    let execution = async {
        let status = async {
            child
                .wait()
                .await
                .map_err(|_| LegacyCredentialError::ProcessIo)
        };
        let output = async {
            tokio::try_join!(
                read_bounded(stdout, limits.max_output_bytes),
                read_bounded(stderr, limits.max_output_bytes)
            )
        };
        let (status, output) = tokio::try_join!(status, output)?;
        Ok::<_, LegacyCredentialError>((status, output))
    };

    let (status, (mut stdout, mut stderr)) =
        match tokio::time::timeout(limits.timeout, execution).await {
            Ok(Ok(result)) => result,
            Ok(Err(error)) => {
                terminate(&mut child).await;
                return Err(error);
            }
            Err(_) => {
                terminate(&mut child).await;
                return Err(LegacyCredentialError::TimedOut);
            }
        };
    if !status.success() {
        stdout.fill(0);
        stderr.fill(0);
        return Err(LegacyCredentialError::NonZeroExit);
    }
    stderr.fill(0);
    let start = stdout
        .iter()
        .position(|byte| !byte.is_ascii_whitespace())
        .unwrap_or(stdout.len());
    let end = stdout
        .iter()
        .rposition(|byte| !byte.is_ascii_whitespace())
        .map_or(start, |index| index.saturating_add(1));
    if start == end {
        stdout.fill(0);
        return Err(LegacyCredentialError::EmptyOutput);
    }
    let secret = stdout[start..end].to_vec();
    stdout.fill(0);
    Ok(SecretValue::from_bytes(secret))
}

fn validate_limits(
    command_reference: &str,
    limits: LegacyCommandLimits,
) -> Result<(), LegacyCredentialError> {
    if limits.timeout.is_zero() || limits.max_output_bytes == 0 || limits.max_command_bytes == 0 {
        return Err(LegacyCredentialError::InvalidLimits);
    }
    if command_reference.len() > limits.max_command_bytes {
        return Err(LegacyCredentialError::CommandTooLarge);
    }
    Ok(())
}

#[cfg(unix)]
fn platform_command(command_reference: &str) -> Command {
    let mut command = Command::new("/bin/sh");
    command.arg("-c").arg(command_reference);
    command
}

#[cfg(windows)]
fn platform_command(command_reference: &str) -> Command {
    let mut command = Command::new("cmd.exe");
    command.arg("/D").arg("/S").arg("/C").arg(command_reference);
    command
}

async fn read_bounded<R>(mut reader: R, maximum: usize) -> Result<Vec<u8>, LegacyCredentialError>
where
    R: AsyncRead + Unpin,
{
    let mut output = Vec::new();
    let mut chunk = [0_u8; 4_096];
    loop {
        let count = match reader.read(&mut chunk).await {
            Ok(count) => count,
            Err(_) => {
                output.fill(0);
                return Err(LegacyCredentialError::ProcessIo);
            }
        };
        if count == 0 {
            return Ok(output);
        }
        if output.len().saturating_add(count) > maximum {
            output.fill(0);
            return Err(LegacyCredentialError::OutputTooLarge);
        }
        output.extend_from_slice(&chunk[..count]);
    }
}

async fn terminate(child: &mut Child) {
    let _ = child.start_kill();
    let _ = child.wait().await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounds_are_validated_before_process_start() {
        let limits = LegacyCommandLimits {
            max_command_bytes: 3,
            ..LegacyCommandLimits::default()
        };
        assert_eq!(
            validate_limits("four", limits),
            Err(LegacyCredentialError::CommandTooLarge)
        );
        let limits = LegacyCommandLimits {
            max_command_bytes: 4,
            timeout: Duration::ZERO,
            ..LegacyCommandLimits::default()
        };
        assert_eq!(
            validate_limits("four", limits),
            Err(LegacyCredentialError::InvalidLimits)
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn successful_output_moves_into_redacted_secret() -> Result<(), LegacyCredentialError> {
        let secret = resolve_legacy_command(
            "printf 'dummy-provider-value\\n'",
            LegacyCommandLimits::default(),
        )
        .await?;
        assert_eq!(secret.len(), "dummy-provider-value".len());
        assert_eq!(format!("{secret}"), "[REDACTED]");
        assert!(!format!("{secret:?}").contains("dummy-provider-value"));
        Ok(())
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn failure_timeout_and_output_bound_are_sanitized() {
        assert!(matches!(
            resolve_legacy_command("exit 7", LegacyCommandLimits::default()).await,
            Err(LegacyCredentialError::NonZeroExit)
        ));
        let timeout_limits = LegacyCommandLimits {
            timeout: Duration::from_millis(10),
            ..LegacyCommandLimits::default()
        };
        assert!(matches!(
            resolve_legacy_command("sleep 1", timeout_limits).await,
            Err(LegacyCredentialError::TimedOut)
        ));
        let output_limits = LegacyCommandLimits {
            max_output_bytes: 4,
            ..LegacyCommandLimits::default()
        };
        assert!(matches!(
            resolve_legacy_command("printf '12345'", output_limits).await,
            Err(LegacyCredentialError::OutputTooLarge)
        ));
        let rendered = format!("{:?}", LegacyCredentialError::NonZeroExit);
        assert!(!rendered.contains("exit 7"));
    }
}
