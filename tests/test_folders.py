"""The folder catalog: its record in the existing database (folder_store),
its reader policy (folder_policy), its bounded, race-resistant readers
(folder_readers), discovery from synthetic provider homes
(folder_discovery) and the one service over them (folder_service).

Every folder, transcript and provider home here is made under tmp_path; no
real history, path or session is read.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from jarvis import (data_paths, folder_discovery, folder_policy, folder_readers, history_read,
                    run_store)
from jarvis.folder_contract import FolderError, Reader, TEXT_MAX_LINES
from jarvis.folder_service import FolderService
from jarvis.folder_store import FolderStore

NODE = "node-a"


@pytest.fixture
def claude_root(tmp_path, monkeypatch):
    """A synthetic Claude Code config root, the only one JARVIS reads."""
    root = tmp_path / "claude-home"
    (root / "projects").mkdir(parents=True)
    (root / "sessions").mkdir()
    monkeypatch.setattr(history_read, "claude_roots", lambda: [root])
    return root


@pytest.fixture
def store():
    run_store.init_db()
    return FolderStore()


@pytest.fixture
def svc(store, claude_root):
    return FolderService(store, lambda: NODE)


def _tree(base: Path, files: dict) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return base


def _register(svc, path, key=None, **kw):
    return svc.register(request_key=key or f"k-{path}-{len(kw)}", path=str(path), **kw)


def _err(fn, *a, **kw) -> str:
    with pytest.raises(FolderError) as caught:
        fn(*a, **kw)
    return caught.value.code


# ── the record: migrations, persistence, idempotency ─────────────────────

def test_an_existing_database_keeps_its_rows_and_the_migration_is_repeatable():
    run_store.init_db()
    mid = run_store.create_message("s1", "anthropic", "hammer", "p", "hello")
    before = set(os.listdir(data_paths.data_dir()))
    a = FolderStore()
    b = FolderStore()                      # a second start: nothing to do, nothing lost
    assert a.path == b.path == data_paths.db_path()
    assert a.schema_version() == b.schema_version() == 1
    assert run_store.get_message(mid).text == "hello"
    # Only the existing database file (and SQLite's own WAL files) is used.
    added = set(os.listdir(data_paths.data_dir())) - before
    assert added <= {"jarvis.db-wal", "jarvis.db-shm"}
    tables = {r[0] for r in sqlite3.connect(data_paths.db_path()).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"folders", "folder_locations", "folder_access_scopes", "folder_candidates",
            "folder_observations", "folder_location_events", "folder_registrations"} <= tables
    assert not any("project" in t for t in tables - {"projects"}), "no project tables yet"


def test_a_newer_schema_is_left_alone(store):
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE folder_schema SET version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        FolderStore()


def test_a_folder_and_its_scope_survive_a_restart(svc, store, tmp_path):
    folder = _tree(tmp_path / "plans", {"a.md": "one"})
    made = _register(svc, folder)["folder"]
    del svc, store                          # every connection is per call: nothing held open
    again = FolderService(FolderStore(), lambda: NODE)
    got = again.detail(made["id"])["folder"]
    assert got["id"] == made["id"]
    assert got["location"]["canonical_path"] == os.path.realpath(folder)
    assert got["access"] == made["access"] and got["access"]["enabled"]
    assert [e["name"] for e in again.entries(made["id"])["entries"]] == ["a.md"]


def test_one_identity_per_location_and_request_keys_are_checked(svc, tmp_path):
    folder = _tree(tmp_path / "data" / "x", {"f.txt": "x"})
    first = svc.register(request_key="r1", path=str(folder))
    assert first["outcome"] == "created"
    assert svc.register(request_key="r1", path=str(folder))["outcome"] == "replayed"
    assert svc.register(request_key="r2", path=str(folder / ".." / "x"))["outcome"] == "existing"
    link = tmp_path / "alias"
    link.symlink_to(folder)
    alias = svc.register(request_key="r3", path=str(link))
    assert alias["outcome"] == "existing" and alias["folder"]["id"] == first["folder"]["id"]
    other = _tree(tmp_path / "elsewhere", {"g.txt": "g"})
    assert _err(svc.register, request_key="r1", path=str(other)) == "idempotency_conflict"


def test_equal_basenames_are_separate_folders(svc, tmp_path):
    a = _tree(tmp_path / "one" / "docs", {"a": "a"})
    b = _tree(tmp_path / "two" / "docs", {"b": "b"})
    ida = _register(svc, a)["folder"]["id"]
    idb = _register(svc, b)["folder"]["id"]
    assert ida != idb
    names = {f["id"]: f["display_name"] for f in svc.list_folders()["folders"]}
    assert names == {ida: "docs", idb: "docs"}


def test_a_case_alias_is_the_same_directory_where_the_disk_says_so(svc, tmp_path):
    folder = _tree(tmp_path / "Mixed", {"a": "a"})
    upper = tmp_path / "MIXED"
    if not upper.exists():
        pytest.skip("a case-sensitive volume: MIXED is another name")
    first = _register(svc, folder)["folder"]["id"]
    assert _register(svc, upper, key="other")["folder"]["id"] == first


def test_relative_missing_and_file_paths_are_refused(svc, tmp_path):
    (tmp_path / "file.txt").write_text("x")
    assert _err(svc.resolve, "relative/path") == "relative_path"
    assert _err(svc.resolve, "~someone/x") == "relative_path"
    assert _err(svc.resolve, str(tmp_path / "nope")) == "missing"
    assert _err(svc.resolve, str(tmp_path / "file.txt")) == "not_a_directory"


def test_a_stale_edit_is_refused_and_a_current_one_moves_the_revision(svc, tmp_path):
    f = _register(svc, _tree(tmp_path / "d", {"a": "a"}))["folder"]
    edited = svc.edit(f["id"], expected_revision=f["revision"], display_name="Plans",
                      role="source")["folder"]
    assert (edited["display_name"], edited["role"], edited["revision"]) == \
        ("Plans", "source", f["revision"] + 1)
    assert _err(svc.edit, f["id"], expected_revision=f["revision"], display_name="x") \
        == "stale_revision"
    assert _err(svc.edit, f["id"], expected_revision=edited["revision"], role="owner") \
        == "invalid_request"


# ── the reader policy ────────────────────────────────────────────────────

def test_a_named_hidden_subtree_opens_without_its_parent(svc, tmp_path):
    repo = _tree(tmp_path / "repo", {
        "README.md": "readme", ".env": "SECRET=1", "src/app.py": "print()",
        ".private-plans/in-progress/plan.md": "the plan",
        ".private-plans/other/keep-out.md": "no",
        ".private-plans/in-progress/.env": "SECRET=2",
        ".private-plans/in-progress/id_rsa": "key",
    })
    parent = _register(svc, repo)["folder"]
    top = {e["name"] for e in svc.entries(parent["id"])["entries"]}
    assert top == {"README.md", "src"}
    assert _err(svc.entries, parent["id"], path=".private-plans") == "forbidden"
    assert _err(svc.preview, parent["id"], path=".env") == "forbidden"

    plans = _register(svc, repo / ".private-plans" / "in-progress")["folder"]
    assert plans["access"]["reader"] == "files"
    listing = svc.entries(plans["id"])
    assert [e["name"] for e in listing["entries"]] == ["plan.md"]
    assert listing["withheld"] == 2
    assert svc.preview(plans["id"], path="plan.md")["lines"] == ["the plan"]
    assert _err(svc.preview, plans["id"], path="../other/keep-out.md") == "forbidden"
    assert _err(svc.preview, plans["id"], path=".env") == "forbidden"
    # The child's registration did not widen the parent.
    assert _err(svc.entries, parent["id"], path=".private-plans") == "forbidden"


def test_credential_locations_and_jarvis_data_cannot_be_registered(svc, tmp_path):
    ssh = _tree(tmp_path / "home" / ".ssh", {"config": "x"})
    secrets = _tree(tmp_path / "work" / "secrets", {"x": "x"})
    assert _err(svc.register, request_key="a", path=str(ssh)) == "forbidden"
    assert _err(svc.register, request_key="b", path=str(secrets)) == "forbidden"
    data_paths.brain_home().mkdir(parents=True, exist_ok=True)
    assert _err(svc.register, request_key="c", path=str(data_paths.data_dir())) == "forbidden"
    assert _err(svc.register, request_key="d", path=str(data_paths.brain_home())) == "forbidden"


def test_the_brain_s_journal_is_read_as_notes_only(svc):
    journal = data_paths.brain_home() / "journal"
    _tree(journal, {"2026-10-30.md": "today", "stray.json": "{}"})
    f = _register(svc, journal)["folder"]
    assert f["access"]["reader"] == Reader.NOTES
    assert [e["name"] for e in svc.entries(f["id"])["entries"]] == ["2026-10-30.md"]
    assert _err(svc.preview, f["id"], path="stray.json") == "forbidden"


def test_a_provider_home_is_read_only_through_its_history_reader(svc, claude_root, tmp_path):
    _tree(claude_root, {".credentials.json": "{}", "settings.json": "{}"})
    hist = claude_root / "projects" / "-tmp-example"
    _tree(hist, {"abc.jsonl": "", "memory/MEMORY.md": "notes", "tool.log": "x"})
    assert _err(svc.register, request_key="a", path=str(claude_root)) == "forbidden"
    assert _err(svc.register, request_key="b", path=str(claude_root / "projects")) == "forbidden"
    f = _register(svc, hist)["folder"]
    assert f["access"]["reader"] == Reader.CLAUDE_HISTORY and f["role"] == "history"
    names = {e["name"] for e in svc.entries(f["id"])["entries"]}
    assert names == {"abc.jsonl", "memory"}
    # An ordinary folder holding the provider home does not open it.
    outer = _register(svc, claude_root.parent)["folder"]
    assert "claude-home" not in {e["name"] for e in svc.entries(outer["id"])["entries"]}


def test_a_codex_home_opens_only_its_sessions(svc, tmp_path):
    home = Path(os.environ["JARVIS_CODEX_SESSIONS_HOME"])
    _tree(home, {"auth.json": "{}", "sessions/2026/10/30/rollout-x.jsonl": "", "config.toml": ""})
    assert _err(svc.register, request_key="a", path=str(home)) == "forbidden"
    f = _register(svc, home / "sessions")["folder"]
    assert f["access"]["reader"] == Reader.CODEX_HISTORY


# ── path attacks ─────────────────────────────────────────────────────────

@pytest.fixture
def boxed(svc, tmp_path):
    outside = _tree(tmp_path / "outside", {"secret.txt": "OUTSIDE"})
    folder = _tree(tmp_path / "box", {"ok.txt": "inside", "sub/deep.txt": "deep"})
    (folder / "link.txt").symlink_to(outside / "secret.txt")
    (folder / "linkdir").symlink_to(outside)
    return _register(svc, folder)["folder"], folder, outside


def test_traversal_and_absolute_paths_are_refused(svc, boxed):
    f, _folder, outside = boxed
    for path in ("../outside/secret.txt", "sub/../../outside/secret.txt",
                 str(outside / "secret.txt"), "sub/\x00x"):
        assert _err(svc.preview, f["id"], path=path) == "forbidden", path


def test_a_symlink_is_listed_and_never_followed(svc, boxed):
    f, _folder, _outside = boxed
    kinds = {e["name"]: e["kind"] for e in svc.entries(f["id"])["entries"]}
    assert kinds["link.txt"] == "link" and kinds["linkdir"] == "link"
    assert _err(svc.preview, f["id"], path="link.txt") == "forbidden"
    assert _err(svc.entries, f["id"], path="linkdir") == "forbidden"
    assert _err(svc.preview, f["id"], path="linkdir/secret.txt") == "forbidden"


def test_a_file_swapped_for_a_symlink_mid_read_is_not_followed(svc, boxed, monkeypatch):
    f, folder, outside = boxed
    real = folder_policy.entry_visible

    def swap_after_the_check(policy, root, rel, is_dir):
        ok = real(policy, root, rel, is_dir)
        if rel.name == "ok.txt":            # checked: now replace it before it is opened
            (folder / "ok.txt").unlink()
            (folder / "ok.txt").symlink_to(outside / "secret.txt")
        return ok

    monkeypatch.setattr(folder_policy, "entry_visible", swap_after_the_check)
    monkeypatch.setattr(folder_readers, "kind_of", lambda root, rel: "file")
    assert _err(svc.preview, f["id"], path="ok.txt") == "forbidden"


def test_a_directory_swapped_for_a_symlink_mid_walk_is_not_followed(boxed, tmp_path):
    _f, folder, outside = boxed
    (folder / "sub").rename(tmp_path / "moved-sub")
    (folder / "sub").symlink_to(outside)
    with pytest.raises(FolderError) as caught:
        folder_readers.read_text(Path(os.path.realpath(folder)), Path("sub/secret.txt"))
    assert caught.value.code == "forbidden"


# ── large and changing folders ───────────────────────────────────────────

def test_a_big_directory_pages_by_key_without_repeats(svc, tmp_path):
    folder = tmp_path / "many"
    folder.mkdir()
    for i in range(250):
        (folder / f"f{i:03}.txt").write_text("x")
    (folder / "zdir").mkdir()
    f = _register(svc, folder)["folder"]
    seen, cursor, pages = [], None, 0
    while True:
        page = svc.entries(f["id"], cursor=cursor)
        pages += 1
        seen += [e["name"] for e in page["entries"]]
        assert len(page["entries"]) <= 100 and not page["partial"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 251
    assert seen[0] == "zdir", "folders first"


def test_a_scan_past_its_budget_is_marked_partial(tmp_path):
    folder = tmp_path / "many"
    folder.mkdir()
    for i in range(30):
        (folder / f"{i}.txt").write_text("x")
    listing = folder_readers.list_dir(folder, Path(), lambda r, d: True, scan_max=10)
    assert listing.partial and listing.scanned == 10 and len(listing.entries) == 10


def test_a_long_file_is_read_in_bounded_windows(svc, tmp_path):
    folder = _tree(tmp_path / "logs", {})
    (folder / "big.log").write_text("".join(f"line {i}\n" for i in range(1, 501)))
    f = _register(svc, folder)["folder"]
    first = svc.preview(f["id"], path="big.log")
    assert len(first["lines"]) == TEXT_MAX_LINES and first["partial"]
    assert first["lines"][0] == "line 1" and first["first_line"] == 1
    second = svc.preview(f["id"], path="big.log", cursor=first["next_cursor"])
    assert second["first_line"] == 201 and second["lines"][0] == "line 201"


def test_a_file_over_four_mebibytes_is_still_read_a_window_at_a_time(svc, tmp_path):
    folder = _tree(tmp_path / "huge", {})
    with open(folder / "huge.txt", "w") as out:
        for i in range(400_000):
            out.write(f"row {i:07}\n")
    assert (folder / "huge.txt").stat().st_size > 4 * 1024 * 1024
    f = _register(svc, folder)["folder"]
    window = svc.preview(f["id"], path="huge.txt")
    assert len(window["lines"]) == TEXT_MAX_LINES and window["next_cursor"]


def test_binary_and_html_files(svc, tmp_path):
    folder = _tree(tmp_path / "media", {"page.html": "<script>alert(1)</script>"})
    (folder / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    f = _register(svc, folder)["folder"]
    pic = svc.preview(f["id"], path="pic.png")
    assert pic["kind"] == "binary" and pic["lines"] == [] and pic["size"] == 10
    page = svc.preview(f["id"], path="page.html")
    assert page["kind"] == "text" and page["lines"] == ["<script>alert(1)</script>"]


def test_a_cursor_from_before_a_reconnect_is_refused(svc, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    for i in range(150):
        (a / f"{i:03}").write_text("x")
    f = _register(svc, a)["folder"]
    page = svc.entries(f["id"])
    b = _tree(tmp_path / "b", {"other": "x"})
    moved = svc.reconnect(f["id"], expected_revision=f["revision"], path=str(b),
                          replace=True)["folder"]
    assert moved["id"] == f["id"] and moved["location"]["revision"] == 2
    assert _err(svc.entries, f["id"], cursor=page["next_cursor"]) == "stale_cursor"
    assert _err(svc.entries, f["id"], location_revision=1) == "stale_cursor"
    assert [e["name"] for e in svc.entries(f["id"], location_revision=2)["entries"]] == ["other"]


# ── availability, unregister, reconnect ──────────────────────────────────

def test_a_folder_that_disappears_stays_in_the_catalog_as_missing(svc, tmp_path):
    folder = _tree(tmp_path / "gone", {"a": "a"})
    f = _register(svc, folder)["folder"]
    (folder / "a").unlink()
    folder.rmdir()
    detail = svc.refresh(f["id"])
    assert detail["folder"]["location"]["availability"] == "missing"
    assert detail["folder"]["location"]["last_seen_at"] is not None
    assert detail["events"][0]["event"] == "availability"
    assert _err(svc.entries, f["id"]) == "missing"
    assert svc.list_folders(availability="missing")["folders"][0]["id"] == f["id"]


@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else True,
                    reason="root reads anything")
def test_a_folder_this_user_cannot_read_is_access_denied(svc, tmp_path):
    folder = _tree(tmp_path / "locked", {"a": "a"})
    f = _register(svc, folder)["folder"]
    folder.chmod(0)
    try:
        assert svc.refresh(f["id"])["folder"]["location"]["availability"] == "access_denied"
        assert _err(svc.entries, f["id"]) == "access_denied"
    finally:
        folder.chmod(0o755)


def test_another_node_s_folder_is_never_read_from_here(store, tmp_path, claude_root):
    folder = _tree(tmp_path / "theirs", {"a": "a"})
    made = FolderService(store, lambda: "node-b").register(request_key="k", path=str(folder))
    here = FolderService(store, lambda: NODE)
    assert _err(here.entries, made["folder"]["id"]) == "unavailable"
    here.check_all()
    assert here.detail(made["folder"]["id"])["folder"]["location"]["availability"] \
        in ("available",)   # check_all only checks this node's own
    here.refresh(made["folder"]["id"])
    assert here.detail(made["folder"]["id"])["folder"]["location"]["availability"] \
        == "unavailable_node"


def test_unregister_leaves_files_and_needs_an_explicit_reactivation(svc, tmp_path):
    folder = _tree(tmp_path / "keep", {"a.txt": "still here"})
    f = _register(svc, folder)["folder"]
    removed = svc.unregister(f["id"], expected_revision=f["revision"])["folder"]
    assert removed["lifecycle"] == "removed" and not removed["access"]["enabled"]
    assert (folder / "a.txt").read_text() == "still here"
    assert _err(svc.entries, f["id"]) == "removed"
    assert svc.list_folders()["folders"] == []
    assert svc.list_folders(lifecycle="removed")["folders"][0]["id"] == f["id"]
    assert _err(svc.register, request_key="again", path=str(folder)) == "removed_exists"
    back = svc.register(request_key="again2", path=str(folder), reactivate=True)
    assert back["outcome"] == "reactivated" and back["folder"]["id"] == f["id"]
    assert svc.preview(f["id"], path="a.txt")["lines"] == ["still here"]
    events = [e["event"] for e in svc.detail(f["id"])["events"]]
    assert events[:3] == ["reactivated", "unregistered", "registered"]


def test_work_runs_only_in_an_available_ordinary_folder(svc, claude_root, tmp_path):
    """Where spawn_run may work: the same checks as a read, and a files
    reader only. History and notes folders are read-only."""
    plain = _tree(tmp_path / "denice", {"a.txt": "a"})
    f = _register(svc, plain)["folder"]
    got, root = svc.work_location(f["id"])
    assert got.id == f["id"] and root == Path(os.path.realpath(plain))

    journal = _tree(data_paths.brain_home() / "journal", {"x.md": "x"})
    notes = _register(svc, journal)["folder"]
    assert _err(svc.work_location, notes["id"]) == "unsupported"
    hist = _tree(claude_root / "projects" / "-tmp-x", {"s.jsonl": ""})
    history = _register(svc, hist)["folder"]
    assert _err(svc.work_location, history["id"]) == "unsupported"

    assert _err(svc.work_location, "no-such-folder") == "not_found"
    svc.unregister(f["id"])
    assert _err(svc.work_location, f["id"]) == "removed"
    gone = _tree(tmp_path / "gone-soon", {"a": "a"})
    g = _register(svc, gone)["folder"]
    (gone / "a").unlink()
    gone.rmdir()
    assert _err(svc.work_location, g["id"]) == "missing"


def test_reconnect_needs_confirmation_for_a_different_directory(svc, tmp_path):
    first = _tree(tmp_path / "v1", {"a": "a"})
    f = _register(svc, first)["folder"]
    renamed = tmp_path / "v1-renamed"
    first.rename(renamed)                  # the same directory, moved: no confirmation
    moved = svc.reconnect(f["id"], expected_revision=f["revision"], path=str(renamed))["folder"]
    other = _tree(tmp_path / "v2", {"b": "b"})
    assert _err(svc.reconnect, f["id"], expected_revision=moved["revision"],
                path=str(other)) == "identity_mismatch"
    assert _err(svc.reconnect, f["id"], expected_revision=f["revision"],
                path=str(other), replace=True) == "stale_revision"
    taken = _register(svc, _tree(tmp_path / "taken", {"c": "c"}))["folder"]
    assert _err(svc.reconnect, f["id"], expected_revision=moved["revision"],
                path=taken["location"]["canonical_path"], replace=True) == "path_registered"
    event = svc.detail(f["id"])["events"][0]
    assert event["event"] == "reconnected"
    assert event["prior"]["canonical_path"] == os.path.realpath(first)


# ── discovery, from synthetic homes ──────────────────────────────────────

def _transcript(path: Path, session_id: str, cwd: str | None, said: str = "hi") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "summary", "summary": "x"}]
    if cwd is not None:
        lines.append({"type": "user", "sessionId": session_id, "cwd": cwd,
                      "timestamp": "2026-10-30T10:00:00Z",
                      "message": {"role": "user", "content": said}})
    lines.append({"type": "assistant", "sessionId": session_id,
                  "timestamp": "2026-10-30T10:00:05Z",
                  "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def test_discovery_reads_cwd_from_records_never_from_folder_names(svc, store, claude_root,
                                                                    tmp_path):
    work = _tree(tmp_path / "work" / "alpha", {})
    hist = claude_root / "projects" / "an-unrelated-name"
    _transcript(hist / "s1.jsonl", "s1", str(work))
    # A history folder whose name encodes a path, with no cwd recorded: it
    # stays unresolved rather than decoded into a guess.
    _transcript(claude_root / "projects" / "-tmp-guess-me" / "s2.jsonl", "s2", None)
    report = folder_discovery.discover(store, NODE)
    paths = {c["path"] for c in svc.candidates()["candidates"]}
    assert os.path.realpath(work) in paths
    assert os.path.realpath(hist) in paths
    assert not any("guess" in p and "projects" not in p for p in paths)
    assert "/tmp/guess/me" not in paths
    assert report.changed >= 2


def test_a_session_s_provenance_outlives_its_process(svc, store, claude_root, tmp_path):
    work = _tree(tmp_path / "work" / "beta", {})
    roster = claude_root / "sessions" / "123.json"
    roster.write_text(json.dumps({"pid": 123, "sessionId": "live-1", "cwd": str(work)}))
    hist = claude_root / "projects" / session_watch_encode(str(work))
    _transcript(hist / "live-1.jsonl", "live-1", str(work))
    folder_discovery.discover(store, NODE)
    f = _register(svc, work)["folder"]
    first = svc.detail(f["id"])["sessions"]
    assert [s["session_id"] for s in first] == ["live-1"] and first[0]["live"]
    roster.unlink()                         # the process ended
    folder_discovery.discover(store, NODE)
    kept = svc.detail(f["id"])["sessions"]
    assert [s["session_id"] for s in kept] == ["live-1"]
    assert kept[0]["history_file"].endswith("live-1.jsonl")


def session_watch_encode(cwd: str) -> str:
    from jarvis.session_watch import encode_cwd
    return encode_cwd(cwd)


def test_a_malformed_or_half_written_transcript_is_read_again_later(svc, store, claude_root,
                                                                    tmp_path):
    work = _tree(tmp_path / "work" / "gamma", {})
    path = claude_root / "projects" / "g" / "s3.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type": "user", "cwd": ')            # caught mid-write
    folder_discovery.discover(store, NODE)
    assert os.path.realpath(work) not in {c["path"] for c in svc.candidates()["candidates"]}
    _transcript(path, "s3", str(work))                     # it finished writing
    folder_discovery.discover(store, NODE)
    assert os.path.realpath(work) in {c["path"] for c in svc.candidates()["candidates"]}


def test_two_profiles_observe_one_workspace(svc, store, claude_root, tmp_path, monkeypatch):
    second = tmp_path / "claude-other"
    (second / "projects").mkdir(parents=True)
    monkeypatch.setattr(history_read, "claude_roots", lambda: [claude_root, second])
    work = _tree(tmp_path / "work" / "delta", {})
    _transcript(claude_root / "projects" / "a" / "s4.jsonl", "s4", str(work))
    _transcript(second / "projects" / "b" / "s5.jsonl", "s5", str(work))
    folder_discovery.discover(store, NODE)
    cand = [c for c in svc.candidates()["candidates"] if c["path"] == os.path.realpath(work)]
    assert len(cand) == 1 and cand[0]["sessions"] == 2
    f = _register(svc, work)["folder"]
    roots = {s["profile_root"] for s in svc.detail(f["id"])["sessions"]}
    assert roots == {str(claude_root), str(second)}


def test_the_history_folder_is_its_own_candidate_with_memory_and_subagents(svc, store,
                                                                           claude_root, tmp_path):
    work = _tree(tmp_path / "work" / "eps", {})
    hist = claude_root / "projects" / "eps"
    _transcript(hist / "s6.jsonl", "s6", str(work))
    _tree(hist, {"memory/MEMORY.md": "m"})
    _transcript(hist / "s6" / "subagents" / "agent-1.jsonl", "s6", str(work))
    folder_discovery.discover(store, NODE)
    by_path = {c["path"]: c for c in svc.candidates()["candidates"]}
    assert by_path[os.path.realpath(hist)]["proposed_role"] == "history"
    f = _register(svc, hist)["folder"]
    assert f["display_name"] == "eps history"
    session = svc.detail(f["id"])["sessions"][0]
    assert session["relation"] == "history"
    assert session["associated"]["subagents"] == 1 and session["associated"]["memory"]


def test_ignore_persists_until_the_evidence_changes(svc, store, claude_root, tmp_path):
    work = _tree(tmp_path / "work" / "zeta", {})
    _transcript(claude_root / "projects" / "z" / "s7.jsonl", "s7", str(work))
    folder_discovery.discover(store, NODE)
    cand = next(c for c in svc.candidates()["candidates"] if c["path"] == os.path.realpath(work))
    svc.candidate_action(cand["id"], "ignore")
    folder_discovery.discover(store, NODE)
    assert cand["id"] not in {c["id"] for c in svc.candidates()["candidates"]}
    assert cand["id"] in {c["id"] for c in svc.candidates(state="ignored")["candidates"]}
    # Codex now works there too: new evidence brings it back.
    _codex_db(Path(os.environ["JARVIS_CODEX_SESSIONS_HOME"]), [("t1", str(work))])
    folder_discovery.discover(store, NODE)
    assert cand["id"] in {c["id"] for c in svc.candidates()["candidates"]}
    svc.candidate_action(cand["id"], "ignore")
    svc.candidate_action(cand["id"], "restore")
    assert cand["id"] in {c["id"] for c in svc.candidates()["candidates"]}


def _codex_db(home: Path, threads: list[tuple[str, str]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    rollout_dir = home / "sessions" / "2026" / "10" / "30"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "state_5.sqlite") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT, rollout_path TEXT, cwd TEXT, "
                     "title TEXT, git_branch TEXT, created_at_ms INTEGER, updated_at_ms INTEGER, "
                     "archived INTEGER)")
        for i, (tid, cwd) in enumerate(threads):
            rollout = rollout_dir / f"rollout-2026-10-30T10-00-00-{tid}.jsonl"
            rollout.write_text("")
            conn.execute("INSERT INTO threads VALUES (?, ?, ?, 't', 'main', ?, ?, 0)",
                         (tid, str(rollout), cwd, 1000 + i, 2000 + i))


def test_codex_threads_are_discovered_from_the_user_s_home_only(svc, store, claude_root,
                                                                tmp_path):
    work = _tree(tmp_path / "work" / "eta", {})
    _codex_db(Path(os.environ["JARVIS_CODEX_SESSIONS_HOME"]), [("t9", str(work))])
    folder_discovery.discover(store, NODE)
    paths = {c["path"] for c in svc.candidates()["candidates"]}
    assert os.path.realpath(work) in paths
    sessions = os.path.realpath(Path(os.environ["JARVIS_CODEX_SESSIONS_HOME"]) / "sessions")
    assert sessions in paths
    f = _register(svc, work)["folder"]
    s = svc.detail(f["id"])["sessions"][0]
    assert (s["provider"], s["session_id"], s["evidence"]) == ("openai", "t9", "codex_thread")


def test_discovery_is_bounded_and_reports_its_coverage(svc, store, claude_root, tmp_path,
                                                       monkeypatch):
    work = _tree(tmp_path / "work" / "theta", {})
    for i in range(5):
        _transcript(claude_root / "projects" / "t" / f"s{i}.jsonl", f"s{i}", str(work))
    monkeypatch.setattr(folder_discovery, "DISCOVERY_FILES_PER_PASS", 2)
    folder_discovery.discover(store, NODE)
    cov = svc.coverage()
    claude = next(s for s in cov["sources"] if s["source"].startswith("claude:"))
    assert claude["read"] == 2 and claude["pending"] == 3 and not claude["complete"]
    for _ in range(3):
        folder_discovery.discover(store, NODE)
    claude = next(s for s in svc.coverage()["sources"] if s["source"].startswith("claude:"))
    assert claude["pending"] == 0 and claude["complete"]


def test_discovery_never_offers_the_brain_s_own_home(svc, store, claude_root):
    brain = data_paths.brain_home()
    brain.mkdir(parents=True, exist_ok=True)
    _transcript(claude_root / "projects" / "brain" / "b1.jsonl", "b1", str(brain))
    folder_discovery.discover(store, NODE)
    assert os.path.realpath(brain) not in {c["path"] for c in svc.candidates()["candidates"]}


# ── transcripts through their reader ─────────────────────────────────────

def test_a_transcript_is_shown_as_what_was_said_with_its_session(svc, claude_root, tmp_path):
    hist = claude_root / "projects" / "said"
    sid = "0b7c6a8e-1111-4222-8333-944455556666"
    path = _transcript(hist / f"{sid}.jsonl", sid, str(tmp_path), said="please read this")
    with open(path, "a") as out:
        out.write("not json\n")
        out.write(json.dumps({"type": "user", "isMeta": True,
                              "message": {"role": "user", "content": "meta"}}) + "\n")
    f = _register(svc, hist)["folder"]
    window = svc.preview(f["id"], path=f"{sid}.jsonl")
    assert window["kind"] == "transcript"
    assert window["lines"] == ["[2026-10-30 10:00] user: please read this",
                               "[2026-10-30 10:00] assistant: ok"]
    assert window["skipped_records"] == 1
    assert window["sessions"] == [{"provider": "anthropic", "session_id": sid,
                                   "transcript": f"{sid}.jsonl"}]


def test_a_large_transcript_is_read_a_window_at_a_time(svc, claude_root, tmp_path):
    hist = claude_root / "projects" / "long"
    hist.mkdir(parents=True)
    with open(hist / "s.jsonl", "w") as out:
        for i in range(600):
            out.write(json.dumps({"type": "user", "timestamp": "2026-10-30T10:00:00Z",
                                  "message": {"role": "user", "content": f"message {i}"}}) + "\n")
    f = _register(svc, hist)["folder"]
    first = svc.preview(f["id"], path="s.jsonl")
    assert len(first["lines"]) == TEXT_MAX_LINES and first["next_cursor"]
    second = svc.preview(f["id"], path="s.jsonl", cursor=first["next_cursor"])
    assert second["lines"][0].endswith("message 200")
