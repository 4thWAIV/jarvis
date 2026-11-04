"""Openability: what actually happened when the recipient's JARVIS opened a
file, kept per (message, attachment, content revision) and reported to the
sender as a technical receipt over the existing messaging transport (the
wire's `OpenabilityReport` in a `ReceiptItem` of kind `openability`, only to
a team server offering `messaging.bundles.v1`).

`observe` is bundle_inspection's observer: it folds each Observation into
the record (verified, ready, partial, unsupported, corrupt, unavailable,
with the failed operation and member) and owes a receipt when the report
changes. Every observation follows the owner's own instruction to look
(the inspection service downloads only on his `open`), so nothing is
reported about a file he has not opened. The record carries counts, codes
and the archive's member path; never document text, device paths or logs.

Which members are required: those the message's task names (its primary
and supporting artifacts on this file), else every file of the archive (or
the file itself). The status, in the order it is decided:

    unavailable   the download failed
    corrupt       the hash or the container is wrong
    unsupported   the container, or the task's primary document, has no reader
    partial       a required member could not be read (the first one named)
    verified      every required member was read
    ready         downloaded and checked, with nothing failed and not all read

The same observations also feed the recipient's review checklist
(bundle_tasks), so a source counts as checked only once it was read.
"""
from __future__ import annotations

import contextvars
import json
import logging
from typing import Callable, Optional
from uuid import UUID

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import InspectionRefused, member_id
from jarvis.bundle_inspection import AttachmentKey, Observation, Owner
from jarvis.bundle_preflight import all_members

log = logging.getLogger("jarvis.bundle_status")

# Reasons that mean the bytes or the container are wrong, not unreadable.
CORRUPT = frozenset({"corrupt", "not_zip", "attachment_mismatch"})
READ_OPERATIONS = frozenset({"read_text", "render", "ocr"})
UNREADABLE = frozenset({"unsupported", "blocked"})

MemberPaths = Callable[[Owner, AttachmentKey], Optional[dict[str, str]]]

# Set while the record asks the inspection service for an archive's files:
# the observations that listing makes are folded without asking again.
_listing = contextvars.ContextVar("bundle_status_listing", default=False)


def members_of(inspection) -> MemberPaths:
    """Every file member of an attachment, {member_id: archive path}, read
    through the inspection service's own listing (BundleInspection); None
    when it cannot be listed."""
    def walk(owner: Owner, key: AttachmentKey) -> Optional[dict[str, str]]:
        try:
            members = all_members(inspection, owner, key)
        except InspectionRefused:
            return None
        return {m.member_id: m.path for m in members if not m.is_dir}
    return walk


def _empty_facts(sha256: str) -> dict:
    return {"sha256": sha256, "downloaded": None, "download_reason": None,
            "container": None, "members": {}, "order": [], "total": None, "paths": {}}


