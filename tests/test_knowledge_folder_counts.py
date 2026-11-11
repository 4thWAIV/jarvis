"""Each registered folder's document count (requirement 41,
`ui-top-folders-count`): read as Tim through EngineUDS.count under
/folders/<folder id>, the same answer on the page's route and the brain's
tool, and the engine off or down said, never failed."""
from __future__ import annotations

import json

from jarvis import knowledge_api as ka
from jarvis.knowledge.uds import Caller, Transaction, in_process
from tests.test_brain_page_parity import _call, wired  # noqa: F401
from tests.test_knowledge_api import ORIGIN, FakeRuntime

A = "0b6f6a52-6f0e-4c39-9f5c-0d1f6c1e2a01"
B = "4d1e0c55-21f8-4a8e-9d3b-7c2b8e0f9b02"
EMPTY = "9e8d7c6b-5a49-4382-8170-6f5e4d3c2b03"
TIM = Caller("tim")


def _fill(uds) -> None:
    uds.transaction(TIM, Transaction()
                    .put(f"/folders/{A}/README.md", {"title": "readme"}, content={"text": "# A\nhello"})
                    .put(f"/folders/{A}/docs/plan.md", {"title": "plan"}, content={"text": "# Plan\nsteps"})
                    .put(f"/folders/{A}/docs/notes.md", {"title": "notes"}, content={"text": "notes"})
                    .put(f"/folders/{B}/CLAUDE.md", {"title": "claude"}, content={"text": "rules"}))


class _Ctx(ka.Context):
    def __init__(self, engine):
        runtime = FakeRuntime(engine)
        super().__init__(lambda: runtime)


def test_each_folder_is_counted_under_its_own_path(tmp_path):
    with in_process(tmp_path / "engine") as (engine, uds):
        _fill(uds)
        got = ka.folder_counts(_Ctx(engine), [A, B, EMPTY])
    assert got["available"] is True and got["partial"] is False
    assert got["folders"][A]["items"] == 3
    assert got["folders"][B]["items"] == 1
    assert got["folders"][EMPTY] == {"items": 0, "embedded": 0, "waiting": 0}
    for fid in (A, B):
        c = got["folders"][fid]
        assert c["embedded"] + c["waiting"] == c["items"]


def test_the_engine_off_or_down_is_said_never_raised(tmp_path):
    off = ka.folder_counts(ka.Context(lambda: None), [A])
    assert off == {"available": False, "reason": "the knowledge engine is switched off", "folders": {}}
    with in_process(tmp_path / "engine") as (engine, _uds):
        runtime = FakeRuntime(engine)
        runtime.supervisor.pid = lambda: None
        down = ka.folder_counts(ka.Context(lambda: runtime), [A])
    assert down["available"] is False and "not running" in down["reason"]


def test_past_the_deadline_the_rest_is_left_out_and_said(tmp_path):
    with in_process(tmp_path / "engine") as (engine, uds):
        _fill(uds)
        got = ka.folder_counts(_Ctx(engine), [A, B], deadline=0)
    assert got == {"available": True, "partial": True, "folders": {}}


def test_the_route_and_the_brain_s_tool_give_the_same_counts(wired, monkeypatch, tmp_path):  # noqa: F811
    with in_process(tmp_path / "engine") as (engine, uds):
        _fill(uds)
        monkeypatch.setenv("JARVIS_KNOWLEDGE", "1")
        monkeypatch.setattr(wired.server, "knowledge", FakeRuntime(engine))
        monkeypatch.setattr(ka, "_active_folder_ids", lambda: [A, B])
        page = wired.client.get("/api/knowledge/folder-counts", headers=ORIGIN)
        assert page.status_code == 200
        said = _call(wired, "folder_document_counts")["text"]
    assert json.loads(said) == page.json()
    assert page.json()["folders"][A]["items"] == 3


def test_the_route_answers_200_with_the_engine_off(wired, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wired.server, "knowledge", None)
    monkeypatch.setattr(ka, "_active_folder_ids", lambda: [A])
    page = wired.client.get("/api/knowledge/folder-counts", headers=ORIGIN)
    assert page.status_code == 200 and page.json()["available"] is False


def test_only_the_registered_folders_are_asked_about(tmp_path, monkeypatch):
    # The ids come from the folder catalog, never from the caller.
    with in_process(tmp_path / "engine") as (engine, uds):
        _fill(uds)
        monkeypatch.setattr(ka, "_active_folder_ids", lambda: [A])
        got = ka.folder_counts(_Ctx(engine))
    assert list(got["folders"]) == [A]
