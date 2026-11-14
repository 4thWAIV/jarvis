# Every setting in Settings

Tim's direction, approved as written (`.agents/in-progress/settings-only/request.md`):
"All settings move into Settings. There can be no environment setting without a
Settings screen control. It becomes a rule of JARVIS: no one ever leaves the
dashboard for any reason." This replaces the earlier knowledge-settings-only
proposal.

Today `.env` is loaded into `os.environ` at import (`server.py`, `_parse_env_lines`),
and only six names can be written from the page (`SETTABLE_ENV_KEYS`: `FISH_API_KEY`,
`FISH_VOICE_ID`, `USER_NAME`, `HONORIFIC`, `JARVIS_TTS_PROVIDER`, `JARVIS_LOCAL_VOICE`).
Brains, engines, voice input, attention and knowledge already keep records or
files written by Settings. Everything else is read from the environment.

## 1. Inventory

Found by searching `jarvis/`, `scripts/` and `frontend/` for `os.getenv`,
`os.environ`, helpers (`_switched_on`, `_resolve_bound`, `DEV_SERVERS_ENV`,
`EPHEMERAL_ENV`) and `process.env`. `scripts/` reads none.

Areas are the record `/settings/<area>.settings`; **start** is the start file (section 2).

| Name | Controls | Class | Where it goes |
|---|---|---|---|
| FISH_API_KEY | Fish Audio key | SETTING | Voice; Keychain |
| FISH_VOICE_ID | Fish voice | SETTING | Voice (control exists) |
| JARVIS_TTS_PROVIDER, JARVIS_LOCAL_VOICE | Fish or local voice | SETTING | Voice (control exists) |
| JARVIS_MUTE_MIC_DURING_SPEECH | mic muted while speaking | SETTING | Voice, `voice_control` |
| JARVIS_STT_MODEL_DIR | speech models folder | SETTING | Voice, next restart |
| USER_NAME, HONORIFIC | how he is named | SETTING | You (control exists) |
| WEATHER_LATITUDE, _LONGITUDE, _LOCATION_LABEL, _UNIT | weather place and units | SETTING | You |
| JARVIS_DEFAULT_BROWSER | browser JARVIS drives | SETTING | You |
| JARVIS_BRAIN_PROVIDER, JARVIS_BRAIN_MODEL | brain and model | SETTING | Brains (`brains.json`, exists; env override removed) |
| JARVIS_BRAIN_EFFORT, JARVIS_BRAIN_TURN_TIMEOUT, JARVIS_BRAIN_CONTEXT_BUDGET | effort, turn bound, compaction point | SETTING | Brains |
| JARVIS_CLAUDE_PATH, JARVIS_CODEX_PATH | program each brain runs | SETTING | Brains (see Needs the owner) |
| JARVIS_USAGE_POLL | reading subscription usage | SETTING | Brains |
| JARVIS_BRAIN_AUTOSTART | start the brain with JARVIS | SETTING | Server, start |
| JARVIS_RUN_MODEL | default run model | SETTING | `engine_choice` "agent runs" (exists) |
| JARVIS_RUN_TIMEOUT_SEC, JARVIS_RUN_IDLE_SEC | run bounds | SETTING | Agents |
| JARVIS_SKIP_PERMISSIONS | runs skip permission prompts | SETTING | Agents |
| JARVIS_STEER_CANCEL_WINDOW | cancel window before a send | SETTING | Agents |
| JARVIS_CLAUDE_CONFIG_DIRS, JARVIS_CODEX_SESSIONS_HOME | extra session homes watched | SETTING | Agents |
| JARVIS_WATCH_INTERVAL, JARVIS_SPECS_POLL | session and specs poll | SETTING | Agents |
| JARVIS_PROJECTS_DIR, JARVIS_PROJECT_ROOTS | new-project folder, project roots | SETTING | Projects |
| JARVIS_SCAN_BUDGET, JARVIS_SCAN_CACHE | project scan time and cache | SETTING | Projects |
| JARVIS_KNOWLEDGE | ingest on/off | SETTING | Knowledge (exists; env fallback removed) |
| JARVIS_RUN_CONTEXT | context pack for runs | SETTING | Knowledge |
| JARVIS_FOLDERS, JARVIS_FOLDER_DISCOVERY | folder catalog, discovery | SETTING | Knowledge |
| JARVIS_KNOWLEDGE_MEMORY_MB | engine memory budget | SETTING | Knowledge, start |
| JARVIS_REFUSE_UNTRUSTED_ACTIONS | untrusted-content refusal | SETTING | Safety (new section) |
| JARVIS_ALLOW_UNTRUSTED_ACTIONS | obsolete, ignored | SETTING | read deleted; covered by the one above |
| JARVIS_TEAM_DEV_SERVERS | local team servers allowed | SETTING | Team |
| JARVIS_ALLOWED_ORIGINS | extra page origins | SETTING | Server, start |
| JARVIS_DEBUG_DOCS | `/docs` console | SETTING | Server, start |
| JARVIS_BIND_HOST, JARVIS_PORT, JARVIS_SCHEME | the bind, published by `server.main` to itself and children | WIRING | the choice is Server, start (address, port, HTTPS) |
| JARVIS_TOOL_URL, JARVIS_TOOL_TOKEN_FILE | brain's MCP child finds JARVIS | WIRING | |
| JARVIS_AGENT_SESSION | an agent's session for its commands | WIRING | |
| JARVIS_ACCESS_KEY | legacy key for `workflow_report` | WIRING | goes with `legacy_access` |
| JARVIS_DECISION_CHECK_SERVER | isolated decision-check JARVIS | WIRING | |
| JARVIS_URL | address for `update`/`workflow_report` | WIRING | `server_address.running()` is the one reader |
| JARVIS_DATA_DIR | data folder of an isolated instance | WIRING | the user's choice is Server, Data folder |
| JARVIS_PYTHON, JARVIS_SOURCE, PACKAGE_DIR | package commands | WIRING | |
| MSYS_NO_PATHCONV | Windows child path handling | WIRING | |
| JARVIS_API_TARGET, JARVIS_VITE_PORT | Vite proxy target and port (`vite.config.ts`) | WIRING | Vite reads the start file; env only from a parent |
| CLAUDE_CODE_MESSAGING_TOKEN, CLAUDECODE | given by Claude Code | PLATFORM | |
| CLAUDE_CONFIG_DIR, CODEX_HOME | provider homes | PLATFORM | |
| APPDATA, LOCALAPPDATA, HOME, PATH, TMPDIR, USER | OS locations | PLATFORM | |
| ANTHROPIC_*, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ORG_ID, OPENAI_ORGANIZATION, OPENAI_PROJECT, CODEX_API_KEY | read only to scrub from children | PLATFORM | |
| JARVIS_ENV_FILE | `.env` used for the one-time import | TEST | |
| JARVIS_ENGINE_AUTOSTART | engine not started | TEST | records live in the engine, so JARVIS cannot run without it |
| JARVIS_ENGINE_EPHEMERAL | throwaway engine | TEST | |

