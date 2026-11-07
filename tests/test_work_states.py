"""The Work record is authoritative (work_store.py): a piece waits on him or
is blocked only naming what holds it, nothing but that answer moves it on,
the item's state is worked out from its pieces, done is his approval alone,
and his review (Approve, Not done yet, Add work) is the same call from the
page and his voice. Records from before are brought in line by migration."""
from __future__ import annotations

import time

import pytest

from jarvis import access_keys, data_paths, run_store, work_store
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)

BROWSER = {"Origin": "http://localhost:5173"}


def _key(label="asker"):
    return {"Authorization": f"Bearer {access_keys.create(label, ['work'])}"}


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "speech"
    folder.mkdir()
    return work_store.create("Knowledge engine", "folder", str(folder))


def _report(w, item_id, kind, summary, auth=None, ok=True, **detail):
    r = w.client.post(f"/api/work/{item_id}/events", headers=auth or _key(),
                      json={"kind": kind, "summary": summary, "detail": detail})
    assert (r.status_code == 200) == ok, r.text
    return r.json()


def _piece(w, item_id, key, auth=None, ok=True, **detail):
    detail.setdefault("who", {"kind": "tim"})
    return _report(w, item_id, "piece", f"{key} reported.", auth=auth, ok=ok, key=key, **detail)


def _answer(w, item_id, key, answer="yes"):
    r = w.client.post(f"/api/work/{item_id}/decisions/{key}", headers=BROWSER,
                      json={"answer": answer})
    assert r.status_code == 200, r.text


def _review(w, item_id, action, words="", ok=True):
    r = w.client.post(f"/api/work/{item_id}/review", headers=BROWSER,
                      json={"action": action, "words": words})
    assert (r.status_code == 200) == ok, r.text
    return r.json()


def _pieces(item_id):
    return {p.key: p for p in work_store.get(item_id).pieces}


# -- the item's state is worked out, never taken from a report ---------------------

def test_work_going_on_reads_working_while_decisions_also_wait(wired, item):
    """The knowledge engine: two decisions open, its phases working."""
    key = _key()
    _report(wired, item.id, "decision_needed", "Vectors in ONNX?", auth=key, key="vectors-onnx")
    _report(wired, item.id, "decision_needed", "Switch it on?", auth=key, key="switch-on")
    _report(wired, item.id, "update", "Over to Tim.", auth=key, ok=False, state="waiting_on_tim")
    _piece(wired, item.id, "phase-2", auth=key, state="working")
    got = work_store.get(item.id)
    assert (got.state, got.also_waiting) == ("working", 2)
    assert [d.key for d in got.decisions] == ["vectors-onnx", "switch-on"], "still asked"
    row = wired.client.get("/api/work").json()["items"][0]
    assert (row["state"], row["also_waiting"]) == ("working", 2)
    assert "two decisions also waiting on him" in _call(wired, "work_status")["text"]


@pytest.mark.parametrize("states,expected", [
    ({"a": "waiting_on_tim"}, "waiting_on_tim"),
    ({"a": "blocked"}, "blocked"),
    ({"a": "waiting_on_tim", "b": "blocked"}, "waiting_on_tim"),
    ({"a": "waiting_on_tim", "b": "ready"}, "ready"),
    ({"a": "waiting_on_tim", "b": "planned"}, "planned"),
    ({"a": "verifying", "b": "waiting_on_tim"}, "waiting_on_tim"),
    ({"a": "working", "b": "waiting_on_tim"}, "working"),
    ({"a": "verifying", "b": "verifying"}, "verifying"),
])
def test_the_item_is_waiting_only_when_nothing_can_go_on(wired, item, states, expected):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    for k, state in states.items():
        until = {"waiting_on_tim": "decision:which", "blocked": "the font licence"}.get(state)
        _piece(wired, item.id, k, auth=key, state=state, **({"until": until} if until else {}))
    if "waiting_on_tim" not in states.values():
        _answer(wired, item.id, "which")
    assert work_store.get(item.id).state == expected


