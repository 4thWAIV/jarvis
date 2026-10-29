"""The page's messaging routes over the real local store and delivery loop
(the team server is the contract's FakeServe; approval is the contract's
TableGuard standing in for the approval stream's guard, which integration
swaps in): drafts and their conflicts, review opening the capture the owner
accepts, cancel and retry, the dismissal guard, kept work commands, read
receipts only from a displayed incoming body, and the switch."""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import messaging_api
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_client import Delivery
from jarvis.messaging_store import SqliteMessagingStore
from tests.fixtures import messaging_fakes as fakes
from tests.test_messaging_client import SERVER, TIM, MATT, Connection, Lossy

P = "/api/team/messages"


class Captures:
    """server.py's capture box as the review uses it."""

    def __init__(self):
        self.open: dict[int, tuple[UUID, str, str]] = {}
        self.closed: list[int] = []
        self.n = 0

    def open_for_review(self, draft, readback, recipient_label):
        self.n += 1
        self.open[self.n] = (draft.message_id, readback, recipient_label)
        return self.n

    def close(self, capture_id):
        self.closed.append(capture_id)
        self.open.pop(capture_id, None)


class Page:
    def __init__(self, tmp_path, enabled=True):
        self.clock = fakes.FixedClock()
        self.serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=self.clock)
        self.node = UUID(int=1)
        self.store = SqliteMessagingStore(tmp_path / "page.db")
        self.guard = fakes.TableGuard(self.store, ids=fakes.SeqIds(start=900), clock=self.clock)
        self.captures = Captures()
        self.enabled = enabled
        self.hints = 0
        self.transport = Lossy(self.serve.pair(TIM, self.node))
        self.delivery = Delivery(
            store=self.store, connection=Connection(TIM), guard=self.guard,
            node_id=lambda: self.node, clock=self.clock, rand=fakes.SeqRandom(),
            on_change=self._hint, enabled=lambda: self.enabled,
            transport_for=lambda c, p: self.transport)
        self.local = messaging_api.LocalMessaging(
            store=self.store, delivery=self.delivery, guard=self.guard, captures=self.captures,
            ids=fakes.SeqIds(start=100), clock=self.clock)
        app = FastAPI()
        messaging_api.mount(app, self.local)
        self.http = TestClient(app)
        self.matt = self.serve.pair(MATT, UUID(int=2))

    def _hint(self):
        self.hints += 1

    def tick(self):
        asyncio.run(self.delivery.tick())

    def draft(self, text="Friday?", to=MATT, **kw):
        r = self.http.post(P + "/drafts", json={"recipient_github_id": to, "text": text, **kw})
        assert r.status_code == 201, r.text
        return r.json()

    def act(self, message_id, action, revision):
        return self.http.post(f"{P}/drafts/{message_id}/{action}",
                              json={"expected_draft_revision": revision})

    def accept_review(self, draft: dict) -> None:
        """What the owner's acceptance does at hand-over (approval's
        Destination.deliver), through the guard."""
        self.guard.approve(SERVER, TIM, UUID(draft["message_id"]),
                           draft["send"]["content_revision"], draft["send"]["content_digest"],
                           self.node, "gui")

    def matt_sends(self, text="Hello Tim.") -> wire.AcceptedMessage:
        mid = self.serve.ids.new_id()
        content = wire.MessageContent(message_id=mid, thread_id=mid, recipient_github_id=TIM,
                                      content_revision=1, text=text)
        return asyncio.run(self.matt.send(wire.SendRequest(
            **content.model_dump(), source_node_id=UUID(int=2),
            idempotency_key=self.serve.ids.new_id(),
            content_digest=wire.content_digest(content, server_id=SERVER,
                                               sender_github_id=MATT)))).message


@pytest.fixture
def page(tmp_path):
    return Page(tmp_path)


def code(r):
    return r.json()["error"]["code"]


def test_switched_off_the_queue_says_so_and_nothing_else_runs(tmp_path):
    page = Page(tmp_path, enabled=False)
    snap = page.http.get(P).json()
    assert (snap["availability"], snap["items"]) == ("switched_off", [])
    r = page.http.post(P + "/drafts", json={"recipient_github_id": MATT, "text": "x"})
    assert (r.status_code, code(r)) == (503, "messaging_unavailable")
    assert r.json()["error"]["availability"] == "switched_off"


