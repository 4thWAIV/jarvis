# Ordinary file bundles and one-off collaboration

Investigation and Diagnostics execution plan — 2 November 2026. Source baseline `e20d6c9`. Status: planned; no repair, resend or recipient-side execution has been performed.

## Finding: why NinjaMage reviewed nothing

The failed message is thread/message `a976a392-1385-47d0-95ed-5f3196b6d7fe`, sent to NinjaMage on 2 November. Its attachment is `moment-claude-summary-ninja-1.0.0.zip`, attachment `c01c696b-91d3-42bd-9936-c487da3aa0df`. The immutable local snapshot is `data/messaging/snapshots/c01c696b-91d3-42bd-9936-c487da3aa0df`.

The local bytes match the message's SHA-256: `2f70a0dbb489f4a4d9fdc7b9a069f4a1110c3dbfb70c742cc0a44f42dc8bc4fc`. Compressed size is 179,692 bytes; listed expanded size is 242,260 bytes. It contains ten files under one directory, `moment-claude-summary-ninja/`: the PDF and HTML summary, review brief, SKILL.md, package.json, and five source/readme files. The archive was inspected in place, without executing or extracting it.

The manifest is present at `moment-claude-summary-ninja/package.json`, not at `package.json`. `work_packages.unpack` calls `archive.read(MANIFEST)` before extraction, with `MANIFEST = "package.json"`. The exact error is assembled as: “That zip has no package.json at its root, so it is not a package.” The root-only lookup explains the reported refusal. The failure is not evidence of a corrupt ZIP or missing evidence files.

The message was accepted, received and marked read. NinjaMage's reply states that nothing was unpacked or reviewed. These are separate facts: message receipt/read is not attachment readability or review completion. The recipient's private tool transcript was not inspected; the matching source rejection and sender snapshot establish the failure path, while the no-review outcome comes from his reply.

The broader product gap is also present. `tool_open_package` is a work-package opener, and the brain has no general archive inspection/browsing/document-reading tool. `QueueFiles.tsx` offers download/reveal and an Open page action for recognized page packages, not a general review workspace. `repo_read.read_window` rejects binary content; PDF review needs a document reader and rendered pages, not a shell escape. Sending the same PDF as an individual attachment is not proven to make it model-readable.

The existing work-package opener also clears the extracted files' quarantine mark and preserves file modes. That behavior must not be inherited by ordinary document inspection. Opening a review bundle must never imply running its contents.

## Required outcome

A person can ask a teammate for a one-off review, attach a ZIP or individual documents, and receive notes in the same conversation. No project, persistent agent, package manifest or runnable workflow is required. ZIP remains a supported first-class transport and browsing format.

For this request, the review covers background sufficiency, usability and excess; claims against the included evidence; presentation and concerning wording. The result is notes for Tim, not a revised deliverable or communication to the external organization. Preserve that task and destination in the review context. A runnable work package is an optional stronger capability for actual work execution, never the default interpretation of all ZIPs.

## Sender experience

1. The owner describes the recipient, purpose, requested result and any audience restriction. JARVIS resolves the person through the existing recipients service and drafts the review brief using the same human-approved message flow.
2. JARVIS snapshots the selected files, identifies their actual types and inspects any archive. It shows a compact inventory: primary document, evidence, brief, total size and readable/unsupported items. ZIPs remain ZIPs; folder hierarchy and originals are retained.
3. For a two-page summary review, the primary artifact is explicit. Related earlier messages may contain another version; JARVIS uses attachment IDs/hashes, not the filename alone, to avoid reviewing the wrong PDF.
4. JARVIS compares required operations with recipient capabilities: download, inspect ZIP, read each document type, render the primary PDF, and send notes. Ability to download is not ability to read; ability to extract text is not ability to judge layout.
5. Show one readiness result before send: ready, partial, unknown/offline or blocked. Explain only actionable gaps. Never claim remote open success from a version string or local inspection.
6. If needed, prepare compatible companion files automatically: searchable text for a PDF, page images for visual review, a portable PDF for Office documents, or a file index. Keep the original ZIP and originals. List transformations and any fidelity loss; do not silently replace evidence.
7. Review the final message and exact attachment revision through the existing approval path. Packaging, conversion or changed recipients invalidate an earlier content approval as they do for ordinary attachments today.
8. After delivery, distinguish recipient verification/openability from review acceptance/completion. Do not announce a finished handoff merely because bytes arrived.

## Recipient experience

