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

from jarvis import (attention_store, data_paths, make_package, package_signature,
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


def deck_zip(tmp_path: Path, version: str = "1.0.0", session: str = "sess-tea") -> bytes:
    """The example decision deck, zipped as make_package zips it and signed
    by `session`'s onboarded agent with the statement that it passed its
    check, as make_package signs it once its check has passed."""
    from tests.agent_key_fixtures import onboarded_auth
    folder = tmp_path / f"deck-{version}-{session}"
    signed = tmp_path / f"deck-{version}-{session}.zip"
    if signed.is_file():
        return signed.read_bytes()
    import shutil
    shutil.copytree(EXAMPLES / "decision-deck", folder)
    manifest = json.loads((folder / "package.json").read_text())
    manifest["version"] = version
    (folder / "package.json").write_text(json.dumps(manifest))
    raw = tmp_path / f"raw-{version}-{session}.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    agent = onboarded_auth(session=session).agent
    package_signature.sign(raw, signed, agent.private, agent.public, session, (package_signature.DECISION_CHECKED,))
    return signed.read_bytes()


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

    def proven(claimed, reporter):
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


def test_an_unchecked_decision_package_is_refused(wired, shown, marks, tmp_path):
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
    data = deck_zip(tmp_path, session="sess-1")
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
    assert item.untrusted == {"agent": entry.from_agent, "package": "Office tea: decisions"}
    assert entry.from_agent == run_store.agent_names()["sess-1"], "its signer's one name"
    assert item.facts["waiting"] == 2
    assert package_inbox.events(entry.id)[0]["kind"] == "arrived"


def test_the_same_bytes_again_are_the_same_row(wired, shown, marks, tmp_path):
    data = deck_zip(tmp_path, session="s")
    first = send(wired, data, session="s").json()
    again = send(wired, data, session="s").json()
    assert again["arrived"] is False and again["inbox"]["id"] == first["inbox"]["id"]
    assert len(package_inbox.listing()) == 1


def test_a_newer_version_from_the_same_sender_replaces_the_open_one(
        wired, shown, marks, record, tmp_path):
    old = send(wired, deck_zip(tmp_path, "1.0.0", "s"), session="s").json()["inbox"]["id"]
    other = send(wired, deck_zip(tmp_path, "1.0.5", "t"), session="t").json()["inbox"]["id"]
    new = send(wired, deck_zip(tmp_path, "1.0.1", "s"), session="s").json()
    assert new["superseded"] == [old]
    assert package_inbox.get(old).closed_why == package_inbox.SUPERSEDED
    assert package_inbox.get(other).is_open, "another sender's is its own"
    assert record.get("package", old).state == attention_store.RESOLVED
    assert [e.id for e in package_inbox.listing()] == [new["inbox"]["id"], other]


def test_a_package_for_a_missing_workflow_or_work_item_is_refused(wired, shown, marks):
    assert "no workflow" in send(wired, page_zip(), workflow="wf-gone").json()["error"]
    assert "no work item" in send(wired, page_zip(), work="work-gone").json()["error"]
    assert package_inbox.listing() == []


def test_the_packages_right_opens_the_inbox_route_and_nothing_else(wired, shown, marks):
    from tests.agent_key_fixtures import onboarded_auth
    auth = onboarded_auth(("packages",))
    r = wired.client.post(INBOX, content=page_zip(), auth=auth,
                          headers={"Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    inbox_id, package_id = r.json()["inbox"]["id"], r.json()["package"]["id"]
    for path in (f"{INBOX}/{inbox_id}/open", f"{INBOX}/{inbox_id}/close",
                 f"/api/packages/{package_id}/page", "/api/packages/local"):
        assert wired.client.post(path, json={}, auth=auth).status_code == 403, path
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


# -- every package carries his vote -------------------------------------------------------

STORY_HELPER = '<script src="/api/packages/story.js"></script>'


def story_zip(story: dict, name: str = "notes-page") -> bytes:
    return make_zip({"index.html": "<p>notes</p>" + STORY_HELPER,
                     "story.json": json.dumps(story)},
                    {**PAGE, "name": name, "story": "story.json"})


TOLD = {"title": "Notes", "slides": [{"title": "One", "beats": [{"say": "One."}]},
                                     {"title": "Two", "beats": [{"say": "Two."}]}]}
ASKS = {**TOLD, "answers": "results/decisions.json",
        "slides": [TOLD["slides"][0], {**TOLD["slides"][1], "waits": "go-ahead"}]}
# A hand-made page as make_package sends it: its story ends with the vote.
VOTES = package_inbox.with_understood_slide(TOLD, "en")


@pytest.fixture
def work_item(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return work_store.create("Notes", "folder", str(folder))


@pytest.mark.parametrize("data", [
    pytest.param(lambda tmp: page_zip(), id="a page with no story"),
    pytest.param(lambda tmp: story_zip(TOLD), id="a story that waits for nothing"),
])
def test_a_page_for_a_work_item_that_asks_nothing_is_refused(
        wired, shown, marks, record, tmp_path, work_item, data):
    r = send(wired, data(tmp_path), work=work_item.id)
    assert r.status_code == 409, r.text
    said = r.json()["error"]
    assert f"names work item {work_item.id} but asks Tim nothing" in said
    assert "make_package, which adds the standard vote" in said
    assert package_inbox.listing() == [] and record.pending_count() == 0 and shown == []


def test_an_agent_package_for_a_work_item_has_its_vote_asked_on_the_item(
        wired, shown, marks, record, work_item):
    """It cannot carry the vote itself: the standard one is asked on the
    work item as it arrives, on its Decisions tab."""
    agent = make_zip({"BRIEF.md": "Count the words."},
                     {"name": "count", "version": "1.0.0", "purpose": "Count.", "runs": "agent",
                      "brief": "BRIEF.md", "returns": ["results/**"]})
    r = send(wired, agent, work=work_item.id)
    assert r.status_code == 200, r.text
    inbox_id = r.json()["inbox"]["id"]
    [decision] = work_store.get(work_item.id).decisions
    assert decision.key == f"understood-{inbox_id}" and decision.options == [
        "Got it", "I have questions"]
    conversation = make_zip({"SKILL.md": "Say it."},
                            {"name": "say", "version": "1.0.0", "purpose": "Say.",
                             "runs": "conversation", "skill": "SKILL.md",
                             "returns": ["results/**"]})
    r = send(wired, conversation, work=work_item.id)
    assert r.status_code == 409 and "a conversation package cannot carry Tim's vote" in \
        r.json()["error"]
    assert send(wired, conversation).status_code == 200, "with no Work item it is taken"


def test_a_package_for_a_work_item_that_asks_is_taken(
        wired, shown, marks, record, tmp_path, work_item):
    assert send(wired, story_zip(ASKS), work=work_item.id).status_code == 200
    assert send(wired, story_zip(VOTES, "voting-page"), work=work_item.id).status_code == 200
    assert send(wired, deck_zip(tmp_path), work=work_item.id).status_code == 200
    assert len(package_inbox.listing()) == 3


def test_a_package_that_asks_nothing_and_names_no_work_item_is_taken(
        wired, shown, marks, record, tmp_path):
    assert send(wired, page_zip()).status_code == 200
    assert send(wired, story_zip(TOLD, "told-page")).status_code == 200
    assert len(package_inbox.listing()) == 2


def presentation_zip(tmp_path) -> bytes:
    """A presentation as make_package builds it, with the vote added, signed
    as checked by an onboarded agent's key."""
    content = tmp_path / "presentation" / "content.json"
    content.parent.mkdir()
    content.write_text(json.dumps(make_package.PRESENTATION_EXAMPLE))
    out = tmp_path / "presentation" / "out"
    out.mkdir()
    made = make_package.make_deck(content, out)
    from tests.agent_key_fixtures import onboarded_auth
    agent = onboarded_auth(session="sess-1").agent
    package_signature.sign(made.archive, made.archive, agent.private, agent.public, "sess-1",
                           (package_signature.DECISION_CHECKED,))
    return made.archive.read_bytes()


def answer_by(w, how: str, inbox_id: str, option: str, words: str = "") -> None:
    if how == "page":
        r = w.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                          json={"decision": "understood", "option": option, "words": words})
        assert r.status_code == 200, r.text
    else:
        said = _call(w, "answer_package_decision", package=inbox_id, decision="understood",
                     option=option, words=words)["text"]
        assert said.startswith("Recorded."), said


SENT = {"page": lambda tmp: story_zip(VOTES), "deck": presentation_zip}


@pytest.mark.parametrize("kind", ["page", "deck"])
@pytest.mark.parametrize("how", ["page", "voice"])
def test_his_questions_go_back_to_the_sender_and_onto_the_work_item(
        wired, shown, marks, record, tmp_path, work_item, chitauri, kind, how):
    got = send(wired, SENT[kind](tmp_path), session="sess-1", work=work_item.id)
    assert got.status_code == 200, got.text
    inbox_id = got.json()["inbox"]["id"]
    [q] = package_inbox.questions(work_packages.find(package_inbox.get(inbox_id).package_id))
    assert (q.id, [o["name"] for o in q.options]) == ("understood", ["Got it", "I have questions"])
    assert package_inbox.get(inbox_id).waiting == 1, "counted in his updates as one waiting"
    answer_by(wired, how, inbox_id, "questions", "Why two keys?")
    entry = package_inbox.get(inbox_id)
    assert (entry.decisions_answered, entry.decisions_total) == (1, 1)
    said = _call(wired, "sign_off_package", package=inbox_id)["text"]
    assert said.startswith("Signed off. Capture"), said
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    key = f"understood-{inbox_id}"
    assert "- understood: questions (his words: Why two keys?)" in capture["text"]
    assert f"His questions are on work item {work_item.id} as decision {key}." in capture["text"]
    assert not capture.get("approved"), "it goes only on his yes"
    [made] = [a for a in work_store.get(work_item.id).answered if a.key == key]
    assert made.answer == "Why two keys?" and made.where in ("page", "voice")
    assert not work_store.get(work_item.id).decisions, "asked and answered at once: none open"


def test_got_it_goes_back_and_makes_no_work_decision(
        wired, shown, marks, record, tmp_path, work_item, chitauri):
    inbox_id = send(wired, story_zip(VOTES), session="sess-1",
                    work=work_item.id).json()["inbox"]["id"]
    answer_by(wired, "voice", inbox_id, "got-it")
    _call(wired, "sign_off_package", package=inbox_id)
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert "- understood: got-it" in capture["text"] and "His questions" not in capture["text"]
    assert not [a for a in work_store.get(work_item.id).answered
                if a.key.startswith("understood")]


def test_the_page_s_answer_is_his_click_alone(wired, shown, marks, tmp_path):
    inbox_id = send(wired, story_zip(VOTES)).json()["inbox"]["id"]
    r = wired.client.post(f"{INBOX}/{inbox_id}/answer", headers=token(),
                          json={"decision": "understood", "option": "got-it"})
    assert r.status_code == 403, "a tool-token caller cannot answer for him"
    package = work_packages.find(package_inbox.get(inbox_id).package_id)
    assert package_inbox.questions(package)[0].answer is None


@pytest.mark.parametrize("how", ["page", "voice"])
def test_the_head_s_buttons_and_his_voice_record_the_same_answer(
        wired, shown, marks, tmp_path, monkeypatch, how):
    """Parity: the view head's Got it / I have questions (the inbox's answer
    route) and the brain's answer_package_decision reach the one writer,
    package_inbox.record_story_answer, with the same arguments."""
    calls = []
    real = package_inbox.record_story_answer
    monkeypatch.setattr(package_inbox, "record_story_answer",
                        lambda package, q, option, note: (calls.append((q.id, option, note)),
                                                          real(package, q, option, note)))
    inbox_id = send(wired, story_zip(VOTES)).json()["inbox"]["id"]
    answer_by(wired, how, inbox_id, "questions", "Why two keys?")
    assert calls == [("understood", "questions", "Why two keys?")]
    package = work_packages.find(package_inbox.get(inbox_id).package_id)
    assert package_inbox.page_vote(package) == {
        "question": "understood", "answer": "questions", "note": "Why two keys?"}


def test_the_view_shows_the_vote_and_a_reload_shows_his_answer(wired, shown, marks, tmp_path):
    inbox_id = send(wired, story_zip(VOTES)).json()["inbox"]["id"]
    assert wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER).status_code == 200
    frame = shown[-1]["package"]
    assert frame["vote"] == {"question": "understood", "answer": None, "note": ""}
    answer_by(wired, "page", inbox_id, "got-it")
    assert wired.server._package_shown["vote"]["answer"] == "got-it", \
        "a page that connects now shows his answer"


def test_a_deck_has_no_head_vote_its_slide_asks(wired, shown, marks, tmp_path):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    assert shown[-1]["package"]["vote"] is None


def test_a_spanish_story_s_vote_is_in_spanish(wired, shown, marks, tmp_path):
    told = package_inbox.with_understood_slide({**TOLD, "language": "es"}, "es")
    assert told["slides"][-1]["title"] == "¿Entendido?"
    inbox_id = send(wired, story_zip(told)).json()["inbox"]["id"]
    [q] = package_inbox.questions(work_packages.find(package_inbox.get(inbox_id).package_id))
    assert [o["name"] for o in q.options] == ["Entendido", "Tengo preguntas"]
    assert [o["id"] for o in q.options] == ["got-it", "questions"], "the ids never change"


def test_a_page_s_vote_is_written_where_its_story_waits(wired, shown, marks, tmp_path):
    inbox_id = send(wired, story_zip(VOTES)).json()["inbox"]["id"]
    answer_by(wired, "page", inbox_id, "questions", "One thing")
    package = work_packages.find(package_inbox.get(inbox_id).package_id)
    record = json.loads((package.folder / "results" / "decisions.json").read_text())
    assert record["understood"]["answer"] == "questions"
    assert record["understood"]["note"] == "One thing"
    assert package.story().open_questions(record) == [], "the story waiting on it goes on"


# -- open --------------------------------------------------------------------------------

def test_open_is_his_click_or_his_asking(wired, shown, marks, record, tmp_path):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    assert wired.client.post(f"{INBOX}/{inbox_id}/open", headers=token()).status_code == 403
    assert shown == []
    r = wired.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
    assert r.status_code == 200, r.text
    assert shown[-1]["type"] == "package_page"
    assert package_inbox.get(inbox_id).state == package_inbox.OPENED
    assert record.get("package", inbox_id).state == attention_store.PENDING, \
        "opening is not seeing: the update stays until his Seen or Done"
    said = _call(wired, "open_inbox_package", package="the tea one")["text"]
    assert said.startswith(f"{inbox_id} is open in the centre of his screen")
    assert "decision kettle-kind:" in said and len(shown) == 2


def test_open_by_a_stranger_s_turn_is_refused(wired, shown, marks, tmp_path):
    send(wired, deck_zip(tmp_path))
    refused = _call(wired, "open_inbox_package", origin="event", package="tea")
    assert not refused["ok"] and shown == []


# -- opening leaves it unread ------------------------------------------------------------
# Open (his click, or asking JARVIS) shows the package and nothing more: its
# update line stays pending, the Queue says "opened, not done", and only his
# Seen on the line or his Done takes the update away.

def open_by(w, how: str, inbox_id: str) -> None:
    if how == "page":
        r = w.client.post(f"{INBOX}/{inbox_id}/open", headers=BROWSER)
        assert r.status_code == 200 and not r.json().get("problem"), r.text
    else:
        said = _call(w, "open_inbox_package", package=inbox_id)["text"]
        assert said.startswith(f"{inbox_id} is open in the centre of his screen"), said


def seen_by(w, how: str, item_id: int) -> None:
    if how == "page":
        r = w.client.post("/api/attention/seen", headers=BROWSER, json={"ids": [item_id]})
        assert r.status_code == 200, r.text
    else:
        assert json.loads(_call(w, "mark_updates_seen", ids=[item_id])["text"])["marked"] == 1


def done_by(w, how: str, inbox_id: str) -> None:
    if how == "page":
        r = signoff(w, package_inbox.get(inbox_id).package_id)
        assert r.status_code == 200, r.text
    else:
        assert _call(w, "sign_off_package", package=inbox_id)["text"].startswith("Signed off")


@pytest.mark.parametrize("how", ["page", "voice"])
def test_after_open_the_update_is_still_pending_and_reads_as_not_done(
        wired, shown, marks, record, tmp_path, how):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    open_by(wired, how, inbox_id)
    assert shown[-1]["type"] == "package_page"
    item = record.get("package", inbox_id)
    assert item.state == attention_store.PENDING and record.pending_count() == 1
    updates = _call(wired, "attention_updates")["text"]
    assert '"pending":1' in updates, "the brain's count is the page's count"
    assert f'"id":{item.id},"kind":"package","event":"arrived","state":"pending"' in updates
    entry = package_inbox.get(inbox_id)
    assert entry.state == package_inbox.OPENED and entry.is_open
    listed = _call(wired, "package_inbox")["text"]
    assert "opened, not done yet" in listed, listed
    page = wired.client.get(INBOX, headers=BROWSER).json()
    row = next(e for e in page["packages"] if e["id"] == inbox_id)
    assert row["state"] == "opened", "the Queue words it \"Opened, not done\" and keeps its mark"


@pytest.mark.parametrize("how", ["page", "voice"])
def test_after_open_his_seen_takes_the_update_away(wired, shown, marks, record, tmp_path, how):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    open_by(wired, how, inbox_id)
    item = record.get("package", inbox_id)
    assert item.state == attention_store.PENDING
    seen_by(wired, how, item.id)
    assert record.get("package", inbox_id).state == attention_store.SEEN
    assert record.pending_count() == 0
    assert package_inbox.get(inbox_id).is_open, "Seen is his updates only; the package is not done"


@pytest.mark.parametrize("how", ["page", "voice"])
def test_after_open_his_done_resolves_the_update(wired, shown, marks, record, tmp_path, how):
    inbox_id = send(wired, deck_zip(tmp_path)).json()["inbox"]["id"]
    open_by(wired, how, inbox_id)
    assert record.get("package", inbox_id).state == attention_store.PENDING
    done_by(wired, how, inbox_id)
    assert record.get("package", inbox_id).state == attention_store.RESOLVED
    assert record.pending_count() == 0
    entry = package_inbox.get(inbox_id)
    assert (entry.state, entry.closed_why) == (package_inbox.CLOSED, package_inbox.SIGNED_OFF)


# -- answering ---------------------------------------------------------------------------

def opened_deck(w, tmp_path, **query) -> str:
    inbox_id = send(w, deck_zip(tmp_path, session=query.get("session", "sess-tea")),
                    **query).json()["inbox"]["id"]
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
    run_store.sync_agents({})           # its agent has ended since
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
    wired.client.get("/api/work")     # the held view, as a page loads it
    inbox_id = send(wired, deck_zip(tmp_path), work=item.id).json()["inbox"]["id"]
    sent = [h for h in wired.hints if h["type"] == "work_item" and h["item"]["id"] == item.id]
    assert sent and sent[-1]["item"]["package_questions"] == 2
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
def unproven(wired, claims_proven, monkeypatch):
    """The real proof: only a signature (or the signed request) proves who
    sent it (tests/test_agent_key_packages.py has the signatures)."""
    monkeypatch.setattr(wired.server, "_package_sender", claims_proven)


def test_a_claimed_session_nothing_proves_gets_no_answers_and_the_row_says_so(
        wired, shown, marks, unproven):
    got = send(wired, page_zip(), session="sess-1", source="notes/").json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.claimed_session, entry.sender_evidence) == ("", "sess-1", "")
    assert got["inbox"]["proof_pending"] is False and entry.unproven_claim, \
        "nothing proves it later: transcripts prove no package"
    assert "go nowhere" in _call(wired, "package_inbox")["text"]
    r = signoff(wired, got["package"]["id"])
    assert "not proven to have sent it" in r.json()["said"]
    assert not any(c.get("to_session") == "sess-1" for c in wired.server._captures.values())


def test_the_signed_request_proves_its_own_agent_s_session_alone(wired, shown, marks, unproven):
    from tests.agent_key_fixtures import onboarded_auth
    auth = onboarded_auth(("packages",), "sess-1")
    got = wired.client.post(INBOX, content=page_zip(), auth=auth, params={"session": "sess-1"},
                            headers={"Content-Type": "application/zip"}).json()
    entry = package_inbox.get(got["inbox"]["id"])
    assert (entry.from_session, entry.sender_evidence) == ("sess-1", "agent_key")
    other = wired.client.post(INBOX, content=page_zip("1.0.1"), auth=auth,
                              params={"session": "sess-2"},
                              headers={"Content-Type": "application/zip"}).json()
    assert package_inbox.get(other["inbox"]["id"]).from_session == "", "not its own session"


def test_migration_5_leaves_no_row_waiting_to_be_proven_again(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.row_factory = sqlite3.Row
    run_store.migrate(conn, "package_inbox_schema",
                      {k: v for k, v in package_inbox._MIGRATIONS.items() if k <= 4}, "inbox")
    conn.execute("INSERT INTO package_inbox (id, package_id, sha256, name, version, title, "
                 "purpose, runs, state, revision, arrived_at, claimed_session, reprove) VALUES "
                 "('r', 'p', 'x', 'n', '1', 't', 'p', 'page', 'arrived', 1, 0, 's', 1)")
    run_store.migrate(conn, "package_inbox_schema", package_inbox._MIGRATIONS, "inbox")
    assert [r["reprove"] for r in conn.execute("SELECT reprove FROM package_inbox")] == [0]


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
    run_store.migrate(conn, "package_inbox_schema",
                      {k: v for k, v in package_inbox._MIGRATIONS.items() if k <= 4}, "inbox")
    marked = [r["id"] for r in conn.execute("SELECT id FROM package_inbox WHERE reprove=1")]
    assert marked == ["r0"]
