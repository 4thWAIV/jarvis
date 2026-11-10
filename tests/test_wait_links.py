"""Nothing stays waiting on Tim once he has answered (wait_links.py).

A workflow's wait and a Work item's piece waiting on him name the answer
they wait for; every route he answers by ends them in the same transaction
as the answer; one already answered is ended when found (at start and on
every read); a wait with no link is flagged after a day; an answered item
nobody has started on says so, and is flagged after half an hour; an item
whose pieces are all done reads as ready to close; and his answer is written
for the agent that asked it."""
from __future__ import annotations

import time

import pytest

from jarvis import (attention_store, package_inbox, run_store, wait_links,
                    workflow_store, work_store)
from tests.decision_fixtures import DECISION_KINDS, asker_of, on_record
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_package_inbox import (BROWSER, deck_zip, marks,  # noqa: F401
                                      opened_deck, shown)

DAY = wait_links.UNLINKED_AFTER_SEC


def _key(label="asker"):
    """An onboarded agent (by its label's session) holding work and
    workflows, signing every request."""
    from tests.agent_key_fixtures import onboarded_auth
    return onboarded_auth(("work", "workflows"), f"sess-{label}")


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "speech"
    folder.mkdir()
    return work_store.create("Speech migration", "folder", str(folder))


def _report(w, item_id, kind, summary, auth=None, **detail):
    if kind in DECISION_KINDS:
        # No reporter asks a decision any more (round 3): one on record.
        return on_record(item_id, kind, summary, asker_of(auth or _key()), **detail)["item"]
    r = w.client.post(f"/api/work/{item_id}/events", auth=auth or _key(),
                      json={"kind": kind, "summary": summary, "detail": detail})
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _piece(item_id):
    return {p.key: p for p in work_store.get(item_id).pieces}


def _age(sql: str, *args) -> None:
    """Move a recorded time back, as if it had happened long ago."""
    with run_store._connect() as conn:
        conn.execute(sql, args)
        conn.commit()


# -- links ----------------------------------------------------------------------

@pytest.mark.parametrize("text,link", [
    ("decision:merge-pr", ("decision", "", "merge-pr")),
    ("work:work-abc123:merge", ("work", "work-abc123", "merge")),
    ("workflow:wf-1a2b:go", ("workflow", "wf-1a2b", "go")),
    ("package:tea-deck", ("package", "tea-deck", None)),
    ("package:tea-deck-1.0.0:kettle-kind", ("package", "tea-deck-1.0.0", "kettle-kind")),
])
def test_a_link_names_one_answer(text, link):
    found = wait_links.parse(text)
    assert (found.kind, found.ref, found.key) == link and str(found) == text


@pytest.mark.parametrize("text", ["decision:Bad Key", "work:only-one", "package:", "workflow:a:b:c"])
def test_a_link_that_is_not_one_is_refused(text):
    with pytest.raises(wait_links.LinkRefused):
        wait_links.parse(text)


def test_plain_words_are_no_link():
    assert wait_links.parse("the test suite, about ten minutes") is None


# -- workflow waits, by every route ---------------------------------------------

def _waiting_workflow(key="pr-74"):
    wid = workflow_store.start("Diagnostics work", "jarvis", [])
    workflow_store.record(workflow_store.WorkflowEvent(wid, "decision_needed", None,
                                                       "Merge PR 74?", {"key": key}))
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting for his merge approval.",
        {"key": key, "until": f"decision:{key}"}))
    return wid


def _wait_ended(wid):
    return [e for e in workflow_store.events(wid) if e.kind == "wait_ended"]


def test_his_answer_on_the_page_ends_the_workflow_wait_in_the_same_write(wired):
    wid = _waiting_workflow()
    r = wired.client.post(f"/api/workflows/{wid}/decisions/pr-74", headers=BROWSER,
                          json={"answer": "Merge it"})
    assert r.status_code == 200, r.text
    snap = workflow_store.snapshot(wid)
    assert snap.open_waits == [] and snap.state == "running"
    [ended] = _wait_ended(wid)
    assert ended.detail["by"] == "user" and ended.detail["how"] == "page"
    assert ended.detail["link"] == "decision:pr-74" and ended.detail["answered_at"] > 0
    assert {"type": "workflows"} in wired.hints


