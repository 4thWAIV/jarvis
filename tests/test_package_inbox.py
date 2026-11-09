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


@pytest.fixture(autouse=True)
def claims_proven(request, monkeypatch):
    """Most tests here are about the inbox, not the proof: each session a
    package claims counts as proven, by its listed name. The proof's own
    tests (below, "who sent it") take the real `_package_sender` back."""
    if "wired" not in request.fixturenames:
        yield None
        return
    w = request.getfixturevalue("wired")
    real = w.server._package_sender
    from jarvis import session_message_sender

    def proven(claimed, source, reporter):
        if not claimed:
            return None
        return session_message_sender.Sender(
            claimed, w.server.agent_directory.names_by_session().get(claimed, ""), "test")
    monkeypatch.setattr(w.server, "_package_sender", proven)
    yield real


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


# -- signing off -------------------------------------------------------------------------

def signoff(w, package_id: str, headers=BROWSER):
    return w.client.post(f"/api/packages/{package_id}/signoff", headers=headers)


@pytest.fixture
def chitauri(wired, monkeypatch):
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {"sess-1": "chitauri"})


def test_sign_off_sends_what_he_answered_and_notes_what_he_did_not(
        wired, shown, marks, record, tmp_path, chitauri):
    wid = workflow_store.start("Tea service", "tea", [])
    inbox_id = opened_deck(wired, tmp_path, session="sess-1", workflow=wid)
    assert workflow_store.snapshot(wid).open_waits == [inbox_id]
    _call(wired, "answer_package_decision", package=inbox_id, decision="kettle-kind",
          option="recommended", words="the fast one")
    shown.clear()
    said = _call(wired, "sign_off_package", package="the tea one")["text"]
    assert said.startswith("Signed off. Capture") and "chitauri" in said and "read_capture" in said
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    text = capture["text"]
    assert text.startswith("Tim has signed off your package decision-deck-example")
    assert "kettle-kind: electric (his words: the fast one)" in text
    assert "tea-kinds: not answered" in text and "decisions.json" in text
    assert "Office tea" not in text and "An electric kettle" not in text, \
        "read back aloud: ids and his words only, never the sender's titles or option names"
    assert not capture.get("approved"), "it goes only on his yes"
    entry = package_inbox.get(inbox_id)
    assert (entry.state, entry.closed_why) == (package_inbox.CLOSED, package_inbox.SIGNED_OFF)
    assert entry.signed_off_at and entry.capture_id == capture["id"]
    assert record.get("package", inbox_id).state == attention_store.RESOLVED
    assert {"type": "package_closed", "package": entry.package_id} in shown
    snap = workflow_store.snapshot(wid)
    assert not (snap.open_waits or snap.open_decisions), "nothing is left open on the workflow"
    made = {e.detail["key"]: e.detail["answer"] for e in workflow_store.events(wid)
            if e.kind == "decision_made"}
    assert set(made) == {"kettle-kind"}, "only what he answered"

    # The message goes (on his yes): the record follows it.
    message = run_store.create_message("sess-1", "anthropic", "chitauri", "tea", text,
                                       capture_id=capture["id"],
                                       status=run_store.MessageStatus.SENT)
    wired.server._follow_inbox_messages()
    assert package_inbox.get(inbox_id).message_id == message


def test_a_message_before_he_signed_off_is_not_taken_for_his(wired, shown, marks, tmp_path,
                                                             chitauri):
    inbox_id = opened_deck(wired, tmp_path, session="sess-1")
    early = run_store.create_message("sess-1", "anthropic", "chitauri", "tea", "older",
                                     capture_id=1, status=run_store.MessageStatus.SENT)
    with __import__("sqlite3").connect(data_paths.db_path()) as conn:
        conn.execute("UPDATE messages SET staged_at=? WHERE id=?", (time.time() - 3600, early))
    _call(wired, "sign_off_package", package=inbox_id)
    wired.server._follow_inbox_messages()
    assert package_inbox.get(inbox_id).message_id is None


