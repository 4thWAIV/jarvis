"""Bounded MCP contract and adapter for package inspection.

Only this adapter budgets final wrapped output. Continuations preserve the
exact source offsets even when JSON escaping makes a reader page too big.
"""
from __future__ import annotations

import json
import time
from typing import Literal
from pydantic import Field
from jarvis.package_content_contract import *

class InspectRequest(Shape):
    package: str
    expected_revision: str | None = None
    continuation: str | None = None

class ContentRequest(Shape):
    snapshot: str
    member: str
    part: Literal['source', 'results', 'narration'] = 'source'
    location: Location | None = None
    slide: str | None = None
    element: str | None = None
    continuation: str | None = None

class SearchRequest(Shape):
    snapshot: str
    query: str
    continuation: str | None = None

class ViewRequest(Shape):
    package: str
    client: str | None = None
    continuation: str | None = None

class LookRequest(Shape):
    snapshot: str | None = None
    member: str | None = None
    package: str | None = None
    client: str | None = None
    evidence_kind: Literal['reconstructed_render', 'screen_capture']
    page: int = Field(default=1, ge=1)
    display: int = Field(default=1, ge=1)

class PrepareRequest(Shape):
    package: str
    expected_content_revision: str
    expected_results_revision: str
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(max_length=40)
    recipient: str | None = None
    request_key: str = Field(min_length=1, max_length=128)

TOOL_MODELS = {
    'inspect_package': (InspectRequest, 'Inventory and read capabilities for an exact package or inbox ID. Continue to retrieve every member. Opening alone does not read all content.'),
    'read_package_content': (ContentRequest, 'Read exact snapshot member text or narration, with source citations. Continue until complete; never treat narration as visible page content.'),
    'search_package_content': (SearchRequest, 'Find literal text in the indexed part of a package snapshot. Coverage reports unread and pending members; no match does not establish absence.'),
    'observe_package_view': (ViewRequest, 'Request fresh visible package DOM and selection from the originating client. Does not navigate. Current view and narration are independent.'),
    'look_at_package_content': (LookRequest, 'Inspect source image/PDF or a constrained HTML reconstructed render, or deliberately capture the selected local display. Returns image bytes and evidence kind. Remote capture may be unavailable.'),
    'prepare_package_followup': (PrepareRequest, 'On a direct request to ask or instruct an agent, prepare an evidence-bound capture for the proven sender or explicit recipient. Preparation never approves or sends. Read the capture back and use existing Send flow.'),
}

def tool_specs():
    return [{'name': name, 'description': description, 'inputSchema': model.model_json_schema()}
            for name, (model, description) in TOOL_MODELS.items()]


