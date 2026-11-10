"""A review package's answer is his review (work_api.review_from_package).

A package sent with `make_package --work <item>` that asks the well-known
question `approve-item` (options approve, not-done, add-work, his words
under the last two) is the item's review: his answer to it, through the
package's own decide.py (by voice or the Work tab's Decisions), is recorded
by the same `work_api.review` as the item's Approve, Not done yet and Add
work, once per answer. Its own question never stands in the way of
Approve; any other question a package sent for the item asks does, on the
page and on the server alike."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from jarvis import data_paths, make_package, package_signature, run_store, work_store
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_package_inbox import BROWSER, EXAMPLES, INBOX, send
from tests.test_work_packages import marks  # noqa: F401
from tests.test_work_states import _key, _piece, _review

PREFIX = "window.DECK = "


def review_zip(tmp_path: Path, also_ask: bool = False) -> bytes:
    """The example deck made into a review package: its first decision is
    `approve-item` with the three review options; with `also_ask`, the
    deck's other decisions stay too. Stamped as make_package stamps it."""
    folder = tmp_path / ("review-also" if also_ask else "review")
    shutil.copytree(EXAMPLES / "decision-deck", folder)
    data = folder / "deck-data.js"
    text = data.read_text()
    start = text.index(PREFIX) + len(PREFIX)
    deck, end = json.JSONDecoder().raw_decode(text, start)
    first = {**deck["decisions"][0], "id": "approve-item", "question": "Is this item done?",
             "recommended": "approve",
             "options": [{"id": "approve", "name": "Approve", "says": "It is done."},
                         {"id": "not-done", "name": "Not done yet", "says": "Say what is left."},
                         {"id": "add-work", "name": "Add work", "says": "Say what to add."}]}
    deck["decisions"] = [first] + (deck["decisions"][1:] if also_ask else [])
    deck["name"] = "item-review" + ("-also" if also_ask else "")
    data.write_text(text[:start] + json.dumps(deck) + text[end:])
    story = folder / "story.json"
    told = json.loads(story.read_text())
    told["slides"] = [s for s in told["slides"]
                      if s.get("waits") in (None, "kettle-kind") or also_ask]
    story.write_text(json.dumps(told).replace("kettle-kind", "approve-item"))
    manifest = json.loads((folder / "package.json").read_text())
    manifest["name"] = deck["name"]
    (folder / "package.json").write_text(json.dumps(manifest))
    raw, signed = tmp_path / f"{deck['name']}-raw.zip", tmp_path / f"{deck['name']}.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    from tests.agent_key_fixtures import onboarded_auth
    agent = onboarded_auth(session="gone").agent
    package_signature.sign(raw, signed, agent.private, agent.public, "gone", (package_signature.DECISION_CHECKED,))
    return signed.read_bytes()


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    made = work_store.create("Package inbox", "folder", str(folder))
    _piece(wired, made.id, "build", auth=_key(), state="verifying", evidence=["abc1234"])
    return made


def _sent(w, tmp_path, item, also_ask=False) -> str:
    r = send(w, review_zip(tmp_path, also_ask), work=item.id, session="gone")
    assert r.status_code == 200, r.text
    return r.json()["inbox"]["id"]


def _answer_by_page(w, inbox_id, option, words=""):
    r = w.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                      json={"decision": "approve-item", "option": option, "words": words})
    assert r.status_code == 200, r.text
    return r.json()


