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


## Remaining gaps (2026-11-01, branch `ui-nav-remaining-gaps`)

Tim's instruction (JARVIS message 775, capture 47): every remaining issue
evaluated, recorded and addressed. Branch from `feature/ui-navigation-pipeline`
at af9b499, not pushed. Leaf sibling routing and the Spanish register
(`tú`/`usted`) belong to `ui-nav-routing-register` and are not touched here;
any new Spanish string that addresses the person uses `usted`.

### Inventory

| # | What | Where | Fix |
|---|---|---|---|
| A1 | Team server errors reach the page as English sentences: `TeamError` and `last_error` from pairing, the status poll and the network exchange (about 30 sites), and the team routes' refusals. Settings, Team, the team server view and the Diagnostics team row show them in English in Spanish. | `jarvis/team_client.py`, `jarvis/team_api.py`, `jarvis/subsystem_diagnostics.py` (team row); page `TeamSection.tsx`, `teamSignIn.ts`, `TeamServerView.tsx`, `subsystems.ts` | Every message of JARVIS's own becomes a code and values (one table, `team_client.TEAM_SAID`, its English unchanged); `TeamError` carries them. Added beside the existing fields, nothing removed: `last_error_said` on `/api/team` and on `/api/team/network`, `said` inside a refusal's `error`. Words the team server itself sent are the `detail`, shown as original detail. The page words a code from the shell catalog (`shell.team.error.<code>`), else shows the English as it came. |
| A2 | Voice causes are English sentences: the live health cause ("The page was busy for 807 ms"), each sample's and incident's cause, the summary's cause, and the model-load event's line. | `jarvis/voice_diagnostics.py` (`classify`, samples, events, health), `jarvis/speech_models.py` (model-load line), `jarvis/subsystem_diagnostics.py` (voice row); page `VoiceDiagnosticsView.tsx`, `MicControl.tsx`, `subsystems.ts` | `classify` keeps its English and gains a code and values (`voice_diagnostics.CAUSES`); samples and events store `cause_code` / `cause_values` (new columns, added to an existing database), the health frame and the summary carry `causeCode` / `causeValues`. The page words them from `shell.voice.cause.<code>`; a stored record without a code is shown as written, marked original detail. |
| A3 | An incident's own line (a `TranscriptionError` message, e.g. "Speech input is not running.") | `voice_diagnostics` events `text` | Evaluated, kept as it is: the incident's kind is already worded by its code (`incidentWords`); the line is the recogniser's detail and stays shown as original detail. Nine sites share five codes with different sentences, so a code per sentence would be a second vocabulary for one meaning. |
| B1 | FolderDetail's mode switches: Edit, Reconnect, Remove, their Cancel and Keep it (6 controls) | `FolderDetail.tsx` | New command `folder.mode {folderId, mode: view\|edit\|reconnect\|remove}` (folders.json); the mode moves from component state into a store beside it; nothing is checked or saved by it. |
| B2 | FoldersView's Add form Cancel | `FoldersView.tsx` | Runs the existing `folders.add.open {open: false}`. |
| B3 | MessageComposer's discard confirmation: Discard (asks), Keep it | `MessageComposer.tsx` | New command `queue.discard.ask {open}` on the open item's composer; the store moves to `queue.ts`. The discarding itself stays `domain`. |
| B4 | QueueItemView: Respond and Reply to this | `QueueItemView.tsx` | New command `queue.reply {messageId}`: opens the reply editor on the open item, answering that message (nothing saved until he writes). |
| B5 | QueueItemView: the links to a replied-to, reply or related message (3 controls) | `QueueItemView.tsx` | New command `queue.message.show {messageId}`: in the open item it scrolls to the message; in another item it follows the link (as `queue.follow`). |
| B6 | QueueItemView: Edit (a failed or cancelled response, back in the editor) | `QueueItemView.tsx` | New command `queue.draft.edit {messageId}`: puts that response back in the editor (nothing sent or saved). |
| B7 | QueueItemView: the Done blockers' Discard reply (asks) and Keep working | `QueueItemView.tsx` | New command `queue.done.ask {step: closed\|blockers\|discard}`; the prompt's state moves to `queue.ts`. Done itself and Discard and dismiss stay `domain`. |
| B8 | QueuePanel's New message | `QueuePanel.tsx` | New command `queue.new {}`: opens the blank composer in the centre (`queue.ts` `startNew`). |
| B9 | VoiceDiagnosticsView's range buttons and quiet-page switch, and the timeline marks' zoom | `VoiceDiagnosticsView.tsx`, `VoiceVisuals.tsx` | New commands `voice.range {range}`, `voice.zoom {at?}` (no `at` zooms out) and `voice.quiet {quiet}` (diagnostics.json); range and zoom move into a store. |
| B10 | WorkflowDetail's transcript scroll (whether it follows the newest entries) | `WorkflowDetail.tsx` | New command `workflow.transcript.follow {sessionId, agentId?, follow}`; a person's scroll runs it only when following starts or stops, never per scroll event. |
| B11 | The task card's progress bar has no target (`TaskBar` takes no ref) | `WorkflowsView.tsx`, `WorkflowDetail.tsx` | `TaskBar` takes a ref; the task card registers it as its declared `progress` part. |
| B12 | Composite-key reveals: a subsystem's check (`<subsystem>/<check>`) and a folder entry (`<folder>/<path>`) have no way to be brought into view | `SubsystemView.tsx`, `FolderDetail.tsx` | Revealers (the existing `registerRevealer` contract): `subsystem.open` from the key's first part; `folders.open` then `folder.browse` to the entry's directory. |
| B13 | `workflow.open {tab?}` | `navigation.json`, `workflows.ts`, `WorkflowDetail.tsx` | Evaluated below (fits as an optional argument, or recorded). |
| B14 | Subagent rows in the right panel are not targets | `WorkflowsView.tsx` | The subagent kind is keyed `[session, subagent]` now, so the row registers as `subagent` in `right.workflows` (row, state). |
| C | Brain discovery labels follow two rules: Settings' sections and about fifteen other targets give English labels (`translate("en", …)`, literals), the rest the language in force | `Settings.tsx`, `MenuControl.tsx`, `Footer.tsx`, `MicControl.tsx`, `OrbControl.tsx`, `StatusControl.tsx`, `TeamServerTile.tsx`, `TeamSignInReminder.tsx`, `WeeklyUsage.tsx`, `AgentsControl.tsx`, `FoldersPanel.tsx`, `QueuePanel.tsx`, `VoiceSummary.tsx`, `WorkflowsView.tsx` | One rule: every target's label in the language in force, from the catalog; command and target identities unchanged. The coverage rule `label-unlocalized` also reads a target's `label:`, so a new English label fails CI. |
| D1 | Spanish values cut by width | style.css | Already fixed (18a08e9: "Escritura", "Entrada de voz" wrap); nothing left. |
| D2 | Register `tú` / `usted` | catalogs | `ui-nav-routing-register`'s; not touched. |
| D3 | The browser's own file picker words ("Choose Files") | Chrome | Stays: Chrome draws them in its own language; no page can word them. |
| D4 | Recorded content shown as written (stored conversation notices, the voice record's message lines, older voice records without a code) | records | Stays: changing the language never rewrites a record. New voice records carry codes (A2). |
| D5 | Not applied in the core integration: a `source-occluded` reason, a `ui_state` page frame, passing the page into the turn explicitly | ui_contract, ui_control | Evaluated, kept out: each is a contract or protocol shape change the plan lists as optional; nothing fails without them. Recorded for the review. |
| D6 | `useUiTarget` throws on a key that fails its pattern | `uiRegistry.ts` | Evaluated, kept: the contract says a bad key fails clearly, and every data-derived key now comes from the record's own identity (the `index-key` rule). |
| D7 | `missingHandlers()` in the map answer, `visibilityOf` and `pointer-events: none` (left-settings, optional) | uiRegistry, ui_control | Evaluated, kept out: `tests/ui_navigation_cases.mts` already fails CI on a declared command without a handler; the other two were noted as optional with no failing case. |
| D8 | `InputWords` (voice input) beside `Said` | `voiceInput.ts`, `locale.ts` | Evaluated, kept: the integration recorded it as a follow-up; one user, no wrong words shown. |
| D9 | The health dot and other parts beside a sibling are `blocked / no-route` | uiPipeline.ts | Leaf sibling routing, `ui-nav-routing-register`'s. |


### What was done (2026-11-01)

Commits on `ui-nav-remaining-gaps` (from af9b499; not pushed):

| Commit | What |
|---|---|
| 429d983 | The inventory above. |
| e3bfc11 | B1–B9, B11–B14: 21 of the 22 controls run registered commands a person's control and the brain's request share. Declared: `folder.mode {folderId, mode}` (folders.json); `queue.new`, `queue.reply {messageId}`, `queue.draft.edit {messageId}`, `queue.message.show {messageId}`, `queue.discard.ask {open}`, `queue.done.ask {step}` (queue.json); `voice.range {range}`, `voice.zoom {at?}`, `voice.quiet {quiet}` (diagnostics.json); `workflow.open` takes an optional `tab` (navigation.json). The state each changes moved from component state to a store beside its handler (FolderDetail's form, the Queue's questions in `queue.ts` `asking`, the voice view's range and zoom). None saves, sends, discards or dismisses: those stay `domain`. Targets: the task card's progress bar (`TaskBar` takes `barRef`), the subagent row in `right.workflows`; revealers for `subsystemCheck` (opens its subsystem) and `folderEntry` (opens its folder, browses to its directory). The workflow revealers open on the tab in one step. `PENDING` in `test_ui_coverage_rules.py` is down to the one control below. |
| 4b4d432 | C: every target label in the language in force (Settings' sections and fifteen English literals now come from the catalog, `targets.*` in the shell part, reusing tab and menu words). `label-unlocalized` now reads a target's `label` in .ts and .tsx and refuses `translate("<language>", …)` in it; negative fixture `UnlocalizedTargetLabels.tsx`. |
| 02d9a38 | A1, A2: `jarvis/said.py` holds the one `Said` (code, values) and its English (moved from subsystem_diagnostics). `team_client.TEAM_SAID` (30 codes) and `Problem` (said, English, the team server's own `detail`); `TeamError` carries one (bare English still accepted, shown as it came). Added beside the English, nothing removed: `last_error_said` on `/api/team` and `/api/team/network`, `said` inside a refusal's `error` (team_api and the member calls' bad response). `voice_diagnostics.CAUSES` (8 codes), `diagnose` (state and coded cause; `classify` unchanged), `cause_code` / `cause_values` on samples and events (schema `voice_schema` 2 through `run_store.migrate`, once per database per process), `causeCode` / `causeValues` on the health frame, the summary and the records. Subsystem rows carry the nested line (`causeSaid`, `errorSaid`; `teamError` line). Page: `locale.codedSaid`, `CodedLine`, `Said.detail` (SaidText marks the detail as original detail; `said.withDetail` replaces `left.input.withDetail`), `teamSignIn.teamSaid` / `TeamRefusal` / `refusalSaid`, `voiceHealth.causeSaid`; used in Settings, Team (status, notes, member refusals), the sign-in reminder, the team server view, the Diagnostics view (overview, timeline marks, incidents), the health dot and the Diagnostics rows. Catalogs: `team.error.*`, `voice.cause.*` (English word for word the Python's; tests hold them together). |
| a2acf8e | A test that read a process-wide typed count left by an earlier test (fails after `test_voice_ws.py`); unrelated to the codes, found by the targeted run. |

Amended against the inventory:

- A2: the model-load event's line is not shown anywhere on the page (only `message` events and incidents are), so it keeps its English and gets no code; the columns for it were not added.
- B10 (the transcript's scroll): not done; blocker `transcript-scroll-command` raised on the monitor. Every completed command moves the navigation revision, so a command run on scroll would also fire when the brain's reveal scrolls the box (pointing at Load earlier) and the reveal would cancel itself. Options put to Tim: (recommended) a reviewed class for it (`scroll`: remembers where a person scrolled a box), following kept local; a command that does not move the revision (a registry change); or a follow command for the brain only with the scroll classified. `PENDING` keeps it (1).
- Not changed, the owner's: `CLAUDE.md` ("Voice diagnostics", "The team server" could name the codes and `jarvis/said.py` in Key Files) and `coverage.md`'s gap rows; for the integrator on merge.

### Remaining gaps test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass. Contract regenerated (`scripts/generate_ui_contract.py`), `test_ui_contract.py` pass.
- Targeted pytest: `test_ui_contract`, `test_ui_control`, `test_ui_tools`, `test_ui_navigation`, `test_ui_pipeline`, `test_ui_right`, `test_ui_coverage_rules`, `test_ui_locale`, `test_brain_page_parity`, `test_jarvis_mcp`, `test_header_lines`, `test_dead_code_removed`, `test_anchored_patterns`, `test_internal_tool`, `test_untrusted_turn`, `test_tool_handlers`, `test_mcp_config`, `test_voice_ws`, `test_tool_argument_echo`, `test_show_on_screen`, `test_language_setting`, `test_subsystem_diagnostics`, `test_voice_diagnostics`, `test_team_client`, `test_team_network`, `test_team_client_stream`, `test_team_reminder`, `test_speech_models`, `test_transcription_ws`, `test_messaging_client`, `test_brains_api`, `test_web_security`: 949 passed, 2 skipped, 1 failed (the typed-count order dependency, fixed in a2acf8e; `test_voice_ws.py` + `test_voice_diagnostics.py` then 55 passed).
- `pytest -m page`, new: `test_voice_page_remaining_nav.py` (each new command by hand and by request, the new targets and reveals, the map's labels in English and Spanish) 6 passed; `test_voice_page_coded_words.py` (a coded voice cause and an older one, the team server's last error with its detail, in Spanish) 2 passed.
- `pytest -m page`, related: centre_nav, queue, folders, workflows, voice_health, subsystems, right, navigation, subagents (53 passed); left_settings, language, settings, team_reminder, mute (34 passed); coded_words, voice_health, team, team_reminder, network, subsystems, right, speech_input, language, left_settings (73 passed); human_rays, rays, pipeline, diagnostics, layout, agents_list_fits, remaining_nav (86 passed).
- Not run, by rule: the full suite, the rest of `pytest -m page`, the wheel build.

## Final integration (2026-11-01)

Branch `feature/ui-navigation-pipeline`, not pushed. Applies Tim's answers to
`sibling-routing-remainder` ("the pipeline must reach every remaining
control; more space or bevelling allowed") and `transcript-scroll-command`.

### Merges

- 781f243: `ui-nav-routing-register` (dfe52df, 36c7cac, c71a65b). Conflict
  only in this file (both sides appended a section); both kept.
- bc03149: `ui-nav-remaining-gaps` (429d983..2149541). Conflict only in this
  file; both kept. `WorkflowsView.tsx` merged cleanly. The new Spanish
  strings from remaining-gaps (`shell.es.ts` targets, `team.error.*`,
  `voice.cause.*`) address the person as usted (Escriba, revise, actualice,
  inténtelo, Desconéctese, Empiece, inicie);
  `test_spanish_addresses_the_person_as_usted_everywhere` passes.

### a. The microphone's row (0ab205f)

- `--ui-leaf-lane` (8 px, `:root`): the lane a leaf runs along over or under
  a row inside the object's own box. `#mic-control` keeps it below its row
  (`padding-bottom`). Mute, off and the health dot are reached under the
  row, dropping into their bottom edge with a bevel.
- Measured against the 4 px the blocker proposed: the leaf's lane must be
  inside the object's own box (`lane()` stops at the whole's edge), and the
  mic's buttons fill its height, so the 7 px the blocker counted were below
  the box (under the Team Server tile's title) and 4 px gives a 4 px gap,
  short of twice `CLEAR_PX`. 8 px is the least that reaches; Tim's answer
  allowed more space. The column moves down 8 px; the layout tests pass.
- `LEFT_MISSED` is empty (`test_voice_page_left_settings.py`); the health dot
  is reached (`test_voice_page_voice_health.py`); mute and off are in the
  pipeline test's beside-the-row cases.

### b. Phase steps (0ab205f)

- `.phase-step::before` (the connector into a step) has
  `pointer-events: none`, so it no longer counts as covering the step
  before it (`occluded` gone).
- `uiPipeline.ts`: `lane()` is the gap finder `detour()` used, shared; new
  `overTheRow()`: a socket branch that would cross another object (the steps
  after it) runs from the rail along the row's lane inside the object that
  holds it (`holderOf`, the workflow's detail), clear of the other objects
  and the holder's controls and lines of text, to above or below the
  object's inner margin, and drops into its top or bottom edge there; the
  shorter that clears. Its turns are bevelled with the route
  (`bevelEdges`/`bevelled()`). Leaves start from the object's margin
  (`marginOf`), so a part is reached from that entry as from a side one.
  With neither way clear the straight branch stays and the route is
  refused as before. The faint map draws the same branch.
- Page test `test_a_phase_step_left_of_the_last_is_reached_over_the_stepper`
  (each step, its dot; on the map, bevels only, never across another step,
  a control or a line of text; into the top edge at the inner margin; the
  last step still straight in).

### c. The transcript's scroll (0ab205f)

- `CONTROL_CLASSES.scroll` ("Remembers where a person scrolled a box
  (whether it follows its newest entries); kept local, runs no command and
  never moves the navigation revision"), on the transcript box
  (`data-ui-action="scroll"`). `PENDING` in `test_ui_coverage_rules.py` is
  empty.

### d. Every registered target and part

Driver `ui-nav-run/tools/test_reach_sweep.py` (with `analyze.py`), run with
`pytest -p tests.conftest -m page`: the built page, fixtures from the page
tests (workflows with tasks, a wait, a blocker, an attached agent and
subagents; folders with candidates; the team network; subsystems; the voice
record; the conversation record, a capture and sources; an agent's thread;
the Queue's scenario), 115 scenes per run; each mounted target and every
part the contract declares for it pointed at (reveal never; offscreen
again with reveal navigate). Results
`ui-nav-run/evidence/final-integration/` (one JSON per run, `summary.txt`).

First run (English, 1440x900), refused beyond a. and b.: the status line's
words, the agents list's previous and next chevrons and filter, a folder
row's name, the folders list, a folder's contents, a node's name in the
centre, a thread record's agent, a subagent's dot in a workflow, the voice
view's notifications, the Queue's active section. Fixed (uncommitted at
the sweep, committed with this record):

- `uiRegistry.visibilityOf`: an element the point finds its own holder at
  is not covered (the status words ignore the pointer); an element laid out
  but not drawn (`visibility: hidden`, the previous chevron with nothing
  to page to) is `hidden`, not `occluded` (`undrawn()`, shared with
  `uiPipeline.boxOf`, so it is never an obstacle either); the point checked
  is the middle of the part inside the window (a long list or a section
  partly scrolled out was `occluded` by the footer).
- Room, with the two existing variables: `--ui-corridor` on the agents
  list's rail side and on a folder's detail (their chevrons, sections and
  buttons reached the edge their leaves run down); `--ui-leaf-lane` above a
  folder row's name, over a thread or conversation record's head, over a
  workflow's subagent row, and over the title row of the node and subsystem
  details (past the close ×).
- Page test `test_parts_beside_a_close_a_status_or_their_objects_edge_are_reached`
  (status words, agents next, folder row name, folder contents, node name,
  thread record agent).

Final sweep, the same in all four runs (en and es, 1440x900 and 1280x800),
259 contract target/parts:

| Run | Reached | Refused | Mounted, part not on show | Never registered |
|---|---|---|---|---|
| en 1440x900 | 211 | 8 | 26 | 14 |
| en 1280x800 | 211 | 8 | 26 | 14 |
| es 1440x900 | 211 | 8 | 26 | 14 |
| es 1280x800 | 211 | 8 | 26 | 14 |

Refused (blocker `reach-remainder`): Settings' `settings` (whole, close,
nav) and `settingsSection` (whole, heading, body), `blocked / occluded`
because Settings covers the orb every route starts from (by design, page
test `test_pointing_inside_settings_is_blocked_while_it_covers_the_orb`);
the right panel's agent `name` (`no-route`: the agent's orb, 58 px in a
60 px row, sits between the rail and the name); a network tile's
`presence` (`no-route`: the dot is in the tile's title, set on its top
border, beside the name). Not more room: each needs a layout or rule
change.

Never registered (14): the contract's `rightTab`, `mainTab` and `region`
kinds, which no component registers (the remaining-gaps record's
"optional contract items"), and the `main.main` capture (the `?view=main`
page, not swept). Mounted but the part not on show with these fixtures
(26): conditional parts (Stop while speaking, Load earlier, restore, a
file preview, a crumb, the Queue's composer and back, receipts, Dismiss on
a finished workflow, Show done, the subagent detail's parts, the hidden
previous chevron); not refusals.

### Final integration test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
  `scripts/generate_ui_contract.py --check` and
  `scripts/generate_messaging_contract.py --check`: exit 0.
- Targeted pytest (the remaining-gaps list plus the localization
  integration's: `test_ui_contract`, `test_ui_control`, `test_ui_tools`,
  `test_ui_navigation`, `test_ui_pipeline`, `test_ui_right`,
  `test_ui_coverage_rules`, `test_ui_locale`, `test_brain_page_parity`,
  `test_jarvis_mcp`, `test_header_lines`, `test_dead_code_removed`,
  `test_anchored_patterns`, `test_internal_tool`, `test_untrusted_turn`,
  `test_tool_handlers`, `test_mcp_config`, `test_voice_ws`,
  `test_tool_argument_echo`, `test_show_on_screen`, `test_language_setting`,
  `test_spanish_speaking`, `test_subsystem_diagnostics`,
  `test_voice_diagnostics`, `test_team_client`, `test_team_network`,
  `test_team_client_stream`, `test_team_reminder`, `test_speech_models`,
  `test_transcription_ws`, `test_messaging_client`, `test_brains_api`,
  `test_web_security`, `test_spoken_words`, `test_data_paths`,
  `test_system_prompt_header`, `test_brain`, `test_access_keys`,
  `test_messaging_queue_store`, `test_self_update`): 1197 passed, 2 skipped,
  1 failed: `test_ui_navigation` (its Node harness has no
  `getComputedStyle`); fixed with `undrawn()` guarding it, then
  `test_ui_navigation`, `test_ui_pipeline`, `test_ui_coverage_rules`,
  `test_ui_right`: 16 passed.
- `pytest -m page` at 0ab205f (a–c): pipeline, left_settings, voice_health,
  right, layout, workflows: 52 passed. After the agents corridor:
  agents_list_fits, rays, right: 37 passed.
- `pytest -m page` on every voice-page file but refresh and screen, after
  d: 200 passed, 21 failed, every failure a timeout. Rerun: settings,
  navigation and the new pipeline tests pass; the pipeline's two animated
  tests (`test_the_map_stays_…`, `test_clearing_…`), all of
  `test_voice_page_voice_health.py` (it waits for the fake microphone's
  frames) and `test_voice_page_speech_input.py` still time out. Not this
  change: the same tests fail the same way with the d. changes stashed (at
  0ab205f, where the pipeline and voice_health files had passed hours
  before). The machine was at load 9–11 with Chromium's GPU process (the
  orb's software WebGL) at over 600 % CPU; the reduced-motion tests pass.
  Left for CI with the full suite.
- Not run, by rule: the full suite, the wheel build.

## No map at rest (2026-11-02)

Tim, on the second candidate, approved as written: "The current build does
nothing when I look at it: nothing moves or lights up, so I cannot evaluate
it. Visible lines must not appear on the map; it should look as it does
now. Make the navigation demonstrable and preserve the current appearance,
then show me something I can assess."

This supersedes the plan's "the background pipeline remains"
(implementation.md, step 8) and every earlier note here about a faint map
that is always there.

### What changed (e0fdb95)

- `UiPipelineLayer.tsx` draws nothing while no cue runs: the always-on
  map (the orb's rise, the top pipeline, the rails and each object's
  branch) is gone, with its measuring hook. While a cue runs it draws that
  cue's route alone (faint route, three pulses, rings, the contour) and
  fades it to nothing when the cue ends. Amber transmission rays are
  untouched.
- The map stays as data: `mapEdges()` still feeds `resolveRoute`, and
  hit-testing is unchanged. `socketBranches`, used only to draw the map, is
  removed. The `.ui-map` rule is removed from `uiPipeline.css`.
- Each cue's group carries `data-edges`, the ids of the edges it walks in
  that layer, so a test can check the route is on registered edges without
  a drawn map.

### The page at rest against main

origin/main (e20d6c9) and this branch built with `vite build` to scratch
folders, served on loopback with the page tests' fixtures (sessions, team
network, workflows, folders, conversation record), 1440x900, reduced
motion, Settings closed: the start (conversation), each of the six right
tabs, a workflow's detail, and the detail closed again. Shots, diffs and
the two scripts are in `.claude/worktrees/ui-nav-run/evidence/at-rest/`
(`main/`, `branch/`, `diff/`, `comparison.txt`, `tools/`).

- On the branch every view has the three pipeline layers with 0 elements
  drawn in them.
- main against itself (the orb and the microphone meter animate): 0.8 to
  1.5 % of pixels differ. Branch against main: 2.3 to 5.0 %.
- Every difference beyond that noise is a box or line of text shifted by
  the spacing already approved: the conversation and centre views inset
  16 px on each side (the corridor), the right column 16 px narrower, the
  left column below the microphone row 8 px lower (its leaf lane), message
  cards and folder rows 2 px taller (leaf lanes). None is a pipeline line.
- Two differences are not spacing: the voice summary's headroom reads
  "2.0 s drop" where main reads "2 s drop" (the localization's number
  format), and the ⋮ glyph renders a few pixels differently (largest
  channel difference 44 of 255, in a 4 by 15 px area).

### No map at rest test evidence

- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass.
- `pytest -m page tests/test_voice_page_pipeline.py`: 10 passed. Nothing is
  drawn at rest; during a cue its route is drawn on registered edges only,
  out of the orb's rise; after it ends, and after a clear or his own
  navigation, nothing remains.
- `pytest -m page` navigation, centre_nav, remaining_nav, layout, right:
  31 passed. `pytest` ui_pipeline, ui_navigation, dead_code_removed: 41
  passed.
- Not run, by rule: the full suite.

## Tour routing (2026-11-02)

Tim, through JARVIS, after watching the third candidate's tour: "keep the
route on the main line, the primary channel, for as long as possible;
reduce the early cut-ins and branch off only when needed. Use the border or
box around a control as an intermediate point: first draw attention to the
box, then move from its border toward the controls inside it. Use
surrounding controls as transition points where that improves the route.
Make the movement clearer and better."

### The rules as implemented

**Primary channel.** From the existing layout model: a target's primary
channel is the declared trunk from the orb to its region's rail (the orb's
rise, the top pipeline and, for the right, its run on past the main rail's
top; `UI_CONTRACT.pipeline`), followed by that rail down its corridor (left:
the lane at the page's left edge; main: the gutter left of the right column;
right: the rays' channel; overlay: outside an open modal's left edge).

**Latest branch point** (`uiPipeline.ts` `lateBranch()`). The route stays on
the rail until the latest point that still reaches the target:

1. level with the target (a part's middle, or a whole object's), straight
   in to its facing edge, when that run crosses no line of text, no control
   and no other object anywhere on the page between the rail and the
   target (`obstaclesIn()`: the whole page, limited to the run's span and
   the outermost box's height);
2. otherwise level with the lane beside the neighbouring row: the gap over
   or under the target's row (`lane()`), across in it, passing the row
   before (or after) the target, then down or up into the target's top or
   bottom edge; the shorter of the two that clear. This is the
   "surrounding controls as transition points" rule: the neighbouring row
   is passed, never crossed, and nothing is marked on it;
3. only when neither is clear, the earlier entry the second and third
   candidates used for everything: the object's socket near its top and a
   leaf down its inner margin (`socketRoute()`), so nothing reached before
   is lost.

**Boxes as waypoints** (`boxesAround()`, `marksOn()`). Every box holding the
target is a waypoint, outermost first: the registered boxes
(`uiRegistry.ts` `BOX_IDS`: `right.panel`, `right.tabs`, `main.tabs`,
`main.workflow.tabs`, `main.workflow.phases`, `main.folders.tabs`,
registered with `useUiBox` by `RightPanel`, `TabRow`, `WorkflowDetail` and
`FoldersView`), every other registered target holding it (a tile, a centre
view, a workflow's detail, a list), and, for a part, its own object. A box
drawn where another holding it is (within 2 px on every side) is marked
once. Each box is marked where the route last passes into it (its border,
counted from 4 px outside, since a leaf ends 3 px short of its part). The
route's topology now includes its marks, so a cue whose boxes change under
it is cancelled like one whose edges change.

**Timing** (`protocol.json` `timing`, `uiSignal.ts`). The signal moves at an
even pace over `travelMs` 2,000 ms (was 1,400) and stops `stopMs` 450 ms at
each box border while that box is outlined (`boxMarkMs` 900 ms, fading);
the pulses behind it gather at the border and stream on. The line is drawn
growing behind the first pulse (`drawnTo()`), not all at once. Arrival is
`arrivalAtMs` (2,220 ms) plus 450 ms per box; the hold (to `holdUntilMs`
5,000 ms) and the reset (`resetByMs` 5,600 ms) move with it. Reduced motion
still shows the whole connection at once with no marks.

### Each tour stop's route (1440x900, from the recording)

| Stop | Route, in order |
|---|---|
| Mac mini CPU, right | rise, top pipeline, on past the main rail's top, down the right rail to the CPU row's level; in: mark the right panel, mark the tile, into the CPU row's edge |
| Mac mini CPU, centre | rise, top pipeline, down the main rail to the CPU value's level; in: mark the node's detail, into the value's edge |
| Database, left | rise, top pipeline left, down the left rail to the Database row's level; in: mark the Team Server tile, into the row's edge |
| Mute, left | down the left rail to the lane under the mic row; in under the meter: mark the mic control, up into the mute button's bottom edge |
| Design phase step | down the main rail to the lane between the tab strip and the stepper; in: mark the workflow's detail, along the lane past the tabs above, mark the phase stepper, down into the step's top edge |
| Decisions tab | down the main rail to the lane over the tab strip; in: mark the workflow's detail, along the lane, mark the tab strip, down into the Decisions tab's top edge |
| Folders' Registered tab | down the main rail to the lane under the Folders tab strip, across under the folder pane's text; mark the folders list, mark the tab strip, up into the Registered tab's bottom edge |
| Workflow row, right | down the right rail to the row's level; in: mark the right panel, into the row's edge |
| Import entries task | down the main rail to the task's level; in: mark the workflow's detail, into the task's edge |

The third candidate's routes left the rail at each object's socket, 14 px
below its top, and ran down inside it along its inner margin (the node's
CPU: at the detail's top, about 200 px above the value; the task: at the detail's
top). None of the nine does that now.

### Coverage

The reach sweep (`ui-nav-run/tools/test_reach_sweep.py`, as before, results
in `ui-nav-run/evidence/tour-routing/sweep/`, `summary.txt`), 259 contract
target/parts:

| Run | Reached | Refused | Mounted, part not on show | Never registered |
|---|---|---|---|---|
| en 1440x900 | 213 | 6 | 26 | 14 |
| en 1280x800 | 213 | 6 | 26 | 14 |
| es 1440x900 | 213 | 6 | 26 | 14 |
| es 1280x800 | 213 | 6 | 26 | 14 |

Up from 211 and 8: the right panel's agent `name` (reached over its row,
in the gap past the agent before it, dropping into the name's top edge,
never across the agent's orb) and the network tile's `presence` dot. The
six left are Settings' targets, refused while Settings covers the orb.

### Tour routing test evidence

- `npx tsc --noEmit`: pass. `npm run build` (scratch `--outDir`): pass.
  `scripts/generate_ui_contract.py --check`: exit 0.
- `node tests/ui_signal_cases.mts` (through `test_ui_pipeline.py`): 21
  checks, including the stops at each box, the even pace, the line drawn
  behind the first pulse, the marks in turn and the new timing.
- `pytest` ui_contract, ui_control, ui_tools, ui_pipeline, ui_navigation,
  ui_right, ui_coverage_rules, ui_locale, dead_code_removed,
  brain_page_parity: 142 passed.
- `pytest -m page tests/test_voice_page_pipeline.py`: 14 passed, among them
  the new `test_each_tour_stop_stays_on_the_trunk_marks_its_boxes_in_order_and_crosses_no_text`
  (English and Spanish, 1440 and 1280: for each tour stop, the route leaves
  its rail within 40 px of the target's row, its boxes are the expected
  ones outermost first, and from the rail to the target no sampled point,
  every 2 px, lies on any control or line of text on the page). The first
  test now also checks the tile's route goes in from the rail without a
  socket, marks the panel then the tile in that order, and arrives after
  both stops.
- `pytest -m page` right, navigation (with pipeline): 24 passed; earlier in
  the same change, centre_nav, left_settings, navigation, remaining_nav,
  right, layout, rays: 59 passed, 1 failed (the agent's name, now reached;
  the test was changed to check the route over the row and never across
  the orb, then passed).
- Not run, by rule: the full suite.
