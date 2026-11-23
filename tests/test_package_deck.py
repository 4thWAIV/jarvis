"""The deck and deliver steps (package_deck.py, package_deliver.py; workflow.yaml
deck and deliver): the author writes the deck from the passed script in the
same swarm, JARVIS builds and proves it, the owners and the editor compare
each slide with the script from the layout data alone, edit the deck and
sign each of its questions off (the cooperative swarm), the
package is assembled with its briefing, its message written by the program,
signed with JARVIS's own key and sent back to its sender to check; its
approval delivers it on the Work item's Messages tab, its words of what is
wrong start a new run."""
from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from jarvis import (make_package, model_tiers, package_deck as pd, package_deliver as pdl,
                    package_jobs as pj, package_mark, package_signature, package_swarm as ps,
                    presentation_proof, sessions)
from jarvis.knowledge.embedder import FakeEmbedder
from tests.test_agent_keys import keygen  # noqa: F401
from tests.test_package_gather import checkout, gh, indexing, transcript  # noqa: F401
from tests.test_package_swarm import (Marking, Plan, _chat_factory, marked,  # noqa: F401
                                      script_of)


def deck_with_his_words() -> dict:
    """The example deck, its opening quoting Tim in his style and saying his
    words in its narration."""
    deck = copy.deepcopy(make_package.EXAMPLE)
    deck["slides"][0]["objects"][0]["paragraphs"].append(
        {"id": "opening-words-his", "role": "paragraph",
         "runs": [{"id": "opening-words-his-1", "text": "Keep it in the office.",
                   "emphasis": "his-words"}]})
    deck["slides"][0]["narration"][0]["say"] += " In your words: keep it in the office."
    return deck


def deck_script() -> dict:
    """The passed script the example deck is written from: one script slide
    to each deck slide, by id, Tim's words quoted on the opening."""
    opening, changed, where = (script_of(words, "files/situation.md", sid)["slides"][0]
                               for words, sid in (
                                   ("Friday's lunch is now in the office, at noon.", "opening"),
                                   ("Food is brought in.", "what-changed"),
                                   ("Where do we eat on Friday?", "where")))
    opening["title"] = "Friday's lunch has moved"
    opening["on_screen"].append({"kind": "his_words", "text": "Keep it in the office.",
                                 "source": "jarvis/tim.md", "position": "3-25", "path": "",
                                 "rows": []})
    where["decision"] = {"id": "where-to-eat", "question": "Where do we eat on Friday?",
                         "options": [{"id": "office", "name": "Office", "says": "In."},
                                     {"id": "out", "name": "Out", "says": "Out."}]}
    return {"version": "script/1", "slides": [opening, changed, where]}


class DeckPlan(Plan):
    """The swarm's participants, the author writing the deck when asked."""

    def __init__(self, run_folder: Path):
        super().__init__(run_folder)
        self.script = deck_script()
        self.decks: list[dict] = [deck_with_his_words()]

        self.deck_edits: dict[str, list[dict]] = {}
        # Each deck turn's script slides the author changes (`slides`), and
        # the outline slides of new ones (`outline`).
        self.script_slides: list[list[dict]] = []
        self.outlines: list[list[dict]] = []

    def answer(self, name, text, form):
        if name == "author" and set(form["properties"]) == {"message", "slides", "forms", "outline"} and (
                "Now write the deck" in text or "Revise the deck" in text):
            self.sent.setdefault(name, []).append(text)
            deck = self.decks.pop(0) if len(self.decks) > 1 else self.decks[0]
            source = self.folder / pd.FOLDER / pd.SOURCE
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(yaml.safe_dump(deck, sort_keys=False))
            slides = self.script_slides.pop(0) if self.script_slides else []
            outline = self.outlines.pop(0) if slides and self.outlines else []
            return {"message": "author writes the deck", "slides": slides, "forms": [],
                    "outline": outline}
        answer = super().answer(name, text, form)
        if "signoff" in form["properties"] and "deck-says:" + name in str(form):
            answer["edits"] = self.deck_edits.pop(name, [])
        return answer


