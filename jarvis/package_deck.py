"""The deck step of a package run (workflow.yaml deck, approved by Tim
2026-11-19; deck_review's framing approved 2026-11-20): the passed script
turned into a deck in the same swarm, built and proven by JARVIS's own
presentation pipeline, and compared slide by slide with the script.

Run after the swarm passes, in JARVIS's own process (package_jobs.PRODUCERS),
in the run's folder. A round:

  write_deck       the author, in the same swarm, writes
                   `deck/presentation.yaml` (presentation/1) from the passed
                   script (script.yaml) alone, choosing JARVIS's named
                   layouts: one script slide to one deck slide, in order,
                   with the same id (a script slide that asks a decision the
                   deck's decision slide asking it, presentation/1's one
                   slide for a decision); where the script gives Tim's own words
                   (a his_words piece), they are runs in the `his-words`
                   style (presentation_schema.HIS_WORDS), drawn highlighted
                   in a very bright, vibrant way, and narrated. The
                   program's words to it (`write_note`) carry the script,
                   the format, word for word from docs/making-a-package.md,
                   and a complete example.
  build_and_prove  the program fills each picture's size and hash
                   (make_package.fill_assets), compiles and proves it
                   (make_package.make_presentation: every picture drawn
                   readable at every frame, every word's style at least
                   the text floor at both sizes, the story's targets, the
                   renderer JARVIS's own, the story about fifteen minutes at
                   most), checks the package carries the standard vote,
                   Approve and Reject at the check mark, and checks the deck
                   against the script (`script_problems`): its slides the
                   script's, one to one, and every piece of Tim's own words
                   drawn on its slide in the his-words style and said in its
                   narration (his_words_check: the program's check, no
                   vote). What does not build or check is said into the
                   conversation as JARVIS's own turn, to the author.
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
                   deck still builds and proves with it) and sign off each
                   of the deck's questions but deck-readable, the
                   program's (DECK_CONCERNS); what is left is
                   merged by meaning into one list, the author works it, and
                   each owner re-checks its own items (package_swarm.
                   fix_passes).

Every build is measured too (deck_fit, Tim 2026-11-22): JARVIS draws the
built deck as its package frame does, at every frame from 662 x 489 to
1600 x 900, and reads for each slide whether anything on it is clipped or
needs scrolling and the size its words are drawn at. The shared deck fits
its slides itself (deck.js fitSlide): each slide's words at the largest
size at which it fits its frame, between the 13 px floor and the ceiling.
Type size and fit are the program's: the fit and the sizes drawn go to the
reviewers beside each slide's layout data as facts (`drawn_px`), never a
question for them or the sender, and whether every word is readable at its
size (deck-readable, PROGRAM_QUESTIONS) is answered by that measure
(`readable`, the review's `program`). A slide that does not fit with its
words at the floor does not build: the author is told to split it, an
owner's edit that makes one unfit is not applied, and a deck JARVIS cannot
make fit stops the step in Error (NOT_FIT_FIX), never findings for the
sender.

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
`pipeline_digest`) is the deck's, one program call; each slide's fit
(`deck-fit`, keyed by its layout data and the pipeline) is measured only
for the slides whose layout changed. Each slide's review (`deck-review`,
keyed by its source, its fit, its script slide, what it is reviewed with,
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
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

import yaml

from jarvis import (data_paths, deck_fit, decision_package as dp, layout_spec, make_package,
                    package_evidence as pe, package_inbox, package_jobs, package_script,
                    package_swarm as ps, presentation_proof, sessions, spoken_words, step_cache)
from jarvis.presentation_schema import HIS_WORDS

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
)
# Type size and fit are the program's (Tim 2026-11-22): whether every word
# is readable at its size is answered by JARVIS's own measure of the built
# deck (deck_fit: each slide's words drawn at the largest size at which it
# fits its frame, never under the 13 px floor, nothing clipped or to
# scroll), never signed off by a reviewer, never a finding or a question
# for the sender. A slide that does not fit with its words at the floor
# does not build and goes back to the author to split.
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


def write_note(run: ps.Run) -> str:
    """The program's words to the author for writing the deck (workflow.yaml
    deck write_deck and his_words_highlighted)."""
    return "\n\n".join([
        f"The script has passed. Now write the deck from the passed script alone, as one "
        f"presentation/1 source, in {source_of(run)}, with any pictures beside it as files, by "
        f"path. Choose one of JARVIS's named layouts for each slide; write no HTML or CSS. Each "
        f"slide of the script is one slide of the deck, in the same order and with the same "
        f"id: its words on the slide, what JARVIS says over it as its narration. A script slide "
        f"that asks a decision is the deck's decision slide (type decision, the same id), "
        f"asking that decision with the options you wrote: its words in the decision's context "
        f"and what JARVIS says over it in the decision's say.",
        f"Where the script gives Tim's own words (a his_words piece), show them on that slide as "
        f"runs with emphasis {HIS_WORDS}, which JARVIS draws highlighted in a very bright, "
        f"vibrant way, and have JARVIS say them in its narration; never 'this is what you said' "
        f"with a block of text. JARVIS checks every one.",
        "JARVIS fills each picture's size and hash, builds the deck and proves it; what does "
        "not build comes back to you here. The owners and the editor then compare each slide "
        "with the script.",
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT),
        sessions._tag("format", deck_format()),
        sessions._tag("example", example(), file=SOURCE)])


def revise_note(run: ps.Run) -> str:
    return (f"Revise the deck in {source_of(run)}, or answer each point raised. Say what you "
            f"changed and why.")


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
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT),
        sessions._tag("format", deck_format()),
        sessions._tag("example", example(), file=SOURCE)])


def _findings_of_build(problems: list[str]) -> list[dict]:
    """A deck that never built, as the one finding the sender is sent."""
    return [{"id": 1, "what": "The deck never built and proved: " + " ".join(problems),
             "raised_by": [PROGRAM], "concerns": [], "fixes": [], "evidence_missing": []}]


def build_problems_note(problems: list[str]) -> str:
    return "The deck did not build. Revise it:\n" + "\n".join(f"- {p}" for p in problems)


# ── build and prove ───────────────────────────────────────────────────────

class Built:
    def __init__(self, archive: Path, folder: Path, proof: dict, deck: dict,
                 fit: Optional[dict] = None):
        self.archive, self.folder, self.proof, self.deck = archive, folder, proof, deck
        self.fit = fit or {}


class Problems(list):
    """Why a round's deck did not build; `unfit` when what is left is only
    that a slide does not fit its frame even after JARVIS's own fitting
    (deck_fit): the program's to fix, never the sender's or a reviewer's."""
    unfit = False


