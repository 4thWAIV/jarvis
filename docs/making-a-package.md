# Making a package

This page is the only description of how to make a package and send it to Tim. Every kind goes through one command, and nothing else: never build, zip, attach or deliver a package by hand.

```bash
python -m jarvis.make_package <content.json | folder> --session <your session id>
```

Run it with JARVIS's own Python. The talk-to-jarvis skill gives the exact command for this Mac, which works from any folder. From inside the JARVIS checkout it is `.venv/bin/python -m jarvis.make_package`, and then give `--project <your project>` as well, since the project otherwise defaults to the name of the folder you run it from.

## Which kind

| You want Tim to | You write | He gets |
|---|---|---|
| Decide something | One JSON file with `decisions` | A decision package: a slide per decision with its options, your recommendation and its reason, answered on the slide or out loud |
| Know something (an update, a presentation) | One JSON file with only `slides` | A presentation: the same slides, told in JARVIS's voice, with nothing to answer |
| Use a page of your own, have an agent do work, or follow steps with JARVIS | A folder | A page, agent or conversation package |

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
| `decisions` | Each has its `question`, at least two `options`, which one is recommended, and the one-line `reason`. Optional: `context` (a line of background), `picture` (beside the options), `compare` (a large picture comparing the options), `say` (said in place of the recommendation), `short`. |
| `options` | Each has a `name` and what it `says`. Optional: `picture`, `"recommended": true` (on exactly one, or name the option in the decision's `recommended`), and `opens`: the questions asked only when this option is chosen. |

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

1. Checks the content and lists everything to change at once. `--check-only` stops here.
2. Builds and zips it. A deck is built by the shared deck, `examples/packages/decision-deck/`.
3. Checks it again. A deck is opened in an isolated JARVIS of its own, never Tim's: found and opened by its title, every slide fitting at both screen sizes with readable text, its story told to the end, and for a decision package each vote row in view, a Choose saved and a spoken answer shown. Every slide is photographed. A folder's zip is read the way Tim's JARVIS will read it.
4. Only if everything passed, puts it in Tim's JARVIS without showing it, and sends him a message naming it and the words that open it.

It prints the package id. A decision package's answers are recorded in its `results/decisions.json`, at the path it prints and in `delivery.json` in its output folder. If anything fails nothing is delivered: fix what it says and run it again.
