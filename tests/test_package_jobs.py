"""Package requests and their runs (package_request.py, package_jobs.py,
package_jobs_api.py): the sender's typed handoff, checked at once; JARVIS
taking a signed request, recording who asked and the job with its run, and
running it itself; and the Error state, with everyone told."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from jarvis import package_jobs as pj, package_request as pr, records
from tests.agent_key_fixtures import Agent
from tests.test_agent_key_packages import _start_work
from tests.test_agent_keys import DIAG, PLAN, keygen, wired  # noqa: F401

ANSWERS = {"plainly": "PR 171 moves Work items into folders. CI passed on it. I do not know "
                      "whether the move is fast on 5,000 items.",
           "why_now": "The package system waits on it; without his yes nothing ships."}


def handoff(tmp_path, **changes) -> dict:
    situation = tmp_path / "situation.md"
    situation.write_text("What happened, and where things stand.\n")
    value = {"sender_answers": dict(ANSWERS),
             "questions": [{"id": "merge-171", "text": "Merge PR 171?",
                            "options": [{"id": "merge", "name": "Merge", "says": "Merge it."},
                                        {"id": "wait", "name": "Wait", "says": "Wait a day."}]},
                           {"id": "move-live", "text": "Move the live data tonight?"}],
             "evidence": [{"type": "file", "path": str(situation)},
                          {"type": "pr", "repo": "4thWAIV/jarvis", "number": 171},
                          {"type": "ci_run", "repo": "4thWAIV/jarvis", "id": 99881},
                          {"type": "commit", "repo": "4thWAIV/jarvis", "sha": "ff74f106"},
                          {"type": "content", "text": "  Kept exactly as given.  "},
                          {"type": "label", "text": "the CI run that failed yesterday"}],
             "purpose": "I need his yes to merge, then I move the live data.",
             "recommendation": "Merge it."}
    value.update(changes)
    return value


@pytest.fixture
def spawned(monkeypatch):
    """The runs that would have started a process of JARVIS's own."""
    started = []

    def spawn(doc):
        started.append(doc["id"])
        return 4242
    monkeypatch.setattr(pj, "SPAWN", spawn)
    return started


def ask(client, agent, body, work=""):
    return agent.post(client, pr.ROUTE + (f"?work={work}" if work else "?general=1"), body)


# -- the handoff ------------------------------------------------------------------------

def test_a_handoff_is_kept_typed_and_with_the_senders_words_labelled(tmp_path):
    kept = pr.check(handoff(tmp_path))
    assert [q["id"] for q in kept["questions"]] == ["merge-171", "move-live"], "several questions"
    assert kept["questions"][0]["options_from_the_sender"][0]["id"] == "merge"
    assert kept["questions"][1]["options_from_the_sender"] == []
    sender = kept["from_the_sender"]
    assert sender["label"] == pr.FROM_THE_SENDER == "the sender's"
    assert sender["purpose"].startswith("I need his yes")
    assert sender["recommendation"] == "Merge it."
    assert sender["answers"]["plainly"] == {"question": pr.QUESTIONS["plainly"],
                                            "answer": ANSWERS["plainly"]}
    assert sender["answers"]["why_now"]["question"] == pr.QUESTIONS["why_now"]
    by_type = {e["type"]: e for e in kept["evidence"]}
    assert by_type["file"]["sha256"] and by_type["file"]["size"] > 0, "hashed as it is taken"
    assert by_type["pr"]["number"] == 171 and by_type["ci_run"]["id"] == "99881"
    assert by_type["content"]["text"] == "  Kept exactly as given.  "
    assert by_type["label"]["text"] == "the CI run that failed yesterday"


def test_the_two_questions_are_tims_words_exactly():
    assert pr.QUESTIONS["plainly"] == (
        "State plainly and exactly what you are telling us. Name each thing you mention as what "
        "it specifically is, and keep separate things separate: do not merge two problems, "
        "results or decisions into one. Say what you know, what you only believe, and what you "
        "do not know. Leave out wording meant to sound technical, confident or complete.")
    assert pr.QUESTIONS["why_now"] == (
        "Why does Tim need this now? What happened, what happens if he does nothing, and what "
        "changes for him with each answer.")


def test_every_one_of_the_fourteen_types_is_taken(tmp_path):
    from jarvis import evidence_types
    assert len(evidence_types.FIELDS) == 14
    f = tmp_path / "f.txt"
    f.write_text("x")
    sample = {"file": {"path": str(f)}, "url": {"url": "https://example.com/a"},
              "content": {"text": "t"}, "work_item": {"id": "work-1"}, "package": {"id": "p"},
              "message": {"id": 12}, "workflow": {"id": "wf-1"}, "run": {"id": "r-1"},
              "pr": {"repo": "o/r", "number": "3"}, "ci_run": {"repo": "o/r", "id": "7"},
              "commit": {"repo": "o/r", "sha": "abcdef1"}, "issue": {"repo": "o/r", "number": 4},
              "branch": {"repo": "o/r", "name": "main"}, "label": {"text": "that thing"}}
    assert set(sample) == set(evidence_types.FIELDS)
    kept = pr.check(handoff(tmp_path, evidence=[{"type": t, **v} for t, v in sample.items()]))
    assert [e["type"] for e in kept["evidence"]] == list(sample)


