# Patient turn-taking and quiet completion cues

Source audit and proposed immediate change, 3 November 2026. No application code or live settings changed. This document is a contributor report for the combined quiet-conversation plan.

## Current behavior

The older `.agents/in-progress/voice-pause-tuning-plan.md` remains accurate about the principal timers. `jarvis/transcriber.py` sets `ENDPOINT_SILENCE_SEC = 0.8` for sherpa's second endpoint rule and `TAIL_PADDING_SEC = 1.5` for session finishing. Endpointing produces a final recognition segment; it does not determine whether a person has finished their thought.

`server.py:_Utterance` gathers finalized segments and schedules `now()` after `speech.END_OF_TURN_SEC`, currently 1.5 seconds. Each accepted new final restarts that hold. `still_talking()` restarts it on accepted interim text only if at least one final is already buffered. `SpeechScheduler` separately gates audio starts at `min(end_of_turn, pause_after)`, and normal announcements use a three-second `pause_after`. The silent minimum clamp means simply raising the input hold beyond three seconds will not make output equally patient.

Interims are not continuous speech activity. `SherpaSession._decode` emits an interim only when recognized text changes. `Capture` then coalesces interims; final segments are not coalesced. The browser passes interims/finals to the voice socket, where `SpeechScheduler.user_interim/user_final` decides whether they are user speech or echo. There is no acoustical speech-active frame in that conversation protocol. Timing since the latest ASR event is not exact time since the last spoken sound.

A cancellation race also exists in the simple hold timer: `_wait` schedules a callback which spawns `now()`. Cancelling its TimerHandle does not cancel an already-created task; `now()` has no activity-generation argument or current-deadline check. The first fix should eliminate this race rather than changing one constant only.

`voiceInput.ts` requests browser echo cancellation, noise suppression and automatic gain control. `voice.ts` owns an AudioContext and ordered TTS playback/acknowledgements. The server's echo policy is largely transcript matching against recent played text. A new nonverbal cue has no text to match; it must be accounted for explicitly and tested on speakers.

## Recommended immediate package

Use a shared conversation-floor controller with persisted patience settings, then make both dispatch and audible output consult it. Do not replace ASR, speech output or the existing transmission approval flow.

