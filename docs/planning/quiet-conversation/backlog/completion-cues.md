# Gentle turn-completion cues

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `completion-cues`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

Core patient turn handling and speaker echo qualification pass.

## Work

Add optional tentative and likely-end nonverbal cues, volume/Off controls and visual equivalents. Bind each sound to the current input revision and output owner.

## Acceptance

Continuing speech cancels queued cues; cues do not enter recognition, play while quiet, or fire after reconnect/audio unlock.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
