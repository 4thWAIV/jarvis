"""The sender's check before a message with files goes: the draft's actual
snapshots inspected with the same readers the recipient uses
(bundle_inspection over the sender's own copies, `DraftFiles`), the
operations the task needs, and the recipient's nodes from the stored team
network snapshot (bundle_capabilities). One answer, `PreflightResult`:
ready, partial, unknown or blocked, with only the gaps the sender can act on.

What a message needs, per file: `download`; `list_archive` for a ZIP;
`read_text` for every member a reader here can read, or `render` for one
with no text (an image) (members no reader here can read are local
problems, never requirements); `render` for the
task's primary PDF when the outcome is notes (a presentation review needs
the pages, not only their text); `execute` only for a recognised work
package (its mode).

    blocked   inspecting it here fails: a file is corrupt, encrypted or in
              an archive format JARVIS cannot open, or the task's primary
              document has no reader here
    ready     at least one of the recipient's advertised nodes meets every
              requirement
    partial   the best advertised node meets some of them
    unknown   none of the recipient's nodes has a fresh advertisement
              (legacy, stale or offline)

Each of the recipient's nodes is judged on its own and never merged with
another. It is a prediction: it probes nothing remote, sends no content
anywhere, and never says the recipient opened anything. The capability
snapshot it used is named by `capability_digest` and stored with the
revision reviewed for approval (`record_for_approval`): approval binds that
revision, so an edit, a companion file or a repackaging is a new revision
and a new check.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID

from jarvis import bundle_capabilities as capabilities
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.bundle_contract import (GAPS_MAX, MEMBER_PAGE_MAX, Classification, Gap,
                                    InspectionRefused, MemberInfo, NodeFit, PreflightResult,
                                    Requirement, member_id)
from jarvis.bundle_inspection import AttachmentKey, Owner

log = logging.getLogger("jarvis.bundle_preflight")

# Members of one draft looked at, across its files.
MEMBERS_LOOKED_AT = 10_000
# Formats a portable PDF stands in for.
OFFICE = frozenset({"docx", "xlsx", "pptx", "doc", "xls", "ppt", "rtf"})
READABLE = frozenset({"readable", "partial"})


class DraftFiles:
    """A draft's own snapshots, as the inspection service reaches files
    (`attachment_ref`, `verified_path`, `downloaded`): the sender's copies,
    re-hashed on every look, never the original files."""

    def __init__(self, files, store):
        self.files, self.store = files, store

    def attachment_ref(self, server_id: str, owner_github_id: int, message_id: UUID,
                       attachment_id: UUID) -> wire.AttachmentRef:
        draft = self.store.draft(server_id, owner_github_id, message_id)
        ref = next((a for a in (draft.attachments if draft else [])
                    if a.attachment_id == attachment_id), None)
        if ref is None:
            raise wire.ContractError("attachment_not_found")
        return ref

    def verified_path(self, server_id: str, owner_github_id: int, message_id: UUID,
                      attachment_id: UUID) -> Path:
        ref = self.attachment_ref(server_id, owner_github_id, message_id, attachment_id)
        try:
            return self.files.snapshot_verified(server_id, owner_github_id, ref)
        except ports.SnapshotMissing:
            raise wire.ContractError("attachment_mismatch") from None

    async def downloaded(self, server_id: str, owner_github_id: int, message_id: UUID,
                         attachment_id: UUID) -> Path:
        return self.verified_path(server_id, owner_github_id, message_id, attachment_id)


def all_members(inspection, owner: Owner, key: AttachmentKey,
                limit: int = MEMBERS_LOOKED_AT) -> list[MemberInfo]:
    """Every member of an attachment (a file that is not an archive is its
    own one member), folder by folder through the inspection service's own
    listing, at most `limit`."""
    found: list[MemberInfo] = []
    folders = [""]
    while folders and len(found) < limit:
        folder, continuation = folders.pop(), None
        while True:
            page = inspection.list_members(owner, key, folder, continuation, MEMBER_PAGE_MAX)
            for m in page.members:
                found.append(m)
                if m.is_dir:
                    folders.append(m.path)
            continuation = page.continuation
            if not continuation:
                break
    return found[:limit]


def _reading(m) -> str:
    """How a member is read: its text, or, for one with no text (an
    image), its picture."""
    return "render" if "render" in m.operations and "read_text" not in m.operations \
        else "read_text"


def _gap_action(r: Requirement, legacy_wrapper: bool) -> str:
    if r.operation == "execute":
        return "flatten_wrapper" if legacy_wrapper else "ask_recipient_to_update"
    if r.operation == "list_archive":
        return "add_file_index"
    if r.operation == "render" and r.format == "pdf":
        return "add_page_images"
    if r.operation == "read_text" and r.format == "pdf":
        return "add_pdf_text"
    if r.operation == "read_text" and r.format in OFFICE:
        return "add_portable_pdf"
    return "manual_review"


class SenderPreflight:
    """The check over the messaging store and the draft's snapshots.
    `inspection` is a BundleInspection over `DraftFiles` (with no observer:
    the sender's own looks are not openability); `network` the stored team
    network snapshot as JSON (None before the first exchange)."""

    def __init__(self, store, inspection, network: Callable[[], Optional[dict]],
                 clock: ports.Clock = ports.utcnow):
        self.store, self.inspection, self.network, self.clock = store, inspection, network, clock

    # -- what the message needs ------------------------------------------------------------

    def _requirements(self, owner: Owner, draft: ports.Draft
                      ) -> tuple[list[Requirement], list[Requirement], list[Classification],
                                 bool]:
        """(requirements, local problems, classifications, blocked here)."""
        needs: list[Requirement] = []
        problems: list[Requirement] = []
        found: list[Classification] = []
        blocked = False
        task = draft.task
        primary = task.primary if task else None
        for ref in draft.attachments:
            key = AttachmentKey(draft.message_id, ref.attachment_id)
            try:
                c = self.inspection.classify(owner, key)
                members = all_members(self.inspection, owner, key)
            except InspectionRefused as e:
                problems.append(Requirement(attachment_id=ref.attachment_id, format="unknown",
                                            operation="download"))
                blocked = True
                log.info("preflight: %s could not be inspected here (%s)", ref.attachment_id,
                         e.code)
                continue
            found.append(c)
            needs.append(Requirement(attachment_id=ref.attachment_id, format=c.format,
                                     operation="download"))
            if c.container != "none" and c.support in ("blocked", "unsupported"):
                problems.append(Requirement(attachment_id=ref.attachment_id, format=c.format,
                                            operation="list_archive"))
                blocked = True
                continue
            if c.container == "zip":
                needs.append(Requirement(attachment_id=ref.attachment_id, format="zip",
                                         operation="list_archive"))
                if c.package is not None and c.package.recognised:
                    needs.append(Requirement(attachment_id=ref.attachment_id, format="zip",
                                             operation="execute"))
            primary_id = member_id(ref.sha256, primary.member_path or "") \
                if primary and primary.attachment_id == ref.attachment_id else None
            for m in members:
                if m.is_dir:
                    continue
                req = Requirement(attachment_id=ref.attachment_id, member_id=m.member_id,
                                  path=m.path or None, format=m.format,
                                  operation=_reading(m))
                if m.support not in READABLE:
                    problems.append(req)
                    blocked = blocked or m.member_id == primary_id
                    continue
                needs.append(req)
                if m.member_id == primary_id and task.outcome_kind == "notes" \
                        and m.format == "pdf":
                    needs.append(req.model_copy(update={"operation": "render"}))
            if primary_id and all(m.member_id != primary_id for m in members):
                problems.append(Requirement(attachment_id=ref.attachment_id,
                                            member_id=primary_id, path=primary.member_path,
                                            format="unknown", operation="read_text"))
                blocked = True
        return needs, problems, found, blocked

    # -- the recipient's nodes -----------------------------------------------------------------

    def _nodes(self, recipient: int) -> list[dict]:
        snapshot = self.network() or {}
        return [n for n in (snapshot.get("nodes") or [])
                if isinstance(n, dict) and n.get("owner_github_id") == recipient]

    @staticmethod
    def _meets(caps: wire.AttachmentCapabilities, r: Requirement, sizes: dict) -> bool:
        if r.operation == "download":
            return sizes.get(r.attachment_id, 0) <= caps.max_file_bytes
        if r.operation == "execute":
            runs = sizes.get(("runs", r.attachment_id))
            return caps.work_packages is not None and runs in caps.work_packages.modes
        fmt = next((f for f in caps.formats if f.format == r.format), None)
        if fmt is None or r.operation not in fmt.operations:
            return False
        if r.operation == "list_archive":
            entries, expanded = sizes.get(("archive", r.attachment_id), (0, 0))
            return entries <= caps.max_archive_entries and expanded <= caps.max_expanded_bytes
        size = sizes.get((r.attachment_id, r.member_id), 0)
        return fmt.max_bytes is None or size <= fmt.max_bytes

    def preflight(self, server_id: str, owner_github_id: int,
                  message_id: UUID) -> PreflightResult:
        owner = Owner(server_id, owner_github_id)
        draft = self.store.draft(server_id, owner_github_id, message_id)
        if draft is None:
            raise InspectionRefused("attachment_not_found", "That draft is not here any more.")
        needs, problems, found, blocked = self._requirements(owner, draft)
        sizes: dict = {a.attachment_id: a.size_bytes for a in draft.attachments}
        for c in found:
            if c.inventory is not None:
                sizes[("archive", c.attachment.attachment_id)] = (
                    c.inventory.files + c.inventory.folders, c.inventory.expanded_bytes)
            if c.package is not None and c.package.recognised:
                sizes[("runs", c.attachment.attachment_id)] = c.package.runs
        now_ms = int(self.clock().timestamp() * 1000)
        fits: list[NodeFit] = []
        used: list[dict] = []
        for node in self._nodes(draft.recipient_github_id)[:16]:
            standing = capabilities.node_standing(node, now_ms)
            caps = capabilities.advertised(node) if standing == "advertised" else None
            if standing == "advertised" and caps is None:
                standing = "legacy"
            used.append({"node_id": str(node.get("node_id")), "standing": standing,
                         "attachments": node.get("attachments") if caps else None})
            if caps is None:
                fits.append(NodeFit(node_id=node["node_id"], label=str(node.get("label", ""))[:64],
                                    capabilities=standing, fit="unknown"))
                continue
            missing = [r for r in needs if not self._meets(caps, r, sizes)]
            fits.append(NodeFit(node_id=node["node_id"], label=str(node.get("label", ""))[:64],
                                capabilities=standing, fit="partial" if missing else "ready",
                                missing=missing[:GAPS_MAX]))
        advertised = [f for f in fits if f.capabilities == "advertised"]
        best = min(advertised, key=lambda f: len(f.missing), default=None)
        if blocked:
            readiness = "blocked"
        elif best is None:
            readiness = "unknown"
        else:
            readiness = "ready" if not best.missing else "partial"
        gaps = self._gaps(readiness, best, needs, found)
        return PreflightResult(
            message_id=message_id, content_revision=draft.send.content_revision,
            recipient_github_id=draft.recipient_github_id, readiness=readiness, nodes=fits,
            gaps=gaps, local_problems=problems[:GAPS_MAX],
            capability_digest="sha256:" + hashlib.sha256(json.dumps(
                sorted(used, key=lambda u: u["node_id"]), sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            checked_at=self.clock())

    @staticmethod
    def _gaps(readiness: str, best: Optional[NodeFit], needs: list[Requirement],
              found: list[Classification]) -> list[Gap]:
        """What the sender can do, one gap per action: for the best node's
        missing requirements, or, with no advertised node, the one repair a
        legacy recipient is known to need (a work package in a wrapper
        folder) and an update."""
        wrapped = {c.attachment.attachment_id for c in found
                   if c.package is not None and c.package.recognised and c.inventory is not None
                   and c.inventory.wrapper}
        if readiness == "partial" and best is not None:
            missing = best.missing
        elif readiness == "unknown":
            missing = [r for r in needs if r.operation == "execute"]
        else:
            return []
        gaps: dict[str, Gap] = {}
        for r in missing:
            action = _gap_action(r, readiness == "unknown" and r.attachment_id in wrapped)
            gaps.setdefault(action, Gap(requirement=r, action=action))
        return list(gaps.values())[:GAPS_MAX]

    def record_for_approval(self, server_id: str, owner_github_id: int, message_id: UUID,
                            content_revision: int, result: PreflightResult) -> None:
        """Keep the check with the revision the owner reviews (and so
        approves): the capability snapshot it used goes with it."""
        if result.message_id != message_id or result.content_revision != content_revision:
            raise ValueError("a preflight is stored only with the revision it checked")
        self.store.save_preflight(server_id, owner_github_id, message_id, content_revision,
                                  result.capability_digest, result.readiness,
                                  result.model_dump_json())

    def recorded(self, server_id: str, owner_github_id: int, message_id: UUID,
                 content_revision: int) -> Optional[PreflightResult]:
        body = self.store.preflight(server_id, owner_github_id, message_id, content_revision)
        return PreflightResult.model_validate_json(body) if body else None


# -- the bound service ------------------------------------------------------------------------
#
# messaging_runtime builds the one check at startup and binds it here; the
# routes and the brain's attachment_readiness reach it through these.

_bound: Optional[SenderPreflight] = None


def bind(service: Optional[SenderPreflight]) -> None:
    global _bound
    _bound = service


def _service() -> SenderPreflight:
    if _bound is None:
        raise InspectionRefused("messaging_unavailable", "Messaging is not available.")
    return _bound


def preflight(server_id: str, owner_github_id: int, message_id: UUID) -> PreflightResult:
    """The draft's current revision against its recipient's nodes."""
    return _service().preflight(server_id, owner_github_id, message_id)


def record_for_approval(server_id: str, owner_github_id: int, message_id: UUID,
                        content_revision: int, result: PreflightResult) -> None:
    _service().record_for_approval(server_id, owner_github_id, message_id, content_revision,
                                   result)
