//! Service-first lifecycle glue (WF-ADR-0042 §4): the app never owns the gateway process — the
//! WF-ADR-0038 launchd agent does. These are the exact, fixed commands the tray/CTAs shell out
//! to, kept pure and unit-tested here; execution is a thin wrapper. No arbitrary shell: the
//! action enum maps to one known argv, and `wayfinder-router` is resolved from a fixed
//! candidate set (a GUI app's PATH is not the shell's).

use std::path::PathBuf;
use std::process::Command;

/// The launchd label the service installs under (mirrors wayfinder_router.service.LAUNCHD_LABEL).
pub const LAUNCHD_LABEL: &str = "com.wayfinder-router.gateway";
pub const GATEWAY_PORT: &str = "8088";

/// The one well-known config file the app and the service share (WF-ADR-0044): the first-run
/// scaffold writes here, and every desktop-managed install bakes `--config <this>` into the
/// unit so a launchd-launched gateway (unpredictable cwd) still finds it. Installing before the
/// file exists is safe: a set-but-missing override resolves to built-in defaults, and a gateway
/// with no models answers decision-only rather than erroring.
pub fn desktop_config_path(home: &str) -> String {
    format!("{home}/Library/Application Support/Wayfinder/wayfinder-router.toml")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServiceAction {
    Install,
    Uninstall,
    Start,
    Stop,
}

impl ServiceAction {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "install" => Some(Self::Install),
            "uninstall" => Some(Self::Uninstall),
            "start" => Some(Self::Start),
            "stop" => Some(Self::Stop),
            _ => None,
        }
    }
}

/// The concrete (program, args) for an action. Install/Uninstall drive the `wayfinder-router
/// service` verbs (WF-ADR-0038); Start/Stop drive `launchctl` directly (the service CLI has no
/// start/stop). `wf_bin` is the resolved gateway launcher; `uid` scopes the launchctl domain;
/// `home` locates the shared config Install always bakes into the unit (WF-ADR-0044). NOTE:
/// re-installing over a *loaded* agent leaves launchd's old job spec in place (bootstrap fails,
/// the legacy-load fallback no-ops, the probe passes) — callers that need new ProgramArguments
/// applied must uninstall first (see `commands::scaffold_config`).
pub fn argv(action: ServiceAction, wf_bin: &str, uid: u32, home: &str) -> (String, Vec<String>) {
    let target = format!("gui/{uid}/{LAUNCHD_LABEL}");
    match action {
        ServiceAction::Install => (
            wf_bin.to_string(),
            vec![
                "service".into(),
                "install".into(),
                "--port".into(),
                GATEWAY_PORT.into(),
                "--config".into(),
                desktop_config_path(home),
            ],
        ),
        ServiceAction::Uninstall => (
            wf_bin.to_string(),
            vec!["service".into(), "uninstall".into()],
        ),
        ServiceAction::Start => (
            "launchctl".into(),
            vec!["kickstart".into(), "-k".into(), target],
        ),
        ServiceAction::Stop => ("launchctl".into(), vec!["bootout".into(), target]),
    }
}

/// Resolve a native `wayfinder-router` launcher. A packaged GUI app inherits a
/// minimal PATH, so also check the usual native binary locations.
pub fn resolve_wayfinder(home: &str, path_env: &str) -> Option<String> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    for dir in path_env.split(':').filter(|s| !s.is_empty()) {
        candidates.push(PathBuf::from(dir).join("wayfinder-router"));
    }
    for dir in [
        format!("{home}/.local/bin"),
        "/opt/homebrew/bin".to_string(),
        "/usr/local/bin".to_string(),
    ] {
        candidates.push(PathBuf::from(dir).join("wayfinder-router"));
    }
    candidates
        .into_iter()
        .find(|p| p.is_file())
        .map(|p| p.to_string_lossy().into_owned())
}

/// Run an action, returning a human message on success or an error string. Best-effort resolver
/// inputs come from the environment so the pure `argv`/`resolve_wayfinder` stay testable.
pub fn run(action: ServiceAction) -> Result<String, String> {
    let uid = unsafe { libc::getuid() };
    let home = std::env::var("HOME").unwrap_or_default();
    let (program, args) = if matches!(action, ServiceAction::Install | ServiceAction::Uninstall) {
        let path = std::env::var("PATH").unwrap_or_default();
        let wf = resolve_wayfinder(&home, &path).ok_or_else(|| {
            "couldn't find the native `wayfinder-router` binary".to_string()
        })?;
        argv(action, &wf, uid, &home)
    } else {
        argv(action, "", uid, &home)
    };

    let out = Command::new(&program)
        .args(&args)
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    if out.status.success() {
        Ok(format!("{action:?} ok"))
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        Err(format!("{action:?} failed: {}", stderr.trim()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_bakes_the_shared_config_into_the_unit() {
        let (p, a) = argv(ServiceAction::Install, "/opt/homebrew/bin/wayfinder-router", 501, "/Users/t");
        assert_eq!(p, "/opt/homebrew/bin/wayfinder-router");
        assert_eq!(
            a,
            [
                "service",
                "install",
                "--port",
                "8088",
                "--config",
                "/Users/t/Library/Application Support/Wayfinder/wayfinder-router.toml",
            ]
        );
        let (p, a) = argv(ServiceAction::Uninstall, "/x/wayfinder-router", 501, "/Users/t");
        assert_eq!(p, "/x/wayfinder-router");
        assert_eq!(a, ["service", "uninstall"]);
    }

    #[test]
    fn start_stop_drive_launchctl_scoped_to_the_gui_domain() {
        let (p, a) = argv(ServiceAction::Start, "", 501, "/Users/t");
        assert_eq!(p, "launchctl");
        assert_eq!(a, ["kickstart", "-k", "gui/501/com.wayfinder-router.gateway"]);
        let (p, a) = argv(ServiceAction::Stop, "", 501, "/Users/t");
        assert_eq!(p, "launchctl");
        assert_eq!(a, ["bootout", "gui/501/com.wayfinder-router.gateway"]);
    }

    #[test]
    fn action_parse_rejects_unknown() {
        assert_eq!(ServiceAction::parse("install"), Some(ServiceAction::Install));
        assert_eq!(ServiceAction::parse("nuke"), None);
    }

    #[test]
    fn resolver_finds_a_binary_on_the_path() {
        let dir = std::env::temp_dir().join(format!("wf-resolve-{}", unsafe { libc::getpid() }));
        std::fs::create_dir_all(&dir).unwrap();
        let bin = dir.join("wayfinder-router");
        std::fs::write(&bin, b"#!/bin/sh\n").unwrap();
        let found = resolve_wayfinder("/nonexistent-home", &dir.to_string_lossy());
        assert_eq!(found.as_deref(), Some(bin.to_string_lossy().as_ref()));
        std::fs::remove_dir_all(&dir).ok();
    }
}
