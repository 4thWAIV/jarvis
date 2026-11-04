> Superseded in part by [current audio review](current-audio-review.md), 3 November 2026: microphone control is the first project; explicit Mic Off aborts unfinished voice input; Mic Off is distinct from stopping playback or background jobs; transport acknowledgement must distinguish played from failed audio. Earlier estimates do not cover the expanded scope. Retain other sections as supporting proposals.

# Quieter conversation, patient turns and presence-aware delivery

Prepared 3 November 2026 against source baseline `375290c`. This is the immediate implementation proposal and acceptance plan. Application code and live settings have not been changed by preparing it. Three agents audited speech bypasses and collaborated on turn-taking and notification/presence behavior. Their source-level reports accompany this document.

## First delivery

Make the current voice interaction patient and quiet without changing speech recognition models, Fish output or requiring a camera. Fix premature turn submission; remove unsolicited canned speech; collect incoming events into a durable queue; permit one short brain-written digest only when the user is engaged and the conversation has room. When the user is away or their presence is unknown, use persistent visual status and a grouped notification surface instead of speaking into the room.

The first acceptance demonstration must include a long mid-sentence pause, continued speech after each cue, incoming work during the pause, an away interval with several arrivals, and a return that does not trigger an audible backlog. A higher silence constant alone is not the fix.

## What the audit changes

The original report is `docs/server-speech-events.md`, with the earlier working inventory in `.agents/in-progress/server-spoken-lines.md`, dated 21 October. Its default recommendation was “Keep spoken, notify the brain”. That recommendation is superseded here. Use `speech-audit.md` for current call sites, ownership and additional categories.

Current failure mechanisms include:

- `_Utterance` holds finalized segments for 1.5 seconds. An already scheduled callback can still invoke `now()` after continued speech reschedules the timer. A generation/deadline check is needed at dispatch, not only TimerHandle cancellation.
- The speech scheduler clamps its end-of-turn interval to the announcement pause. Raising input patience without removing that hidden clamp leaves inconsistent behavior.
- Recognition interims are text changes, not continuous proof that speech is active. Decoder backlog or an unfinalized continuation can look like a pause.
- LOW-priority announcements concatenate sentences; NORMAL events bypass that batch. Many failures/waiting events are classified URGENT. A reconnect can release an urgent unread backlog.
- Last-human-interaction exists, but a connected browser is treated as an audio destination rather than evidence that a person is ready to hear a notification.
- Some announcement records describe speech as heard before playback finishes. Returning notification summaries must not count queued or interrupted speech as delivered.

## Interaction policy

| User state | Reply behavior | Incoming work behavior |
|---|---|---|
| Speaking or thinking within a turn | Keep the microphone open and retain the floor; optional two-stage cues; immediate continuation cancels the handover | Update queue/badges silently; no summary generation or speech interrupt |
| Turn committed and waiting for answer | Respond to that turn; preserve corrections and interruption | Hold routine arrivals behind the answer |
| Engaged, conversation settled | Answer direct requests immediately through normal turn handling | A bounded digest may become eligible; it is never one announcement per item |
| Quiet/focus mode | Reply when addressed; suppress unsolicited sound | Persistent queue and badge only |
| Away explicitly, page closed, or transport unavailable | No unsolicited local audio | Persist events and show grouped existing OS notification if enabled |
| Presence uncertain | Treat availability conservatively; absence is not proved by stillness | Silent queue; do not speak merely because a page is connected |
| Returning | Resume normal interaction after deliberate engagement | Show one catch-up affordance; do not replay stored announcements or read a digest automatically on reconnect |

User presence, node online status, microphone state and conversation floor are separate. A server may remain online doing work while its owner is away. Reading without moving the mouse is not proof of absence. Activity from programmatic navigation does not make the user present.

## Patient turn-taking and subtle cues

