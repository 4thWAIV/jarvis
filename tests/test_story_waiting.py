"""A story that asks questions is told one question at a time
(story_teller.py, package_story.py's `answers`, `waits` and `asks_when`):
it waits, as running state, after each question until the answers file has
an answer; Stop ends it whether it is telling, paused or waiting, and a
second Stop says it is already stopped; resume picks up at the first
question with no answer.

Speech is the real SpeechScheduler over test_speech's fake synthesizer and
recording transport; answers are written to the package's results as the
page's save and scripts/decide.py write them."""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis import package_story, spoken_words, story_teller, work_packages
from jarvis.package_story import StoryError
from tests.test_package_modes import PAGE, unpacked
from tests.test_package_story import Teller
from tests.test_speech import Harness, h  # noqa: F401  (the fixture)
from tests.test_work_packages import marks  # noqa: F401  (the fixture)

STORY = {"title": "Choices", "answers": "results/decisions.json", "slides": [
    {"title": "Intro", "beats": [{"say": "Two choices."}]},
    {"title": "Tea", "waits": "tea", "beats": [{"say": "Which tea?"}]},
    {"title": "Milk", "waits": "milk", "asks_when": {"question": "tea", "answers": ["black"]},
     "beats": [{"say": "Milk with it?"}]},
    {"title": "Cups", "waits": "cups", "beats": [{"say": "How many cups?"}]},
    {"title": "Answers", "beats": [{"say": "That is all."}]}]}


@pytest.fixture
def deck(tmp_path, marks):
    return unpacked(tmp_path, {**PAGE, "story": "story.json"},
                    {"web/index.html": "x", "story.json": json.dumps(STORY)})


def answer(package, question, choice, at="1"):
    path = package.folder / "results" / "decisions.json"
    record = json.loads(path.read_text()) if path.exists() else {}
    record[question] = {"answer": choice, "at": at}
    work_packages.save_result(package, "decisions.json", json.dumps(record).encode())


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(story_teller, "ANSWER_POLL_SEC", 0.02)
    monkeypatch.setattr(story_teller, "FLOOR_SETTLE_SEC", 0.02)


async def until(t, check, rounds=150):
    for _ in range(rounds):
        await t.h.ack_all(rounds=1)
        if check():
            return
    raise AssertionError(f"never happened; frames: {t.frames}")


def state(t):
    f = t.frames[-1] if t.frames else {}
    return f.get("state"), f.get("slide"), f.get("question")


def said(t):
    return [text for _, _, text in t.h.audio()]


# -- the format ------------------------------------------------------------------------

def test_the_questions_are_read_and_checked():
    story = package_story.parse(json.dumps(STORY).encode())
    assert story.answers == "results/decisions.json"
    assert [s.waits for s in story.slides] == [None, "tea", "milk", "cups", None]
    assert story.slides[2].asks_when == ("tea", ("black",))
    assert story.outline()["asks"] == [2, 3, 4]
    assert story.open_questions({}) == [2, 4]
    assert story.open_questions({"tea": {"answer": "black"}}) == [3, 4]


@pytest.mark.parametrize("change,problem", [
    ({"answers": None}, "names no answers file"),
    ({"answers": "../x.json"}, "is not results/<name>.json"),
    ({"slides": [{"title": "a", "waits": "x", "beats": [{"say": "a"}]},
                 {"title": "b", "waits": "x", "beats": [{"say": "b"}]}]}, "same question"),
    ({"slides": [{"title": "a", "waits": "Bad Id", "beats": [{"say": "a"}]}]}, "question id"),
    ({"slides": [{"title": "a", "waits": "x", "asks_when": {"question": "y", "answers": ["z"]},
                  "beats": [{"say": "a"}]}]}, "which no slide asks"),
])
def test_a_story_s_questions_that_cannot_be_asked_are_refused(change, problem):
    body = {**STORY, **change}
    if body["answers"] is None:
        del body["answers"]
    with pytest.raises(StoryError, match=problem):
        package_story.parse(json.dumps(body).encode())


