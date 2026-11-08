"""Projects, pieces of work and accomplishments (phase 1): the records of
truth, candidates as proposals (I9), the service, and page and brain
parity."""
from __future__ import annotations

import json
import sqlite3

import pytest

from jarvis import data_paths, folder_service, folder_store, run_store
from jarvis import project_service as ps
from jarvis import project_store as pstore
from jarvis.project_store import ProjectError
from tests.test_brain_page_parity import _call, wired  # noqa: F401

ORIGIN = {"Origin": "http://localhost:8340", "Host": "localhost:8340"}


@pytest.fixture
def stores(tmp_path):
    run_store.init_db()
    folders = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
    records = pstore.ProjectStore()
    service = ps.ProjectService(records, lambda: folders)
    return records, folders, service


def register(folders, path, name=None, key=None):
    path.mkdir(parents=True, exist_ok=True)
    return folders.register(request_key=key or str(path), path=str(path), display_name=name,
                            role="source")["folder"]["id"]


# --- the store ---------------------------------------------------------------

def test_names_are_unique_per_owner_by_spoken_key_until_archived(stores):
    records, _, _ = stores
    p = records.create("local", "Step Checker")
    with pytest.raises(ProjectError) as e:
        records.create("local", "step-checker")
    assert e.value.code == "name_taken"
    assert records.create("github:2", "Step Checker").owner_id == "github:2"
    records.change(p.id, lifecycle="archived")
    assert records.create("local", "step checker").spoken_key == "step checker"


def test_every_change_moves_the_revision_and_writes_its_event(stores):
    records, _, _ = stores
    p = records.create("local", "Orbs", summary="the brain orbs")
    q = records.change(p.id, name="Brain Orbs", expected_revision=1)
    assert q.revision == 2 and q.spoken_key == "brain orbs"
    with pytest.raises(ProjectError) as e:
        records.change(p.id, summary="x", expected_revision=1)
    assert e.value.code == "stale_revision"
    actions = [ev["action"] for ev in records.events(record=("project", p.id))]
    assert actions == ["created", "renamed"]


def test_folders_one_home_and_unlink(stores, tmp_path):
    records, folders, _ = stores
    p = records.create("local", "Jarvis")
    a, b = register(folders, tmp_path / "a"), register(folders, tmp_path / "b")
    records.link_folder(p.id, a, home=True)
    records.link_folder(p.id, b, part="docs", home=True)
    homes = [f.folder_id for f in records.folders_of(p.id) if f.is_home]
    assert homes == [b]
    with pytest.raises(ProjectError):
        records.link_folder(p.id, a)
    records.change_folder(p.id, b, unlink=True)
    assert [f.folder_id for f in records.folders_of(p.id)] == [a]
    assert [x.id for x in records.projects_of_folder(a)] == [p.id]


def test_works_have_one_primary_and_accomplishments_count_once_confirmed(stores):
    records, _, _ = stores
    a, b = records.create("local", "A"), records.create("local", "B")
    w = records.start_work("local", "Spike", projects=[a.id, b.id])
    assert [(x["project_id"], x["role"]) for x in w.projects] == [(a.id, "primary"), (b.id, "involved")]
    with pytest.raises(ProjectError):
        records.change_work(w.id, remove_project=a.id)
    acc = records.propose_accomplishment(a.id, "Shipped the checker", evidence=["https://example/pr/1"])
    assert records.accomplishments(a.id) == []
    records.answer_accomplishment(acc.id, "confirm")
    assert [x.title for x in records.accomplishments(a.id)] == ["Shipped the checker"]
    with pytest.raises(ProjectError):
        records.answer_accomplishment(acc.id, "confirm")


