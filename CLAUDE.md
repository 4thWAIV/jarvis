# JARVIS — Voice AI Assistant

## Overview
JARVIS is a voice-first macOS workspace for conversation, coding agents and workflows. Its React interface centres on Conversation, with agent threads, workflow details and voice diagnostics opening alongside it. Claude and Codex are available as conversational brain providers. The project direction is in `docs/roadmap.md`; current user-facing changes are in `docs/whats-new.md`. Keep planned successor engines distinct from implemented prototype functionality.

## Quick Start
Read `jarvis/skills/jarvis-setup/SKILL.md` first — it carries setup and debugging
facts (Chrome for the voice page, the per-port permission trap, subscription vs. API
key, what an expired login sounds like, Accessibility) that were only learned
by hitting them live, and this walkthrough assumes them.

When a user clones this repo and starts Claude Code, help them:
1. Copy jarvis/.env.example to .env
2. Install Claude Code (`npm install -g @anthropic-ai/claude-code`, 2.1.224 or
   newer) and log in with `claude` — JARVIS's brain runs on your Claude
   subscription, not on an API key
3. Get a Fish Audio API key from fish.audio (required — there is no fallback
   voice)
4. Make the virtual environment and install Python dependencies:
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
5. Install the Playwright browser: .venv/bin/python -m playwright install chromium
   (`read_page` / `look_at_page` need it)
5a. Install the current local Kroko speech model:
   `.venv/bin/python scripts/setup_speech_input.py` (English; `--language es`
   adds the Spanish model, `--language all` both)
6. Install frontend dependencies: cd frontend && npm install
7. Generate the SSL certs — see below, they are NOT optional:
   `openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'`
8. Run the backend: .venv/bin/python -m jarvis --host 127.0.0.1
9. Run the frontend: cd frontend && npm run dev
10. Open Chrome (the voice page is only tested in Chrome) to http://localhost:5173
11. Click to enable audio, speak to JARVIS
12. Teach your own Claude Code and Codex sessions to reach you through
    JARVIS: `.venv/bin/python -m jarvis.session_skill install`

**The certs are required for the dev-server workflow**, despite `server.py`
treating them as optional. JARVIS serves HTTPS only when `cert.pem` and
`key.pem` are both in its configuration directory (the repository root in a checkout), but `frontend/vite.config.ts` hard-codes its
proxy target as `https://localhost:8340`. Measured with a stand-in backend on
8340 and `npx vite`: with a plain-HTTP backend the Vite dev server itself
answers 200 while every proxied `/api` request returns **500**; with an HTTPS
backend the same request returns 200. The page loads either way, which is why
this presents as "the UI is up but nothing works" rather than as an error.

## Architecture
- **Backend**: FastAPI + Python, packaged under `jarvis/`; `python -m jarvis` starts the service. Python module names below refer to files in that package. A checkout keeps configuration and data at the repository root; an installed copy uses `~/Library/Application Support/JARVIS`.
- **Frontend**: React + TypeScript + Vite. Conversation and detailed agent, workflow and diagnostic views occupy the centre; the team server's health and brain usage are on the left, the microphone control, Stop and the voice health dot at the lower right of the Conversation's message input (on every other centre view the one microphone control and health dot sit in the centre area's lower-right corner, `MicControl.tsx` `MicCorner`, contract view `main.corner`); Folders, Agents, Queue, Work, Network and Diagnostics are on the right, as six labelled tabs in two rows. The Three.js orb is one control within that workspace.
- **Communication**: REST and WebSockets. Recognition receives binary PCM; voice output uses JSON with base64 MP3 chunks.
- **AI**: conversational sessions use `brain_provider.py`, with Claude Code and Codex implementations. Development runs use their own Claude execution path.
- **TTS**: Fish Audio with JARVIS voice model
- **System**: AppleScript for Terminal and Chrome integration
- **Runs**: every Claude Code execution goes through one recorded pipeline —
  `run_store.py` (SQLite) + `run_executor.py`, surfaced at `/api/runs`,
  `/ws/runs`; the React workspace is the focus of user-interface development
- **Internal tool channel**: the brain reaches JARVIS's tools (session
  listing, steering, etc.) via a stdio MCP child that forwards
  `tools/call` to `POST /internal/tool` over loopback HTTP, bearing the
  token from `data_paths.ensure_tool_token()`.
