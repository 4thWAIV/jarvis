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

A zip with `package.json` at its root, or inside one folder that holds everything else (the way Finder's Compress zips a folder). macOS's `__MACOSX` folder and `.DS_Store` files are set aside when looking for that folder, and never unpacked. Every path `package.json` names is relative to the folder it is in, and the package's folder on the receiver holds what that folder holds:

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
| `story` | Path inside the zip to a story file: what JARVIS says over the page, and what it points at as it says it (see "Telling a page's story"). Only for `runs` `page`; optional. |
| `returns` | File globs, relative to the package's folder, for the files that go back to the sender. `**` matches any depth. |

`name`, `version`, `purpose` and `returns` are always required, and so is
the file the package's `runs` needs; the other two may be there too, and
every file package.json names must be in the zip, beside package.json.

A `package.json` that has none of `purpose`, `returns`, `runs`, `skill`, `brief` or `entry` is some other project's (an npm one, say), not a work package's. A zip whose `package.json` is in a folder that is not its only one, or with several folders holding one and none at the root, is not a package either; each of these is refused with the reason, and the zip is still an ordinary file.

The zip is checked by `jarvis/bundle_archive.py` before anything is written. An absolute path, a drive or network path, a backslash, a `..` that climbs out, a link, a device, an encrypted entry, two entries that would be the same file on a Mac's disk (by case or Unicode form), or a file where another entry needs a folder refuses the whole zip. So do more than 10,000 entries, a file over 256 MB, more than 1 GB in all, or a file expanding more than 100 times its compressed size past its first megabyte, counted as it is actually decompressed. It is unpacked into a private staging folder and renamed into place only once complete, so a failure leaves nothing. File permissions in the zip are kept (never set-user-ID), so scripts stay executable.

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

### Telling a page's story (page, optional)

A page package can carry a story: the words JARVIS says over the page, slide
by slide, and the parts of the page it points at and rings while each part
is said. Name the file in package.json's `story`. JARVIS checks it when the
package is unpacked, like the rest of package.json, and refuses the package
with the reason if it is not valid.

```json
{
  "title": "A pot of tea",
  "language": "en",
  "slides": [
    {
      "title": "Four steps",
      "target": "#s2",
      "beats": [
        {"say": "Boil the water.", "targets": ["#s2 [data-step='1']"]},
        {"say": "Green tea wants water well off the boil.",
         "actions": [{"click": ".tea[data-detail='green']"}],
         "targets": ["#detail-green .temp"]}
      ]
    }
  ]
}
```

| field | what it is |
|---|---|
| `title` | The story's name. |
| `language` | `en` (the default) or `es`. JARVIS tells it only while it is speaking that language. |
| `slides` | The slides, in order, at most 60. Each has a `title` (shown in the slide list, and to the brain so he can ask for a slide by name), an optional `target` (the slide's own element: every beat of the slide first scrolls it into view) and its `beats`, in order, at most 40. |
| `say` | A beat's words, at most 800 characters. JARVIS says them in its own voice, as written: they are the sender's narration, never JARVIS's own words or instructions to it. |
| `targets` | CSS selectors inside the page, at most 12. Each is ringed while the beat is heard, around every visible element it matches; the pointer points at the first. |
| `actions` | Done before the targets are found, in order, at most 6: `{"show": selector}` scrolls the element into view, `{"click": selector}` clicks it (a detail view's opener, or its close button). |

The page loads JARVIS's helper, which draws the pointer and the rings and
runs the actions inside the page's own frame:

```html
<script src="/api/packages/story.js"></script>
```

JARVIS keeps one copy of it (`jarvis/package_story.js`) and serves it at
that address; a package never carries its own copy. A page opened outside
JARVIS simply has no helper.

The story is spoken through JARVIS's usual voice, one beat after another.
Each beat's words carry the beat with them to the page, and the page rings
the beat's targets the moment those words start playing, then clears them
as the last of them ends, so the pointing follows the voice rather than a
timer. The person speaking, the Stop button beside it, Escape, or JARVIS's
own Stop end both the voice and the rings.

Besides `save` and `listen`, the bridge carries the story:

| from JARVIS | when |
|---|---|
| `{jarvis: "package", type: "story.cue", beat, targets, actions}` | a beat's words start playing (`beat` is `"<slide>.<beat>"`, both from 1) |
| `{jarvis: "package", type: "story.clear"}` | its words have ended, or the voice stopped |

| from the page | what JARVIS does |
|---|---|
| `{jarvis: "package", type: "story.ready"}` | posts the beat playing now again (the helper says this once loaded) |
| `{jarvis: "package", type: "story.missing", beat, targets}` | shows, under the frame, the selectors nothing visible matched |

## On the receiver's JARVIS

The brain's tools:

- `open_package` (message id, attachment id): downloads the attachment if it
  is not here yet, unpacks it into `<data>/packages/<name>-<version>-<id>/`,
  takes macOS's quarantine mark off the folder so its scripts run, and
  returns `package.json` and the skill. Acting: only on the person's word.
  The page's own way is Open package on the zip in the Queue: the same
  `_open_received_package`, through `POST /api/packages/received`; a page
  package then opens in the centre at once.
  Opening the same bytes again, from that attachment or a resent one, gives
  the package already unpacked, results and all: which archive each package
  came from (its SHA-256 and attachment ids) is kept in
  `<data>/packages/.records/`. A package unpacked before that record existed
  is recognised by its folder name and adopted.
- `open_local_package` (path): opens a package zip that is a file on this
  Mac, by its full path. The path is read by the folder catalog's
  ordinary-file rule (no link followed; keys, credentials and JARVIS's own
  data refused; at most a received file's size), the zip is unpacked exactly
  as a received one, and a page package is shown in the centre at once.
  The page's own way is Open package on a .zip's preview in the Folders
  view; both go through `_open_local_package`, and `POST
  /api/packages/local`. Acting.
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
  file in the Queue, once it is unpacked; both go through the same
  `_open_package_page`, and `POST /api/packages/<id>/page`. Acting.
  JARVIS keeps the page package shown and shows it again on a page that
  connects later, a reload included, until its × is pressed.
- `tell_package_story` (package, `action` tell or stop, `slide`): opens a
  page package's page and tells its story in JARVIS's voice from that slide,
  or stops it; telling another slide jumps there. The page's own way is Tell
  the story, the slide list and Stop above the frame; both go through the
  same `_package_story` (`jarvis/story_teller.py`), and `POST
  /api/packages/<id>/story`. The slides' titles come back to the brain in an
  untrusted block; the story's words never reach it. Acting.
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

Zip the package's folder with `package.json` at the root (a zip of the folder itself, with `package.json` one folder down, opens too):

```bash
python3 examples/packages/zip_package.py examples/packages/spanish-speech-test
```

It writes `spanish-speech-test-1.0.0.zip`, leaving out `results/`, `model/`,
`new_deck.py` and `.DS_Store`, after checking that the file the package's `runs` needs,
and its story if it names one, is there. Attach it to a message: pick it on the Queue page, or tell
JARVIS to send it to the person, giving its full path.

## Decision packages

A decision package asks the receiver a set of decisions and records the
answers. Every one is built from one shared deck,
`examples/packages/decision-deck/`, so they all look and work the same,
and every one reaches the owner through one command,
`python -m jarvis.decision_package` (below). Write the data and run the
command; there is no other way to build or send one.

Any choice put to the owner must be a decision slide in a package sent
this way. A presentation that describes choices but has no decision slides
gives him nothing to vote with, so it is never sent; the talk-to-jarvis
skill tells every session the same.

### The data

One JSON file holds the package fields, the slides and the decisions
(`examples/packages/decision-deck/deck-data.js` is a working example):

| field | what it is |
|---|---|
| `name`, `version`, `purpose` | The package.json fields of the same names. |
| `title` | The deck's title, and the heading of `results/decisions.md`. |
| `language` | The story's language, `en` (the default) or `es`. |
| `slides` | The slides before the decisions, in order. Each has a `title`, and optionally an `id` (for story targets), a `kicker`, a `short` name for the jump strip, `say` (its story line) or its own `beats` (a story slide's beats; the builder adds the click that brings the slide up), and `blocks`. An entry `{"decision": "<id>"}` places that decision's slide there; decisions not placed follow the slides. The "Your answers" slide is always last. |
| `blocks` | Each one of `{"p": text}`, `{"lead": text}`, `{"note": text}`, `{"points": [text or {"b", "text", "id"}]}`, `{"facts": [{"n", "text"}]}`, `{"table": {"head", "rows", "rowIds", "rowHeads"}}`, or `{"picture": path, "alt", "caption"}` / `{"pictures": [{"src", "alt", "caption", "id"}]}`. Any block may have an `id`. Text may use `**bold**` and `` `code` ``. |
| `decisions` | The decisions, in order. Each has an `id` (a slug: a-z, 0-9 and `-`), a `short` name, the `question`, its `options`, the `recommended` option's id and the one `reason` for it, and optionally `context` (a line of words), a `picture` (shown beside the options), a `compare` picture (shown large above the options, or beside them), and `say` (its story line in place of the recommendation). |
| `options` | Each has an `id` (a slug; not `later`, `none` or `recommended`), a `name`, what it `says`, optionally a `picture`, and optionally `opens`: the ids of child questions asked only when this option is chosen. A child has one parent. |

Pictures are paths inside the package, found beside the data file.

No slide is a wall of text: the builder refuses a slide with more than 60
words of body text (split it in two), and a decision whose question is
over 30 words or whose reason, context or any option's words are over 40.

### The short form

An agent need only supply the substance: the `title`, and for each decision
its `question`, `options` (each a `name` and what it `says`), the
`recommended` option and the `reason`. The command works out the rest
(`decision_package.expand`), and a field given is never replaced, so a
full-form deck passes through unchanged:

- `name` from the title, `version` `1.0.0`, `purpose` from the title;
- a decision's `id` from its `short` or question, its `short` from the
  question; an option's `id` from its name;
- the recommendation: write the option's id or name in `recommended`, or put
  `"recommended": true` on exactly one option;
- `opens` and a slide's `decision` may name a decision by its id, `short` or
  question.

A field the deck does not know (`recomended`, `sais`) is refused by name with
the field it was probably meant to be, since the deck would silently ignore
it. `python -m jarvis.decision_package --example` prints a minimal content
file, and `--check-only` validates a file and stops, with nothing built,
checked in a browser or delivered.

### Sending one

From the JARVIS checkout, with its own Python:

```bash
.venv/bin/python -m jarvis.decision_package /full/path/content.json \
    [--project <project>] [--session <session id>] [--out <folder>]
```

It does every step, and stops at the first that fails:

1. **Checks the content**, and lists everything wrong with it at once:
   every decision has a slug id, a question, at least two options with
   slug ids (not `later`, `none` or `recommended`), names and words, a
   recommendation that is one of them and a one-line reason; every child
   question it opens is another decision; every picture is a PNG, JPEG,
   GIF, WebP or SVG file beside the content; and no piece of text is
   longer than the deck can show (`decision_package.CAPS`: a question 120
   characters, a reason 160, an option's words 140, one line of a content
   slide 280 and a whole content slide 700).
2. **Builds** it with the shared deck's builder,
   `examples/packages/decision-deck/new_deck.py`, called as it is, into the
   folder (`--out`, else a new temporary one) as `<name>/` and
   `<name>-<version>.zip`.
3. **Checks it end to end in a real, isolated JARVIS**
   (`jarvis/decision_check.py`): JARVIS's own server
   (`jarvis/decision_check_server.py`) in a child process on a free port
   above 19000, with its own temporary data folder and `.env` copy (its
   Fish key replaced; the voice is a silent stand-in), the brain never
   spawned, and the voice page built with vite into a temporary folder that
   it serves. There it unpacks the zip through the same route delivery
   uses, finds it with `list_packages` by its title's words and opens it
   with `open_package_page` by its title, both through that JARVIS's
   `/internal/tool`. Then, in a headless Chromium on the real sandboxed
   package frame, sized to 809 × 589 and to 662 × 489:
   - every slide fits: nothing outside its slide, nothing cut off or
     scrolled, every word from 13 to 25 px;
   - every decision slide has its vote row and Choose buttons in view;
   - `tell_package_story` tells the story through JARVIS's speech path, and
     it brings up every decision slide and points at every beat's targets;
   - one Choose click saves its answer to `results/decisions.json`;
   - one answer by voice (`run_package_command` running
     `scripts/decide.py`) appears on its slide.

   Every slide is photographed at both sizes, in `<folder>/check/`.
4. **Delivers only if every check passed**: it unpacks the zip into the
   live JARVIS, without showing it, through `POST
   https://127.0.0.1:8340/api/packages/local` with the tool token, and
   prints the package id and the words to open it ("Open the <title>
   package."), which go in the session's message to the owner. On any
   failure nothing is delivered; it prints what failed and the screenshots.
   `<folder>/delivery.json` keeps the id, `--project`, `--session` and the
   path of the answers, `results/decisions.json` in the package's folder.

The checked page is never left running: the isolated JARVIS and the browser
are stopped when the check ends. To change a deck, change the content and
run the command again.

### What the receiver gets

The deck draws on a fixed 880 × 640 canvas scaled to fit the frame, so a
decision slide shows its question, the recommendation and its reason, each
option with its Choose button, Decide later, None of these, a words box and
the recorded answer without scrolling, at both centre frame sizes (809 ×
589 in a 1440 × 900 window, 662 × 489 in a 1280 × 800 one). On a decision
slide with pictures, the pictures take all the room the words and the vote
row leave: the deck tries each option's picture above its words, beside
them and, for two options, one option a row, and keeps whichever shows the
smallest picture largest with nothing cut off. The canvas is never scaled
past body text of about 22 px, however large the frame. Previous and Next, the arrow keys, a
jump strip that marks answered decisions, and "n of N" move between
slides; the current slide is kept in memory only. The last slide, "Your
answers", lists every decision with its recorded answer, changed in place,
and Approve all recommended, which asks once and then records the
recommended option for every decision with no answer yet. Nothing is ever
pre-selected.

The answers go to `results/decisions.json`, `results/decisions.md` and
`results/decisions.js` (the same record as a script, which is how the frame
reads its own file). Each record is
`{"<decision>": {"answer": "<option id>" | "later" | "none", "at", "choice", "note", "recommended"}}`.

### Answers by voice

The package carries `SKILL.md` for JARVIS, and JARVIS's own instructions
say the same: when the person answers out loud, JARVIS runs, with
`run_package_command` on the package,

```bash
"$JARVIS_PYTHON" scripts/decide.py <decision> <option> [his words]
"$JARVIS_PYTHON" scripts/decide.py all-recommended
"$JARVIS_PYTHON" scripts/decide.py show [<decision>]
```

`<option>` is an option's id, its letter, `recommended`, `later` or `none`.
`decide.py` writes the same three files, byte for byte as the page writes
them, and the page reads `results/decisions.js` again every three seconds,
so a spoken answer appears on screen.

A page package's skill reaches the brain only when the brain opens the
package itself (`open_package` or `open_local_package` return it). A deck
opened from the page, by the Queue's or the Folders view's Open package,
reaches the brain only through its instructions above, which do not depend
on the skill.

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

## The example story package: story-deck

`examples/packages/story-deck/` (`"runs": "page"`, `"story": "story.json"`)
is a three-slide deck on making a pot of tea. Its story rings each of the
three ingredients, each of the four numbered steps, and on the last slide
opens the green tea's detail view, points at its temperature and time, and
closes it again. It sends nothing back (`returns` is empty).
