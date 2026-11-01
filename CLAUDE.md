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
- **Frontend**: React + TypeScript + Vite. Conversation and detailed agent, workflow and diagnostic views occupy the centre; voice controls and brain usage are on the left; Folders, Agents, Queue, Workflows, Network and Diagnostics are on the right, as six labelled tabs in two rows. The Three.js orb is one control within that workspace.
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
  categories (`captures`, `agents`, `runs`, `workflows`, `watch`), never the voice
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
(`server._rotation_answer`, `ROTATION_YES_MAX_WORDS` still 4), the clear
command, the cancel and replay words and echo rejection (`speech._tokens`),
agent and people names (`agent_directory.spoken_key`, folded, and `_words`) and memory search and slugs
(`jarvis_memory`). Its splitter keeps accented letters ("sí" is a word, not
"s") and `fold` takes accents off before comparing; on ASCII text both give
exactly what the old `[a-z0-9]` splitters gave. Each language's word lists
are one `Vocabulary` there, English and Latin American Spanish (yes: sí, si,
claro, adelante, hazlo, dale, "de acuerdo"; no: no, espera, luego, después,
nunca, negativo, todavía; cancel: espera, espérate, cancela, alto, detente,
never "para"; replay: repite, otra vez). Which list counts is
`spoken_words.active_language()`, the one place the language is decided; it
returns English until the language setting exists, so the Spanish words
match nothing yet. Tests switch it with the `spanish` fixture.

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
`undelivered` and Tim hears "<agent> hasn't picked it up", but it is still
looked for: arriving late (behind a permission prompt he then approves) it is
delivered and answered as usual. Anything unresolved
`session_watch.MESSAGE_WATCH_SEC` (a day) after it was sent is failed, so the
watcher only ever looks for a bounded set; settled messages are never read
again. Seen, he hears "Delivered to
<agent>"; answered, he hears "<agent> has answered your message about …" and
the brain's note carries the message's id. The record is written before each
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
session's sign it was picked up), then says "<agent> has sent you a message"
and nothing of its text. The agent is every listed agent in that project
(`_project_owner`, shared with the needs board). The brain reads the record
with `session_messages` and one message with `read_session_message`, which
marks it read; both taint the turn. Sessions learn both folders from the
talk-to-jarvis skill (`session_skills/talk-to-jarvis/SKILL.md`, outside
`skills/` because that folder is copied into the brain's own home).
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
says when to read again. JARVIS says a new phase, a decision needed, a
blocker, a finish and going quiet, once each. The brain answers anything
about a workflow from `workflow_status`; open decisions are added to the
needs board (`with_decisions`) without writing the sessions' board files,
and he answers them on the Workflows view or by voice: the page's answer and
Dismiss routes and the brain's acting `answer_workflow_decision` and
`dismiss_workflow` share `_answer_workflow_decision` and `_dismiss_workflow`,
which record each as done "on the page" or "by voice".

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
the read-back (`message_approvals.Reviews`), accepted with Send or after
the read-back (`send_message`), staged and offered the cancel window; the
hand-over is the owner's approval (`MessageApprovals.approve`). Nothing
reaches the team server's send route except through `GuardedTransport`,
which releases only the stored approved envelope of a send already on
record as `submitting`; an edit after review makes a new revision that
must be reviewed again. `messaging_runtime.py` builds the one store
(`messaging_store.py`), file snapshots (`messaging_files.py`), guard,
delivery loop (`messaging_client.py`), transfer presentation
(`transfer_presentation.py`, the `human_transfer` frame) and arrival
announcer (`arrival_announcer.py`, the ordinary notice path) at startup,
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

## Work packages
A teammate sends runnable work as a zip with `package.json` at its root,
attached to an ordinary message between people (format:
`docs/work-packages.md`). `work_packages.py` unpacks it into
`data_paths.packages_dir()/<name>-<version>-<id>/`, refusing the whole zip if
any entry escapes the folder or is a link, and removes the quarantine mark.
There is no other sandbox: the team is trusted. The brain's `open_package`
and `run_package_command` are ACTING_TOOLS, so the person's yes in that turn
is the gate. `run_package_command` runs one shell step in the folder with a
10-minute timeout, or in a Terminal window through `actions.open_terminal`.
`list_packages` and `package_results` read what is there. Results go back
through `draft_message`'s `files`, which attaches each file with the page's
own snapshot step (`FileSnapshots.add_to_draft`), so review and approval are
unchanged. The example is `examples/packages/spanish-speech-test/`.

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
(`brain_accounts.py`; no email leaves the machine) and the last human
activity (`human_activity.py`: a sequence number, an age and a source, never
content). The team server binds each node to the member whose session first
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
Team (`openSettings("team")`). Later hides it until the page loads again or
the voice socket reconnects (a JARVIS restart), never for good; it goes by
itself once signed in. Spoken, once per process: `team_reminder.remind_once`,
started by `team_api.start(say=...)` and cancelled by `team_api.stop`, looks
once `SETTLE_SEC` (30 s) after startup and, not signed in, says
"You're not signed in to the team server, sir; messages between people are
off until you are." through `_say_notice` (the ordinary notice path and its
turn-taking); signed in then, nothing is said in that process.

