"""On-disk conversation persistence for the terminal chat (WF-ADR-0030).

The disk-side sibling of the demo's localStorage threads (WF-ADR-0026): a thread is a
saved transcript as JSON on the user's own disk. Titles are derived from the first user
message with no model call (WF-ADR-0026). The gateway stays stateless (WF-ADR-0022);
this is purely client-side and stdlib-only (WF-ADR-0001), so it tests without a terminal.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def threads_dir() -> Path:
    """Storage root for conversations: ``$WAYFINDER_DATA_DIR`` or the XDG data home."""
    base = os.environ.get("WAYFINDER_DATA_DIR")
    if base:
        root = Path(base)
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        root = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "wayfinder"
    return root / "threads"


@dataclass
class Thread:
    """A saved conversation: id, derived title, timestamps, and the message list.

    Mutable by design — callers reassign ``id`` / ``updated`` / ``messages`` in place.
    """

    id: str
    title: str = ""
    created: str = ""
    updated: str = ""
    messages: list[dict] = field(default_factory=list)


def _now() -> str:
    # ISO-ish UTC timestamp with a trailing Z; used for created/updated.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_thread() -> Thread:
    """A fresh, empty thread with a sortable, collision-resistant id.

    The id is a compact 1-second UTC stamp plus 8 random bytes (2^64 of entropy). Since
    ``save_thread`` overwrites by id, the random suffix is what stops two threads minted in
    the same wall-clock second from clobbering each other — a burst stays collision-free
    while the stamp prefix keeps ids sorting by creation time.
    """
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())  # compact, lexically sortable
    now = _now()
    return Thread(id=f"{stamp}-{os.urandom(8).hex()}", created=now, updated=now)


def title_from(messages: list[dict], *, limit: int = 50) -> str:
    """Whitespace-collapsed first non-empty user message, truncated — no model call.

    Scans every message, returning the first ``user`` turn whose collapsed content is
    non-empty (earlier user turns with blank content are skipped). Truncation appends a
    single U+2026 ellipsis only when the text actually exceeds ``limit``.
    """
    for message in messages:
        if message.get("role") == "user":
            text = " ".join(str(message.get("content", "")).split())
            if text:
                return text[:limit] + ("…" if len(text) > limit else "")
    return "(empty)"


def save_thread(thread: Thread, directory: Path | None = None) -> Path:
    """Write ``thread`` to ``<directory>/<id>.json``, refreshing its title and updated time."""
    directory = directory or threads_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # Title and timestamp are re-derived on every save, ignoring any prior title.
    thread.updated = _now()
    thread.title = title_from(thread.messages)
    path = directory / f"{thread.id}.json"
    path.write_text(json.dumps(asdict(thread), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_thread(path: Path) -> Thread:
    """Rebuild a :class:`Thread` from its JSON file, coercing types and dropping junk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Thread(
        id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        created=str(data.get("created", "")),
        updated=str(data.get("updated", "")),
        # Missing key -> []; non-dict entries are discarded.
        messages=[m for m in data.get("messages", []) if isinstance(m, dict)],
    )


def list_threads(directory: Path | None = None) -> list[Thread]:
    """All saved threads, most-recently-updated first; unreadable/corrupt files are skipped."""
    directory = directory or threads_dir()
    if not directory.is_dir():
        return []
    found: list[Thread] = []
    for path in directory.glob("*.json"):
        try:
            found.append(load_thread(path))
        except (OSError, ValueError):
            continue  # unreadable or malformed JSON — silently ignore
    # Plain string compare of the ISO-lexical ``updated`` stamp; newest first.
    found.sort(key=lambda t: t.updated, reverse=True)
    return found
