"""Collaboration bundles' transport on this JARVIS, over the real store,
file snapshots, approval guard and delivery loop, with the contract's fake
team server: the recipient's openability record and its receipts, the
sender's preflight across the recipient's nodes, the task on a draft and
its read-back, companions, repair and the review checklist."""
from __future__ import annotations

import asyncio
import json
import zipfile
from datetime import timedelta
from uuid import UUID

import pytest

from jarvis import bundle_capabilities, bundle_preflight, bundle_status, bundle_tasks
from jarvis import message_approvals, messaging_api, messaging_files
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import member_id
from jarvis.bundle_inspection import AttachmentKey, Observation, Owner
from jarvis.messaging_client import Delivery
from jarvis.messaging_store import SqliteMessagingStore
from tests import bundle_transport_fakes as bf
from tests.bundle_transport_fakes import MATT, SERVER, T0, TIM
from tests.fixtures import messaging_fakes as fakes
from tests.test_bundle_capabilities import Health
from tests.test_messaging_client import Connection

BUNDLES = [wire.CAPABILITY, wire.BUNDLES_CAPABILITY]


class Side:
    """One person's JARVIS node: store, private copies, guard, delivery and
    the bundle services, over the fake team server."""

    def __init__(self, tmp_path, serve, owner: int, node: int, clock, specs=None):
        self.owner, self.clock, self.serve = owner, clock, serve
        self.node = UUID(int=node)
        self.store = SqliteMessagingStore(tmp_path / f"{owner}-{node}.db")
        self.transport = serve.pair(owner, self.node)
        self.files = messaging_files.FileSnapshots(
            self.store, lambda: self.transport, ids=fakes.SeqIds(start=node * 1000),
            snapshots_dir=tmp_path / f"snap-{node}", downloads_dir=tmp_path / f"down-{node}",
            quarantine=lambda path: None)
        self.files.sleep = self._no_wait
        self.waits: list[float] = []
        self.specs = specs if specs is not None else {}
        self.status = bundle_status.OpenabilityStatus(
            self.store, clock=clock,
            members=lambda o, k: {member_id(self.specs[k.attachment_id].ref.sha256, m[0]): m[0]
                                  for m in self.specs[k.attachment_id].members})
        self.guard = message_approvals.MessageApprovals(
            self.store, self.files, ids=fakes.SeqIds(start=node * 2000), clock=clock,
            bundles=lambda: self.delivery.bundles_offered())
        self.captures = fakes.ReviewCaptures()
        self.delivery = Delivery(store=self.store, connection=Connection(owner),
                                 guard=self.guard, snapshots=self.files,
                                 node_id=lambda: self.node, clock=clock,
                                 rand=fakes.SeqRandom(),
                                 transport_for=lambda c, p: self.transport,
                                 openability=self.status)
        self.inspection = bf.FakeInspection(self.specs, observer=self.observe)
        self.preflight = bundle_preflight.SenderPreflight(
            self.store, self.inspection, network=lambda: self.network, clock=clock)
        self.network: dict = {"nodes": []}
        self.local = messaging_api.LocalMessaging(
            store=self.store, delivery=self.delivery, guard=self.guard,
            reviews=message_approvals.Reviews(self.guard, self.captures,
                                              lambda: self.local.members()),
            snapshots=self.files, ids=fakes.SeqIds(start=node * 3000), clock=clock,
            preflight=self.preflight)
        self.tasks = bundle_tasks.BundleTasks(self.store, self.local, self.files,
                                              drafts=self.inspection, messages=self.inspection,
                                              status=self.status.status)

    async def _no_wait(self, seconds):
        self.waits.append(seconds)

    def observe(self, o: Observation) -> None:
        self.status.observe(o)
        self.tasks.observe(o)

    @property
    def me(self) -> Owner:
        return Owner(SERVER, self.owner)

    def tick(self, times: int = 1):
        for _ in range(times):
            asyncio.run(self.delivery.tick())

    def attach(self, draft: ports.Draft, name: str, data: bytes) -> ports.Draft:
        async def chunks():
            yield data
        ref = asyncio.run(self.files.snapshot(SERVER, self.owner, draft.message_id, name,
                                              "application/zip", chunks()))
        return self.local.save_draft(draft.message_id, ports.DraftSave.keeping(
            draft, attachment_ids=[*(a.attachment_id for a in draft.attachments),
                                   ref.attachment_id]))

    def send(self, draft: ports.Draft) -> wire.AcceptedMessage:
        """Review, the owner's yes, and delivery: what goes is what he heard."""
        reviewed = asyncio.run(self.local.review(draft.message_id, ports.DraftAction(
            expected_draft_revision=draft.draft_revision)))
        self.guard.approve(SERVER, self.owner, draft.message_id,
                           reviewed.send.content_revision, reviewed.send.content_digest,
                           self.node, "gui")
        self.tick()
        held = self.store.message(SERVER, self.owner, draft.message_id)
        assert held is not None, self.store.draft(SERVER, self.owner, draft.message_id).send
        return held.message


