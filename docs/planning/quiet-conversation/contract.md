# Voice and audio core: control contract

Phase 1 (`baseline-and-contract`) of workflow `wf-fc70d73b6799`.
Branch `feature/voice-audio-core`, base `b16584c` (PR 62 head, open, CI
pending when this was written; `origin/main` at `fd20c54`). Plan commit
`79a7680`. The plan is [delivery-plan.html](delivery-plan.html); this
document is the shared contract every later phase builds against. It
changes no application code.

Line references are to `b16584c`.

## 1. Settled requirements, quoted

From the workflow brief:

> Both microphone-stop outcomes are per-user persistent settings (keep captured words / discard unfinished input).

> Both tab-switch outcomes are per-user persistent settings (continue an explicitly enabled session / stop listening).

> Implement and test all four combinations. Defaults are assessed separately during Tim's validation. Keeping words never adds an automatic-send action.

> Quiet reload first: Mute/Mic Off survive reload, and the preference is read before any getUserMedia.

From [delivery-plan.html](delivery-plan.html), "Two user settings":

> When I stop the microphone — Keep captured words or Discard unfinished input. — Both choices immediately stop new capture. Keep processes only audio captured before the stop and preserves the resulting words under the normal turn/submission rules. Discard invalidates unfinished input, pending commits and late recognition results. Keeping words is distinct from introducing a new automatic-send action.

> When I switch away from JARVIS — Continue listening or Stop listening. — Continue preserves an already explicitly enabled listening session. Stop releases capture and applies the user's keep/discard preference to unfinished input. Switching back does not silently restart listening. This setting cannot enable a microphone that is muted or off.

> Preference changes apply to subsequent stop/visibility transitions; changing a setting must not itself submit, discard or start capture.

> For missing, unreadable or invalid settings, remain disarmed until the effective preferences are established; never infer permission to listen from storage failure.

From "Do next":

> A fresh or previously listening page starts disarmed and requires a deliberate enable.

> Keep the saved preference separate from actual device state. A reload may restore Muted while the newly loaded page has no open microphone. Show that honestly as "Muted · microphone inactive"; do not reacquire a device merely to reproduce an old muted state. Mic Off remains Off. Storage failure defaults to disarmed.

From "Prepare the shared control contract":

> A model request may make controls available, but must not manufacture the explicit human listening grant.

> Live microphone grants and playback permission are session-only; never restore them from disk. Do not persist raw room audio or credentials for this feature.

## 2. The Mute proposal: decided

Tim approved the plan's Mute proposal as proposed (4 November 2026). Quoted:

> Mute proposal: suspend PCM delivery to every consumer, stop automatic turn commitment and retain recognized text as an unsent draft. A device already open may remain open, visibly labelled. Unmute is explicit and begins a new capture epoch; it does not silently submit the old draft. This separate Mute behavior remains a proposal for review.

So Mute suspends PCM delivery to every consumer (the recogniser and
package listeners alike), stops automatic turn commitment for this page
(the turn being gathered is dropped on the server and its words, with what
the stopped capture still finishes, become an unsent draft in the
composer), and keeps the device open, labelled Muted. Unmute is explicit,
begins a new capture epoch, and never submits the old draft.

- Mute surviving reload, Mute never acquiring a device on reload, and
  Muted being shown as "Muted · microphone inactive" when no device is open
  are settled (section 1).
- The keep/discard and continue/stop settings do not depend on Mute. They
  govern Mic Off and leaving the tab (section 5).

## 3. State owners

One owner per fact. No parallel timers, no second transcript.

