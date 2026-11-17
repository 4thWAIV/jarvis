# Making a package

This page is the only description of how to make a package and send it to Tim. Every kind goes through one command, and nothing else: never build, zip, attach or deliver a package by hand.

```bash
python -m jarvis.make_package --handoff handoff.json --engine <claude|codex> --session <your session id> [--workflow <id>] [--work <id> | --general]
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

`purpose` and `audience` are nonempty text. `questions` and `evidence` are nonempty lists of text. For an architecture document only, `"approval": true` asks Tim to approve the design (see [Architecture documents](#architecture-documents)). Evidence may name local files, URLs and the source revisions needed to evaluate them; relative local references are relative to the handoff file. Additional context may include the actual decision and its scope, known limitations and ownership. Supply facts and reader questions, not an instruction to defend a draft. Existing source can accompany the handoff as the positional argument; the fresh author treats it as evidence.

The command waits for the author, accepts its `content.json`, `presentation.yaml` or `package/` output, then runs the existing validation, build, rendered checks and inbox delivery. The author must run `--check-only` itself last, after its final change: that check prints the source's digest (`jarvis-author-source sha256:…`), the package carries the authored source under `.jarvis/author/`, and the signature names the author session and that digest. JARVIS reads the author session's own transcript and holds an agent package whose author did not check exactly the source it carries (or that names no fresh author, or reuses an author already used for another package); Tim is told why. The command checks the same thing before delivering, so a source changed after the author's last check is never sent. `--session`, `--work` and `--workflow` remain the originating caller's associations. Author failure, timeout, missing source or invalid output prevents delivery. The author receipt and evidence remain under the output folder's `author/` directory. The author does not deliver or recursively launch another author.


The same author session stays open through private build and visual review. The factory renders presentation and decision slides and custom/canonical HTML pages at both existing frame sizes, waits up to five seconds for image decoding, and rejects missing, hidden, zero-size or clipped required pictures. Custom pages use their existing story `show` and `click` actions to expose every intended tab and expanded view. Required images that never become visible stop delivery; mark only genuinely decorative images with `role="presentation"`. Scrollable pages are inspected at each picture's viewing position.

The factory returns the actual screenshots and exact built archive to that author on its existing engine. The author opens every screenshot with its image tool, inspects the illustration's meaning and internal text, and accounts for all built members against its story and understanding. Supplied images and document pages intended for reading are inspected with existing browser/document tools. Supporting, unused and decorative files need no separate reader-page inspection. An uninspectable intended view blocks delivery with its name. A package without visual material gets an explicit, content-based not-applicable receipt; a DOM size check never establishes the readability of content inside SVG, raster, canvas or PDF material.

The factory permits one source repair followed by a new build, render and review in that same session. It records failures under the existing output folder. When rendered review still fails, it sends a signed failure message on the existing `--work` item without attaching the package, and returns failure to the caller. Acceptance is tied to the source digest, the unchanged archive bytes (including its template), and screenshot hashes. Any rebuild requires another review. The signed package carries `.jarvis/visual-review.json`, and the author receipt preserves the review. Delivery requires both the machine result and this author's acceptance.

**A status briefing.** For a status presentation on Work items, build the handoff from their records rather than writing it yourself: `python -m jarvis.status_briefing --work <id> [--work <id> ...] --out <folder> [--since <24h | 7d | ISO time | epoch seconds>]` writes `<folder>/handoff.json` and `<folder>/status-evidence.json`. Per item it gives what needs Tim first (his open decisions, packages still asking him, his review, every held piece and what holds it, stalled pieces, each with who and since when; or one line saying nothing needs him), then pieces reported working with their latest note, then what finished since `--since` (24 hours by default), every fact carrying the evidence its piece has on record. The handoff asks one decision, `approve-status`: Approve ("Approve: this status is complete and accurate") or Reject ("Reject: say what is missing or wrong", with his words), with its lead-in: the question, why it is his (he steers the work) and the evidence file. Then run `python -m jarvis.make_package --handoff <folder>/handoff.json --engine <claude|codex> --session <your session id> --work <id>`. Run `python -m jarvis.status_briefing --work <id> --check` first: it writes nothing and names each piece with no note, or past planned or ready with no evidence, so you can report what is missing before the briefing is built on a stale record.

For source validation while authoring, use `python -m jarvis.make_package <content.json | presentation.yaml | folder> --check-only`. This never launches a model or delivers. Creation without a handoff is refused. `--example` also remains model-free. Install the shared skills into existing Claude and Codex homes with `python -m jarvis.session_skill install`.

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
| Know something (an update, a presentation) | One JSON file with only `slides` | A presentation: the same slides, told in JARVIS's voice, ending with the standard vote, Approve or Reject |
| Use a page of your own, have an agent do work, or follow steps with JARVIS | A folder | A page, agent or conversation package |
| Review an architecture | A canonical architecture YAML, checked with `python -m jarvis.language_document` | An architecture package: JARVIS's own page of that exact checked revision, with the fresh author's plain explanation (see [Architecture documents](#architecture-documents)) |

Every package carries Tim's vote. A deck or a page that asks him nothing gets the standard one at its end, added by the command: his approval of the package, the question `approve-package`, with the options `approve` (Approve) and `reject` (Reject, which needs his words saying what to change). It is in the deck's or story's language (Aprobar and Rechazar in Spanish); a deck that names none, and a page with no story, get the language Tim's JARVIS speaks when you build it. A deck gets it as one more decision, after its slides; a hand-made page as its story's last slide, waiting on the story's `answers` file (`results/decisions.json` when it names none; a page with no story gets one of that slide alone), and Tim answers it with the Approve and Reject buttons JARVIS shows beside Done, or by voice. A deck or page that already asks something is left as it is: a review package's `approve-item`, a merge package's `merge-pr` and an architecture package's `approve-architecture` already are his approval, so no second one is added. An agent or conversation package cannot carry a vote, so the command and his JARVIS refuse one sent with `--work`; without `--work` it goes as before.

Got it is retired (Tim's decision, 2026-11-14): no package asks it again. Never declare the vote yourself: the command refuses a deck decision or a story slide that asks `understood` (Got it) or `approve-package`. A package sent before, still open and waiting on Got it, is asked Approve or Reject in its place, and his answer comes back to you under `approve-package`; an answer he gave to Got it before stays on record as it was.

Only real product choices go to Tim, and he chooses by looking: every option is a picture of what he would see. Decide technical details (flags, labels, limits, thresholds, internal behaviour) yourself and never put them in a package.

## A composed presentation: one YAML source

Use `presentation/1` when the package needs named text, vector members, contained images, literal bar comparisons or explicit region composition. The source model is implemented in `jarvis/presentation_schema.py`; the complete examples are `examples/presentations/street/presentation.yaml` and `examples/presentations/walkthrough/presentation.yaml`.

Run `python -m jarvis.make_package presentation.yaml --check-only` to check source references, assets, the shared briefing/story policy and browser layout before author proof is recorded. For delivery, pass the source as evidence with the usual `--handoff` and `--work`; the independent author may return `presentation.yaml`. No additional service, answer writer or delivery command is used.

Each source declares `requires: [presentation-core/1]`, its theme, percentage regions, objects, reading order, asset hashes, evidence references, cues and pictured decisions. Unknown fields, duplicate keys and IDs, YAML aliases/tags, missing or cross-slide targets and unreadable layouts fail creation. Author IDs generate target selectors; authors never write selectors or generated HTML. The compiler retains exact source bytes and generates `target-index.json` alongside the existing package manifest, story and shared decision controls.

Desktop composition uses readable text without slide scaling. Narrow frames and text enlargement use Document view from More, with the same objects in source reading order. Details exposes object provenance. The border calls the existing Tell, Stop and Continue actions; Previous, Next and Chapters open a slide in the presentation and select it for Tell, and the border's count follows every slide the presentation shows. Continue starts at an unanswered question. Pause and return to an interrupted cue require the later behavior implementation.

## A deck: one JSON file

Start from an example:

```bash
python -m jarvis.make_package --example               # a decision package
python -m jarvis.make_package --example presentation  # a presentation
```

Write only the substance. Every deck is a briefing in decision-memo order. The opening says what it is about, where it stands, its bottom line and what Tim will decide at the end. Then come what he already decided that it touches, what happened and why, the options and what changes for him under each, and the decision. Technical detail comes last, after the decision or in the evidence. Every decision is led in by why it is Tim's and what it rests on (see [The opening and the lead-ins](#the-opening-and-the-lead-ins)); the fresh author's skill (`human-package-author`) says how to write each part. A decision package:

```json
{
  "title": "Team lunch",
  "purpose": "Choose where the team eats on Friday.",
  "conclusion": "Choose lunch in the office on Friday, so nobody has to book a table.",
  "slides": [
    {"title": "Where should Friday's lunch be?",
     "blocks": [{"lead": "Choose lunch in the office on Friday, so nobody has to book a table."},
                {"p": "The restaurant needs a booking by Wednesday."}],
     "say": "This is about where the team eats on Friday. I suggest the office, so nobody has to book a table; the restaurant needs a booking by Wednesday."}
  ],
  "decisions": [
    {"question": "Where do we eat on Friday?",
     "why_you": "It is your team's day and your budget.",
     "evidence": ["The restaurant needs a booking by Wednesday.",
                  "The office caterer needs a day's notice."],
     "options": [{"name": "In the office", "says": "Food is brought in.",
                  "picture": "pictures/office.png", "recommended": true},
                 {"name": "A restaurant", "says": "A short walk away.",
                  "picture": "pictures/restaurant.png"}],
     "reason": "Nobody has to book a table."}
  ]
}
```

A presentation is the same file with `slides` and no `decisions`. A decision package's `slides` are shown before its decisions, the first of them its opening.

| field | what it is |
|---|---|
| `title` | The deck's title. Tim opens it by saying "Open the <title> package." |
| `purpose` | Required. One sentence: what the package is for, in Tim's terms; at most 200 characters. It is also the package's own purpose. |
| `conclusion` | Required. One sentence: the bottom line, what was found and what is recommended, worded for someone new to the work (never a bare "Approve X"); at most 200 characters. One sentence means one line that ends in `.`, `!`, `?` or `…` at most once. |
| `language` | `en` (the default) or `es`, the language its story is told in. |
| `slides` | The slides, in order. Each has a `title`, `say` (what JARVIS says over it; without it JARVIS says only the title), and `blocks`. Optional: `short` (its name in the border's Chapters), `id`, `kicker`, and `beats` in place of `say` (several lines, each pointing at a part of the slide; see "Telling a page's story" in [work packages](work-packages.md)). `{"decision": "<question, short name or id>"}` places that decision's slide there. |
| `blocks` | Each one of `{"p": text}`, `{"lead": text}`, `{"note": text}`, `{"points": [text or {"b", "text"}]}`, `{"facts": [{"n", "text"}]}`, `{"table": {"head", "rows"}}`, `{"picture": path, "alt", "caption"}` or `{"pictures": [{"src", "alt", "caption"}]}`. Text may use `**bold**` and `` `code` ``. |
| `decisions` | Each has its `question`, at least two `options`, which one is recommended, the one-line `reason`, and its lead-in: `why_you` (one line, at most 160 characters: why it is his to decide and not an agent's) and `evidence` (one to three lines, each at most 160 characters: the facts it rests on; one line may be given as a string). Optional: `needs_words` (the option ids whose answer needs his words, such as `["reject"]`), `context` (a line of background), `picture` (beside the options), `compare` (a large picture comparing the options), `say` (said in place of the recommendation), `short`, and, rarely, `text_only`: why its options cannot be pictured, in at least five words. |
| `options` | Each has a `name`, what it `says` and a `picture` of what he would see (required unless the decision is `text_only`). Optional: `"recommended": true` (on exactly one, or name the option in the decision's `recommended`), and `opens`: the questions asked only when this option is chosen. |

Pictures are PNG, JPEG, GIF, WebP or SVG files, given as paths beside the JSON file. An SVG must give its size: its root `<svg>` has `width` and `height`, each a positive number of pixels (`width="320" height="220"`, or `320px`). A `viewBox` alone, a percentage or a missing size is refused, on a slide and on an option alike, because such a picture has no size of its own and can draw as nothing.

### The opening and the lead-ins

The command refuses a deck that is not a briefing, naming what to write. These are the rules (`decision_package.briefing_problems`), the same for a decision package and a presentation:

- **`purpose` and `conclusion`** are written by you, both nonempty, the conclusion one sentence. They are deck/0.1's document fields of the same names.
- **The first slide is the opening.** It is a content slide of yours: not a decision, not a lead-in. Its first block is words (`lead`, `p`, `points`, `note` or a picture), never a `table` or `facts`: data comes after the opening, on slides titled with the claim it supports. It has a `say` (or `beats`), what JARVIS tells over it.
- **The opening shows the conclusion.** At least 80% of the conclusion's words (see below) are in the opening's title and blocks, so the conclusion itself or a close paraphrase of it is on the slide, not only in the metadata.
- **The opening carries the purpose.** At least 60% of the purpose's words are in its title, blocks and narration together.
- **Every decision has its lead-in**: `why_you` and `evidence` beside its `question`. The command draws them as the decision's own lead-in slide, "Before you decide: <short name>", placed just before the decision's slide: the question, why it is his, and the evidence. You never write that slide yourself; a slide of yours with `lead_in` is refused. JARVIS tells it as the first part of the decision's own story slide, so the question is never asked without it: not the first time, not when the story resumes at it, and a decision opened by another's answer has its lead-in told only when it is asked. The standard vote the command adds needs none.

The words test: both texts are split into words with accents and case folded (`spoken_words.folded_words`, so it works in Spanish as in English), `**` and `` ` `` marks dropped, and only the distinct words of four letters or more are counted (all of them when there are none that long). Order, punctuation and the short words do not matter, so "At noon we eat in the office: nobody books tables." carries "Eat in the office at noon, so nobody books a table." This 80/60 word overlap is policy: it checks that the words are present, not that he understands them. The fresh author's comprehension review, which starts from the opening slide alone, stays required for that.

