# Voice capture cues

Status: Backlog — not started. Priority: first in line among the voice backlog items (Tim, 2026-11-05).
Owner: Diagnostics.
Tracking slug: `completion-cues`.
Parent: [Voice and audio execution register](../execution-register.md).
Sound design and timing: [turn-taking.md, Sound design](../turn-taking.md#sound-design).

## Purpose

One coherent set of quiet sounds, with visual equivalents, that tells Tim what voice capture is doing without his having to look: when JARVIS starts listening, when it stops listening, and, while he is talking, when it thinks he may have finished.

## Work

- **Listening started.** A very subtle beep each time capture actually starts: Start listening, Unmute, and a tab resuming after a voice connection drop. It plays when the microphone is really streaming, not when the button is pressed.
- **Listening stopped.** A very subtle beep, different from the start beep, each time capture stops for any reason: Mic off, Mute, switching tabs under Stop listening, another tab taking the microphone, the connection or the recogniser failing. This is the cue that tells him JARVIS is no longer listening, so it must play even when the stop was not his own doing.
- **Turn-completion cues.** The optional tentative and likely-end nonverbal cues described in turn-taking.md, bound to the current input revision and output owner.
- **One setting.** A persisted per-user on/off setting for capture sounds, in Settings, Voice and language, with a brain tool calling the same service. Default On (Tim's decision, 2026-11-05). It turns every sound in this item on or off together; the visual states stay either way.
- **Choosing the sounds** (which beeps, and a volume control) is left for later. Ship simple, very quiet placeholder tones under roughly 150 ms, distinguished by pattern rather than loudness.

## Acceptance

- Start and stop each play exactly once per real capture change, never on page load or reload (the microphone starts off), and never when nothing changed.
- An involuntary stop (connection lost, recogniser failure, another tab taking the microphone) plays the stop sound.
- Continuing speech cancels queued turn-completion cues. No cue enters recognition, plays while muted or off (other than the stop sound marking that change), or fires late after reconnect or audio unlock.
- With the setting Off, no sound plays and the visual states are unchanged.

## Execution boundary

This is filed future work, not part of the voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
