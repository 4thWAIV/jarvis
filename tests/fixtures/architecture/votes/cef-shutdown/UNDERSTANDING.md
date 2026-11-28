# Stop DENICE crashing at close after a page picks a speaker

## The answer and the ask

Today, when a web page in DENICE plays sound to a speaker or headset it picked itself, DENICE crashes when it closes. The process ends with exit code 139 and macOS writes a crash report. Pages that only use the default output close cleanly.

The proposal adds one Chromium switch, `disable-features=AudioServiceOutOfProcess`, to DENICE's normal macOS build. It goes in the method that already sets Chromium's switches (`OnBeforeCommandLineProcessing`). In the runs it stopped the crash: without the switch, every run that picked a device exited 139. With it, the same runs exited 0, in both the stock build and the experiment build. Single-process mode and every other switch stay as they are. No new file, setting or dependency.

You are asked two things:

1. **Approve the design**: add the switch to every macOS build. The approval question comes last, after the walk-through.
2. **The listening check**: will you run a 20-second check by hand, so that a person has heard the experiment's playback? Nobody has heard it yet.

The sender recommends approving the switch and running the check. I agree on both, for the reasons below.

## What the page shows

The page is one card, **Application**. It is DENICE's existing CEF startup class (a private class inside `CefPlatformBrowser`, in `Denice.CrossPlatform.MacOS`). It has one member, `OnBeforeCommandLineProcessing`. That method runs before CEF starts and lists the Chromium switches DENICE uses. The class exists today. The only proposed change is one more line in that method.

## One input, walked through

The input: a page creates an `AudioContext` whose output is a device it picked (in the test, the ZoomAudioDevice), plays a sound, and later you close DENICE.

1. **Application.OnBeforeCommandLineProcessing (existing, changed).** Before CEF starts, it adds `single-process`, `in-process-gpu`, `disable-gpu-sandbox` and `use-mock-keychain`. Under the proposal it also adds `disable-features=AudioServiceOutOfProcess`. It passes the finished switch list to CEF, and nothing else in DENICE changes.
2. **Chromium decides where its audio service runs (existing, inside Chromium).** Because of `single-process`, Chromium runs its audio service inside DENICE's own process. This is the same with or without the switch.
3. **The page picks its output device (existing, inside Chromium).** Chromium's device manager starts watching for audio devices being added or removed.
   - **Today:** that one check looks only at the `AudioServiceOutOfProcess` feature, which is on by default, and ignores single-process. So it creates a device watcher (`DeviceNotifier`) inside the audio service.
   - **With the switch:** the feature is off, so the device manager watches devices through the browser's own monitor (`SystemMonitor`) and creates no `DeviceNotifier`.
4. **The sound plays.** It plays the same either way. With the switch, the run played the chosen-device stream, switched output on a playing element in 34 ms, and handled speech, pause/resume and cancel as before.
5. **DENICE closes; CEF shuts down (existing).**
   - **Today:** Chromium deletes the browser's `SystemMonitor` first and the audio service after it. As the audio service is deleted, its `DeviceNotifier` tries to unregister from the `SystemMonitor`, which no longer exists. The process crashes (exit 139). The symbolized stack shows exactly this sequence: `SystemMonitor::RemoveDevicesChangedObserver` ← `DeviceNotifier::~DeviceNotifier` ← `audio::Service::~Service` ← `BrowserTaskExecutor::Shutdown` ← `CefShutdown`.
   - **With the switch:** there is no `DeviceNotifier`, so nothing touches the missing monitor. The process exits 0.

The cause comes from the sender's reading of the Chromium 156.0.8078.80 sources, with the file and line numbers in the document's notes. The crash stack and exit codes are measured. I did not re-read the Chromium sources myself.

## What the runs show

| Build | Switch | Exit |
|---|---|---|
| stock, three runs | no | 139, same crash stack |
| stock | yes | 0 |
| experiment (interception proof) | no | 139, same crash stack |
| experiment | yes | 0 |

All runs used the same script: navigation, default-output sounds, the chosen device, an output switch on a playing element, a media element, speech, pause/resume and cancel.

## What the check proves, and what it does not

The checker passed the document ("Checked, and nothing is wrong"). That proves the document is valid `arch`. It does not prove that the code matches the document, or that the fix works. The checker's own words: "Language conformance is separate from code correspondence and human approval." The document covers its one declaration fully, with nothing unresolved. These things are outside what the language can express, so nothing checks them:

- **The exact line of code** and where it goes: after the `use-mock-keychain` line. The page can only say in words that the method "also appends" the switch.
- **The method's access level.** In C# the method is `protected override`. The language has no word for that, so the page says `private`. That label is wrong for the C# code, but it doesn't change the design.
- **Why the crash happens** in Chromium. The page names no Chromium parts, and none of the Chromium reasoning is checked.
- **The test results**. The checker didn't check them.
- **Other `#if` blocks in the same method.** The method also holds switches that are compiled in only for experiments (the device-ID diagnostic, the GPU shutdown proof, the audio capture proof). The page doesn't show them. The added line sits outside them, so it applies to every build, including those.

### A gap you should know about: untested machines and paths

**The problem.** The switch was tested only on this Intel Mac (x86_64 CEF). It was not tested on Apple Silicon or with a device plugged in or unplugged during a session. Nobody looked for other places where Chromium uses this feature beyond the two the sender named. The second of those, the check that creates the audio manager, already runs in process because of single-process mode. Nothing on the page can check any of this. If the switch behaves differently on Apple Silicon, or device hot-plug relies on the watcher it removes, nothing would catch it before a user hit it. The change is limited to macOS: it is in the macOS project, so Windows and Linux are not touched. Whether those platforms have their own version of this crash is unknown.

**Your options.** Both are answered through the approval question:

- **Approve now.** The measured crash goes away on the tested Mac. Apple Silicon and hot-plug stay untested until someone runs them. In my judgment a failure there is unlikely, because the switch only stops one watcher from being created. But no run backs that up.
- **Request changes, asking for an Apple Silicon run and a hot-plug run first.** The fix waits for those runs. Until then, every session where a page picks a device keeps ending in a crash report at close.

## The listening check (a separate question)

Every playback result so far is a measured recording. No person has listened. The check is ready but has not run. You start `handoff/macos-completion/validation/listening-check.sh` by hand from the repository root. It opens the experiment build of DENICE for 20 seconds:

- after about 4 seconds, one quiet tone (0.5 s, 660 Hz, gain 0.15)
- 2 seconds later, the words "listening check"
- under 3 seconds of sound in total, from the Mac's default output (MacBook Pro Speakers)

Afterwards, say whether you heard the tone once and the words once, from which speaker, and whether anything was doubled or missing. The script briefly sets DENICE's `browser.debugPort` setting to 9223 and resets it at the end.

What I checked: the experiment build the script launches was built at 02:26 today without this switch. The page uses only the default output, though, and default-output pages close cleanly. So the crash is not expected to affect the check.

- **I will run it:** playback gets its first human confirmation, or an exact report of what was wrong.
- **Not now:** playback stays recorded as unheard. Nothing runs until you start it.

## A correction to earlier audio results

An earlier result said "zero leading samples lost". That is exact only for sounds played through an `AudioContext`. For `<audio>` and `<video>` elements, Chromium fades in the first 240 samples, so every sample is there but the first 240 are quieter. In the output-switch run, the analyzer missed 8 of 96,000 samples. All 8 are present: 7 sit on the start fade below the analyzer's cutoff, and 1 is the new stream's first sample, at fade gain 0. Switching output inserts 26.67 ms of silence, then a 240-sample fade-in.
