"""Workspace identity and references (design section 4): the one parser and
printer, the path rule, the hashes, the resolver, and the node ID's backup."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import uuid

import pytest

from jarvis import data_paths, folder_service, folder_store, run_store
from jarvis import project_service as ps
from jarvis import project_store as pstore
from jarvis import workspace_ref as w


# --- the text form ----------------------------------------------------------

def test_format_and_parse_are_each_others_inverse():
    r = w.ref("folder_file", "f-1", "docs/A b é.md", revision=3, sha256="a" * 64)
    text = w.format(r)
    assert text.startswith(f"jref:1:{data_paths.node_id()}:folder_file:f-1@3/docs/A%20b%20")
    assert text.endswith("#sha256=" + "a" * 64)
    assert w.parse(text) == r
    tree = w.ref("folder_tree", "f-1", sha256="b" * 64, tree=True)
    assert w.format(tree).endswith("#tree=" + "b" * 64) and w.parse(w.format(tree)).tree


@pytest.mark.parametrize("bad", [
    "jref:2:n:topic:x",                      # a version this JARVIS does not read
    "jref:1:n:nope:x",                       # no such kind
    "jref:1:n:note:memory/../x",             # climbs out
    "jref:1:n:note:memory//x",               # an empty part
    "jref:1:n:note:memory/./x",
    "jref:1:n:note:memory/a%5Cb",            # a backslash
    "jref:1:n:topic:x#sha256=ABC",
    "jref:1:n:topic:x/",
    "jref:1:n:topic:x@01",                   # a second spelling of revision 1
    "file:///etc/passwd",
])
def test_anything_else_is_refused(bad):
    with pytest.raises(w.RefError):
        w.parse(bad)


def test_the_path_rule_is_bundle_archives_and_canonical():
    assert w.check_path("a/b.md") == "a/b.md"
    composed = "é.md"
    with pytest.raises(w.RefError):
        w.check_path(composed + "/")
    assert w.check_path(composed) == "é.md"
    for bad in ("/abs", "a\\b", "../x", "", "a\x01b", "x" * 1025):
        with pytest.raises(w.RefError):
            w.check_path(bad)


def test_owner_of_converts_a_github_id():
    assert w.owner_of(42) == "github:42" and w.owner_of(None) == "local" and w.owner_of(True) == "local"


# --- hashes -------------------------------------------------------------------

def test_a_records_hash_leaves_out_its_bookkeeping():
    a = {"id": "p", "display_name": "X", "revision": 1, "updated_at": 5}
    b = {"id": "p", "display_name": "X", "revision": 9, "updated_at": 7}
    assert w.record_hash(a) == w.record_hash(b)
    assert w.canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert w.record_hash(a) != w.record_hash(dict(a, display_name="Y"))


@pytest.fixture
def folders(tmp_path, monkeypatch):
    run_store.init_db()
    monkeypatch.setattr(w, "_git", False)           # no git: the tree is walked as a plain folder
    svc = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / "README.md").write_text("# hi\n")
    (root / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(root / "run.sh", 0o755)
    (root / "link").symlink_to(root / "README.md")
    f = svc.register(request_key="k", path=str(root), role="source")["folder"]
    return svc, f, root


def test_a_tree_hash_is_the_merkle_hash_of_its_entries(folders):
    svc, f, root = folders
    got = svc.tree_hash(f["id"])

    def h(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()
    src = h(w.tree_line("f", h(b"a = 1\n"), "a.py"))
    expected = h(w.tree_line("f", h(b"# hi\n"), "README.md") + w.tree_line("x", h(b"#!/bin/sh\n"), "run.sh")
                 + w.tree_line("d", src, "src"))
    assert got["tree"] == expected and got["files"] == 3
    assert {"path": "link", "reason": "link"} in got["left_out"]
    assert got["git"] is None

    (root / "src" / "a.py").write_text("a = 2\n")
    assert svc.tree_hash(f["id"])["tree"] != expected


def test_a_file_hash_is_its_bytes_read_through_one_descriptor(folders):
    svc, f, _root = folders
    got = svc.file_hash(f["id"], "src/a.py")
    assert got["sha256"] == hashlib.sha256(b"a = 1\n").hexdigest() and got["size"] == 6
    from jarvis.folder_contract import FolderError
    with pytest.raises(FolderError):
        svc.file_hash(f["id"], "link")                # a link is never followed


# --- resolving ----------------------------------------------------------------

def test_a_folder_reference_is_here_changed_moved_missing_or_foreign(folders, tmp_path):
    svc, f, root = folders
    via = w.Resolver(folders=lambda: svc)
    pinned = w.ref("folder_file", f["id"], "src/a.py", revision=f["location"]["revision"],
                   sha256=hashlib.sha256(b"a = 1\n").hexdigest())
    assert w.resolve(pinned, via).outcome == w.Outcome.HERE
    (root / "src" / "a.py").write_text("changed\n")
    assert w.resolve(pinned, via).outcome == w.Outcome.CHANGED
    assert w.resolve(w.ref("folder_file", f["id"], "nope.md"), via).outcome == w.Outcome.MISSING

    elsewhere = tmp_path / "moved"
    os.rename(root, elsewhere)
    svc.reconnect(f["id"], expected_revision=f["revision"], path=str(elsewhere))
    unpinned = w.ref("folder_file", f["id"], "README.md", revision=f["location"]["revision"])
    assert w.resolve(unpinned, via).outcome == w.Outcome.MOVED

    foreign = w.WorkspaceRef("another-node", w.RefKind.FOLDER_FILE, f["id"], path="README.md")
    assert w.resolve(foreign, via).outcome == w.Outcome.FOREIGN_NODE
    assert w.resolve(w.ref("attachment", "m1"), via).outcome == w.Outcome.UNSUPPORTED


def test_records_resolve_through_their_stores(folders):
    svc, _f, _root = folders
    store = pstore.ProjectStore()
    service = ps.ProjectService(store, lambda: svc)
    p = store.create("local", "Steps")
    via = w.Resolver(folders=lambda: svc, projects=lambda: service)
    pinned = w.ref("project", p.id, sha256=w.record_hash(p.json()))
    assert w.resolve(pinned, via).outcome == w.Outcome.HERE
    store.change(p.id, summary="now with a summary")
    assert w.resolve(pinned, via).outcome == w.Outcome.CHANGED
    assert w.resolve(w.ref("project", str(uuid.uuid4())), via).outcome == w.Outcome.MISSING
    assert pstore.subject("project", p.id) == w.format(w.ref("project", p.id))


def test_a_note_resolves_under_brain_home_only():
    from jarvis import jarvis_memory
    jarvis_memory.write_project_note("steps", "a note")
    via = w.Resolver()
    assert w.resolve(w.ref("note", "projects", "steps.md"), via).outcome == w.Outcome.HERE
    assert w.resolve(w.ref("note", "secrets", "x.md"), via).outcome == w.Outcome.REFUSED


# --- the node ID ----------------------------------------------------------------

@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(data_paths)
    yield
    importlib.reload(data_paths)


def _forget():
    data_paths._node_id = None


def test_the_node_id_is_backed_up_once_and_restored_before_a_new_one_is_made(fresh):
    first = data_paths.node_id()
    assert data_paths.node_id_backup_path().read_text().strip() == first
    data_paths.node_id_path().unlink()
    _forget()
    assert data_paths.node_id() == first
    assert data_paths.node_id_path().read_text().strip() == first
    assert data_paths.node_id_minted() is None


def test_a_node_id_made_anyway_is_recorded_and_the_old_one_kept(fresh):
    first = data_paths.node_id()
    data_paths.node_id_path().write_text(first + " half a write")
    data_paths.node_id_backup_path().write_text("nonsense")
    _forget()
    made = data_paths.node_id()
    assert made != first
    minted = data_paths.node_id_minted()
    assert minted["node_id"] == made and minted["previous"] == first
    assert data_paths.previous_node_ids() == [first]
    assert w.is_self(first) and w.is_self(made) and not w.is_self(str(uuid.uuid4()))
    assert json.loads(data_paths.node_id_minted_path().read_text())["why"]


def test_a_first_start_records_nothing(fresh):
    data_paths.node_id()
    assert data_paths.node_id_minted() is None and data_paths.previous_node_ids() == []


def test_diagnostics_shows_a_node_id_made_anyway():
    import time as _time
    from jarvis import subsystem_diagnostics as sd
    row = sd.team_server({}, _time.time(), {"at": _time.time() - 60, "node_id": "x", "previous": None})
    node = next(c for c in row.checks if c.id == "node_id")
    assert node.state == sd.DEGRADED and "new node ID" in node.detail
    old = sd.team_server({}, _time.time(), {"at": _time.time() - sd.NODE_ID_NOTICE_SEC - 1})
    assert next(c for c in old.checks if c.id == "node_id").state == sd.IDLE


def test_git_ignored_entries_are_summarised_and_submodules_and_deletions_listed(folders, monkeypatch):
    svc, f, root = folders
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "i.js").write_text("1")
    (root / "vendor").mkdir()
    answers = {
        ("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"): b"node_modules/\0",
        ("ls-files", "-z", "--stage"): b"160000 abc 0\tvendor\x00100644 def 0\tREADME.md\x00",
        ("ls-files", "-z", "--deleted"): b"gone.md\0",
        ("rev-list", "--max-parents=0", "HEAD"): b"r00t\n",
        ("rev-parse", "HEAD"): b"h3ad\n",
        ("status", "--porcelain", "--untracked-files=no"): b"",
    }
    monkeypatch.setattr(w, "_git_out", lambda _root, *args: answers.get(args))
    without = svc.tree_hash(f["id"])
    (root / "node_modules" / "x" / "i.js").write_text("2")
    got = svc.tree_hash(f["id"])
    assert got["tree"] == without["tree"]                 # an ignored change changes nothing
    assert got["ignored"] == {"count": 1, "names": ["node_modules"], "truncated": False}
    assert {"path": "vendor", "reason": "special_file"} in got["left_out"]
    assert {"path": "gone.md", "reason": "missing"} in got["left_out"]
    assert got["git"] == {"root_commit": "r00t", "head": "h3ad", "dirty": False}
