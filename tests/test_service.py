"""Tests for the local-service unit generators (WF-ADR-0038).

Pure generators + path/platform helpers — golden-tested with no I/O, like ``test_cache.py``.
The live ``launchctl`` / ``systemctl`` install paths are not exercised here (the CI host is
Linux); ``--print`` and these golden checks cover the generated unit text.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import types
from pathlib import Path

from wayfinder_router import service
from wayfinder_router.cli import EXIT_CONFIG, EXIT_OK, _resolve_serve_args, build_parser


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


def test_launchd_plist_default_log_dir_is_absolute(monkeypatch):
    # launchd cannot expand ~ in StandardOut/ErrPath; the default log_dir must be resolved even
    # when a caller omits it (not only when the CLI pre-expands it).
    monkeypatch.setenv("HOME", "/home/tester")
    plist = service.launchd_plist(["/usr/local/bin/wayfinder-router", "serve"])
    assert "~/Library/Logs" not in plist  # no unexpanded tilde reaches the plist
    assert "/home/tester/Library/Logs" in plist


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


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_service_install_reports_launchctl_failure(monkeypatch, tmp_path, capsys):
    # `service install` must not claim success when launchctl couldn't load the agent.
    monkeypatch.setenv("HOME", str(tmp_path))  # redirect agent_path / log dir into tmp
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "macos")
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/launchctl")
    # bootstrap, the legacy load fallback, and the end-state probe all fail.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(1, stderr="Bootstrap failed: 5: busy"))
    args = build_parser().parse_args(["service", "install"])
    rc = args.func(args)
    err = capsys.readouterr().err
    assert rc == EXIT_CONFIG  # not EXIT_OK
    assert "could not load" in err and "busy" in err


def test_service_install_succeeds_when_launchctl_loads(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "macos")
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/launchctl")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(0))  # bootstrap + probe both OK
    args = build_parser().parse_args(["service", "install"])
    rc = args.func(args)
    assert rc == EXIT_OK
    assert "installed and loaded" in capsys.readouterr().err


def test_service_install_reports_systemctl_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/systemctl")

    def fake_run(cmd, *a, **k):
        if "enable" in cmd:
            return _proc(1, stderr="Failed to enable unit: permission denied")
        return _proc(0)  # daemon-reload succeeds

    monkeypatch.setattr(subprocess, "run", fake_run)
    args = build_parser().parse_args(["service", "install"])
    rc = args.func(args)
    err = capsys.readouterr().err
    assert rc == EXIT_CONFIG
    assert "could not enable" in err and "permission denied" in err


def test_resolve_serve_args_includes_config_when_given():
    args = _resolve_serve_args("127.0.0.1", 8088, "/etc/wf/wayfinder-router.toml")
    assert args[-2:] == ["--config", "/etc/wf/wayfinder-router.toml"]
    assert "--config" not in _resolve_serve_args("127.0.0.1", 8088)  # absent by default


def test_service_install_print_bakes_config_into_the_unit(monkeypatch, capsys):
    # `service install --config PATH --print` threads --config into the unit's ProgramArguments,
    # so a launchd / systemd gateway loads a fixed file regardless of its working directory.
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "macos")
    args = build_parser().parse_args(
        ["service", "install", "--print", "--config", "/etc/wf/wayfinder-router.toml"]
    )
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "<string>--config</string>" in out
    assert "<string>/etc/wf/wayfinder-router.toml</string>" in out


def test_service_install_resolves_an_absolute_log_path(monkeypatch, capsys):
    # launchd cannot expand ``~`` in StandardOutPath/StandardErrorPath; an unresolved tilde
    # makes the agent fail to spawn (EX_CONFIG). The CLI must emit an absolute log path.
    monkeypatch.setattr(service, "detect_platform", lambda platform=None: "macos")
    args = build_parser().parse_args(["service", "install", "--print"])
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "<string>~/" not in out  # no unexpanded tilde anywhere in the emitted plist
    home = os.path.expanduser("~")
    assert f"<string>{home}/Library/Logs/wayfinder-router.log</string>" in out
    assert f"<string>{home}/Library/Logs/wayfinder-router.err.log</string>" in out
