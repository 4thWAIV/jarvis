# Making a package

This page is the only description of how to make a package and send it to Tim. Every kind goes through one command, and nothing else: never build, zip, attach or deliver a package by hand.

```bash
python -m jarvis.make_package --handoff handoff.json --engine <claude|codex> --session <your session id> [--workflow <id>] [--work <id>]
```

It signs the package with your session's own key (`--session`; `python -m jarvis.agent_key init` makes it and `submit` sends it, and Tim accepts it by switching your agent on), so JARVIS knows it is yours: nothing is delivered without a key, and a package signed by a key Tim has not accepted is held until he does. A subagent signs with its parent's key; it is named as the sender, beside its parent, only when its own transcript shows it running the command. Give `--work <id>` with an agent package and it carries a key limited to that one Work item (`.jarvis/work-key`), so whoever works in the unpacked package reports on that item with `work_report.py`; it ends when the item closes or after 30 days.

Run it with JARVIS's own Python. The talk-to-jarvis skill gives the exact command for this Mac, which works from any folder. From inside the JARVIS checkout it is `.venv/bin/python -m jarvis.make_package`, and then give `--project <your project>` as well, since the project otherwise defaults to the name of the folder you run it from.

## Fresh author on the caller's engine

Creation launches a fresh author through the existing provider session on the caller's own engine. A Codex caller uses `--engine codex`; a Claude caller uses `--engine claude`. Only that executable and its existing subscription login are needed. The other engine is never a fallback. The author starts a new conversation with the shipped `human-package-author` skill, the handoff and its evidence, without inheriting the originating conversation. The author's tools can read evidence and write the package source.

The originating caller supplies one handoff JSON file:

```json
{
  "purpose": "Explain the released microphone-check page",
  "audience": "The person using JARVIS, who knows the microphone controls",
  "questions": ["Does recording send my clip?", "What does saving do?"],
  "evidence": ["./release-receipt.json", "./source/BRIEF.md"],
  "decision": "information only"
}
```

`purpose` and `audience` are nonempty text. `questions` and `evidence` are nonempty lists of text. Evidence may name local files, URLs and the source revisions needed to evaluate them; relative local references are relative to the handoff file. Additional context may include the actual decision and its scope, known limitations and ownership. Supply facts and reader questions, not an instruction to defend a draft. Existing source can accompany the handoff as the positional argument; the fresh author treats it as evidence.

The command waits for the author, accepts its `content.json` or `package/` output, then runs the existing validation, build, rendered checks and inbox delivery. `--session`, `--work` and `--workflow` remain the originating caller's associations. Author failure, timeout, missing source or invalid output prevents delivery. The author receipt and evidence remain under the output folder's `author/` directory. The author does not deliver or recursively launch another author.

For source validation while authoring, use `python -m jarvis.make_package <content.json | folder> --check-only`. This never launches a model or delivers. Creation without a handoff is refused. `--example` also remains model-free. Install the shared skills into existing Claude and Codex homes with `python -m jarvis.session_skill install`.

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
| Decide something | One JSON file with `decisions` | A decision package: a slide per decision with its options, your recommendation and its reason, answered on the slide or out loud |
| Know something (an update, a presentation) | One JSON file with only `slides` | A presentation: the same slides, told in JARVIS's voice, ending with the standard vote, Got it or I have questions |
| Use a page of your own, have an agent do work, or follow steps with JARVIS | A folder | A page, agent or conversation package |

Every package carries Tim's vote. A deck or a page that asks him nothing gets the standard one at its end, added by the command: the question `understood`, with the options `got-it` (Got it) and `questions` (I have questions, his words under it). It is in the deck's or story's language (Entendido and Tengo preguntas in Spanish); a deck that names none, and a page with no story, get the language Tim's JARVIS speaks when you build it. A deck gets it as one more decision, after its slides; a hand-made page as its story's last slide, waiting on the story's `answers` file (`results/decisions.json` when it names none; a page with no story gets one of that slide alone), and Tim answers it with the Got it and I have questions buttons JARVIS shows beside Done, or by voice. A deck or page that already asks something is left as it is. An agent or conversation package cannot carry a vote, so the command and his JARVIS refuse one sent with `--work`; without `--work` it goes as before.

Only real product choices go to Tim, and he chooses by looking: every option is a picture of what he would see. Decide technical details (flags, labels, limits, thresholds, internal behaviour) yourself and never put them in a package.

## A deck: one JSON file

Start from an example:

```bash
python -m jarvis.make_package --example               # a decision package
python -m jarvis.make_package --example presentation  # a presentation
```

Write only the substance. A decision package:

```json
{
  "title": "Team lunch",
  "decisions": [
    {"question": "Where do we eat on Friday?",
     "options": [{"name": "In the office", "says": "Food is brought in.",
                  "picture": "pictures/office.png", "recommended": true},
                 {"name": "A restaurant", "says": "A short walk away.",
                  "picture": "pictures/restaurant.png"}],
     "reason": "Nobody has to book a table."}
  ]
}
```

A presentation is the same file with `slides` and no `decisions`. A decision package may have `slides` too, shown before its decisions.