def _unfit(problems: list[str]) -> Problems:
    out = Problems(problems)
    out.unfit = True
    return out


# How a build's fit is measured (deck_fit.measure; the suite gives its own
# outside the page tests, conftest._deck_fit_without_a_browser).
MEASURE: Callable[[Path], dict] = deck_fit.measure

# Where the deck step stops when JARVIS cannot make a slide fit (Tim
# 2026-11-22: a layout failure is the program's, never the sender's).
NOT_FIT_FIX = ("JARVIS's shared deck (examples/packages/decision-deck, its fit in deck.css and "
               "deck.js) must lay the slide out to fit; once it does, a new run of the package "
               "(make_package --rerun) goes on from the deck.")


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
    words as they are, in order."""
    out = []
    want = [s["id"] for s in script.get("slides") or []]
    have = [s.get("id") for s in deck.get("slides") or [] if isinstance(s, dict)]
    if want != have:
        out.append(f"The deck's slides are {have}; the script's are {want}. Write one deck "
                   f"slide for each script slide, in the same order, with the same id; a "
                   f"script slide that asks a decision is the deck's decision slide.")
    slides = {s.get("id"): s for s in deck.get("slides") or [] if isinstance(s, dict)}
    for wanted in script.get("slides") or []:
        slide = slides.get(wanted["id"])
        if slide is None:
            continue
        asked = (wanted.get("decision") or {}).get("id")
        if asked and (slide.get("type") != "decision" or slide.get("decision") != asked):
            out.append(f"Script slide {wanted['id']} asks the decision {asked}: its deck slide "
                       f"is a decision slide (type decision, id {wanted['id']}) asking "
                       f"{asked}, its words in the decision's context and what JARVIS says "
                       f"over it in the decision's say.")
        elif not asked and slide.get("type") != "content":
            out.append(f"Script slide {wanted['id']} asks no decision: its deck slide is a "
                       f"content slide.")
    for quote in package_script.his_words(script):
        slide = slides.get(quote["slide"]) or {}
        drawn = " ".join(run.get("text", "") for obj in slide.get("objects") or []
                         for para in obj.get("paragraphs") or []
                         for run in para.get("runs") or [] if run.get("emphasis") == HIS_WORDS)
        said = " ".join(c.get("say", "") for c in slide.get("narration") or [])
        words = _words(quote["text"])
        if not _within(words, _words(drawn)):
            out.append(f"Slide {quote['slide']} does not draw Tim's words {quote['text']!r} in "
                       f"the {HIS_WORDS} style.")
        if not _within(words, _words(said)):
            out.append(f"Slide {quote['slide']}'s narration does not say Tim's words "
                       f"{quote['text']!r}.")
    return out


HEADER, SLIDE_SOURCE, BUILD, FIT, SLIDE_REVIEW, STEP_UNIT = (
    "deck-header", "deck-source", "deck-build", "deck-fit", "deck-review", "deck")
REVIEW_LABEL = SLIDE_REVIEW            # the stage an earlier run's findings answer
# Findings on a deck that never built are the writing's: their answers are
# said before the author writes and are in every deck-header and
# deck-source key, so the author writes again with them.
WRITE_LABEL = SLIDE_SOURCE

# The Python that lays out, builds, proves and measures a deck (in the
# build and fit keys, whole), and the shared deck's own files.
PIPELINE_CODE = ("jarvis.presentation", "jarvis.presentation_source",
                 "jarvis.presentation_schema", "jarvis.presentation_proof",
                 "jarvis.picture_proof", "jarvis.decision_package", "jarvis.deck_fit",
                 "jarvis.layout_spec", "jarvis.make_package")
# The code that shapes what the author and the reviewers write.
DECK_CODE = ("jarvis.package_deck",)


def format_digest() -> str:
    """How a deck is written, as the author is told it: the format and the
    example."""
    return step_cache.digest([deck_format(), example()])


def pipeline_digest() -> str:
    """JARVIS's presentation pipeline as a build and a fit read it: the
    Python that lays out, builds, proves and measures a deck
    (PIPELINE_CODE, each module's source), the shared deck's files
    (examples/packages/decision-deck, its new_deck.py among them) and the
    presentation assets (the layout spec, the CSS and JS), the renderer
    files a built deck carries, and the fit's frames."""
    return step_cache.digest({"code": step_cache.code(*PIPELINE_CODE),
                              "deck": step_cache.tree_sha(dp.BUILDER.parent),
                              "assets": step_cache.tree_sha(layout_spec.ASSETS),
                              "templates": presentation_proof.template_hashes(),
                              "frames": deck_fit.frames()})


def writing_tool(doc: dict) -> dict:
    """What the deck source is written with: the author's role, prompt and
    engine choice, the format it is told and the code."""
    return {"author": step_cache.role_tool(ps.AUTHOR, requester=doc.get("engine")),
            "format": format_digest(),
            "code": step_cache.code(*step_cache.MODEL_CODE, *ps.SWARM_CODE, *DECK_CODE)}


def review_tool(doc: dict) -> dict:
    """What each slide is reviewed with: every participant's role, prompt
    and engine choice, the deck's concerns and questions, the labels and
    the code (package_swarm.tool_of)."""
    return {**ps.tool_of(doc, more=DECK_CONCERNS), "questions": [list(q) for q in DECK_QUESTIONS],
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


def _files_of(run: ps.Run, value) -> list[str]:
    """The files beside the deck source that a part names by path."""
    folder = run.folder / FOLDER
    return sorted(v for v in _ids_in(value) if v and not v.startswith("/") and ".." not in v
                  and len(v) < 300 and (folder / v).is_file() and v != SOURCE)


def build_unit(run: ps.Run) -> step_cache.Unit:
    """The deck's build: its source as written, the files it names, the
    passed script it is checked against and JARVIS's pipeline."""
    source = _read_source(run)
    return step_cache.Unit(BUILD, "", {
        "source": step_cache.file_sha(source_of(run)),
        "files": step_cache.files_sha(run.folder / FOLDER, _files_of(run, source)),
        "script": step_cache.file_sha(run.script), "pipeline": pipeline_digest()})


def _compile(run: ps.Run, out: Path) -> list[str]:
    """The source filled, compiled and proven into `out`; the problems."""
    source = source_of(run)
    try:
        make_package.refuse_json_deck(source)
        make_package.fill_assets(source)
        make_package.make_presentation(source, out)
    except dp.Refused as e:
        return [str(p) for p in e.problems]
    except (OSError, ValueError, yaml.YAMLError) as e:
        return [str(e)]
    return []


def fit_of(run: ps.Run, folder: Path, proof: dict) -> dict:
    """Every slide's fit at every frame (deck_fit): each slide's reused from
    the step cache when its layout data is kept, the rest measured
    together."""
    pipeline = pipeline_digest()
    units, have, missing = {}, {}, []
    for s in proof.get("slides") or []:
        layout = {k: v for k, v in s.items() if k != "slide"}
        units[s["id"]] = unit = step_cache.Unit(FIT, s["id"], {"layout": layout,
                                                               "pipeline": pipeline})
        entry = step_cache.reuse(unit, run.doc)
        if entry is None:
            missing.append(s["id"])
        else:
            have[s["id"]] = entry.value
    if missing:
        began = time.time()
        whole = len(missing) == len(units)
        measured = MEASURE(folder) if whole else MEASURE(folder, only=missing)
        # The slides are measured in one drawing: each one's time is its share.
        share = (time.time() - began) / len(missing)
        for one in measured.get("slides") or []:
            if one["id"] in missing:
                have[one["id"]] = one
                step_cache.computed(units[one["id"]], run.doc, began, seconds=share,
                                    value={k: v for k, v in one.items() if k != "slide"})
    slides = []
    for n, s in enumerate(proof.get("slides") or [], 1):
        if s["id"] in have:
            slides.append({**have[s["id"]], "slide": s.get("slide", n)})
    return {"version": deck_fit.VERSION, "frames": [list(f) for f in deck_fit.frames()],
            "fits": all(x["fits"] for x in slides), "slides": slides}


def build_and_prove(run: ps.Run, round_no: int) -> tuple[Optional[Built], list[str]]:
    """The author's source filled, compiled and proven by JARVIS's own
    presentation pipeline (make_package.make_presentation), into
    `deck-built/<round>/`, reused from the step cache when the same source
    was built before; (the build, []) or (None, every problem)."""
    source = source_of(run)
    if not source.is_file():
        return None, [f"There is no {SOURCE} at {source}."]
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
    # Every slide's fit at every frame, measured where its words are drawn
    # (deck_fit, Tim 2026-11-22): a fact for the reviewers, and a slide
    # that does not fit even after JARVIS's own fitting does not build.
    try:
        fit = fit_of(run, folder, proof)
    except deck_fit.Unavailable as e:
        raise package_jobs.Blocked(
            DECK, f"JARVIS could not measure whether the deck's slides fit their frame: {e}",
            "JARVIS built and proved the deck and drew it in headless Chromium at every frame "
            "from 662 x 489 to 1600 x 900 to measure each slide.",
            "Install Playwright's Chromium (.venv/bin/python -m playwright install chromium); "
            "then a new run of the package (make_package --rerun) goes on from the deck.") from e
    deck_fit.write(folder, fit)
    if not fit["fits"]:
        return None, _unfit(deck_fit.problems(fit))
    return Built(archive, folder, proof, deck, fit), []


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
        sessions._tag("layout", json.dumps(_with_fit(layout.get(s["id"]), built.fit, s["id"]),
                                           indent=1, ensure_ascii=False),
                      file=presentation_proof.PROOF_FILE)]), id=s["id"])
        for s in script.get("slides") or [])
    return "\n\n".join([
        "The deck written from the passed script is built and proven, and every quote of Tim's "
        "own words is drawn in his style and narrated. Below, slide by slide, each slide of the "
        "passed script beside the layout data JARVIS computed for its deck slide as it compiled "
        "and proved it (its text and pictures with their positions and sizes at the smallest "
        "and the largest frame, in deck units on the 880 x 640 canvas and in pixels) and its "
        "narration, and its fit and the size its words are drawn at: JARVIS drew every slide "
        "at every frame from 662 x 489 to 1600 x 900, drawing each slide's words at the "
        "largest size at which the slide fits, never under the 13 px floor, and measured that "
        "nothing on it, words, pictures or a decision's controls, is clipped or needs "
        "scrolling, and the size its words are drawn at (drawn_px). Type size and fit are the "
        "program's to measure and to fix, facts here: they are not for the review or the "
        "sender, and whether every word is readable at its size is answered by that measure. "
        "No screenshots. Compare each slide with the script.",
        *([f"Only the slides this revision changed are below ({', '.join(only)}); every "
           f"other slide of the deck is as the run before made it and was reviewed then."]
          if only else []),
        slides,
        "The deck's questions, by id:\n" + "\n".join(
            sessions._tag("question", asks, id=qid) for qid, asks in DECK_QUESTIONS
            if qid not in PROGRAM_QUESTIONS)])


