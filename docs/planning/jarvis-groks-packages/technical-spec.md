# JARVIS package understanding

Status: Proposed specification for Tim's review. Implementation has not started. Work: `work-afc13119d082`. Design date: 2026-11-08.

## review-request

Approve this specification as the design for reading package contents, following the view Tim is using, discussing the evidence, and preparing agent follow-ups for his approval. Implementation remains a separate assignment. Approval of this design does not mark the work item complete.

Recommended: Use the existing readers, sandboxed package frame, and message transmission flow. Add a shared inspection service and synchronized view observations. This connects the content JARVIS reads to the package Tim is discussing without making another document reader or message sender.

## user-experience

Opening a package gives JARVIS its contents index and a bounded first reading. Tim can ask about the whole package, a specific file, a diagram, or the slide on screen. JARVIS retrieves the relevant text and images before answering. Its answer distinguishes information in the supplied files, information reported by the current page, and information the story has spoken.

When Tim moves to a slide, changes a tab, expands a detail, or selects text, JARVIS can obtain that current state on the next question. Asking about the current view never moves the page. Asking to inspect another file does not replace the view unless Tim asks to show it.

When Tim asks for a follow-up, JARVIS prepares a message to the package's proven sender or another agent Tim names. The message states the requested work and includes precise source references. Tim reviews the wording and recipient through the existing capture flow. Discussion, inspection, design approval, decision recording, package sign-off, and message transmission remain separately recorded actions.

“Understands the package” means every supplied member is accounted for, readable content can be retrieved, visual evidence can be inspected when supported, and the limits are reported. It does not mean every possible state of an arbitrary program has been explored. Unopened dynamic states, unread files, and unsupported media remain visible gaps.

## source-baseline

The implementation worktree predates the current inbox and package factory. This specification uses the live checkout at `eaf36837afad00c3c323d4a4caf28a5fef372e1a` for source inspection, without changing it. The implementation assignment must reconcile these changes with its target branch before editing. The design files live in the assigned isolated worktree.

| Existing code | Present behavior | Work proposed here |
|---|---|---|
| `jarvis/work_packages.py`: `Package`, `unpack_held`, `page_file`, `sha256_of` | Validates and unpacks runnable packages. Records the delivered archive hash. Reads named skill, brief, story, and result files. | Supply package members through the shared inspection core. Distinguish original delivery from later file changes. |
| `jarvis/package_inbox.py`: `Entry`, `Sender`, `questions` | Tracks arrivals, state, sender evidence, work and workflow links. Reads decision data without evaluating JavaScript. | Resolve exact arrivals and versions. Keep lifecycle revision separate from content identity. Reuse the decision parser. |
| `server.py`: `tool_package_inbox`, `tool_open_inbox_package` | Returns titles, purpose, manifest, and questions. Opening a page does not return its page content. | Return a content reference and first bounded overview. Expose readers for the rest. |
| `frontend/src/PackageView.tsx`, `packageBridge.ts` | Sandboxed frame with save, microphone, story, and layout messages. Checks `event.source`. | Report the current mount, visibility, active location, and bounded page content. |
| `examples/packages/decision-deck/deck.js`: `show` | Manual slide navigation changes classes and controls inside the frame. | Publish active slide changes through the shared observation helper. |
| `jarvis/package_story.js`, `package_story.py`, `story_teller.py` | Cue targets and narration state are available. Missing targets are reported. | Keep narration state separate from observed page state. Link their references where possible. |
| `jarvis/bundle_inspection.py`: `BundleInspection` | Owner-scoped, verified attachment inspection with archive listing, text paging, preview, rendering, and observation callbacks. | Extract its member-reading operations into one core used by attachments and package snapshots. Preserve its attachment facade. |
| `jarvis/bundle_readers`, `bundle_archive.py`, `bundle_cache.py` | Common format adapters, bounded archive access, versioned derived cache. HTML text extraction skips scripts. | Reuse those adapters and limits. Add structured deck reading; add presentation observation separately. |
| `frontend/src/BundleMemberView.tsx`, `bundles.ts` | Ordinary file display is separate from the package frame. | Publish document page, member, zoom, and selection through the same top-level view registry. |
| `jarvis/screen.py`: `capture_screen` | Deliberate image of one Mac display. Temporary files removed after use. | Reuse for requested actual screen evidence, with display/view association and explicit limitations. |
| `jarvis/transmission.py`, `server.py`: `_set_capture`, `_AgentRecipient`, `_AgentDestination` | Exact text and recipient approval, cancel window, queued and sent states. | Prepare package follow-ups through this flow, with package references attached to the capture record. |

The source contains these exact statements:

> “Only the claimed session is looked in: a claim is proven or refused, never moved to another.”

Source: `jarvis/server.py`, `_package_sender` docstring.

> “His yes covers this recipient and no other.”

Source: `jarvis/transmission.py`, `recipient` docstring.

> “Default off: JARVIS keeps acting after reading foreign text unless JARVIS_REFUSE_UNTRUSTED_ACTIONS is 1/true/yes/on.”

Source: `jarvis/server.py`, `_untrusted_actions_refused` docstring.

