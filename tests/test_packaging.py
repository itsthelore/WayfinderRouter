"""Guards the packaged dependency contract (WF-ADR-0029).

The terminal chat ships by default (rich + textual are core deps), but the scorer /
library stays import-light: importing ``wayfinder_router`` must not load the UI stack.
"""

from __future__ import annotations

import subprocess
import sys

from importlib.metadata import requires


def _core_requirements() -> list[str]:
    # Core deps have no environment marker; extras carry `; extra == "..."`.
    return [r for r in (requires("wayfinder-router") or []) if "extra ==" not in r]


def test_tui_deps_ship_by_default():
    core = " ".join(_core_requirements()).lower()
    assert "rich" in core and "textual" in core  # chat works out of the box (WF-ADR-0029)


def test_gateway_and_ui_stay_extras():
    core = " ".join(_core_requirements()).lower()
    assert "fastapi" not in core and "uvicorn" not in core  # only the TUI is promoted


def test_importing_the_library_does_not_load_the_tui_stack():
    code = "import wayfinder_router, sys; print('rich' in sys.modules or 'textual' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"  # lazy imports keep embedding light
