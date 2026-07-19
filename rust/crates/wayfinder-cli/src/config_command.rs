//! Native routing-only config commands used by Wayfinder Desktop.
//!
//! The rest of the `config` surface remains delegated to Python until it has
//! its own parity gate. These two commands deliberately accept and emit only
//! the bounded routing contract consumed by the native app.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use uuid::Uuid;
use wayfinder_config::gateway::gateway_config_from_toml;
use wayfinder_config::{
    CONFIG_FILE, CONFIG_PATH_ENV, TierOrderPolicy, apply_supported_routing_fragment,
    find_config_file, routing_config_from_toml,
};
use wayfinder_core::{FEATURE_ORDER, RoutingConfig, Weights};

use crate::{EXIT_CONFIG, EXIT_OK, EXIT_USAGE, write_error, write_output};

const MAX_CONFIG_BYTES: usize = 1024 * 1024;
const MAX_ROUTING_FRAGMENT_BYTES: usize = 64 * 1024;
const CONFIG_HELP: &str = "usage: wayfinder-router config {read-routing,apply-routing} [--path PATH]\n\nRead or atomically replace the supported routing portion of a config file.";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ConfigAction {
    ReadRouting,
    ApplyRouting,
}

#[derive(Debug, PartialEq, Eq)]
struct ConfigOptions {
    action: ConfigAction,
    path: Option<PathBuf>,
}

/// Whether `arguments` select one of the two config operations owned by Rust.
///
/// `arguments` includes the top-level `config` token. Options may appear on
/// either side of argparse's positional action, matching the retained CLI.
pub(crate) fn is_native_config_command(arguments: &[OsString]) -> bool {
    if arguments.first().and_then(|value| value.to_str()) != Some("config") {
        return false;
    }
    native_action_from_os_arguments(&arguments[1..]).is_some()
}

fn native_action_from_os_arguments(arguments: &[OsString]) -> Option<ConfigAction> {
    let mut index = 0;
    while let Some(argument) = arguments.get(index).and_then(|value| value.to_str()) {
        match argument {
            "--path" => index = index.saturating_add(2),
            value if value.starts_with("--path=") => index = index.saturating_add(1),
            "-h" | "--help" => return None,
            value if value.starts_with('-') => index = index.saturating_add(1),
            "read-routing" => return Some(ConfigAction::ReadRouting),
            "apply-routing" => return Some(ConfigAction::ApplyRouting),
            _ => return None,
        }
    }
    None
}

