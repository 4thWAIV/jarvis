"""The collaboration-bundles contract: the typed JSON every bundle route
answers with, the brain's bundle tools and how each is gated, and the shared
words (formats, operations, support, reasons) the readers, the bindings and
the transport all use. The page's copy of these shapes is generated into
`frontend/src/bundleContract.ts` by `scripts/generate_messaging_contract.py`.

The whole design is `.agents/in-progress/collaboration-bundles-contract.md`.
In short: a received attachment is named by (message id, attachment id) for
the owner signed in now, never by a path; a member of an archive by an
opaque `member_id` derived from the attachment's SHA-256 and the member's
checked path. Reading is bounded and paged: a page that is not the whole
member says so (`complete` false, with a `continuation`), never silently
truncated.

`Operation`, `FormatId` and `Openability` cross the wire (capability
advertisement, openability receipts), so they are defined in
`team_server/teamserver/messaging_models.py` and imported here from
`jarvis.messaging_wire`; every name here stays where it was.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jarvis.messaging_wire import FormatId, Openability, Operation  # noqa: F401  (re-exported)

# The shape version of everything below. A change that a page or a lane
# already built against would misread moves it.
BUNDLE_CONTRACT_VERSION = 1

# -- limits ------------------------------------------------------------------------------

MEMBER_PAGE_DEFAULT = 100
MEMBER_PAGE_MAX = 200
TEXT_PAGE_DEFAULT_CHARS = 12_000
TEXT_PAGE_MAX_CHARS = 20_000
PREVIEW_REFS_MAX = 50
BLOCKED_RESOURCES_MAX = 50
RENDER_EDGE_DEFAULT = 1600
RENDER_EDGE_MIN = 256
RENDER_EDGE_MAX = 2400
CONTINUATION_MAX_CHARS = 512
PATH_MAX_CHARS = 1024
CHECKLIST_MAX = 200
GAPS_MAX = 20

# -- shared words ------------------------------------------------------------------------

# What can be done with a file (Operation) and a file's actual format
# (FormatId) cross the wire in capability advertisements, so they are the
# wire's: see messaging_wire.

# Whether this JARVIS can read a member: all of it, some of it (`reason`
# says what not), not at all with the readers it has, or refused for safety.
Support = Literal["readable", "partial", "unsupported", "blocked"]

# Why a member, page or attachment is not fully readable. Fixed codes; the
# words are REASONS_SHOWN.
Reason = Literal[
    "not_local", "corrupt", "not_zip", "encrypted", "password_required",
    "unknown_format", "reader_unavailable", "nested_archive", "scanned_no_text",
    "page_failed", "too_large", "budget", "outside", "link", "duplicate",
    "bad_name", "macros_ignored", "external_resources_blocked", "formulas_not_evaluated"]

REASONS_SHOWN: dict[str, str] = {
    "not_local": "It is not downloaded here yet.",
    "corrupt": "The file is damaged.",
    "not_zip": "It is not a zip.",
    "encrypted": "It is encrypted.",
    "password_required": "It needs a password.",
    "unknown_format": "Its format is not one JARVIS knows.",
    "reader_unavailable": "This Mac has no reader for it.",
    "nested_archive": "It is an archive inside the archive; open it on its own.",
    "scanned_no_text": "Its pages are images with no text; reading them needs OCR.",
    "page_failed": "Some pages could not be read.",
    "too_large": "It is larger than JARVIS reads.",
    "budget": "Reading it went over the safe limits.",
    "outside": "Its name would land outside the archive's folder.",
    "link": "It is a link, which JARVIS never follows.",
    "duplicate": "Another member has the same name.",
    "bad_name": "Its name is not usable.",
    "macros_ignored": "Its macros were not loaded.",
    "external_resources_blocked": "Its links to the web were not loaded.",
    "formulas_not_evaluated": "Formulas are shown as written, with their saved values.",
}

# Openability (the recipient's report to the sender) is the wire's too.

# The sender's prediction before sending.
Readiness = Literal["ready", "partial", "unknown", "blocked"]

# What the sender can do about a gap. Each keeps the original attachment.
GapAction = Literal["flatten_wrapper", "add_pdf_text", "add_page_images", "add_portable_pdf",
                    "add_file_index", "ask_recipient_to_update", "manual_review"]

# A one-off request's progress on the recipient's thread, shown as one label.
ReviewProgress = Literal["file_ready", "evidence_unavailable", "review_in_progress",
                         "notes_ready"]
ChecklistState = Literal["unread", "read", "unreadable", "cited"]
ArtifactRole = Literal["primary", "supporting", "brief"]
OutcomeKind = Literal["notes", "revised_copy", "data", "answer", "other"]

# Hints and frames: the page re-reads a thread's bundle state on `bundles`
# (on /ws/sessions); `bundle_view` (on the voice socket) opens a member in
# the centre, sent by the brain's show_bundle_member as by the page's Open.
BUNDLES_HINT = "bundles"
BUNDLE_VIEW_FRAME = "bundle_view"


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# -- naming an attachment and a member ----------------------------------------------------

class AttachmentInfo(_Shape):
    """A received (or sent) file, as the message's manifest names it. The
    filename and the declared type are the sender's words."""
    message_id: UUID
    attachment_id: UUID
    filename: str
    declared_media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str