Recommended initial trial settings, not measured optimal values: cue one at 1.5 seconds of eligible quiet, cue two at 3 seconds, commit at 4 seconds. The second cue leaves one full second for continuation. Tune the overall patience and keep cue positions/grace valid from one setting. Manual Send/Done remains available; typed Send follows its explicit submit behavior. Support a manual-finish mode for users whose thinking pauses exceed any automatic deadline.

Cue one is a very soft short rounded tone: possible end of thought. Cue two is a distinguishable soft resolving tone: preparing to take the turn. Neither is a countdown, spoken prompt or claim of semantic certainty. The orb/inline listening indicator mirrors them, and cues can be disabled independently of patience. Play each at most once per silence episode, with an overall cue rate limit so repeated hesitations do not become a new source of interruption. Volume is adjustable with a preview; do not adopt a fixed loudness without testing on Tim's speakers/headphones.

State progression: listening → possible-end → ready-with-grace → committed → answering. Any accepted new speech before commit increments the activity revision, cancels both queued cues and commit, and returns to listening with the existing words retained. A stale callback checks revision, silence deadline, finalized-text coverage and decode backlog before doing anything. There is one authoritative controller, not independent browser and Python timers making separate decisions.

Use a bounded speech-activity signal from the existing input pipeline, independent of transcript changes, with capture epoch and monotonic timing. Reuse the audio pipeline's actual activity facilities where available. If current input lacks a usable signal, add it before claiming an acoustic-silence guarantee. Do not mistake raw amplitude or a backlog-free transport for speech classification. Keep recognizer endpoint segmentation separate from conversation completion.

Commit requires finalized input, no active speech and a decoder watermark confirming capture through the silence boundary has been processed. If recognition stalls, show that condition and retain recoverable buffered text; do not silently dispatch old words or hang forever without a visible recovery action. Do not execute brain tools speculatively before commit. Once an action is committed, later speech is a correction/new turn; do not falsely promise to retract an already executed action.

Recheck floor/presence eligibility before model response playback, not just before generation. Continued user speech defers/cancels pending audio through existing barge-in behavior. Preserve accepted content and avoid duplicate submissions. Hush/stop remains immediate.

Cue audio must use the existing controlled playback path/reference used for echo handling, or a measured equivalent registered with that controller. Cue completion must not count as human speech/activity. Do not mute/drop microphone input to hide cue echo: that could lose the continuation the cues are intended to invite. Test simultaneous tone and human speech. If that is not reliable on a device, leave cues off and retain the improved patience plus visual signals.

## Reduce speech and consolidate incoming events

Introduce one notification-policy owner above speech playback. Producers emit structured events; they do not compose and speak sentences. Reuse existing source message/run/workflow IDs and storage; persist only presentation/digest state that is not already represented. Use the existing local SQLite database if a small notification-state table is required, not a separate database or new queue service.

The event representation includes identity/revision, source kind, timestamp, subject/reference, factual outcome, required action and expiry/resolution state. Untrusted sender names/message text remain data. Do not store repeated copies of entire transcripts or expand every event into the brain context.

Recommended routine default: collect for at least 60 seconds, allow at most one unsolicited digest every 120 seconds, and require at least 10 seconds of settled conversation quiet. These are trial settings. A timer makes a digest eligible; it never overrides speaking, partial input, decoder backlog, an active reply/readback, focus mode, uncertain presence or absence. Continuous activity may defer speech indefinitely while the queue remains visible. Queue age alone does not manufacture urgency.

Deduplicate and coalesce related events: sent → delivered → answered becomes an answer, not three spoken interruptions; repeated failure updates become one current issue; resolved decisions disappear from the digest; run/session completion for the same work is one item. Keep underlying facts available on demand. Show actionable decisions first in the digest, then grouped results. Limit spoken length to one short sentence or roughly ten seconds; detailed reading occurs only when requested. These are content bounds, not permission to truncate approved readback.

A request such as “tell me as soon as this finishes” can override the routine interval for that specific event, while still respecting the user's current speech. Generic failures, ordinary arrivals and agents waiting on decisions are not automatically allowed to interrupt. Any truly exceptional interrupt class requires an explicit product reason and a test; do not retain broad URGENT classifications by default.

