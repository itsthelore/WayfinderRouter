"""Tests for the terminal chat UI (WF-DESIGN-0001).

The pure pieces (palette, decision classification, command parsing) are tested
directly; the rich renderers are smoke-tested via a recording Console. The
interactive loop itself is not unit-tested.
"""

from __future__ import annotations

import pytest

pytest.importorskip("rich")  # ships in the default install (WF-ADR-0029)

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
    out = con.export_text()
    assert "Wayfinder" in out and "decision-first" in out


def test_render_welcome_compact_shows_wordmark_text():
    from rich.console import Console

    con = Console(record=True, width=40)
    con.print(tui.render_welcome(tui.palette_for("dark"), subtitle="v0", compact=True))
    assert "Wayfinder" in con.export_text()


def test_status_and_footer_bars_smoke():
    from rich.console import Console

    palette = tui.palette_for("dark")
    state = tui.TuiState(threshold=0.08, scope="turn")
    con = Console(record=True, width=80)
    con.print(tui._status_bar(state, palette))
    con.print(tui._status_bar(state, palette, note="streaming…"))
    con.print(tui._footer_bar(palette))
    out = con.export_text()
    assert "threshold 0.08" in out and "local" in out and "cloud" in out
    assert "streaming" in out and "help" in out


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


def test_model_reply_invokes_mapped_model(monkeypatch):
    from wayfinder_router import gateway

    captured: dict = {}

    def fake(model, messages, timeout=60.0):
        captured["messages"] = messages
        return f"hi from {model.model}"

    monkeypatch.setattr(gateway, "invoke_messages", fake)
    models = {"local": gateway.GatewayModel(base_url="http://x/v1", model="m7b")}
    decision = tui.Decision(text="q", model="local", score=0.1, mode="tiered", is_local=True)
    msgs = [{"role": "user", "content": "q"}]
    assert tui.model_reply(models, decision, msgs) == "hi from m7b"
    assert captured["messages"] == msgs  # full conversation handed to the relay


def test_model_reply_none_when_model_unmapped():
    from wayfinder_router import gateway

    models = {"local": gateway.GatewayModel(base_url="http://x/v1", model="m7b")}
    decision = tui.Decision(text="q", model="cloud", score=0.9, mode="tiered", is_local=False)
    assert tui.model_reply(models, decision, [{"role": "user", "content": "q"}]) is None


def test_render_reply_smoke():
    from rich.console import Console

    con = Console(record=True, width=80)
    con.print(tui.render_reply("**bold** and `code`"))
    out = con.export_text()
    assert "bold" in out and "code" in out


def test_decision_from_debug_builds_decision():
    payload = {
        "model": "cloud",
        "score": 0.71,
        "mode": "scored",
        "tiers": [{"min_score": 0.0, "model": "local"}, {"min_score": 0.3, "model": "cloud"}],
        "contributions": [
            {"name": "reasoning_terms", "value": 2, "normalized": 0.5,
             "weight": 1.0, "contribution": 0.12},
        ],
    }
    decision = tui.decision_from_debug(payload)
    assert decision.model == "cloud" and decision.is_local is False
    assert abs(decision.score - 0.71) < 1e-9
    assert decision.contributions[0].name == "reasoning_terms"
    # the lowest tier's model classifies as local
    assert tui.decision_from_debug({**payload, "model": "local", "score": 0.05}).is_local is True


def test_chat_app_routes_decision_in_dry_run(tmp_path):
    """The full-screen app mounts, routes a typed prompt, and shows a decision line."""
    import asyncio

    pytest.importorskip("textual")

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            before = len(app.query("#transcript Static"))
            assert before >= 1  # the welcome header was written
            assert not app.history
            app.query_one("#entry").value = "what is an API?"
            await pilot.press("enter")
            await pilot.pause()
            # the prompt was routed: a decision recorded, the transcript grew, input cleared
            assert len(app.history) == 1 and app.history[0].model
            assert len(app.query("#transcript Static")) > before
            assert app.messages == [{"role": "user", "content": "what is an API?"}]
            assert app.query_one("#entry").value == ""

    asyncio.run(scenario())