def test_a_sender_that_is_gone_gets_no_message(wired, shown, marks, tmp_path):
    inbox_id = opened_deck(wired, tmp_path, session="gone")
    r = signoff(wired, package_inbox.get(inbox_id).package_id)
    assert r.status_code == 200 and "not running" in r.json()["said"]
    assert package_inbox.get(inbox_id).closed_why == package_inbox.SIGNED_OFF


def test_a_presentation_tells_its_sender_it_was_reviewed(wired, shown, marks, chitauri):
    got = send(wired, page_zip(), session="sess-1").json()
    signoff(wired, got["package"]["id"])
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert capture["text"].startswith("Tim has reviewed your package notes-page")
    assert "signed it off" in capture["text"]


def test_sign_off_needs_his_click_or_his_turn(wired, shown, marks):
    package_id = send(wired, page_zip()).json()["package"]["id"]
    assert signoff(wired, package_id, headers={}).status_code == 403
    refused = _call(wired, "sign_off_package", origin="event", package=package_id)
    assert not refused["ok"] and package_inbox.listing()[0].is_open


def test_a_package_from_outside_the_inbox_is_signed_off_the_same_way(
        wired, shown, marks, tmp_path):
    archive = write_zip(tmp_path, page_zip(), "old.zip")
    package, _ = work_packages.unpack(archive, "ab12cd34")
    assert package_inbox.listing() == []
    r = signoff(wired, package.id)
    assert r.status_code == 200, r.text
    [entry] = package_inbox.listing(closed=True)
    assert (entry.package_id, entry.closed_why) == (package.id, package_inbox.SIGNED_OFF)
    assert entry.opened_at and entry.from_session == ""
    assert "no sender is on record" in r.json()["said"]
    assert {"type": "package_closed", "package": package.id} in shown


def test_a_work_decision_naming_the_package_gets_his_answer_or_is_closed_without_one(
        wired, shown, marks, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    item = work_store.create("Tea service", "folder", str(folder))
    inbox_id = opened_deck(wired, tmp_path, work=item.id)
    package_id = package_inbox.get(inbox_id).package_id
    for key in ("kettle-kind", "tea-kinds"):
        work_store.record(work_store.WorkEvent(item.id, "decision_needed", f"Decide {key}",
                                               {"key": key, "package": "decision-deck-example"}))
    _call(wired, "answer_package_decision", package=inbox_id, decision="kettle-kind",
          option="tap")
    signoff(wired, package_id)
    item = work_store.get(item.id)
    assert not item.decisions
    closed = {a.key: (a.where, a.answer) for a in item.answered}
    assert closed["kettle-kind"] == ("page", "A hot water tap"), "his answer, as he gave it"
    assert closed["tea-kinds"] == (work_store.SIGNED_OFF, ""), "closed, never answered for him"
    assert item.latest_update == f"He signed off the package {package_id}."
    kinds = [e.kind for e in work_store.events(item.id)]
    assert "decision_signed_off" in kinds
    said = _call(wired, "work_status", item=item.id)["text"]
    assert "without answering it; it has no answer of his" in said
    r = wired.client.post(f"/api/work/{item.id}/events", headers=token(),
                          json={"kind": "decision_signed_off", "summary": "x",
                                "detail": {"key": "kettle-kind"}})
    assert r.status_code == 409, "never a reporter's to send"


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
ACTS = ("open_inbox_package", "answer_package_decision", "sign_off_package",
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


# -- who sent it -----------------------------------------------------------------------

@pytest.fixture
def proof(wired, claims_proven, monkeypatch, tmp_path):
    """The real proof, with chitauri (sess-1) and designer (sess-2) running
    in /tmp/tea and their transcripts under tmp/claude."""
    from types import SimpleNamespace
    monkeypatch.setattr(wired.server, "_package_sender", claims_proven)
    sessions = [SimpleNamespace(session_id=sid, cwd=str(tmp_path / "tea"), project="tea",
                                provider="anthropic", voice_name=name, state="idle",
                                agents_active=0, agents_capped=False)
                for sid, name in (("sess-1", "chitauri"), ("sess-2", "designer"))]
    monkeypatch.setattr(wired.server, "_snapshot_or_empty",
                        lambda: SimpleNamespace(sessions=sessions))
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {"sess-1": "chitauri", "sess-2": "designer"})
    monkeypatch.setattr(wired.server.session_watch, "config_roots", lambda: [tmp_path / "claude"])

    def ran(session, command):
        from jarvis import session_watch
        t = session_watch.transcript_path(tmp_path / "claude", str(tmp_path / "tea"), session)
        t.parent.mkdir(parents=True, exist_ok=True)
        with open(t, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}) + "\n")
    return ran


