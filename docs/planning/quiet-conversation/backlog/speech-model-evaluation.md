# Recognition and output model alternatives

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `speech-model-evaluation`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

Measured residual latency or fidelity identifies a need after core fixes.

## Work

Compare recognition alternatives and optional local speech against current recognition and Fish on Intel i9 and Apple Silicon; include multilingual behavior and resource contention.

## Acceptance

Publish reproducible latency/accuracy/listening evidence; changing the selected/default model is a separate reviewed rollout, never an automatic fallback.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