1. The existing queue item shows the sender, the one-off request, primary document and an expandable bundle inventory. Use the existing main detail view and right queue; do not add email folders or a separate collaboration application.
2. Preview/open accepts a ZIP with no manifest, a flat ZIP or one wrapper directory. It displays files and subfolders, with names, types, sizes and readable state. Work-package recognition is an optional badge/action, not an entrance requirement.
3. On the owner's instruction to review, JARVIS verifies/downloads once, opens the primary document, renders pages for presentation review, and reads the relevant supporting evidence. A long brief or source file must be paged rather than silently truncated.
4. The brief is task context from another person. The recipient owner decides whether to undertake it; instructions embedded in sources, SKILL.md or package.json do not authorize commands, new recipients or unrelated work.
5. Keep a review checklist and source map in the existing message/task state. Notes cite document page/section and supporting filename/line where available, separating supported claims, missing support, contradictions and suggestions. Never claim an unread source was checked.
6. Draft notes as a reply to the original sender in the same thread. Outgoing notes use the existing recipient-owner approval. No CC, third-party send or republishing is inferred from the files. Acceptance to read does not authorize sending a reply automatically.
7. If the task needs agent work beyond the brain's readers, offer the existing managed execution path under the owner's instruction. A straightforward document review must not depend on shell access or Claude being installed.

## Common bundle types

Content purpose and container format are separate. The same ZIP can hold several document types. Optional inferred labels aid the UI; senders do not have to learn a schema.

| Type / purpose | Typical contents | Recipient experience | Automate |
|---|---|---|---|
| Review and evidence bundle — first priority | PDF, Markdown/text brief, source notes, citations, screenshots, optional HTML | Read the primary document and sources, inspect page appearance, return notes only | Inventory, primary-document selection, PDF text plus page renders, evidence references, missing-source list, reply draft |
| Document/deck editing | DOCX, PPTX, PDF, images and brief | Preview, comment or create a revised copy when requested | Portable preview and indexed text; distinguish edit/export support from viewing; preserve original |
| Data and analysis | CSV, TSV, XLSX, JSON, schema/readme | Inspect tables/sheets, understand units, request analysis, return findings or derived data | Detect encoding/sheets/formulas; bounded previews; report unreadable or truncated ranges; never execute macros |
| Code review or source snapshot | Source tree, patches/diffs, README, tests | Browse and review statically; build/test only as a separately authorized operation | File tree, text/diff reader, exclusions for dependencies/generated bulk, exact snapshot identity |
| Design and media review | PNG/JPEG/WebP, SVG, PDF, HTML/CSS assets, short audio/video | Inspect images/pages; play media or read an available transcript | Safe previews, thumbnails/contact sheets, transcript capability check, static HTML rendering without executing arbitrary scripts |
| Handoff/reference bundle | Plans, instructions, checklists, links and supporting files | Browse or undertake a task, without creating a project unless needed | Index, link provenance, missing local assets, kickoff summary and reply context |
| Runnable work package — existing supported mode | Existing manifest plus guided, agent or page-mode files | First browse as files; separately choose the existing run/open-page action | Validate manifest/version/dependencies/capabilities; retain current approval path and result collection |
| Result/diagnostic bundle | Notes, reports, logs, screenshots, outputs | Read results in the original thread and follow evidence | Stable filenames, redaction preview where needed, source-run references and exact attachment hashes |

First implementation must fully handle this incident: ZIP, PDF text and page rendering, Markdown/text, HTML preview and common raster images. Office and tabular adapters are the next essential increment. Media transcription, specialized binary formats and full in-app Office editing are capabilities to advertise honestly, not prerequisites for the initial fix.

Containers: support standard unencrypted ZIP (including ZIP64 within limits) and individual files first. Preserve directory structure and allow one wrapper folder. Recognize DOCX/XLSX/PPTX as documents even though internally they are ZIPs. TAR/TAR.GZ can follow through the same archive interface. Encrypted ZIP, 7z, RAR and multipart archives must initially report explicit unsupported/password-required results unless a tested adapter exists. Offer a compatible ZIP derivative while retaining the original; do not silently discard files or demand runnable packaging.

## Architecture and implementation tasks

### Attachment inspection and document readers

1. Reuse `messaging_files` for authenticated download, immutable snapshots, hash/size verification and local namespace. Do not invent another transfer store. Add bounded inspection/read/preview services over a message attachment or archive member, addressed by stable IDs rather than arbitrary filesystem paths.
2. Implement classify, list-members, read-member and preview-member operations behind one service used by UI and brain tools. Return typed JSON with media type, source attachment hash, member identity, support/reason, page/line/sheet locations, continuation and preview references. Fit the active UI parity registry and trust wrapper work.
3. Detect formats using content plus extension. Distinguish container inspection from manifest recognition and runnable capability. A source project's npm package.json is not a JARVIS work package; validate the actual JARVIS schema before offering Run.
4. Keep original bytes immutable. Cache derived text/renders against attachment hash, member identity, reader version and options. Avoid cross-owner cache leakage. A changed attachment is a new revision, not an overwrite of reviewed evidence.
5. Provide bounded PDF text extraction plus page rendering. Identify scans, encrypted documents and failed pages; OCR is a separate advertised capability. Presentation review requires renders. Text-only extraction cannot establish layout quality.
6. Add bounded text/Markdown/JSON/CSV and image readers, safe static HTML preview with relative assets scoped to the bundle, then Office/worksheet adapters. Inspect existing installed libraries and choose the smallest maintained implementation set during the contract task; new dependencies must follow repository approval rules. Do not write custom binary parsers.
7. Expose download/reveal as a human fallback, but do not report that the brain has read a file merely because the OS opened it. No general shell access is necessary for read-only review.