def test_his_answer_by_voice_ends_it_too(wired):
    wid = _waiting_workflow()
    said = _call(wired, "answer_workflow_decision", workflow=wid, decision="pr-74",
                 answer="Merge it")["text"]
    assert "Recorded" in said
    [ended] = _wait_ended(wid)
    assert ended.detail["how"] == "voice" and workflow_store.snapshot(wid).open_waits == []


def test_a_reporter_cannot_answer_for_him(wired):
    wid = _waiting_workflow()
    r = wired.client.post(f"/api/workflows/{wid}/events", auth=_key(),
                          json={"kind": "decision_made", "summary": "Decided.",
                                "detail": {"key": "pr-74", "answer": "yes", "by": "user"}})
    assert r.status_code == 200
    assert workflow_store.snapshot(wid).open_waits == ["pr-74"], "only his answer ends it"


def test_a_wait_on_a_decision_the_workflow_never_asked_is_refused(wired):
    wid = workflow_store.start("Diagnostics work", "jarvis", [])
    with pytest.raises(workflow_store.WorkflowRefused, match="asked no decision"):
        workflow_store.record(workflow_store.WorkflowEvent(
            wid, "wait_started", None, "Waiting.", {"key": "x", "until": "decision:nope"}))


def test_a_wait_opened_on_an_answer_already_given_ends_at_once(wired):
    wid = workflow_store.start("Diagnostics work", "jarvis", [])
    workflow_store.record(workflow_store.WorkflowEvent(wid, "decision_needed", None, "Merge?",
                                                       {"key": "pr-73"}))
    wired.client.post(f"/api/workflows/{wid}/decisions/pr-73", headers=BROWSER,
                      json={"answer": "yes"})
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting.", {"key": "pr-73", "until": "decision:pr-73"}))
    assert workflow_store.snapshot(wid).open_waits == []


# -- Work pieces, by every route ------------------------------------------------

def _waiting_piece(w, item, until="decision:merge", then=None):
    key = _key()
    _report(w, item.id, "decision_needed", "Merge the PR?", auth=key, key="merge")
    detail = {"key": "land", "who": {"kind": "tim"}, "state": "waiting_on_tim", "until": until}
    if then:
        detail["then"] = then
    _report(w, item.id, "piece", "Waiting for his merge approval.", auth=key, **detail)
    return key


def test_his_answer_on_the_page_moves_the_waiting_piece_on(wired, item):
    _waiting_piece(wired, item)
    assert _piece(item.id)["land"].until == ["decision:merge"]
    r = wired.client.post(f"/api/work/{item.id}/decisions/merge", headers=BROWSER,
                          json={"answer": "Merge it"})
    assert r.status_code == 200, r.text
    land = _piece(item.id)["land"]
    assert (land.state, land.until) == ("ready", [])
    [moved] = [e for e in work_store.events(item.id) if e.detail.get("wait_ended")]
    [ended] = moved.detail["wait_ended"]
    assert (ended["how"], ended["by"], ended["link"]) == ("page", "user", "decision:merge")


def test_his_answer_by_voice_moves_it_to_its_next_state(wired, item):
    _waiting_piece(wired, item, then="verifying")
    _call(wired, "answer_work_decision", item=item.id, decision="merge", answer="Merge it")
    assert _piece(item.id)["land"].state == "verifying"


def test_a_piece_waits_only_on_a_decision_its_item_asked(wired, item):
    r = wired.client.post(f"/api/work/{item.id}/events", auth=_key(), json={
        "kind": "piece", "summary": "Waiting.",
        "detail": {"key": "land", "who": {"kind": "tim"}, "state": "waiting_on_tim",
                   "until": "decision:nope"}})
    assert r.status_code == 409 and "asked no decision" in r.json()["error"]


def test_a_workflow_wait_on_a_work_decision_ends_when_he_answers_there(wired, item, monkeypatch):
    # The write is on a worker thread; its hint goes to the pages from any thread.
    monkeypatch.setattr(wired.server, "_hint_from_any_thread", wired.hints.append)
    _report(wired, item.id, "decision_needed", "Merge?", key="merge")
    wid = workflow_store.start("Diagnostics work", "jarvis", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting.", {"key": "merge",
                                                "until": f"work:{item.id}:merge"}))
    wired.client.post(f"/api/work/{item.id}/decisions/merge", headers=BROWSER,
                      json={"answer": "yes"})
    assert workflow_store.snapshot(wid).open_waits == []
    assert {"type": "workflows"} in wired.hints


