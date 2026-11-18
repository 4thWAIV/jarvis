"""An architecture view set: one package of several checked revisions, each a
view of the same system (one per document or language), its story walking
one concrete scenario through every view in turn, and his approval given to
the exact set, stale when any one view's revision is no longer in force.

The story's rules, the author's own check and the approval's subject run
anywhere. The projection, the gate and the approval through the inbox need
the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow)."""
from __future__ import annotations

import json

import pytest

from jarvis import (data_paths, language_document_api, language_documents, language_projection,
                    make_package, package_inbox, package_story, work_packages)
from tests.test_architecture_packages import factory, through_the_route  # noqa: F401  (fixtures)
from tests.test_architecture_approval import answer, approval, package_of, waiting_on, still_waiting
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_package_inbox import BROWSER, INBOX, chitauri, claims_proven, shown  # noqa: F401
from tests.test_work_packages import marks  # noqa: F401  (a fixture)
from tests.test_language_projection import folder_read

PURPOSE = "Decide whether the store reads the same in its logical and its placement view."
MARKDOWN = b"# The store, in two views\n\nOne revision is saved and then read back.\n"
SCENARIO = "One revision of a document is saved, then read back."


def walk(views, **changes) -> dict:
    """A story walking the scenario through `views` in order, one slide each."""
    story = {"title": "The store, view by view", "scenario": SCENARIO,
             "slides": [{"title": f"View {n}", "view": view,
                         "beats": [{"say": f"The revision passes through view {n}."}]}
                        for n, view in enumerate(views, 1)]}
    story.update(changes)
    return story


# -- the walk: one scenario through every view in turn ---------------------------------------

def test_one_view_asks_no_walk_and_names_only_its_own_view():
    assert package_story.walk_problem(None, ("doc-a",)) is None
    assert package_story.walk_problem({"slides": [{"title": "x"}]}, ("doc-a",)) is None
    assert package_story.walk_problem(walk(["doc-a"]), ("doc-a",)) is None
    assert "does not hold" in package_story.walk_problem(walk(["doc-b"]), ("doc-a",))


@pytest.mark.parametrize("story, said", [
    (None, "needs a story"),
    (walk(["doc-a", "doc-b"], scenario=None), "names its scenario"),
    (walk(["doc-a", "doc-b"], scenario="two\nlines"), "names its scenario"),
    ({**walk(["doc-a"]), "slides": [{"title": "No view", "beats": [{"say": "x"}]}]}, "names no view"),
    (walk(["doc-b", "doc-a"]), "out of order"),
    (walk(["doc-a", "doc-b", "doc-a"]), "out of order"),
    (walk(["doc-a"]), "never reaches view doc-b"),
    (walk(["doc-a", "doc-c"]), "names no view"),
])
def test_a_view_set_walks_one_scenario_through_every_view_in_turn(story, said):
    assert said in package_story.walk_problem(story, ("doc-a", "doc-b"))


def test_a_walk_may_tell_a_view_over_several_slides():
    story = walk(["doc-a", "doc-a", "doc-b"])
    assert package_story.walk_problem(story, ("doc-a", "doc-b")) is None


def test_the_story_carries_each_slide_s_view_and_the_scenario_to_the_page():
    told = package_story.parse(package_story.compose(
        package_story.authored(json.dumps(walk(["doc-a", "doc-b"])).encode()), "en"))
    assert [s.view for s in told.slides] == ["doc-a", "doc-b", None], "the vote stays on the view shown"
    outline = told.outline()
    assert outline["views"] == ["doc-a", "doc-b", None]
    assert outline["scenario"] == SCENARIO
    assert [cue["view"] for _, cue in told.cues("p1", 2)] == ["doc-b"]
    assert "view" not in told.cues("p1", 3)[0][1]
    alone = package_story.parse(package_story.compose(None, "en")).outline()
    assert "views" not in alone and "scenario" not in alone, "one view's outline is as before"


def test_a_slide_s_view_is_a_document_id():
    with pytest.raises(package_story.StoryError, match="not a document id"):
        package_story.parse(json.dumps({"title": "t", "slides": [
            {"title": "s", "view": "../other", "beats": [{"say": "x"}]}]}).encode())


