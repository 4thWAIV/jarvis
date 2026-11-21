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
from tests.test_agent_keys import keygen  # noqa: F401
from tests.test_package_gather import checkout, gh, indexing, transcript  # noqa: F401
from tests.test_package_swarm import Marking, Plan, _chat_factory, marked  # noqa: F401


def deck_with_his_words() -> dict:
    """The example deck, its opening quoting Tim in his style."""
    deck = copy.deepcopy(make_package.EXAMPLE)
    deck["slides"][0]["objects"][0]["paragraphs"].append(
        {"id": "opening-words-his", "role": "paragraph",
         "runs": [{"id": "opening-words-his-1", "text": "Keep it in the office.",
                   "emphasis": "his-words"}]})
    return deck


class DeckPlan(Plan):
    """The swarm's participants, the author writing the deck when asked."""

    def __init__(self, run_folder: Path):
        super().__init__(run_folder)
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
    assert "emphasis his-words" in told and "very bright, vibrant way" in told
    assert pd.deck_format() in told and "## A deck: one presentation.yaml" in told
    assert "<example file=\"presentation.yaml\">" in told
    # JARVIS's own turn: the layout data beside the passed script, and the
    # questions; no browser.
    review = json.loads((Path(doc["folder"]) / pd.REVIEW).read_text())
    [turn] = [m for m in review["conversation"] if m["from"] == pd.PROGRAM]
    assert "<script file=\"script.md\">Slide 1. Pull request 171" in turn["text"]
    assert all(f'<question id="{q.id}">' in turn["text"] for q in pd.DECK_QUESTIONS)
    layout = json.loads(turn["text"].split('<layout file=".jarvis/layout-proof.json">')[1]
                        .split("</layout>")[0])
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
    assert check["package"]["message"] == (
        "Friday's lunch has moved\n\nFriday's lunch is now in the office, at noon.\nThis tells "
        "the team where Friday's lunch is now: nobody has to book a table.\nKeep it in the "
        "office.\n\nMerge PR 171?")
    assert "Where do we eat on Friday?" not in check["package"]["message"]
    archive = Path(check["package"]["archive"])
    signed = package_signature.read(archive)
    assert signed.by_jarvis() and signed.requested_by == f"agent:{job['session']}"
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
    assert {f".jarvis/briefing/{n}" for n in ("research.md", "prep.md", "script.md",
                                              "conversation.md", "votes.json")} <= names
    assert pj.to_tell_sender() == [job]