Tests that set SETTING names today (`conftest` sets `JARVIS_BRAIN_AUTOSTART`,
`JARVIS_KNOWLEDGE`, `JARVIS_USAGE_POLL`, `JARVIS_FOLDER_DISCOVERY`,
`JARVIS_PROJECTS_DIR`, `JARVIS_STT_MODEL_DIR` and others) set them through the
same record or start file in their throwaway data folder, using one fixture.

## 2. Starting before records are open

A few choices are needed before the knowledge engine is up: the bind address,
port, HTTPS, data folder, brain autostart, allowed origins, the docs console and
the engine's memory budget. They are kept in one start file,
`<data folder>/settings/start.json`, read by `start_settings.py` (the only reader
and writer) before anything else starts. The default data folder is the place a
fresh process can always find. If the file names another data folder, the
server uses that folder from then on.

- Only Settings writes it (`start_settings.change`, under one atomic rename, 0600).
  The file carries a SHA-256 of its body keyed with the tool token. A file edited
  by hand fails that check. JARVIS then starts on the safe defaults
  (127.0.0.1:8340, HTTPS when certificates exist), and the Server row in
  Diagnostics plus an attention item say why. A hand edit is never trusted.
- These controls are labelled "takes effect at next restart". Next to them is
  Restart now (`_restart_server`).
- If a restart fails to bind with the new address or port, the file goes back to
  the last start that served, so the page always comes back.
- `JARVIS_DATA_DIR` (WIRING) points an isolated instance at its own folder, and
  that folder's own start file applies there.

Every other SETTING is a record (`/settings/<area>.settings`, through
`records.py`, one module per area like `knowledge_settings.py`, with
`read`/`change`, one route, one brain read tool and one acting tool). Secrets go in the
Keychain through `team_client`'s `security` store (service `JARVIS secrets`),
never in a record. **Import once:** at the first start, `settings_import.py`
reads `.env` with `_parse_env_lines`, copies each SETTING it finds into its
record, Keychain item or start file, and records `/settings/env-import.settings`.
After that `.env` is never loaded. The boot loader, `_read_env` and
`_write_env_key` are deleted, along with `SETTABLE_ENV_KEYS`, which the records'
own validators replace. Settings, Server says "Imported from .env on <date>".

