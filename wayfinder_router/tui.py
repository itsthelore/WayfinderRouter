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

The UI is an opt-in extra (``pip install 'wayfinder-router[tui]'`` → rich + textual);
both are imported lazily so the package still imports without them (mirrors the
gateway's fastapi pattern). The Textual ``App`` is built behind a factory so importing
this module never requires textual.
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

if TYPE_CHECKING:  # type-only; the runtime imports rich/textual/gateway lazily
    from rich.console import RenderableType

    from .gateway import GatewayModel

_INSTALL_HINT = "the terminal UI needs its extra: pip install 'wayfinder-router[tui]'"
_SCOPES = ("turn", "last_user", "user", "all")

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
        ("streaming", "on" if state.stream else "off"),
        ("theme", state.theme),
    ]
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=muted, justify="right")
    grid.add_column(style=text_c)
    for key, val in rows:
        grid.add_row(key, val)

    hint = Text(
        "\nchange:  /threshold  /scope  /sticky  /why  /stream  /theme   ·   /help",
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
        Text("? for help   ·   / for commands   ·   ctrl-c to quit", style=muted),
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
    tiers = payload.get("tiers") or []
    model = str(payload.get("model", "?"))
    is_local = bool(tiers) and model == tiers[0].get("model")
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
        score=float(payload.get("score", 0.0)),
        mode=str(payload.get("mode", "")),
        is_local=is_local,
        contributions=contributions,
    )


def remote_reply(
    base_url: str, messages: list[dict], *, threshold: float | None = None, timeout: float = 60.0
) -> tuple[Decision | None, str | None]:
    """POST to a running gateway's ``/v1/chat/completions``; return ``(decision, reply)``.

    The thin-client backend (WF-DESIGN-0001): the *remote* gateway makes the routing
    decision (surfaced via ``X-Wayfinder-Debug``) and the reply. Non-streaming.
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
    body = {"model": "auto", "messages": list(messages)}
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
    from textual.widgets import Input, Static
    from rich.text import Text

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
            Binding("ctrl+c", "quit", "quit", priority=True),
            Binding("ctrl+d", "quit", "quit", priority=True),
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
                yield Input(placeholder="Send a message — Wayfinder routes it…", id="entry")
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
            else:
                self._note(
                    "preview · routing decisions only — add [gateway.models] "
                    "(and drop --dry-run) for replies"
                )
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
            right = "no model call to decide" if not note else "routing…"
            self.query_one("#footer", Static).update(_footer_bar(self.palette, right=right))

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

        def _user_line(self, line: str) -> RenderableType:
            text = Text()
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
            entry = self.query_one("#entry", Input)
            entry.disabled = busy
            if not busy:
                entry.focus()

        # --- input ---
        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line:
                return
            cmd, arg = parse_command(line)
            if cmd is not None:
                self._handle_command(cmd, arg)
                return

            self._append(self._user_line(line))
            self.messages.append({"role": "user", "content": line})

            if self.base_url is not None:  # the remote gateway decides and replies
                self._set_busy(True)
                self._remote_worker()
                return

            try:  # in-process: score locally, then call the chosen model
                decision = decide(line, start_dir=self.start_dir, threshold=self.state.threshold)
            except WayfinderConfigError as exc:
                self._warn(str(exc))
                self.messages.pop()
                return
            self.history.append(decision)
            self._append(render_decision(decision, self.palette, expanded=self.state.show_why))
            if not self.models:
                return
            model = self.models.get(decision.model)
            if model is None:
                self._note(f"no model configured for '{decision.model}'")
                return
            self._set_busy(True)
            self._stream_worker(model)

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
            if cmd == "threshold":
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
        def _stream_worker(self, model: GatewayModel) -> None:
            from .gateway import (
                GatewayUnavailable,
                UpstreamError,
                invoke_messages,
                stream_messages,
            )

            self.call_from_thread(self._set_note, "streaming…")
            live = self.call_from_thread(self._append, Text("", style=self.palette["text"]))
            full = ""
            try:
                if self.state.stream:
                    parts: list[str] = []
                    for delta in stream_messages(model, self.messages, timeout=self.timeout):
                        parts.append(delta)
                        self.call_from_thread(self._set_live_text, live, "".join(parts))
                    full = "".join(parts)
                else:
                    full = invoke_messages(model, self.messages, timeout=self.timeout)
                self.call_from_thread(self._finalize_reply, live, full)
                if full:
                    self.messages.append({"role": "assistant", "content": full})
            except (GatewayUnavailable, UpstreamError, RuntimeError) as exc:
                self.call_from_thread(self._finalize_error, live, str(exc))
            finally:
                self.call_from_thread(self._set_note, None)
                self.call_from_thread(self._set_busy, False)

        @work(thread=True, exclusive=True, group="reply")
        def _remote_worker(self) -> None:
            from .gateway import GatewayUnavailable, UpstreamError

            assert self.base_url is not None  # only spawned in the remote backend
            self.call_from_thread(self._set_note, "asking gateway…")
            try:
                decision, reply = remote_reply(
                    self.base_url, self.messages, threshold=self.state.threshold,
                    timeout=self.timeout,
                )
            except (GatewayUnavailable, UpstreamError, RuntimeError) as exc:
                self.call_from_thread(self._warn, f"gateway error: {exc}")
                self.call_from_thread(self.messages.pop)
                self.call_from_thread(self._set_note, None)
                self.call_from_thread(self._set_busy, False)
                return
            if decision is not None:
                self.history.append(decision)
                self.call_from_thread(
                    self._append,
                    render_decision(decision, self.palette, expanded=self.state.show_why),
                )
            if reply is not None:
                self.messages.append({"role": "assistant", "content": reply})
                self.call_from_thread(self._append, render_reply(reply))
            else:
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
