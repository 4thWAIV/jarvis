"""Collaboration bundles on the team server (messaging.bundles.v1), against a
real MongoDB: a node's attachment capabilities on the network, a message's
task context and supersedes links, openability receipts, and what an older
JARVIS (no `?bundles=1`) is answered with."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from conftest import ADMIN, ALICE, BOB
from teamserver import messaging
from teamserver import messaging_models as mm
from test_messaging import PREFIX, Member, Pins, bearer, error

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)

CAPS = {"version": 1, "observed_age_ms": 120_000, "max_file_bytes": 1 << 28,
        "max_archive_entries": 10_000, "max_expanded_bytes": 1 << 30,
        "formats": [{"format": "zip", "operations": ["download", "list_archive"]},
                    {"format": "pdf", "operations": ["download", "read_text", "render"]}],
        "work_packages": {"manifest_versions": [1], "modes": ["conversation"]}}


def caps(value: dict) -> dict:
    """An advertisement as the snapshot serves it (defaults filled in)."""
    return mm.AttachmentCapabilities.model_validate(value).model_dump(mode="json")


@pytest.fixture
def pins():
    return Pins()


@pytest.fixture
def client(make_client, pins):
    c = make_client()
    c.app.state.messaging = messaging.Messaging(messaging.MongoMessages(c.app.state.db),
                                                attachments=pins)
    return c


@pytest.fixture
def admin(client, github):
    from conftest import browser_token
    return browser_token(client, github, ADMIN)


@pytest.fixture
def tim(client, github, admin):
    return Member(client, github, ADMIN)


@pytest.fixture
def alice(client, github, admin):
    return Member(client, github, ALICE, admin)


@pytest.fixture
def bob(client, github, admin):
    return Member(client, github, BOB, admin)


def ref(n: int, sha: str) -> mm.AttachmentRef:
    return mm.AttachmentRef(attachment_id=uuid.UUID(int=n), filename=f"f{n}.zip",
                            media_type="application/zip", size_bytes=10, sha256=sha)


def request(member: Member, to: int, *, files=(), task=None, supersedes=(),
            reply_to: Optional[dict] = None) -> dict:
    mid = uuid.uuid4()
    content = mm.MessageContent(
        message_id=mid, thread_id=reply_to["thread_id"] if reply_to else mid,
        in_reply_to_message_id=reply_to["message_id"] if reply_to else None,
        recipient_github_id=to, content_revision=1, text="Please review.",
        attachments=list(files), task=task, supersedes=list(supersedes))
    return mm.SendRequest(
        **content.model_dump(), source_node_id=member.node, idempotency_key=uuid.uuid4(),
        content_digest=mm.content_digest(content, server_id=member.server_id,
                                         sender_github_id=member.github_id)
    ).model_dump(mode="json")


def send(member: Member, body: dict, bundles: bool = True) -> dict:
    r = member.client.post(PREFIX + ("?bundles=1" if bundles else ""), json=body,
                           headers=bearer(member.token))
    assert r.status_code == 200, r.text
    return r.json()["message"]


def task_on(r: mm.AttachmentRef) -> mm.TaskContext:
    return mm.TaskContext(requested_outcome="Notes on the summary.", outcome_kind="notes",
                          primary=mm.ArtifactRef(attachment_id=r.attachment_id, sha256=r.sha256,
                                                 member_path="summary.pdf"))


def report(r: mm.AttachmentRef, status="partial", minutes=0, **over) -> dict:
    value = {"attachment_id": str(r.attachment_id), "sha256": r.sha256, "content_revision": 1,
             "status": status, "members_total": 3, "members_readable": 2,
             "failed_operation": "read_text", "failed_member": "review/summary.pdf",
             "reason": "reader_unavailable",
             "checked_at": (T0 + timedelta(minutes=minutes)).isoformat()}
    return {**value, **over}


def openability(member: Member, message_id, value: dict, node=None):
    return member.client.post(PREFIX + "/receipts?bundles=1", headers=bearer(member.token),
                              json={"node_id": str(node or member.node), "receipts": [
                                  {"message_id": str(message_id), "kind": "openability",
                                   "openability": value}]})


def scan(member: Member, bundles: bool) -> list[dict]:
    params = {"node_id": str(member.node), "limit": 25}
    if bundles:
        params["bundles"] = "1"
    r = member.client.get(PREFIX + "/sync", params=params, headers=bearer(member.token))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# -- the contract's rules ---------------------------------------------------------------

def test_bundles_are_offered_only_beside_messaging():
    assert mm.bundles_offered([mm.CAPABILITY, mm.BUNDLES_CAPABILITY])
    assert not mm.bundles_offered([mm.CAPABILITY])
    assert not mm.bundles_offered([mm.BUNDLES_CAPABILITY])


def test_a_message_without_bundle_fields_digests_as_before():
    content = mm.MessageContent(message_id=uuid.UUID(int=1), thread_id=uuid.UUID(int=1),
                                recipient_github_id=2, content_revision=1, text="Hi.")
    keys = set(mm.digest_input(content, server_id="s", sender_github_id=1))
    assert not keys & {"task", "supersedes"}
    with_task = content.model_copy(update={"attachments": [ref(5, "a" * 64)],
                                           "task": task_on(ref(5, "a" * 64))})
    assert "task" in mm.digest_input(with_task, server_id="s", sender_github_id=1)
    assert mm.content_digest(with_task, server_id="s", sender_github_id=1) != \
        mm.content_digest(with_task.model_copy(update={"task": None}), server_id="s",
                          sender_github_id=1)


def test_an_openability_report_is_kept_per_file_and_only_a_later_one_replaces_it():
    facts = mm.ReceiptFacts(message_id=uuid.UUID(int=1), recipient_github_id=2, revision=1)
    node = uuid.UUID(int=9)
    r = ref(5, "a" * 64)
    first = mm.OpenabilityReport.model_validate(report(r, minutes=5))
    merged = mm.merge_receipt(facts, "openability", node, T0, first)
    assert merged.openability == [first] and merged.revision == 2
    older = mm.OpenabilityReport.model_validate(report(r, "verified", minutes=1))
    assert mm.merge_receipt(merged, "openability", node, T0, older) == merged
    newer = mm.OpenabilityReport.model_validate(report(r, "verified", minutes=9,
                                                       members_readable=3))
    latest = mm.merge_receipt(merged, "openability", node, T0, newer)
    assert latest.openability == [newer] and latest.revision == 3
    assert (latest.received_at, latest.read_at) == (None, None), "never a received or a read"


# -- the network ---------------------------------------------------------------------------

def publish(member: Member, **over):
    node, boot = uuid.uuid4(), uuid.uuid4()
    body = {"node_id": str(node), "boot_id": str(boot), "seq": 1, "label": "DESK",
            "jarvis_version": "1", "os": "macOS",
            "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0,
                          "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
            "brains": [], "activity": None, **over}
    return node, boot, member.client.post("/api/v1/network/nodes/self", json=body,
                                          headers=bearer(member.token))


def view(snapshot: dict, node) -> dict:
    return next(n for n in snapshot["nodes"] if n["node_id"] == str(node))


def test_a_node_advertises_its_capabilities_and_an_older_one_is_legacy(tim, frozen):
    node, boot, r = publish(tim, attachments=CAPS)
    assert r.status_code == 200, r.text
    mine = view(r.json(), node)
    assert mine["attachments"] == caps(CAPS)
    observed = datetime.fromisoformat(mine["attachments_observed_at"])
    seen = datetime.fromisoformat(mine["last_seen_at"])
    assert seen - observed == timedelta(milliseconds=CAPS["observed_age_ms"])
    legacy, _, r = publish(tim)
    other = view(r.json(), legacy)
    assert other["attachments"] is None and other["attachments_observed_at"] is None
    # An update without them leaves the node legacy, never the old capabilities.
    again = tim.client.post("/api/v1/network/nodes/self", headers=bearer(tim.token), json={
        "node_id": str(node), "boot_id": str(boot), "seq": 2, "label": "DESK",
        "jarvis_version": "1", "os": "macOS",
        "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0,
                      "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
        "brains": []})
    assert view(again.json(), node)["attachments"] is None


@pytest.mark.parametrize("bad", [
    {**CAPS, "version": 2},
    {**CAPS, "observed_age_ms": 7 * 86_400_000 + 1},
    {**CAPS, "formats": [{"format": "pdf", "operations": ["print"]}]},
    {**CAPS, "formats": [{"format": "pdf", "operations": []}] * 41},
    {**CAPS, "formats": CAPS["formats"] + CAPS["formats"][:1]},
])
def test_an_unbounded_or_unknown_advertisement_is_refused(tim, bad):
    _, _, r = publish(tim, attachments=bad)
    assert r.status_code == 422


def test_a_member_with_two_nodes_shows_each_nodes_own_capabilities(tim):
    full, _, _ = publish(tim, attachments=CAPS)
    small = {**CAPS, "formats": [{"format": "zip", "operations": ["download"]}]}
    narrow, _, r = publish(tim, attachments=small)
    snap = r.json()
    assert view(snap, full)["attachments"] == caps(CAPS)
    assert view(snap, narrow)["attachments"] == caps(small)


# -- task context and supersedes ------------------------------------------------------------

def test_a_task_is_kept_and_an_older_jarvis_is_answered_without_it(tim, alice, pins):
    bundle = ref(31, "2" * 64)
    pins.ready.add(bundle.attachment_id)
    m = send(tim, request(tim, ALICE.id, files=[bundle], task=task_on(bundle)))
    assert m["task"]["primary"]["member_path"] == "summary.pdf"
    (full,) = scan(alice, bundles=True)
    assert full["message"]["task"] == m["task"] and full["receipts"]["openability"] == []
    (legacy,) = scan(alice, bundles=False)
    assert "task" not in legacy["message"] and "supersedes" not in legacy["message"]
    assert "openability" not in legacy["receipts"]
    for key in ("task", "supersedes"):
        legacy["message"].pop(key, None)
    assert mm.AcceptedMessage.model_validate(legacy["message"]).task is None
    got = alice.client.get(f"{PREFIX}/{m['message_id']}", headers=bearer(alice.token)).json()
    assert "task" not in got
    got = alice.client.get(f"{PREFIX}/{m['message_id']}?bundles=1",
                           headers=bearer(alice.token)).json()
    assert got["task"] == m["task"]


def test_a_repair_supersedes_a_file_of_an_earlier_message_in_its_thread(tim, alice, bob, pins):
    old, new = ref(41, "4" * 64), ref(42, "5" * 64)
    pins.ready |= {old.attachment_id, new.attachment_id}
    first = send(tim, request(tim, ALICE.id, files=[old], task=task_on(old)))
    link = mm.Supersedes(message_id=uuid.UUID(first["message_id"]),
                         attachment_id=old.attachment_id, by_attachment_id=new.attachment_id,
                         reason="repair")
    repair = send(tim, request(tim, ALICE.id, files=[new], task=task_on(new),
                               supersedes=[link], reply_to=first))
    assert repair["supersedes"][0]["attachment_id"] == str(old.attachment_id)
    assert repair["thread_id"] == first["thread_id"]

    # Another thread, a file the message never had, or another person's message: refused.
    elsewhere = request(tim, ALICE.id, files=[new], supersedes=[link])
    assert error(tim.post(elsewhere)) == "relation_not_allowed"
    wrong_file = link.model_copy(update={"attachment_id": uuid.UUID(int=99)})
    body = request(tim, ALICE.id, files=[new], supersedes=[wrong_file], reply_to=first)
    assert error(tim.post(body)) == "relation_not_allowed"
    pins.ready.add(ref(43, "6" * 64).attachment_id)
    bobs = send(bob, request(bob, ALICE.id, files=[ref(43, "6" * 64)]))
    theirs = mm.Supersedes(message_id=uuid.UUID(bobs["message_id"]),
                           attachment_id=uuid.UUID(int=43), by_attachment_id=new.attachment_id,
                           reason="repair")
    body = request(tim, ALICE.id, files=[new], supersedes=[theirs], reply_to=first)
    assert error(tim.post(body)) == "relation_not_allowed"


# -- openability receipts -----------------------------------------------------------------

def test_the_recipient_reports_openability_and_the_sender_sees_it(tim, alice, pins):
    bundle = ref(51, "7" * 64)
    pins.ready.add(bundle.attachment_id)
    m = send(tim, request(tim, ALICE.id, files=[bundle]))
    r = openability(alice, m["message_id"], report(bundle, minutes=2))
    assert r.status_code == 200, r.text
    facts = r.json()["facts"][0]
    assert facts["openability"][0]["status"] == "partial"
    assert facts["received_at"] is None and facts["read_at"] is None
    seen = scan(tim, bundles=True)[0]["receipts"]["openability"]
    assert [x["failed_member"] for x in seen] == ["review/summary.pdf"]
    later = openability(alice, m["message_id"],
                        report(bundle, "verified", minutes=8, members_readable=3,
                               failed_operation=None, failed_member=None, reason=None))
    assert later.json()["facts"][0]["openability"][0]["status"] == "verified"
    stale = openability(alice, m["message_id"], report(bundle, "unavailable", minutes=1))
    assert stale.json()["facts"][0]["openability"][0]["status"] == "verified"
    # An older JARVIS reading its receipts gets the messaging.v1 shape.
    legacy = alice.receipt(m["message_id"], "received").json()["facts"][0]
    assert "openability" not in legacy


def test_a_report_on_another_file_hash_or_revision_is_settled_unread(tim, alice, pins):
    bundle = ref(61, "8" * 64)
    pins.ready.add(bundle.attachment_id)
    m = send(tim, request(tim, ALICE.id, files=[bundle]))
    for wrong in (report(bundle, sha256="9" * 64), report(ref(62, "8" * 64)),
                  report(bundle, content_revision=2)):
        r = openability(alice, m["message_id"], wrong)
        assert r.json() == {"facts": [], "not_found": [m["message_id"]]}
    assert scan(tim, bundles=True)[0]["receipts"]["openability"] == []


def test_only_the_recipients_own_nodes_report_openability(tim, alice, pins):
    bundle = ref(71, "a" * 64)
    pins.ready.add(bundle.attachment_id)
    m = send(tim, request(tim, ALICE.id, files=[bundle]))
    r = openability(tim, m["message_id"], report(bundle))
    assert r.json()["not_found"] == [m["message_id"]], "the sender's report is no receipt"
    assert error(openability(alice, m["message_id"], report(bundle), node=tim.node)) == \
        "source_node_not_owned"
