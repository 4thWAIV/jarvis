# UI navigation and white pipeline: local validation

Workflow `wf-dc741957b946`, phase `local-validation-ready` done; `human-validation` waiting for Tim. Nothing is merged, pushed or deployed.

## Where

| | |
|---|---|
| URL | http://localhost:5183/ (open in Chrome) |
| Worktree | `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-navigation-pipeline` |
| Branch | `feature/ui-navigation-pipeline` (local only), based on origin/main `91b13fb` |
| Commit | `b701d9b` adds this record; the candidate runs the code of `af9b499` (b701d9b changes only docs) |
| Run folder | `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run` (`R` below) |

## The running candidate

| | |
|---|---|
| Backend | `R/tools/candidate.py serve`: the real JARVIS app from the worktree, `https://127.0.0.1:8350`, fresh self-signed `cert.pem`/`key.pem` in the worktree root (git-ignored; the live key was not copied). PID in `R/candidate/backend.pid` |
| Page | Vite on 5183, `JARVIS_API_TARGET=https://localhost:8350` (the proxy target is configurable; unset it is 8340 as before). PID in `R/candidate/vite.pid` |
| Data / env / homes | `JARVIS_DATA_DIR=R/candidate/data`, `JARVIS_ENV_FILE=R/candidate/.env` (empty, mode 600), `HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and the session homes under `R/candidate/home`. Live `data/`, provider homes, 8340 and 5173 untouched |
| Speech models | English and Spanish Kroko installed into `R/candidate/data/models` by `scripts/setup_speech_input.py --language all` (download, SHA-256 check, install; log `R/logs/candidate-models.log`), so Español is selectable |
| Logs | `R/logs/candidate-backend.log`, `R/logs/candidate-vite.log` |
| Start | `R/start.sh` (each process in its own session via `start_new_session`, survives the shell) |
| Stop | `R/stop.sh` |
| Restart | `R/stop.sh && R/start.sh` |

Fixture seams (in `candidate.py`, outside the repository; nothing else is faked):

- Team network: no team server is contacted and nothing is signed in. `/api/team/network` answers the page tests' snapshot (`tests/test_voice_page_network.py` `_envelope`: "Tim's Mac mini", this Mac, a healthy team server), dated afresh each read, with a `network` hint every 5 s. So the Team Server tile shows Ready and its figures, while Settings, Team still says signed out, and the Queue is off.
- Brain: none is spawned (`JARVIS_BRAIN_AUTOSTART=0`); typed or spoken words get no answer. A stand-in says it is the user's turn so the brain's tools can be called through the real `/internal/tool` with this instance's own tool token.
- Records: workflow "Ledger migration" (3 phases, 3 tasks, a decision, a wait, a blocker, a note) seeded through the real `/api/workflows` routes; folder "Ledger" (`R/candidate/fixtures/ledger`) registered through the real `/api/folders` route.

Readiness, checked from the URL Tim opens: `GET http://localhost:5183/` 200; `/api/workflows` through 5183 returns the candidate's own `wf-2548254143a3` (exists only in the candidate database, so the page talks to 8350, not 8340); `/api/settings/language` both models installed; `/api/team/network` the fixture, `connected`; WebSockets `/ws/sessions` (snapshot frame with `language`), `/ws/voice` and `/ws/transcribe` open through 5183 with the page's origin.

## Evidence

Folder: `R/evidence/local-validation/` (78 files; the drivers are copied beside them).

- Recording: `video/local-validation.webm` (1440×900, full motion, the whole run below).
- `evidence.json`: every step's tool answer, timings, screenshots and the brain path.
- Brain path: fixture stand-in brain (no model) calling the real `POST /internal/tool` (`ui_indicate`, `ui_journey`, `ui_navigate`, `set_language`) with the isolated tool token → `ui_control` → `/ws/voice` → the page → its acknowledgement. The live isolated brain was not used: it would need a Claude login in the isolated home, and copying provider credentials is not allowed.

Each journey has five timed frames from when its signal leaves the orb: `-emit` (0.15 s), `-travel` (0.9 s), `-arrival` (1.75 s), `-hold` (3.2 s), `-reset` (5.6 s, no cue left, map still drawn).

| Journey | Request | Answer | Frames |
|---|---|---|---|
| Left: Team Server database value | `ui_indicate` teamServer `database` | completed, animated; events resolving+emitting+travelling+arrived | `en-left-database-*`, `es-left-database-*` |
| Right: a node's CPU | `ui_indicate` node (Mac mini) `cpu` in `right.network`, reveal navigate, from the Agents tab | completed, animated; the Network tab opened by `right.tab.select` | `en-right-node-cpu-*`, `es-right-node-cpu-*` |
| Main: a workflow task | `ui_journey`: Workflows tab, the workflow's row, `workflow.open`, Tasks tab, the task "Import entries" | completed; both signals animated | `en-main-workflow-task-*`, `es-main-workflow-task-*` |
| Hidden target | `ui_indicate` the blocker "Staging credentials", reveal navigate, workflow closed | completed, animated; effects `centre.view-shown`; Blockers tab shown | `en-hidden-before.png`, `en-hidden-blocker-*` |
| Real transmission at the same time | JARVIS's own `_message_went` (the steer path's "a message went to this session") draws the amber ray; `ui_indicate` asked at once | the ray kept `#fbbf24`; the attention waited for the orb (dashed), then answered `busy / source-busy`; asked again once the orb was free, completed | `en-transmission-attention-waits.png`, `en-transmission-after.png`, `en-after-transmission-*` |
| Idle map | | backbone, rails and sockets drawn with nothing running | `00-idle-map.png` |

