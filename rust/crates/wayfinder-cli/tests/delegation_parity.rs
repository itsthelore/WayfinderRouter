use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use uuid::Uuid;

const DELEGATED: &[&str] = &[
    "calibrate",
    "recalibrate",
    "webchat",
    "ui",
    "chat",
    "onboard",
    "judge",
    "init",
    "doctor",
    "config",
    "keys",
];

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new() -> Result<Self, Box<dyn std::error::Error>> {
        let path = std::env::temp_dir().join(format!("wayfinder-native-config-{}", Uuid::new_v4()));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }

    fn join(&self, path: impl AsRef<Path>) -> PathBuf {
        self.0.join(path)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn delegated_help_preserves_python_exit_and_stream_contracts()
-> Result<(), Box<dyn std::error::Error>> {
    let binary = env!("CARGO_BIN_EXE_wayfinder-router");
    let repository = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .ok_or("cannot locate repository root")?
        .to_path_buf();

    for command in DELEGATED {
        let python = Command::new("python3")
            .args(["-m", "wayfinder_router.cli", command, "--help"])
            .current_dir(&repository)
            .output()?;
        let rust = Command::new(binary)
            .args([command, "--help"])
            .env("WAYFINDER_PYTHON_EXECUTABLE", "python3")
            .current_dir(&repository)
            .output()?;
        assert_eq!(
            rust.status.code(),
            python.status.code(),
            "{command} exit code"
        );
        assert_eq!(rust.stdout, python.stdout, "{command} stdout");
        assert_eq!(rust.stderr, python.stderr, "{command} stderr");
    }
    Ok(())
}

#[test]
fn delegated_parse_failure_preserves_exit_two_and_stderr() -> Result<(), Box<dyn std::error::Error>>
{
    let binary = env!("CARGO_BIN_EXE_wayfinder-router");
    let repository = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .ok_or("cannot locate repository root")?
        .to_path_buf();
    let arguments = ["doctor", "--not-a-real-option"];
    let python = Command::new("python3")
        .args(["-m", "wayfinder_router.cli"])
        .args(arguments)
        .current_dir(&repository)
        .output()?;
    let rust = Command::new(binary)
        .args(arguments)
        .env("WAYFINDER_PYTHON_EXECUTABLE", "python3")
        .current_dir(repository)
        .output()?;
    assert_eq!(rust.status.code(), Some(2));
    assert_eq!(rust.status.code(), python.status.code());
    assert_eq!(rust.stdout, python.stdout);
    assert_eq!(rust.stderr, python.stderr);
    Ok(())
}

#[test]
fn desktop_routing_config_is_native_when_python_is_unavailable()
-> Result<(), Box<dyn std::error::Error>> {
    let binary = env!("CARGO_BIN_EXE_wayfinder-router");
    let directory = TestDirectory::new()?;
    let config = directory.join("wayfinder-router.toml");
    fs::write(
        &config,
        "[gateway]\noffline = true\n\n[routing]\nthreshold = 0.25\n",
    )?;
    let missing_python = directory.join("python-does-not-exist");

    let read = Command::new(binary)
        .args(["config", "read-routing", "--path"])
        .arg(&config)
        .env("WAYFINDER_PYTHON_EXECUTABLE", &missing_python)
        .output()?;
    assert!(
        read.status.success(),
        "{}",
        String::from_utf8_lossy(&read.stderr)
    );
    let payload: serde_json::Value = serde_json::from_slice(&read.stdout)?;
    assert_eq!(payload["mode"], "binary");
    assert_eq!(payload["threshold"], 0.25);

    let mut child = Command::new(binary)
        .args(["config", "apply-routing", "--path"])
        .arg(&config)
        .env("WAYFINDER_PYTHON_EXECUTABLE", &missing_python)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut stdin = child.stdin.take().ok_or("config child has no stdin")?;
    stdin.write_all(b"[routing]\nthreshold = 0.8\n")?;
    drop(stdin);
    let applied = child.wait_with_output()?;
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    let updated = fs::read_to_string(config)?;
    assert!(updated.contains("[gateway]\noffline = true\n"));
    assert!(updated.contains("threshold = 0.8"));
    assert!(!updated.contains("threshold = 0.25"));
    Ok(())
}