| Fact | Owner | Where |
|---|---|---|
| Quiet preference (none / muted / off) | Input layer, browser | `mic.ts` store, persisted by the page before startup (section 6) |
| Listening grant (armed or not) | Input layer, this tab, this page load only | `voiceInput.ts`; never persisted |
| Device and recogniser readiness | Input layer | `voiceInput.ts` facts, shown through `inputStatus` |
| Capture ownership across tabs | Server, one owner per node | `transcriber.TranscriptionService` (today `admit()` gives `busy`), extended with the owning client ID and epoch |
| Stop and tab-switch settings | Server, per user, SQLite | new settings service (section 6), page and brain call the same function |
| Gathered turn, its revision, its commit | Server turn controller | replaces `server._Utterance` (section 7) |
| Who may speak now (the floor) | Same turn controller | consulted by `SpeechScheduler` before any audio starts |
| Playback execution and outcomes | `SpeechScheduler` + `voice.ts` | section 8 |
| Background events and their presentation | Attention store, SQLite | section 9 |
| Unsent typed and voice drafts | Matt's composer draft (`conversation/Composer.tsx`, per-tab) | reused, not duplicated |

## 4. Identities

Every delayed action is checked against the current identities just before
it acts. Cancelling a timer handle alone is never enough (`_Utterance._wait`
spawns `now()` from `call_later`; the spawned task cannot be revoked).

| Identity | Made by | Scope | Carried on |
|---|---|---|---|
| `clientId` | page, once per load (`uiClient.ts` `CLIENT_ID`, already sent in `ui_hello`) | one tab, one load | `ui_hello`, transcribe `start`, every voice frame that changes input or turn state |
| `nodeId` | `data_paths.node_id()` | this JARVIS | attention records, arrival leases |
| `captureEpoch` | page, integer, +1 on every human enable, unmute, mic on, stop, discard, tab-switch stop and device/session loss | one `clientId` | transcribe `start`; every `transcript`/`interim` on `/ws/voice` |
| `turnId` | server turn controller | one gathered utterance | `turn` frames to the page; the committed request; the reply's `utt` link |
| `activityRevision` | server turn controller, monotonic | one `turnId` | every scheduled commit check and every playback permission |
| `frameSeq` | page, count of frames sent in this `captureEpoch` | one capture | stop snapshot ("last allowed frame") |
| `decodedSeq` | server recogniser, frames decoded for this capture | one capture | `interim`/`final` on `/ws/transcribe`; the turn controller's coverage check |
| `utt` (+ `idx`) | `SpeechScheduler` | one reading (+ chunk) | `audio`, `played`, `skip_to_end`, `reading_skipped`, playback outcome frames |
| attention `itemId` + source revision | attention store | one source object (message, run, task) | digest snapshot and presentation outcomes |

Rules:

1. A `final` or `interim` whose `captureEpoch` is not the current one for
   its `clientId` is dropped and logged, never gathered.
2. A capture `finishing` under Keep has its own epoch; Discard, a new
   enable or loss revokes it too.
3. A commit runs only if `turnId`, `activityRevision` and the expected
   deadline all still match, and `decodedSeq` covers the capture's
   `frameSeq` at the moment the hold began (section 7).
4. Audio for a reading starts only if the floor permission it was granted
   under is still current (same `activityRevision`, floor not taken back).
5. A scheduler `utt` belongs to one voice-socket connection epoch, as today
   (`jarvis.ts` clears skip state on open).

## 5. Input state and transitions

### States

Shown state comes from facts, never from a request having been made.

| State | Meaning | Shown |
|---|---|---|
| `off` | Quiet preference is Off; no device | Mic off |
| `muted-inactive` | Quiet preference is Muted; no device open in this load | Muted · microphone inactive |
| `muted` | Muted, device open | Muted |
| `disarmed` | No quiet preference; no listening grant in this tab | Not listening, with an explicit Start listening control |
| `starting` | Grant given; asking for device or connecting | Starting |
| `listening` | Device open, recogniser `ready`, this tab owns capture | Listening |
| `finishing` | Stopped under Keep: draining frames up to the stop snapshot | Finishing |
| `busy` | Another tab or client owns capture; this tab has not taken it | Busy (in another tab) |
| `unavailable` | No device, no worklet, permission refused, recogniser unavailable | Unavailable, with the reason |
| `paused` | Existing `muteMicDuringSpeech` option while JARVIS speaks | Paused |

