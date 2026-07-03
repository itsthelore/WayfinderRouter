"""Scaffolding for ``wayfinder-router init`` and ``doctor``.

These helpers get a new user routing in one command: they emit a starter
``wayfinder-router.toml`` (from a preset or an interactive wizard), a matching
``.env.example`` that lists key *names* only, and a read-only report of whether
each arm's key is present in the environment.

By design nothing here ever touches a secret value on disk. Keys are read from
the environment at request time (WF-ADR-0004); we only ever name the variables,
suggest a command that might fetch one, or check whether one is set. This module
is pure and heavy-dependency-free — ``subprocess`` and ``shutil`` are imported
lazily so importing the package stays cheap and gateway-free.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Duck-typed at runtime (we read a handful of attributes); importing gateway
    # here would create a load-time cycle, since gateway lazily calls us back.
    from collections.abc import Mapping, MutableMapping

    from .gateway import GatewayModel

# Upper bound on a key-fetch command. Long enough that an interactive vault
# unlock (a biometric prompt, a hardware token tap) has time to complete.
KEY_CMD_TIMEOUT = 30.0

# Injected console I/O for the wizard, kept as plain callables so the flow is
# pure and fully scriptable in tests. ``ask`` returns the user's answer (or the
# supplied default on a blank line); ``say`` prints a narration line.
Ask = Callable[[str, str], str]
Say = Callable[[str], None]


@dataclass(frozen=True)
class Preset:
    """A ready-made starter config: the TOML to write and the keys it expects."""

    name: str
    summary: str
    config_toml: str
    env_vars: tuple[str, ...]


# --- built-in presets --------------------------------------------------------
# Each ``config_toml`` below is a complete document that must load back through
# both the gateway parser and the routing parser. Prose is free to edit; the
# endpoints, model ids, key names and costs are the contract.

_HYBRID_TOML = """\
# wayfinder-router.toml (preset: hybrid) — a free local arm plus a cloud arm.
#
# Secrets stay in your environment: an arm names an api_key_env and the key is
# read at request time, never written here (WF-ADR-0004). Re-run `doctor` anytime.

[routing]
# A single structural cut. Prompts below it stay local; at or above it they go
# to the cloud arm. Retune with `wayfinder-router calibrate` or /threshold in chat.
threshold = 0.08

# On-device arm via Ollama (OpenAI-compatible, no key). Run `ollama serve` and
# `ollama pull llama3.1` first.
[gateway.models.local]
base_url = "http://localhost:11434/v1"
model = "llama3.1"
cost_per_1k = 0.0   # free on your own hardware — feeds the savings estimate

# Cloud arm via Anthropic. Export ANTHROPIC_API_KEY before serving; the value is
# read from the environment each request and is never stored in this file.
[gateway.models.cloud]
base_url = "https://api.anthropic.com/v1"
model = "claude-sonnet-4-6"
api_key_env = "ANTHROPIC_API_KEY"
# Rather not export a raw key? Have `doctor` suggest an api_key_cmd that pulls it
# from your secret manager into memory at startup, e.g.:
#   api_key_cmd = "op read op://Private/Anthropic/credential"
cost_per_1k = 0.009   # rough blended $/1k tokens — adjust to your pricing

# Want Gemini for the cloud arm instead? It speaks the OpenAI chat API at its
# compat endpoint, so swap the [gateway.models.cloud] block above for:
#   [gateway.models.cloud]
#   base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
#   model = "gemini-2.5-flash"
#   api_key_env = "GEMINI_API_KEY"
#   cost_per_1k = 0.0003
"""

_OPENAI_TOML = """\
# wayfinder-router.toml (preset: openai) — two OpenAI cost tiers, one key.
#
# Secrets stay in your environment: an arm names an api_key_env and the key is
# read at request time, never written here (WF-ADR-0004). Re-run `doctor` anytime.

# Both tiers are the same provider at different price points: cheap below the cut,
# capable at or above it. Retune the cut with `wayfinder-router calibrate`.
[[routing.tiers]]
min_score = 0.0
model = "small"

[[routing.tiers]]
min_score = 0.08
model = "large"

# The small, inexpensive model.
[gateway.models.small]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"
cost_per_1k = 0.0004   # rough blended $/1k tokens — adjust to your pricing

# The large model, sharing the same key from the environment.
[gateway.models.large]
base_url = "https://api.openai.com/v1"
model = "gpt-4o"
api_key_env = "OPENAI_API_KEY"
cost_per_1k = 0.0075   # rough blended $/1k tokens — adjust to your pricing
"""

_GEMINI_TOML = """\
# wayfinder-router.toml (preset: gemini) — two Gemini cost tiers, one key.
#
# Secrets stay in your environment: an arm names an api_key_env and the key is
# read at request time, never written here (WF-ADR-0004). Re-run `doctor` anytime.

