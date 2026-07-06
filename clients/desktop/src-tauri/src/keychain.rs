//! macOS Keychain glue for provider keys (WF-ADR-0044 / WF-ADR-0004): the desktop app stores a
//! key under service "wayfinder-router" / account "<ENV_VAR>", and the gateway reads it back at
//! startup through the `api_key_cmd` reference `init --keychain` scaffolds. Two disciplines:
//!
//! 1. **Key material never crosses argv** (argv is `ps`-visible). We spawn `/usr/bin/security -i`
//!    and feed the one command line over stdin.
//! 2. **`-T /usr/bin/security` is load-bearing**: the gateway reads via `/usr/bin/security
//!    find-generic-password …` from a headless launchd context — without that ACL entry every
//!    gateway restart raises a Keychain consent dialog nobody can see. (Also why a native
//!    SecItemAdd binding is wrong here: its items would trust the app, not `/usr/bin/security`.)
//!
//! The script builder + validators are pure and unit-tested on any OS; only `run_security`
//! actually spawns, and only on macOS. Real-Mac smoke test before release: CI is Linux.

use std::io::Write;
use std::process::{Command, Stdio};

pub const KEYCHAIN_SERVICE: &str = "wayfinder-router";
const MAX_KEY_LEN: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyOp {
    Add,
    Delete,
}

/// Env-var names are the Keychain account: strict `^[A-Z][A-Z0-9_]{0,63}$` so nothing
/// quote-hostile ever reaches the `security -i` tokenizer.
fn valid_env_var(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c.is_ascii_uppercase() => {}
        _ => return false,
    }
    name.len() <= 64 && chars.all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
}

/// Keys must be printable ASCII (no control chars — a newline is unrepresentable on the one
/// stdin line), non-empty, length-capped. Real provider keys are token strings; a reject here
/// is an honest error, not a lost user.
fn valid_key(key: &str) -> bool {
    !key.is_empty()
        && key.len() <= MAX_KEY_LEN
        && key.chars().all(|c| c.is_ascii() && !c.is_ascii_control())
}

/// `security -i` tokenizes with double quotes + backslash escapes.
fn escape(key: &str) -> String {
    key.replace('\\', "\\\\").replace('"', "\\\"")
}

/// The one command line fed to `/usr/bin/security -i` over stdin. Pure and table-tested.
pub fn keychain_script(op: KeyOp, env_var: &str, key: &str) -> Result<String, String> {
    if !valid_env_var(env_var) {
        return Err(format!("invalid env var name: {env_var}"));
    }
    match op {
        KeyOp::Add => {
            if !valid_key(key) {
                return Err(
                    "invalid key: must be non-empty printable ASCII, no control characters, \
                     at most 4096 bytes"
                        .to_string(),
                );
            }
            // -U updates in place (re-entering a key must not fail on "item exists").
            Ok(format!(
                "add-generic-password -U -s {KEYCHAIN_SERVICE} -a {env_var} \
                 -T /usr/bin/security -w \"{}\"",
                escape(key)
            ))
        }
        KeyOp::Delete => Ok(format!(
            "delete-generic-password -s {KEYCHAIN_SERVICE} -a {env_var}"
        )),
    }
}

/// Spawn `/usr/bin/security -i`, feed `line`, wait. macOS only — everywhere else this is an
/// honest error, not a silent no-op (a key the user thinks is saved must actually be saved).
pub fn run_security(line: &str) -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Err("the Keychain is only available on macOS".to_string());
    }
    let mut child = Command::new("/usr/bin/security")
        .arg("-i")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("security: {e}"))?;
    child
        .stdin
        .take()
        .ok_or("security: no stdin")?
        .write_all(format!("{line}\n").as_bytes())
        .map_err(|e| format!("security: {e}"))?;
    let out = child.wait_with_output().map_err(|e| format!("security: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        Err(format!("security failed: {}", stderr.trim()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_builds_the_acl_bearing_stdin_line() {
        let line = keychain_script(KeyOp::Add, "ANTHROPIC_API_KEY", "sk-ant-abc123").unwrap();
        assert_eq!(
            line,
            "add-generic-password -U -s wayfinder-router -a ANTHROPIC_API_KEY \
             -T /usr/bin/security -w \"sk-ant-abc123\""
        );
    }

    #[test]
    fn delete_names_only_service_and_account() {
        let line = keychain_script(KeyOp::Delete, "OPENAI_API_KEY", "").unwrap();
        assert_eq!(
            line,
            "delete-generic-password -s wayfinder-router -a OPENAI_API_KEY"
        );
    }

    #[test]
    fn keys_with_quotes_and_backslashes_are_escaped() {
        let line = keychain_script(KeyOp::Add, "K", r#"a"b\c"#).unwrap();
        assert!(line.ends_with(r#"-w "a\"b\\c""#));
    }

    #[test]
    fn hostile_or_malformed_inputs_are_rejected() {
        // env var must be SCREAMING_SNAKE
        for bad in ["", "lower", "1LEADING", "HAS-DASH", "HAS SPACE", "X\n"] {
            assert!(keychain_script(KeyOp::Add, bad, "k").is_err(), "{bad:?}");
        }
        // 65 chars: over the cap
        assert!(keychain_script(KeyOp::Add, &"A".repeat(65), "k").is_err());
        // keys: empty, control chars, newline injection, non-ascii, oversized
        for bad in ["", "line\nbreak", "tab\there", "bell\x07", "ключ"] {
            assert!(keychain_script(KeyOp::Add, "K", bad).is_err(), "{bad:?}");
        }
        assert!(keychain_script(KeyOp::Add, "K", &"x".repeat(4097)).is_err());
        // delete ignores the key argument but still validates the env var
        assert!(keychain_script(KeyOp::Delete, "bad name", "").is_err());
    }

    #[test]
    fn boundary_accepts() {
        assert!(keychain_script(KeyOp::Add, &format!("A{}", "B".repeat(63)), "k").is_ok());
        assert!(keychain_script(KeyOp::Add, "K", &"x".repeat(4096)).is_ok());
        assert!(keychain_script(KeyOp::Add, "GEMINI_API_KEY", "AIza x-y_z.9").is_ok());
    }

    #[test]
    fn run_security_is_macos_only() {
        if !cfg!(target_os = "macos") {
            assert!(run_security("list-keychains").is_err());
        }
    }
}
