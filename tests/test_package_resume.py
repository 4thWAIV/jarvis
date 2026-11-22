"""A package's next run goes on from where the run before failed (Tim
2026-11-22: only what failed is run again; every earlier success is reused):
package_resume.py, package_jobs.new_run(resume=True) and rerun, the
server's answer and rerun routes, make_package --rerun and package_rerun."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import package_deck as pd, package_jobs as pj, package_resume as pr_, \
    package_request as pr, package_swarm as ps
from tests.agent_key_fixtures import Agent
from tests.test_agent_key_packages import _start_work
from tests.test_agent_keys import PLAN, keygen, wired  # noqa: F401
from tests.test_package_deck import deck_plan, _passed  # noqa: F401
from tests.test_package_gather import checkout, gh, indexing, transcript  # noqa: F401
from tests.test_package_jobs import ask, handoff, spawned  # noqa: F401
from tests.test_package_swarm import LEFT, marked  # noqa: F401


def _deck_failed(doc, deck_plan):  # noqa: F811
    """A run whose deck review ends with what is left for the sender, and
    the sender's answer to it."""
    doc = _passed(doc)
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    assert pd.deck(doc) == pj.WAITING
    asked = pj.get(doc["id"])["rounds"][-1]
    pj.answer(doc["id"], {q["id"]: "Each option now says what it changes." for q in
                          asked["questions"]})
    assert pj.get(doc["id"])["state"] == pj.FAILED
    return pj.get(doc["id"])


def test_a_failed_deck_review_goes_on_from_the_deck_with_everything_kept(marked, deck_plan):
    before = _deck_failed(marked, deck_plan)
    assert pr_.failed_at(before) == "deck"
    later = pj.new_run(before["id"], "the sender answered", resume=True)
    old, new = Path(before["folder"]), Path(later["folder"])
    # Research and the swarm are done and kept; gather and mark reuse themselves.
    assert later["done"] == ["research", "swarm"]
    assert later["reused"] == {"from": before["id"], "run": "run-1", "step": "deck",
                               "stage": "deck-pass", "steps": ["research", "swarm"],
                               "files": later["reused"]["files"], "fresh_sessions":
                                   later["reused"]["fresh_sessions"]}
    for name in ("research.md", "prep.md", "outline.yaml", "script.yaml", "swarm.json",
                 "deck/"):
        assert name in later["reused"]["files"]
        assert (new / name.rstrip("/")).exists()
    assert (new / "script.yaml").read_text() == (old / "script.yaml").read_text()
    assert (new / pd.FOLDER / pd.SOURCE).read_text() == (old / pd.FOLDER / pd.SOURCE).read_text()
    # The swarm's passed result is the new run's, its files in the new folder.
    assert later["swarm"]["result"] == "passed"
    assert later["swarm"]["script"] == str(new / "script.yaml")
    # The deck review starts its passes again, reviewing the deck rebuilt;
    # the program says so, then the findings' answers, as the sender's.
    kept = json.loads((new / ps.STATE).read_text())
    assert kept["deck_flow"] == {"stage": "deck-pass", "pass": 1, "review_again": True}
    assert kept["asking"]["answers_said"] == 0
    said = kept["swarm"]["conversation"][-2:]
    assert said[0]["from"] == "jarvis" and "goes on from run-1 at the deck step" in said[0]["text"]
    assert said[1]["from"] == "sender" and "Each option now says what it changes." in \
        said[1]["text"]
    # A Claude participant's session lives in its own folder: it starts anew
    # and is given the whole conversation.
    assert set(later["reused"]["fresh_sessions"]) == set(ps.NAMES)
    assert "participants" not in kept or not kept["participants"]
    assert kept["swarm"]["introduced"] == []

    # The deck step goes on: nothing written again, the deck rebuilt and
    # reviewed again, and it passes.
    deck_plan.folder = new
    deck_plan.signoffs = {}
    wrote = len(deck_plan.sent.get("author", []))
    assert pd.deck(pj.get(later["id"])) is None
    assert pj.get(later["id"])["deck"]["result"] == "passed"
    assert not any("Now write the deck" in t for t in deck_plan.sent["author"][wrote:])
    review = json.loads((new / pd.REVIEW).read_text())
    resumed = next(i for i, m in enumerate(review["conversation"])
                   if "goes on from run-1" in m["text"])
    reviews = [m for m in review["conversation"][resumed:] if m["from"] == pd.PROGRAM
               and "built and proven" in m["text"]]
    assert len(reviews) == 1, "reviewed again with the deck rebuilt"
    assert "nothing on it, words, pictures or a decision's controls" in reviews[-1]["text"]
    assert '"fit": {' in reviews[-1]["text"] and "nothing to scroll (measured)" in \
        reviews[-1]["text"]


