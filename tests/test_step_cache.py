"""The package steps' cache, keyed by their inputs (step_cache.py;
construction-drafts.md "Step cache keyed by inputs", approved by Tim
2026-11-22): every run of a package, whatever started it, reuses each step
and unit whose inputs are unchanged and does again only what its new
inputs change; timing.json says which units were reused and which
computed, and no unit is ever computed with inputs already kept."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import (package_deck as pd, package_jobs as pj, package_request as pr,
                    package_swarm as ps, run_report, step_cache)
from tests.agent_key_fixtures import Agent
from tests.test_agent_key_packages import _start_work
from tests.test_agent_keys import PLAN, keygen, wired  # noqa: F401
from tests.test_package_deck import deck_plan, deck_with_his_words  # noqa: F401
from tests.test_package_gather import checkout, gh, indexing, transcript  # noqa: F401
from tests.test_package_jobs import ask, handoff, spawned  # noqa: F401
from tests.test_package_swarm import LEFT, Marking, marked  # noqa: F401


# ── the cache itself ──────────────────────────────────────────────────────

def test_a_key_is_the_canonical_hash_of_the_inputs_never_their_order():
    one = step_cache.Unit("research", "", {"a": 1, "b": [1, 2], "c": {"y": 2, "x": 1}})
    two = step_cache.Unit("research", "", {"c": {"x": 1, "y": 2}, "b": [1, 2], "a": 1})
    assert one.key == two.key
    assert step_cache.Unit("research", "", {"a": 2}).key != one.key
    assert step_cache.Unit("deck-fit", "s1", {}).key != step_cache.Unit("deck-fit", "s2", {}).key


def test_an_output_is_kept_with_its_timing_and_restored_where_asked(tmp_path):
    made = tmp_path / "made"
    (made / "deck").mkdir(parents=True)
    (made / "script.yaml").write_text("version: script/1\n")
    (made / "deck" / "pic.png").write_bytes(b"png")
    unit = step_cache.Unit("swarm-fill", "", {"before": "abc"})
    assert step_cache.lookup(unit) is None
    outcome, entry = step_cache.keep(unit, {"id": "job-1", "run": "run-1"}, folder=made,
                                     files=["script.yaml", "deck"], value={"n": 1},
                                     seconds=12.5)
    assert outcome == step_cache.COMPUTED
    assert set(entry.files) == {"script.yaml", "deck/pic.png"} and entry.value == {"n": 1}
    assert entry.seconds == 12.5 and entry.made["run"] == "run-1"
    elsewhere = tmp_path / "elsewhere"
    step_cache.restore(step_cache.lookup(unit), elsewhere)
    assert (elsewhere / "deck" / "pic.png").read_bytes() == b"png"
    # Kept once: computing it again with the same inputs is never expected,
    # and says so.
    again, there = step_cache.keep(unit, None, folder=made, files=["script.yaml"])
    assert again == step_cache.RECOMPUTED and there.digest == entry.digest


def test_each_unit_reused_or_computed_is_in_the_step_s_timing(tmp_path):
    doc = {"id": "job-1", "package": "p", "run": "run-1", "folder": str(tmp_path)}
    unit = step_cache.Unit("deliver", "", {"deck": "x"})
    with run_report.step(doc, "deliver"):
        assert step_cache.run(unit, doc, lambda: 7, value=lambda r: r)[0] == step_cache.COMPUTED
        assert step_cache.run(unit, doc, lambda: 8)[0] == step_cache.REUSED
    timing = json.loads((tmp_path / run_report.TIMING).read_text())
    [entry] = timing["steps"]
    assert [u["outcome"] for u in entry["units"]] == ["computed", "reused"]
    assert entry["units"][1]["made_by"]["run"] == "run-1"
    assert entry["cache"] == {"reused": 1, "computed": 1, "recomputed": 0}
    assert timing["totals"]["cache"] == {"reused": 1, "computed": 1, "recomputed": 0}


# ── every run of a package ────────────────────────────────────────────────

def _units(doc: dict) -> list[dict]:
    timing = json.loads((Path(doc["folder"]) / run_report.TIMING).read_text())
    return [u for s in timing["steps"] for u in s.get("units") or []]


def _computed(doc: dict) -> set[str]:
    return {f"{u['kind']}:{u['name']}" if u["name"] else u["kind"]
            for u in _units(doc) if u["outcome"] == step_cache.COMPUTED}


def _never_recomputed(doc: dict) -> None:
    """The guard: no unit is ever computed with inputs already kept."""
    assert not [u for u in _units(doc) if u["outcome"] == step_cache.RECOMPUTED]


def _run(job_id: str, plan) -> dict:
    doc = pj.get(job_id)
    plan.folder = Path(doc["folder"])
    assert pj.run(job_id) == 0, pj.get(job_id).get("error")
    doc = pj.get(job_id)
    _never_recomputed(doc)
    return doc


def _turns(plan) -> int:
    return sum(len(v) for v in plan.sent.values())


def test_a_run_with_unchanged_inputs_recomputes_nothing(marked, deck_plan, keygen):  # noqa: F811
    first = _run(marked["id"], deck_plan)
    assert first["state"] == pj.WAITING and pj.kind_of(first["rounds"][-1]) == pj.CHECK
    computed = _computed(first)
    assert {"research", "swarm-outline", "swarm-fill", "swarm-passes", "deck-header",
            "deck-build", "deck", "deliver"} <= computed
    assert {"script-slide:opening", "deck-source:where", "deck-fit:what-changed",
            "deck-review:opening"} <= computed
    # Asked again (an Error, a fresh go): nothing it reads has changed.
    pj.error(first["id"], "deliver", "stopped", "tried", "fix")
    turns, marked_calls = _turns(deck_plan), len(Marking.calls)
    later = _run(pj.new_run(first["id"], "the sender asked for it again")["id"], deck_plan)
    assert _computed(later) == set(), "no unit is computed again"
    assert {u["outcome"] for u in _units(later)} == {step_cache.REUSED}
    assert _turns(deck_plan) == turns and len(Marking.calls) == marked_calls, \
        "no model is asked anything"
    # The run goes on to its sender's check with the same package.
    assert later["state"] == pj.WAITING and pj.kind_of(later["rounds"][-1]) == pj.CHECK
    old, new = (Path(d["rounds"][-1]["package"]["archive"]) for d in (first, later))
    assert old.read_bytes() == new.read_bytes() and old != new


class OneSlide:
    """The author's revision: it changes one slide and names it."""

    def __init__(self, plan, slide: str):
        self.plan, self.slide = plan, slide

    def __call__(self, name, text, form):
        answer = type(self.plan).answer(self.plan, name, text, form)
        if "changed" in form["properties"]:
            answer["changed"] = [self.slide]
        return answer


