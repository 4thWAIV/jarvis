# Local folders — Diagnostics implementation handoff

Status: ready for implementation planning and execution in an isolated worktree. Prepared 30 October 2026 against local main afcea2f. Recheck the current source before editing. This document specifies the local Folders increment; the broader projects/compute transition is not this build.

## Outcome and scope

Deliver an independently usable Folders feature in JARVIS: persistent local folder registration, discovery candidates from existing sessions, scoped directory browsing and supported previews, location/availability tracking, and separate main and right-hand Folders views. Existing agents, their names, processes, conversations, delivery and controls continue operating.

Store all catalog records in each node's existing SQLite database returned by `data_paths.db_path()`. Use `data_paths.node_id()` for local node identity. Do not create another database, JSON catalog or storage service. Persist through application restart and machine reboot. Original files, memory and provider histories stay at their original paths.

Projects follow later. The eventual relationship is many-to-many: multiple folders per project and multiple projects per folder, using a relationship table in this same existing database. Keep stable folder IDs and do not put a required project ID or exclusive project ownership into this increment. Do not add project CRUD, project tabs or project-link mutations now.

Do not implement new agent launching, agent retirement, compute scheduling, remote filesystem access, full-text indexing, file editing, uploads, filesystem moves or filesystem deletion in this increment. Registration and catalog removal affect metadata and access, not the underlying files.

## Reference designs

The visual documents are currently local working artifacts under `.agents/in-progress` in the original checkout:

- `folders-display-and-project-links.html`: main folder catalog, right selector, details and future many-to-many relationships. Project controls shown there belong to the next increment.
- `folders-first-implementation-plan.html`: folder-only behavior and rationale.
- `project-folder-compute-implementation-plan.html`: broader sequence; use only its continuity and existing-SQLite constraints for this build.

These ignored artifacts may not appear in a new worktree. Read them from the original checkout or copy only the needed design documents into a temporary reference directory. This handoff contains the implementation requirements so those visuals are not a hidden runtime dependency.

## Start safely in a separate worktree

1. Read the current repository instructions and applicable code-quality skills. Inspect `git status`, current main, and local changes. Leave unrelated work intact; `docs/vector-search-models.md` was untracked at inspection.
2. Read this handoff from the original checkout before creating the worktree. Uncommitted files do not automatically follow a new worktree. Copy this document and the startup prompt into the new worktree if they are not yet committed; include them with the implementation change.
3. Create a feature branch/worktree using repository conventions. Record the actual baseline commit in the implementation report. Do not build in the running application's checkout.
4. Set a task-specific, temporary `JARVIS_DATA_DIR` for tests and development before starting any service or importing initialization paths. Fixtures must use synthetic provider homes and transcripts. Do not mutate the live database or use real private histories in test output.
5. Inspect the existing service/run lifecycle before a live rollout. Prove that the chosen deployment mechanism leaves current agent processes running and preserves message queues. A successful resume after killing a process does not satisfy continuity. If safe live rollout is unresolved, finish the implementation and report the concrete deployment blocker; do not restart the live instance to experiment.

## Code entry points

| Existing source | Required use/change |
| --- | --- |
| `jarvis/data_paths.py` | Reuse `db_path()` and `node_id()`. Do not call a new storage root for folder metadata. |
| `jarvis/run_store.py` | Follow the SQLite connection/transaction conventions. Register additive folder schema initialization with existing startup. Extract shared connection logic if needed instead of cloning it or importing unrelated agent behavior into the folder module. |
| `jarvis/server.py` | Wire startup, HTTP routes, existing authorization, and off-thread filesystem work. Keep handlers thin over the folder service. |
| `jarvis/session_watch.py` | Consume structured Claude roots, cwd, profile and session metadata. Do not alter roster lifecycle or message routing. |
| `jarvis/codex_sessions.py` | Consume configured development-session discovery. Keep the brain's Codex home distinct. |
| `jarvis/history_read.py` | Reuse appropriate transcript parsing and safe-root primitives without widening its existing brain-only API scope. |
| `jarvis/repo_read.py` | Reuse bounded reading and sensitive-file checks. Its existing project and hidden-path restrictions are not a generic folder access contract. |
| `jarvis/jarvis_mcp.py` and server tool dispatch | Add bounded folder tools through the same service as HTTP. Preserve normal content wrapping and tool authorization. |
| `frontend/src/CentreArea.tsx`, `RightPanel.tsx`, `Tabs.tsx` | Add Folders main/detail view and right selector. Keep the existing left column unchanged. |
| Existing frontend store and request patterns | One folder store supplies both views. Reuse event/reconnect conventions. |