> “The label is a warning, not a wall. This is the wall.”

Source: `jarvis/server.py`, `_untrusted_content_refusal` docstring.

> “Read-only inspection never extracts into the packages folder, never clears the quarantine mark, keeps no execute bit, runs no script, loads no macro and executes no HTML.”

Source: `jarvis/bundle_inspection.py`, module docstring. This describes the existing static inspection path. Live package execution and observation are different operations.

## scope

Include every registered work package, whether received in the inbox, received as an attachment, or opened locally. Include its entire member inventory, manifest, narrative, decision data, supporting files, and saved results. Accept packages without a story, without a decision deck, and without a cooperative sender-supplied content index. Ordinary attachments keep their existing identities and tools; they use the same reading core and view-state model.

Read agent and conversation package briefs as documents without executing them. Opening an agent package for inspection must not dispatch its task. Reading a skill must not run its commands. Existing execution commands retain their separate authorization and behavior.

The design introduces no new content-authoring requirement for existing senders. The factory updates newly generated decks to publish exact slide state. Older and custom HTML receives the generic helper when served. If observation cannot work, JARVIS still reads its files and states that its current view is unavailable.

Automatic semantic transcription of arbitrary audio/video, decrypting files, installing converters, and exhaustive exploration of an arbitrary application are outside this implementation. These files remain listed with their capability status. An unavailable reader is a reported gap, not a missing inventory entry.

## evidence-model

Three independent records answer three different questions:

| Record | Answers | Authority |
|---|---|---|
| Source snapshot | What was supplied, and what is stored now? | Verified bytes and immutable derived records. Package statements remain untrusted content. |
| View observation | What does the currently selected JARVIS client report showing? | Host identity and timing are host-owned. DOM text and active locators reported inside the sandbox remain package-controlled evidence. |
| Narration state | What story beat is playing or was completed? | `story_teller` playback state, linked to the source story revision. It does not establish the visible slide. |

A rendered image has a fourth evidence kind: `reconstructed_render`. It is a render of particular bytes with particular options. It does not establish what is on Tim's screen. `screen_capture` means pixels captured from the actual selected display at a recorded instant. A frame's self-supplied bitmap is never labeled a screen capture.

Each answer carries internally the exact evidence references used. User-facing citations use a short title plus meaningful location, for example “Budget proposal, Costs slide” or “supporting-costs.pdf, page 4.” IDs and hashes remain available in the detail view. A factual answer about an unseen diagram requires its visual evidence; a caption alone supports only a statement about the caption.

Coverage has independent dimensions. `inventory_complete` means all members have been listed. `text_complete` means the requested text range has reached its end. `visual_reviewed` identifies particular images or pages actually inspected. `dynamic_coverage` is `source_only`, `observed_state`, or `unknown`. A summary does not change unread locations into read locations. No aggregate “fully read” flag is inferred from opening the package.

## content-identity

`inbox_id` selects one delivery. `package_id` selects the unpacked package. `delivery_sha256` identifies the archive recorded at arrival. `content_revision` identifies a verified snapshot of all regular source members except the mutable `results/` tree. `results_revision` identifies the results snapshot. `inbox_revision` remains the inbox lifecycle revision. These values are not interchangeable.

Build each revision from a canonical, sorted list of checked relative paths, byte lengths, and SHA-256 digests, plus a snapshot format version. Include the manifest, story, entry page, scripts, styles, and supporting assets. File modification time is a change hint, never proof of identity. A source snapshot is created with bounded reads and a before/after consistency check; a concurrent mutation retries once and then returns `source_changed`. Symlinks and path escapes are refused. Never read a caller-supplied absolute path through this API.

Snapshot bytes are private read-only copies created from the validated package directory. Keep the delivered source identity even if later files differ; report `source_modified_since_delivery` and both identities. The recorded archive hash alone does not prove current directory bytes. For legacy packages without a delivery hash, mark that value unavailable and compute the current revision from actual bytes.

Serve a mounted package from its pinned source snapshot through revision-bound asset URLs under `/api/packages/{id}/files/@snapshots/{revision}/{path}`. Register this controlled route before the existing file catch-all and retain `PACKAGE_PAGE_HEADERS` on every response, including errors. The reserved route resolves only registered snapshots, never a caller-supplied filesystem directory. The top-level entry, scripts, styles, and images must all resolve against the same snapshot. Results remain a separately versioned read path. An observer reports the host-supplied mounted revision, never a fresh directory scan masquerading as the bytes already loaded. Relative package assets resolve within that revision route. Rewrite declarative same-package absolute resource references structurally when serving HTML and CSS. If runtime JavaScript loads unversioned absolute resources, record `mixed_source_revision`; do not claim the whole loaded page is revision-verified. External resources remain separately identified observations, not members of the source snapshot. For an existing unpinned mount, report the revision as unverified until a user-requested reload establishes the pinned mount.

A newly delivered version creates a new arrival reference and revision. A superseded package can still be inspected by its exact reference. Selecting “current” resolves once at the beginning of an operation; it does not switch revision midway through reading. On supersession while a package is open, the current view remains associated with its mounted revision until Tim opens the replacement. Show that a replacement exists. Do not silently move the page or use its old contents to answer about the new package.

