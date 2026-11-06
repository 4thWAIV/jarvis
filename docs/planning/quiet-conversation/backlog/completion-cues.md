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

- **Speech heard** (Tim, 2026-11-05). A separate cue, distinct from listening started: it plays once when JARVIS hears him begin speaking, that is when a new turn starts gathering from the first word accepted as his. It also plays when he resumes speaking after a pause cue sounded in the current turn (the first word accepted as his after the tentative, Deciding or about-to-send cue), once per resume (Tim, 2026-11-05). Never for JARVIS's own voice or its echo, never while muted, off or away, and not again for each word after. Its own setting like the others (on or off, tone, default `double`, which no other cue uses by default, a Test button and a line saying when it plays), in the brain's tools too.

## Pause cue timing

The pause is four intervals in order, each set on its own and each measured from the point before it (Tim, 2026-11-05): from when his speech was last heard (the hold start) to the tentative "may be finished" cue; from that cue to the Deciding cue; from that cue to the likely "about to send" cue; and from that cue to the send. The wait before sending is their sum, so every cue that is on always plays, in order, before the turn is sent; no cue can play after a later one or after the send. Defaults 1.0, 0.75, 0.75 and 0.5 seconds: the cues after 1.0, 1.75 and 2.5 s of quiet and the send at 3.0. Each interval is 0.25 to 5 seconds. Speech at any point starts the pause again from the first interval.

His patience is that total, shown in Settings and never set separately; everything that used patience uses it (the turn controller's hold, the speech scheduler's rule that nothing JARVIS says starts while he has spoken within his patience, the brain's settings read). A cue that is off keeps its interval: the time passes with no sound, so the later stages keep their times.

One ordered list of pause stages (`turn_controller.PAUSE_STAGES`) drives it and one function (`turn_controller.pause_schedule`) turns the intervals into each stage's time; the route answers that schedule, and Settings shows it rather than working it out. Settings shows the four intervals in order, each with its own field and the time its stage comes after he stops, each cue's tone, on or off and Test beside its interval, and the total. The brain's `voice_input_settings` and `set_voice_input_settings` read and take the four intervals.

Earlier timings are dropped (migration 7): a patience saved on its own, and the cues' shares of it (percent, which replaced seconds before the end of his turn in migration 6), cannot become intervals he never chose, so those rows read with the default intervals (a total of 3.0 s) and keep each cue's tone and on or off. The shares were rejected because at his patience of 1.5 s the first cue came after only 0.6 s.

## Acceptance

- Start and stop each play exactly once per real capture change, never on page load or reload (the microphone starts off), and never when nothing changed.
- An involuntary stop (connection lost, recogniser failure, another tab taking the microphone) plays the stop sound.
- Continuing speech cancels queued turn-completion cues. No cue enters recognition, plays while muted or off (other than the stop sound marking that change), or fires late after reconnect or audio unlock.
- With the setting Off, no sound plays and the visual states are unchanged.

## Execution boundary

This is filed future work, not part of the voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
