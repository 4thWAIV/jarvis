# UI navigation and white pipeline: local validation

Workflow `wf-dc741957b946`, phase `local-validation-ready` done; `human-validation` waiting for Tim. Nothing is merged, pushed or deployed.

## Merged with main (2026-11-03)

Merge commit `f57dfb7` (`fd3a651` + origin/main `57cc964`: #48, #49, #50, #51). The 16 conflicting files were resolved in that one commit, keeping both sides.

What main brought is on the navigation contract and in both languages:
- Package view and a received file are centre details, like the Queue item: `package.open` / `package.close` and `bundle.member.open` / `close`, with `bundle.member.view` (preview or text, which page) and `bundle.member.more`. Open page, Open, Close, the server's `package_page` and `bundle_view` frames (run as the brain's) and the brain's navigation all go through them. Tabs "Work package" / "Paquete de trabajo" and "File" / "Archivo"; `#package-view` and `#bundle-member-view` in the corridor.
- An archive's contents in the Queue: `bundle.contents`, `bundle.folder` and `bundle.folder.more`. Each member is a `bundleEntry` target (name, Open). A file's Download, location, Copy, Reveal, Open page, Contents, path and More are parts of its `queueAttachment`. A message's request line and a draft's request fields are `request` parts.
- Settings, Updates: the version picker's words, LATEST, why a version cannot be chosen, the confirm and "Yes, install PR N", and the new refusal codes `not_newer`, `not_offered` and `version_ci_not_passed`, in English and Spanish. The radios are marked `input` and Install `restart`. No Settings target was added; Settings is to move into the centre next.
- The simpler Queue's shorter words replace the catalog's, in both languages. Bundle words (`main.bundle.*`) and location states (`main.queue.location.*`) come from the catalog; a reason the catalog does not know keeps the contract's English.
- English dates on the picker follow the interface's `en-US` ("Nov 1"), as every other date does.

