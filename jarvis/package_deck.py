"""The deck step of a package run (workflow.yaml deck, approved by Tim
2026-11-19; deck_review's framing approved 2026-11-20): the passed script
turned into a deck in the same swarm, built and proven by JARVIS's own
presentation pipeline, and compared slide by slide with the script.

Run after the swarm passes, in JARVIS's own process (package_jobs.PRODUCERS),
in the run's folder. A round:

  write_deck       the author, in the same swarm, writes
                   `deck/presentation.yaml` (presentation/1) from the passed
                   script alone, choosing JARVIS's named layouts; where the
                   script gives Tim's own words, they are runs in the
                   `his-words` style (presentation_schema.HIS_WORDS), drawn
                   highlighted in a very bright, vibrant way, and narrated.
                   The program's words to it (`write_note`) carry the
                   format, word for word from docs/making-a-package.md, and
                   a complete example.
  build_and_prove  the program fills each picture's size and hash
                   (make_package.fill_assets), compiles and proves it
                   (make_package.make_presentation: every picture drawn
                   readable at every frame, every word's style at least
                   the text floor at both sizes, the story's targets, the
                   renderer JARVIS's own, the story about fifteen minutes at
                   most), and checks the package carries the standard vote,
                   Approve and Reject at the check mark. What does not build
                   is said into the conversation as JARVIS's own turn, to the
                   author.
  deck_review      the program posts, as its own turn in the same
                   conversation, each slide's layout data (the deck
                   compiler's and the layout proof's, presentation_proof.
                   layout_data, .jarvis/layout-proof.json) beside the
                   passed script and the questions (DECK_QUESTIONS); the
                   editor and the four judges answer with their approved
                   prompts; then, as in the swarm, a question closed on an
                   earlier deck reopens when a majority of its voters judge
                   the change touched it, and the program calls a vote on
                   every open question. The editor and the judges vote; the
                   author never on its own deck.

As in the swarm, the deck passes when every deck question is proven; after
three rounds without that (a round whose deck did not build among them),
nothing is delivered: the sender is sent the findings (package_jobs.
failed_swarm) and its answer starts a new run. A passed deck's zip, its
built folder and the review are kept on the job (`deck`) for the deliver
step (package_deliver.py).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import yaml

from jarvis import (data_paths, decision_package as dp, make_package, package_jobs,
                    package_evidence as pe, package_inbox, package_swarm as ps, presentation_proof,
                    sessions)
from jarvis.presentation_schema import HIS_WORDS

DECK = "deck"
FOLDER = "deck"                         # the author's source, in the run's folder
SOURCE = "presentation.yaml"
BUILT = "deck-built"                    # each round's build, in the run's folder
REVIEW = "deck-review.json"
PROGRAM = "jarvis"                      # the program's own turns in the conversation
ROUNDS_MAX = sessions.ROUNDS_MAX        # as in the swarm
DOC = data_paths.SOURCE_ROOT / "docs" / "making-a-package.md"
FORMAT_START, FORMAT_END = "## A deck: one presentation.yaml", "## A folder"

# deck_review's questions, each the design's words (workflow.yaml deck
# deck_review questions), and the settled check that every quote of his
# uses his named style.
DECK_QUESTIONS = (
    sessions.Question("deck-says", "Does each slide say what the passed script says for it, "
                                   "nothing lost or added?"),
    sessions.Question("deck-visual", "Is each slide visual where the script calls for it, not "
                                     "one line of text spoken over?"),
    sessions.Question("deck-narration", "Does the narration say the substance, not read "
                                        "headings?"),
    sessions.Question("deck-readable", "Is every word on the slide readable at the size the "
                                       "layout data gives it?"),
    sessions.Question("deck-his-words", f"Does every quote of Tim's own words use the "
                                        f"{HIS_WORDS} style?"),
)
DECK_IDS = frozenset(q.id for q in DECK_QUESTIONS)
# The participants who answer the program's review turn, in order.
REVIEWERS = (ps.EDITOR, *ps.JUDGE_QUESTION)


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
        f"slide of the script is a slide of the deck: its words on the slide, what JARVIS says "
        f"over it as its narration. Each question is a decision with the options you wrote.",
        f"Where the script gives Tim's own words, show them on the slide as runs with emphasis "
        f"{HIS_WORDS}, which JARVIS draws highlighted in a very bright, vibrant way, and have "
        f"JARVIS narrate them; never 'this is what you said' with a block of text.",
        "JARVIS fills each picture's size and hash, builds the deck and proves it; what does "
        "not build comes back to you here. The editor and the judges then compare each slide "
        "with the script.",
        sessions._tag("format", deck_format()),
        sessions._tag("example", example(), file=SOURCE)])


def revise_note(run: ps.Run) -> str:
    return (f"Revise the deck in {source_of(run)}, or answer each point raised. Say what you "
            f"changed and why.")


def build_problems_note(problems: list[str]) -> str:
    return "The deck did not build. Revise it:\n" + "\n".join(f"- {p}" for p in problems)


# ── build and prove ───────────────────────────────────────────────────────

class Built:
    def __init__(self, archive: Path, folder: Path, proof: dict, deck: dict):
        self.archive, self.folder, self.proof, self.deck = archive, folder, proof, deck


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
    return Built(made.archive, folder, proof, made.deck), []


# ── the review ────────────────────────────────────────────────────────────

def review_turn(run: ps.Run, built: Built) -> str:
    """The program's own turn in the conversation: each slide's layout data
    beside the passed script, and the questions (workflow.yaml deck
    deck_review how)."""
    return "\n\n".join([
        "The deck written from the passed script is built and proven. Below, for each slide, "
        "the layout data JARVIS computed as it compiled and proved it (each slide's text and "
        "pictures with their positions and sizes at the smallest and the largest frame, in "
        "deck units on the 880 x 640 canvas and in pixels) and its narration, beside the "
        "passed script. No browser and no screenshots. Compare each slide with the script.",
        sessions._tag("script", ps._read(run.script), file=ps.SCRIPT_MD),
        sessions._tag("layout", json.dumps(built.proof.get("slides") or [], indent=1,
                                           ensure_ascii=False), file=presentation_proof.PROOF_FILE),
        "The questions to prove, by id:\n" + "\n".join(
            sessions._tag("question", q.asks, id=q.id) for q in DECK_QUESTIONS)])


def _findings_of_build(problems: list[str]) -> dict:
    return {"question": "deck-built", "asks": "Does the deck build and prove?", "state":
            sessions.FAILED, "against": [{"participant": PROGRAM, "reason": " ".join(problems),
                                         "ask": ""}]}


def _write_review(run: ps.Run, result: dict) -> None:
    pe.write_file(run.folder / REVIEW, json.dumps(result, indent=2, ensure_ascii=False)
                  .encode("utf-8"))
    pe.write_file(run.folder / ps.CONVERSATION, ps.conversation_md(result).encode("utf-8"))


def deck(doc: dict) -> Optional[str]:
    """The deck step of one run (package_jobs.PRODUCERS["deck"]): WAITING
    when the deck failed its three rounds and the sender is sent the
    findings."""
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
    state = kept.setdefault(DECK, {"rounds": 0})
    outcome: dict = {}

    async def go():
        swarm_, _ = ps.build(run, kept, DECK_QUESTIONS)
        try:
            await swarm_.open()
            while True:
                state["rounds"] += 1
                await swarm_.turn(ps.AUTHOR, ps.TIMEOUT, add=write_note(run)
                                  if state["rounds"] == 1 else revise_note(run))
                built, problems = build_and_prove(run, state["rounds"])
                state["problems"] = problems
                if built is None:
                    swarm_.say(PROGRAM, build_problems_note(problems))
                else:
                    swarm_.script_now(source_of(run).read_text(encoding="utf-8"))
                    swarm_.say(PROGRAM, review_turn(run, built))
                    for name in REVIEWERS:
                        await swarm_.turn(name, ps.TIMEOUT)
                    await swarm_.reopen_changed(ps.TIMEOUT, only=set(DECK_IDS), what="deck")
                    await swarm_.vote_open(ps.TIMEOUT)
                proven = built is not None and all(
                    swarm_.state[q] == sessions.PROVEN for q in DECK_IDS)
                if proven or state["rounds"] >= ROUNDS_MAX:
                    result = swarm_.result()
                    result["findings"] = [f for f in result["findings"] if f["question"]
                                          in DECK_IDS] + ([_findings_of_build(problems)]
                                                          if built is None else [])
                    result["result"] = "passed" if proven else "not passed"
                    result["deck_rounds"] = state["rounds"]
                    outcome.update(result=result, built=built)
                    return
                ps._keep(run, swarm_, kept)
        finally:
            ps._keep(run, swarm_, kept)
            await swarm_.close()
    package_jobs.asked(DECK, "the swarm",
                       "JARVIS asked the author to write the deck from the passed script, built "
                       "and proved it, and had the editor and the judges compare it with the "
                       "script.", go())
    result, built = outcome["result"], outcome["built"]
    _write_review(run, result)
    if result["result"] == "passed":
        package_jobs.noted(doc["id"], DECK, {
            "result": "passed", "rounds": result["deck_rounds"], "archive": str(built.archive),
            "folder": str(built.folder), "source": str(source_of(run)),
            "review": str(run.folder / REVIEW)})
        return None
    package_jobs.noted(doc["id"], DECK, {"result": "not passed",
                                         "rounds": result["deck_rounds"],
                                         "review": str(run.folder / REVIEW)})
    package_jobs.failed_swarm(doc["id"], ps.findings_questions(result), result["findings"])
    return package_jobs.WAITING
