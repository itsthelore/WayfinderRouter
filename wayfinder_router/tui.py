"""Wayfinder terminal chat — a Claude-Code-style TUI prototype (WF-DESIGN-0001).

A decision-first terminal chat: it scores each prompt with the deterministic core
(``score_complexity`` / ``explain_score``) and renders the routing decision inline —
``● LOCAL`` (green) vs ``◆ CLOUD`` (amber) and the score — in the Wayfinder palette
pulled from ``demo.html``. The "why" breakdown is collapsed by default and expanded
on demand (``/why``) so the transcript stays readable.

From WF-DESIGN-0001: **Rich-only**, decision-first. It routes and explains, and — when
``[gateway.models]`` are configured — calls the chosen model **in-process** (reusing the
gateway's relay, ``invoke_messages``) to return a real reply; with no models (or
``--dry-run``) it stays decision-only. Scoring stays in the pure, offline core
(WF-ADR-0001); this module is presentation + relay glue and never enters the scored path.

Rich is an opt-in extra (``pip install 'wayfinder-router[tui]'``); it is imported
lazily so the package still imports without it (mirrors the gateway's fastapi pattern).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .complexity import (
    DEFAULT_TIERS,
    FeatureContribution,
    RoutingConfig,
    binary_tiers,
    explain_score,
    score_complexity,
)
from .config import WayfinderConfigError, load_routing_config

if TYPE_CHECKING:  # type-only; the runtime imports rich lazily inside the renderers
    from rich.console import Console, RenderableType

_INSTALL_HINT = "the terminal UI needs its extra: pip install 'wayfinder-router[tui]'"
_SCOPES = ("turn", "last_user", "user", "all")

# --- brand palette (from wayfinder_router/demo.html) -------------------------
# Foreground roles only: a TUI inherits the terminal's background, so we paint
# accents and borders, never a full-screen fill. accent = local (green),
# cloud = hosted (amber); warn matches the demo's .warn.
THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "accent": "#19c8a4",
        "cloud": "#e0a25c",
        "text": "#ececec",
        "muted": "#9a9aa6",
        "line": "#39393d",
        "warn": "#d97706",
    },
    "light": {
        "accent": "#10a37f",
        "cloud": "#bd6a13",
        "text": "#0d0d0d",
        "muted": "#6b6b78",
        "line": "#e2e2e6",
        "warn": "#d97706",
    },
}


def palette_for(theme: str = "auto") -> dict[str, str]:
    """Resolve a palette. ``auto`` honours ``WAYFINDER_THEME`` then defaults to dark."""
    if theme == "auto":
        theme = os.environ.get("WAYFINDER_THEME", "dark").strip().lower()
    return THEMES.get(theme, THEMES["dark"])


# --- session state -----------------------------------------------------------
@dataclass
class TuiState:
    """The live settings the chat manages — surfaced by ``/settings``, set by commands."""

    threshold: float | None = None
    scope: str = "turn"
    sticky: bool = False
    cooldown: int = 0
    show_why: bool = False  # auto-expand the breakdown on every turn
    theme: str = "dark"


# --- the routing decision (reuses the deterministic core) --------------------
@dataclass
class Decision:
    """A scored turn: the recommendation plus the "why", for inline rendering."""

    text: str
    model: str
    score: float
    mode: str
    is_local: bool
    contributions: list[FeatureContribution] = field(default_factory=list)
    threshold: float | None = None


def decide(text: str, *, start_dir: str = ".", threshold: float | None = None) -> Decision:
    """Score ``text`` and classify the route — the same path as ``wayfinder-router route``.

    ``is_local`` is true when the recommendation falls in the lowest tier (the cheap,
    local arm); any escalation reads as cloud. Pure and offline (WF-ADR-0001).
    """
    config = load_routing_config(start_dir)
    if threshold is not None:
        config = RoutingConfig(
            weights=config.weights, tiers=binary_tiers(threshold), lexicon=config.lexicon
        )
    score = score_complexity(text, config=config)
    tiers = config.tiers or DEFAULT_TIERS
    idx = 0
    for i, tier in enumerate(tiers):
        if score.score >= tier.min_score:
            idx = i
    return Decision(
        text=text,
        model=score.recommendation,
        score=score.score,
        mode=score.mode,
        is_local=idx == 0,
        contributions=explain_score(score.features, config.weights),
        threshold=threshold,
    )


# --- slash commands ----------------------------------------------------------
def parse_command(line: str) -> tuple[str | None, str]:
    """Split a composer line. ``/cmd arg`` → ``("cmd", "arg")``; plain text → ``(None, text)``."""
    if not line.startswith("/"):
        return None, line
    parts = line[1:].strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


_HELP = (
    "commands\n"
    "  /threshold <0..1>              set the local/cloud cut\n"
    "  /scope turn|last_user|user|all what each turn scores\n"
    "  /sticky on|off [N]            keep hard chats on cloud (cooldown N)\n"
    "  /why [on|off|N]               expand the last (or Nth) decision; on/off auto-expands\n"
    "  /theme dark|light|auto        recolour\n"
    "  /settings                     show current settings\n"
    "  /help    /quit\n"
    "anything else is routed."
)


# --- rich rendering (lazy import) --------------------------------------------
class TUIUnavailable(RuntimeError):
    """The terminal-UI extra (rich) is not installed."""


def _require_rich() -> None:
    try:
        import rich  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise TUIUnavailable(_INSTALL_HINT) from exc


def render_welcome(palette: dict[str, str], *, subtitle: str) -> RenderableType:
    """The launch box: wordmark + brand taglines + a functional hint."""
    from rich.panel import Panel
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    words = Text()
    words.append("Wayfinder", style=f"bold {accent}")
    words.append("  terminal chat\n", style=text_c)
    words.append("Choose your path to your answers\n", style=text_c)
    words.append("Deterministic. Offline. No model call to decide.\n\n", style=muted)
    words.append(subtitle, style=muted)
    return Panel(words, border_style=accent, padding=(1, 3), expand=False)


def render_decision(
    decision: Decision, palette: dict[str, str], *, expanded: bool = False
) -> RenderableType:
    """The decision line; collapsed shows a ``/why`` affordance, expanded adds the table."""
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    role_color = palette["accent"] if decision.is_local else palette["cloud"]
    glyph = "●" if decision.is_local else "◆"
    role = "LOCAL" if decision.is_local else "CLOUD"
    muted, text_c = palette["muted"], palette["text"]

    head = Text()
    head.append(f"{glyph} {role}", style=f"bold {role_color}")
    head.append(f"  {decision.model}", style=text_c)
    head.append(f"   score {decision.score:.2f}", style=muted)
    if decision.is_local:
        head.append("  · kept local", style=muted)
    if decision.contributions:
        head.append("   /why " + ("⌃" if expanded else "⌄"), style=muted)

    if not (expanded and decision.contributions):
        return head

    table = Table.grid(padding=(0, 2))
    table.add_column(style=muted)
    table.add_column(justify="right", style=muted)
    table.add_column(justify="right", style=muted)
    for fc in sorted(decision.contributions, key=lambda c: -c.contribution)[:5]:
        table.add_row(
            fc.name,
            f"{fc.value}",
            f"{fc.normalized:.2f}×{fc.weight:g} = {fc.contribution:.3f}",
        )
    return Group(head, table)


def render_settings(state: TuiState, palette: dict[str, str]) -> RenderableType:
    """A settings panel: the live routing controls and how to change them."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    rows = [
        ("threshold", f"{state.threshold:.2f}" if state.threshold is not None else "auto (config)"),
        ("routing scope", state.scope),
        ("sticky", f"on · cooldown {state.cooldown}" if state.sticky else "off"),
        ("why breakdown", "expanded" if state.show_why else "collapsed"),
        ("theme", state.theme),
    ]
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=muted, justify="right")
    grid.add_column(style=text_c)
    for key, val in rows:
        grid.add_row(key, val)

    hint = Text(
        "\nchange:  /threshold  /scope  /sticky  /why  /theme   ·   /help for syntax",
        style=muted,
    )
    return Panel(
        Group(grid, hint),
        title="settings",
        title_align="left",
        border_style=accent,
        padding=(1, 2),
        expand=False,
    )