Suggested new modules: `folder_store.py` for persistence, `folder_discovery.py` for observations, and `folder_service.py` for policy/resolution/reader composition. Add reader modules only where provider formats require them. The store must not parse transcripts; UI and MCP must not independently implement path permissions.

## Persistent records and migrations

Use an additive, idempotent, versioned migration in the existing database. Enable the same foreign-key and transaction behavior as current stores. Initialization must work both on a fresh database and a populated pre-feature database.

| Table | Required data and invariants |
| --- | --- |
| `folders` | UUID, existing local-owner identity where available, display name, role, lifecycle, revision, creation/update times. Role labels describe data/source/history, not permission. No project dependency. |
| `folder_locations` | UUID, folder ID, local node ID, supplied path, canonical path, optional filesystem identity, availability, observation times, last error code. One primary location per folder initially. |
| `folder_access_scopes` | Location ID, reader kind, explicitly permitted subtree, policy version, enabled state. Catalog discovery alone never enables access. |
| `folder_observations` | Candidate/location reference, provider, profile identity, native session ID, observed cwd, evidence type and timestamp. Retain associations after session exit. |
| `folder_candidates` | Stable candidate ID, local node/path key, proposed role, state, last observation and optional registered folder ID. States include discovered, ignored and registered. |
| `folder_location_events` | Folder/location ID, prior and new location metadata, event type and timestamp. Preserve reconnect and availability provenance without storing credentials. |
| Registration idempotency record | Unique request key, canonical request fingerprint, resulting folder ID. A repeated key with different input is a conflict. Use existing idempotency infrastructure if suitable. |

Index normalized local location identities, active catalog lookup and provider/session observations. Deduplicate validated aliases; do not merge by basename. Filesystem identity is evidence, not an eternal key. Parent and child roots can both be registered for different scopes. Use location plus canonical relative file identity to avoid duplicate content identities; listing a parent does not bypass restrictions on a protected subtree.

Update the record and its revision in the same transaction. Return the committed revision, then publish notifications. Reject stale metadata edits. Unregister soft-removes the record, disables access and preserves session provenance. Registration can explicitly reactivate a removed record after validation. No filesystem deletion occurs.

A later migration adds projects and `project_folder_refs(project_id, folder_id, scope, purposes, ...)` to this database. Do not create empty project infrastructure merely to make folders work.

## Registration and discovery behavior

1. Manual registration accepts a local absolute path or a home-relative path. Reject relative paths whose base is ambiguous. Validate a directory, local node ownership and OS access; resolve aliases. Show the resolved location and applicable reader scope before saving.
2. Begin with registration of existing directories. Creating a new directory is not needed for this increment. A missing initial path returns a clear error; an already-registered path that disappears remains in the catalog as Missing.
3. Discover candidate workspaces from existing Claude/Codex metadata, configured custom profiles and retained histories. Reuse shared root resolution instead of separately maintaining conflicting root lists.
4. Register history locations separately from cwd. Read structured metadata to establish the relationship. Never decode a lossy Claude directory name to guess the original cwd.
5. Keep candidate discovery separate from registration. Opening Folders may show candidates, but does not automatically grant access or create project records. Ignore persists across scans until the candidate materially changes or the user resets it.
6. Observe memory, subagent histories and outputs where supported metadata establishes their location. Keep unresolved associations visible. Do not recursively scan the entire disk or ingest a whole provider configuration home.
7. Run startup reconciliation and throttled subsequent observation off the voice loop. Bound historical discovery, retain a cursor/progress state and report incomplete coverage. A malformed or mid-write record is skipped/retried without dropping previously known associations.
8. Availability values distinguish Available, Missing, Access denied and Unavailable node. Record last successful observation separately from last attempted check. Initial registration only supports the current node.

## One bounded access service

Use a shared resolver and typed reader adapters. Existing `repo_read` excludes hidden and sensitive paths; existing `history_read` grants narrowly scoped brain-history access. Preserve those APIs' existing restrictions. Extract common checks rather than disabling them globally.

