"""Tests for on-disk conversation persistence (WF-ADR-0030)."""

from __future__ import annotations

from wayfinder_router import threads


def test_threads_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WAYFINDER_DATA_DIR", str(tmp_path))
    assert threads.threads_dir() == tmp_path / "threads"


def test_title_from_first_user_message_no_model_call():
    msgs = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "  In one sentence,\n what is an API?  "},
    ]
    assert threads.title_from(msgs) == "In one sentence, what is an API?"
    assert threads.title_from([{"role": "user", "content": "x" * 80}]).endswith("…")
    assert threads.title_from([]) == "(empty)"


def test_new_thread_ids_are_unique_under_rapid_creation():
    # save_thread overwrites by id, so two threads minted in the same wall-clock second must not
    # collide and clobber each other's transcript. With 8 random bytes (2^64) per id, a burst of
    # fresh threads is collision-free; the prefix still sorts by creation time.
    ids = [threads.new_thread().id for _ in range(2000)]
    assert len(set(ids)) == len(ids)  # no collisions across a tight burst
    assert all("-" in tid for tid in ids)  # still the sortable "<stamp>-<rand>" shape


def test_save_and_load_round_trip(tmp_path):
    thread = threads.new_thread()
    thread.messages = [
        {"role": "user", "content": "what is an API?"},
        {"role": "assistant", "content": "A contract between programs."},
    ]
    path = threads.save_thread(thread, tmp_path)
    assert path.is_file() and path.name == f"{thread.id}.json"
    loaded = threads.load_thread(path)
    assert loaded.id == thread.id
    assert loaded.messages == thread.messages
    assert loaded.title == "what is an API?"  # derived on save
    assert loaded.updated  # stamped on save


def test_list_threads_newest_first(tmp_path):
    a = threads.new_thread()
    a.id, a.updated = "a", "2026-06-22T07:00:00Z"
    a.messages = [{"role": "user", "content": "older"}]
    b = threads.new_thread()
    b.id, b.updated = "b", "2026-06-22T09:00:00Z"
    b.messages = [{"role": "user", "content": "newer"}]
    # write directly so the saved `updated` is preserved (save_thread restamps)
    for thread in (a, b):
        (tmp_path / f"{thread.id}.json").write_text(
            __import__("json").dumps(
                {"id": thread.id, "title": "", "created": "", "updated": thread.updated,
                 "messages": thread.messages}
            ),
            encoding="utf-8",
        )
    listed = threads.list_threads(tmp_path)
    assert [t.id for t in listed] == ["b", "a"]  # most-recently-updated first


def test_list_threads_missing_dir_is_empty(tmp_path):
    assert threads.list_threads(tmp_path / "nope") == []
