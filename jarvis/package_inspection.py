"""Package retrieval over immutable snapshots and the shared reader core.

All cursors are opaque, owner-bound and point at one operation on one exact
snapshot. Index completion and user reading are recorded independently.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass

from jarvis import bundle_readers, package_inbox, package_story, work_packages
from jarvis.bundle_contract import LineLocation, SlideLocation, RenderOptions
from jarvis.bundle_readers import Description, TextSlice, Unsupported, _paging
from jarvis.inspection_core import InspectionCore
from jarvis.package_content_contract import *
from jarvis.package_snapshots import PackageSnapshots, owner_key


class _StructuredReader:
    name, version = "package-structure", 1

    def __init__(self, path):
        self.path = path

    def describe(self, source, detected):
        from jarvis.bundle_contract import Extent
        return Description("readable", Extent(), operations=frozenset({"read_text"}))

    def read_text(self, source, detected, cursor, max_chars):
        raw = source.read(16 * 1024 * 1024)
        units = []
        if self.path == "deck-data.js":
            data = package_inbox.read_deck_data(raw.decode("utf-8"))
            if data is None:
                raise Unsupported("corrupt")
            # Retain every supported block, caption and decision association.
            for n, slide in enumerate(data.get("slides") or [], 1):
                if not isinstance(slide, dict):
                    continue
                text = json.dumps(slide, ensure_ascii=False, indent=2) + "\n"
                units.append(_paging.Unit(str(slide.get("id") or n), n, n, text))
            for n, decision in enumerate(data.get("decisions") or [], len(units) + 1):
                if isinstance(decision, dict):
                    units.append(_paging.Unit(str(decision.get("id") or n), n, n,
                                               json.dumps(decision, ensure_ascii=False, indent=2) + "\n"))
        else:
            story = package_story.parse(raw)
            for n, slide in enumerate(story.slides, 1):
                for beat_index, beat in enumerate(slide.beats, 1):
                    text = json.dumps({"slide": slide.title, "target": slide.target,
                        "beat": beat_index, "say": beat.say, "targets": beat.targets,
                        "actions": beat.actions}, ensure_ascii=False) + "\n"
                    units.append(_paging.Unit(f"{n}:{beat_index}", n, n, text))
        chunks, after = _paging.page(units, cursor, max_chars,
            lambda section, start, end: SlideLocation(slide=start))
        return TextSlice(chunks, after)


@dataclass
class Cursor:
    owner: str
    snapshot: SnapshotRef
    operation: str
    binding: str
    data: dict
    expires: float


class PackageInspection:
    def __init__(self, snapshots: PackageSnapshots):
        self.snapshots = snapshots
        self.core = InspectionCore(snapshots.cache)
        from jarvis.package_images import PackageImages
        self.images = PackageImages()
        self._cursors: dict[str, Cursor] = {}
        self._evidence: dict[str, tuple[str, EvidenceRef]] = {}
        self._jobs: dict[tuple[str, str], asyncio.Task] = {}
        self._workers = asyncio.Semaphore(2)
        self._read: dict[tuple[str, str], set[str]] = {}
        self._visual: dict[tuple[str, str], set[str]] = {}
        self._mutex = threading.RLock()
        self._thread_workers = threading.BoundedSemaphore(2)

    def cursor(self, owner, snapshot, operation, binding, data):
        now = time.monotonic()
        self._cursors = {k: v for k, v in self._cursors.items() if v.expires > now}
        if len(self._cursors) >= 4096:
            raise InspectionError("budget", "Too many live inspection continuations.")
        token = secrets.token_urlsafe(18)
        self._cursors[token] = Cursor(owner_key(owner), snapshot, operation, binding,
                                      data, now + CURSOR_SECONDS)
        return token

    def resume(self, owner, snapshot, operation, binding, token):
        self.snapshots.get(owner, snapshot.snapshot_id)
        found = self._cursors.get(token)
        if found is None or found.expires <= time.monotonic():
            raise InspectionError("cursor_expired", "The continuation expired; restart at this snapshot.", restart=snapshot)
        if (found.owner, found.snapshot, found.operation, found.binding) != (
                owner_key(owner), snapshot, operation, binding):
            raise InspectionError("bad_continuation", "The continuation belongs to a different owner, snapshot or operation.")
        return dict(found.data)

    def evidence(self, owner, snapshot, location, kind, **kwargs):
        identity = json.dumps([owner_key(owner), snapshot.model_dump(),
                               location.model_dump() if location else None, kind, kwargs], sort_keys=True)
        eid = "ev-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        result = EvidenceRef(evidence_id=eid, snapshot=snapshot, location=location, kind=kind, **kwargs)
        self._evidence[eid] = (owner_key(owner), result)
        # Static citation records survive eviction and restart. DOM/capture data stays in memory.
        if kind in {"source_text", "narration_text", "reconstructed_render"}:
            with self.snapshots.db() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS package_content_evidence (evidence_id TEXT PRIMARY KEY, owner TEXT NOT NULL, metadata TEXT NOT NULL)")
                conn.execute("INSERT OR IGNORE INTO package_content_evidence VALUES (?,?,?)",
                             (eid, owner_key(owner), result.model_dump_json()))
        return result

    def resolve_evidence(self, owner, eid):
        found = self._evidence.get(eid)
        if found and found[0] == owner_key(owner):
            return found[1]
        with self.snapshots.db() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='package_content_evidence'").fetchone()
            row = conn.execute("SELECT metadata FROM package_content_evidence WHERE evidence_id=? AND owner=?",
                               (eid, owner_key(owner))).fetchone() if exists else None
        if row:
            return EvidenceRef.model_validate_json(row[0])
        raise InspectionError("evidence_not_found", "That evidence is unavailable for this owner.")

    def coverage(self, owner, snapshot, *, complete=False, dynamic="source_only", gaps=()):
        members = self.snapshots.members(owner, snapshot)
        key = (owner_key(owner), snapshot.snapshot_id)
        read = self._read.get(key, set())
        return Coverage(inventory_complete=True, text_complete=complete, dynamic_coverage=dynamic,
            unread_members=sum(m.member_id not in read for m in members),
            pending_members=sum(m.state in {"pending", "partial"} for m in members),
            unreadable_members=sum(m.state == "unreadable" for m in members),
            visual_evidence=tuple(sorted(self._visual.get(key, set()))),
            gaps=tuple(dict.fromkeys((*gaps, "unvisited_dynamic_states",
                *(f"{m.relative_path}: {m.reason}" for m in members if m.reason))))[:20])

    async def inspect(self, *, owner, package, expected_revision=None):
        snapshot = await asyncio.to_thread(self.snapshots.capture, owner, package, expected_revision)
        members = self.snapshots.members(owner, snapshot)
        registered, entry = self.snapshots.resolve(package)
        replacement = None
        if entry and entry.closed_why == package_inbox.SUPERSEDED:
            replacement_entry = next((e for e in package_inbox.listing()
                if e.name == entry.name and package_inbox.Sender(project=e.from_project, session=e.from_session).key()
                == package_inbox.Sender(project=entry.from_project, session=entry.from_session).key()), None)
            if replacement_entry:
                replacement = PackageRef(package_id=replacement_entry.package_id, inbox_id=replacement_entry.id)
        baseline = work_packages.delivered_source_revision(registered)
        self.start_index(owner, snapshot)
        more = self.cursor(owner, snapshot, "members", "", {"offset": 20}) if len(members) > 20 else None
        return ContentOverview(snapshot=snapshot, inbox_revision=entry.revision if entry else None,
            source_modified_since_delivery=(snapshot.content_revision != baseline) if baseline else None, replacement=replacement,
            coverage=self.coverage(owner, snapshot, gaps=() if baseline else ("delivery_source_baseline_unavailable",)),
            member_count=len(members), first_members=tuple(members[:20]), next=more)

    async def members(self, *, owner, snapshot, continuation=None):
        start = self.resume(owner, snapshot, "members", "", continuation)["offset"] if continuation else 0
        rows = self.snapshots.members(owner, snapshot)
        after = start + 20
        more = self.cursor(owner, snapshot, "members", "", {"offset": after}) if after < len(rows) else None
        return MemberPage(snapshot=snapshot, members=tuple(rows[start:after]), complete=more is None, next=more)

    def _reader(self, owner, snapshot, location):
        source = self.snapshots.source(owner, snapshot, location.member_id)
        member = source.member
        expected_part = "results" if member.relative_path.startswith("results/") else "source"
        if location.part != expected_part and location.part != "narration":
            raise InspectionError("location_mismatch", "The requested part does not match the member.")
        with self.snapshots.db() as conn:
            manifest = json.loads(conn.execute("SELECT manifest FROM package_content_snapshots WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone()[0])
        is_story = member.relative_path == manifest.get("story")
        if location.part == "narration" and not is_story:
            raise InspectionError("location_mismatch", "That member is not this snapshot's story.")
        detected = bundle_readers.detect(member.relative_path, source.head())
        if member.relative_path == "deck-data.js" or is_story:
            reader = _StructuredReader(member.relative_path)
        else:
            reader = bundle_readers.reader_for(detected.format)
        if reader is None or member.support in {"unsupported", "blocked"}:
            raise InspectionError(member.reason or "reader_unavailable", "This member has no available reader.")
        return source, detected, reader, is_story

    async def read(self, *, owner, snapshot, location, continuation=None):
        binding = location.model_dump_json()
        state = self.resume(owner, snapshot, "read", binding, continuation) if continuation else {"reader_cursor": None, "offset": 0}
        source, detected, reader, story = self._reader(owner, snapshot, location)
        lease = "read-" + secrets.token_hex(8)
        self.snapshots.lease(owner, snapshot, lease, 30)
        try:
            got = await asyncio.to_thread(self.core.read_text, owner=owner, source=source,
                detected=detected, reader=reader, cursor=state["reader_cursor"], max_chars=700)
        except (Unsupported, ValueError) as e:
            raise InspectionError(getattr(e, "reason", "reader_error"), "The member could not be read at this location.") from None
        finally:
            self.snapshots.cache.release(lease)
        chunks = []
        offset = state["offset"]
        for loc, text in got.chunks:
            slide_id = str(loc.slide) if isinstance(reader, _StructuredReader) and hasattr(loc, "slide") else location.slide_id
            if source.path == "deck-data.js" and slide_id:
                data = package_inbox.read_deck_data(source.read().decode("utf-8"))
                slides = (data or {}).get("slides") or []
                if 1 <= loc.slide <= len(slides) and isinstance(slides[loc.slide - 1], dict):
                    slide_id = str(slides[loc.slide - 1].get("id") or "s" + str(loc.slide - 1))
            target = location.model_copy(update={"location": loc, "slide_id": slide_id, "text_start": offset,
                "text_end": offset + len(text), "part": "narration" if story else location.part})
            evidence = self.evidence(owner, snapshot, target, "narration_text" if story else "source_text")
            chunks.append(ContentChunk(evidence=evidence, text=text))
            offset += len(text)
        more = self.cursor(owner, snapshot, "read", binding, {"reader_cursor": got.next_cursor, "offset": offset}) if got.next_cursor else None
        if more is None:
            self._read.setdefault((owner_key(owner), snapshot.snapshot_id), set()).add(location.member_id)
        return ContentPage(chunks=tuple(chunks), coverage=self.coverage(owner, snapshot, complete=more is None, gaps=got.notes), complete=more is None, next=more)

    def start_index(self, owner, snapshot):
        key = (owner_key(owner), snapshot.package.package_id)
        if key not in self._jobs or self._jobs[key].done():
            self._jobs[key] = asyncio.create_task(self._index(owner, snapshot))

    async def _index(self, owner, snapshot):
        async with self._workers:
            deadline = time.monotonic() + 30
            for member in self.snapshots.members(owner, snapshot):
                if member.state not in {"pending", "partial"}:
                    continue
                if time.monotonic() >= deadline:
                    return
                part = "results" if member.relative_path.startswith("results/") else "source"
                try:
                    await asyncio.to_thread(self._index_member, owner, snapshot, member, part, deadline)
                except asyncio.CancelledError:
                    raise
                except (Unsupported, InspectionError, ValueError, OSError) as error:
                    reason = getattr(error, "reason", None) or (error.failure.code if isinstance(error, InspectionError) else "reader_error")
                    self._member_state(snapshot, member, "unreadable", reason)

    def _member_state(self, snapshot, member, state, reason=None):
        with self.snapshots.db() as conn:
            conn.execute("UPDATE package_content_members SET metadata=? WHERE snapshot_id=? AND member_id=?",
                (member.model_copy(update={"state": state, "reason": reason}).model_dump_json(), snapshot.snapshot_id, member.member_id))

    def _index_member(self, owner, snapshot, member, part, deadline):
        with self._thread_workers:
            return self._index_member_bounded(owner, snapshot, member, part, deadline)

    def _index_member_bounded(self, owner, snapshot, member, part, deadline):
        location = PackageLocation(member_id=member.member_id, part=part)
        source, detected, reader, story = self._reader(owner, snapshot, location)
        lease = f"index-{snapshot.snapshot_id}-{member.member_id}"
        self.snapshots.lease(owner, snapshot, lease, 60)
        try:
            with self.snapshots.db() as conn:
                checkpoint = conn.execute("SELECT cursor,ordinal FROM package_content_index_progress WHERE snapshot_id=? AND member_id=?", (snapshot.snapshot_id, member.member_id)).fetchone()
            cursor = json.loads(checkpoint[0]) if checkpoint and checkpoint[0] else None
            ordinal = checkpoint[1] if checkpoint else 0
            with self.snapshots.db() as conn:
                previous = conn.execute("SELECT text FROM package_content_chunks WHERE snapshot_id=? AND member_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT 500", (snapshot.snapshot_id, member.member_id, ordinal)).fetchall()
            tail = "".join(r[0] for r in reversed(previous))[-500:]
            while time.monotonic() < deadline:
                got = self.core.read_text(owner=owner, source=source, detected=detected,
                                         reader=reader, cursor=cursor, max_chars=12000)
                with self.snapshots.db() as conn:
                    for loc, text in got.chunks:
                        conn.execute("INSERT INTO package_content_chunks VALUES (?,?,?,?,?,?)",
                            (snapshot.snapshot_id, member.member_id, ordinal, loc.model_dump_json(), text, "narration" if story else part))
                        conn.execute("INSERT INTO package_content_search VALUES (?,?,?,?)",
                            (snapshot.snapshot_id, member.member_id, ordinal, tail + text))
                        tail = (tail + text)[-500:]
                        ordinal += 1
                    conn.execute("INSERT OR REPLACE INTO package_content_index_progress VALUES (?,?,?,?)", (snapshot.snapshot_id, member.member_id, json.dumps(got.next_cursor) if got.next_cursor else None, ordinal))
                cursor = got.next_cursor
                if cursor is None:
                    self._member_state(snapshot, member, "indexed", next(iter(got.notes), None))
                    with self.snapshots.db() as conn:
                        conn.execute("DELETE FROM package_content_index_progress WHERE snapshot_id=? AND member_id=?", (snapshot.snapshot_id, member.member_id))
                    return
            self._member_state(snapshot, member, "partial", "indexing")
        finally:
            self.snapshots.cache.release(lease)

    def _sweep_index(self, owner, snapshot):
        """Remove derived text whose source cache was evicted or package deleted."""
        try:
            self.snapshots.resolve(snapshot.package)
            deleted = False
        except InspectionError:
            deleted = True
        for member in self.snapshots.members(owner, snapshot):
            available = self.snapshots.cache.path_for(self.snapshots.cache_key(owner, member)).is_file()
            if deleted or not available:
                with self.snapshots.db() as conn:
                    for table in ("package_content_chunks", "package_content_search", "package_content_index_progress"):
                        conn.execute(f"DELETE FROM {table} WHERE snapshot_id=? AND member_id=?", (snapshot.snapshot_id, member.member_id))
                if member.state in {"indexed", "partial"}:
                    self._member_state(snapshot, member, "pending", "snapshot_unavailable")

    def _literal_matches(self, snapshot, query):
        phrase = '\"' + query.replace('\"', '\"\"') + '\"'
        with self.snapshots.db() as conn:
            if len(query) >= 3:
                candidates = [dict(r) for r in conn.execute("SELECT c.* FROM package_content_search s JOIN package_content_chunks c ON c.snapshot_id=s.snapshot_id AND c.member_id=s.member_id AND c.ordinal=s.ordinal WHERE s.snapshot_id=? AND package_content_search MATCH ? ORDER BY c.member_id,c.ordinal", (snapshot.snapshot_id, phrase))]
            else:
                candidates = [dict(r) for r in conn.execute("SELECT * FROM package_content_chunks WHERE snapshot_id=? ORDER BY member_id,ordinal", (snapshot.snapshot_id,))]
            matches = {}
            for row in candidates:
                previous = []
                chars = 0
                for prior in conn.execute("SELECT * FROM package_content_chunks WHERE snapshot_id=? AND member_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT 500", (snapshot.snapshot_id, row["member_id"], row["ordinal"])):
                    previous.append(dict(prior)); chars += len(prior["text"])
                    if chars >= 500: break
                context = list(reversed(previous)) + [row]
                if query.casefold() in "".join(r["text"] for r in context).casefold():
                    for match in context:
                        matches[(match["member_id"], match["ordinal"])] = match
        return [matches[key] for key in sorted(matches)]

    async def search(self, *, owner, snapshot, query, continuation=None):
        if not query.strip() or len(query) > 500:
            raise InspectionError("bad_query", "Supply a literal query of 1 to 500 characters.")
        self.snapshots.get(owner, snapshot.snapshot_id)
        self._sweep_index(owner, snapshot)
        if continuation:
            state = self.resume(owner, snapshot, "search", query, continuation)
            rows, start = state["rows"], state["offset"]
        else:
            self.start_index(owner, snapshot)
            rows = await asyncio.to_thread(self._literal_matches, snapshot, query)
            start = 0
        # Freeze search matches so indexing cannot shift a continuation.
        expanded = []
        for row in rows:
            text = row["text"]
            for offset in range(0, len(text), 700):
                expanded.append({**row, "text": text[offset:offset + 700], "offset": offset})
        page = expanded[start:start + 1]
        after = start + len(page)
        more = self.cursor(owner, snapshot, "search", query, {"rows": rows, "offset": after}) if after < len(expanded) else None
        chunks = []
        from pydantic import TypeAdapter
        from jarvis.bundle_contract import Location
        for row in page:
            location = PackageLocation(member_id=row["member_id"], part=row["part"],
                location=TypeAdapter(Location).validate_json(row["location"]),
                text_start=row["offset"], text_end=row["offset"] + len(row["text"]))
            evidence = self.evidence(owner, snapshot, location, "narration_text" if row["part"] == "narration" else "source_text")
            chunks.append(ContentChunk(evidence=evidence, text=row["text"]))
        return ContentPage(chunks=tuple(chunks), coverage=self.coverage(owner, snapshot,
            complete=more is None, gaps=("search_covers_indexed_text_only",)), complete=more is None, next=more)

    async def render(self, *, owner, snapshot, location, options=RenderOptions()):
        source, detected, reader, _ = self._reader(owner, snapshot, location)
        page = getattr(location.location, "page", 1) if location.location else 1
        lease = "render-" + secrets.token_hex(8)
        self.snapshots.lease(owner, snapshot, lease, 30)
        try:
            if detected.format in {"html", "svg"}:
                from jarvis import package_render
                image = await package_render.render(self, owner, snapshot, location, options)
            else:
                image = await asyncio.to_thread(self.core.render, owner=owner, source=source,
                    detected=detected, reader=reader, page=page, options=options)
        except Unsupported as e:
            raise InspectionError(e.reason, "This format has no available image renderer.") from None
        finally:
            self.snapshots.cache.release(lease)
        evidence = self.evidence(owner, snapshot, location, "reconstructed_render")
        self._visual.setdefault((owner_key(owner), snapshot.snapshot_id), set()).add(evidence.evidence_id)
        return evidence, image