def test_a_change_naming_one_slide_recomputes_only_that_slide(marked, deck_plan, keygen,  # noqa: F811
                                                               monkeypatch):
    first = _run(marked["id"], deck_plan)
    words = "The second slide should say who brings the food."
    pj.answer(first["id"], {"check": "change", "what_is_wrong": words})
    later = pj.new_run(first["id"], "the sender said what is wrong",
                       revision={"words": words, "by": ps.SENDER})
    assert later["inputs"]["revisions"] == [{"run": "run-1", "by": "sender", "words": words}]
    script = json.loads(json.dumps(deck_plan.script))
    script["slides"][1]["on_screen"][0]["text"] = "Food is brought in by the caterer."
    script["slides"][1]["facts"][0]["fact"] = "Food is brought in by the caterer."
    # The author also touches a slide it does not name: put back.
    script["slides"][0]["narration"] = "Reworded without being asked."
    deck_plan.script = script
    monkeypatch.setattr(deck_plan, "answer", OneSlide(deck_plan, "what-changed"))
    deck = deck_with_his_words()
    deck["slides"][1]["narration"][0]["say"] = "Food is brought in by the caterer."
    deck_plan.decks = [deck]
    marked_calls = len(Marking.calls)
    done = _run(later["id"], deck_plan)
    assert _computed(done) == {"swarm-revision:1", "script-slide:what-changed",
                               "deck-source:what-changed", "deck-build",
                               "deck-fit:what-changed", "deck-review:what-changed", "deck",
                               "deliver"}
    new = Path(done["folder"])
    passed = ps.package_script.read(new / ps.SCRIPT)
    assert passed["slides"][0] == deck_plan.script["slides"][0] | {
        "narration": "It moves every item."}, "the slide not named is put back"
    assert done["revision"]["slides_changed"] == ["what-changed"]
    assert done["revision"]["put_back"] == ["opening"]
    assert done["deck"]["slides_reviewed"] == ["what-changed"]
    assert len(Marking.calls) == marked_calls + 1
    conversation = json.loads((new / ps.STATE).read_text())["swarm"]["conversation"]
    assert any(m["from"] == "sender" and words in m["text"] for m in conversation)
    review = json.loads((new / pd.REVIEW).read_text())
    turns = [m["text"] for m in review["conversation"] if m["from"] == pd.PROGRAM
             and "built and proven" in m["text"]]
    assert '<slide id="what-changed">' in turns[-1] and '<slide id="opening">' not in turns[-1]