@pytest.mark.parametrize("change, said", [
    ({"sender_answers": None}, "needs sender_answers"),
    ({"sender_answers": {"plainly": "x"}}, "sender_answers needs why_now"),
    ({"questions": []}, "needs questions"),
    ({"question": "Merge?"}, "no longer takes question"),
    ({"decisions": []}, "questions and decisions are one list"),
    ({"situation": "x"}, "give the situation as an evidence entry"),
    ({"conclusion": "x"}, "may not give conclusion"),
    ({"audience": "Tim"}, "may not give audience"),
    ({"slides": []}, "may not give slides"),
    ({"says": "hello"}, "may not give says"),
    ({"evidence": []}, "needs evidence"),
    ({"evidence": ["a/file.md"]}, "evidence entry 1 is an object"),
    ({"evidence": [{"type": "path", "path": "/x"}]}, "type is one of"),
    ({"evidence": [{"type": "file", "path": "relative.md"}]}, "path is absolute"),
    ({"evidence": [{"type": "file", "path": "/no/such/file.md"}]}, "there is no file at"),
    ({"evidence": [{"type": "pr", "repo": "jarvis", "number": 1}]}, "repo is owner/name"),
    ({"evidence": [{"type": "pr", "repo": "o/r", "number": "one"}]}, "a whole number"),
    ({"evidence": [{"type": "commit", "repo": "o/r", "sha": "zzz"}]}, "a commit's hex id"),
    ({"evidence": [{"type": "url", "url": "file:///etc/hosts"}]}, "http or https"),
    ({"evidence": [{"type": "work_item", "id": "w", "title": "x"}]}, "takes only id"),
    ({"purpose": ""}, "purpose, when given"),
])
def test_a_bad_handoff_is_refused_saying_why(tmp_path, change, said):
    with pytest.raises(pr.Refused) as refused:
        pr.check(handoff(tmp_path, **change))
    assert any(said in p for p in refused.value.problems), refused.value.problems


# -- accepting a request ----------------------------------------------------------------

def test_a_real_request_is_accepted_recorded_with_its_run_and_started(wired, tmp_path,
                                                                     spawned):
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    work = _start_work(client, agent, folder)
    r = ask(client, agent, {"handoff": handoff(tmp_path), "project": "jarvis"}, work=work)
    assert r.status_code == 200, r.text
    taken = r.json()
    assert taken["state"] == pj.QUEUED and taken["run"] == "run-1"
    assert taken["package"].startswith("package-")
    assert spawned == [taken["job"]], "JARVIS starts the run itself"
    doc = pj.get(taken["job"])
    path = f"/work/{work}/packages/{taken['package']}/runs/run-1/run.job"
    assert pj.path_of(doc) == path and records.get(path).data["id"] == taken["job"]
    assert doc["requested_by"] == f"agent:{PLAN}", "who asked, from the signature"
    assert doc["pid"] == 4242 and doc["work"] == work and doc["project"] == "jarvis"
    assert doc["request"]["questions"][0]["id"] == "merge-171"
    assert doc["submitted"]["sender_answers"] == ANSWERS, "the handoff as submitted"
    from jarvis import data_paths
    assert doc["folder"] == str(data_paths.work_dir() / work / "packages" / taken["package"]
                                / "runs" / "run-1")
    first = records.history(path)[0]
    assert "jarvis" in (first.by, first.agent), "JARVIS's own record"


