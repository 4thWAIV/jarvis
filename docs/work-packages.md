# Work packages

A work package is runnable work one teammate sends another: a zip attached to
an ordinary message between people. The receiver's JARVIS unpacks it, runs it
with them by following the package's own instructions, and sends the results
back as a reply. Nobody's JARVIS needs updating to run a new package.

The team is a few trusted people on a closed network, so there is no sandbox.
Each step needs the receiver's spoken go-ahead in that turn, and the results go
back through the usual draft, review and approval.

## The format

A zip with `package.json` at its root:

```json
{
  "name": "spanish-speech-test",
  "version": "1.0.0",
  "purpose": "Record 15 Spanish commands and measure how well the Spanish model hears them.",
  "skill": "SKILL.md",
  "returns": ["results/summary.md", "results/bench.json", "results/clips/*.wav"]
}
```

| field | what it is |
|---|---|
| `name` | The package's name. Letters, digits, `.`, `_` and `-` are kept in its folder name. |
| `version` | Its version, also part of its folder name. |
| `purpose` | One sentence: what it does and why. |
| `skill` | Path inside the zip to a SKILL.md-style Markdown file: the instructions JARVIS follows with the person, step by step. |
| `returns` | File globs, relative to the package's folder, for the files that go back to the sender. `**` matches any depth. |

All five are required. Every entry in the zip must stay inside the package
folder: an absolute path, a `..` that climbs out or a symbolic link refuses
the whole zip, and nothing is written. File permissions in the zip are
kept, so scripts stay executable.

### Writing the skill

The skill is written to JARVIS. It names each step as a shell command run
from the package's folder. Every step gets these environment variables:

- `PACKAGE_DIR`: the package's folder.
- `JARVIS_PYTHON`: the Python JARVIS runs on, with its dependencies
  (sherpa-onnx, for example).
- `JARVIS_SOURCE`: the folder that holds JARVIS's `jarvis` package. Put it on
  `sys.path` to use JARVIS's own code, as the example's bench does with
  `jarvis.transcriber`.

A step that needs the person to see and answer prompts, such as a recording,
runs in a Terminal window. Write outputs where `returns` will find them,
usually under `results/`.

## On the receiver's JARVIS

The brain's tools:

- `open_package` (message id, attachment id): downloads the attachment if it
  is not here yet, unpacks it into `<data>/packages/<name>-<version>-<id>/`,
  takes macOS's quarantine mark off the folder so its scripts run, and
  returns `package.json` and the skill. Acting: only on the person's word.
- `list_packages`: the packages unpacked here.
- `run_package_command` (package, command, `terminal`): runs one step in
  the package's folder and returns its exit code and the last 4,000
  characters of its output. A step is stopped after 10 minutes. With
  `terminal`, it runs in a Terminal window (`actions.open_terminal`)
  instead, and its output stays there. Acting.
- `package_results` (package): the files that match `returns`, as full paths.
- `draft_message` `files`: full paths to attach to a draft. Each file is
  copied by the same snapshot step as a file chosen on the page
  (`FileSnapshots.add_to_draft`), so review, approval and upload are
  unchanged. A path can be in a package folder or anywhere else the person
  names. JARVIS's own data outside `packages/` is refused.

`read_people_thread` lists each file of a message with its attachment id. The
persona (`jarvis/jarvis_home/CLAUDE.md`, "Packages") tells the brain to offer
to open a .zip, follow the skill, and reply with the results.

## Sending one

Zip the package's folder with `package.json` at the root:

```bash
python3 examples/packages/zip_package.py examples/packages/spanish-speech-test
```

It writes `spanish-speech-test-1.0.0.zip`, leaving out `results/`, `model/`
and `.DS_Store`. Attach it to a message: pick it on the Queue page, or tell
JARVIS to send it to the person, giving its full path.

## The example: spanish-speech-test

`examples/packages/spanish-speech-test/` measures the Spanish Kroko streaming
model (`sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06`, SHA-256
`31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1`) on the
receiver's own voice:

1. `scripts/fetch_model.py` downloads the model into the package's folder,
   checks the SHA-256, and extracts it. Nothing is installed into JARVIS.
2. The person pauses JARVIS's listening with the page's microphone button.
   Otherwise JARVIS hears the Spanish too.
3. `scripts/record.sh`, in a Terminal window, shows each of the 15 sentences in
   `sentences.txt`. The person presses Enter, says the sentence, and five
   seconds are recorded with `scripts/record.js`. That script is JavaScript for
   Automation driving AVFoundation's `AVAudioRecorder`, writing 16 kHz mono
   16-bit WAV, so nothing needs installing. A silent clip, which is what macOS
   records when Terminal is not allowed the microphone, is offered again.
   `--say VOICE` makes the clips with macOS's `say` instead, to test
   everything except the microphone.
4. `scripts/bench.py` measures each clip with the person's own
   `jarvis.transcriber` engine. It writes `results/bench.json` and
   `results/summary.md`: word error rate as written and with accents ignored,
   real-time factor, slowest 100 ms, and clips split into more than one final.
