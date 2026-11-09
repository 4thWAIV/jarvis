# Proposed interfaces

Status: Proposed design, not implemented code. These definitions describe the service boundary and wire shapes for `technical-spec.md`. Ellipses indicate protocol declarations, not unfinished implementations. Existing bundle `Location`, `RenderedPage`, `Reader`, `MemberSource`, `Support`, and `Reason` are imported and reused.

## python-service

```python
from dataclasses import dataclass
from typing import Literal, Protocol
from jarvis.bundle_contract import Location, RenderedPage, RenderOptions, Support
from jarvis.bundle_readers import Description, Detected, MemberSource, Reader, TextSlice

@dataclass(frozen=True)
class LocalOwner:
    installation_id: str

@dataclass(frozen=True)
class TeamOwner:
    server_id: str
    owner_github_id: int

OwnerScope = LocalOwner | TeamOwner

@dataclass(frozen=True)
class PackageRef:
    package_id: str
    inbox_id: str | None

@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    package: PackageRef
    delivery_sha256: str | None
    content_revision: str
    results_revision: str

@dataclass(frozen=True)
class PackageLocation:
    member_id: str
    part: Literal["source", "results", "narration"]
    location: Location | None
    slide_id: str | None = None
    beat_id: str | None = None
    element_id: str | None = None
    # Source offsets are explicit when a large reader chunk is subdivided.
    text_start: int | None = None
    text_end: int | None = None

@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    snapshot: SnapshotRef
    location: PackageLocation | None
    kind: Literal["source_text", "narration_text", "reported_dom",
                  "reconstructed_render", "screen_capture", "summary"]
    view_id: str | None = None
    sequence: int | None = None
    # Capture and observation times, never a claim that evidence remains current.
    observed_at: str | None = None

@dataclass(frozen=True)
class Coverage:
    inventory_complete: bool
    text_complete: bool
    dynamic_coverage: Literal["source_only", "observed_state", "unknown"]
    unread_members: int
    pending_members: int
    unreadable_members: int
    visual_evidence: tuple[str, ...]
    gaps: tuple[str, ...]
    # Counts supplement per-member records; continuation lists the remainder.
    next: str | None = None

@dataclass(frozen=True)
class Member:
    member_id: str
    relative_path: str
    size: int
    sha256: str | None
    detected_format: str
    support: Support
    operations: tuple[str, ...]
    state: Literal["pending", "indexed", "partial", "unreadable"]
    reason: str | None

@dataclass(frozen=True)
class ContentOverview:
    snapshot: SnapshotRef
    inbox_revision: int | None
    source_modified_since_delivery: bool | None
    replacement: PackageRef | None
    coverage: Coverage
    member_count: int
    first_members: tuple[Member, ...]
    next: str | None

@dataclass(frozen=True)
class ContentChunk:
    evidence: EvidenceRef
    text: str

@dataclass(frozen=True)
class ContentPage:
    chunks: tuple[ContentChunk, ...]
    coverage: Coverage
    complete: bool
    next: str | None

@dataclass(frozen=True)
class MemberPage:
    snapshot: SnapshotRef
    members: tuple[Member, ...]
    complete: bool
    next: str | None

@dataclass(frozen=True)
class ViewRef:
    client_id: str
    view_id: str
    mount_id: str
    package: PackageRef
    content_revision: str
    sequence: int

@dataclass(frozen=True)
class NarrationRef:
    package: PackageRef
    story_revision: str
    slide_id: str | None
    beat_id: str | None
    playback: Literal["telling", "waiting", "paused", "finished", "stopped"]
    completed_through_beat: str | None

@dataclass(frozen=True)
class ViewObservation:
    view: ViewRef
    evidence: EvidenceRef
    received_at: str
    focused: bool
    visible: bool
    current: bool
    active_location: PackageLocation | None
    content: ContentPage
    narration: NarrationRef | None
    selected_text: str | None
    limits: tuple[str, ...]

@dataclass(frozen=True)
class ScreenImage:
    data: bytes
    media_type: Literal["image/png"]
    width: int
    height: int
    display: int
    captured_at: str

@dataclass(frozen=True)
class VisualEvidence:
    evidence: EvidenceRef
    image: RenderedPage | ScreenImage
    display: int | None
    sequence_before: int | None
    sequence_after: int | None
    limits: tuple[str, ...]

@dataclass(frozen=True)
class InspectionFailure:
    code: str
    retryable: bool
    explanation: str
    references: tuple[EvidenceRef, ...] = ()
    restart: SnapshotRef | None = None

class InspectionCore(Protocol):
    def describe(self, *, owner: OwnerScope, source: MemberSource,
                 detected: Detected, reader: Reader) -> Description: ...
    def read_text(self, *, owner: OwnerScope, source: MemberSource,
                  detected: Detected, reader: Reader,
                  cursor: dict | None, max_chars: int) -> TextSlice: ...
    def render(self, *, owner: OwnerScope, source: MemberSource,
               detected: Detected, reader: Reader, page: int,
               options: RenderOptions) -> RenderedPage: ...
    def digest(self, *, owner: OwnerScope, source: MemberSource) -> str: ...

class PackageInspection(Protocol):
    async def inspect(self, *, owner: OwnerScope, package: PackageRef,
                      expected_revision: str | None = None) -> ContentOverview: ...
    async def members(self, *, owner: OwnerScope, snapshot: SnapshotRef,
                      continuation: str | None = None) -> MemberPage: ...
    async def read(self, *, owner: OwnerScope, snapshot: SnapshotRef,
                   location: PackageLocation,
                   continuation: str | None = None) -> ContentPage: ...
    async def search(self, *, owner: OwnerScope, snapshot: SnapshotRef,
                     query: str, continuation: str | None = None) -> ContentPage: ...
    async def render(self, *, owner: OwnerScope, snapshot: SnapshotRef,
                     location: PackageLocation,
                     options: RenderOptions) -> VisualEvidence: ...

class PackageViews(Protocol):
    async def observe(self, *, owner: OwnerScope, package: PackageRef,
                      client_id: str | None,
                      turn_view: ViewRef | None) -> ViewObservation: ...
    async def continue_observation(self, *, owner: OwnerScope,
                                   continuation: str) -> ViewObservation: ...
    async def capture(self, *, owner: OwnerScope, view: ViewRef,
                      display: int) -> VisualEvidence: ...

@dataclass(frozen=True)
class PackageActionScope:
    scope_id: str
    user_turn_id: str
    package: PackageRef
    operation: Literal["prepare_followup", "answer_decision",
                       "sign_off", "execute_package"]
    explicit_recipient_session: str | None
    direct_utterance_digest: str
    origin: Literal["direct_voice_intent", "user_control"]
    expires_at: str

@dataclass(frozen=True)
class FollowupRequest:
    request_key: str
    package: PackageRef
    expected_content_revision: str
    expected_results_revision: str
    text: str
    evidence_ids: tuple[str, ...]
    # None means resolve the arrival's proven sender.
    explicit_recipient_session: str | None = None

@dataclass(frozen=True)
class PreparedFollowup:
    capture_id: int
    recipient_session: str
    package: PackageRef
    evidence_ids: tuple[str, ...]
    state: Literal["capturing"]
    # This result contains no approval or send capability.

class PackageFollowups(Protocol):
    async def prepare(self, *, owner: OwnerScope,
                      request: FollowupRequest,
                      authority: PackageActionScope) -> PreparedFollowup: ...
```