- **The web boundary** (`web_auth.py`): one ASGI gate above the router.
  Every WebSocket handshake and every state-changing request must carry
  either an `Origin` JARVIS serves from (localhost/127.0.0.1/[::1] on the
  API port or Vite's) or that same tool token. The browser needs no setup —
  it is same-origin through Vite's proxy, so it sends the `Origin` itself.
  There is no CORS: same-origin needs none, and sending none is what stops
  a hostile page reading the GETs that cannot be gated (a same-origin GET
  carries no `Origin` to check). The `Host` header IS checked on every
  request including reads, because DNS rebinding is the way around "no
  CORS". `--host` defaults to `127.0.0.1`; `0.0.0.0` still works and warns,
  and then needs `JARVIS_ALLOWED_ORIGINS`.
  An agent the user lets do one kind of thing carries an access key
  instead of the token (`access_keys.py`): it opens only the routes of its
  categories (`captures`, `agents`, `runs`, `workflows`, `work`, `watch`), never the voice
  socket, settings or `/internal/tool`. `python -m jarvis.access_keys create
  <label> <category>...` prints the key once; only its digest is stored.

## Product direction

`docs/roadmap.md` is the shared product roadmap; keep priorities and status distinctions there rather than duplicating them in implementation notes. The new React workspace is the primary user interface. Denice and its successor engine stages are future product work, not implemented backends to describe as available today.

## Clearing the conversation
JARVIS never clears, rotates or replaces its conversation without the user's
yes. Clearing takes two turns: the whole utterance must be exactly
`start fresh` (lowercased, punctuation stripped, optional leading "jarvis";
no substring matching), which only makes JARVIS ask "Clear the whole
conversation, sir? Say yes to confirm."; only an unambiguous yes in the very
next utterance (`_rotation_answer`) clears. Anything else, or a timeout,
cancels and says nothing was cleared. Context rotation likewise waits for a
yes to its own question. Every clear or rotation is logged with its trigger
and the exact utterance. The one involuntary replacement is the crash/stall
restart in `brain.py`, which is recovery, not a decision, and is logged.
In Spanish the one clear command is exactly `empezar de cero`, under the
same rules.

## Spoken words
Every matcher of what the user said against fixed words splits and folds it
with `spoken_words.py`: the yes and no to clearing and compaction
(`server._rotation_answer`, `ROTATION_YES_MAX_WORDS` still 4) and the same
yes to a read-back (`server._approve_on_his_yes`), the clear
command, the cancel and replay words and echo rejection (`speech._tokens`),
agent and people names (`agent_directory.spoken_key`, folded, and `_words`) and memory search and slugs
(`jarvis_memory`). Its splitter keeps accented letters ("sí" is a word, not
"s") and `fold` takes accents off before comparing; on ASCII text both give
exactly what the old `[a-z0-9]` splitters gave. Each language's word lists
are one `Vocabulary` there, English and Latin American Spanish (yes: sí, si,
claro, adelante, hazlo, dale, "de acuerdo"; no: no, espera, luego, después,
nunca, negativo, todavía; cancel: espera, espérate, cancela, alto, detente,
never "para"; replay: repite, otra vez). Which list counts is
`spoken_words.active_language()`, the one place the language is decided;
only `language_setting` sets it (see "The language setting"). Every test
starts in English (`conftest._english_until_a_test_chooses`) and switches
with the `spanish` fixture.

## Sending on his yes
A capture (a message to a session, or the review of a message to a person)
is sent only once he has approved exactly its text for exactly its
recipient, and the brain's tool call alone never approves it
(`transmission.accepted`, checked by `server._capture_to_send` for
`steer_session` and `send_message`). The recipient is an identity, not a
spoken name (`transmission.recipient`): the agent's session id, settled when
`capture` is given `to` (`capture["to_session"]`), or the person's GitHub id
from the reviewed revision (`capture["human"]`). A capture addressed to no
one is neither read back nor accepted (`server._unaddressed`). Every
read-back names who it is for: a session's opens with "To chitauri." (a
`lead` chunk, heard as part of the reading, which the page does not light on
the box; `captureLead` on its frame), and a person's text already opens "To
<name>." (`messaging_ports.human_readback`). The brain reads a capture back
as soon as it is addressed, without asking whether he wants it read back.
Approved means one of two things. Either it was heard in full since its text
or its recipient last changed (`read_capture` or the box's Read, played and
not skipped or cut short; or Accept on screen), recording the recipient it
named (`read_to`), and then the very next thing he said, spoken or typed,
was a plain yes by the clear command's rule (`_rotation_answer`) or an
explicit instruction to send (`server._send_instruction`: "send it", "send it
to chitauri", "envíalo", at most `SEND_ANSWER_MAX_WORDS`, no word of refusal
or delay; the phrases are each `Vocabulary`'s `send_phrases`), on its way
to the brain as his own turn: every utterance closes the read-backs waiting
for an answer (`server._take_read_back_answers`) and only that yes approves
them (`server._approve_on_his_yes`), which tells the brain in a note. "Send
it to <name>" approves a capture only when <name> resolves to exactly its
recipient (`server._names_recipient`: the agent directory for a session,
`message_approvals.github_ids_called` for a person); for any other it
approves nothing and the note says why. A yes in the words that started the
turn that read it back is no answer to it, and a yes taken by another
question (clearing, compaction) approves nothing. Or he pressed Send on its
box (`transmission.approve`). `steer_session` to any session other than the
capture's recipient is refused, telling the brain to address it there and
read it back naming the new recipient. Any change of its text or of its
recipient, a new read-back, and a send of it that did not go (cancelled in
the window, failed) each clear the approval, so it is read back and approved
again. The refusal tells the brain to ask and wait for his yes, or to read it
back again.

## Messages to sessions
Every message JARVIS sends a session (`steer_session`) is one row of the
`messages` table in `run_store.py`, written when it is staged and BEFORE it is
sent, then updated at each step; its status is `run_store.MessageStatus`
(`staged`, `queued`, `cancelled`, `sent`, `delivered`, `working`, `answered`,
`failed`). Messages go one per turn: one to a session that is mid-turn, or
that has an earlier message queued or still open, is `queued` on record
instead of sent (`server._message_must_wait`), and the session watcher sends
each session's oldest queued message once it is between turns and has
answered the one before (`SessionWatcher.send_queued`). Sent mid-turn, Claude
Code would fold it into the turn under way and its one answer would become
the reply to both. Queued messages survive a restart. `sent` only means the provider took it (bytes on the inbox socket,
or `codex queue` exiting 0). The session watcher (`SessionWatcher`, given the
store as `messages`) follows every open message on every poll through its
session's provider: `track(session, message) -> Tracking` finds it in the
transcript by the text after the signature (`session_steer.STEER_SIGNATURE`)
and its staged time, and reports when it arrived, when the turn that took it
began and ended, and that turn's final answer, which is stored as `reply`.
Not seen within `session_watch.DELIVERY_TIMEOUT_SEC` (60s) it becomes
`undelivered` (an attention item, see "Attention"), but it is still
looked for: arriving late (behind a permission prompt he then approves) it is
delivered and answered as usual. Anything unresolved
`session_watch.MESSAGE_WATCH_SEC` (a day) after it was sent is failed, so the
watcher only ever looks for a bounded set; settled messages are never read
again. Each step that wants him (undelivered, answered, and a send that
failed) moves the message's one attention item to its next revision;
queued, sent and delivered are only progress, kept in the record and the
agent's thread, and resolve the item instead (`server._MESSAGE_PROGRESS`),
since what it last said (not picked up) is no longer so. Nothing is spoken
of any step.
The record is written before each
event, and the open messages are read from the store each poll, so a restart
follows on from where it stopped. The brain reads the record with
`list_messages` and a stored answer with `read_message_reply`;
`read_session_reply` still reads whatever a session said last. Keystrokes
(`answer_dialog`), Terminal commands and refused attempts that never formed a
message (no session, no capture) stay in the `steers` table.

## Messages from sessions
A session starts a conversation with the user by dropping one Markdown file per
message into `data/messages/<project>/` (`data_paths.session_messages_dir()`):
first line `# <subject>`, the rest the message. `session_messages.collect`
takes each file once it has not changed for `SETTLE_SEC`, never through a
symlink. The server (`_watch_session_messages`) writes it to the
`session_messages` table in `run_store.py`, THEN deletes the file (the
session's sign it was picked up), then records an attention item naming the
agent and nothing of its text. The agent is every listed agent in that project
(`_project_owner`, shared with the needs board). The brain reads the record
with `session_messages` and one message with `read_session_message`, which
marks it read; both taint the turn. Sessions learn both folders from the
talk-to-jarvis skill (`session_skills/talk-to-jarvis/SKILL.md`, outside
`skills/` because that folder is copied into the brain's own home).
A need tied to a work item (a decision, a blocker or a question about it)
goes on that work item (`work_report.py decision`, a blocked piece or
update), never on the needs board; the board is only for needs tied to no
item. The skill says so, and the brain, asked what needs him, reads the
work items' open decisions and blockers first, then the needs board.
`python -m jarvis.session_skill install` renders it with this install's data paths
into every Claude Code config root and the user's Codex home; preflight's
`session_skill` check only reports whether it is there and current.

## The workflow monitor
A long workflow (a phased build, a run of several agents) reports every
step to one durable record, `workflow_store.py`: `workflow_events` is
append-only and is the truth, and the workflow's status, its phases and
tasks, and its open waits, decisions and blockers are updated in the same
transaction as each event, by `record`, the only writer. Going quiet
(running, with no event for `SILENT_AFTER_SEC`; a workflow that has said it
is waiting is waiting, not quiet) is worked out when it is read, never
stored. Sessions report with `workflow_report.py` and an access key in the
`workflows` category (the talk-to-jarvis skill teaches them);
`POST /api/workflows` and `/api/workflows/{id}/events` take the reports, the
page reads `GET /api/workflows*`, and a `workflows` hint on `/ws/sessions`
says when to read again. A new phase, a decision needed, a blocker, a finish
and going quiet are each an attention item on the workflow's one item. The brain answers anything
about a workflow from `workflow_status`; open decisions are added to the
needs board (`with_decisions`) without writing the sessions' board files,
and he answers them on the Workflows view or by voice: the page's answer and
Dismiss routes and the brain's acting `answer_workflow_decision` and
`dismiss_workflow` share `_answer_workflow_decision` and `_dismiss_workflow`,
which record each as done "on the page" or "by voice".

## The Work tracker
The right panel's Work tab (the tab that was Workflows, id still
`workflows`) lists each piece of work in progress: its title, its state
(`planned`, `working`, `verifying`, `waiting_on_tim`, `blocked`, `done`;
`verifying` is between working and done) and one useful update, an open
decision's question first (Planning's Work view at 52a1837, bare minimum).
Items are listed under their group (`work_store.grouped`, the API's
`groups`): the optional `area` an item is given (`start --area`, `update
--area`, an empty one clears it), else its source folder (the folder, or
the folder a zip is in), headed by its last name. An item blocked or waiting
on Tim, or with a piece that is (`blocked_pieces`, `waiting_pieces`, at read
time), has a red or amber edge and dot in its row (`work.itemMark`,
`WorkMarkDot`); a piece carries the same mark in the detail. Each piece keeps its latest note (the summary of the last report
about it; migration 2 fills it from the log), an optional `worker` label
(the reporting agent's own words for who did it, e.g. a coordinator's
subagent, shown quoted and italic beside the JARVIS-verified owner, never in
its place, and walled for the brain), the pieces it comes after (`--after
key[,key]`: pieces of the same item, never itself or a circle; `waits_for`
the ones not done) and its evidence (`--evidence`, repeatable, added to
what it has: a commit, an absolute path or an http(s) URL, told apart by
shape and never opened). The detail lists the pieces in dependency order,
numbered, a URL as a link and a commit or path with Copy.
The detail is tabbed (`work.tab.select`; `work.open` takes a `tab`):
Overview (update, source, area, who), Decisions (open ones answered there,
then the decided ones), Blocking (`blockers`, worked out at read time in
`work_store.BLOCKER_KINDS` order: his decisions, the item blocked, blocked
pieces, pieces waiting on him, pieces waiting on others), Pieces and
Reports. While a decision is open a "Decision needed" banner above every
tab opens Decisions, the Decisions tab is counted, and the list row counts
the decisions beside its state; the row's third line is the first blocker
that is not a decision, with how many more. A decision may name a page
package (`decision --package <id or name>`); the Decisions tab's "Open the
package" posts to `/api/packages/<id>/page`, the same `_open_package_page`
as the brain's `open_package_page`, on his click. A decision is answered by
Tim alone; its asker alone may withdraw it (`work_report.py withdraw <id>
<key> "<reason>"`, kind `decision_withdrawn`): the route records the asker
as its access key's label (`work_api.asker`, `web_auth.access_key_label`),
never what the report says, refuses a page's withdrawal, and a decision
asked before askers were recorded may be withdrawn by any reporter. A
withdrawn decision is no longer open or counted, and is kept beside the
answered ones (`answered`, `where` = `withdrawn`). An item closed
(`work_report.py close`, kind `closed`, `closed_at`; nothing more is
reported to it and it is never deleted) or done for `DONE_LINGER_SEC` (five
minutes) moves to History: `GET /api/work`'s `history`, newest first, under
the list's History toggle (`work.showHistory`), each still opening in full;
`work_status` with `history` lists them and finds one by id or title. The Workflows view stays below the list, under its
own heading. The record is `work_store.py` (schema `work_schema`, through
`run_store.migrate`): `work_events` is append-only and the truth;
`work_items`, `work_pieces` and `work_decisions` are updated in the same
transaction by `create` and `record`, the only writers. An item wraps a
ZIP work package or a folder on this Mac, checked by a stat and never
opened or run; each piece names exactly who is on it (`agent:<session id>`
shown by its one name from the agent directory, `run:<id>`,
`workflow:<id>` or `tim`), and a reported who must be a record JARVIS has.
An open decision reads as waiting on Tim and a working or verifying item
silent for `QUIET_AFTER_SEC` as "no recent update", both at read time. Agents report
with `work_report.py` and an access key in the `work` category (the
talk-to-jarvis skill teaches it); `work_api.py` holds `/api/work*`, and
every change is a `work` hint on `/ws/sessions`, after the write; a
decision, blocked and done are attention items, each resolved once it is no
longer so (no decision open, no longer blocked or done:
`server._work_news_stale`). A decision's key is asked once: an answered key
is refused, so `work_report.py answer` reads the answer to that question. A
row's pill is always the state, and a quiet item says so as its update. A
read names the agents on its pieces from one directory read
(`work_api.WhoNames`, `agent_directory.names_by_session`), and the latest
events are `work_store.latest`. A row opens the item in the
centre (`WorkDetail.tsx`, the `work` detail slot, `work.open` /
`work.close`) and the list stays in view. His answer is
`work_api.answer_decision`, from the page and the brain's acting
`answer_work_decision` alike; `work_status` reads the record, walled (with everything above):
the list by group with each item's blocked and waiting pieces, and an item's
pieces in order with their notes, worker labels, what they wait for and
their evidence. The page changes nothing but his answers, so there is no
other acting tool.
Each Work row and piece, and each phase and task of a workflow's detail,
shows its live activity from the record (`nodeActivity.tsx`): working (a
working or verifying piece or item, a running workflow item, whose
read-time `quiet_at` is still ahead: `work_store.quiet_at` from its latest
note or update, `QUIET_AFTER_SEC`; a workflow item's from its latest event,
`heard_at`, `SILENT_AFTER_SEC`) has a glow travelling along its edge; past
`quiet_at` it is paused, dimmed and still and marked "Paused"; blocked is
still red, waiting on him still amber (a running workflow item while its
workflow needs him), done still with a check. Each report that arrives
while a view shows a node, read after a `work` or `workflows` hint, flashes
it once (`useArrivalFlash`, against when that view began to show); a
piece's "waits for" line flows while a piece it waits for is working. The
page's one timer is for the soonest `quiet_at` ahead, so nothing redraws
while nothing works. Reduced motion and Steady (`data-motion="steady"` on
the document, which no setting sets yet) keep the looks and drop the
motion and the flash.

## The team server
`team_server/` is a separate service, not part of the JARVIS package: its own
pinned `requirements.txt`, `Dockerfile`, tests (`team_server/tests`, against a
real MongoDB) and API contract (`team_server/openapi.json`, checked by its
tests). It gives one team GitHub sign-in, administrator-managed membership,
device pairing, status, version and health, and holds the team's network
inventory and each node's presence (see "The Network view"), and messages
between members (see "Messages between people"), which are always on: its
`/version` always offers `messaging.v1` and there is no switch (a
`team_settings` document left from the old switch is not read). Operating
it is `docs/team-server.md`. JARVIS's side is
`team_client.py` (the connection, its 30-second status poll and the
Keychain store, through `security` with the secret on stdin) and
`team_api.py` (`/api/team*`, the Settings Team section's routes). The
member routes and the brain's team tools call the same functions
(`team_api.members_call`, `resolve_call`, `change_call`, `revoke_call`):
`team_status` reads the status and, for an administrator, the roster;
`find_github_user` is Look up; the acting `change_team_member` is Add and
each member's buttons. The session credential lives only in the Keychain; the saved connection
(`data_paths.team_connection_path()`) holds the URL, the pinned server ID and
non-secret status. Nothing on a voice or agent path waits for the team
server. Tests never touch the Keychain (`conftest._never_touch_the_real_keychain`).
Staging alone has a test sign-in (`team_server/teamserver/staging_sessions.py`,
`POST /api/v1/test/device-sessions`): with the test key as bearer it gives an
ordinary device session to the synthetic member `test-a` or `test-b`
(GitHub IDs -1 and -2, never an administrator), so a workflow can pair test
backends with no GitHub login and no Approve click. The route exists only when
`RAILWAY_ENVIRONMENT_NAME` is `staging` and `TEST_SESSION_KEY_SHA256` is set;
the server refuses to start with that digest anywhere else, a wrong or missing
key gets the same 404 as no route, and it is not in `openapi.json`. The key
itself is only in this Mac's Keychain; rotating or removing it is in
`docs/team-server.md`.

## Messages between people
There is no switch: messages between people work whenever this JARVIS is
signed in to a team server that offers `messaging.v1`
(`messaging_ports.availability` fails closed: not signed in, then a server
that does not offer it). The runtime follows the team connection
(`MessagingRuntime.follow_once`, every `FOLLOW_EVERY_SEC`): it starts its
loops once signed in to such a server and stops them once signed out.
Signed out, Settings, Team says in one line that signing in turns messaging
on. The old `human_switch` table is kept and nothing reads it. A JARVIS
trusts the capabilities it read for `CAPABILITIES_TTL_SEC` (30 s). A message to a
person goes the agents' way (`transmission.py`): drafted (the page or the
brain's `draft_message`), reviewed on a capture of its own whose text is
the read-back (`message_approvals.Reviews`), accepted with Send or with
his yes after the read-back (`send_message`; see "Sending on his yes"), staged and offered the cancel window; the
hand-over is the owner's approval (`MessageApprovals.approve`). Nothing
reaches the team server's send route except through `GuardedTransport`,
which releases only the stored approved envelope of a send already on
record as `submitting`; an edit after review makes a new revision that
must be reviewed again. `messaging_runtime.py` builds the one store
(`messaging_store.py`), file snapshots (`messaging_files.py`), guard,
delivery loop (`messaging_client.py`), transfer presentation
(`transfer_presentation.py`, the `human_transfer` frame) and arrival
announcer (`arrival_announcer.py`: each arrival an attention item, its
presentation lease claimed only while he is engaged here) at startup,
and mounts `/api/team/messages*` (`messaging_api.py`). The team server's
side is `team_server/teamserver/messaging.py` and `attachments.py`; the
page's is the Queue tab (`QueuePanel.tsx`, `QueueItemView.tsx`,
`queue.ts`, `queueStore.ts`). The wire contract is
`team_server/teamserver/messaging_models.py`, copied (with a generated
header) into `jarvis/messaging_wire.py`, and its types generated into
`frontend/src/messagingContract.ts`, by
`scripts/generate_messaging_contract.py` (`--check` lists stale copies).
`tests/test_messaging_integration.py` runs the end-to-end scenarios
against a real local team server and needs TEAM_SERVER_TEST_MONGODB_URI.
The brain reads and moves the Queue through the same `LocalMessaging` the
routes use: `people` (the recipients read, which also fills the roster
`draft_message` names people from), `people_messages` (the snapshot, or its
history), `read_people_thread` (one item; no read receipt), and the acting
`mark_people_message_read` (`read`), `set_people_thread` (the work
command) and `change_people_draft` (discard, cancel, retry). A refusal is worded by `messaging_api.Refused.shown`; names,
subjects and bodies come back in an untrusted block.

## Received file locations
Where a file a person sent is on this Mac has one answer,
`attachment_locations.py`: `AttachmentLocations` over the messaging-files
service (its record and its downloads folder), by message and attachment
id for the owner signed in, never by name. `lookup` reads only; its
`AttachmentLocation` has `local_path` only for a file here whose size and
SHA-256 are the manifest's now, and otherwise its state (`remote_only`,
`downloading`, `missing`, `changed`, `moved`, `failed`) with `intended_path`,
which is never a saved file. `ensure_local` fetches through the existing
downloader and verifies again, on the owner's word; nothing is unpacked,
opened or run. Its table `attachment_locations` keeps where each file was
verified (how a changed downloads folder reads as `moved`) and, when a work
package was unpacked from it, the zip's hash, the folder and the members
its manifest names; `attachment_leases` keeps a file an agent holds (a run
while active, a message to a session while open) from `FileSnapshots.cleanup`.
`GET`/`POST /api/team/messages/{m}/files/{a}/location`, the brain's
`attachment_location` and the acting `make_attachment_local` and
`reveal_attachment` give the same JSON (name and path in the untrusted
block); the Queue shows a downloaded file's path with Copy and Reveal
(`QueueFileLocation.tsx`, `attachmentLocation.ts`). Handing files to an agent
(`spawn_run`'s and `steer_session`'s `attachments`, and a package's agent,
given the zip it came from) goes through `handoff`: the verified path where
that agent can read it (a run with permissions skipped, or a path inside its
folder), else a verified copy in its folder under `.jarvis-attachments/<id>/`
with where it came from, else nothing is started or sent and the reason is
said; an agent on another node gets only the message and attachment ids.

