"""Tests for the `wayfinder` CLI (route and calibrate subcommands)."""

from __future__ import annotations

import io
import json

import pytest
from wayfinder.cli import main
from wayfinder.complexity import FEATURE_ORDER
from wayfinder.config import THRESHOLD_ENV

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
    assert payload["schema_version"] == "2"
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
    (tmp_path / "wayfinder.toml").write_text("[routing]\nthreshold = 2.0\n", encoding="utf-8")
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
    out = tmp_path / "wayfinder.toml"
    rc = main(["calibrate", _dataset(tmp_path), "--mode", "classifier", "--out", str(out)])
    assert rc == 0
    assert "[routing.classifier]" in out.read_text(encoding="utf-8")


def test_calibrate_missing_dataset_is_usage_error(capsys):
    rc = main(["calibrate", "nope.jsonl"])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err
