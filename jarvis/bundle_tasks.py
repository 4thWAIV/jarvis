"""One-off requests on an ordinary thread: the task context a message
carries (requested outcome, primary artifact by attachment id and hash,
supporting members, reply audience), the recipient's review checklist and
source map, the scope he accepts, companions, and repair resends linked to
what they supersede.

The task context is message content (`MessageContent.task` on the wire):
setting it changes the draft, so the owner reviews and approves it like
any edit. A repair is a new draft in the same thread, replying to the
failed message, whose `supersedes` names the old attachment; the old one is
kept. Nothing here sends: every message goes through the existing approval.

Companions are files the readers make from a file, added to a draft beside
it (never in its place) with what they are in their names:

    pdf_text      every PDF's text, page by page, as "<name> (text).txt"
    page_images   the primary PDF's pages (else every PDF's), at most
                  PAGE_IMAGES_MAX, as PNGs in "<name> (page images).zip"
    file_index    every member's path, size, format and whether it reads
                  here, as "<name> (files).txt"

A repair:

    flatten_wrapper   the same files without the archive's one wrapper
                      folder, in "<name> (flattened).zip": a pure
                      restructure, each member's bytes checked against the
                      original's (platform metadata such as __MACOSX left
                      out). It supersedes the old file.
    companions        the companions of the old file, beside it in the
                      thread; it supersedes nothing.

The review record (`human_review`, local, never sent) is the checklist of
the latest message in the thread whose task the owner received: its primary
and supporting artifacts, and briefs found by name. The inspection service's
observations mark items read or unreadable (bundle_status hands them on), so
a source counts as checked only once it was read. A repair of what was
reviewed carries the items already done across (the same bytes, checked);
a revision starts them again.

    file_ready            the files can be opened
    evidence_unavailable  a required member could not be read
    review_in_progress    the owner accepted a scope (full, or partial with
                          its limits labelled)
    notes_ready           a reply is drafted in the thread
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import tempfile
import zipfile
from typing import Callable, Iterator, Literal, Optional
from uuid import UUID

from jarvis import bundle_archive
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import (CHECKLIST_MAX, TEXT_PAGE_MAX_CHARS, ChecklistItem,
                                    InspectionRefused, LineLocation, Location, ReviewState,
                                    member_id)
from jarvis.bundle_inspection import AttachmentKey, Observation, Owner
from jarvis.bundle_preflight import all_members
from jarvis.messaging_api import Refused

log = logging.getLogger("jarvis.bundle_tasks")

CompanionKind = Literal["pdf_text", "page_images", "file_index"]
RepairMode = Literal["flatten_wrapper", "companions"]

COMPANION_KINDS: tuple[str, ...] = ("pdf_text", "page_images", "file_index")
PAGE_IMAGES_MAX = 30
# Text pages read for one PDF's text companion, at most.
TEXT_PAGES_MAX = 200
# A member is a brief by its name when it is text or Markdown.
_BRIEF_NAME = re.compile(r"(^|[/_\-. ])(brief|readme|instructions?|request|skill)([/_\-. ]|$)",
                         re.IGNORECASE)
_BRIEF_FORMATS = frozenset({"markdown", "text"})
_SPOOL_BYTES = 8 * 1024 * 1024


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename.strip(".") else filename


# -- companions -------------------------------------------------------------------------

def _pdfs(inspection, owner: Owner, key: AttachmentKey) -> list:
    return [m for m in all_members(inspection, owner, key)
            if not m.is_dir and m.format == "pdf" and m.support in ("readable", "partial")]


def pdf_text(inspection, owner: Owner, key: AttachmentKey) -> bytes:
    """Every PDF's text, with each member's path and each page marked."""
    out = io.StringIO()
    pdfs = _pdfs(inspection, owner, key)
    if not pdfs:
        raise InspectionRefused("unsupported", "It has no PDF whose text can be read.")
    for m in pdfs:
        out.write(f"== {m.path or m.name} ==\n")
        continuation = None
        for _ in range(TEXT_PAGES_MAX):
            page = inspection.read_member(owner, key, m.member_id, continuation,
                                          TEXT_PAGE_MAX_CHARS)
            for chunk in page.chunks:
                where = getattr(chunk.location, "page", None)
                if where is not None:
                    out.write(f"\n[page {where}]\n")
                out.write(chunk.text)
            continuation = page.continuation
            if page.complete or not continuation:
                break
        else:
            out.write("\n[more text not included]\n")
        out.write("\n\n")
    return out.getvalue().encode("utf-8")


def page_images(inspection, owner: Owner, key: AttachmentKey,
                primary: Optional[str] = None) -> bytes:
    """The primary PDF's pages (else every PDF's), as PNGs in a ZIP."""
    pdfs = _pdfs(inspection, owner, key)
    if primary is not None:
        pdfs = [m for m in pdfs if m.member_id == primary] or pdfs
    if not pdfs:
        raise InspectionRefused("unsupported", "It has no PDF whose pages can be drawn.")
    buffer, done = io.BytesIO(), 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as out:
        for m in pdfs:
            page, count = 1, 1
            stem = _stem(m.name) if len(pdfs) > 1 else "page"
            while page <= count and done < PAGE_IMAGES_MAX:
                rendered = inspection.render_page(owner, key, m.member_id, page)
                count = rendered.info.page_count
                name = f"{stem}-{page:03d}.png" if len(pdfs) > 1 else f"page-{page:03d}.png"
                out.writestr(name, rendered.data)
                page, done = page + 1, done + 1
    return buffer.getvalue()


def file_index(inspection, owner: Owner, key: AttachmentKey) -> bytes:
    """Every member, one line each: path, size, format, and whether it
    reads here."""
    lines = ["path\tbytes\tformat\treadable here"]
    for m in all_members(inspection, owner, key):
        if m.is_dir:
            continue
        lines.append(f"{m.path or m.name}\t{m.size_bytes}\t{m.format}\t"
                     f"{'yes' if m.support == 'readable' else m.support}")
    return ("\n".join(lines) + "\n").encode("utf-8")


COMPANIONS: dict[str, tuple[str, str]] = {
    "pdf_text": ("{} (text).txt", "text/plain"),
    "page_images": ("{} (page images).zip", "application/zip"),
    "file_index": ("{} (files).txt", "text/plain"),
}


# -- flattening a wrapper folder -------------------------------------------------------------

def flatten_wrapper(original, out, budgets: bundle_archive.Budgets = bundle_archive.DEFAULT_BUDGETS
                    ) -> int:
    """Write the archive at `original` into the file object `out` without
    its one wrapper folder, each member's bytes, date and mode kept, and
    check every file member of the result against the original's; how many
    files it holds. InspectionRefused when it has no wrapper folder."""
    with bundle_archive.open_archive(original) as archive:
        listing = bundle_archive.list_members(archive, budgets)
        if not listing.wrapper:
            raise InspectionRefused("unsupported", "It has no wrapper folder to take away.")
        prefix = listing.root
        expected: dict[str, str] = {}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as flat:
            for m in listing.members:
                if m.path == listing.wrapper:
                    continue
                name = m.path[len(prefix):]
                info = zipfile.ZipInfo(name + "/" if m.is_dir else name,
                                       date_time=m.info.date_time)
                info.external_attr = m.info.external_attr
                info.compress_type = zipfile.ZIP_STORED if m.is_dir else zipfile.ZIP_DEFLATED
                if m.is_dir:
                    flat.writestr(info, b"")
                    continue
                data = bundle_archive.read_member(archive, m, budgets)
                expected[name] = hashlib.sha256(data).hexdigest()
                flat.writestr(info, data)
    out.seek(0)
    with zipfile.ZipFile(out) as flat:
        checked = bundle_archive.list_members(flat, budgets).files()
        if set(checked) != set(expected) or any(
                hashlib.sha256(bundle_archive.read_member(flat, checked[n], budgets)).hexdigest()
                != digest for n, digest in expected.items()):
            raise InspectionRefused("corrupt", "The flattened copy did not match the original.")
    out.seek(0)
    return len(expected)


def _chunks(stream, size: int = wire.UPLOAD_CHUNK_BYTES) -> Iterator[bytes]:
    while chunk := stream.read(size):
        yield chunk


# -- the service ---------------------------------------------------------------------------------

class BundleTasks:
    """Over the messaging store, the page's draft writer (`local`,
    messaging_api.LocalMessaging), the file snapshots, and two inspection
    services: `drafts` reads a draft's own snapshots
    (bundle_preflight.DraftFiles), `messages` a message's files (its
    private copy or verified download, `MessageFiles`)."""

    def __init__(self, store, local, files, *, drafts, messages,
                 status: Optional[Callable[[str, int, UUID, UUID], dict]] = None,
                 on_loop: Callable[[Callable], object] = lambda fn: fn()):
        """`on_loop` runs a draft edit on the event loop (the routes call
        these from a worker thread; an edit closes a review capture and
        sends a hint, which belong to the loop)."""
        self.store, self.local, self.files = store, local, files
        self.drafts, self.messages, self.status = drafts, messages, status
        self.on_loop = on_loop

    def _edit(self, message_id: UUID, expected: int, **fields) -> ports.Draft:
        return self.on_loop(lambda: self.local.edit_bundle(message_id, expected, **fields))

    def _ns(self, server_id: str, owner_github_id: int) -> tuple[str, int]:
        ns = self.local.pairing().ns
        if ns != (server_id, owner_github_id):
            raise Refused("messaging_unavailable", availability="not_paired")
        return ns

    def _draft(self, ns, message_id: UUID, expected: int) -> ports.Draft:
        draft = self.store.draft(*ns, message_id)
        if draft is None:
            raise Refused("draft_not_found")
        if draft.draft_revision != expected:
            raise Refused("revision_conflict", draft)
        return draft

    # -- task context -----------------------------------------------------------------------

    def set_task(self, server_id: str, owner_github_id: int, message_id: UUID,
                 expected_draft_revision: int, task: Optional[dict]) -> ports.Draft:
        ns = self._ns(server_id, owner_github_id)
        draft = self._draft(ns, message_id, expected_draft_revision)
        value = None
        if task is not None:
            if not isinstance(task, dict):
                raise Refused("invalid_request")
            outcome = wire.normalize_text(str(task.get("requested_outcome", ""))).strip()
            try:
                value = wire.TaskContext.model_validate({**task, "requested_outcome": outcome})
            except ValueError:
                raise Refused("invalid_request") from None
            files = {a.attachment_id: a.sha256 for a in draft.attachments}
            if any(files.get(r.attachment_id) != r.sha256
                   for r in [value.primary, *value.supporting] if r is not None):
                raise Refused("invalid_request")
        return self._edit(message_id, expected_draft_revision, task=value)

    # -- companions ---------------------------------------------------------------------------

    def _make(self, inspection, owner: Owner, key: AttachmentKey, kind: str,
              primary: Optional[str]) -> bytes:
        if kind == "pdf_text":
            return pdf_text(inspection, owner, key)
        if kind == "page_images":
            return page_images(inspection, owner, key, primary)
        if kind == "file_index":
            return file_index(inspection, owner, key)
        raise Refused("invalid_request")

    def _companions(self, ns, inspection, key: AttachmentKey, ref: wire.AttachmentRef,
                    kinds: list[str], into: UUID, primary: Optional[str]
                    ) -> list[wire.AttachmentRef]:
        owner = Owner(*ns)
        made = []
        for kind in dict.fromkeys(kinds):
            name, media = COMPANIONS[kind]
            data = self._make(inspection, owner, key, kind, primary)
            made.append(self.files.snapshot_made(*ns, into, name.format(_stem(ref.filename)),
                                                 media, [data]))
        return made

    def add_companions(self, server_id: str, owner_github_id: int, message_id: UUID,
                       expected_draft_revision: int, attachment_id: UUID,
                       kinds: list[CompanionKind]) -> ports.Draft:
        ns = self._ns(server_id, owner_github_id)
        draft = self._draft(ns, message_id, expected_draft_revision)
        ref = next((a for a in draft.attachments if a.attachment_id == attachment_id), None)
        if ref is None or not kinds or any(k not in COMPANIONS for k in kinds):
            raise Refused("invalid_request")
        if len(draft.attachments) + len(set(kinds)) > wire.ATTACHMENTS_MAX:
            raise Refused("too_large")
        primary = None
        if draft.task and draft.task.primary and draft.task.primary.attachment_id == attachment_id:
            primary = member_id(ref.sha256, draft.task.primary.member_path or "")
        made = self._companions(ns, self.drafts, AttachmentKey(message_id, attachment_id), ref,
                                list(kinds), message_id, primary)
        return self._edit(
            message_id, expected_draft_revision,
            attachment_ids=[a.attachment_id for a in draft.attachments] +
            [m.attachment_id for m in made])

    # -- repair ---------------------------------------------------------------------------------

    def prepare_repair(self, server_id: str, owner_github_id: int, message_id: UUID,
                       attachment_id: UUID, mode: RepairMode) -> ports.Draft:
        """A new draft in the failed message's thread, replying to it, with
        the repaired file or the companions, its task carried over (onto the
        new file where it named the old one) and only the original recipient."""
        ns = self._ns(server_id, owner_github_id)
        held = self.store.message(*ns, message_id)
        if held is None or held.message.sender_github_id != owner_github_id:
            raise Refused("message_not_found")
        failed = held.message
        ref = next((a for a in failed.attachments if a.attachment_id == attachment_id), None)
        if ref is None:
            raise Refused("attachment_not_found")
        if mode not in ("flatten_wrapper", "companions"):
            raise Refused("invalid_request")
        try:
            original = self.files.original_bytes(*ns, message_id, attachment_id)
        except wire.ContractError:
            raise InspectionRefused("not_local", "Its copy is not here any more; download "
                                                 "it first.", "not_local") from None
        draft = self.on_loop(lambda: self.local.create_draft(ports.DraftSave(
            thread_id=failed.thread_id, in_reply_to_message_id=failed.message_id,
            recipient_github_id=failed.recipient_github_id, subject=failed.subject)))
        try:
            if mode == "flatten_wrapper":
                with tempfile.SpooledTemporaryFile(_SPOOL_BYTES,
                                                   dir=self.files.snapshots_dir()) as out:
                    flatten_wrapper(original, out)
                    made = [self.files.snapshot_made(
                        *ns, draft.message_id, f"{_stem(ref.filename)} (flattened).zip",
                        "application/zip", _chunks(out))]
                supersedes = [wire.Supersedes(message_id=failed.message_id,
                                              attachment_id=attachment_id,
                                              by_attachment_id=made[0].attachment_id,
                                              reason="repair")]
            else:
                kinds = self._companion_kinds(ns, message_id, attachment_id)
                made = self._companions(ns, self.messages, AttachmentKey(message_id,
                                                                         attachment_id),
                                        ref, kinds, draft.message_id, None)
                supersedes = []
        except BaseException:
            self.on_loop(lambda: self.local.discard_draft(draft.message_id,
                                                          draft.draft_revision))
            raise
        task = _carried(failed.task, ref, made[0], mode == "flatten_wrapper")
        return self._edit(draft.message_id, draft.draft_revision,
                          attachment_ids=[m.attachment_id for m in made],
                          task=task, supersedes=supersedes)

    def _companion_kinds(self, ns, message_id: UUID, attachment_id: UUID) -> list[str]:
        """The smallest representation the recipient's report asks for, or
        all three when there is no report: page images where pages failed
        to draw, PDF text where text failed to read, an index otherwise."""
        report = self.status(*ns, message_id, attachment_id) if self.status else {}
        op = (report or {}).get("failed_operation")
        if op == "render":
            return ["page_images"]
        if op == "read_text":
            return ["pdf_text", "file_index"]
        if op == "list_archive":
            return ["file_index"]
        return list(COMPANION_KINDS)

    # -- the recipient's review ---------------------------------------------------------------

    def _source(self, ns, thread_id: UUID) -> Optional[ports.LocalMessage]:
        """The latest message in the thread whose task the owner received."""
        item = self.store.item(*ns, thread_id)
        found = [m for m in (item.messages if item else [])
                 if m.message.task is not None
                 and m.message.recipient_github_id == ns[1]]
        return found[-1] if found else None

    def _briefs(self, ns, message: wire.AcceptedMessage, taken: set[str]) -> list[ChecklistItem]:
        """Members found to be briefs by name, in files already verified here."""
        found: list[ChecklistItem] = []
        for ref in message.attachments:
            try:
                members = all_members(self.messages, Owner(*ns),
                                      AttachmentKey(message.message_id, ref.attachment_id))
            except InspectionRefused:
                continue
            for m in members:
                if not m.is_dir and m.format in _BRIEF_FORMATS and m.member_id not in taken \
                        and _BRIEF_NAME.search(m.path or m.name):
                    found.append(ChecklistItem(attachment_id=ref.attachment_id,
                                               member_id=m.member_id, path=m.path or m.name,
                                               role="brief", state="unread"))
        return found

    def _seed(self, ns, source: ports.LocalMessage) -> list[ChecklistItem]:
        message, task = source.message, source.message.task
        items: list[ChecklistItem] = []
        for role, refs in (("primary", [task.primary] if task.primary else []),
                           ("supporting", task.supporting)):
            for r in refs:
                name = next((a.filename for a in message.attachments
                             if a.attachment_id == r.attachment_id), "")
                items.append(ChecklistItem(
                    attachment_id=r.attachment_id, member_id=member_id(r.sha256,
                                                                       r.member_path or ""),
                    path=r.member_path or name, role=role, state="unread"))
        items += self._briefs(ns, message, {i.member_id for i in items})
        return items[:CHECKLIST_MAX]

    def _carry(self, ns, old: list[ChecklistItem], new: list[ChecklistItem],
               repair: wire.AcceptedMessage) -> list[ChecklistItem]:
        """Items done on the file a repair replaced (read or cited),
        carried to the same member of its repaired copy only when that
        member holds the same bytes (member_digest on both, checked here,
        never taken from the repair's word); an item not done, unreadable
        ones included, or whose bytes changed or cannot be compared, starts
        again."""
        replaced = {s.by_attachment_id: s for s in repair.supersedes if s.reason == "repair"}
        owner = Owner(*ns)

        def digest(message_id: UUID, item: ChecklistItem) -> Optional[str]:
            try:
                return self.messages.member_digest(
                    owner, AttachmentKey(message_id, item.attachment_id), item.member_id)
            except (InspectionRefused, wire.ContractError):
                return None

        def same(a: str, b: str) -> bool:
            return a == b or (a.endswith("/" + b) and a.count("/") == b.count("/") + 1)
        carried = []
        for item in new:
            link = replaced.get(item.attachment_id)
            done = None if link is None else next(
                (o for o in old if o.attachment_id == link.attachment_id and o.role == item.role
                 and o.state in ("read", "cited") and same(o.path, item.path)), None)
            if done is not None:
                before = digest(link.message_id, done)
                if before is None or before != digest(repair.message_id, item):
                    done = None
            carried.append(item.model_copy(update={"state": done.state, "read": done.read})
                           if done else item)
        return carried

    def _load(self, ns, thread_id: UUID) -> tuple[Optional[ReviewState], Optional[str]]:
        row = self.store.review(*ns, thread_id)
        if row is None:
            return None, None
        body = json.loads(row[1])
        return ReviewState.model_validate(body["state"]), body.get("source")

    def _progress(self, ns, thread_id: UUID, state: ReviewState) -> Optional[str]:
        if self._source(ns, thread_id) is None:
            return None             # no review was asked of him here (he sent the request)
        item = self.store.item(*ns, thread_id)
        if item and any(d.text or d.attachments for d in item.drafts):
            return "notes_ready"
        if state.scope is not None:
            return "review_in_progress"
        if any(i.state == "unreadable" for i in state.items):
            return "evidence_unavailable"
        if any(i.state in ("read", "cited") for i in state.items):
            return "file_ready"
        return None

    def _write(self, ns, thread_id: UUID, change: Callable[[ReviewState], ReviewState]
               ) -> ReviewState:
        """The record brought up to date (reseeded when a newer task arrived
        in the thread, done items carried across a repair), then `change`d,
        its progress worked out, and saved by compare-and-set when anything
        moved (its revision with it)."""
        for _ in range(4):
            stored, source_id = self._load(ns, thread_id)
            state = stored or ReviewState(thread_id=thread_id, revision=1)
            source = self._source(ns, thread_id)
            new_source = str(source.message.message_id) if source else None
            if source is not None and new_source != source_id:
                repair = any(s.reason == "repair" for s in source.message.supersedes)
                items = self._seed(ns, source)
                if stored is not None and repair:
                    items = self._carry(ns, stored.items, items, source.message)
                state = state.model_copy(update={"items": items,
                                                 "scope": state.scope if repair else None})
            state = change(state)
            state = state.model_copy(update={"progress": self._progress(ns, thread_id, state)})
            if stored is not None and state.model_copy(update={"revision": stored.revision}) \
                    == stored and new_source == source_id:
                return stored
            if stored is not None:
                state = state.model_copy(update={"revision": stored.revision + 1})
            if self.store.save_review(*ns, thread_id, stored.revision if stored else None,
                                      state.revision,
                                      json.dumps({"state": state.model_dump(mode="json"),
                                                  "source": new_source})):
                return state
        raise Refused("revision_conflict", self._load(ns, thread_id)[0])

    def review_state(self, server_id: str, owner_github_id: int, thread_id: UUID) -> ReviewState:
        ns = self._ns(server_id, owner_github_id)
        if self.store.item(*ns, thread_id) is None:
            raise Refused("message_not_found")
        return self._write(ns, thread_id, lambda state: state)

    def set_scope(self, server_id: str, owner_github_id: int, thread_id: UUID,
                  expected_revision: int, scope: Literal["full", "partial"]) -> ReviewState:
        """The scope the owner accepts: full, or partial (what could not be
        read stays labelled unreadable, and the notes say so)."""
        if scope not in ("full", "partial"):
            raise Refused("invalid_request")
        current = self.review_state(server_id, owner_github_id, thread_id)
        if current.revision != expected_revision:
            raise Refused("revision_conflict", current)
        ns = self._ns(server_id, owner_github_id)
        return self._write(ns, thread_id, lambda state: state.model_copy(update={"scope": scope}))

    def observe(self, o: Observation) -> None:
        """A read or a failure marks the matching checklist item; a cited
        item stays cited, and a member once read is not unread by a later
        failure of another operation on it."""
        if o.member_id is None:
            return
        ns = (o.owner.server_id, o.owner.owner_github_id)
        held = self.store.message(*ns, o.key.message_id)
        if held is None or self._source(ns, held.message.thread_id) is None:
            return
        readable = o.support not in ("unsupported", "blocked")
        mark = "read" if readable and o.operation in ("read_text", "render", "ocr") \
            else (None if readable else "unreadable")
        if mark is None:
            return

        def changed(i: ChecklistItem) -> ChecklistItem:
            if i.attachment_id != o.key.attachment_id or i.member_id != o.member_id:
                return i
            update = {}
            if i.state not in ("cited", mark) and not (mark == "unreadable" and i.state == "read"):
                update["state"] = mark
            if mark == "read" and o.locations:
                update["read"] = read_locations(i.read, o.locations)
            return i.model_copy(update=update) if update else i

        def marked(state: ReviewState) -> ReviewState:
            return state.model_copy(update={"items": [changed(i) for i in state.items]})
        self._write(ns, held.message.thread_id, marked)


READ_LOCATIONS_MAX = 50       # ChecklistItem.read's own bound


def read_locations(read: list[Location], new) -> list[Location]:
    """Where a member has been read, with `new` added: a place already
    there is not repeated, and line ranges that touch are joined. At most
    READ_LOCATIONS_MAX; past that, the first places read are kept."""
    out = list(read)
    for loc in new:
        if loc in out:
            continue
        if isinstance(loc, LineLocation):
            joined = next((i for i, o in enumerate(out) if isinstance(o, LineLocation)
                           and o.start <= loc.end + 1 and loc.start <= o.end + 1), None)
            if joined is not None:
                o = out[joined]
                out[joined] = LineLocation(start=min(o.start, loc.start), end=max(o.end, loc.end))
                continue
        out.append(loc)
    return out[:READ_LOCATIONS_MAX]


def _carried(task: Optional[wire.TaskContext], old: wire.AttachmentRef,
             new: wire.AttachmentRef, flattened: bool) -> Optional[wire.TaskContext]:
    """The failed message's task for the repair: refs to the old file move
    to the new one (without the wrapper folder when flattened); refs to its
    other files, which this message does not carry, are left out."""
    if task is None:
        return None

    def moved(ref: Optional[wire.ArtifactRef]) -> Optional[wire.ArtifactRef]:
        if ref is None or ref.attachment_id != old.attachment_id or not flattened:
            return None
        path = ref.member_path
        if path and "/" in path:
            path = path.split("/", 1)[1]
        return wire.ArtifactRef(attachment_id=new.attachment_id, sha256=new.sha256,
                                member_path=path or None)
    return task.model_copy(update={"primary": moved(task.primary),
                                   "supporting": [r for r in map(moved, task.supporting) if r]})


class MessageFiles:
    """A message's files as the inspection service reaches them: the
    manifest entry, and the verified bytes (the owner's private copy of a
    file he sent while it is there, else the verified download)."""

    def __init__(self, files):
        self.files = files

    def attachment_ref(self, server_id: str, owner_github_id: int, message_id: UUID,
                       attachment_id: UUID) -> wire.AttachmentRef:
        return self.files.attachment_ref(server_id, owner_github_id, message_id,
                                         attachment_id)

    def verified_path(self, server_id: str, owner_github_id: int, message_id: UUID,
                      attachment_id: UUID):
        return self.files.original_bytes(server_id, owner_github_id, message_id,
                                         attachment_id)

    async def downloaded(self, server_id: str, owner_github_id: int, message_id: UUID,
                         attachment_id: UUID):
        return await self.files.downloaded(server_id, owner_github_id, message_id,
                                           attachment_id)


# -- the bound service ------------------------------------------------------------------------

_bound: Optional[BundleTasks] = None


def bind(service: Optional[BundleTasks]) -> None:
    global _bound
    _bound = service


def _service() -> BundleTasks:
    if _bound is None:
        raise Refused("messaging_unavailable", availability="not_started")
    return _bound


def set_task(server_id: str, owner_github_id: int, message_id: UUID,
             expected_draft_revision: int, task: Optional[dict]):
    """Set or clear a draft's task context (wire TaskContext JSON); the Draft."""
    return _service().set_task(server_id, owner_github_id, message_id,
                               expected_draft_revision, task)


def add_companions(server_id: str, owner_github_id: int, message_id: UUID,
                   expected_draft_revision: int, attachment_id: UUID,
                   kinds: list[CompanionKind]):
    """Derived files added to a draft beside the original; the Draft."""
    return _service().add_companions(server_id, owner_github_id, message_id,
                                     expected_draft_revision, attachment_id, kinds)


def prepare_repair(server_id: str, owner_github_id: int, message_id: UUID,
                   attachment_id: UUID, mode: RepairMode):
    """A new draft replying to `message_id` with a repaired attachment and
    its supersedes link; the Draft."""
    return _service().prepare_repair(server_id, owner_github_id, message_id, attachment_id,
                                     mode)


def review_state(server_id: str, owner_github_id: int, thread_id: UUID) -> ReviewState:
    return _service().review_state(server_id, owner_github_id, thread_id)


def set_scope(server_id: str, owner_github_id: int, thread_id: UUID,
              expected_revision: int, scope: Literal["full", "partial"]) -> ReviewState:
    return _service().set_scope(server_id, owner_github_id, thread_id, expected_revision,
                                scope)


def observe(observation: Observation) -> None:
    if _bound is not None:
        _bound.observe(observation)