An opening, for the example above:

```json
{"title": "Where should Friday's lunch be?",
 "blocks": [{"lead": "Choose lunch in the office on Friday, so nobody has to book a table."},
            {"p": "The restaurant needs a booking by Wednesday."}],
 "say": "This is about where the team eats on Friday. I suggest the office, so nobody has to book a table."}
```

Worked out for you, and never needed: the package's `name` (from the title), each decision's lead-in slide (from its `why_you` and `evidence`), its `version` (one past the highest of that name already delivered), every `id`, each decision's `short` name, the story JARVIS tells, `package.json`, the answering skill and `decide.py`.

No slide may be a wall of text. The limits are 80 characters for a title, 16 for a short name, 120 for a question, 160 for a reason, 40 for an option's name and 140 for what it says; 280 for any one line and 700 for a whole slide; and 60 words of body text on a slide, 30 words for a question, and 40 for a reason, context or what an option says. A field the deck does not know (`sais`, `recomended`) is refused with the field you probably meant. Split a long slide into two.

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

The opening rule applies here through the explanation, not through slides: the page is the kit's drawing of the checked document, whose title and content come from its revision and cannot be reworded without a new one, so the command does not check its first screen. The fresh author is told to write `UNDERSTANDING.md` in decision-memo order (what the architecture is for, the problem it serves, why it is in front of Tim now and what he will decide, before the walk-through), and its comprehension review checks that; the command checks only that the explanation is there and readable.

