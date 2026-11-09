# Package understanding implementation review

Branch: `feature/jarvis-groks-packages`. Work: `work-afc13119d082`.

The build is available for isolated behavior review. The live checkout and services were preserved. Release and deployment remain outside this handoff.

Subsequent release integration found that the configured OpenAI model is unavailable to the account and the measured offered-model requests exceed the approved two-second classification deadline. Production package actions fail closed in those cases. Current release gates, diagnostic output and deployment coordination are recorded in [release integration](../release.md).

## Review evidence

- [Current Delivery tab](current-delivery-tab.png) shows the actual built frontend served by an isolated backend.
- [Diagram pixels](diagram-render.png) came through the actual MCP image result. The arrow runs from Budget to Operations. The supplied caption states the opposite direction.
- [MCP transcript](mcp-transcript.json) records scripted review requests and returned evidence. The run used the real HTTP/WebSocket application and MCP forwarding adapter. The cloud brain and speech were disabled for this browser demonstration.
- `tests/test_voice_page_package_content.py` reproduces the demonstration, including selection, open details, manual tabs, hidden frames, factory keyboard navigation, independent narration metadata, and different clients showing different slides.

## Implementation map

| Stage | Implementation | Evidence |
|---|---|---|
| `shared-readers` | `inspection_core.py` supplies description, paged text, rendering and digest operations to `BundleInspection` and `PackageInspection`. `bundle_readers.support_for` supplies inventory capabilities. `bundle_cache.py` supports local/team owners, private atomic writes, read-only source copies and shared active leases. | Attachment regression tests and package PDF/text/image parity tests. |
| `package-retrieval` | `package_snapshots.py` owns checked inventories and immutable source/results identities. `package_inspection.py` provides raw source, structured deck/story extraction, HTML outlines, literal FTS search, checkpoints, exact ranges, coverage, citations and continuations. `package_content_tools.py` measures the final wrapped output against the existing cap. | Exact continuation, malicious delimiters, unsupported media, malformed source, source mutation, results changes, source render reuse and restart/eviction tests. |
| `synchronized-view` | `package_observer.js`, `packageViews.ts` and `package_views.py` negotiate mount-bound fresh observations. The host supplies client and mount identity. `PackageView`, `packageBridge`, the factory deck and `BundleMemberView` report their current location through the authenticated connection. Story revision and completed narration position remain separate. | The browser demonstration and stale-mount/client tests. |
| `visual-understanding` | Existing PDF/image readers and the constrained HTML/SVG renderer return owner-bound short-lived image references. MCP resolves them to image bytes. Deliberate display capture reuses `screen.capture_screen` and returns a screen payload without a document page count. | Actual reconstructed SVG pixels through MCP; renderer, reader, remote-client and evidence-kind checks. |
| `approved-followups` | `PackageAuthority` classifies only direct user words and host IDs, then issues a turn-local scope. `PackageFollowups` resolves the proven sender or exact explicit recipient and calls the extracted `CapturePreparation`. `transmission` binds text, recipient and evidence, checks revisions at staging and delivery, and retains package context in existing capture/conversation/message records. | Scope, recipient, draft idempotency, changed binding, capture/transmission and existing delivery-status regression tests. |
| `integrated-review` | An isolated real server served the built frontend and actual package routes. Scripted MCP calls retrieved current DOM/selection and source image pixels. Review screenshots and returned evidence are stored here. | `mcp-transcript.json`, the PNGs, and the browser test. |

## Acceptance coverage

| Named case | Exercise |
|---|---|
| `whole-package-accounting` | Complete pageable member inventory, readable/unsupported states, and independent unread counts. |
| `slide-without-story` | The custom review page has no narration file. Source and current view remain available. |
| `manual-slide-following` | Actual factory clicks and arrow keys change the observed slide while the narration fixture remains on its own slide. |
| `generic-page-state` | Actual details, tabs, selection, visibility and reopen behavior. |
| `visual-claim` | The diagram pixels and its conflicting caption are retained as separate evidence. |
| `current-versus-replay` | Source renders carry `reconstructed_render`; display results carry `screen_capture` and display metadata. The browser screenshot in this review is a test artifact, not a product screen-capture result. |
| `unvisited-state` | Source and DOM results carry the unvisited dynamic-state limit. |
| `file-reader-parity` | The same PDF/text bytes use equivalent reader text/locations through package and attachment facades. The package image renderer reuses the existing image adapter. |
| `long-document-continuation` | Exact source text and delimiters survive final tool paging without raising the global cap. |
| `search-incomplete-index` | Search returns indexed coverage and pending/unread states. Substring matches can cross reader chunk boundaries. |
| `results-versus-source` | Results get a separate revision; unchanged source member identity and rendered image cache remain reusable. |
| `replacement-arrives` | Inbox replacement behavior remains covered by existing inbox tests. Mounts and citations retain their exact snapshot. |
| `mutation-during-read` | A repeatedly changing source is refused; evidence changes invalidate capture acceptance and revision validation. |
| `two-clients` | Two real browser clients show different slides; no client is inferred for an ambiguous request. |
| `close-and-reopen` | An old mount is rejected, and a hidden preserved frame is unavailable as the current view. |
| `blocked-member` | Existing archive refusal/inventory tests remain in force. Runnable ingestion retains its separate refusal behavior. |
| `injected-content` | Content remains untrusted; missing or mismatched direct scopes cannot mutate a package. Existing capture approval remains required for transmission. |
| `claimed-sender` | Follow-ups use proven sender fields; existing sender-proof tests remain in force. |
| `approved-agent-question` | Preparation uses the existing capture and message transmission path. Existing queued/sent/reply tracking remains the delivery record. Transport tests use controlled recipients. |
| `changed-draft` | Evidence binding participates in read-back/acceptance; changed text/context clears approval. Existing request keys and transmission records prevent duplicate preparation after staging. |
| `discussion-only` | No operation scope is created from inspection; an action still needs matching direct intent and capture approval. |
| `capability-loss` | Missing readers, observation timeout, unavailable images and remote display capture retain explicit failures while supported source reading continues. |
| `restart-and-eviction` | Static citation metadata survives; matching member bytes may be reacquired. Changed old bytes return `snapshot_unavailable`. Derived index records are swept when their source bytes are unavailable. |

## Runtime limits

DOM contents and locators remain package-controlled reports. Canvas painting, inaccessible frames, closed shadow roots and occlusion are not established by DOM text. A reconstructed load shows its initial state. Remote browsers have no display capture in this implementation. A local display image carries an explicit limit when package-to-display association is unverified.

Legacy registrations without a delivered source baseline retain the archive hash when available and report the source comparison as unavailable. Audio/video transcription, new Office rendering and converter installation were not added. Search covers indexed text and never infers absence across unread members.

A missing, warming or timed-out intent provider leaves inspection available and package mutations unavailable. The separate classification session has no tools, package material or previous conversation; Claude classification uses bare mode without session persistence. These internal processes are excluded from the user-agent roster.

Actual Mac display permission, cloud-provider classification latency, spoken read-back and delivery to a real external agent were not exercised by the isolated browser run. Existing capture/transmission tests exercise their controlled boundaries. Review those behaviors with the normal runtime configuration before release.

## Reproduce

Use the repository's existing Python environment. Run `scripts/generate_messaging_contract.py --check`, the focused package/reader/capture/transmission/provider regression tests, and `pytest -m page tests/test_voice_page_package_content.py`. Set `JARVIS_PACKAGE_REVIEW_DIR` to export the browser evidence to a chosen folder. The browser test uses temporary data and its own loopback port, and shuts that server down afterwards.