def test_paused_pieces_are_not_progress(wired, item, monkeypatch):
    """The knowledge engine again: every piece paused, two decisions open.
    Working only while a piece works and reports; paused, it waits on him."""
    key = _key()
    _report(wired, item.id, "decision_needed", "Vectors in ONNX?", auth=key, key="vectors-onnx")
    _report(wired, item.id, "decision_needed", "Switch it on?", auth=key, key="switch-on")
    _piece(wired, item.id, "build", auth=key, state="working")
    _piece(wired, item.id, "plan", auth=key, state="verifying")
    assert work_store.get(item.id).state == "working"
    later = time.time() + work_store.QUIET_AFTER_SEC + 60
    monkeypatch.setattr(work_store.time, "time", lambda: later)
    got = work_store.get(item.id)
    assert (got.state, got.also_waiting) == ("waiting_on_tim", 0)
    assert got.headline == "Vectors in ONNX?"
    _report(wired, item.id, "decision_withdrawn", "Not now.", auth=key, key="vectors-onnx")
    _report(wired, item.id, "decision_withdrawn", "Not now.", auth=key, key="switch-on")
    got = work_store.get(item.id)
    assert got.state == "verifying", "nothing waits on him but his review"


def test_paused_with_nothing_else_reads_working_but_quiet(wired, item, monkeypatch):
    key = _key()
    _piece(wired, item.id, "build", auth=key, state="working")
    later = time.time() + work_store.QUIET_AFTER_SEC + 60
    monkeypatch.setattr(work_store.time, "time", lambda: later)
    got = work_store.get(item.id)
    assert (got.state, got.quiet) == ("working", True)


@pytest.mark.parametrize("states,decision,approvable", [
    ({"a": "verifying"}, False, True),
    ({"a": "verifying", "b": "ready"}, False, True),
    ({"a": "verifying"}, True, False),
    ({"a": "verifying", "b": "working"}, False, False),
    ({"a": "verifying", "b": "blocked"}, False, False),
])
def test_approve_is_offered_only_when_it_can_be_given(wired, item, states, decision, approvable):
    key = _key()
    for k, state in states.items():
        _piece(wired, item.id, k, auth=key, state=state,
               **({"until": "the font licence"} if state == "blocked" else {}))
    if decision:
        _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert got["approvable"] is approvable
    r = wired.client.post(f"/api/work/{item.id}/review", headers=BROWSER, json={"action": "approve"})
    assert (r.status_code == 200) is approvable, r.text


