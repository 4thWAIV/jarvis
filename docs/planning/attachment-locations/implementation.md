# Received attachment locations — independent Diagnostics build

Prepared 2 November 2026. Implementation authorized as a separate build. This plan is independent of the active collaboration-bundles workflow. Its next step is a dedicated worktree and workflow; it does not gate or expand that build.

## Result

For any received attachment, JARVIS can identify its exact verified local path, download it when asked if necessary, show/copy/reveal its location, and provide an accessible reference to an authorized agent. A ZIP needs no work-package manifest for this operation. Locating a file does not unpack or execute it.

## Source finding

The second reported failure is a missing product interface, not an absence of download-path logic. `messaging_files.Files.download_path` computes `<downloads>/<attachment id>/<safe filename>`. `verified_path` resolves the received message attachment and rechecks size/hash before returning that exact path. `downloaded` ensures download and returns it, but is currently used by work-package opening. `messaging_ports.LocalFile` exposes only attachment ID, state, progress and failure; it does not expose location. JARVIS therefore lacks a general tool result it can use to answer where a received ordinary file is saved.

NinjaMage's actual local absolute path has not been inspected and must not be guessed from Tim's installation. Every location answer is node-specific and must come from the recipient's own file service.

### Implement a shared attachment-location resolver

1. Add a typed location result and shared resolver to the existing messaging-file service. Identify the attachment by authenticated owner/server, message ID and attachment ID, never a filename search. Reuse the existing persisted messaging-files record and configured download directory; no second attachment registry or storage system.
2. Return `message_id`, `attachment_id`, `filename`, `node_id`, `state`, `local_path`, `size_bytes`, `sha256`, `verified_at`, availability reason and supported actions. `local_path` is an absolute native filesystem path only for an existing verified original. Keep an intended download destination separate and explicitly not yet downloaded; never describe it as a saved file.
3. Separate read-only lookup from ensure-local. A lookup reports remote-only, downloading, verified, missing, changed or failed accurately. On the owner's request to open, locate for use, or give a local agent the attachment, ensure-local uses the existing authenticated downloader automatically within that request, verifies the bytes, and returns the real location. There is no manifest prerequisite and no extraction or execution side effect.
4. Expose both operations through local API and brain tools backed by the same service. Include location information when reading an attachment detail or completing a download. Return structured JSON and preserve the existing untrusted-filename/content handling. Do not make the brain reconstruct filesystem paths from a general data-directory hint.
5. In the queue file detail, show the exact location with Copy path and Reveal in Finder/platform equivalent. Keep the compact row concise; show download state and an action to make the file local when necessary. Register every new control and equivalent call through the UI-parity work. Selecting/copying a path does not execute its file.
6. Give the owner the complete path on request, plus a clickable reveal/open affordance where supported. A voice answer may offer to show/copy the location rather than reading a long path aloud, but the exact path must be present in the tool result and visible UI.
7. When handing an attachment to a same-node agent, include its exact verified path and stable attachment reference. Check the agent's permitted filesystem access. If its sandbox cannot read the location, provide the existing scoped attachment reader or an authorized verified copy in its assigned workspace; return that copy's actual path and provenance. Do not say an agent can access a path merely because JARVIS can.
8. A path on another desktop is not a local path. For a remote agent/node, pass the authenticated message/attachment reference through the existing authorized transfer flow, then resolve/download on that node and return its path. Do not forward contents to another owner or publish local paths to team presence records merely to locate a file.
9. Resolve location after restart from durable attachment metadata and the configured storage root. If bytes were removed, changed or moved, return the real missing/mismatch state and offer or perform authorized re-download; never keep claiming the stale path is accessible. Respect existing retention policy, and protect attachments held by an active review/agent lease from cleanup until released.
10. Preserve the original archive location separately from any extracted directory and member paths. Inspection can succeed without extraction; then report member references, not nonexistent extracted files. On successful extraction, record the verified source hash, extraction root and requested member path so JARVIS can locate both ZIP and contents. Root-level manifests, wrapper manifests and manifest-free ZIPs all use this same location path.