## Work packages
A teammate sends runnable work as a zip with `package.json` at its root,
attached to an ordinary message between people (format:
`docs/work-packages.md`). Every received zip is read through one module,
`bundle_archive.py` (`open_archive`, `list_members`, `read_member`,
`extract`): every entry checked before anything is written (absolute, drive
or network paths, backslashes, `..`, links, devices, encrypted entries, two
entries landing at one path by case or Unicode form, a file where a folder
is needed), entry, depth, per-file, total and compression-ratio budgets
counted on what is actually decompressed, macOS metadata (`__MACOSX`,
`.DS_Store`) set aside, the one wrapper folder found, and extraction into a
private staging folder renamed into place whole. It never runs anything,
clears the quarantine mark or keeps execute bits unless asked
(`keep_modes`). `work_packages.recognise` finds `package.json` at the
zip's root or in its one wrapper folder, every path it names relative to
that folder; several candidates, a malformed one or another project's (an
npm `package.json` has none of the JARVIS fields) say why it is not a
package. `work_packages.py` unpacks it into
`data_paths.packages_dir()/<name>-<version>-<id>/` (the wrapper's contents),
and removes the quarantine mark. Opening the same bytes again, from any
attachment, gives the package already there: `packages_dir()/.records/`
holds each package's SHA-256 and attachment ids (`Package.sources`, which
the Queue's Open page matches on), and a folder from before the record is
adopted by its name.
There is no other sandbox: the team is trusted. The brain's `open_package`
and `run_package_command` are ACTING_TOOLS, so the person's yes in that turn
is the gate. The page's Open package (on a received zip in the Queue, `POST
/api/packages/received`) is his own click, and unpacks through the same
`_open_received_package` as `open_package`; a page package then opens at
once, and its file has Open page from then on. A package zip that is a
file on this Mac opens the same way through `_open_local_package`: the
acting `open_local_package` (a path) and the Folders view's Open package on
a .zip's preview (`POST /api/packages/local`). The path is read by
`draft_files.Source` (the folder catalog's ordinary-file rule, no link
followed, a received file's size limit), copied privately and unpacked by
`work_packages.unpack_held` exactly as a received zip (recorded under
`local<sha>` in place of an attachment id), and a page package is shown at
once when he asked. A package reaches his screen only on his own act: his
click on a page JARVIS serves (`packages_api.his_click`: admitted by its
`Origin`, `web_auth.BY_ORIGIN`) or a brain tool on a turn he drove (the
ACTING_TOOLS). A caller on the tool token or an access key may unpack a
package but never show it: `POST /api/packages/local` unpacks and answers
`packages_api.NOT_HIS_ACT` as its `problem` (`_open_local_package(path,
show)`), and `POST /api/packages/<id>/page` and a story told (`POST
/api/packages/<id>/story`, any action but stop) are refused with 403.
The page package shown is held by the server (`_package_shown`) and
sent to every page that connects, so a reload keeps it; its × (`DELETE
/api/packages/<id>/page`) forgets it. `run_package_command` runs one shell step in the folder with a
10-minute timeout, or in a Terminal window through `actions.open_terminal`.
`list_packages` and `package_results` read what is there: `list_packages`
pages every package here, newest first, and with `name` keeps those that
fit best the words he called one by (`work_packages.matching`: a word counts
for less the more packages have it, and twice in an id or name). Every tool
that takes a package (`work_packages.find`) takes its id, its name and
version ("denice-pulses-1.0.1"), its name (the highest version), or his
words, which open the one package they fit or name each one they fit. Results go back
through `draft_message`'s `files`, which attaches each file with the page's
own snapshot step (`FileSnapshots.add_to_draft`), so review and approval are
unchanged. A package runs one of three ways, package.json's `runs`
(`work_packages.MODES`): `conversation` (the default, as above: the brain
follows its `skill`), `agent` and `page`. An agent package's `brief` is
carried out by one run through `RunExecutor` in its folder, started by the
acting `run_package_agent` and recorded in `runs.package_id` (added by
`run_store.init_db`'s column backfill; one active run per package, and a
retry keeps it); `_on_run_event` hands its end to `_attend_package_run`, an
attention item; the brain calls `package_results` and `draft_message` once
he agrees. A page package's
`entry` opens in the centre area (`PackageView.tsx`) in an iframe
`sandbox="allow-scripts"`, never `allow-same-origin`: its files
(`packages_api.py`, `GET /api/packages/<id>/files/<path>`, read by
`work_packages.page_file` through `folder_readers` with no link followed)
are served from JARVIS's own origin, so web_auth gives exactly those paths
`frame-ancestors 'self'; sandbox allow-scripts` and `SAMEORIGIN`
(`PACKAGE_PAGE_HEADERS`) in place of DENY. The frame asks the page by
postMessage (`packageBridge.ts`): `save` writes `results/<name>`
(`work_packages.save_result`, plain names, 20 MB, through `POST
/api/packages/<id>/results`), and `listen` streams the page's own
microphone frames (`voiceInput.listenToMicrophone`), because Chrome refuses
`getUserMedia` to a frame without an origin even with `allow="microphone"`.
The acting `open_package_page` and the page's Open page (on the package's
file in the Queue, `POST /api/packages/<id>/page`) share
`_open_package_page`, which sends every page a `package_page` frame on the
voice socket; unpacking one sends a `packages` hint on `/ws/sessions`. A
page package may name a `story` (`package_story.py`, checked on unpack):
slides of beats, each the words JARVIS says and the selectors it rings.
The page's Tell the story, Continue and Stop (`POST /api/packages/<id>/story`)
and the acting `tell_package_story` (tell, resume, stop, status) share
`_package_story` and `story_teller.StoryTeller`, which opens the page and
speaks the slides through `SpeechScheduler.narrate`, one NORMAL reply each.
A story may name its `answers` file and slides that `wait` for one question
(`asks_when` for a child question): such a slide is told, then the story is
WAITING, as running state the server knows (which question), until the
answers file has a new answer (a Choose saved through the bridge, or
`scripts/decide.py`), and goes on once `conversation_floor` is idle; an
answered question is passed over unless told from its own slide; `resume`
starts at the first with no answer. His speaking over a slide PAUSES it
(on a waiting slide it waits), never ends it, so Stop always finds it. Stop
ends it told, paused or waiting: the page's Stop, the tool, `_stop_speaking`
(hush), a whole utterance of `Vocabulary.stop_words` (checked after
`user_final`, before the brain hears it) and closing its page; a second
stop says it is already stopped (`StoryTeller.ended`), never "no story".
Each chunk carries its beat as `cue` on its `audio` frame (the way
`capture` rides); the page sets `packages.storyCue` when that chunk STARTS
playing and clears it when the beat's last chunk is played or the voice
stops, and the bridge posts `story.cue` / `story.clear` to the frame, whose
helper (`jarvis/package_story.js`, one copy, served at
`/api/packages/story.js`) rings the targets and runs the beat's `show` and
`click` actions. `package_story` frames on the voice socket say a story's
state. The beats are spoken as the sender's narration and never reach the
brain; the tool gives back only the slides' titles, walled. The
examples are `examples/packages/spanish-speech-test/` (conversation),
`text-stats-agent/` (agent), `mic-check-page/` (page) and `story-deck/`
(a page with a story). Every package an agent sends Tim is made with
one command, `python -m jarvis.make_package <content.json | folder>`
(`make_package.py`), and `docs/making-a-package.md` is the only place
that says how: a deck's content (a decision package, or a presentation
when it has no decisions; `decision_package.py` reads, expands and checks
it, the shared deck's `new_deck.py` builds it) or a hand-made package's
folder (only `purpose` given; read back through
`work_packages.recognise`). A deck is checked end to end in an isolated
JARVIS (`decision_check.py`, `decision_check_server.py`: layout at both
sizes and the story told to the end; for a decision package also voting,
the story waiting at every question as a first-time user answers by click
and by decide.py, Stop within 1 s from the page and the tool, and resume).
Only then is it delivered unshown through `POST /api/packages/local`, a
decision package stamped for the live JARVIS first, with a message to Tim.
`_open_local_package` refuses a decision package (`deck-data.js` and
`scripts/decide.py`, the shared deck's) without a matching stamp for its
own tool token (`decision_stamp.py`), so no other way delivers one.
`python -m jarvis.decision_package` is the command's old name.

## The package inbox
Every package an agent sends Tim arrives one way: `python -m
jarvis.make_package` checks and builds it, then posts the zip's bytes to
`POST /api/packages/inbox?project=&session=&workflow=&work=`
(`decision_package.deliver`, the tool token, which the decision stamp needs
anyway; the `packages` access-key category opens that one route and
nothing else). `package_inbox_api.py` hands it to `server._receive_inbox_package`:
the workflow or work item it names must exist and be open, the bytes are
unpacked by `work_packages.unpack_held` exactly as a local zip (a decision
package only with its stamp), and `package_inbox.arrive` records it. Nothing
calls `_open_package_page` on arrival. `package_inbox.py` is the only
writer of `package_inbox` (one row per arrival) and
`package_inbox_events` (each change, no words of the package), schema
`package_inbox_schema` (`run_store.migrate`), each change one transaction
with its revision, before any hint (`packages`, and `work` when it names a
work item). States `arrived -> opened -> answering -> answered ->
closed` (`signed_off`, `dismissed`, `superseded`). The same bytes while open
are the same row; an open package of the same name from the same sender
(its session, else its project) is closed `superseded` in the same
transaction. A decision package's questions are read from `deck-data.js`
as JSON after `window.DECK = ` (never executed) and its answers from its
own `results/decisions.json` (`package_inbox.questions`; a follow-up
applies only once its parent's answer opens it); the row mirrors only the
counts, read again after the frame saves a result (`packages_api.mount`'s
`saved`) and after each answer. Arrival is an attention item of kind
`package` (`untrusted`: the agent, the title; `facts`: decisions waiting)
that is counted and listed but never spoken: `attention_store.SILENT_KINDS`
keeps it out of every digest whatever a caller asks. Opening resolves it.
The page shows the backlog as the Packages section at the top of the Queue
tab (`QueuePackages.tsx`, signed in or not, closed ones behind Closed) and
each arrival as one reduced line in Updates waiting (`UpdatesNotice.tsx`
`PackageLine`: who, title, decisions waiting, Open and Seen; never expands
or plays). Open is `_open_inbox_package`, on his click only
(`packages_api.his_click`) or the acting `open_inbox_package`; a page package
is shown in the centre, any other is in the brain's hands. His answers go
through the package's own `scripts/decide.py`, run with a fixed argument
list and no shell (`_answer_inbox_decision`: the acting
`answer_package_decision` and `POST .../answer`, which the Work tab uses).
Signing a package off is one function, `_sign_off_package`, for every kind
of package: the Done in the head of its view (`PackageView.tsx`, outside
the frame), Done on its Queue row (`POST /api/packages/{package_id}/signoff`)
and the acting `sign_off_package`. A package with no inbox row (unpacked
some other way, an older local one) is given one there and then
(`package_inbox.adopt`). Whatever he answered goes back: each answered
question as the workflow's own `decision_needed` then `decision_made`
under its id, and the workflow's wait ended (`wait_started` at arrival, key
the inbox id); then a capture addressed to the sending session
(`_new_capture` and `_set_capture`, shared with `capture`) holding, by slug
id (`package_inbox.ID`), each answer or "not answered" and the record's
path, or for a package that asks nothing that he has reviewed it
(`_sign_off_text`: no title, question or option name), read back and sent
only on his yes or Send. A Work-item decision that names the package
(`work_store` `package`) gets the answer he gave in the package to the
question of its key (or its only question) as his own answer; with none it
is closed by `decision_signed_off` (`work_store.SIGNED_OFF`: his act,
never a reporter's, never an answer; `work_report.py answer` exits 4), and
the item it was sent for (`--work`) gets an update. The row is closed
`signed_off` with `signed_off_at`, its update line resolved, and every
page showing it closes it (`package_closed` on the voice socket).
`package_inbox.follow_messages`, run on every message event
(`_attend_message`) and every direct send, gives a signed-off row the id
of the message that carried it back once that has gone.
`_close_inbox_package` (the acting `close_inbox_package`, `POST
.../close`, the row's Dismiss) sets one aside with nothing sent back. `package_inbox`
reads the backlog for the brain, walled. A package sent with `--work <id>`
shows its questions on that work item's Decisions tab (`work_api.packages_json`
on `GET /api/work/{id}`, `package_questions` on every item; the
`workPackageQuestion` target), answered there into the same record, and
`work_status` lists them.

## Collaboration bundles
Any file a teammate sends, a ZIP with no manifest included, can be inspected and read
without running anything. The shared contract is `bundle_contract.py`: the
typed JSON of every bundle route (Classification, MemberPage, TextPage,
Preview, PreflightResult, ReviewState), the fixed formats, operations,
support and reason codes, the opaque `member_id` (from the attachment's
SHA-256 and the member's checked path) and continuation, and `BRAIN_TOOLS`,
which says for each bundle tool whether it is an ACTING_TOOL, what it
taints and which route calls the same service. The page's copy is
generated into `frontend/src/bundleContract.ts` by
`scripts/generate_messaging_contract.py`. The pieces:
`bundle_readers/` (one reader per format and the
registry, `detect` deciding a format from content before extension, and
`health`, which asks osascript rather than assuming it), `bundle_cache.py`
(derived output keyed by owner, attachment hash, member, reader version
and options, owner-only under `data_paths.bundle_cache_dir()`, least
recently used trimmed past 512 MiB, plus each owner's scratch folder for
the private 0600 copies a reader needs on disk), `bundle_inspection.py` (the
one service: open, classify, list, read, preview, render and a static
preview's files, reached only through `messaging_files`: `downloaded` on
open, `verified_path` and `attachment_ref` otherwise; every outcome to its
observer), `bundle_api.py` (the routes under
`/api/team/messages/{message_id}/attachments/{attachment_id}/bundle`),
`bundle_capabilities.py` (what this node can read, on its node update),
`bundle_preflight.py` (the sender's readiness), `bundle_status.py` (the
recipient's openability, reported as a technical receipt) and
`bundle_tasks.py` (a one-off request's task context, review checklist,
companions and repair). Inspection never clears quarantine, keeps no
execute bit, runs no script, loads no macro and executes no HTML.

The readers, none a new dependency: PDF (`pdf.py`) through PDFKit and
CoreGraphics in `osascript -l JavaScript` (`_quartz.py`, the one subprocess
seam, `RUNNER`): page count, `encrypted`, each page's text (at most
`PDF_PAGES_PER_CALL` pages and a timeout per call), `scanned_no_text` when
no page has text, `page_failed` for a page that cannot be read, and a page
drawn into a raw CoreGraphics bitmap that `_quartz.png` encodes with zlib;
never ImageIO, NSBitmapImageRep or `sips`, which crash on this Mac (ImageIO's
GIF reader too). Text, Markdown, JSON, CSV and TSV (`text.py`): UTF-8, a
byte order mark, else Windows-1252 (noted `legacy_encoding`); CSV and TSV
paged by whole rows; malformed JSON or CSV still read, noted `corrupt`. HTML and SVG (`html.py`):
text with `html.parser`, and what it would load from outside listed as
`blocked_resources`; its static preview's files are served only from the
same archive with `html.PREVIEW_HEADERS` (CSP `default-src 'none'` and
`sandbox` without `allow-scripts`). PNG, JPEG, GIF and WebP (`image.py`):
sized from their headers and never decoded; the preview is the file
itself, and so is its one rendered page, served with its own type
(`RenderedPageInfo.media_type`; a PDF page is PNG), so the brain sees a
JPEG, GIF or WebP as sent; an image is never shrunk to `max_edge`, since
that would mean decoding it. A member with text and nothing to draw
(Markdown, CSV, an Office file) has an empty preview (no refs): asking for
it is not a failed read and is not observed. No preview is a read: it only
names what can be shown, so its success is never observed; a page counts
once `render_page` draws it and words once `read_member` reads them. docx, xlsx and pptx
(`office.py`): their XML through `bundle_archive`, by paragraph, by sheet
and row (saved values with the formula beside them,
`formulas_not_evaluated`) and by slide; `vbaProject.bin` is never read
(`macros_ignored`); an XML part with a DOCTYPE or entity is `corrupt`.
Legacy doc, xls and ppt, RTF, HEIC, audio and video have no reader
(`reader_unavailable`). Every reader pages through `_paging.page`, a page
at most `max_chars` and ending on a whole line, row, page or slide unless
one alone is longer; a member too large to read is refused
(`too_large`), never cut. An archive is listed with
`bundle_archive.list_members(..., per_entry=True)`: a bad entry (outside,
link, encrypted, duplicate) is a `blocked` member and the rest stay
readable; only the entry count and total size refuse the whole archive.
Tar, gzip, 7z, RAR and split archives are `unsupported`; a nested archive
is a member (`nested_archive`), never expanded.

The transport crosses the wire under its own capability,
`messaging.bundles.v1`, which the team server offers beside `messaging.v1`
(`messaging_models.bundles_offered`). A node's update carries its
`AttachmentCapabilities` (`bundle_capabilities.local_capabilities`: the
readers' health, checked at most every five minutes, the archive budgets
and the work-package modes; `zip` always); the snapshot shows each node's
own with `attachments_observed_at`, and a node judged from it is
`advertised`, `legacy` (none: an older JARVIS), `stale` (checked over
`CAPABILITY_FRESH_SEC` ago) or `offline`. A message may carry `task` (a
`TaskContext`: outcome, primary and supporting artifacts by attachment id
and hash, answer to the sender only) and `supersedes` (a file of an earlier
message in the same thread between the same two people, which is kept);
both are in the digest only when set, so every older digest is unchanged.
A draft holds them (`Draft.task`, `.supersedes`); `draft_content` and the
read-back include them only while the server offers bundles
(`MessageApprovals(bundles=...)`, from `Delivery.bundles_offered`), else
the read-back says the task does not go, and a change of server between
review and yes is a new digest and a new review. The recipient's
openability is a receipt kind (`openability`, one `OpenabilityReport` per
file in `ReceiptFacts.openability`, never a read): `bundle_status` folds
the inspection service's observations per (message, file, revision)
(`human_openability`, messaging store schema 3; a listing that already
shows a member with no reader is `partial` before anything is tried on it,
and a review's progress is only worked out on a thread whose task was asked
of him), owes the receipt when the report changes, and delivery sends it only to a server offering bundles.
JARVIS asks every message and receipt route for the bundle fields
(`?bundles=1`) and for a reply's cites (`?cites=1`, `BUNDLES_QUERY`); a
team server answers a JARVIS that does not ask for the bundle fields with
the messaging.v1 fields only, and one that asks for them but not for cites
without `cites` (`messaging.hidden_fields`, the one rule, applied by
`messaging.legacy_view`), so older nodes, whose strict wire copy refuses a
field it does not know, keep working. `bundle_preflight.SenderPreflight` inspects the draft's own
snapshots (`DraftFiles`) with the same inspection service, derives what
the message needs, and judges each of the recipient's nodes on its own:
blocked, ready, partial or unknown, with only the gaps the sender can act
on; a review of a draft with files stores it with the revision
(`human_preflight`). `bundle_tasks.BundleTasks` sets the task (an edit:
`LocalMessaging.edit_bundle`), adds companions beside the original
(`FileSnapshots.snapshot_made`), prepares a repair (a draft replying in the
thread with a flattened copy checked member by member, or companions,
from `FileSnapshots.original_bytes`), and keeps the recipient's review
checklist (`human_review`, never sent), resumed across a repair: an item
read on the replaced file carries to the repaired copy's member only when
`BundleInspection.member_digest` (the member's SHA-256, under the archive
budgets) is the same on both, never on the path alone; each
item keeps where it was read (`read`: the pages and line ranges an
`Observation`'s `locations` carried, ranges that touch joined,
`bundle_tasks.read_locations`). His reply in a thread whose task was asked
of him carries the items it relies on (`Draft.cited`):
`BundleTasks.propose_cited`, which `LocalMessaging` calls in the same save
as every edit (`propose_cited`, set by `messaging_runtime`), proposes the
read items whose name or path the reply's text names as words
(`spoken_words.folded_words`), again as a new revision when an item is
read while the reply is still a draft, until he sets them himself
(`set_cited`: the composer's Cited ticks in `TaskFields`, `PUT
/api/team/messages/drafts/{id}/cited`, and the acting `set_cited_files`;
`cited_by_owner` then stops the proposal). Each mark stores its wire
reference when it is proposed or set (`CitedFile.ref`, a `CitedRef`: the
message in the thread he received the file on, the file's SHA-256 and the
member's archive path, or none for the whole file; `BundleTasks._marks`);
a mark saved before that has none. The reply carries the marks with one
as `cites` (`ports.sent_cites`, at most `CITES_MAX`, in the digest only
when set) only while the team server offers `messaging.bundles.cites.v1`
(`messaging_models.cites_offered`, `MessageApprovals(cites=...)` from
`Delivery.cites_offered`, as bundles are). The read-back has a `Cited:`
line and says which stay here (`CITES_NOT_CARRIED` when the server takes
none, else "Not sent with it:" and the marks with no reference), and the
hand-over checks the read-back again, since the digest cannot see what is
not sent; a change is an edit, so a reviewed reply is reviewed again, and a
change of server between review and yes is a new digest. The team server
takes a cite only of a file of an earlier message in the same thread that
was sent to the person replying, by its attachment id and hash, else
`relation_not_allowed`. Once the team server has accepted the reply at the
revision that carried them, those items are `cited` (`_cited_by_replies`,
worked out each time the record is brought up to date); a cited item stays
cited and carries across a repair as a read does. On the sender's side the
cites stay with the reply as it is stored, and `ports.with_citations`
(called by the messaging store for every thread it reads) gives each
message he sent the cites the other person's replies make of its files
(`LocalMessage.cited_in_reply`): the one record the Queue's file card
("Cited in reply", with the members) and `read_people_thread`'s file lines
("cited in reply") both read. A received
file fetched for a look (`FileSnapshots.downloaded`) is tried again on a
transient failure, at most `DOWNLOAD_ATTEMPTS` times, against its hash.
`messaging_runtime` builds and binds the three records; a draft edit from a
worker thread runs on the event loop (`MessagingRuntime.on_loop`).

The routes and the brain's tools share one set of calls,
`bundle_api.Calls`, over the context the messaging runtime gives
(`MessagingRuntime.bundle_context`: its one `BundleInspection` over the
received files, `received`, and the owner signed in now). A tool answers with the route's own JSON text
(`bundle_api.answer`), a page of it at a time within the tool cap
(`server._paged(..., exact=True)`, so the pages join back), walled as the
sender's words. `look_at_bundle_page` hands the brain the PNG as an image
block, as `look_at_screen` does. The Codex brain reaches the same MCP
server and the same tools. `show_bundle_member` and the page's Open share
the show route's call, which checks the member and sends ids only in a
`bundle_view` frame on the voice socket. A member's static HTML is served
with `web_auth.BUNDLE_PREVIEW_HEADERS`, which are the html reader's own
`PREVIEW_HEADERS` (framed by JARVIS only, `sandbox` with no permissions,
nothing from outside the bundle), and framed with `sandbox=""`. A sent
file's card shows the recipient's JARVIS's report once it has opened it
(`receipts.openability`; "Read there", "Opens there", "Partly readable there" with the
first member that failed), and `read_people_thread` says the same on each
file line and the thread's review progress in its head. Downloading or revealing a file is not reading it. The
page's side: `bundles.ts` (the routes, the open member and `bundleWords`,
every string shown), `BundleInventory.tsx` (Open on a checked incoming
file's card: a document opens in the centre, an archive lists a folder at
a time), `BundleMemberView.tsx` (the centre's File view: a PDF's pages
beside its text, text with More, rows as a table, images, the static
preview) and `BundleLines.tsx` (a message's request line, the thread's
review progress, a draft's readiness, each shown only when there is
something to say, and a draft's request fields, `TaskFields`: the kind,
the words and which file is primary, saved through the task route; a
member inside an archive is named as primary through `set_message_task`).
A member a zip lists as a device or pipe is `special_file`.

## The Network view
Every paired JARVIS backend is a node on the team's network. Every five
seconds while paired, `team_client` sends one node update to the team
server (`POST /api/v1/network/nodes/self`, `team_server/teamserver/network.py`)
and stores the team's whole snapshot that comes back exactly as it was
sent; `GET /api/team/network` returns it unchanged, and a `network` hint on
`/ws/sessions` says when to read again. The update is built by
`team_api.node_update` from the local collectors alone: the node ID
(`data_paths.node_id()`, one per data directory), the boot ID and update
sequence (`team_api`), the machine's CPU and memory (`host_resources.py`),
each brain's account as a keyed hash with its quota windows
(`brain_accounts.py`; no email leaves the machine), the last human
activity (`human_activity.py`: a sequence number, an age and a source, never
content) and what it can open (`bundle_capabilities.py`, see "Collaboration
bundles"). The team server binds each node to the member whose session first
published it, refuses an older boot or sequence, and works out presence
(online within 15 s, delayed to 45 s, then offline), the brains' pace and
its own health when the snapshot is built. The brain's `team_network` reads
the same stored snapshot (`team_client.network_status`), this Mac first. The page's Network tab
(`NetworkPanel.tsx`, one `NetworkTile.tsx` per node, `NodeView.tsx` in the
centre) and the left column's Team Server tile (`TeamServerTile.tsx`,
`TeamServerView.tsx`) read only that snapshot (`network.ts`); nothing local
is merged in, this machine's own tile included. The Team Server tile's word
is decided in one place, `network.readiness`: signed out is Sign in;
otherwise the network exchange decides, not the 30-second status poll, so
no snapshot or a stale one is No contact (never Ready), a database the team
server says is down is Not ready, and a fresh snapshot is Ready.

## The sign-in reminder
A JARVIS that is not signed in to a team server works as before and reminds
Tim to sign in. Not signed in is `team_client.NOT_SIGNED_IN` (disconnected
or never paired, signed out, or part way through pairing;
`TeamConnection.signed_in`), and the page's side of the same set is
`network.needsSignIn`, which the Team Server tile's Sign in also comes from,
so the tile and the reminder always agree. While it holds, the centre area
shows one line under its tabs (`TeamSignInReminder.tsx`): "Sign in to your
team server: messages between people and the Network view need it", with
Sign in and Later. It sets `--centre-notice-height` to its own height and
every centre view starts at `--centre-view-top`, below it, so it covers
nothing. Signing in has one path, `teamSignIn.ts`, which Settings, Team
(`TeamSection.tsx`) and the reminder both call (`connectTeam`: `POST
/api/team/connect`, the approval page in a new tab). The reminder's Sign in
(`signInAgain`) reads `GET /api/team`: with a `last_server_url` it signs in
there and opens Settings at Team, where the code is; part way through pairing, or with no address, it only opens Settings at
Team (`settings.open {section: "team"}`). Later hides it until the page loads again or
the voice socket reconnects (a JARVIS restart), never for good; it goes by
itself once signed in. Once per process, `team_reminder.remind_once`,
started by `team_api.start(attention=...)` and cancelled by `team_api.stop`,
looks once `SETTLE_SEC` (30 s) after startup and, not signed in, records an
attention item (`sign_in`), resolved once it is signed in; nothing is said
by it.

## Subagents
The subagents a Claude Code session dispatches are read from its own files by
`session_watch.read_subagents`, the one reader (the counts on the snapshot
come from it too): `agent-<id>.jsonl` and the `agent-<id>.meta.json` beside it
in `projects/<encoded cwd>/<sessionId>/subagents/`, and the agents the
Workflow tool starts one level deeper, a folder per run, in
`subagents/workflows/<run id>/` (the run id, such as `wf_f21d5ed2-0a4`, is
each one's `workflow_run`, None for the rest; the workflows folder and each
run folder are entered only when not a symlink). Finished means its last
message is the assistant's with `stop_reason: end_turn`; otherwise it is
`working` while written within `AGENT_ACTIVE_WITHIN_SEC`, else `stale`, which
claims neither. Deduped by name across roots, capped at `MAX_AGENT_FILES`
(a floor when capped, counting both levels), never through a symlink,
never raising. They ride on
the sessions snapshot (`subagents`), and `/api/sessions/{id}/transcript
?subagent=<id>` reads one. The Workflows area shows them, worked out at read
time and never written to `workflow_store`: a subagent of a session attached
to a running workflow is in that workflow's detail (`subagents` on
`/api/workflows/{id}` and its transcript), any other has its own row
(`subagents` on `/api/workflows`), lingering `LINGER_SEC` once it stops. The
Agents panel shows "2 working" on an agent's box. An `agents` and a
`workflows` hint go out when any session's subagents change.

## Voice diagnostics
How voice input is doing is one durable record, `voice_diagnostics.py`:
`voice_samples` (one a second while a page listens, taken as the page's
`telemetry` arrives on `/ws/transcribe`) and `voice_events` (each capture's
start and end, reconnects, every failure with the cause in force and the
busiest processes, each drop of audio the page held (`dropped`, its reason
and the seconds lost, reported on the abort and counted as a drop), every line of text the page showed, and each switch of
speech model as `model-load`). `classify` gives
each second one state and one cause, in the order the audio travels: the
microphone, the browser, the connection, the server, the decoder. Counts and
timings only; no audio, and no words but the page's own text. Samples are
kept 7 days, events 30. The page gets `health` once a second back on the same
socket; `GET /api/voice/diagnostics*` reads the record and a `voice` hint on
`/ws/sessions` says when to read again. The Diagnostics tab draws a summary
(`VoiceSummary.tsx`) and the centre area's Diagnostics view the detail
(`VoiceDiagnosticsView.tsx`). Nothing about voice input or JARVIS's own state
is written under the orb any more: it goes to that view's Messages
(`voiceHealth.notice`), and a health dot beside the meter shows a problem.

## Subsystem diagnostics
The Diagnostics tab lists each major part of JARVIS, one row each:
communications with the agents first, then voice input, the brain, voice
output, the tool channel, the workflow monitor, the folder catalog and the team
server connection
(`subsystem_diagnostics.py`), and last this page's own connection and graphics,
which only the page can judge (`frontend/src/subsystems.ts`). Without WebGL
(Chrome switches it off after its graphics process crashes) the orbs go still
(`orb.graphicsTrouble`) and the page runs on; that row says so. The team server row reads
`team_client`'s status and is never worse than `degraded`, since JARVIS works
without a team server; the left column shows it too (`TeamServerTile.tsx`). Each is `ok`, `degraded`,
`down`, `idle` or `unknown`, worked out when it is read and never stored,
from records that already exist (the `messages` table, the workflow record,
`voice_diagnostics.summary`) and what the server holds in memory (the
session watcher's last poll and failures in a row, the brain, each Fish
synthesis and each `/internal/tool` request, and a heartbeat per `_every`
loop). Communications are down when the session watcher is not running, has
not finished a poll for a minute or has failed three in a row; when the
messages sent last, back to the last one that arrived, were lost on their way
to two or more different sessions; or when the loop taking session messages
or reading the needs board has stopped. A message held in the queue for its session's turn over ten minutes makes them degraded, naming the agent and what JARVIS takes its session to be doing. `GET /api/diagnostics/subsystems`
reads it all, and the brain's `diagnostics` reads the same `_subsystems()`
(one row in full on request, voice input with `voice_diagnostics.summary`'s
figures); `_watch_subsystems` looks every five seconds, sends a
`subsystems` hint on `/ws/sessions` when any state moves, and records an
attention item once communications with the agents are down, resolved once
they are back.
A down row is red, and so is the Diagnostics tab whichever tab is open;
clicking a row opens it in the centre area (`SubsystemView.tsx`; voice input
opens the voice view). Every name and line JARVIS writes itself is sent as a
code and its values (`name_said`, `said`: `subsystem_diagnostics.say` and
`named`, from `SAID` and `NAMES`) beside its English, which the brain reads;
the page words the code in its own language (`subsystems.ts` `subsystemName`,
`subsystemSummary`, `checkName`, `checkDetail`, catalog `right.subsystem.*`)
and shows a line with no code (another program's words) as it came. A test
keeps the Python English and the catalog's English word for word the same.

## Tool activity
Every brain tool call that names a real tool at `/internal/tool` is a call
in `tool_activity.py`'s feed, in memory only: `started`, then once
`refused` (the digest and acting-tool gates), `failed` (raised, or the
request went away) or `completed` (the handler returned; its text is never
read). An event is its sequence number, call id, tool, category, stage,
time and, on an ending, duration; never arguments, results or words. The
category is the orb study's grouping (`data`, `communication`, `execution`,
`ui`), one table, `tool_activity.CATEGORIES`, checked against `TOOL_SPECS`.
At most `KEEP_EVENTS` are kept, oldest dropped first; `generation` changes
with each process. `GET /api/tools/activity?since=<seq>` reads it. There is
no `/ws/sessions` hint, so a reader asks again with the last `seq` it saw.
`internal_tool` runs the gates in `_dispatch_tool`, which returns the reply
unchanged and how the call ended.

## Brain activity
The tool calls are one kind in the brain's one activity feed,
`brain_activity.py` (`feed`; `tool_activity` records into it), which the
Denice page reads at `GET /api/brain/activity?since=<seq>` (same
`generation`, `seq` and `oldest` as above; `/api/tools/activity` reads only
its `tool` events, in their old shape). Each event is a kind, a stage, a
time, the brain provider in force and, where there is one, the counterpart
(an agent's name, or "a person"); `record` refuses any field outside
`FIELDS`, so no words, arguments, results or subjects. Each is recorded by
the one writer of its fact: `turn` in `Brain._turn_locked` (`started` with
its origin, `first_output`, `streaming` at most every `PROGRESS_EVERY_SEC`
with `chars_per_sec`, then `completed`, `failed` or `cancelled`);
`agent_message` in `run_store.create_message` / `update_message` (each status
it takes, with the session id); `session_message` in
`run_store.create_session_message`; `person_message` in
`ServePresentation.present` (`sent`, `arrived`) and `Delivery.submit`
(`accepted`); `digest` in `AttentionStore.begin_digest` / `finish_digest`;
`speech` in `SpeechScheduler` (`started` when the page says it began to
sound, `ended` with its outcome, through `Utterance.close_outcomes` or its
retirement); `listening` in `TurnController` (its `state`
setter: `started` when a turn starts gathering his speech, `ended` once it
stops gathering, committed, discarded or its capture stopped; the turn's id
only). Recording never raises into the caller. At most
`brain_activity.KEEP_EVENTS`, oldest first dropped; no hint.

## The folder catalog
A folder is a place on this Mac JARVIS knows by a stable ID; its files stay
where they are and nothing here creates, moves, edits or deletes one. The
record is in the existing database (`data_paths.db_path()`) under its own
schema version (`folder_schema`, migrated with `run_store.migrate`, the
helper human messaging uses too): `folders` (label, role, active or removed,
a revision), `folder_locations` (node from `data_paths.node_id()`, path as
given and canonical, filesystem identity as evidence, availability; one
current location per folder, a reconnect retires it and adds the next
location revision), `folder_access_scopes` (the reader and policy version;
discovery never writes one), `folder_candidates` and `folder_observations`
(what discovery found and why, kept after a session ends),
`folder_location_events`, `folder_registrations` (request keys) and
discovery's progress. There are no project tables yet: projects come in a
later migration and refer to these IDs. `folder_store.py` is the only writer,
one transaction per change, the revision moved inside it; it reads no
transcript and checks no permission.

Which reader may read a folder is decided by where it is, never by its role
or by the caller (`folder_policy.py`): a Claude Code `projects/<folder>` under
`history_read.claude_roots` is CLAUDE_HISTORY (transcripts and memory notes
only), the user's Codex `sessions` is CODEX_HISTORY, the brain's journal,
memory and project notes are NOTES (Markdown only), the rest of JARVIS's own
data and of each provider home is refused, and anything else is FILES. A
folder the user names may itself be hidden (`repo_read.sensitive_reason(...,
named_dotfiles=True)`), but `.ssh`, `.env`, keys and credentials never; inside
any folder every entry gets the full rule, so a parent never opens what a
narrower folder or the rule closes. `folder_readers.py` reads from the
canonical root one component at a time with `openat(O_NOFOLLOW)`, so a link
is listed and never followed, and a swap between check and read fails
closed; a page is 100 entries out of at most 10,000 looked at within 2 s, a
window 200 lines and 64 KiB by `pread`, however large the file, and a
transcript is shown as what was said (`history_read.said_lines`).

Discovery (`folder_discovery.py`) reads only the providers' structured
records: the Claude roster, the `cwd` each transcript records (the encoded
folder name is never decoded), each Codex thread's cwd and rollout, and the
session watcher's live sessions. A session's cwd and its history folder are
separate candidates. A pass reads at most 200 transcript heads, newest first,
and records what is left; one caught mid-write is read again when it grows.
The server runs it every `DISCOVERY_EVERY_SEC` and after a Refresh, off the
event loop (`_watch_folders`), and checks every folder's availability
(Available, Missing, Access denied, Unavailable node). `folder_service.py` is
the one service the page's routes (`folders_api.py`, `/api/folders*`; changes
need the page's origin or the tool token, and no access-key category covers
them) and the brain's tools (`list_folders`, `find_folder`, `browse_folder`,
`read_folder_file`, `folder_detail`, `folder_candidates`, and the acting
`register_folder`, `unregister_folder`, `change_folder` (edit, reconnect,
refresh) and `change_folder_candidate` (ignore, restore)) both call; tool
results come back in an untrusted block. All of them are in
`claude_provider.ALLOWED_TOOLS`, and the brain's instructions
(`jarvis_home/CLAUDE.md`, "Folders") tell it a registered folder from a
project and to look in both before saying it cannot find one. Work runs in
a folder through `spawn_run`'s `folder_id` (never together with `project`):
`FolderService.work_location` gives the folder's current canonical location
under the same checks as a read (active, enabled, this node's, available,
the reader the policy still gives it) and only for a FILES reader, never a
history or notes one. The run records it in `runs.folder_id` (added by
`run_store.init_db`'s column backfill), and a retry goes through
`work_location` again. `start_build` takes projects only: `build_status`,
`review_document` and `approve_document` find a build by its project. Every
committed change is a `folders` hint on `/ws/sessions`. The page's side is `folders.ts`
(one store for both views), `FoldersView.tsx` and `FolderDetail.tsx` in the
centre and `FoldersPanel.tsx` on the right. `JARVIS_FOLDERS=0` switches the
routes and tools off and keeps the records.

## The knowledge engine
A document store, a typed graph, a keyword index and a vector index in a
process of its own, `python -m jarvis.knowledge serve` (`jarvis/knowledge/`,
which imports nothing from `server.py`); the design is
`docs/planning/work-package-bundles/design.md` and the build order
`build-plan.md` beside it. It holds nothing that cannot be rebuilt from
JARVIS's records, notes and folders, and `python -m jarvis.knowledge rebuild`
drops it. Off unless `JARVIS_KNOWLEDGE` is on: then `knowledge_runtime.py`
starts it after startup settles through `knowledge_client.Supervisor`
(loopback only, its own token, `knowledge-address.json`, restarts after 1, 5
and 30 s, down after three in ten minutes) and paces it every second: hold
while the conversation floor is not idle or the CPU is over 70 percent. Ingest
is a child of its own, `python -m jarvis.knowledge_ingest`, one pass at a time,
stopped and continued with the pace; it reads the brain's notes, the
conversation log, finished runs, workflow events and registered folders'
Markdown (through `folder_service`, FILES reader only) and keeps each source's
watermark in the engine. No embedder is wired until one is chosen
(`knowledge/embedder.py`'s `RUNNER`). Diagnostics shows its row (`knowledge`).
Its process, crash and budget tests carry the `knowledge_engine` marker.

## Self-update
A checkout whose `origin` is on github.com updates itself from GitHub through
one service, `self_update.py`. A version is a pull request merged to main:
each first-parent merge commit on origin/main newer than HEAD
(`newer_versions`, newest first, named by the PR number in "Merge pull
request #N" and the title on its body's first line, with its date; a merge
that is no pull request, and origin/main's latest when it is not a merge,
are listed by short sha and subject). The version running here is HEAD
described the same way (`current_version`). `check` fetches origin and says
the branch, this commit, origin/main's latest, the current version, the
newer versions and each one's CI, all from ONE GitHub request
(`ci_lookup`: the `ci.yml` workflow's runs on main, read without a login,
matched by head sha, the latest run per sha; the scheduled team server
health check does not count, and a commit with no `ci` run has no CI
result). Only a version whose CI passed can be chosen; going back to an
older one is not offered. `check` and `update` take an optional version (a
PR number or a sha; origin/main's latest when none), refused when it is the
running one or older, not one of those listed, or its CI has not passed;
`update` checks again, moves to the chosen commit (`git merge --ff-only`),
then runs `.venv/bin/pip install -r requirements.txt` if
that file changed, `npm ci` if `frontend/package-lock.json` changed, and
`npm run build` if the page is served from built files (`jarvis/web`) and
anything under `frontend/` changed; under Vite a changed lock file says to
restart `npm run dev`. A step that fails moves the checkout back (`git reset
--keep`), so the update is offered again and runs every step. It is refused,
with the reason, when not on main, with modified tracked files, with local
commits not on origin/main, with untracked files the update would add
(renames included), or when the latest commit's CI has not passed; an
installed copy or another origin is told why it is not offered.
`GET /api/update` is the check, with the versions and the current one, and
`POST /api/update` (optional `{"version": ...}`) the update, then the
restart (page origin or tool token; no access-key category). The restart is
one function, `server._restart_server`, shared with `/api/restart`. The
command `python -m jarvis.update` (`update.py`) asks a running server to do
it, so it restarts itself, and with the server stopped only updates;
`--to <PR number or sha>` chooses the version and `--check` only reports,
listing the versions. It finds the server by the one reader,
`server_address.running()`: at startup the server writes its scheme, host,
port and pid to `data_paths.server_address_path()` (`server-address.json`
in its data folder; the same address the brain's tool channel dials,
`server_address.own()`) and removes it on a clean shutdown; a record whose
pid is dead is stale and ignored. With a live record the command asks that
address with the tool token, and a refused connection there stops it
without updating. With none it looks at `JARVIS_URL`, else
`JARVIS_TOOL_URL`'s address, else 127.0.0.1:8340; only a refused connection
there counts as stopped. An answer that is not an update outcome (a 403, a
404) stops it without updating. The page's side is Settings, Updates, which the ⋮
menu's Check for updates opens: the version running, the newer versions
as a radio list (newest tagged LATEST, the newest that passed chosen, the
others disabled with why), and Install and restart, which asks once in an
inline box before "Yes, install PR N". The brain's is `update_status`
(the versions, titles walled) and the acting `update_jarvis` with an
optional `version`, which says it is updating, updates after the turn and
restarts once that has been heard (`_update_by_voice`). Tests point
`self_update.repo_root` at temporary repositories
(`conftest._never_update_or_restart_the_real_checkout`).

## Attention
Routine events are never spoken by the server. Each (an agent's message
not picked up, answered or not sent; a session's message,
needs board or needs-you; a session finishing; a run or package run ending;
a person's message arriving; a workflow's news; communications down; not
signed in; a person's message that did not go; the brain down, needing its
sign-in or at its usage limit; a shell command's or keypress's outcome after
its exact read-back) is first an item in
`attention_store.py` (JARVIS's database, schema `attention_schema`): one
item per source object (kind, source id), each change its next revision and
a line of `attention_events`, JARVIS's own facts apart from other people's
words (`untrusted`). `server._attend` is the one way in. `attention.py` is
the one admission policy: engaged (his last trusted interaction within
`engagement_sec`, not away; a connected page is not engagement), the
conversation controller's floor `idle`, `settled_exchange_sec` since it was
last busy and since his last interaction, `digest_cooldown_sec` since the
last digest attempt, and the oldest candidate at least `oldest_event_sec`
old (trial values 120, 10, 120 and 60 s, settings in `attention_settings`).
What changed while he was away or unknown is counted, never played. When
all pass, one digest: the candidates' facts to the brain with other
people's words walled, under the `digest` origin, where `/internal/tool`
refuses every tool; at most two sentences or SILENT; discarded if the floor,
the activity revision or any item moved meanwhile. A failed brain or voice
says nothing in its place. A digest is made only from the candidates whose
facts fit its walled block whole (`attention.fitting`, under
`server._WRAP_CONTENT_CAP`, each name or subject at most
`DIGEST_WORDS_CHARS`), so played (or SILENT) finishes with the revisions of
only the items the brain was shown; the rest wait for the next. Seen is his
act: his click, his mark, or hearing it read in full. The brain's reading
tools (`read_message_reply` on an answer, `read_session_message`,
`mark_people_message_read`) note the pending update they read on a turn he
drove (`server._read_to_him`), and once that turn's reply has been heard in
full (`speech.wait_for`, every chunk played) it is seen at the revision read
(`server._seen_once_heard`, `attention_api.mark_seen_source`); a reply cut
off, skipped or failed leaves it pending, and no digest marks anything
seen. `attention_updates` gives the brain every item the page has, each
brief (`attention_api.for_brain`), a page at a time
(`server._paged(..., exact=True)`). The digest's brain turn takes the brain's
turn lock for the few seconds it runs, so a turn of his that starts then
waits behind it. The floor is `server.conversation_floor`
(`_ConversationFloor`, an `attention.TurnState`), read from every open
page's turn controller (`server._voice_turns`), the recogniser's backlog
(over `RECOGNITION_BEHIND_SEC`), a brain turn under way and the mouth
(speech playing or queued, a read-back's cancel window). The speech
scheduler reads the same floor (`SpeechScheduler.floor_held`): an
announcement waits while it is his or the brain's, checked when it is
picked and again before its first audio, and a held one goes back to the
queue so the reply to his turn is never behind it. Nothing is batched:
`Priority.LOW` has no batch, and nothing cut off by a barge-in or Stop is
kept to be said later. The page shows a grouped count with Review updates, Mark
all seen and Hold spoken updates (`UpdatesNotice.tsx`); the brain's
`attention_updates`, `mark_updates_seen` and `set_attention_settings` call
the same `attention_api` functions as `/api/attention*`.
`tests/test_speech_call_sites.py` fails any call to the voice outside the
approved entry points it lists, and drives every routine producer against a
voice that must stay silent.

## Quiet voice input
The contract is `docs/planning/quiet-conversation/contract.md` (states,
transitions, identities); the plan and its evidence are beside it. A page
loads not listening: no `getUserMedia`, no recogniser socket. Listening
needs his deliberate Start listening (`#btn-listen`), Unmute or Mic on in
that page load (the grant is `voiceInput.ts`'s, in memory, never restored);
a click elsewhere, the browser's permission or unlocking audio is not one.
Mute and Mic off are this browser's quiet preference (`mic.ts`, localStorage
`jarvis.voice.quiet`, read synchronously when the module loads, before any
`getUserMedia`): a reload keeps Muted (shown as Muted with no microphone) or
Off, and storage that cannot be read is no preference, which is not
listening. Another tab's change arrives by the `storage` event and never
arms a tab. Mute suspends PCM to every consumer (the recogniser and a page
package's listeners), drops the server's gathered turn and keeps its words
as an unsent draft; Unmute starts a new capture epoch and never sends the
old draft. His two settings are per user (`voice_control.py`, the node's
SQLite, keyed by the signed-in member's GitHub id or `local`): "When I stop
the microphone" (`keep` finishes only audio from before the stop, its words
under the ordinary turn rules, never a new automatic send; `discard` throws
away unfinished input) and "When I switch away from JARVIS" (`continue` a
session he started, or `stop`); with them his four pause intervals (see
below), whose total is his patience. Defaults
are `keep` and `stop`, to be assessed. Until they are read Start listening
is unavailable. `GET`/`POST /api/settings/voice-input` and the brain's
`voice_input_settings` and acting `set_voice_input_settings` share
`voice_control.read` and `change`; a change publishes `voice_input_settings`
on `/ws/sessions` and starts, stops and sends nothing. One page owns the
recogniser (`transcriber.TranscriptionService.admit`): a start carries its
`clientId`, `captureEpoch` and whether it claims (his Start); without a
claim another page's capture makes it `busy`, and a claim tells the page it
displaces `taken` (`transcribeProtocol.json` version 2). Every word on
`/ws/voice` carries `clientId` and `captureEpoch`; a stale epoch's words are
dropped. A dropped voice socket sends nothing and keeps what the server had
gathered as an unsent draft; that page, still open, resumes listening
under a new epoch when the socket is back. The brain may only make input
quieter: its acting `quiet_microphone` (muted or off) sends every page a
`quiet` frame that it applies through `mic.chooseQuiet`, as his buttons do;
there is no tool to unmute, turn on or start listening. The headset in the power
button's place turns the whole audio system off and on (see "Playback and
its outcomes").
A quiet tone marks listening really starting (the recogniser ready for a
streaming microphone: Start, Unmute, Mic on, a resume or a retry that got
through) and a falling one its stopping for any reason (his Mic off or
Mute, leaving under Stop, `taken`, the voice socket or recogniser lost, a
capture dropped): `voiceInput.ts`'s `onCapture`, once per change, played by
the output's `AudioPlayer.cue` (`voice.ts`), never through the microphone's
audio context, and dropped, never delayed, while audio is locked. His third
setting, capture sounds (`sounds`, default on, in the same
`voice_control.py` service, route and tools), is the master switch for
every cue; the page shows the same either way. Beside it, per user in the
same service: each of the six cues (`start`, `stop`, `heard`, and the pause
cues `tentative`, `deciding`, `likely`) on or off with a tone from
`voice_control.TONES` (notes in `voice.ts` `TONE_NOTES`, placeholders), one
`volume`, and the pause's four `intervals`. The pause has four stages in
one ordered list, `turn_controller.PAUSE_STAGES` (the pause cues, then
`send`; mirrored by `voiceControl.ts` and imported by `jarvis_mcp`), each
with its own interval in seconds measured from the stage before, the first
from when his speech was last heard: to the "may be finished" cue, to the
"Deciding" cue, to the "about to send" cue, to the send. Defaults 1.0,
0.75, 0.75 and 0.5 (`turn_controller.INTERVAL_DEFAULTS`), each 0.25 to 5.
One function, `turn_controller.pause_schedule`, turns them into each
stage's time after he stops (their running sum; `voice_control.schedule_of`,
and `schedule` on the route's answer, which the page shows and never works
out itself), so every cue plays, in order, before the send, and none can
come after a later one or after the send. His patience is the send's time,
their total (3.0 s by default, `speech.END_OF_TURN_SEC`): shown, never set
on its own. A cue that is off keeps its interval: the time passes, its
stage still reaches the turn bar, no sound. Settings, Voice and language
shows the four intervals in order, each with its own field, the time that
stage comes after he stops, and a pause cue's tone and Test beside it, then
his patience as a reading; each cue has a Test (`voiceStatus.cueTest`; the
brain's acting `play_cue` sends the page of the turn a `cue_test` frame to
the same call). The brain's `voice_input_settings` reads, and
`set_voice_input_settings` takes, the four intervals. The pause cue in the
middle is "Deciding" ("Decidiendo"), default tone `low`. The "speech heard"
cue (`heard`, default tone `double`) plays once when he begins speaking:
the turn controller's `on_heard` when a new turn starts gathering (the
first word accepted as his) and when he resumes after a pause cue was given
in this pause (the first word accepted as his after it, final or interim),
never for echo, a stale epoch's or a typed word, and not again for each
word after; the server sends only the page gathering it a `turn` frame with
state `heard` and `sound` (as the pause cues', `server._cue_sounds_here`;
on a resume only if that pause cue sounded too), and the page plays it only
while listening, on the output, never into the microphone's graph. Migration 7 drops
a patience saved on its own and the cues' earlier shares of it (`percent`,
and before migration 6 `before`): those rows read with the default
intervals, a total of 3.0 s, keeping each cue's tone and on or off. The
pause is the turn controller's (see "Turn-taking").

