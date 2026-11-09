"""Package inspection domain and wire contract. Unknown fields are refused.

Frontend types and observation validation are generated from these schemas.
The attachment contract remains in bundle_contract.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from jarvis.bundle_contract import Location, Support

VERSION = 1
TOOL_CAP = 1500
CURSOR_SECONDS = 300
OBSERVATION_BYTES = 65536

class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class PackageRef(Shape):
    package_id: str
    inbox_id: str | None = None

class SnapshotRef(Shape):
    snapshot_id: str
    package: PackageRef
    delivery_sha256: str | None = None
    content_revision: str
    results_revision: str

class PackageLocation(Shape):
    member_id: str
    part: Literal["source", "results", "narration"] = "source"
    location: Location | None = None
    slide_id: str | None = None
    beat_id: str | None = None
    element_id: str | None = None
    text_start: int | None = None
    text_end: int | None = None

class EvidenceRef(Shape):
    evidence_id: str
    snapshot: SnapshotRef
    location: PackageLocation | None = None
    kind: Literal["source_text", "narration_text", "reported_dom", "reconstructed_render", "screen_capture", "summary"]
    view_id: str | None = None
    sequence: int | None = None
    observed_at: str | None = None

class Coverage(Shape):
    inventory_complete: bool
    text_complete: bool
    dynamic_coverage: Literal["source_only", "observed_state", "unknown"] = "source_only"
    unread_members: int
    pending_members: int
    unreadable_members: int
    visual_evidence: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    next: str | None = None

class Member(Shape):
    member_id: str
    relative_path: str
    size: int
    sha256: str | None = None
    detected_format: str
    support: Support
    operations: tuple[str, ...]
    state: Literal["pending", "indexed", "partial", "unreadable"]
    reason: str | None = None

class ContentOverview(Shape):
    snapshot: SnapshotRef
    inbox_revision: int | None = None
    source_modified_since_delivery: bool | None = None
    replacement: PackageRef | None = None
    coverage: Coverage
    member_count: int
    first_members: tuple[Member, ...]
    next: str | None = None

class ContentChunk(Shape):
    evidence: EvidenceRef
    text: str

class ContentPage(Shape):
    chunks: tuple[ContentChunk, ...]
    coverage: Coverage
    complete: bool
    next: str | None = None

class MemberPage(Shape):
    snapshot: SnapshotRef
    members: tuple[Member, ...]
    complete: bool
    next: str | None = None

class ViewRef(Shape):
    client_id: str
    view_id: str
    mount_id: str
    package: PackageRef
    content_revision: str
    sequence: int

class NarrationRef(Shape):
    package: PackageRef
    story_revision: str
    slide_id: str | None = None
    beat_id: str | None = None
    playback: Literal["telling", "waiting", "paused", "finished", "stopped"]
    completed_through_beat: str | None = None

class ViewObservation(Shape):
    view: ViewRef
    evidence: EvidenceRef
    received_at: str
    focused: bool
    visible: bool
    current: bool
    active_location: PackageLocation | None = None
    content: ContentPage
    narration: NarrationRef | None = None
    selected_text: str | None = None
    limits: tuple[str, ...] = ()

class InspectionFailure(Shape):
    code: str
    retryable: bool = False
    explanation: str
    references: tuple[EvidenceRef, ...] = ()
    restart: SnapshotRef | None = None

class InspectionError(ValueError):
    def __init__(self, code: str, explanation: str, *, retryable=False, restart=None):
        self.failure = InspectionFailure(code=code, explanation=explanation,
                                         retryable=retryable, restart=restart)
        super().__init__(explanation)

class Rect(Shape):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)

class Viewport(Shape):
    width: float = Field(ge=0)
    height: float = Field(ge=0)
    scrollX: float
    scrollY: float

class PageLocator(Shape):
    slideId: str | None = Field(default=None, max_length=256)
    tabId: str | None = Field(default=None, max_length=256)
    elementId: str | None = Field(default=None, max_length=256)
    fragment: str | None = Field(default=None, max_length=1024)
    openDetails: list[str] = Field(max_length=200)

class VisibleBlock(Shape):
    id: str = Field(max_length=256)
    role: Literal["heading", "paragraph", "table", "list", "control", "other"]
    text: str = Field(max_length=65536)
    rect: Rect
    sourceElementId: str | None = Field(default=None, max_length=256)

class VisibleImage(Shape):
    source: str = Field(max_length=2048)
    alt: str = Field(max_length=4000)
    rect: Rect

class Selection(Shape):
    text: str = Field(max_length=4000)
    anchorBlock: str | None = Field(default=None, max_length=256)
    focusBlock: str | None = Field(default=None, max_length=256)
    startOffset: int | None = Field(default=None, ge=0)
    endOffset: int | None = Field(default=None, ge=0)

class ObserveResponse(Shape):
    jarvis: Literal["package"]
    type: Literal["observe.response"]
    protocol: Literal[1]
    mountNonce: str = Field(max_length=128)
    requestId: str = Field(max_length=128)
    sequence: int = Field(ge=0)
    observedAt: str = Field(max_length=64)
    locator: PageLocator
    viewport: Viewport
    blocks: list[VisibleBlock] = Field(max_length=200)
    images: list[VisibleImage] = Field(max_length=64)
    selection: Selection | None = None
    complete: bool
    continuation: str | None = Field(default=None, max_length=128)
    limits: list[str] = Field(max_length=32)

class PackageCaptureContext(Shape):
    inboxId: str
    packageId: str
    contentRevision: str
    resultsRevision: str
    evidenceIds: list[str]
    evidenceDigest: str
    senderSession: str | None = None
    senderEvidence: str | None = None
    workId: str | None = None
    workflowId: str | None = None
    requestKey: str

class FollowupRequest(Shape):
    request_key: str = Field(min_length=1, max_length=128)
    package: PackageRef
    expected_content_revision: str
    expected_results_revision: str
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...] = Field(max_length=40)
    explicit_recipient_session: str | None = None

class PreparedFollowup(Shape):
    capture_id: int
    recipient_session: str
    package: PackageRef
    evidence_ids: tuple[str, ...]
    state: Literal["capturing"] = "capturing"
