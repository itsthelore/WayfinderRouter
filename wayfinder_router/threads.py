"""On-disk conversation persistence for the terminal chat (WF-ADR-0030).

The disk sibling of the demo's localStorage threads (WF-ADR-0026): a thread is the
saved transcript, JSON on the user's own disk. Titles come from the first user message
— no model call to name a chat (WF-ADR-0026). The gateway stays stateless
(WF-ADR-0022); this is purely client-side and pure/stdlib (WF-ADR-0001), so it is
testable without a terminal.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def threads_dir() -> Path:
    """Where conversations are stored: ``$WAYFINDER_DATA_DIR`` or the XDG data home."""
    base = os.environ.get("WAYFINDER_DATA_DIR")
    if base:
        root = Path(base)
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        root = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "wayfinder"
    return root / "threads"


@dataclass
class Thread:
    """A saved conversation: an id, a derived title, timestamps, and the messages."""

    id: str
    title: str = ""
    created: str = ""
    updated: str = ""
    messages: list[dict] = field(default_factory=list)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_thread() -> Thread:
    """A fresh, empty thread with a sortable, collision-resistant id."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    now = _now()
    return Thread(id=f"{stamp}-{os.urandom(2).hex()}", created=now, updated=now)


def title_from(messages: list[dict], *, limit: int = 50) -> str:
    """The first user message, whitespace-collapsed and truncated — no model call."""
    for message in messages:
        if message.get("role") == "user":
            text = " ".join(str(message.get("content", "")).split())
            if text:
                return text[:limit] + ("…" if len(text) > limit else "")
    return "(empty)"


def save_thread(thread: Thread, directory: Path | None = None) -> Path:
    """Write ``thread`` to ``<directory>/<id>.json``, refreshing its title + updated time."""
    directory = directory or threads_dir()
    directory.mkdir(parents=True, exist_ok=True)
    thread.updated = _now()
    thread.title = title_from(thread.messages)
    path = directory / f"{thread.id}.json"
    path.write_text(json.dumps(asdict(thread), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_thread(path: Path) -> Thread:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Thread(
        id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        created=str(data.get("created", "")),
        updated=str(data.get("updated", "")),
        messages=[m for m in data.get("messages", []) if isinstance(m, dict)],
    )


def list_threads(directory: Path | None = None) -> list[Thread]:
    """All saved threads, most-recently-updated first; unreadable files are skipped."""
    directory = directory or threads_dir()
    if not directory.is_dir():
        return []
    found: list[Thread] = []
    for path in directory.glob("*.json"):
        try:
            found.append(load_thread(path))
        except (OSError, ValueError):
            continue
    found.sort(key=lambda t: t.updated, reverse=True)
    return found
