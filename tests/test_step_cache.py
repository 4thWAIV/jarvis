"""The package steps' cache, keyed by their inputs (step_cache.py;
construction-drafts.md "Step cache keyed by inputs", approved by Tim
2026-11-22): every run of a package, whatever started it, reuses each step
and unit whose inputs are unchanged and does again only what its new
inputs change; timing.json says which units were reused and which
computed, and no unit is ever computed with inputs already kept. The
cache is one package's (Tim, 2026-11-22): a new package reuses nothing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dataclasses
import os
import time

from jarvis import (package_deck as pd, package_jobs as pj, package_mark, package_request as pr,
                    package_swarm as ps, role_prompts, run_report, step_cache, work_store)
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
    assert step_cache.Unit("deck-review", "s1", {}).key != \
        step_cache.Unit("deck-review", "s2", {}).key


def test_an_output_is_kept_with_its_timing_and_restored_where_asked(tmp_path):
    made = tmp_path / "made"
    (made / "deck").mkdir(parents=True)
    (made / "script.yaml").write_text("version: script/1\n")
    (made / "deck" / "pic.png").write_bytes(b"png")
    unit = step_cache.Unit("swarm-fill", "", {"before": "abc"})
    doc = {"id": "job-1", "run": "run-1", "package": "pkg-a"}
    assert step_cache.lookup(unit, doc) is None
    outcome, entry = step_cache.keep(unit, doc, folder=made,
                                     files=["script.yaml", "deck"], value={"n": 1},
                                     seconds=12.5)
    assert outcome == step_cache.COMPUTED
    assert set(entry.files) == {"script.yaml", "deck/pic.png"} and entry.value == {"n": 1}
    assert entry.seconds == 12.5 and entry.made["run"] == "run-1"
    elsewhere = tmp_path / "elsewhere"
    step_cache.restore(step_cache.lookup(unit, doc), elsewhere)
    assert (elsewhere / "deck" / "pic.png").read_bytes() == b"png"
    # Kept once: computing it again with the same inputs is never expected,
    # and says so.
    again, there = step_cache.keep(unit, doc, folder=made, files=["script.yaml"])
    assert again == step_cache.RECOMPUTED and there.digest == entry.digest
    # Kept in the package's own folder, and only for it: another package
    # with the same inputs has nothing kept.
    assert entry.path.is_relative_to(step_cache.root(doc))
    assert step_cache.root(doc).parent.name == "pkg-a"
    assert step_cache.lookup(unit, {**doc, "package": "pkg-b"}) is None
    with pytest.raises(ValueError, match="one package's"):
        step_cache.lookup(unit, {"id": "job-1"})


def test_the_package_s_cache_is_never_indexed():
    from jarvis import knowledge_content
    assert knowledge_content.run_bookkeeping("w1/packages/pkg-a/.step-cache", True)
    assert knowledge_content.run_bookkeeping(
        "w1/packages/pkg-a/.step-cache/research/ab/abc/files/research.md", False)
    assert not knowledge_content.run_bookkeeping("w1/packages/pkg-a/evidence/handoff.json",
                                                 False)


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
    assert entry["cache"] == {"reused": 1, "computed": 1, "recomputed": 0, "replaced": 0}
    assert timing["totals"]["cache"] == {"reused": 1, "computed": 1, "recomputed": 0, "replaced": 0}


# ── every run of a package ────────────────────────────────────────────────

def _units(doc: dict) -> list[dict]:
    timing = json.loads((Path(doc["folder"]) / run_report.TIMING).read_text())
    return [u for s in timing["steps"] for u in s.get("units") or []]


def _computed(doc: dict) -> set[str]:
    return {f"{u['kind']}:{u['name']}" if u["name"] else u["kind"]
            for u in _units(doc) if u["outcome"] == step_cache.COMPUTED}


def _reused(doc: dict) -> set[str]:
    return {f"{u['kind']}:{u['name']}" if u["name"] else u["kind"]
            for u in _units(doc) if u["outcome"] == step_cache.REUSED}


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
    assert {"deck-source:where", "deck-review:what-changed", "deck-review:opening"} <= computed
    # Every computed unit has its own time, measured: a model step's is
    # never nothing.
    assert all(isinstance(u["seconds"], float) for u in _units(first))
    assert all(u["seconds"] > 0 for u in _units(first) if u["outcome"] == step_cache.COMPUTED
               and u["kind"] in ("research", "swarm-outline", "swarm-fill", "swarm-passes"))
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
    assert _computed(done) == {"swarm-revision:1",
                               "deck-source:what-changed", "deck-build",
                               "deck-review:what-changed", "deck",
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


def test_a_new_package_with_the_same_handoff_reuses_nothing(marked, deck_plan, keygen,  # noqa: F811
                                                            files_marked):
    first = _run(marked["id"], deck_plan)
    fresh = pj.accept(first["request"], session=first["session"], work=first["work"] or "",
                      engine=first["engine"] or "", submitted=first["submitted"])
    assert fresh["package"] != first["package"]
    turns = _turns(deck_plan)
    done = _run(fresh["id"], deck_plan)
    # Two packages are two problems (Tim, 2026-11-22): the second starts
    # fresh, every unit computed and every model asked, nothing reused.
    assert _reused(done) == set()
    assert {"gather", "research", "swarm-outline", "swarm-fill", "swarm-passes",
            "deck-header", "deck-build", "deck", "deliver"} <= _computed(done)
    assert _turns(deck_plan) > turns
    assert done["state"] == pj.WAITING
    assert step_cache.root(done) != step_cache.root(first)


# ── one input changed at a time: exactly what reads it is computed ────────

class BothMarkings(Marking):
    """The script marked by the test's Marking; a file of the evidence by
    the real MarkingSession on the test's roles (a changed file is marked
    again)."""

    def __init__(self, doc):
        self.real = package_mark._marking(doc)

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture
def files_marked(deck_plan, monkeypatch):  # noqa: F811
    monkeypatch.setattr(package_mark, "MARKING", BothMarkings)


def _again(first: dict, plan) -> dict:
    """The same package run again after an Error, nothing new asked."""
    pj.error(first["id"], "deliver", "stopped", "tried", "fix")
    return _run(pj.new_run(first["id"], "asked again")["id"], plan)




def test_a_changed_prompt_redoes_only_the_steps_of_that_role(marked, deck_plan, keygen,  # noqa: F811
                                                            monkeypatch):
    first = _run(marked["id"], deck_plan)
    prompt = role_prompts.PROMPTS["editor"]
    monkeypatch.setitem(role_prompts.PROMPTS, "editor",
                        dataclasses.replace(prompt, system=prompt.system + "\nOne more line."))
    done = _again(first, deck_plan)
    # The editor is in the swarm's stages and every slide's review, never in
    # mark or research, nor in the author's writing of the deck.
    assert _computed(done) == {"swarm-outline", "swarm-fill", "swarm-passes",
                               "deck-review:opening", "deck-review:what-changed",
                               "deck-review:where", "deck", "deliver"}
    assert {"research", "deck-header", "deck-source:where", "deck-build"} <= _reused(done)


def test_a_changed_engine_choice_redoes_the_role_s_steps(marked, deck_plan, keygen,  # noqa: F811
                                                        monkeypatch):
    from jarvis import engine_choice
    first = _run(marked["id"], deck_plan)
    real = engine_choice.choose
    monkeypatch.setattr(engine_choice, "choose", lambda kind, **kw: engine_choice.Choice(
        "codex", None, "fixed") if kind == "package_author" else real(kind, **kw))
    # Codex's live list, as its app server would give it.
    monkeypatch.setitem(step_cache._LISTED, "codex", ["gpt-6.2-astra", "gpt-6.2-sol",
                                                      "gpt-6.1-terra", "gpt-6-luna"])
    done = _again(first, deck_plan)
    assert {"research", "swarm-outline", "deck-header", "deck-source:where"} <= _computed(done)
    assert not [c for c in _computed(done) if c.startswith("mark-file")]


def test_a_change_to_the_deck_pipeline_s_code_redoes_every_unit_whose_code_imports_it(
        marked, deck_plan, keygen, files_marked, monkeypatch):  # noqa: F811
    first = _run(marked["id"], deck_plan)
    real = step_cache._path_sha
    monkeypatch.setattr(step_cache, "_path_sha", lambda path: "changed" if path.name ==
                        "presentation_proof.py" else real(path))
    done = _again(first, deck_plan)
    # The build is made again; so is every unit whose code imports the
    # proof at any depth (step_cache.closure), the model steps among them;
    # mark's code never does, and nothing is computed with its key kept.
    assert {"deck-build", "research"} <= _computed(done)
    importing = set(step_cache.closure(*step_cache.MODEL_CODE, *ps.SWARM_CODE))
    assert "jarvis.presentation_proof" in importing


def test_code_is_named_by_its_source_hash(tmp_path, monkeypatch):
    (tmp_path / "shaped_here.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    before = step_cache.code("shaped_here")
    assert before["shaped_here"] == step_cache.file_sha(tmp_path / "shaped_here.py")
    (tmp_path / "shaped_here.py").write_text("X = 2\n")
    os.utime(tmp_path / "shaped_here.py", ns=(time.time_ns() + 10**9,) * 2)
    assert step_cache.code("shaped_here") != before
    # Every deck pipeline module is in the build's key.
    assert set(pd.PIPELINE_CODE) >= {"jarvis.presentation", "jarvis.presentation_source",
                                     "jarvis.decision_package", "jarvis.presentation_proof",
                                     "jarvis.picture_proof", "jarvis.deck_capacity",
                                     "jarvis.presentation_data"}


def test_a_kept_deck_source_that_no_longer_builds_is_replaced_by_the_working_one(
        marked, deck_plan, keygen, monkeypatch):  # noqa: F811
    from jarvis import make_package
    first = _run(marked["id"], deck_plan)
    # What the build is made with changes (the language it is built in,
    # which no writing reads), and the kept source no longer builds with
    # it; the author's new deck does.
    language = make_package.built_language()
    monkeypatch.setattr(make_package, "built_language",
                        lambda: "es" if language != "es" else "en")
    compile_ = pd._compile

    def strict(run, out):
        if "Rewritten." not in pd.source_of(run).read_text():
            return ["slide what-changed: no longer fits JARVIS's layout"]
        return compile_(run, out)
    monkeypatch.setattr(pd, "_compile", strict)
    rewritten = deck_with_his_words()
    rewritten["slides"][1]["narration"][0]["say"] += " Rewritten."
    deck_plan.decks = [rewritten]
    done = _again(first, deck_plan)
    outcomes = {f"{u['kind']}:{u['name']}" if u["name"] else u["kind"]: u["outcome"]
                for u in _units(done) if u["kind"] in ("deck-header", "deck-source")}
    assert set(outcomes.values()) == {step_cache.REPLACED}, outcomes
    assert set(outcomes) == {"deck-header", "deck-source:opening", "deck-source:what-changed",
                             "deck-source:where"}
    # The next run reuses the working source: nothing is written again.
    turns = _turns(deck_plan)
    later = _again(done, deck_plan)
    assert _computed(later) == set() and _turns(deck_plan) == turns
    assert not [u for u in _units(later) if u["outcome"] == step_cache.REPLACED]


def test_the_sender_s_answers_to_a_deck_that_never_built_reach_the_author_s_writing(
        marked, deck_plan, keygen):  # noqa: F811
    broken = deck_with_his_words()
    broken["slides"][0]["layout"] = "no-such-layout"
    deck_plan.decks = [broken]
    first = _run(marked["id"], deck_plan)
    assert first["rounds"][-1]["kind"] == pj.FINDINGS
    assert first["rounds"][-1]["stage"] == pd.WRITE_LABEL
    pj.answer(first["id"], {q["id"]: "Use the title layout for the opening."
                            for q in first["rounds"][-1]["questions"]})
    later = pj.new_run(first["id"], "the sender answered the findings")
    deck_plan.decks = [deck_with_his_words()]
    sent = len(deck_plan.sent["author"])
    done = _run(later["id"], deck_plan)
    writing = [t for t in deck_plan.sent["author"][sent:] if "Now write the deck" in t
               or "Revise the deck" in t]
    assert writing and "Use the title layout for the opening." in writing[0]
    assert {"deck-header", "deck-source:opening", "deck-source:where"} <= _computed(done)
    assert done["state"] == pj.WAITING and pj.kind_of(done["rounds"][-1]) == pj.CHECK


def test_a_restored_stage_starts_every_participant_afresh_on_either_brain():
    kept = {"participants": {"author": {"session": "s1", "engine": "codex", "model": "m"},
                             "editor": {"session": "s2", "engine": "claude", "model": "m"}},
            "swarm": {"seen": {"author": 9, "editor": 9}, "introduced": ["author", "editor"]},
            "asking": {"answers_said": 3, "groups": []}}
    ps._fresh_sessions(kept)
    assert kept["participants"] == {}, "no thread kept by another run is resumed"
    assert set(kept["swarm"]["seen"].values()) == {0} and kept["swarm"]["introduced"] == []


def test_entries_unused_for_thirty_days_are_pruned():
    from jarvis import data_paths
    old = step_cache.Unit("research", "", {"n": 1})
    new = step_cache.Unit("research", "", {"n": 2})
    loose = {"package": "pkg-loose"}
    for_work = {"package": "pkg-w", "work": "w1"}
    step_cache.keep(old, loose, value=1)
    step_cache.keep(new, for_work, value=2)
    gone = time.time() - step_cache.PRUNE_AFTER_SEC - 60
    entry = step_cache.lookup(old, loose)
    os.utime(entry.path / step_cache.USED, (gone, gone))
    os.utime(step_cache.lookup(new, for_work).path / step_cache.USED, (gone, gone))
    step_cache.reuse(new, for_work)             # used now
    shared = data_paths.data_dir() / step_cache.SHARED_BEFORE / "research"
    shared.mkdir(parents=True)
    assert set(step_cache.caches()) == {step_cache.root(loose), step_cache.root(for_work)}
    assert step_cache.prune() == 1
    assert step_cache.lookup(old, loose) is None and step_cache.lookup(new, for_work) is not None
    assert not shared.parent.exists(), "the shared cache of before is removed"


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


# ── what a key covers that no list names ──────────────────────────────────

def test_code_covers_every_module_imported_at_any_depth(tmp_path, monkeypatch):
    pkg = tmp_path / "shaped"
    (pkg / "inner").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "top.py").write_text("from shaped import middle\n")
    (pkg / "middle.py").write_text("def f():\n    from .inner import deep\n    return deep\n")
    (pkg / "inner" / "__init__.py").write_text("")
    (pkg / "inner" / "deep.py").write_text("X = 1\n")
    (pkg / "unused.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    before = step_cache.code("shaped.top")
    # Read from the source, never imported: a lazy import in a function and
    # a relative one count; a module nothing imports does not.
    assert set(before) == {"shaped", "shaped.top", "shaped.middle", "shaped.inner",
                           "shaped.inner.deep"}
    (pkg / "inner" / "deep.py").write_text("X = 2\n")
    os.utime(pkg / "inner" / "deep.py", ns=(time.time_ns() + 10**9,) * 2)
    assert step_cache.code("shaped.top") != before


def test_an_imported_module_no_list_names_changes_the_build_key(monkeypatch):
    # package_story, package_inbox and package_understanding are imported
    # (some only inside functions) by the modules the build names.
    for unlisted in ("jarvis.package_story", "jarvis.package_inbox",
                     "jarvis.package_understanding"):
        assert unlisted not in pd.PIPELINE_CODE
        assert unlisted in step_cache.code(*pd.PIPELINE_CODE)
    before = pd.pipeline_digest()
    real = step_cache._path_sha
    monkeypatch.setattr(step_cache, "_path_sha", lambda path: "changed" if path.name ==
                        "package_story.py" else real(path))
    assert pd.pipeline_digest() != before


def test_a_changed_build_language_rebuilds_the_deck(marked, deck_plan, keygen,  # noqa: F811
                                                   monkeypatch):
    from jarvis import make_package
    first = _run(marked["id"], deck_plan)
    language = make_package.built_language()
    monkeypatch.setattr(make_package, "built_language",
                        lambda: "es" if language != "es" else "en")
    done = _again(first, deck_plan)
    assert "deck-build" in _computed(done)
    assert {"research", "deck-header", "deck-source:where"} <= _reused(done)


class LoggingRetrieval:
    """The test's retrieval, logging one GitHub lookup as its tool server
    would (package_gather.LOOKUPS_LOG)."""
    asked = 0

    def __init__(self, doc):
        self.doc = doc

    async def resolve(self, references, timeout):
        from jarvis import package_gather, research_log
        LoggingRetrieval.asked += 1
        research_log.record(package_gather.lookups_log(self.doc), "retrieval", "github_run_list",
                            {"repo": "4thWAIV/jarvis"}, True, FOUND["now"])
        return [{"id": r.id, "resolved": "ok", "searched": "github_run_list",
                 "entries": [{"type": "ci_run", "repo": "4thWAIV/jarvis", "id": "99881",
                              "why": "it failed"}]} for r in references]


FOUND = {"now": '[{"databaseId": 99881, "conclusion": "failure"}]'}


def test_retrieval_is_asked_again_when_what_its_lookups_find_changed(
        marked, deck_plan, keygen, monkeypatch):  # noqa: F811
    from jarvis import package_gather, role_mcp
    monkeypatch.setattr(package_gather, "RETRIEVAL", LoggingRetrieval)
    replayed = []

    def replay(role, tool, arguments):
        replayed.append((role, tool, arguments))
        return True, FOUND["now"]
    monkeypatch.setattr(role_mcp, "replay", replay)
    monkeypatch.setitem(FOUND, "now", FOUND["now"])
    first = _run(marked["id"], deck_plan)
    # The fixture's retrieval logged nothing: with what it was asked with
    # no longer kept, the package's evidence gathered again (a gather
    # stopped part way) asks the logging retrieval.
    import shutil
    shutil.rmtree(step_cache.root(first) / package_gather.LOOKUPS)
    LoggingRetrieval.asked = 0
    asked = _gathered_again(first, deck_plan)
    assert {"gather-lookups", "gather-labels"} <= _computed(asked)
    assert LoggingRetrieval.asked == 1
    n = LoggingRetrieval.asked
    # The same lookups find the same: retrieval's answer is reused, its
    # lookups made again first.
    same = _gathered_again(asked, deck_plan)
    assert "gather-labels" in _reused(same) and LoggingRetrieval.asked == n
    assert ("retrieval", "github_run_list", {"repo": "4thWAIV/jarvis"}) in replayed
    # What they find changed: retrieval is asked again.
    FOUND["now"] = '[{"databaseId": 99881, "conclusion": "success"}]'
    changed = _gathered_again(same, deck_plan)
    assert "gather-labels" in _computed(changed) and LoggingRetrieval.asked == n + 1


def _gathered_again(before: dict, plan) -> dict:
    """The same package run again with its evidence not yet gathered (a
    run that stopped while gathering): gather reads everything again."""
    from jarvis import records
    place = ps.pe.place_of(before)
    records.update(place.record, lambda d: {**(d or {}), "gathered_at": None},
                   by=records.JARVIS, change="test: gathered again", summary="test")
    return _again(before, plan)


# ── what runs a model step, and what signs ────────────────────────────────

MODEL_STEPS = {"research", "swarm-outline", "swarm-fill", "swarm-passes", "deck-header",
               "deck-source:where", "deck-review:where"}


def test_a_rerun_of_the_same_package_reuses_everything_unchanged(marked, deck_plan,  # noqa: F811
                                                                 keygen):
    first = _run(marked["id"], deck_plan)
    turns = _turns(deck_plan)
    done = _again(first, deck_plan)
    assert _computed(done) == set() and _turns(deck_plan) == turns
    assert MODEL_STEPS | {"deck-build", "deck", "deliver"} <= _reused(done)


def test_a_changed_resolved_model_recomputes_the_model_steps(marked, deck_plan, keygen,  # noqa: F811
                                                             files_marked, monkeypatch):
    from jarvis import model_tiers
    first = _run(marked["id"], deck_plan)
    before = step_cache.role_tool(ps.AUTHOR, first)["model"]
    # The same tier, the same engine choice: only the model it resolves to
    # now differs (a newer model in the brain's list).
    real = model_tiers.resolve
    monkeypatch.setattr(model_tiers, "resolve", lambda engine, family, listed: [
        f"{m}-next" for m in real(engine, family, listed)])
    assert step_cache.role_tool(ps.AUTHOR, first)["model"] == f"{before}-next"
    done = _again(first, deck_plan)
    assert MODEL_STEPS <= _computed(done)
    assert [c for c in _computed(done) if c.startswith("mark-file")], "the reading roles too"
    assert "deck-build" in _reused(done), "the build runs no model"


def test_a_model_refused_in_the_package_is_passed_over_in_its_keys(marked, deck_plan,  # noqa: F811
                                                                   keygen, files_marked):
    first = _run(marked["id"], deck_plan)
    # Marking's tier has a model above it for the sweep to move to.
    tool = step_cache.role_tool("marking", first)
    # A session of the package found the model refused, swept to the next:
    # the run's step notes it (run_report.refused), and every later pick of
    # the package, its keys and its sessions alike, passes over it.
    with run_report.step(first, "test"):
        run_report.refused(tool["engine"], tool["model"])
    assert step_cache.refused(first) == {tool["engine"]: [tool["model"]]}
    swept = step_cache.role_tool("marking", first)["model"]
    assert swept and swept != tool["model"]
    assert package_mark.pick_args(first)["refused"] == {tool["engine"]: [tool["model"]]}
    done = _again(first, deck_plan)
    # Marking reads every file and the swarm's script: those are made again
    # on the model the sweep ends on; the author's research is not.
    assert {"swarm-outline", "swarm-fill", "swarm-passes"} <= _computed(done)
    assert [c for c in _computed(done) if c.startswith("mark-file")]
    assert "research" in _reused(done)
    # Another package has seen no refusal.
    assert step_cache.refused({**first, "package": "pkg-other"}) == {}


def test_a_changed_signing_key_signs_again(marked, deck_plan, keygen, monkeypatch):  # noqa: F811
    from jarvis import package_deliver
    first = _run(marked["id"], deck_plan)
    monkeypatch.setattr(package_deliver, "signer_fingerprint", lambda: "SHA256:made-again")
    done = _again(first, deck_plan)
    assert _computed(done) == {"deliver"}, "only the delivery is signed again"


def test_the_deck_step_s_record_is_made_again_when_only_the_build_changed(
        marked, deck_plan, keygen, monkeypatch):  # noqa: F811
    first = _run(marked["id"], deck_plan)
    real = pd.pipeline_digest
    monkeypatch.setattr(pd, "pipeline_digest", lambda: real() + "-changed")
    done = _again(first, deck_plan)
    # The review's record carries the build's verdict: rebuilt, it is made
    # again; the slides' reviews are not.
    assert {"deck-build", "deck"} <= _computed(done)
    assert "deck-review:where" in _reused(done)
