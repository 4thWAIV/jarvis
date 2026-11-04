# Current audio and conversation review

3 November 2026. Source revision: 375290c. This reconciliation reads current source and test definitions. It does not measure live microphone performance or inspect private recordings. User-reported behavior is identified separately from source mechanisms.

## Material reviewed

Matt's package: data/packages/jarvis-usability-review-1.0.0-4c6a2bfb, including package.json, PROPOSAL.md and REVIEW_TEMPLATE.md. Its source audit was at a633169 on fix/conversation-submit; this review checks the findings against the present checkout. Supporting prior work: speech-audit.md, turn-taking.md, notifications-presence.md and implementation.md in this package.

Matt reported quiet controls resetting after refresh and unintended room transcription. His package also covers capture ownership, actual microphone status, hidden-tab behavior, typed delivery, history recovery, scrollback and Conversation layout. Those findings substantially broaden the earlier notification-focused proposal.

## Microphone control findings

| Finding | Current evidence | Impact and proposed fix |
|---|---|---|
| Automatic startup remains | frontend/src/mic.ts initializes micMuted/micOff false; frontend/src/jarvis.ts calls voiceInput.start and sets listening | Refresh loses the quiet choice. Start disarmed before device acquisition; require explicit enable in the current tab. |
| Generic interaction can activate suspended audio | voiceInput.ts openMicrophone resumes a suspended AudioContext on click/keydown | Existing permission plus an unrelated click is insufficient listening intent. Gate acquisition and session startup on explicit authorization. |
| Off and Mute finish words | voiceInput.ts stopSending calls finishSession; closing-session messages remain accepted | Off can produce a final request. Abort live and closing sessions, invalidate capture epochs and immediately release tracks for Off. Separate Finish and pause. |
| Muted audio still has consumers | voiceInput.ts onFrame dispatches frameListeners before its quiet check | Muted package listeners may receive PCM. Decide and enforce Mute across every consumer, not only recognition. Recommended: suppress consumer delivery while retaining an explicitly labeled open device. |
| Busy is not ownership | transcriber.py TranscriptionService.admit admits one recognizer capture; voiceInput.ts retries busy sessions | Several tabs may acquire devices, and a waiting tab can take over recognition. Introduce explicit ownership and cross-tab Off propagation; no unrequested takeover. |
| Hidden pages remain active | voiceHealth reports tabHidden; visibility/activity code does not stop capture | Background capture continues. Recommend hidden-tab release initially; a later session-specific opt-in can allow background listening. |
| Readiness is visually inconsistent | jarvis.ts sets listening before recognizer readiness; MicControl exposes much of inputStatus through diagnostics/tooltip | User may speak before input is ready. Persistent actual state must show Off, Muted, Starting, Listening, Busy or Unavailable. Expose toggled state to assistive technology. |
| Retry can outlive intent | voiceInput.ts schedules device/session recovery while wanted and not quiet | Use one authorization epoch across retries and reconnect; a failure must never arm a disarmed tab. |

Existing late-session checks, segment deduplication and device-release facilities should be reused. Current tests deliberately expect automatic startup and finish-on-Mute/Off. Change those expectations rather than merely adding a UI label: tests/test_voice_page_speech_input.py and tests/test_voice_page_mute.py.

## Turn completion, latency and interruption

_Utterance in jarvis/server.py uses a 1.5-second hold and spawns now() from a timer without a generation/deadline check. Cancelling the TimerHandle cannot revoke an already spawned task. still_talking only resets when final parts exist. Interims are emitted when text changes, not as continuous speech activity. The voice WebSocket finally block calls utterance.now(), submitting buffered text on disconnect.

The proposed controller must distinguish capture activity, recognition progress, a thinking pause and a completed turn. New speech invalidates pending commits. Disconnect saves only recoverable text as an unsent draft rather than executing it. Explicit Off discards unfinished voice input; Finish and pause is the separate deliberate completion action. This resolves the earlier proposal's ambiguous treatment of Off as draft preservation versus Matt's recommended abort.

Already present: local streaming recognition; a 10-second startup buffer; paced drain; a two-second backend queue bound; a five-second finish limit; overload and lag diagnostics. Recognition overflow drops and reports an attempt rather than silently skipping arbitrary frames. A client final cannot silently replay when the voice socket is down, although the server's buffered disconnect submission still needs correction.