## Turn-taking
JARVIS waits for Tim to finish. One `turn_controller.TurnController` per
voice page gathers what he says into a turn with an id and a revision that
every accepted word moves on; a final or an interim heard as speech starts
the hold (his patience, the total of his pause intervals, read from
`voice_control.schedule_of` each time it starts; default
`speech.END_OF_TURN_SEC`) again,
and an interim not yet followed by its final keeps older finals back. At
the deadline the commit is checked against the turn, revision and deadline
it was scheduled for (a timer is never trusted to have been cancelled) and
against coverage: the recogniser must have decoded every frame the page had
sent by the send moment (the deadline, or his Send now), and a frame whose
interim is still held back to coalesce does not count until it has gone to
the page (`transcriber` `coverage_of`, `Capture.frames_settled`, read on the
server). A final or an interim heard as speech from that last stretch of
audio moves the revision on, so nothing is sent and gathering goes on; with
no words in it the send waits only for the recogniser to catch up, looked at
every `COVERAGE_POLL_SEC` (0.02 s). Not covered within `COVERAGE_BOUND_SEC`, the turn is `stalled`: never sent
on its own; the conversation control (`ConversationControl.tsx`) offers Retry (wait again),
Send now (as heard) and Keep as draft (into the composer, unsent), and the
brain's acting `voice_turn` calls the same `server._turn_action`. Send now
is offered while a turn is gathered too (it waits for coverage to the
press, as at a deadline). The
server tells the page what it has gathered (`turn` frames: `gathering` with
its text, `commit-pending`, `stalled`, `committed`, `draft`, `idle`), so only
words it accepted as his, never echo, become a draft if the socket drops.
Speech after a commit and before its reply sounds stops that reply's
reading (its text stays, nothing is undone) and starts the next turn. Typed
Send is its own message; voice words being gathered go on as their own
turn. A stop under Discard is `turn_discard`. While he is quiet in a gathered turn the
controller times the pause from one schedule (`schedule`, read each time
the hold starts; `on_pause`): `tentative`, then `deciding`, then `likely`,
each at its running time after he stopped, then the commit at their total,
each cue checked when due against the turn, revision and deadline and that
it is still gathering, so any accepted speech cancels them and starts again
from the first interval, and each plays at most once per pause. The server sends them only to that
page, as a `turn` frame with state `pause`, its `stage` (shown on the turn
bar whether or not it sounds) and `sound` (his capture sounds and that cue on, and him
engaged, never away); the page plays it only while listening, at once or
never. Nothing JARVIS says starts
while he has spoken within his patience, replies included (the scheduler's
`end_of_turn` is the same value, set when a page connects and again on
every change of his settings, never clamped shorter), and announcements
also wait for the floor (see "Attention").