For the explicitly registered `./.agents/in-progress` root, permit that subtree without opening the whole `.agents` parent. For brain notes and provider histories, select a reader policy designed for that scope. Exclude auth, tokens, connection secrets and unrelated provider configuration. Folder role is not a way to override restrictions.

Revalidate path containment and authorization for every listing/read. Reject traversal and symlink escapes. Use descriptor-relative operations or an equivalent race-resistant approach for replacement between validation and reading. Handle missing files and changed locations explicitly.

Proposed initial budgets: 100 directory entries per page; a two-second listing deadline; text windows of at most 200 lines and 64 KiB; files above 4 MiB require a supported bounded reader rather than whole-file reads. Align limits with current reader behavior where stricter. Provider transcript adapters must support large files through bounded reads, not load the whole conversation. Mark incomplete results; expose continuation only where it is safe.

Sort within an explicit budget. Do not promise a complete alphabetical listing for a huge directory if the bounded scan was truncated; indicate partial coverage and use a stable continuation strategy. Cancel superseded requests and reject cursors after a location revision changes.

Return binary/media file metadata in the first increment. Render text escaped; HTML files are displayed as text, not executed inside the application. Annotate folder, location, relative path, revision and source session provenance. Brain tools wrap retrieved content using existing untrusted-content conventions.

## HTTP and tool contracts

Finalize typed payload definitions before parallel implementation. These route names are the proposed implementation contract, subject only to repository naming conventions.

| Operation | Route and behavior |
| --- | --- |
| List catalog | `GET /api/folders` with query, role, availability, cursor and bounded limit. Return folder summaries and revisions. |
| Register | `POST /api/folders` with request key, path, display name and requested reader scope. Return resolved location, policy and folder ID. Never accept arbitrary policy overrides. |
| Detail/edit/remove | `GET`, `PATCH`, `DELETE /api/folders/{id}`. Edit takes expected revision; DELETE unregisters only. |
| Discovery | `GET /api/folders/candidates`; candidate Register uses the same registration service; Ignore persists through a candidate action endpoint. |
| Refresh | `POST /api/folders/{id}/refresh` schedules bounded observation and reports current/pending state. |
| Reconnect | `POST /api/folders/{id}/reconnect` with new path and expected revision. Validate identity and explicit replacement intent before retaining the folder ID. |
| Browse | `GET /api/folders/{id}/entries` with relative path, location revision and cursor. |
| Preview | `GET /api/folders/{id}/preview` with relative path and bounded window/reader cursor. |

Define static routes before a dynamic `{id}` route where the framework requires it. Use a stable structured error code for missing, forbidden, unavailable, unsupported, stale revision and partial coverage. Keep display errors free of credential material. Use 409 for conflicting revisions/idempotency keys; return permission-aware errors under existing API conventions.

Integrate new endpoints with existing web-origin authentication and scoped access-key authorization; do not accidentally make every existing key a folder-reading key. Reuse established notification transports and reconnect invalidation rather than adding a second event service.

Add `list_folders`, `find_folder`, `browse_folder`, `read_folder_file`, `register_folder` and `unregister_folder` tool definitions. All route through the same service. Mutation tools describe effects precisely. Ambiguous names require selection; persistence and reads use folder IDs. Never expose raw arbitrary-path reading as a shortcut.

## Intended UI

Main Folders view: catalog with name/path search, Registered/Discovered views, role and availability filters, Add existing folder, and the selected folder's detail. A row shows role icon, name, node, labelled availability and last observation. Do not show fake project-link counts before project support exists.

Detail: actual location, current reader/access state, contents with breadcrumbs and bounded paging, related sessions, refresh, label editing, reconnect and unregister. Clearly distinguish catalog removal from file deletion. Show empty, loading, partial, missing, denied and unsupported-preview states. Retain selected folder and scroll when switching main views.

Right Folders view: compact selection list using the same store; selecting a row opens the main folder detail. Add it alongside the existing five destinations, using six labelled tabs in two rows. Existing left-side controls and attention indicators stay in place. Project tabs/link editing follow in the next increment.

No side panel becomes a duplicate full file browser. Content and long paths belong in the center. Keyboard focus, tab semantics, filter labels and status text must work without relying on color.

## Build order and ownership