def test_a_claimed_session_nothing_proves_gets_no_answers_and_the_row_says_so(
        wired, shown, marks, proof):
    got = send(wired, page_zip(), session="sess-1", source="notes/").json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.claimed_session, entry.sender_evidence) == ("", "sess-1", "")
    assert got["inbox"]["proof_pending"] is True, "looked for again: its output may prove it"
    later = time.time() + wired.server.REPROVE_FOR_SEC + 1
    [(entry, sender)] = wired.server._reprove_inbox_senders(now=later)
    assert sender is None and entry.unproven_claim
    assert "go nowhere" in _call(wired, "package_inbox")["text"]
    r = signoff(wired, got["package"]["id"])
    assert "not proven to have sent it" in r.json()["said"]
    assert not any(c.get("to_session") == "sess-1" for c in wired.server._captures.values())


@pytest.mark.parametrize("command, source", [
    ("python -m jarvis.make_package notes/ --session sess-1", "notes"),
    ("python -m jarvis.make_package --handoff handoff.json --engine codex --session sess-1", "handoff.json"),
    ("python -m jarvis.make_package --handoff handoff.json --engine claude --session sess-1", "handoff.json"),
])
def test_the_claimed_session_s_transcript_running_make_package_proves_it(
        wired, shown, marks, proof, command, source):
    proof("sess-1", command)
    got = send(wired, page_zip(), session="sess-1", source=source).json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.from_agent, entry.sender_evidence) == (
        "sess-1", "chitauri", "transcript")
    assert got["inbox"]["unproven_claim"] is False


def test_another_session_s_transcript_proves_no_claim(wired, shown, marks, proof):
    """designer ran it: a package claiming chitauri is not chitauri's."""
    proof("sess-2", "python -m jarvis.make_package notes/ --session sess-1")
    got = send(wired, page_zip(), session="sess-1", source="notes").json()
    assert package_inbox.get(got["inbox"]["id"]).from_session == ""


def test_a_key_proves_the_sessions_of_the_agent_its_label_names(wired, shown, marks, proof):
    run_store.sync_agents({"sess-1": ("chitauri", "idle")})
    auth = {"Authorization": f"Bearer {access_keys.create('chitauri', ['packages'])}"}
    got = send(wired, page_zip(), headers=auth, session="sess-1").json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.sender_evidence) == ("sess-1", "access_key")
    other = send(wired, page_zip("1.0.1"), headers=auth, session="sess-2").json()
    assert package_inbox.get(other["inbox"]["id"]).from_session == "", "not its agent's session"


# -- the packages delivered before their sender had to be proven ------------------------

def _printed(tmp_path, session, text):
    """`session`'s transcript holds a tool result printing `text`, as
    make_package's output lands in the session that ran it."""
    from jarvis import session_watch
    t = session_watch.transcript_path(tmp_path / "claude", str(tmp_path / "tea"), session)
    t.parent.mkdir(parents=True, exist_ok=True)
    with open(t, "a") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}) + "\n")


# Days ago: a row older than every window.
OLD = time.time() - 3 * 24 * 3600


