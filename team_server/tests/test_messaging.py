"""Human messaging on the team server, against a real MongoDB: acceptance,
idempotency, authorization, sync, receipts, work state, announcement leases
and the crash points between them (injected `ArmedFaults`, never a route)."""
from __future__ import annotations

import uuid
from typing import Optional

import pytest

from conftest import ADMIN, ALICE, BOB, SESSION_COOKIE, browser_token, pair, run_db
from teamserver import messaging
from teamserver import messaging_models as mm

PREFIX = "/api/v1/messages"


# -- the harness ------------------------------------------------------------------

def device_dependency(app):
    """The app's own device-session dependency, as app.py's wiring passes it
    to `messaging.mount`."""
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v1/network/nodes/self")
    return next(d.call for d in route.dependant.dependencies if d.call.__name__ == "device")


class Pins:
    """An AttachmentStorage stand-in for the two calls acceptance makes; the
    real GridFS storage is the files stream's and has its own tests."""

    def __init__(self):
        self.ready: set[uuid.UUID] = set()
        self.pinned: dict[uuid.UUID, uuid.UUID] = {}
        self.published: list[uuid.UUID] = []

    async def pin(self, server_id, sender_github_id, ref, message_id, digest, now) -> bool:
        if ref.attachment_id not in self.ready or self.pinned.get(
                ref.attachment_id, message_id) != message_id:
            return False
        self.pinned[ref.attachment_id] = message_id
        return True

    async def mark_published(self, server_id, attachment_id) -> None:
        self.published.append(attachment_id)


@pytest.fixture
def faults():
    return mm.ArmedFaults()


@pytest.fixture
def pins():
    return Pins()


@pytest.fixture
def client(make_client, faults, pins):
    c = make_client()
    messaging.mount(c.app, device=device_dependency(c.app), enabled=True, attachments=pins,
                    faults=faults)
    return c


@pytest.fixture
def admin(client, github):
    return browser_token(client, github, ADMIN)


class Member:
    """One member with one paired JARVIS node published on the network."""

    def __init__(self, client, github, identity, admin: Optional[str] = None):
        self.client, self.github_id = client, identity.id
        if admin is not None:
            roster = client.get("/api/v1/team/members", headers=bearer(admin)).json()
            r = client.put(f"/api/v1/team/members/{identity.id}", headers=bearer(admin),
                           json={"expected_revision": roster["revision"]})
            assert r.status_code == 200, r.text
        self.browser = browser_token(client, github, identity)
        self.token = pair(client, self.browser)
        self.node = self.publish_node()
        self.server_id = client.app.state.server_id

    def publish_node(self, token: Optional[str] = None) -> uuid.UUID:
        node = uuid.uuid4()
        r = self.client.post("/api/v1/network/nodes/self", headers=bearer(token or self.token),
                             json={"node_id": str(node), "boot_id": str(uuid.uuid4()), "seq": 1,
                                   "label": "TEST NODE", "jarvis_version": "1", "os": "macOS",
                                   "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0, "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
                                   "brains": [],
                                   "activity": None})
        assert r.status_code == 200, r.text
        return node

    def request(self, to: int, text: str = "Hello.", *, reply_to: Optional[dict] = None,
                related=(), message_id=None, key=None, revision=1, node=None,
                attachments=()) -> dict:
        mid = message_id or uuid.uuid4()
        content = mm.MessageContent(
            message_id=mid, thread_id=reply_to["thread_id"] if reply_to else mid,
            in_reply_to_message_id=reply_to["message_id"] if reply_to else None,
            related_message_ids=list(related), recipient_github_id=to, content_revision=revision,
            text=mm.normalize_text(text), attachments=list(attachments))
        req = mm.SendRequest(**content.model_dump(), source_node_id=node or self.node,
                             idempotency_key=key or uuid.uuid4(),
                             content_digest=mm.content_digest(content, server_id=self.server_id,
                                                              sender_github_id=self.github_id))
        return req.model_dump(mode="json")

    def post(self, body: dict):
        return self.client.post(PREFIX, json=body, headers=bearer(self.token))

    def send(self, to: int, text: str = "Hello.", **kw) -> dict:
        r = self.post(self.request(to, text, **kw))
        assert r.status_code == 200, r.text
        return r.json()["message"]

    def scan(self, limit: int = 25, node=None) -> list[dict]:
        entries, continuation = [], None
        while True:
            page = self.sync(continuation, limit, node).json()
            entries += page["entries"]
            if page["scan_complete"]:
                return entries
            continuation = page["continuation"]

    def sync(self, continuation=None, limit=25, node=None):
        params = {"node_id": str(node or self.node), "limit": limit}
        if continuation:
            params["continuation"] = continuation
        return self.client.get(PREFIX + "/sync", params=params, headers=bearer(self.token))

    def receipt(self, message_id, kind, node=None):
        return self.client.post(PREFIX + "/receipts", headers=bearer(self.token), json={
            "node_id": str(node or self.node),
            "receipts": [{"message_id": str(message_id), "kind": kind}]})

    def work(self, thread_id, change: dict):
        return self.client.put(f"{PREFIX}/threads/{thread_id}/work-state",
                               headers=bearer(self.token), json={"change": change})

    def announce(self, message_id, step, token=None, node=None):
        return self.client.post(f"{PREFIX}/{message_id}/announcement", headers=bearer(self.token),
                                json={"node_id": str(node or self.node), "step": step,
                                      "lease_token": token})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tim(client, github, admin):
    return Member(client, github, ADMIN)