Measure capture, recognition, turn hold, brain, synthesis and playback separately. No evidence here justifies replacing the recognizer or Fish voice again. Optional 1.5/3-second cues and a four-second hold remain trial settings after capture and cancellation are dependable.

## Echo, readback and Stop

Browser input requests echo cancellation, noise suppression and automatic gain control. The mic stays open during output by default. speech.py includes played-text matching, short-word grace, stem checks, cancel words and playback-aware echo timing. Existing tests exercise short replies, echoes and Spanish variants.

Remaining risk: similar human words may look like echo, and garbled echo may look like new speech. The optional pause-during-playback setting avoids some echo by losing the person's simultaneous speech. Source inspection cannot settle physical double-talk performance. Measure readback plus short stop/no/wait, repeated phrases and cues on both i9 and M1 with actual output devices.

frontend/src/jarvis.ts hush returns unless the state is speaking; the server routes hush to speech.barge_in. This is playback interruption, not provider request cancellation. Keep Stop speaking immediate and accurately named. Define Cancel request separately, with explicit treatment of already executed tools or running jobs; never imply playback stop undoes an action.

## Notifications and delivery accounting

_say_notice sends NORMAL events outside LOW batching. Failure and needs-you paths may be URGENT. speech.py _release_gated can release unread backlog as URGENT, and connection greeting has a cooldown rather than an availability policy. Queue current structured facts, deduplicate lifecycle updates, and let one policy determine summary eligibility. Preserve exact user-requested narration/readback.

Delivery requires more than the current acknowledgement. _say_notice records a notice before playback; arrival_announcer completes after its callback returns. frontend/src/voice.ts playNext calls playedCallback for failed-to-decode chunks so ordering can advance. Introduce distinct played, failed, discarded, interrupted and queued outcomes. A transport-advance acknowledgement must not assert delivery. Even successful playback proves output was played, not that a human actually listened or understood it.

This corrects the earlier plan's assumption that the existing playback acknowledgement alone could establish delivery. Record the actual played content for follow-up context, and leave unheard/failed items available without automatically replaying them on return.

## Matt's conversation findings

| Area | Current evidence | Required work |
|---|---|---|
| Typed fallback | ConversationView HearingBox uses React draft state, calls sendTyped and immediately clears; ws.ts send does nothing unless OPEN | Persist drafts, show Send, distinguish pending/accepted/failed, use request identity for safe retry. A functioning text fallback matters when voice is Off or unavailable. |
| History continuity | captions.ts caps live turns at 40; conversation.ts filters current-page ordinary rows expecting live captions | Use one durable timeline keyed by stable identity. Older turns can disappear from the current view without being deleted from storage. |
| History failures | History catches older-page failure and returns without visible error | Preserve content and expose loading, stale/error and retry. |
| Reading position | History scrolls to bottom on updates | Preserve anchor and selection; show new-item indicator instead of dragging the reader. |
| Keyboard and composer | Enter-only send and limited collapsed-content accessibility | Visible Send, Enter/Shift+Enter guidance, keyboard-operable expand/retry. |
| Conversation presentation | Matt describes cramped layout and an always-reserved speaking area | Separate center-only preview for spacing, hierarchy and live captions. Preserve orb, side panels and navigation. Do not bundle a product-wide visual rewrite into the audio fix. |

## Delivery order and review choices

First project: quiet startup and explicit activation, aborting Off, cross-tab ownership, hidden-tab policy, accurate status and protection against stale/disconnect submissions. Routine canned announcements can be silenced in parallel once all outcomes remain visible. Use synthetic device/recognizer spies before controlled microphone tests.

Next: finish turn timing, durable typed fallback/history recovery, mode coordination, event digests and accurate playback outcomes. Add optional cues only after the echo and activity paths support them. Conversation visual changes are a separately reviewable track.

Decisions for Matt and Tim: start disarmed on every fresh page/reload; Off discards unfinished voice input with separate Finish and pause; Mute keeps a clearly labeled open device but stops delivery to consumers; release hidden-tab capture initially; permit only explicit listening takeover. The deck emphasizes the first three capture decisions; the implementation review must settle the complete contract.

The prior 6–10-hour estimate covered a narrower interruption-focused cut. It must not be used for this expanded microphone-control project. Estimate it from the accepted contract after ownership/retry source decomposition. Camera presence, gestures and always-listening wake detection stay separate. True Mic Off cannot hear a wake word.