def member_id(attachment_sha256: str, path: str) -> str:
    """A member's stable, opaque id: the same bytes and path always give the
    same id, a changed attachment a new one. The attachment itself (a file
    that is not an archive) is the member at path ""."""
    digest = hashlib.sha256(f"{attachment_sha256}\x00{path}".encode("utf-8")).hexdigest()
    return "m" + digest[:24]


def encode_continuation(member: str, reader_version: int, cursor: dict) -> str:
    """An opaque continuation: which member, which reader version, and the
    reader's own cursor. A reader version change makes old ones invalid."""
    raw = json.dumps({"m": member, "v": reader_version, "c": cursor},
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(token) > CONTINUATION_MAX_CHARS:
        raise ValueError("continuation too long")
    return token


def decode_continuation(token: str, member: str, reader_version: int) -> dict:
    """The reader's cursor, or ValueError when the token is not one this
    member and reader version made."""
    if not token or len(token) > CONTINUATION_MAX_CHARS:
        raise ValueError("bad continuation")
    try:
        raw = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, TypeError):
        raise ValueError("bad continuation") from None
    if not isinstance(raw, dict) or raw.get("m") != member or raw.get("v") != reader_version \
            or not isinstance(raw.get("c"), dict):
        raise ValueError("bad continuation")
    return raw["c"]


# -- where in a document -----------------------------------------------------------------

class PageLocation(_Shape):
    kind: Literal["page"] = "page"
    page: int = Field(ge=1)


class LineLocation(_Shape):
    kind: Literal["lines"] = "lines"
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class SheetLocation(_Shape):
    kind: Literal["sheet"] = "sheet"
    sheet: str
    start_row: int = Field(ge=1)
    end_row: int = Field(ge=1)


class SlideLocation(_Shape):
    kind: Literal["slide"] = "slide"
    slide: int = Field(ge=1)


Location = Annotated[Union[PageLocation, LineLocation, SheetLocation, SlideLocation],
                     Field(discriminator="kind")]


class Extent(_Shape):
    """How big a member is in its own units, where the reader knows."""
    pages: Optional[int] = None
    lines: Optional[int] = None
    sheets: list[str] = Field(default_factory=list, max_length=100)
    slides: Optional[int] = None


class ReaderId(_Shape):
    name: str
    version: int = Field(ge=1)


# -- classify ----------------------------------------------------------------------------

class PackageBadge(_Shape):
    """Whether the archive is also a JARVIS work package (work_packages.recognise).
    Optional: an ordinary bundle opens either way, and Run is offered only
    when `recognised`."""
    recognised: bool
    runs: Optional[str] = None
    reason: Optional[str] = None


class Inventory(_Shape):
    files: int = Field(ge=0)
    folders: int = Field(ge=0)
    expanded_bytes: int = Field(ge=0)
    readable: int = Field(ge=0)
    partial: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    blocked: int = Field(ge=0)
    wrapper: str = ""                 # the one top folder, or ""
    ignored: int = Field(default=0, ge=0)   # platform metadata set aside