@pytest.fixture
def alice(client, github, admin):
    return Member(client, github, ALICE, admin)


@pytest.fixture
def bob(client, github, admin):
    return Member(client, github, BOB, admin)


def error(r) -> str:
    return r.json()["error"]["code"]


# -- indexes and the switch -------------------------------------------------------------

@pytest.fixture
def delivery_db(mongo):
    """A database in delivery's own namespace (hm_delivery_), dropped after."""
    name = "hm_delivery_" + uuid.uuid4().hex[:12]
    yield name
    assert name.startswith("hm_delivery_")
    mongo.drop_database(name)


def test_indexes_are_made_twice_on_a_fresh_namespace_and_nothing_is_purged(delivery_db, mongo):
    seed = mongo[delivery_db].sessions.insert_one({"_id": "keep-me"})

    async def twice(db):
        await messaging.ensure_indexes(db)
        await messaging.ensure_indexes(db)

    run_db(delivery_db, twice)
    db = mongo[delivery_db]
    assert db.sessions.find_one({"_id": seed.inserted_id}) is not None
    indexes = {name: spec for name, spec in db.messages.index_information().items()}
    assert indexes["sender_idempotency_unique"].get("unique") is True
    assert {"by_participant_arrival", "by_thread_arrival", "by_parent",
            "by_attachment"} <= set(indexes)
    assert db.message_work.index_information()["owner_thread_unique"].get("unique") is True
    assert "ttl_purge" in db.message_sync.index_information()
    assert "by_owner" in db.messaging_nodes.index_information()


def test_switched_off_every_route_says_so_and_nothing_is_offered(make_client, github):
    c = make_client()
    messaging.mount(c.app, device=device_dependency(c.app), enabled=False)
    assert messaging.capabilities(False) == [] and messaging.capabilities(True) == [mm.CAPABILITY]
    for method, path in (("GET", PREFIX + "/recipients"), ("POST", PREFIX),
                         ("GET", f"{PREFIX}/{uuid.uuid4()}")):
        r = c.request(method, path, json={} if method == "POST" else None)
        assert (r.status_code, error(r)) == (503, "messaging_disabled")


def test_only_a_paired_jarvis_may_use_messaging(client, github, tim):
    client.cookies.set(SESSION_COOKIE, tim.browser, domain="team.test")
    try:
        r = client.get(PREFIX + "/recipients")
    finally:
        client.cookies.clear()
    assert (r.status_code, error(r)) == (403, "device_required")
    r = client.get(PREFIX + "/recipients")
    assert r.status_code == 401


def test_the_recipient_list_is_the_active_roster_including_me(client, github, admin, tim, alice):
    roster = client.get("/api/v1/team/members", headers=bearer(admin)).json()
    r = client.put(f"/api/v1/team/members/{BOB.id}", headers=bearer(admin),
                   json={"expected_revision": roster["revision"]})
    assert r.status_code == 200                # approved, never signed in: not active
    r = client.get(PREFIX + "/recipients", headers=bearer(alice.token))
    assert [m["github_id"] for m in r.json()["members"]] == [ALICE.id, ADMIN.id]
    assert tim.post(tim.request(BOB.id)).json()["error"]["code"] == "recipient_not_allowed"


# -- acceptance --------------------------------------------------------------------------