## 3. Enforcement

`jarvis/env_names.py` is the one registry. Each name has its class and what it
controls. A SETTING also names its area, key, Settings target id (ui_contract)
and brain tools. `env_names.read(name)` is the only environment reader. It refuses
an unknown name and every SETTING. `env_names.child(**wiring)` builds a child's
environment and accepts WIRING names only.

`tests/test_env_registry.py` fails when:
- code under `jarvis/` or `scripts/`, or `frontend/vite.config.ts`, reads the
  environment anywhere except `env_names.py` (checked by an AST walk for `os.getenv`, `os.environ`
  reads and subscripts, `environ.get`; and `process.env` in the Vite config). The one exception
  is copying the whole environment for a child, which must go through `child`;
- a name read or set is not in the registry;
- a SETTING has no target in `ui_contract`, no read and acting tool in
  `TOOL_SPECS`, or no words in both catalogs (it runs alongside
  `test_brain_page_parity.py`);
- CLAUDE.md's "Settings" list and its "Not settings" list differ from the
  registry.

## 4. CLAUDE.md

The rule, under Conventions:

> Every choice a person makes is a Settings control. No one ever leaves the
> dashboard for any reason. Nothing reads a setting from the environment.
> SETTING values are records (secrets in the Keychain, start-up choices in the
> start file only Settings writes), each with a brain tool. The environment
> carries only WIRING, PLATFORM and TEST names, every one listed in
> `env_names.py`, and `tests/test_env_registry.py` holds it.

"Environment Variables" becomes **"Settings"**. It is a list of each Settings
section and its controls (You, Voice and language, Brains, Agents, Projects,
Knowledge, Safety, Team, Updates, Server), with the start-up ones marked "next
restart". After it comes a short **"Not settings"** paragraph: the WIRING names (set by
JARVIS for its children), the PLATFORM names (read to find things) and the TEST
names (the suite's harness). None of them is for a person to set. Setup step 1
("Copy jarvis/.env.example to .env") and the `.env.example` file are removed.
The Fish key is entered in first-time setup.

## Needs the owner

1. **The brain's program (`JARVIS_CLAUDE_PATH`, `JARVIS_CODEX_PATH`).** This is
   the binary the brain is spawned from. I propose a page-only control (his click,
   page origin), with a read tool but no acting tool. That is an exception to
   page parity, like secrets.
2. **Network exposure (allowed origins, a non-loopback host).** Same question:
   should only his click be able to do this?
3. **A start setting that leaves the page unreachable while the server is up**
   (a Vite port mismatch, a firewall). The rollback covers a failed bind, not
   this case. Is "restart on the defaults" from the command line an accepted
   exception?
4. **Command-line `--host`, `--port`, `--ssl`.** Keep them as one-run overrides
   (shown in Settings), or remove them?
5. **Changing the data folder.** Should it only point at the new folder, or also
   move the data?
6. **HTTPS.** Should Settings make the certificate itself (`openssl`, already
   needed by setup) when HTTPS is switched on?

## As built (pending the owner's answers)

The six questions above are still his. Until he answers, the build takes the
proposal or the smallest change, so each can be reversed:

1. The brain's programs (`claude_path`, `codex_path`): page-only, read by
   `jarvis_settings`, refused by `set_jarvis_setting`.
2. The address (`host`) and the extra origins (`allowed_origins`): page-only
   in the same way. The port, HTTPS and the rest of the start file the brain
   may change.
3. No exception is written; a start whose address or port cannot be bound
   falls back to the last one that served.
4. `--host`, `--port` and `--ssl` stay as one run's own, saved nowhere.
5. No data folder control: `JARVIS_DATA_DIR` stays WIRING (an isolated
   instance), and the data folder is the default.
6. Settings does not make the certificate; HTTPS serves when `cert.pem` and
   `key.pem` are there and the switch is on.

Also as built: `.env.example` is removed; the start file's problem is said in
Settings, Server (a Diagnostics row and an attention item for it are not
built yet); `JARVIS_ALLOW_UNTRUSTED_ACTIONS`, which was read only to log that
it is ignored, is read by nothing. A test sets a setting with
`tests/settings_fixtures.hold_setting` (held for the process, never saved).