# Flash below the cut, Pro at or above it — both reached through Gemini's
# OpenAI-compatible endpoint. Retune the cut with `wayfinder-router calibrate`.
[[routing.tiers]]
min_score = 0.0
model = "flash"

[[routing.tiers]]
min_score = 0.08
model = "pro"

# Flash: fast and cheap. The key is read from GEMINI_API_KEY at request time.
[gateway.models.flash]
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
model = "gemini-2.5-flash"
api_key_env = "GEMINI_API_KEY"
cost_per_1k = 0.0003   # rough blended $/1k tokens — adjust to your pricing

# Pro: the capable tier, same key from the environment.
[gateway.models.pro]
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
model = "gemini-2.5-pro"
api_key_env = "GEMINI_API_KEY"
cost_per_1k = 0.005   # rough blended $/1k tokens — adjust to your pricing
"""

DEFAULT_PRESET = "hybrid"


def _preset(name: str, summary: str, toml: str, *env_vars: str) -> Preset:
    return Preset(name=name, summary=summary, config_toml=toml, env_vars=env_vars)


# Built from a tuple so the dict keys stay in sync with each preset's own name.
# Iteration order (hybrid first) is user-facing via the menu / `sorted(PRESETS)`.
PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        _preset("hybrid", "keyless local Ollama → Anthropic cloud", _HYBRID_TOML,
                "ANTHROPIC_API_KEY"),
        _preset("openai", "OpenAI two-tier (gpt-4o-mini → gpt-4o)", _OPENAI_TOML,
                "OPENAI_API_KEY"),
        _preset("gemini", "Gemini two-tier (gemini-2.5-flash → gemini-2.5-pro)", _GEMINI_TOML,
                "GEMINI_API_KEY"),
    )
}


def render_config(preset: Preset) -> str:
    """Return the preset's ``wayfinder-router.toml`` text unchanged."""
    return preset.config_toml


def render_env_example(preset: Preset) -> str:
    """Build a ``.env.example`` listing the preset's key *names* — never values.

    Each variable becomes a bare ``NAME=`` line (no value, no trailing space), so
    the file documents what to set without ever committing a secret.
    """
    header = [
        "# Wayfinder model keys — export these in your shell (direnv, dotenv, …).",
        "# They are read at request time; secrets are never written to disk.",
        "# Leave blank to lean on the keyless local arm only.",
        "",
    ]
    body = [f"{name}=" for name in preset.env_vars]
    return "\n".join(header + body) + "\n"


@dataclass(frozen=True)
class KeyStatus:
    """One arm's key readiness, as reported by ``doctor``."""

    name: str  # routing target (the gateway model name), not the upstream id
    model: str  # the upstream model id
    base_url: str
    env_var: str | None  # None for a keyless arm
    ok: bool  # keyless, or the named variable is present
    cmd: str | None = None  # an api_key_cmd that could fill env_var, if declared


def key_status(models: Mapping[str, GatewayModel]) -> list[KeyStatus]:
    """Report each model's key readiness by reading the environment only.

    This never runs an ``api_key_cmd``; call :func:`resolve_keys` first if you
    want command-filled keys reflected. Order follows ``models``.
    """
    report: list[KeyStatus] = []
    for name, model in models.items():
        env_var = model.api_key_env
        present = env_var is None or bool(os.environ.get(env_var))
        report.append(
            KeyStatus(
                name=name,
                model=model.model,
                base_url=model.base_url,
                env_var=env_var,
                ok=present,
                cmd=getattr(model, "api_key_cmd", None),
            )
        )
    return report


def missing_keys(statuses: list[KeyStatus]) -> list[str]:
    """The named-but-unset variables across ``statuses``: sorted and deduplicated."""
    return sorted({s.env_var for s in statuses if s.env_var and not s.ok})


# --- secret resolution (WF-DESIGN-0006) --------------------------------------
class KeyResolutionError(Exception):
    """Raised when an ``api_key_cmd`` yields no usable key (exit, timeout, empty)."""


