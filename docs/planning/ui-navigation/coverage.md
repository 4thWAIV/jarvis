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

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| OrbControl.tsx canvas, BrainLogo | — (display) | `orb` in `left.fixed`; canvas, logo | none | always; canvas internals need a projection adapter (none: whole canvas only) | — | none | rail.left, left | contract |
| StatusControl.tsx status text, Stop | — Stop is `stopSpeaking` (domain: hush) | `status`; text, stop | `stopSpeaking.current()` | stop only while speaking | hush stays its own action | none | rail.left, left | classify (Stop) / contract (target) |
| MicControl.tsx health dot | proposed `voice.diagnostics.open` | `mic`; meter, health, mute, off | `openDiagnostics.set(true)` | always | `centre.view-shown` | none | rail.left, left | contract |
| MuteButton.tsx, MicOffButton.tsx | — (microphone permission/state) | `mic` parts mute, off | `micMuted`, `micOff` stores | always | changes the microphone | none | rail.left | classify |
| TeamServerTile.tsx tile | `network.open {kind: server}` (declared) | `teamServer` in `left.fixed`; readiness, server, database, lastWrite (`data-row`) | `byHand("network.open")` | always | `centre.view-shown`, `centre.detail-replaced` | none | rail.left, left | done (command); part adapters: contract |
| BrainUsage.tsx → WeeklyUsage.tsx each account | — (display only) | `brainAccount` keyed provider; name, remaining, pace, reset | none | while the provider is configured | — | none | rail.left, left | contract |

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