def _with_fit(layout: Optional[dict], fit: dict, slide_id: str) -> Optional[dict]:
    """A slide's layout data with its measured fit (deck_fit) beside it."""
    measured = deck_fit.of_slide(fit, slide_id)
    if layout is None or measured is None:
        return layout
    return {**layout, "fit": {"fits": measured["fits"], "said": deck_fit.said(measured)},
            "drawn_px": deck_fit.drawn_at_ends(measured)}


def readable(fit: dict) -> dict:
    """The program's answer to whether every word is readable at its size
    (PROGRAM_QUESTIONS): the build's measure, each slide's words drawn at
    the largest size at which it fits, at the floor at least."""
    floor = layout_spec.spec()["text"]["min_px"]
    least = [t["least"] for s in fit.get("slides") or [] for d in s.get("drawn") or []
             if (t := d.get("text_px"))]
    ok = bool(fit.get("fits")) and all(px >= floor for px in least)
    return {"question": "deck-readable", "by": PROGRAM, "state": "resolved" if ok else "not_yet",
            "reason": (f"Measured: every slide fits its frame at every frame, its words drawn "
                       f"as large as it allows, the smallest word {min(least):g} px, never "
                       f"under the {floor} px floor." if ok and least else
                       "Measured: " + " ".join(deck_fit.problems(fit)) if not ok else
                       "Measured: every slide fits its frame at every frame.")}


