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
parity for the tile). Every left part is reached (final integration,
2026-11-01): a part beside another on the side its leaf comes from goes
over or under its row (`uiPipeline.ts` `leafEdge`); the microphone's row
keeps `--ui-leaf-lane` (8 px) below it for that.

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| OrbControl.tsx `#orb-control`, canvas, BrainLogo | — (display) | `orb` in `left.fixed`; canvas (the orb's box: the canvas fills it, whole-canvas only), logo | `useUiTarget` | always; logo while a brain with a logo runs | — | none | rail.left, left | done |
| StatusControl.tsx status text, Stop | — Stop: `data-ui-action="speech"` (hush) | `status`; text, stop | `useUiTarget`; Stop `stopSpeaking.current()` | text while there is one; stop only while speaking | hush stays its own action | none | rail.left, left | done (whole tested; text/stop parts live while shown) |
| MicControl.tsx health dot | `voice.diagnostics.open` (declared, navigation) via `bindingProps` | `mic`; meter, health, mute, off | was `openDiagnostics.set(true)` | dot only while input has a problem | `centre.view-shown` | none | rail.left, left | done (binding, target); health dot pointing not page-tested (needs an input fault fixture) |
| MicMeter.tsx / `#mic-silenced` | — (display) | `mic` part meter | `partRef` | always | — | none | rail.left | done |
| MuteButton.tsx, MicOffButton.tsx | — `data-ui-action="microphone"` | `mic` parts mute, off | `micMuted`, `micOff` stores | always | changes the microphone | none | rail.left | classify; parts reached under the row, in the lane the mic keeps below it (`--ui-leaf-lane`) |
| TeamServerTile.tsx tile | `network.open {kind: server}` via `bindingProps` | `teamServer` in `left.fixed`; readiness (the word), server, database, lastWrite | was `byHand("network.open")` | always | `centre.view-shown`, `centre.detail-replaced` | none | rail.left, left | done; lastWrite reached under its row |
| BrainUsage.tsx → WeeklyUsage.tsx each account | — (display only) | `brainAccount` keyed provider (the one named, else the figures', else the running brain); name (logo), remaining, pace, reset (burn-down) | `useAccountTarget` | while its provider is known (no target before) | — | none | rail.left, left | done |

## Right panel (coverage-right)

Branch `ui-nav-coverage-right`. Every right-panel target hangs from
`rail.right` and is entered on its right edge (`uiRight.RIGHT_SOCKET`). A
row's whole is its own button (`uiRight.row`), so its parts are reached
inside it; the panel-level targets' leaves run in the panel's
`--ui-corridor`. Tests: `tests/test_ui_right.py` (node parity for the Agents
list, and the static rule that the panel's controls bind marked commands)
and `tests/test_voice_page_right.py` (`pytest -m page`: by hand and by
request, hidden-target reveal on another tab and a later page, filter kept,
Queue row never opened).

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| FoldersPanel.tsx filter | `folders.panel.search {text?}` (no text clears it, as `folders.search` does), bound to the box (integration) | `foldersList` in `right.folders`: whole, search, open (registered) | module store `search` in FoldersPanel.tsx, emptied when the tab leaves the page (as before); changed only by the command | always | `list.filtered` | `right.tab.select folders` | rail.right, right | done |
| FoldersPanel.tsx row | `folders.open {folderId}` (navigation's handler), bound with `bindingProps` | `folder` keyed folder in `right.folders`: whole and row = the row button, name, availability | `folders.open` | folder in the catalog | `centre.view-shown` | tab select, then the right.folders revealer: catalog not read → `not-loaded`, not listed → `entity-gone`, hidden by his filter → `hidden` (his filter is never cleared) | rail.right, right | done |
| FoldersPanel.tsx Open Folders | `folders.open {folderId: selected}`, or `main.tab.select {tab: folders}` with none selected | `foldersList` part open | bound | always | `centre.view-shown` | tab select | rail.right, right | done |
| AgentsControl.tsx Active / All | `agents.filter {show}` (agents.json), handler in `uiRight.ts` | `agentsList` in `right.agents`: whole, filter, previous, next | `agentsShow` store (remembered as before), back to the top | always (also while the tab is hidden: the choice is remembered) | `list.filtered` | tab select | rail.right, right | done; the `filter` part is refused `no-route` until the pipeline's leaf stops counting the part's own buttons (requested) |
| AgentsControl.tsx chevrons, wheel and touch gestures | `agents.page {direction: previous/next}` | `agentsList` previous, next | `agentsFirst`, held in range by `firstShown`; a gesture step runs one command each | the list on the page and room that way, else `not-mounted` / `no-entity` | `list.paged` | tab select | rail.right, right | done |
| AgentsControl.tsx one agent's row brought into view | `agents.reveal {sessionId}`; the same `bringAgentIntoView` keeps a just-messaged agent in view for its ray | `agent` keyed session in `right.agents`: whole and row = the agent's box, orb and state = its orb canvas, name | `bringAgentIntoView` | the list on the page and the agent let through by the switch, else `not-mounted` / `no-entity` | `list.paged` | tab select, then the right.agents revealer reads the list if needed and pages to the row; one the Active switch hides → `hidden` (the switch is not changed) | rail.right, right | done; the `name` part sits behind the orb from the rail's side and is refused `no-route` (truthful; would need a leaf around the orb) |
| AgentsControl.tsx activation switch (`toggle(agent)`) | — recipient selection (not navigation) | none (inside the agent's row, outside its box) | `setAgentActive` | All view only | changes who a message goes to | — | — | classify |
| QueuePanel.tsx Waiting / Active rows | `queue.open {threadId}` (navigation's handler), bound | `queueThread` keyed thread in `right.queue`: whole and row = the row button, sender, subject, turn, receipt (while shown) | `queue.open` | thread listed | `queue.item-activated` (a waiting one), `centre.view-shown`, `queue.read-marked` (an unread body), `queue.draft-saved` (the item left) | tab select | rail.right, right | done; pointing at a row never opens, activates or reads it (page test) |
| QueuePanel.tsx unsent and dismissed rows | `queue.follow {threadId}`, bound | `queueThread` as above (an unsent draft whose item has come registers once, as the item) | `queue.follow` (keeps placement) | thread listed | `centre.view-shown`, `queue.read-marked`, `queue.draft-saved` | tab select, then the right.queue revealer: a dismissed item opens Show dismissed and reads pages until it is listed, within `revealWaitMs`; never opened | rail.right, right | done |
| QueuePanel.tsx Show / Hide dismissed, Show more | `queue.history {shown}`, `queue.history.more` (queue.json), handlers in QueuePanel.tsx (`showHistory` shared with the revealer) | `queuePanel` in `right.queue`: whole, waiting, active, history, more, new | `historyOpen` store (kept while the tab is switched, as before) | messaging offered, else `not-signed-in`; more only while shown with a next page | `list.paged` | tab select | rail.right, right | done |
| QueuePanel.tsx New, Restore | — (domain: drafts, restore) | `queuePanel` new | `startNew`, `restore` | — | creates a draft / restores | — | rail.right | classify |
| WorkflowsView.tsx Done (show finished) | `workflows.showDone {shown}`, bound to the toggle (integration) | `workflowsList` in `right.workflows`: whole, showDone | `showDone` store in WorkflowsView.tsx, collapsed when the tab leaves (as before); changed only by the command | while something is done | `list.filtered` | tab select | rail.right, right | done |
| WorkflowsView.tsx workflow and Done rows | `workflow.open {workflowId}` (navigation's handler), bound | `workflow` keyed workflow in `right.workflows`: whole and row = the row button, state = its orb (a Done row: the outcome dot), tasks | `workflow.open` | listed, or read first | `centre.view-shown`, `centre.detail-replaced` | tab select, then the right.workflows revealer: not read → `not-loaded`, not listed → `entity-gone`, under a collapsed Done → Done is opened, as `workflows.showDone` does (integration) | rail.right, right | done; `dismiss` part not registered (the × sits beside the row's whole; needs a pipeline leaf to a part beside its whole) |
| WorkflowsView.tsx subagent row | `subagent.open {sessionId, agentId}`, bound | `subagent`: **not registered** — its key `agent:<session>/<id>` fails the `subagent` key pattern; needs the kind keyed `keyOf [session, subagent]` (workflows.json, coverage-main) | `subagent.open` | while listed (LINGER_SEC) | as workflow.open | — | rail.right, right | command done; target contract |
| WorkflowsView.tsx Dismiss × | — (domain: `dismissWorkflow`) | — | `dismissWorkflow` | — | records a dismissal | — | — | classify |
| NetworkPanel.tsx / NetworkTile.tsx tile | `network.open {kind: node, nodeId}`, now bound with `bindingProps` | `node` keyed node in `right.network`; name, presence, human, cpu, memory, pace | `network.open` | node in snapshot, else `not-loaded`/`no-entity` | `centre.view-shown`, `centre.detail-replaced` | `right.tab.select network` | rail.right, right | done |
| SubsystemList.tsx row | `subsystem.open {subsystemId}` (navigation's handler; voice input opens voice diagnostics), bound; `openSubsystemRow` removed | `subsystem` keyed subsystem in `right.diagnostics`: whole and row = the row button, status | `subsystem.open` | listed, not `page` (its button is disabled; still a target) | `centre.view-shown`, `centre.detail-replaced`, `centre.detail-closed` | tab select | rail.right, right | done |
| VoiceSummary.tsx open | `voice.diagnostics.open`, bound | `voice` in `right.diagnostics`: the summary button is the whole, summary and open | `voice.diagnostics.open` | always | `centre.view-shown` | tab select | rail.right, right | done |
| main.* reveals declared in coverage-right's families | `queueThread` in `main.queue` → `queue.open {threadId: $key}`; `subsystem` in `main.subsystem` → `subsystem.open {subsystemId: $key}`; `voice` in `main.diagnostics` → `voice.diagnostics.open` | — | — | — | `queue.open`'s reveal reports its own effects (a waiting item becomes Active) | — | — | done (declarations; the centre targets are coverage-main's) |

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
| WorkflowDetail.tsx phases, tasks, progress rows | — (display) | `workflowPhase` / `workflowTask` keyed `<workflow>/<slug>`; row, state, progress (Phases, Tasks and Progress tabs) | — | on their tab | — | revealer: `workflow.open` then `workflow.tab.select` to the tab that draws it, from the key's workflow | rail.main | done (task card `progress` needs TaskBar to take a ref) |
| WorkflowDetail.tsx waits, blockers | — (display) | `workflowWait` / `workflowBlocker` keyed `<workflow>/<key>`; row | — | on their tab | — | as above | rail.main | done |
| WorkflowDetail.tsx decisions, options, Answer | — (domain: `_answer_workflow_decision`) | `workflowDecision` keyed `<workflow>/<key>`; row, options, answer | `answer(...)` | on its tab | answers | as above | rail.main | classify (target done) |
| WorkflowDetail.tsx Transcript, Load earlier | `workflow.transcript.earlier {sessionId, agentId?}` | `workflowTranscript` keyed workflow (list); `workflowAgent` keyed `<workflow>/<session>` (section, entries, earlier); a subagent's as `subagent` (transcript, earlier) | `earlierPages` keyed `<session>[/<agent>]` | that transcript shown with earlier entries | `list.paged` | as above | rail.main | done |
| WorkflowDetail.tsx events kind | `workflow.events.filter {workflowId, kind?}` | `workflowEvents` keyed workflow; list, filter | tab and kind store | that workflow open | `list.filtered` | as above | rail.main | done |
| WorkflowDetail.tsx Close, Dismiss | `workflow.close` (binding); Dismiss — (domain) | `workflow` close, dismiss | navigation's handler; `dismissWorkflow` | open | `centre.detail-closed` | — | rail.main | done / classify |
| WorkflowDetail.tsx subagent rows, SubagentDetail | `subagent.open {sessionId, agentId}`, `workflow.close` (bindings) | `subagent` keyed `<session>/<agent>` (`keyOf [session, subagent]`); row, state (Overview), header, state, close, transcript, earlier (detail) | navigation's handlers | listed | `centre.detail-replaced` | revealer: `subagent.open` from the key's two parts for the detail's parts; the row (inside whichever workflow lists it) is not revealed | rail.main | done |
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

- A leaf runs from the object's right inner margin straight to the part;
  where that would cross another part or a control (a part left of others
  in a row: the workflow's Decisions tab, the Folders tabs) it goes over
  or under the row instead and drops into the part's top or bottom edge
  (2026-11-01). A phase step left of the last is reached too (final
  integration): the next step's connector ignores the pointer, and a socket
  branch that would cross the next object in its row runs over or under the
  row inside the object holding it and drops into the object's top or
  bottom edge at its inner margin (`overTheRow`).
- A part outside its object's box cannot be reached; the file preview is
  therefore the folder's part, not the entry's.

## Menu, Settings, reminder, footer (coverage-left-settings)

Commands in `families/settings.json`, handlers next to their state in
`MenuControl.tsx`, `Settings.tsx`, `TeamSignInReminder.tsx`. Tested by
`tests/test_voice_page_left_settings.py` (parity: click versus the brain's
`ui_request` for the same command leaves the same page, with the declared
effects; the overlay map; Settings in the centre, every section, control
and reading in it reached along the main rail).

| Source / control | Command | Target / parts | Current handler / state | Availability | Side effects | Reveal | Socket | State |
|---|---|---|---|---|---|---|---|---|
| MenuControl.tsx ⋮ toggle, any click elsewhere | `menu.toggle {open}` | `menu` in `overlay.controls`; toggle (the button is the whole) | `menuOpen` (route state, read-only export) | always | `menu.opened` (when opening) | none | rail.right, right | done |
| MenuControl.tsx Settings, Check for updates | `settings.open {section?}` (`{}`, `{section: updates}`) | `menu` in `overlay.menu`; settings, updates, restart | handler closes the menu, shows the centre's `settings` detail | always (a section hidden by first-time setup: `hidden`) | `settings.opened`, `centre.view-shown` | `menu.toggle {open: true}` | rail.right, right | done (dropdown keeps its corridor clear, ui-left.css) |
| MenuControl.tsx Restart Server | — `data-ui-action="restart"` (excluded from the brain) | `menu` part restart | `restart` | — | restarts | — | — | classify |
| Settings.tsx section row | `settings.section {section}` | `settings` in `main.settings`, a part per section (you, voice, brains, team, updates, server); `settingsSection` keyed the same, heading, body | instant scroll | Settings open (`not-mounted`), section shown (`hidden` in setup) | `centre.view-shown` when Settings was not the view shown | `settings.open {section: $key}` | rail.main, right | done |
| Settings.tsx ×, Finish Setup | `settings.close` | `settings` in `main.settings`; close, nav, setup | ends setup too; back to Conversation | open (`no-entity`) | `centre.detail-closed` | `settings.open {}` | rail.main, right | done |
| Settings.tsx first-time setup open, Next | `settings.open {setup: true}` (useFirstRunSetup, as his navigation); Next `data-ui-action="setup"` | `settings` part setup | `setup` route state | no Fish key | `settings.opened`, `centre.view-shown` | — | — | done (open); classify (Next) |
| Settings.tsx You, Voice and language, Updates, Server controls and readings | — `input`, `domain`, `refresh`, `restart` | `settingsItem` keyed name, honorific, saveYou, language, spanishModel, voiceId, saveVoiceId, fishKey, testFishKey, saveFishKey, currentVersion, checkUpdates, installUpdate, serverPort, serverUptime; part label; `updateVersion` by commit (choose, ci) | section handlers | while shown | saves settings / updates | `settings.open {}` (`{section: updates}` for a version) | rail.main, right | done |
| BrainsSection.tsx add, model, sign in, default, remove | — `domain`, `sign-in` | `brainCard` by brain (model, checks, signIn, startWith, remove); `brainAdd` by brain | `act(...)` | while shown | changes brains | `settings.open {section: brains}` | rail.main, right | done |
| TeamSection.tsx connect, approval, cancel, disconnect, members, look up, add | — `sign-in`, `domain`, `input` | `settingsItem` teamState, teamAddress, teamSignIn, teamApproval, teamCancel, teamSignInAgain, teamDisconnect, teamLogin, teamLookUp, teamRole, teamAdd, teamAddCancel; `teamMember` by GitHub id (name, role, actions) | `connect`, `change`, `resolve`, `add` | while shown | membership / sign-in | `settings.open {}` (`{section: team}` for a member) | rail.main, right | done |
| TeamSignInReminder.tsx Later | `reminder.later` | `teamReminder` in `main.notice`; text, signIn, later | `later` route state | while not signed in and not hidden (`no-entity`) | — | none | rail.main, right | done; text and signIn reached under the row (left of Later) |
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

## Gaps left after the coverage merge (integrator)

- Unclassified controls: 22 left in `PENDING` (`tests/test_ui_coverage_rules.py`),
  all navigation that needs a registered command: FolderDetail's mode
  switches (Edit, Reconnect, Remove, Cancel, Keep it), FoldersView's Add
  form Cancel, MessageComposer's discard confirmation (Discard, Keep it),
  QueueItemView's related-message links (`jump`), Reply, Respond, Edit
  again and the leave confirmation (Discard reply, Keep working),
  QueuePanel's New message, VoiceDiagnosticsView's range and quiet toggle
  and the spark marks' zoom (VoiceVisuals), WorkflowDetail's transcript
  scroll. The other 51 were classified at the integration review
  (`data-ui-action`): `domain` (capture read/accept/delete/send, record
  restore, agent switch, folder replace/edit/remove/register/ignore/restore,
  draft keep-mine/take-theirs/discard/review, Queue turn, put back,
  addressed, unread, restore, done, retry, cancel send, workflow answer and
  dismiss), `input` (form fields, the typing box), `recipient` (the
  composer's recipient), `file` (attach, download, reveal, remove, save the
  voice record), `refresh` (a folder's Refresh).
- Parts beside a sibling, reached over or under their row since
  2026-11-01 (`leaf-sibling-routing`, approved): workflow Decisions tab
  and other tabs left of the last, Folders tabs, Team Server last write,
  reminder text and Sign in; the workflow row's Dismiss × (now the
  `dismiss` part) straight down the margin it straddles; since the final
  integration also microphone mute, off and the health dot, and phase
  steps left of the last (see "Final integration reach" below).
- Phases, tasks, open items, transcript, events and the subagent detail
  are revealed by `main.workflow` revealers that run the registered
  commands their key names (`uiNavigationReveal.ts` `RevealerContext.navigate`;
  page test `test_a_workflows_hidden_item_is_revealed_by_opening_it_on_its_tab`).
  Still no target on the task card's progress bar (`TaskBar`).
- Subagent rows in the right panel are not targets yet.
- Health dot: page-tested (`test_voice_page_voice_health.py`); while shown it is a named button and a mounted target, reached under the mic row.

## Final integration reach (2026-11-01)

Every target and part the contract declares (259, counting each kind's
whole once per view) pointed at with `ui_indicate` (reveal never; an
offscreen one again with reveal navigate) on the built page, in English
and Spanish, at 1440x900 and 1280x800, through 115 scenes per run (each right tab,
each workflow tab, folders lists, filters and every folder, the team
server and each node, voice diagnostics, each subsystem, the
conversation's record, a capture and sources, an agent's thread, a
workflow with tasks, a wait, a blocker, an attached agent and subagents,
the menu, each Settings section, the sign-in reminder, the Queue and each
thread). Driver `ui-nav-run/tools/test_reach_sweep.py`, results
`ui-nav-run/evidence/final-integration/`. The same in all four runs:

| Outcome | Count |
|---|---|
| Reached | 211 |
| Refused | 8: Settings' six (`blocked / occluded`: Settings covers the orb, by design); the right panel's agent `name` (`no-route`: its orb sits between the rail and the name, filling the row's height); a network tile's `presence` (`no-route`: the dot is in the tile's title, set on its top border, beside the name) |
| Mounted, part not on show in these fixtures | 26 (conditional: Stop while speaking, Load earlier, restore, a preview, a crumb, a composer, receipts, Dismiss on a finished workflow, Show done, the subagent detail's parts, the hidden previous chevron); most reached by their own page tests |
| Never registered | 14: `rightTab`, `mainTab` and `region` kinds (declared in the contract, registered by no component) and the `main.main` capture (the `?view=main` page, not swept) |

Fixed while sweeping, within the approved rules (more room, or a box that
is not drawn or holds the part no longer counted as a cover):
the status line's words (`occluded` by its own control), the agents
list's next chevron (the list keeps `--ui-corridor`), a folder row's name
(`--ui-leaf-lane` above it), a folder's contents (the detail keeps
`--ui-corridor`), a node's name past the close × (`--ui-leaf-lane` over
the title row, node and subsystem details), an agent's name in a thread
record past its status, a subagent's state dot in a workflow, the voice
view's notifications and the folders list (partly scrolled out: the
visible part's middle is what is checked), the hidden previous chevron
(`hidden`, no longer `occluded`, and never an obstacle).

## Localization coverage (integrator)

Every row above is worded through the catalogs in English and Spanish
(`frontend/src/messages/{shell,right,main,left}.{en,es}.ts`); the
`label-unlocalized` rule finds no unworded label in `frontend/src`. Command
and target identities, `data-ui-tab`, `data-row` and readiness codes stay
English; no element is found by its words. The acceptance run opened every
right tab, every centre family (each of the workflow detail's nine tabs) and
every Settings section by its registered command in both languages, and
the three pipeline demonstrations resolved the same targets and parts in
both (execution.md, "Localization integration"). Gaps: team server error
text and voice causes stay the server's English; two Spanish values are cut
by width; the `usted` / `tú` register differs between `left.*` and `main.*`;
brain discovery labels follow two rules (English in Settings, the language
in force elsewhere). Details in execution.md, "Localization gaps remaining".

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