def test_the_schema_is_its_own_and_leaves_the_others_alone(stores):
    records, _, _ = stores
    assert records.schema_version() == pstore.SCHEMA_VERSION
    with sqlite3.connect(data_paths.db_path()) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"projects", "project_events", "folders", "runs"} <= tables
        for t in ("projects", "project_aliases", "project_folders", "project_candidates", "works",
                  "work_projects", "work_folders", "accomplishments", "accomplishment_evidence",
                  "project_bindings", "project_events", "project_schema"):
            conn.execute(f"DROP TABLE {t}")
        assert "folders" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert pstore.ProjectStore().schema_version() == 1      # and it comes back


# --- candidates ----------------------------------------------------------------

def test_candidates_from_folders_and_legacy_names_are_proposals_only(stores, tmp_path):
    records, folders, service = stores
    one = register(folders, tmp_path / "x" / "denice", "denice")
    two = register(folders, tmp_path / "y" / "Denice", "Denice", key="k2")
    run_store.create_run("p", "Chitauri", "/nowhere/chitauri", "brain")
    run_store.create_run("p", "jarvis-src", str(tmp_path / "x" / "denice"), "brain")
    found = {c["proposed_name"].lower(): c for c in service.refresh_candidates()}
    assert records.projects(["local"]) == []                         # nothing made
    assert sorted(f["folder_id"] for f in found["denice"]["evidence"]["folders"]) == sorted([one, two])
    assert [x["source"] for x in found["denice"]["evidence"]["legacy"]] == ["run_project"]   # by its path
    assert found["chitauri"]["evidence"]["folders"] == []


def test_ignored_stays_ignored_until_its_evidence_changes_and_accept_makes_it_whole(stores, tmp_path):
    records, folders, service = stores
    a = register(folders, tmp_path / "x" / "orbs", "orbs")
    cand = service.refresh_candidates()[0]
    service.change_candidate(cand["id"], "ignore")
    assert service.refresh_candidates() == []
    b = register(folders, tmp_path / "y" / "orbs", "Orbs", key="k2")
    again = service.refresh_candidates()
    assert len(again) == 1 and again[0]["id"] == cand["id"]          # offered again: new evidence
    done = service.change_candidate(cand["id"], "accept", name="Orb Work", folders=[a, b], home=b)
    p = records.require(done["project_id"])
    assert p.display_name == "Orb Work"
    assert {f.folder_id: f.is_home for f in records.folders_of(p.id)} == {a: False, b: True}
    assert service.refresh_candidates() == []                        # its folders are in a project now
    with pytest.raises(ProjectError):
        service.change_candidate(cand["id"], "accept")


def test_a_legacy_name_resolves_once_accepted(stores):
    records, _, service = stores
    run_store.create_run("p", "Kestrel", "/nowhere/kestrel", "brain")
    cand = service.refresh_candidates()[0]
    done = service.change_candidate(cand["id"], "accept")
    assert records.project_for_legacy(["local"], "run_project", "kestrel").id == done["project_id"]
    assert service.resolve("KESTREL").id == done["project_id"]


# --- where work runs ----------------------------------------------------------------

def test_a_run_starts_in_the_projects_home_or_a_folder_of_its_work(stores, tmp_path):
    records, folders, service = stores
    a, b, c = (register(folders, tmp_path / n, n) for n in ("a", "b", "c"))
    p, q = records.create("local", "P"), records.create("local", "Q")
    records.link_folder(p.id, a, home=True)
    records.link_folder(q.id, b, home=True)
    _, f, root = service.run_location(project="P")
    assert f.id == a and root.name == "a"
    w = records.start_work("local", "Both", projects=[p.id, q.id])
    assert service.run_location(work=w.id, folder_id=b)[1].id == b
    with pytest.raises(ProjectError):
        service.run_location(work=w.id, folder_id=c)
    assert folders.folder_for_cwd(str(tmp_path / "a" / ".claude" / "worktrees" / "x")).id == a


# --- page and brain --------------------------------------------------------------------

