"""The package inbox (jarvis/package_inbox.py, package_inbox_api.py and the
server's functions and tools): a package arrives through one route, is
checked and unpacked exactly as a local zip is, recorded before its hint,
and shown nowhere; its arrival is counted, never spoken; he opens it by his
click or by asking; his answers are recorded by the package's own
decide.py; they go back as the workflow's decisions and as a capture to the
sending session that goes only on his yes; and it closes once the session
answers, when he closes it, or when a newer one replaces it.

Each test has its own JARVIS_DATA_DIR. Nothing is really shown, sent to a
session, or run but the example deck's own decide.py."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from jarvis import (access_keys, attention_store, data_paths, decision_stamp, make_package,
                    package_inbox, run_store, work_packages, workflow_store, work_store)
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_work_packages import make_zip, marks, write_zip  # noqa: F401

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"
BROWSER = {"Origin": "http://localhost:5173"}
INBOX = "/api/packages/inbox"
PAGE = {"name": "notes-page", "version": "1.0.0", "purpose": "Show the notes.", "runs": "page",
        "entry": "index.html", "returns": ["results/**"]}


def token() -> dict:
    return {"Authorization": f"Bearer {data_paths.ensure_tool_token()}"}


def deck_zip(tmp_path: Path, version: str = "1.0.0") -> bytes:
    """The example decision deck, zipped as make_package zips it and stamped
    for this JARVIS, as make_package stamps it once its check has passed."""
    folder = tmp_path / f"deck-{version}"
    stamped = tmp_path / f"deck-{version}.zip"
    if stamped.is_file():
        return stamped.read_bytes()
    import shutil
    shutil.copytree(EXAMPLES / "decision-deck", folder)
    manifest = json.loads((folder / "package.json").read_text())
    manifest["version"] = version
    (folder / "package.json").write_text(json.dumps(manifest))
    raw, stamped = tmp_path / f"raw-{version}.zip", tmp_path / f"deck-{version}.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    decision_stamp.stamp(raw, stamped, data_paths.ensure_tool_token())
    return stamped.read_bytes()


def page_zip(version: str = "1.0.0") -> bytes:
    return make_zip({"index.html": "<p>notes</p>"}, {**PAGE, "version": version})


def send(w, data: bytes, headers=None, **query) -> "object":
    return w.client.post(INBOX, content=data, params=query,
                         headers={**(headers or token()), "Content-Type": "application/zip"})


@pytest.fixture
def shown(wired, monkeypatch):
    """Every frame a page would be sent, with one page open."""
    sent: list[dict] = []

    async def emit(message):
        sent.append(message)
    monkeypatch.setattr(wired.server, "_voice_emit", emit)
    monkeypatch.setattr(wired.server, "voice_clients", {object()})
    return sent


@pytest.fixture
def record(wired):
    """The attention record, running as the server runs it."""
    wired.server.attention_record = attention_store.AttentionStore()
    yield wired.server.attention_record
    wired.server.attention_record = None


# -- arrival ---------------------------------------------------------------------------

def test_a_bad_zip_is_refused_with_its_reason_and_nothing_is_recorded(wired, shown, marks):
    r = send(wired, b"not a zip at all")
    assert r.status_code == 409 and r.json()["error"]
    r = send(wired, make_zip({"README.md": "x"}, {"name": "x", "version": "1"}))
    assert r.status_code == 409 and "not a JARVIS work package" in r.json()["error"]
    assert package_inbox.listing() == [] and shown == [] and wired.hints == []


def test_an_unstamped_decision_package_is_refused(wired, shown, marks, tmp_path):
    folder = tmp_path / "deck"
    import shutil
    shutil.copytree(EXAMPLES / "decision-deck", folder)
    raw = tmp_path / "raw.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    r = send(wired, raw.read_bytes())
    assert r.status_code == 409 and package_inbox.listing() == []


def test_a_good_one_is_recorded_before_its_hint_and_shown_nowhere(
        wired, shown, marks, record, tmp_path, monkeypatch):
    seen_at_hint = []
    real = wired.hints.append
    monkeypatch.setattr(wired.server, "_send_to_session_clients",
                        lambda m: (seen_at_hint.append(len(package_inbox.listing())), real(m)))
    data = deck_zip(tmp_path)
    r = send(wired, data, project="tea", session="sess-1")
    assert r.status_code == 200, r.text
    got = r.json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert got["arrived"] is True and entry.state == package_inbox.ARRIVED
    assert entry.sha256 == hashlib.sha256(data).hexdigest()
    assert (entry.title, entry.from_project, entry.from_session) == (
        "Office tea: decisions", "tea", "sess-1")
    assert (entry.decisions_answered, entry.decisions_total) == (0, 2), \
        "the third decision applies only after one answer to the second"
    assert shown == [], "nothing is shown on arrival"
    assert {"type": "packages"} in wired.hints and all(n == 1 for n in seen_at_hint)
    item = record.get("package", entry.id)
    assert item.state == attention_store.PENDING and not item.audible
    assert item.untrusted == {"agent": "tea", "package": "Office tea: decisions"}
    assert item.facts["waiting"] == 2
    assert package_inbox.events(entry.id)[0]["kind"] == "arrived"


def test_the_same_bytes_again_are_the_same_row(wired, shown, marks, tmp_path):
    data = deck_zip(tmp_path)
    first = send(wired, data, session="s").json()
    again = send(wired, data, session="s").json()
    assert again["arrived"] is False and again["inbox"]["id"] == first["inbox"]["id"]
    assert len(package_inbox.listing()) == 1


def test_a_newer_version_from_the_same_sender_replaces_the_open_one(
        wired, shown, marks, record, tmp_path):
    old = send(wired, deck_zip(tmp_path, "1.0.0"), session="s").json()["inbox"]["id"]
    other = send(wired, deck_zip(tmp_path, "1.0.5"), session="t").json()["inbox"]["id"]
    new = send(wired, deck_zip(tmp_path, "1.0.1"), session="s").json()
    assert new["superseded"] == [old]
    assert package_inbox.get(old).closed_why == package_inbox.SUPERSEDED
    assert package_inbox.get(other).is_open, "another sender's is its own"
    assert record.get("package", old).state == attention_store.RESOLVED
    assert [e.id for e in package_inbox.listing()] == [new["inbox"]["id"], other]


def test_a_package_for_a_missing_workflow_or_work_item_is_refused(wired, shown, marks):
    assert "no workflow" in send(wired, page_zip(), workflow="wf-gone").json()["error"]
    assert "no work item" in send(wired, page_zip(), work="work-gone").json()["error"]
    assert package_inbox.listing() == []


def test_the_packages_access_key_opens_the_inbox_route_and_nothing_else(wired, shown, marks):
    key = access_keys.create("sender", ["packages"])
    auth = {"Authorization": f"Bearer {key}"}
    r = send(wired, page_zip(), headers=auth)
    assert r.status_code == 200, r.text
    inbox_id, package_id = r.json()["inbox"]["id"], r.json()["package"]["id"]
    for path in (f"{INBOX}/{inbox_id}/open", f"{INBOX}/{inbox_id}/close",
                 f"/api/packages/{package_id}/page", "/api/packages/local"):
        assert wired.client.post(path, json={}, headers=auth).status_code == 403, path
    assert shown == []


# -- the backlog -------------------------------------------------------------------------

def test_the_backlog_route_and_the_brain_read_the_same_rows(wired, shown, marks, tmp_path):
    send(wired, page_zip(), project="notes")
    send(wired, deck_zip(tmp_path), project="tea")
    listed = wired.client.get(INBOX).json()
    assert [p["title"] for p in listed["packages"]] == ["Office tea: decisions", "notes page"]
    assert listed["closed_count"] == 0 and listed["packages"][0]["waiting"] == 2
    said = _call(wired, "package_inbox")["text"]
    assert said.startswith("Two packages in his inbox")
    assert "Office tea: decisions, from tea, 0 of 2 decisions answered" in said
    assert 'untrusted="true"' in said
    one = _call(wired, "package_inbox", package="tea")["text"]
    assert "decision kettle-kind:" in one and "decision tea-kinds:" in one


def test_an_arrival_is_never_a_digest_candidate(record):
    record.record("package", "pkg-1", "arrived", audible=True)
    record.set_audible("package", "pkg-1", True)
    assert record.candidates(0) == [] and record.pending_count() == 1


# -- open --------------------------------------------------------------------------------

def test_open_is_his_click_or_his_asking(wired, shown, marks, record, tmp_path):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    assert wired.client.post(f"{INBOX}/{inbox_id}/open", headers=token()).status_code == 403
    assert shown == []
    r = wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    assert r.status_code == 200, r.text
    assert shown[-1]["type"] == "package_page"
    assert package_inbox.get(inbox_id).state == package_inbox.OPENED
    assert record.get("package", inbox_id).state == attention_store.RESOLVED
    said = _call(wired, "open_inbox_package", package="the tea one")["text"]
    assert said.startswith(f"{inbox_id} is open in the centre of his screen")
    assert "decision kettle-kind:" in said and len(shown) == 2


def test_open_by_a_stranger_s_turn_is_refused(wired, shown, marks, tmp_path):
    send(wired, deck_zip(tmp_path))
    refused = _call(wired, "open_inbox_package", origin="event", package="tea")
    assert not refused["ok"] and shown == []


# -- answering ---------------------------------------------------------------------------

def opened_deck(w, tmp_path, **query) -> str:
    inbox_id = send(w, deck_zip(tmp_path), **query).json()["inbox"]["id"]
    assert w.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER).status_code == 200
    return inbox_id


def test_his_answers_go_through_the_package_s_own_decide_py(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path)
    said = _call(wired, "answer_package_decision", package=inbox_id, decision="kettle-kind",
                 option="recommended", words="the fast one")["text"]
    assert said == "Recorded. 1 of 2 answered."
    entry = package_inbox.get(inbox_id)
    assert entry.state == package_inbox.ANSWERING
    package = work_packages.find(entry.package_id)
    record = json.loads((package.folder / "results" / "decisions.json").read_text())
    assert record["kettle-kind"]["note"] == "the fast one"
    r = wired.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                          json={"decision": "tea-kinds", "option": "b"})
    assert r.status_code == 200, r.text
    entry = package_inbox.get(inbox_id)
    assert (entry.decisions_answered, entry.decisions_total) in ((2, 2), (2, 3))
    bad = wired.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                            json={"decision": "kettle-kind", "option": "rubbish"})
    assert bad.status_code == 409 and "Nothing was recorded" in bad.json()["error"]


def test_the_frame_s_save_is_counted_too(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path)
    package_id = package_inbox.get(inbox_id).package_id
    record = {"kettle-kind": {"answer": "later", "at": "now"}}
    r = wired.client.post(f"/api/packages/{package_id}/results?name=decisions.json",
                          content=json.dumps(record).encode(), headers=BROWSER)
    assert r.status_code == 200, r.text
    assert package_inbox.get(inbox_id).decisions_answered == 1


def answer_all(w, inbox_id: str) -> None:
    for _ in range(4):
        entry = package_inbox.get(inbox_id)
        if entry.state == package_inbox.ANSWERED:
            return
        package = work_packages.find(entry.package_id)
        q = next(q for q in package_inbox.questions(package) if q.applies and not q.answer)
        _call(w, "answer_package_decision", package=inbox_id, decision=q.id,
              option="recommended")
    assert package_inbox.get(inbox_id).state == package_inbox.ANSWERED


# -- moving it forward ---------------------------------------------------------------------

def test_answers_go_back_only_once_every_one_is_answered(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path)
    said = _call(wired, "return_package_answers", package=inbox_id)["text"]
    assert "still wait on him" in said and said.endswith("Nothing was sent.")


def test_answers_go_back_to_the_workflow_and_as_a_capture_for_the_session(
        wired, shown, marks, tmp_path, monkeypatch):
    wid = workflow_store.start("Tea service", "tea", [])
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {"sess-1": "chitauri"})
    inbox_id = opened_deck(wired, tmp_path, session="sess-1", workflow=wid)
    assert workflow_store.snapshot(wid).open_waits == [inbox_id]
    answer_all(wired, inbox_id)
    said = _call(wired, "return_package_answers", package=inbox_id)["text"]
    assert "holds the answers for chitauri" in said and "read_capture" in said
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert capture["text"].startswith("Tim's answers to your package decision-deck-example")
    assert "kettle-kind:" in capture["text"] and "decisions.json" in capture["text"]
    assert not capture.get("approved"), "it goes only on his yes"
    snap = workflow_store.snapshot(wid)
    assert not (snap.open_waits or snap.open_decisions), "nothing is left open on the workflow"
    answered = [e for e in workflow_store.events(wid) if e.kind == "decision_made"]
    assert {e.detail["key"] for e in answered} >= {"kettle-kind", "tea-kinds"}
    entry = package_inbox.get(inbox_id)
    assert entry.state == package_inbox.ANSWERED and entry.capture_id == capture["id"]

    # The message goes (on his yes) and the session answers it.
    message = run_store.create_message("sess-1", "anthropic", "chitauri", "tea",
                                       capture["text"], capture_id=capture["id"],
                                       status=run_store.MessageStatus.SENT)
    wired.server._follow_inbox_messages()
    entry = package_inbox.get(inbox_id)
    assert entry.state == package_inbox.RETURNED and entry.message_id == message
    run_store.update_message(message, status=run_store.MessageStatus.ANSWERED,
                             answered_at=time.time(), reply="Thanks")
    wired.server._follow_inbox_messages()
    entry = package_inbox.get(inbox_id)
    assert (entry.state, entry.closed_why) == (package_inbox.CLOSED, package_inbox.DONE)


def test_a_message_before_he_asked_is_not_taken_for_the_answers(wired, shown, marks, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {"sess-1": "chitauri"})
    inbox_id = opened_deck(wired, tmp_path, session="sess-1")
    answer_all(wired, inbox_id)
    early = run_store.create_message("sess-1", "anthropic", "chitauri", "tea", "older",
                                     capture_id=1, status=run_store.MessageStatus.SENT)
    with __import__("sqlite3").connect(data_paths.db_path()) as conn:
        conn.execute("UPDATE messages SET staged_at=? WHERE id=?", (time.time() - 3600, early))
    _call(wired, "return_package_answers", package=inbox_id)
    wired.server._follow_inbox_messages()
    assert package_inbox.get(inbox_id).message_id is None


def test_a_sender_that_is_gone_gets_no_message_and_it_is_returned(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path, session="gone")
    answer_all(wired, inbox_id)
    r = wired.client.post(f"{INBOX}/{inbox_id}/return", headers=BROWSER)
    assert r.status_code == 200 and "not running" in r.json()["said"]
    assert package_inbox.get(inbox_id).state == package_inbox.RETURNED


def test_a_page_with_no_questions_goes_back_once_he_has_finished(wired, shown, marks):
    inbox_id = send(wired, page_zip(), session="gone").json()["inbox"]["id"]
    assert "not been opened" in _call(wired, "return_package_answers", package=inbox_id)["text"]
    wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    _call(wired, "return_package_answers", package=inbox_id)
    assert package_inbox.get(inbox_id).state == package_inbox.RETURNED


def test_close_dismisses_an_open_one_and_ends_its_workflow_s_wait(
        wired, shown, marks, record, tmp_path):
    wid = workflow_store.start("Tea service", "tea", [])
    inbox_id = send(wired, deck_zip(tmp_path), workflow=wid).json()["inbox"]["id"]
    r = wired.client.post(f"{INBOX}/{inbox_id}/close", headers=BROWSER)
    assert r.status_code == 200
    entry = package_inbox.get(inbox_id)
    assert (entry.state, entry.closed_why) == (package_inbox.CLOSED, package_inbox.DISMISSED)
    assert not workflow_store.snapshot(wid).open_waits
    assert record.get("package", inbox_id).state == attention_store.RESOLVED
    listed = wired.client.get(INBOX, params={"closed": 1}).json()
    assert [p["id"] for p in listed["packages"]] == [inbox_id]
    assert "No package in the inbox fits" in _call(wired, "close_inbox_package",
                                                   package=inbox_id)["text"]


# -- the work item --------------------------------------------------------------------

def test_a_package_for_a_work_item_shows_its_questions_there(wired, shown, marks, tmp_path,
                                                             monkeypatch):
    folder = tmp_path / "work"
    folder.mkdir()
    item = work_store.create("Tea service", "folder", str(folder))
    inbox_id = send(wired, deck_zip(tmp_path), work=item.id).json()["inbox"]["id"]
    assert {"type": "work"} in wired.hints
    got = wired.client.get(f"/api/work/{item.id}").json()
    assert got["item"]["package_questions"] == 2
    [package] = got["packages"]
    assert package["inbox"]["id"] == inbox_id
    assert [q["id"] for q in package["questions"]][:2] == ["kettle-kind", "tea-kinds"]
    assert wired.client.get("/api/work").json()["items"][0]["package_questions"] == 2
    said = _call(wired, "work_status", item=item.id)["text"]
    assert "2 package questions waiting" not in said  # one item in full lists them instead
    assert f"package {inbox_id}" in said and "decision kettle-kind:" in said


# -- parity --------------------------------------------------------------------------------

READS = ("package_inbox",)
ACTS = ("open_inbox_package", "answer_package_decision", "return_package_answers",
        "close_inbox_package")


def test_every_inbox_tool_is_defined_granted_and_gated(wired):
    from jarvis import claude_provider, jarvis_mcp, tool_activity
    server = wired.server
    specs = {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    for name in READS + ACTS:
        assert name in specs and name in server.TOOL_HANDLERS
        assert f"mcp__jarvis__{name}" in claude_provider.ALLOWED_TOOLS
        assert name in tool_activity.CATEGORIES
    assert set(ACTS) <= server.ACTING_TOOLS and not set(READS) & server.ACTING_TOOLS
    assert set(READS) <= set(server.TAINTING_TOOLS)


@pytest.mark.parametrize("tool", ACTS)
def test_every_inbox_act_acts_only_on_his_own_turn(wired, tool):
    refused = _call(wired, tool, origin="event", package="x", option="a")
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
