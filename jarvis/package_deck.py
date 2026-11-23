"""The deck step of a package run (workflow.yaml deck, approved by Tim
2026-11-19; deck_review's framing approved 2026-11-20): the passed script
turned into a deck in the same swarm, built and proven by JARVIS's own
presentation pipeline, and compared slide by slide with the script.

Run after the swarm passes, in JARVIS's own process (package_jobs.PRODUCERS),
in the run's folder. A round:

  from_script      JARVIS builds every slide from the script by program
                   (script_visuals): each content slide's visuals, each
                   script piece to its presentation/1 object, laid out in
                   the first of its layouts in which every object fits its
                   slot; and each decision slide's words, its question and
                   options the script's decision's, its context the slide's
                   text pieces, its say the slide's narration and what else
                   it shows (a visual, a picture, a table, Tim's words) its
                   objects, drawn in the decision's context area; and gives
                   them to the author (`built_tag`, `BUILT_NOTE`). Before
                   each build it puts them back on each slide, the author's
                   layout and slots kept (`from_script`): the author
                   arranges, the script says what is shown (construction-
                   drafts.md "The script describes each slide's visual",
                   Tim 2026-11-22).
  write_deck       the author, in the same swarm, writes
                   `deck/presentation.yaml` (presentation/1) from the passed
                   script (script.yaml) alone, choosing JARVIS's named
                   layouts: one script slide to one deck slide, in order,
                   with the same id (a script slide that asks a decision the
                   deck's decision slide asking it, presentation/1's one
                   slide for a decision, its visuals on it); where the script gives Tim's own words
                   (a his_words piece), they are runs in the `his-words`
                   style (presentation_schema.HIS_WORDS), drawn highlighted
                   in a very bright, vibrant way, and narrated. The
                   program's words to it (`write_note`) carry the script,
                   the format, word for word from docs/making-a-package.md,
                   and a complete example. What only a change of a script
                   slide gives (another step, a node, a piece, a different
                   visual form, fewer words) the author gives in the same
                   turn: that slide of the script, whole, in `slides` (its
                   form in `forms` with why; a slide split as each new slide
                   after it, with its outline slide in `outline`); JARVIS
                   checks the script with it and changes the outline
                   (`script_change`), and the slide goes back through the
                   script stage for that slide only (`ScriptStage`: marked,
                   the outline's new slides and every owner's concern on the
                   script signed off for it); then its deck slide is written
                   again and reviewed in full (never a refusal nobody can
                   act on: a capacity refusal at the deck offers only what
                   can be done there, deck_capacity.at_the_deck).
  build_and_prove  the program fills each picture's size and hash
                   (make_package.fill_assets), compiles and proves it
                   (make_package.make_presentation: every slot's content
                   within its capacity, worked out from the layout spec at
                   662 x 489 with every word at its floor (deck_capacity),
                   and no slide of only prose over one paragraph; every
                   picture drawn readable at every frame, every word's
                   style at least the text floor at both sizes, the story's
                   targets, the renderer JARVIS's own, the story about
                   fifteen minutes at most), checks the package carries the standard vote,
                   Approve and Reject at the check mark, and checks the deck
                   against the script (`script_problems`): its slides the
                   script's, one to one, and every piece of Tim's own words
                   drawn on its slide in the his-words style and said in its
                   narration (his_words_check: the program's check, no
                   vote), and every object the script's visuals make
                   shown on its slide as the script gives it, and each
                   decision's words (script_visuals.unmatched). What does not build or check
                   is said into the conversation as JARVIS's own turn, to
                   the author.
  deck_review      the program posts, as its own turn in the same
                   conversation, slide by slide, each slide of the passed
                   script beside its deck slide's layout data (the deck
                   compiler's and the layout proof's, presentation_proof.
                   layout_data, .jarvis/layout-proof.json), and the
                   questions (DECK_QUESTIONS). The deck review is run by
                   the same owners, the same way (construction-drafts.md
                   "Cooperative swarm"): Tim's reader, the context owner,
                   the structure and decision owner and the editor, in
                   parallel, each edit the deck source (each edit by its
                   offsets in presentation.yaml, applied by the program only
                   where its old text is exactly there and only when the
                   deck still builds and proves with it; an edit of anything
                   built from the script is made in script.yaml, and the
                   slide built from it again, DeckSource) and sign off each
                   of the deck's questions but deck-readable, the
                   program's (DECK_CONCERNS); what is left is
                   merged by meaning into one list, the author works it, and
                   each owner re-checks its own items (package_swarm.
                   fix_passes).

No build is drawn in a browser (Tim 2026-11-22, construction-drafts.md
"Visuals, the salvage, and sizing": no per-package browser measurement).
Sizing comes from the language: each slot holds what deck_capacity works
out from the layout spec at the smallest frame with every word at its
floor, and content over it does not build, said to the author with the slot
and how much over and what it can do at the deck (say less, show it in
another form or give it a larger slot; a slide that needs splitting goes
back to the script stage, deck_capacity.AT_DECK_WAYS); an owner's edit
that puts a slot over is not applied. A decision's slide and the answers
slide are held to theirs the same way (deck_capacity, from the shared
deck's own layout in deck.css). A deck still over a capacity after the
author's rounds is the run's Error (NOT_FIT_FIX: the slot, the lines and
the fix, told to the Work item as every Error is), never findings for the
sender: a layout failure is never the sender's (Tim 2026-11-22). The
shared deck fits its slides itself as it draws them (deck.js fitSlide): each slide's words at the
largest size at which it fits its frame, never under the floor. Type size
is the program's: each object's capacity goes to the reviewers in its
layout data as a fact, never a question for them or the sender, and
whether every word is readable at its size (deck-readable,
PROGRAM_QUESTIONS) is answered by the build (`readable`, the review's
`program`).

The author turns dry text into visuals (Tim 2026-11-22): it is told to show
what can be shown (a sequence as numbered steps, numbers as a chart from a
dataset of the evidence's figures, a key number as a key figure, a
comparison as a comparison or a table, a warning or finding as a callout),
never long paragraphs, and is given an example that does
(make_package.VISUALS_EXAMPLE); each owner and the editor signs off that
it did (deck-shown).

Every part of the step is a unit of the step cache (step_cache.py, Tim
2026-11-22), one a slide where the work is a slide's: the deck's header
(`deck-header`: everything but its slides, keyed by the script's slide ids,
its opening slide and how it is written: the author's role, prompt and
engine choice, the deck format and the code, `writing_tool`) and each
slide's source (`deck-source`: the deck slide, its decision, the pictures
and references it names, keyed by its script slide and the same) are
written by the author only when not kept: nothing kept, the whole deck is
written; some kept, the author writes only the others and the program puts
every kept one back exactly. Both also key the sender's answers to a deck
that never built (findings filed under `WRITE_LABEL`), which are said to
the author before it writes. A kept source that no longer builds (the
pipeline changed since it was kept) is given up (`Plan.release`): the
author writes it all again and what it writes replaces the kept entries
(step_cache `replaced`), so no later run restores the broken parts. The
build (`deck-build`, keyed by the source, its pictures, the script and
JARVIS's presentation pipeline: the Python that lays out, builds, proves
and measures a deck, the shared deck's files and the presentation assets,
`pipeline_digest`) is the deck's, one program call. Each slide's review
(`deck-review`, keyed by its source, its layout data, its script slide,
what it is reviewed with,
`review_tool`, and the answers to an earlier run's findings on the review)
is done only for the slides not kept: the review turn and the fix passes
cover those alone (`flow["slides"]`), and the kept ones' reviewed source
stands. The step's record (`deck`: the review and the conversation) is
kept once it passes.

The deck passes when every deck concern is signed off; after two fix passes
without that, or a deck the author could not make build, nothing is
delivered: the sender is sent what is left (package_jobs.failed_swarm) and
its answer starts a new run. A passed deck's zip, its built folder and the
review are kept on the job (`deck`) for the deliver
step (package_deliver.py).
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

import yaml

from jarvis import (briefing_labels, data_paths, deck_capacity, decision_package as dp,
                    layout_spec, make_package, package_evidence as pe, package_inbox,
                    package_jobs, package_mark, package_script, package_swarm as ps,
                    presentation_proof, script_visuals, sessions, spoken_words, step_cache)
from jarvis.presentation_schema import HIS_WORDS, file_paths

DECK = "deck"
FOLDER = "deck"                         # the author's source, in the run's folder
SOURCE = "presentation.yaml"
BUILT = "deck-built"                    # each round's build, in the run's folder
REVIEW = "deck-review.json"
PROGRAM = sessions.PROGRAM              # the program's own turns in the conversation
ROUNDS_MAX = sessions.ROUNDS_MAX        # fix passes, as in the swarm
DOC = data_paths.SOURCE_ROOT / "docs" / "making-a-package.md"
FORMAT_START, FORMAT_END = "## A deck: one presentation.yaml", "## A folder"

# deck_review's questions, each the design's words (workflow.yaml deck
# deck_review questions). That every quote of his is drawn in his named
# style and narrated is the program's check, not a vote (his_words_check,
# Tim 2026-11-20: "Aproved").
DECK_QUESTIONS = (
    ("deck-says", "Does each slide say what the passed script says for it, nothing lost or "
                  "added?"),
    ("deck-visual", "Is each slide visual where the script calls for it, not one line of text "
                    "spoken over?"),
    ("deck-narration", "Does the narration say the substance, not read headings?"),
    ("deck-readable", "Is every word on the slide readable at the size the layout data gives "
                      "it?"),
    # Authors turn dry text into visuals (Tim 2026-11-22; first-round
    # wording, pre-approved, owner-prompts.md "Deck review: what can be shown").
    ("deck-shown", "Is what can be shown shown: a sequence as numbered steps, numbers as a chart "
                   "or key figures from the evidence, a comparison as a comparison or a table, a "
                   "warning or finding as a callout, never a long paragraph?"),
)
# Type size is the program's (Tim 2026-11-22): whether every word is
# readable at its size is answered by the build itself (deck_capacity: every
# slot's content within what it holds at 662 x 489 with every word at its
# 13 px floor; the shared deck draws it as large as the slide allows), never
# signed off by a reviewer, never a finding or a question for the sender. A
# slot over its capacity does not build and goes back to the author.
PROGRAM_QUESTIONS = ("deck-readable",)
# The deck review is run by the same owners, the same way (construction-
# drafts.md "Cooperative swarm"): each owner and the editor signs off each
# of the deck's other questions.
DECK_CONCERNS = tuple(sessions.Concern(f"{qid}:{owner}", asks, owner)
                      for qid, asks in DECK_QUESTIONS if qid not in PROGRAM_QUESTIONS
                      for owner in ps.FIXERS)
DECK_IDS = tuple(c.id for c in DECK_CONCERNS)


def _log(line: str) -> None:
    print(line, flush=True)


# ── what the author is told ───────────────────────────────────────────────

def deck_format() -> str:
    """How a deck is written, word for word from docs/making-a-package.md
    (its section "A deck: one presentation.yaml")."""
    try:
        text = DOC.read_text(encoding="utf-8")
    except OSError:
        return ""
    start = text.find(FORMAT_START)
    end = text.find(FORMAT_END, start)
    return text[start:end].strip() if start >= 0 and end > start else ""


def example() -> str:
    """A complete deck that passes (make_package --example)."""
    return yaml.safe_dump(make_package.EXAMPLE, sort_keys=False, allow_unicode=True, width=100)


def source_of(run: ps.Run) -> Path:
    return run.folder / FOLDER / SOURCE


def visuals_example() -> str:
    """A deck that shows what can be shown (make_package --example visuals)."""
    return yaml.safe_dump(make_package.VISUALS_EXAMPLE, sort_keys=False, allow_unicode=True,
                          width=100)


# What the author is told about visuals (Tim 2026-11-22, construction-drafts.md
# "Visuals, the salvage, and sizing"; first-round wording, role-prompts.md
# "Author: the deck shows what can be shown").
SHOW_NOTE = (
    "Turn dry text into visuals; never wait for the evidence to supply a picture. Show what can "
    "be shown with JARVIS's own parts: a sequence as numbered steps (steps), numbers as a chart "
    "drawn from a dataset you build from the evidence's figures, each with its source (chart, or "
    "a table from the same dataset), a key number as a key figure (figures), a comparison as a "
    "comparison layout or a table, a warning or a finding as a callout. Never put long "
    "paragraphs on a slide. Each slot holds only so much at 662 x 489 with words at their 13 px "
    "floor: JARVIS refuses a slot over its capacity, naming the slot and how much over, and a "
    "slide of only prose longer than one paragraph; at the deck, "
    + deck_capacity.AT_DECK_WAYS + ". The visuals example below shows each part.")


# What the author is told of the slides JARVIS builds from the script's
# visuals (construction-drafts.md "The script describes each slide's visual",
# first-round wording, role-prompts.md "Author: the deck from the script's
# visuals"); SHOW_NOTE stays beside it as a backstop.
BUILT_NOTE = (
    "JARVIS has built every slide from the script, below: each content slide's objects with "
    "their content (words, steps, key figures, a callout, a table, a bar, line or scatter "
    "chart, a flow, cycle, hierarchy or timeline diagram, a picture), the datasets its charts "
    "draw and the pictures it shows, laid out in the first of JARVIS's layouts in which every "
    "object fits its slot; and each decision slide's decision: its question and options, its "
    "context (the slide's words), its say (what JARVIS says over it) and what else it shows as "
    "objects, which JARVIS draws in the decision's context area above its options. Use them as "
    "given. You arrange: you may choose another layout and another slot for each object, and "
    "split the narration into cues that light each object; JARVIS puts the script's content "
    "back on every slide by its id, so a word changed in what it built is not kept: the script "
    "says what is shown, and a decision's short name is its script slide's title. On each "
    "decision you write its recommendation and its reason, the answers that need his words, "
    "and each option's picture (or why the options cannot be pictured, text_only). Write the "
    "deck's header yourself. Where a slide needs a change of the script itself (another step, "
    "a node, a piece, a different visual form, fewer words), give that slide of the script, "
    "whole, in `slides`, and a changed visual form in `forms` with why; where a slide needs "
    "splitting, give in `slides` the slide it comes from, whole, then each new slide right "
    "after it, with its outline slide in `outline`. Each goes back to the script stage for those slides only: JARVIS "
    "checks it and puts it in the script, marks it, and the owners sign their concerns off "
    "on it; then its deck slide is written again and reviewed in full.")


def built_tag(run: ps.Run) -> str:
    """The content slides JARVIS built from the passed script's visuals."""
    built = script_visuals.built_slides(package_script.read(run.script) or {})
    return sessions._tag("built", yaml.safe_dump(built, sort_keys=False, allow_unicode=True,
                                                 width=100), file=SOURCE)


