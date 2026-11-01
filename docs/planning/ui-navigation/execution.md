# UI navigation and white pipeline — execution record

Resumable record of the build. No credentials are kept here.

## Where

| | |
|---|---|
| Workflow | `wf-dc741957b946` (from dispatch.json), reported with `jarvis/workflow_report.py` from the source checkout |
| Base | `origin/main` 91b13fb (design baseline 6a37860 plus PR #32, self-update) |
| Integration branch | `feature/ui-navigation-pipeline` (local only; no push, PR, merge or deploy) |
| Integration worktree | `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-navigation-pipeline` |
| Isolated run directory | `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run` (mode 700) |
| Integrator session | `a5b4be76-7b3d-4a0c-9220-817d38b9e5f3` (Diagnostics integration owner) |

Bundle copied from the source checkout (untracked there), digests verified:

| File | SHA-256 |
|---|---|
| implementation.md | 4f1110da84f0402848f8157d6d5d7467aac73b07a63714280afb493b369bfa15 |
| diagnostics-prompt.txt | c3d01cf3c75e02443cf760cbc895a10206b31584f33c7716ae8538e8792f4716 |
| dispatch.json | e74d8cc76842d02e48cfc4b9f753a5d8c8d7c4bde2604431e1bbb7a7423d2df8 |
| docs/design/ui-map/design.html | 14cef6629157bca83b1d81fd2e53f913d08508f1dcfeb3a13e21c3163f4d63af |

No duplicate implementation existed: no branch, worktree or module for UI
navigation, and no command or UI-control infrastructure on the base.

## Phases

| Phase | State | Evidence |
|---|---|---|
| `preflight` | finished | worktree, isolated ports and data, digests, baseline checks below, `coverage.md`, vertical slice |
| `shared-contract` | finished | commit 064f1b3: schema, registries, pipeline graph, request/ack contract, journey steps |
| `navigation` | next | see File ownership |
| `pipeline-animation` | next | |
| `brain-transport` | next | |
| `component-coverage` | after the core three land their shared APIs | |
| `integration-review`, `local-validation-ready`, `human-validation` | later | |

## The contract (what workers build against)

- **Schema**: `jarvis/ui_contract/protocol.json` (version 1: frames, regions,
  actors, operations, statuses, lifecycle, arrivals, styles, visibility,
  availability reasons, timing, limits, entity key patterns, side effects,
  the pipeline's fixed junctions, edges and rails) plus one declaration file
  per feature family in `jarvis/ui_contract/families/` (views, commands with
  argument schemas, actors and effects, target kinds with parts and
  presentations, reveal commands). Merged and checked as a whole by
  `jarvis.ui_contract.load` (refuses duplicates, dangling references,
  undeclared parts and effects, disconnected junctions, a region without a
  rail). Packaged with the wheel (`pyproject.toml` package-data).
- **Generated page copy**: `frontend/src/uiContract.ts`, by
  `.venv/bin/python scripts/generate_ui_contract.py` (`--check` in
  `tests/test_ui_contract.py`). After any family change, regenerate; on a
  merge conflict in it, take either side and regenerate.
- **Validation, both sides, same cases**: `jarvis/ui_contract/__init__.py`
  (`args_problem`, `target_problem`, `operation_problem`) and
  `frontend/src/uiValidate.ts`; shared cases
  `tests/fixtures/ui_contract_cases.json`, run under node by
  `tests/ui_validate_cases.mts`.
- **Registry and dispatcher**: `frontend/src/uiRegistry.ts` —
  `registerCommand(id, {availability, execute})` (only declared IDs, one
  handler each, `missingHandlers()`), `dispatch(id, args, {actor})` (validate,
  actor, availability, execute, revision, after-paint), `byHand(id, args)`
  for controls; `registerTarget` / `useUiTarget(spec)` for mounted
  presentations with `whole` and `part(name)` callback refs; duplicate
  presentations refused; `navigationRevision`, `lastNavigationActor`,
  `layoutRevision`; `visibilityOf`.
- **Route state**: `frontend/src/uiNavigation.ts` — `rightTab`, `mainView`,
  `openDetails`; handlers for `right.tab.select` and `main.tab.select`.
- **Pipeline graph**: `frontend/src/uiPipeline.ts` — junction geometry,
  `mapEdges`, `resolveRoute` (walks declared edges, then the target's rail
  tap, socket branch and part leaf; no free routing; `no-route` /
  `no-adapter`), `socketBranches`, travel helpers.
- **Attention lifecycle**: `frontend/src/uiAttention.ts` (one scheduler,
  `indicate`, `clearCues`, answers at arrival or static reduced-motion
  arrival; cancelled by a person's navigation, target loss or clear) and
  `frontend/src/UiPipelineLayer.tsx` + `uiPipeline.css` (map under the side
  columns, leaf, rings and contour above them, screen-reader status).
- **Orb ownership**: `frontend/src/orbSource.ts` (tokens; transfers always
  take the orb; attention waits `sourceQueueMs` then `busy`; release only
  your own).
- **Request / ack**: `ui_hello` → `ui_welcome {epoch}`; `ui_request
  {protocol, requestId, clientId, epoch, turn, expectedNavigationRevision,
  expiresAt, operation}`; `ui_event {event}`; `ui_result {status, effects,
  cueId, arrival, resolvedTarget, navigationRevision, layoutRevision, steps}`.
  Page side `frontend/src/uiClient.ts`; server side `jarvis/ui_control.py`
  (`UiClients`: page binding to the turn, epochs, one page per request,
  bounded wait, disconnect = cancelled with delivery unknown).
- **Journey**: operation `journey {steps: navigate|indicate[]}`, at most
  `journeyMaxSteps`; each indicate starts at the orb; a navigation revision
  that moved between steps (a person) cancels the rest.
- **Brain tools**: `ui_map`, `ui_navigate`, `ui_indicate`, `ui_clear` from
  `ui_contract.tool_specs()` (jarvis_mcp.py), handlers in server.py,
  `ui_navigate` and `ui_indicate` are ACTING_TOOLS (his turn only),
  `ui_map` taints ("what is on your screen") and returns the map in an
  untrusted block; results are walled with `_safe_label`.

## FILE OWNERSHIP

One owner per file. Anyone else needing a change asks the owner (or the
integrator) rather than editing. Workers commit on their own branch from
`feature/ui-navigation-pipeline`; the integrator merges.

### Integrator (shared wiring)

`jarvis/ui_contract/__init__.py`, `jarvis/ui_contract/protocol.json`
(including its `families` list: new family files are added by the
integrator on request), `scripts/generate_ui_contract.py`,
`frontend/src/uiContract.ts` (generated; regenerate, never hand-edit),
`frontend/src/uiRegistry.ts`, `frontend/src/uiValidate.ts`,
`frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/jarvis.ts`,
`frontend/src/store.ts`, `frontend/vite.config.ts`, `pyproject.toml`,
`tests/test_ui_contract.py`, `tests/fixtures/ui_contract_cases.json`,
`tests/ui_validate_cases.mts`, `.github/`, `docs/planning/ui-navigation/*`,
`CLAUDE.md`, `docs/whats-new.md`.

### navigation (core)

`frontend/src/uiNavigation.ts` (and any new `uiNavigation*.ts`),
`frontend/src/CentreArea.tsx`, `frontend/src/RightPanel.tsx`,
`frontend/src/Tabs.tsx`, `jarvis/ui_contract/families/shell.json`,
`jarvis/ui_contract/families/network.json`, new
`jarvis/ui_contract/families/navigation.json` (the open/close/select
commands of every detail family: workflow, subagent, thread, voice
diagnostics, subsystem, network, queue item, folders, settings, menu,
reminder), and the navigation stores inside the data modules:
`frontend/src/workflows.ts`, `conversation.ts`, `subsystems.ts`,
`voiceHealth.ts` (openDiagnostics only), `queue.ts` (openQueue, openItem,
followLink, closeItem), `folders.ts` (selectedFolder, showFolders,
openFolder), `network.ts` (openNetwork and its commands).
Exit: one route state, no effect-based selection races, every detail opened
and closed through registered commands, human/brain parity tests.

### pipeline-animation (core)

`frontend/src/uiPipeline.ts`, `frontend/src/uiAttention.ts`,
`frontend/src/UiPipelineLayer.tsx`, `frontend/src/uiPipeline.css`,
`frontend/src/orbSource.ts`, `frontend/src/orb.ts`,
`frontend/src/OrbControl.tsx` (mainOrb only; the left target registration
is coverage-left-settings'), `frontend/src/RayLane.tsx`,
`frontend/src/rayGeometry.ts`, `frontend/src/rayColours.ts`,
`frontend/src/transferEndpoints.ts`, `frontend/src/transfers.ts`,
`frontend/src/agents.ts`, `frontend/src/style.css` (layout corridors and
gutters; coverage workers put component styles in their own CSS module).
Exit: persistent map, corridors reserved, overlay and footer rails,
modal layer, shared ray lifecycle extracted, arbitration and timing tests.

### brain-transport (core)

`frontend/src/uiClient.ts`, `jarvis/ui_control.py`, `jarvis/server.py`
(only the section "The page's navigation and white attention, for the
brain" and the voice handler's ui frame branch and `note_turn` line),
`jarvis/jarvis_mcp.py`, `jarvis/claude_provider.py` (ui lines),
`jarvis/jarvis_home/CLAUDE.md` ("Showing him where something is"),
`tests/test_ui_control.py`, `tests/test_brain.py` (allowlist pin), new
`tests/test_ui_tools.py` (through `/internal/tool` and `/ws/voice`).
Exit: discovery bounded, page binding and handshake, expiry, dedupe,
reconnect without replay, journey exposed, every failure case truthful.

### coverage-right

`frontend/src/FoldersPanel.tsx`, `AgentsControl.tsx`, `QueuePanel.tsx`,
`QueueIcons.tsx`, `WorkflowsView.tsx`, `NetworkPanel.tsx`,
`NetworkTile.tsx`, `SubsystemList.tsx`, `VoiceSummary.tsx`,
`jarvis/ui_contract/families/agents.json`, `queue.json`, `diagnostics.json`,
new `frontend/src/ui-right.css` if needed, page tests for the right panel.

### coverage-main

`frontend/src/ConversationView.tsx`, `CaptureBox.tsx`, `CaptureChip.tsx`,
`SourcesChip.tsx`, `CaptionBox.tsx`, `captions.ts` (durable live-turn
identity), `FoldersView.tsx`, `FolderDetail.tsx`, `FolderMarks.tsx`,
`WorkflowDetail.tsx` (all nine subtabs, transcript paging, event filter),
`WorkflowVisuals.tsx`, `AgentThread.tsx`, `VoiceDiagnosticsView.tsx`,
`VoiceVisuals.tsx`, `SubsystemView.tsx`, `NodeView.tsx`,
`TeamServerView.tsx`, `QueueItemView.tsx`, `QueueFiles.tsx`,
`MessageComposer.tsx`, `MessageReview.tsx`,
`jarvis/ui_contract/families/workflows.json`, `folders.json`,
`conversation.json`, new `frontend/src/ui-main.css` if needed, page tests
for the centre views.

### coverage-left-settings

`frontend/src/OrbControl.tsx` (target registration only), `StatusControl.tsx`,
`MicControl.tsx`, `MicMeter.tsx`, `MuteButton.tsx`, `MicOffButton.tsx`,
`TeamServerTile.tsx`, `BrainUsage.tsx`, `WeeklyUsage.tsx`,
`MenuControl.tsx`, `Settings.tsx`, `BrainsSection.tsx`, `TeamSection.tsx`,
`TeamSignInReminder.tsx`, `Footer.tsx`,
`jarvis/ui_contract/families/left.json`, `settings.json`, new
`frontend/src/ui-left.css` if needed, page tests for the left column,
menu and Settings.

## Isolated preview instance

| | |
|---|---|
| Backend | `ui-nav-run/backend.sh` → `https://localhost:8350` (fresh self-signed `cert.pem`/`key.pem` in the worktree root, git-ignored; the live key was not copied) |
| Frontend | `ui-nav-run/frontend.sh` → Vite `http://localhost:5183`, proxy `JARVIS_API_TARGET=https://localhost:8350` |
| Data / env | `JARVIS_DATA_DIR=ui-nav-run/data`, `JARVIS_ENV_FILE=ui-nav-run/.env` (empty, mode 600) |
| Provider homes | `HOME`, `CLAUDE_CONFIG_DIR`, `JARVIS_CLAUDE_CONFIG_DIRS`, `CODEX_HOME`, `JARVIS_CODEX_SESSIONS_HOME` all under `ui-nav-run/home` |
| Switches | `JARVIS_BRAIN_AUTOSTART=0`, `JARVIS_FOLDER_DISCOVERY=0`, `JARVIS_PORT=8350`, `JARVIS_ALLOWED_ORIGINS=http://localhost:5183,http://127.0.0.1:5183`; no team-server sign-in |
| Logs | `ui-nav-run/logs/backend.log`, `ui-nav-run/logs/frontend.log` |
| PIDs | `ui-nav-run/backend.pid`, `ui-nav-run/frontend.pid` |
| Start | `nohup ui-nav-run/backend.sh > ui-nav-run/logs/backend.log 2>&1 & echo $! > ui-nav-run/backend.pid` (same for frontend) |
| Stop | `kill $(cat ui-nav-run/backend.pid) $(cat ui-nav-run/frontend.pid)`, then any listener left: `kill $(lsof -tiTCP:5183 -sTCP:LISTEN) $(lsof -tiTCP:8350 -sTCP:LISTEN)` (npm exec leaves Vite as a child) |

State now: Vite on 5183 is running (PID file `frontend.pid`, child process
listening on 5183); the backend on 8350 is stopped (the slice driver runs
its own copy of the real server on 8350 while it runs).

`JARVIS_PORT=8350` matters: without it the isolated data directory's
`mcp.json` points the brain's tool child at 8340, the live JARVIS.

## Test evidence

Baseline on 91b13fb (before any change):

- `npx tsc --noEmit`: pass. `npx vite build` (to a scratch dir): pass.
- Targeted pytest (test_brain_page_parity, test_internal_tool,
  test_jarvis_mcp, test_mcp_config, test_untrusted_turn, test_tool_handlers,
  test_voice_ws, test_header_lines, test_dead_code_removed,
  test_anchored_patterns, test_brain): 503 passed, 2 skipped, 1 failed:
  `tests/test_brain.py::test_a_warmup_that_errors_never_reports_ready`
  (timing assertion under load; passes alone: test_brain.py 52 passed).
  Pre-existing, not introduced.

After the contract and slice (commit 064f1b3):

- `npx tsc --noEmit`: pass. `npx vite build`: pass.
- `pytest tests/test_ui_contract.py tests/test_ui_control.py
  tests/test_untrusted_turn.py tests/test_answer_dialog.py
  tests/test_jarvis_mcp.py tests/test_internal_tool.py
  tests/test_show_on_screen.py tests/test_header_lines.py
  tests/test_dead_code_removed.py tests/test_anchored_patterns.py
  tests/test_tool_argument_echo.py tests/test_voice_ws.py
  tests/test_brain_page_parity.py tests/test_mcp_config.py
  tests/test_connections.py tests/test_create_project.py
  tests/test_github_lookup.py tests/test_agents_list.py`: 777 passed,
  2 skipped. `tests/test_brain.py -k "launch or command or flags"`: 3 passed.
- Vertical slice through the real app (`ui-nav-run/tools/slice_driver.py`,
  evidence `ui-nav-run/evidence/slice/`): the real server (stand-in brain
  that only reports the user's turn; no provider), the real page through
  Vite 5183, headless Chromium, the Network snapshot answered by the browser
  as a fixture. `ui_navigate right.tab.select network` → completed;
  `ui_indicate main.network node <Mini> cpu reveal navigate` → opened the
  detail, emitted, travelled (3 pulses), arrived; the tool returned
  `status: completed / arrival: animated` 1.65 s after the call, with
  lifecycle events resolving, revealing, emitting, travelling, arrived;
  contour and rings at arrival, hold, fade, reset by 5.6 s with the faint
  map (10 edges) still drawn. Hidden target with `reveal: never` →
  `not_found / not-mounted`; `ui_navigate page.eval` → `invalid`. While
  first-time setup's Settings covered the page, the same request was
  answered `not_visible / occluded` and no contour was drawn.

Gates left for CI (not run locally by rule): the full `pytest` selection,
`pytest -m page`, and the wheel build.

## Open items and known issues

1. Preview fixtures without sign-in: the Network tab, node detail and the
   Team Server tile need a team snapshot, and the isolated instance may not
   sign in. The slice answers `GET /api/team/network` with a fixture inside
   the browser. A backend fixture mode would be new behaviour; decide before
   `local-validation-ready` how the running preview gets realistic records.
2. The main rail sits in the 12 px gutter; the NodeView socket and leaf run
   past the detail's × button. Corridor reservation (pipeline-animation)
   must move the branch clear of buttons.
3. Overlay and footer rails are declared but not laid out: targets there
   return `no-route` until pipeline-animation measures them.
4. `ui_map` taints the turn; with `JARVIS_REFUSE_UNTRUSTED_ACTIONS` on, a
   map followed by navigate in one turn is refused. Default is off.
5. The journey operation is in the contract and the page, not yet a tool
   (brain-transport).
6. Live conversation turns (`t<id>`) have no durable identity
   (coverage-main, via captions.ts).
7. CentreArea still selects detail tabs from effects on each open store;
   the dispatcher sets `mainView` too (network.open). Navigation removes the
   effects.
8. The live brain-tool demonstration needs an authorised isolated provider
   path (the isolated home has no Claude login by design).

## Next action

Start the three core workers on their branches with the ownership above;
coverage workers start once navigation's `families/navigation.json` and
pipeline's corridor rules land.