def _older(wired, version="1.0.0", session="sess-1"):
    """A row as migration 3 left one delivered before it: its session only
    a claim, and (migration 4) to be proven once again."""
    got = wired.send_unproven(version, session)
    with __import__("sqlite3").connect(data_paths.db_path()) as conn:
        conn.execute("UPDATE package_inbox SET reprove=1, arrived_at=? WHERE id=?", (OLD, got))
    return got


@pytest.fixture
def older(wired, shown, marks, proof):
    def send_unproven(version, session):
        return send(wired, page_zip(version), session=session).json()["inbox"]["id"]
    wired.send_unproven = send_unproven
    return wired


def test_an_older_row_is_proven_by_its_session_s_own_record_of_delivering_it(older, tmp_path):
    inbox_id = _older(older)
    _printed(tmp_path, "sess-1", "Built a page package.\n" + make_package.ARRIVED_AS.format(
        inbox_id=inbox_id) + " (package notes-page-1.0.0-x).")
    import os
    os.utime(next((tmp_path / "claude").rglob("sess-1.jsonl")), (OLD + 60, OLD + 60))
    [(entry, sender)] = older.server._reprove_inbox_senders()
    assert (entry.from_session, entry.from_agent, entry.sender_evidence, entry.reprove) == (
        "sess-1", "chitauri", "transcript", 0)
    assert package_inbox.events(inbox_id)[-1]["kind"] == package_inbox.SENDER_PROVEN
    assert older.server._reprove_inbox_senders() == [], "once"
    assert package_inbox.get(inbox_id).revision == entry.revision


def test_an_older_row_nothing_proves_stays_unproven_and_says_why(older, tmp_path):
    inbox_id = _older(older)
    _printed(tmp_path, "sess-2", make_package.ARRIVED_AS.format(inbox_id=inbox_id))
    [(entry, sender)] = older.server._reprove_inbox_senders()
    assert sender is None and entry.from_session == "" and entry.reprove == 0
    last = package_inbox.events(inbox_id)[-1]
    assert (last["kind"], last["how"]) == (package_inbox.SENDER_NOT_PROVEN,
                                           "no transcript of its session shows it delivered")
    assert older.server._reprove_inbox_senders() == []


def test_an_older_row_from_a_session_no_longer_listed_says_so(older, tmp_path):
    inbox_id = _older(older, session="sess-gone")
    [(entry, sender)] = older.server._reprove_inbox_senders()
    assert sender is None and package_inbox.events(inbox_id)[-1]["how"] == \
        "its session is not a listed agent running now"


def test_a_new_arrival_is_proven_once_its_make_package_output_says_its_inbox_id(
        older, tmp_path):
    """The command line may name its source through a shell variable ($D):
    unproven as it arrives, it is proven by make_package's own output, which
    gives the inbox id JARVIS answered it alone."""
    got = send(older, page_zip(), session="sess-1", source="/tmp/x/content.json").json()
    inbox_id = got["inbox"]["id"]
    assert got["inbox"]["proof_pending"] and not got["inbox"]["unproven_claim"]
    assert older.server._reprove_inbox_senders() == [], "its output is not written yet"
    _printed(tmp_path, "sess-1", make_package.ARRIVED_AS.format(inbox_id=inbox_id))
    [(entry, _)] = older.server._reprove_inbox_senders()
    assert (entry.from_session, entry.sender_evidence, entry.proof_pending) == (
        "sess-1", "transcript", False)


def _claude_subagent(tmp_path, session, agent_id, description, line):
    from jarvis import session_watch
    d = (tmp_path / "claude" / "projects" / session_watch.encode_cwd(str(tmp_path / "tea"))
         / session / "subagents")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n")
    (d / f"agent-{agent_id}.meta.json").write_text(json.dumps({"description": description}))


