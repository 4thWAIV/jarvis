# Isolated macOS browser-audio interception proof

## What you are asked

You are asked to approve the architecture of one isolated macOS experiment. The experiment redirects the browser's audio output and its page speech into one proof mixer, which plays the sound and records the same samples. Nothing in this design is built or run yet. It changes nothing in the product: it is a compile-selected diagnostic behind the `DeniceAudioInterceptionProof` property, run from a separate bundle, and normal builds do not contain it.

You are also asked one question: whether to allow one diagnostic multi-process run whose only job is to get a device ID, so the experiment can test a page that picks a particular speaker or headset. That question is stated in full under "explicit-device-prerequisite" below.

The sender recommends approving both. The reason given is that this is the candidate ready to test for capturing every browser sample from the first one without a Chromium build, and the ID run lets the test cover pages that pick a device.

## What this fixes, if it works

Today, in the stock host, CEF's audio callback starts only after Chromium detects sound, and it stops after 2 s of silence. These are measured results from 2026-11-26, with a test sound whose left channel is a ramp, so the first delivered value names the exact sample:

- A cold sound lost its first 3460 samples (72.08 ms) before the callback started.
- A sound after 3 s of silence lost its first 4165 samples (86.77 ms).
- A sound 0.5 s after the previous one, while capture was still running, arrived complete. So did a new AudioContext and a new media element started while capture was running.
- DENICE's recorder kept every sample it was given. The decoded MP4 matched the callback samples at lag 0.
- An offset of about 74 ms between the two capture streams' timestamps was seen. Which stream is off, and whether CEF or DENICE causes it, is not known.

Two production gaps stay open whatever you answer here. CEF mutes normal output while it captures, and DENICE has nothing that plays the sound back. The experiment plays the sound through its own mixer, but that mixer is a proof, not the production mixer.

## What the check covers and what it does not

The document passed the `arch` language check with nothing wrong. That check proves the document is well formed in the language. It does not prove the design works, and there is no code for it to be compared with.

These parts of the design are outside what the language can carry, so the check does not look at them. Each one can go wrong without the check noticing:

- **Shutdown ordering.** The order of steps across `CefRuntime.Initialize` and `CefRuntime.Shutdown` is only partly in the document, one member line at a time. The full order is in the evidence: the replacements stay installed until the process exits, the mixer keeps pulling streams until `CefRuntime.Shutdown` has returned with no stream left, and only then does the mixer stop. If the build gets this order wrong, Chromium can call a stopped mixer during shutdown, and the result is a crash, a hang or a cut-off final sound.
- **Acceptance and failure observations.** What counts as a pass or a fail for each case is listed in the evidence, not in the document. The table is below under "What the experiment must show".
- **The three calls that are not replaced.** The CEF framework imports eight AudioToolbox functions. The design replaces five. According to the evidence, `AudioComponentFindNext`, `AudioUnitInitialize` and `AudioUnitUninitialize` pass through and are logged. The document itself does not name them. The evidence's acceptance row says "the eight replaced slots logged", which conflicts with five replaced and three passed through. The build has to log all eight and replace five.
- **The existing CEF audio handler.** The document does not say whether CEF's existing audio callback into DENICE stays attached during the proof. If it stays attached and also reaches the recorder, the recording gets each sound twice. The evidence lists "doubled playback" and "each sound heard once" as checks, so the experiment would catch it, but the design does not settle it.
- **Existing types the proof uses but does not change:** `CefPlatformBrowser`, `MacDesktop`, `Mp4Recorder` and `IPlatformBrowserAudioSink`. The evidence says the proof adds guarded lines in `CefPlatformBrowser.cs` and `MacDesktop.cs`, and a proof-only allowance in the native-import analyzer for dyld image enumeration and `vm_protect`.
- **Windows and Linux.** The evidence records possible routes on both, from source and binary inspection only. They are not part of this approval.
- **The production native mixer and output interface.** It is not designed and is not part of this approval.

What is known, believed and not known:

- Known from binary inspection, not from a run: the CEF framework calls each of the eight AudioToolbox functions through one slot in its `__DATA_CONST __got` table, on both Intel and Apple silicon, with no lookup by name. That is the precondition for replacing them in the CEF image only.
- Believed, on the basis that the render callback the mixer pulls is the same one that would feed the speaker in normal playback: the mixer gets each sound from its first sample. This is the point of the experiment, and it has not been run.
- Not known: whether the redirection delivers every sample at runtime. Whether the speech replacement keeps Chromium's start, word-range and finish events. Whether a page that picks a device can be tested at all.

## explicit-device-prerequisite

**The problem.** The experiment cannot test a page that sends sound to a particular speaker or headset unless it has a valid device ID for that page. The page gets an ID by listing devices, and in this single-process host that listing always also lists cameras, and camera listing exits the host. The crash is in Chromium's video capture code, which needs a run-loop thread that the single-process host does not give it. So no page can get an ID here, and the explicit-device case cannot run. Without it, the experiment cannot show that a stream sent to a chosen device also goes through the mixer. If such a stream bypasses the mixer, it plays straight to the device and is never recorded, and nothing in the experiment would show that.

A valid ID would avoid the crash, because when Chromium checks an ID it lists audio outputs only, not cameras. An ID is valid only for one page origin and one browser profile. DENICE's server takes a different port each run, so its origin changes and an old ID does not carry over. The test therefore needs a fixed test page on a fixed localhost port, one device listing of that page in DENICE's profile during a run where camera listing does not crash, and a check that the ID is still valid in the next run.

**The options.**

- **approve, "Approve the ID run".** One diagnostic multi-process session on a fixed test page gets the ID. The single-process experiment then uses it to test explicit-device streams. What it changes for you: one extra diagnostic run that uses a different process model than the host does. The run is only for the ID and is not a change to the host. Not known from the evidence: what it takes to make a multi-process run of this host on macOS. CEF on macOS normally runs its child processes from separate helper app bundles, and the bundle today has no helper executable. I believe this means the ID run needs extra bundle work that the evidence does not describe. Also not known: whether the ID survives into the next run, and whether the single-process run accepts it.
- **skip, "Skip explicit devices".** The experiment runs without that case and reports it as untested. What it changes for you: the result says nothing about pages that pick a speaker or headset. That gap stays open into any later decision about adopting interception.

The sender recommends approve.

## One sound walked through the design

The input is the test page's cold sound: one 0.4 s sound at 48 kHz, 19200 samples, played on the default device after silence. This is the case that loses its first 3460 samples today. Every component named below is proposed, except Chromium, the CEF framework and DENICE's recorder, which exist.

1. **Before the browser starts.** `AudioUnitRedirectionProof.Install` finds the loaded Chromium Embedded Framework image. It maps its `__got` slots to symbol names, saves the originals and writes the replacements, once, before `CefRuntime.Initialize`, while no CEF audio unit exists. `SpeechRedirectionProof.Install` replaces AVSpeechSynthesizer's speaking methods in the same window. `ProofMixer.Start` opens the mixer's own output through DENICE's own AudioToolbox calls, which do not go through the CEF image's replaced slots. It returns only after the mixer's first render callback, before the browser is created.
2. **The page plays the sound.** Chromium (existing) creates a HAL output unit. The call reaches `ReplaceAudioComponentInstanceNew`, which calls the original and starts tracking the unit as a `RedirectedStream` in state `Created`. Calls for units the proof did not create pass straight through.
3. **Chromium configures the unit.** Its `AudioUnitSetProperty` calls reach `ReplaceAudioUnitSetProperty`. The render callback and stream format are kept in the `RedirectedStream` instead of being set on the real unit, and the stream moves to `Configured`. Every other property, including the selected device, goes to the original, and the device is recorded in `Device`. **If it cannot proceed:** a format other than 48 kHz float stereo fails the proof run. The mixer does not resample.
4. **Chromium starts the unit.** `ReplaceAudioOutputUnitStart` registers the stream with the mixer and never starts the hardware unit, so Chromium's own output never reaches a device. `ProofMixer.Register` adds the stream to the list the render callback reads, and the stream moves to `Registered`.
5. **The mixer renders.** On every cycle of its own clock, `ProofMixer.Render` calls the stream's render callback with the mixer's timestamp and frame count, adds any queued speech, sums at unity gain, writes the sum to the device, and copies the same sum to the diagnostic dump and to DENICE's existing recorder (`Mp4Recorder`). **The pass:** ramp sample 0 is present in the dump, and all 19200 samples arrive in order. Today the first sample delivered is 3460.
6. **Chromium stops the unit.** `ReplaceAudioOutputUnitStop` calls `ProofMixer.Unregister`, which takes the stream out of the list and waits a bounded number of render cycles until no cycle can still be inside its callback. Only then does the stop return, which matches Core Audio's promise that no callback runs after a stop returns. The stream moves to `Unregistered`. **If it cannot proceed:** going past the bound is reported as a proof failure.
7. **Chromium disposes the unit.** `ReplaceAudioComponentInstanceDispose` unregisters the stream the same way, then disposes the never-started real unit through the original. The stream moves to `Disposed`.
8. **Shutdown.** `ProofMixer.Stop` stops the mixer's own output only after `CefRuntime.Shutdown` has returned with no tracked stream left.