@pytest.fixture
def deck_plan(marked, monkeypatch):  # noqa: F811
    plan = DeckPlan(Path(marked["folder"]))
    monkeypatch.setattr(ps, "CHAT", _chat_factory(plan))
    monkeypatch.setattr(ps, "PICK", lambda kind, tier, **kw: model_tiers.Pick(
        "claude", {1: "fable", 2: "opus"}.get(tier, "sonnet"), tier, "test", ["x"]))
    monkeypatch.setattr(package_mark, "MARKING", lambda doc, **pin: Marking())
    embedder = FakeEmbedder(64)
    monkeypatch.setattr(ps, "EMBED", lambda texts: [list(v) for v in
                                                    embedder.embed(texts, "query")])
    Marking.calls = []
    Marking.slides = []
    return plan


def _passed(doc):
    ps.research(pj.get(doc["id"]))
    assert ps.swarm(pj.get(doc["id"])) is None
    return pj.get(doc["id"])


def _said_on_the_deck(review: dict) -> list[str]:
    """The program's turns since the author first wrote the deck."""
    conversation = review["conversation"]
    start = conversation.index({"from": "author", "text": "author writes the deck"})
    return [m["text"] for m in conversation[start:] if m["from"] == pd.PROGRAM]


# ── the deck ──────────────────────────────────────────────────────────────

def test_the_deck_is_written_built_proven_and_compared_with_the_script(marked, deck_plan):
    doc = _passed(marked)
    assert pd.deck(doc) is None
    job = pj.get(doc["id"])
    assert job["deck"]["result"] == "passed" and job["deck"]["passes"] == 1
    # The author was told to write it from the passed script alone, in
    # JARVIS's named layouts, his words in his style, with the format word
    # for word and a complete example.
    told = deck_plan.sent["author"][-1]
    assert "Now write the deck from the passed script alone" in told
    assert "one slide of the deck, in the same order and with the same id" in told
    assert '<script file="script.yaml">version: script/1' in told
    assert "emphasis his-words" in told and "very bright, vibrant way" in told
    assert pd.deck_format() in told and "## A deck: one presentation.yaml" in told
    assert "<example file=\"presentation.yaml\">" in told
    # It is told to turn dry text into visuals, with an example that does.
    assert pd.SHOW_NOTE in told and "Never put long paragraphs on a slide" in told
    # Every content slide built by program from the script's visuals; the
    # author arranges.
    assert pd.BUILT_NOTE in told and '<built file="presentation.yaml">' in told \
        and "opening-v1" in told
    assert '<visuals file="presentation.yaml">' in told and "type: steps" in told \
        and "type: chart" in told and "type: figures" in told and "type: callout" in told
    # JARVIS's own turn: slide by slide, each script slide beside its deck
    # slide's layout data, and the questions; no browser. His words are the
    # program's check: no vote is asked of them.
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    [turn] = [m for m in review["conversation"] if m["from"] == pd.PROGRAM
              and "built and proven" in m["text"]]
    assert [qid for qid, _ in pd.DECK_QUESTIONS] == ["deck-says", "deck-visual",
                                                     "deck-narration", "deck-readable",
                                                     "deck-shown"]
    # Each owner and the editor signs off that what can be shown is shown.
    assert {c.id for c in pd.DECK_CONCERNS if c.id.startswith("deck-shown:")} == \
        {f"deck-shown:{owner}" for owner in ps.FIXERS}
    # Type size is the program's: deck-readable is answered by the build
    # (every slot within its capacity), never asked of a reviewer.
    assert pd.PROGRAM_QUESTIONS == ("deck-readable",)
    assert all(f'<question id="{qid}">' in turn["text"] for qid, _ in pd.DECK_QUESTIONS
               if qid != "deck-readable")
    assert '<question id="deck-readable">' not in turn["text"]
    assert "Type size is the program's" in turn["text"]
    [answered] = review["program"]
    assert answered["question"] == "deck-readable" and answered["state"] == "resolved"
    assert "within its capacity at 662 x 489" in answered["reason"]
    slides = turn["text"].split('<slide id="')[1:]
    assert [s.split('"')[0] for s in slides] == ["opening", "what-changed", "where"]
    assert '<script file="script.yaml">id: opening' in slides[0]
    layout = [json.loads(s.split('<layout file=".jarvis/layout-proof.json">')[1]
                         .split("</layout>")[0]) for s in slides]
    opening = layout[0]
    assert opening["layout"] == "opening" and opening["title_px_range"]["smallest"][0] >= 13
    assert opening["title_px_range"]["largest"][1] == 64
    words = opening["objects"][0]
    assert words["capacity"] == {"holds_lines": 22, "needs_lines": words["capacity"]["needs_lines"],
                                 "fits": True} and 0 < words["capacity"]["needs_lines"] < 22
    assert words["size_px"]["smallest"][0] > 0 and words["size_px"]["largest"][0] > \
        words["size_px"]["smallest"][0]
    runs = [r for p in words["paragraphs"] for r in p["runs"]]
    # The script's words, built by program; his words in his style.
    assert {"id": "opening-v1-p2-r1", "text": "Keep it in the office.",
            "emphasis": "his-words"} in runs
    assert opening["narration"][0]["say"].startswith("This tells you")
    assert layout[-1]["type"] == "decision" and layout[-1]["question"] == \
        "Where do we eat on Friday?"
    # A decision slide's words are sized as the shared deck draws them, and
    # what JARVIS says on it is there too (deck_review needs both).
    assert layout[-1]["text_px_range"]["question"] == {"smallest": [13.0, 58.7],
                                                        "largest": [18.3, 64.0]}
    assert layout[-1]["text_px_range"]["says"]["smallest"][0] >= 13
    assert layout[-1]["narration"][0]["say"] == "Decision 1 of 1: Where do we eat on Friday?"
    # The same owners and the editor reviewed it in parallel, each signing
    # off each of the deck's questions; the author never on its own deck.
    after = [m["from"] for m in review["conversation"][review["conversation"].index(turn) + 1:]]
    assert after[:4] == ["tims_reader", "context_owner", "structure_owner", "editor"]
    signed = {(s["concern"], s["owner"]) for s in review["signoffs"]
              if s["stage"] == "deck-pass-1"}
    assert signed == {(f"{qid}:{o}", o) for qid, _ in pd.DECK_QUESTIONS for o in ps.FIXERS
                      if qid not in pd.PROGRAM_QUESTIONS}
    # The built deck is proven, carries his vote and the layout data.
    folder = Path(job["deck"]["folder"])
    proof = json.loads((folder / presentation_proof.PROOF_FILE).read_text())
    assert proof["proven"] and proof["slides"] == layout
    assert json.loads((folder / "package.json").read_text())["vote"] == "approve-package"


