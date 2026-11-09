"""Evidence-bound preparation through the existing capture service.

The proven inbox sender is the only implicit recipient. No send function,
queue or approval flag is introduced here.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from jarvis import agent_directory, package_inbox, transmission
from jarvis.package_content_contract import *

class PackageFollowups:
    def __init__(self, inspection, authority, captures, new_capture, preparation):
        self.inspection, self.authority = inspection, authority
        self.captures, self.new_capture, self.preparation = captures, new_capture, preparation

    def context_guard(self, context, recipient):
        try:
            context = PackageCaptureContext.model_validate(context)
            if recipient != context.senderSession:
                return 'The recipient differs from the approved package follow-up.'
            ref = PackageRef(package_id=context.packageId, inbox_id=context.inboxId)
            current = self.inspection.snapshots.capture(self.owner(), ref)
            if (current.content_revision, current.results_revision) != (context.contentRevision, context.resultsRevision):
                return 'The cited package source or results changed. Keep this draft and review a refreshed draft before sending.'
            entry = package_inbox.get(context.inboxId)
            if entry is None or not context.senderSession or not agent_directory.find_sessions(context.senderSession):
                return 'The exact recipient session is unavailable.'
            if context.senderEvidence and (entry.from_session != context.senderSession or entry.sender_evidence != context.senderEvidence):
                return 'The proven sender binding changed.'
        except (InspectionError, ValueError):
            return 'The package evidence binding is unavailable; review the draft again.'
        return None

    async def prepare(self, *, owner, request, authority):
        self.authority.require(authority, package=request.package, operation='prepare_followup',
                               recipient=request.explicit_recipient_session)
        if request.package.inbox_id is None:
            raise InspectionError('routing_problem', 'A follow-up requires an exact inbox arrival.')
        entry = package_inbox.get(request.package.inbox_id)
        if entry is None or entry.package_id != request.package.package_id:
            raise InspectionError('package_not_found', 'That exact inbox arrival is unavailable.')
        snapshot = await asyncio.to_thread(self.inspection.snapshots.capture, owner, request.package)
        if (snapshot.content_revision, snapshot.results_revision) != (request.expected_content_revision, request.expected_results_revision):
            raise InspectionError('revision_mismatch', 'The package changed since the requested evidence was read. The original draft is retained.')
        evidence = [self.inspection.resolve_evidence(owner, eid) for eid in request.evidence_ids]
        if any(e.snapshot.package != request.package or e.snapshot.content_revision != snapshot.content_revision
               or e.snapshot.results_revision != snapshot.results_revision for e in evidence):
            raise InspectionError('revision_mismatch', 'Every cited passage must belong to the requested source and results snapshot.')
        members = {m.member_id: m for m in self.inspection.snapshots.members(owner, snapshot)}
        citations = []
        for item in evidence:
            location = item.location
            if location:
                title = members[location.member_id].relative_path
                if location.slide_id:
                    title += " slide " + location.slide_id
                if location.beat_id:
                    title += " beat " + location.beat_id
                if location.location:
                    title += " " + location.location.model_dump_json()
                citations.append(title)
            else:
                citations.append("Package inventory")
        outgoing = (request.text.rstrip() + f"\n\nPackage: {entry.name} {entry.version}."
                    + f"\nSource revision: {snapshot.content_revision}."
                    + f"\nResults revision: {snapshot.results_revision}."
                    + ("\nEvidence: " + "; ".join(dict.fromkeys(citations)) if citations else ""))
        if len(outgoing) > 4000:
            raise InspectionError("draft_too_long", "The draft and its source references exceed 4,000 characters; condense the requested wording or split it into separately reviewable messages.")
        with self.inspection.snapshots.db() as conn:
            has_messages = conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone()
            sent = conn.execute("SELECT id,status,capture_id FROM messages WHERE json_extract(package_context,'$.inboxId')=? AND json_extract(package_context,'$.requestKey')=? ORDER BY id DESC LIMIT 1", (entry.id, request.request_key)).fetchone() if has_messages else None
        if sent:
            raise InspectionError("existing_transmission", f"This request already has message {sent['id']} with status {sent['status']}; reconcile that transmission before preparing another.")
        recipient = request.explicit_recipient_session or (entry.from_session if entry.sender_evidence else None)
        found = agent_directory.find_sessions(recipient) if recipient else []
        exact = next((s for s in found if s.session_id == recipient), None)
        context = PackageCaptureContext(inboxId=entry.id, packageId=entry.package_id,
            contentRevision=snapshot.content_revision, resultsRevision=snapshot.results_revision,
            evidenceIds=list(request.evidence_ids), evidenceDigest=hashlib.sha256(json.dumps(
                [e.model_dump(mode='json') for e in evidence], sort_keys=True).encode()).hexdigest(),
            senderSession=recipient, senderEvidence=entry.sender_evidence if not request.explicit_recipient_session else None,
            workId=entry.work_id, workflowId=entry.workflow_id, requestKey=request.request_key)
        if exact is None:
            capture = self.new_capture()
            self.preparation.set(capture, outgoing, '', None, package_context=context.model_dump(mode='json'))
            capture['routing_problem'] = 'The package has no live proven recipient.'
            raise InspectionError('routing_problem', f'Capture {capture["id"]} holds the draft; the package has no live proven recipient.')
        previous = next((c for c in self.captures().values() if (c.get('package_context') or {}).get('requestKey') == request.request_key
                         and (c.get('package_context') or {}).get('inboxId') == entry.id), None)
        if previous and previous.get('staged'):
            raise InspectionError('already_staged', 'This unchanged follow-up already has a transmission record.')
        capture = previous or self.new_capture()
        self.preparation.set(capture, outgoing, exact.voice_name, recipient,
                             package_context=context.model_dump(mode='json'))
        return PreparedFollowup(capture_id=capture['id'], recipient_session=recipient,
                                package=request.package, evidence_ids=request.evidence_ids)