Generate one digest from a bounded snapshot using the existing brain, only when eligible. Generation is read-only and cannot authorize tools, reply to senders or execute a task. Route it through the existing provider-neutral trust/turn machinery. Do not spawn an independent summarizer for every item or let a background digest delay the user's direct request. If the user speaks while it is generated, cancel/defer it and retain the events. If the snapshot has become stale, regenerate at the next eligible time.

Commit delivery only from the actual playback acknowledgement for the covered digest items. Queued, generated, cancelled, partial and fully heard are distinct. Record exactly what was heard so “which one?” refers to the real last digest. Do not mark source messages read merely because their arrival was summarized, and do not mark decisions answered because mentioned. After reconnect, recompute one current digest rather than replay old utterances.

## Remove hard-coded speech without losing required behavior

Target zero server-authored conversational sentences sent to TTS. Keep stable machine event/error codes, localized UI labels and logs; those are not the unsolicited voice problem. A guard test should prevent new application producers from calling speech output outside the approved boundary.

| Existing speech category | Replacement |
|---|---|
| Arrivals, delivery receipts, replies, finished/stalled/failed runs, needs boards and package endings | Structured facts → queue → one eligible brain-written digest |
| Greeting, startup, reconnection and sign-in reminder | Quiet state indicator; brain responds naturally when addressed; no auto greeting flood |
| Sent/cancelled/running acknowledgements | Existing transmission visuals and status; brain can explain on request or in the direct response |
| Rotation/fresh-start confirmation wording | Brain-generated question tied to deterministic pending-action ID; explicit confirmation still required. If brain unavailable, show the existing safe visual confirmation instead of guessing a yes |
| TTS filler/bridges and unread prefixes | Remove; quiet visual progress while waiting |
| Brain unavailable/auth/rate-limit faults | Accurate localized visual diagnostic and optional one nonverbal alert when engaged. No fallback paragraph, no reliance on the failed brain to report its own failure |
| Explicit message/command readback and requested quotation/replay | Speak the exact approved/user-requested content through the guarded readback path. Do not paraphrase it with the brain |

Exact payload readback is not a canned system announcement. Preserve the action digest/revision, readback acknowledgement, cancellation window and permission gate. Removing a spoken confirmation must not remove the confirmation operation. All failed actions retain inspectable outcomes; quieter does not mean silently pretending success.

For accessibility, expose complete status and confirmation data through the existing accessible UI and user-requested readout. Automatic TTS can be disabled without making failures undiscoverable. The no-brain visual fallback must have a recovery action and preserve the user's pending input where possible.

## Away experience now

Use existing trusted human-activity timestamps plus explicit Quiet/Away control, page visibility and eligible audio-client presence. Do not equate elapsed inactivity with confirmed physical absence; label the uncertain state and suppress unsolicited audio conservatively. The immediate implementation needs no camera.

While away: a stable pending-work badge and queue show current results/decisions; finished work continues and remains recorded. Use one grouped/replaced native notification through the existing notifier when the owner enables that surface, with privacy-preserving counts by default. Do not send to email, phone or another teammate unless that channel and audience are explicitly configured. Do not repeat an alert per incoming item.

On return: the UI shows one catch-up card with counts, oldest actionable item and a Read summary action. A click/pointer movement reestablishes activity but does not itself authorize an immediate spoken digest. Let the user speak first. Explicitly asking for the summary bypasses the unsolicited cadence, not readback/authorization rules.

Only one selected client/node owns unsolicited audio for a user; background tabs and reconnects must not duplicate it. Losing an audio lease silences that client and leaves events pending. If multi-node coordination is not available yet, keep unsolicited summaries opt-in per active local device rather than claim global deduplication.

## Camera and hands later

An opt-in local vision experiment can estimate occupied/unoccupied/unknown with temporal hysteresis, and recognize a deliberate hold/stop gesture. It must not treat a hand, face or motion detector as proof of identity or consent to send/delete/approve. Leaving the frame should disengage unsolicited audio after a grace period; returning should restore a quiet ready state rather than greet immediately.