@pytest.fixture
def clock():
    return fakes.FixedClock(T0)


@pytest.fixture
def serve(clock):
    s = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=clock)
    s.capabilities = list(BUNDLES)
    return s


def obs(side: Side, message: wire.AcceptedMessage, r: wire.AttachmentRef, op: str,
        member=None, support="readable", reason=None) -> None:
    side.observe(Observation(side.me, AttachmentKey(message.message_id, r.attachment_id),
                             r.sha256, op, member, support, reason))


def report(side: Side, message, r) -> dict:
    return side.status.status(SERVER, side.owner, message.message_id, r.attachment_id)


def owed(side: Side) -> list[str]:
    return [o.kind for o in side.store.receipts_owed(SERVER, side.owner)]


# -- the recipient's openability ---------------------------------------------------------

ZIP = bf.ref(31, "review.zip")
MEMBERS = (("review/summary.pdf", "pdf", "readable"), ("review/sources.md", "markdown",
                                                       "readable"),
           ("review/run.sh", "text", "readable"))


def received(tmp_path, serve, clock, *, task=True, members=MEMBERS):
    matt = Side(tmp_path, serve, MATT, 2, clock,
                specs={ZIP.attachment_id: bf.Spec(ZIP, members=members, wrapper="review")})
    m = bf.accepted(TIM, MATT, [ZIP], n=1, task=bf.task_on(ZIP) if task else None)
    bf.hold(matt.store, MATT, m)
    return matt, m