# -- packages: decide.py and finishing with it ----------------------------------

def test_an_answer_through_decide_py_ends_a_wait_on_that_decision(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path)
    wid = workflow_store.start("Tea service", "tea", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting on the kettle.",
        {"key": "kettle", "until": f"package:{inbox_id}:kettle-kind"}))
    _call(wired, "answer_package_decision", package=inbox_id, decision="kettle-kind",
          option="recommended")
    assert workflow_store.snapshot(wid).open_waits == []


def test_signing_a_package_off_ends_every_wait_on_it(wired, shown, marks, tmp_path, item):
    inbox_id = opened_deck(wired, tmp_path, session="gone")
    _report(wired, item.id, "piece", "Waiting on the deck.", **{
        "key": "deck", "who": {"kind": "tim"}, "state": "waiting_on_tim",
        "until": f"package:{inbox_id}"})
    wid = workflow_store.start("Tea service", "tea", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting on the deck.",
        {"key": "deck", "until": "package:decision-deck-example"}))
    _call(wired, "answer_package_decision", package=inbox_id, decision="kettle-kind",
          option="recommended")
    assert _piece(item.id)["deck"].state == "waiting_on_tim", "a decision is not the package"
    package_id = package_inbox.get(inbox_id).package_id
    r = wired.client.post(f"/api/packages/{package_id}/signoff", headers=BROWSER)
    assert r.status_code == 200, r.text
    assert _piece(item.id)["deck"].state == "ready"
    assert workflow_store.snapshot(wid).open_waits == [], "by its name too"
    [ended] = _wait_ended(wid)
    assert ended.detail["how"] == "signed_off" and ended.detail["by"] == "user"


def test_a_package_s_own_workflow_wait_ends_with_his_sign_off(wired, shown, marks, tmp_path):
    wid = workflow_store.start("Tea service", "tea", [])
    inbox_id = opened_deck(wired, tmp_path, workflow=wid)
    assert workflow_store.snapshot(wid).open[0].detail["link"] == f"package:{inbox_id}"
    _call(wired, "sign_off_package", package=inbox_id)
    assert workflow_store.snapshot(wid).open_waits == []
    assert any(e.detail.get("by") == "user" for e in _wait_ended(wid))


# -- the sweep --------------------------------------------------------------------

def test_a_wait_left_open_on_an_answer_already_given_is_swept(wired, item, monkeypatch):
    _waiting_piece(wired, item, until=f"work:{item.id}:merge")
    wid = _waiting_workflow("merge")
    # Recorded as it was before links ended anything: his answer, and both waits left open.
    with monkeypatch.context() as m:
        m.setattr(wait_links, "settle", lambda conn, found, now: wait_links.Settled())
        m.setattr(wait_links, "sweep", lambda: wait_links.Settled())
        wired.client.post(f"/api/work/{item.id}/decisions/merge", headers=BROWSER,
                          json={"answer": "yes"})
        workflow_store.record(workflow_store.WorkflowEvent(
            wid, "decision_made", None, "Answered.", {"key": "merge", "answer": "yes",
                                                       "by": "user"}))
    with run_store._connect() as conn:
        assert conn.execute("SELECT state FROM work_pieces WHERE key='land'").fetchone()[0] \
            == "waiting_on_tim"
    moved = wait_links.sweep()
    assert moved.work == {item.id} and moved.workflows == {wid}
    assert _piece(item.id)["land"].state == "ready"
    assert workflow_store.snapshot(wid).open_waits == []


def test_the_work_view_s_tick_sweeps_the_record(wired, item, monkeypatch):
    """The list is read from the held view, which reads nothing; the sweep
    a read of the list used to make is the view's tick's, every
    work_view.SWEEP_EVERY_SEC."""
    _waiting_piece(wired, item)
    with monkeypatch.context() as m:
        m.setattr(wait_links, "settle", lambda conn, found, now: wait_links.Settled())
        m.setattr(wait_links, "sweep", lambda: wait_links.Settled())
        wired.client.post(f"/api/work/{item.id}/decisions/merge", headers=BROWSER,
                          json={"answer": "yes"})
    wired.server.work_held.tick()
    listed = wired.client.get("/api/work").json()["items"]
    [land] = [p for p in listed[0]["pieces"] if p["key"] == "land"]
    assert land["state"] == "ready"


