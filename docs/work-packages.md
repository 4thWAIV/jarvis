# Work packages

A work package is runnable work one teammate sends another: a zip attached to
an ordinary message between people. The receiver's JARVIS unpacks it, runs it,
and sends the results back as a reply. Nobody's JARVIS needs updating to run a
new package.

A package runs one of three ways, its `runs`:

- `conversation` (the default): JARVIS follows the package's skill with the
  person, step by step, running each step on their word.
- `agent`: one coding agent (a Claude Code run, through JARVIS's run
  pipeline) carries out the package's brief in its folder, unattended.
- `page`: the package's own HTML page opens in the centre of JARVIS's page,
  where the person uses it.

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
| `runs` | `conversation`, `agent` or `page`. Optional; without it the package is a `conversation` one, as every package before it was. |
| `skill` | Path inside the zip to a SKILL.md-style Markdown file: the instructions JARVIS follows with the person, step by step. Required when `runs` is `conversation`. |
| `brief` | Path inside the zip to a Markdown file: the task the agent carries out. Required when `runs` is `agent`. |
| `entry` | Path inside the zip to the HTML page that opens. Required when `runs` is `page`. |
| `returns` | File globs, relative to the package's folder, for the files that go back to the sender. `**` matches any depth. |

`name`, `version`, `purpose` and `returns` are always required, and so is
the file the package's `runs` needs; the other two may be there too, and
every file package.json names must be in the zip. Every entry in the zip must stay inside the package
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

### Writing the brief (agent)

The brief is the task, written to a coding agent that runs with nobody to
answer it: JARVIS gives it JARVIS's usual unattended-run preamble, the brief
word for word, and a line saying it is in the package's folder and which
files go back (`returns`). The agent runs in that folder, on the person's
Claude subscription, with the same permissions as any run JARVIS starts, and
is stopped like any other run (six hours by default). Say exactly which files
to write, and where.

### Writing the page (page)

The entry is an ordinary HTML page, with its scripts and styles as files
beside it in the zip, loaded with ordinary `<script src>` and `<link>` tags
(not ES modules, and not `fetch` of its own files: see below). It is served
from JARVIS's own address, `/api/packages/<id>/files/<path>`, only from
inside the package's folder, never through a link, never a hidden file, and
at most 32 MB a file.

It runs in a frame with `sandbox="allow-scripts"` and without
`allow-same-origin`, and its files are served with
`Content-Security-Policy: sandbox allow-scripts`, so it is sandboxed the same
way if opened on its own. That gives the page no origin of its own: it can
run its scripts, but it cannot reach JARVIS's routes, read JARVIS's page, or
use cookies or storage. Everything else it needs it asks JARVIS for with
`parent.postMessage`, and JARVIS answers with `postMessage` to the frame.
Every message both ways carries `jarvis: "package"`:

| from the page | JARVIS's answer |
|---|---|
| `{jarvis: "package", type: "save", id, name, data}` | `{jarvis: "package", type: "saved", id, name, ok: true, saved: "results/<name>", bytes}`, or `ok: false` with `error` |
| `{jarvis: "package", type: "listen", on: true}` | `{jarvis: "package", type: "listening", on: true, microphone, sampleRate: 16000, channels: 1, format: "pcm_s16le"}`, then one `{jarvis: "package", type: "audio", pcm, sampleRate, ...}` every 100 ms |
| `{jarvis: "package", type: "listen", on: false}` | `{jarvis: "package", type: "listening", on: false, ...}`, and the audio stops |

- `save` writes `data` (a string, written as UTF-8, an `ArrayBuffer`, a typed
  array or a `Blob`) as `results/<name>` in the package's folder, replacing a
  file of that name. `name` is at most four parts separated by `/`, each
  letters, digits, `.`, `_` and `-`, starting with a letter or a digit; a
  result is at most 20 MB. `id` is anything the page likes, given back with
  the answer.
- `listen` hears JARVIS's own microphone. A frame without an origin cannot
  have the microphone itself: Chrome refuses `getUserMedia` there ("Invalid
  security origin") even with `allow="microphone"` on the frame. So the page
  gets the frames JARVIS's voice input already takes: 16 kHz mono 16-bit
  little-endian PCM, 100 ms each, in `pcm` (an `ArrayBuffer`). They arrive
  while JARVIS is muted, which is how the person keeps JARVIS from hearing
  it too; with JARVIS's microphone off there are none. `microphone` is
  JARVIS's voice input state (`listening`, `muted`, `off`, ...).

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
- `run_package_agent` (package): an agent package's run, started in its
  folder through the run pipeline (`RunExecutor`), recorded with the
  package's id (`runs.package_id`). One at a time per package. When it ends,
  JARVIS says so and, if it succeeded, asks whether to draft the reply; the
  brain is told to call `package_results` and `draft_message`. A failed run
  can be run again with `retry_run`, still as that package's. Acting.
- `open_package_page` (package): opens a page package in the centre of every
  JARVIS page that is open. The page's own way is Open page on the package's
  file in the Queue, once JARVIS has opened it; both go through the same
  `_open_package_page`, and `POST /api/packages/<id>/page`. Acting.
- `package_results` (package): the files that match `returns`, as full paths.
- `draft_message` `files`, or `attach_message_files` on a saved draft: full
  paths to attach. Both go through `draft_files.py`, all or none at the
  draft's exact revision, and copy each file by the same snapshot step as a
  file chosen on the page (`FileSnapshots.add_to_draft`), so review,
  approval and upload are unchanged. A path can be in a package folder or
  anywhere else the person names that the folder catalog's ordinary-file
  rule reads; keys, credentials, unknown dotfiles, the provider homes and
  the rest of JARVIS's own data are refused.

`read_people_thread` lists each file of a message with its attachment id. The
persona (`jarvis/jarvis_home/CLAUDE.md`, "Packages") tells the brain to offer
to open a .zip, run it the way it runs, and reply with the results.

## Sending one

Zip the package's folder with `package.json` at the root:

```bash
python3 examples/packages/zip_package.py examples/packages/spanish-speech-test
```

It writes `spanish-speech-test-1.0.0.zip`, leaving out `results/`, `model/`
and `.DS_Store`, after checking that the file the package's `runs` needs is
there. Attach it to a message: pick it on the Queue page, or tell
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

## The example agent package: text-stats-agent

`examples/packages/text-stats-agent/` (`"runs": "agent"`) has three short
texts and a brief: write a small standard-library Python script that counts
each text's words and sentences and finds its longest word, run it, and write
`results/counts.json` and `results/summary.md`. Those two go back.

## The example page package: mic-check-page

`examples/packages/mic-check-page/` (`"runs": "page"`) is one page: Record
takes three seconds of JARVIS's microphone through `listen`, shows the peak
and average level in dBFS, and Save results saves `results/clip.wav` (16 kHz
mono WAV), `results/level.json` and, if the person wrote one, `results/note.txt`
through `save`.