def test_a_draft_is_normalized_once_and_a_stale_save_shows_the_latest(page):
    draft = page.draft("Line one\r\nline two")
    assert draft["text"] == "Line one\nline two" and draft["send"]["state"] == "draft"
    mid = draft["message_id"]
    saved = page.http.put(f"{P}/drafts/{mid}", json={
        "expected_draft_revision": 1, "recipient_github_id": MATT, "text": "Agent's words."})
    assert saved.status_code == 200 and saved.json()["draft_revision"] == 2
    stale = page.http.put(f"{P}/drafts/{mid}", json={
        "expected_draft_revision": 1, "recipient_github_id": MATT, "text": "Page's words."})
    assert (stale.status_code, code(stale)) == (409, "revision_conflict")
    assert stale.json()["error"]["latest"]["text"] == "Agent's words."
    snap = page.http.get(P).json()
    assert [d["message_id"] for d in snap["unsent"]] == [mid]
    bad = page.http.post(P + "/drafts", json={"recipient_github_id": MATT, "colour": "red"})
    assert (bad.status_code, code(bad)) == (422, "invalid_request")
    assert page.http.delete(f"{P}/drafts/{mid}", params={"expected_draft_revision": 2}
                            ).status_code == 204
    assert page.http.get(P).json()["unsent"] == []


def test_review_opens_the_capture_an_edit_closes_it_and_cancel_holds(page):
    draft = page.draft("Can we move the deploy?")
    mid = draft["message_id"]
    reviewed = page.act(mid, "review", 1).json()
    assert reviewed["send"]["state"] == "awaiting_approval" and reviewed["capture_id"] == 1
    (_, readback, label), = page.captures.open.values()
    assert label == f"member{MATT}" and readback.startswith(f"To member{MATT}.")
    assert "Can we move the deploy?" in readback
    assert page.act(mid, "review", 1).json() == reviewed, "a second click changes nothing"
    assert page.store.unresolved_sends(SERVER, TIM) == [], "a review approves nothing"
    edited = page.http.put(f"{P}/drafts/{mid}", json={
        "expected_draft_revision": 1, "recipient_github_id": MATT, "text": "Friday instead?"})
    edited = edited.json()
    assert (edited["send"]["content_revision"], edited["send"]["state"]) == (2, "draft")
    assert page.captures.closed == [1] and edited["capture_id"] is None
    again = page.act(mid, "review", edited["draft_revision"]).json()
    cancelled = page.act(mid, "cancel", again["draft_revision"]).json()
    assert cancelled["send"]["state"] == "cancelled" and cancelled["capture_id"] is None
    assert page.captures.closed == [1, 2]


def test_an_accepted_review_goes_out_and_becomes_an_active_item(page):
    draft = page.draft("Ship it?")
    reviewed = page.act(draft["message_id"], "review", 1).json()
    page.accept_review(reviewed)
    hints = page.hints
    page.tick()
    snap = page.http.get(P).json()
    assert snap["unsent"] == [] and page.hints > hints
    (item,) = snap["items"]
    assert item["work_state"]["placement"] == "active"
    assert item["work_state"]["action_owner_github_id"] == MATT
    assert item["messages"][0]["direction"] == "outgoing"
    r = page.http.post(f"{P}/{draft['message_id']}/read")
    assert r.status_code == 200
    assert page.store.receipts_owed(SERVER, TIM) == [], "the sender's own read is no receipt"


def test_a_refused_send_is_retried_by_the_owner_under_the_same_revision(page):
    draft = page.draft("Still there?")
    reviewed = page.act(draft["message_id"], "review", 1).json()
    page.accept_review(reviewed)
    page.serve.members.discard(MATT)          # suspended after the owner's yes
    page.tick()
    failed = page.store.send(SERVER, TIM, UUID(draft["message_id"]))
    assert (failed.state, failed.failure) == ("failed", "recipient_not_allowed")
    page.serve.members.add(MATT)
    current = page.store.draft(SERVER, TIM, UUID(draft["message_id"]))
    r = page.act(draft["message_id"], "retry", current.draft_revision)
    assert r.status_code == 200, r.text
    page.tick()
    assert page.store.send(SERVER, TIM, UUID(draft["message_id"])).state == "accepted"
    r = page.act(draft["message_id"], "retry", current.draft_revision)
    assert (r.status_code, code(r)) == (409, "send_refused")