pub(crate) fn run_config(
    arguments: &[String],
    stdin: &mut dyn Read,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> i32 {
    let options = match parse_options(arguments) {
        Ok(Some(options)) => options,
        Ok(None) => {
            write_output(stdout, CONFIG_HELP);
            return EXIT_OK;
        }
        Err(message) => {
            write_error(stderr, &format!("wayfinder-router: {message}"));
            return EXIT_USAGE;
        }
    };
    let selected = selected_config_path(options.path.as_deref());
    let Some(path) = selected else {
        let where_ = options
            .path
            .as_deref()
            .map_or_else(|| CONFIG_FILE.to_owned(), |path| path.display().to_string());
        write_error(
            stderr,
            &format!(
                "wayfinder-router: no config at {where_} — run `wayfinder-router init` to create one"
            ),
        );
        return EXIT_USAGE;
    };

    match options.action {
        ConfigAction::ReadRouting => read_routing(&path, stdout, stderr),
        ConfigAction::ApplyRouting => apply_routing(&path, stdin, stderr),
    }
}

fn parse_options(arguments: &[String]) -> Result<Option<ConfigOptions>, String> {
    if arguments
        .iter()
        .any(|argument| argument == "-h" || argument == "--help")
    {
        return Ok(None);
    }
    let mut path = None;
    let mut positional = Vec::new();
    let mut index = 0;
    while let Some(argument) = arguments.get(index) {
        match argument.as_str() {
            "--path" => {
                index = index.saturating_add(1);
                let value = arguments
                    .get(index)
                    .ok_or_else(|| "config --path needs a value".to_owned())?;
                if value.is_empty() {
                    return Err("config --path needs a non-empty value".to_owned());
                }
                if path.replace(PathBuf::from(value)).is_some() {
                    return Err("config accepts --path only once".to_owned());
                }
            }
            value if value.starts_with("--path=") => {
                let value = value.strip_prefix("--path=").unwrap_or_default();
                if value.is_empty() {
                    return Err("config --path needs a non-empty value".to_owned());
                }
                if path.replace(PathBuf::from(value)).is_some() {
                    return Err("config accepts --path only once".to_owned());
                }
            }
            value if value.starts_with('-') => {
                return Err(format!("unrecognized config argument: {value}"));
            }
            value => positional.push(value),
        }
        index = index.saturating_add(1);
    }
    if positional.len() != 1 {
        return Err("config needs exactly one action: read-routing or apply-routing".to_owned());
    }
    let action = match positional[0] {
        "read-routing" => ConfigAction::ReadRouting,
        "apply-routing" => ConfigAction::ApplyRouting,
        other => return Err(format!("unsupported native config action: {other}")),
    };
    Ok(Some(ConfigOptions { action, path }))
}

fn selected_config_path(explicit: Option<&Path>) -> Option<PathBuf> {
    if let Some(explicit) = explicit {
        return find_config_file(Path::new("."), Some(explicit));
    }
    let environment = std::env::var_os(CONFIG_PATH_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .map(expand_tilde);
    find_config_file(Path::new("."), environment.as_deref())
}

fn expand_tilde(path: PathBuf) -> PathBuf {
    let rendered = path.to_string_lossy();
    let Some(rest) = rendered.strip_prefix("~/") else {
        return path;
    };
    let Some(home) = std::env::var_os("HOME") else {
        return path;
    };
    PathBuf::from(home).join(rest)
}

fn read_routing(path: &Path, stdout: &mut dyn Write, stderr: &mut dyn Write) -> i32 {
    let text = match read_config(path) {
        Ok(text) => text,
        Err(message) => {
            write_error(stderr, &format!("wayfinder-router: {message}"));
            return EXIT_CONFIG;
        }
    };
    let routing = match routing_config_from_toml(
        &text,
        &path.display().to_string(),
        None,
        TierOrderPolicy::StrictInput,
    ) {
        Ok(routing) => routing,
        Err(error) => {
            write_error(stderr, &format!("wayfinder-router: {error}"));
            return EXIT_CONFIG;
        }
    };
    let payload = routing_payload(&routing);
    if serde_json::to_writer_pretty(&mut *stdout, &payload).is_err() || writeln!(stdout).is_err() {
        write_error(
            stderr,
            "wayfinder-router: cannot write routing config output",
        );
        return EXIT_CONFIG;
    }
    EXIT_OK
}

fn apply_routing(path: &Path, stdin: &mut dyn Read, stderr: &mut dyn Write) -> i32 {
    let fragment =
        match read_utf8_bounded(stdin, MAX_ROUTING_FRAGMENT_BYTES, "routing config input") {
            Ok(fragment) => fragment,
            Err(message) => {
                write_error(
                    stderr,
                    &format!("wayfinder-router: refusing to write — {message}"),
                );
                return EXIT_CONFIG;
            }
        };
    let original = match read_config(path) {
        Ok(text) => text,
        Err(message) => {
            write_error(
                stderr,
                &format!("wayfinder-router: refusing to write — {message}"),
            );
            return EXIT_CONFIG;
        }
    };
    let updated = match apply_supported_routing_fragment(&original, &fragment) {
        Ok(updated) => updated,
        Err(error) => {
            write_error(
                stderr,
                &format!("wayfinder-router: refusing to write — {error}"),
            );
            return EXIT_CONFIG;
        }
    };
    if let Err(error) = gateway_config_from_toml(&updated, &path.display().to_string()) {
        write_error(
            stderr,
            &format!("wayfinder-router: refusing to write — {error}"),
        );
        return EXIT_CONFIG;
    }
    if let Err(message) = atomic_replace(path, updated.as_bytes()) {
        write_error(
            stderr,
            &format!("wayfinder-router: refusing to write — {message}"),
        );
        return EXIT_CONFIG;
    }
    write_error(
        stderr,
        &format!(
            "wayfinder-router: applied routing config in {} (restart the gateway from Gateway settings if behavior does not update immediately)",
            path.display()
        ),
    );
    EXIT_OK
}

fn read_config(path: &Path) -> Result<String, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!(
            "cannot read {}: not a regular file",
            path.display()
        ));
    }
    if metadata.len() > MAX_CONFIG_BYTES as u64 {
        return Err(format!(
            "cannot read {}: config exceeds {MAX_CONFIG_BYTES} bytes",
            path.display()
        ));
    }
    let mut file =
        File::open(path).map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    read_utf8_bounded(&mut file, MAX_CONFIG_BYTES, &path.display().to_string())
}

fn read_utf8_bounded<R: Read + ?Sized>(
    reader: &mut R,
    maximum: usize,
    label: &str,
) -> Result<String, String> {
    let mut bytes = Vec::new();
    reader
        .take((maximum as u64).saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|error| format!("cannot read {label}: {error}"))?;
    if bytes.len() > maximum {
        return Err(format!("{label} exceeds {maximum} bytes"));
    }
    String::from_utf8(bytes).map_err(|_| format!("{label} must be valid UTF-8"))
}