def from_script(run: ps.Run) -> None:
    """The deck source with the script's objects put on each content slide
    by program (script_visuals.apply): the author's layout and slots kept
    where they take every object; written back only when it changed."""
    path = source_of(run)
    script = package_script.read(run.script)
    if not script or not path.is_file():
        return
    before = _read_source(run)
    after = script_visuals.apply(before, script, run.folder / FOLDER, run.sources(),
                                 revision=package_script.VERSION)
    if after != before:
        _write_source(run, after)


def write_note(run: ps.Run) -> str:
    """The program's words to the author for writing the deck (workflow.yaml
    deck write_deck and his_words_highlighted), and what can be shown."""
    return "\n\n".join([
        f"The script has passed. Now write the deck from the passed script alone, as one "
        f"presentation/1 source, in {source_of(run)}, with any pictures beside it as files, by "
        f"path. Choose one of JARVIS's named layouts for each slide; write no HTML or CSS. Each "
        f"slide of the script is one slide of the deck, in the same order and with the same "
        f"id: its words on the slide, what JARVIS says over it as its narration. A script slide "
        f"that asks a decision is the deck's decision slide (type decision, the same id), "
        f"asking that decision: JARVIS builds its question, options, words (its context), say "
        f"and what else it shows from the script, below, and you give its recommendation and "
        f"reason.",
        f"Where the script gives Tim's own words (a his_words piece), show them on that slide as "
        f"runs with emphasis {HIS_WORDS}, which JARVIS draws highlighted in a very bright, "
        f"vibrant way, and have JARVIS say them in its narration; never 'this is what you said' "
        f"with a block of text. JARVIS checks every one.",
        BUILT_NOTE,
        SHOW_NOTE,
        "JARVIS fills each picture's size and hash, builds the deck and proves it; what does "
        "not build comes back to you here. The owners and the editor then compare each slide "
        "with the script.",
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT),
        built_tag(run),
        sessions._tag("format", deck_format()),
        sessions._tag("example", example(), file=SOURCE),
        sessions._tag("visuals", visuals_example(), file=SOURCE)])


def revise_note(run: ps.Run) -> str:
    return (f"Revise the deck in {source_of(run)}, or answer each point raised. Where a point "
            f"needs a slide of the script changed (another step, a node, a piece, a different "
            f"visual form, fewer words), give that slide, whole, in `slides` (a changed form in "
            f"`forms` with why); where a slide needs splitting, give in `slides` the slide it "
            f"comes from, whole, then each new slide right after it, with its outline slide in "
            f"`outline`. Each goes back "
            f"to the script stage for those slides only, then its deck slide is written again "
            f"and reviewed in full. Say what you changed and why.")


def rewrite_note(run: ps.Run, slides: list[str]) -> str:
    """The author's words once slides went through the script stage at the
    deck: their deck slides are written again from the script as it stands
    now; every other deck slide stays as it is."""
    return "\n\n".join([
        f"The script stage passed slide(s) {', '.join(slides)}. Revise the deck in "
        f"{source_of(run)}: write those deck slides again from the script as it stands now "
        f"(JARVIS has built them, below), each script slide one deck slide, in the same order "
        f"and with the same id; leave every other deck slide as it is. Say which slides you "
        f"wrote.",
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT),
        built_tag(run)])


def revise_deck_note(run: ps.Run, slides: list[str], kept: list[str]) -> str:
    """The author's words when only some deck slides are written: the deck
    source holds the slides kept; only the others are written."""
    return "\n\n".join([
        f"Revise the deck in {source_of(run)}: the deck source there holds every slide whose "
        f"script slide did not change ({', '.join(kept) or 'none'}), which stay exactly as "
        f"they are (JARVIS puts them back). Write only the deck slides whose script slide "
        f"changed (the script changed on: {', '.join(slides) or 'none'}). Each script slide is "
        f"still one deck slide, in the same order and with the same id. Say which slides you "
        f"changed.",
        BUILT_NOTE,
        SHOW_NOTE,
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT),
        built_tag(run),
        sessions._tag("format", deck_format()),
        sessions._tag("example", example(), file=SOURCE),
        sessions._tag("visuals", visuals_example(), file=SOURCE)])


# Where the deck step stops when the deck is still over a slot's capacity
# after the author's rounds (Tim 2026-11-22: a layout failure is the
# program's, never the sender's).
NOT_FIT_FIX = ("Each slot named must hold its words at 662 x 489 with every word at its 13 px "
               "floor (jarvis/deck_capacity.py, from presentation_assets/layout-spec.json and the "
               "shared deck's deck.css): say less in it, show it in another form or give it a "
               "larger slot, or split the slide in the script stage, as each problem says. A new "
               "run of the package (make_package --rerun) writes the deck again from the passed "
               "script.")
_CAPACITY = re.compile(r"^capacity: .*?:\d+:\d+: (.*)$", re.M)


def over_capacity(problems: list[str]) -> list[str]:
    """The build's problems that are a slot, a decision's slide or the
    answers slide over its capacity (deck_capacity), each in its words."""
    return [m.group(1) for p in problems for m in _CAPACITY.finditer(p)]


def _not_fit(run: ps.Run, kept: dict, state: dict):
    """The run's Error: the deck is still over a capacity after the
    author's rounds."""
    ps._save(run, kept)
    raise package_jobs.Blocked(
        DECK, "The deck is still over what its slides hold after the author's revisions: "
              + " ".join(state.get("unfit") or []),
        f"JARVIS worked out each slot's capacity, each decision's slide's and the answers "
        f"slide's at 662 x 489 with every word at its floor, said what was over to the author "
        f"with the slot, the lines and how to fix it, and had it revise the deck up to "
        f"{ROUNDS_MAX + 1} times.", NOT_FIT_FIX)


def _findings_of_build(problems: list[str]) -> list[dict]:
    """A deck that never built, as the one finding the sender is sent."""
    return [{"id": 1, "what": "The deck never built and proved: " + " ".join(problems),
             "raised_by": [PROGRAM], "concerns": [], "fixes": [], "evidence_missing": []}]