def test_the_server_sweeps_at_start(wired):
    import inspect
    assert "wait_links.sweep" in inspect.getsource(wired.server.lifespan)


# -- answered decisions clear the item's waiting ------------------------------------

def test_an_item_with_no_pieces_waits_on_every_hold_it_names(wired, item):
    key = _key()
    _report(wired, item.id, "decision_needed", "Build it?", auth=key, key="build")
    _report(wired, item.id, "decision_needed", "Ship it?", auth=key, key="ship")
    _report(wired, item.id, "update", "Over to Tim.", auth=key, state="waiting_on_tim",
            until=["decision:build", "decision:ship"])
    wired.client.post(f"/api/work/{item.id}/decisions/build", headers=BROWSER, json={"answer": "yes"})
    got = work_store.get(item.id)
    assert (got.state, got.until) == ("waiting_on_tim", ["decision:ship"])
    wired.client.post(f"/api/work/{item.id}/decisions/ship", headers=BROWSER, json={"answer": "yes"})
    got = work_store.get(item.id)
    assert (got.state, got.reported_state, got.until) == ("ready", "ready", [])


def _legacy(sql: str, *args) -> None:
    """A row as it was recorded before holds were kept."""
    _age(sql, *args)


# -- flags -------------------------------------------------------------------------

def test_a_waiting_piece_with_no_link_is_flagged_after_a_day(wired, item):
    _report(wired, item.id, "piece", "Waiting for his merge approval.", **{
        "key": "land", "who": {"kind": "tim"}})
    _legacy("UPDATE work_pieces SET state='waiting_on_tim', until='', waiting_since=?",
            time.time())
    [b] = [b for b in work_store.get(item.id).blockers if b.kind == "piece_waiting"]
    assert not b.unlinked
    _age("UPDATE work_pieces SET waiting_since=?", time.time() - DAY - 60)
    [b] = [b for b in work_store.get(item.id).blockers if b.kind == "piece_waiting"]
    assert b.unlinked
    # Rows written past the writers are only ever from before: read at start.
    wired.server.work_held.load()
    blocking = wired.client.get(f"/api/work/{item.id}").json()["item"]["blockers"]
    assert any(x["kind"] == "piece_waiting" and x["unlinked"] for x in blocking)
    assert "waiting, no link to an answer" in _call(wired, "work_status", item=item.id)["text"]


def test_a_linked_piece_is_never_flagged(wired, item):
    _waiting_piece(wired, item)
    _age("UPDATE work_pieces SET waiting_since=?", time.time() - DAY - 60)
    assert not any(b.unlinked for b in work_store.get(item.id).blockers)


def test_an_item_waiting_with_no_decision_is_a_blocker_flagged_after_a_day(wired, item):
    _legacy("UPDATE work_items SET state='waiting_on_tim', until='', waiting_since=?",
            time.time())
    [b] = [b for b in work_store.get(item.id).blockers if b.kind == "item_waiting"]
    assert not b.unlinked
    _age("UPDATE work_items SET waiting_since=?", time.time() - DAY - 60)
    [b] = [b for b in work_store.get(item.id).blockers if b.kind == "item_waiting"]
    assert b.unlinked


def test_a_workflow_wait_with_no_link_is_flagged_after_a_day(wired):
    wid = workflow_store.start("Diagnostics work", "jarvis", [])
    workflow_store.record(workflow_store.WorkflowEvent(wid, "wait_started", None, "Waiting.",
                                                       {"key": "ci", "until": "the CI run"}))
    assert not workflow_store.snapshot(wid).open[0].unlinked
    _age("UPDATE workflow_open SET opened_at=?", time.time() - DAY - 60)
    assert workflow_store.snapshot(wid).open[0].unlinked


# -- after his answer: the asker's follow-up -----------------------------------------