Trial defaults: tentative cue after 1.5 seconds of accepted-recognition inactivity; likely-end cue after 3 seconds; turn commit after 4 seconds. (As built, 2026-11-05: each pause cue is set as a share of his patience from when the hold starts, with a third cue between them, Deciding; defaults 40, 65 and 85 percent, so at a patience of 4 after 1.6, 2.6 and 3.4 seconds and at 1.5 after 0.6, 0.98 and 1.28, every cue that is on always before the commit and in order; see [completion-cues](backlog/completion-cues.md#pause-cue-timing).) The last interval is an extra opportunity to continue, not immediate permission to speak. These are starting values for a local listening trial, not measured optimal timings. Do not claim these occur exactly 1.5/3/4 seconds after acoustic silence; recognition finalization changes the observed timing. Compare with 3.5 seconds if four feels too slow.

The first code checkpoint can deliver the four-second shared hold, stale-callback protection and routine-notification suppression without audible cues. Add the two cues to that same local candidate after cancellation, audio ownership and echo behavior pass. Do not use `speech.say` to generate the tones or add two spoken prompts.

### Floor states

| State | Meaning | Allowed output |
|---|---|---|
| Idle/listening | No pending human utterance or reply | Eligible background digest only after its own quiet/cooldown policy |
| Gathering | User owns the floor; finalized/interim segments belong to one utterance | No unsolicited speech |
| Tentative end | First patience threshold reached, user still owns the floor | One optional soft cue plus existing orb/listening indication |
| Deciding | Middle threshold reached (added 2026-11-05), user still owns the floor | One optional soft cue of its own; no digest or response playback |
| Likely end | Last threshold reached, user still owns the floor | Last optional soft cue; no digest or response playback |
| Commit pending | Deadline reached; recognition and activity revision being checked | No speech until check succeeds |
| Awaiting reply | One user turn dispatched | Only that reply/explicit requested readback may take the floor when still eligible |
| Reply playing | Assistant has the floor while user may barge in | Current utterance only; queued events remain visual |
| Away/unknown | User not available or availability not established | No ordinary speech or cue; persistent visual summary |

Accepted speech at any cue stage immediately returns to gathering, cancels the cue/commit generation and extends the same buffered utterance. Merely reaching a likely-end threshold never dispatches an action. Do not speculate a model/tool turn at the first cue; resumed speech would otherwise arrive after an action has already started.

An explicit typed submit is intentional completion and keeps its immediate behavior. A dedicated Send-now control may commit the current utterance without waiting; it must have a brain-callable equivalent under UI parity. Hush remains immediate. If a spoken wait phrase is accepted, keep gathering or explicitly hold; never auto-repeat a reminder each few seconds.

### Shared cancellation and ownership

1. Introduce one monotonic activity sequence and deadline owner for the originating voice client/capture generation. Every accepted interim/final, explicit submit, stop, disconnect and state-changing operation invalidates stale callbacks.
2. A scheduled cue or dispatch carries the sequence, utterance ID and expected deadline. Immediately before executing, recheck identity, current sequence, buffered state and monotonic time. Cancelling a TimerHandle alone is insufficient.
3. Publish the state to the bound voice page; local audio reacts to server-authorized state. Do not run a second browser completion timer or emit tones in every connected tab.
4. Recheck the same state at the actual audio-start boundary, after synthesis/loading. If the user resumed, hold or cancel the stale response before playback. Avoid assuming the check at model dispatch is enough.
5. If a continuation arrives after the brain turn was already committed, preserve it as a next user input and prevent the now-stale spoken reply from barging over it. Do not rewind or silently duplicate already-started tool actions.
6. Hold current finalized words if a later recognized segment is still interim-only. Preserve a pending-segment identity. Recognition backlog/failure is not silence: map the originating transcription session and its backlog/health to the floor controller; require relevant audio to be processed before commitment.
7. A continuous stream always has fresh frames, so requiring total queue length zero forever is wrong. Use capture/processed sequence watermarks and a bounded lag threshold appropriate to frame timing. Capture identity must match the voice client; do not use a global worst/last monitor value.
8. If recognition stalls, preserve the partial text and show the existing voice-health condition with Retry/Send visible. Do not send only the old final portion as if the sentence were complete. A bounded error state is better than an invisible infinite wait; recovery must not duplicate buffered text.
9. A raw level meter may veto output conservatively but must not establish turn completion or user identity. Stronger speech activity requires a tested VAD/AEC path; do not claim the current RMS/ASR event stream provides one.

### Sound design

Use a short, low-volume, rounded nonverbal sound, without a click or alarm envelope. The first is a single soft pulse; the second is a closely related two-note resolution. Keep each audible event under roughly 150 ms as a trial and distinguish them by pattern rather than loudness. Default gain should be auditioned on the user's normal speakers; numeric gain is not a guaranteed perceived loudness. Provide one persisted cue-volume control and Off, with visual state always available.

Play through the existing audio context/output ownership where practical, but do not create ordinary TTS utterances, captions or played-message receipts for cues. Never change mic state merely to play a cue. A cue cancelled before playback must not be emitted later after audio-context unlock. On resumed user activity, cancel scheduled sources and use a short envelope to avoid clicks.

Only play cues during an active gathered utterance and at most once per stage of that pause. Do not beep periodically while the room is idle, while away, while mute/off is active, on reconnection, or when an announcement arrives. Cue playback is neither human activity nor new recognition input.

Account for cue playback in echo handling. Prefer the existing browser AEC/reference path and tiny non-speech cues; record cue timing separately and test that it does not generate accepted text. Do not solve echo by dropping all microphone audio during the tone: that would lose the very continuation the cue invites. If speakers trigger false words, retain visual-only cues until the reference path works.

## Agreement with notification/presence work

Routine digest delivery requires both the notification policy (proposed oldest item at least 60 seconds, cooldown at least 120 seconds) and a genuinely free conversation floor. Tentative/likely-end stages remain owned by the user; the digest cannot use them as an opening. Pending reply, approval/readback and buffered words also block it. A further ten-second quiet interval after both the completed exchange and latest accepted human activity is a trial, not permission to break an explicit wait state. Away/unknown suppresses routine sound entirely. User-initiated requests for an update can receive a concise brain-generated summary immediately under the normal reply policy.

A simple boolean such as `speech.speaking` is insufficient: a user can be thinking silently with no TTS playing. The floor state must be the single input to both dispatch and background-delivery eligibility.

## Tests and tuning

Extend `tests/test_turn_taking.py`, `tests/test_speech.py`, `tests/test_voice_ws.py`, transcriber/backpressure tests and page audio tests. Replace arbitrary sleeps with a controllable clock for new transitions where possible.

Required cases: final→interim→final continuation; initial interim with no final; already-queued old timer; speech at each cue boundary; speech during synthesis; stale cue after audio unlock; multiple tabs; ASR backlog watermark; capture rotation; disconnect; muted/off state; typed submit; hush; no routine digest during a tentative pause; no tone recognized as user speech; no duplicate turn/action; recognition failure preserves partial words.

Run short questions, deliberate mid-sentence pauses, lists, corrections and pauses while another agent finishes work, on the i9 and M1 under normal load. Track premature turn splits, lost/duplicated words, false cue echoes and actual first-audio latency. Annotate ASR finalization, turn-hold, brain and playback delays separately. A local hands-on audition decides whether the four-second default and two sounds feel right; no live global timing change before that acceptance.

## Later presence/gesture work

Camera presence or hand gestures should send additional engagement evidence to the same floor controller, not a competing conversation state machine. A visible hand is not necessarily a command; presence is not authentication. Begin with an explicit away/focus control and trusted GUI/speech activity. Later opt-in on-device camera/gesture experiments must cover occlusion, multiple people, leaving/returning and accidental gestures. Never treat physical presence as consent to an action or permission to speak every pending item.
