# Outgoing conversation flow

## Basis

Tim's report of the Zoom exchange with Matt adds a presentation and pacing issue: following JARVIS's moving words felt painful and potentially tiring across a larger workflow. This is reported user feedback, not a measured speech-rate result. Current source supports the mechanism: ConversationView.tsx SpeakingBox scrolls the highlighted spoken word into view, and captions.ts tracks word progression.

## Stable reading, selective speaking

Show complete available text as stable paragraphs instead of making visual reading depend on audio progress. Streaming content may append as generated; do not imply unavailable future content already exists. Preserve the reading anchor and selection. Make word-following optional, with phrase-level emphasis only when it helps. Offer short spoken summaries by default for background results and extended explanations, with detail available visually or by explicit request. Exact requested reading and approval payloads remain exact.

Natural pacing combines shorter speech with pauses at idea boundaries. Simply reducing speech speed increases the time the user must wait. Support a per-user delivery preference and sentence/paragraph breaks; measure synthesis and playback latency separately from intended silence. Do not make a pause between paragraphs trigger a new input submission or notification interruption.

## Gentle Skip the rest

Provide a visible action and callable/voice equivalent targeting the active reading identifier. The action stops that reading's current audio promptly with a short fade where available, omits its queued remainder and suppresses later audio chunks belonging to the same reading. Preserve the text, pending decisions and ongoing computation. Return to ready without a spoken acknowledgement or replay of the skipped remainder.

Bind the action to the displayed/active reading generation. A delayed skip cannot suppress a new unrelated reply. Record skipped chunks as skipped, not played, failed or read. Acknowledge transport advancement independently of delivery outcomes. If a summary is skipped, retain underlying events for visual inspection and suppress automatic repetition of that same summary; a genuinely new event remains eligible under the ordinary notification policy.

Keep three actions distinct: Skip the rest ends this reading; Stop speaking immediately silences audio; Cancel request targets a generation or job under its own cancellation semantics. None retroactively reverses an executed tool action. Preserve emergency stopping and do not route every action to a global queue clear.

For a guided presentation, the target is the current slide's narration; continue to the next slide only when the walkthrough's explicit advance policy calls for it. A normal reply simply returns to readiness. Skip during required approval readback leaves approval pending and cannot satisfy its readback-completion gate. The visible review remains available for the normal approval path.

## Acceptance

Test skip while a chunk plays, while later chunks synthesize, and at the boundary to a new reply. Verify the full text remains, unrelated queued replies survive, skipped chunks never replay on reconnect, and no canned acknowledgement is added. Test voice-triggered skip during readback with echo handling. Confirm skipping approval readback cannot approve or send. Compare stable paragraphs with optional word-following during a multi-item workflow.

## Walkthrough delivery

NARRATOR.md instructs conversational pacing with breath pauses, short beats and a clear break between the two parts. The current package story parser has no timing or speech-rate property. The package uses sentence and paragraph boundaries; it does not claim exact pause timing. Any future explicit silence support should use the shared playback scheduler and remain interruptible, rather than browser timers pretending to synchronize speech.