`validate` exits 1 and prints the kit's diagnostics while the draft does not check or leaves anything `unresolved`; such a revision cannot be sent. Do not give `readable_markdown`: the explanation is the fresh author's. `make_package --document` launches the fresh author as for any package, giving it the checked revision (its YAML, rendered page, check and coverage); the author writes `package/UNDERSTANDING.md`, the plain explanation for your handoff's audience, optionally `package/story.json` (below), and nothing else, and checks them last with `make_package package --check-only --document <document_id> --revision <revision>`. The command then projects the revision around them (the YAML, the kit's rendered page as the package's page, the check and the canonical identity, `language_projection.project`, the same projection JARVIS's own publish makes), with your handoff's `purpose` as the package's purpose (one line, at most 300 characters), binds the explanation, the story and that purpose in `canonical.json`, runs the kit's check again on it as Tim's JARVIS will, signs it with your key and the author's proof, and sends it as a message on the document's own Work item (`--work` may be left out; another item is refused). It ends with the standard vote, added once by the factory.

**Asking Tim to approve the design.** The standard vote (`approve-package`) approves the package as a whole and never counts as his approval of an exact checked revision. When you need his approval of the architecture design, add `"approval": true` to the handoff. The factory then asks it as the package's last question, in place of the standard vote, with the reserved id `approve-architecture` and the options `approve` and `request-changes` (which needs his words; he may name declarations or members): one answer for the whole design. Neither you nor the author may ask it any other way, and no other package can carry it. His answer is recorded with the exact checked revision it was given to (`document_id`, `revision`, `source_revision`, `kit_digest`) and comes back to your session on sign-off as `approve-architecture: approve, given to revision <revision> of <document_id>`. Once a newer revision of the same document is checked, or a kit whose language files (`metalang.yaml`, `arch.yaml`, `propose.yaml`) differ from those it was checked under comes into force, that answer is stale (a kit release that changes none of them leaves it standing): it is shown so and counts for nothing, and a new package of the newer revision asks again. A wait on it (a workflow's `wait_started --until package:<id>:approve-architecture`, or a work piece's `--until`) ends only when he approves the revision in force; request changes and signing the package off never end it.