Coverage (reach sweep, 283 contract target/parts, up from 259 with the new kinds and parts): 232 reached, 6 refused, 31 mounted with the part not on show, 14 never registered. The figures are the same in English and Spanish at 1440x900 and 1280x800. The six refused are Settings' targets while Settings covers the orb, as before (`reach-remainder` is still open). Every new package and received-file target and part is reached. The five new parts not on show are conditional, with nothing in the fixtures to show them: a file's Open page and More, a message's and a draft's request line, and the archive path (shown in the sweep's sources scene, but the sweep stops trying a part after three misses in earlier scenes). Sweep: `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/evidence/merge-main/sweep/` (`summary.txt`); the sweep added a scene with the bundle fixtures and a page package.

Checks on the merge: `npx tsc --noEmit` pass; `generate_ui_contract.py` regenerated; targeted pytest (UI, brain-page parity, header lines, data paths, self-update, messaging, bundles, attachment locations, packages, untrusted turn) 1160 passed; `pytest -m page` on queue, bundles, packages, navigation, centre_nav, remaining_nav, settings, left_settings, coded_words, language, pipeline, human_rays and speech_input: 94 passed after three expectations in the Settings tests moved to the picker's words.

## Fourth candidate: tour routing (2026-11-02)

Tim, through JARVIS, after watching the third candidate: "keep the route on the main line, the primary channel, for as long as possible; reduce the early cut-ins and branch off only when needed. Use the border or box around a control as an intermediate point: first draw attention to the box, then move from its border toward the controls inside it. Use surrounding controls as transition points where that improves the route. Make the movement clearer and better."

What changed (`9184754`; the rules in full are in `execution.md`, "Tour routing"):
- Trunk first. The signal stays on the primary channel (the orb's rise, the top pipeline, then the target's rail down its corridor) to the latest branch point that still reaches the target. That is level with the target, straight in, when that run crosses no control, line of text or other object anywhere on the page. Otherwise it is level with the gap beside the neighbouring row, along that gap past the row, and into the target's top or bottom edge. The third candidate cut in at each object's top and ran down inside it. None of the nine tour routes does that now.
- Boxes as waypoints. Each box holding the target is marked in turn, outermost first: the right panel, a tile, the node's or the workflow's detail, a tab strip, the phase stepper. As the signal reaches a box's border it stops for 450 ms and the box is outlined, fading over 900 ms. Then the signal runs on from that border toward the control.
- Neighbouring rows as transition points. Where the straight way in is blocked, the route passes the row before (or after) the target in the gap beside it, never across it. Nothing is marked on the neighbouring row.
- Clearer movement. The line grows behind the first pulse instead of appearing whole. Travel is slower: 2.0 s of movement instead of 1.4 s, plus 0.45 s per box. A stop arrives 2.2 to 3.1 s after it leaves the orb. Nothing is drawn at rest, as before.
- Kept: lines never cross text or controls (now checked across the whole page, not only inside the target's object), the lanes, corridor and 45-degree bevels, usted, stable semantic IDs.

Tour length: the tour is 9 signals for 7 showings, 2.5 s apart. Typed "tour" to "That was the tour, sir." took 43.5 s. "recorrido" to "Ese fue el recorrido, señor." took 44.2 s. The third candidate's tour was about 30 s.

| | |
|---|---|
| URL | http://localhost:5183/ (open in Chrome) |
| Code | `9184754` on `feature/ui-navigation-pipeline` (local only) |
| Backend | https://localhost:8350, restarted on this code with `stop.sh` and `start.sh`; the page, `/api/workflows` and `/api/settings/language` answer through 5183 |

The tour's stops, each route in order:

| Stop | Route |
|---|---|
| Mac mini CPU, right | down the right rail to the CPU row's level; marks the right panel, then the tile; into the CPU row |
| Mac mini CPU, centre | down the main rail to the value's level; marks the node's detail; into the value |
| Database | down the left rail to the row's level; marks the Team Server tile; into the row |
| Mute | down the left rail to the lane under the mic row; marks the mic control; up into the mute button |
| Design phase step | down the main rail to the lane between the tabs and the stepper; marks the workflow's detail, the tab panel, then the phase stepper; down into the step |
| Decisions tab | down the main rail to the lane over the tab strip; marks the workflow's detail, then the tab strip; down into the tab |
| Folders' Registered tab | down the main rail to the lane under the Folders tabs, under the folder pane's text; marks the folders list, then the tab strip; up into the tab |
| Workflow row (right) | down the right rail to the row's level; marks the right panel; into the row |
| Import entries task | down the main rail to the task's level; marks the workflow's detail, then the tab panel; into the task |

Coverage (reach sweep, 259 contract target/parts): 213 reached, 6 refused, 26 mounted with the part not on show, 14 never registered. The figures are the same in English and Spanish at 1440x900 and 1280x800. The third candidate reached 211 with 8 refused. Two more are reached now: the right panel's agent name, over its row and never across the agent's orb, and the network tile's presence dot. The six refused are Settings' targets, while Settings covers the orb.

Evidence in `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/evidence/demo/`:
- `tour-routing-en-es.mp4` (also `.webm`, 2 min 15 s): the page at rest, the tour in English, "switch to Spanish", the recorrido, and back to English.
- `tour-routing/stills/`: for each stop, `<en|es>-<nn>-mark<k>.png` as each box is marked, and `<en|es>-<nn>-arrival.png` at arrival (44 stills).
- `tour-routing/tour-routing.json`: each signal's edges, the boxes it marked in order, its paths and its timings. 0 console errors; 0 pipeline elements drawn at rest and after.
- `tour-routing/at-rest.png`.
- Sweep: `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/evidence/tour-routing/sweep/` (`summary.txt`).
- Recorder: `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/tools/tour_routing.py` (the same method as `demo_check.py`, headless Chromium at 1440x900, recording video).

What Tim does:
1. Open http://localhost:5183/ in Chrome. Close the first-time setup if it shows. Nothing of the pipeline is drawn.
2. Click the Conversation box, type `tour`, and press Enter. For each stop, the white line runs from the orb along the top and down the edge channel. It leaves the channel only level with the target, or with the gap beside its row. Each box it enters (the panel, the tile or view, the tab strip) is outlined in turn while the pulses wait at its border. Then the line goes in to the control, with rings and an outline.
3. Type `switch to Spanish`, then `recorrido`. Type `cambia a inglés` to go back.

### Independent check of the fourth candidate (2026-11-02)

Driven on the running candidate (http://localhost:5183) with Playwright, headless Chromium, at 1440x900 and 1280x800: `tour`, `switch to Spanish`, `recorrido`, `cambia a inglés`. A frame about every 250 ms through each stop, each route's points at arrival, and every point every 2 px from the orb to the target checked here against every line of text and control on the page (not the page's own rules). Script and frames: `/private/tmp/claude-501/-Users-timothystockstill-code-macos-4thWAIV-jarvis/a5b4be76-7b3d-4a0c-9220-817d38b9e5f3/scratchpad/check/` (`capture.py`; `after1440x900/`, `after1280x800/`).

Each stop against Tim's direction (same in English and Spanish, both widths):

| Stop | Route as seen | Main line until it must leave | Box, border, then in | Neighbours used | Crosses text |
|---|---|---|---|---|---|
| Mac mini CPU, right | top line to the right rail, down to the CPU row; panel outlined, then the tile; straight into the row | yes | yes | not needed | no |
| Mac mini CPU, centre | main rail down to the value's level; the node's detail outlined; straight in to the value | yes | yes | not needed | no |
| Database | left rail down to the row; the tile outlined; straight in | yes | yes | not needed | no (crosses the tile's server icon, a graphic) |
| Mute | left rail down to the gap under the mic row; the mic control outlined; along under the meter and the mic button, ends under mute | yes | yes | yes, passes under the meter and mic | no |
| Design phase step | main rail down to the gap over the stepper; detail, tab panel, stepper outlined in turn; along the gap over build and rollout, down into design | yes | yes (after the fix) | yes, passes over build and rollout | no |
| Decisions tab | main rail down to the gap over the tab strip; detail, then strip outlined; along the gap over Events and Blockers, into Decisions | yes | yes | yes | no |
| Folders' Registered tab | main rail down to the gap under the tabs (above them in Spanish); list, then strip outlined; along past Discovered, into Registered | yes | yes | yes | no |
| Workflow row, right | right rail down to the row; panel outlined; straight in | yes | yes | not needed | no |
| Import entries task | main rail down to the task's level; detail, then tab panel outlined; straight in | yes | yes (after the fix) | not needed | no |

- Nothing drawn at rest, between stops or after the tour (0 pipeline elements each time).
- Pace: each signal arrives 2.6 to 3.8 s after it leaves the orb, and stops visibly at each border. The line grows behind the pulse. Easy to follow. The tour took 43 to 44 s in each language.

Defects found and fixed (`5305463`):
1. The task and the phase step sit in the workflow's bordered tab panel (the red-edged box under the tabs), but the route never marked it: it outlined the whole detail and then ran across the empty panel straight to the control. The panel is now a registered box, `main.workflow.view`, marked between the detail and the stepper or task. The tour test expects it.
2. `resolveRoute`'s doc comment had been left above the new section header, away from the function. It is back on the function.

Seen and left as they are (within the approved rules):
- On Mute, Decisions and the Folders tab, the gap beside the row is only 8 px, so the lane runs 4 px from the row and the final turn into the control is 1 px. The control is marked and ringed, but the "drop" into its edge is not visible as a drop. A wider gap would need layout space; not changed.
- On Database the line still crosses the Team Server tile's server icon (a graphic, not text). Every way in at the Database row's height crosses the icon or the word Ready.

Tests after the fix: `npx tsc --noEmit` pass; `npm run build` (scratch `--outDir`) pass; contract `--check` exit 0; ui unit tests 142 passed; `pytest -m page tests/test_voice_page_pipeline.py` 14 passed; `pytest -m page` right, navigation and workflows 16 passed. No full suite. Candidate restarted on 5183 with `stop.sh` and `start.sh`; page, `/api/workflows` and `/api/settings/language` answer 200.

## Third candidate (2026-11-02)

Tim on the second candidate: "The current build does nothing when I look at it: nothing moves or lights up, so I cannot evaluate it. Visible lines must not appear on the map; it should look as it does now. Make the navigation demonstrable and preserve the current appearance, then show me something I can assess."

What changed:
- Nothing of the pipeline is drawn at rest (`e0fdb95`, recorded in `execution.md`, "No map at rest"). A cue draws its own route alone and fades to nothing when it ends. Against main, the only differences are the approved spacing changes.
- The candidate is now demonstrable. A demo stand-in takes the brain's place: `tools/demo_brain.py` in the run folder, started by `start.sh`, not in the repository. You type a phrase into the Conversation box and it reaches the stand-in through the page's own socket and the server's ordinary turn. The stand-in calls the brain's real tool path (`POST /internal/tool`, `ui_journey`, with the candidate's own tool token), then answers with one short line in the conversation. That line is not spoken: there is no Fish key here. The phrases are in `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/DEMO.md`.

| | |
|---|---|
| URL | http://localhost:5183/ (open in Chrome) |
| Code | `2677e3f` on `feature/ui-navigation-pipeline` (local only) |
| Backend | https://localhost:8350, isolated data in `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/candidate/data` |

Phrases (English / Spanish):

| Phrase | What it shows |
|---|---|
| show me the CPU / muéstrame la CPU | Network tab, the Mac mini's CPU on the right, then the CPU line in the node's view |
| show me the database / muéstrame la base de datos | the Team Server tile's Database value, left |
| show me the microphone, mute button / muéstrame el micrófono, botón de silencio | the mic's mute button, left |
| show me the phase steps / muéstrame los pasos de fase | the workflow's design phase step |
| show me the decisions / muéstrame las decisiones | the workflow's Decisions tab |
| show me the folders / muéstrame las carpetas | the Folders view's Registered tab |
| show me the task / muéstrame la tarea | Workflows tab, the workflow's row, then the Import entries task |
| tour / recorrido | all seven in turn, 2.5 s apart (about 30 s) |

Anything else gets the stand-in's list of what it can show.

Checked in headless Chromium at 1440x900 (`tools/demo_check.py`):
- At rest: 0 pipeline elements drawn, 0 cues.
- A typed "tour": 7 journeys, all completed, 9 arrivals (the CPU and the task have two each). Answer: "That was the tour, sir."
- "switch to Spanish", then a typed "recorrido": 7 completed, 9 arrivals. Answer: "Ese fue el recorrido, señor."
- "cambia a inglés" puts it back in English.
- At the end: 0 pipeline elements drawn, no console errors.

Evidence in `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/evidence/demo/`:
- `tour-en-es.mp4` (also `.webm`, 1 min 40 s): the tour in English, then the recorrido in Spanish.
- `at-rest.png`: the page at rest, with no pipeline drawn.
- `run1/demo-check.json`: the counts above.
- `frames/`: stills from the video.

What Tim does:
1. Open http://localhost:5183/ in Chrome. Close the first-time setup if it shows.
2. Look at the page at rest. Nothing of the pipeline is drawn, and it looks as it does on main.
3. Click the Conversation box ("Speak, or click to type"), type `tour` and press Enter. You should see:
   - A white line runs from the orb along the page's edges to each target in turn: the CPU, the database, the mute button, the phase step, the Decisions tab, the Folders tab, then the task.
   - Two rings at each arrival, an outline on the target, and the orb turning white while the signal runs.
   - Each signal fades to nothing.
   - Views open by themselves where a target needs them.
4. Click the Conversation tab to read "That was the tour, sir." and to type again. Try any single phrase from the table.
5. Type `switch to Spanish`, then `recorrido`. Type `cambia a inglés` to go back.

The first and second candidates' notes below describe a faint white map that stayed drawn at rest. There is no map at rest now; those lines are marked where they occur.

The candidate is left running. Stop it with `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run/stop.sh`.

## Second candidate (2026-11-02)

The candidate now runs the final integration. Everything below this section is the first candidate's record (2026-11-01, code `af9b499`); where the two differ, this section is current.

| | |
|---|---|
| URL | http://localhost:5183/ (open in Chrome) |
| Branch | `feature/ui-navigation-pipeline` (local only) |
| Code | `230d3f5`; the commit adding this section changes only this file |
| Run folder | `/Users/timothystockstill/code/macos/4thWAIV/jarvis/.claude/worktrees/ui-nav-run` (`R` below) |
| Restarted | `R/stop.sh` then `R/start.sh`; backend 8350, Vite 5183, its own data, the same fixture seams as the first candidate. 8340 and 5173 untouched |

Readiness, checked through 5183 (`R/evidence/local-validation-2/readiness.txt`, from `ready_check.py` beside it): the page 200; `/api/settings/language` `en`, English and Spanish models installed; `/api/workflows` lists only `wf-2548254143a3` "Ledger migration", which is in the candidate's database and not in the live one; `/api/team/network` the fixture, `connected`; `/ws/sessions` open with its `snapshot` frame, `/ws/voice` open with its `config` frame, `/ws/transcribe` open (it waits for audio).

### What changed since the first candidate

- Neighbouring controls (blocker `leaf-sibling-routing`, approved: over or under the row): a part whose straight leaf would cross a neighbour is reached by a lane over or under its row that drops into the part's top or bottom edge with 45-degree corners, never across another control or a line of text (`dfe52df`). Reached now: the workflow tabs left of the last (Decisions among them), the Folders tabs, the Team Server tile's last write, the reminder's text and Sign in, a finished workflow's ×.
- Microphone row (`0ab205f`): 8 px under the row (`--ui-leaf-lane`, the `#mic-control` padding), so mute, off and the health dot are reached under it.
- Phase steps (`0ab205f`): the connector line between steps ignores the pointer, and a step left of the last is reached by a lane over or under the stepper (`overTheRow()` in `uiPipeline.ts`).
- Every registered target (`230d3f5`): the element holding a part no longer counts as covering it, a box laid out but not drawn counts as hidden, visibility is judged at the middle of the part inside the window, and `--ui-leaf-lane` room was added over a folder row's name, a record's head, a subagent row and the node and subsystem detail titles. Reached: 211 of the contract's 259 targets and parts, the same in English and Spanish at 1440×900 and 1280×800.
- Usted throughout (blocker `spanish-register`, approved): the centre's Spanish (46 strings) and every string added since address the person as usted (`36c7cac`).
- Remaining navigation controls (`e3bfc11`): 21 of the 22 controls that navigate run registered commands a person's click and the brain's request share; the transcript's scroll is classified `scroll` (`0ab205f`).
- Coded words (`02d9a38`): the team server's errors (30 codes) and voice input's causes (8 codes) are worded in the page's language; the team server's own words are shown as original detail.
- The brain's map labels every target in the language in force (`4b4d432`).

### Evidence

Folder `R/evidence/local-validation-2/` (the drivers `validation_evidence.py`, `candidate.py`, `start.sh`, `stop.sh` are copied beside it). The brain path is the same as the first candidate's: the fixture stand-in brain calling the real `POST /internal/tool` with the candidate's own tool token.

- Video: `video/local-validation-2.webm` (the whole run, 3.5 min, 1440×900) and `video/arrivals-en-es.mp4` (70 s: the five new arrivals in English, then in Spanish).
- `evidence.json`: every step's tool answer, timings and screenshots. No missing-translation warning; the only console errors are the two refused team addresses below (400).
- Every region, each opened by its registered command, every command `completed`, no page scrolling sideways, in English and in Spanish: `{en,es}-right-{folders,agents,queue,workflows,network,diagnostics}.png`, `{en,es}-main-{conversation,folders,voice-diagnostics,subsystem,node,team-server}.png`, the nine workflow tabs `{en,es}-main-workflow-*.png`, `{en,es}-settings-{brains,voice,you,team,updates,system}.png`, `{en,es}-menu-open.png`.
- The three journeys in both languages, each `completed` and animated: `{en,es}-left-database-*`, `{en,es}-right-node-cpu-*`, `{en,es}-main-workflow-task-*` (frames `-emit`, `-travel`, `-arrival`, `-hold`, `-reset`, as before).
- New arrivals, both languages, each `completed` and animated: microphone mute `{en,es}-arrive-mic-mute-*`; the phase step "design" (left of the last) `{en,es}-arrive-phase-step-*`; the workflow's Decisions tab `{en,es}-arrive-decisions-tab-*`; the Folders "Registered" tab `{en,es}-arrive-folders-tab-*`; the reminder's Sign in `{en,es}-arrive-reminder-sign-in-*`. The reminder shows only while signed out, so for that arrival the fixture network answered the page tests' `SIGNED_OUT` snapshot, then went back.
- Hidden target and a real transmission at the same time (English, as before): `en-hidden-*`, `en-transmission-*`, `en-after-transmission-*`.
- Error wording: `{en,es}-error-team-address.png` (below, step 3).
- Language switch: `switch-1-menu.png`, `switch-2-settings-voice-en.png`, `switch-3-settings-voice-es.png` (to Español in Settings), `switch-4-settings-voice-es.png`, `switch-5-back-to-en-by-settings.png` (back to English in Settings; server `en`).

### What Tim checks (about fifteen minutes)

Open http://localhost:5183/ in Chrome and close the first-time setup if it shows. JARVIS stays silent and does not answer: no Fish key and no brain here. The Diagnostics tab is red because voice output is down for the same reason.

1. Routes around neighbouring controls. Watch each arrival in `video/arrivals-en-es.mp4`, then ask for them yourself. In a terminal, `R/point.sh` asks the candidate's pipeline to point at one target on the open page, through the brain's real tool path. Paste each line, watching the page:
   - Microphone mute, with Conversation open: `R/point.sh '{"region":"left","view":"left.fixed","kind":"mic","part":"mute"}'`
   - Click the Workflows tab, then "Ledger migration", then its Phases tab. The step "design": `R/point.sh '{"region":"main","view":"main.workflow","kind":"workflowPhase","key":"wf-2548254143a3/design"}'`
   - With the workflow still open, its Decisions tab: `R/point.sh '{"region":"main","view":"main.workflow","kind":"workflow","key":"wf-2548254143a3","part":"tab.decisions"}'`
   - Click Folders in the centre's tabs. Its Registered tab: `R/point.sh '{"region":"main","view":"main.folders","kind":"foldersList","part":"registered"}'`

   Each answer should start `status: completed / arrival: animated`. On the page, the white signal leaves the orb, runs along the pipeline (in this second candidate a faint map drawn at rest; since the third, nothing is drawn at rest), goes over or under the row and drops into the part's edge with 45-degree corners. It never crosses the neighbouring button, step or line of text, then rings and outlines the part. The reminder's Sign in does not show in this candidate, because its network fixture says signed in. Its arrival is in the clip and in `{en,es}-arrive-reminder-sign-in-*.png`.
2. Usted wording. Switch to Español (step 4) and look for these, word for word:
   - the typing box: "Hable, o haga clic para escribir"
   - the workflow's badge, in the Workflows tab and its header: "Requiere su atención"
   - Configuración, Usted: "Su nombre"
   - Configuración, Equipo: "Iniciar sesión en el servidor de su equipo activa los mensajes entre personas."
   - Carpetas in the centre: "Seleccione una carpeta para ver su ubicación, su contenido y sus sesiones."
   - the reminder in the clip: "Inicie sesión en el servidor de su equipo: los mensajes entre personas y la vista de Red lo necesitan"

   Nothing on the page should say tú, tu, te or ti.
3. Spanish error wording. In Spanish, go to ⋮, Configuración, Equipo. Type `http://team.example.com` in the address box and press "Iniciar sesión con GitHub". It should say "La dirección del servidor del equipo debe empezar con https://." Then type `https://team.example.com/x`; it should say "Escriba solo la dirección del servidor, sin una ruta." Neither address is contacted: JARVIS refuses both before connecting. Some words stay English on purpose, marked as original detail:
   - Cerebros' "Run `claude` and log in." (the provider check's own remedy)
   - lines already in the conversation, which are stored as said
4. Language switch through Settings, and back. Go to ⋮, Settings, Voice, Language and choose Español. Allow a few seconds while the speech model loads. The whole page changes in place, with nothing reloaded. Back: ⋮, Configuración, Voz, Idioma, English.

Notes:
- The conversation holds 36 "Communications with the agents are down/back" lines from earlier candidate starts. They are not from this run. They were left alone, because clearing the conversation needs your yes.
- Stop the candidate when done: `R/stop.sh`.

### Open

- Blocker `reach-remainder` (waiting for Tim) covers the 8 targets still refused:
  - Settings' six targets, because Settings covers the orb every route starts from
  - the right panel's agent name, because the agent's orb fills 58 of the row's 60 px
  - a network tile's presence dot, which sits in the tile's title on the top border

  The recommendation is to leave them refused and point at the whole row, the orb or the tile name instead. Separately, register the tab kinds as a follow-up, or drop them from the contract.
- Not reached in the sweep for want of data: 26 conditional parts that the fixtures do not show. Never registered: 14, which are the contract's `rightTab`, `mainTab` and `region` kinds, and the `?view=main` page's capture.
- Left for CI: the full `pytest`, and the page tests that timed out on the loaded machine (the two animated pipeline tests, `voice_health`, `speech_input`). They timed out the same way with the final changes stashed.

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
| Idle map (first candidate only; since the third, nothing is drawn at rest) | | backbone, rails and sockets drawn with nothing running | `00-idle-map.png` |

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
2. (Superseded: there is no map at rest now. Nothing of the pipeline is drawn while nothing is happening; a route is drawn only while a cue runs. See "Fourth candidate: tour routing" above.)
3. The three region journeys are in the recording (`video/local-validation.webm`): the left Team Server tile's Database figure, the Network tab's Mac mini CPU, and the workflow's Import entries task. Each one is a white signal leaving the orb, running along the map, rings at the target, an outline, then fading.
4. Nested navigation by hand: Workflows tab, click "Ledger migration", go through its nine tabs. Network tab, click the Mac mini tile. Diagnostics tab, click a row. The menu (⋮), Settings and its sections.
5. Hidden target: in the recording, the Blockers tab opens by itself from a closed workflow before the signal arrives.
6. Ordinary transmissions: in the recording, the amber ray to the Agents tab keeps its colour, and the white signal waits, then reports busy.
7. Spanish: ⋮, Settings, Voice, Language, Español. Everything changes in place with nothing reloaded; change it back the same way.

Stop the candidate when done: `R/stop.sh`.