def model_reply(
    models: dict, decision: Decision, messages: list[dict], *, timeout: float = 60.0
) -> str | None:
    """Call the upstream the decision points at; return its reply, or None if no model maps.

    In-process reuse of the gateway's relay (``invoke_messages``) — the same forward path
    the server uses, without spawning one (WF-DESIGN-0001).
    """
    from .gateway import invoke_messages

    model = models.get(decision.model)
    if model is None:
        return None
    return invoke_messages(model, messages, timeout=timeout)


def render_reply(text: str) -> RenderableType:
    """Render a model reply as Markdown (code blocks and lists render nicely)."""
    from rich.markdown import Markdown

    return Markdown(text)


def _reply_timeout() -> float:
    raw = os.environ.get("WAYFINDER_ROUTER_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


def run_tui(
    *, start_dir: str = ".", theme: str = "auto", show_why: bool = False,
    threshold: float | None = None, dry_run: bool = False,
) -> None:
    """Interactive loop: route each line, render the decision, and — when models are
    configured — the model's reply. Ctrl-C / /quit to exit."""
    _require_rich()
    from rich.console import Console

    from .gateway import GatewayUnavailable, UpstreamError

    console: Console = Console()
    state = TuiState(threshold=threshold, show_why=show_why, theme=theme)
    palette = palette_for(state.theme)
    history: list[Decision] = []
    messages: list[dict] = []

    models: dict = {}
    if not dry_run:
        try:
            from .gateway import load_gateway_config

            models = dict(load_gateway_config(start_dir).models)
        except WayfinderConfigError as exc:
            console.print(str(exc), style=palette["warn"])
    live = bool(models)
    timeout = _reply_timeout()

    console.print(
        render_welcome(palette, subtitle="decision-first routing · /help · /settings · ctrl-c to quit")
    )
    if live:
        console.print(
            f"connected · routing between {', '.join(sorted(models))}", style=palette["muted"]
        )
    else:
        console.print(
            "preview · routing decisions only — add [gateway.models] (and drop --dry-run) for replies",
            style=palette["muted"],
        )

    while True:
        try:
            line = console.input(f"[{palette['accent']}]›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue

        cmd, arg = parse_command(line)
        if cmd is None:
            messages.append({"role": "user", "content": line})
            try:
                decision = decide(line, start_dir=start_dir, threshold=state.threshold)
            except WayfinderConfigError as exc:
                console.print(str(exc), style=palette["warn"])
                messages.pop()
                continue
            history.append(decision)
            console.print(render_decision(decision, palette, expanded=state.show_why))
            if live:
                try:
                    reply = model_reply(models, decision, messages, timeout=timeout)
                except (GatewayUnavailable, UpstreamError, RuntimeError) as exc:
                    console.print(f"upstream error: {exc}", style=palette["warn"])
                    reply = None
                if reply is not None:
                    messages.append({"role": "assistant", "content": reply})
                    console.print(render_reply(reply))
                elif decision.model not in models:
                    console.print(
                        f"no model configured for '{decision.model}'", style=palette["muted"]
                    )
            continue

        if cmd in {"quit", "q", "exit"}:
            return
        elif cmd == "help":
            console.print(_HELP, style=palette["muted"])
        elif cmd == "settings":
            console.print(render_settings(state, palette))
        elif cmd == "threshold":
            try:
                state.threshold = max(0.0, min(1.0, float(arg)))
                console.print(f"threshold {state.threshold:.2f}", style=palette["accent"])
            except ValueError:
                console.print("threshold must be a number 0..1", style=palette["warn"])
        elif cmd == "scope":
            if arg in _SCOPES:
                state.scope = arg
                console.print(f"scope {arg}", style=palette["accent"])
            else:
                console.print("scope must be turn|last_user|user|all", style=palette["warn"])
        elif cmd == "sticky":
            parts = arg.split()
            if parts and parts[0] in {"on", "off"}:
                state.sticky = parts[0] == "on"
                if len(parts) > 1 and parts[1].isdigit():
                    state.cooldown = int(parts[1])
                tail = f" · cooldown {state.cooldown}" if state.sticky else ""
                console.print(f"sticky {'on' if state.sticky else 'off'}{tail}", style=palette["accent"])
            else:
                console.print("sticky on|off [N]", style=palette["warn"])
        elif cmd == "theme":
            if arg in {"dark", "light", "auto"}:
                state.theme = arg
                palette = palette_for(arg)
                console.print(f"theme {arg}", style=palette["accent"])
            else:
                console.print("theme dark|light|auto", style=palette["warn"])
        elif cmd == "why":
            w = arg.strip().lower()
            if w == "on":
                state.show_why = True
                console.print("why: auto-expand on", style=palette["muted"])
            elif w == "off":
                state.show_why = False
                console.print("why: collapsed", style=palette["muted"])
            elif w.isdigit() and 1 <= int(w) <= len(history):
                console.print(render_decision(history[int(w) - 1], palette, expanded=True))
            elif not w and history:
                console.print(render_decision(history[-1], palette, expanded=True))
            elif not w:
                console.print("nothing to expand yet", style=palette["muted"])
            else:
                console.print("why [on|off|N]", style=palette["warn"])
        else:
            console.print(f"unknown command /{cmd} — /help", style=palette["warn"])
