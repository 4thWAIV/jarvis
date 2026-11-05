# Explicitly armed wake-word mode

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `wake-word`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

An opt-in armed-listening contract and supported-device probe are reviewed.

## Work

Add a separate visible wake-listening mode with ownership and explicit enable/disable.

## Acceptance

True microphone Off never listens; false activation, multiple tabs, restart and device failure preserve the selected quiet state.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