def test_the_author_s_check_holds_a_set_s_story_to_the_walk(tmp_path, capsys):
    package = tmp_path / "package"
    package.mkdir()
    (package / "UNDERSTANDING.md").write_bytes(MARKDOWN)
    check = [str(package), "--check-only", "--document", "doc-a", "--revision", "a" * 64,
             "--document", "doc-b", "--revision", "b" * 64]
    assert make_package.main(check) == 1, "a set needs a story"
    assert "needs a story" in capsys.readouterr().out
    (package / "story.json").write_text(json.dumps(walk(["doc-b", "doc-a"])))
    assert make_package.main(check) == 1
    assert "out of order" in capsys.readouterr().out
    (package / "story.json").write_text(json.dumps(walk(["doc-a", "doc-b"])))
    assert make_package.main(check) == 0
    out = capsys.readouterr().out
    assert "architecture document doc-a revision " + "a" * 64 in out
    assert "architecture document doc-b revision " + "b" * 64 in out


def test_each_document_comes_with_its_revision_and_once(tmp_path):
    for argv in (["--document", "doc-a", "--revision", "a" * 64, "--document", "doc-b"],
                 ["--document", "doc-a", "--revision", "a" * 64,
                  "--document", "doc-a", "--revision", "b" * 64]):
        with pytest.raises(SystemExit):
            make_package.main([str(tmp_path), "--check-only", *argv])


def test_his_approval_of_a_set_is_of_every_view_and_stale_when_any_one_is(monkeypatch):
    first = {"document_id": "doc-a", "revision": "1" * 64, "source_revision": "2" * 64,
             "kit_digest": "3" * 64}
    second = {"document_id": "doc-b", "revision": "4" * 64, "source_revision": "5" * 64,
              "kit_digest": "3" * 64}
    subject = {**first, "set": [second]}
    assert package_inbox.subject_views(subject) == [first, second]
    in_force = {first["revision"], second["revision"]}
    monkeypatch.setattr(language_projection, "current_subject",
                        lambda view: view.get("revision") in in_force)
    assert package_inbox.current_subject(subject)
    in_force.discard(second["revision"])
    assert not package_inbox.current_subject(subject), "one view no longer in force"
    assert not package_inbox.current_subject({**first, "set": "malformed"})
    q = package_inbox.Question("approve-architecture", "", "", [], "", "", True,
                               {"answer": "approve", "subject": subject})
    assert q._given_to() == (f", given to revision {first['revision']} of doc-a and revision "
                             f"{second['revision']} of doc-b")


# -- the projection and the gate (the pinned kit) ------------------------------------------

def second_draft(draft):  # noqa: F811
    """Another view of the same store: its own document, on the same Work item,
    declaring the same element (DocumentStore)."""
    return {**draft, "source": draft["source"]
            .replace("A proposed document store", "The document store, as it is placed")
            .replace("Owns an immutable document revision.",
                     "Is placed in the jarvis package, where revisions are kept.")}


def envelopes(draft):  # noqa: F811
    service = language_documents.Documents()
    found = []
    for one in (draft, second_draft(draft)):
        checked = service.validate(**one)
        assert checked["check"]["ok"], checked["check"]
        found.append(language_projection.checked_revision(service, checked["document_id"],
                                                          checked["revision"]))
    return found


def view_set(draft, tmp_path, *, story=None, approval=False, name="store-views"):  # noqa: F811
    """A view set of two checked documents as the factory projects it, built:
    (its folder, manifest, the two envelopes)."""
    first, second = envelopes(draft)
    told = story or walk([first["document_id"], second["document_id"]])
    folder = tmp_path / "projection" / name
    language_projection.project(first, folder, MARKDOWN, json.dumps(told).encode(), PURPOSE,
                                "en", approval, (second,))
    manifest = json.loads((folder / "package.json").read_text())
    (folder / "package.json").write_text(json.dumps({**manifest, "name": name}))
    made = make_package.make_folder(folder, tmp_path / "built")
    built = made.archive.parent / name
    manifest = json.loads((built / "package.json").read_text())
    return built, manifest, (first, second), made.archive


def test_a_view_set_holds_every_checked_view_bound_in_one_canonical_set(draft, tmp_path):  # noqa: F811
    folder, manifest, (first, second), archive = view_set(draft, tmp_path)
    language_projection.folder_gate(folder, manifest["communication"])
    canonical = json.loads((folder / "canonical.json").read_text())
    assert canonical["revision"] == first["revision"], "the first view is the package's own"
    assert [v["document_id"] for v in canonical["set"]] == [second["document_id"]]
    assert manifest["communication"]["set"] == [
        {k: second[k] for k in language_projection.IDENTITY}]
    prefix = language_projection.view_prefix(second["document_id"])
    assert (folder / prefix / "source.yaml").read_text() == second_draft(draft)["source"]
    assert (folder / "source.yaml").read_text() == draft["source"]
    assert set(manifest["understanding"]) >= {prefix + n for n in language_projection.VIEW_FILES}
    package, _ = work_packages.unpack(archive, language_documents.kit.sha256(archive.read_bytes()),
                                      clear_mark=lambda p: None)
    views = language_projection.views_shown(package)
    assert [v["id"] for v in views] == [first["document_id"], second["document_id"]]
    assert [v["entry"] for v in views] == ["index.html", prefix + "index.html"]
    assert views[1]["declarations"] == ["DocumentStore"], "the same element in both views"
    assert language_projection.views_shown(type("One", (), {"manifest": type("M", (), {
        "communication": {k: v for k, v in manifest["communication"].items() if k != "set"}})()})()) \
        is None, "one view is no set"