class OpenabilityStatus:
    """The record over the messaging store. `members` lists an archive's
    files (members_of(inspection) in production); `on_change` is told after
    a receipt is owed (delivery's wake)."""

    def __init__(self, store, *, members: Optional[MemberPaths] = None,
                 clock: ports.Clock = ports.utcnow, on_change: Callable[[], None] = lambda: None):
        self.store, self.members, self.clock, self.on_change = store, members, clock, on_change
        # An archive's files, by its hash, once listed: names a failed member.
        self._paths: dict[str, dict[str, str]] = {}

    def _member_paths(self, owner: Owner, key: AttachmentKey,
                      sha256: str) -> Optional[dict[str, str]]:
        if sha256 in self._paths:
            return self._paths[sha256]
        if self.members is None or _listing.get():
            return None
        token = _listing.set(True)
        try:
            paths = self.members(owner, key)
        finally:
            _listing.reset(token)
        if paths is not None:
            if len(self._paths) >= 32:
                self._paths.pop(next(iter(self._paths)))
            self._paths[sha256] = paths
        return paths

    # -- what the message says --------------------------------------------------------

    def _message(self, owner: Owner, message_id: UUID) -> Optional[wire.AcceptedMessage]:
        held = self.store.message(owner.server_id, owner.owner_github_id, message_id)
        return held.message if held is not None else None

    @staticmethod
    def _named(message: wire.AcceptedMessage, attachment_id: UUID,
               sha256: str) -> tuple[Optional[str], dict[str, str]]:
        """The task's primary member id on this file, and every member it
        names, {member_id: path}."""
        task = message.task
        if task is None:
            return None, {}
        def on_file(ref: Optional[wire.ArtifactRef]) -> bool:
            return ref is not None and ref.attachment_id == attachment_id and ref.sha256 == sha256
        named = {member_id(sha256, r.member_path or ""): r.member_path or ""
                 for r in [task.primary, *task.supporting] if on_file(r)}
        primary = member_id(sha256, task.primary.member_path or "") \
            if on_file(task.primary) else None
        return primary, named

    # -- folding ----------------------------------------------------------------------------

    def observe(self, o: Observation) -> None:
        owner, key = o.owner, o.key
        message = self._message(owner, key.message_id)
        ref = next((a for a in (message.attachments if message else [])
                    if a.attachment_id == key.attachment_id), None)
        if message is None or ref is None or ref.sha256 != o.attachment_sha256:
            return
        ns = (owner.server_id, owner.owner_github_id)
        row = self.store.openability(*ns, key.message_id, key.attachment_id,
                                     message.content_revision)
        facts = json.loads(row.facts) if row else _empty_facts(ref.sha256)
        self._fold(facts, o)
        primary, named = self._named(message, key.attachment_id, ref.sha256)
        if named:
            facts["paths"].update(named)
        elif facts["downloaded"] and (facts["total"] is None or _failed_unnamed(facts)):
            paths = self._member_paths(owner, key, ref.sha256)
            if paths is not None:
                facts["total"] = len(paths)
                facts["paths"].update({m: paths[m] for m in facts["members"]
                                       if facts["members"][m]["failed"] and m in paths})
        report = self._report(facts, ref, message.content_revision, primary, named)
        previous = wire.OpenabilityReport.model_validate_json(row.report) \
            if row and row.report else None
        changed = report is not None and (previous is None or _differs(report, previous))
        if report is not None and not changed:
            report = previous
        incoming = message.recipient_github_id == owner.owner_github_id
        self.store.save_openability(*ns, key.message_id, key.attachment_id,
                                    message.content_revision, json.dumps(facts),
                                    report.model_dump_json() if report else None,
                                    owe=changed and incoming)
        if changed and incoming:
            log.info("openability: message=%s attachment=%s %s", key.message_id,
                     key.attachment_id, report.status)
            self.on_change()

    @staticmethod
    def _fold(facts: dict, o: Observation) -> None:
        readable = o.support not in UNREADABLE
        if o.operation == "download":
            facts["downloaded"] = readable
            facts["download_reason"] = None if readable else (o.reason or "unavailable")
            return
        if o.reason == "not_local":
            return                                  # nothing here to judge yet
        facts["downloaded"] = True                  # every other look reads a verified copy
        if o.member_id is None:
            facts["container"] = {"support": o.support, "reason": o.reason}
            return
        seen = facts["members"].setdefault(o.member_id, {"read": False, "failed": None})
        if o.member_id not in facts["order"]:
            facts["order"].append(o.member_id)
        if not readable:
            if seen["failed"] is None:
                seen["failed"] = {"operation": o.operation, "reason": o.reason}
        elif o.operation in READ_OPERATIONS:
            seen["read"] = True

    def _report(self, facts: dict, ref: wire.AttachmentRef, revision: int,
                primary: Optional[str], named: dict[str, str]
                ) -> Optional[wire.OpenabilityReport]:
        if facts["downloaded"] is None:
            return None
        members = facts["members"]
        root = member_id(ref.sha256, "")
        required = list(named) if named else None
        total = len(named) if named else (facts["total"] if facts["total"] is not None
                                          else (1 if root in members else 0))
        failed = [m for m in facts["order"] if members[m]["failed"]
                  and (required is None or m in required)]
        base = dict(attachment_id=ref.attachment_id, sha256=ref.sha256,
                    content_revision=revision, checked_at=self.clock(), members_total=total)
        if facts["downloaded"] is False:
            reason = facts["download_reason"]
            return wire.OpenabilityReport(
                **base, status="corrupt" if reason in CORRUPT else "unavailable",
                failed_operation="download", reason=_code(reason), members_readable=0)
        container = facts["container"]
        if container and container["support"] in UNREADABLE:
            reason = container["reason"]
            return wire.OpenabilityReport(
                **base, status="corrupt" if reason in CORRUPT else "unsupported",
                failed_operation="list_archive" if ref.media_type.endswith("zip")
                else "read_text", reason=_code(reason), members_readable=0)
        if not failed and container and container["support"] == "partial":
            # Listed, and the listing already says some member has no reader
            # here, though nothing has been tried on it yet: not "opens".
            return wire.OpenabilityReport(
                **base, status="partial", failed_operation="read_text",
                reason=_code(container["reason"]),
                members_readable=min(total, sum(1 for m in members.values() if m["read"])))
        readable = max(0, total - len(failed))
        if failed:
            first = failed[0]
            status = "unsupported" if first == primary else "partial"
            return wire.OpenabilityReport(
                **base, status=status, failed_operation=members[first]["failed"]["operation"],
                failed_member=facts["paths"].get(first) or None,
                reason=_code(members[first]["failed"]["reason"]), members_readable=readable)
        wanted = required if required is not None else None
        read_all = (all(members.get(m, {}).get("read") for m in wanted) if wanted
                    else total > 0 and sum(1 for m in members.values() if m["read"]) >= total)
        return wire.OpenabilityReport(**base, status="verified" if read_all else "ready",
                                      members_readable=readable)

    # -- reading and reporting --------------------------------------------------------------

    def status(self, server_id: str, owner_github_id: int, message_id: UUID,
               attachment_id: UUID) -> dict:
        """The latest report on one file as OpenabilityReport JSON: this
        node's own for a file it received, the one the recipient reported
        for a file it sent; {} when there is none."""
        held = self.store.message(server_id, owner_github_id, message_id)
        if held is None:
            return {}
        if held.message.recipient_github_id == owner_github_id:
            row = self.store.openability(server_id, owner_github_id, message_id, attachment_id,
                                         held.message.content_revision)
            return json.loads(row.report) if row and row.report else {}
        report = next((r for r in held.receipts.openability
                       if r.attachment_id == attachment_id
                       and r.content_revision == held.message.content_revision), None)
        return report.model_dump(mode="json") if report else {}

    def reports(self, server_id: str, owner_github_id: int,
                message_id: UUID) -> list[wire.OpenabilityReport]:
        """What an owed openability receipt for the message sends: one report
        per file opened."""
        return [wire.OpenabilityReport.model_validate_json(r)
                for r in self.store.openability_reports(server_id, owner_github_id, message_id)]

    def reported(self, server_id: str, owner_github_id: int, message_id: UUID,
                 sent: list[wire.OpenabilityReport]) -> None:
        self.store.openability_reported(server_id, owner_github_id, message_id,
                                        [r.model_dump_json() for r in sent])