def _run_key_cmd(cmd: str) -> str:
    """Execute ``cmd`` in a shell and return its stdout, stripped.

    ``shell=True`` lets a user paste the exact line their secret tool documents;
    the command comes from their own config, so it runs with their privileges —
    the same trust as the ``export`` they would otherwise type. stderr is left
    attached to the terminal so an unlock prompt is visible; only stdout (the
    key) is captured, and it is never logged.
    """
    import subprocess

    try:
        completed = subprocess.run(  # noqa: S602 - shell intentional; command is user-authored
            cmd, shell=True, stdout=subprocess.PIPE, text=True, timeout=KEY_CMD_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise KeyResolutionError(f"timed out after {KEY_CMD_TIMEOUT:g}s: {cmd}") from exc
    except OSError as exc:
        raise KeyResolutionError(f"could not run command: {exc}") from exc
    if completed.returncode != 0:
        raise KeyResolutionError(f"command exited {completed.returncode}: {cmd}")
    return (completed.stdout or "").strip()


def resolve_keys(
    models: Mapping[str, GatewayModel],
    *,
    environ: MutableMapping[str, str] | None = None,
    runner: Callable[[str], str] | None = None,
) -> dict[str, str]:
    """Fill each arm's ``api_key_env`` from its ``api_key_cmd`` when it is unset.

    The fetched value is loaded into the process environment in memory only and
    never persisted. An already-set variable always wins, so a command runs only
    when needed and is never mandatory. Returns ``{model_name: error}`` for arms
    whose command failed; the messages carry the *command*, never the key.
    """
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    run = _run_key_cmd if runner is None else runner
    errors: dict[str, str] = {}
    for name, model in models.items():
        cmd = getattr(model, "api_key_cmd", None)
        var = model.api_key_env
        # Nothing to do for a keyless arm, an arm with no command, or one whose
        # variable is already populated (an explicit export / CI secret wins).
        if not cmd or not var or env.get(var):
            continue
        try:
            value = run(cmd).strip()  # strip here so every runner is treated alike
        except KeyResolutionError as exc:
            errors[name] = str(exc)
            continue
        if not value:
            errors[name] = f"command produced no output: {cmd}"
            continue
        env[var] = value
    return errors


# Secret managers we can propose an ``api_key_cmd`` for, in rough preference
# order. Each tuple is (executable, human label, command template with a {var}
# slot). Inner literals are single-quoted so the rendered line is always a valid
# double-quoted TOML value — a test round-trips every one through the parser.
_KEY_HELPERS: tuple[tuple[str, str, str], ...] = (
    ("op", "1Password CLI", "op read 'op://Private/{var}/credential'"),
    ("pass", "pass store", "pass show {var}"),
    ("gopass", "gopass store", "gopass show -o {var}"),
    ("security", "macOS Keychain", "security find-generic-password -w -s '{var}'"),
    ("secret-tool", "freedesktop Secret Service", "secret-tool lookup service '{var}'"),
    ("vault", "HashiCorp Vault", "vault kv get -field={var} secret/wayfinder"),
    (
        "aws",
        "AWS Secrets Manager",
        "aws secretsmanager get-secret-value --secret-id {var} "
        "--query SecretString --output text",
    ),
    ("gcloud", "Google Secret Manager", "gcloud secrets versions access latest --secret={var}"),
    ("bw", "Bitwarden CLI", "bw get password {var}"),
    ("doppler", "Doppler", "doppler secrets get {var} --plain"),
)


def suggest_key_commands(
    env_var: str, *, which: Callable[[str], str | None] | None = None
) -> list[str]:
    """Return example ``api_key_cmd`` lines for the secret tools found on PATH.

    One ready-to-paste command per detected executable (empty when none are
    installed). Detection only — nothing is ever executed.
    """
    if which is None:
        from shutil import which as _shutil_which

        detect: Callable[[str], str | None] = _shutil_which
    else:
        detect = which
    return [
        template.format(var=env_var)
        for exe, _label, template in _KEY_HELPERS
        if detect(exe)
    ]


# --- interactive wizard ------------------------------------------------------
@dataclass(frozen=True)
class Provider:
    """A selectable upstream: its endpoint, the key it reads, and a sample model."""

    key: str
    label: str
    base_url: str
    api_key_env: str | None
    example_model: str


# Menu order fixes the numbers users type, so it is part of the contract. Custom
# sits last (stable number) and carries placeholder defaults, so no field is ever
# required — piped or EOF input can never trap the wizard in a re-prompt.
PROVIDER_CHOICES: tuple[Provider, ...] = (
    Provider("ollama", "Ollama — local, keyless", "http://localhost:11434/v1", None, "llama3.1"),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    Provider(
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com/v1",
        "ANTHROPIC_API_KEY",
        "claude-sonnet-4-6",
    ),
    Provider(
        "custom",
        "Custom (any OpenAI-compatible endpoint)",
        "http://localhost:8000/v1",
        None,
        "your-model",
    ),
)

# Suggested escalation cuts for the 2nd, 3rd, … tiers (the base tier is always 0).
_DEFAULT_CUTS = (0.08, 0.30, 0.60, 0.80)


@dataclass(frozen=True)
class ModelArm:
    """One routing tier assembled by the wizard: an upstream plus its entry score."""

    name: str
    base_url: str
    model: str
    api_key_env: str | None
    min_score: float


def _default_tier_name(index: int) -> str:
    """Suggested name for tier ``index``: local, cloud, then tierN."""
    if index == 0:
        return "local"
    if index == 1:
        return "cloud"
    return f"tier{index + 1}"


def _default_cut(index: int) -> float:
    """Default entry score for tier ``index`` (>= 1); clamps past the last cut."""
    return _DEFAULT_CUTS[min(index - 1, len(_DEFAULT_CUTS) - 1)]


def _slug(name: str, fallback: str) -> str:
    """Coerce ``name`` into a TOML-key-safe routing target, or use ``fallback``."""
    kept = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip().lower())
    return kept.strip("-") or fallback