def test_nothing_is_reported_before_the_owner_looks(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    assert report(matt, m, ZIP) == {}
    obs(matt, m, ZIP, "list_archive", support="blocked", reason="not_local")
    assert report(matt, m, ZIP) == {} and "openability" not in owed(matt)


def test_a_failed_download_is_unavailable_and_wrong_bytes_corrupt(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    obs(matt, m, ZIP, "download", support="blocked", reason="not_local")
    assert report(matt, m, ZIP)["status"] == "unavailable"
    assert report(matt, m, ZIP)["failed_operation"] == "download"
    obs(matt, m, ZIP, "download", support="blocked", reason="corrupt")
    assert report(matt, m, ZIP)["status"] == "corrupt"
    assert owed(matt).count("openability") == 1, "owed once per message, however often it moves"


def test_ready_then_partial_names_the_first_failed_member_and_never_its_text(
        tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    asyncio.run(matt.inspection.open(matt.me, AttachmentKey(m.message_id, ZIP.attachment_id)))
    first = report(matt, m, ZIP)
    assert (first["status"], first["members_total"], first["members_readable"]) == \
        ("ready", 2, 2), "the task's primary and supporting members are the required ones"
    sources = member_id(ZIP.sha256, "review/sources.md")
    obs(matt, m, ZIP, "read_text", sources, "unsupported", "reader_unavailable")
    partial = report(matt, m, ZIP)
    assert partial["status"] == "partial" and partial["failed_member"] == "review/sources.md"
    assert partial["failed_operation"] == "read_text" and partial["reason"] == "reader_unavailable"
    assert partial["members_readable"] == 1
    text = json.dumps(partial)
    assert "text of" not in text and "/Users" not in text


def test_every_required_member_read_is_verified_and_an_unreadable_primary_unsupported(
        tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    key = AttachmentKey(m.message_id, ZIP.attachment_id)
    asyncio.run(matt.inspection.open(matt.me, key))
    for path in ("review/summary.pdf", "review/sources.md"):
        matt.inspection.read_member(matt.me, key, member_id(ZIP.sha256, path))
    assert report(matt, m, ZIP)["status"] == "verified"

    other, m2 = received(tmp_path / "b", serve, clock, members=(
        ("review/summary.pdf", "pdf", "unsupported"), ("review/sources.md", "markdown",
                                                       "readable")))
    key2 = AttachmentKey(m2.message_id, ZIP.attachment_id)
    asyncio.run(other.inspection.open(other.me, key2))
    with pytest.raises(Exception):
        other.inspection.read_member(other.me, key2, member_id(ZIP.sha256, "review/summary.pdf"))
    assert report(other, m2, ZIP)["status"] == "unsupported"


def test_without_a_task_every_file_is_required(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock, task=False)
    asyncio.run(matt.inspection.open(matt.me, AttachmentKey(m.message_id, ZIP.attachment_id)))
    assert report(matt, m, ZIP)["members_total"] == 3
    run = member_id(ZIP.sha256, "review/run.sh")
    obs(matt, m, ZIP, "read_text", run, "blocked", "link")
    assert report(matt, m, ZIP)["failed_member"] == "review/run.sh"


def test_the_receipt_goes_only_to_a_server_offering_bundles_and_reaches_the_sender(
        tmp_path, serve, clock):
    tim = Side(tmp_path, serve, TIM, 1, clock)
    matt = Side(tmp_path, serve, MATT, 2, clock)
    draft = tim.attach(bf.draft_for(tim.local, MATT), "review.zip",
                       bf.zip_bytes({"review/summary.pdf": b"%PDF-1.7 fake"}))
    sent = tim.send(draft)
    matt.tick()
    held = matt.store.message(SERVER, MATT, sent.message_id).message
    zip_ref = held.attachments[0]
    matt.specs[zip_ref.attachment_id] = bf.Spec(zip_ref, members=(
        ("review/summary.pdf", "pdf", "readable"),))

    serve.capabilities = [wire.CAPABILITY]               # an older team server
    matt.delivery._capabilities.clear()
    asyncio.run(matt.inspection.open(matt.me, AttachmentKey(held.message_id,
                                                            zip_ref.attachment_id)))
    matt.tick()
    assert "openability" in owed(matt), "kept, and local, while the server cannot take it"
    assert serve.receipts[held.message_id].openability == []

    serve.capabilities = list(BUNDLES)
    matt.delivery._capabilities.clear()
    matt.tick()
    assert "openability" not in owed(matt)
    assert [r.status for r in serve.receipts[held.message_id].openability] == ["ready"]
    tim.tick()
    seen = report(tim, sent, zip_ref)
    assert seen["status"] == "ready" and seen["sha256"] == zip_ref.sha256
    assert serve.receipts[held.message_id].read_at is None, "openability is never a read"


def test_a_report_that_changes_after_it_was_sent_is_owed_again(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    asyncio.run(matt.inspection.open(matt.me, AttachmentKey(m.message_id, ZIP.attachment_id)))
    sent = matt.status.reports(SERVER, MATT, m.message_id)
    matt.store.settle_receipts(SERVER, MATT, matt.store.receipts_owed(SERVER, MATT),
                               wire.ReceiptBatchResult(facts=[]))
    obs(matt, m, ZIP, "read_text", member_id(ZIP.sha256, "review/sources.md"), "blocked",
        "link")
    matt.store.settle_receipts(SERVER, MATT, matt.store.receipts_owed(SERVER, MATT),
                               wire.ReceiptBatchResult(facts=[]))
    matt.status.reported(SERVER, MATT, m.message_id, sent)
    assert owed(matt) == ["openability"]


def test_the_sender_owes_nothing_for_its_own_look(tmp_path, serve, clock):
    tim = Side(tmp_path, serve, TIM, 1, clock, specs={ZIP.attachment_id: bf.Spec(
        ZIP, members=MEMBERS)})
    m = bf.accepted(TIM, MATT, [ZIP], n=3)
    bf.hold(tim.store, TIM, m)
    obs(tim, m, ZIP, "download")
    assert "openability" not in owed(tim)


# -- the sender's preflight -----------------------------------------------------------------

def caps(*formats, runs=("conversation", "agent", "page")) -> dict:
    health = {fmt: Health(True, frozenset(ops)) for fmt, ops in formats}
    advert = bundle_capabilities.capabilities_from(health, 0)
    if runs is None:
        advert["work_packages"] = None
    return advert


FULL = caps(("pdf", {"read_text", "render"}), ("markdown", {"read_text"}),
            ("text", {"read_text"}))
TEXT_ONLY = caps(("pdf", {"read_text"}), ("markdown", {"read_text"}), ("text", {"read_text"}))


def drafted(tmp_path, serve, clock, *, members=MEMBERS, spec=None, task=True, runs=None):
    tim = Side(tmp_path, serve, TIM, 1, clock)
    draft = tim.attach(bf.draft_for(tim.local, MATT), "review.zip", b"PK fake zip")
    r = draft.attachments[0]
    tim.specs[r.attachment_id] = spec(r) if spec else bf.Spec(r, members=members,
                                                              wrapper="review", runs=runs)
    if task:
        draft = tim.tasks.set_task(SERVER, TIM, draft.message_id, draft.draft_revision,
                                   bf.task_on(r).model_dump(mode="json"))
    return tim, draft, r


def check(tim: Side, draft) -> object:
    return tim.preflight.preflight(SERVER, TIM, draft.message_id)


def test_ready_when_one_of_the_recipients_nodes_can_do_everything(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=TEXT_ONLY, label="LAPTOP"),
                             bf.node(MATT, 2, attachments=FULL, label="DESK"),
                             bf.node(TIM, 3, attachments=FULL)]}
    result = check(tim, draft)
    assert result.readiness == "ready" and result.recipient_github_id == MATT
    fits = {f.label: (f.capabilities, f.fit, [m.operation for m in f.missing])
            for f in result.nodes}
    assert fits == {"LAPTOP": ("advertised", "partial", ["render"]),
                    "DESK": ("advertised", "ready", [])}, "each node judged on its own"
    assert result.gaps == []


def test_partial_names_the_action_the_sender_can_take(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=TEXT_ONLY)]}
    result = check(tim, draft)
    assert result.readiness == "partial"
    assert [g.action for g in result.gaps] == ["add_page_images"]
    assert result.gaps[0].requirement.path == "review/summary.pdf"


def test_two_nodes_together_are_never_counted_as_one(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    renders_only = caps(("pdf", {"render"}), ("markdown", {"read_text"}),
                        ("text", {"read_text"}))
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=TEXT_ONLY),
                             bf.node(MATT, 2, attachments=renders_only)]}
    assert check(tim, draft).readiness == "partial"


