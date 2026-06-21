"""Tests for the terminal chat UI (WF-DESIGN-0001).

The pure pieces (palette, mascot, decision classification, command parsing) are
tested directly; the rich renderers are smoke-tested via a recording Console. The
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


def test_mascot_frame_nonempty_and_cycles():
    frame = tui.mascot(0)
    assert frame and all(isinstance(row, str) for row in frame)
    assert tui.mascot(999)  # cycles by index, never IndexError
    assert tui.mascot(0, ascii_only=True)


def test_parse_command():
    assert tui.parse_command("/threshold 0.3") == ("threshold", "0.3")
    assert tui.parse_command("/help") == ("help", "")
    assert tui.parse_command("hello there") == (None, "hello there")
    assert tui.parse_command("/") == ("", "")


def test_decide_threshold_extremes_classify(tmp_path):
    # threshold 1.0: nothing reaches the cloud tier -> local; 0.0: everything escalates.
    local = tui.decide("anything at all", start_dir=str(tmp_path), threshold=1.0)
    assert local.is_local is True
    assert local.contributions  # the "why" breakdown is present
    assert local.model

    cloud = tui.decide("anything at all", start_dir=str(tmp_path), threshold=0.0)
    assert cloud.is_local is False


def test_render_decision_smoke():
    from rich.console import Console

    palette = tui.palette_for("dark")

    local = tui.Decision(
        text="x", model="local", score=0.12, mode="tiered", is_local=True, contributions=[]
    )
    con = Console(record=True, width=80)
    con.print(tui.render_decision(local, palette, show_why=False))
    out = con.export_text()
    assert "LOCAL" in out and "local" in out and "0.12" in out

    cloud = tui.Decision(
        text="x", model="cloud", score=0.88, mode="tiered", is_local=False, contributions=[]
    )
    con2 = Console(record=True, width=80)
    con2.print(tui.render_decision(cloud, palette, show_why=False))
    assert "CLOUD" in con2.export_text()


def test_render_welcome_smoke():
    from rich.console import Console

    con = Console(record=True, width=80)
    con.print(tui.render_welcome(tui.palette_for("dark"), subtitle="decision-first"))
    assert "Wayfinder" in con.export_text()


def test_cli_tui_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["tui", "--help"])
    assert exc.value.code == 0


def test_cli_tui_rejects_bad_threshold(capsys):
    assert main(["tui", "--threshold", "2.0"]) == 2  # EXIT_USAGE, before the loop starts
    assert "threshold" in capsys.readouterr().err
