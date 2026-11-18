"""The forced briefing pipeline (jarvis/briefing_prep.py): typed questions
answered by the author's engine and recorded, the three editors held to
their rules, the four judges, findings back to the same author session,
and the refusal after three rounds. The engine is a stand-in (`ASK`)."""
import asyncio
import json
import zipfile

import pytest

from jarvis import briefing_prep as bp, records
from jarvis.decision_package import Refused
from tests.author_fixtures import passing_judges, write_prep


def replies(*texts):
    """A stand-in engine giving `texts` in turn, then the last one again."""
    given = list(texts)
    prompts = []

    async def ask(prompt, choice, folder, timeout=0):
        prompts.append(prompt)
        return given.pop(0) if len(given) > 1 else given[0]
    ask.prompts = prompts
    return ask


def said(answer, why="because"):
    return "Here is my answer.\n" + json.dumps({"answer": answer, "why": why})


CONTEXT = {"prep": "## Does Tim care\nYes.", "package": "author-session-1"}


# -- decide ---------------------------------------------------------------------------------

def test_decide_asks_the_engine_a_few_times_and_gives_the_share_that_agreed(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "ASK", replies(said("yes"), said("no", "slide 1 asks nothing"),
                                           said("yes")))
    decided = bp.decide("decision_ready", "## Slide 1", dict(CONTEXT, folder=tmp_path))
    assert (decided["answer"], round(decided["probability"], 2)) == ("yes", 0.67)
    assert bp.ASK.prompts[0].count("## Slide 1") == 1 and "Does Tim care" in bp.ASK.prompts[0]
    assert len(bp.ASK.prompts) == bp.ASKS


def test_every_call_is_recorded_so_his_verdict_can_be_joined_to_it(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "ASK", replies(said("lacks", "never says which test")))
    decided = bp.decide("background", "the script", dict(CONTEXT, folder=tmp_path, round=2))
    rec = records.get(f"{bp.RECORDS}/{decided['record']}.decision")
    assert rec is not None
    data = rec.data
    assert (data["question"], data["kind"], data["answer"], data["probability"]) == \
        ("background", bp.CHOICE, "lacks", 1.0)
    assert data["package"] == "author-session-1", "joined to his verdict by its author session"
    assert data["input_sha256"] == bp.input_digest("the script", CONTEXT)
    assert data["round"] == 2 and isinstance(data["at"], float)
    assert [a["why"] for a in data["answers"]] == ["never says which test"] * bp.ASKS


def test_an_unrecorded_call_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "ASK", replies(said("yes")))

    def down(*a, **kw):
        raise OSError("engine down")
    monkeypatch.setattr(records, "write", down)
    with pytest.raises(Refused, match="could not be recorded: engine down"):
        bp.decide("cares", "s", dict(CONTEXT, folder=tmp_path))


def test_a_0_to_10_question_answers_the_median_and_agreement_within_one(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "ASK", replies(said(9), said("8"), said(3)))
    decided = bp.decide("order", "s", dict(CONTEXT, folder=tmp_path))
    assert (decided["answer"], round(decided["probability"], 2)) == (8, 0.67)
    assert bp.passed("order", decided)


def test_unreadable_answers_are_left_out_and_none_at_all_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "ASK", replies("no JSON here", said("maybe"), said("no")))
    decided = bp.decide("cares", "s", dict(CONTEXT, folder=tmp_path))
    assert (decided["answer"], decided["probability"]) == ("no", 1.0)
    monkeypatch.setattr(bp, "ASK", replies("nothing"))
    with pytest.raises(Refused, match="No ask of the cares question"):
        bp.decide("cares", "s", dict(CONTEXT, folder=tmp_path))


def test_a_tie_goes_to_the_failing_answer():
    question = bp.QUESTIONS["background"]
    assert bp.agreement(question, ["right", "repeats"]) == ("repeats", 0.5)
    assert not bp.passed("background", {"answer": "right", "probability": 0.5}), \
        "a judge passes only on what most asks agreed"


def test_an_unknown_question_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        bp.decide("is-it-good", "s", dict(CONTEXT, folder=tmp_path))


# -- editors --------------------------------------------------------------------------------

EDITOR = {e.id: e for e in bp.EDITORS}
SCRIPT = ("Slide 1. Approve PR 171? It is important to note this is your decision. "
          "It fixes the failed test. The fix is a fairly small and quite simple change.")


