"""Wayfinder terminal chat — a full-screen Textual app (WF-DESIGN-0001).

A decision-first terminal chat: it scores each prompt with the deterministic core
(``score_complexity`` / ``explain_score``) and renders the routing decision inline —
``● LOCAL`` (green) vs ``◆ CLOUD`` (amber) and the score — in the Wayfinder palette
pulled from ``demo.html``. The "why" breakdown is collapsed by default and expanded
on demand (``/why``) so the transcript stays readable.

The chrome is a fixed full-screen layout: a scrolling transcript, a one-line status
bar, a pinned input box (bordered in the brand accent), and a footer — the wordmark
heads the transcript and scrolls away as the conversation grows. When
``[gateway.models]`` are configured it calls the chosen model **in-process** (reusing
the gateway's relay, ``stream_messages`` / ``invoke_messages``) to return a real reply,
streamed token-by-token; with no models (or ``--dry-run``) it stays decision-only.
Scoring stays in the pure, offline core (WF-ADR-0001); this module is presentation +
relay glue and never enters the scored path.

rich + textual ship in the default install (WF-ADR-0029), but both are imported
**lazily** so ``import wayfinder_router`` (the scorer/library) still loads nothing extra
— embedding stays light, mirroring the gateway's fastapi pattern. The Textual ``App``
is built behind a factory so importing this module never requires textual.
"""

from __future__ import annotations

import os
import threading
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

if TYPE_CHECKING:  # type-only; the runtime imports rich/textual/gateway lazily
    from rich.console import RenderableType

    from .gateway import GatewayModel

_INSTALL_HINT = "the terminal UI needs its extra: pip install 'wayfinder-router[tui]'"
_SCOPES = ("turn", "last_user", "user", "all")

# Slash commands offered as inline autocomplete in the composer (typing `/` suggests).
_SLASH_COMMANDS = [
    "/init", "/models", "/cost", "/new", "/threads", "/open", "/route", "/auto", "/local",
    "/cloud", "/btw", "/threshold", "/scope", "/sticky", "/why", "/stream", "/theme",
    "/settings", "/help", "/quit",
]

# The wordmark that heads the transcript (pyfiglet "ansi_shadow", baked so figlet
# is never a runtime dependency). It spells WAYFINDER in box-drawing blocks.
_WORDMARK = (
    "██╗    ██╗ █████╗ ██╗   ██╗███████╗██╗███╗   ██╗██████╗ ███████╗██████╗ \n"
    "██║    ██║██╔══██╗╚██╗ ██╔╝██╔════╝██║████╗  ██║██╔══██╗██╔════╝██╔══██╗\n"
    "██║ █╗ ██║███████║ ╚████╔╝ █████╗  ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝\n"
    "██║███╗██║██╔══██║  ╚██╔╝  ██╔══╝  ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗\n"
    "╚███╔███╔╝██║  ██║   ██║   ██║     ██║██║ ╚████║██████╔╝███████╗██║  ██║\n"
    " ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝"
)

# --- brand palette (from wayfinder_router/demo.html) -------------------------
# accent = local (green), cloud = hosted (amber); warn matches the demo's .warn.
# bg is the full-screen fill (the app takes over the terminal, so it owns one).
THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "accent": "#19c8a4",
        "cloud": "#e0a25c",
        "text": "#ececec",
        "muted": "#9a9aa6",
        "line": "#39393d",
        "warn": "#d97706",
        "bg": "#161618",
    },
    "light": {
        "accent": "#10a37f",
        "cloud": "#bd6a13",
        "text": "#0d0d0d",
        "muted": "#6b6b78",
        "line": "#e2e2e6",
        "warn": "#d97706",
        "bg": "#ffffff",
    },
}


def palette_for(theme: str = "auto") -> dict[str, str]:
    """Resolve a palette. ``auto`` honours ``WAYFINDER_THEME`` then defaults to dark."""
    if theme == "auto":
        theme = os.environ.get("WAYFINDER_THEME", "dark").strip().lower()
    return THEMES.get(theme, THEMES["dark"])


def _resolve_theme(theme: str) -> str:
    """Map a CLI theme name (incl. ``auto``) to a concrete palette key."""
    if theme == "auto":
        theme = os.environ.get("WAYFINDER_THEME", "dark").strip().lower()
    return theme if theme in THEMES else "dark"


def _version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:  # pragma: no cover - defensive
        return ""


# --- session state -----------------------------------------------------------
@dataclass
class TuiState:
    """The live settings the chat manages — surfaced by ``/settings``, set by commands."""

    threshold: float | None = None
    scope: str = "turn"
    sticky: bool = False
    cooldown: int = 0
    show_why: bool = False  # auto-expand the breakdown on every turn
    stream: bool = True  # stream replies token-by-token (in-process backend)
    theme: str = "dark"
    # A standing route override: a configured model name, or the sentinels
    # "prefer-local" / "prefer-hosted" (cheapest / most-capable tier), or None for
    # normal routing. One-shot forces (/local <msg>, /cloud <msg>, /btw) bypass this.
    pinned: str | None = None


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
    # The configured model names in tier order (cheapest → most capable); used to
    # resolve forced routes (prefer-local / prefer-hosted) against the same tiers.
    targets: list[str] = field(default_factory=list)


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
    tiers = sorted(config.tiers or DEFAULT_TIERS, key=lambda t: t.min_score)
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
        targets=[tier.model for tier in tiers],
    )


