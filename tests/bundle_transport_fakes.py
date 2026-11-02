"""Test doubles for the bundle transport tests: an inspection service that
answers from a table (the readers lane's BundleInspection is tested on its
own), messages put straight into a store, and team network snapshots."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import (AttachmentInfo, Classification, Extent, InspectionRefused,
                                    Inventory, LineLocation, MemberInfo, MemberPage,
                                    PackageBadge, PageLocation, ReaderId, RenderedPage,
                                    RenderedPageInfo, TextChunk, TextPage, member_id)
from jarvis.bundle_inspection import AttachmentKey, Observation, Owner

SERVER = "server-1"
TIM, MATT = 863470, 1001
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class Spec:
    """One file as the fake inspection sees it. `members` are (path,
    format, support) of an archive's files; a plain file is its own member."""

    def __init__(self, ref: wire.AttachmentRef, *, fmt: str = "zip", container: str = "zip",
                 support: str = "readable", reason: Optional[str] = None,
                 members: tuple = (), runs: Optional[str] = None, wrapper: str = "",
                 pages: int = 2):
        self.ref, self.fmt, self.container = ref, fmt, container
        self.support, self.reason, self.members = support, reason, list(members)
        self.runs, self.wrapper, self.pages = runs, wrapper, pages
        if container == "none":
            self.members = [("", fmt, support)]


class FakeInspection:
    """classify / list_members / read_member / render_page from a table of
    Specs, reporting each outcome to `observer` as the real one does."""

    def __init__(self, specs: dict[UUID, Spec], observer=lambda o: None):
        self.specs, self.observer = specs, observer
        self.calls: list[str] = []

    def _spec(self, key: AttachmentKey) -> Spec:
        spec = self.specs.get(key.attachment_id)
        if spec is None:
            raise InspectionRefused("attachment_not_found", "No such file.")
        return spec

    def _info(self, key: AttachmentKey, spec: Spec) -> AttachmentInfo:
        r = spec.ref
        return AttachmentInfo(message_id=key.message_id, attachment_id=r.attachment_id,
                              filename=r.filename, declared_media_type=r.media_type,
                              size_bytes=r.size_bytes, sha256=r.sha256)

    def _observe(self, owner, key, spec, op, member, support, reason=None, locations=()):
        self.observer(Observation(owner, key, spec.ref.sha256, op, member, support, reason,
                                  tuple(locations)))

    def _member(self, spec: Spec, path: str, fmt: str, support: str) -> MemberInfo:
        IMAGES = ("png", "jpeg", "gif", "webp")
        return MemberInfo(member_id=member_id(spec.ref.sha256, path), path=path,
                          name=path.rsplit("/", 1)[-1] or spec.ref.filename, is_dir=False,
                          size_bytes=1000, compressed_size=500, format=fmt,
                          media_type="application/octet-stream", support=support,
                          reason=None if support == "readable" else "reader_unavailable",
                          operations=["download", "render"] if fmt in IMAGES
                          else ["download", "read_text"])

    async def open(self, owner: Owner, key: AttachmentKey) -> Classification:
        self._observe(owner, key, self._spec(key), "download", None, "readable")
        return self.classify(owner, key)

    def classify(self, owner: Owner, key: AttachmentKey) -> Classification:
        self.calls.append("classify")
        spec = self._spec(key)
        if spec.container == "zip":
            self._observe(owner, key, spec, "list_archive", None, spec.support, spec.reason)
        files = [m for m in spec.members]
        inventory = Inventory(files=len(files), folders=1 if spec.wrapper else 0,
                              expanded_bytes=1000 * len(files),
                              readable=sum(1 for m in files if m[2] == "readable"),
                              partial=0,
                              unsupported=sum(1 for m in files if m[2] == "unsupported"),
                              blocked=sum(1 for m in files if m[2] == "blocked"),
                              wrapper=spec.wrapper) if spec.container == "zip" else None
        package = None
        if spec.container == "zip":
            package = PackageBadge(recognised=spec.runs is not None, runs=spec.runs)
        return Classification(attachment=self._info(key, spec), format=spec.fmt,
                              media_type="application/zip", container=spec.container,
                              support=spec.support, reason=spec.reason,
                              root_member_id=member_id(spec.ref.sha256, ""),
                              inventory=inventory, package=package)

    def list_members(self, owner, key, folder="", continuation=None, limit=100) -> MemberPage:
        spec = self._spec(key)
        if spec.container != "none" and spec.support in ("blocked", "unsupported"):
            raise InspectionRefused("corrupt", "It cannot be listed.", spec.reason)
        members = [self._member(spec, *m) for m in spec.members]
        return MemberPage(attachment=self._info(key, spec), folder=folder, members=members,
                          total=len(members))

    def _find(self, spec: Spec, mid: str) -> tuple:
        found = next((m for m in spec.members if member_id(spec.ref.sha256, m[0]) == mid), None)
        if found is None:
            raise InspectionRefused("member_not_found", "No such member.")
        return found

    def read_member(self, owner, key, mid, continuation=None, max_chars=12_000) -> TextPage:
        spec = self._spec(key)
        path, fmt, support = self._find(spec, mid)
        if support != "readable":
            self._observe(owner, key, spec, "read_text", mid, support, "reader_unavailable")
            raise InspectionRefused("unsupported", "No reader here.", "reader_unavailable")
        location = PageLocation(page=1) if fmt == "pdf" else LineLocation(start=1, end=1)
        self._observe(owner, key, spec, "read_text", mid, "readable", None, (location,))
        return TextPage(attachment=self._info(key, spec), member=self._member(spec, *self._find(
            spec, mid)), reader=ReaderId(name="fake", version=1),
            chunks=[TextChunk(location=location, text=f"text of {path or spec.ref.filename}")],
            chars=10, extent=Extent(pages=spec.pages), complete=True)

    def render_page(self, owner, key, mid, page, options=None) -> RenderedPage:
        spec = self._spec(key)
        self._find(spec, mid)
        self._observe(owner, key, spec, "render", mid, "readable")
        return RenderedPage(info=RenderedPageInfo(member_id=mid, page=page,
                                                  page_count=spec.pages, width=10, height=10),
                            data=b"\x89PNG fake page %d" % page)