@pytest.mark.parametrize("tamper, said", [
    ("further-source", "one checked revision"),
    ("further-page", "one checked revision"),
    ("set-dropped", "differs from the one its canonical revision binds"),
    ("set-renamed", "differs from the one its canonical revision binds"),
    ("set-twice", "each document once"),
    ("other-work", "Work linkage differs"),
])
def test_each_view_of_the_set_is_held_to_its_own_checked_revision(draft, tmp_path, tamper, said):  # noqa: F811
    folder, manifest, (_, second), _ = view_set(draft, tmp_path)
    communication = manifest["communication"]
    prefix = folder / language_projection.view_prefix(second["document_id"])
    canonical_path = folder / "canonical.json"
    canonical = json.loads(canonical_path.read_text())
    if tamper == "further-source":
        (prefix / "source.yaml").write_bytes((prefix / "source.yaml").read_bytes() + b"# x\n")
    elif tamper == "further-page":
        (prefix / "index.html").write_bytes((prefix / "index.html").read_bytes() + b" ")
    elif tamper == "set-dropped":
        del canonical["set"]
    elif tamper == "set-renamed":
        communication = {**communication, "set": [{**communication["set"][0], "revision": "0" * 64}]}
    elif tamper == "set-twice":
        canonical["set"] = [canonical["set"][0], canonical["set"][0]]
    else:
        canonical["set"][0]["work_id"] = "work-other"
    canonical_path.write_text(json.dumps(canonical, sort_keys=True))
    with pytest.raises(language_documents.DocumentError, match=said):
        language_projection.folder_gate(folder, communication)


def test_a_set_is_projected_only_with_a_story_that_walks_every_view(draft, tmp_path):  # noqa: F811
    first, second = envelopes(draft)
    ids = [first["document_id"], second["document_id"]]
    with pytest.raises(language_documents.DocumentError, match="fresh author's story"):
        language_projection.project(first, tmp_path / "a", MARKDOWN, None, PURPOSE, "en",
                                    False, (second,))
    with pytest.raises(language_documents.DocumentError, match="never reaches view"):
        language_projection.project(first, tmp_path / "b", MARKDOWN,
                                    json.dumps(walk(ids[:1])).encode(), PURPOSE, "en", False,
                                    (second,))
    with pytest.raises(language_documents.DocumentError, match="each document once"):
        language_projection.project(first, tmp_path / "c", MARKDOWN,
                                    json.dumps(walk(ids[:1])).encode(), PURPOSE, "en", False,
                                    (first,))


def test_a_received_set_whose_story_no_longer_walks_is_refused(draft, tmp_path):  # noqa: F811
    folder, manifest, (first, second), _ = view_set(draft, tmp_path)
    out_of_order = json.dumps(walk([second["document_id"], first["document_id"]])).encode()
    (folder / language_projection.AUTHORED_STORY).write_bytes(out_of_order)
    canonical = json.loads((folder / "canonical.json").read_text())
    canonical["understanding"]["story_sha256"] = language_documents.kit.sha256(out_of_order)
    (folder / "canonical.json").write_text(json.dumps(canonical, sort_keys=True))
    (folder / make_package.STORY_FILE).write_bytes(package_story.compose(
        package_story.authored(out_of_order), "en"))
    with pytest.raises(language_documents.DocumentError, match="out of order"):
        language_projection.gate(folder_read(folder), manifest["communication"])


def test_a_set_s_page_lists_its_views_and_its_story_moves_between_them(
        wired, shown, marks, draft, tmp_path):  # noqa: F811
    inbox_id, (first, second) = sent(wired, draft, tmp_path)
    package = package_of(inbox_id)
    described = next(p for p in wired.client.get("/api/packages").json()["packages"]
                     if p["id"] == package.id)
    assert [v["id"] for v in described["views"]] == [first["document_id"], second["document_id"]]
    assert described["story"]["views"] == [first["document_id"], second["document_id"], None]
    assert described["story"]["scenario"] == SCENARIO
    prefix = language_projection.view_prefix(second["document_id"])
    page = wired.client.get(f"/api/packages/{package.id}/files/{prefix}index.html",
                            params={"canonical_revision": first["revision"]})
    assert page.status_code == 200 and page.content == open(
        second["references"]["view"], "rb").read()