1. **folder-contract:** record typed requests/responses, schema migration and reader-policy boundaries. Establish how rollout preserves existing agent processes. Produce sanitized fixtures and contract tests.
2. **folder-core:** implement persistence, identity resolution, registration, revisions, unregister and reconnect. Demonstrate persistence with zero project tables.
3. **folder-discovery:** implement metadata adapters, candidates, ignore state, observations and bounded reconciliation. This can run alongside UI once the shared service contract exists.
4. **folder-readers:** implement scoped directory and preview adapters with budgets and race-resistant containment. Integrate existing policies rather than copy/pasting readers.
5. **folder-interface:** implement shared store, main/right views and actual registration/browser flows. Use contract fixtures while backend work proceeds, then remove mock paths.
6. **folder-integration:** wire startup, routes, tool authorization, notifications and diagnostics. Exercise representative directories one at a time.
7. **folder-release:** run integrated local verification, then required CI on a reviewable combined change. Stage the completed feature; validate ongoing-agent continuity before any live rollout.

A single integration owner owns schema and API changes. Parallel workers, if used by Diagnostics, should own separate modules after the contract is fixed. Do not launch separate CI/deploy runs for every subtask. Keep required checks enabled and push reviewable milestones. Do not merge, deploy or restart the live instance merely because tests pass; use the established release authorization and continuity gate.

## Test matrix and validation commands

Use temporary databases and synthetic directories; do not test destructive behavior on real folders.

| Case | Required result |
| --- | --- |
| Upgrade and repeat initialization | Existing rows/messages survive; migration is idempotent; only existing local SQLite is used. |
| Restart/reboot persistence | Close all connections and restart against the same data directory; IDs and access scopes persist. Perform a real reboot smoke test on an isolated test node before claiming reboot verification. |
| Idempotency and aliases | Repeated registration returns one identity; conflicting request-key reuse is rejected; equal basenames remain separate. |
| Parent/child access | Explicit hidden subtree works; unrelated sensitive children stay closed; overlap cannot widen access. |
| Path attacks | Traversal, symlink escape and symlink replacement during read cannot expose outside content. |
| Large and mutable folders | Limits and partial markers work; stale cursors after reconnect are rejected; voice event loop stays responsive. |
| Histories | Live, stopped, malformed and multi-profile session records preserve provenance; cwd is not guessed from encoded names. |
| Unregister/reconnect | Files and native sessions remain untouched; removed scopes stop reading; reactivation/reconnect is explicit. |
| HTTP and MCP | Both enforce the same policies; unauthorized scoped keys cannot gain folder access; mutations obey normal origin checks. |
| UI | Main/right selection matches; filter/no-results/denied states are visible; keyboard navigation and context retention work. |
| Live continuity | A running session retains its process and conversation and can exchange a queued/in-flight message through the chosen deployment path. |

Run new focused tests together with relevant existing suites such as `tests/test_repo_tools.py`, `tests/test_history_read.py`, `tests/test_projects_api.py`, `tests/test_scan_projects_offloaded.py` and database/auth tests affected by the change. Discover current names before execution. Use the repository's Python environment and pytest invocation conventions.

Frontend validation: run `npm run build` from `frontend` (currently TypeScript plus Vite). Run browser behavior checks through the project's authorized test workflow; do not claim visual verification from TypeScript compilation alone. After focused checks pass, run the full relevant backend suite once on the integrated candidate. Document actual commands and results. Investigate failures instead of blindly repeating CI runs.

## Acceptance, rollout and report

The release is complete when registered local folders persist in the existing database, are browseable through their approved readers in the main/right UI and tools, retain session provenance after process exit, and leave current agents operating. It must work with no projects and no compute redesign.

Revalidate these original example paths only during explicitly authorized manual acceptance: the JARVIS planning subtree, the JARVIS source root, the brain journal subtree, the exact JARVIS Claude history scope, and Tokens for Cash's docs folder. Register them individually, not by bulk disk scan. Test data precedes real registrations.

Roll out additive schema and feature changes. If rollback is needed, disable the folder feature/routes while preserving new metadata and ongoing writes. Do not restore an old whole-database snapshot over messages produced since deployment. Existing agent processes must not be killed by the rollout.

Diagnostics reports: changed files, migration version, schema/API decisions, actual test evidence, continuity/reboot tests performed versus still pending, known limitations, review/PR location, and any concrete deployment blocker. Deliver screenshots of the working main and right views with sanitized data. Do not report the HTML design as implemented functionality.