class Classification(_Shape):
    """What one attachment is. `format` and `media_type` are detected from
    its content, never taken from the sender's declaration."""
    attachment: AttachmentInfo
    format: FormatId
    media_type: str
    container: Literal["zip", "none", "unsupported"]
    support: Support
    reason: Optional[Reason] = None
    root_member_id: str               # the attachment as a member (path "")
    inventory: Optional[Inventory] = None   # archives only
    package: Optional[PackageBadge] = None  # zips only


# -- list --------------------------------------------------------------------------------

class MemberInfo(_Shape):
    """One member. `path` is the archive's checked name (the sender's words)."""
    member_id: str
    path: str = Field(max_length=PATH_MAX_CHARS)
    name: str
    is_dir: bool
    size_bytes: int = Field(ge=0)
    compressed_size: int = Field(ge=0)
    format: FormatId
    media_type: str
    support: Support
    reason: Optional[Reason] = None
    operations: list[Operation] = Field(default_factory=list, max_length=8)


class MemberPage(_Shape):
    attachment: AttachmentInfo
    folder: str = ""
    members: list[MemberInfo] = Field(max_length=MEMBER_PAGE_MAX)
    total: int = Field(ge=0)          # members in this folder
    continuation: Optional[str] = Field(default=None, max_length=CONTINUATION_MAX_CHARS)


# -- read --------------------------------------------------------------------------------

class TextChunk(_Shape):
    location: Location
    text: str


class TextPage(_Shape):
    """A page of one member's text. `complete` is false exactly when there
    is more (`continuation`); `chars` counts this page's text."""
    attachment: AttachmentInfo
    member: MemberInfo
    reader: ReaderId
    chunks: list[TextChunk]
    chars: int = Field(ge=0)
    extent: Extent
    complete: bool
    continuation: Optional[str] = Field(default=None, max_length=CONTINUATION_MAX_CHARS)
    notes: list[Reason] = Field(default_factory=list, max_length=8)


# -- preview and render ------------------------------------------------------------------

class RenderOptions(_Shape):
    max_edge: int = Field(default=RENDER_EDGE_DEFAULT, ge=RENDER_EDGE_MIN, le=RENDER_EDGE_MAX)

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True)
                              .encode()).hexdigest()[:16]


class PreviewRef(_Shape):
    """Something the page can show: a rendered page, an image, or a static
    HTML preview, each at a bundle route (`url`, same origin)."""
    kind: Literal["page_image", "image", "html"]
    member_id: str
    page: Optional[int] = None
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


class Preview(_Shape):
    attachment: AttachmentInfo
    member: MemberInfo
    refs: list[PreviewRef] = Field(max_length=PREVIEW_REFS_MAX)
    extent: Extent
    blocked_resources: list[str] = Field(default_factory=list,
                                         max_length=BLOCKED_RESOURCES_MAX)
    continuation: Optional[str] = Field(default=None, max_length=CONTINUATION_MAX_CHARS)


class RenderedPageInfo(_Shape):
    member_id: str
    page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


@dataclass(frozen=True)
class RenderedPage:
    """A rendered page's PNG: the route serves `png`, the brain's tool
    returns it as an image block. Never JSON."""
    info: RenderedPageInfo
    png: bytes


# -- refusals ----------------------------------------------------------------------------

RefusalCode = Literal["not_local", "member_not_found", "unsupported", "blocked", "corrupt",
                      "budget", "bad_continuation", "page_out_of_range", "attachment_not_found",
                      "messaging_unavailable"]

REFUSAL_STATUS: dict[str, int] = {
    "not_local": 409, "member_not_found": 404, "unsupported": 415, "blocked": 422,
    "corrupt": 422, "budget": 422, "bad_continuation": 400, "page_out_of_range": 404,
    "attachment_not_found": 404, "messaging_unavailable": 409,
}


class InspectionRefused(Exception):
    """Why an inspection call cannot answer. `reason` refines `code` where
    a member's own reason applies."""

    def __init__(self, code: str, shown: str, reason: Optional[str] = None):
        super().__init__(shown)
        self.code, self.shown, self.reason = code, shown, reason


# -- sender preflight (transport lane) ---------------------------------------------------

