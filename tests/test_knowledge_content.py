"""Content indexed in place (knowledge_content.py): files in a temporary
folder become items at their paths with their text and sections; the
watcher (real FSEvents on macOS) sees a create, a change, a rename and a
delete; dropped events scan their subtree; a backup scan finds what
changed while nothing watched; a replay from the last event ID handled
finds it too; the reading lane waits while the pace holds."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from jarvis import fs_watch, knowledge_content as kc
from jarvis.knowledge import embedder as emb
from jarvis.knowledge.uds import Query, Stream, in_process

TIM = kc.WRITER
ALL = {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS}
DOC = "# Plan\nWe will build the watcher.\n## Risks\nFSEvents can drop events under load."
WAIT_SEC = 30.0

macos = pytest.mark.skipif(not fs_watch.available(), reason="FSEvents is macOS only")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    with in_process(tmp_path / "engine") as found:
        yield found


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "docs-folder"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "plan.md").write_text(DOC)
    (root / "readme.txt").write_text("Read me first.\n\nIt says hello.")
    (root / "tool.py").write_text("print('code is text, not a doc')\n")
    (root / "Makefile").write_text("all:\n\techo no suffix\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "readme.md").write_text("# Not indexed\n")
    return root


def _root(folder: Path, scope: str = "docs") -> kc.Root:
    return kc.Root("/folders/f-1", os.path.realpath(folder), scope, "own", "f-1")


def _until(check, timeout: float = WAIT_SEC):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    return check()


def _index_all(indexer: kc.Indexer, root: kc.Root) -> list[str]:
    todo = indexer.scan(root)
    for rel in todo:
        indexer.extract(root, rel)
    return todo


def test_scan_and_read_make_items_at_their_paths(engine, folder):
    _, uds = engine
    indexer = kc.Indexer(uds)
    root = _root(folder)
    assert sorted(_index_all(indexer, root)) == ["notes/plan.md", "readme.txt"]
    item = uds.get(TIM, "/folders/f-1/notes/plan.md", ALL)
    assert item.kind == "md"
    assert item.data["state"] == "indexed" and item.data["title"] == "Plan" and item.data["format"] == "markdown"
    assert item.data["file"] == "notes/plan.md" and item.data["size"] == len(DOC)
    assert len(item.data["sha256"]) == 64 and item.data["inode"] == os.stat(folder / "notes/plan.md").st_ino
    assert item.content["ref"].startswith("jref:1:") and item.content["ref"].endswith("f-1/notes/plan.md")
    assert [s.heading for s in item.sections] == ["Plan", "Risks"]
    assert uds.count(TIM, under="/folders/f-1").items == 2
    # The text scope takes code and suffixless-free text too; .env, node_modules and Makefile never.
    wide = kc.Root("/folders/f-2", os.path.realpath(folder), "text", "own", "f-2")
    assert sorted(_index_all(indexer, wide)) == ["notes/plan.md", "readme.txt", "tool.py"]
    hits = uds.search(TIM, Query(phrase="drop events under load"))
    assert {h.path for h in hits} == {"/folders/f-1/notes/plan.md", "/folders/f-2/notes/plan.md"}


def test_changes_are_read_again_only_when_the_bytes_change(engine, folder):
    _, uds = engine
    indexer = kc.Indexer(uds)
    root = _root(folder)
    _index_all(indexer, root)
    first = uds.get(TIM, "/folders/f-1/readme.txt").version
    os.utime(folder / "readme.txt", None)                  # touched: same bytes
    assert indexer.scan(root) == ["readme.txt"]
    assert indexer.extract(root, "readme.txt") == "unchanged"
    assert uds.get(TIM, "/folders/f-1/readme.txt", ALL).data["state"] == "indexed"
    (folder / "readme.txt").write_text("Read me second.\n\nIt says goodbye now.")
    assert indexer.scan(root) == ["readme.txt"]
    pending = uds.get(TIM, "/folders/f-1/readme.txt", ALL)
    assert pending.data["state"] == "pending" and pending.version > first
    assert indexer.extract(root, "readme.txt") == "indexed"
    assert "goodbye" in uds.get(TIM, "/folders/f-1/readme.txt", ALL).content["text"]
    (folder / "readme.txt").unlink()
    assert indexer.scan(root) == []
    assert uds.get(TIM, "/folders/f-1/readme.txt") is None
    assert indexer.forget_root("/folders/f-1") == 1
    assert uds.count(TIM, under="/folders/f-1").items == 0


def test_unreadable_files_are_counted_with_their_reason(engine, folder):
    _, uds = engine
    indexer = kc.Indexer(uds)
    (folder / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0legacy word")
    root = _root(folder)
    _index_all(indexer, root)
    item = uds.get(TIM, "/folders/f-1/old.doc", ALL)
    assert item.data["state"] == "unreadable" and item.data["reason"] and item.content is None


def test_dropped_events_scan_their_subtree(engine, folder):
    _, uds = engine
    service = kc.ContentService(uds, roots=lambda: [_root(folder)], watch=False,
                                marks=_Marks(), clock=_Never())
    service.refresh_roots(start=True)
    service.backup_scan("start")
    _drain(service)
    (folder / "notes" / "new.md").write_text("# New\nWritten while events were dropped.")
    root_path = os.path.realpath(folder)
    service.handle([fs_watch.Event(os.path.join(root_path, "notes"), fs_watch.MUST_SCAN_SUBDIRS, 41)])
    _drain(service)
    assert uds.get(TIM, "/folders/f-1/notes/new.md", ALL).data["state"] == "indexed"
    assert service.marks.mark["event_id"] == 41
    # A folder event inside a folder the rules pass over scans nothing there.
    service.handle([fs_watch.Event(os.path.join(root_path, "node_modules", "x"),
                                   fs_watch.ITEM_CREATED | fs_watch.ITEM_IS_DIR, 42)])
    _drain(service)
    assert uds.get(TIM, "/folders/f-1/node_modules/x/readme.md") is None


def test_a_backup_scan_finds_what_changed_while_nothing_watched(engine, folder):
    _, uds = engine
    first = kc.ContentService(uds, roots=lambda: [_root(folder)], watch=False, marks=_Marks(), clock=_Once())
    first.refresh_roots(start=True)
    first.step(wait=0)
    _drain(first)
    assert uds.count(TIM, under="/folders/f-1").items == 2
    # Stopped: a file changed, one added, one deleted.
    (folder / "notes" / "plan.md").write_text("# Plan\nThe plan changed while JARVIS was away.")
    (folder / "added.md").write_text("# Added\nA new file.")
    (folder / "readme.txt").unlink()
    second = kc.ContentService(uds, roots=lambda: [_root(folder)], watch=False, marks=_Marks(), clock=_Once())
    second.refresh_roots(start=True)
    second.step(wait=0)
    _drain(second)
    assert "while JARVIS was away" in uds.get(TIM, "/folders/f-1/notes/plan.md", ALL).content["text"]
    assert uds.get(TIM, "/folders/f-1/added.md", ALL).data["state"] == "indexed"
    assert uds.get(TIM, "/folders/f-1/readme.txt") is None
    assert second.counters.last_scan_reason == "start"


def test_reading_waits_while_the_pace_holds(engine, folder):
    _, uds = engine
    service = kc.ContentService(uds, roots=lambda: [_root(folder)], watch=False, marks=_Marks(), clock=_Once())
    service.set_paused(True)
    service.start()
    try:
        assert _until(lambda: uds.count(TIM, under="/folders/f-1").items == 2)
        time.sleep(0.3)
        states = {i.data["state"] for i in uds.scan(TIM, "/folders/f-1", streams={Stream.DATA})}
        assert states == {"pending"}                     # the path entries went in; reading waits
        service.set_paused(False)
        assert _until(lambda: {i.data["state"] for i in uds.scan(TIM, "/folders/f-1", streams={Stream.DATA})}
                      == {"indexed"})
    finally:
        service.stop()


@macos
def test_the_watcher_sees_create_change_rename_and_delete(engine, folder, capsys):
    _, uds = engine
    service = kc.ContentService(uds, roots=lambda: [_root(folder)], marks=_Marks(), clock=_Once(),
                                latency=0.05)
    service.start()
    seen = {}
    try:
        assert _until(lambda: uds.count(TIM, under="/folders/f-1").embedded == 2)

        def timed(what, change, check):
            started = time.monotonic()
            change()
            assert _until(check), what
            seen[what] = time.monotonic() - started

        new = "/folders/f-1/notes/fresh.md"
        timed("create", lambda: (folder / "notes" / "fresh.md").write_text("# Fresh\nJust made."),
              lambda: (uds.get(TIM, new, {Stream.DATA}) or _Nothing).data.get("state") == "indexed")
        timed("change", lambda: (folder / "notes" / "fresh.md").write_text("# Fresh\nChanged words now."),
              lambda: "Changed words" in ((uds.get(TIM, new, ALL) or _Nothing).content or {}).get("text", ""))
        moved = "/folders/f-1/notes/moved.md"
        timed("rename", lambda: os.rename(folder / "notes" / "fresh.md", folder / "notes" / "moved.md"),
              lambda: uds.get(TIM, new) is None and
              (uds.get(TIM, moved, {Stream.DATA}) or _Nothing).data.get("state") == "indexed")
        timed("delete", lambda: (folder / "notes" / "moved.md").unlink(), lambda: uds.get(TIM, moved) is None)
        assert service.marks.mark["event_id"] > 0
    finally:
        service.stop()
    with capsys.disabled():
        print("\nwatch latency to the item written (s):",
              {k: round(v, 3) for k, v in seen.items()},
              "index ms a file:", service.health()["index_ms_per_file"])


@macos
def test_a_restart_replays_what_changed_from_the_last_event_handled(engine, folder):
    _, uds = engine
    marks = _Marks()
    first = kc.ContentService(uds, roots=lambda: [_root(folder)], marks=marks, clock=_Once(), latency=0.05)
    first.start()
    try:
        assert _until(lambda: uds.count(TIM, under="/folders/f-1").items == 2)
        (folder / "seen.md").write_text("# Seen\nWhile watched.")
        assert _until(lambda: uds.get(TIM, "/folders/f-1/seen.md") is not None)
        assert _until(lambda: marks.mark.get("event_id"))
    finally:
        first.stop()
    (folder / "away.md").write_text("# Away\nWritten while stopped.")
    second = kc.ContentService(uds, roots=lambda: [_root(folder)], marks=marks, clock=_Never(), latency=0.05)
    second.start()
    try:
        assert _until(lambda: (uds.get(TIM, "/folders/f-1/away.md", {Stream.DATA}) or _Nothing).data.get("state")
                      == "indexed")
        assert second.counters.scans == 0                   # found by the replay, not a scan
    finally:
        second.stop()


def test_scope_and_roots(monkeypatch, tmp_path):
    assert kc.scope_of([]) == "docs"
    assert kc.scope_of(["off", "docs"]) == "docs"
    assert kc.scope_of(["docs", "text"]) == "text"
    assert kc.scope_of(["off", "off"]) == "off"
    root = kc.Root("/folders/f", str(tmp_path), "docs", "own", "f")
    assert kc.item_path(root, "a/b.md") == "/folders/f/a/b.md"
    assert kc.item_path(root, "Makefile") is None
    assert kc.item_path(root, "café.md") == "/folders/f/café.md"

    class Service:
        def list_folders(self, cursor=None):
            def f(fid, reader="files", local=True, availability="available"):
                return {"id": fid, "access": {"reader": reader},
                        "location": {"local": local, "availability": availability, "canonical_path": str(tmp_path)}}
            return {"folders": [f("a"), f("b", reader="notes"), f("c", local=False), f("d", availability="missing"),
                                f("e"), f("off")], "next_cursor": None}
    found = kc.folder_roots(Service(), {"e": ["text"], "off": ["off"]})
    assert [(r.prefix, r.scope) for r in found] == [("/folders/a", "docs"), ("/folders/e", "text")]


class _Nothing:
    data: dict = {}
    content: dict = {}


class _Marks:
    def __init__(self) -> None:
        self.mark: dict = {}

    def read(self) -> dict:
        return dict(self.mark)

    def write(self, mark: dict) -> None:
        self.mark = dict(mark)


class _Never:
    def due(self, *_a):
        return None


class _Once:
    def __init__(self) -> None:
        self.done = False

    def due(self, *_a):
        if self.done:
            return None
        self.done = True
        return "start"


def _drain(service: kc.ContentService) -> None:
    while service.todo:
        key = next(iter(service.todo))
        root = service.todo.pop(key)
        service.indexer.extract(root, key[1])
