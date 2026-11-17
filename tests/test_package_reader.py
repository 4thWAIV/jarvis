"""The independent reader check (jarvis/package_reader.py): a second fresh
session, given only the slides before the first decision, must answer the
six cold-read questions; a miss goes back to the author once, a second
refuses the package. The reader's session is faked here as the author's is
in tests/test_package_author.py."""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis import decision_package as dp, make_package as mp, package_author, package_reader
from tests.author_fixtures import cold_reader
from tests.deck_fixtures import write_yaml
from tests.test_decision_package import _Delivery, small_deck


@pytest.fixture
def session_key():
    from jarvis import agent_keys
    return agent_keys.make_pair(agent_keys.key_folder("sess-1"), "jarvis-agent sess-1")


def built(tmp_path) -> dict:
    return mp.make_presentation(write_yaml(tmp_path / "in", small_deck()), tmp_path / "out").deck


def test_the_six_questions_are_the_skill_s_own():
    skill = package_author.SKILL.read_text(encoding="utf-8")
    for question in package_reader.QUESTIONS:
        assert question in skill, question


def test_the_reader_sees_only_the_slides_before_the_first_decision(tmp_path):
    deck = built(tmp_path)
    slides = package_reader.before_the_ask(deck)
    assert slides.startswith("Slide 1: Team lunch\n"), "JARVIS's opening first"
    assert "What happened: The restaurant now needs a booking" in slides
    assert "Bottom line: Eat in the office at noon" in slides
    assert "Slide 2: Two decisions on the team lunch" in slides
    assert "JARVIS says: Two decisions on the team lunch." in slides
    assert "[Picture: A picture]" in slides
    assert "Before you decide" not in slides and "Where do we eat?" not in slides, \
        "no lead-in and no question"
    assert "Slide 3" not in slides


def run(coro):
    return asyncio.run(coro)


def test_every_question_answered_is_read_once(tmp_path):
    asked = []

    async def revise(message):
        asked.append(message)

    async def rebuild():
        pytest.fail("built again")
    reader = cold_reader()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(package_reader, "read", reader)
        assert run(package_reader.check({"slides": []}, "claude", tmp_path, revise, rebuild)) == \
            {"status": "read", "attempts": 1}
    assert asked == [] and len(reader.decks) == 1


def test_a_miss_goes_back_to_the_author_once_and_the_deck_is_read_again(tmp_path):
    asked, rebuilt = [], {"slides": [{"title": "Rewritten"}]}

    async def revise(message):
        asked.append(message)

    async def rebuild():
        return rebuilt
    reader = cold_reader([package_reader.QUESTIONS[1]])
    with pytest.MonkeyPatch.context() as m:
        m.setattr(package_reader, "read", reader)
        assert run(package_reader.check({"slides": []}, "claude", tmp_path, revise, rebuild)) == \
            {"status": "read", "attempts": 2}
    [message] = asked
    assert "What did I already approve that it touches? Missing: it never says" in message
    assert "check-only" in message, "the author checks its source again, last"
    assert reader.decks[1] is rebuilt, "the second reader reads the deck built again"


def test_a_second_miss_refuses_the_package_with_the_reader_s_answers(tmp_path):
    async def revise(message):
        pass

    async def rebuild():
        return {"slides": []}
    first = package_reader.QUESTIONS[0]
    with pytest.MonkeyPatch.context() as m:
        m.setattr(package_reader, "read", cold_reader([first], [first]))
        with pytest.raises(dp.Refused) as refused:
            run(package_reader.check({"slides": []}, "claude", tmp_path, revise, rebuild))
    said = "\n".join(refused.value.problems)
    assert "still could not follow" in said and first in said and '"answered": false' in said


def test_the_factory_asks_its_author_again_and_delivers_only_what_a_reader_follows(
        tmp_path, monkeypatch, authored_package_source, session_key, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    monkeypatch.setattr(package_reader, "read", cold_reader([package_reader.QUESTIONS[2]]))
    source = write_yaml(tmp_path / "in", small_deck())
    assert mp.main([str(source), "--out", str(tmp_path / "out"), "--session", "sess-1",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [again] = authored_package_source
    assert "What happened, and why?" in again
    assert "A cold reader followed it (2 read(s))." in capsys.readouterr().out
    assert len(delivery.calls) == 1

    delivery.calls.clear()
    authored_package_source.clear()
    second = package_reader.QUESTIONS[5]
    monkeypatch.setattr(package_reader, "read", cold_reader([second], [second]))
    assert mp.main([str(source), "--out", str(tmp_path / "out2"), "--session", "sess-1",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 1
    said = capsys.readouterr().out
    assert "Not delivered" in said and "still could not follow" in said and second in said
    assert delivery.calls == [] and len(authored_package_source) == 1


class _Reader:
    """A provider session that answers as a reader would: it writes the
    answers file the prompt names, from `rows`."""

    def __init__(self, rows, identity="reader-session"):
        self.rows, self.identity, self.prompts, self.closed = rows, identity, [], False

    def provider(self, *_):
        reader = self

        class Provider:
            name = "openai"

            def session(self, config, event, exited):
                reader.config, reader.event = config, event
                return reader
        return Provider()

    async def open(self, instructions):
        pass

    async def send(self, text):
        from jarvis.brain_provider import SessionInfo, TurnEnded
        self.prompts.append(text)
        path = text.split("Write JSON to ", 1)[1].split(" and nothing else", 1)[0]
        if self.rows is not None:
            (self.config.home / path.rsplit("/", 1)[-1]).write_text(json.dumps({"answers": self.rows}))
        self.event(self, SessionInfo(self.identity, None))
        self.event(self, TurnEnded(0, 0))

    async def close(self, timeout):
        self.closed = True


def answered(miss: str = "") -> list[dict]:
    return [{"question": q, "answered": q != miss, "answer": "From the slides.",
             "missing": "x" if q == miss else ""} for q in package_reader.QUESTIONS]


def test_a_fresh_reader_session_is_given_the_slides_alone_and_its_answers_read(
        tmp_path, monkeypatch):
    session = _Reader(answered(package_reader.QUESTIONS[3]))
    monkeypatch.setattr(package_author.brain_provider, "provider", session.provider)
    deck = built(tmp_path)
    rows = run(package_reader.read(deck, "codex", tmp_path / "reader", 1))
    assert [r["question"] for r in package_reader.missed(rows)] == [package_reader.QUESTIONS[3]]
    [prompt] = session.prompts
    assert prompt.endswith(package_reader.before_the_ask(deck)), "the slides, and nothing else"
    assert "handoff" not in prompt.lower() and "Do not open, search or read any file" in prompt
    assert session.config.authoring and session.closed
    [receipt] = (tmp_path / "reader").glob("reader-1-*/reader-receipt.json")
    receipt = json.loads(receipt.read_text())
    assert receipt["status"] == "read" and receipt["reader_session"] == "reader-session"
    assert receipt["missed"] == [package_reader.QUESTIONS[3]]


@pytest.mark.parametrize("rows", [None, answered()[:5], [{"question": "Other?", "answered": True}] * 6])
def test_a_reader_that_does_not_answer_the_six_questions_refuses(tmp_path, monkeypatch, rows):
    session = _Reader(rows)
    monkeypatch.setattr(package_author.brain_provider, "provider", session.provider)
    with pytest.raises(dp.Refused, match="cold reader"):
        run(package_reader.read({"slides": []}, "codex", tmp_path, 1))
    assert session.closed