Tim's JARVIS admits it only with that author proof and only when the package's explanation and story are byte for byte the ones the author checked, opens it like any page package with the explanation readable beside the page, and his answers come back to your session when he signs it off.

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

To discuss it afterwards, `python -m jarvis.language_document --as <your session id> ground --json '{"package_id": "<package id>"}'` gives back the exact canonical YAML, its revision and the author's explanation, with a `followup_reference` to quote. A change is a new revision (`validate` with `document_id` and `parent_revision`) sent the same way; the newer package replaces the open one. `retrieve` and `render` read one revision by `document_id` and `revision` (`render` also `source_revision`). There is no publish for agents.

## What the command does

1. Launches a fresh author from the handoff, then checks its source and lists everything to change at once. `--check-only` validates existing source without launching an author.
2. Builds and zips it. A deck is built by the shared deck, `examples/packages/decision-deck/`.
3. Checks it again. A deck is opened in an isolated JARVIS of its own, never Tim's: found and opened by its title, its own styles in force in the package frame, every slide fitting at both screen sizes with readable text, every picture on every slide (a content slide's, a decision's and each option's) loaded and drawn at least 60 pixels each way at both sizes, and its story told to the end. For a decision package it also checks that every vote control is visible and large enough, that the story waits at each question until it is answered (by a Choose click and by voice in turn), that Stop ends it within a second from the page and from JARVIS, and that it resumes at the first unanswered question. Every slide and step is photographed. A folder's zip is read the way Tim's JARVIS will read it, and HTML package pages go through the same image checks and screenshots for their entry and story views. The same fresh author inspects the resulting screenshots and intended attachments before acceptance.
4. Only if everything passed, signs it with your session's key (a decision package as checked, since his JARVIS opens no other) and sends it to Tim as a message carrying it (`POST /api/messages`, the request signed too), the same one way every message to him goes. The message says `--says` (else the package's kind and title) and is on the Work item `--work` names, in its Messages tab, or in his Inbox with no `--work`; while you have a piece on an open item, `--work` is required. The package is unpacked in his package inbox, and is never shown and never spoken until he opens it.