def resolve_target(pin: str | None, decision: Decision) -> tuple[str, bool]:
    """Resolve a forced route to ``(model_name, is_local)`` against the decision's tiers.

    ``pin`` is a model name, the sentinel ``prefer-local`` / ``prefer-hosted`` (cheapest /
    most-capable tier), or ``None`` for the natural route. Mirrors the gateway's
    ``resolve_pin`` so in-process and ``--base-url`` agree on what a force means.
    """
    if pin is None:
        return decision.model, decision.is_local
    targets = decision.targets or [decision.model]
    if pin == "prefer-local":
        name = targets[0]
    elif pin == "prefer-hosted":
        name = targets[-1]
    else:
        name = pin
    return name, name == targets[0]


def _pin_label(pin: str | None) -> str:
    """A short human label for a pin (sentinels read as local/cloud)."""
    return {"prefer-local": "local", "prefer-hosted": "cloud", None: "auto"}.get(pin, pin or "auto")


# --- session cost accounting -------------------------------------------------
@dataclass
class SessionCost:
    """A running tally of model calls and their estimated cost vs always-cloud."""

    calls: int = 0
    local: int = 0
    spent: float = 0.0
    saved: float = 0.0
    priced: bool = False  # a turn had cost_per_1k for both the chosen and cloud arms


