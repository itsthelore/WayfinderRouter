"""Tests for the `wayfinder-router` CLI (route and calibrate subcommands)."""

from __future__ import annotations

import io
import json

import pytest
from wayfinder_router.cli import main
from wayfinder_router.complexity import FEATURE_ORDER
from wayfinder_router.config import THRESHOLD_ENV

TRIVIAL = "Say hello."
COMPLEX = (
    "# Plan\n\n## Steps\n\n"
    + "".join(f"- step {i}\n" for i in range(12))
    + "\n## Refs\n\n[a](https://x) [b](https://y)\n\n```py\nx=1\n```\n"
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(THRESHOLD_ENV, raising=False)


def _feed_stdin(monkeypatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


# --- route ------------------------------------------------------------------


def test_route_stdin_human(monkeypatch, capsys):
    _feed_stdin(monkeypatch, TRIVIAL)
    rc = main(["route", "-"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Recommended Model: local" in out


def test_route_json_is_versioned_contract(monkeypatch, capsys):
    _feed_stdin(monkeypatch, COMPLEX)
    rc = main(["route", "-", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["schema_version"] == "3"
    assert payload["recommendation"] in ("local", "cloud")
    assert payload["mode"] == "tiered"
    assert set(payload["features"]) == set(FEATURE_ORDER)


def test_route_is_deterministic(monkeypatch, capsys):
    _feed_stdin(monkeypatch, COMPLEX)
    main(["route", "-", "--json"])
    first = capsys.readouterr().out
    _feed_stdin(monkeypatch, COMPLEX)
    main(["route", "-", "--json"])
    second = capsys.readouterr().out
    assert first == second


def test_route_reads_a_file(tmp_path, capsys):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(TRIVIAL, encoding="utf-8")
    rc = main(["route", str(prompt)])
    assert rc == 0
    assert "Recommended Model:" in capsys.readouterr().out


def test_route_threshold_override_forces_cloud(tmp_path, capsys):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(TRIVIAL, encoding="utf-8")
    rc = main(["route", str(prompt), "--threshold", "0.0", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["recommendation"] == "cloud"  # score 0.0 >= threshold 0.0


def test_route_explain_shows_the_breakdown(monkeypatch, capsys):
    _feed_stdin(monkeypatch, COMPLEX)
    rc = main(["route", "-", "--explain"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Score Breakdown" in out
    assert "word_count" in out


def test_route_file_not_found_is_usage_error(capsys):
    rc = main(["route", "does-not-exist.md"])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err


def test_route_non_utf8_file_is_usage_error(tmp_path, capsys):
    # A non-UTF-8 input file is a clean usage error (EXIT_USAGE), not a raw UnicodeDecodeError traceback.
    bad = tmp_path / "prompt.bin"
    bad.write_bytes(b"\xff\xfe\x00\x80 not utf-8")
    rc = main(["route", str(bad)])
    assert rc == 2
    assert "not valid UTF-8" in capsys.readouterr().err


def test_calibrate_non_utf8_file_is_usage_error(tmp_path, capsys):
    bad = tmp_path / "data.jsonl"
    bad.write_bytes(b"\xff\xfe\x00\x80 not utf-8")
    rc = main(["calibrate", str(bad)])
    assert rc == 2
    assert "not valid UTF-8" in capsys.readouterr().err


def test_route_threshold_out_of_range_is_usage_error(monkeypatch, capsys):
    _feed_stdin(monkeypatch, TRIVIAL)
    rc = main(["route", "-", "--threshold", "5"])
    assert rc == 2
    assert "--threshold" in capsys.readouterr().err


def test_route_malformed_config_is_config_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "wayfinder-router.toml").write_text("[routing]\nthreshold = 2.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _feed_stdin(monkeypatch, TRIVIAL)
    rc = main(["route", "-"])
    assert rc == 1
    assert "threshold" in capsys.readouterr().err


# --- calibrate --------------------------------------------------------------


def _dataset(tmp_path) -> str:
    rows = [{"text": TRIVIAL, "label": "local"}] * 4 + [{"text": COMPLEX, "label": "cloud"}] * 4
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def test_calibrate_emits_toml_to_stdout(tmp_path, capsys):
    rc = main(["calibrate", _dataset(tmp_path), "--mode", "threshold"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[[routing.tiers]]" in captured.out
    assert "mode=threshold" in captured.err


def test_calibrate_writes_a_file(tmp_path, capsys):
    out = tmp_path / "wayfinder-router.toml"
    rc = main(["calibrate", _dataset(tmp_path), "--mode", "classifier", "--out", str(out)])
    assert rc == 0
    assert "[routing.classifier]" in out.read_text(encoding="utf-8")


def test_calibrate_missing_dataset_is_usage_error(capsys):
    rc = main(["calibrate", "nope.jsonl"])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err


def test_calibrate_cost_quality_emits_cost_and_reports_savings(tmp_path, capsys):
    rc = main([
        "calibrate", _dataset(tmp_path), "--mode", "threshold",
        "--objective", "cost-quality", "--target-savings", "0.4",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "cost = " in captured.out
    assert "objective=cost-quality" in captured.err
    assert "cost_savings=" in captured.err


def test_calibrate_cost_quality_accepts_custom_costs(tmp_path, capsys):
    rc = main([
        "calibrate", _dataset(tmp_path), "--mode", "threshold",
        "--objective", "cost-quality", "--target-savings", "0.3",
        "--costs", "local=0.1,cloud=1.0",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cost = 0.1" in out  # the cheap tier's cost
    assert "cost = 1.0" in out  # ...and the dear tier's — each tier keeps its own cost


def test_calibrate_unreachable_savings_is_config_error(tmp_path, capsys):
    rc = main([
        "calibrate", _dataset(tmp_path), "--mode", "threshold",
        "--objective", "cost-quality", "--target-savings", "0.99",
    ])
    assert rc == 1
    assert "target savings" in capsys.readouterr().err


# --- chat (demo launcher) ---------------------------------------------------

import threading  # noqa: E402
import webbrowser  # noqa: E402

from wayfinder_router.cli import _demo_url  # noqa: E402


class _FakeTimer:
    instances: list = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.started = self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def gw():
    return pytest.importorskip("wayfinder_router.gateway")


@pytest.fixture
def fake_browser(monkeypatch):
    _FakeTimer.instances.clear()
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: None)


def test_demo_url_maps_wildcard_to_loopback():
    assert _demo_url("127.0.0.1", 8088) == "http://127.0.0.1:8088/demo"
    assert _demo_url("0.0.0.0", 9000) == "http://127.0.0.1:9000/demo"
    assert _demo_url("::", 8088) == "http://127.0.0.1:8088/demo"
    assert _demo_url("example.internal", 80) == "http://example.internal:80/demo"


def test_webchat_runs_gateway_and_opens_browser(monkeypatch, gw, fake_browser, capsys):
    captured: dict = {}
    monkeypatch.setattr(gw, "run", lambda **kw: captured.update(kw))
    rc = main(["webchat"])
    assert rc == 0
    assert captured == {"start_dir": ".", "host": "127.0.0.1", "port": 8088,
                        "dry_run": False, "timeout": None}
    assert "http://127.0.0.1:8088/demo" in capsys.readouterr().out
    assert _FakeTimer.instances[-1].args == ("http://127.0.0.1:8088/demo",)
    assert _FakeTimer.instances[-1].started is True


def test_webchat_honours_port_and_dry_run(monkeypatch, gw, fake_browser):
    captured: dict = {}
    monkeypatch.setattr(gw, "run", lambda **kw: captured.update(kw))
    assert main(["webchat", "--port", "9000", "--dry-run"]) == 0
    assert captured["port"] == 9000 and captured["dry_run"] is True


def test_webchat_no_open_skips_browser(monkeypatch, gw, fake_browser):
    monkeypatch.setattr(gw, "run", lambda **kw: None)
    assert main(["webchat", "--no-open"]) == 0
    assert _FakeTimer.instances == []  # no browser timer scheduled


def test_webchat_missing_extra_returns_usage_and_cancels_open(monkeypatch, gw, fake_browser, capsys):
    def boom(**kw):
        raise gw.GatewayUnavailable("the gateway needs its extra: pip install 'wayfinder-router[gateway]'")
    monkeypatch.setattr(gw, "run", boom)
    rc = main(["webchat"])
    assert rc == 2  # EXIT_USAGE
    assert "gateway needs its extra" in capsys.readouterr().err
    assert _FakeTimer.instances[-1].cancelled is True  # scheduled open was cancelled


def test_webchat_nudges_to_init_when_no_models(monkeypatch, gw, fake_browser, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # no wayfinder-router.toml here
    monkeypatch.setattr(gw, "run", lambda **kw: None)
    assert main(["webchat", "--no-open"]) == 0
    assert "wayfinder-router init" in capsys.readouterr().err


# --- init / doctor ----------------------------------------------------------


def test_init_scaffolds_config_and_env_example(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = main(["init"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (tmp_path / "wayfinder-router.toml").is_file()
    env_text = (tmp_path / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env_text  # the name only
    assert "✗ not set" in out  # the cloud key check flags the unset var
    assert 'export OPENAI_API_KEY="..."' in out
    # the scaffold is loadable
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert "[gateway.models.local]" in cfg and "[gateway.models.cloud]" in cfg


def test_init_refuses_to_clobber_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wayfinder-router.toml").write_text("# mine\n", encoding="utf-8")
    assert main(["init"]) == 2  # EXIT_USAGE
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "wayfinder-router.toml").read_text() == "# mine\n"  # untouched


def test_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wayfinder-router.toml").write_text("# mine\n", encoding="utf-8")
    assert main(["init", "--force"]) == 0
    assert "gateway.models.cloud" in (tmp_path / "wayfinder-router.toml").read_text()


def test_init_print_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--print"]) == 0
    assert "[gateway.models.cloud]" in capsys.readouterr().out
    assert not (tmp_path / "wayfinder-router.toml").exists()
    assert not (tmp_path / ".env.example").exists()


def test_init_openai_preset(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["init", "--preset", "openai"]) == 0
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert "gpt-4o-mini" in cfg and "gpt-4o" in cfg
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out and 'export OPENAI_API_KEY="..."' in out


def test_init_gemini_preset(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["init", "--preset", "gemini"]) == 0
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert "gemini-2.5-flash" in cfg and "gemini-2.5-pro" in cfg
    assert "generativelanguage.googleapis.com/v1beta/openai" in cfg
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY" in out and 'export GEMINI_API_KEY="..."' in out


def test_init_unknown_preset_is_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "nope"]) == 2
    assert "unknown preset" in capsys.readouterr().err


def test_init_local_preset_is_offline_and_keyless(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "local"]) == 0
    text = (tmp_path / "wayfinder-router.toml").read_text()
    assert "offline = true" in text
    assert "api_key_env" not in text


def test_init_apple_local_preset_is_explicit_and_keyless(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--preset", "apple-local", "--keychain"]) == 0
    text = (tmp_path / "wayfinder-router.toml").read_text()
    assert 'provider = "apple-foundation-models"' in text
    assert 'model = "system-default"' in text
    assert "offline = true" in text
    assert "api_key_env" not in text
    assert "api_key_cmd" not in text
    assert not (tmp_path / ".env.example").exists()


def test_init_creates_missing_parent_directory(tmp_path, capsys):
    target = tmp_path / "nested" / "config" / "wayfinder-router.toml"
    assert main(["init", "--preset", "local", "--path", str(target)]) == 0
    assert target.exists()


def test_init_keychain_reaches_the_output(tmp_path, monkeypatch, capsys):
    """`--keychain` (WF-ADR-0044) threads through to the rendered config, with --print and
    with a real write; without the flag no Keychain reference appears."""
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--print", "--keychain"]) == 0
    printed = capsys.readouterr().out
    assert (
        'api_key_cmd = "/usr/bin/security find-generic-password '
        '-s wayfinder-router -a OPENAI_API_KEY -w"' in printed
    )
    assert main(["init", "--keychain"]) == 0
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert "find-generic-password" in cfg
    assert main(["init", "--force"]) == 0  # plain init: no reference
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert 'api_key_cmd = "/usr/bin/security' not in cfg


def test_doctor_without_config_is_usage_error(tmp_path, capsys):
    assert main(["doctor", "--dir", str(tmp_path)]) == 2
    assert "no wayfinder-router.toml" in capsys.readouterr().err


def test_doctor_reports_missing_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    main(["init"])
    capsys.readouterr()  # drop init's output
    rc = main(["doctor", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1  # EXIT_CONFIG: a named key is unset
    assert "✗ not set" in out and "not ready" in out


def test_doctor_ready_when_keys_present(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    main(["init"])
    capsys.readouterr()
    rc = main(["doctor", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ready:" in out and "✓ set" in out and "keyless ✓" in out


def test_init_interactive_print_streams_toml_to_stdout(monkeypatch, capsys):
    # one Ollama tier (provider 1, defaults), then "no" to add another
    _feed_stdin(monkeypatch, "1\n\n\nn\n")
    rc = main(["init", "-i", "--print"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[[routing.tiers]]" in out and "[gateway.models.local]" in out
    assert "localhost:11434" in out  # the Ollama base_url


def test_init_interactive_writes_config_and_reports_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Ollama local + Anthropic cloud (cut 0.08)
    _feed_stdin(monkeypatch, "1\n\n\ny\n3\n\n\n0.08\nn\n")
    rc = main(["init", "-i"])
    out = capsys.readouterr().out
    assert rc == 0
    cfg = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert "[gateway.models.cloud]" in cfg and "claude-sonnet-4-6" in cfg
    assert (tmp_path / ".env.example").read_text(encoding="utf-8").count("ANTHROPIC_API_KEY=") == 1
    assert "✗ not set" in out  # the cloud key check still runs after the wizard


def test_keys_new_mints_paste_able_block(capsys):
    from wayfinder_router import vkeys
    from wayfinder_router.cli import main

    assert main(["keys", "new", "--id", "team-a", "--tag", "prod"]) == 0
    out = capsys.readouterr().out
    assert "[gateway.keys.team-a]" in out and 'tags = ["prod"]' in out
    # the printed key verifies against the printed hash
    import re
    key = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("wf-")][0]
    khash = re.search(r'hash = "([0-9a-f]{64})"', out).group(1)
    assert vkeys.verify(key, khash)


def test_keys_new_escapes_toml_unsafe_id_and_tag(capsys):
    # A TOML-special --id/--tag (dot, quote) must produce valid TOML that round-trips to the exact
    # id/tag, not a malformed block or injected structure.
    import tomllib

    from wayfinder_router.cli import main

    assert main(["keys", "new", "--id", 'we"ird.id', "--tag", 'a"b', "--tag", "ok"]) == 0
    out = capsys.readouterr().out
    block = out.split("# Give this key", 1)[0]  # the config block (comments + TOML), before the key
    data = tomllib.loads(block)
    keys = data["gateway"]["keys"]
    assert 'we"ird.id' in keys  # a single quoted key, not nested by the dot
    assert keys['we"ird.id']["tags"] == ['a"b', "ok"]


def test_config_set_flips_offline_and_preserves_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WAYFINDER_CONFIG", raising=False)
    assert main(["init"]) == 0
    before = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert main(["config", "set", "gateway.offline", "true"]) == 0
    after = (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")
    assert after.endswith("\n[gateway]\noffline = true\n")
    assert after.startswith(before.rstrip("\n") + "\n") or before in after  # nothing clobbered
    # flip back off — the in-place replace path
    assert main(["config", "set", "gateway.offline", "false"]) == 0
    assert "offline = false" in (tmp_path / "wayfinder-router.toml").read_text(encoding="utf-8")


def test_config_set_explicit_path(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("WAYFINDER_CONFIG", raising=False)
    target = tmp_path / "elsewhere" / "wayfinder-router.toml"
    target.parent.mkdir()
    assert main(["init", "--path", str(target)]) == 0
    capsys.readouterr()
    assert main(["config", "set", "gateway.offline", "true", "--path", str(target)]) == 0
    assert "offline = true" in target.read_text(encoding="utf-8")
    assert str(target) in capsys.readouterr().err


def test_config_set_rejects_unknown_key_bad_value_and_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WAYFINDER_CONFIG", raising=False)
    assert main(["config", "set", "gateway.offline", "true"]) == 2  # no config anywhere
    assert "run `wayfinder-router init`" in capsys.readouterr().err
    assert main(["init"]) == 0
    assert main(["config", "set", "routing.threshold", "0.5"]) == 2  # off-whitelist
    assert "unknown config key" in capsys.readouterr().err
    assert main(["config", "set", "gateway.offline", "maybe"]) == 2
    assert "'true' or 'false'" in capsys.readouterr().err


def test_config_read_routing_emits_json(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.delenv("WAYFINDER_CONFIG", raising=False)
    target = tmp_path / "wayfinder-router.toml"
    target.write_text("[routing]\nthreshold = 0.42\n", encoding="utf-8")

    assert main(["config", "read-routing", "--path", str(target)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "binary"
    assert payload["threshold"] == 0.42
    assert [row["id"] for row in payload["weights"]] == list(FEATURE_ORDER)


def test_config_apply_routing_writes_threshold_and_preserves_gateway(tmp_path, monkeypatch):
    import io

    monkeypatch.delenv("WAYFINDER_CONFIG", raising=False)
    target = tmp_path / "wayfinder-router.toml"
    target.write_text(
        "[gateway]\noffline = true\n\n"
        "[gateway.models.local]\nbase_url = \"http://localhost:11434/v1\"\nmodel = \"llama\"\n\n"
        "[routing]\nthreshold = 0.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("[routing]\nthreshold = 0.73\n"))

    assert main(["config", "apply-routing", "--path", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert "[gateway]\noffline = true" in text
    assert "[gateway.models.local]" in text
    assert "threshold = 0.73" in text
    assert "threshold = 0.2" not in text


def test_config_apply_routing_rejects_bad_threshold(tmp_path, monkeypatch, capsys):
    import io

    target = tmp_path / "wayfinder-router.toml"
    target.write_text("[routing]\nthreshold = 0.2\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("[routing]\nthreshold = 2.0\n"))

    assert main(["config", "apply-routing", "--path", str(target)]) == 1
    assert "threshold" in capsys.readouterr().err
    assert "threshold = 0.2" in target.read_text(encoding="utf-8")


def test_config_apply_routing_rejects_invalid_duplicate_and_descending_tiers(
    tmp_path, monkeypatch, capsys
):
    import io

    target = tmp_path / "wayfinder-router.toml"
    target.write_text("[routing]\nthreshold = 0.2\n", encoding="utf-8")
    cases = [
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"local\"\n",
        (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"local\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"cloud\"\n"
        ),
        (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"local\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.8\nmodel = \"cloud\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.4\nmodel = \"mid\"\n"
        ),
    ]
    for fragment in cases:
        monkeypatch.setattr("sys.stdin", io.StringIO(fragment))
        assert main(["config", "apply-routing", "--path", str(target)]) == 1
        assert "tier" in capsys.readouterr().err
    assert "threshold = 0.2" in target.read_text(encoding="utf-8")


def test_config_apply_routing_rejects_negative_or_unknown_weights(tmp_path, monkeypatch, capsys):
    import io

    target = tmp_path / "wayfinder-router.toml"
    target.write_text("[routing]\nthreshold = 0.2\n", encoding="utf-8")
    for fragment in (
        "[routing]\nthreshold = 0.2\nweights = { word_count = -1 }\n",
        "[routing]\nthreshold = 0.2\nweights = { surprise = 1 }\n",
    ):
        monkeypatch.setattr("sys.stdin", io.StringIO(fragment))
        assert main(["config", "apply-routing", "--path", str(target)]) == 1
        assert "weights" in capsys.readouterr().err
    assert "threshold = 0.2" in target.read_text(encoding="utf-8")