def test_a_request_for_no_work_item_lives_under_packages(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    r = ask(client, agent, {"handoff": handoff(tmp_path)})
    assert r.status_code == 200, r.text
    taken = r.json()
    doc = pj.get(taken["job"])
    assert pj.path_of(doc) == f"/packages/{taken['package']}/runs/run-1/run.job"
    from jarvis import data_paths
    assert doc["folder"] == str(data_paths.package_dir() / taken["package"] / "runs" / "run-1")


def test_a_bad_request_is_refused_at_once_and_nothing_is_recorded(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    bad = handoff(tmp_path, question="Merge?", conclusion="Merge it.")
    bad.pop("sender_answers")
    r = ask(client, agent, {"handoff": bad})
    assert r.status_code == 400
    problems = r.json()["problems"]
    assert any("sender_answers" in p for p in problems)
    assert any("conclusion" in p for p in problems) and any("question" in p for p in problems)
    assert spawned == [] and pj._jobs([["state", "in", list(pj.STATES)]]) == []
    r = ask(client, agent, {"handoff": handoff(tmp_path)}, work="work-nonesuch")
    assert r.status_code == 404 and spawned == []


def test_only_an_onboarded_agent_s_own_key_asks(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    stranger = Agent(home, DIAG)               # its key never onboarded
    r = ask(client, stranger, {"handoff": handoff(tmp_path)})
    assert r.status_code in (401, 403) and spawned == []
    r = client.post(pr.ROUTE + "?general=1", json={"handoff": handoff(tmp_path)},
                    headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 403 and spawned == []


def test_a_sender_follows_and_answers_only_its_own_job(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent, other = Agent(home, PLAN).onboarded(), Agent(home, DIAG).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    assert agent.get(client, f"{pr.ROUTE}/{job}").json()["state"] == pj.QUEUED
    assert other.get(client, f"{pr.ROUTE}/{job}").status_code == 403
    assert pj.ask(job, [{"id": "which-ci", "question": "Which CI run failed?"}])
    assert agent.get(client, f"{pr.ROUTE}/{job}").json()["state"] == pj.WAITING
    assert other.post(client, f"{pr.ROUTE}/{job}/answer",
                      {"answers": {"which-ci": "99881"}}).status_code == 409
    r = agent.post(client, f"{pr.ROUTE}/{job}/answer", {"answers": {"which-ci": "99881"}})
    assert r.status_code == 200 and r.json()["state"] == pj.QUEUED
    assert spawned == [job, job], "the run goes on in JARVIS's own process"
    assert pj.get(job)["rounds"][-1]["answers"] == {"which-ci": "99881"}


def test_at_most_five_rounds_of_questions_are_asked(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    for n in range(pj.MAX_ROUNDS):
        assert pj.ask(job, [{"id": f"q{n}", "question": "?"}])
        pj.answer(job, {f"q{n}": "a"}, session=PLAN)
    assert pj.MAX_ROUNDS == 5
    assert pj.ask(job, [{"id": "q6", "question": "?"}]) is False


# -- the Error state --------------------------------------------------------------------

def gather_blocked(doc):
    """A gather step that cannot go on (named in PRODUCERS by a test)."""
    raise pj.Blocked("gather", "The evidence was not indexed within 10 minutes.",
                     "JARVIS indexed it urgently and waited 10 minutes.",
                     "Free the knowledge engine, then a new run.")


def test_a_blocked_run_is_in_error_with_everyone_told(wired, tmp_path, spawned, monkeypatch):
    server, _hints, store, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    work = _start_work(client, agent, folder)
    job = ask(client, agent, {"handoff": handoff(tmp_path)}, work=work).json()["job"]

    monkeypatch.setitem(pj.PRODUCERS, "gather", f"{__name__}:gather_blocked")
    assert pj.run(job) == 1
    doc = pj.get(job)
    assert doc["state"] == pj.ERROR and doc["told"] is False
    assert doc["error"]["step"] == "gather"
    server._look_after_package_jobs()
    # Tim's notice: the Work item is in Error, saying the step and why.
    from jarvis import work_store
    item = work_store.get(work)
    assert item.state == work_store.ERROR
    assert "gather" in item.headline and "not indexed within 10 minutes" in item.headline
    # JARVIS is told: an update with the step, and the agent's words walled.
    told = store.get("package_job", job)
    assert told is not None and told.event == "error" and told.facts["step"] == "gather"
    assert told.untrusted["fix"].startswith("Free the knowledge engine")
    # The agent is told on its job: the step, why, what was tried, what would fix it.
    seen = agent.get(client, f"{pr.ROUTE}/{job}").json()
    assert seen["state"] == pj.ERROR and set(seen["error"]) >= {"step", "reason", "tried", "fix"}
    assert pj.get(job)["told"] is True
    server._look_after_package_jobs()
    assert len([e for e in work_store.events(work) if e.kind == work_store.ERROR_RAISED]) == 1, \
        "told once"
    # A new run of the package clears it.
    after = server._new_package_run(job, "the agent fixed the index")
    assert (after["package"], after["run"]) == (doc["package"], "run-2")
    assert work_store.get(work).state != work_store.ERROR
    assert store.get("package_job", job).state != "pending"


def test_a_step_not_built_yet_is_an_error_not_a_silent_stop(wired, tmp_path, spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    assert pj.run(job) == 1
    error = pj.get(job)["error"]
    assert error["step"] == "gather" and "not built" in error["reason"]


def test_with_no_work_item_the_error_is_a_message_to_tim(wired, tmp_path, spawned):
    server, _hints, store, _folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    pj.error(job, "swarm", "The sender did not answer the questions within 2 hours.",
             "JARVIS asked 2 questions and waited 2 hours.", "The sender answers.")
    server._look_after_package_jobs()
    from jarvis import messages_api
    general = messages_api.general_list(None)
    said = [m for m in general["messages"] if m["from"]["sender"] == "jarvis"]
    assert len(said) == 1 and "swarm" in said[0]["says"]
    assert said[0]["from"]["name"] == "JARVIS" and said[0]["from"]["proof"] == "jarvis"
    assert store.get("package_job", job).event == "error"


def test_a_sender_that_does_not_answer_in_two_hours_puts_the_job_in_error(wired, tmp_path,
                                                                          spawned):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    pj._change(job, "swarm", lambda d: d.update(step="swarm"))
    pj.ask(job, [{"id": "which", "question": "Which one?"}])
    assert pj.overdue(time.time() + pj.SENDER_WAIT_SEC - 60) == []
    [moved] = pj.overdue(time.time() + pj.SENDER_WAIT_SEC + 1)
    assert moved["state"] == pj.ERROR and moved["error"]["step"] == "swarm"
    assert "2 hours" in moved["error"]["reason"]
    with pytest.raises(pj.JobError):
        pj.answer(job, {"which": "late"}, session=PLAN)


def test_a_run_whose_process_ended_is_in_error(wired, tmp_path, spawned, monkeypatch):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    job = ask(client, agent, {"handoff": handoff(tmp_path)}).json()["job"]
    monkeypatch.setattr(pj, "_alive", lambda pid: False)
    [moved] = pj.overdue()
    assert moved["id"] == job and "ended without finishing" in moved["error"]["reason"]


def test_more_than_one_package_in_a_hundred_in_error_is_a_major_problem():
    assert pj.major_problem(1, 100) is False
    assert pj.major_problem(2, 100) is True
    assert pj.major_problem(0, 0) is False


# -- the sender's side ------------------------------------------------------------------

def test_start_sends_the_handoff_signed_to_jarvis(tmp_path, monkeypatch):
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(handoff(tmp_path)))
    sent = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def report(client, route, body):
        sent.update(route=route, body=body)
        return {"job": "job-1", "package": "package-1", "run": "run-1", "state": "queued"}
    monkeypatch.setattr(pr, "_client", lambda session: sent.update(session=session) or Client())
    monkeypatch.setattr("jarvis.workflow_report.report", report)
    out = pr.start(path, session=PLAN, work="work-1", workflow="wf-1",
                   documents=(("doc-1", "rev-1"),))
    assert out["job"] == "job-1" and sent["session"] == PLAN
    assert sent["route"] == pr.ROUTE + "?work=work-1"
    assert sent["body"]["handoff"]["sender_answers"] == ANSWERS
    assert sent["body"]["documents"] == [["doc-1", "rev-1"]]


def test_make_package_has_no_says_any_more(capsys):
    from jarvis import make_package as mp
    with pytest.raises(SystemExit):
        mp.main(["--start", "--handoff", "h.json", "--session", PLAN, "--says", "hello"])
    assert "--says" in capsys.readouterr().err


def test_the_agent_tools_ask_jarvis(monkeypatch, tmp_path):
    from jarvis import agent_mcp
    monkeypatch.delenv("JARVIS_AGENT_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    calls = []
    monkeypatch.setattr(pr, "start", lambda handoff, **kw: calls.append(("start", kw)) or
                        {"job": "job-1"})
    monkeypatch.setattr(pr, "status", lambda job, **kw: calls.append(("status", job, kw)) or
                        {"state": "error"})
    ok, text, _ = agent_mcp.call("package_start", {"handoff": str(tmp_path / "h.json"),
                                                   "work": "work-1", "as_session": PLAN})
    assert ok and json.loads(text) == {"job": "job-1"}
    assert calls[0][1] == {"session": PLAN, "work": "work-1", "general": False}
    ok, text, _ = agent_mcp.call("package_status", {"job": "job-1", "as_session": PLAN})
    assert ok and calls[1] == ("status", "job-1", {"session": PLAN})


def test_jarvis_has_its_own_key_made_once_in_its_data_folder(keygen):
    from jarvis import agent_keys, data_paths
    private, public = agent_keys.jarvis_key()
    assert private == data_paths.data_dir() / "jarvis" / "agent-keys" / "jarvis" / "id_ed25519"
    assert public.startswith("ssh-ed25519 ")
    assert agent_keys.jarvis_key() == (private, public), "made once"
    signature = agent_keys.sign(private, agent_keys.PACKAGE, b"bytes")
    assert agent_keys.verify(public, agent_keys.PACKAGE, b"bytes", signature)