def ref(n: int, name: str = "review.zip", sha: Optional[str] = None,
        media: str = "application/zip", size: int = 1000) -> wire.AttachmentRef:
    return wire.AttachmentRef(attachment_id=UUID(int=n), filename=name, media_type=media,
                              size_bytes=size, sha256=sha or f"{n:064x}")


def accepted(sender: int, recipient: int, files=(), *, n: int, task=None, supersedes=(),
             reply_to: Optional[wire.AcceptedMessage] = None, text: str = "Please review.",
             at: Optional[datetime] = None) -> wire.AcceptedMessage:
    mid = UUID(int=0xA000 + n)
    content = wire.MessageContent(
        message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
        in_reply_to_message_id=reply_to.message_id if reply_to else None,
        recipient_github_id=recipient, content_revision=1, subject="Summary review",
        text=text, attachments=list(files), task=task, supersedes=list(supersedes))
    digest = wire.content_digest(content, server_id=SERVER, sender_github_id=sender)
    return wire.AcceptedMessage(**content.model_dump(), server_id=SERVER,
                                sender_github_id=sender, source_node_id=UUID(int=7),
                                content_digest=digest, idempotency_key=UUID(int=0xB000 + n),
                                accepted_at=at or T0 + timedelta(minutes=n))


def hold(store, owner: int, *messages: wire.AcceptedMessage) -> None:
    """Messages as the owner's node holds them after a sync."""
    entries = []
    for m in messages:
        receipts = wire.ReceiptFacts(message_id=m.message_id,
                                     recipient_github_id=m.recipient_github_id, revision=1)
        state = wire.new_work_state(server_id=SERVER, owner_github_id=owner, message=m)
        entries.append(wire.SyncEntry(message=m, receipts=receipts, work_state=state))
    store.store_page(SERVER, owner, UUID(int=1), wire.SyncPage(
        epoch="e", entries=entries, scan_complete=True, server_time=T0))


def task_on(r: wire.AttachmentRef, primary: Optional[str] = "review/summary.pdf",
            supporting=("review/sources.md",), kind: str = "notes") -> wire.TaskContext:
    return wire.TaskContext(
        requested_outcome="Notes on the summary.", outcome_kind=kind,
        primary=wire.ArtifactRef(attachment_id=r.attachment_id, sha256=r.sha256,
                                 member_path=primary) if primary is not None else None,
        supporting=[wire.ArtifactRef(attachment_id=r.attachment_id, sha256=r.sha256,
                                     member_path=p) for p in supporting])


def node(owner: int, n: int, *, presence="online", attachments=None,
         checked=timedelta(seconds=20), label=None, now: datetime = T0) -> dict:
    return {"node_id": str(UUID(int=0xC000 + n)), "owner_github_id": owner,
            "label": label or f"NODE {n}", "presence": presence, "attachments": attachments,
            "attachments_observed_at": (now - checked).isoformat()}


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buffer.getvalue()


def draft_for(local, recipient: int, text: str = "Please review the summary.",
              attachment_ids=()) -> ports.Draft:
    return local.create_draft(ports.DraftSave(recipient_github_id=recipient, text=text,
                                              attachment_ids=list(attachment_ids)))