class PackageToolPager:
    def __init__(self, inspection, wrap):
        self.inspection, self.wrap = inspection, wrap

    def resume(self, owner, snapshot, tool, args, continuation):
        if not continuation:
            return None
        found = self.inspection._cursors.get(continuation)
        if found is None or found.operation != 'tool:' + tool:
            return None
        binding = json.dumps({k: v for k, v in args.items() if k != 'continuation'}, sort_keys=True)
        return self.inspection.resume(owner, snapshot, 'tool:' + tool, binding, continuation)

    def emit(self, owner, snapshot, tool, args, records, *, reader_next=None, coverage=None, state=None):
        binding = json.dumps({k: v for k, v in args.items() if k != 'continuation'}, sort_keys=True)
        if state:
            records, reader_next, coverage = state['records'], state['reader_next'], state['coverage']
        records = [dict(r) for r in records]
        if not records:
            records = [{'kind': 'summary', 'text': 'No match in indexed text.'}]
        record = records[0]
        original = record.pop('text')
        start = int(record.pop('text_start', 0))
        take = min(700, len(original))
        while True:
            remains = ([{**record, 'text': original[take:], 'text_start': start + take}] if take < len(original) else []) + records[1:]
            next_token = self.inspection.cursor(owner, snapshot, 'tool:' + tool, binding,
                {'records': remains, 'reader_next': reader_next, 'coverage': coverage}) if remains else reader_next
            envelope = {**record, 'snapshot': snapshot.snapshot_id, 'text': original[:take],
                'text_start': start, 'text_end': start + take, 'complete': next_token is None,
                'next': next_token, **({'coverage': coverage} if coverage else {})}
            raw = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e')
            wrapped = self.wrap(raw)
            # The existing wrapper has its own body budget. Never invoke its truncator.
            if len(raw) <= 1200 and len(wrapped) <= TOOL_CAP:
                return wrapped
            if next_token and remains:
                self.inspection._cursors.pop(next_token, None)
            if take <= 1:
                raise InspectionError('budget', 'The evidence envelope exceeds the final tool budget.')
            take = max(1, take // 2)

    @staticmethod
    def compact_coverage(coverage):
        return {'inventory_complete': coverage.inventory_complete, 'text_complete': coverage.text_complete,
                'pending': coverage.pending_members, 'unread': coverage.unread_members,
                'unreadable': coverage.unreadable_members, 'dynamic': coverage.dynamic_coverage,
                'gaps': [g[:80] for g in coverage.gaps[:3]]}

class PackageToolAdapter:
    def __init__(self, inspection, views, owner, wrap, image, turn_view):
        self.inspection, self.views, self.owner = inspection, views, owner
        self.pager, self.image, self.turn_view = PackageToolPager(inspection, wrap), image, turn_view
        self.image_available = lambda: True

    def visual_text(self, visual):
        raw = json.dumps({"evidence_id": visual.evidence.evidence_id, "kind": visual.evidence.kind,
            "image": visual.image.model_dump(mode="json"), "limits": visual.limits,
            "sequence_before": visual.sequence_before, "sequence_after": visual.sequence_after,
            "complete": True, "next": None}, separators=(",", ":"))
        if len(raw) > 1200 or len(self.pager.wrap(raw)) > TOOL_CAP:
            raise InspectionError("budget", "The visual evidence envelope exceeds the tool result budget.")
        return self.pager.wrap(raw)

    def package_ref(self, reference):
        from jarvis import package_inbox, work_packages
        entry = package_inbox.get(reference)
        if entry:
            return PackageRef(package_id=entry.package_id, inbox_id=entry.id)
        package = work_packages.find(reference)
        if package.id != reference:
            raise InspectionError('ambiguous_package', 'Use the exact package or inbox ID.')
        entries = package_inbox.for_package(package.id)
        return PackageRef(package_id=package.id, inbox_id=entries[0].id if len(entries) == 1 else None)

    async def call(self, tool, args):
        import asyncio
        try:
            model = TOOL_MODELS[tool][0].model_validate(args)
            owner = self.owner()
            if tool == 'inspect_package':
                reference = self.package_ref(model.package)
                found = self.inspection._cursors.get(model.continuation) if model.continuation else None
                if model.continuation and found is None:
                    raise InspectionError("cursor_expired", "The inspection continuation expired; restart explicitly rather than substituting the latest package.")
                if found:
                    snapshot = found.snapshot
                    state = self.pager.resume(owner, snapshot, tool, args, model.continuation)
                    if state:
                        return self.pager.emit(owner, snapshot, tool, args, [], state=state)
                    if found.operation == "structure":
                        result = await self.inspection.structure(owner, snapshot, model.continuation)
                        records = [{"evidence_id": chunk.evidence.evidence_id, "kind": "summary", "location": "Source outline",
                            "text": chunk.text, "text_start": chunk.evidence.location.text_start or 0} for chunk in result.chunks]
                        return self.pager.emit(owner, snapshot, tool, args, records,
                            reader_next=result.next, coverage=self.pager.compact_coverage(result.coverage))
                    if found.operation == "members":
                        page = await self.inspection.members(owner=owner, snapshot=snapshot, continuation=model.continuation)
                        evidence = self.inspection.evidence(owner, snapshot, None, "summary")
                        return self.pager.emit(owner, snapshot, tool, args,
                            [{"evidence_id": evidence.evidence_id, "kind": "summary", "location": "Package inventory", "text": page.model_dump_json()}], reader_next=page.next or self.inspection.structure_start(owner, snapshot))
                overview = await self.inspection.inspect(owner=owner, package=reference, expected_revision=model.expected_revision)
                evidence = self.inspection.evidence(owner, overview.snapshot, None, 'summary')
                # Overview is exact structured metadata paged inside a JSON text field.
                records = [{'evidence_id': evidence.evidence_id, 'kind': 'summary',
                            'location': 'Package inventory', 'text': overview.model_dump_json()}]
                return self.pager.emit(owner, overview.snapshot, tool, args, records,
                    reader_next=overview.next or self.inspection.structure_start(owner, overview.snapshot), coverage=self.pager.compact_coverage(overview.coverage))
            if tool in {'read_package_content', 'search_package_content'}:
                snapshot = self.inspection.snapshots.get(owner, model.snapshot)
                state = self.pager.resume(owner, snapshot, tool, args, model.continuation)
                if state:
                    return self.pager.emit(owner, snapshot, tool, args, [], state=state)
                if tool == 'read_package_content':
                    result = await self.inspection.read(owner=owner, snapshot=snapshot,
                        location=PackageLocation(member_id=model.member, part=model.part, location=model.location,
                            slide_id=model.slide, element_id=model.element), continuation=model.continuation)
                else:
                    result = await self.inspection.search(owner=owner, snapshot=snapshot,
                        query=model.query, continuation=model.continuation)
                members = {m.member_id: m for m in self.inspection.snapshots.members(owner, snapshot)}
                records = []
                for chunk in result.chunks:
                    loc = chunk.evidence.location
                    title = members[loc.member_id].relative_path if loc else 'Package content'
                    if loc and loc.location:
                        title += ' ' + loc.location.model_dump_json()
                    records.append({'evidence_id': chunk.evidence.evidence_id, 'kind': chunk.evidence.kind,
                        'location': title[:100], 'text': chunk.text,
                        'text_start': loc.text_start or 0 if loc else 0})
                return self.pager.emit(owner, snapshot, tool, args, records, reader_next=result.next,
                                       coverage=self.pager.compact_coverage(result.coverage))
            if tool == 'observe_package_view':
                if model.continuation:
                    found = self.inspection._cursors.get(model.continuation)
                    if found:
                        state = self.pager.resume(owner, found.snapshot, tool, args, model.continuation)
                        if state:
                            for record in state["records"]:
                                record["current"] = False
                            return self.pager.emit(owner, found.snapshot, tool, args, [], state=state)
                    observation = await self.views.continue_observation(owner=owner, continuation=model.continuation)
                else:
                    observation = await self.views.observe(owner=owner, package=self.package_ref(model.package),
                        client_id=model.client, turn_view=self.turn_view())
                # Keep the source of 'this', selection, and narration separate in the returned data.
                text = json.dumps({'view': observation.view.model_dump(mode='json'), 'current_at_receipt': observation.current,
                    'location': observation.active_location.model_dump(mode='json') if observation.active_location else None,
                    'selection': observation.selected_text, 'narration': observation.narration.model_dump(mode='json') if observation.narration else None,
                    'text': '\n'.join(c.text for c in observation.content.chunks if c.evidence.kind == 'reported_dom'),
                    'source_evidence': [c.model_dump(mode='json') for c in observation.content.chunks if c.evidence.kind != 'reported_dom'], 'limits': observation.limits}, ensure_ascii=False)
                return self.pager.emit(owner, observation.evidence.snapshot, tool, args,
                    [{'evidence_id': observation.evidence.evidence_id, 'kind': 'reported_dom',
                      'location': 'Observed package view', 'current': observation.current, 'text': text}], reader_next=observation.content.next,
                    coverage=self.pager.compact_coverage(observation.content.coverage))
            if tool == 'look_at_package_content':
                if not self.image_available():
                    raise InspectionError('image_input_unavailable', 'The current brain cannot inspect image input; text remains readable.')
                from jarvis.bundle_contract import PageLocation, RenderOptions
                if model.evidence_kind == 'screen_capture':
                    if not model.package:
                        raise InspectionError('ambiguous_view', 'Choose the package and visible local client to capture.')
                    mount = self.views.choose(owner, self.package_ref(model.package), model.client, self.turn_view())
                    visual = await self.views.capture(owner=owner, view=mount.view, display=model.display)
                    data, media_type = self.inspection.images.resolve(owner, visual.image.imageRef.rsplit('/', 1)[-1])
                    return self.image(self.visual_text(visual), data, media_type)
                if not model.snapshot or not model.member:
                    raise InspectionError('member_required', 'Choose a source snapshot and member to render.')
                snapshot = self.inspection.snapshots.get(owner, model.snapshot)
                visual = await self.inspection.render(owner=owner, snapshot=snapshot,
                    location=PackageLocation(member_id=model.member, location=PageLocation(page=model.page)), options=RenderOptions())
                data, media_type = self.inspection.images.resolve(owner, visual.image.imageRef.rsplit('/', 1)[-1])
                return self.image(self.visual_text(visual), data, media_type)
            raise InspectionError('unsupported_operation', 'This adapter does not prepare transmissions.')
        except InspectionError as error:
            return self.pager.wrap(error.failure.model_dump_json())
        except (ValueError, KeyError) as error:
            return self.pager.wrap(json.dumps({'code': 'bad_request', 'explanation': str(error)[:300]}))