# -- one question at a time -------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_waits_at_each_question_and_goes_on_only_once_answered(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id)
    await until(t, lambda: state(t) == ("waiting", 2, "tea"))
    assert said(t) == ["Two choices.", "Which tea?"]
    for _ in range(20):                  # far longer than a beat: still waiting
        await h.ack_all(rounds=1)
    assert state(t) == ("waiting", 2, "tea") and said(t) == ["Two choices.", "Which tea?"]
    assert t.teller.status()["question"] == "tea"
    answer(deck, "tea", "green")         # milk applies only to black tea: passed over
    await until(t, lambda: state(t) == ("waiting", 4, "cups"))
    assert said(t)[-1] == "How many cups?" and "Milk with it?" not in said(t)
    answer(deck, "cups", "later")        # Decide later is an answer too
    await until(t, lambda: state(t)[0] == "finished")
    assert said(t)[-1] == "That is all." and t.teller.telling is None


@pytest.mark.asyncio
async def test_it_goes_on_only_once_the_floor_is_free(h, deck):
    t = Teller(h)
    free = {"now": False}
    t.teller._floor_free = lambda: free["now"]
    await t.teller.tell(deck.id, 2)
    await until(t, lambda: state(t)[0] == "waiting")
    answer(deck, "tea", "green")
    for _ in range(15):
        await h.ack_all(rounds=1)
    assert "How many cups?" not in said(t), "his turn and the reply come first"
    free["now"] = True
    await until(t, lambda: state(t) == ("waiting", 4, "cups"))


@pytest.mark.asyncio
async def test_going_back_asks_that_question_again_and_passes_over_the_answered(h, deck):
    answer(deck, "tea", "green")
    answer(deck, "cups", "two")
    t = Teller(h)
    await t.teller.tell(deck.id, 2)
    await until(t, lambda: state(t) == ("waiting", 2, "tea"))
    for _ in range(10):
        await h.ack_all(rounds=1)
    assert state(t) == ("waiting", 2, "tea"), "an answer already there is not a new one"
    answer(deck, "tea", "black", at="2")
    await until(t, lambda: state(t) == ("waiting", 3, "milk"))
    answer(deck, "milk", "yes")
    await until(t, lambda: state(t)[0] == "finished")
    assert "How many cups?" not in said(t), "cups was already answered"


@pytest.mark.asyncio
async def test_resume_picks_up_at_the_first_question_with_no_answer(h, deck):
    t = Teller(h)
    told = await t.teller.tell(deck.id, None)
    assert told.slide == 1, "nothing answered yet: from the start"
    await until(t, lambda: state(t)[0] == "waiting")
    answer(deck, "tea", "green")
    await until(t, lambda: state(t) == ("waiting", 4, "cups"))
    await t.teller.stop()
    told = await t.teller.act("resume", deck.id)
    assert told.slide == 4
    await until(t, lambda: state(t) == ("waiting", 4, "cups"))


# -- stop -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_while_waiting_ends_it_and_says_so_and_again_says_already(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id, 2)
    await until(t, lambda: state(t)[0] == "waiting")
    stopped = await t.teller.stop("by voice")
    assert stopped.package.id == deck.id and stopped.waiting == "tea"
    assert state(t) == ("stopped", 2, "tea") and t.teller.telling is None
    answer(deck, "tea", "green")
    for _ in range(10):
        await h.ack_all(rounds=1)
    assert said(t) == ["Which tea?"], "nothing more once stopped, answered or not"
    again = await t.teller.stop()
    assert again.already and again.ended.slide == 2 and again.ended.by == "by voice"


@pytest.mark.asyncio
async def test_stop_while_telling_withdraws_the_words_at_once(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id)
    await asyncio.sleep(0.05)
    await t.teller.stop()
    assert "stop" in h.types() and state(t)[0] == "stopped"
    heard = len(h.audio())
    await h.ack_all(rounds=3)
    assert len(h.audio()) == heard


