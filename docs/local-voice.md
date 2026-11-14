# Local speech output

JARVIS can use Fish Audio or local Kokoro v1.0 int8 through the existing
sherpa-onnx CPU runtime. Fish remains the default for existing installs. Choose
**Settings → Voice → Speech output**. Local mode needs no Fish key and never
falls back to paid synthesis; text conversation remains available on failure.

Install the local model explicitly using the button in Voice settings or:

```sh
.venv/bin/python -m jarvis.setup_speech_output
```

The 126 MiB archive is pinned to the release SHA-256, extracted with path/link
checks into a temporary directory and installed atomically under JARVIS data.
Nothing downloads on startup or per utterance. An existing incomplete directory
is preserved and reported. `--archive PATH` supports an already downloaded file.
After setup, local synthesis needs no network. Claude and JarvisNet retain their
separate network requirements.

Select George, Daniel, Lewis or Emma and use **Preview selected voice**. These are
preset British English voices, not clones of Fish's JARVIS voice. The worker uses
two CPU threads, stays warm between sentences, and runs outside the server event
loop. Interruptions cancel waiting synthesis and terminate the owned worker if a
native call is in progress; the next utterance reloads it. WAV playback duration
is included in the scheduler's early-ack protection. No extra HTTP service or
GPU runtime is used. The export's fixed dictionary would otherwise skip unknown
words. This adapter spells unknown English names/terms and reads unknown numeric
identifiers digit by digit instead; it leaves the displayed reply unchanged.
Unsupported scripts fall back to visible text, not paid synthesis. This can sound
less natural for proper names; listen before accepting it as your preferred voice. The same CPU path is intended for Intel and Apple Silicon;
this feature does not constitute a Windows port or an i9 benchmark.

Settings changes persist immediately; a provider/voice change stops queued speech.
Both are chosen in Settings, Voice and language (the voice provider and the
local voice; the brain's `set_jarvis_setting` changes the same). Diagnostics report selected local provider, model
availability and last synthesis timing. Invalid provider values disable synthesis
rather than selecting a billable provider.

The initial Pocket path was deferred: the available sherpa export README links
noncommercial export terms despite a differently labelled bundled license.
This increment uses the agreed Kokoro fallback instead of resolving that conflict
or adding PyTorch. Pocket's newer small-model runtime remains a future comparison.

[Kokoro weights](https://huggingface.co/hexgrad/Kokoro-82M) are Apache-2.0;
[sherpa model documentation](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html)
describes the export and voice IDs. Keep the downloaded license and linguistic
data notices when redistributing. Component terms do not change JARVIS's existing
whole-product license.


## Reproduce the CPU trial

```sh
.venv/bin/python -m jarvis.local_tts --benchmark --output /tmp/local-voice.wav
```

The model must already be installed. This reports cold/warm load, generation,
audio duration and real-time factor, and optionally writes a new WAV for listening.
It does not change the selected provider. Try it while ordinary development work
is active; compare listening/recognition diagnostics before and during synthesis.
This benchmark is not a microphone/acoustic-quality test or a promise for an i9.
