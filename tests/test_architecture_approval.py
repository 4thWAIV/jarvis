"""His approval of an architecture design, through the package that puts the
checked canonical revision to him (the signature amendment contract, JARVIS
items 1-4 and 6, the language as it is).

The factory alone asks it (`approve-architecture`), only in an architecture
package whose handoff asks for it, before the standard vote. Got it is
acknowledgement and never approval. His answer is recorded through the
existing answer route with the {document_id, revision, source_revision,
kit_digest} it was given to; request changes needs his words. A newer checked
revision of the document, or another kit, makes it stale: shown so, counted
as unanswered, and ending nothing. A wait on package:<id>:approve-architecture
ends only on approve of the revision in force, never on Got it, request
changes or a sign-off.

Needs the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit
workflow)."""
from __future__ import annotations

import json

import pytest

from jarvis import (data_paths, language_documents, language_projection, make_package,
                    package_inbox, package_story, work_packages, workflow_store)
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_package_inbox import BROWSER, INBOX, chitauri, claims_proven, shown  # noqa: F401
from tests.test_work_packages import make_zip, marks  # noqa: F401  (a fixture)

PURPOSE = "Approve the document store's design before tests are written against it."
MARKDOWN = b"# The store\n\nOne input goes through DocumentStore.\n"
STORY = {"title": "The document store", "slides": [
    {"title": "What it owns", "beats": [{"say": "DocumentStore owns each revision."}]}]}


def checked(draft):  # noqa: F811
    service = language_documents.Documents()
    return service, service.validate(**draft)


def approval_folder(draft, tmp_path, *, approval=True, story=STORY, name="store-approval"):  # noqa: F811
    service, revision = checked(draft)
    envelope = language_projection.checked_revision(service, revision["document_id"],
                                                    revision["revision"])
    folder = tmp_path / name / "architecture"
    language_projection.project(envelope, folder, MARKDOWN,
                                None if story is None else json.dumps(story).encode(),
                                PURPOSE, "en", approval)
    manifest = json.loads((folder / "package.json").read_text())
    (folder / "package.json").write_text(json.dumps({**manifest, "name": name}))
    return folder, revision


def sent(w, draft, tmp_path, **kwargs) -> tuple[str, dict]:  # noqa: F811
    folder, revision = approval_folder(draft, tmp_path, **kwargs)
    made = make_package.make_folder(folder, tmp_path / "built" / folder.parent.name)
    r = w.client.post(INBOX, content=made.archive.read_bytes(), params={"session": "sess-1"},
                      headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                               "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    return r.json()["inbox"]["id"], revision


def package_of(inbox_id: str) -> work_packages.Package:
    return work_packages.find(package_inbox.get(inbox_id).package_id)


def approval(inbox_id: str) -> package_inbox.Question:
    return next(q for q in package_inbox.questions(package_of(inbox_id))
                if q.id == package_inbox.APPROVAL_ID)


def answer(w, how: str, inbox_id: str, decision: str, option: str, words: str = ""):
    if how == "page":
        r = w.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                          json={"decision": decision, "option": option, "words": words})
        return r.status_code == 200, r.text
    said = _call(w, "answer_package_decision", package=inbox_id, decision=decision,
                 option=option, words=words)["text"]
    return said.startswith("Recorded."), said


def waiting_on(inbox_id: str) -> str:
    wid = workflow_store.start("Store design", "store", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting on his approval of the design.",
        {"key": "design", "until": f"package:{inbox_id}:{package_inbox.APPROVAL_ID}"}))
    return wid


def still_waiting(wid: str) -> bool:
    return workflow_store.snapshot(wid).open_waits == ["design"]


# -- the factory asks it, once, before the vote --------------------------------------------

@pytest.mark.parametrize("story", [STORY, None], ids=["with a story", "without one"])
def test_the_factory_asks_his_approval_once_before_the_vote(draft, tmp_path, story):  # noqa: F811
    folder, _ = approval_folder(draft, tmp_path, story=story)
    canonical = json.loads((folder / "canonical.json").read_text())
    assert canonical["understanding"]["approval"] is True
    told = package_story.parse((folder / "story.json").read_bytes())
    assert [s.waits for s in told.slides][-2:] == [package_inbox.APPROVAL_ID,
                                                   package_inbox.UNDERSTOOD_ID]
    manifest = json.loads((folder / "package.json").read_text())
    language_projection.folder_gate(folder, manifest["communication"])