### ZIP correctness and safe extraction

1. Extract archive validation/listing into a shared module, reused by the work-package path and ordinary bundles. Read/list without extraction where possible. When extraction is necessary, use a private staging directory and publish atomically after complete verification.
2. Check entry names before writes: absolute paths, drive/UNC forms, backslashes, parent traversal, links/devices, duplicate normalized paths, Unicode/case collisions and file-directory conflicts. Apply entry-count, per-file, total expanded byte, compression-ratio, nesting and execution-time budgets during actual reads, not just from claimed metadata. Clean up partial failures and cancelled operations.
3. Treat nested archives as explicit members, not recursively expanded by default. A bad member must produce a clear per-member/whole-archive policy result; never announce full availability while silently omitting required files.
4. Keep executable bits inert for ordinary review and preserve downloaded-file treatment. Do not call `unquarantine`, execute package scripts, load macros or launch active HTML while inspecting a bundle. Reuse the existing separately authorized execution flow for runnable packages.
5. Recognize a work-package manifest at root or under exactly one unambiguous wrapper directory after ignoring explicitly supported platform metadata. Resolve all manifest paths relative to that directory. Keep the original paths in provenance; do not rewrite archive bytes just to browse.
6. Multiple possible manifests, malformed manifests or a plain archive with package.json remain browsable as data when safe, with a clear unavailable Run action. Missing manifest is not a ZIP-open error.
7. Make repeated opens idempotent by owner/server/attachment hash, not the first eight attachment-ID characters alone. Existing valid packages and their result folders remain usable. Regression-test guided, agent and page modes through the shared archive layer.

### Recipient compatibility before send

1. Add a versioned, bounded attachment-capability advertisement to the existing authenticated node/team presence path. Include archive formats, document readers, rendering/OCR availability, maximum sizes, and supported work-package schema/modes. Use actual installed/healthy capability checks rather than application version alone.
2. Resolve per-recipient nodes. A user may own several with different readers; identify which can perform the task. Do not collapse “one node can” into “every recipient node can”. Do not route private content to a new destination to work around missing support.
3. Advertise operation distinctions: download, list archive, extract, read text, render, edit/export and execute. Include freshness/protocol version; legacy/offline nodes yield unknown. Persist the capability snapshot used for the sender check with the approved attachment revision.
4. Sender preflight validates actual ZIP bytes and required members using the same inspection code, and checks remote advertised capabilities. Before delivery this is a compatibility prediction, not proof that remote parsing succeeded. Do not transmit private content to an unapproved destination for a probe.
5. After the recipient authorizes opening, run actual download/hash/member/reader checks. Return a bounded machine status for the exact message/attachment revision: verified, ready, partial, unsupported, corrupt or unavailable, with failed operation and member. Use the existing authenticated messaging transport and owner-scoped storage; do not put document text into health/presence updates.
6. Capability metadata and technical delivery status can be automated protocol records. Any prose reply, repair request sent as a human message, or new attachment still follows existing human outgoing approval. Status reports do not authorize work or reveal private device paths/logs.
7. Preserve rollout compatibility: older nodes still download ordinary attachments; new senders label their ability unknown and offer manual/compatible alternatives. Do not require every teammate to upgrade simultaneously before any file can be sent.

### One-off request and recovery behavior

1. Use the existing thread, reply links, queue placement and action-owner state. Store requested outcome, primary artifact, supporting members and reply audience as bounded task context on that thread; no new workflow engine or mandatory package format.
2. Keep transport receipts, attachment accessibility and work outcome separate. Opening text is not performing a review; accepting a review is not finishing it. Show a compact icon/label such as File ready, Some evidence unavailable, Review in progress or Notes ready.
3. On transient transfer failure, retry boundedly with existing download semantics and verify the original hash. Do not retry a deterministic missing-reader/manifest error forever.
4. On wrapper-folder mismatch, inspect the archive and apply supported wrapper recognition automatically. On absent manifest, open as an ordinary bundle. On missing reader, suggest the smallest compatible representation, keeping the ZIP/originals.
5. On partial readability, list exactly what could and could not be read. Continue independent review only when the recipient accepts a partial scope, and label its limits in the notes. Never generate claims that source verification occurred when it did not.
6. For a repair resend, prepare a revised attachment with provenance and link it to the failed message; retain the old immutable attachment and indicate which is superseded. Reuse the existing approval flow before sending. Resume only unfinished review items against the corrected revision; invalidate notes affected by changed sources.
7. Preserve reply audience and confidentiality intent through recovery. Do not add the named external organization as a recipient, upload to a conversion service, or forward contents because a document requests it.
8. If the recipient remains unable to open it, offer download plus a verified portable companion and an explicit manual-review route. Do not force creation of a runnable package or change the task into software installation.