# --- forced routing / /btw --------------------------------------------------


def test_resolve_target_forces_routes():
    decision = tui.Decision(
        text="x", model="local", score=0.1, mode="tiered", is_local=True,
        targets=["local", "cloud"],
    )
    assert tui.resolve_target(None, decision) == ("local", True)  # natural
    assert tui.resolve_target("prefer-local", decision) == ("local", True)
    assert tui.resolve_target("prefer-hosted", decision) == ("cloud", False)
    assert tui.resolve_target("cloud", decision) == ("cloud", False)  # exact name
    assert tui.resolve_target("local", decision) == ("local", True)
    # no tier info -> fall back to the decision's own model
    bare = tui.Decision(text="x", model="m", score=0.1, mode="t", is_local=True)
    assert tui.resolve_target("prefer-hosted", bare) == ("m", True)


def test_pin_label():
    assert tui._pin_label("prefer-local") == "local"
    assert tui._pin_label("prefer-hosted") == "cloud"
    assert tui._pin_label(None) == "auto"
    assert tui._pin_label("smart") == "smart"


def test_decide_populates_tier_targets(tmp_path):
    decision = tui.decide("anything", start_dir=str(tmp_path), threshold=0.3)
    assert decision.targets == ["local", "cloud"]  # binary threshold -> two tiers, in order


def test_render_decision_forced_shows_override_and_natural():
    from rich.console import Console

    palette = tui.palette_for("dark")
    decision = tui.Decision(
        text="x", model="local", score=0.04, mode="tiered", is_local=True,
        targets=["local", "cloud"],
    )
    con = Console(record=True, width=100)
    con.print(tui.render_decision(decision, palette, forced_to=("cloud", False)))
    out = con.export_text()
    assert "CLOUD" in out and "forced" in out and "cloud" in out
    assert "would route" in out and "LOCAL" in out  # decision-first transparency

    # forcing to the route the scorer already picked drops the "would route" note
    same = Console(record=True, width=100)
    same.print(tui.render_decision(decision, palette, forced_to=("local", True)))
    same_out = same.export_text()
    assert "forced" in same_out and "would route" not in same_out


def test_decision_from_debug_uses_natural_route_even_when_pinned():
    payload = {
        "model": "local",  # the gateway pinned to local…
        "score": 0.71, "mode": "pinned",
        "tiers": [{"min_score": 0.0, "model": "local"}, {"min_score": 0.3, "model": "cloud"}],
        "contributions": [],
    }
    decision = tui.decision_from_debug(payload)
    # …but the decision-first view is the natural route for score 0.71 (cloud)
    assert decision.model == "cloud" and decision.is_local is False
    assert decision.targets == ["local", "cloud"]


def test_estimate_tokens():
    assert tui.estimate_tokens("") == 1
    assert tui.estimate_tokens("a" * 40) == 10  # ~4 chars/token


def test_account_turn_and_summary():
    tally = tui.SessionCost()
    tui.account_turn(tally, is_local=True, tokens=1000, chosen_cost=0.0, cloud_cost=0.009)
    tui.account_turn(tally, is_local=False, tokens=1000, chosen_cost=0.009, cloud_cost=0.009)
    assert tally.calls == 2 and tally.local == 1 and tally.priced
    assert abs(tally.saved - 0.009) < 1e-9  # the local turn saved a full cloud turn
    assert abs(tally.spent - 0.009) < 1e-9  # only the cloud turn spent
    summary = tui.cost_summary(tally)
    assert "1/2 local" in summary and "saved" in summary


def test_account_turn_without_costs_counts_only():
    tally = tui.SessionCost()
    tui.account_turn(tally, is_local=True, tokens=500, chosen_cost=None, cloud_cost=None)
    assert tally.calls == 1 and not tally.priced
    assert tui.cost_summary(tally) == "1/1 local"  # no $ without configured costs


def test_render_cost_smoke():
    from rich.console import Console

    tally = tui.SessionCost(calls=3, local=2, spent=0.01, saved=0.05, priced=True)
    con = Console(record=True, width=80)
    con.print(tui.render_cost(tally, tui.palette_for("dark")))
    out = con.export_text()
    assert "saved" in out and "67%" in out  # 2 of 3 kept local