def test_a_recipient_off_the_roster_cannot_be_reviewed(page):
    page.serve.members.discard(MATT)
    draft = page.draft("Anyone?")
    r = page.act(draft["message_id"], "review", 1)
    assert (r.status_code, code(r)) == (403, "recipient_not_allowed")
    assert page.captures.open == {}


def test_reading_an_incoming_body_owes_its_read_receipt(page):
    m = page.matt_sends()
    page.tick()
    page.tick()
    assert page.serve.receipts[m.message_id].read_at is None, "arrival is not a read"
    r = page.http.post(f"{P}/{m.message_id}/read")
    assert r.status_code == 200 and r.json()["direction"] == "incoming"
    page.tick()
    assert page.serve.receipts[m.message_id].read_at is not None
    assert code(page.http.post(f"{P}/{UUID(int=5)}/read")) == "message_not_found"


def test_a_reply_draft_names_its_parent_and_one_editable_draft_per_item(page):
    m = page.matt_sends("Lunch?")
    page.tick()
    reply = page.draft("Yes.", thread_id=str(m.thread_id),
                       in_reply_to_message_id=str(m.message_id))
    assert reply["thread_id"] == str(m.thread_id)
    second = page.http.post(P + "/drafts", json={
        "recipient_github_id": MATT, "text": "Also yes.", "thread_id": str(m.thread_id),
        "in_reply_to_message_id": str(m.message_id)})
    assert (second.status_code, code(second)) == (409, "revision_conflict")
    assert second.json()["error"]["latest"]["message_id"] == reply["message_id"]
    orphan = page.http.post(P + "/drafts", json={
        "recipient_github_id": MATT, "text": "x", "thread_id": str(m.thread_id),
        "in_reply_to_message_id": str(UUID(int=9))})
    assert code(orphan) == "invalid_request", "a reply's parent is a message in its thread"


def test_unsent_text_blocks_dismissal_and_a_kept_command_goes_later(page):
    m = page.matt_sends("Lunch?")
    page.tick()
    item = page.http.get(f"{P}/threads/{m.thread_id}").json()
    rev = item["work_state"]["revision"]
    reply = page.draft("Maybe.", thread_id=str(m.thread_id),
                       in_reply_to_message_id=str(m.message_id))
    r = page.http.put(f"{P}/threads/{m.thread_id}/work-state",
                      json={"change": {"command": "dismiss", "expected_revision": rev}})
    assert (r.status_code, code(r)) == (409, "send_refused")
    assert r.json()["error"]["dismiss_blockers"] == ["unsent_text"]
    page.http.delete(f"{P}/drafts/{reply['message_id']}", params={"expected_draft_revision": 1})
    page.serve.capabilities = []
    page.delivery._capabilities.clear()
    kept = page.http.put(f"{P}/threads/{m.thread_id}/work-state",
                         json={"change": {"command": "open", "expected_revision": rev}}).json()
    assert kept["pending"] is True and kept["work_state"]["revision"] == rev
    assert page.http.get(f"{P}/threads/{m.thread_id}").json()["pending_command"] is not None
    page.serve.capabilities = [wire.CAPABILITY]
    page.delivery._capabilities.clear()
    page.tick()
    after = page.http.get(f"{P}/threads/{m.thread_id}").json()
    assert after["pending_command"] is None and after["work_state"]["placement"] == "active"
    stale = page.http.put(f"{P}/threads/{m.thread_id}/work-state",
                          json={"change": {"command": "put_back", "expected_revision": rev}})
    assert (stale.status_code, code(stale)) == (409, "revision_conflict")
    assert stale.json()["error"]["latest"]["revision"] == after["work_state"]["revision"]


def test_history_pages_dismissed_items(page):
    for n in range(3):
        page.matt_sends(f"m{n}")
    page.tick()
    for item in page.http.get(P).json()["items"]:
        r = page.http.put(f"{P}/threads/{item['work_state']['thread_id']}/work-state", json={
            "change": {"command": "dismiss",
                       "expected_revision": item["work_state"]["revision"]}})
        assert r.status_code == 200 and r.json()["pending"] is False
    snap = page.http.get(P).json()
    assert (snap["items"], snap["history_count"]) == ([], 3)
    first = page.http.get(P + "/history", params={"limit": 2}).json()
    rest = page.http.get(P + "/history", params={"continuation": first["continuation"]}).json()
    assert len(first["items"]) == 2 and len(rest["items"]) == 1
    assert rest["continuation"] is None