`PackageActionScope` is a server-internal object looked up from the authenticated current turn. It is never accepted from a frame, request body, or brain tool argument. The dispatcher checks its exact operation/package/recipient binding and expiration; an inspected document cannot produce one. Preparation creates no send authorization.

A protocol method raises the service's typed exception carrying `InspectionFailure` on failure. HTTP and MCP adapters translate that same failure; they do not derive separate interpretations. Wire schema implementations use the project's Pydantic conventions and forbid unknown fields. The dataclasses above specify the domain shape, not a second serialization library.

`PackageViews.capture` wraps the existing `screen.Shot` in `ScreenImage`. `VisualEvidence.image` is a document/screen union so a display has no invented document page count. The existing bundle wire shape stays unchanged. The document/screen wire shape is given below.

## image-result

```typescript
type VisualPayload =
  | { kind: "document"; mediaType: string; width: number; height: number;
      page: number; pageCount: number; imageRef: string }
  | { kind: "screen"; mediaType: "image/png"; width: number; height: number;
      display: number; capturedAt: string; imageRef: string };
```

`imageRef` is an authenticated short-lived result reference. The MCP adapter resolves it to image bytes and `ToolImage`; it is not a filesystem path or a public URL. Image content and metadata carry the same untrusted evidence label.

## observation-protocol