## Subagents
The subagents a Claude Code session dispatches are read from its own files by
`session_watch.read_subagents`, the one reader (the counts on the snapshot
come from it too): `agent-<id>.jsonl` and the `agent-<id>.meta.json` beside it
in `projects/<encoded cwd>/<sessionId>/subagents/`. Finished means its last
message is the assistant's with `stop_reason: end_turn`; otherwise it is
`working` while written within `AGENT_ACTIVE_WITHIN_SEC`, else `stale`, which
claims neither. Deduped by name across roots, capped at `MAX_AGENT_FILES`
(a floor when capped), never through a symlink, never raising. They ride on
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
busiest processes, every line of text the page showed, and each switch of
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
`subsystems` hint on `/ws/sessions` when any state moves, and says once
"Communications with the agents are down, sir." and once that they are back.
A down row is red, and so is the Diagnostics tab whichever tab is open;
clicking a row opens it in the centre area (`SubsystemView.tsx`; voice input
opens the voice view).

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

## Turn-taking
JARVIS waits for Tim to finish. The server gathers what he says
(`server._Utterance`) until he has been quiet for `speech.END_OF_TURN_SEC`, so
a pause mid-sentence does not send the brain half a sentence; typed words go
at once. Nothing JARVIS says starts while he has spoken more recently than
that, replies included, and announcements never start at once.

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
- `tts.py` — Fish Audio synthesis, one request per sentence chunk
- `frontend/src/orb.ts` — Three.js particle orb visualization
- `frontend/src/voice.ts` — Audio playback; re-exports voice input
- `frontend/src/voiceInput.ts` — The microphone: one getUserMedia stream,
  an AudioWorklet (`pcm-worklet.ts`, `pcm.ts`) making 16 kHz PCM16, streamed
  to `/ws/transcribe`; its finals go to `/ws/voice` as before
- `transcriber.py` — Local speech recognition: the model list (`MODELS`,
  English and Spanish Kroko, each with its digest) and where each lives, the
  sherpa-onnx engine, and the worker that owns it (one model loaded at a
  time, `switch` loads another in its place; one capture at a time, overload
  refused rather than trimmed)
- `speech_models.py` — Installing a speech model (`install`: download,
  SHA-256 check, safe extract, one rename; `scripts/setup_speech_input.py`
  is a thin command over it) and the service the page's
  `/api/speech/models*` routes and the brain's `speech_models`,
  `install_speech_model` and `switch_speech_model` share. A switch ends the
  open capture with a non-incident `interrupted` (the page reconnects and
  waits while it loads) and is a `model-load` event in the voice record.
  Choosing and keeping the language is the language setting's, not this
  module's
- `transcription_api.py` — `/ws/transcribe`; the protocol is
  `jarvis/transcribeProtocol.json`, read by both sides
- `voice_diagnostics.py` — The voice record (see "Voice diagnostics");
  `frontend/src/voiceHealth.ts` is the page's side of it
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
  An agent's name is the one set in JARVIS (`rename_agent`), else the one
  the user set in Claude Code (`session_watch.agent_name`, which survives a
  resume), else the derived voice name. A stopped session's agent is kept,
  ended, for `run_store.AGENT_ENDED_KEEP_SEC` (30 days), so resuming the same
  session brings back its name and activation (`run_store.sync_agents`)
- `session_steer.py` — Sends a message into a running session's inbox socket;
  holds the signature every message starts with
- `session_messages.py` — Reads the messages sessions drop for Tim in
  `data/messages/<project>/` (see "Messages from sessions")
- `workflow_store.py` — The workflow monitor's one durable record (see "The
  workflow monitor"); `workflow_report.py` is the command sessions report with
- `access_keys.py` — Access keys by category, for agents the user lets
  reach some of JARVIS's routes without the tool token
- `session_skill.py` — Installs the talk-to-jarvis skill that tells sessions
  about the needs board and session messages
- `dialog.py` — Answers a permission prompt in a Terminal window by sending
  it a keystroke (needs Accessibility)
- `notifier.py` — macOS notification fallback when no browser tab is
  connected to speak through
- `jarvis_memory.py` — Long-term memory: a folder of plain Markdown files the
  user can read and edit directly, not a database
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
- `frontend/src/timeFormat.ts` — How long ago something was, the one way the
  page says it
- `frontend/src/TeamSection.tsx` — Settings → Team: connect, status, members
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
- `work_packages.py` — Work packages: unpacking a received zip, finding,
  running a step, its results, and which files the brain may attach (see
  "Work packages")
- `arrival_announcer.py` — Saying that a person's message has come in,
  through the ordinary notice path
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
  database, memory Markdown, usage.json, the tool token. Defaults to `data/`
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
`test_voice_page_rays.py`, `test_voice_page_folders.py`) are marked `page` and deselected too: they build
the page with `npx vite build` into a temporary folder and check it in
headless Chromium on loopback, and their timing depends on how busy the
machine is. Run them with `pytest -m page`; they skip themselves without
node, the frontend's `node_modules`, or Playwright's Chromium.

Everything else uses fakes at the subprocess seam — `screencapture`, `sips`,
`osascript` and `claude` are never really invoked. If you add a test that
needs the network, a real window, or a real `claude`, mark it.

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
  the Fish key test), `/api/restart`, and the sign-in and connect flows that
  need a browser approval
- AppleScript for Terminal and Chrome control (no OAuth needed)
- SQLite for runs, run events and usage (`run_store.py`, `usage_store.py`);
  long-term memory is plain Markdown files instead (`jarvis_memory.py`) —
  the user edits it directly, so it is never a database