def build_problems_note(problems: list[str]) -> str:
    return "The deck did not build. Revise it:\n" + "\n".join(f"- {p}" for p in problems)


# ── build and prove ───────────────────────────────────────────────────────

class Built:
    def __init__(self, archive: Path, folder: Path, proof: dict, deck: dict):
        self.archive, self.folder, self.proof, self.deck = archive, folder, proof, deck


def _words(text: str) -> list[str]:
    return spoken_words.folded_words(text)


def _within(part: list[str], whole: list[str]) -> bool:
    return bool(part) and any(whole[i:i + len(part)] == part
                              for i in range(len(whole) - len(part) + 1))


def script_problems(script: dict, deck: dict) -> list[str]:
    """What the program checks of the deck against the passed script
    (workflow.yaml deck): one script slide to one deck slide, in order, by
    id (write_deck); and each piece of Tim's own words drawn on its slide in
    the his-words style and said in its narration (his_words_check), its
    words as they are, in order (on a decision slide, among its objects and
    in its say); and everything the script's slides make shown on their deck
    slides as the script gives it (script_visuals.unmatched: the deck only
    arranges)."""
    out = []
    want = [s.get("id") for s in script.get("slides") or [] if isinstance(s, dict)]
    have = [s.get("id") for s in deck.get("slides") or [] if isinstance(s, dict)]
    if want != have:
        out.append(f"The deck's slides are {have}; the script's are {want}. Write one deck "
                   f"slide for each script slide, in the same order, with the same id; a "
                   f"script slide that asks a decision is the deck's decision slide.")
    slides = {s.get("id"): s for s in deck.get("slides") or [] if isinstance(s, dict)}
    decisions = {d.get("id"): d for d in deck.get("decisions") or [] if isinstance(d, dict)}
    for wanted in script.get("slides") or []:
        slide = slides.get(wanted["id"])
        if slide is None:
            continue
        asked = (wanted.get("decision") or {}).get("id")
        if asked and (slide.get("type") != "decision" or slide.get("decision") != asked):
            out.append(f"Script slide {wanted['id']} asks the decision {asked}: its deck slide "
                       f"is a decision slide (type decision, id {wanted['id']}) asking "
                       f"{asked}, with its recommendation and reason; JARVIS puts its "
                       f"question, options, words, say and what it shows on it.")
        elif not asked and slide.get("type") != "content":
            out.append(f"Script slide {wanted['id']} asks no decision: its deck slide is a "
                       f"content slide.")
    for quote in package_script.his_words(script):
        slide = slides.get(quote["slide"]) or {}
        decision = decisions.get(slide.get("decision")) if slide.get("type") == "decision" \
            else None
        objects = (decision or {}).get("objects") if decision is not None \
            else slide.get("objects")
        drawn = " ".join(run.get("text", "") for obj in objects or []
                         for para in obj.get("paragraphs") or []
                         for run in para.get("runs") or [] if run.get("emphasis") == HIS_WORDS)
        said = (decision or {}).get("say") or "" if decision is not None else \
            " ".join(c.get("say", "") for c in slide.get("narration") or [])
        words = _words(quote["text"])
        if not _within(words, _words(drawn)):
            out.append(f"Slide {quote['slide']} does not draw Tim's words {quote['text']!r} in "
                       f"the {HIS_WORDS} style.")
        if not _within(words, _words(said)):
            out.append(f"Slide {quote['slide']}'s narration does not say Tim's words "
                       f"{quote['text']!r}.")
    return out + script_visuals.unmatched(script, deck)


HEADER, SLIDE_SOURCE, BUILD, SLIDE_REVIEW, STEP_UNIT = (
    "deck-header", "deck-source", "deck-build", "deck-review", "deck")
REVIEW_LABEL = SLIDE_REVIEW            # the stage an earlier run's findings answer
# Findings on a deck that never built are the writing's: their answers are
# said before the author writes and are in every deck-header and
# deck-source key, so the author writes again with them.
WRITE_LABEL = SLIDE_SOURCE

# The Python that lays out, sizes, builds and proves a deck (in the build
# key, whole), and the shared deck's own files.
PIPELINE_CODE = ("jarvis.presentation", "jarvis.presentation_source",
                 "jarvis.presentation_schema", "jarvis.presentation_proof",
                 "jarvis.presentation_data", "jarvis.deck_capacity",
                 "jarvis.picture_proof", "jarvis.decision_package",
                 "jarvis.layout_spec", "jarvis.make_package")
# The code that shapes what the author and the reviewers write.
DECK_CODE = ("jarvis.package_deck", "jarvis.script_visuals")


def format_digest() -> str:
    """How a deck is written, as the author is told it: the format and the
    example."""
    return step_cache.digest([deck_format(), example()])


def pipeline_digest() -> str:
    """JARVIS's presentation pipeline as a build reads it: the Python that
    lays out, sizes, builds and proves a deck (PIPELINE_CODE, each module's
    source), the shared deck's files (examples/packages/decision-deck, its
    new_deck.py among them) and the presentation assets (the layout spec,
    the CSS and JS), and the renderer files a built deck carries."""
    return step_cache.digest({"code": step_cache.code(*PIPELINE_CODE),
                              "deck": step_cache.tree_sha(dp.BUILDER.parent),
                              "assets": step_cache.tree_sha(layout_spec.ASSETS),
                              "templates": presentation_proof.template_hashes()})


def writing_tool(doc: dict, kept: Optional[dict] = None) -> dict:
    """What the deck source is written with: the author's role, prompt and
    engine choice (the model `kept` runs it on in this run, ps._had), the
    format it is told and the code."""
    had = ps._had(kept).get(ps.AUTHOR)
    return {"author": step_cache.role_tool(ps.AUTHOR, doc, had=had),
            "format": format_digest(),
            "code": step_cache.code(*step_cache.MODEL_CODE, *ps.SWARM_CODE, *DECK_CODE)}


def review_tool(doc: dict, kept: Optional[dict] = None) -> dict:
    """What each slide is reviewed with: every participant's role, prompt
    and engine choice (the model `kept` runs each on, ps._had), the
    deck's concerns and questions, the labels and the code
    (package_swarm.tool_of)."""
    return {**ps.tool_of(doc, more=DECK_CONCERNS, kept=kept),
            "questions": [list(q) for q in DECK_QUESTIONS],
            "program": list(PROGRAM_QUESTIONS),
            "code": step_cache.code(*step_cache.MODEL_CODE, *ps.SWARM_CODE, *DECK_CODE)}


def _ids_in(value) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            out |= _ids_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= _ids_in(v)
    return out


def parts_of(source: dict) -> tuple[dict, dict[str, dict]]:
    """A deck source split into its header (everything but its slides, their
    decisions and the pictures and references a slide names) and one part a
    slide: {"slide", "decision", "assets", "references"}."""
    decisions = {d.get("id"): d for d in source.get("decisions") or [] if isinstance(d, dict)}
    named: set[str] = set()
    parts: dict[str, dict] = {}
    for slide in source.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        decision = decisions.get(slide.get("decision")) if slide.get("decision") else None
        said = _ids_in(slide) | _ids_in(decision)
        part = {"slide": slide, "decision": decision}
        for key in ("assets", "references"):
            part[key] = [x for x in source.get(key) or [] if isinstance(x, dict)
                         and x.get("id") in said]
            named |= {x.get("id") for x in part[key]}
        parts[slide.get("id")] = part
    header = {k: v for k, v in source.items() if k not in ("slides", "decisions")}
    for key in ("assets", "references"):
        if key in header:
            header[key] = [x for x in header[key] if not (isinstance(x, dict)
                                                         and x.get("id") in named)]
    return header, parts


def assemble(header: dict, parts: list[dict]) -> dict:
    """A deck source from its header and its slides' parts, in order."""
    out = {k: v for k, v in header.items() if k not in ("assets", "references")}
    for key in ("assets", "references"):
        seen, merged = set(), []
        for x in list(header.get(key) or []) + [x for p in parts for x in p.get(key) or []]:
            if x.get("id") not in seen:
                seen.add(x.get("id"))
                merged.append(x)
        if key in header or merged:
            out[key] = merged
    out["slides"] = [p["slide"] for p in parts]
    out["decisions"] = [p["decision"] for p in parts if p.get("decision")]
    return out