def test_self_one_node_is_one_item_on_my_side_and_acceptance_is_no_receipt(client, tim, mongo,
                                                                           frozen):
    body = tim.request(ADMIN.id, "Remember the deploy.")
    r = tim.post(body)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["duplicate"] is False
    work = result["work_state"]
    assert (work["placement"], work["action_owner_github_id"], work["unread_reminder"]) == \
        ("queued", ADMIN.id, True)
    assert result["receipts"]["received_at"] is None
    again = tim.post(body).json()
    assert again["duplicate"] is True and again["message"] == result["message"]
    db = mongo[client.app.state.config.mongodb_database]
    assert db.messages.count_documents({}) == 1 and db.message_work.count_documents({}) == 1
    (entry,) = tim.scan()
    assert entry["message"] == result["message"] and entry["work_state"] == work
    frozen.advance(seconds=5)
    facts = tim.receipt(result["message"]["message_id"], "received").json()["facts"][0]
    assert facts["received_at"] is not None and facts["read_at"] is None
    assert tim.scan()[0]["work_state"] == work, "a receipt never moves the item"


def test_two_owner_isolation_holds_for_guessed_ids_and_administrators(client, tim, alice, bob):
    m = alice.send(BOB.id, "Private.")
    assert tim.scan() == [], "the administrator is not a participant"
    r = client.get(f"{PREFIX}/{m['message_id']}", headers=bearer(tim.token))
    assert (r.status_code, error(r)) == (404, "message_not_found")
    r = tim.receipt(m["message_id"], "read")
    assert r.json() == {"facts": [], "not_found": [m["message_id"]]}
    r = tim.work(m["thread_id"], {"command": "open", "expected_revision": 1})
    assert (r.status_code, error(r)) == (404, "message_not_found")
    r = tim.announce(m["message_id"], "claim")
    assert (r.status_code, error(r)) == (404, "message_not_found")
    r = tim.post(tim.request(BOB.id, "Me too.", related=[m["message_id"]]))
    assert error(r) == "relation_not_allowed", "a link never grants access"
    r = tim.post(tim.request(BOB.id, "Hijack.", reply_to=m))
    assert error(r) == "relation_not_allowed"
    assert alice.receipt(m["message_id"], "read").json()["not_found"] == [m["message_id"]], \
        "the sender's own read is no receipt"
    r = tim.sync(node=alice.node)
    assert (r.status_code, error(r)) == (403, "source_node_not_owned")
    r = tim.post(tim.request(ALICE.id, "Forged node.", node=alice.node))
    assert error(r) == "source_node_not_owned"
    r = tim.post(tim.request(ALICE.id, "Unknown node.", node=uuid.uuid4()))
    assert error(r) == "source_node_not_owned"


def test_a_lost_answer_is_retried_into_exactly_one_message(client, tim, alice, faults, mongo):
    body = tim.request(ALICE.id, "Once only.")
    faults.arm("server_after_commit_before_response")
    with pytest.raises(mm.InjectedFault) as e:      # the answer is lost (a 500 on the wire)
        tim.post(body)
    assert e.value.point == "server_after_commit_before_response"
    again = tim.post(body)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    db = mongo[client.app.state.config.mongodb_database]
    assert db.messages.count_documents({}) == 1


def test_same_key_or_id_with_other_content_is_a_conflict_and_a_forgery_is_refused(tim, alice):
    first = tim.request(ALICE.id, "Friday.")
    assert tim.post(first).status_code == 200
    changed = tim.request(ALICE.id, "Saturday.", message_id=uuid.UUID(first["message_id"]),
                          key=uuid.UUID(first["idempotency_key"]), revision=2)
    r = tim.post(changed)
    assert (r.status_code, error(r)) == (409, "idempotency_conflict")
    r = tim.post({**first, "text": "Saturday."})
    assert (r.status_code, error(r)) == (422, "digest_mismatch")
    r = alice.post(alice.request(ADMIN.id, "Mine too.", message_id=uuid.UUID(first["message_id"])))
    assert (r.status_code, error(r)) == (409, "idempotency_conflict")
    assert "latest" not in r.json()["error"], "nothing about the other message is disclosed"


def test_the_envelope_is_refused_with_the_code_that_says_why(client, tim, alice):
    good = tim.request(ALICE.id, "Fine.")
    for body, code in (({**good, "text": "a\r\nb"}, "content_not_normalized"),
                       ({**good, "schema_version": 2}, "unsupported_schema_version"),
                       ({**good, "colour": "red"}, "invalid_request"),
                       ({**good, "text": ""}, "invalid_request")):
        r = tim.post(body)
        assert (r.status_code, error(r)) == (422 if code != "too_large" else 413, code)


