# UI navigation coverage inventory

Base: `origin/main` 91b13fb (design baseline 6a37860 plus PR #32, self-update).
Contract: `jarvis/ui_contract/` (protocol.json and families/*.json), merged by
`jarvis.ui_contract.load`, page copy `frontend/src/uiContract.ts`.

Each row is one actual control or meaningful display part. Column meanings:

- **Command**: the registered navigation command a person's control and the
  brain both run. *Declared* commands exist in the contract today; *proposed*
  ones are added (declaration plus handler, together) by the owning worker.
  `—` means the control is not navigation (see its classification).
- **Target / parts**: `kind` in `view`, keyed by the entity key shown, with
  the parts the contract declares for that presentation.
- **Reveal**: how a hidden target is brought into view before the signal.
- **Socket**: which rail and which edge the object's branch enters.
- **State**: `done` (registered, handler and target live, slice-tested),
  `contract` (IDs and parts declared, adapter or handler to build),
  `classify` (non-navigation; stays on its own authorised path, targetable
  only).

Reconciliation with the design map (changes since 6a37860): Settings has a
sixth section, **Updates** (`SECTIONS` in Settings.tsx), and the menu has an
**Updates** item (`openSettings("updates")`); both are covered below. No
existing command or UI-control infrastructure was found to reuse: the only
lookups were `transferEndpoints.ts`'s DOM queries for rays, which stay for
transmissions and are not used for attention.

## Shell and tab rows (integrator)

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| RightPanel.tsx `TabRow` six tabs | `right.tab.select {tab}` (declared) | `rightTab` in `right.tabs`, key = tab; parts label, attention | shared `rightTab` store (uiNavigation.ts); was local `useState` | always | `agents.tab-attention-cleared` when tab=agents | none (always mounted) | rail.right, right | done (handler); tab target adapter: contract |
| CentreArea.tsx `TabRow` | `main.tab.select {tab}` (declared) | `mainTab` in `main.tabs`, key = tab | shared `mainView` store; open/close effects still select detail tabs | static tabs always; detail tabs only while open (`openDetails`), else `no-entity` | `centre.view-shown` | none | rail.main, left | done (handler); remove effect-based selection: navigation worker |
| `?view=<id>` first view | — (page load) | — | `uiNavigation.firstMain` | static views only | — | — | — | done |
| Regions (App.tsx left-column, right-column, centre, footer) | — | `region` keyed left/right/main/footer | none | always | — | — | rail.<region> | contract (whole-region adapter) |

## Left column (coverage-left-settings)

Tested by `tests/test_voice_page_left_settings.py` (every left part pointed
at with reduced motion, the database row animated, the map, and human/brain
parity for the tile). `no-route` below means the page answers
`blocked / no-route` truthfully: the part sits beside another part on the
side its leaf comes from, and `uiPipeline.ts` `leafEdge` only runs straight
in from the facing edge (requested from pipeline-animation: leaf routing
round sibling parts along the object's inner margin).

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| OrbControl.tsx `#orb-control`, canvas, BrainLogo | — (display) | `orb` in `left.fixed`; canvas (the orb's box: the canvas fills it, whole-canvas only), logo | `useUiTarget` | always; logo while a brain with a logo runs | — | none | rail.left, left | done |
| StatusControl.tsx status text, Stop | — Stop: `data-ui-action="speech"` (hush) | `status`; text, stop | `useUiTarget`; Stop `stopSpeaking.current()` | text while there is one; stop only while speaking | hush stays its own action | none | rail.left, left | done (whole tested; text/stop parts live while shown) |
| MicControl.tsx health dot | `voice.diagnostics.open` (declared, navigation) via `bindingProps` | `mic`; meter, health, mute, off | was `openDiagnostics.set(true)` | dot only while input has a problem | `centre.view-shown` | none | rail.left, left | done (binding, target); health dot pointing not page-tested (needs an input fault fixture) |
| MicMeter.tsx / `#mic-silenced` | — (display) | `mic` part meter | `partRef` | always | — | none | rail.left | done |
| MuteButton.tsx, MicOffButton.tsx | — `data-ui-action="microphone"` | `mic` parts mute, off | `micMuted`, `micOff` stores | always | changes the microphone | none | rail.left | classify; parts `no-route` (beside the meter) |
| TeamServerTile.tsx tile | `network.open {kind: server}` via `bindingProps` | `teamServer` in `left.fixed`; readiness (the word), server, database, lastWrite | was `byHand("network.open")` | always | `centre.view-shown`, `centre.detail-replaced` | none | rail.left, left | done; lastWrite `no-route` (level with the readiness word) |
| BrainUsage.tsx → WeeklyUsage.tsx each account | — (display only) | `brainAccount` keyed provider (the one named, else the figures', else the running brain); name (logo), remaining, pace, reset (burn-down) | `useAccountTarget` | while its provider is known (no target before) | — | none | rail.left, left | done |

## Right panel (coverage-right)

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| FoldersPanel.tsx search | proposed `folders.panel.search {text}` | `foldersList` in `right.folders`; search, open | local `useState` search | always | `list.filtered` | `right.tab.select folders` | rail.right, right | contract |
| FoldersPanel.tsx row → `openFolder(id)` | proposed `folders.open {folderId}` | `folder` keyed folder; row, name, availability | `openFolder` sets `selectedFolder`, bumps `showFolders` | folder listed | `centre.view-shown` | tab select, then row by folder ID | rail.right, right | contract |
| AgentsControl.tsx show filter | proposed `agents.filter {show}` | `agentsList`; filter, previous, next | local `useState` `show` (persisted by `readShow`) | always | `list.filtered` | tab select | rail.right, right | contract |
| AgentsControl.tsx chevrons `cycle(±1)` | proposed `agents.page {direction}` | `agentsList` previous/next | local `first` | while it overflows | `list.paged` | tab select | rail.right | contract |
| AgentsControl.tsx row toggle (`toggle(agent)`) | — recipient selection (not navigation) | `agent` keyed session; row, name, state, orb | selects message recipient | always | changes who a message goes to | tab select, page to row by session ID | rail.right, right | classify |
| AgentsControl.tsx reveal of one agent row | proposed `agents.reveal {sessionId}` | `agent` row | none today | session listed under current filter | `list.paged` (may change page) | tab select then page | rail.right | contract |
| QueuePanel.tsx Waiting / Active rows `openItem(thread)` | proposed `queue.open {threadId}` | `queueThread` keyed thread in `right.queue`; row, sender, subject, receipt, turn | `openItem` (moves Waiting → Active) | thread listed | `queue.item-activated`, `centre.view-shown`, can `queue.read-marked` | tab select | rail.right, right | contract |
| QueuePanel.tsx UnsentRow / history `followLink(thread)` | proposed `queue.follow {threadId}` | `queueThread` row | `followLink` (keeps placement) | thread listed | `centre.view-shown`, can `queue.read-marked` | tab select | rail.right | contract |
| QueuePanel.tsx history toggle, "more" | proposed `queue.history {shown}`, `queue.history.more` | `queuePanel` history | local state, `readHistory` | always | `list.paged` | tab select | rail.right | contract |
| QueuePanel.tsx New, Restore | — (domain: drafts, restore) | `queuePanel` new; row | `startNew`, `restore` | — | creates a draft / restores | — | rail.right | classify |
| WorkflowsView.tsx show finished | proposed `workflows.showDone {shown}` | `workflowsList`; showDone | local `showDone` | always | `list.filtered` | tab select | rail.right | contract |
| WorkflowsView.tsx row `openWorkflow.set(id)` | proposed `workflow.open {workflowId}` | `workflow` keyed workflow in `right.workflows`; row, state, tasks, dismiss | `openWorkflow` store + CentreArea effect | workflow listed | `centre.view-shown`, `centre.detail-replaced` | tab select (+ showDone if finished) | rail.right, right | contract |
| WorkflowsView.tsx subagent row | proposed `subagent.open {key}` | `subagent` keyed subagent; row, state | `openWorkflow.set(a.key)` | while listed (LINGER_SEC) | as workflow.open | tab select | rail.right | contract |
| WorkflowsView.tsx Dismiss × | — (domain: `dismissWorkflow`) | `workflow` part dismiss | `dismissWorkflow` | — | records a dismissal | — | — | classify |
| NetworkPanel.tsx / NetworkTile.tsx tile | `network.open {kind: node, nodeId}` (declared) | `node` keyed node in `right.network`; name, presence, human, cpu, memory, pace | `byHand("network.open")`; registered via `useUiTarget` | node in snapshot, else `not-loaded`/`no-entity` | `centre.view-shown`, `centre.detail-replaced` | `right.tab.select network` | rail.right, right | done |
| RightPanel `DiagnosticsView` → SubsystemList.tsx row | proposed `subsystem.open {id}` | `subsystem` keyed subsystem in `right.diagnostics`; row, status | `openSubsystemRow` | listed, not `page` (disabled) | `centre.view-shown` | tab select | rail.right | contract |
| VoiceSummary.tsx open | proposed `voice.diagnostics.open` | `voice` in `right.diagnostics`; summary, open | `openDiagnostics.set(true)` | always | `centre.view-shown` | tab select | rail.right | contract |

## Centre views (coverage-main)

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| ConversationView.tsx "earlier" | proposed `conversation.earlier` | `conversation`; history, earlier, input | `loadEarlier` | while older records exist | `list.paged` | `main.tab.select conversation` | rail.main, right | contract |
| ConversationView.tsx record expand (long text) | proposed `conversation.record.expand {recordId, open}` | `conversationRecord` keyed record (`r<id>` from conversation_log id); record, user, answer, agent, expand | local `open` | record loaded | — | tab select, load earlier until present | rail.main | contract |
| ConversationView.tsx AgentLink | proposed `thread.open {sessionId}` | `conversationRecord` part agent | `openThread.set(...)` | agent known | `centre.view-shown` | tab select | rail.main | contract |
| ConversationView.tsx live turns (`t<id>`) | — | needs a durable identity from captions.ts before exact targeting | page-local turn id | — | — | — | — | contract (identity gap: live turns have no durable key) |
| ConversationView.tsx restore capture | — (domain: `restoreCapture`) | `conversationRecord` | `restoreCapture` | — | reopens a capture | — | — | classify |
| ConversationView.tsx typed input (open, type, Enter) | — (typed submit is a turn, not navigation) | `conversation` part input | `sendTyped` | — | sends his words | — | rail.main | classify |
| CaptureBox.tsx read / accept / delete / send | — (domain: capture actions) | `capture` keyed capture id; box, body, recipient, actions | `act(...)` | — | read-back, send, delete | — | rail.main | classify |
| CaptureChip.tsx, SourcesChip.tsx expand | proposed `capture.chip.expand`, `sources.expand {recordId, open}` | `capture` / `sources` keyed record | local `open` | present | — | tab select | rail.main | contract |
| FoldersView.tsx registered / discovered tabs | proposed `folders.list {view}` | `foldersList` in `main.folders`; registered, discovered | local `view` | always | `list.filtered` | `main.tab.select folders` | rail.main | contract |
| FoldersView.tsx search, role, availability, show removed, show ignored | proposed `folders.filter {search?, role?, availability?, removed?, ignored?}` | `foldersList` parts | local state | always | `list.filtered` | tab select | rail.main | contract |
| FoldersView.tsx Add existing folder (open/cancel form) | proposed `folders.add.open {open}` | `foldersList` add | local `adding` | always | — | tab select | rail.main | contract |
| FoldersView.tsx Check / Save / Ignore / Restore | — (domain: register, candidate actions) | `folderCandidate` keyed candidate | `act(...)` | — | changes the catalog | — | rail.main | classify |
| FoldersView.tsx catalog row `selectedFolder.set(id)` | proposed `folders.open {folderId}` (same as right) | `folder` in `main.folders`; row, detail, location, contents, close | `selectedFolder` store | folder listed | — | tab select, filters cleared only if declared | rail.main | contract |
| FolderDetail.tsx breadcrumbs, entry open, Load more | proposed `folder.browse {folderId, path, revision}`, `folder.more` | `folderEntry` keyed `folderId/relativePath`; row, crumb, preview | local `path`, `page`, `rows` | path permitted by reader, revision current | `list.paged` | folders.open then browse | rail.main | contract |
| FolderDetail.tsx file preview, Show more | proposed `folder.preview {folderId, path, revision}` | `folderEntry` part preview | local `file`, `windows` | as the reader allows (no credentials) | — | browse then preview | rail.main | contract |
| FolderDetail.tsx Edit / Reconnect / Remove modes, Refresh, × | close: proposed `folders.close`; modes: — (domain) | `folder` parts | `setMode`, `selectedFolder.set(null)` | — | edit/remove change the catalog | — | rail.main | classify (modes) / contract (close) |
| WorkflowDetail.tsx nine subtabs | proposed `workflow.tab.select {tab}` | `workflow` parts `tab.overview` … `tab.events` | local `tab` in `OneWorkflow` | workflow open | — | workflow.open | rail.main, right | contract |
| WorkflowDetail.tsx phases, tasks, progress rows | — (display) | `workflowPhase` / `workflowTask` keyed `workflowId/slug`; row, state, progress | keyed by `slug` | in the workflow record | — | workflow.open + tab select | rail.main | contract |
| WorkflowDetail.tsx waits, blockers | — (display) | `workflowWait` / `workflowBlocker` keyed `workflowId/key` | keyed by `o.key` | open | — | tab select | rail.main | contract |
| WorkflowDetail.tsx decisions: options, Answer | — (domain: `_answer_workflow_decision`) | `workflowDecision` keyed `workflowId/key`; row, options, answer | `answer(...)` | open | answers | tab select | rail.main | classify (answer) / contract (target) |
| WorkflowDetail.tsx transcript, earlier, scroll | proposed `workflow.transcript.earlier` | `workflowTranscript` keyed workflow; list, earlier | `loadEarlier`, onScroll | while older entries exist | `list.paged` | tab select | rail.main | contract |
| WorkflowDetail.tsx events kind filter | proposed `workflow.events.filter {kind}` | `workflowEvents`; list, filter | local `kind` | always | `list.filtered` | tab select | rail.main | contract |
| WorkflowDetail.tsx Dismiss, × close | close: proposed `workflow.close`; Dismiss: — (domain) | `workflow` header, close, dismiss | `openWorkflow.set(null)`; `dismissWorkflow` | open | `centre.detail-closed` | — | rail.main | contract / classify |
| WorkflowDetail.tsx subagent rows, SubagentDetail × | proposed `subagent.open {key}`, `workflow.close` | `subagent` in `main.workflow`; header, close, transcript | `openWorkflow.set(a.key)` | listed | `centre.detail-replaced` | workflow.open | rail.main | contract |
| AgentThread.tsx × and earlier | proposed `thread.close`, `thread.earlier` | `agentThread` keyed session; `threadRecord` keyed `session/record` | `openThread.set(null)`, `loadEarlier` | open | `centre.detail-closed`, `list.paged` | thread.open | rail.main | contract |
| VoiceDiagnosticsView.tsx range, zoom | proposed `voice.range {range}`, `voice.zoom {at?}` | `voice` in `main.diagnostics`; range, zoom, overview, timeline, incidents, notifications, messages, close | local `range`, `zoom` | open | — | voice.diagnostics.open | rail.main | contract |
| VoiceDiagnosticsView.tsx incidents | — (display) | `voiceIncident` keyed incident id | keyed by `e.id` | in range | — | range select | rail.main | contract |
| VoiceDiagnosticsView.tsx Save as JSON, quiet toggle, × | close: proposed `voice.diagnostics.close`; Save / quiet: — | `voice` close | `save`, `setQuiet`, `openDiagnostics.set(false)` | — | export file; quiet page | — | rail.main | classify / contract |
| SubsystemView.tsx checks, messages, × | close: proposed `subsystem.close` | `subsystem` in `main.subsystem`; `subsystemCheck` keyed `subsystem/check` | `openSubsystem.set(null)` | open | `centre.detail-closed` | subsystem.open | rail.main | contract |
| TeamServerView.tsx facts | `network.open {kind: server}` | `teamServer` in `main.network`; connection, database, service | Facts rows | always | — | network.open | rail.main, right | contract (parts) |
| NodeView.tsx facts, × | `network.open`, `network.close` (declared) | `node` in `main.network`; owner, version, lastUpdate, cpu, memory, pace, coverage registered; name, presence, human, remaining, accounts to add | `byHand("network.close")`; `useUiTarget` | node listed, else the "no longer lists" message and no target | `centre.detail-closed` | `network.open` | rail.main, right | done (cpu slice); remaining parts: contract |
| QueueItemView.tsx Back, Next, Close, related links (`jump`) | proposed `queue.back`, `queue.next`, `queue.close`, `queue.message.show {messageId}` | `queueThread` in `main.queue`; header, back, next, close, composer, turn; `queueMessage` keyed message; card, body, receipt | `goBack`, `next`, `close`/`closeItem`, `jump` | open | `queue.read-marked` (body shown), `queue.draft-saved` (leaving editor), `queue.item-activated` (next) | queue.open | rail.main | contract |
| QueueItemView.tsx turn, addressed, Mark unread, Put back, Done, Restore, Respond, Reply, discard, Retry, Cancel send, Edit | — (domain: work commands, drafts, sends) | `queueThread` turn; `queueMessage` | messaging routes | — | change work state or drafts | — | rail.main | classify |
| QueueFiles.tsx download / reveal / remove | — (domain: files) | `queueAttachment` keyed attachment; entry | `downloadFile`, `revealFile` | — | downloads | — | rail.main | classify |
| MessageComposer.tsx fields, Review, Discard | — (domain: drafts) | `queueThread` composer | `edit`, `review`, `discard` | — | edits a draft | — | rail.main | classify |
| MessageReview.tsx capture buttons | — (domain: approval) | `capture` | capture actions | — | approval stays human | — | — | classify |

## Menu, Settings, reminder, footer (coverage-left-settings)

Commands in `families/settings.json`, handlers next to their state in
`MenuControl.tsx`, `Settings.tsx`, `TeamSignInReminder.tsx`. Tested by
`tests/test_voice_page_left_settings.py` (parity: click versus the brain's
`ui_request` for the same command leaves the same page, with the declared
effects; the overlay map; pointing inside Settings blocked).

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| MenuControl.tsx ⋮ toggle, any click elsewhere | `menu.toggle {open}` | `menu` in `overlay.controls`; toggle (the button is the whole) | `menuOpen` (route state, read-only export) | always | `menu.opened` (when opening) | none | rail.right, right | done |
| MenuControl.tsx Settings, Check for updates | `settings.open {section?}` (`{}`, `{section: updates}`) | `menu` in `overlay.menu`; settings, updates, restart | handler closes the menu, opens Settings | always (a section hidden by first-time setup: `hidden`) | `settings.opened` | `menu.toggle {open: true}` | rail.right, right | done (dropdown keeps its corridor clear, ui-left.css) |
| MenuControl.tsx Restart Server | — `data-ui-action="restart"` (excluded from the brain) | `menu` part restart | `restart` | — | restarts | — | — | classify |
| Settings.tsx nav buttons | `settings.section {section}` | `settingsSection` keyed brains, voice, you, team, updates, system; heading, body | `goTo` (smooth scroll) | Settings open (`not-mounted`), section shown (`hidden` in setup) | — | `settings.open {section: $key}` | rail.overlay, left | done; pointing `blocked / occluded` while Settings covers the orb (by design) |
| Settings.tsx ×, backdrop, Finish Setup | `settings.close` | `settings` in `overlay.settings`; close, nav | ends setup too | open (`no-entity`) | — | `settings.open {}` | rail.overlay, left | done |
| Settings.tsx first-time setup open, Next | — page-initiated open; Next `data-ui-action="setup"` | `settings` | `setup` route state | no Fish key | — | — | — | classify (no actor navigated; revision not moved) |
| Settings.tsx Voice key/Test/Save, You fields/Save, Updates Check/Update | — `input`, `domain`, `refresh`, `restart` | `settingsSection` body | section handlers | — | saves settings / updates | — | — | classify |
| BrainsSection.tsx add, model, sign in, default, remove | — `domain`, `sign-in` | `settingsSection` brains body | `act(...)` | — | changes brains | — | — | classify |
| TeamSection.tsx connect, approval, cancel, disconnect, members, look up, add | — `sign-in`, `domain`, `input` | `settingsSection` team body | `connect`, `change`, `resolve`, `add` | — | membership / sign-in | — | — | classify |
| TeamSignInReminder.tsx Later | `reminder.later` | `teamReminder` in `main.notice`; text, signIn, later | `later` route state | while not signed in and not hidden (`no-entity`) | — | none | rail.main, right | done; text and signIn `no-route` (left of Later) |
| TeamSignInReminder.tsx Sign in | — `data-ui-action="sign-in"`; then `settings.open {section: team}` by hand | `teamReminder` part signIn | `signInAgain` | while shown | starts sign-in, opens Settings | none | rail.main | classify |
| Footer.tsx label | — (display) | `footer` in `footer.fixed`; label (the label is the whole: the footer box has no socket edge) | `useUiTarget`; `#footer #jarvis-label` takes the pointer so it can be seen at its point | always | — | none | rail.footer, left | done |

### Prevention of future coverage gaps (coverage-left-settings)

- `tests/ui_coverage_rules.mts` (TypeScript compiler API and type checker)
  run by `tests/test_ui_coverage_rules.py`: controls must run a registered
  command (`bindingProps`, `data-ui-command`, `byHand`/`runBinding`) or carry
  a reviewed `data-ui-action` class; route state (`@routeState`,
  `detailSlot`, or initialised from one; co-owners by `@routeStateAlso`) is
  written only by its owner, casts included; `useUiTarget` kinds, views and
  parts are declared, part names literal, keys never from numbers.
- Negative fixtures `tests/fixtures/ui_coverage/rejected` (unregistered tab,
  keyboard handler, conditional control, unreviewed class, cast writes to
  Settings' and the menu's state, undeclared target and part, run-time part
  name, index key); accepted fixture `tests/fixtures/ui_coverage/accepted`.
- The page itself passes except the `PENDING` ratchet in the test (other
  workers' files, exact counts that may only shrink).
- Human/brain parity: the page tests above for menu, Settings, reminder and
  the Team Server tile.

## Transmission layer (pipeline-animation)

| Source | Role | State |
|---|---|---|
| RayLane.tsx, transferEndpoints.ts, rayGeometry.ts, transfers.ts | Message transmissions: colours, receipts, tab lighting, incoming/outgoing. Unchanged semantics; RayLane's orb calls now go through `orbSource.ts` (`transferTransmit`, `transferReceive`), which always takes the orb. | done (arbitration hook); shared lifecycle extraction: contract |
| orbSource.ts | One owner of the orb's overlay with tokens; attention waits `sourceQueueMs` then `busy`. | done |
| uiPipeline.ts, uiAttention.ts, UiPipelineLayer.tsx | Graph, route resolution on registered edges, one scheduler, white lifecycle, rings, contour, reduced-motion static arrival. | done (slice); overlay/footer rails, corridor reservation, modal layer: contract |

## Tests that exist for this inventory

- `tests/test_ui_contract.py`: generated copy current; Python and page validators agree on `tests/fixtures/ui_contract_cases.json`; negative fixtures (disconnected junction, duplicate presentation, unknown reveal command, undeclared part, undeclared effect); tools built from declarations.
- `tests/test_ui_control.py`: page binding, epochs, other page/epoch answers ignored, disconnect, timeout, invalid requests never sent.
- Vertical slice through the real app: `ui-nav-run/tools/slice_driver.py` (outside the repository; see execution.md).
- To add per family: page tests (`pytest -m page`) running the human control and the brain command against the same fixture and comparing route state and effects.