`listening` requires all of: grant, device, recogniser ready, ownership.
The control exposes its state to assistive technology (`aria-pressed` on
Mute and Off, the state as accessible text).

### Transitions

`K/D` = the "When I stop the microphone" setting (Keep / Discard).
`C/S` = the "When I switch away from JARVIS" setting (Continue / Stop).

| # | From | Event | To | Effects and guarantees |
|---|---|---|---|---|
| 1 | any | page load or reload, preference reads Off | `off` | No `getUserMedia`, no transcribe socket, no retry timer. |
| 2 | any | load, preference reads Muted | `muted-inactive` | Same: no device acquired to reproduce Muted. |
| 3 | any | load, preference none | `disarmed` | Same. A previously listening tab is not resumed. |
| 4 | any | load, preference storage missing, unreadable or invalid | `disarmed` | Never treated as permission. Settings (section 6) not yet loaded also keeps the Start control disabled. |
| 5 | `disarmed` | human Start listening | `starting` → `listening` | New `captureEpoch`; grant set; claims ownership (row 18). AudioContext unlock, unrelated clicks and existing browser permission are not a Start. |
| 6 | `off` | human Mic on | `starting` | Clears Off preference; counts as the deliberate enable. |
| 7 | `muted-inactive` | human Unmute | `starting` | Clears Muted preference; counts as the deliberate enable. New `captureEpoch`; no old draft is submitted. |
| 8 | `muted` | human Unmute | `listening` | New `captureEpoch` and a fresh session once the muted one has finished; the draft made at Mute stays an unsent draft. |
| 9 | `listening`/`starting` | human Mute | `muted` | Preference saved before anything else. PCM to every consumer suspended; the device stays open. New `captureEpoch`; `turn_discard` drops the server's gathered turn; its words and what the stopped capture finishes (only audio from before the Mute) become an unsent draft. Nothing is committed automatically. |
| 10 | `listening` | human Mic off, **K** | `finishing` → `off` | Preference saved first. Snapshot `frameSeq`; no frame after it is sent; tracks released at once; drain only up to the snapshot under a finishing epoch; resulting words go to the normal turn rules (section 7). Timeout leaves the words as an unsent draft with an error, never a partial automatic submission. |
| 11 | `listening` | human Mic off, **D** | `off` | Preference saved first. Abort live and closing sessions; revoke both epochs; server drops the gathered-but-uncommitted turn for this client (`turn_discard`); late finals ignored (rule 1). |
| 12 | `listening` | tab hidden, **C** | `listening` | Session continues only because it was explicitly enabled. |
| 13 | `listening` | tab hidden, **S** + **K** | `finishing` → `disarmed` | As row 10, but preference unchanged and the result is `disarmed`, not `off`. |
| 14 | `listening` | tab hidden, **S** + **D** | `disarmed` | As row 11, preference unchanged. |
| 15 | `disarmed`/`off`/`muted*` | tab hidden or visible, either setting | unchanged | Visibility never arms. Continue cannot enable a muted or off microphone. |
| 16 | `disarmed` after row 13/14 | tab visible again | `disarmed` | Switching back does not silently restart listening. |
| 17 | any | stop or tab-switch setting changed | unchanged | Takes effect at the next stop or visibility change. Changing it submits, discards and starts nothing. |
| 18 | `starting` | server says another client owns capture | `busy` | No silent takeover. The tab does not acquire the device while waiting. Only a human Start in this tab takes ownership, and the server tells the previous owner, which goes `disarmed` under its own stop rules. |
| 19 | `listening` | sibling tab Mute or Mic off (same browser) | sibling's quiet state | Quiet propagates: the preference is shared by the browser (section 6), so every tab applies it; the owner releases. |
| 20 | `listening` | voice socket (`/ws/voice`) lost | `disarmed`-with-draft until reconnected | Stop accepting input under the old epoch; the device is released. Server no longer submits buffered words on teardown. Words gathered but not committed become an unsent draft in Matt's composer. Decided: a tab that was listening resumes on reconnect, in the same open page, under a new epoch without a second click (it does not claim the recogniser from another page). A reload or a new tab starts not listening, with no microphone. |
| 21 | `listening` | transcribe socket lost or recogniser error | `starting` (retry) or `unavailable` | Retries recheck the same grant and epoch; a retry can never arm a tab that is not armed. Words in flight shown as lost, as today. |
| 22 | `listening` | device lost (track ended) | `unavailable` → retry | Same retry rule. |
| 23 | `finishing` | late `final` after its epoch was revoked | unchanged | Dropped (rule 1). |
| 24 | any input state | human typed Send | unchanged | Typing works whether voice is quiet or not. Typed Send is intentional completion; a pending voice draft for the same turn is kept, not silently merged (section 7). |
| 25 | `listening` | JARVIS starts speaking, `muteMicDuringSpeech` on | `paused` | Existing option, unchanged. |