@pytest.fixture
def served(wired, monkeypatch, tmp_path):  # noqa: F811
    folders = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
    service = ps.ProjectService(pstore.ProjectStore(), lambda: folders)
    monkeypatch.setattr(wired.server, "folder_catalog", folders)
    monkeypatch.setattr(wired.server, "projects", service)
    return wired, folders, service


def joined(w, tool, **args):
    out, n = "", 1
    while True:
        r = _call(w, tool, page=n, **args)
        assert r["ok"], r
        head, _, block = r["text"].partition("\n")
        assert 'untrusted="true"' in block
        out += block.split(">", 1)[1].rsplit("</session-output>", 1)[0].strip("\n")
        if "again with page" not in head:
            return json.loads(out)
        n += 1


def test_projects_are_the_same_on_the_page_and_by_voice(served, tmp_path):
    w, folders, service = served
    fid = register(folders, tmp_path / "src" / "orbs", "orbs")
    r = w.client.post("/api/project-records", json={"name": "Orbs", "summary": "the orbs"}, headers=ORIGIN)
    assert r.status_code == 200
    pid = r.json()["project"]["id"]
    linked = _call(w, "change_project_folder", action="link", project="orbs", folder_id=fid)
    assert linked["ok"] and json.loads(linked["text"])["done"] == "link"
    page = w.client.get("/api/project-records", headers=ORIGIN).json()
    assert joined(w, "projects") == page
    assert page["projects"][0]["home"]["folder_id"] == fid
    detail = w.client.get(f"/api/project-records/{pid}", headers=ORIGIN).json()
    assert joined(w, "project_detail", project=pid) == detail
    refused = _call(w, "change_project", origin="event", action="archive", project=pid)
    assert not refused["ok"]
    assert {"type": "projects"} in w.hints
    history = w.client.get(f"/api/project-records/{pid}/history", headers=ORIGIN).json()
    assert [e["action"] for e in history["events"]] == ["folder_linked", "created"]


def test_candidates_and_works_through_both(served, tmp_path):
    w, folders, service = served
    register(folders, tmp_path / "x" / "kestrel", "kestrel")
    found = w.client.post("/api/project-records/candidates/refresh", headers=ORIGIN).json()["proposed"]
    assert joined(w, "project_candidates")["candidates"][0]["id"] == found[0]["id"]
    said = _call(w, "change_project_candidate", action="accept", candidate=found[0]["id"])
    pid = json.loads(said["text"])["project_id"]
    started = w.client.post("/api/works", json={"title": "Spike", "projects": [pid]}, headers=ORIGIN).json()
    assert joined(w, "works") == w.client.get("/api/works", headers=ORIGIN).json()
    assert joined(w, "work_detail", work=started["work"]["id"])["projects"][0]["role"] == "primary"
    acc = json.loads(_call(w, "change_accomplishment", action="record", project=pid, title="Built it")["text"])
    assert acc["accomplishment"]["state"] == "confirmed"
    bad = w.client.post(f"/api/project-records/{pid}/accomplishments", json={"action": "nope"}, headers=ORIGIN)
    assert bad.status_code == 400


# --- the Work tab's tie-in -------------------------------------------------------

def test_a_work_items_source_names_the_projects_holding_it(stores, tmp_path, monkeypatch):
    from jarvis import work_api, work_store
    records, folders, service = stores
    fid = register(folders, tmp_path / "repo", "Repo")
    p = records.create("local", "Repo work")
    records.link_folder(p.id, fid, home=True)
    inside = tmp_path / "repo" / ".claude" / "worktrees" / "w1" / "pkg"
    inside.mkdir(parents=True)
    assert service.projects_for_path(str(inside)) == [{"id": p.id, "name": "Repo work"}]
    assert service.projects_for_path(str(tmp_path / "elsewhere")) == []

    monkeypatch.setattr(work_api, "projects_for_path", service.projects_for_path)
    item = work_store.create("Ship it", "folder", str(inside))
    assert work_api.item_json(item, package_questions={})["projects"] == [{"id": p.id, "name": "Repo work"}]