# -- his approval of the exact set ---------------------------------------------------------

def sent(w, draft, tmp_path, **kwargs):  # noqa: F811
    folder, _, envelopes_sent, archive = view_set(draft, tmp_path, **kwargs)
    r = w.client.post(INBOX, content=archive.read_bytes(), params={"session": "sess-1"},
                      headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                               "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    return r.json()["inbox"]["id"], envelopes_sent


def test_his_approval_is_given_to_the_exact_set_and_goes_back_naming_every_view(
        wired, shown, marks, draft, tmp_path, chitauri):  # noqa: F811
    inbox_id, (first, second) = sent(wired, draft, tmp_path, approval=True)
    wid = waiting_on(inbox_id)
    asked = package_inbox.page_vote(package_of(inbox_id))["asks"][-1]
    assert asked["kind"] == "approval"
    assert asked["revisions"] == [first["revision"], second["revision"]]
    ok, said = answer(wired, "page", inbox_id, package_inbox.APPROVAL_ID, "approve")
    assert ok, said
    record = json.loads(work_packages.read_result(package_of(inbox_id), "decisions.json"))
    subject = record[package_inbox.APPROVAL_ID]["subject"]
    assert [v["revision"] for v in package_inbox.subject_views(subject)] == [
        first["revision"], second["revision"]]
    assert approval(inbox_id).ends_waits()
    assert not still_waiting(wid), "approve of the set in force ends the wait"
    _call(wired, "sign_off_package", package=inbox_id)
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert (f"given to revision {first['revision']} of {first['document_id']} and revision "
            f"{second['revision']} of {second['document_id']}") in capture["text"]


def test_a_newer_revision_of_any_one_view_makes_his_approval_of_the_set_stale(
        wired, shown, marks, draft, tmp_path):  # noqa: F811
    inbox_id, (first, second) = sent(wired, draft, tmp_path, approval=True)
    ok, said = answer(wired, "page", inbox_id, package_inbox.APPROVAL_ID, "approve")
    assert ok, said
    assert not approval(inbox_id).stale
    newer = language_documents.Documents().validate(
        **{**second_draft(draft), "source": second_draft(draft)["source"].replace("record", "class")},
        document_id=second["document_id"], parent_revision=second["revision"])
    assert newer["check"]["ok"]
    q = approval(inbox_id)
    assert q.stale and not q.counts() and not q.ends_waits(), "the second view moved on"
    assert still_waiting(waiting_on(inbox_id))


# -- make_package: a view set from two checked documents, through the fresh author ----------

def test_make_package_sends_a_view_set_of_its_documents_on_their_work_item(
        factory, draft, tmp_path, capsys, monkeypatch):  # noqa: F811
    from tests.test_architecture_packages import (SESSION, agent, collect, handoff,
                                                  validate_arguments)
    who = agent()
    service = language_document_api.Adapter().documents
    first = service.validate(**validate_arguments(draft), source=draft["source"])
    second = service.validate(**validate_arguments(draft), source=second_draft(draft)["source"])
    story = tmp_path / "authored-story.json"
    story.write_text(json.dumps(walk([first["document_id"], second["document_id"]])))
    monkeypatch.setenv("AUTHOR_TEST_STORY", str(story))
    code = make_package.main([
        "--document", first["document_id"], "--revision", first["revision"],
        "--document", second["document_id"], "--revision", second["revision"],
        "--handoff", str(handoff(tmp_path)), "--engine", "claude", "--session", SESSION,
        "--out", str(tmp_path / "out"), "--deliver-to", "http://testserver"])
    assert code == 0, capsys.readouterr().out
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    assert record["work"] == draft["work_id"]
    assert record["views"] == [{"document": d["document_id"], "revision": d["revision"]}
                               for d in (first, second)]
    package = work_packages.find(record["package"])
    assert [v["id"] for v in language_projection.views_shown(package)] == [
        first["document_id"], second["document_id"]]
    assert [s.view for s in package.story().slides] == [
        first["document_id"], second["document_id"], None]
    # Each view is grounded by its own document id, for a follow-up on it.
    grounded = collect(factory.client, who, {"operation": "ground", "arguments": {
        "package_id": package.id, "document_id": second["document_id"]}})
    assert grounded["ok"], grounded
    assert grounded["result"]["revision"] == second["revision"]
    assert grounded["result"]["source"]["text"] == second_draft(draft)["source"]
