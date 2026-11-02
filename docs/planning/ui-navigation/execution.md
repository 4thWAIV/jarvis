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
| `navigation` | finished | `ui-nav-navigation` 6a55a37, merged c8149cd |
| `pipeline-animation` | finished | `ui-nav-pipeline-animation` c198962, merged 9cdfe1e |
| `brain-transport` | finished | `ui-nav-brain-transport` 043b35a, merged afdb36c; its pending items 1–3 applied in the integration commit |
| `component-coverage` | finished | `ui-nav-coverage-right` 45f7fd9, `ui-nav-coverage-main` b51ba75, `ui-nav-coverage-left-settings` e0eed32; merged (see "Coverage integration") |
| `integration-review` | finished: review findings fixed (80e9a5e), matrix gaps closed where the plan covers them (18a08e9, cfae481, af9b499 and the workflow-detail wait; see "Integration review"); open: blockers `leaf-sibling-routing`, `spanish-register` | targeted 722 passed; page 189 + layout 11 passed |
| `localization` | finished, with recorded gaps: origin/main merged (2b0cfb3), foundation (1eb6213), regions merged (`ui-nav-loc-right` 35ae800, `ui-nav-loc-main` d5ac820, `ui-nav-loc-left-settings` e6fb9ae; merges 39d1589, 4e7aea3, 76ca15a), shared changes 7d8401e, c6247c2, 682b1bb; acceptance run on the isolated candidate passed (see "Localization integration") | `ui-nav-run/evidence/loc-integration/` |
| `local-validation-ready` | finished: candidate running on 8350/5183, evidence recorded | `validation.md`, `ui-nav-run/evidence/local-validation/` |
| `human-validation` | waiting for Tim (decision `human-validation`) | |

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
  one detail slot per kind, owned by its data module; handlers for
  `right.tab.select` and `main.tab.select`. Open/close/select commands of
  every detail family are in `families/navigation.json` (navigation's).
- **Reveal**: `frontend/src/uiNavigationReveal.ts` — `reveal(ref, {actor,
  requestId, signal})` and `registerRevealer(view, kind, fn)`; the one
  reveal path, used by `indicate` and journeys.
- **Dispatch actor**: `dispatch(id, args, {actor})` requires the actor; a
  control uses `byHand`.
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

The local validation candidate (from 2026-11-01, local-validation-ready) is in `validation.md`, "The running candidate": `ui-nav-run/start.sh` starts `ui-nav-run/tools/candidate.py serve` (backend, `https://127.0.0.1:8350`, fresh data `ui-nav-run/candidate/data`, `.env` `ui-nav-run/candidate/.env`, homes `ui-nav-run/candidate/home`) and Vite on 5183 proxying to 8350, each detached in its own session; PIDs `ui-nav-run/candidate/backend.pid`, `vite.pid`; logs `ui-nav-run/logs/candidate-backend.log`, `candidate-vite.log`; stop `ui-nav-run/stop.sh`; restart `ui-nav-run/stop.sh && ui-nav-run/start.sh`. Certificates: fresh self-signed `cert.pem`/`key.pem` in the worktree root, git-ignored; the live key was not copied. No team-server sign-in; no brain spawned.

The earlier development instance (`ui-nav-run/backend.sh`, `frontend.sh`, data `ui-nav-run/data`) is stopped and kept only for its earlier evidence.

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

## Core integration (feature/ui-navigation-pipeline)

Merged `ui-nav-navigation`, `ui-nav-pipeline-animation` and
`ui-nav-brain-transport` with no conflicts (they touched disjoint files),
then applied the shared-file changes the workers asked for:

- The 16 open/close commands moved from `shell.json` to a new
  `families/navigation.json` (owner navigation), listed in `protocol.json`;
  IDs unchanged. `uiContract.ts` regenerated.
- `timing.ringMs` (700) added; `uiSignal.RING_MS` reads it.
- `limits.resultWaitMs` (no longer read) replaced by `limits.answerGraceMs`
  (1000), which `ui_control.ANSWER_GRACE_SEC` now reads.
- `uiAttention.indicate` reveals through `uiNavigationReveal.reveal`; its
  own `revealOf` / `waitForTarget` are gone. The reveal's wait runs on
  `EffectLife` (the one frame loop) instead of its own
  `requestAnimationFrame` loop.
- `ui_journey` added to `tool_specs()` (steps are exactly ui_navigate's and
  ui_indicate's properties, 1 to `journeyMaxSteps`), registered in
  `TOOL_HANDLERS`, `ACTING_TOOLS`, `TAINT_EXEMPT_TOOLS`,
  `claude_provider.ALLOWED_TOOLS` and the `test_brain.py` pin; the brain's
  guide mentions it. `ui_map` takes `cursor`.
- `tests/test_header_lines.py` exempts `ui_control.map_lines` with the
  worker's reason.
- `dispatch` requires its actor (navigation's item 7); the node cases pass
  `actor: "human"` explicitly.
- Turn interruption (plan, persistent pipeline 12): Escape / Stop
  (`jarvis.ts` hush) calls `uiClient.interrupt()`, which aborts the running
  request, answers queued ones `cancelled`, and clears every cue.
- `tests/test_voice_page_layout.py` skips the full-page pipeline layers
  (`[id^="ui-pipeline"]`) as it already skips `#ray-lane`.
- `CLAUDE.md` Key Files: the UI contract, transport, registry, navigation,
  reveal, pipeline, signal, effect lifecycle and orb source.

Not applied (optional, left as they are): a `source-occluded` reason (a
covered orb still reports `occluded`), a `ui_state` page frame for
visibility changes, and passing the page into the turn explicitly.

### For the coverage workers

- Reveal declarations still to add on the `main.*` presentations in their
  families: workflow → `workflow.open {workflowId: "$key"}`, thread →
  `thread.open {sessionId: "$key"}`, subsystem → `subsystem.open
  {subsystemId: "$key"}`, voice → `voice.diagnostics.open`, queueThread →
  `queue.open {threadId: "$key"}`, folder → `folders.open {folderId:
  "$key"}`.
- Subagent: the real key is `agent:<session>/<id>`, which the `subagent`
  key pattern rejects; `subagent.open` takes `{sessionId, agentId}`, and the
  subagent target kind should use `keyOf [session, subagent]`.
- Remaining direct writes to switch to `byHand` or bindings: `.set(...)` on
  `openWorkflow` / `openThread` / `openDiagnostics` / `openSubsystem` /
  `selectedFolder`, `openSubsystemRow`, and `WorkflowDetail`'s `TabRow
  onSelect` once `workflow.tab.select` exists (`onSelect` is deprecated).
- Settings, menu and reminder commands go in `settings.json` with handlers
  in `Settings.tsx` and `MenuControl.tsx` (coverage-left-settings).
- Socket rules: centre views `rail.main` right; right panel `rail.right`
  right; left column `rail.left` left; footer `rail.footer` left; menu and
  menu button `rail.right` right; Settings / modal targets `rail.overlay`
  left. A component that draws to its own facing edge keeps
  `var(--ui-corridor)` clear there.
- The Settings modal covers the orb at every width, so pointing inside
  Settings is refused as blocked by design; no overlay target is
  registered yet.

### Integration test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
- `pytest tests/test_ui_contract.py tests/test_ui_control.py
  tests/test_ui_tools.py tests/test_ui_navigation.py tests/test_ui_pipeline.py
  tests/test_brain_page_parity.py tests/test_jarvis_mcp.py
  tests/test_header_lines.py tests/test_dead_code_removed.py
  tests/test_anchored_patterns.py tests/test_internal_tool.py
  tests/test_untrusted_turn.py tests/test_tool_handlers.py
  tests/test_mcp_config.py tests/test_voice_ws.py
  tests/test_tool_argument_echo.py tests/test_show_on_screen.py`:
  569 passed, 2 skipped. `tests/test_brain.py`: 52 passed.
  `tests/test_ui_contract.py` after the journey-spec test change: 12 passed.
- `pytest -m page` navigation, pipeline, rays, human_rays, network, layout:
  55 passed, 6 failed, all in `test_voice_page_layout.py`. After skipping
  the pipeline layers in its overlap check: 8 passed, 3 failed, all
  `[sizeN-]` (Conversation view): the centring assertion (gaps 12 vs 22),
  caused by the corridor padding on `#conversation`. Blocker raised, not
  changed (see below).
- Vertical slice on the merged app (`ui-nav-run/tools/slice_driver.py`,
  evidence `ui-nav-run/evidence/slice-integrated/`, log
  `ui-nav-run/logs/slice-integrated.log`): `ui_navigate right.tab.select
  network` completed; `ui_indicate main.network node <Mini> cpu reveal
  navigate` returned `completed / animated` 1.65 s after the call (events
  resolving, revealing, emitting, travelling, arrived); frames show map
  (12 edges) idle, 3 pulses travelling, rings and contour at arrival,
  contour through hold, reset by 5.6 s with the map still drawn; a
  three-step `ui_journey` (tab, right tile CPU, main detail CPU with reveal)
  returned completed with every step's signal arrived in 3.3 s; hidden
  target with `reveal: never` → `not_found / not-mounted`; `page.eval` →
  `invalid`; no console errors. Stand-in brain (user turn only), Network
  snapshot as a browser fixture; no provider.

Gates left for CI: the full `pytest` selection, the rest of `pytest -m
page` (conversation, workflows, queue, folders, subsystems, voice_health,
subagents, settings, team and others were not rerun after the merge), the
wheel build, and `tests/ui_validate_cases.mts` beyond what
`test_ui_contract.py` runs.

## Open items and known issues

1. Preview fixtures without sign-in: the Network tab, node detail and the
   Team Server tile need a team snapshot, and the isolated instance may not
   sign in. The slice answers `GET /api/team/network` with a fixture inside
   the browser. A backend fixture mode would be new behaviour; decide before
   `local-validation-ready` how the running preview gets realistic records.
2. Closed (c198962): corridors reserved; the node detail's branch clears
   its × button.
3. Closed (c198962): overlay and footer rails laid out; no overlay target
   is registered yet (coverage-left-settings).
4. `ui_map` taints the turn; with `JARVIS_REFUSE_UNTRUSTED_ACTIONS` on, a
   map followed by navigate in one turn is refused. Default is off.
5. Closed: `ui_journey` is a brain tool.
6. Live conversation turns (`t<id>`) have no durable identity
   (coverage-main, via captions.ts).
7. Closed (6a55a37): one route state; no effect-based selection.
8. The live brain-tool demonstration needs an authorised isolated provider
   path (the isolated home has no Claude login by design).

9. Closed (76eb517): `layout-corridor-centring`. Tim's answer (2026-11-01), approved as written: make the spacing changes the pipeline needs, the Conversation box smaller if need be, to look like the UI sample's "symmetric" option, with bevelled corners. Applied: the corridor is kept on both sides of every centre view, so the Conversation box is centred; every right-angled turn of the pipeline is a 45-degree bevel of 6 px (`uiSignal.ts` `bevelled()`, used by `corner()`, the socket edges and every resolved route); the corridor widens by the bevel to 16 px per side, still one variable (`--ui-corridor`, equal to `CORRIDOR_PX` = inner margin 6 + clearance 4 + bevel 6). Route length and points along a route are true lengths, so a pulse keeps its speed on a bevel. `test_voice_page_layout` passes as written; the pipeline page test accepts a bevel between two map lines and nothing else off the registered map. Reported `blocker_cleared --key layout-corridor-centring`. The fixed map's joins between two separate edges (a rail's top under the top pipeline) are junctions of two registered edges and are drawn square; every turn a cue takes is bevelled.
10. Open question from navigation: if the brain switches the centre away
    from Conversation while he is typing there, the box loses focus because
    the hidden view is made inert (true before this work; the plan does not
    say).

## coverage-right (branch `ui-nav-coverage-right`)

Rows and their states are in coverage.md, "Right panel". New files:
`frontend/src/uiRight.ts` (the right socket, the row ref, the Agents
list's state, `agents.*` handlers and revealer, node-testable),
`tests/ui_right_cases.mts`, `tests/test_ui_right.py`,
`tests/test_voice_page_right.py`. Declared: `agents.filter`,
`agents.page`, `agents.reveal`, `queue.history`, `queue.history.more`, and
the main.* reveals of `queueThread`, `subsystem` and `voice`.
`uiContract.ts` regenerated.

Tests (local): `npx tsc --noEmit` pass; `pytest tests/test_ui_right.py`
2 passed; `pytest -m page tests/test_voice_page_right.py` 5 passed;
`pytest -m page` agents_list_fits, queue, folders, workflows, subsystems,
voice_health, network, navigation, pipeline, rays, human_rays, subagents,
diagnostics: 119 passed; test_ui_contract, test_ui_tools, test_header_lines,
test_brain_page_parity, test_dead_code_removed, test_anchored_patterns:
pass; test_ui_navigation: 1 failure, fixed by request 1 below.

Shared changes requested (not made by this worker):

1. `tests/ui_navigation_cases.mts`: add
   `await import("../frontend/src/uiRight.ts");` after the agents.ts import,
   so "no declared command without a handler" sees the agents handlers
   (checked: the cases then pass). Until then
   `test_navigation_cases_pass_as_a_person_and_as_the_brain` fails on this
   branch listing `agents.filter`, `agents.page`, `agents.reveal`.
2. workflows.json (coverage-main): declare `workflows.showDone {shown:
   boolean}` (view right.workflows, effect list.filtered); key the
   `subagent` kind `keyOf ["session", "subagent"]`. Then WorkflowsView's
   toggle binds it (the `showDone` store is ready) and subagent rows
   register.
3. folders.json (coverage-main): declare `folders.panel.search {text:
   string, may be empty}` (view right.folders, effect list.filtered);
   FoldersPanel's `search` store is ready. The string type needs a
   0-length allowance or a separate clear.
4. uiPipeline.ts `leafEdge` (pipeline-animation): reserved controls that
   are the addressed part, or inside it, should not block its leaf
   (`agentsList` filter/previous/next are refused `no-route` today); and a
   leaf to a part beside its whole (the workflow row's Dismiss ×).
5. uiRegistry.ts (integrator, optional): `useUiTarget` throws on a key that
   fails its pattern, which would take the page down; a warning and no
   target would be safer for data-derived keys.

## Coverage integration

Merged into `feature/ui-navigation-pipeline` (from b624a8d):
`ui-nav-coverage-right` (no conflict), `ui-nav-coverage-main` (06b845e;
conflict only in the generated `uiContract.ts`, regenerated) and
`ui-nav-coverage-left-settings` (c2cd70a; same, regenerated). Then the
shared changes the workers asked for, in one integration commit:

- `uiPipeline.ts`: an object inside a hidden view (the kept Conversation
  and Folders views) is no obstacle to a socket branch (coverage-main 1);
  a part's own controls, or the part itself, are no obstacle to its leaf
  (coverage-right 4a: the Agents list's filter, previous and next).
- `workflows.showDone {shown}` declared (right.workflows, `list.filtered`),
  handler beside its store in `WorkflowsView.tsx`, bound to the Done toggle;
  the right.workflows revealer now opens Done for a finished workflow
  instead of answering `hidden` (coverage-right 2). The subagent kind was
  already `keyOf [session, subagent]` on coverage-main.
- `folders.panel.search {text?}` declared (right.folders, `list.filtered`);
  no text clears it, as `folders.search` does, so the string type is
  unchanged; handler in `FoldersPanel.tsx`, the box runs it (coverage-right 3).
- `Tabs.tsx`: the deprecated `onSelect` is gone; `binding` is required
  (coverage-main 5). No `tabRef` (optional).
- `voiceHealth.ts`: `diagnosticsSlot` names `@routeStateAlso subsystems.ts`
  (left-settings 3).
- `tests/ui_navigation_cases.mts`: loads `uiRight.ts` and `captions.ts`
  (whose handlers node can load); the handler check leaves out the commands
  a component (`.tsx`) registers, read from the sources rather than a
  hand-kept prefix list; the "nothing declared" reveal case uses
  `folderEntry` (coverage-right 1, coverage-main 3, left-settings 1).
- `tests/test_ui_navigation.py`: no TabRow selects by hand (coverage-main 2).
- `tests/fixtures/ui_contract_cases.json`: the folderEntry case uses part
  `row` (coverage-main 4).
- `tests/ui_coverage_rules.mts` read views, kinds and part names only as
  literals, so the merged centre views tripped it (19 new violations, all
  valid code). It now reads them from their types too (a union of literals
  is checked member by member; a reference built elsewhere is read from its
  type), resolves a shorthand `ref` to its variable, and counts a record's
  numeric field (`item.id`) as its identity, not a position. To be typed
  that way: `conversationRef` and `RecordPlace` (ConversationView.tsx) and
  the transcript's part names (WorkflowDetail.tsx). `PENDING` shrunk to
  what is left: 73 unclassified controls in 13 files, no other rule.

Not applied, and why:

- Leaf routing around a sibling part, and a leaf to a part beside its
  whole (coverage-right 4b, left-settings 2, coverage-main's row-of-controls
  note): changes the pipeline's geometry, not covered by the plan or the
  design. Blocker `leaf-sibling-routing` raised; waiting for Tim.
- `useUiTarget` warning instead of throwing on a bad key (optional;
  the contract says such keys fail clearly).
- `TaskBar` ref and a target on the task card's progress (coverage-main 6),
  composite-key reveals and `workflow.open {tab?}` (coverage-main 7): new
  contract shape; left as coverage gaps.
- `missingHandlers()` in the map answer, `visibilityOf` and
  `pointer-events: none` (left-settings optional).

### Coverage integration test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
- `pytest tests/test_ui_contract.py tests/test_ui_control.py
  tests/test_ui_tools.py tests/test_ui_navigation.py tests/test_ui_pipeline.py
  tests/test_ui_right.py tests/test_ui_coverage_rules.py
  tests/test_brain_page_parity.py tests/test_jarvis_mcp.py
  tests/test_header_lines.py tests/test_dead_code_removed.py
  tests/test_anchored_patterns.py tests/test_internal_tool.py
  tests/test_untrusted_turn.py tests/test_tool_handlers.py
  tests/test_mcp_config.py tests/test_voice_ws.py
  tests/test_tool_argument_echo.py tests/test_show_on_screen.py`: 573 passed,
  2 skipped, 1 failed (the handler check, `conversation.record.expand`;
  fixed by loading `captions.ts`); then `tests/test_ui_navigation.py`: 4 passed.
- `pytest -m page` on navigation, pipeline, rays, human_rays, right,
  centre_nav, left_settings, network, queue, folders, workflows, subagents,
  subsystems, voice_health, diagnostics, conversation, captions, settings,
  team_reminder, team, mute, agents_list_fits and layout: 179 passed,
  3 failed, all `test_voice_page_layout.py::...footer...[sizeN-]`, the known
  corridor-centring blocker (12 vs 22). The rays test coverage-main saw fail
  on 2a9f718 passed here.

Gates left for CI: the full `pytest` selection, the rest of `pytest -m
page` (refresh, screen, speech_input), the wheel build.

## Next action

Waiting for Tim's answer to `human-validation` on the local candidate (http://localhost:5183/, see `validation.md`). Also open for Tim: blockers `leaf-sibling-routing` and `spanish-register`. On feedback: repair this candidate, rerun the affected checks, restart with `ui-nav-run/stop.sh && ui-nav-run/start.sh`.

## Scope update: Spanish interface (2026-11-01)

Tim's instruction (JARVIS message 733, capture 4): when the language is switched to Spanish, the words in the user interface change to Spanish too. Plan: `docs/planning/ui-navigation/localization.md` (copied from the source checkout). Folded into this workflow as phase `localization`, after the coverage merge and before the independent review: first merge origin/main (the merged Spanish PRs 33-36: `jarvis/language_setting.py`, `LanguageControl.tsx`, `spoken_words.py`) into this branch, then the shared catalogs, locale store and language-state sync, then per-region localization by the same ownership split as coverage, then integration. The review and the local candidate include the localization acceptance list and Spanish screenshots. Estimate added: about 15 to 25 agent-hours on top of the plan's 82 to 132.

## Localization

### origin/main merged (2b0cfb3, 746a0cd)

`git fetch origin` then `git merge --no-ff origin/main` (e8b0496: the Spanish PRs 33-36, self-update 32/38, the workflow reporter's address 39, the shared orb renderer 41). Conflicts, each resolved keeping both sides:

- `frontend/src/Settings.tsx`: imports both `MenuControl` and `LanguageControl`; `LanguageControl` stays at the end of the Voice section, inside this branch's fragment.
- `jarvis/server.py`: imports `server_address`, `spoken_words` and `ui_control`.
- `CLAUDE.md` key files and `tests/test_header_lines.py` allowances: both sides' entries.

Persona hashes, additive: the merged `jarvis_home/CLAUDE.md` (`de87215…`) and this branch's three earlier ones (`ac5d51b…` 064f1b3, `2812d44…` 043b35a, `165255a…` 2a9f718), which the history test asks for, are listed in `data_paths.KNOWN_TEMPLATE_HASHES`.

After the merge: contract `--check` exit 0; `npx tsc --noEmit` pass; `npx vite build` (scratch outDir) pass; targeted pytest (the 19 coverage-integration files plus `test_language_setting`, `test_spanish_speaking`, `test_speech_models`, `test_spoken_words`, `test_data_paths`, `test_system_prompt_header`, `test_brain`): 797 passed, 2 skipped, 1 failed, the coverage ratchet on `LanguageControl.tsx`'s two unclassified controls (fixed below).

### The foundation (plan items 2, 3, 5, 6, 7 in part, 9 in part, 10)

How a component gets a string:

```tsx
import { useT } from "./locale";

export function Example({ n, name }: { n: number; name: string }) {
  const t = useT();                                  // re-renders on a language change; nothing remounts
  return <p title={t("right.example.title")}>
    {t("right.example.count", { count: n, name })}  {/* typed: the key must exist, the values it names must be given */}
  </p>;
}
```

with the words in the region's part of the catalog:

```ts
// messages/right.en.ts
export const rightEn = {
  "right.example.title": "Example",
  "right.example.count": { one: "{name} has {count} item", other: "{name} has {count} items" },
} as const;
// messages/right.es.ts (typed by rightEn: a missing key is a compile error)
export const rightEs: CatalogOf<typeof rightEn> = {
  "right.example.title": "Ejemplo",
  "right.example.count": { one: "{name} tiene {count} elemento", other: "{name} tiene {count} elementos" },
};
```

Outside a component, `t(...)` gives the words of the language in force at that moment. Numbers, percentages and dates: `formatNumber`, `formatPercent`, `formatDateTime` (en-US, es-419); a number given to `t` is formatted that way. Ages: `timeFormat.ts`, now localized.

Files:

- `frontend/src/locale.ts`: the one locale store (`language`, set only by `reported` and `guardedRead`), `t`, `translate`, `useT`, `useLanguage`, `startLocale`, the format helpers, `missingTranslations()` (the missing-translation diagnostic: an unknown key or a missing value, listed once each and warned in the console while developing), and `document.documentElement.lang` kept to the language.
- `frontend/src/messages/en.ts`, `es.ts`: the catalogs, each composed of four parts: `shell.*` (the integrator's: tabs, menu, language control, units, ages) and `right.*`, `main.*`, `left.*`, one per localization worker, so the three never edit the same file.
- `jarvis/language_setting.py`: `change` publishes `state_frame()` (`{"type": "language", "language": ...}`) through the publisher `mount` is given, after the language is saved and in force; a refused or failed change publishes nothing. `jarvis/server.py` mounts it with `_send_to_session_clients` and puts `language` on `/ws/sessions`' opening snapshot.
- `frontend/src/sessionsSocket.ts` takes the frame and the snapshot's language; `startLocale` (called from `jarvis.ts`) reads `/api/settings/language` at start, after every reconnect, retried until it answers; `LanguageControl.tsx`'s own read and its change's answer go through the same guard. A read answered after a pushed frame is dropped.
- `frontend/src/Tabs.tsx`: every tab carries its registered id as `data-ui-tab`; `transferEndpoints.ts` finds the Agents and Queue tabs by it, not by `aria-label`. No source finds an element by its words (`tests/test_ui_locale.py`).
- `frontend/src/effectLife.ts`: a language change is a geometry change, so targets and routes are measured again after the words reflow.
- Worked example, localized: `RightPanel.tsx` and `CentreArea.tsx` tabs (`tabs.right.<id>`, `tabs.main.<id>`; an item's own name is kept), `MenuControl.tsx`, `LanguageControl.tsx` (each language keeps its own name; the server's refusal is shown as it came, marked `data-original-detail`), `timeFormat.ts`.
- Enforcement: `tests/ui_coverage_rules.mts` rule `label-unlocalized` (JSX text, strings in JSX expressions, `aria-label`, `title`, `placeholder`, `alt`, `label`, and `title:` properties in `.tsx`; reviewed names kept as they are: `KEPT_AS_IS`). Its per-file counts are in `PENDING` (a ratchet: 585 at the start, in 47 files); each worker takes its files to zero.
- Tests: `tests/ui_locale_cases.mts` with `tests/test_ui_locale.py` (key, kind and placeholder parity part by part, nothing left in English but `units.megabytes`, plurals, formatting, stale-read guard, diagnostic, no browser storage, no lookup by words); `tests/test_language_setting.py` (published once on a change, by the route and the brain's path; nothing on a refusal or a failed load; the snapshot carries it); `tests/test_voice_page_language.py` (page: Settings switch both ways without reload, Settings still open and the same tab selected; a refused change stays English; a frame from elsewhere; the snapshot's language beats a late read); the accepted and rejected coverage fixtures.

Not in the foundation, and whose (below): every other component's words, the backend's English that reaches the page (item 7), the status words `jarvis.ts` writes (`listening...`, `thinking...`), the brain discovery labels in `useUiTarget` (they may stay English; command and target identities never change), and the acceptance run (screenshots in both languages, widths, text scaling).

### LOCALIZATION OWNERSHIP

Same regions as coverage. Each worker branches from `feature/ui-navigation-pipeline` at the foundation commit, owns the files listed, adds its words only to its own catalog part (`messages/<region>.en.ts` and `.es.ts`, keys starting `<region>.`), and takes its files' `label-unlocalized` counts in `tests/test_ui_coverage_rules.py` `PENDING` to zero (the line for each of its files only). A word shared by two regions goes to the integrator for `shell.*`. Anything not listed stays the integrator's; ask rather than edit.

| Worker | Components (`frontend/src/`) | Modules with shown words | Backend English reaching its views (plan item 7) | Page tests |
|---|---|---|---|---|
| `loc-right` | `FoldersPanel.tsx`, `AgentsControl.tsx`, `AgentOrb.tsx`, `QueuePanel.tsx`, `QueueIcons.tsx`, `WorkflowsView.tsx`, `NetworkPanel.tsx`, `NetworkTile.tsx`, `SubsystemList.tsx`, `VoiceSummary.tsx` | `network.ts` (readiness words, which the left column's Team Server tile shows too), `subsystems.ts`, `queueStore.ts`, `workflows.ts`, `agentName.ts` | `subsystem_diagnostics.py` row words, `workflow_store` status words as shown, the needs board's | `test_voice_page_right.py`, `_network`, `_queue` (panel), `_workflows` (list), `_subsystems`, `_subagents`, `test_agents_list_fits.py`, in Spanish too |
| `loc-main` | `ConversationView.tsx`, `CaptureBox.tsx`, `CaptureChip.tsx`, `SourcesChip.tsx`, `CaptionBox.tsx`, `FoldersView.tsx`, `FolderDetail.tsx`, `FolderMarks.tsx`, `WorkflowDetail.tsx`, `WorkflowVisuals.tsx`, `AgentThread.tsx`, `VoiceDiagnosticsView.tsx`, `VoiceVisuals.tsx`, `SubsystemView.tsx`, `NodeView.tsx`, `TeamServerView.tsx`, `QueueItemView.tsx`, `QueueFiles.tsx`, `MessageComposer.tsx`, `MessageReview.tsx`, `UiPipelineLayer.tsx` (the attention status line) | `captions.ts`, `capture.ts`, `conversation.ts`, `folders.ts`, `queue.ts`, `voiceAnalysis.ts`, `voiceHealth.ts` (notices and their headings), `transfers.ts`, `uiAttention.ts` (the pipeline-attention descriptions) | `messaging_api.Refused.shown`, `folder_contract` refusals, `voice_diagnostics` incident words as shown | `test_voice_page_conversation.py`, `_captions`, `_centre_nav`, `_folders`, `_workflows` (detail), `_diagnostics`, `_voice_health`, `_queue` (item), `_human_rays`, `_pipeline`, in Spanish too |
| `loc-left-settings` | `OrbControl.tsx`, `StatusControl.tsx`, `MicControl.tsx`, `MicMeter.tsx`, `MuteButton.tsx`, `MicOffButton.tsx`, `TeamServerTile.tsx`, `BrainUsage.tsx`, `WeeklyUsage.tsx`, `Settings.tsx`, `BrainsSection.tsx`, `TeamSection.tsx`, `TeamSignInReminder.tsx`, `Footer.tsx`, `SpokenWords.tsx`, `StatusDot.tsx` | `voiceInput.ts`, `mic.ts`, `teamSignIn.ts`, `brains.ts`, `providers.ts`, `voiceStatus.ts`, and the two status labels in `jarvis.ts` (`updateStatus` only: store the state, translate where shown) | `language_setting` and `speech_models` refusals, `team_api` and `brains_config` errors, the update status words (`update.py`) | `test_voice_page_left_settings.py`, `_settings`, `_mute`, `_team`, `_team_reminder`, `_layout`, `_language`, in Spanish too |

Every worker: words whole, values by name, counts through plural entries, numbers and dates through the format helpers; names, paths, people's and agents' words and product names left as they are; no element found by its words; a backend message the page owns becomes a code plus values translated by the catalog, with any raw external detail kept and marked as original detail. Report on the monitor under phase `localization` (task item `localization-<worker>`).

Gates the integration adds: every `label-unlocalized` count zero; the acceptance list in `localization.md` run on the local candidate, with Spanish and English screenshots and the switch steps in the validation report.

### Localization test evidence (foundation)

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
- Contract `--check`: exit 0.
- Targeted pytest at 1eb6213 (the 19 coverage-integration files, `test_ui_locale`, `test_language_setting`, `test_spanish_speaking`, `test_speech_models`, `test_spoken_words`, `test_data_paths`, `test_system_prompt_header`, `test_brain`, `test_runs_are_not_conversations`, `test_web_security`, `test_access_keys`): 885 passed, 2 skipped.
- `pytest -m page` at 1eb6213 on language, right, centre_nav, navigation, rays, human_rays, queue, workflows, layout, left_settings, settings, pipeline, network, diagnostics, subsystems: 137 passed.
- Left for CI: the full `pytest` run, the remaining page tests, the wheel build.
- `pytest -m page tests/test_voice_page_language.py tests/test_voice_page_settings.py`: 16 passed.
- Corridor change, `pytest -m page` on every page file but refresh, screen and speech_input: 186 passed.

## Localization integration

### What each region did (from the workers' reports)

- `loc-right` (35ae800): the right panel's 8 components, catalog `right.*` (273 keys); `workflows.ts`, `subsystems.ts`, `network.ts`, `queueStore.ts` give their words in the language in force under the same export names; `subsystem_diagnostics.py` sends every name and line as a code and values (`name_said`, `said`) beside the English the brain reads, with a test that the Python English and the catalog English are word for word the same (CLAUDE.md, "Subsystem diagnostics"). Layout wordings: Diagnostics rows "Herramientas", "Carpetas", "Equipo"; node tile "RAM", "IA". Page test: Spanish case in `test_voice_page_right.py`. It also taught `tests/test_header_lines.py` to count `say(...)` as building text; reviewed and kept (it makes the check stricter).
- `loc-main` (d20e4f0, d5ac820): the centre views and their modules, catalog `main.*` (Latin American Spanish, `tú`); backend words reaching the centre are worded by their code on the page (message status, agent state, folder and messaging refusals, dismiss blockers, folder reader, voice incident kind, workflow event and item kinds, folder location events), and a code the page does not know is shown as it came, marked `data-original-detail`. Page test: the node-detail cue in English and Spanish (`test_voice_page_pipeline.py`).
- `loc-left-settings` (e6fb9ae): the left column, Settings, the sign-in reminder, catalog `left.*` (220 keys); `jarvis.ts` stores the state and `StatusControl` words it; voice input keeps its notices as messages (`InputWords`); refusals carry codes beside their unchanged English (`speech_models.Refused` and `language_setting`: `code`, `values`, `detail`; an install's `errorCode`; `brains_config.Refused`; `self_update`'s `refused_code`, `note_codes`). Settings' section list widens to its longest name. Page tests: two Spanish cases in `test_voice_page_left_settings.py`.

### Merge

`git merge --no-ff` of each branch in turn. Conflicts only in `tests/test_ui_coverage_rules.py` `PENDING`: each side had removed its own `label-unlocalized` lines; resolved by keeping every removal. After the merges no `label-unlocalized` line is left in `PENDING`; the rule now finds none in `frontend/src` (73 `control-unclassified` remain, the coverage ratchet).

### Shared changes applied (7d8401e, c6247c2, 682b1bb)

- `locale.ts` holds the shared wording helpers: `wordsOf` (from `workflows.ts`), `codeWords`, `Said`, `saidShown`, `SaidText` (from `conversation.ts` and `ConversationView.tsx`), `placed` (from `TeamServerTile.tsx`). Every importer points there.
- Not moved: `inputWords` / `InputWords` stay in `voiceInput.ts`, their one user; moving them would put a second "message kept for later" shape beside `Said` in the shared module. Unifying the two (an `InputWords` can be said in any language, for the English log) is a follow-up.
- Readiness: the Team Server tile, its spoken label and `TeamServerView` show `network.READINESS_WORDS`; readiness codes stay English for classes and tests. "ms" is on `KEPT_AS_IS`. The readiness word may wrap to two centred lines in the tile's 62px column (Spanish "Iniciar sesión" was cut, found by the left-settings Spanish page test).
- `SubsystemView.tsx` and the subsystem's centre tab (`CentreArea.tsx`) word names, summaries and checks through `subsystemName`, `subsystemSummary`, `checkName`, `checkDetail` (the tab was found in English by the acceptance run).
- `agents.ts` `workingCount`: `agents.working` / `agents.workingCapped` in the shell catalog.
- `MicControl.tsx` words a health state by code (`codeWords("main.voice.health", …)`); `HEALTH_WORDS` is gone.
- `LanguageControl.tsx` words a refused change, a failed load and a failed download by code through `voiceInput.speechRefusal`, which now returns a `Said` (original detail when the code is unknown).
- `messagingApi.ts`: the unused `shownFor` and `MessagingError.shown` are gone; the Queue words refusals through `queue.messagingSaid`.
- `server.py` `_brains_change`: a `brains_config.Refused` answers `detail={"message", "code", "values"}` (asserted in `test_brains_api.py`); `brains.ts` already read both shapes.
- `test_voice_page_speech_input.py`: the model-unavailable expectation is the coded wording ("Speech recognition is not available. Detail: …").
- CLAUDE.md, "Subsystem diagnostics": the codes.

### Acceptance on the isolated candidate (localization.md, "Acceptance")

The real backend on 8350 with the isolated data, `.env` and homes, both real Kroko models installed into the isolated data (`scripts/setup_speech_input.py --language all`), the real page through Vite on 5183, headless Chromium; no brain (a stand-in says it is the user's turn), no team sign-in (the network snapshot is a browser fixture), workflows seeded through the real route. Drivers: `ui-nav-run/tools/loc_acceptance.py` and `loc_restart.py` (copies beside the evidence). Results: `ui-nav-run/evidence/loc-integration/acceptance.json`, `restart.json`, 69 screenshots.

Switch steps and what held:

1. English, a draft typed in the conversation box, the Workflows tab, a workflow open on Tasks, Settings open at Voice (`01-en-settings-voice.png`).
2. Settings, Language, Español: page and a second open page went to `es-419`, the server says `es`; no reload (a window marker survived), the draft, the right tab, the centre view, the workflow tab and Settings all as they were (`02-es-settings-voice.png`, `02b-es-second-page.png`).
3. The brain's `set_language` to English: both pages back to `en-US`, same state kept (`03-en-after-tool.png`).
4. A failed change (the next model load made to fail): through Settings the interface, the server and speech input stayed English and the control said "The speech model could not be loaded. Detail: injected by the acceptance run" (`04-en-failed-switch.png`); through `set_language` the tool said the language was not changed and the page stayed English.
5. `set_language` to Spanish, then a refresh: back in Spanish.
6. Every family, opened by its registered command, in Spanish then English, every command `completed`, no page scrolling sideways: the six right tabs; conversation, folders, the workflow detail on each of its nine tabs, voice diagnostics, a subsystem, a node, the team server; the six Settings sections (`es-*.png`, `en-*.png`). No missing-translation warning in the console.
7. The three pipeline demonstrations in both languages, each `completed` with an animated arrival and the same resolved target in both: the left Team Server tile's database row, a node's CPU in the centre, a workflow task's row on the Tasks tab (`*-demo-*.png`). The task demo needs the Tasks tab open first (no reveal for tasks, a gap recorded under "Gaps left after the coverage merge").
8. Spanish at 1280×800, 1920×1080, and 1440×900 with text at 125 %: no page scrolling sideways (`es-width-*.png`).
9. Restart and reconnect (`loc_restart.py`, backend as its own process): started in Spanish, the page Spanish; restarted, still Spanish with no reload; stopped, the saved language set to English while down, started: the open page followed to English on reconnect with no reload; a refresh stayed English (`10-*.png`, `11-*.png`).

Stale responses (a read answered after a pushed frame) and the refused-change path stay covered by `test_voice_page_language.py`. Agent and queue transmissions in Spanish are covered by the region page tests (`test_voice_page_human_rays.py`, `_rays.py`, `_queue.py`), run in English; the Spanish run of the transmissions themselves was not driven on the candidate.

### Localization integration test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass. Contract `--check`: exit 0.
- Ratchet: `label-unlocalized` 0 everywhere; `test_ui_coverage_rules.py`, `test_ui_locale.py`: 8 passed.
- Targeted pytest at 7d8401e (the 19 coverage-integration files, `test_ui_locale`, `test_language_setting`, `test_spanish_speaking`, `test_speech_models`, `test_spoken_words`, `test_data_paths`, `test_system_prompt_header`, `test_brain`, `test_runs_are_not_conversations`, `test_web_security`, `test_access_keys`, `test_subsystem_diagnostics`, `test_messaging_queue_store`, `test_brains_api`, `test_self_update`): 1055 passed, 2 skipped.
- `pytest -m page` at 7d8401e on language, right, network, queue, workflows, subsystems, subagents, agents_list_fits, conversation, captions, centre_nav, folders, diagnostics, voice_health, human_rays, rays, pipeline, navigation, left_settings, settings, mute, team, team_reminder, layout, speech_input: 203 passed, 2 failed. `test_voice_page_left_settings.py`'s Spanish fit (the clipped readiness word, fixed in c6247c2; the file then 10 passed) and `test_voice_page_workflows.py::test_a_workflow_row_opens_its_detail_and_a_decision_is_answered` (reads the detail's name before its read lands, under load; passed alone).
- After 682b1bb: `pytest -m page` subsystems and centre_nav: 10 passed.
- Left for CI: the full `pytest` run, the rest of `pytest -m page` (refresh, screen), the wheel build.

### Localization gaps remaining

- Team server errors: `team_client.TeamError` and `last_error` (pairing, status poll, network exchange; about 30 sites) carry no code, so Settings, Team shows them in English, marked as original detail. Plan item 7 lists them; not done here.
- Voice causes: the voice record's cause and message lines, and the live health cause ("The page was busy for 807 ms"), are English sentences, so the Diagnostics view, its summary and the voice input row show them in English in Spanish. Wording them needs codes and values in `voice_diagnostics`, including the stored events.
- Spanish values cut by width: the tile's "Escritura" row next to "hace 1 min" ("Escritu…"), and the Diagnostics row "Entrada de voz" beside "Con problemas" ("Entrada de …").
- Register: decided 2026-11-01, `usted` everywhere (see "Routing and register (2026-11-01)").
- Brain discovery labels: Settings sections keep English labels (`translate("en", …)`), the centre and right panels' targets give labels in the language in force. Command and target identities never change; the plan allows either, but it is not one rule.
- The browser's own file picker words ("Choose Files") follow Chrome's language.
- Recorded content (stored conversation notices, the voice record) is shown as it was written.


## Integration review

Review findings, both real:

1. Stale navigation could not be detected: the brain's requests always went with `expectedNavigationRevision: null`. Now `ui_navigate`, `ui_indicate` and `ui_journey` take `expectedNavigationRevision` (request-level, not per journey step), `_ui_request` passes it (a whole number from 0, else invalid), the tool descriptions and the persona say to pass `ui_map`'s `navigationRevision` (persona hash `493370b…` listed). Tests: `test_ui_tools.py` (forwarded, page answers stale, bad value invalid, every acting tool's schema), `test_voice_page_navigation.py` (a real page: map, his click, the planned request answers stale and leaves his tab; at the current revision it runs). 80e9a5e.
2. With `JARVIS_REFUSE_UNTRUSTED_ACTIONS` on, `ui_map` (tainting) blocked every UI tool after it. `UI_TAINT_EXEMPT_ACTING` (`ui_navigate`, `ui_indicate`, `ui_journey`; `ui_clear` is not acting) survives a tainted turn unless the commands it would run declare `queue.item-activated` or `queue.read-marked` (`ui_contract.declared_effects`: a navigation's command, an indication's reveal command, each journey step's). `TAINT_EXEMPT_ACTING` is unchanged. Test: `test_untrusted_turn.py::test_pointing_after_reading_the_map_survives_but_opening_his_messages_does_not`. 80e9a5e.

Matrix gaps:

- Layout footer: `test_voice_page_layout.py` 11 passed on the current branch (the corridor change 76eb517 had already closed it).
- Health dot: page test `test_voice_page_voice_health.py::test_the_health_dot_is_named_and_reached_only_while_it_shows` (a named button while shown; pointing refused when hidden; `blocked / no-route` beside the meter: the leaf blocker). 18a08e9.
- Spanish cut words: the Team Server tile's row gap 5→4 px fits "Escritura" with "hace 1 min" / "hace 20 h" (test parametrized by write age); subsystem names wrap in the Diagnostics list (test asserts no `subsystem-name` cut in either language, with voice input falling behind; fails without the CSS). 18a08e9.
- Workflow detail flake: the test waits for the record's words, not the element (6 passed).
- Hidden targets: revealers may run registered commands through `RevealerContext.navigate` (dispatch as the caller, effects reported, its own revision move not taken for a person's). `main.workflow` revealers open the workflow named by the key and select the tab that draws a phase, task, decision, wait, blocker, transcript or events; a subagent's detail parts open by `subagent.open`. Page test `test_voice_page_centre_nav.py::test_a_workflows_hidden_item_is_revealed_by_opening_it_on_its_tab`. cfae481.
- Controls: 51 non-navigation controls classified (`data-ui-action`: domain, input, recipient, file, refresh); `PENDING` 73 → 22, all navigation needing commands. af9b499.
- Register: raised as blocker `spanish-register` (recommend usted); not changed.
- Not done: the 22 navigation commands; localization item 7 (team server error codes, voice cause codes); task card progress and right subagent row targets.

## Routing and register (2026-11-01)

Branch `ui-nav-routing-register` from `feature/ui-navigation-pipeline`, not pushed. Applies Tim's two approvals (JARVIS msg 775): `leaf-sibling-routing` (over or under the row, bevelled drop) and `spanish-register` (usted throughout).

### Leaf routing over or under the row (dfe52df)

- `uiPipeline.ts` `leafEdge`: the straight leaf is tried first, unchanged. When it would cross a control or another part, `detour()` tries over and under the part's row: along the inner margin to the middle of the gap between the row (the part and whatever shares its band in the run's span) and the next control, part or line of text beyond it (or the object's own edge), across to above or below the part's centre, and into its top or bottom edge, `LEAF_END_PX` (3) outside it as a straight leaf ends. A gap narrower than twice `CLEAR_PX` does not count; each candidate is checked against controls, other parts and every line of text in the object outside the part (`labelsIn`); the shorter of the sides that clear is used, else `no-route`. Its turns are bevelled with the rest of the route (`bevelled()`), shortened only where a leg is too short, as before.
- A part that is the whole itself (a row registered as its own `row` part) is no obstacle to its own leaves.
- A part straddling the inner margin (the finished workflow row's ×) is reached straight down or up the margin into its edge, or directly when the socket is level with it. The × is now registered as the row's `dismiss` part (`WorkflowsView.tsx`).
- Reached now: workflow Decisions tab (and every tab left of the last), Folders tabs, Team Server last write, reminder text and Sign in, workflow Dismiss ×.
- Not reached (blocker `sibling-routing-remainder`, raised): microphone mute and off and the health dot, because the mic row touches the status line above and has 7 px below it, short of the 8 px the leaf needs; phase steps left of the last, because the next step's connector line covers the dot (`occluded`) and the socket branch would cross the next step, another object. Options and recommendation are on the monitor.
- Tests: `test_voice_page_pipeline.py` `test_a_part_beside_others_in_its_row_is_reached_over_or_under_the_row` (each part above arrives; every route segment is on the registered map, a segment the map draws, or a bevel; every leaf segment straight or a 45-degree bevel of at most 6 px; no leaf point inside a control or a line of text; the leaf ends at the part, coming down or up into it). `_on` also accepts a segment identical to one the map draws (a socket branch the bevel takes whole). `LEFT_MISSED` and the reminder test updated; coverage.md updated.

### Usted everywhere (36c7cac)

- `left.*`, `right.*` and `shell.*` already used usted. `main.es.ts`: 46 strings changed (imperatives such as Seleccione, Elija, Vuelva a iniciar sesión; su and sus for tu and tus; usted for ti and tú; "Usted está diciendo"; "No hay decisiones pendientes para usted"; the agent's `needs_you` is "requiere su atención", as the right panel says; "Te escucho" is "Lo escucho", as the right panel's "Escuchándolo").
- `test_ui_locale.py` `test_spanish_addresses_the_person_as_usted_everywhere`: no Spanish catalog value contains tú, tu, tus, te, ti, contigo, estás, haz or a gerund with -te on it. It catches 18 of the 46 old strings; imperatives are not checked, since tú and usted imperatives cannot be told apart reliably from third-person verbs.
- Spanish centre checked on the built page: nothing in the conversation or centre area overflows its box.

### Test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
- `pytest tests/test_ui_locale.py tests/test_ui_pipeline.py tests/test_ui_coverage_rules.py tests/test_ui_contract.py tests/test_ui_navigation.py tests/test_ui_right.py tests/test_spanish_speaking.py tests/test_language_setting.py`: 114 passed.
- `pytest -m page` on language, settings, pipeline, left_settings, right, voice_health, conversation, queue, folders, workflows, centre_nav, team_reminder, mute, navigation, layout, rays, subagents, diagnostics and agents_list_fits: 159 passed.