Page speech takes a different path into the same mixer. When a page reads text aloud, `SpeechRedirectionProof.SpeakUtterance` renders the utterance with `writeUtterance:toBufferCallback:toMarkerCallback:` into the mixer's speech queue through `ProofMixer.QueueSpeech`, instead of playing it on the speaker. It calls Chromium's delegate itself: start when the first buffer is queued, word ranges from the markers, and finish after the last buffer is mixed. `StopSpeaking`, `PauseSpeaking` and `ContinueSpeaking` cancel, hold and resume the speech queue. Whether Chromium still behaves correctly with events that DENICE sends instead of the system is not known until the run.

## What the experiment must show

These are from the evidence, not the document:

| Case | Pass | Fail |
|---|---|---|
| Cold sound | ramp sample 0 present at the mixer dump, and 19200 samples in order | any missing leading sample |
| After 3 s silence, same and new context | sample 0 present | any loss, or a gap at a new stream |
| New AudioContext and media element while running | both from sample 0 | loss or doubled playback |
| Cross-site navigation | sample 0 in the new document | a unit that was not intercepted, or loss |
| Page speech | the utterance's audio in the mixer, Chromium gets start, word-range and finish events, and pause, resume and cancel work | speech straight to the speaker, missing events or cut-off audio |
| Explicit device | needs the ID from explicit-device-prerequisite, then the same intercepted unit with its chosen device | sound straight to the device |
| Shutdown right after the final sound | tracked units reach zero with the mixer running, exit 0, and the final sound complete | crash, hang or cut-off sound |
| All cases | every slot logged, real starts of CEF units equal zero, the MP4 matches the dump at lag 0, and each sound is heard once | any CEF audio reaching a device outside the mixer |

---
OPTIONAL DETAILS (do not need to read)

**What the five declarations are.** `AudioUnitRedirectionProof` is the class that installs and holds the replacements for the CEF image's AudioToolbox output calls. `RedirectedStream` is the record of one Chromium output stream the proof has taken over: the real unit, its render callback, its format, its device and its state. `StreamState` is the enum of that stream's lifetime: `Created`, `Configured`, `Registered`, `Unregistered`, `Disposed`. `ProofMixer` is the class that owns the mixer's own output, pulls every stream and the speech queue, and hands one sum to the speaker, the dump and the recorder. `SpeechRedirectionProof` is the class that replaces AVSpeechSynthesizer's speaking methods. All five are internal, in the `Denice.CrossPlatform.MacOS.Browser` namespace of the `Denice.CrossPlatform.MacOS` assembly, and none adds a public interface.

**What approval does not cover.** It does not cover adopting interception in the product, the Windows and Linux implementations, any new dependency, or the production native mixer and output interface. The evidence lists those as separate decisions, to be proposed after the proof.

**What the 74 ms offset might mean here.** In the proof, every stream is called with the mixer's own timestamp, so all streams share one clock. Whether that removes the offset is not known, because its cause is not known.

**Where the facts come from.** The measured numbers, the replacement list, the shutdown order, the acceptance table and the device-ID analysis are from the evidence excerpt of `docs/prototype-mvp-progress.md` on branch `prototype-mvp-macos-completion`, dated 2026-11-26. The camera-listing crash cause is from that file's symbolized crash section. The design is the checked document `document-0b78de3410fd424d849566aebdb47c67`, revision `77fe8a2b…a987`.
