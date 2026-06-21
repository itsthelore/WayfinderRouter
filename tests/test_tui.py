"""Tests for the terminal chat UI (WF-DESIGN-0001).

The pure pieces (palette, decision classification, command parsing) are tested
directly; the rich renderers are smoke-tested via a recording Console. The
interactive loop itself is not unit-tested.
"""

from __future__ import annotations

import pytest

pytest.importorskip("rich")  # the [tui] extra

from wayfinder_router import tui  # noqa: E402
from wayfinder_router.cli import main  # noqa: E402


def test_palette_for_resolves_themes(monkeypatch):
    assert tui.palette_for("light")["accent"] == "#10a37f"
    assert tui.palette_for("dark")["accent"] == "#19c8a4"
    monkeypatch.delenv("WAYFINDER_THEME", raising=False)
    assert tui.palette_for("auto") == tui.THEMES["dark"]
    monkeypatch.setenv("WAYFINDER_THEME", "light")
    assert tui.palette_for("auto")["accent"] == "#10a37f"
    assert tui.palette_for("chartreuse") == tui.THEMES["dark"]  # unknown -> dark


def test_parse_command():
    assert tui.parse_command("/threshold 0.3") == ("threshold", "0.3")
    assert tui.parse_command("/help") == ("help", "")
    assert tui.parse_command("hello there") == (None, "hello there")
    assert tui.parse_command("/") == ("", "")


def test_decide_threshold_extremes_classify(tmp_path):
    local = tui.decide("anything at all", start_dir=str(tmp_path), threshold=1.0)
    assert local.is_local is True
    assert local.contributions  # the "why" breakdown is present
    assert local.model

    cloud = tui.decide("anything at all", start_dir=str(tmp_path), threshold=0.0)
    assert cloud.is_local is False


def test_render_decision_collapses_by_default_expands_on_demand():
    from rich.console import Console

    from wayfinder_router.complexity import DEFAULT_WEIGHTS, explain_score, extract_features

    palette = tui.palette_for("dark")
    contribs = explain_score(extract_features("prove that the limit exists"), DEFAULT_WEIGHTS)
    top = sorted(contribs, key=lambda c: -c.contribution)[0].name
    decision = tui.Decision(
        text="x", model="local", score=0.12, mode="tiered", is_local=True, contributions=contribs
    )

    collapsed = Console(record=True, width=80)
    collapsed.print(tui.render_decision(decision, palette, expanded=False))
    ctext = collapsed.export_text()
    assert "LOCAL" in ctext and "local" in ctext and "0.12" in ctext
    assert "/why" in ctext  # the expand affordance
    assert top not in ctext  # breakdown hidden when collapsed

    expanded = Console(record=True, width=80)
    expanded.print(tui.render_decision(decision, palette, expanded=True))
    assert top in expanded.export_text()  # breakdown shown when expanded


def test_render_decision_cloud_label():
    from rich.console import Console

    cloud = tui.Decision(
        text="x", model="cloud", score=0.88, mode="tiered", is_local=False, contributions=[]
    )
    con = Console(record=True, width=80)
    con.print(tui.render_decision(cloud, tui.palette_for("dark"), expanded=False))
    assert "CLOUD" in con.export_text()


def test_render_welcome_smoke():
    from rich.console import Console

    con = Console(record=True, width=80)
    con.print(tui.render_welcome(tui.palette_for("dark"), subtitle="decision-first"))
    assert "Wayfinder" in con.export_text()


def test_render_settings_smoke():
    from rich.console import Console

    state = tui.TuiState(threshold=0.3, scope="turn", sticky=True, cooldown=2, theme="dark")
    con = Console(record=True, width=80)
    con.print(tui.render_settings(state, tui.palette_for("dark")))
    out = con.export_text()
    assert "settings" in out and "threshold" in out and "0.30" in out
    assert "scope" in out and "turn" in out and "cooldown 2" in out


def test_cli_chat_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["chat", "--help"])
    assert exc.value.code == 0


def test_cli_chat_rejects_bad_threshold(capsys):
    assert main(["chat", "--threshold", "2.0"]) == 2  # EXIT_USAGE, before the loop starts
    assert "threshold" in capsys.readouterr().err