Results changes invalidate result-derived data, current answer state, and follow-up context that cites those results. They do not invalidate a PDF rendered from unchanged source bytes. Package answer state comes from `package_inbox.questions` and its current results revision; page form values are observation evidence, not recorded decisions.

## reading-architecture

Refactor the reusable operations in `BundleInspection` into an `InspectionCore` that takes a verified `MemberSource`, format detection, reader, cache scope, and limits. Its responsibilities are format description, paged text, render, and member digest. Leave owner checks, message download, attachment verification, inventory receipts, and bundle URL construction in `BundleInspection`. Keep its public attachment methods and wire responses compatible.

Add `PackageInspection` as an orchestration service for registered packages. It resolves inbox/package identity, obtains source snapshots, lists members, calls `InspectionCore`, adds package structure, and exposes coverage. Package member IDs are opaque, derived from the content revision and checked relative path. All requests resolve IDs through the snapshot inventory. Unknown members never fall back to filesystem paths.

Do not invent a fake messaging owner or attachment for a locally received package. Generalize the cache namespace to an explicit local-owner scope or existing team-owner scope. Preserve decoding of old attachment keys and owner cleanup. Cache options include the snapshot revision, member digest, reader/version, operation, page, render options, and schema version. A shared cache implementation retains the current size cap and atomic private writes.

Structured deck reading reuses the parser behind `package_inbox.questions`. Extract a side-effect-free `read_deck_data` function if needed so decisions and content are derived from the same validated JSON. Read slides, headings, text blocks, tables, picture references, captions, and decision links from the validated `window.DECK` assignment. Do not evaluate JavaScript to recover data. An unsupported or malformed assignment falls back to files and observed DOM and reports the limitation.

Story extraction uses `package_story.parse` and indexes every slide and beat with its target selectors and actions. Story prose is separately labeled `narration`; it is not substituted for page body text. Associate a story target with a content location when it can be resolved; unresolved targets stay unresolved.

HTML source extraction uses `HtmlReader` for static document words and resource references. A source outline includes headings and links where possible. Referenced same-package files map to their inventory member IDs. Scripts are readable as source files on request, but executable source does not become rendered text merely because it contains strings. CSS-hidden content remains source content until a view observation says it is visible.

A background index job may inventory and extract safe static members when the package arrives. It emits no speech, opens no page, executes no scripts, and sends no message. Work is bounded and cancellable. The arrival remains usable while indexing. Opening or querying prioritizes the requested member ahead of the queue. The complete inventory survives partial extraction; each member has its own error and progress.

## supported-formats

| Content | Static content | Visual or current view | Limits to report |
|---|---|---|---|
| Factory HTML deck | Validated deck JSON, slide text, tables, captions, decisions, story, assets. | Exact slide locator from `show`, visible DOM from observer, image assets through image reader. Requested actual screen image for appearance. | Missing image, unsupported block, DOM/source mismatch, scripts required for other states. |
| Custom HTML page | HTML text, headings, local links, assets, and source files. | Generic observer reports visible DOM, selection, open details, tabs, fragment, and scroll. | Shadow roots that are closed, nested inaccessible frames, canvas/WebGL content, script-generated states not visited, and unsupported observer installation. |
| PDF | Existing PDF reader and page locations. | Existing page renderer; selected document page from JARVIS viewer. | Password, malformed page, missing PDF capability, scanned text absent. Vision can inspect rendered scanned pages when the model supports images; OCR availability is separate. |
| PNG/JPEG/GIF/WebP/HEIC | Metadata and declared text alternatives. | Existing image reader where available; selected image and zoom state. | Decoder capability, image size, unsupported animation frames. Text alternatives do not establish pixel content. |
| Markdown/text/JSON/CSV/TSV and source files | Existing text readers with line and range locations. | Viewer location and selection if shown in JARVIS. | Encoding, size, truncated ranges, structured parsing failure. |
| DOCX/XLSX/PPTX | Existing Open XML readers, including their locations. | Existing capability result governs rendering. A portable PDF/image supplied beside it can be inspected as a separate member. | No newly promised Office rendering. Formulas and saved values stay distinguished. Macros are ignored. |
| ZIP | Full checked inventory and readable members through archive budgets. | Each supported member uses its own view. | Encrypted/unsafe entries, nested archives, decompression limits. Nested ZIP remains listed with `nested_archive`; inspect separately only through an explicit supported import path. |
| Agent/conversation package | Brief, skill, manifest, supporting files, results. | Document view if opened. | Inspection does not execute package code or commands. |
| Other files, audio/video, legacy Office, other archives | Inventory, sniffed format, size, capability and reason. | Only an already available reader's actual capabilities. | `reader_unavailable` or `unknown_format`; no fabricated transcript or converter install. |

The table describes intended integration, not a promise that every Mac has every reader. Runtime `Reader.health()` decides the available operations. Failures for one member leave other members readable. A blocked ZIP entry must appear with its refusal reason.