def test_a_claude_subagent_of_the_claimed_session_proves_it_as_it_arrives(
        wired, shown, marks, proof, tmp_path):
    _claude_subagent(tmp_path, "sess-1", "a1b2c3", "Feynman", {
        "type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "python -m jarvis.make_package notes/ --session sess-1"}}]}})
    got = send(wired, page_zip(), session="sess-1", source="notes").json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.from_agent, entry.sender_evidence) == (
        "sess-1", "Feynman (chitauri)", "subagent:Feynman")


def test_a_claude_subagent_s_output_proves_it_after(wired, shown, marks, proof, tmp_path):
    got = send(wired, page_zip(), session="sess-1", source="$D/content.json").json()
    _claude_subagent(tmp_path, "sess-1", "d4e5f6", "Review packages", {
        "type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
            "content": make_package.ARRIVED_AS.format(inbox_id=got["inbox"]["id"])}]}})
    [(entry, _)] = wired.server._reprove_inbox_senders()
    assert (entry.from_agent, entry.sender_evidence) == (
        "Review packages (chitauri)", "subagent:Review packages")


def test_a_codex_subagent_of_the_claimed_thread_proves_it(wired, shown, marks, proof, tmp_path,
                                                         monkeypatch):
    from types import SimpleNamespace
    parent = "019a-codex-parent"
    sessions = wired.server._snapshot_or_empty().sessions + [SimpleNamespace(
        session_id=parent, cwd=str(tmp_path / "tea"), project="tea", provider="openai",
        voice_name="Planning", state="idle", agents_active=0, agents_capped=False)]
    monkeypatch.setattr(wired.server, "_snapshot_or_empty", lambda: SimpleNamespace(sessions=sessions))
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {"sess-1": "chitauri", parent: "Planning"})
    own, sub = tmp_path / "codex" / "own.jsonl", tmp_path / "codex" / "sub.jsonl"
    own.parent.mkdir(parents=True)
    own.write_text(json.dumps({"type": "session_meta", "payload": {"source": "cli"}}) + "\n")
    sub.write_text(json.dumps({"type": "session_meta", "payload": {"source": {"subagent": {
        "thread_spawn": {"parent_thread_id": parent, "agent_nickname": "Hopper"}}}}}) + "\n"
        + json.dumps({"type": "response_item", "payload": {"type": "function_call", "arguments":
            json.dumps({"cmd": f"python -m jarvis.make_package notes/ --session {parent}"})}})
        + "\n")
    monkeypatch.setattr(wired.server.codex_sessions, "threads", lambda home: {
        parent: {"rollout_path": str(own), "thread_source": "cli"},
        "019b-child": {"rollout_path": str(sub), "thread_source": "subagent"}})
    got = send(wired, page_zip(), session=parent, source="notes").json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.from_agent, entry.sender_evidence) == (
        parent, "Hopper (Planning)", "subagent:Hopper")


def test_migration_4_marks_only_the_open_claimed_unproven_rows(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.row_factory = sqlite3.Row
    run_store.migrate(conn, "package_inbox_schema",
                      {k: v for k, v in package_inbox._MIGRATIONS.items() if k <= 3}, "inbox")
    for n, (claimed, proven, state) in enumerate((("s", "", "arrived"), ("s", "s", "arrived"),
                                                  ("", "", "arrived"), ("s", "", "closed"))):
        conn.execute("INSERT INTO package_inbox (id, package_id, sha256, name, version, title, "
                     "purpose, runs, state, revision, arrived_at, claimed_session, from_session, "
                     "sender_evidence) VALUES (?, 'p', 'x', 'n', '1', 't', 'p', 'page', ?, 1, 0, "
                     "?, ?, ?)", (f"r{n}", state, claimed, proven, "transcript" if proven else ""))
    run_store.migrate(conn, "package_inbox_schema", package_inbox._MIGRATIONS, "inbox")
    marked = [r["id"] for r in conn.execute("SELECT id FROM package_inbox WHERE reprove=1")]
    assert marked == ["r0"]
