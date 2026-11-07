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

from jarvis import data_paths, decision_stamp, make_package, work_store
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
    raw, stamped = tmp_path / f"{deck['name']}-raw.zip", tmp_path / f"{deck['name']}.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    decision_stamp.stamp(raw, stamped, data_paths.ensure_tool_token())
    return stamped.read_bytes()


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
    assert event.detail["source"]["package"] == inbox_id and event.detail["where"] == "voice"


def test_not_done_in_the_package_is_not_done_yet_with_his_note(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    _answer_by_page(wired, inbox_id, "not-done", "The empty state is missing.")
    got = work_store.get(item.id)
    assert (got.state, got.review_note) == ("working", "The empty state is missing.")
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


def test_a_review_with_no_words_is_not_taken_and_says_why(wired, marks, tmp_path, item):
    inbox_id = _sent(wired, tmp_path, item)
    said = _answer_by_page(wired, inbox_id, "not-done")["said"]
    assert "not taken as his review" in said and "his words" in said, said
    assert work_store.get(item.id).state == "verifying"


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
    assert wired.client.get("/api/work").json()["items"][0]["approvable"] is False
    assert "still asks him" in _review(wired, item.id, "approve", ok=False)["error"]