## view-synchronization

The host owns `client_id`, `view_id`, `mount_id`, package identity, source revision, and frame visibility. A `mount_id` is new on every load or remount. The server records which authenticated JARVIS client originated the current turn. Voice clients without an unambiguous focused visible package get `ambiguous_view`; server-global last-opened package is insufficient.

Extend `connectBridge` with a negotiated observation protocol. The parent accepts messages only from `frame.contentWindow`, validates a strict schema and byte limits, binds them to its own mount, and overwrites any claimed host identity. A nonce prevents stale messages from a previous mount being accepted; it is freshness binding, not a trust proof against the package itself. Never add `allow-same-origin`.

Use a shared helper served by JARVIS for all package HTML. The response adapter installs it in served HTML without modifying delivered source files, using structural HTML handling rather than regular-expression replacement. A parser/serializer must preserve doctypes, raw script text, encodings, and layout fixtures. If the parser cannot preserve the document safely, return source inspection plus `observer_unavailable`. Newly generated factory decks explicitly load the same helper; a singleton guard prevents duplicate observers. It is separate from `package_story.js`, so pages without narration work.

The generic helper observes DOM mutations, history/fragment changes, scrolling, resizing, document visibility, selection, and native detail toggles. It builds bounded semantic blocks from the visible DOM using computed visibility, geometry, clipping ancestors, and viewport intersection. It ignores the story highlight layer, scripts, styles, inert/hidden nodes, password controls, hidden inputs, and non-content control values. Ordinary input contents are omitted by default; a visible selection in a non-password text control may be returned when Tim asks about that selection. It records open detail IDs, selected tabs with ARIA state, current fragment, scroll, viewport, and visible image references/rectangles. Visibility is a DOM observation, not a guarantee against arbitrary overlays or canvas painting; those limits travel with the result.

Factory `deck.js.show` calls the helper with a stable deck slide ID after applying the new slide state. The helper still gathers the actual visible content. Custom pages may publish semantic locators through the same API, but a claimed locator remains package data and is checked against the inventory where possible. Generic HTML works without that API. Every action path, including keyboard and story-triggered navigation, produces an observation after the display settles.

Events are coalesced over 150 ms; continuous changes report at most four updates per second. Host focus/visibility changes and mount resets transmit immediately. Metadata updates contain no whole page text. A question requiring current content issues `observe.request` with a fresh request ID; a matching response within two seconds supplies a fresh snapshot. The server rejects old sequence numbers, old mounts, other packages, over-limit payloads, and responses for replaced revisions. A timeout returns `view_unavailable` or `view_stale`, never the previous view labeled current.

At the start of a user turn, freeze a `ViewRef` identifying the visible client/mount/sequence, where available. “This” binds to that reference. If the view changes while evidence is being collected, refresh once. If it keeps changing, answer about the named captured location and state that it changed, or request the missing location when necessary. A late response from a previous slide is never promoted to the current view. Closing a package or disconnecting the client invalidates its live observation immediately. A hidden preserved frame remains inspectable as source but is not Tim's current view.

Narration retains its own story revision, beat, slide, playback state, and completed-through position. A cue being sent is not a completed spoken beat. If Tim manually moves while narration continues, the record may say “view: Costs; narration: Delivery.” JARVIS answers “this slide” from the view. Stop/resume and question waiting retain their existing behavior. Inspection does not click story actions or resume narration.

## visual-evidence

Use the existing PDF/image renderers to inspect source visuals. Return the `ToolImage` bytes, not a local path the brain cannot read. Each image result names its source revision, member, page, image dimensions, evidence kind, and any unsupported content.

For a requested current screen appearance, reuse `screen.capture_screen` on a user-origin turn. Bind the request to the chosen local client and display before capture. Return display identity, capture time, and before/after view sequence. If the selected package cannot be associated with that local screen, or the captured screen shows another app, return the image as screen evidence without claiming package-view verification. A changed view during capture returns `view_changed` and may retry once. Capture does not run on a timer or on package arrival. Temporary PNGs retain the existing deletion behavior.

A browser DOM cannot reliably screenshot an arbitrary sandboxed cross-origin frame. This design does not use DOM-to-canvas reconstruction as an actual capture. Remote-browser packages still provide DOM observation; actual pixels require an explicitly granted browser display capture in a later extension, or a supplied image. Initial implementation returns `capture_unavailable_for_client` for that case. That limitation is visible in capabilities and acceptance tests.

An optional isolated HTML render is a reconstructed preview only. It uses the existing Playwright dependency and a new constrained renderer adapter. It starts a fresh context with local package resource routing, no credentials, no service workers, no downloads, no external network, and no parent save/listen/transmission bridge. It cannot open JARVIS's authenticated routes. Rendering never clicks arbitrary package controls, runs an agent, executes a skill, or supplies input. The initial loaded state or a factory-deck declarative slide state is the only render target. Missing runtime state is reported. This is needed only for source visuals that cannot be read from the existing image/PDF readers; live observation remains the primary way to discuss interactive pages.

## contracts

