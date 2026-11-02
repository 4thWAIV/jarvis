"""Collaboration bundles end to end over the real team server, MongoDB and
JARVIS's messaging runtime (the harness of tests/test_messaging_integration.py):
a node's attachment capabilities reach the team's network, a message's task
and a repair's supersedes link are accepted and synced, and the recipient's
openability goes back to the sender as a receipt.

The file readers are the readers lane's and are tested on their own: here
the inspection service is the table-driven stand-in
(tests/bundle_transport_fakes.FakeInspection), reporting to the same
observer, so everything this lane owns runs for real.

Needs TEAM_SERVER_TEST_MONGODB_URI (unset: skipped, as in CI's default
selection; run it locally against a MongoDB, as the messaging integration
scenarios are).
"""
from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import pytest

from tests.test_messaging_integration import (  # noqa: F401  (fixtures)
    MATT, P, TIM, Raw, _attach, here, mongo, nodes, server, team_server)

from jarvis import bundle_capabilities, bundle_status, bundle_tasks
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import member_id
from jarvis.bundle_inspection import AttachmentKey, Owner
from tests import bundle_transport_fakes as bf
from tests.test_bundle_capabilities import Health

FULL = {"pdf": Health(True, frozenset({"read_text", "render"})),
        "markdown": Health(True, frozenset({"read_text"}))}
MEMBERS = (("review/summary.pdf", "pdf", "readable"),
           ("review/sources.md", "markdown", "readable"))
WRAPPED = bf.zip_bytes({"review/summary.pdf": b"%PDF-1.7 two pages",
                        "review/sources.md": b"# Sources\n"})


def advertising(monkeypatch, health=FULL) -> None:
    monkeypatch.setattr(bundle_capabilities, "advertiser",
                        bundle_capabilities.Advertiser(health=lambda: health))


def stand_in(tim, specs: dict) -> bf.FakeInspection:
    """The inspection service the runtime built, replaced by the stand-in
    reporting to the same observer (bundle_status.observe)."""
    fake = bf.FakeInspection(specs, observer=bundle_status.observe)
    tim.rt.received = fake
    tim.rt.preflight.inspection = fake
    tim.rt.tasks.drafts = tim.rt.tasks.messages = fake
    tim.rt.openability.members = bundle_status.members_of(fake)
    return fake


async def exchange(tim) -> None:
    """One network exchange, as the loop makes it: this node's update out,
    the team's snapshot back and stored."""
    from jarvis import team_api, team_client
    team_client.connection._node_update = team_api.node_update
    await team_client.connection.exchange_once()
    assert team_client.connection.network.raw is not None


async def send_reviewed(tim, draft_id) -> tuple[dict, str]:
    """Review (the read-back he hears), his Send, and delivery."""
    current = tim.rt.store.draft(*tim.ns, UUID(str(draft_id)))
    reviewed = await tim.review({"message_id": str(draft_id),
                                 "draft_revision": current.draft_revision})
    readback = tim.server._captures[reviewed["capture_id"]]["text"]
    await tim.send(reviewed)
    await tim.tick()
    return reviewed, readback


@pytest.mark.asyncio
async def test_self_message_task_openability_and_repair(server, team_server, mongo,
                                                        monkeypatch):
    db = mongo[team_server.dbname]
    advertising(monkeypatch)
    tim = await here(server, team_server, TIM, monkeypatch)
    assert tim.rt.delivery.bundles_offered()
    await exchange(tim)
    snapshot = db.network_nodes.find_one({"owner_github_id": TIM.id})
    assert {f["format"] for f in snapshot["attachments"]["formats"]} >= {"zip", "pdf"}

    draft = await _attach(tim, await tim.draft(TIM, "Please review the summary."),
                          "review.zip", WRAPPED)
    zip_ref = tim.rt.store.draft(*tim.ns, UUID(draft["message_id"])).attachments[0]
    fake = stand_in(tim, {zip_ref.attachment_id: bf.Spec(zip_ref, members=MEMBERS,
                                                         wrapper="review")})
    task = bf.task_on(zip_ref).model_dump(mode="json")
    bundle_tasks.set_task(*tim.ns, UUID(draft["message_id"]), draft["draft_revision"], task)
    readiness = tim.rt.preflight.preflight(*tim.ns, UUID(draft["message_id"]))
    assert readiness.readiness == "ready" and readiness.recipient_github_id == TIM.id

    reviewed, readback = await send_reviewed(tim, draft["message_id"])
    assert "Task: Notes on the summary." in readback
    kept = tim.rt.preflight.recorded(*tim.ns, UUID(draft["message_id"]),
                                     reviewed["send"]["content_revision"])
    assert kept is not None and kept.readiness == "ready"
    body = db.messages.find_one({"_id": draft["message_id"]})["body"]
    assert body["task"]["primary"]["member_path"] == "review/summary.pdf"

    # Received back on this node: the owner opens it; the download works and
    # the supporting file's reader fails.
    m = tim.rt.store.message(*tim.ns, UUID(draft["message_id"])).message
    key = AttachmentKey(m.message_id, zip_ref.attachment_id)
    me = Owner(*tim.ns)
    fake.specs[zip_ref.attachment_id] = bf.Spec(zip_ref, wrapper="review", members=(
        ("review/summary.pdf", "pdf", "readable"), ("review/sources.md", "markdown",
                                                    "unsupported")))
    await fake.open(me, key)
    fake.read_member(me, key, member_id(zip_ref.sha256, "review/summary.pdf"))
    with pytest.raises(Exception):
        fake.read_member(me, key, member_id(zip_ref.sha256, "review/sources.md"))
    await tim.tick(2)
    receipts = db.message_receipts.find_one({"_id": draft["message_id"]})["body"]
    (sent,) = receipts["openability"]
    assert (sent["status"], sent["failed_member"], sent["failed_operation"]) == \
        ("partial", "review/sources.md", "read_text")
    assert receipts["read_at"] is None, "openability is never a read"
    local = tim.rt.store.message(*tim.ns, m.message_id).receipts
    assert [r.status for r in local.openability] == ["partial"]

    review = bundle_tasks.review_state(*tim.ns, m.thread_id)
    assert [(i.role, i.state) for i in review.items] == [("primary", "read"),
                                                         ("supporting", "unreadable")]
    assert review.progress == "evidence_unavailable"

    # The repair: the same files without the wrapper folder, in the thread.
    repair = bundle_tasks.prepare_repair(*tim.ns, m.message_id, zip_ref.attachment_id,
                                         "flatten_wrapper")
    (flat,) = repair.attachments
    fake.specs[flat.attachment_id] = bf.Spec(flat, members=(
        ("summary.pdf", "pdf", "readable"), ("sources.md", "markdown", "readable")))
    _, readback = await send_reviewed(tim, repair.message_id)
    assert "review (flattened).zip replaces the earlier copy, which is kept." in readback
    accepted = db.messages.find_one({"_id": str(repair.message_id)})["body"]
    assert accepted["supersedes"][0]["attachment_id"] == str(zip_ref.attachment_id)
    assert accepted["thread_id"] == str(m.thread_id)
    assert accepted["task"]["primary"]["member_path"] == "summary.pdf"
    resumed = bundle_tasks.review_state(*tim.ns, m.thread_id)
    assert [(i.path, i.state) for i in resumed.items] == [("summary.pdf", "read"),
                                                          ("sources.md", "unread")]
    assert db.messages.count_documents({}) == 2, "the old message is kept"