def test_what_does_not_build_goes_back_to_the_author(marked, deck_plan):
    broken = deck_with_his_words()
    broken["slides"][0]["layout"] = "no-such-layout"
    deck_plan.decks = [broken, deck_with_his_words()]
    doc = _passed(marked)
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    said = _said_on_the_deck(review)
    assert said[0].startswith("The deck did not build. Revise it:") and "no-such-layout" in \
        said[0]
    assert "Revise the deck in" in deck_plan.sent["author"][-1]
    assert json.loads((Path(doc["folder"]) / ps.STATE).read_text())["deck"]["builds"] == 2


def test_the_deck_is_checked_against_the_script_by_program(marked, deck_plan):
    # write_deck: one script slide to one deck slide; his_words_check: each
    # quote of his drawn in his style and said in the narration (no vote).
    plain = deck_with_his_words()
    plain["slides"][0]["objects"][0]["paragraphs"][-1]["runs"][0]["emphasis"] = "normal"
    plain["slides"][0]["narration"][0]["say"] = "This tells you where lunch is."
    reordered = deck_with_his_words()
    reordered["slides"][0], reordered["slides"][1] = reordered["slides"][1], \
        reordered["slides"][0]
    deck_plan.decks = [plain, reordered, deck_with_his_words()]
    doc = _passed(marked)
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    said = _said_on_the_deck(review)
    # His words are drawn in his style by program (the script's objects);
    # his words said in the narration is still checked.
    assert "narration does not say Tim's words 'Keep it in the office.'" in said[0]
    assert "the script's are ['opening', 'what-changed', 'where']" in said[1]
    assert json.loads((Path(doc["folder"]) / ps.STATE).read_text())["deck"]["builds"] == 3
    assert not [c for c in review["concerns"] if "his" in c["id"]]