def test_chat_app_accounts_cost(tmp_path, monkeypatch):
    import asyncio

    pytest.importorskip("textual")
    from wayfinder_router import gateway

    monkeypatch.setattr(
        gateway, "stream_messages",
        lambda model, messages, timeout=60.0: iter(["Hello ", "there."]),
    )
    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", threshold=0.5)
    app.models = {
        "local": gateway.GatewayModel(base_url="http://x/v1", model="m", cost_per_1k=0.0),
        "cloud": gateway.GatewayModel(base_url="http://y/v1", model="c", cost_per_1k=0.009),
    }

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#entry").value = "hi"
            await pilot.press("enter")
            for _ in range(100):  # let the threaded reply worker finish + account
                if app._cost.calls >= 1 and not app._busy:
                    break
                await asyncio.sleep(0.02)
                await pilot.pause()
            assert app._cost.calls == 1 and app._cost.local == 1
            assert app._cost.priced and app._cost.saved > 0

    asyncio.run(scenario())


def test_render_threads_lists_and_empty():
    from rich.console import Console

    from wayfinder_router import threads

    con = Console(record=True, width=80)
    con.print(tui.render_threads([], tui.palette_for("dark")))
    assert "no saved conversations" in con.export_text()

    t = threads.new_thread()
    t.title, t.updated = "what is an API?", "2026-06-22T07:00:00Z"
    con2 = Console(record=True, width=90)
    con2.print(tui.render_threads([t], tui.palette_for("dark")))
    out = con2.export_text()
    assert "what is an API?" in out and "/open" in out


def test_chat_app_persists_and_reopens_threads(tmp_path, monkeypatch):
    import asyncio

    pytest.importorskip("textual")
    monkeypatch.setenv("WAYFINDER_DATA_DIR", str(tmp_path))

    from wayfinder_router import threads

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#entry").value = "what is an API?"
            await pilot.press("enter")
            await pilot.pause()
            # the turn auto-saved to disk (decision-only is still a conversation)
            saved = threads.list_threads(tmp_path / "threads")
            assert len(saved) == 1 and saved[0].title == "what is an API?"
            first_id = app._thread.id

            app.query_one("#entry").value = "/new"
            await pilot.press("enter")
            await pilot.pause()
            assert app.messages == [] and app._thread.id != first_id  # fresh thread

            app.query_one("#entry").value = "/threads"
            await pilot.press("enter")
            await pilot.pause()
            assert app._thread_list and app._thread_list[0].title == "what is an API?"

            app.query_one("#entry").value = "/open 1"
            await pilot.press("enter")
            await pilot.pause()
            # reopened: messages restored and we're continuing that thread
            assert app.messages == [{"role": "user", "content": "what is an API?"}]
            assert app._thread.id == first_id

    asyncio.run(scenario())


def test_friendly_error():
    ollama = tui._friendly_error("Connection refused", "http://localhost:11434/v1")
    assert "Ollama" in ollama
    generic = tui._friendly_error("connect timed out", "https://api.example.com/v1")
    assert "is it running" in generic and "Ollama" not in generic
    assert tui._friendly_error("400 bad request", "http://x/v1") == "upstream error: 400 bad request"


def test_chat_app_tab_expands_why_and_esc_cancels(tmp_path):
    import asyncio

    pytest.importorskip("textual")

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#entry").value = "explain something"
            await pilot.press("enter")
            await pilot.pause()
            before = len(app.query("#transcript Static"))
            await pilot.press("tab")  # expand the last decision's why
            await pilot.pause()
            assert len(app.query("#transcript Static")) > before

            app._busy = True  # esc cancels an in-flight reply, never quits
            app._cancel.clear()
            app.action_cancel()
            assert app._cancel.is_set()

    asyncio.run(scenario())


def test_slash_command_autocomplete():
    import asyncio

    from textual.suggester import SuggestFromList

    assert "/btw" in tui._SLASH_COMMANDS and "/init" in tui._SLASH_COMMANDS
    suggester = SuggestFromList(tui._SLASH_COMMANDS, case_sensitive=False)

    async def check():
        assert await suggester.get_suggestion("/in") == "/init"
        assert await suggester.get_suggestion("/mod") == "/models"
        assert await suggester.get_suggestion("what is an API?") is None  # plain prompts

    asyncio.run(check())