def test_tim_s_reject_is_a_revision_too(marked, deck_plan, keygen):  # noqa: F811
    first = _run(marked["id"], deck_plan)
    later = pj.new_run(first["id"], "Tim rejected it",
                       revision={"words": "Lead with the cost.", "by": ps.TIM})
    sent = len(deck_plan.sent["author"])
    done = _run(later["id"], deck_plan)
    assert "research" not in _computed(done) and "swarm-passes" not in _computed(done)
    assert "swarm-revision:1" in _computed(done)
    assert "Lead with the cost." in "".join(deck_plan.sent["author"][sent:])
    kept = json.loads((Path(done["folder"]) / ps.STATE).read_text())
    assert {"from": "tim", "text": "Lead with the cost."} in kept["swarm"]["conversation"]


class DeckLeft:
    """The editor leaves the deck's concerns not signed off."""

    def __init__(self, plan):
        self.plan, self.on = plan, True

    def __call__(self, name, text, form):
        if self.on and name == "editor" and "deck-says:editor" in str(form):
            self.plan.signoffs = {"editor": [("not_yet", LEFT)]}
        return type(self.plan).answer(self.plan, name, text, form)


def test_a_findings_answer_on_the_deck_recomputes_only_the_review_onward(
        marked, deck_plan, keygen, monkeypatch):  # noqa: F811
    left = DeckLeft(deck_plan)
    monkeypatch.setattr(deck_plan, "answer", left)
    first = _run(marked["id"], deck_plan)
    assert first["rounds"][-1]["kind"] == pj.FINDINGS
    assert first["rounds"][-1]["stage"] == "deck-review"
    pj.answer(first["id"], {q["id"]: "Each option now says what it changes."
                            for q in first["rounds"][-1]["questions"]})
    later = pj.new_run(first["id"], "the sender answered the findings")
    assert later["inputs"]["findings"][0]["stage"] == "deck-review"
    left.on, deck_plan.signoffs = False, {}
    done = _run(later["id"], deck_plan)
    assert _computed(done) == {"deck-review:opening", "deck-review:what-changed",
                               "deck-review:where", "deck", "deliver"}
    review = json.loads((Path(done["folder"]) / pd.REVIEW).read_text())
    assert any(m["from"] == "sender" and "Each option now says what it changes." in m["text"]
               for m in review["conversation"])


def test_a_findings_answer_on_the_swarm_recomputes_only_its_fix_passes_onward(
        marked, deck_plan, keygen):  # noqa: F811
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    first = _run(marked["id"], deck_plan)
    assert first["rounds"][-1]["stage"] == "swarm-passes"
    pj.answer(first["id"], {q["id"]: "Fixed." for q in first["rounds"][-1]["questions"]})
    deck_plan.signoffs = {}
    done = _run(pj.new_run(first["id"], "answered")["id"], deck_plan)
    computed = _computed(done)
    assert "swarm-passes" in computed and "deliver" in computed
    assert not computed & {"gather", "research", "swarm-outline", "swarm-fill"}
    assert not [c for c in computed if c.startswith("mark-file")]


def test_a_new_request_with_the_same_handoff_reuses_everything(marked, deck_plan, keygen):  # noqa: F811
    first = _run(marked["id"], deck_plan)
    fresh = pj.accept(first["request"], session=first["session"], work=first["work"] or "",
                      engine=first["engine"] or "", submitted=first["submitted"])
    assert fresh["package"] != first["package"]
    turns = _turns(deck_plan)
    done = _run(fresh["id"], deck_plan)
    assert _computed(done) == set()
    assert _turns(deck_plan) == turns
    assert done["state"] == pj.WAITING


# ── asking for it again ───────────────────────────────────────────────────

def test_rerun_takes_only_an_ended_or_failed_run_with_none_later_going(marked, deck_plan,  # noqa: F811
                                                                       keygen):
    doc = pj.get(marked["id"])
    with pytest.raises(pj.JobError, match="only a run that ended in Error or failed"):
        pj.rerun(doc["id"], doc["session"])
    with pytest.raises(pj.JobError):
        pj.rerun(doc["id"], "someone-else")
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    doc = _run(doc["id"], deck_plan)
    # Waiting on its findings, asked again without answers: it ends failed.
    assert pj.rerun(doc["id"], doc["session"])["state"] == pj.FAILED
    later = pj.new_run(doc["id"], "asked again")
    assert later["inputs"] == {"revisions": [], "findings": []}, "no answers: nothing new"
    with pytest.raises(pj.JobError, match="run-2 of package .* is queued"):
        pj.rerun(doc["id"], doc["session"])
    pj.error(later["id"], "deck", "stopped", "t", "f")
    assert pj.rerun(doc["id"], doc["session"])["id"] == doc["id"]


def test_the_sender_asks_again_through_the_route_and_its_error_clears(wired, tmp_path,  # noqa: F811
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
    assert later["run"] == "run-2" and later["after"] == job and spawned[-1] == later["id"]
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