@pytest.mark.parametrize("editor, edit, refused", [
    ("cut", {"before": "It is important to note this is your decision. ", "after": ""}, None),
    ("cut", {"before": "It fixes the failed test.", "after": "It fixes the test."}, None),
    ("cut", {"before": "It fixes the failed test.", "after": "It fixes the stale test."},
     "may only delete"),
    ("tighten", {"before": "a fairly small and quite simple change", "after": "a small change"},
     None),
    ("tighten", {"before": "a fairly small and quite simple change",
                 "after": "a fairly small and quite simple, one-line change"}, "only shorten"),
    ("specifics", {"before": "the failed test", "after": "test_stalled_turn_retry",
                   "source": "prep Q2"}, None),
    ("specifics", {"before": "the failed test", "after": "the test (UNANSWERED: which)"}, None),
    ("specifics", {"before": "the failed test", "after": "test_stalled_turn_retry"},
     "names the prep's source"),
    ("cut", {"before": "nowhere in the script", "after": ""}, "not in the script"),
    ("tighten", {"before": "It", "after": "I"}, "more than once"),
])
def test_each_editor_is_held_to_its_rule(editor, edit, refused):
    script, edited = bp.apply_edits(EDITOR[editor], SCRIPT, {"edits": [edit]})
    if refused is None:
        assert edited.applied and not edited.refused
        assert script == SCRIPT.replace(edit["before"], edit["after"], 1)
    else:
        assert script == SCRIPT and not edited.applied
        assert refused in edited.refused[0][1]


def test_edits_are_logged_before_and_after(tmp_path):
    _, edited = bp.apply_edits(EDITOR["specifics"], SCRIPT, {
        "edits": [{"before": "the failed test", "after": "test_x", "why": "names it",
                   "source": "prep Q2"},
                  {"before": "absent", "after": "x", "source": "s"}],
        "to_prep": [{"claim": "a fairly small", "why": "no figure"}]})
    log = tmp_path / bp.EDITS
    bp.log_edits(log, 1, EDITOR["specifics"], edited)
    bp.log_edits(log, 1, EDITOR["cut"], bp.Edited())
    text = log.read_text()
    assert '## round 1, specifics\n\n1. "the failed test" -> "test_x" (names it; source: prep Q2)' \
        in text
    assert '- Not applied, its before is not in the script: "absent" -> "x"' in text
    assert '- Back to the prep: "a fairly small" (no figure)' in text
    assert "## round 1, cut\n\nNo changes." in text


# -- the rounds -----------------------------------------------------------------------------

class Author:
    """The same author session: each turn's message kept, and `revise`
    run on its folder."""

    def __init__(self, folder, revise=None):
        self.folder, self.revise, self.turns = folder, revise, []

    async def __call__(self, message):
        self.turns.append(message)
        if self.revise:
            self.revise(self.folder)


def engine(fail_rounds: dict, edits: dict | None = None):
    """Judges that fail (by judge, the rounds they fail in), editors that
    propose `edits` by editor id in round 1 only."""
    passing = passing_judges()
    state = {"round": 0, "prompts": []}

    async def ask(prompt, choice, folder, timeout=0):
        state["prompts"].append(prompt)
        if prompt.startswith("You are the cut editor"):
            state["round"] += 1
        for editor in bp.EDITORS:
            if prompt.startswith(f"You are the {editor.id} editor"):
                return json.dumps((edits or {}).get(editor.id) if state["round"] == 1
                                  and edits and editor.id in edits else {"edits": []})
        for judge, rounds in fail_rounds.items():
            question = bp.QUESTIONS[judge]
            if question.asks in prompt and state["round"] in rounds:
                answer = {bp.YES_NO: "no", bp.CHOICE: "repeats", bp.SCALE: 2}[question.kind]
                return said(answer, f"{judge} fails in round {state['round']}")
        return await passing(prompt, choice, folder)
    ask.state = state
    return ask


def run(folder, author, context=None):
    return asyncio.run(bp.run(folder, author, context or {"package": "author-1"}))


@pytest.mark.parametrize("judge", bp.JUDGES)
def test_a_judge_s_findings_go_back_to_the_same_session_and_the_next_round_passes(
        judge, tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder)
    monkeypatch.setattr(bp, "ASK", engine({judge: {1}}))
    author = Author(folder)
    result = run(folder, author)
    assert result["status"] == "passed" and len(result["rounds"]) == 2
    [message] = author.turns
    assert f"{judge}: answered" in message and f"{judge} fails in round 1" in message
    assert "write nothing else yet: no deck" in message
    assert result["rounds"][0]["judges"][judge]["probability"] == 1.0


def test_a_script_that_passes_at_once_takes_one_round_and_no_turn(tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder)
    monkeypatch.setattr(bp, "ASK", engine({}))
    author = Author(folder)
    assert run(folder, author)["rounds"][0]["round"] == 1
    assert author.turns == []
    assert (folder / bp.EDITS).read_text().count("No changes.") == 3