def test_a_failed_swarm_goes_on_from_its_fix_passes(marked, deck_plan, monkeypatch):
    doc = pj.get(marked["id"])
    ps.research(doc)
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    assert ps.swarm(pj.get(doc["id"])) == pj.WAITING
    asked = pj.get(doc["id"])["rounds"][-1]
    pj.answer(doc["id"], {q["id"]: "Fixed." for q in asked["questions"]})
    before = pj.get(doc["id"])
    assert pr_.failed_at(before) == "swarm"
    later = pj.new_run(before["id"], "answered", resume=True)
    assert later["done"] == ["research"]
    assert later["reused"]["stage"] == "pass"
    kept = json.loads((Path(later["folder"]) / ps.STATE).read_text())
    assert kept["flow"] == {"stage": "pass", "pass": 1} and kept["researched"] is True
    assert "swarm" not in later or later.get("swarm") is None


def test_a_run_in_error_goes_on_from_the_step_it_stopped_at(marked, deck_plan):
    doc = _passed(marked)
    pj.error(doc["id"], "deck", "JARVIS could not make the deck fit its frame.", "tried", "fix")
    later = pj.new_run(doc["id"], "asked again", resume=True)
    assert later["reused"]["step"] == "deck" and later["done"] == ["research", "swarm"]
    kept = json.loads((Path(later["folder"]) / ps.STATE).read_text())
    assert kept["deck_flow"]["stage"] == "deck-write", "the deck never written is written"


def test_a_check_or_a_reject_is_not_resumed():
    assert pr_.failed_at({"state": pj.FAILED, "rounds": [{"kind": pj.CHECK}], "step": "deliver"}) \
        is None
    assert pr_.failed_at({"state": pj.DELIVERED, "rounds": [], "step": "deliver"}) is None
    assert pr_.failed_at({"state": pj.ERROR, "rounds": [], "step": "gather",
                          "error": {"step": "mark"}}) == "mark"


def test_rerun_takes_only_an_ended_or_failed_run_with_none_later_going(marked, deck_plan):
    doc = _passed(marked)
    with pytest.raises(pj.JobError, match="only a run that ended in Error or failed"):
        pj.rerun(doc["id"], doc["session"])
    with pytest.raises(pj.JobError):
        pj.rerun(doc["id"], "someone-else")
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    assert pd.deck(pj.get(doc["id"])) == pj.WAITING
    # Waiting on its findings, asked again without answers: it ends failed.
    assert pj.rerun(doc["id"], doc["session"])["state"] == pj.FAILED
    later = pj.new_run(doc["id"], "asked again", resume=True)
    assert later["reused"]["step"] == "deck"
    kept = json.loads((Path(later["folder"]) / ps.STATE).read_text())
    assert all(m["from"] != "sender" for m in kept["swarm"]["conversation"][-1:])
    with pytest.raises(pj.JobError, match="run-2 of package .* is queued"):
        pj.rerun(doc["id"], doc["session"])
    pj.error(later["id"], "deck", "stopped", "t", "f")
    assert pj.rerun(doc["id"], doc["session"])["id"] == doc["id"]


def test_the_sender_asks_again_through_the_route_and_its_error_clears(wired, tmp_path,
                                                                      spawned):  # noqa: F811
    server, _hints, store, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    work = _start_work(client, agent, folder)
    job = ask(client, agent, {"handoff": handoff(tmp_path)}, work=work).json()["job"]
    r = agent.post(client, f"{pr.ROUTE}/{job}/rerun", {})
    assert r.status_code == 409 and "only a run that ended in Error or failed" in \
        r.json()["error"]
    pj.error(job, "swarm", "The author could not answer.", "tried", "fix")
    server._look_after_package_jobs()
    from jarvis import work_store
    assert work_store.get(work).state == work_store.ERROR
    r = agent.post(client, f"{pr.ROUTE}/{job}/rerun", {})
    assert r.status_code == 200, r.text
    later = r.json()
    assert later["run"] == "run-2" and later["after"] == job
    assert later["reused"]["step"] == "swarm" and spawned[-1] == later["id"]
    assert work_store.get(work).state != work_store.ERROR
    assert pj.errored_work() == set()


def test_make_package_rerun_and_the_tool_ask_jarvis(monkeypatch, capsys):
    from jarvis import agent_mcp, make_package
    for name in ("JARVIS_AGENT_SESSION", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    asked = []

    def rerun(job, *, session):
        asked.append((job, session))
        return {"id": "job-2", "run": "run-2"}
    monkeypatch.setattr(pr, "rerun", rerun)
    assert make_package.main(["--rerun", "job-1", "--session", "sess-a"]) == 0
    assert json.loads(capsys.readouterr().out)["run"] == "run-2"
    ok, text, _ = agent_mcp.call("package_rerun", {"job": "job-1", "as_session": "sess-b"})
    assert ok and json.loads(text)["id"] == "job-2"
    assert asked == [("job-1", "sess-a"), ("job-1", "sess-b")]