def test_legacy_stale_and_offline_nodes_are_unknown(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    tim.network = {"nodes": [
        bf.node(MATT, 1),
        bf.node(MATT, 2, attachments=FULL,
                checked=timedelta(seconds=bundle_capabilities.CAPABILITY_FRESH_SEC + 5)),
        bf.node(MATT, 3, attachments=FULL, presence="offline")]}
    result = check(tim, draft)
    assert result.readiness == "unknown"
    assert [f.capabilities for f in result.nodes] == ["legacy", "stale", "offline"]
    assert all(f.fit == "unknown" for f in result.nodes)
    tim.network = None
    assert check(tim, draft).readiness == "unknown"


def test_a_legacy_recipient_of_a_wrapped_package_is_offered_the_flattened_copy(
        tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock, task=False, runs="conversation")
    tim.network = {"nodes": [bf.node(MATT, 1)]}
    result = check(tim, draft)
    assert result.readiness == "unknown" and [g.action for g in result.gaps] == \
        ["flatten_wrapper"]
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=caps(("pdf", {"read_text"}),
                                                                ("markdown", {"read_text"}),
                                                                ("text", {"read_text"}),
                                                                runs=None))]}
    assert [g.action for g in check(tim, draft).gaps] == ["ask_recipient_to_update"]


