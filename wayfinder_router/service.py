"""Render OS service-manager units so the gateway runs as an always-on local service (WF-ADR-0038).

These are pure text/path generators: the CLI is what writes the files and drives
``launchctl`` / ``systemctl``. Keeping them side-effect free lets them golden-test like the
rest of the deterministic core (WF-ADR-0001). macOS launchd is the primary target; a systemd
user unit ships for Linux.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

# Label / unit name double as the on-disk identity used for uninstall and status.
LAUNCHD_LABEL = "com.wayfinder-router.gateway"
SYSTEMD_UNIT_NAME = "wayfinder-router.service"


def detect_platform(platform: str | None = None) -> str:
    """Map a ``sys.platform`` string to ``"macos"`` / ``"linux"`` / ``"other"`` (host by default).

    Kept a module-level function so the CLI tests can monkeypatch it to force a branch.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        return "macos"
    if plat.startswith("linux"):  # "linux", "linux2", ... all collapse to "linux"
        return "linux"
    return "other"


def _xml_escape(value: str) -> str:
    # Ampersand MUST be escaped first, or the subsequent replacements double-escape it.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchd_plist(
    program_args: list[str],
    *,
    label: str = LAUNCHD_LABEL,
    log_dir: str = "~/Library/Logs",
) -> str:
    """A launchd LaunchAgent plist that runs ``program_args`` at login and keeps it alive.

    ``RunAtLoad`` starts it on every login and ``KeepAlive`` restarts it on exit — the
    always-on behavior that makes the gateway feel like infrastructure.
    """
    # Each argument becomes its own six-space-indented <string>, XML-escaped.
    args_xml = "\n".join(f"      <string>{_xml_escape(arg)}</string>" for arg in program_args)
    # launchd will not expand ``~`` in the log paths (an unresolved tilde makes the agent
    # fail to spawn with EX_CONFIG), so resolve it here — even for the default log_dir.
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
    # Shell-quote each argument so a spaced path survives the ExecStart line intact.
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