def test_a_body_without_a_length_is_counted_as_it_is_read(client, tim, alice):
    def chunks():
        for _ in range(mm.MESSAGE_REQUEST_MAX_BYTES // 1024 + 2):
            yield b" " * 1024

    r = client.post(PREFIX, content=chunks(), headers={**bearer(tim.token),
                                                       "Content-Type": "application/json"})
    assert (r.status_code, error(r)) == (413, "too_large")


def test_a_suspended_sender_is_refused_before_anything_is_read(client, github, admin, alice):
    roster = client.get("/api/v1/team/members", headers=bearer(admin)).json()
    r = client.put(f"/api/v1/team/members/{ALICE.id}", headers=bearer(admin),
                   json={"expected_revision": roster["revision"], "status": "suspended"})
    assert r.status_code == 200, r.text
    r = alice.post(alice.request(ADMIN.id))
    assert r.status_code == 401, "suspension ends the session's generation"


# -- replies, relations and work state ----------------------------------------------------

def test_a_reply_joins_its_thread_and_the_turn_follows_the_messages(client, tim, alice, frozen):
    root = tim.send(ALICE.id, "Can we move the deploy?")
    (entry,) = alice.scan()
    assert entry["work_state"]["action_owner_github_id"] == ALICE.id
    frozen.advance(minutes=1)
    reply = alice.send(ADMIN.id, "Friday works.", reply_to=root)
    assert reply["thread_id"] == root["thread_id"]
    tims = {e["message"]["message_id"]: e for e in tim.scan()}
    item = tims[reply["message_id"]]["work_state"]
    assert (item["action_owner_github_id"], item["unread_reminder"]) == (ADMIN.id, True)
    assert item["arrival_at"] == reply["accepted_at"]
    other = tim.send(ALICE.id, "Unrelated.")
    r = alice.post(alice.request(ADMIN.id, "Wrong thread.",
                                 reply_to={**root, "thread_id": other["thread_id"]}))
    assert error(r) == "relation_not_allowed", "the parent must be in the named thread"
    related = alice.send(ADMIN.id, "See the other one.", related=[other["message_id"]])
    assert related["thread_id"] == related["message_id"], "a link joins nothing"


def test_work_state_is_the_owners_by_optimistic_concurrency(client, tim, alice):
    m = tim.send(ALICE.id, "Hello.")
    (entry,) = alice.scan()
    rev = entry["work_state"]["revision"]
    opened = alice.work(m["thread_id"], {"command": "open", "expected_revision": rev}).json()
    assert opened["placement"] == "active" and opened["revision"] == rev + 1
    again = alice.work(m["thread_id"], {"command": "open", "expected_revision": rev + 1})
    assert again.json() == opened, "a repeat keeps the revision"
    stale = alice.work(m["thread_id"], {"command": "dismiss", "expected_revision": rev})
    assert (stale.status_code, error(stale)) == (409, "revision_conflict")
    assert stale.json()["error"]["latest"] == opened
    assert tim.scan()[0]["work_state"]["placement"] == "active", "Tim's item is his own"


# -- sync ----------------------------------------------------------------------------------

def test_a_scan_pages_without_gaps_and_late_publication_is_found_by_the_next(client, tim,
                                                                             alice, frozen):
    for n in range(3):
        frozen.advance(seconds=1)
        tim.send(ALICE.id, f"m{n}")
    first = alice.sync(limit=2).json()
    assert len(first["entries"]) == 2 and not first["scan_complete"]
    frozen.advance(seconds=-10)
    late = tim.send(ALICE.id, "accepted earlier, published later")
    frozen.advance(seconds=10)
    rest = alice.sync(first["continuation"], 2).json()
    seen = {e["message"]["message_id"] for e in first["entries"] + rest["entries"]}
    assert late["message_id"] not in seen and rest["epoch"] == first["epoch"]
    assert late["message_id"] in {e["message"]["message_id"] for e in alice.scan()}


def test_a_continuation_is_one_nodes_and_expires(client, tim, alice, frozen):
    desk = alice.publish_node()
    for n in range(3):
        tim.send(ALICE.id, f"m{n}")
    page = alice.sync(limit=1).json()
    r = alice.sync(page["continuation"], 1, node=desk)
    assert (r.status_code, error(r)) == (410, "sync_restart")
    r = tim.sync(page["continuation"], 1)
    assert error(r) == "sync_restart", "nor another owner's"
    frozen.advance(seconds=mm.SYNC_CONTINUATION_TTL_SEC + 1)
    r = alice.sync(page["continuation"], 1)
    assert (r.status_code, error(r)) == (410, "sync_restart")
    for bad in (0, mm.SYNC_PAGE_MAX + 1):
        assert error(alice.sync(limit=bad)) == "invalid_request"


def test_projections_a_crash_skipped_are_repaired_once_by_whoever_comes_next(client, tim, alice,
                                                                            faults, mongo):
    root = tim.send(ALICE.id, "Root.")
    alice.scan()
    body = alice.request(ADMIN.id, "Reply.", reply_to=root)
    faults.arm("server_after_insert_before_projection")
    with pytest.raises(mm.InjectedFault):
        alice.post(body)
    entries = {e["message"]["message_id"]: e for e in tim.scan()}
    item = entries[body["message_id"]]["work_state"]
    assert item["action_owner_github_id"] == ADMIN.id, "repaired when Tim's scan read it"
    assert alice.post(body).json()["duplicate"] is True
    assert tim.scan()[-1]["work_state"] == item, "applied once, however often repaired"
    db = mongo[client.app.state.config.mongodb_database]
    assert db.message_work.count_documents({}) == 2


# -- receipts and announcements -------------------------------------------------------------

def test_reordered_receipts_keep_both_first_facts_and_each_node_acknowledges(client, tim, alice,
                                                                            frozen):
    m = tim.send(ALICE.id, "Read me.")
    desk = alice.publish_node()
    frozen.advance(minutes=1)
    read = alice.receipt(m["message_id"], "read").json()["facts"][0]
    assert read["read_at"] is not None and read["received_at"] is None
    frozen.advance(minutes=1)
    both = alice.receipt(m["message_id"], "received").json()["facts"][0]
    assert both["read_at"] == read["read_at"] and both["received_at"] is not None
    frozen.advance(minutes=1)
    later = alice.receipt(m["message_id"], "received", node=desk).json()["facts"][0]
    assert later["received_at"] == both["received_at"], "the first time never moves"
    assert [n["node_id"] for n in later["nodes"]] == [str(alice.node), str(desk)]
    assert alice.receipt(m["message_id"], "received", node=desk).json()["facts"][0][
        "revision"] == later["revision"], "a repeat changes nothing"
    r = alice.receipt(m["message_id"], "read", node=tim.node)
    assert error(r) == "source_node_not_owned"


def test_one_of_the_owners_nodes_holds_the_announcement(client, tim, alice, frozen):
    m = tim.send(ALICE.id, "Ping.")
    desk = alice.publish_node()
    claim = alice.announce(m["message_id"], "claim").json()
    assert claim["state"] == "claimed" and claim["lease_token"]
    r = alice.announce(m["message_id"], "claim", node=desk)
    assert (r.status_code, error(r)) == (409, "announcement_claimed")
    assert r.json()["error"]["latest"]["lease_token"] is None, "the token is withheld"
    done = alice.announce(m["message_id"], "completed", claim["lease_token"]).json()
    assert done["state"] == "completed"
    frozen.advance(seconds=messaging.ANNOUNCEMENT_LEASE_SEC + 1)
    assert error(alice.announce(m["message_id"], "claim", node=desk)) == "announcement_claimed"
    r = tim.announce(m["message_id"], "claim")
    assert error(r) == "message_not_found", "only the recipient announces"


# -- files at acceptance ------------------------------------------------------------------

def test_a_message_with_files_is_accepted_only_once_they_are_pinned(client, tim, alice, pins,
                                                                    faults):
    ref = mm.AttachmentRef(attachment_id=uuid.uuid4(), filename="plan.pdf",
                           media_type="application/pdf", size_bytes=3, sha256="a" * 64)
    body = tim.request(ALICE.id, "", attachments=[ref])
    assert error(tim.post(body)) == "attachment_not_ready"
    pins.ready.add(ref.attachment_id)
    faults.arm("server_after_pin_before_insert")
    with pytest.raises(mm.InjectedFault):
        tim.post(body)
    assert alice.scan() == [], "pinned but not inserted: nothing visible"
    r = tim.post(body)
    assert r.status_code == 200 and r.json()["message"]["attachments"][0]["filename"] == "plan.pdf"
    assert pins.published == [ref.attachment_id]
