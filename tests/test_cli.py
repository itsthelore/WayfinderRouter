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
    assert rc == 0
    assert "cost = 0.1" in capsys.readouterr().out


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
