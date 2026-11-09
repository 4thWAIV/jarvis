"""Reader operations shared by verified attachments and package snapshots.

Identity, authorization, inventory and URLs belong to the calling facade.
The core handles only bytes, format readers and derived cache records.
"""
from __future__ import annotations

import hashlib
import json

from jarvis import bundle_archive
from jarvis.bundle_cache import (CacheKey, DerivedCache, LocalOwner, OwnerScope,
                                 ScopedCacheKey, TeamOwner)
from jarvis.bundle_contract import (Extent, RenderedPage, RenderedPageInfo,
                                    RenderOptions, TextChunk)
from jarvis.bundle_readers import Description, Detected, MemberSource, Reader, TextSlice, archive_reason


def content_revision(rows) -> str:
    """Canonical sorted checked member paths, byte lengths and digests."""
    return hashlib.sha256(json.dumps({"version": 1, "members": sorted(rows)},
        ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


class InspectionCore:
    def __init__(self, cache: DerivedCache):
        self.cache = cache

    def _key(self, owner: OwnerScope, source: MemberSource, reader: str,
             version: int, operation: str, options: str = ""):
        args = (source.attachment_sha256, source.member_id, reader, version, operation, options)
        if isinstance(owner, TeamOwner):
            return CacheKey(owner.server_id, owner.owner_github_id, *args)
        return ScopedCacheKey(owner, *args)

    def describe(self, *, owner: OwnerScope, source: MemberSource,
                 detected: Detected, reader: Reader) -> Description:
        key = self._key(owner, source, reader.name, reader.version, "describe", detected.format)
        cached = self.cache.get(key)
        if cached is not None:
            raw = json.loads(cached)
            return Description(raw["support"], Extent.model_validate(raw["extent"]),
                               raw["reason"], frozenset(raw["operations"]))
        try:
            found = reader.describe(source, detected)
        except bundle_archive.ArchiveError as e:
            found = Description("blocked", Extent(), archive_reason(e))
        self.cache.put(key, json.dumps({"support": found.support,
            "extent": found.extent.model_dump(mode="json"), "reason": found.reason,
            "operations": sorted(found.operations)}).encode())
        return found

    def read_text(self, *, owner: OwnerScope, source: MemberSource,
                  detected: Detected, reader: Reader, cursor: dict | None,
                  max_chars: int) -> TextSlice:
        options = json.dumps({"cursor": cursor, "max": max_chars}, sort_keys=True)
        key = self._key(owner, source, reader.name, reader.version, "text-slice", options)
        cached = self.cache.get(key)
        if cached is not None:
            raw = json.loads(cached)
            chunks = [TextChunk.model_validate(c) for c in raw["chunks"]]
            return TextSlice([(c.location, c.text) for c in chunks], raw["next"],
                             tuple(raw["notes"]))
        result = reader.read_text(source, detected, cursor, max_chars)
        self.cache.put(key, json.dumps({"chunks": [TextChunk(location=loc, text=text)
            .model_dump(mode="json") for loc, text in result.chunks], "next": result.next_cursor,
            "notes": result.notes}).encode())
        return result

    def render(self, *, owner: OwnerScope, source: MemberSource,
               detected: Detected, reader: Reader, page: int,
               options: RenderOptions) -> RenderedPage:
        key = self._key(owner, source, reader.name, reader.version, "render",
                        f"{options.digest()}:{page}")
        cached = self.cache.get(key)
        if cached is not None:
            size = int.from_bytes(cached[:4], "big")
            return RenderedPage(RenderedPageInfo.model_validate_json(cached[4:4 + size]),
                                cached[4 + size:])
        result = reader.render(source, detected, page, options)
        head = result.info.model_dump_json().encode()
        self.cache.put(key, len(head).to_bytes(4, "big") + head + result.data)
        return result

    def digest(self, *, owner: OwnerScope, source: MemberSource) -> str:
        key = self._key(owner, source, "inspection", 1, "digest")
        cached = self.cache.get(key)
        if cached is not None:
            return cached.decode("ascii")
        found = hashlib.sha256(source.read()).hexdigest()
        self.cache.put(key, found.encode("ascii"))
        return found