def _collect_arm(ask: Ask, say: Say, index: int) -> ModelArm:
    """Prompt for a single tier. The question order is the scripted-answer contract."""
    say("")
    say(f"Tier {index + 1}:")
    for number, provider in enumerate(PROVIDER_CHOICES, start=1):
        say(f"  {number}) {provider.label}")

    choice = ask(f"  provider (1-{len(PROVIDER_CHOICES)})", "1")
    try:
        provider = PROVIDER_CHOICES[int(choice) - 1]
    except (ValueError, IndexError):
        provider = PROVIDER_CHOICES[0]  # unparseable / out of range → Ollama

    base_url = provider.base_url
    api_key_env = provider.api_key_env
    if provider.key == "custom":
        base_url = ask("  base_url", provider.base_url)
        api_key_env = ask("  API key env var (blank = keyless)", "") or None

    model = ask("  model id", provider.example_model)
    default_name = _default_tier_name(index)
    name = _slug(ask("  name for this tier", default_name), default_name)

    if index == 0:
        min_score = 0.0  # the base tier always begins at zero
    else:
        raw = ask("  escalate to this tier at score (0-1)", f"{_default_cut(index):g}")
        try:
            min_score = max(0.0, min(1.0, float(raw)))
        except ValueError:
            min_score = _default_cut(index)

    return ModelArm(
        name=name,
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        min_score=min_score,
    )


def run_init_wizard(ask: Ask, say: Say) -> Preset:
    """Assemble a multi-tier config interactively, collecting key names only."""
    say("Let's configure your models — Wayfinder routes cheap → capable.")
    say("Keys are read from the environment at request time and are never stored here.")
    arms: list[ModelArm] = []
    while True:
        arms.append(_collect_arm(ask, say, index=len(arms)))
        again = ask("Add another model tier? (y/N)", "n")
        if again.strip().lower() not in {"y", "yes"}:
            break
    return _preset_from_arms(arms)


def _preset_from_arms(arms: list[ModelArm]) -> Preset:
    """Fold the collected arms into a ``custom`` preset."""
    env_vars = tuple(sorted({a.api_key_env for a in arms if a.api_key_env}))
    plural = "" if len(arms) == 1 else "s"
    return Preset(
        name="custom",
        summary=f"{len(arms)} tier{plural} (interactive)",
        config_toml=render_config_from_arms(arms),
        env_vars=env_vars,
    )


def render_config_from_arms(arms: list[ModelArm]) -> str:
    """Render a ``wayfinder-router.toml`` from ``arms`` (parses back unchanged)."""
    ordered = sorted(arms, key=lambda a: a.min_score)
    lines = [
        "# wayfinder-router.toml — generated by `wayfinder-router init --interactive`.",
        "#",
        "# Secrets stay in the environment: an arm names an api_key_env and the key is",
        "# read at request time (WF-ADR-0004). Hand-tune the score cuts, or run calibrate.",
        "",
    ]
    for arm in ordered:  # ascending score bands (WF-ADR-0002)
        lines += ["[[routing.tiers]]", f"min_score = {arm.min_score:g}", f'model = "{arm.name}"', ""]
    for arm in ordered:
        lines += [
            f"[gateway.models.{arm.name}]",
            f'base_url = "{arm.base_url}"',
            f'model = "{arm.model}"',
        ]
        if arm.api_key_env:
            lines.append(f'api_key_env = "{arm.api_key_env}"')
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