The proposed Python and TypeScript definitions are in `interfaces.md`. They specify one shared service contract used by the brain tools and HTTP routes. Generated TypeScript shapes follow the existing `bundleContract.ts` generation pattern. Do not hand-maintain independent field lists in server, frontend, MCP schemas, and tests.

Proposed brain tools:

| Tool | Arguments | Result and effect |
|---|---|---|
| `inspect_package` | Exact inbox/package reference; optional expected revision and continuation. | Content identity, provenance, member and location index, coverage and gaps. Read only. |
| `read_package_content` | Snapshot reference; location/member; continuation. | Bounded source or narrative text with locators. Read only. |
| `search_package_content` | Snapshot reference; literal query; continuation. | Bounded matching passages and coverage of searched members. Read only. |
| `observe_package_view` | Exact package reference; optional explicit client; optional continuation for a frozen observation. | Fresh host binding, reported active location, selection, visible text, gaps, separate narration reference. Read only; no navigation. |
| `look_at_package_content` | Snapshot/member/page or explicit current view plus requested evidence kind. | `ToolImage` for a source render or actual screen capture, labeled accordingly. Actual capture is an acting reading tool. |
| `prepare_package_followup` | Exact inbox reference; expected source/results revisions; message text; evidence references; optional explicit recipient. | Existing capture ID with resolved recipient and provenance. Creates a draft only. |

Existing `package_inbox` and `open_inbox_package` remain backward compatible and add the exact content reference in their next-action text. Do not stuff entire inventories into their existing output. Existing decision and sign-off tools continue to own their effects. The package-content tools never call them automatically.

HTTP routes are under `/api/packages/{package_id}/inspection`: `GET` overview, `/members`, `/text`, `/search`, `/pages/{page}`, `/view`, and `POST /view/observe` for a requested fresh observation. Parent-client observation updates use the authenticated voice/client connection, not an iframe-callable endpoint. A POST that initiates screen capture requires the same user-origin gate as existing screen tools. A `POST /followup` routes through the same preparation service as the brain tool; any acceptance or send continues through capture controls and `transmission`.

Typed failures have a stable code, retryability, a short explanation, and bounded evidence references. All endpoints enforce package owner visibility and exact identity. Tool routes are not an exemption from authentication or revision checks. Sender-facing receipts keep existing attachment semantics; local inspection does not invent a remote read receipt.

## retrieval-and-context

The current `TOOL_RESULT_CAP` is 1,500 characters, while bundle readers default to 12,000 characters. The proposed tools must page their final serialized, wrapped output below the existing cap. Do not raise a global cap or allow the generic truncator to cut JSON, provenance, an untrusted delimiter, or a continuation token.

Use a server-stored opaque cursor bound to owner, snapshot, operation, location, reader version, and options. Return a short `evidence_id` resolving to full metadata so each reply has room for content. Reserve a measured envelope allowance, then fit complete chunks into the remainder, splitting long text at deterministic character boundaries with exact offsets when needed. A default payload target of 700 characters leaves room for the wrapper and metadata; serialization must calculate actual final length, not rely on that estimate. `complete` and `next` are always present. An expired cursor returns `cursor_expired` and a restart reference. Replaying it never changes which snapshot it reads.

Opening supplies a bounded overview and the visible location first. A question retrieves exact matching headings, current visible text or selection, relevant source passages, then images if the answer depends on appearance. Search uses a local SQLite FTS index over extracted chunks, with exact source locations. No vector service or remote embedding dependency is introduced. Search output states which members are indexed, pending, or unreadable. “No match in indexed text” never means “not present in the package.”

Model context receives evidence only when needed. A conversation references stable evidence IDs and compact coverage summaries, rather than repeating whole inventories each turn. Reused summaries are derived evidence with source citations and extractor/model version. They remain untrusted and are invalidated on source change. A generated summary cannot replace the original passage when quoting or resolving a disputed fact.

Initial operational limits reuse archive/member/reader/cache limits already present. New observation limits are 64 KiB per complete frame response, 200 semantic blocks, 4,000 characters of selection, and 64 visible image references. Larger views return `complete=false` with ranges that the helper can page from the frozen observation. Index jobs use a 30-second work slice, one job per package, and two total workers; resumable checkpoints preserve progress. A user-facing tool has a 15-second deadline below the existing MCP deadline; long extraction returns `indexing` with a resumable reference. Canceling a request kills its render subprocess and releases temporary copies. These values are implementation defaults, not product decisions for Tim.

## storage-and-lifecycle

Use JARVIS's existing SQLite database and migration mechanism. Add namespaced tables rather than another database: `package_content_snapshots`, `package_content_members`, `package_content_chunks`, and `package_content_search` (FTS). Snapshot rows hold package/delivery identity, source/results revisions, extractor schema version, inventory status, and timestamps. Member rows hold checked path, digest, size, detected format, supported operations, progress, and reason. Chunks hold stable locations and derived-cache references. Index and extract in transactions per member so a crash leaves a resumable state.