Spanish: every region opened by its registered command, each `completed`, no page scrolling sideways: right `es-right-{folders,agents,queue,workflows,network,diagnostics}.png`; centre `es-main-{conversation,folders,voice-diagnostics,subsystem,node,team-server}.png` and the nine workflow tabs `es-main-workflow-*.png`; Settings `es-settings-{brains,voice,you,team,updates,system}.png`; menu `es-menu-open.png` (and `en-menu-open.png`). No missing-translation warning and no console error.

Language-switch steps: `switch-1-menu.png` (menu), `switch-2-settings-voice-en.png` (Settings, Voice), `switch-3-settings-voice-es.png` (Language set to Español by hand: page and server `es`, no reload), then the three journeys in Spanish, then `switch-4-back-to-en-by-tool.png` (the brain's `set_language` English: server `en`, page `en-US`). The candidate is left in English.

## Checks run on the candidate code

- Review finding 1 (stale navigation): `ui_navigate`, `ui_indicate`, `ui_journey` take `expectedNavigationRevision` (from `ui_map`); tests `test_ui_tools.py::test_a_person_navigating_after_the_map_makes_the_pointing_stale`, `::test_every_acting_ui_tool_takes_the_revision_from_the_map`, page test `test_voice_page_navigation.py::test_a_request_planned_before_he_navigated_is_stale_and_leaves_his_view`.
- Review finding 2 (tainted turn): UI tools survive `JARVIS_REFUSE_UNTRUSTED_ACTIONS` unless their commands declare `queue.item-activated` or `queue.read-marked`; `test_untrusted_turn.py::test_pointing_after_reading_the_map_survives_but_opening_his_messages_does_not`.
- `npx tsc --noEmit`: pass. `npx vite build` (scratch outDir): pass. `scripts/generate_ui_contract.py --check`: exit 0.
- Targeted pytest (25 files: the UI contract, control, tools, navigation, pipeline, right, coverage rules, locale, page parity, MCP, header lines, dead code, anchored patterns, internal tool, untrusted turn, tool handlers, MCP config, voice ws, argument echo, show on screen, data paths, system prompt header, brain, web tools, memory writers): 722 passed, 2 skipped.
- `pytest -m page` on pipeline, left_settings, right, centre_nav, navigation, voice_health, workflows, folders, queue, conversation, captions, rays, human_rays, language, settings, subsystems, network, diagnostics, subagents, agents_list_fits, mute, team, team_reminder: 189 passed. `test_voice_page_layout.py`: 11 passed.

Left for CI: the full `pytest` run, `pytest -m page` for refresh, screen and speech_input, the wheel build.

## Not done, and open

- Blocker `leaf-sibling-routing` (waiting for Tim): a part whose leaf would cross a sibling is refused `blocked / no-route`, never drawn across: workflow tabs left of the last, phase steps, Folders tabs, microphone mute, off and health dot, Team Server last write, reminder text and Sign in, workflow Dismiss. Seen in this run too: the task "Ledger schema" (left of "Import entries" in its row) answers no-route, so the journey points at "Import entries".
- Blocker `spanish-register` (waiting for Tim): left column and right panel say usted, centre views say tú. Recommended: usted.
- 22 controls that navigate still have no registered command (`PENDING` in `tests/test_ui_coverage_rules.py`; list in `coverage.md`, "Gaps").
- Localization item 7: team server errors (`team_client`, about 30 sites) and the voice record's cause sentences reach the page in English, marked as original detail.
- Not covered by targets: the task card's progress bar, subagent rows in the right panel.
- Agent and Queue transmissions were not driven in Spanish on the candidate (the Queue needs a team server); the region page tests cover them in English.

## Walkthrough for Tim (about ten minutes)

1. Open http://localhost:5183/ in Chrome. Close the first-time setup if it shows (no Fish key here, so JARVIS stays silent).
2. Look at the faint white map: the line from the orb up to the top, the rails down each side, and the small branches into each tile. It stays while nothing is happening.
3. The three region journeys are in the recording (`video/local-validation.webm`): the left Team Server tile's Database figure, the Network tab's Mac mini CPU, and the workflow's Import entries task. Each one is a white signal leaving the orb, running along the map, rings at the target, an outline, then fading.
4. Nested navigation by hand: Workflows tab, click "Ledger migration", go through its nine tabs. Network tab, click the Mac mini tile. Diagnostics tab, click a row. The menu (⋮), Settings and its sections.
5. Hidden target: in the recording, the Blockers tab opens by itself from a closed workflow before the signal arrives.
6. Ordinary transmissions: in the recording, the amber ray to the Agents tab keeps its colour, and the white signal waits, then reports busy.
7. Spanish: ⋮, Settings, Voice, Language, Español. Everything changes in place with nothing reloaded; change it back the same way.

Stop the candidate when done: `R/stop.sh`.