fn atomic_replace(path: &Path, contents: &[u8]) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!(
            "cannot replace {}: not a regular file",
            path.display()
        ));
    }
    let parent = path
        .parent()
        .ok_or_else(|| format!("cannot replace {}: no parent directory", path.display()))?;
    let filename = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(CONFIG_FILE);
    let stage = parent.join(format!(".{filename}.wayfinder-stage-{}", Uuid::new_v4()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
            options.mode(metadata.permissions().mode());
        }
        let mut file = options
            .open(&stage)
            .map_err(|error| format!("cannot create config stage: {error}"))?;
        file.write_all(contents)
            .and_then(|()| file.sync_all())
            .map_err(|error| format!("cannot persist config stage: {error}"))?;
        fs::set_permissions(&stage, metadata.permissions())
            .map_err(|error| format!("cannot preserve config permissions: {error}"))?;
        fs::rename(&stage, path)
            .map_err(|error| format!("cannot replace {}: {error}", path.display()))?;
        if let Ok(directory) = File::open(parent) {
            // The replacement already committed once rename succeeds. A
            // directory fsync is useful durability hardening where supported,
            // but cannot be reported as a refused write after that point.
            let _ = directory.sync_all();
        }
        Ok(())
    })();
    if stage.exists() {
        let _ = fs::remove_file(&stage);
    }
    result
}

fn routing_payload(config: &RoutingConfig) -> Value {
    let defaults = Weights::default();
    let weights = FEATURE_ORDER
        .iter()
        .map(|name| {
            json!({
                "id": name,
                "label": title_label(name),
                "value": config.weights.get(name).unwrap_or_else(|| defaults.get(name).unwrap_or(0.0)),
                "default": defaults.get(name).unwrap_or(0.0),
            })
        })
        .collect::<Vec<_>>();
    if let Some(classifier) = &config.classifier {
        return json!({
            "mode": "classifier",
            "models": classifier.models(),
            "weights": weights,
        });
    }
    let tiers = &config.tiers;
    let binary = tiers.len() == 2
        && tiers[0].min_score == 0.0
        && tiers[0].model == "local"
        && tiers[1].model == "cloud";
    if binary {
        json!({
            "mode": "binary",
            "threshold": tiers[1].min_score,
            "tiers": tiers,
            "weights": weights,
        })
    } else {
        json!({
            "mode": "tiered",
            "tiers": tiers,
            "weights": weights,
        })
    }
}