```typescript
type ObservationProtocol = 1;
type Rect = { x: number; y: number; width: number; height: number };
type Viewport = { width: number; height: number; scrollX: number; scrollY: number };
type PageLocator = {
  slideId?: string;
  tabId?: string;
  elementId?: string;
  fragment?: string;
  openDetails: string[];
};
type VisibleBlock = {
  id: string;
  role: "heading" | "paragraph" | "table" | "list" | "control" | "other";
  text: string;
  rect: Rect;
  sourceElementId?: string;
};
type VisibleImage = {
  source: string;
  alt: string;
  rect: Rect;
  // The parent resolves package-local source to a member; remote URLs are data.
};
type Selection = {
  text: string;
  anchorBlock?: string;
  focusBlock?: string;
  startOffset?: number;
  endOffset?: number;
};
type ObserveRequest = {
  jarvis: "package";
  type: "observe.request";
  protocol: ObservationProtocol;
  mountNonce: string;
  requestId: string;
  // Present only to continue the exact frozen frame response.
  continuation?: string;
};
type ObserveReady = {
  jarvis: "package";
  type: "observe.ready";
  protocol: ObservationProtocol;
  capabilities: ("dom" | "selection" | "deck-location")[];
};
type ObserveResponse = {
  jarvis: "package";
  type: "observe.response";
  protocol: ObservationProtocol;
  mountNonce: string;
  requestId: string;
  sequence: number;
  observedAt: string;
  locator: PageLocator;
  viewport: Viewport;
  blocks: VisibleBlock[];
  images: VisibleImage[];
  selection?: Selection;
  complete: boolean;
  continuation: string | null;
  limits: string[];
};
type ObserveChanged = {
  jarvis: "package";
  type: "observe.changed";
  protocol: ObservationProtocol;
  mountNonce: string;
  sequence: number;
  locator: PageLocator;
  // Metadata only; content is collected on request.
};

type HostObservation = {
  clientId: string;
  viewId: string;
  mountId: string;
  packageId: string;
  contentRevision: string;
  receivedAt: string;
  focused: boolean;
  visible: boolean;
  response: ObserveResponse;
};
```

The parent sends a mount-bound initialization before accepting `ObserveChanged`. `observe.ready` requests negotiation and is not evidence of content. The parent's registered connection supplies every field outside `response`; the frame cannot provide or override those fields. The parent validates nested values, integer ranges, finite geometry, UTF-8 byte size, and list bounds before forwarding. Unknown protocol versions receive an explicit unsupported response and remain source-readable.

The frame continuation pages one immutable observation. A fresh navigation creates a new observation and never mutates an existing continuation. The server may return the old frozen observation as historical content but cannot call it current after navigation. Timestamp currentness uses host receipt time and the fresh request handshake; a frame-controlled clock does not prove freshness.

A successful response can still be package-controlled misinformation. It is labeled `reported_dom`. The parent owns which sandbox supplied it, not the truth of its text. Actual screen pixels come only from the trusted capture service.

## minimal-tool-envelope

```json
{
  "evidence_id": "ev-opaque",
  "kind": "source_text",
  "location": "Costs slide",
  "text": "A bounded exact passage.",
  "complete": false,
  "next": "opaque-continuation"
}
```

The example shows the inner data envelope. The tool adapter adds the existing untrusted-content wrapper and verifies the final character count before returning. `location` is untrusted sender text. Full metadata is resolved server-side from `evidence_id` and is available through continued overview/detail reads. Neither caller-supplied evidence IDs nor continuation tokens bypass owner checks.

## followup-binding

The existing capture record gains this optional typed metadata. Its text, recipient, and evidence-binding digest together determine whether its accepted content still matches. Ordinary captures without package context retain their behavior.

```typescript
type PackageCaptureContext = {
  inboxId: string;
  packageId: string;
  contentRevision: string;
  resultsRevision: string;
  evidenceIds: string[];
  evidenceDigest: string;
  senderSession: string | null;
  senderEvidence: string | null;
  workId: string | null;
  workflowId: string | null;
  requestKey: string;
};
```

The implementation must extend the accepted binding comparison through the existing capture/transmission boundary, including persistence and restore, rather than simply storing these fields beside an unchanged text-only approval check. Read-back shows any evidence content or attachment that will be transmitted. Internal citation metadata does not authorize an attachment. Changes after approval call `transmission.changed` and are refused at staging and delivery until a matching approval exists.