def estimate_tokens(text: str) -> int:
    """A rough token count (~4 chars/token); everything derived from it is labelled ``~``."""
    return max(1, len(text) // 4)


def account_turn(
    tally: SessionCost, *, is_local: bool, tokens: int,
    chosen_cost: float | None, cloud_cost: float | None,
) -> None:
    """Fold one model call into ``tally`` — spend, and savings vs routing it all to cloud."""
    tally.calls += 1
    if is_local:
        tally.local += 1
    if chosen_cost is not None and cloud_cost is not None:
        tally.priced = True
        units = tokens / 1000.0
        tally.spent += chosen_cost * units
        tally.saved += max(0.0, (cloud_cost - chosen_cost) * units)


def cost_summary(tally: SessionCost) -> str:
    """The footer tally line, or ``""`` before any model call this session."""
    if tally.calls == 0:
        return ""
    summary = f"{tally.local}/{tally.calls} local"
    if tally.priced:
        summary += f" · ~${tally.saved:.4f} saved"
    return summary


def render_cost(tally: SessionCost, palette: dict[str, str]) -> RenderableType:
    """A panel breaking down the session's routing mix and estimated savings."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    if tally.calls == 0:
        return Panel(Text("no model calls yet this session", style=muted), title="cost",
                     title_align="left", border_style=accent, padding=(1, 2), expand=False)
    pct = round(100 * tally.local / tally.calls)
    rows = [("model calls", str(tally.calls)), ("kept local", f"{tally.local}  ({pct}%)")]
    if tally.priced:
        rows += [
            ("est. spent", f"~${tally.spent:.4f}"),
            ("est. saved", f"~${tally.saved:.4f}  vs always-cloud"),
        ]
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=muted, justify="right")
    grid.add_column(style=text_c)
    for key, val in rows:
        grid.add_row(key, val)
    tail = "estimated from ~4 chars/token"
    if not tally.priced:
        tail += " · set cost_per_1k on your models for $ figures"
    return Panel(Group(grid, Text("\n" + tail, style=muted)), title="cost", title_align="left",
                 border_style=accent, padding=(1, 2), expand=False)


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
    "  /init [hybrid|openai]         scaffold a wayfinder-router.toml and load its models\n"
    "  /models                       show configured models and whether each key is set\n"
    "  /cost                         session routing mix and estimated savings vs cloud\n"
    "  /new                          start a fresh conversation (the current one is saved)\n"
    "  /threads      /open <n>       list saved conversations · reopen one\n"
    "  /route <model>|auto           pin every turn to a model (the router still shows why)\n"
    "  /local        /cloud          pin to the cheapest / most-capable tier; /auto clears\n"
    "  /local <msg>  /cloud <msg>    force just this turn (kept in the thread)\n"
    "  /btw <question>               quick one-off aside → local, not added to the thread\n"
    "  /threshold <0..1>              set the local/cloud cut\n"
    "  /scope turn|last_user|user|all what each turn scores\n"
    "  /sticky on|off [N]            keep hard chats on cloud (cooldown N)\n"
    "  /why [on|off|N]               expand the last (or Nth) decision; on/off auto-expands\n"
    "  /stream on|off                stream replies token-by-token\n"
    "  /theme dark|light|auto        recolour\n"
    "  /settings                     show current settings\n"
    "  /help    /quit\n"
    "anything else is routed."
)


# --- rich rendering (lazy import) --------------------------------------------
class TUIUnavailable(RuntimeError):
    """The terminal-UI extra (rich + textual) is not installed."""


def _require_tui() -> None:
    try:
        import rich  # noqa: F401
        import textual  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise TUIUnavailable(_INSTALL_HINT) from exc


def render_welcome(
    palette: dict[str, str], *, subtitle: str, compact: bool = False
) -> RenderableType:
    """The transcript header: the wordmark, brand subtitle, and a functional hint.

    ``compact`` swaps the block wordmark for a plain "Wayfinder" line on narrow terminals.
    """
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    accent, muted, text_c, cloud = (
        palette["accent"], palette["muted"], palette["text"], palette["cloud"]
    )
    if compact:
        word: RenderableType = Text("Wayfinder", style=f"bold {accent}", justify="center")
    else:
        word = Text(_WORDMARK, style=f"bold {accent}")

    cap = Text(justify="center")
    cap.append("local ", style=muted)
    cap.append("✓   ", style=accent)
    cap.append("cloud ", style=muted)
    cap.append("✓   ", style=cloud)
    cap.append("offline routing ", style=muted)
    cap.append("✓", style=accent)

    return Group(
        Text(),
        Align.center(word),
        Align.center(Text(subtitle, style=muted)),
        Text(),
        Align.center(
            Text("type a prompt — Wayfinder routes it and shows the score + why", style=text_c)
        ),
        Text(),
        Align.center(cap),
        Text(),
    )


def _glyph_role(is_local: bool) -> tuple[str, str]:
    return ("●", "LOCAL") if is_local else ("◆", "CLOUD")


def render_decision(
    decision: Decision,
    palette: dict[str, str],
    *,
    expanded: bool = False,
    forced_to: tuple[str, bool] | None = None,
) -> RenderableType:
    """The decision line; collapsed shows a ``/why`` affordance, expanded adds the table.

    ``forced_to`` is ``(model_name, is_local)`` when the route was overridden — the
    forced target is shown as the primary, flagged ``· forced``, with the natural route
    the scorer would have picked shown alongside (decision-first transparency).
    """
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    muted, text_c = palette["muted"], palette["text"]
    if forced_to is not None:
        f_name, f_local = forced_to
        f_glyph, f_role = _glyph_role(f_local)
        head = Text()
        head.append(f"{f_glyph} {f_role}", style=f"bold {palette['accent'] if f_local else palette['cloud']}")
        head.append(f"  {f_name}", style=text_c)
        head.append("  · forced", style=palette["warn"])
        head.append(f"   score {decision.score:.2f}", style=muted)
        if f_name != decision.model:
            n_glyph, n_role = _glyph_role(decision.is_local)
            head.append(f"   would route {n_glyph} {n_role}", style=muted)
        if decision.contributions:
            head.append("   /why " + ("⌃" if expanded else "⌄"), style=muted)
    else:
        glyph, role = _glyph_role(decision.is_local)
        role_color = palette["accent"] if decision.is_local else palette["cloud"]
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
        ("forced route", _pin_label(state.pinned) if state.pinned else "auto (routing)"),
        ("threshold", f"{state.threshold:.2f}" if state.threshold is not None else "auto (config)"),
        ("routing scope", state.scope),
        ("sticky", f"on · cooldown {state.cooldown}" if state.sticky else "off"),
        ("why breakdown", "expanded" if state.show_why else "collapsed"),
        ("streaming", "on" if state.stream else "off"),
        ("theme", state.theme),
    ]
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=muted, justify="right")
    grid.add_column(style=text_c)
    for key, val in rows:
        grid.add_row(key, val)

    hint = Text(
        "\nchange:  /route  /local  /cloud  /threshold  /scope  /sticky  /why  /stream  /theme   ·   /help",
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


def render_models(models: dict, palette: dict[str, str]) -> RenderableType:
    """A panel of the configured models and whether each one's key resolves.

    The in-chat equivalent of ``wayfinder-router doctor`` — keys are read from the
    environment, never stored (WF-ADR-0004); this only reports ``set`` / ``not set``.
    """
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .bootstrap import key_status

    accent, muted, text_c, cloud, warn = (
        palette["accent"], palette["muted"], palette["text"], palette["cloud"], palette["warn"]
    )
    if not models:
        body: RenderableType = Text(
            "no models configured — type /init to scaffold one", style=muted
        )
        return Panel(body, title="models", title_align="left", border_style=accent,
                     padding=(1, 2), expand=False)

    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=text_c)  # name
    grid.add_column(style=muted)  # model id
    grid.add_column(style=muted)  # base url
    grid.add_column()  # key status
    for status in key_status(models):
        if status.env_var is None:
            key = Text("keyless ✓", style=accent)
        elif status.ok:
            key = Text(f"{status.env_var} ✓ set", style=accent)
        else:
            key = Text(f"{status.env_var} ✗ not set", style=warn)
        glyph = Text("● ", style=accent if status.ok else cloud)
        grid.add_row(Text(status.name, style=text_c), status.model, status.base_url, glyph + key)

    hint = Text("\nkeys live in your environment · /init to add models · /route to pin", style=muted)
    return Panel(Group(grid, hint), title="models", title_align="left", border_style=accent,
                 padding=(1, 2), expand=False)


def render_empty_state(palette: dict[str, str]) -> RenderableType:
    """The onboarding panel shown when no models are configured (in-process, no --dry-run)."""
    from rich.panel import Panel
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    body = Text()
    body.append("You're in preview — routing decisions only, no replies yet.\n\n", style=text_c)
    body.append("Add models without leaving the chat:\n", style=muted)
    body.append("  /init", style=accent)
    body.append("          scaffold the hybrid preset (keyless local Ollama → Anthropic cloud)\n",
                style=muted)
    body.append("  /init openai", style=accent)
    body.append("   two OpenAI tiers (gpt-4o-mini → gpt-4o)\n", style=muted)
    body.append("  /models", style=accent)
    body.append("        check which model keys are set\n\n", style=muted)
    body.append("Keyless local replies work as soon as Ollama is running ", style=muted)
    body.append("(ollama serve)", style=text_c)
    body.append(".", style=muted)
    return Panel(body, title="get started", title_align="left", border_style=accent,
                 padding=(1, 2), expand=False)


def render_threads(entries: list, palette: dict[str, str]) -> RenderableType:
    """A numbered list of saved conversations (newest first); `/open <n>` reopens one."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    accent, muted, text_c = palette["accent"], palette["muted"], palette["text"]
    if not entries:
        body: RenderableType = Text(
            "no saved conversations yet — they save automatically as you chat", style=muted
        )
        return Panel(body, title="threads", title_align="left", border_style=accent,
                     padding=(1, 2), expand=False)

    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=accent, justify="right")  # index
    grid.add_column(style=text_c)  # title
    grid.add_column(style=muted)  # updated
    for i, thread in enumerate(entries, start=1):
        when = (thread.updated or thread.created or "").replace("T", " ").rstrip("Z")
        grid.add_row(str(i), thread.title or "(untitled)", when)
    hint = Text("\n/open <n> to reopen · /new to start fresh", style=muted)
    return Panel(Group(grid, hint), title="threads", title_align="left", border_style=accent,
                 padding=(1, 2), expand=False)


