# Voice capture cues

Status: Built (2026-11-05): the listening-started and listening-stopped sounds, the tentative, deciding and likely-end pause cues driven by the server's turn controller, and their tuning in Settings and by the brain. The sounds themselves are still placeholders to be tuned by ear. Priority: first in line among the voice backlog items (Tim, 2026-11-05).
Owner: Diagnostics.
Tracking slug: `completion-cues`.
Parent: [Voice and audio execution register](../execution-register.md).
Sound design and timing: [turn-taking.md, Sound design](../turn-taking.md#sound-design).

## Purpose

One coherent set of quiet sounds, with visual equivalents, that tells Tim what voice capture is doing without his having to look: when JARVIS starts listening, when it stops listening, and, while he is talking, when it thinks he may have finished.

## Work

- **Listening started.** A very subtle beep each time capture actually starts: Start listening, Unmute, and a tab resuming after a voice connection drop. It plays when the microphone is really streaming, not when the button is pressed.
- **Listening stopped.** A very subtle beep, different from the start beep, each time capture stops for any reason: Mic off, Mute, switching tabs under Stop listening, another tab taking the microphone, the connection or the recogniser failing. This is the cue that tells him JARVIS is no longer listening, so it must play even when the stop was not his own doing.
- **Turn-completion cues.** The optional tentative and likely-end nonverbal cues described in turn-taking.md, bound to the current input revision and output owner, and between them a third, Deciding (Tim, 2026-11-05): he is still quiet and JARVIS is deciding whether he is done. Its own sound and its own settings, like the other two.
- **One setting.** A persisted per-user on/off setting for capture sounds, in Settings, Voice and language, with a brain tool calling the same service. Default On. It turns every sound in this item on or off together; the visual states stay either way.
- **Tuning** (Tim, 2026-11-05). Per user, in the same voice input settings service, route and brain tools: each cue on or off, a tone from a small fixed set of quiet presets (rise, fall, pulse, low, double, resolve), each pause cue's timing, and one cue volume (5 to 100 percent of a quiet ceiling). Capture sounds stays the master switch. Settings, Voice and language has a row per cue with a Test button that plays it once and a line saying when it plays; the brain's `play_cue` plays one on his page. The tones are placeholders under roughly 150 ms, distinguished by pattern rather than loudness, until they are chosen by ear.

## Pause cue timing

Each pause cue is timed as a share of his patience (`percent`), counted from the moment the hold starts, when his speech was last heard; the turn is sent at 100 percent. So at any patience every pause cue that is on plays, in order, before the send, and none can fall before he stopped (Tim, 2026-11-05: at his patience of 1.5 s the earlier seconds-before timings put the tentative and deciding cues before his speech stopped, so only the likely-end cue ever played). Defaults 40, 65 and 85 percent: at 1.5 s after 0.6, 0.98 and 1.28 s of quiet, at a patience of 4 after 1.6, 2.6 and 3.4 s.

The order holds by construction. The pause cues that are on must each be at least 5 points after the one before that is on, from 5 to 95 (so the first is at least 5 points after the hold starts and the last at least 5 before the send). The service refuses any change that would break that, turning a cue back on included, and says the range each cue may take; the brain's tools go through the same service. In Settings each field is bounded by its neighbours that are on and a value typed past one is kept at the bound, so the fields cannot cross. A cue that is off is skipped: it is not timed, is not shown on the turn bar, and holds nothing in place. Each cue has its own share and there is no shared spacing, so the gaps either side of the deciding beep are tuned apart. Settings shows, under each timing, after how many seconds of quiet it plays at his patience and its gap to the pause cue before it that is on and to the next one (or to the send); the brain's settings read says the same.

Timings saved before this as seconds before the end of his turn are dropped (migration 6): a lead in seconds is a different share at every patience, and a lead longer than his patience never played, so there is nothing meaningful to convert. Those cues read with the default shares and keep their tone and on or off.

## Acceptance

- Start and stop each play exactly once per real capture change, never on page load or reload (the microphone starts off), and never when nothing changed.
- An involuntary stop (connection lost, recogniser failure, another tab taking the microphone) plays the stop sound.
- Continuing speech cancels queued turn-completion cues. No cue enters recognition, plays while muted or off (other than the stop sound marking that change), or fires late after reconnect or audio unlock.
- With the setting Off, no sound plays and the visual states are unchanged.

## Execution boundary

This is filed future work, not part of the voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
