"""Wayfinder terminal chat — a Claude-Code-style TUI prototype (WF-DESIGN-0001).

A decision-first terminal chat: it scores each prompt with the deterministic core
(``score_complexity`` / ``explain_score``) and renders the routing decision inline —
``● LOCAL`` (green) vs ``◆ CLOUD`` (amber), the score, and the "why" — in the
Wayfinder palette pulled from ``demo.html``.

This is the first cut from WF-DESIGN-0001: **Rich-only**, decision-first, and keyless
(it routes and explains; it does not yet call a model — that is the thin-client step
over the gateway contract). The scoring stays in the pure, offline core (WF-ADR-0001);
this module is presentation only and never enters the scored path.

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


# --- mascot ------------------------------------------------------------------
# PLACEHOLDER mascot (a compass rose — Wayfinder = navigation). The real mascot
# art drops in here, and animation is a matter of adding frames: the renderer
# already cycles ``_MASCOT_FRAMES`` by index, so a frame loop is the only wiring
# left once the artwork/animation is finalized.
_MASCOT_FRAMES: tuple[tuple[str, ...], ...] = (
    (
        r" \ | / ",
        r"– ✦ –",
        r" / | \ ",
    ),
)
# ASCII fallback for terminals/locales that can't render the unicode mascot.
_MASCOT_ASCII: tuple[str, ...] = (
    r" \ | / ",
    r"-  *  -",
    r" / | \ ",
)


def mascot(frame: int = 0, *, ascii_only: bool = False) -> tuple[str, ...]:
    """One mascot frame. ``frame`` cycles ``_MASCOT_FRAMES`` (the animation seam)."""
    if ascii_only:
        return _MASCOT_ASCII
    return _MASCOT_FRAMES[frame % len(_MASCOT_FRAMES)]


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
    "commands:  /threshold <0..1>   set the local/cloud cut\n"
    "           /why on|off         show or hide the score breakdown\n"
    "           /help               this help\n"
    "           /quit               leave\n"
    "type anything else to route it."
)


# --- rich rendering (lazy import) --------------------------------------------
class TUIUnavailable(RuntimeError):
    """The terminal-UI extra (rich) is not installed."""


def _require_rich() -> None:
    try:
        import rich  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise TUIUnavailable(_INSTALL_HINT) from exc


def render_welcome(
    palette: dict[str, str], *, subtitle: str, frame: int = 0
) -> RenderableType:
    """The launch box: mascot + wordmark + active config (Claude-Code-style)."""
    from rich.panel import Panel
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    body = Text()
    for row in mascot(frame):
        body.append(f"{row}\n", style=accent)
    body.append("\n")
    body.append("Wayfinder", style=f"bold {accent}")
    body.append("  terminal chat\n", style=text_c)
    body.append(subtitle, style=muted)
    return Panel(body, border_style=accent, padding=(1, 3), expand=False)


def render_decision(
    decision: Decision, palette: dict[str, str], *, show_why: bool = True
) -> RenderableType:
    """The inline decision line (``● LOCAL`` / ``◆ CLOUD``) and optional "why" table."""
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    role_color = palette["accent"] if decision.is_local else palette["cloud"]
    glyph = "●" if decision.is_local else "◆"
    role = "LOCAL" if decision.is_local else "CLOUD"
    muted = palette["muted"]

    head = Text()
    head.append(f"{glyph} {role}", style=f"bold {role_color}")
    head.append(f"  {decision.model}", style=palette["text"])
    head.append(f"   score {decision.score:.2f}", style=muted)
    if decision.is_local:
        head.append("   · kept local", style=muted)

    parts: list[RenderableType] = [head]
    if show_why and decision.contributions:
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
        parts.append(table)
    parts.append(Text("routed, not answered (dry-run prototype)", style=muted))
    return Group(*parts)


def run_tui(
    *, start_dir: str = ".", theme: str = "auto", show_why: bool = True,
    threshold: float | None = None,
) -> None:
    """The interactive loop: read a line, route it, render the decision. Ctrl-C / /quit to exit."""
    _require_rich()
    from rich.console import Console

    console: Console = Console()
    palette = palette_for(theme)
    sub = "decision-first routing · /help for commands · ctrl-c to quit"
    console.print(render_welcome(palette, subtitle=sub))

    prompt = f"[{palette['accent']}]›[/] "
    while True:
        try:
            line = console.input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue
        cmd, arg = parse_command(line)
        if cmd is not None:
            if cmd in {"quit", "q", "exit"}:
                return
            if cmd == "help":
                console.print(_HELP, style=palette["muted"])
            elif cmd == "why":
                show_why = arg.strip().lower() != "off"
                console.print(f"why {'on' if show_why else 'off'}", style=palette["muted"])
            elif cmd == "threshold":
                try:
                    threshold = max(0.0, min(1.0, float(arg)))
                    console.print(f"threshold {threshold:.2f}", style=palette["accent"])
                except ValueError:
                    console.print("threshold must be a number 0..1", style=palette["warn"])
            else:
                console.print(f"unknown command /{cmd} — /help", style=palette["warn"])
            continue
        try:
            decision = decide(line, start_dir=start_dir, threshold=threshold)
        except WayfinderConfigError as exc:
            console.print(str(exc), style=palette["warn"])
            continue
        console.print(render_decision(decision, palette, show_why=show_why))
