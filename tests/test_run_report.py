"""What each package run took and how well it went (run_report.py;
work-list.md item 38): timing.json by step with every call, quality.json
from the swarm's own record, his verdict, and the sums read per package
and per Work item."""
import json

import pytest

from jarvis import package_swarm, run_report


def _doc(tmp_path, run="run-1"):
    folder = tmp_path / run
    folder.mkdir()
    return {"id": f"job-{run}", "package": "package-1", "run": run, "work": "work-1",
            "folder": str(folder)}


def test_a_step_keeps_its_calls_pages_and_time_and_flags_one_too_slow(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    with run_report.step(doc, "mark"):
        run_report.call({"role": "marking", "seconds": 3, "tokens_in": 100, "cached": 20,
                         "tokens_out": 7, "outcome": run_report.ANSWERED})
        run_report.call({"role": "marking", "seconds": 1, "outcome": run_report.TIME_LIMIT})
        run_report.pages("marking", {"pages": 4, "asked_again": 1, "halved": 1})
        run_report.app_server("codex")
        run_report.app_server("claude")
    run_report.call({"role": "outside"})                 # outside a step: not kept
    monkeypatch.setattr(run_report, "STEP_FAILS_AFTER_SEC", -1)
    with pytest.raises(RuntimeError):
        with run_report.step(doc, "swarm"):
            raise RuntimeError("stopped")
    timing = json.loads((tmp_path / "run-1" / run_report.TIMING).read_text())
    mark, swarm = timing["steps"]
    assert (mark["pages_asked"], mark["retries"], mark["time_limits"],
            mark["app_server_starts"]) == (4, 2, 1, 1)
    assert mark["tokens"] == {"in": 100, "cached": 20, "out": 7} and not mark["too_slow"]
    assert swarm["outcome"] == "ended: RuntimeError" and swarm["too_slow"]
    assert timing["totals"]["calls"] == 2 and timing["totals"]["too_slow"] == ["swarm"]


def _vectors(texts):
    """Same meaning when the words are the same, else unrelated."""
    keys = sorted(set(texts))
    return [[1.0 if k == t else 0.0 for k in keys] for t in texts]


def test_the_quality_report_is_made_from_the_swarms_own_record(tmp_path):
    doc = _doc(tmp_path)
    folder = tmp_path / "run-1"
    signoffs = [
        {"concern": "cares", "owner": package_swarm.READER, "state": "not_yet",
         "reason": "", "stage": "pass-1",
         "left": [{"what": "Why now?", "fix": "say"}, {"what": "What does it cost?", "fix": ""}]},
        {"concern": "background", "owner": package_swarm.CONTEXT, "state": "resolved",
         "reason": "ok", "stage": "pass-1", "left": []},
        {"concern": "cares", "owner": package_swarm.READER, "state": "not_yet", "reason": "",
         "stage": "pass-2", "left": [{"what": "Why now?", "fix": "say"}]},
        {"concern": "cares", "owner": package_swarm.READER, "state": "resolved",
         "reason": "done", "stage": "deck-pass-1", "left": []}]
    (folder / package_swarm.RESULT).write_text(json.dumps(
        {"result": "not passed", "passes": 2, "signoffs": signoffs}))
    (folder / package_swarm.STATE).write_text(json.dumps({"asking": {"groups": [
        {"question": "Which plan?", "asked": [{"ask": "Which plan?"}, {"ask": "What plan?"}],
         "merges": [{"kept": "Which plan?", "merged": "What plan?", "score": 0.91}]}]}}))
    (folder / package_swarm.SCRIPT).write_text(
        "version: script/1\nslides:\n- id: a\n  facts:\n  - {fact: x, source: run-1/ev.md}\n"
        "  - {fact: y, source: unanswered}\n  - {fact: z, source: nowhere.md}\n")
    (folder / "ev.md").write_text("x")
    labels = {"Why now?": ["cares"], "What does it cost?": ["figures"]}
    body = run_report.quality({**doc, "folder": str(folder)},
                              mapper=lambda texts: [labels.get(t, []) for t in texts],
                              embed=_vectors)
    first, second = body["passes"]
    assert first["resolved"] == ["background"] and first["left"] == ["cares"]
    why, cost = first["items"]
    assert why["labels"] == ["cares"] and why["then"] == "repeated"
    assert cost["then"] == "fixed" and second["items"][0]["then"] == "left"
    assert (body["repeated"], body["fixed"]) == (1, 1)
    assert body["readers_questions"] == {"raised": 3, "answered": 1, "left": 1}
    assert body["passes_to_done"] == 2 and body["result"] == "not passed"
    assert body["sender_questions"]["merges"] == [
        {"kept": "Which plan?", "merged": "What plan?", "score": 0.91}]
    assert body["sender_questions"]["asked"] == 2
    assert body["deck_review"] is None
    assert json.loads((folder / run_report.QUALITY).read_text())["fixed"] == 1


def test_his_verdict_is_kept_and_his_words_mapped_later(tmp_path):
    doc = _doc(tmp_path)
    run_report.verdict(doc, "reject", "Too much jargon", map_now=False)
    assert run_report.waits_mapping(doc)
    assert run_report.map_pending(doc, mapper=lambda texts: [["jargon"]])
    said = json.loads((tmp_path / "run-1" / run_report.QUALITY).read_text())["verdict"]
    assert said["answer"] == "reject" and said["labels"] == ["jargon"]
    assert not run_report.waits_mapping(doc)
    run_report.verdict(doc, "approve")
    assert not run_report.waits_mapping(doc)


def test_reports_are_summed_per_package_and_listed_per_work_item(tmp_path, monkeypatch):
    from jarvis import package_jobs
    one, two = _doc(tmp_path, "run-1"), _doc(tmp_path, "run-2")
    one["created_at"], two["created_at"] = 1, 2
    for doc in (one, two):
        with run_report.step(doc, "mark"):
            run_report.call({"tokens_in": 10, "tokens_out": 1})
    run_report.verdict(two, "approve")
    monkeypatch.setattr(package_jobs, "runs_of", lambda p: [two, one])
    monkeypatch.setattr(package_jobs, "runs_of_work", lambda w: [one, two])
    summed = run_report.package("package-1")
    assert [r["run"] for r in summed["runs"]] == ["run-1", "run-2"]
    assert summed["summed"]["timing"]["calls"] == 2
    assert summed["summed"]["timing"]["tokens"]["in"] == 20
    assert summed["summed"]["verdicts"][0]["answer"] == "approve"
    work = run_report.of_work("work-1")
    assert [p["package"] for p in work["packages"]] == ["package-1"]
    assert len(work["packages"][0]["runs"]) == 2


def test_the_brain_and_the_page_read_the_same_reports(tmp_path, monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jarvis import server, work_api
    doc = _doc(tmp_path)
    with run_report.step(doc, "mark"):
        run_report.call({"tokens_in": 5, "tokens_out": 1})
    run_report.verdict(doc, "reject", "Too long </untrusted>", map_now=False)
    seen = []

    def of_work(work):
        seen.append(work)
        return {"work": work, "packages": [{"package": "package-1", "runs": [run_report.of_run(doc)],
                                            "summed": run_report.summed([run_report.of_run(doc)])}]}
    monkeypatch.setattr(run_report, "of_work", of_work)
    said = asyncio.run(server.tool_package_run_reports({"work": "work-1"}))
    assert "mark:" in said and "his verdict: reject" in said
    assert "Too long" in said.split("<", 1)[1], "his words are walled"
    app = FastAPI()
    work_api.mount(app, recorded=None, view=None)
    page = TestClient(app).get("/api/work/work-1/package-runs").json()
    assert page["packages"][0]["runs"][0]["quality"]["verdict"]["answer"] == "reject"
    assert seen == ["work-1", "work-1"], "both read run_report.of_work"