## Acceptance for this independent build

- Receive a plain ZIP with no manifest, request its location, download/verify it, return its exact path, and read those bytes through a permitted local agent. Repeat for PDF and an arbitrary non-package attachment.
- Resolve two identically named attachments without ambiguity using their IDs; handle spaces, Unicode and configurable data roots without shell interpolation.
- After restart, return the same valid location. After deletion or tampering, report missing/mismatch and recover through verified download rather than a stale answer.
- Exercise another node and a restricted agent: report only a path accessible in that environment or the explicit access limitation with a supported reader/copy alternative.
- Assert owner isolation, no work-package parser dependency, no automatic unpack/run, correct UI/tool JSON parity and unchanged original bytes.

## Worktree and workflow execution

1. Diagnostics creates a separate native workflow named Received attachment locations and a dedicated `feature/attachment-locations` worktree from current main. Record its base, task owners and progress. Do not restart or modify the scope of collaboration-bundles (`wf-b44fd11532f5`).
2. Read the existing file service, local result schemas, UI controls, tool dispatch and applicable repository instructions. Implement against the existing downloader, not the future bundle reader. Use one shared location resolver for API, tool and UI.
3. Coordinate ownership of `messaging_files.py`, `messaging_ports.py`, `QueueFiles.tsx`, tool registration and server wiring with the bundle integrator. Work on the separate branch, keep changes narrow, and resolve overlapping edits during integration. The bundles build continues independently.
4. Add a structured location result without unnecessarily changing the Team Server wire contract. Local paths stay node-local. Persist only additional metadata actually required; reuse existing SQLite/file records and stable attachment IDs.
5. Implement lookup and ensure-local, then UI and brain access, then agent handoff checks. Existing authorized workspace copy/reader mechanisms may be reused; if a remote or sandboxed agent has no supported access path, return an explicit reason instead of claiming success or building a new transfer system.
6. Extracted-member paths are optional information only when extraction already exists. Do not implement ZIP extraction, document readers, capability negotiation or bundle review in this build. The original ZIP location must work without any of those features.
7. Test with synthetic local attachment fixtures, isolated data and a self-message flow. Include owner isolation, identical filenames, deleted/tampered files, download failures, restart, and local/remote/sandboxed agent access. No private review material in committed tests.
8. Run relevant messaging-file/API/UI/tool checks and required repository checks. Keep intermediate runs local and batch integration fixes. Deliver a running isolated candidate on available ports, with exact location demonstrations, commands/results and a reviewable diff. Do not disturb either existing preview or the live instance.
9. Report through Diagnostics with workflow/worktree/branch, candidate URL and validation steps. Wait for Tim's local validation before merge or deployment. This build does not authorize resending the real review bundle.

## Task order and ownership

- `location-contract`: integrator defines result schema and shared resolver, including unavailable states.
- `location-service`: lookup/ensure-local and verification, with focused file-service tests.
- `location-surfaces`: API/tool/UI bindings with identical JSON and Copy/Reveal/download actions.
- `location-agent-handoff`: same-node access check and supported reader/copy reference; clear remote limitation.
- `location-validation`: independent review, isolated running candidate and acceptance evidence.

Plan allowance: 2–4 active agent-hours for the existing-service resolver and basic surfaces. Reforecast at preflight if existing agent handoff or UI parity infrastructure cannot supply the required access checks; do not conceal additional work in the bundles estimate. No dependency on the bundles release.

## Demonstration for Tim

Receive a manifest-free ZIP, ask where it is, obtain the complete verified path, copy/reveal it, and have an authorized local agent read the same bytes. Repeat after restart and after deleting the local copy: JARVIS must report the real state and recover the download on request. Show that another node's path is never passed off as local.