Snapshot bytes and derived files use the owner-scoped private cache. Active frame mounts and in-flight readers hold leases on the members they need, so eviction cannot remove a mounted page midway through viewing. If the bounded cache cannot admit the required snapshot without evicting leased content, return `budget` with the available readable coverage; do not exceed the cap or delete active assets. Release leases on close, disconnect, cancellation, and worker completion. Crash recovery clears expired leases before trimming. Source snapshot metadata remains after derived data eviction; operations then reacquire matching bytes or return `snapshot_unavailable`. Do not silently substitute the latest source. Cache eviction and package deletion remove orphaned FTS text and derived records. When a message cites a snapshot, retain its small citation record with the message; full source retention follows package retention. A citation remains resolvable as metadata even if its bytes are later unavailable.

Live observations live in bounded memory per client/mount, with at most two recent frozen observations and a five-minute cursor lifetime. Client disconnect and mount replacement invalidate currentness immediately. Selected text and raw screen pixels are not ambient durable storage. Persist only the evidence fragments actually used in a conversation or approved outgoing message, through the existing conversation/message record, with their untrusted provenance. Observed DOM text has the same handling as other user-requested tool content.

Follow-up metadata is attached to the existing capture/conversation/message record through a typed `package_context` field. It contains inbox/package identity, source/results revisions, evidence references, work/workflow links, and the trusted sender resolution used. Delivery records remain those of the existing transmission system. No second send table, queue, message approval flag, or agent addressing registry is introduced.

## followups-and-authorization

Add a host-owned `PackageActionScope` for package mutations. It is derived before package retrieval from the current direct user turn or explicit UI action, the selected package reference, and trusted host view metadata. Its operation is one of `prepare_followup`, `answer_decision`, `sign_off`, or `execute_package`. The dispatcher supplies it out of band; package content and model tool arguments cannot mint or widen it. It binds the user turn, package, requested recipient when explicit, and action, and expires at the end of that turn. Existing capture acceptance remains the authority for actual transmission.

For voice, use a separate bounded intent-classification pass through the existing brain provider, with a strict schema and a two-second deadline. A timeout leaves reading available and returns `intent_unavailable` for mutation. The pass sees only the direct user utterance and host-owned IDs/state, with no retrieved package prose, summaries, tool output, or prior agent messages. It returns the operation and unresolved user-supplied target words, not a command to execute. A discussion question creates no mutation scope. Ambiguous action or target returns a clarification result. Resolve target words through the existing directory/package resolver after classification; ambiguity never broadens the scope. Exact action parameters are checked against the scope and the direct utterance: recording a decision uses only the answer the user supplied, never words lifted from a package. Explicit UI controls mint the corresponding exact scope without classification. This is an intent bound, not a claim that classification is infallible; adversarial and ambiguous utterances are required test fixtures.

Require that scope at `prepare_package_followup` and the existing package decision, sign-off, and execution dispatcher entries when operating on package content. The mandatory package check applies in either setting of the optional global untrusted-action refusal. It does not change the global policy for unrelated tools. Inspection and observation never create a mutation scope. A story action inside the sandbox cannot provide one.

Preparation starts only after Tim asks for a message or an instruction to an agent. Reading and discussing a package never sends one. For “ask the sender,” resolve the inbox entry's proven `from_session` and `sender_evidence`. `claimed_session`, filenames, manifest text, page scripts, story prose, and package-supplied contact instructions cannot select the recipient. A missing, ambiguous, or no-longer-live sender produces a draft with a routing problem; it never switches to a similarly named session. A different recipient is allowed when Tim explicitly names one and the existing directory resolves it.

The draft includes the requested action or question, the relevant package title/version, source locations, supporting facts, and any unresolved limit that affects the request. It carries relevant work and workflow links. A visual request includes a description plus the referenced asset/page; a file is attached or transferred only through an existing authorized attachment mechanism. Sending a local path to a remote recipient does not count as providing the evidence. When the sender already has its own source, stable member/location references usually suffice.

Preparation calls a refactored capture service extracted from `_set_capture` and its current persistence hooks. Both ordinary `capture` and package preparation use it. The recipient is fixed by session identity before the read-back. A new package-specific draft must be rejected if its rendered text exceeds the capture's 4,000-character limit; it is condensed with Tim's requested action preserved or split into separately reviewable messages. Silent `[:CAPTURE_MAX_CHARS]` truncation is not used by this new path.

Exact outgoing text, recipient, and attached evidence binding are frozen for approval. An explicitly historical source reference is part of that wording, so an old revision is usable without pretending it is current. Any change clears approval through `transmission.changed`. Capture acceptance and send use the existing `read_capture`, Send control, `steer_session`, `stage`, and `perform` flow. The feature must not infer approval from a package opening, a story finishing, a decision answer, design review, or content that claims approval. The new intent scope grants preparation or the particular package action only; it never bypasses capture approval to send a message.

Before staging and before delivery, compare the cited source/results identities and recipient binding. If content changed, keep the existing draft and show the difference; obtain a refreshed approved draft or retain an explicitly historical reference. A newer package does not authorize rewriting or rerouting an approved message. Duplicate preparation calls with the same request key reuse the draft while unchanged. Duplicate sends use the existing transmission identity and status reconciliation; they do not produce another message.