### The four combinations

| Stop setting | Tab-switch setting | Mic off while listening | Tab hidden while listening |
|---|---|---|---|
| Keep | Continue | row 10 | row 12 |
| Keep | Stop | row 10 | row 13 |
| Discard | Continue | row 11 | row 12 |
| Discard | Stop | row 11 | row 14 |

Each cell is tested with synthetic device and recogniser spies, plus reload
persistence and late recognition for each.

## 6. Persistence

| Datum | Store | Scope | Read when | Failure |
|---|---|---|---|---|
| Quiet preference (none/muted/off) | `localStorage`, read synchronously in `mic.ts` initialisation, before `startJarvis` and before any `getUserMedia` | this browser on this node | first thing at load | unreadable → `disarmed`, never listening |
| Sibling-tab propagation | `storage` event on the same key | this browser | on change | — |
| Stop setting, tab-switch setting | node SQLite, new table, additive migration through `run_store.migrate` | per user (see open question 1) | page reads `GET` before enabling Start; brain reads through the same service | missing/invalid → Start stays disabled until established |
| Patience setting (hold seconds) | same table | per user | turn controller start | invalid → current constant |
| Attention items, presentation outcomes | node SQLite, new tables | per node | server start | — |
| Listening grant, device, playback permission | memory only | this tab, this load | — | never restored |
| Raw audio | not stored | — | — | — |

The quiet preference is browser storage because it must be read before the
server is reachable; the brief's quote says it is read "before any
getUserMedia". The two settings are server-side per user because they must
be brain-callable and shared by the user's tabs.

## 7. Turn controller (server)

Replaces `_Utterance` (`server.py` 4944). One per voice client.

| State | Meaning | Allowed output |
|---|---|---|
| `idle` | nothing gathered | reply to the last turn; digest only if section 9 allows |
| `gathering` | finals and interims of one `turnId` | none unsolicited |
| `commit-pending` | hold expired; checking identities and coverage | none |
| `stalled` | coverage not reached within its bound | none; page shows Retry / Send now / keep as draft |
| `awaiting-reply` | turn committed | that turn's reply only |
| `reply-playing` | its reading is playing | that reading only; queued events stay visual |