def _read_source(run: ps.Run) -> dict:
    try:
        found = yaml.safe_load(source_of(run).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return found if isinstance(found, dict) else {}


def _write_source(run: ps.Run, source: dict) -> None:
    pe.write_file(source_of(run), yaml.safe_dump(source, sort_keys=False, allow_unicode=True)
                  .encode("utf-8"))


def _files_of(run: ps.Run, value: dict) -> list[str]:
    """The files beside the deck source that a source, its header or a
    slide's part names in its path fields (presentation_schema.file_paths:
    asset paths, file references' locators); no other string is a path."""
    folder = run.folder / FOLDER
    out = set()
    for v in file_paths(value):
        rel = Path(v)
        if not v or rel.is_absolute() or ".." in rel.parts or v == SOURCE:
            continue
        try:
            if (folder / rel).is_file():
                out.add(v)
        except OSError:
            continue
    return sorted(out)


def build_unit(run: ps.Run) -> step_cache.Unit:
    """The deck's build: its source as written, the files it names, the
    passed script it is checked against, JARVIS's pipeline and the
    language it is built in (make_package.built_language: a deck or story
    that names none is built, and its vote worded, in it)."""
    source = _read_source(run)
    return step_cache.Unit(BUILD, "", {
        "source": step_cache.file_sha(source_of(run)),
        "files": step_cache.files_sha(run.folder / FOLDER, _files_of(run, source)),
        "script": step_cache.file_sha(run.script), "pipeline": pipeline_digest(),
        "language": make_package.built_language()})


def _compile(run: ps.Run, out: Path) -> list[str]:
    """The source filled, compiled and proven into `out`; the problems."""
    source = source_of(run)
    try:
        with deck_capacity.at_the_deck():
            make_package.refuse_json_deck(source)
            make_package.fill_assets(source)
            make_package.make_presentation(source, out)
    except dp.Refused as e:
        return [str(p) for p in e.problems]
    except (OSError, ValueError, yaml.YAMLError) as e:
        return [str(e)]
    return []


def build_and_prove(run: ps.Run, round_no: int) -> tuple[Optional[Built], list[str]]:
    """The author's source filled, compiled and proven by JARVIS's own
    presentation pipeline (make_package.make_presentation), into
    `deck-built/<round>/`, reused from the step cache when the same source
    was built before; (the build, []) or (None, every problem)."""
    source = source_of(run)
    if not source.is_file():
        return None, [f"There is no {SOURCE} at {source}."]
    from_script(run)
    out = run.folder / BUILT / str(round_no)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    unit = build_unit(run)
    if step_cache.reuse(unit, run.doc, out) is None:
        began = time.time()
        problems = _compile(run, out)
        if problems:
            return None, problems
        step_cache.computed(unit, run.doc, began, folder=out, files=["compiled"] + [
            p.name for p in out.glob("*.zip")])
    try:
        archive = next(out.glob("*.zip"))
        folder = next((out / "compiled").iterdir())
        proof = json.loads((folder / presentation_proof.PROOF_FILE).read_text(encoding="utf-8"))
        manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        deck = dp.read(folder / "deck-data.js")
    except (OSError, ValueError, StopIteration, dp.Refused) as e:
        return None, [f"The built deck could not be read back: {e}"]
    if manifest.get("vote") != package_inbox.VOTE_ID:
        return None, ["The built deck does not carry Tim's vote, Approve and Reject at the "
                      "check mark."]
    against = script_problems(package_script.read(run.script) or {},
                              yaml.safe_load(source.read_text(encoding="utf-8")) or {})
    if against:
        return None, against
    return Built(archive, folder, proof, deck), []


# ── the review ────────────────────────────────────────────────────────────

def review_turn(run: ps.Run, built: Built, only: Optional[list[str]] = None) -> str:
    """The program's own turn in the conversation: slide by slide, each
    slide of the passed script beside the layout data of its deck slide,
    then the questions (workflow.yaml deck deck_review how). With `only` (a
    revision), only those slides."""
    script = dict(package_script.read(run.script) or {})
    if only:
        script["slides"] = [s for s in script.get("slides") or [] if s.get("id") in only]
    layout = {s.get("id"): s for s in built.proof.get("slides") or []}
    slides = "\n".join(sessions._tag("slide", "\n".join([
        sessions._tag("script", package_script.dump(s), file=ps.SCRIPT),
        sessions._tag("layout", json.dumps(layout.get(s["id"]), indent=1, ensure_ascii=False),
                      file=presentation_proof.PROOF_FILE)]), id=s["id"])
        for s in script.get("slides") or [])
    return "\n\n".join([
        "The deck written from the passed script is built and proven, and every quote of Tim's "
        "own words is drawn in his style and narrated. Below, slide by slide, each slide of the "
        "passed script beside the layout data JARVIS computed for its deck slide as it compiled "
        "and proved it (its text and pictures with their positions and sizes at the smallest "
        "and the largest frame, in deck units on the 880 x 640 canvas and in pixels) and its "
        "narration, and each object's capacity: the lines of words its slot holds at 662 x 489 "
        "with every word at its 13 px floor and the lines it needs (JARVIS refuses a slot over "
        "its capacity, so every word fits; the deck draws each slide's words as large as the "
        "slide allows). Type size is the program's, a fact here: it is not for the review or "
        "the sender, and whether every word is readable at its size is answered by the build. "
        "No screenshots. Compare each slide with the script, and see that what can be shown "
        "is shown (steps, a chart or key figures, a comparison or a table, a callout), not a "
        "long paragraph. A decision slide shows its objects in its context area, above its "
        "options. Where a slide needs more than its words changed (another step, a node, a "
        "piece, a different visual form, fewer words, or a split into more slides), say it as "
        "an item: the author changes that slide of the script, it goes back through the script "
        "stage for that slide, and only that slide is written and reviewed again.",
        *([f"Only the slides this revision changed are below ({', '.join(only)}); every "
           f"other slide of the deck is as the run before made it and was reviewed then."]
          if only else []),
        slides,
        "The deck's questions, by id:\n" + "\n".join(
            sessions._tag("question", asks, id=qid) for qid, asks in DECK_QUESTIONS
            if qid not in PROGRAM_QUESTIONS)])


def readable(proof: dict) -> dict:
    """The program's answer to whether every word is readable at its size
    (PROGRAM_QUESTIONS): the build's, every object within its slot's
    capacity at 662 x 489 with every word at its floor (deck_capacity), so
    the deck draws every word at the floor at least."""
    floor = layout_spec.spec()["text"]["min_px"]
    rooms = [o["capacity"] for s in proof.get("slides") or [] for o in s.get("objects") or []
             if o.get("capacity")]
    ok = bool(proof.get("proven")) and all(r["fits"] for r in rooms)
    return {"question": "deck-readable", "by": PROGRAM, "state": "resolved" if ok else "not_yet",
            "reason": (f"Every slot's words are within its capacity at 662 x 489 with every word "
                       f"at the {floor} px floor, so the deck draws each slide's words as large "
                       f"as the slide allows and never under {floor} px." if ok else
                       "A slot is over its capacity or the deck is not proven.")}


class DeckSource:
    """The author's deck source as the owners work on it (sessions.Script):
    each edit by its offsets in presentation.yaml, applied by the program
    only where its old text is exactly there (sessions.apply_edits); edits
    with which the deck no longer builds and proves are not applied.

    The script is the one source of what a slide shows: an edit of anything
    JARVIS builds from it (an object's words or data, a slide's title) is
    made in the script (script_visuals.script_edits: each built word's
    origin in script.yaml, applied as the swarm's owners' edits are,
    package_script.ScriptFile, so the script still checks) and the slide is
    built from it again; it is applied only once it is in the script. An
    edit of the arrangement (a layout, a slot, the narration's cues, the
    header, a decision) stays an edit of the deck. One that can be neither
    is refused with why (`why`)."""

    key = "deck"
    version = "presentation/1"
    form: dict = {}

    def __init__(self, run: ps.Run, built: Callable[[], Optional["Built"]]):
        self.run = run
        self.path = source_of(run)
        self.name = SOURCE
        self.edit_form = sessions.EDIT
        self._build = built

    def text(self) -> str:
        return ps._read(self.path)

    def _sorted(self, before: str, edits: list[dict]
                ) -> tuple[list[dict], list[tuple[dict, list[dict]]], list[dict]]:
        """Each edit as an edit of the deck (it changes only the
        arrangement), an edit of the script (with its edits there), or
        refused (old text not there, or not one the script can take)."""
        script = package_script.read(self.run.script) or {}
        deck, scripted, refused = [], [], []
        for edit in edits:
            alone, done, _ = sessions.apply_edits(before, [edit])
            if not done:
                refused.append(edit)
                continue
            try:
                edited = yaml.safe_load(alone)
            except yaml.YAMLError as error:
                refused.append({**edit, "why": f"the deck source would not read: {error}"})
                continue
            if not isinstance(edited, dict):
                refused.append({**edit, "why": "the deck source would not read."})
                continue
            moved, why = script_visuals.script_edits(script, edited)
            if why:
                refused.append({**edit, "why": " ".join(why)})
            elif moved:
                scripted.append((edit, [{**m, "reason": edit.get("reason", ""),
                                         "source": edit.get("source", "")} for m in moved]))
            else:
                deck.append(edit)
        return deck, scripted, refused

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        before = self.text()
        script_file = self.run.script_file()
        script_before = script_file.text()
        deck, scripted, refused = self._sorted(before, edits)
        applied: list[dict] = []
        for edit, moved in scripted:
            held = script_file.text()
            _, done, not_done = script_file.edit(moved)
            if done and not not_done:
                applied.append({**edit, "in": ps.SCRIPT})
                continue
            if done:
                pe.write_file(script_file.path, held.encode("utf-8"))
            refused.append({**edit, "why": "its change of the script was not applied (old text "
                                           "not exactly there, or the script would not check)."})
        after, done, not_done = sessions.apply_edits(before, deck)
        applied += done
        refused += not_done
        if not applied:
            return before, [], refused
        if after != before:
            pe.write_file(self.path, after.encode("utf-8"))
        if self._build() is None:
            pe.write_file(self.path, before.encode("utf-8"))
            if script_file.text() != script_before:
                pe.write_file(script_file.path, script_before.encode("utf-8"))
            self._build()
            return before, [], refused + applied
        return self.text(), applied, refused


# ── a change of the script at the deck ────────────────────────────────────
# What only a change of a script slide gives (another step, a node, a piece,
# a different visual form, fewer words) is never a dead end at the deck:
# the author gives that slide of the script, whole, in the same turn it
# writes the deck (`slides`, a changed form in `forms` with why), and a slide
# that needs splitting as the slide it comes from, whole, then each new slide
# right after it, with its outline slide (`outline`; `_placed`). JARVIS checks the script with them
# (package_script.ScriptFile: each slide's visuals, values, its decision
# slide's fit as the deck will draw it, and the outline's form, which it
# changes) and puts them in the script; then those slides go back through
# the script stage, for them only (`ScriptStage`): marked against the
# labels (mark_slides for those slides), the outline's new slides signed off
# by the owners, and every owner's concern on the script re-signed for them
# in the same swarm (package_swarm.fix_passes, its closes and the sender's
# questions as in the swarm); then their deck slides are written again and
# reviewed in full by every owner and the editor. The step cache keeps what
# it made (`deck-script`: each slide the swarm passed as the deck step left
# it, the slides it became, their outline slides and marks, keyed by the
# slide as passed), so a later run with the same inputs takes it back.
SCRIPT_CHANGE = "deck-script"
SCRIPT_STAGE = "deck_script_stage"     # the stage under way, in the step's kept state
SCRIPT_PREFIX = "deck-script-"         # its stages and sign-offs in the conversation
SLIDES_WORDS = ("Each slide of the script that needs more than its words changed (another "
                "step, a node, a piece, a different visual form, fewer words), as it should "
                "be, whole, in script/1, with its own id; and, where a slide needs splitting, "
                "the slide it comes from, whole, then each new slide right after it, with a "
                "new id. Empty when "
                "none does. JARVIS checks the script with them and puts them in the script; "
                "they go back through the script stage, then their deck slides are written "
                "again.")
OUTLINE_WORDS = ("The outline slide (outline/1) of each new slide in `slides`, with the same "
                 "id: its title, its one point, the question it answers, its evidence and its "
                 "visual with why. Empty when no slide is new.")


def author_form(run: ps.Run) -> dict:
    """The author's turn at the deck: what it says, each script slide it
    changes or adds (`slides`), each visual form it changes (`forms`) and
    each new slide's outline slide (`outline`)."""
    qids = ps.question_ids(run.doc)
    slide = package_script.schema(qids)["properties"]["slides"]["items"]
    line = package_script.outline_schema(qids)["properties"]["slides"]["items"]
    return sessions._object({**ps.TALK["properties"],
                             "slides": {"type": "array", "items": slide,
                                        "description": SLIDES_WORDS},
                             "forms": package_script.FORMS,
                             "outline": {"type": "array", "items": line,
                                         "description": OUTLINE_WORDS}})


def _placed(now: list[dict], given: list[dict], key: str = "id"
            ) -> tuple[list[dict], list[str], list[str]]:
    """`now` with each of `given` in place of its own slide, and each one
    `now` does not have right after the one given before it: (the slides,
    the new ids, what cannot be placed)."""
    slides = [dict(x) for x in now]
    have = [x.get(key) for x in slides]
    new, problems, before = [], [], None
    for x in given:
        sid = x.get(key)
        if sid in have and sid not in new:
            slides[have.index(sid)] = x
        elif sid in new:
            problems.append(f"slides: {sid!r} is given twice.")
        elif before is None:
            problems.append(f"slides: {sid!r} is a new slide, so it comes right after the slide "
                            f"it is split from: give that slide first, whole, then the new one.")
        else:
            at = have.index(before) + 1
            slides.insert(at, x)
            have.insert(at, sid)
            new.append(sid)
        before = sid
    return slides, new, problems


def script_change(run: ps.Run, answer, only: Optional[list[str]] = None
                  ) -> tuple[list[str], list[dict], list[str]]:
    """The script slides the author gave at the deck put in the script, a
    new one right after the slide given before it, with its outline slide
    put in the outline after that one's, checked whole with the outline's
    forms changed as it says (package_script.ScriptFile, which writes
    nothing that does not check; each decision slide sized with the
    author's part of it from the deck source): (the slides that changed or
    were added, the form changes made, what did not check). With `only`
    (the script stage's close), only those slides and new ones."""
    answer = answer if isinstance(answer, dict) else {}
    given = [x for x in answer.get("slides") or [] if isinstance(x, dict)]
    forms = [c for c in answer.get("forms") or [] if isinstance(c, dict)]
    lines = [x for x in answer.get("outline") or [] if isinstance(x, dict)]
    if not given and not forms:
        return [], [], ([f"outline: {x.get('id')!r} is not a new slide in `slides`; change an "
                         f"existing slide's visual form in `forms`." for x in lines])
    now = package_script.read(run.script) or {}
    ids = package_script.slide_ids(now)
    if only is not None:
        outside = [str(x.get("id")) for x in given if x.get("id") in ids
                   and x.get("id") not in only]
        if outside:
            return [], [], [f"slides: only {', '.join(only)} are in the script stage now; "
                            f"{i!r} is not." for i in outside]
    slides, new, problems = _placed(now.get("slides") or [], given)
    by_line = {x.get("id"): x for x in lines}
    problems += [f"outline: the new slide {i!r} needs its outline slide, with the same id."
                 for i in new if i not in by_line]
    problems += [f"outline: {i!r} is not a new slide in `slides`; change an existing slide's "
                 f"visual form in `forms`." for i in by_line if i not in new]
    if problems:
        return [], [], problems
    outline_before = run.outline.read_text(encoding="utf-8") if run.outline.is_file() else None
    if new:
        outline = package_script.read(run.outline) or {}
        anchors = {sid: slides[n - 1].get("id") for n, x in enumerate(slides)
                   for sid in [x.get("id")] if sid in new}
        olines = [dict(x) for x in outline.get("slides") or [] if isinstance(x, dict)]
        for sid in new:
            have = [x.get("id") for x in olines]
            at = have.index(anchors[sid]) + 1 if anchors[sid] in have else len(olines)
            olines.insert(at, by_line[sid])
        outline = {**outline, "slides": olines}
        found = package_script.outline_problems(outline, ps.question_ids(run.doc))
        if found:
            return [], [], found
        package_script.write(run.outline, outline)
    work = run.script_file(deck=source_of(run))
    problems = work.take({"script": {**now, "slides": slides}, "forms": forms})
    if problems:
        if new and outline_before is not None:
            pe.write_file(run.outline, outline_before.encode("utf-8"))
        return [], [], problems
    return package_script.changed_slides(now, work.read()), list(work.form_changes), []


class _OutlineAtDeck(package_script.OutlineFile):
    """The outline as the owners sign its new slides off at the deck: an
    owner's edit stands only while the outline checks and the script still
    checks against it."""

    def __init__(self, run: ps.Run):
        super().__init__(run.outline, ps.question_ids(run.doc))
        self.run = run

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        before = self.text()
        text, applied, refused = super().edit(edits)
        if applied and self.run.script_file(deck=source_of(self.run))._check(
                package_script.read(self.run.script) or {}):
            pe.write_file(self.path, before.encode("utf-8"))
            return before, [], refused + applied
        return text, applied, refused


class _StageScript(package_script.ScriptFile):
    """The script as the owners work on it in the script stage at the deck:
    an edit stands only on a slide in the stage (`only`)."""

    def __init__(self, run: ps.Run, only: Callable[[], list[str]]):
        super().__init__(run.script, ps.question_ids(run.doc), sources=run.sources(),
                         outline=run.outline, deck=source_of(run))
        self.only = only

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        ids = set(self.only())
        mine = [e for e in edits if e.get("slide") in ids]
        others = [{**e, "why": f"only {', '.join(sorted(ids))} are in the script stage now."}
                  for e in edits if e.get("slide") not in ids]
        if not mine:
            return self.text(), [], others
        text, applied, refused = super().edit(mine)
        return text, applied, refused + others


class StageEnded(Exception):
    """The script stage at the deck waits on the sender (`waiting`), or
    ended without its concerns signed off (`not passed`), or with the
    author's slides never checking (`unchecked`), with its findings."""

    def __init__(self, end: str, findings: Optional[list[dict]] = None):
        super().__init__(end)
        self.end, self.findings = end, findings or []


class ScriptStage:
    """A slide changed at the deck back through the script stage, for the
    slides changed only (workflow.yaml deck: what needs the script goes back
    to it for that slide): marked against the labels, the outline's new
    slides signed off by the owners, every owner's concern on the script
    re-signed for those slides in the swarm (package_swarm.fix_passes over
    SCRIPT_CONCERNS: the owners edit and sign off, what is left goes to the
    author as one list, the owners re-check; the sender asked as in the
    swarm). Its state is kept in the step's (`SCRIPT_STAGE`), so a run that
    waits on the sender goes on from where it stopped."""

    def __init__(self, run: ps.Run, swarm: sessions.BrainSwarmSession, kept: dict,
                 clock: ps.Clock):
        self.run, self.swarm, self.kept, self.clock = run, swarm, kept, clock
        self.work = _StageScript(run, self.slides)

    @property
    def state(self) -> Optional[dict]:
        return self.kept.get(SCRIPT_STAGE)

    def begin(self, base: dict, outline_base: dict, why: str) -> None:
        now = package_script.read(self.run.script) or {}
        added = [i for i in package_script.slide_ids(now)
                 if i not in package_script.slide_ids(base)]
        self.kept[SCRIPT_STAGE] = {
            "base": base, "outline_base": outline_base, "why": why, "added": added,
            "step": "outline" if added else "mark",
            "flow": {"stage": f"{SCRIPT_PREFIX}pass", "pass": 1}}

    def slides(self) -> list[str]:
        """The slides in the stage: those that differ from the script as it
        was when the stage began, as the script stands now."""
        now = package_script.read(self.run.script) or {}
        present = set(package_script.slide_ids(now))
        return [i for i in package_script.changed_slides(self.state["base"], now)
                if i in present]

    async def mark(self) -> None:
        """The stage's slides marked against the labels (MarkingSession,
        mark_slides for those slides only); every other slide's marks kept."""
        sids = set(self.slides())
        now = self.work.read()
        units = [u for u in package_script.units(now) if u.slide in sids]
        present = set(package_script.slide_ids(now))
        keep = [m for m in self.kept.get("marked") or []
                if str(m.get("id", "")).split("/")[0] in present - sids]
        marking = package_mark.MARKING(self.run.doc, **ps.marking_pin(self.run.doc, self.kept))
        with self.clock(f"{SCRIPT_PREFIX}mark"):
            fresh = await marking.mark_slides(
                units, self.work.text(), briefing_labels.for_marking(),
                package_mark.tim_known(self.run.rows, self.run.texts), ps.TIMEOUT) if units else []
        self.kept["marked"] = keep + list(fresh)

    def given(self, name: str) -> str:
        sids = self.slides()
        state = self.state
        out = [f"Script slide(s) {', '.join(sids)} changed at the deck ({state['why']}). This "
               f"is the script stage for those slides only: check them, for your concerns, "
               f"against the script as it is now, and sign off; every other slide was signed "
               f"off before and stays as it is."]
        if state.get("added"):
            outline = package_script.read(self.run.outline) or {}
            lines = [x for x in outline.get("slides") or [] if isinstance(x, dict)
                     and x.get("id") in state["added"]]
            out.append(sessions._tag("outline", package_script.dump(
                {"version": outline.get("version"), "slides": lines}), file=ps.OUTLINE))
        out.append(ps._work_text(name, self.run, self.work, list(self.kept.get("marked") or []),
                                 self.kept))
        return "\n".join(out)

    async def revise(self, items: list[dict]) -> bool:
        """The author's close in the stage: each of its slides it changes
        (and any slide it splits) given whole, checked and put in the
        script; at most ROUNDS_MAX more times."""
        note = ("Work through the list above, item by item, for script slide(s) "
                f"{', '.join(self.slides())} only: change them or say why not. Give each slide "
                f"you change, whole, in `slides` (a new slide right after the one it is split "
                f"from, with its outline slide in `outline`), and a changed visual form in "
                f"`forms` with why.")
        for attempt in range(ROUNDS_MAX + 1):
            answer = await self.swarm.answer(ps.AUTHOR, ps.TIMEOUT, note if attempt == 0 else
                                             "Give them again, whole.", author_form(self.run))
            self.swarm.ask(ps.AUTHOR, answer, f"{SCRIPT_PREFIX}close")
            _, forms, problems = script_change(self.run, answer, only=self.slides())
            if not problems:
                ps.forms_changed(self.swarm, forms, f"{SCRIPT_PREFIX}close")
                ps.form_item(items, forms)
                added = [i for i in package_script.slide_ids(package_script.read(self.run.script))
                         if i not in package_script.slide_ids(self.state["base"])]
                self.state["added"] = added
                return True
            self.state["problems"] = problems
            self.swarm.say(PROGRAM, "The script slides in your answer do not check; the script "
                                    "stays as it was:\n" + "\n".join(f"- {p}" for p in problems))
        return False

    async def outline_signoff(self) -> None:
        """The outline's new slides signed off by the owners for their
        concerns (each may edit the outline; an edit stands only while the
        script still checks against it)."""
        added = self.state["added"]
        with self.clock(f"{SCRIPT_PREFIX}outline"):
            await ps.owners_turn(
                self.swarm, ps.OWNERS, ps.OUTLINE_CONCERNS, _OutlineAtDeck(self.run),
                f"{SCRIPT_PREFIX}outline", lambda name: (
                    f"The outline gained slide(s) {', '.join(added)} at the deck, split from "
                    f"the slide before each. Sign the outline off for your concerns on those "
                    f"slides; every other outline slide was signed off before.\n"
                    + ps._work_text(name, self.run, self.run.outline_file(), [], self.kept)))

    async def go(self) -> list[str]:
        """The stage from where it stands: the slides it passed. Raises
        StageEnded when it waits on the sender or does not pass."""
        state = self.state
        if state["step"] == "outline":
            await self.outline_signoff()
            state["step"] = "mark"
            ps._keep(self.run, self.swarm, self.kept)
        if state["step"] == "mark":
            await self.mark()
            state["step"] = "passes"
            ps._keep(self.run, self.swarm, self.kept)
        end = await ps.fix_passes(self.run, self.swarm, self.kept, state["flow"],
                                  ps.SCRIPT_CONCERNS, self.work, mark=self.mark,
                                  revise=self.revise, given=self.given, clock=self.clock,
                                  prefix=SCRIPT_PREFIX)
        if end == "waiting":
            ps._keep(self.run, self.swarm, self.kept)
            raise StageEnded("waiting")
        if end == "unchecked":
            self.kept.pop(SCRIPT_STAGE, None)
            raise StageEnded("unchecked", [{
                "id": 1, "what": "The script slides changed at the deck never checked against "
                                 f"{package_script.VERSION}: " + " ".join(state.get("problems")
                                                                           or []),
                "raised_by": [PROGRAM], "concerns": [], "fixes": [], "evidence_missing": []}])
        if end != "passed":
            self.kept.pop(SCRIPT_STAGE, None)
            raise StageEnded("not passed", state["flow"].get("left") or [])
        passed = self.slides()
        self.kept.pop(SCRIPT_STAGE, None)
        ps._keep(self.run, self.swarm, self.kept)
        return passed


def script_change_unit(run: ps.Run, sid: str, passed: dict,
                       kept: Optional[dict] = None) -> step_cache.Unit:
    """A script slide as the deck step changed it: keyed by the slide as the
    swarm passed it and everything the deck is written and reviewed with
    and asked (the tools, the findings answered, the revisions)."""
    return step_cache.Unit(SCRIPT_CHANGE, sid, {
        "script": step_cache.digest(passed), "write": writing_tool(run.doc, kept),
        "review": review_tool(run.doc, kept),
        "findings": [ps.findings_for(run.doc, WRITE_LABEL), ps.findings_for(run.doc, REVIEW_LABEL)],
        "revisions": [[r.get("by") or "", r.get("words") or ""] for r in ps.revisions_of(run.doc)]})


def _groups(passed: dict, now: dict) -> dict[str, list[str]]:
    """Each slide the swarm passed and the slides it is now: itself, and each
    new slide after it until the next slide the swarm passed."""
    was = set(package_script.slide_ids(passed))
    out: dict[str, list[str]] = {}
    at = None
    for sid in package_script.slide_ids(now):
        if sid in was:
            at = sid
            out[sid] = [sid]
        elif at is not None:
            out[at].append(sid)
    return out


def _marks_of(kept: dict, ids: list[str]) -> list[dict]:
    return [m for m in kept.get("marked") or [] if str(m.get("id", "")).split("/")[0] in ids]


def restore_script_changes(run: ps.Run, passed: dict, kept: dict) -> list[str]:
    """Each script slide an earlier run's deck step changed or split, kept
    under its unit, put back in the script (the slides it became in its
    place, their outline slides in the outline and their marks with the
    run's), when the script still checks with them; the slides put back."""
    now = package_script.read(run.script) or {}
    outline = package_script.read(run.outline) or {}
    slides = {x.get("id"): [x] for x in now.get("slides") or [] if isinstance(x, dict)}
    lines = {x.get("id"): [x] for x in outline.get("slides") or [] if isinstance(x, dict)}
    marks: dict[str, list[dict]] = {}
    back = []
    for slide in passed.get("slides") or []:
        if not isinstance(slide, dict) or slide.get("id") not in slides:
            continue
        entry = step_cache.reuse(script_change_unit(run, slide["id"], slide, kept), run.doc)
        value = entry.value if entry is not None and isinstance(entry.value, dict) else {}
        became = value.get("slides") or ([value["slide"]] if isinstance(value.get("slide"), dict)
                                         else [])
        became = [x for x in became if isinstance(x, dict)]
        if not became:
            continue
        slides[slide["id"]] = became
        olines = value.get("outline")
        olines = [olines] if isinstance(olines, dict) else [x for x in olines or []
                                                            if isinstance(x, dict)]
        if olines and slide["id"] in lines:
            lines[slide["id"]] = olines
        if isinstance(value.get("marks"), list):
            marks[slide["id"]] = value["marks"]
        back.append(slide["id"])
    if not back:
        return []
    doc = {**now, "slides": [y for x in now.get("slides") or [] for y in slides[x.get("id")]]}
    new_outline = {**outline, "slides": [y for x in outline.get("slides") or []
                                         for y in lines.get(x.get("id"), [x])]}
    work = run.script_file()
    if outline and new_outline != outline:
        before = run.outline.read_text(encoding="utf-8")
        package_script.write(run.outline, new_outline)
        if work._check(doc):
            pe.write_file(run.outline, before.encode("utf-8"))
            return []
    elif work._check(doc):
        return []
    package_script.write(run.script, doc)
    for sid, given in marks.items():
        ids = [x.get("id") for x in slides[sid]]
        kept["marked"] = [m for m in kept.get("marked") or []
                          if str(m.get("id", "")).split("/")[0] not in ids] + given
    return back


def keep_script_changes(run: ps.Run, passed: dict, kept: dict, began: float) -> list[str]:
    """Each script slide the deck step changed or split this run, kept under
    its unit: the slides it became, their outline slides and their marks;
    the slides kept."""
    now = package_script.read(run.script) or {}
    by_id = {x.get("id"): x for x in now.get("slides") or [] if isinstance(x, dict)}
    lines = {x.get("id"): x for x in (package_script.read(run.outline) or {}).get("slides")
             or [] if isinstance(x, dict)}
    groups = _groups(passed, now)
    out = []
    for slide in passed.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        ids = groups.get(slide.get("id")) or []
        if not ids or (ids == [slide["id"]] and by_id[slide["id"]] == slide):
            continue
        step_cache.computed(script_change_unit(run, slide["id"], slide, kept), run.doc, began,
                            value={"slides": [by_id[i] for i in ids],
                                   "outline": [lines[i] for i in ids if i in lines],
                                   "marks": _marks_of(kept, ids)})
        out.append(slide["id"])
    return out


def _write_review(run: ps.Run, result: dict) -> None:
    pe.write_file(run.folder / REVIEW, json.dumps(result, indent=2, ensure_ascii=False)
                  .encode("utf-8"))
    pe.write_file(run.folder / ps.CONVERSATION, ps.conversation_md(result).encode("utf-8"))


def _part_entry(run: ps.Run, part: dict) -> dict:
    """What a slide's part keeps: the part, and the files beside the deck
    source it names."""
    return {"value": part, "files_from": {f"{FOLDER}/{rel}": (run.folder / FOLDER / rel)
                                          .read_bytes() for rel in _files_of(run, part)}}


def _restore_files(run: ps.Run, entry: step_cache.Entry) -> None:
    step_cache.restore(entry, run.folder)


class Plan:
    """The deck's units as they stand for this run: the header and each
    slide's source, kept or to write (step_cache.py)."""

    def __init__(self, run: ps.Run, script: dict, kept: Optional[dict] = None):
        self.run = run
        self.kept = kept
        self.header: Optional[dict] = None
        self.parts: dict[str, dict] = {}
        # The keys whose kept output was rejected (it no longer builds):
        # what the author writes in their place replaces them.
        self.rejected: set[str] = set()
        self._units(script)

    def _units(self, script: dict) -> None:
        self.slides = [s for s in script.get("slides") or [] if isinstance(s, dict)]
        self.ids = [s.get("id") for s in self.slides]
        tool = writing_tool(self.run.doc, self.kept)
        # The sender's answers to a deck that never built: the writing's
        # inputs, said to the author before it writes.
        self.findings = ps.findings_for(self.run.doc, WRITE_LABEL)
        self.header_unit = step_cache.Unit(HEADER, "", {
            "slides": self.ids, "opening": step_cache.digest(self.slides[:1]), "tool": tool,
            "findings": self.findings})
        self.source_units = {s.get("id"): step_cache.Unit(SLIDE_SOURCE, s.get("id"), {
            "script": step_cache.digest(s), "tool": tool, "findings": self.findings})
            for s in self.slides}

    def refresh(self) -> list[str]:
        """The units as the script stands now (re-read after every edit
        written into it, a deck owner's or the author's slides): a slide
        whose script slide changed has its source written again, the rest
        as they are; the slides that changed."""
        before = {sid: u.key for sid, u in self.source_units.items()}
        header = self.header_unit.key
        self._units(package_script.read(self.run.script) or {})
        changed = [sid for sid, u in self.source_units.items() if before.get(sid) != u.key]
        for sid in list(self.parts):
            if sid in changed or sid not in self.source_units:
                self.parts.pop(sid)
        if self.header_unit.key != header:
            self.header = None
        return changed

    def keep_all(self, began: float) -> None:
        """The source as it passed, every slide and the header kept under
        its key as the script stands now (a slide whose script changed
        during the step, the author's or an owner's edit, under its new
        key), so a later run reuses them."""
        self.refresh()
        header, parts = parts_of(_read_source(self.run))
        if step_cache.lookup(self.header_unit, self.run.doc) is None:
            step_cache.computed(self.header_unit, self.run.doc, began, value=header,
                                files_from=_part_entry(self.run, header)["files_from"])
        for sid, unit in self.source_units.items():
            if sid in parts and step_cache.lookup(unit, self.run.doc) is None:
                step_cache.computed(unit, self.run.doc, began, **_part_entry(self.run, parts[sid]))

    def reuse(self) -> None:
        entry = step_cache.reuse(self.header_unit, self.run.doc)
        if entry is not None:
            _restore_files(self.run, entry)
            self.header = entry.value
        for sid, unit in self.source_units.items():
            entry = step_cache.reuse(unit, self.run.doc)
            if entry is not None:
                _restore_files(self.run, entry)
                self.parts[sid] = entry.value

    def release(self) -> None:
        """The kept header and slides given up: the deck they make does not
        build (JARVIS's pipeline changed since they were kept). The author writes them all
        again and what it writes replaces them under the same keys."""
        if self.header is not None:
            self.rejected.add(self.header_unit.key)
        self.rejected |= {self.source_units[sid].key for sid in self.parts
                          if sid in self.source_units}
        self.header = None
        self.parts.clear()

    @property
    def to_write(self) -> list[str]:
        return [sid for sid in self.ids if sid not in self.parts]

    @property
    def complete(self) -> bool:
        return self.header is not None and not self.to_write

    def put_back(self) -> list[str]:
        """The kept header and slides put back into the source exactly as
        kept (the author writes only the others), in the author's order;
        the slides put back that it had changed."""
        if not self.parts and self.header is None:
            return []
        now = _read_source(self.run)
        header, parts = parts_of(now)
        back = [sid for sid, part in self.parts.items() if sid in parts and parts[sid] != part]
        order = list(parts)
        for sid in self.ids:
            if sid in self.parts and sid not in order:
                order.insert(min(self.ids.index(sid), len(order)), sid)
        final = assemble(self.header if self.header is not None else header,
                         [self.parts.get(sid) or parts[sid] for sid in order])
        if final != now:
            _write_source(self.run, final)
        return back

    def start_source(self) -> None:
        """The source as kept so far, for the author to write the rest in."""
        if self.parts or self.header is not None:
            _write_source(self.run, assemble(self.header or {},
                                             [self.parts[sid] for sid in self.ids
                                              if sid in self.parts]))

    def keep_written(self, began: float) -> None:
        """Once the written source builds: the header and each slide the
        author wrote, kept under their keys."""
        header, parts = parts_of(_read_source(self.run))
        if self.header is None:
            step_cache.computed(self.header_unit, self.run.doc, began, value=header,
                                files_from=_part_entry(self.run, header)["files_from"],
                                replace=self.header_unit.key in self.rejected)
            self.header = header
        for sid in self.to_write:
            if sid in parts:
                unit = self.source_units[sid]
                step_cache.computed(unit, self.run.doc, began,
                                    replace=unit.key in self.rejected,
                                    **_part_entry(self.run, parts[sid]))
                self.parts[sid] = parts[sid]


def review_units(run: ps.Run, built: Built, script: dict,
                 kept: Optional[dict] = None) -> dict[str, step_cache.Unit]:
    """Each slide's review unit: its source part as built, its layout data,
    its script slide, the deck's questions, the sender's answers to an
    earlier run's findings on the review, and the revisions the run is
    asked (each one's words and who said them: Tim's Reject, the sender's
    change at its check), so a slide kept as it was is reviewed again
    against them."""
    _, parts = parts_of(_read_source(run))
    by_id = {s.get("id"): s for s in script.get("slides") or [] if isinstance(s, dict)}
    layout = {s.get("id"): s for s in built.proof.get("slides") or []}
    findings = ps.findings_for(run.doc, REVIEW_LABEL)
    tool = review_tool(run.doc, kept)
    revisions = [[r.get("by") or "", r.get("words") or ""] for r in ps.revisions_of(run.doc)]
    out = {}
    for sid, part in parts.items():
        out[sid] = step_cache.Unit(SLIDE_REVIEW, sid, {
            "part": step_cache.digest(part), "layout": step_cache.digest(
                {k: v for k, v in (layout.get(sid) or {}).items() if k != "slide"}),
            "script": step_cache.digest(by_id.get(sid)), "tool": tool,
            "findings": findings, "revisions": revisions})
    return out


def deck(doc: dict) -> Optional[str]:
    """The deck step of one run (package_jobs.PRODUCERS["deck"]): every
    unit reused from the step cache when its inputs are kept, only the rest
    done; WAITING when it waits on the sender (questions, or what is left
    after its fix passes)."""
    run = ps._run(doc, DECK)
    kept = ps._kept(run)
    if (package_jobs.get(doc["id"]).get("swarm") or {}).get("result") != "passed":
        raise package_jobs.Blocked(DECK, "The script did not pass the swarm.",
                                   "JARVIS reached the deck step.", "A new run.")
    if not deck_format():
        raise package_jobs.Blocked(DECK, f"JARVIS's description of how a deck is written is not "
                                         f"in this install ({DOC}).",
                                   "JARVIS reached the deck step.",
                                   "Install JARVIS with its docs folder, then a new run.")
    # The script as the swarm passed it, kept for the run: the slides the
    # deck step changes are keyed by it (`deck-script`), and an earlier
    # run's changes of it are taken back once, as the step begins.
    if "deck_script_base" not in kept:
        kept["deck_script_base"] = package_script.read(run.script) or {}
        restored = restore_script_changes(run, kept["deck_script_base"], kept)
        if restored:
            _log(f"deck: script slides {', '.join(restored)} as an earlier run's deck changed "
                 f"them")
    state = kept.setdefault(DECK, {"builds": 0})
    units = kept.setdefault("deck_units", {})
    flow = kept.setdefault("deck_flow", {"stage": "deck-pass", "pass": 1})
    clock = ps.Clock(kept)
    outcome: dict = {}
    last: dict = {}
    began = time.time()

    def build_now() -> Optional[Built]:
        state["builds"] += 1
        built, problems = build_and_prove(run, state["builds"])
        state["problems"] = list(problems)
        state["unfit"] = over_capacity(problems)
        last["built"] = built
        return built

    def begin_review(built: Built) -> list[str]:
        """Each slide's review reused when kept; the slides left to review."""
        plan.refresh()
        reviews = review_units(run, built, package_script.read(run.script) or {}, kept)
        reviewed: dict[str, dict] = {}
        only = [sid for sid in plan.ids if sid in reviews
                and not _reuse_review(run, reviews[sid], reviewed)]
        units.update(phase="review", only=only, said=False, reviewed=reviewed,
                     inputs={sid: u.inputs for sid, u in reviews.items()},
                     script_at_review=package_script.read(run.script) or {})
        flow.clear()
        flow.update({"stage": "deck-pass", "pass": 1,
                     "slides": only if len(only) < len(plan.ids) else None})
        return only

    plan = Plan(run, package_script.read(run.script) or {}, kept)
    if units.get("phase") != "review":
        plan.reuse()
        if plan.complete:
            plan.start_source()
            build_now()
            # A kept deck that is over a capacity goes back to the author
            # below, as any deck that does not build.
            if last.get("built") is not None and not begin_review(last["built"]):
                return _finish(run, kept, plan, outcome, last, state, build_now, began, True)

    async def go():
        swarm_ = ps.build(run, kept, DECK_CONCERNS)
        source = DeckSource(run, build_now)
        stage = ScriptStage(run, swarm_, kept, clock)

        async def through_script(why: str = "") -> list[str]:
            """The slides changed at the deck through the script stage
            (begun with `why`, else going on from where it stopped); the
            deck slides to write again."""
            if stage.state is None:
                stage.begin(kept["deck_change_base"], kept["deck_change_outline"], why)
            passed = await stage.go()
            kept.pop("deck_change_base", None)
            kept.pop("deck_change_outline", None)
            rewrite = plan.refresh()
            if "closing" in units:
                # In a close of the review: every slide passed in it is
                # reviewed again in full, however often it went back before.
                closing = units["closing"]
                closing += [sid for sid in dict.fromkeys(passed + rewrite) if sid not in closing]
            swarm_.say(PROGRAM, f"The script stage passed script slide(s) "
                                f"{', '.join(passed) or 'none'}; their deck slides "
                                f"({', '.join(rewrite) or 'none'}) are written again and "
                                f"reviewed in full.")
            return rewrite

        async def written(note: str, close: bool = False) -> bool:
            """The author writes the slides not kept (every slide when none
            is), the kept ones put back, until the deck builds and proves,
            at most ROUNDS_MAX more times. A script slide the author gives
            goes back through the script stage for that slide first, then
            its deck slide is written again (`through_script`). In a close
            of the review (`close`), what the author revised stands:
            nothing is put back."""
            if stage.state is not None or "deck_change_base" in kept:
                note = rewrite_note(run, await through_script())
            attempt, stages = 0, 0
            while attempt <= ROUNDS_MAX:
                answer = await swarm_.answer(ps.AUTHOR, ps.TIMEOUT, note, author_form(run))
                note = revise_note(run)
                base = package_script.read(run.script) or {}
                outline_base = package_script.read(run.outline) or {}
                changed, forms, problems = (
                    script_change(run, answer) if stages <= ROUNDS_MAX else
                    ([], [], ["slides: the script stage has run "
                              f"{stages} times in this turn; write the deck from the script "
                              f"as it stands."] if isinstance(answer, dict) and (
                        answer.get("slides") or answer.get("forms")) else []))
                if problems:
                    swarm_.say(PROGRAM, "The script slides in your answer do not check; the "
                                        "script stays as it was. Give them again, whole:\n"
                               + "\n".join(f"- {p}" for p in problems))
                    attempt += 1
                    continue
                if changed or forms:
                    stages += 1
                    ps.forms_changed(swarm_, forms, "deck")
                    swarm_.say(PROGRAM, f"JARVIS put script slide(s) {', '.join(changed)} in the "
                                        f"script as you gave them. They go back through the "
                                        f"script stage, for those slides only: marked against "
                                        f"the labels, and the owners sign their concerns off on "
                                        f"them; then their deck slides are written again and "
                                        f"reviewed in full.")
                    kept["deck_change_base"], kept["deck_change_outline"] = base, outline_base
                    ps._keep(run, swarm_, kept)
                    note = rewrite_note(run, await through_script(
                        str((answer or {}).get("message") or "the author's change")))
                    continue
                plan.refresh()
                back = [] if close else plan.put_back()
                if back:
                    swarm_.say(PROGRAM, f"JARVIS put back slides {', '.join(back)} as they "
                                        f"were: their script slides did not change.")
                if build_now() is not None:
                    return True
                if plan.parts or plan.header is not None:
                    # The kept slides are in the deck that does not build:
                    # the author may change them too now, and what it
                    # writes replaces them.
                    plan.release()
                    swarm_.say(PROGRAM, "Every slide of the deck is yours to change now, the "
                                        "ones JARVIS put back too.")
                swarm_.say(PROGRAM, build_problems_note(state["problems"]))
                attempt += 1
            return False

        async def close(items: list[dict]) -> bool:
            """The author's close of the deck review; each slide that went
            back through the script stage in it is reviewed again in full,
            by every owner and the editor, for every deck concern, however
            often it went back before (`closing`: the slides through_script
            passed in this close, kept across the run's processes)."""
            units.setdefault("closing", [])
            if not await written(revise_note(run), close=True):
                units.pop("closing", None)
                return False
            again = [sid for sid in units.get("closing") or [] if sid in plan.ids]
            if again and last.get("built") is not None:
                with clock(f"deck-slides-{flow['pass']}"):
                    await ps.owners_turn(
                        swarm_, ps.FIXERS, DECK_IDS, source, f"deck-slides-{flow['pass']}",
                        lambda name: (
                            f"Slide(s) {', '.join(again)} went back through the script stage "
                            f"and their deck slides were written again. Review those slides in "
                            f"full, for every one of your concerns, against the script as it "
                            f"is now; every other slide was reviewed before.\n"
                            + review_turn(run, last["built"], again) + "\n"
                            + sessions._tag(DeckSource.key, source.text(), file=SOURCE)))
            units.pop("closing", None)
            return True

        async def nothing() -> None:
            return None

        def given(name: str) -> str:
            slides = flow.get("slides")
            return ((f"Review only these slides: {', '.join(slides)}; every other slide was "
                     f"reviewed before and stays as it is.\n" if slides else "")
                    + "Review the deck, above, against the passed script, for your concerns.\n"
                    + sessions._tag(DeckSource.key, source.text(), file=SOURCE))
        try:
            await swarm_.open()
            ps._take_answers(run, swarm_, kept)
            if units.get("phase") != "review":
                if plan.complete and last.get("built") is None:
                    # The kept source no longer builds: the author writes it
                    # all, and what it writes replaces what was kept.
                    plan.release()
                if plan.findings and not units.get("write_said"):
                    swarm_.say(PROGRAM, "The run before ended with a deck that did not build, "
                                        "with findings for the sender; its answers follow, and "
                                        "the deck is written again with them.")
                    swarm_.say("sender", ps.findings_said(plan.findings))
                    units["write_said"] = True
                if not plan.complete:
                    write = plan.to_write
                    kept_ids = [i for i in plan.ids if i in plan.parts and i not in write]
                    plan.start_source()
                    with clock("deck-write"):
                        note = (write_note(run) if not kept_ids and plan.header is None
                                else revise_deck_note(run, write, kept_ids))
                        if not await written(note):
                            outcome.update(result="not passed", stage=WRITE_LABEL,
                                           findings=_findings_of_build(state["problems"]))
                            return
                    plan.keep_written(began)
                if not begin_review(last["built"]):
                    outcome["result"] = "passed"
                    return
            if not units.get("said"):
                said = ps.findings_said(ps.findings_for(doc, REVIEW_LABEL))
                if said:
                    swarm_.say(PROGRAM, "The run before ended the deck's review with findings "
                                        "for the sender; its answers follow, and the deck is "
                                        "reviewed again as it stands.")
                    swarm_.say("sender", said)
                built = last.get("built") or build_now()
                if built is None:
                    outcome.update(result="not passed", stage=WRITE_LABEL,
                                   findings=_findings_of_build(state["problems"]))
                    return
                swarm_.say(PROGRAM, review_turn(run, built, flow.get("slides")))
                units["said"] = True
                ps._keep(run, swarm_, kept)
            end = await ps.fix_passes(
                run, swarm_, kept, flow, DECK_IDS, source, mark=nothing,
                revise=close, given=given,
                clock=clock,
                prefix="deck-")
            if end == "waiting":
                outcome["waiting"] = True
            elif end == "unchecked":
                outcome.update(result="not passed",
                               findings=_findings_of_build(state["problems"]))
            else:
                outcome.update(result=end, findings=flow.get("left") or [])
        except StageEnded as ended:
            if ended.end == "waiting":
                outcome["waiting"] = True
            else:
                outcome.update(result="not passed", stage=WRITE_LABEL, findings=ended.findings)
        finally:
            ps._keep(run, swarm_, kept)
            outcome["swarm"] = swarm_.result()
            await swarm_.close()
    package_jobs.asked(DECK, "the swarm",
                       "JARVIS asked the author to write the deck from the passed script, built "
                       "and proved it, and had the owners and the editor compare it with the "
                       "script.", go())
    if outcome.get("waiting"):
        ps._save(run, kept)
        return package_jobs.WAITING
    if last.get("built") is None and state.get("unfit"):
        return _not_fit(run, kept, state)
    return _finish(run, kept, plan, outcome, last, state, build_now, began,
                   outcome.get("result") == "passed" and (last.get("built") is not None
                                                          or units.get("phase") == "review"))


def _finish(run: ps.Run, kept: dict, plan: "Plan", outcome: dict, last: dict, state: dict,
            build_now: Callable[[], Optional[Built]], began: float, passed: bool
            ) -> Optional[str]:
    """The deck step's end: passed, each slide reviewed now kept and the
    final deck built; the review written (reused when the step's record
    is kept) and the outcome on the job; else what is left to the sender."""
    doc = run.doc
    units = kept.get("deck_units") or {}
    reviews: dict[str, step_cache.Unit] = {}
    built = last.get("built") or (build_now() if passed else None)
    if passed and built is not None:
        final, reviews = _final(run, plan, kept, built, build_now)
        built = final or built
    if built is None and state.get("unfit"):
        return _not_fit(run, kept, state)
    passed = passed and built is not None
    # The review's record carries the build's program verdict (`program`,
    # from the final build's proof): the build's own key is in the step's.
    step = step_cache.Unit(STEP_UNIT, "", {"reviews": sorted(u.key for u in reviews.values()),
                                           "source": step_cache.file_sha(source_of(run)),
                                           "build": build_unit(run).key})
    if not (passed and "swarm" not in outcome and step_cache.reuse(step, doc, run.folder)):
        swarm = outcome.get("swarm") or {**(kept.get("swarm") or {})}
        result = {**swarm, "result": "passed" if passed else "not passed",
                  "program": [readable(built.proof)] if built is not None else [],
                  "findings": [] if passed else (outcome.get("findings") or _findings_of_build(
                      state.get("problems") or [])),
                  "deck_passes": (kept.get("deck_flow") or {}).get("pass", 1),
                  "times": kept.get("times") or []}
        result.setdefault("conversation", [])
        _write_review(run, result)
        if passed:
            step_cache.computed(step, doc, began, folder=run.folder,
                                files=[REVIEW, ps.CONVERSATION])
    only = list(units.get("only") or [])
    kept.pop("deck_units", None)
    ps._save(run, kept)
    flow = kept.get("deck_flow") or {}
    if passed:
        package_jobs.noted(doc["id"], DECK, {
            "result": "passed", "passes": flow.get("pass", 1), "archive": str(built.archive),
            "folder": str(built.folder), "source": str(source_of(run)),
            "review": str(run.folder / REVIEW), "slides_reviewed": only})
        return None
    package_jobs.noted(doc["id"], DECK, {"result": "not passed", "passes": flow.get("pass", 1),
                                         "review": str(run.folder / REVIEW)})
    findings = json.loads((run.folder / REVIEW).read_text(encoding="utf-8"))["findings"]
    package_jobs.failed_swarm(doc["id"], ps.findings_questions(findings), findings,
                              stage=outcome.get("stage") or (
                                  WRITE_LABEL if built is None else REVIEW_LABEL))
    return package_jobs.WAITING


def _reuse_review(run: ps.Run, unit: step_cache.Unit, reviewed: dict) -> bool:
    """A slide's review reused from the step cache: its reviewed part kept
    for the final source. False when not kept."""
    entry = step_cache.reuse(unit, run.doc)
    if entry is None:
        return False
    _restore_files(run, entry)
    reviewed[unit.name] = entry.value
    return True


def _final(run: ps.Run, plan: Plan, kept: dict, built: Built,
           build_now: Callable[[], Optional[Built]]
           ) -> tuple[Optional[Built], dict[str, step_cache.Unit]]:
    """The review passed: the final source is each slide whose review was
    reused as it was reviewed (unless its script slide changed since the
    review began) and the rest as they stand, built again when it changed;
    then, with the script as it stands now (re-read: an owner's edit or the
    author's slides may have changed it), each slide's review kept under its
    key with its source as reviewed, every slide's source and the header
    kept (Plan.keep_all) and each script slide the step changed kept
    (`keep_script_changes`). (The final build when built again, the review
    units.)"""
    began = time.time()
    units = kept.get("deck_units") or {}
    reviewed = dict(units.get("reviewed") or {})
    script = package_script.read(run.script) or {}
    for sid in package_script.changed_slides(units.get("script_at_review") or script, script):
        reviewed.pop(sid, None)
    plan.refresh()
    header, parts = parts_of(_read_source(run))
    final = assemble(header, [reviewed.get(sid) or parts[sid] for sid in plan.ids
                              if sid in parts or sid in reviewed])
    rebuilt = None
    if final != _read_source(run):
        _write_source(run, final)
        rebuilt = build_now()
    reviews = review_units(run, rebuilt or built, script, kept)
    _, parts = parts_of(_read_source(run))
    for sid, unit in reviews.items():
        if sid in parts and step_cache.lookup(unit, run.doc) is None:
            step_cache.computed(unit, run.doc, began, **_part_entry(run, parts[sid]))
    plan.keep_all(began)
    keep_script_changes(run, kept.get("deck_script_base") or {}, kept, began)
    return rebuilt, reviews
