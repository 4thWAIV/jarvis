# Readback echo cancellation — Diagnostics execution handoff

Prepared 28 October 2026 from checkout `73190ca`. Status: implementation proposal; no audio changes or acoustic measurements performed. Recheck the named code on the implementation branch before editing. This document supersedes the earlier proposed fix in `self-echo.md` for this work. It includes Windows alongside Intel and Apple Silicon Macs.

## Outcome

During readback, keep microphone capture and transcription running. Reduce JARVIS playback entering recognition enough to prevent self-triggered replies and interruptions, while preserving other people speaking, including short commands and words JARVIS just said. Preserve the current voice provider, voice quality, recognizer and explicit user mute/off controls.

Use acoustic echo cancellation (AEC) before recognition. The reference is the audio actually rendered to the speakers, including its timing. AEC estimates the delayed and room-filtered playback in microphone input. It is not simple waveform subtraction, a speaker identity model, or a transcript blacklist. Perfect removal in every room is not promised; simultaneous human speech is part of acceptance.

## Current implementation and edit map

| Location | Current behavior | Work |
|---|---|---|
| `frontend/src/voiceInput.ts`, `openMicrophone` | Requests `echoCancellation: true`, noise suppression and automatic gain control. Captures continuously unless paused, muted or off. | Inspect effective settings/capabilities; preserve stream lifecycle; integrate cleaned input if needed. |
| `frontend/src/voice.ts`, `createAudioPlayer` | Decodes chunks, plays AudioBufferSourceNode through analyser to AudioContext destination, acknowledges completed chunks. | Observe actual playback timing; preserve ordering, stop, queue and acknowledgements. Supply a render reference if explicit AEC is required. |
| `frontend/src/jarvis.ts`, `micFollowsState` | Optional mute-during-speech calls input pause. | Continuous-listening mode must keep capture and delivery running throughout readback. Keep intentional user mute/off behavior. |
| `frontend/src/pcm-worklet.ts`, `pcm.ts` | Downmixes/resamples to 16 kHz PCM16, frames 100 ms packets. | Place any explicit AEC before final framing; reuse existing PCM conversion. |
| `jarvis/transcribeProtocol.json` | Shared input contract; 16 kHz mono PCM16, 100 ms frames, bounded queue. | Preserve existing wire format unless a measured need requires an explicit versioned amendment. |
| `jarvis/transcriber.py` | Kroko streaming Zipformer through sherpa-onnx on CPU. | Keep recognizer, endpoint timing and overload behavior. Historical Nemotron/Chrome-recognition plans are not the current baseline. |
| `jarvis/speech.py`, `classify`, `_should_interrupt` | Filters recent word overlap and stem echoes; treats typed input separately. | Reconcile lexical filters with cleaned human audio so repeated words remain usable. |
| `jarvis/server.py` voice socket | Routes interim/final results through scheduler; logs rejected text. | Carry processing mode/provenance if needed, and record classification decisions in existing diagnostics. |
| `frontend/src/voiceHealth.ts`, `jarvis/voice_diagnostics.py` | Existing health, events and lag diagnostics. | Extend these facilities; consolidate repeated failures into an incident rather than per-frame UI notifications. |

The browser already receives an AEC request. Separate input/output AudioContexts do not by themselves prove that playback is missing from its reference. Current Chromium source enables Chrome-wide cancellation by default under its build flag; installed version, settings, OS processing and routing still need measurement. Changing to an HTML audio element or adding a loopback peer connection is not an established fix.

## Scope and platform boundary

Implement and test the same product behavior on macOS Intel, macOS ARM64 and Windows x64. Use current Chrome as the initial shared browser target; test Edge on Windows if it is distributed as a supported client. Other browsers and Windows ARM64 remain explicitly unvalidated until tested.

The existing Windows feasibility document identifies unrelated startup/process-control work. Inspect `session_watch.pid_alive` before starting the whole product on Windows: the current source uses `os.kill(pid, 0)`, which is not a portable Windows liveness check. Run isolated voice tests first. Complete or coordinate the Windows prerequisites before full-app acceptance; do not report an isolated audio test as whole-product Windows support.

Room voices are the primary acceptance case. Include another application's played speech as a separate scope test: browser-wide or system-wide cancellation may suppress it too. If retaining those voices is necessary for release, use a JARVIS-only reference and treat failure of that test as a release blocker. Do not silently expand cancellation to all system playback.