class DeckSource:
    """The author's deck source as the owners work on it (sessions.Script):
    each edit by its offsets in presentation.yaml, applied by the program
    only where its old text is exactly there (sessions.apply_edits); edits
    with which the deck no longer builds and proves are not applied."""

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

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        before = self.text()
        after, applied, refused = sessions.apply_edits(before, edits)
        if not applied:
            return before, [], refused
        pe.write_file(self.path, after.encode("utf-8"))
        if self._build() is None:
            pe.write_file(self.path, before.encode("utf-8"))
            self._build()
            return before, [], refused + applied
        return after, applied, refused


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

    def __init__(self, run: ps.Run, script: dict):
        self.run = run
        self.slides = [s for s in script.get("slides") or [] if isinstance(s, dict)]
        self.ids = [s.get("id") for s in self.slides]
        tool = writing_tool(run.doc)
        # The sender's answers to a deck that never built: the writing's
        # inputs, said to the author before it writes.
        self.findings = ps.findings_for(run.doc, WRITE_LABEL)
        self.header_unit = step_cache.Unit(HEADER, "", {
            "slides": self.ids, "opening": step_cache.digest(self.slides[:1]), "tool": tool,
            "findings": self.findings})
        self.source_units = {s.get("id"): step_cache.Unit(SLIDE_SOURCE, s.get("id"), {
            "script": step_cache.digest(s), "tool": tool, "findings": self.findings})
            for s in self.slides}
        self.header: Optional[dict] = None
        self.parts: dict[str, dict] = {}
        # The keys whose kept output was rejected (it no longer builds):
        # what the author writes in their place replaces them.
        self.rejected: set[str] = set()

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
        build (JARVIS's pipeline changed since they were kept, or a kept
        slide no longer fits beside the rest). The author writes them all
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