def test_a_script_slide_that_asks_a_decision_is_the_deck_s_decision_slide():
    # presentation/1 asks a decision on a slide of its own, so the one deck
    # slide of a script slide that asks one is that decision slide.
    from jarvis import script_visuals
    script = deck_script()
    deck = script_visuals.apply(deck_with_his_words(), script)
    assert pd.script_problems(script, deck) == []
    # Backstops: an object that is not the script's, and his words not in
    # his style, are each said.
    plain = copy.deepcopy(deck)
    plain["slides"][0]["objects"][0]["paragraphs"][-1]["runs"][0]["emphasis"] = "normal"
    assert pd.script_problems(script, plain) == [
        "Slide opening does not draw Tim's words 'Keep it in the office.' in the his-words "
        "style.",
        "Slide opening's text opening-v1 is not the script's: JARVIS builds every visual from "
        "the script, and the deck only arranges its layout and slots."]
    content = copy.deepcopy(deck)
    content["slides"][2] = {**deck["slides"][1], "id": "where"}
    assert pd.script_problems(script, content) == [
        "Script slide where asks the decision where-to-eat: its deck slide is a decision slide "
        "(type decision, id where) asking where-to-eat, with its recommendation and reason; "
        "JARVIS puts its question, options, words, say and what it shows on it."]
    script["slides"][2]["decision"] = None
    assert pd.script_problems(script, deck) == [
        "Script slide where asks no decision: its deck slide is a content slide."]


def _steps_slide(passed: dict) -> dict:
    changed = copy.deepcopy(passed["slides"][1])
    changed["on_screen"] = [{"kind": "steps", "steps": [
        {"title": "Order by Thursday noon", "says": ""},
        {"title": "Food is brought in", "says": "The team budget pays."}]}]
    return changed


def _signed_at(review: dict, stage: str) -> set[tuple[str, str]]:
    return {(s["concern"], s["owner"]) for s in review["signoffs"] if s["stage"] == stage}


def test_a_slide_that_needs_another_step_goes_back_to_the_script_stage_for_that_slide_only(
        marked, deck_plan):
    # Never a dead end at the deck: what only a change of a script slide
    # gives (another step, a different form) the author gives as that slide
    # of the script, whole; JARVIS checks it and puts it in the script, and
    # the slide goes back through the script stage for that slide only:
    # marked against the labels, every owner's concern on the script signed
    # off again for it; then its deck slide is written again and reviewed.
    from jarvis import package_script, step_cache
    passed = deck_script()
    changed = _steps_slide(passed)
    deck_plan.script_slides = [[{**changed, "id": "no-such-slide"}], [changed]]
    doc = _passed(marked)
    Marking.slides = []
    assert pd.deck(doc) is None
    folder = Path(doc["folder"])
    script = package_script.read(folder / package_script.FILE)
    assert script["slides"][1] == changed and script["slides"][0] == passed["slides"][0]
    source = yaml.safe_load((folder / pd.FOLDER / pd.SOURCE).read_text())
    assert [o["type"] for o in source["slides"][1]["objects"]] == ["steps"]
    review = json.loads((folder / pd.REVIEW).read_text())
    said = _said_on_the_deck(review)
    assert any("'no-such-slide' is a new slide, so it comes right after the slide" in t
               for t in said)
    assert any("JARVIS put script slide(s) what-changed in the script as you gave them. They "
               "go back through the script stage" in t for t in said)
    assert any("The script stage passed script slide(s) what-changed" in t for t in said)
    # Marked for that slide alone.
    assert Marking.slides == [["what-changed"]]
    kept = json.loads((folder / ps.STATE).read_text())
    assert {m["slide"] for m in kept["marked"] if m["slide"] == "what-changed"} == \
        {"what-changed"}
    # Every owner's concern on the script signed off again, in the script
    # stage, for that slide only.
    assert _signed_at(review, "deck-script-pass-1") == {
        (c.id, c.owner) for c in ps.concerns() if c.id in ps.SCRIPT_CONCERNS}
    told = [t for t in deck_plan.sent["tims_reader"] if "script stage for those slides" in t]
    assert told and "Script slide(s) what-changed changed at the deck" in told[0]
    # Then its deck slide written again, and the deck reviewed with it.
    assert any("The script stage passed slide(s) what-changed. Revise the deck" in t
               for t in deck_plan.sent["author"])
    assert ("tims_reader" in {s["owner"] for s in review["signoffs"]
                              if s["stage"] == "deck-pass-1"})
    assert pj.get(doc["id"])["deck"]["result"] == "passed"
    # The slide as changed is kept, with its marks, keyed by the slide the
    # swarm passed, so a later run takes it back rather than ask again.
    entries = [json.loads(p.read_text()) for p in
               (step_cache.root(doc) / pd.SCRIPT_CHANGE).glob(f"*/*/{step_cache.ENTRY}")]
    assert [(e["name"], e["value"]["slides"]) for e in entries] == [("what-changed", [changed])]
    assert entries[0]["value"]["marks"] and {m["slide"] for m in entries[0]["value"]["marks"]} \
        == {"what-changed"}
    run = ps._run(pj.get(doc["id"]), pd.DECK)
    package_script.write(run.script, passed)
    state = ps._kept(run)
    state["marked"] = [m for m in state["marked"] if m["slide"] != "what-changed"]
    assert pd.restore_script_changes(run, passed, state) == ["what-changed"]
    assert package_script.read(run.script)["slides"][1] == changed
    assert {m["slide"] for m in state["marked"]} >= {"what-changed"}


