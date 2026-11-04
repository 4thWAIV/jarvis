# Spoken-event audit

Source baseline: `375290c`, inspected 2026-11-03. This is a source audit and implementation recommendation, not an implemented change or a live audio test.

The server still authors unsolicited speech independently of the brain. Most notices now tell the brain something was said, but that happens before playback and does not make the brain responsible for deciding when or whether to interrupt. Most notice classes bypass the existing completion batch. A connected voice page is also treated as sufficient reason to suppress desktop notification; it does not establish that the owner is present.

## Status of the earlier report

The earlier report is [server-speech-events.md](../../server-speech-events.md), dated 2026-10-21. A related working copy is `.agents/in-progress/server-spoken-lines.md`. Leave them as historical records.

The report says: “Today only two lines do that.” That statement about informing the brain is stale: the current `_say_notice` path records notes for agent message lifecycle events, human message arrivals, package runs and team sign-in reminders. Its recommendation “Keep spoken, notify the brain” applies to 40 of its 46 listed lines and would retain the interruptions under review. Those counts describe its old inventory, not current coverage.

The older report also mixes visual-only error text with actual speech. Current coverage must distinguish speech generation, queued audio, acknowledged playback, visual messages, and tool results the brain may use. A tool result containing “sir” is not necessarily directly spoken.

The prior agent-send readback entry predates the capture/transmission path: `transmission.perform` now states, “Nothing is sent unheard, and that was decided before this runs”. Current captures retain their heard/accepted gate, and the send runs after that gate; an audit must not reintroduce a second readback or remove approval protection.

## Current speech inventory

Paths and line numbers refer to the baseline above. One row groups related triggers; a row is not one literal sentence. Template substitutions and alternative outcomes make a literal-string count misleading.

| Source | Current trigger and speech route | Recommended treatment |
|---|---|---|
| `jarvis/server.py:832` | Refused language change; direct NORMAL system speech | Persistent setting error; let the brain explain on request. |
| `jarvis/server.py:860–866` | Brain reboot, down/login failure, quota reset; direct system speech, down explicitly immediate/URGENT | Persistent health indicator and actionable card, optionally one nonverbal failure cue. Healthy brain can explain; unavailable brain does not require a spoken fallback. |
| `jarvis/server.py:1256–1318` | Fresh-start confirm/cancel/clear failure/success | Preserve confirmation and cancellation logic; visual action/confirmation and brain-authored requested explanation replace canned wording. |
| `jarvis/server.py:1387–1413` | Context budget question and declined-rotation acknowledgement | Retain explicit rotation decision; quiet pending decision card, offered at a conversation break. |
| `jarvis/server.py:1643–1722`, `5498–5523` | Agent queued-send, delivered, undelivered, answered and empty-answer notices through `_say_notice` | Track message state visually. Coalesce by message; do not speak each lifecycle change or ask to read every answer. |
| `jarvis/server.py:1757–1770` | Changed needs board: name/count announcement | Retain needs list; digest unresolved needs at an eligible break. |
| `jarvis/server.py:1854–1886` | Session message files: single arrival/read question or batch names/count | Persist immediately; one cross-source digest rather than one utterance per five-second poll. |
| `jarvis/server.py:1889–1950` | Permission, dialog, input or generic needs-you; URGENT speech plus desktop fallback | Blocking work is not automatically permission to interrupt a human. Queue an actionable card; explicit urgency policy decides cue/digest. |
| `jarvis/server.py:1983–2006` | Session and voice-run completions assembled into LOW speech | Reuse underlying event collection, replace concatenated templates with semantic deduplication and one brain digest. |
| `jarvis/server.py:2074–2106` | Run stalled, no changes, timeout, failure: URGENT notices | Preserve errors in run record/UI; apply the same user-awareness policy. |
| `jarvis/server.py:2315`, `jarvis/preflight.py` | Startup preflight summary as announcement | Show readiness/diagnostics; avoid narrating startup automatically. |
| `jarvis/server.py:2724–2758` | Not-ready response and brain turn exception/quota/error/death fallbacks | Show recoverable error with retry/action and preserved user input; no canned interruption. |
| `jarvis/server.py:5592–5617` | Agent send waiting, passed, not-live and failed outcomes | Keep transmission animation and exact delivery state; include failure in digest or requested reply. |
| `jarvis/server.py:5689–5720` | Shell-command readback, cancellation, terminal opened/failed | Preserve exact command review and execution gate; move canned outcome chatter to visual status. |
| `jarvis/server.py:5798–5838` | Keypress/window readback, cancellation, success and accessibility/terminal failures | Preserve target/key approval semantics; quiet status and actionable failure card. |
| `jarvis/transmission.py:165` | Shared transmission cancellation acknowledgement | Cancel immediately; visual/cue confirmation rather than a new spoken interruption. |
| `jarvis/message_approvals.py:459–472` | Human message queued/approved/private-copy-missing/changed outcomes | Preserve content-revision and snapshot guards. Show exact state; approval is not delivery. |
| `jarvis/arrival_announcer.py:114–139`, `server.py:6121` | Human/self messages, sender/node, subject and file count; single or plural, via `_say_notice` | Retain claim/lease and multi-node ownership. Queue arrivals for shared digest instead of narrating each arrival. |
| `jarvis/server.py:7277–7308` | Work package success asks to draft reply; failure/timeout announces | Keep package outcome available; offer next action in digest, without a spoken question for each package. |
| `jarvis/team_reminder.py:20–44` | Not signed in after startup settles for 30 seconds | Existing persistent team sign-in reminder is sufficient; remove automatic speech. |
| `jarvis/server.py:11753`, `11826` | Greeting when voice client attaches; nothing-to-replay response | No greeting per connection; requested empty replay can be visual or brain-authored. |
| `jarvis/speech.py:162–163`, `1123`, `1240–1257` | Canned unread prefix and resumed-speech bridges | Remove inserted phrases; no replay of stale notice pile after reconnection. |
| `jarvis/server.py:3010` | Explicit TTS diagnostic clip, “Testing audio, sir.”, direct synthesis outside scheduler | Diagnostic only; replace with test tone if removing all canned voice. Do not confuse with normal unsolicited notices. |

