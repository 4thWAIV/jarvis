"""The deck and deliver steps (package_deck.py, package_deliver.py; workflow.yaml
deck and deliver): the author writes the deck from the passed script in the
same swarm, JARVIS builds and proves it, the editor and the judges compare
each slide with the script from the layout data alone and vote, the
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

    def answer(self, name, text, form):
        if name == "author" and form is sessions.TURN_FORM and (
                "Now write the deck" in text or "Revise the deck" in text):
            self.sent.setdefault(name, []).append(text)
            deck = self.decks.pop(0) if len(self.decks) > 1 else self.decks[0]
            source = self.folder / pd.FOLDER / pd.SOURCE
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(yaml.safe_dump(deck, sort_keys=False))
            return {"message": "author writes the deck", "call_vote": []}
        return super().answer(name, text, form)


@pytest.fixture
def deck_plan(marked, monkeypatch):  # noqa: F811
    plan = DeckPlan(Path(marked["folder"]))
    monkeypatch.setattr(ps, "CHAT", _chat_factory(plan))
    monkeypatch.setattr(ps, "PICK", lambda kind, tier, **kw: model_tiers.Pick(
        "claude", {1: "fable", 2: "opus"}.get(tier, "sonnet"), tier, "test", ["x"]))
    monkeypatch.setattr(package_mark, "MARKING", lambda doc: Marking())
    embedder = FakeEmbedder(64)
    monkeypatch.setattr(ps, "EMBED", lambda texts: [list(v) for v in
                                                    embedder.embed(texts, "query")])
    Marking.calls = []
    return plan


def _passed(doc):
    ps.research(pj.get(doc["id"]))
    assert ps.swarm(pj.get(doc["id"])) is None
    return pj.get(doc["id"])


# ── the deck ──────────────────────────────────────────────────────────────

def test_the_deck_is_written_built_proven_and_compared_with_the_script(marked, deck_plan):
    doc = _passed(marked)
    assert pd.deck(doc) is None
    job = pj.get(doc["id"])
    assert job["deck"]["result"] == "passed" and job["deck"]["rounds"] == 1
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
    # JARVIS's own turn: slide by slide, each script slide beside its deck
    # slide's layout data, and the questions; no browser. His words are the
    # program's check: no vote is asked of them.
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    [turn] = [m for m in review["conversation"] if m["from"] == pd.PROGRAM]
    assert all(f'<question id="{q.id}">' in turn["text"] for q in pd.DECK_QUESTIONS)
    assert [q.id for q in pd.DECK_QUESTIONS] == ["deck-says", "deck-visual", "deck-narration",
                                                 "deck-readable"]
    slides = turn["text"].split('<slide id="')[1:]
    assert [s.split('"')[0] for s in slides] == ["opening", "what-changed", "where"]
    assert '<script file="script.yaml">id: opening' in slides[0]
    layout = [json.loads(s.split('<layout file=".jarvis/layout-proof.json">')[1]
                         .split("</layout>")[0]) for s in slides]
    opening = layout[0]
    assert opening["layout"] == "opening" and opening["title_px"]["smallest"] >= 13
    words = opening["objects"][0]
    assert words["size_px"]["smallest"][0] > 0 and words["size_px"]["largest"][0] > \
        words["size_px"]["smallest"][0]
    runs = [r for p in words["paragraphs"] for r in p["runs"]]
    assert {"id": "opening-words-his-1", "text": "Keep it in the office.",
            "emphasis": "his-words"} in runs
    assert opening["narration"][0]["say"].startswith("This tells you")
    assert layout[-1]["type"] == "decision" and layout[-1]["question"] == \
        "Where do we eat on Friday?"
    # A decision slide's words are sized as the shared deck draws them, and
    # what JARVIS says on it is there too (deck_review needs both).
    assert layout[-1]["text_px"]["question"] == {"smallest": 19.6, "largest": 25.0}
    assert layout[-1]["text_px"]["says"]["smallest"] >= 13
    assert layout[-1]["narration"][0]["say"] == "Decision 1 of 1: Where do we eat on Friday?"
    # The editor and the four judges answered it, then voted; the author
    # never on its own deck, the reader never.
    after = [m["from"] for m in review["conversation"][review["conversation"].index(turn) + 1:]]
    assert after[:5] == ["editor", "cares_judge", "decision_ready_judge", "background_judge",
                         "order_judge"]
    voters = {v["participant"] for v in review["votes"] if v["question"] == "deck-says"}
    assert voters == {"editor", "cares_judge", "decision_ready_judge", "background_judge",
                      "order_judge"}
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
    said = [m["text"] for m in review["conversation"] if m["from"] == pd.PROGRAM]
    assert said[0].startswith("The deck did not build. Revise it:") and "no-such-layout" in \
        said[0]
    assert "Revise the deck in" in deck_plan.sent["author"][-1]
    assert pj.get(doc["id"])["deck"]["rounds"] == 2


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
    said = [m["text"] for m in review["conversation"] if m["from"] == pd.PROGRAM]
    assert "does not draw Tim's words 'Keep it in the office.' in the his-words style" in said[0]
    assert "narration does not say Tim's words 'Keep it in the office.'" in said[0]
    assert "the script's are ['opening', 'what-changed', 'where']" in said[1]
    assert pj.get(doc["id"])["deck"]["rounds"] == 3
    assert not [v for v in review["votes"] if "his" in v["question"]]


def test_a_script_slide_that_asks_a_decision_is_the_deck_s_decision_slide():
    # presentation/1 asks a decision on a slide of its own, so the one deck
    # slide of a script slide that asks one is that decision slide.
    script, deck = deck_script(), deck_with_his_words()
    assert pd.script_problems(script, deck) == []
    content = copy.deepcopy(deck)
    content["slides"][2] = {**deck["slides"][1], "id": "where"}
    assert pd.script_problems(script, content) == [
        "Script slide where asks the decision where-to-eat: its deck slide is a decision slide "
        "(type decision, id where) asking where-to-eat, its words in the decision's context and "
        "what JARVIS says over it in the decision's say."]
    script["slides"][2]["decision"] = None
    assert pd.script_problems(script, deck) == [
        "Script slide where asks no decision: its deck slide is a content slide."]


def test_a_deck_not_proven_in_three_rounds_sends_the_sender_the_findings(marked, deck_plan):
    deck_plan.votes = {"deck-says": ["no"] * 30}
    doc = _passed(marked)
    assert pd.deck(doc) == pj.WAITING
    job = pj.get(doc["id"])
    assert job["deck"]["result"] == "not passed" and job["deck"]["rounds"] == 3
    last = job["rounds"][-1]
    assert pj.kind_of(last) == pj.FINDINGS
    assert [q["id"] for q in last["questions"]] == ["deck-says"]


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
    assert {f".jarvis/briefing/{n}" for n in ("research.md", "prep.md", "script.yaml",
                                              "conversation.md", "votes.json")} <= names
    assert pj.to_tell_sender() == [job]
