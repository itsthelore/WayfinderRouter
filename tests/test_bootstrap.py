"""Tests for the bootstrap scaffolding (the pure init/doctor helpers)."""

from __future__ import annotations

from wayfinder_router import bootstrap
from wayfinder_router.config import routing_config_from_toml
from wayfinder_router.gateway import gateway_config_from_toml


def test_hybrid_preset_config_round_trips():
    text = bootstrap.render_config(bootstrap.PRESETS["hybrid"])
    gw = gateway_config_from_toml(text)
    assert set(gw.models) == {"local", "cloud"}
    assert gw.models["local"].base_url == "http://localhost:11434/v1"
    assert gw.models["local"].api_key_env is None  # keyless local arm
    assert gw.models["cloud"].api_key_env == "ANTHROPIC_API_KEY"
    # the [routing] threshold shorthand parses to local/cloud tiers
    routing = routing_config_from_toml(text)
    assert [t.model for t in routing.tiers] == ["local", "cloud"]


def test_openai_preset_config_round_trips(monkeypatch):
    text = bootstrap.render_config(bootstrap.PRESETS["openai"])
    gw = gateway_config_from_toml(text)
    assert set(gw.models) == {"small", "large"}
    assert gw.models["small"].model == "gpt-4o-mini"
    assert gw.models["large"].model == "gpt-4o"
    # one provider, one key shared across both tiers
    assert {m.api_key_env for m in gw.models.values()} == {"OPENAI_API_KEY"}
    routing = routing_config_from_toml(text)
    assert [t.model for t in routing.tiers] == ["small", "large"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert bootstrap.missing_keys(bootstrap.key_status(gw.models)) == ["OPENAI_API_KEY"]  # deduped


def test_presets_carry_rough_costs():
    hybrid = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    assert hybrid.models["local"].cost_per_1k == 0.0  # local is free
    assert hybrid.models["cloud"].cost_per_1k == 0.009
    openai = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["openai"]))
    assert openai.models["small"].cost_per_1k and openai.models["large"].cost_per_1k
    assert openai.models["large"].cost_per_1k > openai.models["small"].cost_per_1k


def test_env_example_lists_names_without_secrets():
    text = bootstrap.render_env_example(bootstrap.PRESETS["hybrid"])
    assert "ANTHROPIC_API_KEY=" in text
    for line in text.splitlines():
        if line.startswith("ANTHROPIC_API_KEY"):
            assert line == "ANTHROPIC_API_KEY="  # the name only — never a value


def test_key_status_flags_keyless_and_missing(monkeypatch):
    gw = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    by_name = {s.name: s for s in bootstrap.key_status(gw.models)}
    assert by_name["local"].env_var is None and by_name["local"].ok is True  # keyless
    assert by_name["cloud"].env_var == "ANTHROPIC_API_KEY" and by_name["cloud"].ok is False
    assert bootstrap.missing_keys(list(by_name.values())) == ["ANTHROPIC_API_KEY"]


def test_key_status_ok_when_key_set(monkeypatch):
    gw = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    statuses = bootstrap.key_status(gw.models)
    assert all(s.ok for s in statuses)
    assert bootstrap.missing_keys(statuses) == []


# --- secure key resolution (WF-DESIGN-0006) ---------------------------------
_CMD_MODEL = (
    "[gateway.models.cloud]\n"
    'base_url = "https://api.anthropic.com/v1"\n'
    'model = "claude-sonnet-4-6"\n'
    'api_key_env = "ANTHROPIC_API_KEY"\n'
    'api_key_cmd = "op read op://Private/Anthropic/credential"\n'
)


def _boom(_cmd):
    raise AssertionError("no key command should have run")


def test_resolve_keys_fills_unset_var_in_memory():
    gw = gateway_config_from_toml(_CMD_MODEL)
    env: dict[str, str] = {}
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return "  sk-from-vault\n"  # secret tools often add surrounding whitespace

    errors = bootstrap.resolve_keys(gw.models, environ=env, runner=runner)
    assert errors == {}
    assert env["ANTHROPIC_API_KEY"] == "sk-from-vault"  # stripped, in memory only
    assert seen == ["op read op://Private/Anthropic/credential"]


def test_resolve_keys_preset_env_var_wins_and_command_is_not_run():
    gw = gateway_config_from_toml(_CMD_MODEL)
    env = {"ANTHROPIC_API_KEY": "already-here"}
    errors = bootstrap.resolve_keys(gw.models, environ=env, runner=_boom)
    assert errors == {}
    assert env["ANTHROPIC_API_KEY"] == "already-here"


def test_resolve_keys_reports_command_failure_without_setting_var():
    gw = gateway_config_from_toml(_CMD_MODEL)
    env: dict[str, str] = {}

    def runner(_cmd):
        raise bootstrap.KeyResolutionError("command exited 1: op read ...")

    errors = bootstrap.resolve_keys(gw.models, environ=env, runner=runner)
    assert "cloud" in errors and "exited 1" in errors["cloud"]
    assert "ANTHROPIC_API_KEY" not in env  # nothing leaks in on failure


def test_resolve_keys_treats_empty_output_as_failure():
    gw = gateway_config_from_toml(_CMD_MODEL)
    env: dict[str, str] = {}
    errors = bootstrap.resolve_keys(gw.models, environ=env, runner=lambda _c: "   \n")
    assert "cloud" in errors and "no output" in errors["cloud"]
    assert "ANTHROPIC_API_KEY" not in env