class Requirement(_Shape):
    attachment_id: UUID
    member_id: Optional[str] = None
    path: Optional[str] = Field(default=None, max_length=PATH_MAX_CHARS)
    format: FormatId
    operation: Operation


class NodeFit(_Shape):
    """One of the recipient's nodes. `capabilities` says what its record
    was: advertised and fresh, from an older JARVIS (legacy), too old
    (stale), or the node is offline. Only `advertised` can be ready."""
    node_id: UUID
    label: str
    capabilities: Literal["advertised", "legacy", "stale", "offline"]
    fit: Readiness
    missing: list[Requirement] = Field(default_factory=list, max_length=GAPS_MAX)


class Gap(_Shape):
    requirement: Requirement
    action: GapAction


class PreflightResult(_Shape):
    """The one readiness shown before send: a prediction, never proof that
    the recipient opened it. `capability_digest` names the capability
    snapshot it used, which is stored with the approved revision."""
    message_id: UUID
    content_revision: int = Field(ge=1)
    recipient_github_id: int
    readiness: Readiness
    nodes: list[NodeFit] = Field(default_factory=list, max_length=16)
    gaps: list[Gap] = Field(default_factory=list, max_length=GAPS_MAX)
    local_problems: list[Requirement] = Field(default_factory=list, max_length=GAPS_MAX)
    capability_digest: str
    checked_at: datetime


# -- recipient review state (transport lane) ---------------------------------------------

class ChecklistItem(_Shape):
    attachment_id: UUID
    member_id: str
    path: str = Field(max_length=PATH_MAX_CHARS)
    role: ArtifactRole
    state: ChecklistState
    read: list[Location] = Field(default_factory=list, max_length=50)


class ReviewState(_Shape):
    """The recipient's own record of a one-off request on one thread: the
    checklist and source map the notes cite from. Local only; never sent."""
    thread_id: UUID
    progress: Optional[ReviewProgress] = None
    scope: Optional[Literal["full", "partial"]] = None
    items: list[ChecklistItem] = Field(default_factory=list, max_length=CHECKLIST_MAX)
    revision: int = Field(ge=1)


# -- the brain's tools -------------------------------------------------------------------

@dataclass(frozen=True)
class BrainTool:
    """One bundle tool: `acting` puts it in server.ACTING_TOOLS; `taints`
    is the TAINTING_TOOLS source when its result carries other people's
    names or file content (then wrapped with _wrap_untrusted)."""
    acting: bool
    taints: Optional[str]
    route: str                      # the page route that calls the same service


BRAIN_TOOLS: dict[str, BrainTool] = {
    # Inspection (readers' service, bound by the bindings lane).
    "inspect_attachment": BrainTool(True, "a file a teammate sent",
                                    "POST {A}/bundle"),
    "list_bundle_members": BrainTool(False, "a file a teammate sent",
                                     "GET {A}/bundle/members"),
    "read_bundle_member": BrainTool(False, "a file a teammate sent",
                                    "GET {A}/bundle/members/{member_id}/text"),
    "look_at_bundle_page": BrainTool(False, "a page of a file a teammate sent",
                                     "GET {A}/bundle/members/{member_id}/pages/{page}"),
    "show_bundle_member": BrainTool(True, None,
                                    "POST {A}/bundle/members/{member_id}/show"),
    # Sending and recovery (transport lane's services).
    "attachment_readiness": BrainTool(False, "the team's devices",
                                      "GET /api/team/messages/drafts/{message_id}/readiness"),
    "set_message_task": BrainTool(True, None,
                                  "PUT /api/team/messages/drafts/{message_id}/task"),
    "add_companion_files": BrainTool(True, None,
                                     "POST /api/team/messages/drafts/{message_id}/companions"),
    "prepare_repair": BrainTool(True, None, "POST {A}/repair"),
    "review_checklist": BrainTool(False, "a file a teammate sent",
                                  "GET /api/team/messages/threads/{thread_id}/review"),
    "set_review_scope": BrainTool(True, None,
                                  "PUT /api/team/messages/threads/{thread_id}/review/scope"),
}

# `{A}` in a route above.
ATTACHMENT_PREFIX = "/api/team/messages/{message_id}/attachments/{attachment_id}"