| field | what it is |
|---|---|
| `title` | The deck's title. Tim opens it by saying "Open the <title> package." |
| `language` | `en` (the default) or `es`, the language its story is told in. |
| `slides` | The slides, in order. Each has a `title`, `say` (what JARVIS says over it; without it JARVIS says only the title), and `blocks`. Optional: `short` (its name on the slide strip), `id`, `kicker`, and `beats` in place of `say` (several lines, each pointing at a part of the slide; see "Telling a page's story" in [work packages](work-packages.md)). `{"decision": "<question, short name or id>"}` places that decision's slide there. |
| `blocks` | Each one of `{"p": text}`, `{"lead": text}`, `{"note": text}`, `{"points": [text or {"b", "text"}]}`, `{"facts": [{"n", "text"}]}`, `{"table": {"head", "rows"}}`, `{"picture": path, "alt", "caption"}` or `{"pictures": [{"src", "alt", "caption"}]}`. Text may use `**bold**` and `` `code` ``. |
| `decisions` | Each has its `question`, at least two `options`, which one is recommended, and the one-line `reason`. Optional: `context` (a line of background), `picture` (beside the options), `compare` (a large picture comparing the options), `say` (said in place of the recommendation), `short`, and, rarely, `text_only`: why its options cannot be pictured, in at least five words. |
| `options` | Each has a `name`, what it `says` and a `picture` of what he would see (required unless the decision is `text_only`). Optional: `"recommended": true` (on exactly one, or name the option in the decision's `recommended`), and `opens`: the questions asked only when this option is chosen. |

Pictures are PNG, JPEG, GIF, WebP or SVG files, given as paths beside the JSON file.

Worked out for you, and never needed: the package's `name` and `purpose` (from the title), its `version` (one past the highest of that name already delivered), every `id`, each decision's `short` name, the story JARVIS tells, `package.json`, the answering skill and `decide.py`.

No slide may be a wall of text. The limits are 80 characters for a title, 16 for a short name, 120 for a question, 160 for a reason, 40 for an option's name and 140 for what it says; 280 for any one line and 700 for a whole slide; and 60 words of body text on a slide, 30 words for a question, and 40 for a reason, context or what an option says. A field the deck does not know (`sais`, `recomended`) is refused with the field you probably meant. Split a long slide into two.

## A folder

Write the content and a `package.json` holding only `purpose`, one sentence saying what the package does and why:

- a page: `index.html`, with its scripts and styles beside it, loaded with ordinary `<script src>` and `<link>` tags (no ES modules, no `fetch` of its own files). An optional `story.json` is told over it; the page must then load `<script src="/api/packages/story.js"></script>`. The page's bridge (saving results, the microphone) and the story format are in [work packages](work-packages.md).
- an agent's task: `BRIEF.md`, written to a coding agent that runs with nobody to answer it.
- steps JARVIS follows with Tim: `SKILL.md`, each step a shell command run from the package's folder.

Worked out for you: `name` (from the folder's name), `version`, `runs` (from which of `index.html`, `BRIEF.md` or `SKILL.md` is there), the file it runs, `story` (when `story.json` is there), and `returns` (everything under `results/`). Give any of them in `package.json` to choose otherwise. A folder holding more than one of those three files must say which it is with `runs`.

## What the command does

1. Launches a fresh author from the handoff, then checks its source and lists everything to change at once. `--check-only` validates existing source without launching an author.
2. Builds and zips it. A deck is built by the shared deck, `examples/packages/decision-deck/`.
3. Checks it again. A deck is opened in an isolated JARVIS of its own, never Tim's: found and opened by its title, its own styles in force in the package frame, every slide fitting at both screen sizes with readable text, and its story told to the end. For a decision package it also checks that every vote control is visible and large enough, that the story waits at each question until it is answered (by a Choose click and by voice in turn), that Stop ends it within a second from the page and from JARVIS, and that it resumes at the first unanswered question. Every slide and step is photographed. A folder's zip is read the way Tim's JARVIS will read it.
4. Only if everything passed, signs it with your session's key (a decision package as checked, since his JARVIS opens no other) and sends it to Tim's package inbox, the request signed too. It is unpacked there and listed in the Packages section of his Queue tab and in his updates, as one line naming you, its title and how many decisions wait. It is never shown and never spoken until he asks for it.

It prints the inbox id and the package id. When he is done with it he signs it off (Done on the package, or telling JARVIS), answered or not: whatever he answered comes to your session (`--session`) as a message, unanswered questions marked unanswered, or for a package that asks nothing a message that he has reviewed it; it is read back to him first and sent on his yes. With `--workflow <id>` the answers also come back as that workflow's decisions, one per decision id (`workflow_report.py answer <id> <decision id>`), and the workflow waits on them meanwhile, its wait linked to the package so it ends itself once he signs it off. When you wait on the package yourself, link the wait to it: a workflow's `wait_started ... --until package:<inbox or package id>` (or `package:<id>:<decision id>` for one decision), or a work item's piece `--state waiting_on_tim --until package:<id>` (a work item piece waiting on him must name what it waits for, and only that answer moves it on). JARVIS ends it the moment he signs the package off or answers that decision; never wait on him with no link. With `--work <id>` its questions show on that work item's Decisions tab, where he can answer them too; a work item decision that names the package gets the answer he gave to the question of the same key, or is closed without an answer (`work_report.py answer` exits 4). A review package for a work item (sent with `--work <id>`) asks one question with the id `approve-item` and the options `approve`, `not-done` and `add-work` (his words go under the last two): his answer to it is his review of the item itself (approve makes it done, not-done sends it back to working with his note, add-work adds a piece in his words), recorded as if he had used the item's own Approve, Not done yet or Add work, and you are told in the answers folder. Its question never stands in the way of his approving the item; any other question you send for the item does, until he answers it. When he answers the standard vote I have questions, his words come back to you in the sign-off message as `understood: questions (his words: ...)`, and with `--work <id>` they are also that work item's decision `understood-<inbox id>`, asked and answered at once (`work_report.py answer <id> understood-<inbox id>`). A newer version of a package you sent replaces the older one while it is still open. A decision package's answers are recorded in its `results/decisions.json`, at the path it prints and in `delivery.json` in its output folder. If anything fails nothing is delivered: fix what it says and run it again.
