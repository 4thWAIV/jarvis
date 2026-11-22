"""The gather step (package_gather.py, package_evidence.py, evidence_readers.py;
workflow.yaml gather): a real package's evidence folder and its index.

Each typed entry resolved by its program (a GitHub address as its own type,
a reference named twice one file), the labels to RetrievalSession, JARVIS's
own material, Tim's knowledge state by program, the manifest, the evidence
indexed urgently with its vectors and found by a search limited to its path,
and the package's record linking to each file and record it gathered;
gathered once per package and reused by every run."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jarvis import (evidence_readers, package_evidence as pe, package_gather, package_jobs as pj,
                    package_request as pr, records, session_watch, work_store)
from jarvis.knowledge import embedder as emb
from jarvis.knowledge import paths as item_paths
from jarvis.knowledge.uds import Caller, Query, Stream
from tests.settings_fixtures import hold_setting

TIM = Caller("tim")
SESSION = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def indexing(monkeypatch):
    hold_setting("knowledge_enabled", "1")
    found = emb.FakeEmbedder(32)
    monkeypatch.setattr(emb, "RUNNER", lambda: found)
    monkeypatch.setattr(pe, "INDEX_LOOK_SEC", 0.05)
    return found


@pytest.fixture
def gh(monkeypatch):
    """GitHub, answered from a table: argument list -> output."""
    calls = []

    def answer(args):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return True, json.dumps({"number": 171, "title": "Work items in folders",
                                     "state": "MERGED", "body": "Moves every Work item."})
        if args[:2] == ["pr", "diff"]:
            return True, "diff --git a/x b/x\n+moved\n"
        if args[:2] == ["run", "view"] and "--log-failed" in args:
            return True, "FAILED tests/test_x.py::test_move - AssertionError"
        if args[:2] == ["run", "view"]:
            return True, json.dumps({"databaseId": 99881, "name": "ci", "conclusion": "failure",
                                     "jobs": [{"name": "test", "conclusion": "failure"}]})
        return False, "GitHub answered: not_found: gh: Not Found (HTTP 404)"
    monkeypatch.setattr(evidence_readers, "_gh", answer)
    return calls


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git checkout of 4thWAIV/jarvis with one commit; its hash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,  # noqa: E731
                                    capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("remote", "add", "origin", "git@github.com:4thWAIV/jarvis.git")
    (repo / "move.py").write_text("print('moved')\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Move Work items")
    monkeypatch.setattr(evidence_readers, "CHECKOUTS", lambda: [repo])
    return run("rev-parse", "HEAD")


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    """The sending session's own Claude Code transcript."""
    root = tmp_path / "claude"
    folder = root / "projects" / "-x"
    folder.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"role": "user", "content": "Move the Work items."},
         "timestamp": "2026-11-20T10:00:00Z"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pytest tests/test_work_folders.py -q"}}]},
         "timestamp": "2026-11-20T10:00:01Z"},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "12 passed in 3.1s"}]},
         "timestamp": "2026-11-20T10:00:05Z"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working on it."}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "All 12 tests pass; the move is ready."}]}},
    ]
    (folder / f"{SESSION}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    monkeypatch.setattr(session_watch, "config_roots", lambda: [root])
    return folder / f"{SESSION}.jsonl"


class FakeRetrieval:
    """RetrievalSession, answering from what the test says each label names."""

    def __init__(self, named: dict[str, list[dict]]):
        self.named, self.asked = named, []

    async def resolve(self, references, timeout):
        self.asked.append([r.text for r in references])
        out = []
        for r in references:
            entries = self.named.get(r.text, [])
            out.append({"id": r.id, "resolved": "ok" if entries else "not_found",
                        "entries": entries, "searched": "work_status, github_run_list"})
        return out


def _work(tmp_path) -> str:
    source = tmp_path / "speech"
    source.mkdir(exist_ok=True)
    item = work_store.create("Work items in folders", "folder", str(source))
    work_store.record(work_store.WorkEvent(item.id, "piece", "Built the move.", {
        "key": "move", "title": "The move", "who": {"kind": "tim"}, "state": "working",
        "evidence": ["https://github.com/4thWAIV/jarvis/pull/171",
                     "https://github.com/4thWAIV/jarvis/actions/runs/99881"]}))
    return item.id


def _job(tmp_path, work: str, evidence: list[dict], **more) -> dict:
    handed = {"sender_answers": {"plainly": "PR 171 moves the Work items.",
                                 "why_now": "The package system waits on it."},
              "questions": [{"id": "merge-171", "text": "Merge PR 171?"}],
              "evidence": evidence, **more}
    request = pr.check(handed)
    return pj.accept(request, session=SESSION, work=work, engine="claude", submitted=handed)


def _evidence(tmp_path, checkout) -> list[dict]:
    situation = tmp_path / "situation.md"
    situation.write_text("# Situation\n\nThe Work items moved into folders on the copy.\n")
    (tmp_path / "gone.md").write_text("Removed after the request was taken.\n")
    return [{"type": "file", "path": str(situation)},
            {"type": "file", "path": str(tmp_path / "gone.md")},
            {"type": "content", "text": "The live data is untouched."},
            {"type": "pr", "repo": "4thWAIV/jarvis", "number": 171},
            {"type": "url", "url": "https://github.com/4thWAIV/jarvis/pull/171"},
            {"type": "commit", "repo": "4thWAIV/jarvis", "sha": checkout[:10]},
            {"type": "label", "text": "the CI run that failed yesterday"},
            {"type": "label", "text": "the meeting notes nobody wrote"}]


def test_a_real_package_s_evidence_is_gathered_indexed_and_linked(tmp_path, gh, checkout,
                                                                    transcript, monkeypatch):
    work = _work(tmp_path)
    doc = _job(tmp_path, work, _evidence(tmp_path, checkout))
    (tmp_path / "gone.md").unlink()
    retrieval = FakeRetrieval({"the CI run that failed yesterday": [
        {"type": "ci_run", "repo": "4thWAIV/jarvis", "id": "99881", "why": "it failed yesterday"}]})
    monkeypatch.setattr(package_gather, "RETRIEVAL", lambda d: retrieval)

    package_gather.gather(pj.get(doc["id"]))

    place = pe.place_of(doc)
    assert place.folder == (Path(doc["folder"]).parents[1] / "evidence")
    assert place.path == f"/work/{work}/packages/{doc['package']}/evidence"
    stored = sorted(str(p.relative_to(place.folder)) for p in place.folder.rglob("*")
                    if p.is_file())
    assert stored == ["files/situation.md", "given/3.md", "handoff.json",
                      "jarvis/sender-session.md", "jarvis/tim.md", "jarvis/work-item.md",
                      "manifest.json", "retrieved/ci_run-99881.md",
                      f"retrieved/commit-{checkout[:10]}.md", "retrieved/pr-171.md"]
    rows = {r.id: r for r in pe.read_manifest(place)}
    # A file not there, and a label naming nothing: not_found, saying why.
    assert rows["e2"].resolved == "not_found" and "There is no file" in rows["e2"].reason
    assert rows["e8"].resolved == "not_found" and "work_status" in rows["e8"].reason
    # The PR named three times (the handoff, its address, the Work item) is one file.
    assert sum(1 for r in rows.values() if r.stored == "retrieved/pr-171.md") == 1
    assert (place.folder / "retrieved/pr-171.md").read_text().count("Moves every Work item.") == 1
    assert "+moved" in (place.folder / "retrieved/pr-171.md").read_text()
    assert "FAILED tests/test_x.py" in (place.folder / "retrieved/ci_run-99881.md").read_text()
    assert "Move Work items" in (place.folder / f"retrieved/commit-{checkout[:10]}.md").read_text()
    assert (place.folder / "given/3.md").read_text() == "The live data is untouched."
    # The label went to retrieval in one call; what it found is a row of its type.
    assert retrieval.asked == [["the CI run that failed yesterday",
                                "the meeting notes nobody wrote"]]
    # The sender's session: its commands with their results and its final
    # message, not its whole transcript.
    session = (place.folder / "jarvis/sender-session.md").read_text()
    assert "pytest tests/test_work_folders.py -q" in session and "12 passed" in session
    assert "All 12 tests pass" in session and "Working on it." not in session
    assert "/x" not in session                              # a Read is no command
    work_text = (place.folder / "jarvis/work-item.md").read_text()
    assert "The move" in work_text and "Built the move." in work_text
    # Tim's knowledge state, by program from where each item came.
    states = {r.stored: r.knowledge_state for r in rows.values() if r.stored}
    assert states["jarvis/tim.md"] == pe.KNOWS
    assert states["jarvis/work-item.md"] == pe.HAD_ACCESS
    assert states["retrieved/ci_run-99881.md"] == pe.PROBABLY_NOT
    assert states["jarvis/sender-session.md"] == pe.PROBABLY_NOT
    assert set(states.values()) <= set(pe.STATES)
    # Every stored file is an item at the evidence path, indexed, its vectors made.
    uds = records.uds()
    items = [i.path for i in uds.scan(TIM, place.path, streams={Stream.DATA}, limit=100)]
    assert len(items) == len(stored)
    for path in items:
        item = uds.get(TIM, path, {Stream.DATA, Stream.VECTORS})
        assert item.data["state"] == "indexed" and item.vectors["complete"], path
    # A search limited to the evidence path finds only the package's evidence.
    hits = uds.search(TIM, Query(words="Work items moved folders copy", under=place.path))
    assert hits and all(item_paths.is_under(h.path, place.path) for h in hits)
    assert any(h.path.endswith("files/situation.md") for h in hits)
    # The package links to each file and record it gathered.
    record = records.get(place.record).data
    assert set(record["gathered"]) == set(items)
    assert work_store.path_of(work) in record["records"]
    linked = {(l.kind, l.target) for l in uds.links(TIM, place.record)}
    assert ("gathered[]", f"{place.path}/files/situation.md") in linked
    assert ("records[]", work_store.path_of(work)) in linked


def test_evidence_is_gathered_once_and_every_run_reuses_it(tmp_path, gh, checkout, transcript,
                                                            monkeypatch):
    work = _work(tmp_path)
    doc = _job(tmp_path, work, [{"type": "pr", "repo": "4thWAIV/jarvis", "number": 171}])
    monkeypatch.setattr(package_gather, "RETRIEVAL", lambda d: FakeRetrieval({}))
    package_gather.gather(pj.get(doc["id"]))
    asked = len(gh)
    second = pj.new_run(doc["id"], "the sender answered")
    assert (second["package"], second["run"]) == (doc["package"], "run-2")
    package_gather.gather(second)
    assert len(gh) == asked                         # nothing fetched again
    assert pe.place_of(second) == pe.place_of(doc)


def test_a_package_with_no_work_item_keeps_its_evidence_under_packages(tmp_path, transcript,
                                                                       monkeypatch):
    doc = _job(tmp_path, "", [{"type": "content", "text": "What the sender knows."}])
    package_gather.gather(pj.get(doc["id"]))
    place = pe.place_of(doc)
    assert place.path == f"/packages/{doc['package']}/evidence"
    assert (place.folder / "given/1.md").read_text() == "What the sender knows."
    tim = (place.folder / "jarvis/tim.md").read_text(encoding="utf-8")
    assert package_gather.OWN_PART in tim and "no Work item" in tim, \
        "no Work item: his record holds what he said elsewhere alone"
    assert package_gather.ELSEWHERE_PART in tim


def test_evidence_not_indexed_in_ten_minutes_puts_the_run_in_error(tmp_path, transcript,
                                                                  monkeypatch):
    doc = _job(tmp_path, "", [{"type": "content", "text": "Words."}])
    clock = iter(range(0, 10_000, 120))
    monkeypatch.setattr(pe, "CLOCK", lambda: next(clock))
    monkeypatch.setattr(pe, "not_indexed", lambda items, uds=None, text_only=(): pe.Waiting(list(items)))
    with pytest.raises(pj.Blocked) as blocked:
        package_gather.gather(pj.get(doc["id"]))
    assert blocked.value.step == "gather" and "within 10 minutes" in blocked.value.reason
    assert pe.INDEX_WAIT_SEC == 600


def test_with_no_embedding_model_the_run_still_waits_ten_minutes(tmp_path, transcript,
                                                                 monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    doc = _job(tmp_path, "", [{"type": "content", "text": "Words."}])
    now, slept = [0.0], []
    monkeypatch.setattr(pe, "CLOCK", lambda: now[0])

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(pe, "SLEEP", sleep)
    with pytest.raises(pj.Blocked) as blocked:
        package_gather.gather(pj.get(doc["id"]))
    assert now[0] >= pe.INDEX_WAIT_SEC and slept                # waited the whole 10 minutes
    assert "within 10 minutes" in blocked.value.reason
    assert "No embedding model is installed" in blocked.value.reason


def test_a_label_retrieval_cannot_answer_is_an_error_saying_why(tmp_path, transcript,
                                                               monkeypatch):
    from jarvis import model_tiers

    class NoModel:
        async def resolve(self, references, timeout):
            raise model_tiers.NoModel("no claude model in tier 3")
    monkeypatch.setattr(package_gather, "RETRIEVAL", lambda d: NoModel())
    doc = _job(tmp_path, "", [{"type": "label", "text": "the failing run"}])
    with pytest.raises(pj.Blocked) as blocked:
        package_gather.gather(pj.get(doc["id"]))
    assert "No model could be found for retrieval" in blocked.value.reason


def _marked(doc):
    """The mark step, standing in (tests/test_package_mark.py runs it)."""


def test_the_run_goes_through_gather_to_the_next_step(tmp_path, transcript, monkeypatch):
    doc = _job(tmp_path, "", [{"type": "content", "text": "Words."}])
    monkeypatch.setitem(pj.PRODUCERS, "mark", f"{__name__}:_marked")
    assert pj.run(doc["id"]) == 1
    after = pj.get(doc["id"])
    assert after["done"] == ["gather", "mark"] and after["error"]["step"] == "research"


def test_the_sender_s_answers_join_the_evidence_as_they_arrive(tmp_path, transcript):
    doc = _job(tmp_path, "", [{"type": "content", "text": "Words."}])
    package_gather.gather(pj.get(doc["id"]))
    assert pj.ask(doc["id"], [{"id": "q1", "question": "Which data was moved?"}])
    pj.answer(doc["id"], {"q1": "A copy of the live data."}, session=SESSION)
    place = pe.place_of(doc)
    assert "A copy of the live data." in (place.folder / "sender-answers/1.md").read_text()
    row = next(r for r in pe.read_manifest(place) if r.type == pe.SENDER_ANSWER)
    assert row.stored == "sender-answers/1.md" and row.knowledge_state == pe.PROBABLY_NOT
    assert records.uds().get(TIM, place.item("sender-answers/1.md")) is not None


# -- the readers -------------------------------------------------------------------------

@pytest.mark.parametrize("url,entry", [
    ("https://github.com/o/r/pull/7", {"type": "pr", "repo": "o/r", "number": "7"}),
    ("https://github.com/o/r/pull/7/files", {"type": "pr", "repo": "o/r", "number": "7"}),
    ("https://github.com/o/r/actions/runs/123/job/9", {"type": "ci_run", "repo": "o/r", "id": "123"}),
    ("https://github.com/o/r/commit/abc1234", {"type": "commit", "repo": "o/r", "sha": "abc1234"}),
    ("https://github.com/o/r/issues/3", {"type": "issue", "repo": "o/r", "number": "3"}),
    ("https://github.com/o/r/tree/fix/x", {"type": "branch", "repo": "o/r", "name": "fix/x"}),
    ("https://github.com/o/r", None),
    ("https://example.com/o/r/pull/7", None),
])
def test_a_github_address_is_read_as_its_own_type(url, entry):
    assert evidence_readers.github_entry(url) == entry


def test_a_url_is_a_plain_fetch_then_the_page_reader(monkeypatch):
    tried = []
    monkeypatch.setattr(evidence_readers, "FETCHERS", (
        lambda u: (tried.append("plain"), (None, "the plain fetch gave no readable text"))[1],
        lambda u: (tried.append("page"), ("The page's words.", ""))[1]))
    found = evidence_readers.read({"type": "url", "url": "https://example.com/a"})
    assert tried == ["plain", "page"] and found.resolved == "ok"
    assert "The page's words." in found.text
    monkeypatch.setattr(evidence_readers, "FETCHERS", (lambda u: (None, "HTTP 404"),))
    failed = evidence_readers.read({"type": "url", "url": "https://example.com/b"})
    assert failed.resolved == "failed" and "HTTP 404" in failed.reason


def test_a_commit_with_no_checkout_here_is_not_found_saying_so(monkeypatch):
    monkeypatch.setattr(evidence_readers, "CHECKOUTS", lambda: [])
    found = evidence_readers.read({"type": "commit", "repo": "o/r", "sha": "abc1234"})
    assert found.resolved == "not_found" and "no checkout of o/r" in found.reason


def test_jarvis_s_own_records_not_there_are_not_found():
    for kind in ("work_item", "package", "message", "workflow", "run"):
        found = evidence_readers.read({"type": kind, "id": "nothing-1"})
        assert found.resolved == "not_found", kind


# -- urgent indexing --------------------------------------------------------------------

def test_an_evidence_item_is_built_first_at_every_stage():
    from jarvis.knowledge import vectors
    queue = vectors.BuildQueue()
    evidence = "/work/w/packages/p/evidence/files/a.md"
    background = vectors.Job(1, "/jarvis/answers/a.md", "s1",
                             vectors.build_rank({"_key": "/jarvis/answers/a.md", "_at": 9.0}), 0, 3)
    urgent = vectors.Job(2, evidence, "s2", vectors.build_rank({"_key": evidence, "_at": 1.0}), 1, 40)
    queue.put(background)
    queue.put(urgent)
    assert queue.urgent_first() and queue.pop() is urgent and queue.pop() is background
    assert item_paths.is_package_evidence("/packages/p/evidence/x.md")
    assert not item_paths.is_package_evidence("/packages/p.package")
    assert not item_paths.is_package_evidence("/work/w/research/queries.md")


def test_evidence_is_embedded_while_the_pace_holds_the_background():
    uds = records.uds()
    engine = next(e for e, u in records._opened.values() if u is uds)
    engine.vectors.set_held(True)
    try:
        evidence, other = "/packages/p/evidence/given/1.md", "/jarvis/answers/one.md"
        for path in (evidence, other):
            uds.put(TIM, path, data={"title": "t"}, content={"text": "# Part\nThe move is done."})
        assert pe.wait_indexed  # the step waits on exactly this
        deadline = __import__("time").monotonic() + 20
        while not (uds.get(TIM, evidence, {Stream.VECTORS}).vectors or {}).get("complete"):
            assert __import__("time").monotonic() < deadline, "evidence never embedded"
            __import__("time").sleep(0.05)
        assert uds.get(TIM, other, {Stream.VECTORS}).vectors["state"] == "waiting"
    finally:
        engine.vectors.set_held(False)


def test_knowledge_search_keeps_to_the_path_it_is_given_under():
    from jarvis import knowledge_api

    class Here(knowledge_api.Context):
        def uds(self, deadline=knowledge_api.CALL_DEADLINE_SEC):
            return records.uds()
    ctx = Here(lambda: None)
    uds = records.uds()
    uds.put(TIM, "/packages/p/evidence/given/1.md", data={}, content={"text": "lighthouse keeper lamp"})
    uds.put(TIM, "/jarvis/answers/other.md", data={}, content={"text": "lighthouse keeper lamp"})
    found = knowledge_api.search(ctx, "lighthouse keeper", under="/packages/p/evidence")
    assert [h["key"] for h in found["hits"]] == ["/packages/p/evidence/given/1.md"]
    assert found["under"] == "/packages/p/evidence"
    everywhere = knowledge_api.search(ctx, "lighthouse keeper")
    assert len(everywhere["hits"]) == 2
    with pytest.raises(knowledge_api.Refused):
        knowledge_api.search(ctx, "lighthouse", under="packages/../x")


def test_tim_s_record_holds_his_answers_on_every_earlier_package_and_his_rejects(
        tmp_path, transcript, monkeypatch):
    """workflow.yaml gather gather_jarvis_material: "Tim's messages and
    answers on this Work item, his answers on its earlier packages, his
    Reject words -> evidence/jarvis/tim.md". A package he has signed off is
    closed, and its answers are his record all the same."""
    from jarvis import package_inbox
    work = _work(tmp_path)
    earlier = _job(tmp_path, work, [{"type": "content", "text": "Words."}])
    pj.rejected(earlier["id"], "Lead with the cost.")
    closed = {c: None for c in package_inbox.COLUMNS}
    closed.update(id="pkg-0123456789ab", package_id="old-1.0.0-x", sha256="ab", name="old",
                  version="1.0.0", title="The earlier plan", purpose="", runs="page",
                  decisions_total=1, decisions_answered=1, from_project="", from_session="",
                  from_agent="", work_id=work, state=package_inbox.CLOSED, revision=3,
                  arrived_at=1.0, closed_at=2.0, closed_why=package_inbox.SIGNED_OFF)
    records.update(package_inbox.path_of(closed["id"]), lambda _d: closed)

    class Answered:
        id, question = "approve-package", "Approve this package?"
        answer = {"answer": "reject", "note": "Say what it costs first."}

        def answered_id(self):
            return f"{self.answer['answer']} (his words: {self.answer['note']})"
    monkeypatch.setattr(evidence_readers, "package_questions",
                        lambda row: [Answered()] if row.id == closed["id"] else [])
    doc = _job(tmp_path, work, [{"type": "content", "text": "Words."}])
    package_gather.gather(pj.get(doc["id"]))
    text = (pe.place_of(doc).folder / pe.TIM_RECORD).read_text(encoding="utf-8")
    assert "package pkg-0123456789ab (The earlier plan), closed (signed_off):" in text
    assert "approve-package: Approve this package? His answer: reject (his words: Say what " \
           "it costs first.)" in text
    assert f"{earlier['package']} run-1: Lead with the cost." in text
    assert "### His acts on it" in text


def test_tim_s_record_is_his_own_work_first_then_what_he_said_elsewhere(tmp_path, transcript,
                                                                       monkeypatch):
    """construction-drafts.md, "Tim's record comes from everything he has
    said" (decided by Tim): what he said on the package's own Work item,
    then, kept apart and weighted lower, what he said anywhere else that
    bears on the package's questions and evidence, found in the engine by
    words and meaning, each search run at once with the run's patience."""
    from jarvis import conversation_log as cl, work_api
    work = _work(tmp_path)
    cl.append(cl.ConversationItem.new(
        "heard", "Merge PR 171 only once the folders move is checked on the copy."))
    cl.append(cl.ConversationItem.new("jarvis", "Merge PR 171 is noted, sir."))
    other = work_store.create("Merge policy", "folder", str(tmp_path))
    work_store.record(work_store.WorkEvent(other.id, "decision_needed", "Merge PR 171 now?",
                                           {"key": "merge"}))
    work_api.answer_decision(other.id, "merge", "Not before the copy is checked.", "voice")
    searched = []
    real = package_gather.SEARCH

    def search():
        found = real()

        def counted(text, under, kinds):
            searched.append((text, under, tuple(kinds or ())))
            return found(text, under, kinds)
        return counted
    monkeypatch.setattr(package_gather, "SEARCH", search)
    doc = _job(tmp_path, work, [{"type": "label", "text": "the folders move on the copy"}])
    monkeypatch.setattr(package_gather, "RETRIEVAL", lambda d: FakeRetrieval({}))
    package_gather.gather(pj.get(doc["id"]))

    place = pe.place_of(doc)
    text = (place.folder / pe.TIM_RECORD).read_text(encoding="utf-8")
    own, _, elsewhere = text.partition(package_gather.ELSEWHERE_PART)
    assert package_gather.OWN_PART in own and f"### Work item {work}" in own, \
        "the package's own Work item first"
    assert "weighted higher" in package_gather.OWN_PART
    assert "weighted lower" in package_gather.ELSEWHERE_PART
    assert "Merge PR 171 only once the folders move is checked on the copy." in elsewhere
    assert "is noted, sir" not in text, "JARVIS's own turns are not his words"
    assert f"Work item {other.id} (Merge policy), merge: Merge PR 171 now? His answer " \
           f"(voice): Not before the copy is checked." in elsewhere
    assert f"Work item {work}" not in elsewhere, "his own Work item is Part 1's alone"
    assert "found for: Merge PR 171?" in elsewhere
    assert {(t, k) for t, _u, k in searched} == {
        (t, (k,)) for t in ("Merge PR 171?", "the folders move on the copy")
        for k in package_gather.HIS_RECORDS}, "each term in each of his records, everywhere"
    assert all(u is None for _t, u, _k in searched)
    row = next(r for r in pe.read_manifest(place) if r.stored == pe.TIM_RECORD)
    assert row.knowledge_state == pe.KNOWS


def test_what_the_program_reads_back_is_waited_for_by_its_text_only(monkeypatch):
    """The mark step's output is read back from its own files by the
    program: its vectors are made in the background, never waited for."""
    class Item:
        def __init__(self, state, complete):
            self.data = {"state": state}
            self.vectors = {"state": "on", "complete": complete}

    class Uds:
        items = {"/e/files/a.md": Item("indexed", False),
                 "/e/mark/files/a.md.json": Item("indexed", False),
                 "/e/mark/marked.json": Item("pending", False)}

        def get(self, writer, path, streams):
            return self.items[path]
    waiting = pe.not_indexed(list(Uds.items), Uds(), text_only=("/mark/",))
    assert waiting.paths == ["/e/files/a.md", "/e/mark/marked.json"], \
        "raw evidence waits for its vectors; mark output only until its text is read"
