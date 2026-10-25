# JARVIS — Voice AI Assistant

## Overview
JARVIS (Just A Rather Very Intelligent System) is a voice-first AI assistant for macOS. It runs locally on your machine, driving Claude Code for development tasks — every execution recorded as a *run* and watchable at `/dashboard`.

## Quick Start
Read `skills/jarvis-setup/SKILL.md` first — it carries setup and debugging
facts (mic-in-Chrome-only, the per-port permission trap, subscription vs. API
key, what an expired login sounds like, Accessibility) that were only learned
by hitting them live, and this walkthrough assumes them.

When a user clones this repo and starts Claude Code, help them:
1. Copy .env.example to .env
2. Install Claude Code (`npm install -g @anthropic-ai/claude-code`, 2.1.224 or
   newer) and log in with `claude` — JARVIS's brain runs on your Claude
   subscription, not on an API key
3. Get a Fish Audio API key from fish.audio (required — there is no fallback
   voice)
4. Install Python dependencies: pip install -r requirements.txt
5. Install the Playwright browser: python -m playwright install chromium
   (`read_page` / `look_at_page` need it)
6. Install frontend dependencies: cd frontend && npm install
7. Generate the SSL certs — see below, they are NOT optional:
   `openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'`
8. Run the backend: python server.py --host 127.0.0.1
9. Run the frontend: cd frontend && npm run dev
10. Open Chrome (mic only works in Chrome) to http://localhost:5173
11. Click to enable audio, speak to JARVIS
12. Teach your own Claude Code and Codex sessions to reach you through
    JARVIS: `python session_skill.py install`

**The certs are required for the dev-server workflow**, despite `server.py`
treating them as optional. `server.py` serves HTTPS only when `cert.pem` and
`key.pem` are both beside it, but `frontend/vite.config.ts` hard-codes its
proxy target as `https://localhost:8340`. Measured with a stand-in backend on
8340 and `npx vite`: with a plain-HTTP backend the Vite dev server itself
answers 200 while every proxied `/api` request returns **500**; with an HTTPS
backend the same request returns 200. The page loads either way, which is why
this presents as "the UI is up but nothing works" rather than as an error.

## Architecture
- **Backend**: FastAPI + Python (server.py, ~5800 lines)
- **Frontend**: Vite + TypeScript. The voice page is React, with the
  Three.js audio-reactive orb as one control in its upper left; the
  dashboard is vanilla TS
- **Communication**: WebSocket (JSON messages + binary audio)
- **AI**: a long-lived Claude Code process (`brain.py`, Sonnet by default) on
  the user's subscription; no Anthropic API calls on the voice path
- **TTS**: Fish Audio with JARVIS voice model
- **System**: AppleScript for Terminal and Chrome integration
- **Runs**: every Claude Code execution goes through one recorded pipeline —
  `run_store.py` (SQLite) + `run_executor.py`, surfaced at `/api/runs`,
  `/ws/runs`, and the `/dashboard` UI
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
  socket, settings or `/internal/tool`. `python access_keys.py create
  <label> <category>...` prints the key once; only its digest is stored.

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
`python session_skill.py install` renders it with this install's data paths
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
and he answers them on the Workflows view.

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

Two more rules for this area: no new npm or Python dependencies, and the
dashboard never uses `innerHTML` / `insertAdjacentHTML` — it renders
arbitrary LLM and file content, so everything goes through
`createElement` / `textContent`.

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
- `frontend/src/voice.ts` — Web Speech API + audio playback
- `frontend/src/main.tsx` — Renders the voice page with React (`App.tsx`;
  the orb is `OrbControl.tsx`)
- `frontend/src/jarvis.ts` — The voice page's state machine, started once
  the orb is on the page
- `frontend/src/dashboard/` — The `/dashboard` run monitor (vanilla TS)
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
- `session_watch.py` — Watches every Claude Code session on the machine
  (process / conversation / project), and follows each message JARVIS sent
  through its session's transcript
- `agent_directory.py` — The one way agents are listed and found: every
  tool that lists agents or takes an agent's name goes through it, matching
  names as they are said (`spoken_key`: hyphens and underscores are spaces)
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
- `JARVIS_ENV_FILE` (optional) — the `.env` JARVIS loads at startup and the
  settings endpoints read and write. Defaults to the repo's own; the test
  suite redirects it so no test reads or rewrites the developer's real
  configuration

## Testing
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
`test_voice_page_rays.py`) are marked `page` and deselected too: they build
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
- AppleScript for Terminal and Chrome control (no OAuth needed)
- SQLite for runs, run events and usage (`run_store.py`, `usage_store.py`);
  long-term memory is plain Markdown files instead (`jarvis_memory.py`) —
  the user edits it directly, so it is never a database