MediaPipe provides JavaScript hand and body landmark models. Its inference should be isolated from the audio/UI thread, sampled conservatively and tested on both the i9 and M1 under normal workload. Camera privacy, lighting, occlusion and another person entering the frame need explicit tests. Keep only coarse presence/gesture events; do not record images or upload camera streams by default. Camera denial/disconnection returns to unknown and the non-camera controls. Sources: [hand landmarks](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/web_js) and [pose landmarks](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/web_js).

This vision work is not required for the immediate delivery. Its first useful result is reliable disengagement and a stop/hold signal, not full gesture navigation or facial recognition.

## Build sequence and tests

1. Create an isolated worktree from current main, read the three accompanying reports and reconcile ongoing voice/UI changes. Record active file ownership. Preserve current live settings while assembling a locally reviewable candidate.
2. Stabilize floor ownership first: shared settings, no hidden clamp, revision/deadline-safe timer, late-final/backlog handling, immediate stop and resumed-speech protection. Use deterministic clocks and existing utterance/scheduler tests.
3. Add a single notification policy and persistent presentation state. Migrate routine producers and the urgent unread reconnection path before adding tones; first demonstrate fewer interruptions with visuals only.
4. Move remaining automatic spoken statements to structured events or visual state, retaining deterministic approval/readback operations. Add a static call-site gate plus runtime sink tests; generated strings in helpers must not bypass the boundary.
5. Add one bounded read-only brain digest flow and actual-playback delivery acknowledgement, with interruption/retry/snapshot-staleness tests. Include both configured brain providers and unavailable-brain behavior.
6. Implement explicit Quiet/Away and conservative activity state, grouped existing OS notifications and the return card. Add callable UI equivalents and English/Spanish labels through current UI-parity/localization contracts.
7. Add optional quiet cue playback and the shared activity/watermark contract. Test the cues with AEC, background noise, speech over each tone, mute/hush and disappearing audio clients. Expose tunable patience/cue settings with a preview; no autoplay on page connection.
8. Independent review follows end-to-end journeys. Retain a local candidate and compact evidence showing precisely what was submitted and played. Human comfort tuning uses the i9 and M1, with the same short utterance/notification scenarios.

Required scenarios: long pause then continuation; resume at the commit deadline; recognition lag without changed interims; two finals plus unfinished interim; typed submit amid pending voice; active readback when ten events arrive; successive delivery/reply events for one message; a resolved need before digest; user speech during digest generation/playback; disconnected browser and later reconnect; away return with many events; brain/TTS unavailable; duplicated tabs; English/Spanish; cue echo with real overlapping speech. No automatic sends, confirmations or task actions may be caused by a digest or tone.

Measure premature commits, spoken interruptions, unsolicited sentences per interval, missing/duplicate words, response delay, cue recognitions, digest correctness and unheard events marked delivered. Record timing from accepted speech/capture, ASR completion, commit, first model output and actual audio ack. Do not claim a latency improvement from changing a constant without measuring the full path.

## Delivery and effort

The highest-impact first cut is safe patient turns plus silent routine arrivals, durable queue, presence gating and removal of canned acknowledgements/reconnection backlog. Planning allowance: 6–10 active agent-hours for that first locally testable cut, assuming existing test seams and no new audio activity dependency.

Completing all speech-category migration, brain digest/acknowledgement, optional two-stage cues with speech-activity integration, settings and end-to-end qualification is an additional 10–18 active hours. Total immediate package: 16–28 active agent-hours, reforecast after the activity-signal probe. Camera/gesture work is separate and unestimated until a local prototype is measured. The first cut is a checkpoint; it must not be represented as the completed two-cue experience or zero-template migration.

The completion target is a running local candidate for Tim to evaluate: patient turn-taking, fewer interruptions, brain-written summaries and a quiet away state. Do not merge or replace the live voice experience until that interaction has been tried. A report is not a shipped fix; record implementation and local-validation status separately.