def test_a_planned_piece_behind_a_waiting_one_cannot_go_on(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    _piece(wired, item.id, "a", auth=key, state="waiting_on_tim", until="decision:which")
    _piece(wired, item.id, "b", auth=key, after=["a"])
    assert work_store.get(item.id).state == "waiting_on_tim"


# -- held: named, and moved on only by its answer -----------------------------------

def test_waiting_names_an_open_decision_and_nothing_else(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    for until, why in ((None, "names what holds it"), ("the merge", "names the answer"),
                       ("decision:nope", "asked no decision")):
        r = _piece(wired, item.id, "a", auth=key, ok=False, state="waiting_on_tim",
                   **({"until": until} if until else {}))
        assert why in r["error"], r
    _answer(wired, item.id, "which")
    r = _piece(wired, item.id, "a", auth=key, ok=False, state="waiting_on_tim",
               until="decision:which")
    assert "already closed" in r["error"]


def test_no_report_moves_a_waiting_piece_on(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    _piece(wired, item.id, "a", auth=key, state="waiting_on_tim", until="decision:which")
    for state in ("working", "verifying", "done", "ready", "blocked", "planned"):
        r = _piece(wired, item.id, "a", auth=key, ok=False, state=state)
        assert "decision:which" in r["error"] and "piece of its own" in r["error"] \
            or "Done is Tim's" in r["error"], (state, r)
    _piece(wired, item.id, "a", auth=key, evidence=["abc1234"])   # a note is not progress
    assert _pieces(item.id)["a"].state == "waiting_on_tim"
    _answer(wired, item.id, "which")
    assert _pieces(item.id)["a"].state == "ready"
    _piece(wired, item.id, "a", auth=key, state="working")


def test_a_blocker_in_words_is_cleared_only_by_saying_so(wired, item):
    key = _key()
    _piece(wired, item.id, "fonts", auth=key, state="blocked", until="the font licence")
    r = _piece(wired, item.id, "fonts", auth=key, ok=False, state="working")
    assert "--cleared" in r["error"]
    _piece(wired, item.id, "fonts", auth=key, state="working", cleared=True)
    assert _pieces(item.id)["fonts"].until == []


def test_a_piece_waiting_on_two_answers_moves_on_after_both(wired, item):
    key = _key()
    for k in ("a", "b"):
        _report(wired, item.id, "decision_needed", f"{k}?", auth=key, key=k)
    _piece(wired, item.id, "p", auth=key, state="waiting_on_tim",
           until=["decision:a", "decision:b"], then="working")
    _answer(wired, item.id, "a")
    assert (_pieces(item.id)["p"].state, _pieces(item.id)["p"].until) == (
        "waiting_on_tim", ["decision:b"])
    _answer(wired, item.id, "b")
    assert _pieces(item.id)["p"].state == "working"


def test_a_withdrawn_decision_releases_what_waited_on_it(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    _piece(wired, item.id, "a", auth=key, state="waiting_on_tim", until="decision:which")
    _report(wired, item.id, "decision_withdrawn", "Asked too soon.", auth=key, key="which")
    assert _pieces(item.id)["a"].state == "ready"
    ended = [e for e in work_store.events(item.id) if e.detail.get("wait_ended")]
    assert ended[-1].detail["wait_ended"][0]["by"] == "asker"


def test_an_item_with_pieces_is_never_reported_held(wired, item):
    key = _key()
    _piece(wired, item.id, "a", auth=key, state="working")
    r = _report(wired, item.id, "update", "Stuck.", auth=key, ok=False, state="blocked",
                until="CI")
    assert "worked out from its pieces" in r["error"]


# -- done is his alone -----------------------------------------------------------

def test_no_reporter_can_say_done(wired, item):
    key = _key()
    for kind, detail in (("update", {"state": "done"}),
                         ("piece", {"key": "a", "who": {"kind": "tim"}, "state": "done"}),
                         ("approved", {}), ("review_returned", {"words": "x"}),
                         ("work_added", {"words": "x"})):
        r = _report(wired, item.id, kind, "Done.", auth=key, ok=False, **detail)
        assert "Done is Tim's" in r["error"] or "Only he" in r["error"], (kind, r)
    r = _piece(wired, item.id, "b", auth=key, ok=False, state="blocked", until="x", then="done")
    assert "Done is Tim's" in r["error"]
    r = wired.client.post(f"/api/work/{item.id}/review", headers=key, json={"action": "approve"})
    assert r.status_code == 403, "his review is the page's or his voice's, never a key's"


def test_approve_makes_it_and_every_piece_done_with_when(wired, item):
    key = _key()
    _piece(wired, item.id, "a", auth=key, state="verifying", evidence=["abc1234"])
    _piece(wired, item.id, "b", auth=key, state="verifying")
    assert work_store.get(item.id).state == "verifying"
    got = _review(wired, item.id, "approve")["item"]
    assert got["state"] == "done" and got["approved_at"] and \
        {p["state"] for p in got["pieces"]} == {"done"}
    r = _report(wired, item.id, "update", "More.", auth=key, ok=False, state="working")
    assert "approved this done" in r["error"]
    r = _piece(wired, item.id, "c", auth=key, ok=False, state="working")
    assert "approved this done" in r["error"]


def test_approve_is_refused_while_something_waits_on_him(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Which?", auth=key, key="which")
    assert "still open" in _review(wired, item.id, "approve", ok=False)["error"]
    _piece(wired, item.id, "a", auth=key, state="blocked", until="the font licence")
    _answer(wired, item.id, "which")
    assert "blocked by" in _review(wired, item.id, "approve", ok=False)["error"]


def test_not_done_yet_sends_it_back_with_his_note_for_its_agents(wired, item):
    key = _key()
    _piece(wired, item.id, "a", auth=key, state="verifying")
    _review(wired, item.id, "approve")
    got = _review(wired, item.id, "not_done", "The logo is still blurry.")["item"]
    assert (got["state"], got["review_note"], got["approved_at"]) == (
        "working", "The logo is still blurry.", None)
    assert got["pieces"][0]["state"] == "working"
    folder = data_paths.answers_dir() / "speech"
    [written] = list(folder.glob(f"{item.id}-review-*.md"))
    assert "The logo is still blurry." in written.read_text()
    assert "Not done yet, he said: The logo is still blurry." in _call(
        wired, "work_status", item=item.id)["text"]
    assert "Say it in his words" in _review(wired, item.id, "not_done", ok=False)["error"]


def test_add_work_is_a_new_piece_in_his_words_and_its_agents_are_told(wired, item):
    key = _key()
    _piece(wired, item.id, "a", auth=key, state="verifying")
    _review(wired, item.id, "approve")
    got = _review(wired, item.id, "add_work", "Add a dark mode")["item"]
    added = next(p for p in got["pieces"] if p["key"] == "add-a-dark-mode")
    assert (added["state"], added["title"]) == ("ready", "Add a dark mode")
    assert got["state"] == "ready" and got["approved_at"] is None
    texts = [p.read_text() for p in (data_paths.answers_dir() / "speech").glob("*.md")]
    assert any("add-a-dark-mode" in t for t in texts)


def test_his_voice_reviews_through_the_same_call(wired, item):
    key = _key()
    _piece(wired, item.id, "a", auth=key, state="verifying")
    said = _call(wired, "review_work", item=item.id, action="approve")["text"]
    assert said.startswith("Approved") and work_store.get(item.id).state == "done"
    said = _call(wired, "review_work", item=item.id, action="not_done", words="Again.")["text"]
    assert "back to working" in said and work_store.get(item.id).review_note == "Again."
    said = _call(wired, "review_work", item=item.id, action="add_work", words="Docs")["text"]
    assert "new piece" in said and "docs" in _pieces(item.id)
    refused = _call(wired, "review_work", origin="event", item=item.id, action="approve")
    assert not refused["ok"]


# -- records from before ---------------------------------------------------------

def _at_step_four(tmp_path):
    """A database at work schema 4 (before holds and his approval)."""
    from contextlib import closing
    with closing(run_store._connect()) as conn:
        run_store.migrate(conn, "work_schema",
                          {k: v for k, v in work_store._MIGRATIONS.items() if k <= 4}, "work")
        conn.commit()
    work_store._migrated.clear()


def _legacy_item(conn, item_id, state, path):
    conn.execute("INSERT INTO work_items (id, title, source_kind, source_path, state, "
                 "latest_update, revision, created_at, updated_at) VALUES (?, ?, 'folder', ?, ?, "
                 "'Reported.', 1, 1, 100)", (item_id, item_id, path, state))


def _legacy_piece(conn, item_id, key, state, note_at=100):
    conn.execute("INSERT INTO work_pieces (item_id, key, title, who_kind, state, position, "
                 "note, note_at) VALUES (?, ?, ?, 'tim', ?, 0, 'Note.', ?)",
                 (item_id, key, key, state, note_at))


def _legacy_decision(conn, item_id, key, answered_at=None):
    conn.execute("INSERT INTO work_decisions (item_id, key, question, options, opened_at, "
                 "answer, answered_where, answered_at) VALUES (?, ?, 'Q?', '[]', 50, ?, ?, ?)",
                 (item_id, key, "yes" if answered_at else None,
                  "page" if answered_at else None, answered_at))


def test_records_from_before_are_brought_in_line(wired, tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    _at_step_four(tmp_path)
    from contextlib import closing
    with closing(run_store._connect()) as conn:
        # Planning's item: the reporter left its piece waiting after his answer.
        _legacy_item(conn, "work-planning", "waiting_on_tim", str(folder))
        _legacy_piece(conn, "work-planning", "design-and-review", "waiting_on_tim")
        _legacy_decision(conn, "work-planning", "ownership", answered_at=200)
        # One open decision: the waiting piece is linked to it.
        _legacy_item(conn, "work-one", "working", str(folder))
        _legacy_piece(conn, "work-one", "w", "waiting_on_tim")
        _legacy_decision(conn, "work-one", "only")
        # Two open: it cannot say which, so it is left unlinked and flagged.
        _legacy_item(conn, "work-two", "working", str(folder))
        _legacy_piece(conn, "work-two", "w", "waiting_on_tim")
        _legacy_decision(conn, "work-two", "a")
        _legacy_decision(conn, "work-two", "b")
        # Reported done, never approved; and one closed, left as it is.
        _legacy_item(conn, "work-done", "done", str(folder))
        _legacy_piece(conn, "work-done", "p", "done")
        _legacy_item(conn, "work-closed", "done", str(folder))
        conn.execute("UPDATE work_items SET closed_at=1 WHERE id='work-closed'")
        conn.commit()
    planning = work_store.get("work-planning")
    assert planning.pieces[0].state == "ready" and planning.state == "ready"
    one = work_store.get("work-one")
    assert one.pieces[0].until == ["decision:only"] and one.state == "waiting_on_tim"
    two = work_store.get("work-two")
    assert two.pieces[0].until == [] and two.pieces[0].unlinked
    assert any(b.kind == "piece_waiting" and b.unlinked for b in two.blockers)
    done = work_store.get("work-done")
    assert done.state == "verifying" and done.approved_at is None
    assert done.pieces[0].state == "verifying" and done.pieces[0].note.startswith("Not approved")
    assert work_store.get("work-closed").reported_state == "done"