def _failed_unnamed(facts: dict) -> bool:
    return any(seen["failed"] and m not in facts["paths"]
               for m, seen in facts["members"].items())


def _code(reason: Optional[str]) -> Optional[str]:
    """A reason code as the wire carries it: lower-case words joined by
    underscores, nothing else (never a sentence)."""
    if not reason:
        return None
    code = reason.replace("-", "_").lower()
    return code[:40] if code.replace("_", "").isalpha() else None


def _differs(a: wire.OpenabilityReport, b: wire.OpenabilityReport) -> bool:
    keep = {"checked_at"}
    return a.model_dump(exclude=keep) != b.model_dump(exclude=keep)


# -- the bound service ------------------------------------------------------------------------
#
# messaging_runtime builds the one record at startup and binds it here, so
# bundle_inspection's observer and the routes' `status` reach it without a
# second instance. Unbound (messaging not built), observations are dropped
# and there is no status.

_bound: Optional[OpenabilityStatus] = None
_also: list[Callable[[Observation], None]] = []


def bind(service: Optional[OpenabilityStatus],
         also: tuple[Callable[[Observation], None], ...] = ()) -> None:
    """The record observations fold into, and what else hears each one (the
    review checklist)."""
    global _bound, _also
    _bound, _also = service, list(also)


def observe(observation: Observation) -> None:
    for fold in ([_bound.observe] if _bound is not None else []) + _also:
        try:
            fold(observation)
        except Exception as e:  # a record that fails never breaks a read
            log.warning("bundle status: observation not folded (%s)", type(e).__name__)


def status(server_id: str, owner_github_id: int, message_id: UUID,
           attachment_id: UUID) -> dict:
    """The latest record for one attachment (OpenabilityReport JSON): this
    node's own on the recipient's side, the reported one on the sender's."""
    if _bound is None:
        raise InspectionRefused("messaging_unavailable", "Messaging is not available.")
    return _bound.status(server_id, owner_github_id, message_id, attachment_id)