## Playback and its outcomes
Each chunk ends as one fact (`speech.Outcome`): `queued` (sent),
`started` and `played` (the page says so), `failed` (no audio, or the page
could not decode it: reported on the `playback` frame, never as played),
`skipped`, or `interrupted`. Transport advancing past a chunk is a separate
fact, and a chunk that never sounded is not echo. Three actions stay apart:
Skip the rest (the playback bar's Skip and Escape, `skip_to_end`, the
brain's acting `skip_reading`: one reading, its text kept, its late audio
dropped on `reading_skipped`; a read-back skipped is not heard, so it
approves nothing), Stop speaking (the status control's Stop, `hush`, the
brain's acting `stop_speaking`: all audio, through `server._stop_speaking`;
it cancels no request and undoes nothing), and Cancel, which exists only
where a job already has its own. A reply's last `reply` frame says how its
text ended (`outcome`: `succeeded`, `failed`, `cancelled`) and, when failed,
why (`reason`, `voice_control.ReplyFailure`: `not_ready`, `down`,
`sign_in`, `rate_limited` with `resetsAt`, `error`, `lost`), which the page
words in its language under the reply. No fault of the brain or of a turn
is spoken. `voice_control.py` and `frontend/src/voiceControl.ts` hold the
words both sides name.

Three audio controls, each with its own job, in the microphone's control
(`MicControl.tsx`, the message input and the centre's corner alike): Mute,
unchanged (microphone processing only); the headset in the power button's
place (`MicOffButton.tsx`, id `btn-mic-off`, target part `off`), which turns
the whole audio system off (Mic off and his speech mute on) or back on (the
microphone on and speech unmuted), drawn as the whole subsystem at a glance
(`AudioHeadset.tsx`: the boom what JARVIS hears, the ear cups what it says,
the power symbol when all is off; still, never animated); and the speaker
at the end (`SpeakerButton.tsx`, part `speaker`), which mutes and unmutes
speech, showing the mute and speaking, silent or stopped (for
`STOPPED_SHOWN_MS` after Stop), still like the headset. Stop stays its own control. His speech mute
is a voice input setting (`voice_control.py` `speech_muted`, per user,
saved, published as `voice_input_settings` so every page and the brain
agree): while it is on JARVIS answers in text only. The speech scheduler
skips every utterance begun while it is on (`SpeechScheduler(muted=...)`,
read as each starts), so nothing is synthesized or sent, and a muted
read-back is not heard, so it approves nothing; turning it on stops what is
sounding at once (the publish hook, `_stop_speaking`). The page's side is
`audioState.ts` (`setSpeechMuted`, `setAllAudio`, the looks). The brain only
makes them quieter: acting `mute_speech` (the same `voice_control.change`)
and `turn_audio_off` (that, and the quiet frame's Mic off); unmuting and
turning audio on are his clicks, and `set_voice_input_settings` does not
take the mute.

## Speaking Spanish
The language in force is `spoken_words.active_language()`, the one place it
is decided (see "The language setting"); callers reach it through the
module so there is one answer. Every Fish request body is built
by `tts.request_body`: the speech path (`_synth_for_speech`, which
`synthesize_speech` and `/api/tts-test` go through too) and the key check.
It adds Fish's `language` from `tts.FISH_LANGUAGES` (`es-MX` for Spanish;
English sends none, as before). The splitter knows Spanish abbreviations
(`speech._ABBREVIATIONS`) and keeps the spaced "p. m." / "a. m." in one
piece. `strip_markdown_for_tts` also strips the Spanish banned phrases
(`server._BANNED_SPANISH`) with their ¡ ¿ marks; every banned phrase is
matched with accents folded (`spoken_words.fold`) and as whole words
(`server._banned_match`), so "claro que si" goes as "claro que sí" does and
"claro que siempre" stays. For Spanish the brain's
`launch_prompt` adds `brain.language_rule`: `LANGUAGE_RULES` (answer in
Latin American Spanish, English tool results said in Spanish, memory
searched in both languages) and how he is addressed ("señor" by default,
"señora" under Ma'am). It
is in the launch prompt because an edited persona never receives template
changes; the persona's "Language" section says the same.

## The language setting
JARVIS hears and speaks one language at a time, English or Spanish, chosen
in Settings, Voice and language, Language (`LanguageControl.tsx`), by the brain's acting
`set_language`, or by one exact spoken phrase: "switch to Spanish" while in
English, "cambia a inglés" while in Spanish (`Vocabulary.switch_command`,
matched like the clear command: the whole utterance, optional leading
"jarvis"; `server._language_switch_asked`). All three go through
`language_setting.change`: it loads the language's speech model first
(`speech_models.switch`, a few seconds) and only then saves the choice in
JARVIS's own store (`data_paths.language_path()`, never .env) and puts it in
force (`spoken_words.set_active_language`), so a model that is not installed
or fails to load changes nothing (after a failed load the language in
force's model is loaded again, so he is still heard). Speech input's model is
switched only this way: there is no `POST /api/speech/model` route and no
`switch_speech_model` tool; the language route and `set_language` replaced
them, so what JARVIS hears and what it speaks can never differ. The page and the phrase are confirmed aloud in the new language
(`language_setting.choose`: "Speaking English now, sir." / "Ahora hablo en
español, señor.") through `_say_notice`, whose note tells the brain to answer
in that language from its next turn; the brain's tool returns the same rule
and the brain says it. Español is disabled until the Spanish model is
installed; "Download (124 MB)" is the install route, its progress read every
second while it runs. At startup, before the brain is spawned (its launch
prompt carries the language rule), `language_setting.load` puts the saved
language in force and gives speech input its model, or English for that run
when the saved one's model is missing (preflight checks the same model,
`startup_model`). `GET`/`POST /api/settings/language` are the routes; the
brain's `speech_models` read starts with the language.

The Honorific setting (Settings, You: Sir, Ma'am, Señor, None; `HONORIFIC`
in .env) is now used: `spoken_words.address` turns it into a fixed word
from one table, `spoken_words._ADDRESS`, each honorific's word in each
language (sir is señor in Spanish, ma'am is señora), and any value not on
that list counting as the default, sir, so nothing typed into .env reaches
the prompt. `brain.address_rule` adds a launch-prompt line only when
the address is not plain "sir", so the English prompt is unchanged by
default; the confirmations use it too.

## The Run Pipeline
Nothing spawns Claude Code outside `RunExecutor`. Two invariants govern it:

1. **A run always reaches a terminal state.** `succeeded`, `failed`,
   `timed_out`, or `cancelled` — never left stuck in `running` or `queued`.
   Every exit path out of `_drive` writes a terminal status, and callers that
   create a run then hand it off must fail it if they throw before the
   executor takes ownership.
2. **Every state transition is a DB write BEFORE it is a notification.** The
   WebSocket is a cache-invalidation hint, never a source of truth; clients
   reconcile against `/api/runs`.

The dashboard's Retry and the brain's acting `retry_run` share `_retry_run`:
a finished run's prompt again, resuming from it, a folder run re-checked
through the folder service.

No new npm or Python dependencies. When rendering arbitrary LLM and file content through imperative DOM code, use `createElement` / `textContent`, not `innerHTML` / `insertAdjacentHTML`.

## Key Files
- `server.py` — Main server, WebSocket handler, HTTP API, action system
- `brain.py` — The voice brain: one long-lived `claude -p` process on the
  user's Claude subscription, fed over stdin as stream-json
- `jarvis_mcp.py` — Stdio MCP server exposing JARVIS's tools to the brain;
  forwards `tools/call` to `POST /internal/tool`
- `speech.py` — Sentence splitting and the scheduler that owns every
  utterance JARVIS speaks
- `tts.py` — Fish Audio synthesis, one request per sentence chunk; the one
  request body (`request_body`) with its language
- `frontend/src/orb.ts` — Three.js particle orb visualization
- `frontend/src/voice.ts` — Audio playback, each chunk played or failed;
  re-exports voice input
- `frontend/src/voiceInput.ts` — The microphone: one getUserMedia stream,
  an AudioWorklet (`pcm-worklet.ts`, `pcm.ts`) making 16 kHz PCM16, streamed
  to `/ws/transcribe`; the listening grant, capture epochs, his two
  settings' stops (see "Quiet voice input")
- `frontend/src/mic.ts` — The quiet preference (read before anything
  starts), the input status and the voice input settings in force
- `turn_controller.py` — One page's turns: patient, identity-checked,
  covered commits; stalled turns and their three ways on; the pause stages,
  their default intervals and the one schedule function (see
  "Turn-taking")
- `voice_control.py` / `frontend/src/voiceControl.ts` — His voice input
  settings and pause intervals (their total his patience), per user, with their route and the brain's tools;
  and the words the voice socket names (reply outcomes and failures, the
  playback frame)
- `frontend/src/voiceSettings.ts` / `VoiceInputSettingsControl.tsx` — The
  settings read and changed on the page, and their Settings rows
- `frontend/src/conversation/ConversationControl.tsx` / `controlState.ts` —
  The conversation control, the message input's heading row: one state
  model (`controlView`, from voiceState, voiceTurn, the turns, voice input's
  status and health, the socket and the history read) and one table of
  every state's words, light and buttons; what he said and has not sent
  (Send now; Retry, Send now and Keep as draft when stalled), JARVIS
  thinking or speaking (Skip to end), two at once in yellow, and the
  conversation's errors in red (see docs/conversation-ux.md)
- `transcriber.py` — Local speech recognition: the model list (`MODELS`,
  English and Spanish Kroko, each with its digest) and where each lives, the
  sherpa-onnx engine, and the worker that owns it (one model loaded at a
  time, `switch` loads another in its place; one capture at a time, overload
  refused rather than trimmed)
- `speech_models.py` — Installing a speech model (`install`: download,
  SHA-256 check, safe extract, one rename; `scripts/setup_speech_input.py`
  is a thin command over it) and the service the page's
  `/api/speech/models*` routes and the brain's `speech_models`,
  `install_speech_model` share, and `switch`, which only
  `language_setting.change` calls. A switch ends the
  open capture with a non-incident `interrupted` (the page reconnects and
  waits while it loads) and is a `model-load` event in the voice record
- `language_setting.py` — The language JARVIS hears and speaks: the stored
  choice, `change` (model first, then saved and in force), the spoken
  confirmation and `/api/settings/language` (see "The language setting");
  `frontend/src/LanguageControl.tsx` is the page's side
- `transcription_api.py` — `/ws/transcribe`; the protocol is
  `jarvis/transcribeProtocol.json`, read by both sides
- `voice_diagnostics.py` — The voice record (see "Voice diagnostics");
  `frontend/src/voiceHealth.ts` is the page's side of it
- `tool_activity.py` — The brain's tool calls as a bounded in-memory feed
  and each tool's category (see "Tool activity")
- `brain_activity.py` — Everything the brain does (turns, streaming, tool
  calls, messages, digests, speech) as one bounded in-memory feed, no words
- `subsystem_diagnostics.py` — Each subsystem's state, worked out at read
  time (see "Subsystem diagnostics"); `frontend/src/subsystems.ts` is the
  page's side of it
- `frontend/src/main.tsx` — Renders the voice page with React (`App.tsx`;
  the orb is `OrbControl.tsx`)
- `frontend/src/jarvis.ts` — The voice page's state machine, started once
  the orb is on the page
- `frontend/src/ConversationView.tsx` — Conversation history, live words, typed input, captures and sources
- `frontend/src/AgentThread.tsx` — One agent's message thread
- `frontend/src/WorkflowDetail.tsx` — Workflow phases, tasks, decisions, transcripts and events;
  a subagent's detail and transcript (see "Subagents")
- `frontend/src/VoiceDiagnosticsView.tsx` — Voice incidents, timeline and notification analysis
- `frontend/src/SubsystemList.tsx` / `SubsystemView.tsx` — The Diagnostics tab's
  subsystem rows and one subsystem in full (see "Subsystem diagnostics")
- `run_store.py` — SQLite `runs` / `run_events` tables, six-value status enum;
  the `messages` table (see "Messages to sessions") and `steers` (keystrokes,
  commands)
- `run_executor.py` — Spawns `claude -p --output-format stream-json` and drives
  each run to a terminal state, streaming its events into the store
- `stream_parser.py` — Pure parsing of the stream-json output (no I/O)
- `builds.py` — The spec/brief/plan pipeline behind real, phased,
  multi-session builds (see "The Run Pipeline" above)
- `specs.py` — The review surface: reads back what JARVIS proposed and what a
  build produced
- `projects_view.py` — The Projects tab: a read-only JOIN over runs,
  sessions, plans and the repo, by project
- `history_read.py` — The brain's read-only memory of its OWN earlier
  conversations: `search_history` / `read_history` over its Claude Code
  transcripts (user and assistant text only) and the `journal/`, `memory/` and
  `projects/` Markdown. Allowed roots only, credentials and symlink escapes
  refused; results are untrusted
- `session_watch.py` — Watches Claude Code and Codex sessions through provider adapters. A
  session's state has one source: each provider reads its raw facts into an
  `Activity` and `session_status` alone turns that into the state, what it
  needs, whether it is `between_turns` (the message queue's only test) and
  since when; the transcript's turn markers decide, the roster's word only
  where they are missing
  (process / conversation / project), follows each message JARVIS sent
  through its session's transcript, and reads each session's subagents
- `agent_directory.py` — The one way agents are listed and found: every
  tool that lists agents or takes an agent's name goes through it, matching
  names as they are said (`spoken_key`: hyphens and underscores are spaces).
  An agent has one name, the agents table's `name`
  (`run_store.agent_names`), which the session snapshot goes by, so every
  screen, record, read-back, tool result and lookup says it. It is given
  once, when the agent is first seen (`session_watch.agent_name`: the name
  the user set in Claude Code, else the project's; never a phrase made up to
  tell two apart), and only the user's `rename_agent` changes it, for good.
  Two agents may share a name: they are kept apart by session id, and a name
  that fits both is asked about (`server._ask_which`: the name with a fact
  beside it and the session id). A stopped session's agent is kept,
  ended, for `run_store.AGENT_ENDED_KEEP_SEC` (30 days), so resuming the same
  session brings back its name and activation (`run_store.sync_agents`)
- `session_steer.py` — Sends a message into a running session's inbox socket;
  holds the signature every message starts with
- `session_messages.py` — Reads the messages sessions drop for Tim in
  `data/messages/<project>/` (see "Messages from sessions")
- `workflow_store.py` — The workflow monitor's one durable record (see "The
  workflow monitor"); `workflow_report.py` is the command sessions report with
- `work_store.py` / `work_api.py` / `work_report.py` — The Work tracker's
  record, its routes and shared calls, and the command agents report with
  (see "The Work tracker"); `frontend/src/work.ts`, `WorkPanel.tsx` and
  `WorkDetail.tsx` are the page's side
- `access_keys.py` — Access keys by category, for agents the user lets
  reach some of JARVIS's routes without the tool token
- `session_skill.py` — Installs the talk-to-jarvis skill that tells sessions
  about the needs board and session messages
- `dialog.py` — Answers a permission prompt in a Terminal window by sending
  it a keystroke (needs Accessibility)
- `notifier.py` — macOS notification fallback when no browser tab is
  connected to speak through
- `jarvis_memory.py` — Long-term memory: a folder of plain Markdown files the
  user can read and edit directly, not a database. A project note is one
  stamped line in `projects/<project>.md`, appended by `project_note`; the
  brain reads one project's notes numbered with `project_notes` and, only
  when he asks, changes one with the acting `edit_project_note` (keeps its
  stamp) or `remove_project_note`, each naming the note's number and its
  current text and refused if the file no longer says it; the file is
  rewritten through one rename and every line that is not a note, the
  header included, is kept. Both are `MEMORY_WRITERS`, and every answer
  gives the note's words inside the memory block
- `usage_store.py` — Tracks the subscription's five-hour / seven-day
  rate-limit usage (there is no spend to report — see brain.py)
- `preflight.py` — First-run environment checks: `claude` CLI/login,
  Accessibility, Fish key, cross-session steering
- `claude_env.py` — The environment every spawned Claude Code child gets,
  including the `ANTHROPIC_*` scrub
- `actions.py` — System actions (Terminal, Chrome) via AppleScript
- `browser.py` — Playwright. Only the headless half is live (`read_page`,
  `capture_page`, behind `read_page` / `look_at_page`); the headful
  `JarvisBrowser` search/research class is reachable from nothing but
  `tests/test_browser_integration.py`
- `screen.py` — Seeing the Mac itself: the window list (`osascript`) and one
  downscaled `screencapture` the brain sees as an MCP image block. Captured
  only on a turn the user drove, never persisted, never on a timer
- `repo_read.py` — Cheap, model-free reading of a repository (no `claude`
  subprocess)
- `project_maker.py` — Creates a new project directory from a spoken name,
  path-validated against the projects root
- `work_mode.py` — Vestigial. `is_casual_question` is imported by `server.py`
  and called by nothing; the Haiku-vs-`claude -p` routing it classified for is
  gone. Left in place only because a test pins it
- `team_client.py` / `team_api.py` — The connection to a team server and the
  Team section's routes (see "The team server"), the network exchange and
  `/api/team/network` (see "The Network view")
- `host_resources.py` — The whole machine's CPU and memory (or a limited
  container's), sampled every five seconds for the node update
- `human_activity.py` — When a person last used this JARVIS (page input and
  accepted speech), as a sequence number, age and source; never content
- `brain_accounts.py` — This node's brains for the node update: a keyed hash
  per account, its role, state and quota windows
- `team_server/teamserver/network.py` — The team server's network: node
  records, ownership and ordering, the snapshot, and its own health and
  resources
- `frontend/src/network.ts` — The page's one source for the Network view and
  the Team Server tile: the stored team server snapshot, read and worded
- `frontend/src/NetworkPanel.tsx` / `NetworkTile.tsx` / `NodeView.tsx` — The
  Network tab, one node's tile, and one node in full
- `frontend/src/TeamServerTile.tsx` / `TeamServerView.tsx` — The left
  column's Team Server tile and the team server's health in full
- `frontend/src/humanActivity.ts` — The page's one human-activity collector
- `frontend/src/locale.ts` / `frontend/src/messages/` — The interface's
  language, which is the language setting's (`language_setting.py`), never a
  second preference or browser storage: one store fed by the server's
  `/api/settings/language`, its `language` frame on `/ws/sessions` (published
  by `change`) and that socket's snapshot, a read never undoing a newer
  frame; typed English and Latin American Spanish catalogs with the same
  keys and placeholders, one part per region; a component's words come from
  `useT()` (`t("key", { name })`), numbers and dates from its format helpers.
  Words written in English in a component are the `label-unlocalized`
  coverage rule; elements are found by registered ids, never by their words
- `frontend/src/timeFormat.ts` — How long ago something was, the one way the
  page says it
- `frontend/src/TeamSection.tsx` — Settings → Team: connect, status, members
- `frontend/src/Settings.tsx` / `SettingsRow.tsx` — Settings, a centre view
  opened and closed only by `settings.open`, `settings.section` and
  `settings.close` (the menu, Check for updates, the sign-in reminder and
  first-time setup run them): You, Voice and language, Brains, Team,
  Updates, Server; every control and reading in it a pointer target
- `frontend/src/TeamSignInReminder.tsx` / `teamSignIn.ts` / `team_reminder.py`
  — The sign-in reminder at the top of the centre area, its Sign in, and the
  one spoken reminder per start (see "The sign-in reminder")
- `team_server/teamserver/staging_sessions.py` — The staging-only test sign-in
  (see "The team server")
- `transmission.py` — One message sent on Tim's word, whoever it is for:
  the sequence every agent steer and every message to a person goes through
  (read-back, staging, cancel window, hand-over), with each kind of
  recipient an adapter
- `messaging_runtime.py` — Human messaging put together once at startup
  (see "Messages between people"): store, files, guard, delivery,
  presentation, announcer and the page's routes
- `messaging_store.py` — The local record of human messaging: drafts,
  approved envelopes and sends, messages, receipts, work items, sync
  position, and what is owed to the team server
- `messaging_client.py` — Delivery: the transport over the paired team
  connection and the loop that submits approved sends, reconciles messages
  and settles owed receipts and work commands
- `message_approvals.py` — The owner's approval of a message to a person and
  the one gate (`GuardedTransport`) every outgoing human message passes
- `messaging_api.py` — The page's `/api/team/messages*` routes
- `messaging_files.py` — A message's files: private snapshots, upload,
  verified downloads (quarantined, never opened) and their routes
- `draft_files.py` — Files on this Mac, named by path, onto a draft (the
  brain's `attach_message_files` and `draft_message`'s `files`): the folder
  catalog's ordinary-file rule, a held descriptor reached without links,
  and one save at the exact revision under the same sign-in, all or none
- `attachment_locations.py` — Where a received file is: the location
  result, lookup and ensure-local, leases, extraction records, the two
  routes and the hand-over to agents (see "Received file locations")
- `bundle_archive.py` — Reading a received zip safely: the entry checks,
  budgets, wrapper folder and staged extraction work packages and ordinary
  bundles share (see "Work packages")
- `work_packages.py` — Work packages: unpacking a received zip, finding,
  running a step, its results, and which files the brain may attach; the
  three ways a package runs, an agent package's brief, and a page package's
  files and saved results (see "Work packages")
- `make_package.py` — The one command an agent makes and sends a package
  with (see `docs/making-a-package.md`); `decision_package.py` reads,
  expands, checks, builds and delivers a deck's content
- `package_inbox.py` / `package_inbox_api.py` — The package inbox: its
  record, a deck's questions and answers read as data, and its routes (see
  "The package inbox"); `frontend/src/packageInbox.ts` /
  `QueuePackages.tsx` are the page's side
- `packages_api.py` — The page's `/api/packages*` routes: the packages here,
  a page package's files, what its frame saves, opening it, telling its
  story, and the story helper
- `package_story.py` / `story_teller.py` / `package_story.js` — A page
  package's story: its format and cues, the one service that tells and
  stops it, and the helper that rings its targets inside the frame
- `frontend/src/PackageView.tsx` / `packageBridge.ts` / `packages.ts` — A
  page package in the centre area, its frame's postMessage bridge (save,
  listen), and the page's packages and the one open
- `arrival_announcer.py` — Recording that a person's message has come in,
  as an attention item, under the presentation lease
- `attention_store.py` / `attention.py` / `attention_api.py` — The attention
  record, the one admission policy and digest, and their shared calls (see
  "Attention"); `frontend/src/attention.ts` / `UpdatesNotice.tsx` /
  `CentreNotices.tsx` are the page's side
- `transfer_presentation.py` — The `human_transfer` frames the rays draw,
  and the announcement lease
- `frontend/src/QueuePanel.tsx` / `queue.ts` — The Queue tab, and the Queue's
  live state: its routes read into one record and the owner's commands
- `folder_contract.py` — The folder catalog's words, budgets and one
  refusal (see "The folder catalog")
- `folder_store.py` / `folder_policy.py` / `folder_readers.py` /
  `folder_discovery.py` / `folder_service.py` — Its record, which reader may
  read where, the bounded race-resistant readers, discovery from the
  providers' records, and the one service over them
- `folders_api.py` — The page's `/api/folders*` routes
- `frontend/src/folders.ts` / `FoldersView.tsx` / `FolderDetail.tsx` /
  `FoldersPanel.tsx` — The Folders store, the centre's Folders view and one
  folder in full, and the right panel's Folders tab
- `self_update.py` / `update.py` — Updating a checkout from GitHub, and the
  `python -m jarvis.update` command (see "Self-update")
- `ui_contract/` — The page's navigation and white attention contract:
  `protocol.json` and one file per family in `families/`, merged and checked
  by `ui_contract.load`; `frontend/src/uiContract.ts` is generated from it
  (`scripts/generate_ui_contract.py`), and `tool_specs()` gives the brain's
  `ui_map`, `ui_navigate`, `ui_indicate`, `ui_journey` and `ui_clear`
- `ui_control.py` / `frontend/src/uiClient.ts` — Carrying the brain's UI
  requests to the one page of the turn and its answer back: binding, epochs,
  one deadline, success only at arrival; stopping JARVIS withdraws them
- `frontend/src/uiRegistry.ts` — The one registry of navigation commands
  and mounted targets; `dispatch` (actor always given) and `byHand`
- `frontend/src/uiNavigation.ts` / `uiNavigationReveal.ts` — The route
  state (right tab, centre view, detail slots) and bringing one exact
  presentation into view before anything points at it
- `frontend/src/uiPipeline.ts` / `uiAttention.ts` / `UiPipelineLayer.tsx` —
  The pipeline graph and routes, one cue's lifecycle, and the map and cues
  drawn
- `frontend/src/uiSignal.ts` — The one schedule and path maths for white
  attention, from the contract's timing
- `frontend/src/effectLife.ts` — The one frame scheduler, geometry
  invalidation and effect lifecycle for everything drawn from the orb (rays
  and attention alike)
- `frontend/src/orbSource.ts` — Who holds the orb's overlay (`mainOrb`):
  a real transmission always, attention waits then answers busy
- `server_address.py` — This server's own address (`own`), recorded with
  its pid at startup and removed on shutdown, and the one reader of that
  record (`running`) that `python -m jarvis.update` uses (see "Self-update")
- `bundle_contract.py` / `bundle_inspection.py` / `bundle_readers/` — Reading a
  file someone sent (see "Collaboration bundles")
- `data_paths.py` — Single source of truth for where data is written

## Where the author's own notes live
This repository ships nothing personal. Research notes, milestone
verification checklists and this project's own superpowers specs and plans
were moved to `.agents/`, which is ignored in full — do not re-add them, and
do not treat `git log` as their backup.

Note that `docs/superpowers/specs` and `docs/superpowers/plans` are still a
live convention: `builds.py` and `specs.py` create them **inside the projects
JARVIS builds**. It is only this repository's own copies that are gone.

## Environment Variables
- `ANTHROPIC_API_KEY` — **read by nothing.** Not asked for in setup, not read
  by any module, and the `anthropic` SDK is not a dependency: JARVIS's brain
  and every spawned run go through your Claude Code subscription login.
  `claude_env.child_env()` scrubs every `ANTHROPIC_*` variable from every
  spawned Claude Code child (brain and run pipeline alike) so the CLI can
  never bill an API key instead, and `preflight.py`'s `anthropic_key_leftover`
  check warns you if one is sitting in your `.env` — that is the only place
  the name appears in live code.
- `JARVIS_BRAIN_MODEL` (optional, default `sonnet`) — model for the brain,
  always passed explicitly as `--model`
- `JARVIS_BRAIN_AUTOSTART` (optional, default `1`) — `0` builds the brain but
  never spawns it (every test sets this)
- `JARVIS_BRAIN_CONTEXT_BUDGET` (optional, default `900000`) — tokens of
  conversation before JARVIS asks to compact its context. It never rotates
  without the user's yes; he is asked again every ten turns, and the Claude
  CLI's own auto-compact is the backstop if he never agrees
- `JARVIS_MUTE_MIC_DURING_SPEECH` (optional, default false) — fallback if echo
  rejection is not enough with a given microphone
- `FISH_API_KEY` (required) — Fish Audio TTS
- `FISH_VOICE_ID` (optional) — Voice model ID
- `USER_NAME` (optional) — Your name for JARVIS to use
- `JARVIS_DATA_DIR` (optional) — Where JARVIS writes everything: the SQLite
  database, memory Markdown, usage.json, the tool token, the running
  server's address (`server-address.json`). Defaults to `data/`
  (`data_paths.py`). Set it to run an isolated instance without touching live
  data — the test suite gives every test a fresh one this way.
- `JARVIS_SKIP_PERMISSIONS` (optional) — Defaults to true; passes
  `--dangerously-skip-permissions` to spawned runs, which have no TTY to
  answer a permission prompt
- `WEATHER_LOCATION_LABEL` / `WEATHER_LATITUDE` / `WEATHER_LONGITUDE` /
  `WEATHER_UNIT` (all optional) — override the auto-detected (public-IP)
  weather location and units
- `JARVIS_ALLOWED_ORIGINS` (optional) — extra origins the web boundary
  accepts, comma-separated. Only needed when the page is opened at an
  address JARVIS does not serve from itself (a LAN IP, a tunnel). Their
  host names are also what the `Host` check accepts, so a `.local` or
  tailscale name has to be listed here to work at all
- `JARVIS_DEBUG_DOCS` (optional, default off) — serve `/docs`, `/redoc` and
  `/openapi.json`. That console has a "Try it out" button on every route
- `JARVIS_REFUSE_UNTRUSTED_ACTIONS` (optional, default off) — `1`/`true`/`yes`/`on`
  turns ON the untrusted-content action refusal: a turn that has read foreign
  text may not act, and memory writers are refused for the rest of that
  generation. Off by default, so JARVIS keeps acting after reading foreign
  content. The old `JARVIS_ALLOW_UNTRUSTED_ACTIONS` is obsolete and ignored
- `JARVIS_STT_MODEL_DIR` (optional) — the folder holding the local speech
  models, one folder per model (`<root>/<model name>`). Defaults to
  `<data>/models`; `scripts/setup_speech_input.py` and the install route
  install there, checked against the release's SHA-256
- `JARVIS_ENV_FILE` (optional) — the `.env` JARVIS loads at startup and the
  settings endpoints read and write. Defaults to the repo's own; the test
  suite redirects it so no test reads or rewrites the developer's real
  configuration

- `JARVIS_TEAM_DEV_SERVERS` (optional) — comma-separated exact origins
  (e.g. `http://127.0.0.1:18080`) JARVIS may connect to as a team server
  despite being plain http or on a private address. For running a team
  server locally only

- `JARVIS_FOLDERS` (optional, default on) — `0`/`false`/`no`/`off` switches
  the folder catalog's routes and tools off; its records are kept
- `JARVIS_KNOWLEDGE` (optional, default off) — `1` starts the knowledge
  engine and its ingest (see "The knowledge engine")
- `JARVIS_KNOWLEDGE_MEMORY_MB` (optional, default 384, or 256 under 16 GB)
  — the engine's memory budget; it is restarted past 1.5 times it
- `JARVIS_FOLDER_DISCOVERY` (optional, default on) — `0` stops only the
  background discovery and availability look (every test sets this)

## Testing
The team server has its own suite: `cd team_server/tests && pytest` with its
own environment (`pip install -r team_server/requirements-test.txt`) and a
MongoDB (`TEAM_SERVER_TEST_MONGODB_URI`, default `mongodb://127.0.0.1:27099`).

Run the suite as:

```bash
pytest
```

No flags. `pytest.ini` sets `testpaths` and deselects `-m "not browser and
not page"`, so a bare run touches neither the network nor the screen.

`tests/test_browser_integration.py` is marked `browser` (one `pytestmark` for
the file) because it drives `browser.py`, which launches Chromium with
`headless=False` on purpose and runs live searches. Run those with
`pytest -m browser`; they skip themselves if there is no network.

`tests/local-config.toml` skips tests on one machine only. Git ignores it,
and with no file nothing is skipped; `tests/local-config.example.toml` shows
the format (test node ids or markers under `[skip]`). The two tests marked
`sips` call macOS's real `sips`, so a Mac where `sips` crashes lists that
marker there.

The voice page's browser tests (`tests/test_agents_list_fits.py`,
`test_voice_page_captions.py`, `test_voice_page_mute.py`,
`test_voice_page_rays.py`, `test_voice_page_folders.py`, `test_voice_page_story.py`,
`test_voice_page_quiet_input.py`, `test_voice_page_turns.py`,
`test_voice_page_playback.py`, `test_voice_page_updates.py`, `test_voice_page_agent_names.py`,
`test_voice_page_capture_cues.py`) are marked `page` and deselected too: they build
the page with `npx vite build` into a temporary folder and check it in
headless Chromium on loopback, and their timing depends on how busy the
machine is. Run them with `pytest -m page`; they skip themselves without
node, the frontend's `node_modules`, or Playwright's Chromium.

`tests/test_bundle_readers_mac.py` is marked `osascript`: it runs the PDF
reader against the real PDFKit and skips off macOS; every other bundle
test fakes osascript (`tests/bundle_fixtures.FakeOsascript`).

Everything else uses fakes at the subprocess seam — `screencapture`, `sips`,
`osascript` and `claude` are never really invoked. If you add a test that
needs the network, a real window, or a real `claude`, mark it.

Every CI run uploads a `test-timing` artifact (14 days, failed runs too): both pytest steps' junit XML (`unit.xml`, `page.xml`), the runner's size, and its load sampled every 30 s through the page step; the steps' logs carry `--durations` and a timed bare vite build and Chromium launch.

## Conventions
- JARVIS personality: British butler, dry wit, economy of language
- Max 1-2 sentences per voice response
- The brain reaches JARVIS's capabilities as MCP tools (`jarvis_mcp.py` ->
  `/internal/tool`), not by parsing `[ACTION:X]` tags out of its reply — that
  tag machinery is gone in full, including the last handler (`_execute_browse`),
  which lost its caller with the voice dispatch chain
- Anything the page lets him do or see, JARVIS must be able to do or see
  when he asks by voice: every page feature has a brain tool that calls the
  same service or function as its route (`tests/test_brain_page_parity.py`).
  Changes are ACTING_TOOLS; reads carrying other people's words are wrapped
  as untrusted. Left out on purpose: settings that hold secrets (API keys and
  the Fish key test), `/api/restart` on its own (`update_jarvis` restarts,
  through the same `_restart_server`, once an update is in), and the sign-in
  and connect flows that need a browser approval
- AppleScript for Terminal and Chrome control (no OAuth needed)
- SQLite for runs, run events and usage (`run_store.py`, `usage_store.py`);
  long-term memory is plain Markdown files instead (`jarvis_memory.py`) —
  the user edits it directly, so it is never a database