def test_an_answer_nobody_has_started_on_says_who_it_waits_for(wired, item):
    key = _key("takeover-agent")
    _report(wired, item.id, "decision_needed", "Build it?", auth=key, key="build")
    wired.client.post(f"/api/work/{item.id}/decisions/build", headers=BROWSER,
                      json={"answer": "Build it"})
    got = work_store.get(item.id)
    assert got.follow_up.asker == "takeover-agent" and not got.follow_up.overdue
    assert not any(b.kind == "no_follow_up" for b in got.blockers)
    _age("UPDATE work_items SET reported_at=?", time.time() - 40 * 60)
    _age("UPDATE work_decisions SET answered_at=?", time.time() - 31 * 60)
    got = work_store.get(item.id)
    [b] = [b for b in got.blockers if b.kind == "no_follow_up"]
    assert (b.key, b.text) == ("build", "takeover-agent")
    assert "no follow-up" in _call(wired, "work_status", item=item.id)["text"]
    # Any report from its agents is the follow-up.
    _report(wired, item.id, "update", "Building.", auth=key, state="working")
    assert work_store.get(item.id).follow_up is None


def test_no_follow_up_is_a_silent_attention_item_for_the_asker(wired, item):
    record = attention_store.AttentionStore()
    wired.server.attention_record = record
    try:
        key = _key("takeover-agent")
        _report(wired, item.id, "decision_needed", "Build it?", auth=key, key="build")
        wired.client.post(f"/api/work/{item.id}/decisions/build", headers=BROWSER,
                          json={"answer": "Build it"})
        _age("UPDATE work_items SET reported_at=?", time.time() - 40 * 60)
        _age("UPDATE work_decisions SET answered_at=?", time.time() - 31 * 60)
        wired.server._attend_unfollowed(work_store.listing())
        news = record.get("work_follow_up", item.id)
        assert news.event == "no_follow_up" and news.untrusted["agent"] == "takeover-agent"
        assert not news.audible
        _report(wired, item.id, "update", "Building.", auth=key, state="working")
        assert record.get("work_follow_up", item.id).state == attention_store.RESOLVED
    finally:
        wired.server.attention_record = None


def test_a_working_piece_is_a_follow_up(wired, item):
    key = _key()
    _report(wired, item.id, "piece", "Building.", auth=key, **{
        "key": "build", "who": {"kind": "tim"}, "state": "working"})
    _report(wired, item.id, "decision_needed", "Which colour?", auth=key, key="colour")
    wired.client.post(f"/api/work/{item.id}/decisions/colour", headers=BROWSER,
                      json={"answer": "Blue"})
    assert work_store.get(item.id).follow_up is None


def test_every_piece_in_and_no_decision_open_reads_ready_for_his_review(wired, item):
    key = _key()
    _report(wired, item.id, "update", "Going.", auth=key, state="working")
    for k in ("a", "b"):
        _report(wired, item.id, "piece", "In.", auth=key, **{
            "key": k, "who": {"kind": "tim"}, "state": "verifying"})
    got = work_store.get(item.id)
    assert (got.state, got.reported_state) == ("verifying", "working"), "not left as working"
    _report(wired, item.id, "decision_needed", "Close it?", auth=key, key="close")
    got = work_store.get(item.id)
    assert got.state == "waiting_on_tim", "the work is in, and it asks him something first"


# -- his answer, written for the asker --------------------------------------------

def test_his_answer_is_written_for_the_asker_and_listed_by_its_key(wired, item):
    from jarvis import data_paths
    key = _key("takeover-agent")
    _report(wired, item.id, "decision_needed", "Build it?", auth=key, key="build")
    before = time.time() - 1
    wired.client.post(f"/api/work/{item.id}/decisions/build", headers=BROWSER,
                      json={"answer": "Build it"})
    written = data_paths.answers_dir() / "speech" / f"{item.id}-build.md"
    text = written.read_text()
    assert "His answer: Build it" in text and "Decision: build" in text and item.id in text
    got = wired.client.get("/api/work/answers", params={"since": before}, auth=key)
    assert got.status_code == 200, got.text
    [a] = got.json()["answers"]
    assert (a["item_id"], a["key"], a["answer"]) == (item.id, "build", "Build it")
    other = wired.client.get("/api/work/answers", auth=_key("someone-else")).json()
    assert other["answers"] == [], "only the asker's own questions"
    assert wired.client.get("/api/work/answers").status_code == 403
    later = wired.client.get("/api/work/answers", params={"since": time.time() + 5}, auth=key)
    assert later.json()["answers"] == []