Inspection and discussion work in either setting of `JARVIS_REFUSE_UNTRUSTED_ACTIONS`. Content tools are in `TAINTING_TOOLS`, with appropriate reading-tool classification. Do not label a generated summary or draft clean merely because JARVIS wrote it. Do not add a broad taint exemption for preparation or steering. With refusal enabled, a mixed-content turn may be unable to prepare a draft; preserve the evidence and ask for the user's next instruction through the existing flow. With refusal disabled, the mandatory capture approval still governs transmission. This design does not change the global untrusted-action policy.

Package decision answers continue through `answer_package_decision`; follow-up drafts are separate from decisions and do not auto-sign-off a package. A follow-up reply is associated with the outbound message and its work/package context through existing session messaging. Replies from agents are new untrusted evidence. A reply suggesting implementation does not grant implementation permission.

## safety-and-failure-recovery

| Condition | Result | Recovery |
|---|---|---|
| Ambiguous title or several visible clients | `ambiguous_package` or `ambiguous_view`, with bounded candidates. | Resolve an exact arrival/view before answering about “this.” |
| Corrupt or absent member | Per-member failure; other members remain readable. | Read an available representation or prepare a request for a corrected file. |
| Archive limit or unsafe member | Existing reason code and visible inventory entry. | Inspect allowed members; request a safer representation when relevant. |
| View helper missing, crashed, or stale | Source reading remains available; no current-view assertion. | Retry fresh observation once, then offer source/actual screen evidence as available. |
| Frame returns spoofed host fields or replayed sequence | Reject update; retain only correctly bound earlier evidence as historical. | New host request with current mount; log IDs and codes. |
| DOM/source disagreement | Return both references with `content_mismatch`. | Inspect actual screen or relevant source; do not silently select the favorable statement. |
| No image capability in current brain | `image_input_unavailable`; source text/metadata remain usable. | Explain what can be read; no claim of visual inspection. |
| PDF scan with no text | `scanned_no_text`; render may still work. | Inspect actual rendered pages through vision; report unread pages. |
| Local screen permission absent or remote client | `capture_unavailable` with specific capability reason. | Continue with DOM/source evidence; permission changes are explicit user actions. |
| Source changes during extraction | Discard inconsistent snapshot and retry once. | Return `source_changed` if it repeats; request again against a stable revision. |
| Supersession during discussion | Historical references stay bound; replacement is announced in package context. | Open or inspect the replacement when asked. |
| Recipient absent, renamed, or replaced | Draft remains unsent and names the routing problem. | Resolve the original identity or have Tim choose another recipient. |
| Send times out with uncertain status | Preserve existing delivery record; do not claim sent or resend. | Query/reconcile transport state before a retry. |
| Index worker or renderer crash | Persist completed member checkpoints; remove scratch files. | Resume safe static extraction; retry renderer once on explicit request. |

Untrusted text, filenames, links, DOM attributes, selection, story content, OCR/vision interpretations, and model summaries remain data. They cannot add tools, create jobs, answer decisions, mutate package files, approve messages, or become system instructions. Package inspection APIs are read-only except explicit index/cache bookkeeping and draft preparation. The observer bridge accepts only observation messages; its handlers have no send, execute, approve, or decision capabilities.

## implementation-stages

### shared-readers

Extract `InspectionCore` from `BundleInspection`, retaining the attachment API and its tests. Add owner scopes, package snapshots, member inventory, and source/result revision handling. Add typed contracts and generate the frontend types. Exit when an inbox package, local package, and ordinary attachment use the same format readers and produce equivalent locations and capability outcomes.

### package-retrieval

Add structured deck/story extraction, indexed retrieval, the content tools and HTTP routes, coverage, and continuation handling within the existing final tool cap. Wire the exact inspection reference into opening and listing. Exit when every supported package format can be discussed from cited retrieved evidence, with unread and unsupported content represented explicitly.

### synchronized-view

Add host client/mount registry, helper installation, generic DOM observation, factory slide adapter, and ordinary document-view reporting. Link user turns to view references. Keep narration independent. Exit when manual clicks, keyboard navigation, detail tabs, selection, scrolling, resize, hidden views, multiple clients, remounts, and stale responses all produce correct currentness or an explicit unavailable result.

### visual-understanding

Wire source renders to `ToolImage`, local deliberate screen capture, and constrained reconstructed HTML rendering. Report remote-client and image-model limits. Exit when a chart can be discussed from inspected pixels and a replayed render cannot be mislabeled as Tim's actual screen.

### approved-followups

Extract and reuse capture preparation, add the host-owned package action scope, package context binding, recipient verification, revision checks, and request idempotency. Use the existing send flow and response correlation. Exit when Tim can ask a package-grounded question of the correct agent and see its actual queue/send status, while an injected or merely discussed request remains unsent.

### integrated-review

Run the acceptance cases below in an isolated JARVIS with its own data directory and clients. Use actual built frontend and MCP tool responses. Preserve the live checkout and live user session. Prepare an implementation review package with representative conversation transcripts and screenshots. Tim reviews the resulting behavior before release or deployment.