def test_vague_claims_go_back_to_the_prep_before_it_passes(tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder, "## Slide 1. Approve it?\nThe failed test is fixed.\n")
    monkeypatch.setattr(bp, "ASK", engine({}, edits={
        "cut": {"edits": [{"before": "Approve it?\n", "after": "Approve?\n"}]},
        "specifics": {"edits": [], "to_prep": [{"claim": "The failed test",
                                                "why": "which test"}]}}))
    author = Author(folder)
    result = run(folder, author)
    assert len(result["rounds"]) == 2 and result["rounds"][0]["to_prep"] == 1
    assert '"The failed test": which test' in author.turns[0]
    assert "## Slide 1. Approve?" in (folder / bp.SCRIPT).read_text(), "the editor's change applied"
    assert '1. "Approve it?" -> "Approve?"' in (folder / bp.EDITS).read_text()


def test_three_failed_rounds_refuse_with_the_judges_findings(tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder)
    monkeypatch.setattr(bp, "ASK", engine({"cares": {1, 2, 3}, "order": {3}}))
    author = Author(folder)
    with pytest.raises(Refused) as refused:
        run(folder, author)
    problems = refused.value.problems
    assert "did not pass its judges in 3 rounds" in problems[0]
    assert any("cares: answered no" in p and "round 3" in p for p in problems)
    assert any("order: answered 2" in p for p in problems)
    assert len(author.turns) == 2, "two revisions in the same session, then the refusal"


def test_a_prep_without_its_sections_or_a_script_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "ASK", engine({}))
    folder = tmp_path / "author"
    folder.mkdir()
    (folder / bp.PREP).write_text("## Does Tim care\nYes.\n")
    (folder / bp.SCRIPT).write_text("## Slide 1\n")
    with pytest.raises(Refused) as refused:
        run(folder, Author(folder))
    assert "prep.md has no `## What he already knows` section in its place." in \
        refused.value.problems
    (folder / bp.SCRIPT).unlink()
    with pytest.raises(Refused, match="wrote no script.md"):
        run(folder, Author(folder))


def test_not_for_tim_is_not_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "ASK", engine({}))
    folder = tmp_path / "author"
    folder.mkdir()
    (folder / bp.NOT_FOR_TIM).write_text("He settled this on Tuesday.")
    with pytest.raises(Refused, match="Not sent: .*He settled this on Tuesday."):
        run(folder, Author(folder))


def test_a_revision_that_drops_the_script_is_refused(tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder)
    monkeypatch.setattr(bp, "ASK", engine({"cares": {1}}))
    with pytest.raises(Refused, match="wrote no script.md"):
        run(folder, Author(folder, revise=lambda f: (f / bp.SCRIPT).unlink()))


# -- carried in the package -------------------------------------------------------------

def test_carry_puts_the_three_files_under_jarvis_briefing(tmp_path):
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("pkg/package.json", "{}")
    folder = tmp_path / "author"
    write_prep(folder)
    with pytest.raises(Refused, match="edits.md is missing"):
        bp.carry(archive, folder)
    (folder / bp.EDITS).write_text("## round 1, cut\n\nNo changes.\n")
    bp.carry(archive, folder)
    with zipfile.ZipFile(archive) as z:
        assert {n for n in z.namelist() if ".jarvis/briefing/" in n} == {
            f"pkg/.jarvis/briefing/{name}" for name in bp.FILES}
        assert z.read("pkg/.jarvis/briefing/script.md") == (folder / bp.SCRIPT).read_bytes()


def test_a_briefing_changed_after_it_passed_is_not_carried(tmp_path, monkeypatch):
    folder = tmp_path / "author"
    write_prep(folder)
    monkeypatch.setattr(bp, "ASK", engine({}))
    result = run(folder, Author(folder))
    (folder / "author-receipt.json").write_text(json.dumps({"briefing": result}))
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("pkg/package.json", "{}")
    bp.carry(archive, folder)
    (folder / bp.SCRIPT).write_text("## Slide 1. Something else\n")
    with pytest.raises(Refused, match="script.md changed after its script passed"):
        bp.carry(archive, folder)


def test_the_reply_s_last_whole_object_is_read_not_one_inside_it():
    reply = 'Echo: {"x": 1} then {"edits": [{"before": "a", "after": ""}]}'
    assert bp._last_json(reply) == {"edits": [{"before": "a", "after": ""}]}
    assert bp._last_json("no object {here") is None