## Prepare-worktree

1. Read repository instructions, this handoff, the current speech-input implementation and relevant tests. Inspect local changes; preserve other work.
2. Create the implementation branch/worktree using the project's normal workflow. `.agents/in-progress` is ignored in the source checkout, so copy this document explicitly into the worktree before starting. Do not assume Git copied it.
3. Record the starting commit and versions of browser, OS, recognizer/model and dependencies in a results artifact. Inspect actual runtime mute-during-speech settings.
4. Build the existing frontend and run focused baseline tests. Use isolated test data and fake actions so recognized audio cannot send messages, execute commands or change live sessions.
5. Keep the voice provider and recognition model fixed across comparisons. Use existing credentials through the existing runtime; this task needs no new paid provider or secret.

## Measure-existing-path

1. Add an opt-in diagnostic test flow using the production capture and playback components. Do not build a second independent audio implementation. Provide playback-only, person-only and simultaneous-speech trials.
2. Record `getSupportedConstraints()`, track `getCapabilities()` where available, and `getSettings()` after opening the microphone. Distinguish requested values, supported values and effective values. Settings reporting AEC enabled is not evidence of successful removal.
3. Record actual input/context rates, output route where available, audio-context state, playback start/stop timing, capture/frame continuity, queue depth and recognition timing. Label unknown routing information as unknown.
4. Run short, explicit acoustic trials on each platform. Fix speaker volume, microphone position, room and human distance for comparisons. Record those conditions. Include the real readback voice, short commands and long passages.
5. Compare current processing with an AEC-disabled diagnostic baseline in the isolated test flow. Keep gain and suppression settings constant initially. Separately vary noise suppression and gain control if quiet people or first consonants disappear. Avoid simultaneous independent microphone streams that change OS processing or introduce ambiguous comparisons.
6. Measure playback-only residual energy and false recognition, then human recognition with and without playback. Never infer preserved people from a lower microphone level. Report effective gain differences alongside residual energy comparisons.
7. Keep raw diagnostic audio optional and explicitly consented, outside Git with a short documented deletion period. Ordinary diagnostics store timing, counters and causes; reuse existing transcript retention rules rather than adding a second transcript store.

Deliverable: a reproducible baseline and per-platform decision showing whether the existing browser path can meet the gates below. Do not change a browser feature flag globally as the product fix.

## Correct-browser-path

Prefer the browser's supported AEC when it meets all tests. Inspect actual output/input routing and supported processing modes. Use capability detection and controlled constraints; unsupported settings must produce an explicit degraded state, not a false success label. Preserve device selection and re-evaluate settings after a device change or resumed audio context.

Keep the microphone and recognizer session open during readback, without clearing buffers, discarding pre-roll or restarting recognition at the speaking-state transition. Leave explicit user mute/off semantics intact. If the legacy mute-during-speech option is active, expose that fact and use the continuous-listening configuration for this feature's tests.

The `all` echo-cancellation mode attempts to remove system playback, not just this assistant. Test it only with the declared scope and supported capabilities. Do not rely on a string being accepted as proof that the browser used that mode.

Change playback routing only when a controlled comparison demonstrates both improved suppression and preserved human speech. Keep a single audible output path; duplicate output creates a second echo source. Preserve actual-playback acknowledgements and orb analysis.

Deliverable: the smallest production change supported by acoustic evidence. If browser processing passes, the explicit processor below is unnecessary.

## Explicit-AEC contingency

If the browser path fails acceptance, prepare a bounded integration spike for WebRTC Audio Processing/AEC3. This is a conditional architecture change, not an instruction to install an arbitrary similarly named package.