## acceptance-cases

| Named case | Setup and action | Required observable outcome |
|---|---|---|
| `whole-package-accounting` | Package contains a deck, PDF, image, data file, brief, story, and unreadable member. Ask what it contains. | Complete inventory is pageable; every member appears with its reading state. No “fully read” claim from inventory alone. |
| `slide-without-story` | Open a deck or custom page with no story. Ask about its contents. | Source text and current observed content are retrieved independently of narration. |
| `manual-slide-following` | Narration is on Delivery. Tim clicks Costs, then uses arrow keys to another slide and asks about “this.” | Answer cites the visible observed slide, with no narration-derived substitution. |
| `generic-page-state` | Old custom HTML has tabs, nested details, sticky headers, clipping, and scroll. Change state and select text. | Fresh visible text and selection match the active DOM state or report a specific observation limit. |
| `visual-claim` | Diagram's labels differ from its caption. Ask what the arrows show. | JARVIS reads an image/page, cites visual evidence, and distinguishes caption disagreement. |
| `current-versus-replay` | A live canvas contains user-generated state absent from a new load. Request current appearance. | Actual capture is labeled screen evidence; reconstructed page is labeled reconstructed and never presented as the live canvas. |
| `unvisited-state` | Custom script can generate unseen content after input. Ask whether the entire application has been inspected. | JARVIS reports observed/source coverage and the unvisited-state gap. |
| `file-reader-parity` | Same PDF, image, and text arrive in a package and as attachments. | Same underlying readers, location semantics, refusals, and render evidence; no duplicate parser behavior. |
| `long-document-continuation` | Large document and inventory exceed both reader pages and the 1,500-character tool cap. | Every response parses, wrappers close, exact continuation resumes, and all requested text is recoverable without silent truncation. |
| `search-incomplete-index` | Ask for an absent-looking term while some files await indexing. | Search states its coverage and does not claim absence across unread members. |
| `results-versus-source` | Answer a decision while a supporting PDF stays unchanged. | Results revision changes, decision answer refreshes, PDF render cache remains usable. |
| `replacement-arrives` | New package version arrives while old one is open and a follow-up draft exists. | Old view remains correctly identified. New version is available. Draft does not silently change evidence or recipient. |
| `mutation-during-read` | Alter source during snapshot construction, then during follow-up approval. | Inconsistent read is refused; changed evidence invalidates staging or requires an explicitly historical reference. |
| `two-clients` | Laptop and remote browser show different slides of the same package. | Originating client determines “this”; ambiguous voice origin asks for the view. A remote browser does not get a false local screenshot. |
| `close-and-reopen` | Delay an observation, close the package, remount, then deliver the old response. | Old mount/sequence is rejected; no stale selected text becomes current. |
| `blocked-member` | ZIP includes traversal, symlink, duplicate name, oversized member, encrypted member, and safe text. | Ordinary bundle inspection lists each blocked entry and reads safe members. Runnable-package ingestion retains its whole-package refusal; inspection never weakens that gate. |
| `injected-content` | HTML, PDF text, a picture, and story each instruct JARVIS to send a command or claim Tim approved it. | No transmission or decision action occurs. Content remains untrusted. Capture approval cannot be supplied by content. |
| `claimed-sender` | Manifest names another session; inbox has a different proven sender or none. | Follow-up uses the proven sender only or returns a routing problem. |
| `approved-agent-question` | Tim requests a source-grounded question to the sender, reviews it, and approves. | Exact recipient and text pass the existing capture/transmission flow. Actual queued/sent status and later reply are linked to package/work context. |
| `changed-draft` | Edit text, evidence, or recipient after acceptance. Repeat send request. | Approval clears; no send uses the old approval. Repeated unchanged request creates no duplicate delivery. |
| `discussion-only` | Tim asks whether an agent's proposal is sensible, then moves on. | Discussion produces no follow-up, decision answer, sign-off, or implementation dispatch. |
| `capability-loss` | Disable PDF rendering, image input, observer response, or screen permission independently. | Each failure names the missing operation; supported source reading continues. |
| `restart-and-eviction` | Crash during extraction, restart, evict derived files, then revisit a citation. | Completed work resumes or regenerates against exact bytes. Unavailable old bytes are reported without substituting new content. |

## implementation-handoff

Implement from this reviewed specification only after Tim assigns the build. The first implementation task reconciles the target branch with the cited live baseline and opens its project instructions. No source, API, storage, or frontend implementation is part of this design package.

Expected production change areas are the shared inspection core, package inspection and snapshot service, generated contracts, package/frame observation helper, `PackageView`/`packageBridge`, factory deck navigation, `BundleMemberView`, MCP tool definitions/handlers, and capture preparation reuse. Exact file splitting follows the responsibilities and tests above. Existing attachment wire contracts and message transmission behavior remain compatibility constraints; any intended change to those behaviors is brought back as a design amendment.

The review package gives Tim a short visual explanation and the complete specification. Its Done action records that he reviewed the package; this document itself does not claim approval, and no `approve-item` decision is supplied that could mark unfinished implementation complete.
