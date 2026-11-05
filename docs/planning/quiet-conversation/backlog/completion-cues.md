# Voice capture cues

Status: Built (2026-11-05): the listening-started and listening-stopped sounds, the tentative and likely-end pause cues driven by the server's turn controller, and their tuning in Settings and by the brain. The sounds themselves are still placeholders to be tuned by ear. Priority: first in line among the voice backlog items (Tim, 2026-11-05).
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
- **One setting.** A persisted per-user on/off setting for capture sounds, in Settings, Voice and language, with a brain tool calling the same service. Default On. It turns every sound in this item on or off together; the visual states stay either way.
- **Tuning** (Tim, 2026-11-05). Per user, in the same voice input settings service, route and brain tools: each cue on or off, a tone from a small fixed set of quiet presets (rise, fall, pulse, low, double, resolve), the two pause cues' timings, and one cue volume (5 to 100 percent of a quiet ceiling). Capture sounds stays the master switch. Settings, Voice and language has a row per cue with a Test button that plays it once and a line saying when it plays; the brain's `play_cue` plays one on his page. The tones are placeholders under roughly 150 ms, distinguished by pattern rather than loudness, until they are chosen by ear.

## Pause cue timing

The pause cues are timed in seconds before the end of his turn, from the same deadline as the commit: the end of his turn is his patience after the last word accepted as his, and a pause cue plays at patience minus its seconds. The tentative cue's seconds must be more than the likely-end cue's (a change that would put them out of order is refused), each 0.25 to 10. A cue whose time would fall at or before the moment he stopped (its seconds not shorter than his patience) does not play at that patience; changing his patience never invalidates them. Defaults 2.5 and 1.0 give turn-taking.md's trial timings at a patience of 4 (tentative after 1.5 s, likely-end after 3 s, the commit at 4); at the current default patience of 1.5 only the likely-end cue plays, after 0.5 s. Settings shows, under each timing, when it plays at his patience.

## Acceptance

- Start and stop each play exactly once per real capture change, never on page load or reload (the microphone starts off), and never when nothing changed.
- An involuntary stop (connection lost, recogniser failure, another tab taking the microphone) plays the stop sound.
- Continuing speech cancels queued turn-completion cues. No cue enters recognition, plays while muted or off (other than the stop sound marking that change), or fires late after reconnect or audio unlock.
- With the setting Off, no sound plays and the visual states are unchanged.

## Execution boundary

This is filed future work, not part of the voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