def test_approve_in_the_package_approves_the_item(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    said = _call(wired, "answer_package_decision", package=inbox_id, decision="approve-item",
                 option="approve")["text"]
    assert f"It is his review of {item.id}" in said, said
    got = work_store.get(item.id)
    assert got.state == "done" and got.approved_at
    [event] = [e for e in work_store.events(item.id) if e.kind == work_store.APPROVED]
    assert event.detail["source"]["package"] == inbox_id and event.detail["where"] == "package"
    shown = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert shown["package_review"]["outcome"] == "approved", "the item says what his act did"
    assert shown["latest_update"].startswith("Approved by him in the package")


def test_not_done_in_the_package_is_not_done_yet_with_his_note(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "not-done", "The empty state is missing.")
    got = work_store.get(item.id)
    assert (got.state, got.review_note) == ("activity_unknown", "The empty state is missing.")
    folder = data_paths.answers_dir() / "inbox"
    assert any("The empty state is missing." in p.read_text() for p in folder.glob("*.md"))


def test_add_work_in_the_package_adds_a_piece_in_his_words(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "add-work", "Add a search box")
    assert "add-a-search-box" in {p.key for p in work_store.get(item.id).pieces}


def test_his_review_is_recorded_once_per_answer(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "add-work", "Add a search box")
    # The deck is counted again (the frame saved, it was opened): nothing new.
    wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    reviews = [e for e in work_store.events(item.id) if e.kind in work_store.REVIEW_KINDS]
    assert len(reviews) == 1


def test_an_answer_that_cannot_be_applied_says_why_on_the_item(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    said = _answer_by_page(wired, inbox_id, "add-work")["said"]
    assert "was not applied" in said and "you gave no words with it" in said, said
    got = work_store.get(item.id)
    assert got.state == "verifying"
    assert (got.package_review.outcome, got.package_review.why) == (
        "not_applied", "you gave no words with it"), "in his own terms, never the agent's"
    # Noted once: counted again, nothing more is written.
    wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    assert len([e for e in work_store.events(item.id) if e.kind == work_store.REVIEW_NOTED]) == 1


def test_approve_while_a_piece_still_works_is_noted_not_lost(wired, marks, tmp_path, item):
    _piece(wired, item.id, "polish", auth=_key("polisher"), state="working")
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "approve")
    got = work_store.get(item.id)
    assert got.state == "activity_unknown" and got.package_review.outcome == "not_applied"
    assert got.package_review.why == "a piece of it is still being worked on"


def test_a_package_for_no_item_reviews_nothing(wired, marks, tmp_path, item):
    r = send(wired, review_zip(tmp_path), session="gone")
    inbox_id = r.json()["inbox"]["id"]
    said = _answer_by_page(wired, inbox_id, "approve")["said"]
    assert "review" not in said and work_store.get(item.id).state == "verifying"


# -- Approve and the questions packages ask ---------------------------------------

def test_the_review_question_never_stands_in_the_way_of_approve(wired, marks, tmp_path, item):
    _sent(wired, tmp_path, item)
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert (got["package_questions"], got["approvable"]) == (1, True)
    assert wired.client.get("/api/work").json()["items"][0]["approvable"] is True
    assert _review(wired, item.id, "approve")["item"]["state"] == "done"


def test_any_other_package_question_does_on_the_page_and_the_server(wired, marks, tmp_path, item):
    _sent(wired, tmp_path, item, also_ask=True)
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert got["approvable"] is False
    assert got["state"] == "waiting_on_tim", "not ready for review while a question waits on him"
    assert wired.client.get("/api/work").json()["items"][0]["approvable"] is False
    assert "still asks him" in _review(wired, item.id, "approve", ok=False)["error"]


# -- his acts on record before the link went live, and his sign-off ---------------

def _decide(inbox_id, option, words=""):
    """His answer through the package's own decide.py alone, as before the
    link went live: nothing counted, nothing reviewed."""
    import subprocess
    import sys
    from jarvis import package_inbox, work_packages
    package = work_packages.find(package_inbox.get(inbox_id).package_id)
    argv = [sys.executable, "-I", str(package.folder / "scripts" / "decide.py"), "approve-item",
            option] + ([words] if words else [])
    assert subprocess.run(argv, cwd=package.folder, capture_output=True).returncode == 0
    return package


def _approvals(item):
    return [e for e in work_store.events(item.id) if e.kind == work_store.APPROVED]


def test_an_approval_on_record_closes_the_item_at_start_once(wired, marks, tmp_path, item):
    """Tim's approve on the sign-off review package, before the restart."""
    from jarvis import package_inbox, work_api
    inbox_id = _sent(wired, tmp_path, item)
    _decide(inbox_id, "approve")
    with pytest.MonkeyPatch.context() as m:
        # Signed off before the link existed: nothing applied it then.
        m.setattr(wired.server, "_review_from_package", lambda *a, **k: None)
        package_id = package_inbox.get(inbox_id).package_id
        r = wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
        assert r.status_code == 200, r.text
    assert work_store.get(item.id).state == "verifying"
    assert work_api.apply_package_reviews() == 1
    got = work_store.get(item.id)
    assert got.state == "done" and got.approved_at and got.package_review.outcome == "approved"
    assert work_api.apply_package_reviews() == 0 and len(_approvals(item)) == 1


def test_signing_off_applies_an_answer_given_without_a_count(wired, marks, tmp_path, item):
    from jarvis import package_inbox
    inbox_id = _sent(wired, tmp_path, item)
    _decide(inbox_id, "approve")
    package_id = package_inbox.get(inbox_id).package_id
    wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert work_store.get(item.id).state == "done" and len(_approvals(item)) == 1


def test_signing_off_unanswered_says_review_closed_without_a_verdict(wired, marks, tmp_path, item):
    """The takeover review, signed off with no answer: the item says so."""
    from jarvis import package_inbox, work_api
    inbox_id = _sent(wired, tmp_path, item)
    package_id = package_inbox.get(inbox_id).package_id
    r = wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert r.status_code == 200, r.text
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert got["package_review"]["outcome"] == "no_verdict"
    assert got["latest_update"].startswith("Review closed without a verdict")
    assert got["state"] == "verifying" and got["approvable"] is True, "his review is still his"
    assert work_api.apply_package_reviews() == 0, "noted once"


def test_a_package_he_set_aside_is_no_review(wired, marks, tmp_path, item):
    from jarvis import work_api
    inbox_id = _sent(wired, tmp_path, item)
    _decide(inbox_id, "approve")
    wired.client.post(f"{INBOX}/{inbox_id}/close", headers=BROWSER)
    assert work_api.apply_package_reviews() == 0
    assert work_store.get(item.id).state == "verifying"


def test_the_server_applies_them_at_start(wired):
    import inspect
    assert "_apply_package_reviews" in inspect.getsource(wired.server.lifespan)


def test_signing_off_unanswered_after_he_reviewed_it_another_way_says_nothing(
        wired, marks, tmp_path, item):
    """Takeover mode: approved in its header, then its review package signed
    off unanswered (or swept at start): no "without a verdict" after it."""
    from jarvis import package_inbox, work_api
    inbox_id = _sent(wired, tmp_path, item)
    _review(wired, item.id, "approve")
    package_id = package_inbox.get(inbox_id).package_id
    with pytest.MonkeyPatch.context() as m:
        m.setattr(wired.server, "_review_from_package", lambda *a, **k: None)
        wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert work_api.apply_package_reviews() == 0
    got = work_store.get(item.id)
    assert got.state == "done" and got.package_review is None
    assert not [e for e in work_store.events(item.id) if e.kind == work_store.REVIEW_NOTED]


def test_an_open_package_names_itself_as_where_he_is_needed(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    needs = wired.client.get(f"/api/work/{item.id}").json()["item"]["needs_you"]
    assert [(n["kind"], n.get("inbox")) for n in needs] == [("review_package", inbox_id),
                                                            ("review", None)]
    other = _sent(wired, tmp_path, item, also_ask=True)
    needs = wired.client.get(f"/api/work/{item.id}").json()["item"]["needs_you"]
    assert ("package", other) in [(n["kind"], n.get("inbox")) for n in needs]
    said = _call(wired, "work_status", item=item.id)["text"]
    assert "package and answer its questions" in said


def test_a_done_item_never_shows_review_closed_without_a_verdict(wired, marks, tmp_path, item):
    """Takeover mode: a no-verdict line written before the fix, then Done."""
    from jarvis import work_api
    inbox_id = _sent(wired, tmp_path, item)
    work_api._note_review(item.id, work_store.NO_VERDICT, {"package": inbox_id, "answered_at": ""},
                          "Review closed without a verdict.")
    assert work_store.get(item.id).package_review.outcome == "no_verdict"
    _review(wired, item.id, "approve")
    with run_store._connect() as conn:
        conn.execute("DELETE FROM work_events WHERE item_id=? AND kind=?", (item.id, "approved"))
        conn.commit()
    got = work_store.get(item.id)
    assert got.state == "done" and got.package_review is None


def test_approve_in_the_package_after_approving_in_the_header_is_no_failure(
        wired, marks, tmp_path, item):
    """Approved in the item's header, then Approve in its review package:
    the same act twice, so nothing is noted and nothing says "not applied"."""
    inbox_id = _sent(wired, tmp_path, item)
    _review(wired, item.id, "approve")
    _answer_by_page(wired, inbox_id, "approve")
    got = work_store.get(item.id)
    assert got.state == "done" and got.package_review is None
    assert not [e for e in work_store.events(item.id) if e.kind == work_store.REVIEW_NOTED]


def test_a_done_item_never_shows_not_applied(wired, marks, tmp_path, item):
    """A not-applied line written before the fix stays off a done item."""
    from jarvis import work_api
    inbox_id = _sent(wired, tmp_path, item)
    _review(wired, item.id, "approve")
    work_api._note_review(item.id, work_store.NOT_APPLIED, {"package": inbox_id, "answered_at": "x"},
                          "His approve in the package was not applied.", "It is not ready")
    assert work_store.get(item.id).package_review is None


def test_not_done_yet_shows_his_note_once(wired, marks, tmp_path, item):
    got = _review(wired, item.id, "not_done", "The empty state is missing.")["item"]
    assert got["review_note"] == "The empty state is missing."
    assert "The empty state is missing." not in got["latest_update"], "said once, as his note"


def test_approve_is_only_for_an_item_ready_for_his_review(wired, tmp_path):
    folder = tmp_path / "planned"
    folder.mkdir()
    planned = work_store.create("Denice page", "folder", str(folder))
    got = wired.client.get(f"/api/work/{planned.id}").json()["item"]
    assert (got["state"], got["approvable"]) == ("planned", False)
    assert "not ready for review" in _review(wired, planned.id, "approve", ok=False)["error"]


# --- answered after Done, and not done yet with no words -------------------------------

def test_not_done_yet_with_no_words_goes_back_to_working(wired, marks, tmp_path, item):
    """His words are optional: it goes back all the same, saying so."""
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "not-done")
    got = work_store.get(item.id)
    assert (got.state, got.review_note) == ("activity_unknown", work_store.NO_NOTE)
    assert [e for e in work_store.events(item.id) if e.kind == work_store.REVIEW_RETURNED]
    folder = data_paths.answers_dir() / "inbox"
    assert any("His note: no note given" in p.read_text() for p in folder.glob("*.md"))


def test_answered_after_done_counts_for_the_item_it_was_sent_for(wired, marks, tmp_path, item):
    """He pressed Done first, then answered not done yet in the package and
    pressed Done again: the answer is his review of that item, never lost on
    a new row with no link (pkg-f8c3b2f1d38e)."""
    from jarvis import package_inbox
    inbox_id = _sent(wired, tmp_path, item)
    package_id = package_inbox.get(inbox_id).package_id
    wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert work_store.get(item.id).package_review.outcome == "no_verdict"
    _decide(inbox_id, "not-done")                 # in the package, after Done
    wired.server._inbox_recount(wired.server.work_packages.find(package_id), "on the page")
    got = work_store.get(item.id)
    assert (got.state, got.review_note) == ("activity_unknown", work_store.NO_NOTE)
    r = wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert r.status_code == 200, r.text
    again = package_inbox.latest_for_package(package_id)
    assert again.id != inbox_id and again.work_id == item.id, "the new row keeps the link"
    assert len([e for e in work_store.events(item.id)
                if e.kind == work_store.REVIEW_RETURNED]) == 1, "once"


def test_the_start_pass_applies_a_not_done_given_after_done(wired, marks, tmp_path, item):
    """The live case: signed off unanswered, answered after, never applied.
    The start pass applies it once."""
    from jarvis import package_inbox, work_api
    inbox_id = _sent(wired, tmp_path, item)
    package_id = package_inbox.get(inbox_id).package_id
    wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    _decide(inbox_id, "not-done")                 # recorded, and nothing applied it
    assert work_store.get(item.id).state == "verifying"
    assert work_api.apply_package_reviews() == 1
    got = work_store.get(item.id)
    assert (got.state, got.review_note) == ("activity_unknown", work_store.NO_NOTE)
    assert work_api.apply_package_reviews() == 0