def _status_bar(
    state: TuiState, palette: dict[str, str], *, note: str | None = None
) -> RenderableType:
    """The one-line status bar: routing mode + thresholds (or a transient note)."""
    from rich.table import Table
    from rich.text import Text

    accent, muted, cloud = palette["accent"], palette["muted"], palette["cloud"]
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    left = Text()
    if note:
        left.append("⠿ ", style=cloud)
        left.append(note, style=muted)
    elif state.pinned:
        left.append(f"forced → {_pin_label(state.pinned)}", style=palette["warn"])
        left.append("  ·  /auto to resume routing", style=muted)
    else:
        left.append("decision-first routing", style=accent)
        thr = f"{state.threshold:.2f}" if state.threshold is not None else "auto"
        left.append(f"  ·  threshold {thr}  ·  scope {state.scope}", style=muted)
    right = Text()
    right.append("● local", style=accent)
    right.append("  /  ", style=muted)
    right.append("◆ cloud", style=cloud)
    grid.add_row(left, right)
    return grid


def _footer_bar(palette: dict[str, str], *, right: str = "no model call to decide") -> RenderableType:
    """The footer hint line."""
    from rich.table import Table
    from rich.text import Text

    muted = palette["muted"]
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(
        Text("/help   ·   ↑↓ history   ·   ctrl-c cancel / quit", style=muted),
        Text(right, style=muted),
    )
    return grid