def test_a_slide_that_needs_splitting_goes_back_to_the_script_stage_which_adds_it(
        marked, deck_plan):
    # A slide that needs splitting is never refused at the deck as a dead
    # end: the author gives the slide and the new one after it, with the new
    # one's outline slide; the outline gains it and the owners sign it off,
    # the script stage marks and signs both, and the deck gets the new slide.
    from jarvis import package_script, step_cache
    passed = deck_script()
    first = _steps_slide(passed)
    second = {**copy.deepcopy(passed["slides"][1]), "id": "what-changed-more",
              "title": "Who pays"}
    line = {"id": "what-changed-more", "title": "Who pays", "point": "The budget pays.",
            "answers": "merge-171", "background": True, "evidence": ["files/situation.md"],
            "visual": "text", "why": "One sentence."}
    deck_plan.script_slides = [[first, second]]
    deck_plan.outlines = [[line]]
    split = deck_with_his_words()
    split["slides"].insert(2, {**copy.deepcopy(split["slides"][1]), "id": "what-changed-more",
                               "narration": [{"id": "more-cue", "say": "Who pays.",
                                              "focus": []}]})
    deck_plan.decks = [deck_with_his_words(), split]
    doc = _passed(marked)
    Marking.slides = []
    assert pd.deck(doc) is None
    folder = Path(doc["folder"])
    script = package_script.read(folder / package_script.FILE)
    assert package_script.slide_ids(script) == ["opening", "what-changed", "what-changed-more",
                                                "where"]
    outline = package_script.read(folder / package_script.OUTLINE_FILE)
    assert line in outline["slides"]
    review = json.loads((folder / pd.REVIEW).read_text())
    assert _signed_at(review, "deck-script-outline") == {
        (c.id, c.owner) for c in ps.concerns() if c.id in ps.OUTLINE_CONCERNS}
    assert Marking.slides == [["what-changed", "what-changed-more"]]
    source = yaml.safe_load((folder / pd.FOLDER / pd.SOURCE).read_text())
    assert [s["id"] for s in source["slides"]] == package_script.slide_ids(script)
    assert pj.get(doc["id"])["deck"]["result"] == "passed"
    [entry] = [json.loads(p.read_text()) for p in
               (step_cache.root(doc) / pd.SCRIPT_CHANGE).glob(f"*/*/{step_cache.ENTRY}")]
    assert [s["id"] for s in entry["value"]["slides"]] == ["what-changed", "what-changed-more"]
    assert entry["value"]["outline"] == [line]
    # A new slide given without its outline slide does not check.
    run = ps._run(pj.get(doc["id"]), pd.DECK)
    assert pd.script_change(run, {"slides": [first, {**second, "id": "another"}]})[2] == [
        "outline: the new slide 'another' needs its outline slide, with the same id."]


def test_a_slide_changed_in_a_close_of_the_deck_review_is_reviewed_again_in_full(
        marked, deck_plan):
    # In a close, a script slide the author gives goes through the script
    # stage, its deck slide is written again, and every owner and the
    # editor review it in full for every deck concern, not only the owners
    # who raised items.
    from tests.test_package_swarm import LEFT
    changed = _steps_slide(deck_script())
    doc = _passed(marked)
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)]}
    deck_plan.script_slides = [[], [changed]]
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    assert _signed_at(review, "deck-script-pass-1")
    assert _signed_at(review, "deck-slides-1") == {(c.id, c.owner) for c in pd.DECK_CONCERNS}
    full = [t for t in deck_plan.sent["context_owner"] if "Review those slides in full" in t]
    assert full and "Slide(s) what-changed went back through the script stage" in full[0]
    assert pj.get(doc["id"])["deck"]["result"] == "passed"