Branch `ui-nav-coverage-main`. Every centre target hangs from `rail.main`
and enters at its right edge (`CENTRE_SOCKET`, ConversationView.tsx).
Commands added by this family are declared in `conversation.json`,
`workflows.json` and `folders.json`, with their handlers next to the state
they change; the open/close commands are navigation's (`navigation.json`).
Page tests: `tests/test_voice_page_centre_nav.py` (each control and the
brain's request leave the same state; indications arrive).

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| ConversationView.tsx view | — | `conversation` in `main.conversation`; history, earlier, speaking, input | — | always | — | `main.tab.select conversation` | rail.main, right | done |
| ConversationView.tsx "Earlier" | `conversation.earlier` | `conversation` part earlier | `earlierPages` store (one per list, ConversationView.tsx) | while older records exist (`no-entity` after) | `list.paged` | tab select | rail.main | done |
| ConversationView.tsx record rows (`RecordItem`, `HistoryTurn`) | `conversation.record.expand {recordId, open}` for the status toggle, a long turn | `conversationRecord` keyed `r<id>` (record) or `t<id>` (live turn, `captions.turnKey`); record, text, answer, agent, expand, restore | open records in `captions.ts`, changed only by the command | record shown and able to open (`no-entity` otherwise) | — | tab select; an `r<id>` not loaded is looked for up to 10 earlier pages (`earlierPages.seek`); a dropped `t<id>` is `entity-gone` | rail.main | done |
| ConversationView.tsx AgentLink | `thread.open {sessionId}` (binding) | `conversationRecord` part agent | navigation's handler | agent known | `centre.view-shown` | tab select | rail.main | done |
| ConversationView.tsx live turns | — | `t<id>` from `captions.turnKey`: given by captions.ts, kept for the life of the page; a turn from before a reload is found as its `r<id>` record | — | — | — | — | — | done (identity gap closed for the page's life) |
| CaptureChip.tsx, SourcesChip.tsx expand | `conversation.record.expand` on the turn's key (one command for every record that opens, instead of the proposed `capture.chip.expand` / `sources.expand`) | `conversationRecord` parts expand, answer; `sources` keyed `t<id>`; chip, items | captions.ts open records | shown | — | tab select | rail.main | done |
| ConversationView.tsx restore capture | — (domain: `restoreCapture`) | `conversationRecord` part restore | `restoreCapture` | — | reopens a capture | — | — | classify (target done) |
| ConversationView.tsx typed input | — (a turn, not navigation) | `conversation` part input | `sendTyped` | — | sends his words | — | rail.main | classify (target done) |
| CaptureBox.tsx boxes, Read / Accept / Delete / Send | — (domain: capture actions) | `capture` keyed capture id in `main.conversation` and `main.main`; box, body, recipient, actions | `act(...)` | — | read-back, send, delete | `main.tab.select conversation` / `main` | rail.main | classify (target done) |
| CentreArea MainView (legacy Main) | `main.tab.select main` | `capture` in `main.main`; CaptionBox chips open by `conversation.record.expand` | — | at `?view=main` | — | `main.tab.select main` | rail.main | done |
| FoldersView.tsx Registered / Discovered | `folders.list {view}` | `foldersList` in `main.folders`; registered, discovered, list | list state in FoldersView.tsx, changed only by its commands | always | `list.filtered` | `main.tab.select folders` | rail.main | done |
| FoldersView.tsx search | `folders.search {text?}` (no text clears) | `foldersList` part search | same store | always | `list.filtered` | tab select | rail.main | done |
| FoldersView.tsx role, availability, show removed, show ignored | `folders.filter {role?, availability?, removed?, ignored?}` (`any` clears a filter) | `foldersList` parts role, availability, removed, ignored | same store | always | `list.filtered` | tab select | rail.main | done |
| FoldersView.tsx Add existing folder (open / Cancel) | `folders.add.open {open}` | `foldersList` part add | same store | always | — | tab select | rail.main | done |
| FoldersView.tsx Check / Save / Register / Ignore / Restore | — (domain: register, candidate actions); a registration then runs `folders.open` | `folderCandidate` keyed candidate; row, path, actions | `act(...)` | — | changes the catalog | — | rail.main | classify (target done) |
| FoldersView.tsx catalog row | `folders.open {folderId}` (binding) | `folderRow` keyed folder in `main.folders` (new kind: the row and the detail are two presentations of one folder in one view); row, name, availability | navigation's handler | folder listed under the filters | `centre.view-shown` | `main.tab.select folders`; a filtered-out row is not found, filters are never cleared for it | rail.main | done |
| FolderDetail.tsx detail | `folders.open` / `folders.close` | `folder` in `main.folders`; detail, name, availability, location, contents, preview, close | navigation's handlers | selected | — | `folders.open {folderId: $key}` | rail.main | done |
| FolderDetail.tsx breadcrumbs, entry open | `folder.browse {folderId, path?, revision?}` | `folderEntry` keyed `<folder>/<relative path>`; row, crumb (the top is the folder's `contents`) | contents store in FolderDetail.tsx (folder, location revision, path, pages, file, windows), changed only by its commands | the folder shown; another revision is `stale`; the server's reader refuses what it never reads | `list.paged` | none (browse first) | rail.main | done |
| FolderDetail.tsx Load more, Show more | `folder.more {folderId, list: entries\|preview}` | folder parts contents, preview | same store | while a next cursor exists | `list.paged` | — | rail.main | done |
| FolderDetail.tsx file preview | `folder.preview {folderId, path?, revision?}` (no path closes it) | `folder` part preview | same store | the folder shown | — | — | rail.main | done |
| FolderDetail.tsx Edit / Reconnect / Remove, Refresh | — (domain) | `folder` parts | `setMode`, `act` | — | change the catalog | — | — | classify |
| WorkflowDetail.tsx nine tabs | `workflow.tab.select {workflowId, tab}` | `workflow` in `main.workflow`; header, state, close, dismiss, `tab.overview` … `tab.events` (found by their binding's marks) | tab and kind store in WorkflowDetail.tsx; a workflow closed or replaced starts on Overview | that workflow open (`no-entity` for another) | — | `workflow.open {workflowId: $key}` | rail.main, right | done |
| WorkflowDetail.tsx phases, tasks, progress rows | — (display) | `workflowPhase` / `workflowTask` keyed `<workflow>/<slug>`; row, state, progress (Phases, Tasks and Progress tabs) | — | on their tab | — | none: select the tab first (journey) | rail.main | done (task card `progress` needs TaskBar to take a ref) |
| WorkflowDetail.tsx waits, blockers | — (display) | `workflowWait` / `workflowBlocker` keyed `<workflow>/<key>`; row | — | on their tab | — | as above | rail.main | done |
| WorkflowDetail.tsx decisions, options, Answer | — (domain: `_answer_workflow_decision`) | `workflowDecision` keyed `<workflow>/<key>`; row, options, answer | `answer(...)` | on its tab | answers | as above | rail.main | classify (target done) |
| WorkflowDetail.tsx Transcript, Load earlier | `workflow.transcript.earlier {sessionId, agentId?}` | `workflowTranscript` keyed workflow (list); `workflowAgent` keyed `<workflow>/<session>` (section, entries, earlier); a subagent's as `subagent` (transcript, earlier) | `earlierPages` keyed `<session>[/<agent>]` | that transcript shown with earlier entries | `list.paged` | as above | rail.main | done |
| WorkflowDetail.tsx events kind | `workflow.events.filter {workflowId, kind?}` | `workflowEvents` keyed workflow; list, filter | tab and kind store | that workflow open | `list.filtered` | as above | rail.main | done |
| WorkflowDetail.tsx Close, Dismiss | `workflow.close` (binding); Dismiss — (domain) | `workflow` close, dismiss | navigation's handler; `dismissWorkflow` | open | `centre.detail-closed` | — | rail.main | done / classify |
| WorkflowDetail.tsx subagent rows, SubagentDetail | `subagent.open {sessionId, agentId}`, `workflow.close` (bindings) | `subagent` keyed `<session>/<agent>` (`keyOf [session, subagent]`); row, state (Overview), header, state, close, transcript, earlier (detail) | navigation's handlers | listed | `centre.detail-replaced` | none in `main.workflow` (a composite key cannot fill `subagent.open`'s two arguments) | rail.main | done |
| AgentThread.tsx thread, ×, Earlier | `thread.close`, `thread.earlier` | `agentThread` keyed session; header, state, close, earlier; `threadRecord` keyed `<session>/r<id>` with the record's parts | `earlierPages` keyed session | open | `centre.detail-closed`, `list.paged` | `thread.open {sessionId: $key}`; a record of the open thread is looked for up to 10 earlier pages | rail.main | done |
| VoiceDiagnosticsView.tsx view, × | `voice.diagnostics.close` (binding) | `voice` in `main.diagnostics`; overview, range, zoom, timeline, incidents, notifications, messages, close | navigation's handler | open | `centre.detail-closed` | needs `voice.diagnostics.open` on the presentation (diagnostics.json, coverage-right) | rail.main | done (targets) |
| VoiceDiagnosticsView.tsx range, zoom | proposed `voice.range {range}`, `voice.zoom {at?}` | `voice` parts range, zoom | still local `range`, `zoom` | open | — | — | rail.main | contract: the commands belong in diagnostics.json (coverage-right's); requested |
| VoiceDiagnosticsView.tsx incidents | — (display) | `voiceIncident` keyed event id; row | — | in range | — | — | rail.main | done |
| VoiceDiagnosticsView.tsx Save as JSON, quiet page | — (export file; quiet page) | `voice` parts | `save`, `setQuiet` | — | export; quiet | — | — | classify |
| SubsystemView.tsx head, checks, messages, × | `subsystem.close` (binding) | `subsystem` in `main.subsystem`; header, close, checks, messages; `subsystemCheck` keyed `<subsystem>/<check>`; row | navigation's handler | open | `centre.detail-closed` | needs `subsystem.open {subsystemId: $key}` (diagnostics.json, coverage-right) | rail.main | done (targets) |
| TeamServerView.tsx database, connection, service | `network.open {kind: server}`; × `network.close` (DetailHead binding) | `teamServer` in `main.network`; database, connection, service | — | always | `centre.detail-closed` | `network.open {kind: server}` | rail.main, right | done |
| NodeView.tsx facts, accounts, × | `network.open`, `network.close` | `node` in `main.network`; name, owner, version, presence, lastUpdate, human, cpu, memory, pace, coverage, remaining, accounts | `useUiTarget` | node listed | `centre.detail-closed` | `network.open` | rail.main, right | done |
| QueueItemView.tsx Back, Next item, Close, links to another item | `queue.back`, `queue.next`, `queue.close`, `queue.follow` (queue.ts; Back and Close by binding, Next and the links keep their own note and focus handling over the same bindings) | `queueThread` in `main.queue`; header, turn, back, next, close, composer | navigation's handlers | open | `queue.read-marked` (body shown), `queue.draft-saved` (leaving the editor), `queue.item-activated` (next) | needs `queue.open {threadId: $key}` (queue.json, coverage-right) | rail.main | done (targets) |
| QueueItemView.tsx a link within the item | — (scrolls to the message; the reveal does the same for a pointed-at message) | `queueMessage` keyed message; card, body, receipt | scroll | — | the body on screen sends its receipt | scrolled into view by the reveal | rail.main | done (see the read-receipt note) |
| QueueItemView.tsx turn, addressed, Mark unread, Put back, Done, Restore, Respond, Reply, Retry, Cancel send, Edit | — (domain: work commands, drafts, sends) | `queueThread` turn; `queueMessage` | messaging routes | — | change work state or drafts | — | rail.main | classify |
| QueueFiles.tsx download / reveal / remove | — (domain: files) | `queueAttachment` keyed attachment (on an accepted message); entry | `downloadFile`, `revealFile` | — | downloads | — | rail.main | classify (target done) |
| MessageComposer.tsx fields, Review, Discard | — (domain: drafts) | `queueThread` part composer | `edit`, `review`, `discard` | — | edits a draft | — | rail.main | classify (target done) |
| MessageReview.tsx capture buttons | — (domain: approval) | — (not a `capture` presentation; approval stays human) | capture actions | — | — | — | — | classify |

Known limits found while testing (pipeline geometry, not registration):

- A leaf runs from the object's right inner margin straight to the part,
  so in a horizontal row only the right-most part can be reached: pointing
  at the workflow's Decisions tab is `blocked / no-route`, its Events tab
  arrives. The same holds for the Folders tabs and phase steps left of the
  last.
- A part outside its object's box cannot be reached; the file preview is
  therefore the folder's part, not the entry's.

## Menu, Settings, reminder, footer (coverage-left-settings)

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| MenuControl.tsx ⋮ toggle | proposed `menu.toggle {open}` | `menu` in `overlay.controls`; toggle | local `open` | always | `menu.opened` | none | rail.overlay, left | contract |
| MenuControl.tsx Settings, Updates | proposed `settings.open {section?}` | `menu` in `overlay.menu`; settings, updates | `openSettings()`, `openSettings("updates")` | always | `settings.opened` | menu.toggle | rail.overlay | contract |
| MenuControl.tsx Restart Server | — (domain: `/api/restart`, excluded from the brain) | `menu` part restart | `restart` | — | restarts | — | — | classify |
| Settings.tsx nav buttons `goTo(section)` | proposed `settings.section {section}` | `settingsSection` keyed brains, voice, you, team, updates, system; heading, body | `goTo` scroll | settings open | — | settings.open | rail.overlay | contract |
| Settings.tsx close, backdrop | proposed `settings.close` | `settings` close, nav | `close` | open | — | — | rail.overlay | contract |
| Settings.tsx first-time setup steps | — (setup flow) | `settings` | `setup` state | no Fish key | — | — | — | classify |
| Settings.tsx Voice keys, Test, Save; You Save; Updates Check, Update | — (secrets and domain; excluded from the brain where they hold secrets) | `settingsSection` body | section handlers | — | saves settings / updates | — | — | classify |
| BrainsSection.tsx add, model, sign in, default, remove | — (provider configuration) | `settingsSection` brains body | `act(...)` | — | changes brains | — | — | classify |
| TeamSection.tsx connect, approval, cancel, disconnect, members, look up, add | — (sign-in and membership: existing authorisation paths) | `settingsSection` team body | `connect`, `change`, `resolve`, `add` | — | membership / sign-in | — | — | classify |
| TeamSignInReminder.tsx Sign in, Later | Later: proposed `reminder.later`; Sign in: — (browser approval flow) | `teamReminder` in `main.notice`; text, signIn, later | `signInAgain`, `later.set(true)` | while not signed in | Sign in opens Settings/approval | none | rail.main, right | contract / classify |
| Footer.tsx label | — (display) | `footer` in `footer.fixed`; label | none | always | — | none | rail.footer | contract |

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
