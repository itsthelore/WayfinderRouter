"""Run Wayfinder as an always-on local service (WF-ADR-0038).

Pure generators for the OS service-manager units that keep the gateway running on a stable
localhost endpoint, so every OpenAI-compatible app on the machine can share one ``base_url``
— the near-term, localhost slice of "LLM routing as infrastructure" (WF-ROADMAP-0007). No I/O
lives here: the CLI writes the files and drives ``launchctl`` / ``systemctl``; these functions
just render text and resolve paths, so they golden-test like ``reliability.py`` / ``cache.py``.

macOS (**launchd**) is the primary target; the systemd user unit ships for Linux. This is
packaging in the invocation layer — the deterministic decision core is untouched (WF-ADR-0001).
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

# The launchd label / systemd unit name double as the on-disk identity for uninstall + status.
LAUNCHD_LABEL = "com.wayfinder-router.gateway"
SYSTEMD_UNIT_NAME = "wayfinder-router.service"


def detect_platform(platform: str | None = None) -> str:
    """Map a ``sys.platform`` string to ``"macos"`` / ``"linux"`` / ``"other"`` (host by default)."""
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        return "macos"
    if plat.startswith("linux"):
        return "linux"
    return "other"


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchd_plist(
    program_args: list[str],
    *,
    label: str = LAUNCHD_LABEL,
    log_dir: str = "~/Library/Logs",
) -> str:
    """A launchd LaunchAgent plist that runs ``program_args`` at login and keeps it alive.

    ``RunAtLoad`` starts it immediately and on every login; ``KeepAlive`` restarts it if it
    exits — the always-on behavior that makes the gateway feel like infrastructure.
    """
    args_xml = "\n".join(f"      <string>{_xml_escape(arg)}</string>" for arg in program_args)
    # launchd does not expand ``~`` in StandardOut/ErrPath — an unresolved tilde makes the agent
    # fail to spawn (EX_CONFIG). Resolve it here so every caller (not just the CLI) is safe.
    logs = os.path.expanduser(log_dir).rstrip("/")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        f"  <string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"{args_xml}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "  <key>KeepAlive</key>\n"
        "  <true/>\n"
        "  <key>StandardOutPath</key>\n"
        f"  <string>{logs}/wayfinder-router.log</string>\n"
        "  <key>StandardErrorPath</key>\n"
        f"  <string>{logs}/wayfinder-router.err.log</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def systemd_unit(
    program_args: list[str], *, description: str = "Wayfinder router gateway"
) -> str:
    """A systemd **user** unit (Linux follow-on) that runs ``program_args`` and restarts on failure."""
    exec_start = " ".join(shlex.quote(arg) for arg in program_args)
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def agent_path(home: Path | None = None) -> Path:
    """Where the LaunchAgent plist lives: ``~/Library/LaunchAgents/<label>.plist``."""
    base = home if home is not None else Path.home()
    return base / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def systemd_unit_path(home: Path | None = None) -> Path:
    """Where the systemd user unit lives: ``~/.config/systemd/user/<name>``."""
    base = home if home is not None else Path.home()
    return base / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
