# Making a package

This page is the only description of how to make a package and send it to Tim. Every kind goes through one command, and nothing else: never build, zip, attach or deliver a package by hand.

```bash
python -m jarvis.make_package --handoff handoff.json --engine <claude|codex> --session <your session id> [--workflow <id>] [--work <id> | --general]
```

It signs the package with your session's own key (`--session`; `python -m jarvis.agent_key init` makes it and `submit` sends it, and Tim accepts it by switching your agent on), so JARVIS knows it is yours: nothing is delivered without a key, and a package signed by a key Tim has not accepted is held until he does. A subagent signs with its parent's key; it is named as the sender, beside its parent, only when its own transcript shows it running the command. Give `--work <id>` with an agent package and it carries a key limited to that one Work item (`.jarvis/work-key`), so whoever works in the unpacked package reports on that item with `work_report.py`; it ends when the item closes or after 30 days.

Run it with JARVIS's own Python. The talk-to-jarvis skill gives the exact command for this Mac, which works from any folder. From inside the JARVIS checkout it is `.venv/bin/python -m jarvis.make_package`, and then give `--project <your project>` as well, since the project otherwise defaults to the name of the folder you run it from.

## A hand-made package: the fresh author on the caller's engine

For anything Tim reads or decides, send a request instead (see [Asking JARVIS for a package](#asking-jarvis-for-a-package-start-follow-answer)): JARVIS makes the briefing and checks it. A hand-made package is for a working tool of your own, an agent's brief or a conversation's skill. Nothing refuses a hand-made page, but nothing checks it as a briefing either: it shows "Layout not checked".

Creation launches a fresh author through the existing provider session on the caller's own engine. A Codex caller uses `--engine codex`; a Claude caller uses `--engine claude`. Only that executable and its existing subscription login are needed. The other engine is never a fallback. The author starts a new conversation with the shipped `human-package-author` skill, the handoff and its evidence, without inheriting the originating conversation. The author's tools can read evidence and write the package source.

The originating caller supplies one handoff JSON file, in the one form a request takes (see [Asking JARVIS for a package](#asking-jarvis-for-a-package-start-follow-answer)): `sender_answers`, `questions` and typed `evidence`, read and checked by the same code JARVIS checks a request with (`jarvis/package_request.py`), so a handoff refused there is refused here, with the same reasons. A relative `file` path is taken from the handoff file's own folder. Existing source can accompany the handoff as the positional argument; the fresh author treats it as evidence.

**A deck for Tim is a briefing, and only JARVIS's package workflow makes one** (see [Asking JARVIS for a package](#asking-jarvis-for-a-package-start-follow-answer)): ask for it with `--start`. The fresh author here writes a folder (`package/` with a working tool's `index.html`, an agent's `BRIEF.md` or a conversation's `SKILL.md`), or an architecture document's explanation (see [Architecture documents](#architecture-documents)), in its first turn; a deck (`presentation.yaml`) it writes is refused, and nothing is delivered. A page it writes is built and delivered as any hand-made page is.

The command waits for the author, accepts its `package/` output, then builds it and delivers it to the inbox. The author runs `--check-only` itself last, after its final change. `--session`, `--work` and `--workflow` remain the originating caller's associations. Author failure, timeout, missing source or invalid output prevents delivery. The author receipt and evidence remain under the output folder's `author/` directory. The author does not deliver or recursively launch another author.

No browser runs anywhere in a package's path. A deck is laid out by JARVIS and proven as it compiles (see [What JARVIS proves](#what-jarvis-proves)), the same proof `--check-only` runs; there is no screenshot review. The built package carries the proof as `.jarvis/layout-proof.json`, and Tim's JARVIS makes the proof again from the package's own bytes as it arrives, refusing a deck that does not prove. JARVIS's own path into his inbox (`POST /api/packages/inbox`, on the tool token) refuses a deck from anyone: a deck reaches him only as the package workflow delivers it. A hand-made page (a working tool) gets the folder checks only; JARVIS labels it "Layout not checked". When the build is refused after the author finished, the command sends a signed failure message on the `--work` item without the package and returns failure.

**A status briefing.** For a status presentation on Work items, build the handoff from their records rather than writing it yourself: `python -m jarvis.status_briefing --work <id> [--work <id> ...] --out <folder> [--since <24h | 7d | ISO time | epoch seconds>]` writes `<folder>/handoff.json` and `<folder>/status-evidence.json`. Per item it gives what needs Tim first (his open decisions, packages still asking him, his review, every held piece and what holds it, stalled pieces, each with who and since when; or one line saying nothing needs him), then pieces reported working with their latest note, then what finished since `--since` (24 hours by default), every fact carrying the evidence its piece has on record. The handoff is in the one form (below): one question, `approve-status`, with the options you see, Approve ("Approve: this status is complete and accurate") and Reject ("Reject: say what is missing or wrong"); the evidence file first, then each Work item, then every piece of evidence the facts carry (a path a `file` entry, a URL a `url`, a package a `package`, and a commit, which names no repository, a `label`). No program answers the two questions every sender answers: add your own `sender_answers` to it, then ask for the package with `python -m jarvis.make_package --start --handoff <folder>/handoff.json --session <your session id> --work <id>`. Run `python -m jarvis.status_briefing --work <id> --check` first: it writes nothing and names each piece with no note, or past planned or ready with no evidence, so you can report what is missing before the briefing is built on a stale record.

For source validation while authoring, use `python -m jarvis.make_package <presentation.yaml | folder> --check-only`; `python -m jarvis.make_package presentation.yaml --fill-assets` writes each picture's SHA-256, kind and size into the source (it rewrites the YAML, so comments are not kept). This never launches a model or delivers. Creation without a handoff is refused. `--example` also remains model-free. Install the shared skills into existing Claude and Codex homes with `python -m jarvis.session_skill install`.

## Asking JARVIS for a package: start, follow, answer

You ask; JARVIS makes the package. You write nothing of it, and it is not made in your process (the command without `--start`, the fresh author in your own process described above, is the old way this replaces):

```bash
python -m jarvis.make_package --start --handoff handoff.json --session <your session id> [--work <id> | --general] [--workflow <id>] [--project <name>] [--document <id> --revision <rev>]...
python -m jarvis.make_package --status <job id> --session <your session id>
python -m jarvis.make_package --answer <job id> --reply <question id>=<your answer> [--reply ...] --session <your session id>
```

The agent MCP server's `package_start`, `package_status` and `package_answer` tools make the same calls (`jarvis/package_request.py`). The handoff is sent to JARVIS signed with your session's own key (`POST /api/packages/requests`, the `packages` right) and checked at once: a request with anything wrong is refused, listing every problem, and nothing is recorded. Taken, JARVIS records who asked from the signature (`agent:<your session id>`), gives the package an id and its first run an id, keeps the job with its run (`/work/<work id>/packages/<package id>/runs/<run id>/run.job`, or `/packages/<package id>/runs/<run id>/run.job` with no Work item; the run's files on disk under `data/work/<work id>/packages/...` or `data/package/<package id>/...`), and runs it itself, in a process of its own. `--start` prints `{"job", "package", "run", "state"}` at once.

The handoff is one JSON object of these fields and no others:

```json
{"sender_answers": {
   "plainly": "Your answer to the first question below.",
   "why_now": "Your answer to the second question below."},
 "questions": [
   {"id": "merge-171", "text": "Merge PR 171 now?",
    "options": [{"id": "merge", "name": "Merge", "says": "Merge it today"},
                {"id": "wait", "name": "Wait", "says": "Hold it for the second fix"}]},
   {"id": "move-live", "text": "Move the live data tonight?"}],
 "evidence": [
   {"type": "file", "path": "situation.md"},
   {"type": "pr", "repo": "owner/name", "number": 171},
   {"type": "ci_run", "repo": "owner/name", "id": "19476631"},
   {"type": "content", "text": "Anything you know, as you would say it."},
   {"type": "label", "text": "the CI run that failed yesterday"}],
 "purpose": "What you need from Tim, why, and what you do after his answer.",
 "recommendation": "What you recommend."}
```

`sender_answers` is required: your answer to each of the two questions every sender answers before anything runs, word for word as they are asked:

- `plainly`: "State plainly and exactly what you are telling us. Name each thing you mention as what it specifically is, and keep separate things separate: do not merge two problems, results or decisions into one. Say what you know, what you only believe, and what you do not know. Leave out wording meant to sound technical, confident or complete."
- `why_now`: "Why does Tim need this now? What happened, what happens if he does nothing, and what changes for him with each answer."

They are kept labelled as yours, checked against the evidence, and are where the briefing starts; they are never copied to Tim's screen. `questions` is one or several, each with an `id` and `text`: questions and decisions are one list, and a question's `options` are the options you see, kept labelled as yours (the final options are written for Tim). `evidence` is a nonempty list of typed entries, each `{"type", ...}` with its type's fields: `file` (`path`; a relative one is taken from the handoff file's folder; checked to be there and its SHA-256 recorded as the request is taken), `url` (`url`), `content` (`text`, kept as given), `work_item`, `package`, `message`, `workflow` and `run` (`id`), `pr` and `issue` (`repo`, `number`), `ci_run` (`repo`, `id`), `commit` (`repo`, `sha`), `branch` (`repo`, `name`), and `label` (`text`: anything else, free text, which JARVIS works out). Give the situation as one of them, a file or content entry. List only what you know and references to what you know: JARVIS gathers the Work item, its PRs and CI runs, your session (the commands you ran with their results, and your final messages) and Tim's record itself. JARVIS resolves every entry once per package, into the package's evidence (`/work/<id>/packages/<package id>/evidence/`, or `/packages/<package id>/evidence/` with no Work item): a file copied in, content kept as given, every other type fetched by a program (a GitHub address read as the pull request, CI run, commit, issue or branch it names; a commit with `git show` in a checkout of its repository on this Mac), and a label worked out by a retrieval model into entries of those types. A reference that names nothing is recorded as not found, with why; nothing is guessed. JARVIS then reads what it gathered, once per package: each file is given a type (test results, build log, CI run, diff or commit, session transcript, data or prose; any other file has no reader), the structured ones are read by a program for their facts, the prose parts of logs and session transcripts have that type's facts copied out, and the prose (your reason and recommendation, the situation, documents, notes, pull request descriptions) is marked part by part against the labels Tim's briefings are judged by, in batches small enough to be answered whole, each given the whole file; every fact and part is scored against each of your questions and kept with its file and position (`mark/` in the evidence). Then an author model researches your questions from what is there (the Work item's own research, the marked evidence, and an earlier run's research), searching only for what each question still lacks, every search logged (`research-log.jsonl` in the run's folder), and writes the briefing's prep and its script in one conversation with an editor, four judges (does Tim care about each part, is the decision properly prepared, is the background right, is the most relevant information first) and a Theory of Mind reader that reads it as Tim would, each given only its share of what Tim knows, the whole conversation kept word for word (`conversation.md`). The script is structured YAML, `script.yaml` (`script/1`, `jarvis/package_script.py`), the author's answer in that schema, checked by JARVIS: the slides in order, each with an id, a title, what is on screen (words, Tim's own words with their source, a picture by path, a table), what JARVIS says over it, the source path and position of every fact, the questions it answers and the decision it asks if any; it is marked, judged and edited slide by slide. Each round they vote on every open question, yes or no with a reason; a majority closes it, a tie fails it. A closed question is voted on again only when, after the script changes, a majority of its voters judge that the change touched what it was about. A search with `knowledge_search`'s `under` keeps to that path. `purpose` and `recommendation` are optional, your own reason and your recommendation, kept labelled as yours. `work` (one Work item id or a list of them) names the Work items the package is about: JARVIS gathers them as material, and they never decide where the package goes. The package goes on the Work item `--work` names; with `--general`, or no `--work`, it goes to Tim's Inbox with no Work item. A request naming a Work item (`--work`, or one the handoff's `work` is about) or a `--workflow` that does not exist is refused at once, every one named; a closed Work item is not refused. For an architecture document, `approval` is as before. `conclusion` and `audience` are refused (your view is your labelled recommendation; the audience is always Tim), as is any structure for the briefing (`shape`, `structure`, `slides`, `outline`, `order`); `question`, `decisions` and `situation` say where what they held goes now. There is no `--says`: the message that carries the package to Tim is written by JARVIS.

`--status` prints the job's record, to you alone: its `state` (`queued`, `running` with its `step`, `waiting_on_sender`, `delivered`, `failed` or `error`), the questions it asked you and your answers by round, and the delivery (`result`). When the swarm votes that a question cannot be answered from what there is, it asks you only what its votes said research could not answer, and waits on you (`waiting_on_sender`); answer by question id and the run goes on. At most five rounds of questions are asked, and asking stops once a round of your answers changes nothing still open. The round that asked you counts as one of the swarm's three. If the briefing has not passed after three rounds, nothing is built: JARVIS sends you a message with the swarm's findings, each to answer by its id (`--status` shows them, a round of kind `findings`), and puts them on the Work item. Answer them within 2 hours and the run ends `failed` and a new run of the package starts with your answers, from the research already done.

**The deck, your check and delivery.** Once the script passes, the author writes the deck from it alone, in the same conversation: one `presentation.yaml` (below) in JARVIS's named layouts, one deck slide for each script slide, in order and with the same id (a script slide that asks a decision is the deck's decision slide), Tim's own words, where the script quotes him, as runs of emphasis `his-words`, which JARVIS draws highlighted and narrates. JARVIS builds and proves it as every deck is built, and checks it against the script: its slides the script's, and every quote of Tim's on its slide in his style and said in its narration (a program's check, not a vote); what does not build or check goes back to the author. The editor and the four judges then compare each slide with the passed script slide by slide from the layout data JARVIS computed as it proved the deck (each slide's words and pictures with their positions and sizes at the smallest and the largest frame, and its narration), with no browser, and vote as before: does each slide say what the script says for it, is it visual where the script calls for it, does the narration say the substance, is every word readable at its size. A deck not proven in three rounds is sent to you as findings, as a script is. The finished package carries the research, the prep, the passed script, the whole conversation and every vote under `.jarvis/briefing/`; JARVIS writes the message that carries it to Tim from slide 1 of the passed script and your handoff's questions, signs it with its own key, and sends it back to you before Tim sees it, in a message to your session (`--status` shows it, a round of kind `check`): "This is the package JARVIS made from your request, before Tim sees it. Open it and judge broadly whether it does what you asked for and what Tim needs from it. Approve it, or say what is wrong, and a new run starts with your words." Answer within 2 hours, structured (no free text is read as an answer): `package_answer` with `{"check": "approve"}` (`--reply check=approve`), and JARVIS delivers it at once on the Work item's Messages tab (his Inbox with no Work item) as a message from you (`delivered`, with the inbox id and message in `result`); or `{"check": "change", "what_is_wrong": "<your words>"}` (`--reply check=change --reply what_is_wrong=<your words>`), and the run ends `failed` and a new run starts with your words in its evidence. Any other answer is refused, saying the form. While the swarm needs your answers it asks you the same way, in a message to your session: one list, its voters' questions grouped by meaning, one wording a group and the most asked first, each to answer once by its id (`ask-1`, `ask-2`, ...); unanswered within 2 hours, the run is in Error. Once delivered, Tim's Reject of the package starts a new run of it too, his words in its evidence.

**Error.** A run that cannot go on is in Error: you do not answer within 2 hours, its evidence is not indexed within 10 minutes, no model can be found for a step, or a step fails and cannot recover. `--status` then gives `error`: the `step`, the `reason` in plain words, what was `tried` and what would `fix` it. Its Work item is set to Error, which is Tim's notice (with no Work item, JARVIS sends him a message instead), JARVIS has it as an update, and JARVIS sends you a message in your session saying the same, once your session is between turns. You may try to fix it, or send Tim a message saying why you cannot make the package; never go around the package. A new run of the package clears the Error.

JARVIS signs the package it makes with its own key (made once in its data folder), naming you as who asked (`requested_by`, proven by your signed request when JARVIS took it); Tim's JARVIS admits a package signed so, deck checks included, with you as its sender. You sign nothing of it.

## Author understanding

Every package supplies Markdown explaining its human content and supporting files. The author supplies the meaning; the factory checks that the declared coverage exists and is readable. It does not infer or certify a PDF, diagram or page's meaning.

A hand-made package uses the existing `brief` or `skill` path and an additive `understanding` map from each supplied member to its author-written Markdown:

```json
{
  "purpose": "Explain the proposed workflow",
  "runs": "page",
  "entry": "index.html",
  "brief": "overview.md",
  "understanding": {
    "index.html": "overview.md",
    "app.js": "overview.md",
    "style.css": "overview.md",
    "workflow.pdf": "workflow.md"
  }
}
```

Several source members may share one explanation. Explain the human material with its reading order, headings, table relationships and diagram descriptions. Describe technical assets in that same brief where appropriate: for example, which script supplies controls and which stylesheet supplies layout. The package metadata and the declared Markdown explanations need no self-map. Results and other files already excluded by the shared zipper remain excluded.

Creation checks the exact supplied file set, refuses uncovered or missing members, and reads the declared Markdown as ordinary UTF-8 text. Empty, binary, unreadable or linked explanations are refused before delivery. The complete declared author view must fit the existing 4 MiB text-read budget. These checks establish usable author text and file coverage; they do not establish semantic equivalence. No PDF/HTML/image parser or automatic translation is added.

Factory rebuilds replace their generated output folder; author inputs must be kept outside it. For a factory deck, the existing structured slide/decision content and separately labeled narration are serialized to `UNDERSTANDING.md` (or an unused numbered name when the author already supplies that name), named by `brief`. The standard shared deck assets are covered there. Existing picture alt/caption text, decision context and an option's name/what it says are associated author text for their pictures. If a picture carries meaning beyond that text, supply a Markdown explanation and add a top-level `understanding` mapping to the deck JSON. The factory copies only those explicitly declared Markdown members.

JARVIS receives the declared material through the existing package-open/inbox path. The existing `package_inbox` tool accepts `understanding_offset: 0`; continue at its returned `next_offset` until null to read all declared author Markdown within the existing tool-result limit. It reads no arbitrary source files and makes no claim that opening alone read everything.

## Which kind

| You want Tim to | You write | He gets |
|---|---|---|
| Decide something | A request (`--start`): the package workflow writes the `presentation.yaml`, with `decisions` | A decision package: a slide per decision with its options, the recommendation and its reason, answered on the slide or out loud |
| Know something (an update, a presentation) | A request (`--start`): the package workflow writes the `presentation.yaml`, with no `decisions` | A presentation: the same slides, told in JARVIS's voice, carrying the standard vote, Approve or Reject, asked at Done |
| Use a working tool of your own, have an agent do work, or follow steps with JARVIS (for anything he reads or decides, send a request) | A folder, written by the fresh author | A page ("Layout not checked"), agent or conversation package |
| Review an architecture | A canonical architecture YAML, checked with `python -m jarvis.language_document` | An architecture package: JARVIS's own page of that exact checked revision, with the fresh author's plain explanation (see [Architecture documents](#architecture-documents)) |

Every package carries Tim's vote on the package itself, whatever else it asks, so he can reject a package that is not good enough. The command adds the standard one: his approval of the package, the question `approve-package`, with the options `approve` (Approve) and `reject` (Reject, which needs his words saying what to change). It is never a slide: Tim answers it at Done, the check mark in the package's bar, which offers Approve and Reject and signs the package off in the same act (or by voice). The package's `package.json` names it (`"vote": "approve-package"`; the command writes it, never you), and his answer is kept with the package's other answers (its story's `answers` file, else `results/decisions.json`). It is worded in the deck's or story's language (Aprobar and Rechazar in Spanish), else the language Tim's JARVIS speaks. A deck's or page's own questions are asked on its slides as before; a review package's `approve-item` and a merge package's `merge-pr` carry the vote too. An architecture package's story still ends with the vote, or with `approve-architecture` (Approve or Request changes) in its place, composed by the factory. An agent or conversation package cannot carry a vote, so the command and his JARVIS refuse one sent with `--work`; without `--work` it goes as before.

Got it is retired (Tim's decision, 2026-11-14): no package asks it again. Never declare the vote yourself: the command refuses a deck decision or a story slide that asks `understood` (Got it) or `approve-package`. A package sent before, still open and waiting on Got it, is asked Approve or Reject in its place, and his answer comes back to you under `approve-package`; an answer he gave to Got it before stays on record as it was.

Only real product choices go to Tim, and he chooses by looking: every option is a picture of what he would see. Decide technical details (flags, labels, limits, thresholds, internal behaviour) yourself and never put them in a package.

## A deck: one presentation.yaml

Every deck, decision package or presentation, is one `presentation/1` source, `presentation.yaml`, with its pictures beside it. You write the words, the structure, pictures by path, what JARVIS says over each slide and the decisions; JARVIS lays it out in its own theme, in the system's own interface font. You never write a theme, a rectangle, a size, a font, CSS, HTML or script: a source with `theme`, `layouts` or an object's `region` is refused, naming what replaces it. `content.json` decks are no longer made. The model is `jarvis/presentation_schema.py`; complete examples are `examples/presentations/street/presentation.yaml` and `examples/presentations/walkthrough/presentation.yaml`. Start from one:

```bash
python -m jarvis.make_package --example               # a decision package
python -m jarvis.make_package --example presentation  # a presentation
```

Write only the substance. Every deck is a briefing in decision-memo order. The opening says what it is about, where it stands, its bottom line and what Tim will decide at the end. Then come what he already decided that it touches, what happened and why, the options and what changes for him under each, and the decision. Technical detail comes last, after the decision or in the evidence. The command does not check how you write it.

| field | what it is |
|---|---|
| `version`, `requires` | `presentation/1` and `[presentation-core/1]`. |
| `id`, `title` | The package's name (a slug) and title. Tim opens it by saying "Open the <title> package." |
| `purpose` | Required. One sentence: what the package is for, in Tim's terms. |
| `conclusion` | Required. One sentence: the bottom line, what was found and what is recommended, worded for someone new to the work (never a bare "Approve X"). |
| `language` | `en` or `es`, the language its story is told in. |
| `assets` | Each picture: `id`, `path` beside the source, `sha256`, `mime`, `width`, `height` (all written by `--fill-assets`) and its `source` reference. |
| `references` | The evidence: `id`, `kind` (`authored-example`, `file` or `url`), `locator`, `revision`, `description`. Every object and picture names one as its `source`. |
| `slides` | In order. A content slide: `id`, `type: content`, `title` (JARVIS draws it at the top), `layout` (below), `objects` each with its `slot`, `readingOrder` (every object once), `narration` (cues: `id`, `say`, `focus` naming what is lit), `notes`, and optional `short` (its name in the border's Chapters). A decision's place: `{id, type: decision, decision: <id>}`. |
| objects | `text` (`paragraphs`, each `role` paragraph, heading, bullet or numbered, of `runs` with `emphasis` normal, strong, italic or `his-words`: Tim's own words, quoted, drawn highlighted bright and vibrant; narrate them too), `image` (an `asset`, `alt`, `fit: contain`), `vector` (shapes in a `viewBox`), `bar` (labelled values on a domain from zero) and `table` (`head`, `rows` of `cells`, optional `rowHeads`, `alt`). |
| `decisions` | Each has its `question`, at least two `options`, the `recommended` option's id, and the `reason`. Optional: `needs_words` (the option ids whose answer needs his words), `context` (background), `picture` (an asset beside the options), `compare` (an asset comparing the options), `say` (said in place of the recommendation), `short`, and, rarely, `text_only`: why its options cannot be pictured. |
| options | Each has an `id`, a `name`, what it `says` and a `picture` (an asset) of what he would see, required unless the decision is `text_only`; optional `opens`: the follow-up decisions asked only when this option is chosen. |

The layouts, each slot holding one object (a slot left out is left empty; the required ones must be filled):

| layout | slots |
|---|---|
| `opening` | `words` (text): the first slide |
| `title-and-body` | `body` (text, table, bar, vector or image) |
| `picture-and-words` | `words` (text) beside `picture` (image, vector or bar) |
| `two-pictures` | `first` and `second` (image, vector, bar or text), optional `words` (text) under them |
| `comparison` | two to four columns `first`, `second`, `third`, `fourth` filled in order, optional `words` (text) under them |
| `table` | `table` (table), optional `words` (text) under it |

A decision's slide and the answers slide are JARVIS's own. Run `python -m jarvis.make_package presentation.yaml --check-only` to check it: it starts no browser, so it runs the same on any engine. Unknown fields, duplicate keys and IDs, YAML aliases and tags, missing or cross-slide targets and an object in a slot that does not take it are refused with the line and column. Author IDs become the targets narration lights; you never write selectors or HTML. Narrow frames and text enlargement use Document view from More, with the same objects in reading order; Details shows each object's evidence. The border calls the existing Tell, Stop and Continue actions; Previous, Next and Chapters move through every slide the deck shows.

### What JARVIS proves

JARVIS proves every deck without a browser (`jarvis/presentation_proof.py`), in `--check-only`, as the command builds it and again in Tim's JARVIS as it arrives:

- **Pictures are real and readable** at every frame from 662 x 489 to 1600 x 900 (below).
- **The story points only at what is drawn,** and every decision and option it names exists.
- **Every word's style is readable:** at least the text floor at the smallest and the largest frame.
- **The renderer is JARVIS's own,** byte for byte: no package carries a changed template.

The proof (`.jarvis/layout-proof.json`) also records each slide as JARVIS lays it out: its title, each object in reading order with its slot, its box and its size in pixels at the smallest and the largest frame, each word's style and size there, each picture's drawn size there, and its narration.

Words are not measured: no font ships with JARVIS or a deck, and each draws in the system's own interface font (San Francisco on a Mac, Segoe UI on Windows, then the common sans-serifs), every word 13 to 25 px. The numbers are one spec, `jarvis/presentation_assets/layout-spec.json`, from which the deck's CSS is generated (`python -m jarvis.layout_spec`; `--check` fails when they differ).

Pictures are PNG, JPEG, GIF, WebP or SVG files, given as paths beside the source. JARVIS proves each one without a browser (`jarvis/picture_proof.py`): its kind is what its bytes say, and it is read whole (a PNG's every chunk and its pixel rows, a GIF's every block and its image data, a JPEG's every segment to its end marker, a WebP's container), so a cut-short or damaged file is refused with its name. An SVG's size is read from it: its `width` and `height` in pixels when it gives them, else its `viewBox` (one of the two is needed, or it has no size); a `viewBox` given with both has the same shape. It holds only static drawing elements, every label's `font-size` in pixels and no transform on a label but a translate or a uniform scale. Each picture must also be readable where it is drawn: at the smallest frame (662 x 489) it is drawn contained in its place, never past twice its own size, and under 60 px either way, or with a label under 13 px, it is refused. A picture much wider or taller than its place, or a tiny one, is the usual cause: give it a shape nearer its place's.

### What is yours to write

What each slide says, how long it is and in what order is the author's: no rule counts its words or characters, requires a kind of first slide, draws a slide from fields of the source, or adds a slide before a decision. What stays checked: each decision has at least two options, every picture is readable where it is drawn, and the story JARVIS tells over the deck runs about fifteen minutes at most.

`python -m jarvis.make_package --example` prints a deck that passes.

Worked out for you, and never needed: its `version` (one past the highest of that name already delivered), each decision's `short` name, the story JARVIS tells, `package.json`, the answering skill and `decide.py`.

A field the source does not know is refused with its line and column.

## A folder

Write the content and a `package.json` holding only `purpose`, one sentence saying what the package does and why:

- a page: `index.html`, with its scripts and styles beside it, loaded with ordinary `<script src>` and `<link>` tags (no ES modules, no `fetch` of its own files). An optional `story.json` is told over it; the page must then load `<script src="/api/packages/story.js"></script>`. The page's bridge (saving results, the microphone) and the story format are in [work packages](work-packages.md).
- an agent's task: `BRIEF.md`, written to a coding agent that runs with nobody to answer it.
- steps JARVIS follows with Tim: `SKILL.md`, each step a shell command run from the package's folder.

Worked out for you: `name` (from the folder's name), `version`, `runs` (from which of `index.html`, `BRIEF.md` or `SKILL.md` is there), the file it runs, `story` (when `story.json` is there), and `returns` (everything under `results/`). Give any of them in `package.json` to choose otherwise. A folder holding more than one of those three files must say which it is with `runs`.

## Architecture documents

Architecture you put to Tim is a canonical YAML document in the `arch` language, checked by JARVIS's pinned language kit; the package is JARVIS's projection of one exact checked revision, never a page of your own. You have no `language_document` tool: the same operations are a command, every request signed with your own key (it needs the `packages` right, which an onboarded agent has). In order:

```bash
# 1. What the kit in force covers (the constructs coverage may name).
python -m jarvis.language_document --as <your session id> capabilities
# 2. Check your YAML as a proposed revision on its Work item. It prints the whole envelope:
#    keep result.document_id and result.revision. A correction adds document_id and
#    parent_revision (the revision it replaces) to --json.
python -m jarvis.language_document --as <your session id> validate --source architecture.yaml \
  --json '{"work_id": "<item id>", "scope_revision": "<what it answers>", "author": "<your session id>",
           "coverage": {"constructs": ["forms", "relations"], "declarations": ["<every declared id>"],
                        "unresolved": [], "outside_language": ["<meaning the language does not carry>"]}}'
# 3. Send that exact revision to Tim.
python -m jarvis.make_package --document <document_id> --revision <revision> \
  --handoff handoff.json --engine <claude|codex> --session <your session id> --work <item id>
```

The opening rule applies here through the explanation, not through slides: the page is the kit's drawing of the checked document, whose title and content come from its revision and cannot be reworded without a new one, so the command does not check its first screen. The fresh author writes `UNDERSTANDING.md` (the answer and the ask first, then the walk-through); the command checks only that the explanation is there and readable.

`validate` exits 1 and prints the kit's diagnostics while the draft does not check or leaves anything `unresolved`; such a revision cannot be sent. Do not give `readable_markdown`: the explanation is the fresh author's. `make_package --document` launches the fresh author as for any package, giving it the checked revision (its YAML, rendered page, check and coverage); the author writes `package/UNDERSTANDING.md`, the plain explanation, optionally `package/story.json` (below), and nothing else, and checks them last with `make_package package --check-only --document <document_id> --revision <revision>`. The command then projects the revision around them (the YAML, the kit's rendered page as the package's page, the check and the canonical identity, `language_projection.project`, the same projection JARVIS's own publish makes), with your handoff's questions (their text in order, `package_request.questions_text`) as the package's purpose (its spacing folded into one line), binds the explanation, the story and that purpose in `canonical.json`, runs the kit's check again on it as Tim's JARVIS will, signs it with your key, and sends it as a message on the document's own Work item (`--work` may be left out; another item is refused). It ends with the standard vote, added once by the factory.

**Asking Tim to approve the design.** The standard vote (`approve-package`) approves the package as a whole and never counts as his approval of an exact checked revision. When you need his approval of the architecture design, add `"approval": true` to the handoff. The factory then asks it as the package's last question, in place of the standard vote, with the reserved id `approve-architecture` and the options `approve` and `request-changes` (which needs his words; he may name declarations or members): one answer for the whole design. Neither you nor the author may ask it any other way, and no other package can carry it. His answer is recorded with the exact checked revision it was given to (`document_id`, `revision`, `source_revision`, `kit_digest`) and comes back to your session on sign-off as `approve-architecture: approve, given to revision <revision> of <document_id>`. Once a newer revision of the same document is checked, or a kit whose language files (`metalang.yaml`, `arch.yaml`, `propose.yaml`) differ from those it was checked under comes into force, that answer is stale (a kit release that changes none of them leaves it standing): it is shown so and counts for nothing, and a new package of the newer revision asks again. A wait on it (a workflow's `wait_started --until package:<id>:approve-architecture`, or a work piece's `--until`) ends only when he approves the revision in force; request changes and signing the package off never end it.

Tim's JARVIS admits it only when the package's explanation and story are byte for byte the ones its canonical revision binds, opens it like any page package with the explanation readable beside the page, and his answers come back to your session when he signs it off.

The author's `story.json` is a page story ([work-packages.md, "Telling a page's story"](work-packages.md)), read strictly: at most 128 KB, no key twice, only the fields `title`, `language`, `slides` and `questions` (a slide `title`, `target`, `beats`, `waits`, `asks_when`; a beat `say`, `targets`, `actions`), no `answers` (the factory sets it) and never the standard vote (`approve-package`, or the retired `understood`). Every slide that `waits` waits for a question it declares in `questions`, so each is answered in the package view's head, by click or by voice, and recorded where the story waits:

```json
{"title": "Reading grants",
 "questions": [{"id": "grant-why", "question": "Does every cause reach your sink file?",
                "options": [{"id": "every-cause", "name": "Every cause", "says": "Each refused file is noted."},
                            {"id": "first-only", "name": "First only", "says": "Only the first is noted."}],
                "recommended": "every-cause", "reason": "The audit asked for every cause."}],
 "slides": [{"title": "A read", "beats": [{"say": "A grant read answers with Loaded.",
             "targets": ["[data-declaration=\"IGrantStore\"] [data-part=\"members\"]"]}]},
            {"title": "Causes", "waits": "grant-why", "beats": [{"say": "Does every cause reach the sink?"}]}]}
```

A question has `id`, `question` and two to six `options` (`id`, `name`, `says`), with an optional `recommended`, `reason` and `context`, held to a deck's own word limits; its id is never `approve-package`, `understood` or `approve-item` (an ordinary question is never a review), and `asks_when` asks one only once another has one of the given answers. A beat points at the kit's rendered page only by the targets its page carries: `[data-declaration="<id>"]` for a declaration's card (the declaration's id exactly as written), `[data-part="<part>"]` inside it (`reach`, `exactness`, `groups`, `relates`, `sits`, `members`) and `[data-col="<column>"]` on a member table's header and cells, as in `[data-declaration="IGrantStore"] [data-part="members"]`. `[data-member="<name>"]` inside its declaration's card is one member's row, the row and never the note under it: a name carried once in the declaration is the name itself, as written; a name several of its members share is `<name>#<place>`, counting from 1 among the rows that share it (Tim's answer of 2026-11-13, identity-spelling, among-the-shared), and the bare shared name is reported as ambiguous. For example `[data-declaration="QuotedStore"] [data-member="GetValue"]` or `[data-declaration="SharedStore"] [data-member="Get#2"]`; `package_story.target(declaration, member=...)` writes one with each value quoted. Such a target must match exactly one element or it is reported and nothing is pointed at. While the kit's page in force does not load the story helper, a story with `targets` or `actions` is refused: narrate without them.

**Several views of one system (a view set).** Architecture is several cooperating views of the same system, each answering a different question and naming the same elements: how the code is organised, how it is placed, how it is built and shipped. Check each view as its own document on the same Work item, then send them as one package by giving `--document` and `--revision` once per view, in the order he should see them:

```bash
python -m jarvis.make_package --document <first document_id> --revision <its revision> \
  --document <second document_id> --revision <its revision> \
  --handoff handoff.json --engine <claude|codex> --session <your session id> --work <item id>
```

The first is the package's own revision; the others are bound beside it in `canonical.json`'s `set`, each checked again by his JARVIS as the first is. Each document is in one language the kit in force carries (today `arch`; a build-and-release view language will take its place beside it once the kit's publisher adds it). The fresh author writes one `UNDERSTANDING.md` for the whole set and must write `story.json`, walking one concrete scenario through every view in turn: its `scenario` names that input, every slide names its `view` (the document id), each view is told whole before the next in the order given, and every view has at least one slide; the author's check and his JARVIS both refuse a story that does not. The same id names the same element in every view, so a beat that points at `[data-declaration="DocumentStore"]` in one view leaves it lit as the story moves to the next. In the package view the frame shows one view at a time, the border's Chapters name each slide's view and move between them, and the source and check links follow the view shown. With `"approval": true` his approval is of the exact set: it is recorded with every view's revision, and once a newer revision of any one view's document is checked (or the kit's language changes) it is stale. At most six views; each document once.

To discuss it afterwards, `python -m jarvis.language_document --as <your session id> ground --json '{"package_id": "<package id>"}'` gives back the exact canonical YAML, its revision and the author's explanation, with a `followup_reference` to quote. A change is a new revision (`validate` with `document_id` and `parent_revision`) sent the same way; the newer package replaces the open one. `retrieve` and `render` read one revision by `document_id` and `revision` (`render` also `source_revision`). There is no publish for agents.

## What the command does

1. Launches a fresh author from the handoff, then checks its source and lists everything to change at once; a deck is refused (a briefing: ask for it with `--start`). `--check-only` validates existing source without launching an author.
2. Builds and zips it: an agent's or conversation's folder, or an architecture document's projection around its explanation.
3. Checks it again without a browser: the zip is read the way Tim's JARVIS will read it.
4. Only if everything passed, signs it with your session's key and sends it to Tim as a message carrying it (`POST /api/messages`, the request signed too), the same one way every message to him goes. The message says the package's kind and title and is on the Work item `--work` names, in its Messages tab, or in his Inbox with no `--work`; while you have a piece on an open item, `--work` is required. The package is unpacked in his package inbox, and is never shown and never spoken until he opens it.

It prints the message's id, the inbox id and the package id. To take back a package you sent while it is still open (it is wrong, or no longer wanted), run `python -m jarvis.make_package --withdraw <inbox id> --session <your session id>`: only the session that sent it may, it leaves his Inbox, anything waiting on it ends (a wait on one of its questions as closed unanswered) and its vote on the work item closes, and nothing comes back to you. When he is done with it he signs it off (Done on the package, or telling JARVIS), answered or not (with questions unanswered he is asked once whether he is done anyway, and a wait on one of them then ends as closed unanswered): whatever he answered comes to your session (`--session`) as a message, unanswered questions marked unanswered, or for a package that asks nothing a message that he has reviewed it; it is read back to him first and sent on his yes. With `--workflow <id>` the answers also come back as that workflow's decisions, one per decision id (`workflow_report.py answer <id> <decision id>`), and the workflow waits on them meanwhile, its wait linked to the package so it ends itself once he signs it off. When you wait on the package yourself, link the wait to it: a workflow's `wait_started ... --until package:<inbox or package id>` (or `package:<id>:<decision id>` for one decision), or a work item's piece `--state waiting_on_tim --until package:<id>` (a work item piece waiting on him must name what it waits for, and only that answer moves it on). JARVIS ends it the moment he signs the package off, sets it aside or answers that decision; never wait on him with no link. When a newer version you send replaces the package, what waited on it waits on the newer one, and the newer one is for the same work item when you name none. A wait you placed yourself you may end yourself (`work_report.py end-wait <id> <summary> --piece <key>`, or a workflow's `wait_ended`); one JARVIS or another agent placed ends only by his answer. With `--work <id>` it is a message on that work item's Messages tab, and what he chose shows under it; a work item decision that names the package gets the answer he gave to the question of the same key, or is closed without an answer (`work_report.py answer` exits 4, as it does for the item's vote on a package he set aside or you replaced). A review package for a work item (sent with `--work <id>`) asks one question with the id `approve-item` and the options `approve`, `not-done` and `add-work` (his words go under the last two): his answer to it is his review of the item itself (approve makes it done, not-done sends it back to working with his note, add-work adds a piece in his words), recorded as if he had used the item's own Approve, Not done yet or Add work, and you are told in the answers folder. Its question never stands in the way of his approving the item; any other question you send for the item does, until he answers it. When he answers the standard vote Reject, his words saying what to change come back to you in the sign-off message as `approve-package: reject (his words: ...)`, and with `--work <id>` they are also that work item's decision `approve-package-<inbox id>`, asked and answered at once (`work_report.py answer <id> approve-package-<inbox id>`). Approve comes back as `approve-package: approve`. A newer version of a package you sent replaces the older one while it is still open. If anything fails nothing is delivered: fix what it says and run it again.