fn title_label(name: &str) -> String {
    name.split('_')
        .map(|word| {
            let mut characters = word.chars();
            characters.next().map_or_else(String::new, |first| {
                format!("{}{}", first.to_ascii_uppercase(), characters.as_str())
            })
        })
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Result<Self, Box<dyn std::error::Error>> {
            let path =
                std::env::temp_dir().join(format!("wayfinder-config-command-{}", Uuid::new_v4()));
            fs::create_dir(&path)?;
            Ok(Self(path))
        }

        fn config(&self, contents: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
            let path = self.0.join(CONFIG_FILE);
            fs::write(&path, contents)?;
            Ok(path)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn run_with_path(action: &str, path: &Path, stdin: &[u8]) -> (i32, String, String) {
        let mut input = stdin;
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        let code = run_config(
            &[
                action.to_owned(),
                "--path".to_owned(),
                path.display().to_string(),
            ],
            &mut input,
            &mut stdout,
            &mut stderr,
        );
        (
            code,
            String::from_utf8_lossy(&stdout).into_owned(),
            String::from_utf8_lossy(&stderr).into_owned(),
        )
    }

    #[test]
    fn parser_accepts_argparse_option_order_and_rejects_extras() -> Result<(), String> {
        assert_eq!(
            parse_options(&[
                "--path".to_owned(),
                "fixture.toml".to_owned(),
                "read-routing".to_owned(),
            ])?,
            Some(ConfigOptions {
                action: ConfigAction::ReadRouting,
                path: Some(PathBuf::from("fixture.toml")),
            })
        );
        assert!(parse_options(&["read-routing".to_owned(), "extra".to_owned()]).is_err());
        assert!(parse_options(&["apply-routing".to_owned(), "--path".to_owned()]).is_err());
        Ok(())
    }

    #[test]
    fn read_routing_emits_the_swift_json_contract_in_stable_feature_order()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = TestDirectory::new()?;
        let path = directory.config("[routing]\nthreshold = 0.42\n")?;
        let (code, stdout, stderr) = run_with_path("read-routing", &path, b"");
        assert_eq!(code, EXIT_OK, "{stderr}");
        let payload: Value = serde_json::from_str(&stdout)?;
        assert_eq!(payload["mode"], "binary");
        assert_eq!(payload["threshold"], 0.42);
        assert_eq!(
            payload["tiers"][0],
            json!({"min_score": 0.0, "model": "local"})
        );
        let identifiers = payload["weights"]
            .as_array()
            .ok_or("weights must be an array")?
            .iter()
            .map(|weight| weight["id"].as_str().unwrap_or_default())
            .collect::<Vec<_>>();
        assert_eq!(identifiers, FEATURE_ORDER);
        assert_eq!(payload["weights"][0]["label"], "Word Count");
        assert_eq!(payload["weights"][0]["value"], 3.0);
        assert_eq!(payload["weights"][0]["default"], 3.0);
        assert!(stderr.is_empty());
        Ok(())
    }

    #[test]
    fn read_routing_distinguishes_tiered_and_classifier_modes()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = TestDirectory::new()?;
        let tiered = directory.config(concat!(
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\n\n",
            "[[routing.tiers]]\nmin_score = 0.7\nmodel = \"large\"\n",
        ))?;
        let (code, stdout, stderr) = run_with_path("read-routing", &tiered, b"");
        assert_eq!(code, EXIT_OK, "{stderr}");
        assert_eq!(serde_json::from_str::<Value>(&stdout)?["mode"], "tiered");

        fs::write(
            &tiered,
            concat!(
                "[routing.classifier]\nmodels = [\"small\", \"large\"]\n",
                "intercepts = [0.0, 1.0]\n\n[routing.classifier.weights]\n",
            ),
        )?;
        let (code, stdout, stderr) = run_with_path("read-routing", &tiered, b"");
        assert_eq!(code, EXIT_OK, "{stderr}");
        let payload = serde_json::from_str::<Value>(&stdout)?;
        assert_eq!(payload["mode"], "classifier");
        assert_eq!(payload["models"], json!(["small", "large"]));
        assert!(payload.get("tiers").is_none());
        Ok(())
    }

    #[test]
    fn apply_routing_atomically_preserves_non_routing_config()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = TestDirectory::new()?;
        let original = concat!(
            "# keep this comment\n[gateway]\noffline = true\n\n",
            "[gateway.models.local]\nbase_url = \"http://localhost:11434/v1\"\nmodel = \"llama\"\n\n",
            "[routing]\nthreshold = 0.2\n",
        );
        let path = directory.config(original)?;
        let (code, stdout, stderr) =
            run_with_path("apply-routing", &path, b"[routing]\nthreshold = 0.73\n");
        assert_eq!(code, EXIT_OK, "{stderr}");
        assert!(stdout.is_empty());
        let updated = fs::read_to_string(&path)?;
        assert!(updated.contains("# keep this comment\n[gateway]\noffline = true\n"));
        assert!(
            updated.contains("[gateway.models.local]\nbase_url = \"http://localhost:11434/v1\"")
        );
        assert!(updated.contains("threshold = 0.73"));
        assert!(!updated.contains("threshold = 0.2"));
        assert!(stderr.contains("applied routing config"));
        Ok(())
    }

    #[test]
    fn apply_routing_rejects_invalid_or_oversized_input_without_writing()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = TestDirectory::new()?;
        let original = "[gateway]\noffline = true\n\n[routing]\nthreshold = 0.2\n";
        let path = directory.config(original)?;
        let (code, _, stderr) =
            run_with_path("apply-routing", &path, b"[routing]\nthreshold = 2.0\n");
        assert_eq!(code, EXIT_CONFIG);
        assert!(stderr.contains("refusing to write"));
        assert_eq!(fs::read_to_string(&path)?, original);

        let oversized = vec![b'x'; MAX_ROUTING_FRAGMENT_BYTES + 1];
        let (code, _, stderr) = run_with_path("apply-routing", &path, &oversized);
        assert_eq!(code, EXIT_CONFIG);
        assert!(stderr.contains("exceeds 65536 bytes"));
        assert_eq!(fs::read_to_string(&path)?, original);
        Ok(())
    }

    #[test]
    fn read_routing_rejects_oversized_config() -> Result<(), Box<dyn std::error::Error>> {
        let directory = TestDirectory::new()?;
        let path = directory.config(&"#".repeat(MAX_CONFIG_BYTES + 1))?;
        let (code, _, stderr) = run_with_path("read-routing", &path, b"");
        assert_eq!(code, EXIT_CONFIG);
        assert!(stderr.contains("config exceeds 1048576 bytes"));
        Ok(())
    }
}