1. Select a maintained implementation pinned to a specific version, with license review, reproducible builds and packages for all three platform targets. Present the actual proposed adapter interface and dependency before committing that design under repository approval rules. The handoff does not claim an existing drop-in package.
2. Prefer processing at the client where capture and playback occur. Evaluate a browser/WASM worklet implementation first if it can meet real-time and deployment constraints. A native companion is a separate packaging decision and must own or reliably receive both audio streams; do not send microphone-only audio to a server and call that AEC.
3. Feed rendered PCM into the reverse/render stream and microphone PCM into the capture stream. Use approximately 10 ms processing frames, with preallocated bounded buffers. Account for input/output delay, sample-rate conversion and clock drift. A scheduled or synthesized chunk is not proof that sound played.
4. Use one coordinated audio clock where practical. Preserve the original output format/quality and analyser. Map the clean capture output into the existing 16 kHz protocol through the shared resampler/framer. Do not force the voice output to 16 kHz just to match recognition.
5. Maintain the reference across gaps and include the room's decay after playback stops. Cancelled queued audio contributes no reference; already played audio may still echo. Handle suspended contexts, device replacement, Bluetooth latency and reference discontinuities explicitly.
6. Configure one primary canceller. Avoid running browser AEC and explicit AEC in series without evidence. Check whether OS processing remains active even when browser constraints request it off.
7. Bound work and buffers. On processing failure, keep user-visible microphone state truthful and report degraded cancellation once. A fallback to the existing browser path must be explicit. Never silently mute people or drop old speech to hide a growing queue.
8. Benchmark under concurrent brain, synthesis and recognition load. Reject packaging or processing choices that recreate recognition backlog.

Deliverable: tested artifact and operating envelope for each target, or a documented failed spike. Do not conceal a missing Windows binary behind a Mac-only completion claim.

## Preserve-human-interruptions

Acoustic cleanup and scheduler policy must ship together. Current lexical rules can reject a human saying the same word or sentence as JARVIS even after successful AEC.

1. Add regression cases for an independently spoken repeated word, repeated sentence, short stop command and partial overlap, during readback and immediately after it.
2. Compare classifier outcomes on known playback-only and human-over-playback recordings. A recognized phrase is not automatically human; a word match is not automatically echo.
3. For a processing path that meets acoustic acceptance, stop using recent-word overlap as an unconditional veto on cleaned speech. Retain the legacy lexical behavior for legacy/degraded input paths until their behavior is explicitly resolved. Track input processing mode separately from authentication and never let client telemetry grant privileges.
4. Browser track settings alone cannot certify that an individual frame contains a person. Use measured path behavior and available acoustic evidence; voice activity alone also detects JARVIS. If relaxing the lexical veto reintroduces self-triggered turns, the path has not passed acceptance. Return to audio processing rather than raising word-count thresholds.
5. Preserve typed-input bypass, explicit Stop/Escape, cancel-window behavior, replay behavior and utterance ordering. Revisit the deliberate readback confirmation/cancel window with tests, because changes here can affect message sending.
6. Timestamp accepted speech activity at the existing recognition boundary. Do not fold the separate reply-pause tuning proposal into this work.

## Diagnostics

Use the existing diagnostics records for processing mode, effective constraints, route changes, discontinuities, CPU/queue trends, playback start/end and rejection reason. Correlate capture, utterance and chunk identifiers. Keep monotonic durations distinct from wall-clock log timestamps.

Report measured echo attenuation only for suitable labelled playback-only trials. Report unexposed metrics as unavailable. An `echo` classifier verdict is an allegation, not ground truth. Summarize recurring degraded states in one incident and update its count; do not create a notification per frame or rejected word.

## Acceptance matrix and proposed release gates

These are proposed engineering targets, not measurements or previously approved guarantees. Freeze them in the implementation contract before release; do not lower them silently to pass.

| Test | Proposed gate |
|---|---|
| Continuous capture | No readback-triggered microphone close/reopen, dropped startup audio, or recognition-session reset. |
| Playback alone | Zero accepted self-generated commands, new turns or self-interruptions in a 30-minute varied readback test per target/configuration. Count residual transcripts separately. |
| Human alone | No lost first word in 50 scripted trials; word error rate no more than 2 percentage points worse than baseline. |
| Human over readback | At least 95% of 50 scripted commands recognized with intended meaning at normal volume; WER no more than 5 percentage points worse than human-only baseline. |
| Repeated words and stop | At least 19 of 20 trials accepted for each category, including readback onset and decay; investigate every miss. |
| Interruption timing | Added processing delay p95 at most 50 ms; accepted-interim-to-playback-stop p95 at most 150 ms. Also report speech-onset-to-stop separately, including recognizer delay. |
| Resource stability | One-hour mixed workload without overload resets or growing queues; no more than 100 ms increase in p95 input backlog versus the matched baseline. Record CPU and memory. |
| Lifecycle | Correct behavior after stop, queue cancellation, tab visibility changes, reconnect, output/input switching and resumed context. |
| Explicit controls | User mute/off, keyboard stop and typed messages retain existing behavior. |