| Event | Effect |
|---|---|
| accepted `final` (epoch current) | join `gathering`; `activityRevision`+1; new deadline |
| accepted `interim` (speech, not echo) | `activityRevision`+1; extends the hold whether or not a final is buffered; an unfinished interim blocks commit of older finals |
| hold deadline reached | `commit-pending`: recheck `turnId`, `activityRevision`, deadline, and capture coverage (`decodedSeq` ≥ the capture's `frameSeq` at the deadline, bounded lag). Pass → commit. Not yet → wait to a bound, then `stalled` |
| speech during `commit-pending` | back to `gathering`, same `turnId` |
| speech after commit, before audio starts | playback permission revoked; continuation kept as the next input; the executed action is not undone or replayed |
| typed Send | intentional completion of the typed text; pending voice words for this client stay a separate draft and are not merged silently |
| Mic off **D** / tab-switch **S**+**D** | `turn_discard` for this client: gathered text dropped, timers invalid |
| Mic off **K** / tab-switch **S**+**K** | drained words join the turn and commit under the ordinary hold |
| voice socket closed | no commit; gathered text returned to the page as a draft if the page is still there, otherwise logged and kept as a recoverable draft record |
| explicit Send now (page control and brain tool) | commit now, coverage still checked |

`SpeechScheduler`'s clamp `end_of_turn = min(END_OF_TURN_SEC, pause_after)`
(`speech.py` 421) is removed: one patience value governs both input and
output. A four-second recognised-activity hold is a local trial after the
race and coverage work, not a default.

Coverage needs the recogniser's progress per capture. The transcribe
`start` carries `clientId` and `captureEpoch`; the server's capture records
frames received and decoded; the turn controller reads them by `clientId`.
This is a server-side link, so the page's word is not trusted for coverage.
`transcribeProtocol.json` version goes to 2 (fields added; a v1 page is
refused with the existing `invalid-format`).

## 8. Output

| Action | Target | Effect | Never |
|---|---|---|---|
| Skip the rest | one reading (`utt`) | stops its audio, drops its queued and late chunks, keeps its text; reuses PR 62 `skip_to_end`/`reading_skipped` | touches another `utt`; satisfies an approval readback gate |
| Stop speaking | all audio now | existing `hush` → `barge_in(keep_unread=False)` | cancels the request or undoes a tool |
| Cancel request | one generation/job | that job's own cancellation | claims an executed action was reversed |

Playback outcomes per chunk: `queued`, `started`, `played`, `failed`,
`skipped`, `interrupted`. A decode failure is `failed`, not `played`
(today `voice.ts` `playNext` acks failed chunks as played). Transport
advancement is separate from outcome. Reply text outcome: `succeeded`,
`failed`, `cancelled`, so unfinished text is never shown as complete.

Echo: browser AEC plus existing transcript matching stay. A reproduced echo
failure in the supported path blocks readiness. Muting the microphone for
all readback is not the fix.

## 9. Attention

Routine events (arrivals, needs board, session messages, delivery receipts,
run completions/stalls/failures, package endings, greeting, sign-in
reminder) become attention items in SQLite linked to their source ID and
revision, then follow one admission policy. Eligible speech is one bounded,
read-only brain summary; external text is wrapped as untrusted; no action
tools; a failed brain or TTS leaves the items visible with no canned speech.
Trial gates: oldest item 60 s, digest cooldown 120 s, 10 s after a settled
exchange, floor `idle`, availability engaged. Away and unknown are silent;
return shows a grouped count and Review updates, never a played backlog.
Requested readback and approval readback stay exact. A static call-site
check fails any direct `speech.say` outside the approved entry points.

## 10. Commands shared by page and brain

Each is one function both the route and the brain tool call, with the same
validation and the same result (`tests/test_brain_page_parity.py`).

| Operation | Page | Brain | Notes |
|---|---|---|---|
| Read voice input state and settings | Voice settings, mic control | read tool | |
| Set stop setting / tab-switch setting | Settings, Voice and language | acting tool | returns the saved values |
| Mute, Mic off | buttons | acting tool | brain may only make input quieter |
| Start listening, Unmute, Mic on | buttons | **none** | the human grant cannot be manufactured; the brain may point at the control (`ui_indicate`) |
| Send now | control | acting tool | coverage still checked |
| Skip the rest, Stop speaking | existing | acting tool | |
| Review updates / mark seen | attention panel | read + acting tool | |

All new words in English and Latin American Spanish catalogs with the same
keys; elements found by registered IDs.

## 11. Lanes and file ownership

Three lanes after this contract; one integrator. A lane edits only its own
files. Anything listed under the integrator goes through the integrator.

### Integrator only (this worker)

- `jarvis/server.py`: the `/ws/voice` handler and its protocol frames, the
  turn controller that replaces `_Utterance`, the `/ws/transcribe` link to
  the turn controller, route registration, `ALLOWED_TOOLS`/tool dispatch
  entries
- `jarvis/transcribeProtocol.json`
- `jarvis/transcription_api.py` protocol handling (start fields, version)
- the shared control and identity types: a new `jarvis/voice_control.py`
  (identities, settings service and validation) and `frontend/src/voiceControl.ts`
  (the page's copy of the identities and settings types)
- catalog keys: `frontend/src/messages/` English and Spanish, added through
  the integrator
- `jarvis/ui_contract/` families and the generated `frontend/src/uiContract.ts`
- `tests/test_brain_page_parity.py`, `CLAUDE.md`, this contract

### Input lane: `quiet-input-settings`, `patient-turns`

- `frontend/src/mic.ts`, `voiceInput.ts`, `MicControl.tsx`, `MuteButton.tsx`,
  `MicOffButton.tsx`, `MicMeter.tsx`, `pcm-worklet.ts`, `pcm.ts`,
  `voiceHealth.ts` (input parts), `packageBridge.ts` (listener gating only)
- `frontend/src/jarvis.ts`: the startup and mic subscription parts only
- the Voice settings rows for the two settings (`Settings.tsx` section
  body, new component for the two settings)
- `jarvis/transcriber.py` (capture ownership, `decodedSeq`), the turn
  controller's logic module if split out of `server.py`
- `jarvis/speech.py`: `END_OF_TURN_SEC` clamp and `user_interim`/`user_final`
  floor hooks only
- tests: `test_voice_page_speech_input.py`, `test_voice_page_mute.py`,
  `test_turn_taking.py`, transcriber/backpressure tests, new input and
  turn tests

### Playback lane: `playback-and-echo`

- `frontend/src/voice.ts`, `captions.ts`, `ConversationView.tsx`,
  `frontend/src/conversation/*` (extending Matt's components, never copying
  them), `StatusControl.tsx`, `capture.ts`, `CaptureBox.tsx`
- `frontend/src/jarvis.ts`: the playback, Skip, hush and `audio`/`reply`
  frame handling parts only
- `jarvis/speech.py`: scheduler playback, skip, outcomes, replay
- echo qualification tests
- tests: `test_speech.py`, `test_voice_page_conversation.py`,
  `test_voice_page_captions.py`, playback outcome tests

### Attention lane: `quiet-announcements`

- new `jarvis/attention_store.py` and its admission/digest module
- `jarvis/arrival_announcer.py`, `jarvis/team_reminder.py`,
  `jarvis/transmission.py` (cancellation acknowledgement only),
  `jarvis/message_approvals.py` (spoken outcomes only)
- the announcement producers in `server.py` (`_say_notice` and its callers,
  needs board, session messages, needs-you, run completions, greeting,
  preflight summary): written by the lane as patches, applied by the
  integrator
- `jarvis/speech.py`: `_release_gated`, the unread prefix and bridges
- the attention panel and Review updates on the page
- tests: `test_announcements.py`, `test_run_announcements.py`,
  `test_message_tracking.py`, `test_session_messages.py`,
  `test_team_reminder.py`, `test_needs_you_notification.py`, the static
  speech call-site check

`jarvis.ts` and `speech.py` are split by region between lanes; any change
outside a lane's region goes through the integrator.

## 12. Questions for Tim (decided)

All three are decided.

1. **Mute proposal** (section 2): approved as proposed.
2. **"Per user" for the two settings**: keyed by the signed-in team
   member's GitHub ID when signed in, otherwise a local owner key, in this
   node's SQLite. Never a team-wide setting (accepted as proposed).
3. **Reconnect after a voice-socket drop** (row 20): approved as
   proposed; a tab that was listening in the same open page resumes under
   a new epoch without a second click.