def review_units(run: ps.Run, built: Built, script: dict) -> dict[str, step_cache.Unit]:
    """Each slide's review unit: its source part as built, its fit, its
    script slide, the deck's questions and the sender's answers to an
    earlier run's findings on the review."""
    _, parts = parts_of(_read_source(run))
    by_id = {s.get("id"): s for s in script.get("slides") or [] if isinstance(s, dict)}
    findings = ps.findings_for(run.doc, REVIEW_LABEL)
    tool = review_tool(run.doc)
    out = {}
    for sid, part in parts.items():
        fit = deck_fit.of_slide(built.fit, sid) or {}
        out[sid] = step_cache.Unit(SLIDE_REVIEW, sid, {
            "part": step_cache.digest(part), "fit": step_cache.digest(
                {k: v for k, v in fit.items() if k != "slide"}),
            "script": step_cache.digest(by_id.get(sid)), "tool": tool,
            "findings": findings})
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
    script = package_script.read(run.script) or {}
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
        state["unfit"] = bool(getattr(problems, "unfit", False))
        last["built"] = built
        return built

    def begin_review(built: Built) -> list[str]:
        """Each slide's review reused when kept; the slides left to review."""
        reviews = review_units(run, built, script)
        reviewed: dict[str, dict] = {}
        only = [sid for sid in plan.ids if sid in reviews
                and not _reuse_review(run, reviews[sid], reviewed)]
        units.update(phase="review", only=only, said=False, reviewed=reviewed,
                     inputs={sid: u.inputs for sid, u in reviews.items()})
        flow.clear()
        flow.update({"stage": "deck-pass", "pass": 1,
                     "slides": only if len(only) < len(plan.ids) else None})
        return only

    plan = Plan(run, script)
    if units.get("phase") != "review":
        plan.reuse()
        if plan.complete:
            plan.start_source()
            if build_now() is None and state.get("unfit"):
                return _not_fit(run, kept, state)
            if last.get("built") is not None and not begin_review(last["built"]):
                return _finish(run, kept, plan, outcome, last, state, build_now, began, True)

    async def go():
        swarm_ = ps.build(run, kept, DECK_CONCERNS)
        source = DeckSource(run, build_now)

        async def written(note: str) -> bool:
            """The author writes the slides not kept (every slide when none
            is), the kept ones put back, until the deck builds and proves,
            at most ROUNDS_MAX more times."""
            for attempt in range(ROUNDS_MAX + 1):
                await swarm_.answer(ps.AUTHOR, ps.TIMEOUT, note if attempt == 0 else
                                    revise_note(run), ps.TALK)
                back = plan.put_back()
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
            return False

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
                revise=lambda items: written(revise_note(run)), given=given, clock=clock,
                prefix="deck-")
            if end == "waiting":
                outcome["waiting"] = True
            elif end == "unchecked":
                outcome.update(result="not passed",
                               findings=_findings_of_build(state["problems"]))
            else:
                outcome.update(result=end, findings=flow.get("left") or [])
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
    reviews = {sid: step_cache.Unit(SLIDE_REVIEW, sid, inputs)
               for sid, inputs in (units.get("inputs") or {}).items()}
    built = last.get("built") or (build_now() if passed else None)
    if passed and built is not None:
        built = _final(run, plan, reviews, units.get("reviewed") or {}, build_now) or built
    if built is None and state.get("unfit"):
        return _not_fit(run, kept, state)
    passed = passed and built is not None
    step = step_cache.Unit(STEP_UNIT, "", {"reviews": sorted(u.key for u in reviews.values()),
                                           "source": step_cache.file_sha(source_of(run))})
    if not (passed and "swarm" not in outcome and step_cache.reuse(step, doc, run.folder)):
        swarm = outcome.get("swarm") or {**(kept.get("swarm") or {})}
        result = {**swarm, "result": "passed" if passed else "not passed",
                  "program": [readable(built.fit)] if built is not None else [],
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
            "review": str(run.folder / REVIEW), "fit": str(built.folder / deck_fit.FILE),
            "slides_reviewed": only})
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