Run on macOS Intel and ARM64, and Windows x64. Test built-in and USB devices; test Bluetooth separately before claiming it supported. Include normal and loud usable playback, a quiet speaker, a second person and realistic room distance. Mark adverse configurations that fail as unsupported or unresolved rather than claiming universal cancellation. Human speech must remain intelligible; silence alone never passes.

Fake browser microphones prove lifecycle and protocol behavior, not acoustic performance. Add deterministic DSP fixtures for delay, echo tails, clipping, drift and simultaneous speech if an explicit processor is introduced. Real speaker/microphone testing remains required.

## Automated verification

Use the repository's existing environment and current test instructions. From `frontend`, run `npm run build`. From the repository root, run focused Python tests including `tests/test_speech.py`, `tests/test_turn_taking.py`, `tests/test_voice_ws.py`, `tests/test_voice_backpressure.py` and `tests/test_voice_diagnostics.py` with the existing virtual-environment Python. Run the existing page-marked tests with `-m page`, including speech input, mute, captions and voice health; inspect their fixtures and build requirements first. Extend existing tests instead of creating a parallel test harness for the same behavior.

Page tests already use browser automation fixtures. They are isolated automated tests, not instructions to control Tim's personal browser. Confirm skipped browser/audio tests explicitly in the report. Use Windows-compatible environment commands on Windows; do not copy Unix virtual-environment executable paths there.

## Rollout, rollback and handoff evidence

Ship the measured path behind one clearly documented processing-mode setting using existing configuration conventions. Start with controlled local trials, then ordinary readback on each validated platform. Record default, effective mode and degraded behavior. Preserve the prior mode for rollback; rollback must not silently enable mic muting. Keep rollback compatible with any protocol metadata added.

Diagnostics should deliver a PR with code, focused tests, reproducible test instructions, platform results, measured limits and rollback instructions. Keep optional recordings and machine-specific data outside Git. Include the exact dependency version and build provenance if explicit processing is used. Do not merge or deploy merely because the plan exists.

## Effort and remaining dependencies

Planning estimates for one engineer: baseline instrumentation and trials 1–2 days; browser-path changes plus scheduler/diagnostics work 2–4 days; cross-platform regression, acoustic trials and packaging checks 1–2 days. Browser-path total: approximately 4–8 engineering days. If explicit AEC is needed, allow an additional 5–10 engineering days for the spike, integration and tuning; native companion distribution may require a revised estimate. These are not promises of agent wall-clock runtime.

Human involvement: a short guided overlapping-speech trial on each physical machine, ordinary browser microphone permission if not already granted, and approval of a new dependency/interface if the contingency requires one. Diagnostics automates fixture generation, instrumentation, builds and result collection. Access to a Windows test host is required for a Windows support claim. Existing Windows startup blockers may need separate work. No credentials should be requested simply to run local acoustic tests.

## Primary references

- Chromium audio feature definitions: https://chromium.googlesource.com/chromium/src/+/HEAD/media/base/media_switches.cc
- W3C echo-cancellation scope: https://www.w3.org/TR/mediacapture-streams/#dom-echocancellationmodeenum
- WebRTC processing contract: https://webrtc.googlesource.com/src/+/refs/heads/main/api/audio/audio_processing.h

## Prompt to give Diagnostics

Read `.agents/in-progress/readback-echo-cancellation-diagnostics-handoff.md` from the source JARVIS checkout and copy it into your implementation worktree because the source folder is Git-ignored. Prepare the implementation contract under the repository's instructions. Implement readback echo reduction while keeping the microphone open and preserving human interruptions. Begin with the current code and an isolated acoustic baseline; do not assume browser AEC is absent or replace the voice provider or recognizer. Cover macOS Intel, macOS ARM64 and Windows x64. Resolve lexical rejection of repeated human words as part of acceptance. Use the existing diagnostics and tests. If browser AEC fails, present the measured failure and the pinned explicit-AEC integration design before adding that dependency. Return the PR, platform evidence, remaining limitations and rollback instructions. Keep live actions disabled during audio trials and leave recordings and secrets outside the repository.
