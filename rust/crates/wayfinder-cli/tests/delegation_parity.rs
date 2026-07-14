use std::path::PathBuf;
use std::process::Command;

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