@pytest.mark.asyncio
async def test_two_nodes_with_different_capabilities_and_the_report_reaches_the_sender(
        server, team_server, mongo, nodes, monkeypatch):
    db = mongo[team_server.dbname]
    advertising(monkeypatch)
    tim = await here(server, team_server, TIM, monkeypatch)
    await team_server.member(MATT)
    await tim.rt.local.recipients()
    matt = await nodes(team_server, MATT, "matt")          # a JARVIS process: advertises itself
    desk = Raw(team_server, MATT, await team_server.device(MATT))
    legacy = Raw(team_server, MATT, await team_server.device(MATT))
    await legacy.publish()                                 # an older JARVIS: no capabilities
    status, _ = await desk.connection.call("POST", "/api/v1/network/nodes/self", {
        "node_id": str(desk.node), "boot_id": str(uuid.uuid4()), "seq": 1, "label": "DESK",
        "jarvis_version": "1", "os": "macOS",
        "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0,
                      "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
        "brains": [], "activity": None,
        "attachments": bundle_capabilities.capabilities_from(FULL, 0)})
    assert status == 200
    deadline = asyncio.get_running_loop().time() + 30
    while db.network_nodes.count_documents({"owner_github_id": MATT.id,
                                            "attachments": {"$ne": None}}) < 2:
        assert asyncio.get_running_loop().time() < deadline, "Matt's JARVIS never advertised"
        await asyncio.sleep(0.25)
    await exchange(tim)

    draft = await _attach(tim, await tim.draft(MATT, "Please review the summary."),
                          "review.zip", WRAPPED)
    zip_ref = tim.rt.store.draft(*tim.ns, UUID(draft["message_id"])).attachments[0]
    fake = stand_in(tim, {zip_ref.attachment_id: bf.Spec(zip_ref, members=MEMBERS,
                                                         wrapper="review")})
    bundle_tasks.set_task(*tim.ns, UUID(draft["message_id"]), draft["draft_revision"],
                          bf.task_on(zip_ref).model_dump(mode="json"))
    result = tim.rt.preflight.preflight(*tim.ns, UUID(draft["message_id"]))
    fits = {f.label: (f.capabilities, f.fit) for f in result.nodes}
    assert len(result.nodes) == 3, "every one of Matt's nodes, none merged"
    assert fits["DESK"] == ("advertised", "ready") and fits["RAW NODE"] == ("legacy",
                                                                           "unknown")
    process = [f for f in result.nodes if f.label not in ("DESK", "RAW NODE")]
    assert process[0].capabilities == "advertised", "the process's own node update carried it"
    assert result.readiness == "ready"

    await send_reviewed(tim, draft["message_id"])
    assert tim.send_state(draft["message_id"]).state == "accepted"
    # Matt's desk opened it and reports through the real transport.
    report = wire.OpenabilityReport(attachment_id=zip_ref.attachment_id, sha256=zip_ref.sha256,
                                    content_revision=1, status="verified", members_total=2,
                                    members_readable=2, checked_at=bf.T0)
    answer = await desk.t.receipts(wire.ReceiptBatch(node_id=desk.node, receipts=[
        wire.ReceiptItem(message_id=UUID(draft["message_id"]), kind="openability",
                         openability=report)]))
    assert answer.facts[0].openability[0].status == "verified"
    await tim.tick()
    seen = bundle_status.status(*tim.ns, UUID(draft["message_id"]), zip_ref.attachment_id)
    assert seen["status"] == "verified"
    # Matt's JARVIS process, which asked for the bundle fields, holds the task.
    theirs = await matt.until(P, lambda s: any(
        m["message"]["message_id"] == draft["message_id"]
        for i in s["items"] for m in i["messages"]), "Matt's JARVIS holds the message")
    held = next(m for i in theirs["items"] for m in i["messages"]
                if m["message"]["message_id"] == draft["message_id"])
    assert held["message"]["task"]["outcome_kind"] == "notes"