def test_chat_app_input_history(tmp_path):
    import asyncio

    pytest.importorskip("textual")

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            for line in ("first prompt", "second prompt"):
                app.query_one("#entry").value = line
                await pilot.press("enter")
                await pilot.pause()
            entry = app.query_one("#entry")
            assert entry.value == ""  # cleared after submit
            await pilot.press("up")
            assert entry.value == "second prompt"  # most recent first
            await pilot.press("up")
            assert entry.value == "first prompt"
            await pilot.press("up")
            assert entry.value == "first prompt"  # clamps at the oldest
            await pilot.press("down")
            assert entry.value == "second prompt"
            await pilot.press("down")
            assert entry.value == ""  # past the newest -> live (empty) line

    asyncio.run(scenario())


def test_chat_app_ctrl_c_cancels_an_in_flight_turn(tmp_path):
    import asyncio

    pytest.importorskip("textual")

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True  # simulate a reply in flight
            app._cancel.clear()
            app.action_interrupt()
            # first ctrl-c requests cancel instead of quitting
            assert app._cancel.is_set() and app._busy

    asyncio.run(scenario())


def test_render_models_shows_key_status(monkeypatch):
    from rich.console import Console

    from wayfinder_router import gateway

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    models = {
        "local": gateway.GatewayModel(base_url="http://localhost:11434/v1", model="llama3.1"),
        "cloud": gateway.GatewayModel(
            base_url="https://api.anthropic.com/v1", model="claude-sonnet-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        ),
    }
    con = Console(record=True, width=100)
    con.print(tui.render_models(models, tui.palette_for("dark")))
    out = con.export_text()
    assert "local" in out and "keyless" in out
    assert "ANTHROPIC_API_KEY" in out and "not set" in out


def test_render_models_empty_points_at_init():
    from rich.console import Console

    con = Console(record=True, width=80)
    con.print(tui.render_models({}, tui.palette_for("dark")))
    assert "/init" in con.export_text()


def test_render_empty_state_smoke():
    from rich.console import Console

    con = Console(record=True, width=80)
    con.print(tui.render_empty_state(tui.palette_for("dark")))
    out = con.export_text()
    assert "/init" in out and "preview" in out.lower()


def test_chat_app_init_scaffolds_and_loads_models(tmp_path, monkeypatch):
    import asyncio

    pytest.importorskip("textual")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark")  # no config here -> empty state

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.models  # preview / empty state
            app.query_one("#entry").value = "/init"
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "wayfinder-router.toml").is_file()
            assert (tmp_path / ".env.example").is_file()
            assert set(app.models) == {"local", "cloud"}  # hybrid preset loaded in place
            # a second /init refuses to clobber and leaves the models intact
            app.query_one("#entry").value = "/init"
            await pilot.press("enter")
            await pilot.pause()
            assert set(app.models) == {"local", "cloud"}

    asyncio.run(scenario())


def test_chat_app_persistent_pin_and_btw_are_ephemeral(tmp_path):
    import asyncio

    pytest.importorskip("textual")

    app_cls = tui._build_chat_app()
    app = app_cls(start_dir=str(tmp_path), theme="dark", dry_run=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#entry").value = "/cloud"  # persistent pin, no message
            await pilot.press("enter")
            await pilot.pause()
            assert app.state.pinned == "prefer-hosted" and not app.history

            app.query_one("#entry").value = "hello there"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.history) == 1 and app.state.pinned == "prefer-hosted"
            assert app.messages == [{"role": "user", "content": "hello there"}]

            app.query_one("#entry").value = "/btw quick aside"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.history) == 2  # the aside was still scored/shown…
            assert all("quick aside" not in m["content"] for m in app.messages)  # …but not kept

            app.query_one("#entry").value = "/auto"
            await pilot.press("enter")
            await pilot.pause()
            assert app.state.pinned is None

    asyncio.run(scenario())