def test_a_deck_not_signed_off_in_two_passes_sends_the_sender_what_is_left(marked,
                                                                          deck_plan):
    from tests.test_package_swarm import LEFT
    doc = _passed(marked)
    deck_plan.signoffs = {"editor": [("not_yet", LEFT)] * 4}
    assert pd.deck(doc) == pj.WAITING
    job = pj.get(doc["id"])
    assert job["deck"]["result"] == "not passed" and job["deck"]["passes"] == 2
    last = job["rounds"][-1]
    assert pj.kind_of(last) == pj.FINDINGS
    assert [q["id"] for q in last["questions"]] == ["left-1"]


def test_an_owner_s_edit_to_the_deck_stands_only_when_it_still_builds(marked, deck_plan):
    doc = _passed(marked)
    source = Path(doc["folder"]) / pd.FOLDER / pd.SOURCE
    deck_plan.decks = [deck_with_his_words()]
    text = yaml.safe_dump(deck_with_his_words(), sort_keys=False)
    at = text.index("layout: opening")
    deck_plan.deck_edits = {
        "editor": [{"start": at, "end": at + len("layout: opening"), "old": "layout: opening",
                    "new": "layout: no-such-layout", "reason": "x", "source": ""}]}
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    said = [m["text"] for m in review["conversation"] if m["from"] == pd.PROGRAM]
    assert any("editor's edits to presentation.yaml: 0 applied, 1 refused" in t for t in said)
    assert "layout: opening" in source.read_text(), "the deck as the author wrote it"


def test_each_object_s_capacity_is_a_fact_for_the_reviewers_and_no_browser_draws_it(
        marked, deck_plan):
    doc = _passed(marked)
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    [turn] = [m for m in review["conversation"] if m["from"] == pd.PROGRAM
              and "built and proven" in m["text"]]
    assert "each object's capacity" in turn["text"] and "drawn_px" not in turn["text"]
    layout = [json.loads(s.split('<layout file=".jarvis/layout-proof.json">')[1]
                         .split("</layout>")[0]) for s in turn["text"].split('<slide id="')[1:]]
    rooms = [o["capacity"] for one in layout for o in one.get("objects") or []]
    assert rooms and all(r["fits"] for r in rooms)
    # No package is drawn in a browser on its way (Tim, 18 November): no
    # measure, no fit record.
    assert not hasattr(pd, "MEASURE") and "fit" not in pj.get(doc["id"])["deck"]
    built = Path(pj.get(doc["id"])["deck"]["folder"])
    assert not (built / ".jarvis" / "fit.json").exists()
    assert not (Path(pd.__file__).parent / "deck_fit.py").exists()


def test_a_slot_over_its_capacity_goes_back_to_the_author_never_the_sender(marked, deck_plan):
    # The author arranges the script's objects: first the steps in the
    # small words slot, over its capacity; told how much over, it takes
    # JARVIS's arrangement.
    from jarvis import script_visuals
    changed = deck_plan.script["slides"][1]
    changed["on_screen"] += [
        {"kind": "callout", "label": "", "says": "Nobody has to book a table."},
        {"kind": "steps", "steps": [{"title": f"Step {n}", "says": "Food is brought in at noon "
                                     "and set out on the long table."} for n in range(1, 5)]}]
    built = script_visuals.built_slides(deck_plan.script)["slides"][1]
    assert built["layout"] == "two-pictures"
    assert [o["slot"] for o in built["objects"]] != ["first", "second", "words"]
    over = deck_with_his_words()
    over["slides"][1] = copy.deepcopy(built)
    for obj, slot in zip(over["slides"][1]["objects"], ("first", "second", "words")):
        obj["slot"] = slot
    fits = deck_with_his_words()
    fits["slides"][1] = copy.deepcopy(built)
    deck_plan.decks = [over, fits]
    doc = _passed(marked)
    assert pd.deck(doc) is None
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    said = _said_on_the_deck(review)
    assert said[0].startswith("The deck did not build.")
    assert "The words slot of slide what-changed (two-pictures) is over its capacity" in said[0]
    assert "lines of body text where it holds" in said[0] and "%) over" in said[0]
    # It offers only what can be done at the deck; a split goes back to the
    # script stage.
    from jarvis import deck_capacity
    assert "At the deck: " + deck_capacity.AT_DECK_WAYS in said[0]
    assert "split it across slides" not in said[0] and "Split the slide in two" not in said[0]
    assert pj.get(doc["id"])["deck"]["result"] == "passed"
    assert pj.get(doc["id"])["rounds"] == [], "nothing goes to the sender"