@pytest.mark.asyncio
async def test_his_speaking_pauses_a_slide_and_on_a_question_waits_for_his_answer(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id)
    await asyncio.sleep(0.05)
    await h.sched.barge_in(reason="he spoke")
    await until(t, lambda: state(t)[0] == "paused")
    assert t.teller.telling is not None and t.teller.status()["state"] == "paused", \
        "paused is still running: Stop must find it"
    stopped = await t.teller.stop()
    assert stopped.package.id == deck.id and state(t)[0] == "stopped"

    await t.teller.tell(deck.id, 2)
    await asyncio.sleep(0.05)
    await h.sched.barge_in(reason="he answers over it")
    await until(t, lambda: state(t) == ("waiting", 2, "tea"))
    answer(deck, "tea", "green")
    await until(t, lambda: state(t) == ("waiting", 4, "cups"))


@pytest.mark.asyncio
async def test_closing_its_page_stops_only_its_own_story(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id, 2)
    await until(t, lambda: state(t)[0] == "waiting")
    assert not await t.teller.stop_for("another-package", "by closing its page")
    assert await t.teller.stop_for(deck.id, "by closing its page")
    assert t.teller.telling is None


@pytest.mark.parametrize("text,language,stops", [
    ("stop", "en", True), ("Stop, stop.", "en", True), ("jarvis stop", "en", True),
    ("stop the story and go back", "en", False), ("no", "en", False),
    ("don't stop", "en", False), ("alto", "es", True), ("para", "es", False),
])
def test_stop_words_are_a_whole_utterance(monkeypatch, text, language, stops):
    monkeypatch.setattr(spoken_words, "_active", language)
    assert story_teller.stop_words(text) is stops


# -- the server: what the brain is told, and every Stop ---------------------------------

from tests.test_brain_page_parity import _call, wired  # noqa: E402,F401  (the fixture)


def test_the_brain_hears_waiting_already_stopped_and_never_no_story(wired, deck, monkeypatch):
    server = wired.server
    teller = server.story_teller
    story = deck.story()
    telling = story_teller.Telling(deck, story, 2, 1, False, state="waiting", question="tea")
    monkeypatch.setattr(teller, "telling", telling)
    got = _call(wired, "tell_package_story", package=deck.id, action="status")
    assert f"The story of {deck.id} is waiting for his answer to question tea on slide 2 of 5" \
        in got["text"]
    monkeypatch.setattr(teller, "_speech", lambda: None)
    got = _call(wired, "tell_package_story", package=deck.id, action="stop")
    assert got["text"].startswith(f"Stopped the story of {deck.id}. It was waiting for his "
                                  f"answer to question tea.")
    got = _call(wired, "tell_package_story", package=deck.id, action="stop")
    assert f"The story of {deck.id} is already stopped by voice, at slide 2 of 5" in got["text"]
    assert "No story" not in got["text"]
    got = _call(wired, "tell_package_story", package=deck.id, action="status")
    assert got["text"].startswith("No story is running.")


@pytest.mark.asyncio
async def test_stop_speaking_and_his_stop_word_end_a_waiting_story(wired, deck, monkeypatch):
    server = wired.server
    stops: list[str] = []

    class Speech:
        is_speaking = False

        async def barge_in(self, reason=""):
            pass

    async def stop(by="on the page"):
        stops.append(by)
        server.story_teller.telling = None
        return story_teller.Told(deck, deck.story())
    monkeypatch.setattr(server, "speech", Speech())
    monkeypatch.setattr(server.story_teller, "telling", object())
    monkeypatch.setattr(server.story_teller, "stop", stop)
    assert await server._stop_speaking("the page") is True, "a waiting story counts as speaking"
    assert stops == ["with Stop speaking (the page)"]
    assert "his \"stop\"" not in " ".join(server._notes_for_brain)
