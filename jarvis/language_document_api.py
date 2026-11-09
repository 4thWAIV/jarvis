"""One JSON envelope and bounded continuation protocol for HTTP and brain calls."""
from __future__ import annotations

import asyncio
import json
import secrets
import time

from fastapi import Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from jarvis import decision_package, language_documents as docs, language_kit as kit, language_kit_updates, language_projection, reporters
from jarvis import package_inbox, package_understanding, work_packages

READ_ONLY_OPERATIONS = frozenset({"capabilities", "retrieve", "render", "ground", "continue"})


def read_only_operation(arguments: dict | None) -> bool:
    operation = arguments.get("operation") if isinstance(arguments, dict) else None
    return isinstance(operation, str) and operation in READ_ONLY_OPERATIONS


def encoded_page(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["capabilities", "validate", "retrieve", "render", "publish", "ground", "continue"]
    arguments: dict = Field(default_factory=dict)
    continuation: str | None = None


def tool_spec() -> dict:
    return {"name": "language_document", "description":
            "Inspect language capabilities; validate a proposed architecture YAML revision; retrieve, render or publish its exact revision; "
            "ground discussion through the declared authored understanding and exact canonical source. Publication creates a local package and never approves or sends a message. "
            "Each response carries a JSON segment in data. Concatenate data while calling continue with next to recover the complete envelope. "
            "For validate, arguments are source, work_id, scope_revision, author, coverage (constructs, declarations, unresolved, outside_language), "
            "optional document_id, parent_revision and evidence. readable_markdown is the author-supplied view required for new publication; its Markdown is an immutable authored view, not a semantic proof. retrieve uses document_id/revision. render/publish also need source_revision. "
            "ground needs package_id; document_id, revision and source_revision are optional expected-value checks, with optional inbox_id and declaration. Revisions are proposals, not approvals. "
            "For changes, validate a replacement with its parent_revision. For messages include ground.followup_reference in the existing capture text, then use read_capture and the existing approval/steer_session flow.",
            "inputSchema": RequestBody.model_json_schema()}


class Adapter:
    def __init__(self, owner=lambda: "local"):
        self.documents = docs.Documents()
        self.owner = owner
        self._pages: dict[str, tuple[str, float, str]] = {}

    def _page(self, raw: str, owner: str) -> dict:
        self._pages = {key: value for key, value in self._pages.items() if value[1] > time.monotonic()}
        take = min(700, len(raw))
        while True:
            cursor = secrets.token_urlsafe(18) if take < len(raw) else None
            result = {"data": raw[:take], "next": cursor, "complete": cursor is None}
            if len(encoded_page(result)) <= 1100:
                if cursor:
                    if len(self._pages) > 1024 or sum(len(value[2].encode("utf-8")) for value in self._pages.values()) + len(raw[take:].encode("utf-8")) > 16 * 1024 * 1024:
                        raise docs.DocumentError("Too many document continuations are live; finish or restart the current operation.")
                    self._pages[cursor] = (owner, time.monotonic() + 300, raw[take:])
                return result
            take = max(1, take // 2)

    async def ground(self, **arguments):
        return await asyncio.to_thread(self._ground, **arguments)

    def _ground(self, *, package_id, document_id=None, revision=None, source_revision=None, inbox_id=None, declaration=None):
        package = work_packages.find(package_id, work_packages.PAGE)
        if not package.manifest.communication or package.manifest.communication.get("kind") != "architecture":
            raise docs.DocumentError("That package has no canonical architecture binding.")
        if inbox_id is not None:
            entry = package_inbox.get(inbox_id)
            if entry is None or entry.package_id != package.id:
                raise docs.DocumentError("That exact inbox arrival does not name the requested package.")
        read = language_projection.package_reader(package, revision)
        canonical = json.loads(read("canonical.json"))
        for key, expected in (("document_id", document_id), ("revision", revision), ("source_revision", source_revision)):
            if expected is not None and canonical.get(key) != expected:
                raise docs.DocumentError("The package canonical identity differs from this requested revision.")
        document_id, revision, source_revision = (canonical[key] for key in ("document_id", "revision", "source_revision"))
        envelope = self.documents.render(document_id=document_id, revision=revision, source_revision=source_revision)
        if any(canonical.get(key) != envelope[key] for key in language_projection.IDENTITY):
            raise docs.DocumentError("The package canonical identity differs from this requested revision.")
        if declaration is not None and declaration not in envelope["metadata"]["declarations"]:
            raise docs.DocumentError("That declaration does not belong to the requested canonical revision.")
        readable = envelope["metadata"].get("readable", {})
        member = readable.get("member", "readable.md")
        manifest = json.loads(package.manifest.as_json())
        if member not in package_understanding.documents(manifest):
            raise docs.DocumentError("The canonical authored view is not declared by this package.")
        authored = package_understanding.markdown_text(package.folder, member)
        if authored != read(member).decode("utf-8-sig"):
            raise docs.DocumentError("The authored understanding differs from this canonical revision.")
        return {"document_id": document_id, "revision": revision, "source_revision": source_revision,
                "declaration": declaration, "check_ok": True,
                "package": {"id": package.id, "version": package.manifest.version,
                            "delivered_archive_sha256": work_packages.sha256_of(package)},
                "source": {"member": "source.yaml", "text": read("source.yaml").decode("utf-8"), "sha256": source_revision},
                "understanding": {"member": member, "text": authored, "sha256": readable["markdown_sha256"]},
                "followup_reference": (f"Canonical document: {document_id}.\nCanonical revision: {revision}."
                                       f"\nCanonical YAML SHA256: {source_revision}."),
                "unresolved": envelope["metadata"]["coverage"]["unresolved"],
                "outside_language": envelope["metadata"]["coverage"]["outside_language"],
                "observation": "Canonical source and author-supplied understanding only; no current DOM observation is claimed."}

    async def call(self, request: dict, *, reported_by: str = "") -> dict:
        owner = str(self.owner())
        try:
            body = RequestBody.model_validate(request)
            arguments = dict(body.arguments)
            if body.operation == "continue":
                token = self._pages.get(body.continuation or "")
                if token is None or token[0] != owner or token[1] <= time.monotonic():
                    raise docs.DocumentError("The document continuation is unavailable; restart the exact operation.")
                self._pages.pop(body.continuation)
                return self._page(token[2], owner)
            if body.operation == "capabilities":
                result = {**self.documents.capabilities(), "updates": language_kit_updates.status()}
            elif body.operation == "validate":
                arguments.pop("reported_by", None)
                result = await asyncio.to_thread(self.documents.validate, reported_by=reported_by, **arguments)
            elif body.operation == "publish":
                result = await asyncio.to_thread(language_projection.publish, self.documents, **arguments)
            elif body.operation == "ground":
                result = await self.ground(**arguments)
            else:
                result = await asyncio.to_thread(getattr(self.documents, body.operation), **arguments)
            envelope = {"ok": True, "result": result}
        except (ValueError, TypeError, OSError, decision_package.Refused) as error:
            runtime = isinstance(error, kit.KitError)
            envelope = {"ok": False, "error": {"cause": str(error), "owner": "kit-runtime" if runtime else "document-author",
                "next_step": "Restore the pinned kit and supported runtime, then retry the same revision." if runtime
                    else "Correct the source, coverage, readable view or revision reference and submit a proposed revision."}}
        return self._page(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e"), owner)


def mount(app, adapter: Adapter) -> None:
    async def operation(request: Request, body: dict = Body(default_factory=dict)):
        return JSONResponse(await adapter.call(body, reported_by=reporters.recorded(request.scope)))
    app.add_api_route("/api/language-documents", operation, methods=["POST"])
    async def kit_status():
        return await asyncio.to_thread(language_kit_updates.status)
    app.add_api_route("/api/language-kits/status", kit_status, methods=["GET"])