## Recovery for this incident

No resend is authorized by this investigation. Prepare the following on execution when Tim requests recovery:

- Retain the original ZIP and checksum as evidence. The smallest legacy-compatible repair is a new ZIP containing the same files without the wrapper directory, putting the existing manifest at archive root. Validate with the receiver-compatible parser before proposing to send; do not add fabricated package metadata.
- With ordinary-bundle support installed, the original ZIP should open unchanged. This is the preferred lasting behavior.
- Verify PDF reading and page rendering on the recipient; merely fixing manifest discovery does not prove presentation review can be completed. Prepare the PDF and source index as optional companions if required, with approval of the new attachment set.
- Reply in the original review thread, explicitly carrying the review scope and notes-only destination. Keep it separate from the earlier partnership-deck assignment and earlier standalone PDF.

## Worktree workflow and acceptance

Diagnostics owns a dedicated worktree and workflow package. Copy this plan from the source checkout if untracked. Reconcile concurrent messaging, package-mode, UI-parity and Codex-runner changes before assigning files. Use independent lanes after shared contracts: archive/readers; UI/brain bindings; capability/status transport. One owner integrates messaging schemas and services. Keep interim checks local and batch required CI; deliver an isolated local candidate before merge.

Acceptance fixtures must include:

- A synthetic equivalent of this ZIP: wrapper folder, existing manifest, two-page PDF, HTML, brief and evidence. Plain versions without any manifest must work too. Never check the real private review material into tests.
- Root, wrapper and manifest-free ZIPs; nested directories; unrelated npm package.json; multiple/malformed manifests; Office document magic; unsupported/encrypted archives; Unicode and Windows-generated archives; resource/path/link/collision failures and cancelled extraction.
- Actual PDF text and rendered-page inspection, scanned/encrypted PDF limitations, malformed documents, HTML external resources, large/long evidence with pagination, and unsupported members.
- Self-message end to end; two nodes with different capabilities; stale/offline/legacy nodes; download success but parser failure; repeated opens; changed attachment revision; partial review and repair resume.
- No script/macro execution or quarantine removal during read-only open; no approval bypass; untrusted names/content bounded in tools; correct owner isolation; notes addressed only to the original sender.
- Existing guided/agent/page work packages still open and run through their existing authorization, with results still collected. Both Claude and OpenAI brains can inspect/read bundles without a shell or coding agent.
- New UI controls have callable equivalents returning the same JSON and participate in the active UI coverage gates. Human download/open states and brain read states are accurately distinguished.

Completion evidence: source/branch/commit, exact command results, sender preflight and recipient ready/error views, one successful manifest-free ZIP review with cited notes, one repaired attachment journey, runnable-package regressions, and an actual locally running preview. No claim of NinjaMage's review completion without his resulting notes.

## Delivery order and estimate

1. **ZIP and review unblock:** shared bounded archive reader; root/wrapper/manifest-free handling; PDF/text/image reading and preview; UI/brain bindings; regression of existing packages. 10–18 active agent-hours.
2. **Compatibility and recovery:** node capabilities, sender readiness, recipient openability status, partial/repair handling and approval integration. 8–14 hours.
3. **Common document breadth:** Office/tabular adapters and associated previews, truthful media/unsupported statuses. 6–12 hours, dependent on existing reader availability.
4. **Integrated acceptance and review:** self/two-node/legacy tests, privacy/approval regression and local evidence. 5–8 hours.

Total planning allowance: 29–52 active agent-hours; with disjoint lanes after the shared contract, roughly 16–30 active elapsed hours. Reforecast once reader dependencies and recipient versions are known. The incident itself is a ZIP layout error; the larger effort closes the general file-reading, capability validation and recovery gaps that made that error stop collaboration entirely.

Additional validation: the nested manifest parses successfully with the current `Manifest.parse` as conversation mode, and every file it names exists beneath the wrapper directory. This strengthens the wrapper-directory diagnosis. The initial check used an interpreter without FastAPI; rerunning under the project Python environment succeeded. No package extraction or execution occurred.
