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
                   of the deck's questions (DECK_CONCERNS); what is left is
                   merged by meaning into one list, the author works it, and
                   each owner re-checks its own items (package_swarm.
                   fix_passes).

Every build is measured too (deck_fit, Tim 2026-11-22): JARVIS draws the
built deck as its package frame does, at every frame from 662 x 489 to
1600 x 900, and reads for each slide whether anything on it is clipped or
needs scrolling. The shared deck fits its slides itself (deck.js
fitSlide); the fit goes to the reviewers beside each slide's layout data as
a fact, never a question for them or the sender. A slide that still does
not fit does not build: the author is told, an owner's edit that makes one
unfit is not applied, and a deck JARVIS cannot make fit stops the step in
Error (NOT_FIT_FIX), never findings for the sender.

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
from pathlib import Path
from typing import Callable, Optional

import yaml

from jarvis import (data_paths, deck_fit, decision_package as dp, make_package, package_jobs,
                    package_evidence as pe, package_inbox, package_script, package_swarm as ps,
                    presentation_proof, sessions, spoken_words)
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
# The deck review is run by the same owners, the same way (construction-
# drafts.md "Cooperative swarm"): each owner and the editor signs off each
# of the deck's questions.
DECK_CONCERNS = tuple(sessions.Concern(f"{qid}:{owner}", asks, owner)
                      for qid, asks in DECK_QUESTIONS for owner in ps.FIXERS)
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


def build_and_prove(run: ps.Run, round_no: int) -> tuple[Optional[Built], list[str]]:
    """The author's source filled, compiled and proven by JARVIS's own
    presentation pipeline (make_package.make_presentation), into
    `deck-built/<round>/`; (the build, []) or (None, every problem)."""
    source = source_of(run)
    if not source.is_file():
        return None, [f"There is no {SOURCE} at {source}."]
    out = run.folder / BUILT / str(round_no)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    try:
        make_package.refuse_json_deck(source)
        make_package.fill_assets(source)
        made = make_package.make_presentation(source, out)
    except dp.Refused as e:
        return None, [str(p) for p in e.problems]
    except (OSError, ValueError, yaml.YAMLError) as e:
        return None, [str(e)]
    folder = out / "compiled" / made.deck["name"]
    try:
        proof = json.loads((folder / presentation_proof.PROOF_FILE).read_text(encoding="utf-8"))
        manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
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
        fit = MEASURE(folder)
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
    return Built(made.archive, folder, proof, made.deck, fit), []


# ── the review ────────────────────────────────────────────────────────────

def review_turn(run: ps.Run, built: Built) -> str:
    """The program's own turn in the conversation: slide by slide, each
    slide of the passed script beside the layout data of its deck slide,
    then the questions (workflow.yaml deck deck_review how)."""
    script = package_script.read(run.script) or {}
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
        "narration, and its fit: JARVIS drew every slide at every frame from 662 x 489 to "
        "1600 x 900 and measured that nothing on it, words, pictures or a decision's controls, "
        "is clipped or needs scrolling. Fit is the program's to measure and to fix, a fact "
        "here: it is not for the review or the sender. No screenshots. Compare each slide "
        "with the script.",
        slides,
        "The deck's questions, by id:\n" + "\n".join(
            sessions._tag("question", asks, id=qid) for qid, asks in DECK_QUESTIONS)])


def _with_fit(layout: Optional[dict], fit: dict, slide_id: str) -> Optional[dict]:
    """A slide's layout data with its measured fit (deck_fit) beside it."""
    measured = deck_fit.of_slide(fit, slide_id)
    if layout is None or measured is None:
        return layout
    return {**layout, "fit": {"fits": measured["fits"], "said": deck_fit.said(measured)}}


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


def deck(doc: dict) -> Optional[str]:
    """The deck step of one run (package_jobs.PRODUCERS["deck"]): WAITING
    when it waits on the sender (questions, or what is left after its fix
    passes)."""
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
    state = kept.setdefault(DECK, {"builds": 0})
    flow = kept.setdefault("deck_flow", {"stage": "deck-write", "pass": 1})
    clock = ps.Clock(kept)
    outcome: dict = {}
    last: dict = {}

    def build_now() -> Optional[Built]:
        state["builds"] += 1
        built, problems = build_and_prove(run, state["builds"])
        state["problems"] = list(problems)
        state["unfit"] = bool(getattr(problems, "unfit", False))
        last["built"] = built
        return built

    async def go():
        swarm_ = ps.build(run, kept, DECK_CONCERNS)
        source = DeckSource(run, build_now)

        async def written(note: str) -> bool:
            """The author writes or revises the deck until it builds and
            proves, at most ROUNDS_MAX more times; the review said once it
            does."""
            for attempt in range(ROUNDS_MAX + 1):
                await swarm_.answer(ps.AUTHOR, ps.TIMEOUT, note if attempt == 0 else
                                    revise_note(run), ps.TALK)
                built = build_now()
                if built is not None:
                    swarm_.say(PROGRAM, review_turn(run, built))
                    return True
                swarm_.say(PROGRAM, build_problems_note(state["problems"]))
            return False

        async def nothing() -> None:
            return None

        def given(name: str) -> str:
            return ("Review the deck, above, against the passed script, for your concerns.\n"
                    + sessions._tag(DeckSource.key, source.text(), file=SOURCE))
        try:
            await swarm_.open()
            ps._take_answers(run, swarm_, kept)
            if flow["stage"] == "deck-write":
                with clock("deck-write"):
                    if not await written(write_note(run)):
                        outcome.update(result="not passed",
                                       findings=_findings_of_build(state["problems"]))
                        return
                flow["stage"] = "deck-pass"
                ps._keep(run, swarm_, kept)
            elif last.get("built") is None and build_now() is None:
                flow.pop("review_again", None)
                swarm_.say(PROGRAM, build_problems_note(state["problems"]))
            elif flow.pop("review_again", False):
                # A run that goes on from the run before it (package_resume):
                # the deck built again, with JARVIS's layout as it is now.
                swarm_.say(PROGRAM, review_turn(run, last["built"]))
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
        return package_jobs.WAITING
    built = last.get("built")
    if built is None and state.get("unfit"):
        ps._save(run, kept)
        raise package_jobs.Blocked(
            DECK, "JARVIS could not make the deck fit its frame: "
                  + " ".join(state.get("problems") or []),
            "JARVIS laid each slide out with its own fit (smaller gaps, headings at the body "
            "size, shorter bars) and measured it at every frame from 662 x 489 to 1600 x 900; "
            "the author was told and revised it.", NOT_FIT_FIX)
    passed = outcome["result"] == "passed" and built is not None
    result = {**outcome["swarm"], "result": "passed" if passed else "not passed",
              "findings": [] if passed else (outcome["findings"] or _findings_of_build(
                  state.get("problems") or [])),
              "deck_passes": flow["pass"], "times": kept.get("times") or []}
    _write_review(run, result)
    if passed:
        package_jobs.noted(doc["id"], DECK, {
            "result": "passed", "passes": flow["pass"], "archive": str(built.archive),
            "folder": str(built.folder), "source": str(source_of(run)),
            "review": str(run.folder / REVIEW), "fit": str(built.folder / deck_fit.FILE)})
        return None
    package_jobs.noted(doc["id"], DECK, {"result": "not passed", "passes": flow["pass"],
                                         "review": str(run.folder / REVIEW)})
    package_jobs.failed_swarm(doc["id"], ps.findings_questions(result["findings"]),
                              result["findings"])
    return package_jobs.WAITING