def test_a_decision_over_its_slide_goes_back_to_the_author(marked, deck_plan):
    # A decision's slide is held to what it holds as a content slide is
    # (its context is the script's, built by JARVIS; the reason the author's).
    long = deck_with_his_words()
    long["decisions"][0]["reason"] = "Where we eat on Friday matters to the whole team. " * 40
    deck_plan.decks = [long, deck_with_his_words()]
    doc = _passed(marked)
    assert pd.deck(doc) is None
    said = _said_on_the_deck(json.loads((Path(doc["folder"]) / pd.REVIEW).read_text()))
    assert said[0].startswith("The deck did not build.")
    assert "The decision where-to-eat (slide where) is over what its slide holds" in said[0]
    assert "the recommendation and its reason (`reason`)" in said[0] and "%) over" in said[0]
    assert pj.get(doc["id"])["deck"]["result"] == "passed"


def test_a_deck_still_over_capacity_is_the_run_s_error_never_the_sender_s(marked, deck_plan):
    # The author never makes it fit: a layout failure is the program's,
    # never findings for the sender (Tim 2026-11-22).
    long = deck_with_his_words()
    long["decisions"][0]["reason"] = "Where we eat on Friday matters to the whole team. " * 40
    deck_plan.decks = [long]
    doc = _passed(marked)
    with pytest.raises(pj.Blocked) as stopped:
        pd.deck(doc)
    assert stopped.value.step == "deck"
    assert stopped.value.reason.startswith("The deck is still over what its slides hold")
    assert "The decision where-to-eat (slide where)" in stopped.value.reason
    assert "lines of body text where it holds" in stopped.value.reason
    assert stopped.value.fix == pd.NOT_FIT_FIX
    assert json.loads((Path(doc["folder"]) / ps.STATE).read_text())["deck"]["builds"] == \
        pd.ROUNDS_MAX + 1
    assert pj.get(doc["id"])["rounds"] == [], "nothing goes to the sender"


# ── deliver ───────────────────────────────────────────────────────────────

def test_the_package_is_assembled_signed_and_sent_to_its_sender_to_check(marked, deck_plan,
                                                                         keygen):  # noqa: F811
    doc = _passed(marked)
    pd.deck(doc)
    assert pdl.deliver(pj.get(doc["id"])) == pj.WAITING
    job = pj.get(doc["id"])
    check = job["rounds"][-1]
    assert job["state"] == pj.WAITING and pj.kind_of(check) == pj.CHECK
    assert check["questions"] == [{"id": "check", "question": pj.SENDER_CHECK}]
    assert pj.SENDER_CHECK == (
        "This is the package JARVIS made from your request, before Tim sees it. Open it and "
        "judge broadly whether it does what you asked for and what Tim needs from it. Approve "
        "it, or say what is wrong, and a new run starts with your words.")
    # The message is the program's: the opening slide and the question the
    # request gave (never the deck author's wording of it).
    # The message is slide 1 of the passed script, then the question.
    assert check["package"]["message"] == (
        "Friday's lunch has moved\nFriday's lunch is now in the office, at noon.\nKeep it in "
        "the office.\n\nMerge PR 171?")
    assert "Where do we eat on Friday?" not in check["package"]["message"]
    archive = Path(check["package"]["archive"])
    signed = package_signature.read(archive)
    assert signed.by_jarvis() and signed.requested_by == f"agent:{job['session']}"
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
    assert {f".jarvis/briefing/{n}" for n in ("research.md", "prep.md", "outline.yaml",
                                              "script.yaml", "conversation.md",
                                              "signoffs.json")} <= names
    assert pj.to_tell_sender() == [job]