def model_reply(
    models: dict, decision: Decision, messages: list[dict], *, timeout: float = 60.0
) -> str | None:
    """Call the upstream the decision points at; return its reply, or None if no model maps.

    In-process reuse of the gateway's relay (``invoke_messages``) — the same forward path
    the server uses, without spawning one (WF-DESIGN-0001). The streaming loop uses
    ``stream_messages`` directly; this is the non-streaming convenience.
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


def decision_from_debug(payload: dict, *, text: str = "") -> Decision:
    """Build a :class:`Decision` from a gateway ``X-Wayfinder-Debug`` ``wayfinder`` payload.

    Lets the ``--base-url`` thin client render the same decision-first line and "why"
    breakdown the in-process backend shows, from the remote gateway's response.
    """
    tiers = sorted(payload.get("tiers") or [], key=lambda t: float(t.get("min_score", 0.0)))
    score = float(payload.get("score", 0.0))
    # The natural route the scorer would pick (highest tier whose cut the score clears).
    # When the gateway pinned the call, payload["model"] is the forced target, not this —
    # so derive the decision-first view from score + tiers, the same as the local path.
    nat_idx = 0
    for i, tier in enumerate(tiers):
        if score >= float(tier.get("min_score", 0.0)):
            nat_idx = i
    model = str(tiers[nat_idx]["model"]) if tiers else str(payload.get("model", "?"))
    contributions = [
        FeatureContribution(
            name=str(c["name"]),
            value=int(c["value"]),
            normalized=float(c["normalized"]),
            weight=float(c["weight"]),
            contribution=float(c["contribution"]),
        )
        for c in payload.get("contributions", [])
    ]
    return Decision(
        text=text,
        model=model,
        score=score,
        mode=str(payload.get("mode", "")),
        is_local=bool(tiers) and nat_idx == 0,
        contributions=contributions,
        targets=[str(tier["model"]) for tier in tiers],
    )


def remote_reply(
    base_url: str, messages: list[dict], *, model: str = "auto",
    threshold: float | None = None, timeout: float = 60.0,
) -> tuple[Decision | None, str | None]:
    """POST to a running gateway's ``/v1/chat/completions``; return ``(decision, reply)``.

    The thin-client backend (WF-DESIGN-0001): the *remote* gateway makes the routing
    decision (surfaced via ``X-Wayfinder-Debug``) and the reply. Non-streaming. ``model``
    is the OpenAI ``model`` field — ``"auto"`` routes, a concrete name or
    ``prefer-local`` / ``prefer-hosted`` forces the call server-side (``resolve_pin``).
    """
    from .gateway import GatewayUnavailable

    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise GatewayUnavailable(
            "the --base-url client needs httpx: pip install 'wayfinder-router[gateway]'"
        ) from exc
    headers = {"X-Wayfinder-Debug": "1"}
    if threshold is not None:
        headers["X-Wayfinder-Threshold"] = f"{threshold}"
    body = {"model": model, "messages": list(messages)}
    url = base_url.rstrip("/") + "/v1/chat/completions"
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RuntimeError(str(exc) or exc.__class__.__name__) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"gateway returned non-JSON ({response.status_code})") from exc
    wf = data.get("wayfinder") if isinstance(data, dict) else None
    decision = decision_from_debug(wf) if isinstance(wf, dict) else None
    reply: str | None = None
    try:
        reply = str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        reply = None
    return decision, reply


def _reply_timeout() -> float:
    raw = os.environ.get("WAYFINDER_ROUTER_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


# --- the full-screen Textual app ---------------------------------------------
# Built behind a factory so importing this module never requires textual; the class
# closes over the lazily-imported textual/rich names.
def _build_chat_app() -> type:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.suggester import SuggestFromList
    from textual.widgets import Input, Static
    from rich.text import Text

    from . import threads

    class WayfinderChat(App):
        """Decision-first terminal chat: route every prompt, stream the chosen model."""

        TITLE = "wayfinder"

        CSS = """
        Screen { layers: base; }
        #transcript { height: 1fr; padding: 1 2; scrollbar-size-vertical: 1; }
        #status { height: 1; padding: 0 2; }
        #composer { height: 3; margin: 0 2; border: round #19c8a4; padding: 0 1; }
        #prompt { width: 2; }
        #entry { border: none; padding: 0; height: 1; background: transparent; }
        #footer { height: 1; padding: 0 2; }
        """

        BINDINGS = [
            Binding("ctrl+c", "interrupt", "cancel / quit", priority=True),
            Binding("ctrl+d", "quit", "quit", priority=True),
            Binding("up", "history_prev", "prev", show=False),
            Binding("down", "history_next", "next", show=False),
        ]

        def __init__(
            self,
            *,
            start_dir: str = ".",
            theme: str = "auto",
            show_why: bool = False,
            threshold: float | None = None,
            dry_run: bool = False,
            stream: bool = True,
            base_url: str | None = None,
        ) -> None:
            super().__init__()
            self.start_dir = start_dir
            self.base_url = base_url
            self.dry_run = dry_run
            resolved = _resolve_theme(theme)
            self.state = TuiState(
                threshold=threshold, show_why=show_why, stream=stream, theme=resolved
            )
            self.palette = palette_for(resolved)
            self.messages: list[dict] = []
            self.history: list[Decision] = []
            self.timeout = _reply_timeout()
            self.models: dict = {}
            self._config_warning: str | None = None
            self._busy = False  # a reply worker is in flight
            self._cancel = threading.Event()  # cooperative cancel for the streaming worker
            self._input_history: list[str] = []  # submitted lines, for ↑/↓ recall
            self._hist_index: int | None = None
            self._data_dir = threads.threads_dir()  # where conversations persist (WF-ADR-0030)
            self._thread = threads.new_thread()
            self._thread_list: list = []  # last `/threads` listing, indexed by `/open`
            self._cost = SessionCost()  # session routing mix + estimated savings
            if base_url is None and not dry_run:
                try:
                    from .gateway import load_gateway_config

                    self.models = dict(load_gateway_config(start_dir).models)
                except WayfinderConfigError as exc:
                    self._config_warning = str(exc)

        # --- layout ---
        def compose(self) -> ComposeResult:
            yield VerticalScroll(id="transcript")
            yield Static(id="status")
            with Horizontal(id="composer"):
                yield Static("›", id="prompt")
                yield Input(
                    placeholder="Send a message — Wayfinder routes it…",
                    id="entry",
                    suggester=SuggestFromList(_SLASH_COMMANDS, case_sensitive=False),
                )
            yield Static(id="footer")

        def on_mount(self) -> None:
            self._body = self.query_one("#transcript", VerticalScroll)
            self._apply_palette()
            compact = self.size.width < 78
            subtitle = f"v{_version()}  ·  deterministic LLM routing — local vs cloud"
            self._append(render_welcome(self.palette, subtitle=subtitle, compact=compact))
            if self._config_warning:
                self._warn(self._config_warning)
            if self.base_url is not None:
                self._note(f"connected · remote gateway {self.base_url}")
            elif self.models:
                self._note(f"connected · routing between {', '.join(sorted(self.models))}")
            elif self.dry_run:
                self._note("preview · --dry-run: routing decisions only, no model calls")
            else:
                self._append(render_empty_state(self.palette))
            self.query_one("#entry", Input).focus()

        # --- palette / chrome ---
        def _apply_palette(self) -> None:
            p = self.palette
            self.theme = "textual-light" if self.state.theme == "light" else "textual-dark"
            self.screen.styles.background = p["bg"]
            self.screen.styles.color = p["text"]
            self._body.styles.background = p["bg"]
            self.query_one("#composer", Horizontal).styles.border = ("round", p["accent"])
            prompt = self.query_one("#prompt", Static)
            prompt.update(Text("›", style=p["accent"]))
            prompt.styles.color = p["accent"]
            entry = self.query_one("#entry", Input)
            entry.styles.color = p["text"]
            self._refresh_bars()

        def _refresh_bars(self, *, note: str | None = None) -> None:
            self.query_one("#status", Static).update(_status_bar(self.state, self.palette, note=note))
            if note:
                right = "routing…"
            else:
                right = cost_summary(self._cost) or "no model call to decide"
            self.query_one("#footer", Static).update(_footer_bar(self.palette, right=right))

        def _account(self, is_local: bool, tokens: int,
                     chosen_cost: float | None, cloud_cost: float | None) -> None:
            account_turn(self._cost, is_local=is_local, tokens=tokens,
                         chosen_cost=chosen_cost, cloud_cost=cloud_cost)
            self._refresh_bars()

        # --- transcript helpers (main thread) ---
        def _append(self, renderable: RenderableType) -> Static:
            widget = Static(renderable)
            self._body.mount(widget)
            self._body.scroll_end(animate=False)
            return widget

        def _note(self, message: str) -> Static:
            return self._append(Text(message, style=self.palette["muted"]))

        def _warn(self, message: str) -> Static:
            return self._append(Text(message, style=self.palette["warn"]))

        def _user_line(self, line: str, *, aside: bool = False) -> RenderableType:
            text = Text()
            if aside:  # a /btw sidebar: dimmed, clearly not part of the thread
                text.append("↪ btw  ", style=self.palette["muted"])
                text.append(line, style=self.palette["muted"])
            else:
                text.append("› ", style=self.palette["accent"])
                text.append(line, style=self.palette["text"])
            return text

        def _set_live_text(self, widget: Static, body: str) -> None:
            widget.update(Text(body + "▏", style=self.palette["text"]))
            self._body.scroll_end(animate=False)

        def _finalize_reply(self, widget: Static, full: str) -> None:
            widget.update(
                render_reply(full) if full else Text("(empty reply)", style=self.palette["muted"])
            )
            self._body.scroll_end(animate=False)

        def _finalize_error(self, widget: Static, message: str) -> None:
            widget.update(Text(f"upstream error: {message}", style=self.palette["warn"]))
            self._body.scroll_end(animate=False)

        def _set_note(self, note: str | None) -> None:
            self._refresh_bars(note=note)

        def _set_busy(self, busy: bool) -> None:
            self._busy = busy
            if busy:
                self._cancel.clear()
            entry = self.query_one("#entry", Input)
            entry.disabled = busy
            if not busy:
                entry.focus()

        def _finalize_cancelled(self, widget: Static, full: str) -> None:
            text = Text()
            if full:
                text.append(full + "  ", style=self.palette["text"])
            text.append("⨯ cancelled", style=self.palette["warn"])
            widget.update(text)
            self._body.scroll_end(animate=False)

        # --- key actions ---
        def action_interrupt(self) -> None:
            """Ctrl+C: cancel an in-flight reply if one is running, else quit."""
            if self._busy:
                self._cancel.set()
                self._set_note("cancelling…")
                return
            self.exit()

        def action_history_prev(self) -> None:
            self._recall(-1)

        def action_history_next(self) -> None:
            self._recall(+1)

        def _recall(self, direction: int) -> None:
            entry = self.query_one("#entry", Input)
            if entry.disabled or not self._input_history:
                return
            if self._hist_index is None:
                if direction > 0:
                    return  # already at the live (empty) line
                self._hist_index = len(self._input_history)
            self._hist_index += direction
            if self._hist_index >= len(self._input_history):
                self._hist_index = None
                entry.value = ""
                return
            self._hist_index = max(0, self._hist_index)
            entry.value = self._input_history[self._hist_index]
            entry.cursor_position = len(entry.value)

        # --- input ---
        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line:
                return
            if not self._input_history or self._input_history[-1] != line:
                self._input_history.append(line)  # ↑/↓ recall (no consecutive dups)
            self._hist_index = None
            cmd, arg = parse_command(line)
            if cmd is not None:
                self._handle_command(cmd, arg)
                return
            self._route_message(line, pin=self.state.pinned, ephemeral=False)

        def _route_message(self, text: str, *, pin: str | None, ephemeral: bool = False) -> None:
            """Route one turn: render the decision, then call the (possibly forced) model.

            ``pin`` forces the route for this turn (``None`` = the natural decision).
            ``ephemeral`` (``/btw``) sends the turn standalone — no history attached, and
            neither the question nor the reply is added to the thread.
            """
            self._append(self._user_line(text, aside=ephemeral))
            if ephemeral:
                convo: list[dict] = [{"role": "user", "content": text}]
            else:
                self.messages.append({"role": "user", "content": text})
                convo = self.messages

            if self.base_url is not None:  # the remote gateway decides and replies
                if not ephemeral:
                    self._persist()
                self._set_busy(True)
                self._remote_worker(convo, pin, ephemeral)
                return

            try:  # in-process: score locally, then call the chosen (or forced) model
                decision = decide(text, start_dir=self.start_dir, threshold=self.state.threshold)
            except WayfinderConfigError as exc:
                self._warn(str(exc))
                if not ephemeral:
                    self.messages.pop()
                return
            self.history.append(decision)
            forced_to = resolve_target(pin, decision) if pin is not None else None
            self._append(
                render_decision(
                    decision, self.palette, expanded=self.state.show_why, forced_to=forced_to
                )
            )
            if ephemeral:
                self._note("aside · not added to the thread")
            else:
                self._persist()  # capture the user turn (and decision-only conversations)
            if not self.models:
                return
            if forced_to is not None:
                target, target_is_local = forced_to
            else:
                target, target_is_local = decision.model, decision.is_local
            model = self.models.get(target)
            if model is None:
                self._note(f"no model configured for '{target}'")
                return
            cloud_name = decision.targets[-1] if decision.targets else target
            cloud_model = self.models.get(cloud_name)
            cloud_cost = cloud_model.cost_per_1k if cloud_model is not None else None
            self._set_busy(True)
            self._stream_worker(
                model, convo, not ephemeral, target_is_local, model.cost_per_1k, cloud_cost
            )

        # --- slash commands (main thread) ---
        def _handle_command(self, cmd: str, arg: str) -> None:
            if cmd in {"quit", "q", "exit"}:
                self.exit()
                return
            if cmd == "help":
                self._note(_HELP)
                return
            if cmd == "settings":
                self._append(render_settings(self.state, self.palette))
                return
            if cmd == "models":
                self._handle_models()
                return
            if cmd == "cost":
                self._append(render_cost(self._cost, self.palette))
                return
            if cmd == "init":
                self._handle_init(arg)
                return
            if cmd == "new":
                self._handle_new()
                return
            if cmd == "threads":
                self._thread_list = threads.list_threads(self._data_dir)
                self._append(render_threads(self._thread_list, self.palette))
                return
            if cmd in {"open", "thread"}:
                self._handle_open(arg)
                return
            if cmd == "route":
                self._handle_route(arg)
            elif cmd == "auto":
                self.state.pinned = None
                self._note("routing: auto")
            elif cmd in {"local", "cloud"}:
                sentinel = "prefer-local" if cmd == "local" else "prefer-hosted"
                message = arg.strip()
                if message:  # one-shot force for this turn, kept in the thread
                    self._route_message(message, pin=sentinel, ephemeral=False)
                    return
                self.state.pinned = sentinel
                self._note(f"pinned → {cmd} every turn · /auto to resume routing")
            elif cmd == "btw":
                question = arg.strip()
                if not question:
                    self._warn("usage: /btw <quick question>  — a one-off aside routed local")
                    return
                self._route_message(question, pin="prefer-local", ephemeral=True)
                return
            elif cmd == "threshold":
                try:
                    self.state.threshold = max(0.0, min(1.0, float(arg)))
                    self._note(f"threshold {self.state.threshold:.2f}")
                except ValueError:
                    self._warn("threshold must be a number 0..1")
            elif cmd == "scope":
                if arg in _SCOPES:
                    self.state.scope = arg
                    self._note(f"scope {arg}")
                else:
                    self._warn("scope must be turn|last_user|user|all")
            elif cmd == "sticky":
                parts = arg.split()
                if parts and parts[0] in {"on", "off"}:
                    self.state.sticky = parts[0] == "on"
                    if len(parts) > 1 and parts[1].isdigit():
                        self.state.cooldown = int(parts[1])
                    tail = f" · cooldown {self.state.cooldown}" if self.state.sticky else ""
                    self._note(f"sticky {'on' if self.state.sticky else 'off'}{tail}")
                else:
                    self._warn("sticky on|off [N]")
            elif cmd == "theme":
                if arg in {"dark", "light", "auto"}:
                    self.state.theme = _resolve_theme(arg)
                    self.palette = palette_for(arg)
                    self._apply_palette()
                    self._note(f"theme {self.state.theme}")
                else:
                    self._warn("theme dark|light|auto")
            elif cmd == "why":
                self._handle_why(arg.strip().lower())
            elif cmd == "stream":
                value = arg.strip().lower()
                if value in {"on", "off", ""}:
                    self.state.stream = value != "off"
                    self._note(f"stream {'on' if self.state.stream else 'off'}")
                else:
                    self._warn("stream on|off")
            else:
                self._warn(f"unknown command /{cmd} — /help")
            self._refresh_bars()

        def _handle_route(self, arg: str) -> None:
            target = arg.strip()
            if not target:  # show current pin + the available targets
                names = ", ".join(sorted(self.models)) if self.models else "(set by the gateway)"
                self._note(f"routing: {_pin_label(self.state.pinned)} · models: {names}")
                return
            if target in {"auto", "off"}:
                self.state.pinned = None
                self._note("routing: auto")
                return
            if target in {"local", "cloud"}:  # aliases for the tier ends
                self.state.pinned = "prefer-local" if target == "local" else "prefer-hosted"
                self._note(f"pinned → {target} · /auto to resume routing")
                return
            if self.base_url is None and self.models and target not in self.models:
                self._warn(f"unknown model '{target}' — available: {', '.join(sorted(self.models))}")
                return
            self.state.pinned = target
            self._note(f"pinned → {target} · /auto to resume routing")

        # --- conversation persistence (WF-ADR-0030) ---
        def _persist(self) -> None:
            """Save the active thread to disk. UI-free, so it is safe from a worker thread."""
            if not self.messages:
                return
            self._thread.messages = list(self.messages)
            try:
                threads.save_thread(self._thread, self._data_dir)
            except OSError:
                pass  # never let a failed save crash the chat

        def _handle_new(self) -> None:
            self._persist()  # the current thread is already saved; make sure
            self.messages = []
            self.history = []
            self._thread = threads.new_thread()
            self._body.remove_children()
            self._note("new conversation — type a prompt")

        def _handle_open(self, arg: str) -> None:
            entries = self._thread_list or threads.list_threads(self._data_dir)
            self._thread_list = entries
            try:
                index = int(arg.strip()) - 1
            except ValueError:
                self._warn("usage: /open <number>  (see /threads)")
                return
            if not 0 <= index < len(entries):
                self._warn(f"no thread {arg.strip()!r} — /threads to list")
                return
            self._persist()  # save the current conversation before switching away
            self._load_thread(entries[index])

        def _load_thread(self, thread: threads.Thread) -> None:
            self._thread = thread
            self.messages = list(thread.messages)
            self.history = []
            self._body.remove_children()
            self._note(f"thread · {thread.title}")
            for message in self.messages:
                content = str(message.get("content", ""))
                if message.get("role") == "user":
                    self._append(self._user_line(content))
                    if self.base_url is None and content:
                        try:
                            decision = decide(
                                content, start_dir=self.start_dir, threshold=self.state.threshold
                            )
                        except WayfinderConfigError:
                            continue
                        self.history.append(decision)
                        self._append(
                            render_decision(decision, self.palette, expanded=self.state.show_why)
                        )
                elif message.get("role") == "assistant":
                    self._append(render_reply(content))

        def _handle_models(self) -> None:
            if self.base_url is not None:
                self._note(f"models are managed by the remote gateway at {self.base_url}")
                return
            self._append(render_models(self.models, self.palette))

        def _handle_init(self, arg: str) -> None:
            """Scaffold a wayfinder-router.toml from a preset and load its models in-place."""
            from pathlib import Path

            from . import bootstrap

            if self.base_url is not None:
                self._note("connected to a remote gateway — configure its models there, not here")
                return
            name = arg.strip() or bootstrap.DEFAULT_PRESET
            preset = bootstrap.PRESETS.get(name)
            if preset is None:
                self._warn(f"unknown preset '{name}' — try: {', '.join(sorted(bootstrap.PRESETS))}")
                return
            config_path = Path(self.start_dir) / "wayfinder-router.toml"
            if config_path.exists():
                self._warn(
                    f"{config_path} already exists — edit it, or run "
                    "`wayfinder-router init --force` in a shell"
                )
                return
            try:
                config_path.write_text(bootstrap.render_config(preset), encoding="utf-8")
                extra = ""
                if preset.env_vars:
                    env_path = config_path.parent / ".env.example"
                    if not env_path.exists():
                        env_path.write_text(bootstrap.render_env_example(preset), encoding="utf-8")
                        extra = f" (+ {env_path.name})"
            except OSError as exc:
                self._warn(f"could not write config: {exc}")
                return
            self._note(f"wrote {config_path}{extra} · preset {preset.name}")
            try:
                from .gateway import load_gateway_config

                self.models = dict(load_gateway_config(self.start_dir).models)
            except WayfinderConfigError as exc:
                self._warn(str(exc))
                return
            self._append(render_models(self.models, self.palette))
            missing = bootstrap.missing_keys(bootstrap.key_status(self.models))
            if missing:
                self._note(
                    "set " + ", ".join(missing) + " in your shell and restart for cloud replies — "
                    "keyless local works now"
                )
            else:
                self._note("models ready — type a prompt")

        def _handle_why(self, value: str) -> None:
            if value == "on":
                self.state.show_why = True
                self._note("why: auto-expand on")
            elif value == "off":
                self.state.show_why = False
                self._note("why: collapsed")
            elif value.isdigit() and 1 <= int(value) <= len(self.history):
                self._append(render_decision(self.history[int(value) - 1], self.palette, expanded=True))
            elif not value and self.history:
                self._append(render_decision(self.history[-1], self.palette, expanded=True))
            elif not value:
                self._note("nothing to expand yet")
            else:
                self._warn("why [on|off|N]")

        # --- streaming workers (threads: the relay is blocking sync I/O) ---
        @work(thread=True, exclusive=True, group="reply")
        def _stream_worker(
            self, model: GatewayModel, messages: list[dict], remember: bool,
            is_local: bool, chosen_cost: float | None, cloud_cost: float | None,
        ) -> None:
            from .gateway import (
                GatewayUnavailable,
                UpstreamError,
                invoke_messages,
                stream_messages,
            )

            self.call_from_thread(self._set_note, "streaming… (ctrl-c to cancel)")
            live = self.call_from_thread(self._append, Text("", style=self.palette["text"]))
            full = ""
            try:
                if self.state.stream:
                    parts: list[str] = []
                    for delta in stream_messages(model, messages, timeout=self.timeout):
                        if self._cancel.is_set():  # ctrl-c: stop at the next token
                            break
                        parts.append(delta)
                        self.call_from_thread(self._set_live_text, live, "".join(parts))
                    full = "".join(parts)
                else:
                    full = invoke_messages(model, messages, timeout=self.timeout)
                if self._cancel.is_set():
                    self.call_from_thread(self._finalize_cancelled, live, full)
                else:
                    self.call_from_thread(self._finalize_reply, live, full)
                    if full and remember:  # ephemeral /btw turns are not kept in the thread
                        sent = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
                        self.messages.append({"role": "assistant", "content": full})
                        self._persist()
                        self.call_from_thread(
                            self._account, is_local, sent + estimate_tokens(full),
                            chosen_cost, cloud_cost,
                        )
            except (GatewayUnavailable, UpstreamError, RuntimeError) as exc:
                self.call_from_thread(self._finalize_error, live, str(exc))
            finally:
                self.call_from_thread(self._set_note, None)
                self.call_from_thread(self._set_busy, False)

        @work(thread=True, exclusive=True, group="reply")
        def _remote_worker(self, messages: list[dict], pin: str | None, ephemeral: bool) -> None:
            from .gateway import GatewayUnavailable, UpstreamError

            assert self.base_url is not None  # only spawned in the remote backend
            self.call_from_thread(self._set_note, "asking gateway… (ctrl-c to cancel)")
            model_field = pin if pin is not None else "auto"
            try:
                decision, reply = remote_reply(
                    self.base_url, messages, model=model_field,
                    threshold=self.state.threshold, timeout=self.timeout,
                )
            except (GatewayUnavailable, UpstreamError, RuntimeError) as exc:
                self.call_from_thread(self._warn, f"gateway error: {exc}")
                if not ephemeral:
                    self.call_from_thread(self.messages.pop)
                self.call_from_thread(self._set_note, None)
                self.call_from_thread(self._set_busy, False)
                return
            if self._cancel.is_set():  # ctrl-c during the (non-streaming) request: discard
                if not ephemeral:
                    self.call_from_thread(self.messages.pop)
                self.call_from_thread(self._note, "⨯ cancelled")
                self.call_from_thread(self._set_note, None)
                self.call_from_thread(self._set_busy, False)
                return
            if decision is not None:
                self.history.append(decision)
                forced_to = resolve_target(pin, decision) if pin is not None else None
                self.call_from_thread(
                    self._append,
                    render_decision(
                        decision, self.palette, expanded=self.state.show_why, forced_to=forced_to
                    ),
                )
            if ephemeral:
                self.call_from_thread(self._note, "aside · not added to the thread")
            if reply is not None:
                if not ephemeral:
                    self.messages.append({"role": "assistant", "content": reply})
                    self._persist()
                self.call_from_thread(self._append, render_reply(reply))
            elif not ephemeral:
                self.call_from_thread(self.messages.pop)
            self.call_from_thread(self._set_note, None)
            self.call_from_thread(self._set_busy, False)

    return WayfinderChat


def run_tui(
    *, start_dir: str = ".", theme: str = "auto", show_why: bool = False,
    threshold: float | None = None, dry_run: bool = False, stream: bool = True,
    base_url: str | None = None,
) -> None:
    """Launch the full-screen chat: route each line, render the decision, and — when a
    backend is available — the model's reply (streamed). Ctrl-C / /quit to exit.

    Backends: in-process via the local ``[gateway.models]`` (default), or a remote gateway
    over HTTP with ``base_url`` (the thin-client form). ``dry_run`` forces decision-only.
    """
    _require_tui()
    app_cls = _build_chat_app()
    app = app_cls(
        start_dir=start_dir, theme=theme, show_why=show_why, threshold=threshold,
        dry_run=dry_run, stream=stream, base_url=base_url,
    )
    app.run()