It prints the message's id, the inbox id and the package id. When he is done with it he signs it off (Done on the package, or telling JARVIS), answered or not: whatever he answered comes to your session (`--session`) as a message, unanswered questions marked unanswered, or for a package that asks nothing a message that he has reviewed it; it is read back to him first and sent on his yes. With `--workflow <id>` the answers also come back as that workflow's decisions, one per decision id (`workflow_report.py answer <id> <decision id>`), and the workflow waits on them meanwhile, its wait linked to the package so it ends itself once he signs it off. When you wait on the package yourself, link the wait to it: a workflow's `wait_started ... --until package:<inbox or package id>` (or `package:<id>:<decision id>` for one decision), or a work item's piece `--state waiting_on_tim --until package:<id>` (a work item piece waiting on him must name what it waits for, and only that answer moves it on). JARVIS ends it the moment he signs the package off, sets it aside or answers that decision; never wait on him with no link. When a newer version you send replaces the package, what waited on it waits on the newer one, and the newer one is for the same work item when you name none. A wait you placed yourself you may end yourself (`work_report.py end-wait <id> <summary> --piece <key>`, or a workflow's `wait_ended`); one JARVIS or another agent placed ends only by his answer. With `--work <id>` it is a message on that work item's Messages tab, and what he chose shows under it; a work item decision that names the package gets the answer he gave to the question of the same key, or is closed without an answer (`work_report.py answer` exits 4, as it does for the item's vote on a package he set aside or you replaced). A review package for a work item (sent with `--work <id>`) asks one question with the id `approve-item` and the options `approve`, `not-done` and `add-work` (his words go under the last two): his answer to it is his review of the item itself (approve makes it done, not-done sends it back to working with his note, add-work adds a piece in his words), recorded as if he had used the item's own Approve, Not done yet or Add work, and you are told in the answers folder. Its question never stands in the way of his approving the item; any other question you send for the item does, until he answers it. When he answers the standard vote Reject, his words saying what to change come back to you in the sign-off message as `approve-package: reject (his words: ...)`, and with `--work <id>` they are also that work item's decision `approve-package-<inbox id>`, asked and answered at once (`work_report.py answer <id> approve-package-<inbox id>`). Approve comes back as `approve-package: approve`. A newer version of a package you sent replaces the older one while it is still open. A decision package's answers are recorded in its `results/decisions.json`, at the path it prints and in `delivery.json` in its output folder. If anything fails nothing is delivered: fix what it says and run it again.