## Speech that should remain exact

`SpeechController.begin_turn/feed/end_turn` (`jarvis/speech.py:511–610`) streams the brain's reply. `read_into` and `read_aloud` deliver requested capture text. `narrate` speaks requested story beats; `jarvis/story_teller.py:164` feeds it sender-authored narration. Replay (`speech.py:877–906`) repeats already-spoken content. These are content transport, not server-authored unsolicited chatter.

Keep requested reading verbatim and preserve capture identifiers, chunk acknowledgements, cancellation, exact reviewed content and approval gates. Removing canned statements must not mean paraphrasing the message that is being approved, executing unheard commands, or marking abandoned readback accepted. Sender-authored story/file content remains untrusted content, not instructions for the brain.

## Why the current batching is insufficient

`SpeechController` defaults to a 20-second batch interval, two-second LOW collection delay and three-second announcement pause (`speech.py:378–383`). `_user_silent` uses elapsed time since speech; `_startable` also checks end-of-turn silence (`1028–1040`). These are scheduling gates, not evidence that a person has finished thinking or is present.

`say` (`617–649`) batches LOW items only. `_say_notice` emits NORMAL announcements. Needs-you, stalled and failed runs emit URGENT announcements. `_release_gated` (`1118–1139`) concatenates text rather than combining lifecycle events. `_step` permits urgent preemption at chunk boundaries and then inserts a bridge on resumption. Merely increasing the LOW interval leaves most unwanted speech unaffected.

`_notify_needs_you` checks `voice_clients` before sending a desktop notification. An open tab with nobody at the machine therefore takes the voice path. Presence and speech-delivery capability must be separate facts.

## Delivery bookkeeping that must change with the policy

`_say_notice` appends a note and conversation notice before checking whether speech exists or completing audio playback. Its note wording says the user was told. `arrival_announcer._announce` calls `_notify` and then marks its announcement completed; `_say_notice` normally returns after queueing. Thus announcement completion is not proof of hearing. This is a source-observed distinction, not a live claim that a specific message was lost.

Maintain separate event facts: received, visible, included in a digest, audio queued, playback completed, user opened, user resolved. Use the existing durable message records and ownership machinery; do not reset arrival leases simply to obtain another announcement. When away, persist the event and keep counters/cards current. On return, summarize still-relevant events once; never announce every historical transition.

## Immediate implementation sequence

1. Route unsolicited event producers above through one structured notification policy before creating any utterance. Include object identity, event type, current status, action needed, urgency, created time and source record link. Do not make every event an LLM request.
2. Make normal arrivals, delivery receipts, success notices, startup reminders and canned post-action acknowledgements silent. Keep UI state, transmissions and event records intact. Deduplicate by object and replace obsolete states before digest generation.
3. Allow one brain-authored digest only when present and conversation policy allows it. Send the brain structured facts with untrusted text wrapped; record what actually played. While the brain is unavailable, show actionable diagnostics with optional nonverbal cue.
4. Remove canned bridge/unread phrasing. Preserve exact requested readbacks, narration and replay. Keep confirmation gates even when their wording changes.
5. Connect tentative/confirmed pause sounds to the input-turn state machine, not the arrival scheduler. A continuation cancels those cues and pending digest permission; notification activity never closes the user's turn.
6. Add source coverage enforcement: direct unsolicited `speech.say` outside the shared policy fails a test. Explicitly classify brain reply, approved readback, requested narration/replay and diagnostic audio as separate entry points so enforcement does not ban legitimate speech.

## Existing test seams and required cases

Use `tests/test_speech.py` for fake-clock scheduling, barge-in, replay, stale queued speech and played/abandoned outcomes; `tests/test_announcements.py`, `test_run_announcements.py`, `test_message_tracking.py`, `test_session_messages.py` and `test_team_reminder.py` for producer behavior; `test_needs_you_notification.py` for absence/notification routing. `test_transmission.py`, `test_message_approvals.py`, `test_message_approvals_server.py`, `test_rotation_server.py`, and `test_package_story.py` protect readback, approval, confirmation and narration.

Add scenarios for rapid mixed-source arrivals; delivered-then-answered before a digest; failure then recovery; a long pause followed by continuation; silence while away with an open tab; disconnect/reconnect during queued audio; a disabled brain; self-message; two nodes claiming one arrival; approved exact-content readback interrupted halfway; and returned user receiving one current summary. Tests should assert event retention and absence of unsolicited speech as well as digest content, not merely update literal expected strings.

No application files were edited and no runtime test suite was run for this report. Browser/voice behavior still requires microphone/speaker validation after implementation.