def test_without_approval_asked_there_is_no_approval_question(draft, tmp_path):  # noqa: F811
    folder, _ = approval_folder(draft, tmp_path, approval=False)
    assert "approval" not in json.loads((folder / "canonical.json").read_text())["understanding"]
    told = package_story.parse((folder / "story.json").read_bytes())
    assert package_inbox.APPROVAL_ID not in [s.waits for s in told.slides]


@pytest.mark.parametrize("tamper", ["binding-dropped", "binding-added", "slide-removed",
                                    "slide-twice"])
def test_approval_is_exactly_where_the_binding_says(draft, tmp_path, tamper):  # noqa: F811
    folder, _ = approval_folder(draft, tmp_path, approval=tamper != "binding-added")
    canonical = json.loads((folder / "canonical.json").read_text())
    story = json.loads((folder / "story.json").read_text())
    if tamper == "binding-dropped":
        del canonical["understanding"]["approval"]
    elif tamper == "binding-added":
        canonical["understanding"]["approval"] = True
    elif tamper == "slide-removed":
        story["slides"] = [s for s in story["slides"] if s.get("waits") != package_inbox.APPROVAL_ID]
    else:
        story["slides"].insert(0, package_inbox.approval_slide("en"))
    (folder / "canonical.json").write_text(json.dumps(canonical, sort_keys=True))
    (folder / "story.json").write_bytes(package_story.encode(story))
    manifest = json.loads((folder / "package.json").read_text())
    with pytest.raises(language_documents.DocumentError, match="composition|declaration differs"):
        language_projection.folder_gate(folder, manifest["communication"])


def test_no_other_package_may_ask_it(wired, shown, marks, tmp_path):  # noqa: F811
    told = package_story.compose(None, "en")
    story = json.loads(told)
    story["slides"].insert(0, package_inbox.approval_slide("en"))
    page = make_zip({"index.html": '<p>x</p><script src="/api/packages/story.js"></script>',
                     "story.json": json.dumps(story)},
                    {"name": "fake-approval", "version": "1.0.0", "purpose": "Pretend.",
                     "runs": "page", "entry": "index.html", "story": "story.json",
                     "returns": ["results/**"]})
    r = wired.client.post(INBOX, content=page, headers={
        "Authorization": f"Bearer {data_paths.ensure_tool_token()}",
        "Content-Type": "application/zip"})
    assert r.status_code == 409 and "only an architecture package" in r.json()["error"]


# -- his answers ----------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["page", "voice"])
def test_got_it_is_acknowledgement_never_approval(wired, shown, marks, tmp_path, draft, how):  # noqa: F811
    inbox_id, _ = sent(wired, draft, tmp_path)
    wid = waiting_on(inbox_id)
    ok, said = answer(wired, how, inbox_id, "understood", "got-it")
    assert ok, said
    q = approval(inbox_id)
    assert q.answer is None and not q.counts() and not q.ends_waits()
    assert still_waiting(wid), "Got it ends no wait on his approval"
    vote = package_inbox.page_vote(package_of(inbox_id))
    assert vote["answer"] == "got-it"
    [asked] = vote["asks"]
    assert asked["kind"] == "approval" and asked["answer"] is None, "shown apart from the vote"


@pytest.mark.parametrize("how", ["page", "voice"])
def test_request_changes_needs_his_words_and_ends_no_wait(wired, shown, marks, tmp_path, draft, how):  # noqa: F811
    inbox_id, revision = sent(wired, draft, tmp_path)
    wid = waiting_on(inbox_id)
    ok, said = answer(wired, how, inbox_id, package_inbox.APPROVAL_ID, "request-changes")
    assert not ok and "needs his words" in said
    ok, said = answer(wired, how, inbox_id, package_inbox.APPROVAL_ID, "request-changes",
                      "Rename DocumentStore's revision member.")
    assert ok, said
    q = approval(inbox_id)
    assert q.answer["answer"] == "request-changes" and q.counts() and not q.ends_waits()
    assert q.answer["subject"] == {k: revision[k] for k in
                                   ("document_id", "revision", "source_revision", "kit_digest")}
    assert still_waiting(wid), "only approve ends a wait on his approval"
    ok, said = answer(wired, how, inbox_id, package_inbox.APPROVAL_ID, "later")
    assert not ok, "his approval is approve or request changes"


