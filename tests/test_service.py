"""Tests for the local-service unit generators (WF-ADR-0038).

Pure generators + path/platform helpers — golden-tested with no I/O, like ``test_cache.py``.
The live ``launchctl`` / ``systemctl`` install paths are not exercised here (the CI host is
Linux); ``--print`` and these golden checks cover the generated unit text.
"""

from __future__ import annotations

from pathlib import Path

from wayfinder_router import service
from wayfinder_router.cli import _resolve_serve_args, build_parser


def test_detect_platform():
    assert service.detect_platform("darwin") == "macos"
    assert service.detect_platform("linux") == "linux"
    assert service.detect_platform("linux2") == "linux"
    assert service.detect_platform("win32") == "other"


def test_launchd_plist_is_well_formed():
    plist = service.launchd_plist(["/usr/local/bin/wayfinder-router", "serve", "--port", "8088"])
    assert plist.startswith('<?xml version="1.0"')
    assert f"<string>{service.LAUNCHD_LABEL}</string>" in plist
    assert "<key>RunAtLoad</key>\n  <true/>" in plist
    assert "<key>KeepAlive</key>\n  <true/>" in plist
    # every program argument is rendered as its own <string>
    assert "<string>/usr/local/bin/wayfinder-router</string>" in plist
    assert "<string>serve</string>" in plist
    assert "<string>8088</string>" in plist


def test_launchd_plist_xml_escapes_arguments():
    plist = service.launchd_plist(["/bin/x & <y>", "serve"])
    assert "<string>/bin/x &amp; &lt;y&gt;</string>" in plist
    assert "& <y>" not in plist  # raw special chars never leak into the XML


def test_systemd_unit_is_well_formed():
    unit = service.systemd_unit(["/usr/bin/wayfinder-router", "serve", "--port", "8088"])
    assert "ExecStart=/usr/bin/wayfinder-router serve --port 8088" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_systemd_unit_shell_quotes_arguments():
    unit = service.systemd_unit(["/opt/my router/wayfinder-router", "serve"])
    assert "ExecStart='/opt/my router/wayfinder-router' serve" in unit


def test_unit_paths_use_the_given_home():
    home = Path("/home/tester")
    assert service.agent_path(home) == home / "Library/LaunchAgents/com.wayfinder-router.gateway.plist"
    assert service.systemd_unit_path(home) == home / ".config/systemd/user/wayfinder-router.service"


def test_resolve_serve_args_targets_serve():
    args = _resolve_serve_args("127.0.0.1", 8088)
    assert args[-5:] == ["serve", "--host", "127.0.0.1", "--port", "8088"]
    assert args[0]  # a non-empty launcher (the console script or the python executable)


def test_service_install_print_emits_a_unit_without_touching_the_system(monkeypatch, capsys):
    # Force the macOS branch so --print emits the launchd plist regardless of CI host.
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "macos")
    args = build_parser().parse_args(["service", "install", "--print"])
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith('<?xml version="1.0"')
    assert service.LAUNCHD_LABEL in out
