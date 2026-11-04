# Camera presence and hand gestures

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `camera-and-gestures`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

Core quiet/away delivery works without a camera; user opts in to an experiment.

## Work

Evaluate local presence confidence and deliberate pause/resume gestures, with hysteresis and keyboard alternatives.

## Acceptance

False absence and gesture transitions are measured; no raw camera retention by default; presence alone cannot authorize microphone acquisition.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