def test_approve_of_the_revision_in_force_ends_the_wait_and_goes_back_with_it(
        wired, shown, marks, tmp_path, draft, chitauri):  # noqa: F811
    inbox_id, revision = sent(wired, draft, tmp_path)
    wid = waiting_on(inbox_id)
    ok, said = answer(wired, "page", inbox_id, package_inbox.APPROVAL_ID, "approve")
    assert ok, said
    q = approval(inbox_id)
    assert q.counts() and q.ends_waits() and not q.stale
    assert not workflow_store.snapshot(wid).open_waits, "approve of the current revision ends it"
    record = json.loads(work_packages.read_result(package_of(inbox_id), "decisions.json"))
    assert record["approve-architecture"]["subject"]["revision"] == revision["revision"]
    assert "understood" not in record, "approval is not the vote"
    _call(wired, "sign_off_package", package=inbox_id)
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert (f"approve-architecture: approve, given to revision {revision['revision']} of "
            f"{revision['document_id']}") in capture["text"]
    assert "understood: not answered" in capture["text"]


def test_a_sign_off_without_approval_ends_no_wait_on_it(wired, shown, marks, tmp_path, draft):  # noqa: F811
    inbox_id, _ = sent(wired, draft, tmp_path)
    on_package = waiting_on(inbox_id)
    wid = workflow_store.start("Store", "store", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting on the package.",
        {"key": "deck", "until": f"package:{inbox_id}"}))
    ok, said = answer(wired, "page", inbox_id, "understood", "got-it")
    assert ok, said
    r = wired.client.post(f"/api/packages/{package_of(inbox_id).id}/signoff", headers=BROWSER)
    assert r.status_code == 200, r.text
    assert not workflow_store.snapshot(wid).open_waits, "a wait on the package ends on sign-off"
    assert still_waiting(on_package), "a wait on his approval never ends on a sign-off"


# -- stale ----------------------------------------------------------------------------------

def test_a_newer_checked_revision_makes_his_approval_stale(wired, shown, marks, tmp_path, draft):  # noqa: F811
    inbox_id, revision = sent(wired, draft, tmp_path)
    ok, said = answer(wired, "page", inbox_id, package_inbox.APPROVAL_ID, "approve")
    assert ok, said
    assert not approval(inbox_id).stale
    service = language_documents.Documents()
    newer = service.validate(**{**draft, "source": draft["source"].replace("record", "class")},
                             document_id=revision["document_id"],
                             parent_revision=revision["revision"])
    assert newer["check"]["ok"] and newer["revision"] != revision["revision"]
    q = approval(inbox_id)
    assert q.stale and not q.counts() and not q.ends_waits()
    assert "(stale: a newer revision or kit has been checked since)" in q.answered_id()
    asked = package_inbox.page_vote(package_of(inbox_id))["asks"][0]
    assert asked["stale"] and asked["answered_revision"] == revision["revision"]
    wid = waiting_on(inbox_id)
    assert still_waiting(wid), "a stale approval ends nothing"
    assert package_inbox.counts(package_inbox.questions(package_of(inbox_id)))[0] == 0


def test_another_kit_makes_his_approval_stale(wired, shown, marks, tmp_path, draft, monkeypatch):  # noqa: F811
    inbox_id, _ = sent(wired, draft, tmp_path)
    ok, said = answer(wired, "page", inbox_id, package_inbox.APPROVAL_ID, "approve")
    assert ok, said
    assert not approval(inbox_id).stale
    real = language_projection.kit.active

    class Other:
        def __init__(self, installed):
            self.release = type("R", (), {"digest": "f" * 64})()
    monkeypatch.setattr(language_projection.kit, "active", lambda *a: Other(real(*a)))
    assert approval(inbox_id).stale