def _final(run: ps.Run, plan: Plan, reviews: dict[str, step_cache.Unit], reviewed: dict,
           build_now: Callable[[], Optional[Built]]) -> Optional[Built]:
    """The review passed: each slide reviewed now kept under its review key
    with its source as reviewed; the final source is each reused slide's
    reviewed part and the rest as they stand, built (reused when built
    before). The final build."""
    began = time.time()
    header, parts = parts_of(_read_source(run))
    for sid, unit in reviews.items():
        if sid not in reviewed and sid in parts:
            step_cache.computed(unit, run.doc, began, **_part_entry(run, parts[sid]))
    final = assemble(header, [reviewed.get(sid) or parts[sid] for sid in plan.ids
                              if sid in parts or sid in reviewed])
    if final != _read_source(run):
        _write_source(run, final)
        return build_now()
    return None


def _not_fit(run: ps.Run, kept: dict, state: dict):
    ps._save(run, kept)
    raise package_jobs.Blocked(
        DECK, "JARVIS could not make the deck fit its frame: "
              + " ".join(state.get("problems") or []),
        "JARVIS laid each slide out with its own fit (its words down to the 13 px floor, "
        "then smaller gaps and shorter bars) and measured it at every frame from 662 x 489 "
        "to 1600 x 900; the author was told to split it and revised it.", NOT_FIT_FIX)
