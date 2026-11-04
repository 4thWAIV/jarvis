> Superseded in part by [current audio review](current-audio-review.md), 3 November 2026: microphone control is the first project; explicit Mic Off aborts unfinished voice input; Mic Off is distinct from stopping playback or background jobs; transport acknowledgement must distinguish played from failed audio. Earlier estimates do not cover the expanded scope. Retain other sections as supporting proposals.

# Recovery and interaction modes for review

This extends the presentation with proposed behavior. It is not an implementation status report. The source snapshot distinguishes microphone muted (capture open, no audio sent) from microphone off (capture released) in frontend/src/voiceInput.ts. Full interaction Off and Observe-only require a shared controller across input, output and notification policy; simply relabeling those microphone controls is insufficient.

## Recovery

Reuse the current conversation/history owners and existing local SQLite database for additional presentation state. Keep the committed conversation reference, provider/session reference, last committed turn, safely saved unfinished text, explicit mode preference, pending event IDs and actual playback acknowledgements. Do not create a second transcript store or persist credentials or raw audio for this feature.

On reconnect, read saved references and reconcile live jobs, decisions and event revisions with their owners. Display loading, current, failed or uncertain status. Reattach to an available provider session; when it is unavailable, explain that a new context is needed and offer a deliberate restart using a bounded summary. A restored screen is not proof that provider context or an action resumed.

Preserve partial recognized text only as a reviewable draft. Do not auto-submit it, repeat an action with an uncertain outcome, revive approval grants, replay the speech queue or mark queued speech heard. Persisted input may be incomplete; unrecovered audio cannot be reconstructed. Pending work remains visible until its real completion or dismissal.

## Proposed modes

| Mode | Microphone and input | Output and events | Exit |
|---|---|---|---|
| Off | Release capture; invalidate pending voice timers and callbacks | Stop voice; retain history and pending work | Explicit user activation |
| Observe-only | No ambient capture or speech submission in the first implementation | Update system-event queue and status silently; no new tool actions caused by incoming content | Explicit user engagement |
| Preparing | Acquire permission and initialize existing recognizer; show readiness or failure | No greeting or backlog announcement | Ready becomes Conversation; failure returns to the prior quiet mode with recovery controls |
| Conversation | Patient turn-taking, recognition backlog checks, manual finish option | Direct replies first; routine digests gated | User-selected Observe/Off, connection failure, or an explicitly enabled idle timeout |

Observe-only is not ambient transcription or an instruction to stop already approved jobs. If the user requests a readout while observing, allow that bounded readout and return to Observe-only afterward. Typed requests remain explicit actions and do not silently enable the microphone. Voice cannot wake the first-version Observe-only or Off mode because neither listens.

Off here means the voice interaction is off, not that the server or running work is shut down. The UI must distinguish these concepts. Existing mute remains a separate microphone mechanism; it does not establish the complete mode policy.

## Transition ownership and time

One controller owns mode, active capture epoch, pending turn revision, output cancellation and notification eligibility. Persist explicit preferences. On process/browser restart, restore Off if explicitly chosen; otherwise recover quietly into Observe-only and require deliberate reactivation. This is proposed restart behavior for review.

A short pause inside an unfinished turn never starts the idle timeout. Restart the pause deadline on speech, cancel stale callbacks and wait for recognition coverage before commit. Start any idle timeout only after the conversation is settled and no input, response, approval or requested readout is pending. A timeout duration is a user setting to agree; the presentation does not invent a default.

Time or uncertain presence may reduce unsolicited activity. Only explicit human engagement may increase microphone or speech permission. Mouse movement, returning network connectivity, an incoming message or camera presence is not activation. Queue age is not a reason to override the mode. Resume checks readiness and context; it does not replay the backlog.

Manual Off wins immediately. It stops current and queued output and invalidates input callbacks. Preserve safely captured text as an unsent draft where possible. A tool action already committed is not retroactively cancelled; reconcile its outcome independently. Connection loss stops the client and leaves durable work status available for recovery.

## Earliest independent project

Implement the bounded quiet-session fix: generation-safe pause submission, continued-input preservation, silent routine event collection, suppression of reconnect announcements and explicit quiet control using existing input facilities. Demonstrate a long pause with continuation, an event burst while speaking and a quiet reconnect.

The original 6–10 active-agent-hour first-cut allowance remains a planning reference for that narrower work. Full mode ownership, recovery reconciliation and readiness handling expand the scope and require a new estimate after source reconciliation. Do not represent the original 16–28-hour package allowance as covering all additions automatically.

## Later experiments

Opt-in local camera presence and a deliberate hold/stop gesture can help disengagement. A separately enabled wake detector requires an active microphone and a visible armed state distinct from Off. Measure false activations, missed wake-ups, energy use, speaker playback interaction and usability before choosing it. None is a prerequisite for the first project.

## Acceptance additions

Test restart from each mode, lost provider context, safely saved partial text, uncertain committed actions, cancelled output, stale callbacks after Off, a requested readout returning to Observe-only, activity that must not wake the system, and reconnection while another client owns the voice session. Confirm background job status remains independent of voice mode.
