"""Authenticated host mounts and fresh sandbox observations.

Only the registered voice connection supplies client identity. Frame text
is always reported_dom. Narration is an independent optional reference.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque

from pydantic import ValidationError
from jarvis import package_story, screen, story_teller
from jarvis.package_content_contract import *
from jarvis.package_snapshots import owner_key


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Mount:
    connection: object
    owner: object
    view: ViewRef
    snapshot: SnapshotRef
    nonce: str
    focused: bool
    visible: bool
    local: bool
    received: str = field(default_factory=utc_now)
    observations: deque = field(default_factory=lambda: deque(maxlen=2))


class PackageViews:
    def __init__(self, inspection, send, narration=lambda: None):
        self.inspection, self.send, self.narration = inspection, send, narration
        self.mounts = {}
        self.pending = {}
        self.turn_view = None
        self.documents = {}
        self.document_pending = {}

    def mount(self, connection, owner, client_id, message, *, local=False):
        # The connection's authenticated ui_hello supplies client_id.
        if not client_id:
            raise InspectionError("ambiguous_view", "The page has no registered client identity.")
        package_id, sid = message.get("packageId"), message.get("snapshotId")
        if not isinstance(package_id, str) or not isinstance(sid, str):
            raise InspectionError("bad_view", "A mount requires a pinned package snapshot.")
        snapshot = self.inspection.snapshots.get(owner, sid)
        if snapshot.package.package_id != package_id:
            raise InspectionError("revision_mismatch", "The mount package differs from its snapshot.")
        mount_id = "mount-" + secrets.token_hex(12)
        view_id = "view-" + secrets.token_hex(12)
        previous = [m for m in self.mounts.values() if m.connection is connection and m.view.client_id == client_id]
        for old in previous:
            self.close(connection, old.view.mount_id)
        view = ViewRef(client_id=client_id, view_id=view_id, mount_id=mount_id,
            package=snapshot.package, content_revision=snapshot.content_revision, sequence=0)
        self.inspection.snapshots.lease(owner, snapshot, mount_id, 24 * 3600)
        mount = Mount(connection, owner, view, snapshot, secrets.token_urlsafe(24),
                      message.get("focused") is True, message.get("visible") is True, local)
        self.mounts[mount_id] = mount
        return {"type": "package_view_bound", "packageId": package_id, "mountId": mount_id,
                "viewId": view_id, "mountNonce": mount.nonce, "contentRevision": snapshot.content_revision}

    def close(self, connection, mount_id=None):
        if mount_id is None:
            self.documents.pop(connection, None)
        for mid, mount in list(self.mounts.items()):
            if mount.connection is connection and (mount_id is None or mount_id == mid):
                self.inspection.snapshots.cache.release(mid)
                del self.mounts[mid]
                for rid, (pending_mount, future) in list(self.pending.items()):
                    if pending_mount is mount:
                        if not future.done():
                            future.set_exception(InspectionError("view_unavailable", "The package view closed."))
                        del self.pending[rid]

    def update(self, connection, message):
        mount = self.mounts.get(message.get("mountId"))
        if mount is None or mount.connection is not connection:
            raise InspectionError("stale_mount", "That package mount is no longer live.")
        if message.get("type") == "package_view_state":
            mount.focused, mount.visible = message.get("focused") is True, message.get("visible") is True
            return
        raw = message.get("response")
        if not isinstance(raw, dict) or len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()) > OBSERVATION_BYTES:
            raise InspectionError("observation_limit", "The frame response exceeds its byte limit.")
        try:
            response = ObserveResponse.model_validate(raw, strict=True)
        except ValidationError:
            raise InspectionError("bad_observation", "The frame observation has an invalid schema.") from None
        if response.mountNonce != mount.nonce or response.sequence < mount.view.sequence:
            raise InspectionError("stale_observation", "The observation belongs to an older mount or sequence.")
        pending = self.pending.get(response.requestId)
        if pending is None or pending[0] is not mount:
            raise InspectionError("unsolicited_observation", "No current request matches this observation.")
        mount.view = mount.view.model_copy(update={"sequence": response.sequence})
        mount.received = utc_now()
        mount.observations.append(response)
        if not pending[1].done():
            pending[1].set_result(response)

    def changed(self, connection, message):
        mount = self.mounts.get(message.get("mountId"))
        raw = message.get("response")
        if mount is None or mount.connection is not connection or not isinstance(raw, dict):
            return
        if raw.get("mountNonce") != mount.nonce or raw.get("protocol") != 1:
            return
        sequence = raw.get("sequence")
        if type(sequence) is int and sequence > mount.view.sequence:
            mount.view = mount.view.model_copy(update={"sequence": sequence})

    def document_update(self, connection, client_id, message):
        state = message.get("state")
        if state is None:
            self.documents.pop(connection, None)
            return
        try:
            state = DocumentViewState.model_validate(state, strict=True)
        except ValidationError:
            raise InspectionError("bad_view", "The document viewer returned invalid state.") from None
        self.documents[connection] = (client_id, state, utc_now())
        rid = message.get("requestId")
        pending = self.document_pending.get(rid)
        if pending and pending[0] is connection and not pending[1].done():
            pending[1].set_result(state)

    async def observe_document(self, client_id):
        rows = [(ws, state) for ws, (client, state, _) in self.documents.items()
                if client == client_id and state.visible]
        if len(rows) != 1:
            raise InspectionError("ambiguous_view", "The turn has no unambiguous visible document viewer.")
        connection, _ = rows[0]
        rid = secrets.token_urlsafe(18)
        future = asyncio.get_running_loop().create_future()
        self.document_pending[rid] = (connection, future)
        try:
            self.send(connection, {"type": "document_observe", "requestId": rid})
            return await asyncio.wait_for(future, 2)
        except asyncio.TimeoutError:
            raise InspectionError("view_unavailable", "The document viewer did not answer a fresh observation.") from None
        finally:
            self.document_pending.pop(rid, None)

    def choose(self, owner, package, client_id=None, turn_view=None):
        rows = [m for m in self.mounts.values() if owner_key(m.owner) == owner_key(owner)
            and m.view.package.package_id == package.package_id and m.visible]
        if package.inbox_id:
            rows = [m for m in rows if m.view.package.inbox_id == package.inbox_id]
        if client_id:
            rows = [m for m in rows if m.view.client_id == client_id]
        elif turn_view:
            rows = [m for m in rows if m.view.mount_id == turn_view.mount_id]
        else:
            rows = []
        if len(rows) != 1:
            raise InspectionError("ambiguous_view" if len(rows) > 1 or not client_id and not turn_view else "view_unavailable",
                                  "Name the visible client for this package; the current turn has no unambiguous view.")
        return rows[0]

    def freeze_turn(self, client_id):
        rows = [m for m in self.mounts.values() if m.view.client_id == client_id and m.visible and m.focused]
        return rows[0].view if len(rows) == 1 else None

    async def _request(self, mount, continuation=None):
        rid = secrets.token_urlsafe(18)
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = (mount, future)
        try:
            self.send(mount.connection, {"type": "package_observe", "mountId": mount.view.mount_id,
                "request": {"jarvis": "package", "type": "observe.request", "protocol": 1,
                            "mountNonce": mount.nonce, "requestId": rid, **({"continuation": continuation} if continuation else {})}})
            return await asyncio.wait_for(future, 2)
        except asyncio.TimeoutError:
            raise InspectionError("view_unavailable", "The visible package did not answer a fresh observation within two seconds.", retryable=True) from None
        finally:
            self.pending.pop(rid, None)

    def _observation(self, owner, mount, response, current):
        mount.snapshot = self.inspection.snapshots.refresh_results(owner, mount.snapshot)
        members = self.inspection.snapshots.members(owner, mount.snapshot)
        member = next((m for m in members if m.relative_path == "deck-data.js"), None)
        if member is None:
            member = next((m for m in members if m.detected_format == "html"), None)
        active = PackageLocation(member_id=member.member_id, slide_id=response.locator.slideId,
            element_id=response.locator.elementId) if member else None
        evidence = self.inspection.evidence(owner, mount.snapshot, active, "reported_dom",
            view_id=mount.view.view_id, sequence=response.sequence, observed_at=mount.received)
        image_refs = []
        for image in response.images:
            from urllib.parse import unquote, urlsplit
            path = unquote(urlsplit(image.source).path)
            marker = "/files/@snapshots/" + mount.snapshot.content_revision + "/"
            local_path = path.split(marker, 1)[1] if marker in path else None
            image_member = next((m for m in members if m.relative_path == local_path), None)
            image_refs.append({"member_id": image_member.member_id if image_member else None,
                               "source": image.source, "alt": image.alt})
        text = "\n".join(b.text for b in response.blocks)
        if image_refs:
            text += "\nReported image references (pixels not inspected): " + json.dumps(image_refs)
        if response.locator.tabId or response.locator.openDetails:
            text += "\nReported locator: " + response.locator.model_dump_json()
        limits = list(response.limits)
        source_chunks = []
        html_member = next((m for m in members if m.detected_format == "html"), None)
        if html_member:
            from jarvis import bundle_readers
            source = self.inspection.snapshots.source(owner, mount.snapshot, html_member.member_id)
            try:
                outline = bundle_readers.reader_for("html").outline(source)
                for heading in outline["headings"]:
                    identifier = heading.get("element_id")
                    if not identifier:
                        continue
                    reported = next((b for b in response.blocks if b.role == "heading" and b.sourceElementId == identifier), None)
                    if reported and " ".join(reported.text.split()) != " ".join(heading["text"].split()):
                        limits.append("content_mismatch")
                        reference = self.inspection.evidence(owner, mount.snapshot,
                            PackageLocation(member_id=html_member.member_id, element_id=identifier), "source_text")
                        source_chunks.append(ContentChunk(evidence=reference, text=heading["text"]))
            except (InspectionError, ValueError):
                limits.append("source_correspondence_unavailable")
        chunks = ((ContentChunk(evidence=evidence, text=text),) if text else ()) + tuple(source_chunks)

        next_cursor = self.inspection.cursor(owner, mount.snapshot, "observe", mount.view.mount_id,
            {"mount_id": mount.view.mount_id, "continuation": response.continuation,
             "sequence": response.sequence}) if response.continuation else None
        content = ContentPage(chunks=chunks, coverage=self.inspection.coverage(owner, mount.snapshot,
            complete=response.complete, dynamic="observed_state", gaps=limits),
            complete=response.complete, next=next_cursor)
        # A story cue is not proof of a completed spoken beat.
        status = self.narration()
        narration = None
        if status and status.get("package") == mount.view.package.package_id:
            narration = NarrationRef(package=mount.view.package,
                story_revision=status.get("storyRevision") or "unverified", slide_id=str(status.get("slide") or ""),
                playback=status["state"], completed_through_beat=status.get("completedThroughBeat"))
        return ViewObservation(view=mount.view.model_copy(update={"sequence": response.sequence}),
            evidence=evidence, received_at=mount.received, focused=mount.focused,
            visible=mount.visible, current=current and mount.visible,
            active_location=active, content=content, narration=narration,
            selected_text=response.selection.text if response.selection else None,
            limits=tuple(dict.fromkeys(limits)))

    async def observe(self, *, owner, package, client_id=None, turn_view=None):
        mount = self.choose(owner, package, client_id, turn_view)
        for attempt in range(2):
            response = await self._request(mount)
            if mount.view.sequence == response.sequence:
                return self._observation(owner, mount, response, True)
        raise InspectionError("view_changed", "The view changed while its evidence was being collected.", retryable=True)

    async def continue_observation(self, *, owner, continuation):
        found = self.inspection._cursors.get(continuation)
        if found is None:
            raise InspectionError("cursor_expired", "The observation continuation expired.")
        state = self.inspection.resume(owner, found.snapshot, "observe", found.binding, continuation)
        mount = self.mounts.get(state["mount_id"])
        if mount is None or owner_key(mount.owner) != owner_key(owner):
            raise InspectionError("view_unavailable", "The view that supplied this observation has closed.")
        response = await self._request(mount, state["continuation"])
        return self._observation(owner, mount, response, response.sequence == mount.view.sequence)

    async def capture(self, *, owner, view, display):
        mount = self.choose(owner, view.package, view.client_id, view)
        if not mount.local:
            raise InspectionError("capture_unavailable_for_client", "This remote browser has no granted display capture.")
        if type(display) is not int or display < 1:
            raise InspectionError("bad_display", "Choose a display number from one.")
        for attempt in range(2):
            before = mount.view.sequence
            try:
                shot = await screen.capture_screen(display)
            except screen.ScreenError as e:
                raise InspectionError("capture_unavailable", str(e)) from None
            after = mount.view.sequence
            if before == after:
                evidence = self.inspection.evidence(owner, mount.snapshot, None, "screen_capture",
                    view_id=view.view_id, sequence=after, observed_at=utc_now())
                return self.inspection.images.screen(owner, evidence, shot, {"display": display,
                    "sequence_before": before, "sequence_after": after,
                    "limits": ["display_pixels_package_association_unverified"]})
        raise InspectionError("view_changed", "The package view changed during both display captures.", retryable=True)