def test_resolve_keys_noop_for_keyless_and_command_free_models():
    gw = gateway_config_from_toml(bootstrap.render_config(bootstrap.PRESETS["hybrid"]))
    env: dict[str, str] = {}
    # hybrid: local is keyless, cloud names a var but has no api_key_cmd — nothing to run.
    assert bootstrap.resolve_keys(gw.models, environ=env, runner=_boom) == {}
    assert env == {}


def test_suggest_key_commands_lists_one_per_detected_tool():
    found = {"op", "pass"}
    cmds = bootstrap.suggest_key_commands(
        "ANTHROPIC_API_KEY", which=lambda exe: exe if exe in found else None
    )
    assert len(cmds) == 2
    assert any(c.startswith("op read") for c in cmds)
    assert any(c.startswith("pass show") for c in cmds)
    assert all("ANTHROPIC_API_KEY" in c for c in cmds)


def test_suggest_key_commands_empty_when_no_tools_installed():
    assert bootstrap.suggest_key_commands("X", which=lambda _exe: None) == []


def test_suggest_key_commands_covers_team_and_cloud_stores():
    tools = {"vault", "aws", "bw", "doppler", "gopass", "gcloud"}
    cmds = bootstrap.suggest_key_commands(
        "ANTHROPIC_API_KEY", which=lambda exe: exe if exe in tools else None
    )
    joined = "\n".join(cmds)
    assert "vault kv get" in joined
    assert "aws secretsmanager get-secret-value" in joined
    assert "bw get password" in joined
    assert "doppler secrets get" in joined
    assert "gopass show" in joined
    assert "gcloud secrets versions access" in joined
    assert all("ANTHROPIC_API_KEY" in c for c in cmds)
    assert len(cmds) == len(tools)  # one suggestion per detected tool


def test_resolve_keys_runs_a_real_shell_command_by_default():
    # Exercises the actual subprocess path (no injected runner): stdout is the key.
    gw = gateway_config_from_toml(
        "[gateway.models.cloud]\n"
        'base_url = "https://api.anthropic.com/v1"\n'
        'model = "claude-sonnet-4-6"\n'
        'api_key_env = "ANTHROPIC_API_KEY"\n'
        'api_key_cmd = "echo sk-live"\n'
    )
    env: dict[str, str] = {}
    assert bootstrap.resolve_keys(gw.models, environ=env) == {}
    assert env["ANTHROPIC_API_KEY"] == "sk-live"  # trailing newline stripped


def test_resolve_keys_real_command_nonzero_exit_is_reported():
    gw = gateway_config_from_toml(
        "[gateway.models.cloud]\n"
        'base_url = "https://api.anthropic.com/v1"\n'
        'model = "x"\n'
        'api_key_env = "ANTHROPIC_API_KEY"\n'
        'api_key_cmd = "exit 7"\n'
    )
    env: dict[str, str] = {}
    errors = bootstrap.resolve_keys(gw.models, environ=env)
    assert "cloud" in errors and "exited 7" in errors["cloud"]
    assert "ANTHROPIC_API_KEY" not in env


def _scripted(answers):
    """A fake ``ask`` that replays answers in order, applying the default on a blank."""
    it = iter(answers)
    def ask(prompt, default=""):
        try:
            value = next(it)
        except StopIteration:
            value = ""
        return value if value != "" else default
    return ask


def test_wizard_two_tiers_round_trips():
    # tier 1: provider 1 (Ollama), default model/name; add another? yes
    # tier 2: provider 3 (Anthropic), default model/name, cut 0.08; add another? no
    ask = _scripted(["1", "", "", "y", "3", "", "", "0.08", "n"])
    preset = bootstrap.run_init_wizard(ask, lambda _m: None)

    gw = gateway_config_from_toml(preset.config_toml)
    assert set(gw.models) == {"local", "cloud"}
    assert gw.models["local"].base_url == "http://localhost:11434/v1"
    assert gw.models["local"].api_key_env is None  # keyless Ollama
    assert gw.models["cloud"].model == "claude-sonnet-4-6"
    assert gw.models["cloud"].api_key_env == "ANTHROPIC_API_KEY"
    routing = routing_config_from_toml(preset.config_toml)
    assert [(t.model, round(t.min_score, 2)) for t in routing.tiers] == [
        ("local", 0.0), ("cloud", 0.08)
    ]
    assert preset.env_vars == ("ANTHROPIC_API_KEY",)


def test_wizard_custom_providers_keyless_and_keyed():
    ask = _scripted([
        "4", "http://localhost:1234/v1", "", "mymodel", "fast",  # custom, keyless, named "fast"
        "y",
        "4", "https://api.example.com/v1", "EXAMPLE_KEY", "big", "smart", "0.5",
        "n",
    ])
    preset = bootstrap.run_init_wizard(ask, lambda _m: None)

    gw = gateway_config_from_toml(preset.config_toml)
    assert set(gw.models) == {"fast", "smart"}
    assert gw.models["fast"].base_url == "http://localhost:1234/v1"
    assert gw.models["fast"].api_key_env is None  # blank env -> keyless
    assert gw.models["smart"].api_key_env == "EXAMPLE_KEY"
    assert preset.env_vars == ("EXAMPLE_KEY",)


def test_wizard_single_tier_has_a_zero_base():
    ask = _scripted(["2", "", "", "n"])  # one OpenAI tier, then stop
    preset = bootstrap.run_init_wizard(ask, lambda _m: None)
    routing = routing_config_from_toml(preset.config_toml)
    assert [t.min_score for t in routing.tiers] == [0.0]
    assert preset.env_vars == ("OPENAI_API_KEY",)
