#!/usr/bin/env python3
"""Generate Python-authoritative launchd/systemd compatibility vectors."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from wayfinder_router import service  # noqa: E402


@contextmanager
def _home(path: str) -> Iterator[None]:
    previous = os.environ.get("HOME")
    os.environ["HOME"] = path
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def generate() -> dict[str, object]:
    home = "/Users/compat-user"
    launchd_cases = [
        {
            "name": "default",
            "args": ["/usr/local/bin/wayfinder-router", "serve", "--port", "8088"],
            "label": None,
            "log_dir": None,
        },
        {
            "name": "escaping-and-custom-log",
            "args": ["/opt/router & <helper>", "serve", "--config", "/tmp/a & b.toml"],
            "label": "com.example.compat",
            "log_dir": "/tmp/wayfinder-logs/",
        },
        {
            "name": "empty-program-arguments",
            "args": [],
            "label": None,
            "log_dir": "~",
        },
    ]
    systemd_cases = [
        {
            "name": "safe",
            "args": ["/usr/bin/wayfinder-router", "serve", "--port", "8088"],
            "description": None,
        },
        {
            "name": "shell-quoting",
            "args": ["/opt/my router/wayfinder-router", "serve", "it's-ready", ""],
            "description": "Wayfinder custom gateway",
        },
        {
            "name": "unicode-is-quoted",
            "args": ["/usr/bin/wayfinder-router", "café"],
            "description": None,
        },
    ]

    with _home(home):
        for case in launchd_cases:
            kwargs: dict[str, str] = {}
            if case["label"] is not None:
                kwargs["label"] = str(case["label"])
            if case["log_dir"] is not None:
                kwargs["log_dir"] = str(case["log_dir"])
            case["output"] = service.launchd_plist(case["args"], **kwargs)

    for case in systemd_cases:
        kwargs = {}
        if case["description"] is not None:
            kwargs["description"] = str(case["description"])
        case["output"] = service.systemd_unit(case["args"], **kwargs)

    return {
        "schema_version": "1",
        "home": home,
        "platforms": {
            value: service.detect_platform(value)
            for value in ["darwin", "linux", "linux2", "win32", "freebsd"]
        },
        "paths": {
            "launchd": str(service.agent_path(Path(home))),
            "systemd": str(service.systemd_unit_path(Path(home))),
        },
        "launchd": launchd_cases,
        "systemd": systemd_cases,
    }


if __name__ == "__main__":
    print(json.dumps(generate(), indent=2, ensure_ascii=False, sort_keys=True))