def test_blocked_when_it_cannot_be_inspected_here_or_the_primary_has_no_reader(
        tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock, spec=lambda r: bf.Spec(
        r, support="blocked", reason="encrypted"))
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=FULL)]}
    assert check(tim, draft).readiness == "blocked"
    tim2, draft2, _ = drafted(tmp_path / "b", serve, clock, members=(
        ("review/summary.pdf", "pdf", "unsupported"), ("review/sources.md", "markdown",
                                                       "readable")))
    tim2.network = tim.network
    result = check(tim2, draft2)
    assert result.readiness == "blocked"
    assert [p.path for p in result.local_problems] == ["review/summary.pdf"]


def test_the_check_is_kept_with_the_revision_reviewed_and_a_repackage_needs_another(
        tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    tim.network = {"nodes": [bf.node(MATT, 1, attachments=FULL)]}
    asyncio.run(tim.delivery.availability())
    reviewed = asyncio.run(tim.local.review(draft.message_id, ports.DraftAction(
        expected_draft_revision=draft.draft_revision)))
    kept = tim.preflight.recorded(SERVER, TIM, draft.message_id,
                                  reviewed.send.content_revision)
    assert kept.readiness == "ready" and kept.capability_digest.startswith("sha256:")
    with pytest.raises(ValueError):
        tim.preflight.record_for_approval(SERVER, TIM, draft.message_id, 99, kept)
    again = tim.tasks.add_companions(SERVER, TIM, draft.message_id, reviewed.draft_revision,
                                     r.attachment_id, ["file_index"])
    assert again.send.content_revision == reviewed.send.content_revision + 1
    assert again.send.state == "draft", "a companion is an edit: reviewed again"


# -- the task on a draft ---------------------------------------------------------------------

def test_a_task_names_only_this_drafts_files_by_hash(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock, task=False)
    foreign = bf.task_on(bf.ref(99)).model_dump(mode="json")
    with pytest.raises(messaging_api.Refused) as e:
        tim.tasks.set_task(SERVER, TIM, draft.message_id, draft.draft_revision, foreign)
    assert e.value.code == "invalid_request"
    with pytest.raises(messaging_api.Refused) as e:
        tim.tasks.set_task(SERVER, TIM, draft.message_id, draft.draft_revision + 5,
                           bf.task_on(r).model_dump(mode="json"))
    assert e.value.code == "revision_conflict"
    widened = {**bf.task_on(r).model_dump(mode="json"), "reply_audience": "everyone"}
    with pytest.raises(messaging_api.Refused):
        tim.tasks.set_task(SERVER, TIM, draft.message_id, draft.draft_revision, widened)


def test_the_task_goes_and_is_read_back_only_where_the_server_takes_it(tmp_path, serve,
                                                                         clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    asyncio.run(tim.delivery.availability())
    reviewed = asyncio.run(tim.local.review(draft.message_id, ports.DraftAction(
        expected_draft_revision=draft.draft_revision)))
    readback = list(tim.captures.open.values())[-1]["text"]
    assert "Task: Notes on the summary." in readback and "Answer to you only." in readback
    assert "Primary: review/summary.pdf in review.zip." in readback
    sent = tim.send(reviewed)
    assert sent.task is not None and sent.task.primary.member_path == "review/summary.pdf"

    old = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=clock)
    tim2, draft2, _ = drafted(tmp_path / "old", old, clock)
    asyncio.run(tim2.delivery.availability())
    reviewed2 = asyncio.run(tim2.local.review(draft2.message_id, ports.DraftAction(
        expected_draft_revision=draft2.draft_revision)))
    assert ports.TASK_NOT_CARRIED in list(tim2.captures.open.values())[-1]["text"]
    sent2 = tim2.send(reviewed2)
    assert sent2.task is None, "an older team server gets the message without its task"


def test_a_server_change_between_review_and_yes_needs_a_new_review(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    asyncio.run(tim.delivery.availability())
    reviewed = asyncio.run(tim.local.review(draft.message_id, ports.DraftAction(
        expected_draft_revision=draft.draft_revision)))
    serve.capabilities = [wire.CAPABILITY]
    tim.delivery._capabilities.clear()
    asyncio.run(tim.delivery.availability())
    with pytest.raises(ports.NotApproved):
        tim.guard.approve(SERVER, TIM, draft.message_id, reviewed.send.content_revision,
                          reviewed.send.content_digest, tim.node, "gui")


def test_removing_a_file_takes_it_out_of_the_task(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    saved = tim.local.save_draft(draft.message_id, ports.DraftSave.keeping(
        draft, attachment_ids=[], text="Never mind the file."))
    assert saved.task is not None and saved.task.primary is None and saved.task.supporting == []


# -- companions and repair ---------------------------------------------------------------------

def test_companions_go_beside_the_original_named_for_what_they_are(tmp_path, serve, clock):
    tim, draft, r = drafted(tmp_path, serve, clock)
    after = tim.tasks.add_companions(SERVER, TIM, draft.message_id, draft.draft_revision,
                                     r.attachment_id, ["pdf_text", "page_images", "file_index"])
    names = [a.filename for a in after.attachments]
    assert names == ["review.zip", "review (text).txt", "review (page images).zip",
                     "review (files).txt"]
    text = tim.files.snapshot_path(after.attachments[1].attachment_id).read_text()
    assert "== review/summary.pdf ==" in text and "[page 1]" in text
    with zipfile.ZipFile(tim.files.snapshot_path(after.attachments[2].attachment_id)) as z:
        assert z.namelist() == ["page-001.png", "page-002.png"], "the primary's pages"
    index = tim.files.snapshot_path(after.attachments[3].attachment_id).read_text()
    assert "review/run.sh\t1000\ttext\tyes" in index
    with pytest.raises(messaging_api.Refused):
        tim.tasks.add_companions(SERVER, TIM, draft.message_id, after.draft_revision,
                                 r.attachment_id, ["file_index"] * 1 + ["nonsense"])


WRAPPED = {"review/summary.pdf": b"%PDF-1.7 two pages", "review/sources.md": b"# Sources\n",
           "review/package.json": b'{"name": "x"}', "__MACOSX/review/._summary.pdf": b"meta"}


def sent_bundle(tmp_path, serve, clock, files=WRAPPED):
    tim = Side(tmp_path, serve, TIM, 1, clock)
    matt = Side(tmp_path, serve, MATT, 2, clock)
    draft = tim.attach(bf.draft_for(tim.local, MATT), "review.zip", bf.zip_bytes(files))
    r = draft.attachments[0]
    tim.specs[r.attachment_id] = bf.Spec(r, members=MEMBERS, wrapper="review")
    draft = tim.tasks.set_task(SERVER, TIM, draft.message_id, draft.draft_revision,
                               bf.task_on(r).model_dump(mode="json"))
    asyncio.run(tim.delivery.availability())
    return tim, matt, tim.send(draft), r


def test_a_flattened_repair_replies_in_the_thread_and_supersedes_the_old_file(
        tmp_path, serve, clock):
    tim, matt, failed, old = sent_bundle(tmp_path, serve, clock)
    repair = tim.tasks.prepare_repair(SERVER, TIM, failed.message_id, old.attachment_id,
                                      "flatten_wrapper")
    assert (repair.thread_id, repair.in_reply_to_message_id, repair.recipient_github_id) == \
        (failed.thread_id, failed.message_id, MATT)
    (new,) = repair.attachments
    assert new.filename == "review (flattened).zip"
    assert repair.supersedes == [wire.Supersedes(message_id=failed.message_id,
                                                 attachment_id=old.attachment_id,
                                                 by_attachment_id=new.attachment_id,
                                                 reason="repair")]
    assert repair.task.primary == wire.ArtifactRef(attachment_id=new.attachment_id,
                                                   sha256=new.sha256, member_path="summary.pdf")
    assert [r.member_path for r in repair.task.supporting] == ["sources.md"]
    with zipfile.ZipFile(tim.files.snapshot_path(new.attachment_id)) as z:
        assert sorted(z.namelist()) == ["package.json", "sources.md", "summary.pdf"]
        assert z.read("summary.pdf") == WRAPPED["review/summary.pdf"]
    assert tim.store.message(SERVER, TIM, failed.message_id) is not None, "the old one kept"
    sent = tim.send(repair)
    assert sent.supersedes[0].attachment_id == old.attachment_id
    matt.tick()
    assert matt.store.message(SERVER, MATT, sent.message_id).message.supersedes == \
        sent.supersedes, "the recipient sees which file it replaces"


def test_a_repair_waits_for_an_unsent_reply_in_the_thread(tmp_path, serve, clock):
    tim, matt, failed, old = sent_bundle(tmp_path, serve, clock)
    tim.local.create_draft(ports.DraftSave(thread_id=failed.thread_id,
                                           in_reply_to_message_id=failed.message_id,
                                           recipient_github_id=MATT, text="Half written."))
    with pytest.raises(messaging_api.Refused) as e:
        tim.tasks.prepare_repair(SERVER, TIM, failed.message_id, old.attachment_id,
                                 "flatten_wrapper")
    assert e.value.code == "revision_conflict"


def test_a_zip_with_no_wrapper_is_not_flattened_and_leaves_no_draft(tmp_path, serve, clock):
    tim, matt, failed, old = sent_bundle(tmp_path, serve, clock,
                                         files={"summary.pdf": b"%PDF", "notes.md": b"x"})
    with pytest.raises(Exception):
        tim.tasks.prepare_repair(SERVER, TIM, failed.message_id, old.attachment_id,
                                 "flatten_wrapper")
    item = tim.store.item(SERVER, TIM, failed.thread_id)
    assert [d for d in item.drafts if d.send.state == "draft"] == []


def test_only_the_sender_repairs_and_companions_follow_the_recipients_report(
        tmp_path, serve, clock):
    tim, matt, failed, old = sent_bundle(tmp_path, serve, clock)
    with pytest.raises(messaging_api.Refused):
        matt.tasks.prepare_repair(SERVER, MATT, failed.message_id, old.attachment_id,
                                  "companions")
    tim.status.status = lambda *a: {"failed_operation": "render"}
    tim.tasks.status = tim.status.status
    repair = tim.tasks.prepare_repair(SERVER, TIM, failed.message_id, old.attachment_id,
                                      "companions")
    assert [a.filename for a in repair.attachments] == ["review (page images).zip"]
    assert repair.supersedes == [] and repair.task.primary is None
    assert repair.task.requested_outcome == "Notes on the summary."


# -- the recipient's review ----------------------------------------------------------------------

def test_the_checklist_comes_from_the_task_and_is_marked_by_what_was_read(
        tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock, members=MEMBERS + (
        ("review/BRIEF.md", "markdown", "readable"),))
    state = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    assert [(i.path, i.role, i.state) for i in state.items] == [
        ("review/summary.pdf", "primary", "unread"), ("review/sources.md", "supporting", "unread"),
        ("review/BRIEF.md", "brief", "unread")]
    assert state.progress is None
    key = AttachmentKey(m.message_id, ZIP.attachment_id)
    matt.inspection.read_member(matt.me, key, member_id(ZIP.sha256, "review/summary.pdf"))
    state = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    assert state.items[0].state == "read" and state.progress == "file_ready"
    obs(matt, m, ZIP, "read_text", member_id(ZIP.sha256, "review/sources.md"), "blocked",
        "link")
    state = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    assert state.items[1].state == "unreadable" and state.progress == "evidence_unavailable"
    obs(matt, m, ZIP, "read_text", member_id(ZIP.sha256, "review/summary.pdf"), "blocked",
        "budget")
    assert matt.tasks.review_state(SERVER, MATT, m.thread_id).items[0].state == "read"


def test_a_partial_scope_is_labelled_and_a_stale_change_refused(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    state = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    scoped = matt.tasks.set_scope(SERVER, MATT, m.thread_id, state.revision, "partial")
    assert (scoped.scope, scoped.progress) == ("partial", "review_in_progress")
    assert scoped.revision == state.revision + 1
    with pytest.raises(messaging_api.Refused) as e:
        matt.tasks.set_scope(SERVER, MATT, m.thread_id, state.revision, "full")
    assert e.value.code == "revision_conflict" and e.value.latest.scope == "partial"


def test_a_reply_drafted_is_notes_ready(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    matt.local.create_draft(ports.DraftSave(thread_id=m.thread_id,
                                            in_reply_to_message_id=m.message_id,
                                            recipient_github_id=TIM, text="Notes: page 1 ..."))
    assert matt.tasks.review_state(SERVER, MATT, m.thread_id).progress == "notes_ready"


def test_a_repair_resumes_only_what_was_not_done(tmp_path, serve, clock):
    matt, m = received(tmp_path, serve, clock)
    key = AttachmentKey(m.message_id, ZIP.attachment_id)
    matt.inspection.read_member(matt.me, key, member_id(ZIP.sha256, "review/summary.pdf"))
    before = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    flat = bf.ref(32, "review (flattened).zip")
    task = bf.task_on(flat, primary="summary.pdf", supporting=("sources.md",))
    repair = bf.accepted(TIM, MATT, [flat], n=2, task=task, reply_to=m, supersedes=[
        wire.Supersedes(message_id=m.message_id, attachment_id=ZIP.attachment_id,
                        by_attachment_id=flat.attachment_id, reason="repair")])
    bf.hold(matt.store, MATT, repair)
    after = matt.tasks.review_state(SERVER, MATT, m.thread_id)
    assert [(i.attachment_id, i.path, i.state) for i in after.items] == [
        (flat.attachment_id, "summary.pdf", "read"), (flat.attachment_id, "sources.md", "unread")]
    assert after.revision > before.revision
    revised = bf.accepted(TIM, MATT, [bf.ref(33, "v2.zip")], n=4, reply_to=m,
                          task=bf.task_on(bf.ref(33, "v2.zip"), primary="summary.pdf",
                                          supporting=()), supersedes=[wire.Supersedes(
                              message_id=repair.message_id, attachment_id=flat.attachment_id,
                              by_attachment_id=UUID(int=33), reason="revision")])
    bf.hold(matt.store, MATT, revised)
    assert [i.state for i in matt.tasks.review_state(SERVER, MATT, m.thread_id).items] == \
        ["unread"], "a revision's changed sources are read again"


# -- downloads ---------------------------------------------------------------------------------

class Flaky:
    """A transport whose downloads fail `failures` times first."""

    def __init__(self, inner, failures: list):
        self.inner, self.failures = inner, failures

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def download_attachment(self, message_id, attachment_id):
        failure = self.failures.pop(0) if self.failures else None

        async def stream():
            if failure == "damaged":
                yield b"not the bytes"
                return
            if failure is not None:
                raise ports.TransportError(failure)
            async for chunk in self.inner.download_attachment(message_id, attachment_id):
                yield chunk
        return stream()


def test_a_transient_download_is_fetched_again_bounded_and_checked(tmp_path, serve, clock):
    tim = Side(tmp_path, serve, TIM, 1, clock)
    matt = Side(tmp_path, serve, MATT, 2, clock)
    draft = tim.attach(bf.draft_for(tim.local, MATT), "review.zip",
                       bf.zip_bytes({"a.txt": b"hello"}))
    sent = tim.send(draft)
    matt.tick()
    r = sent.attachments[0]
    matt.transport = Flaky(serve.pair(MATT, matt.node), ["transport", "damaged"])
    path = asyncio.run(matt.files.downloaded(SERVER, MATT, sent.message_id, r.attachment_id))
    assert messaging_files._hash_file(path) == (r.size_bytes, r.sha256)
    assert len(matt.waits) == 2

    other = Side(tmp_path / "o", serve, MATT, 3, clock)
    other.tick()
    other.transport = Flaky(serve.pair(MATT, other.node), ["transport"] * 5)
    with pytest.raises(wire.ContractError):
        asyncio.run(other.files.downloaded(SERVER, MATT, sent.message_id, r.attachment_id))
    assert len(other.waits) == messaging_files.DOWNLOAD_ATTEMPTS - 1

    third = Side(tmp_path / "t", serve, MATT, 4, clock)
    third.tick()
    third.transport = Flaky(serve.pair(MATT, third.node), ["attachment_not_found"])
    with pytest.raises(wire.ContractError) as e:
        asyncio.run(third.files.downloaded(SERVER, MATT, sent.message_id, r.attachment_id))
    assert e.value.code == "attachment_not_found" and third.waits == [], "never retried"
