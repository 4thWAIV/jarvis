"""Ask and Return (story_teller.py, package_place.py): Ask keeps exactly
where he is in a page package, the story's slide and beat and what its frame
shows, and pauses the words if they were sounding; Return takes the last
place back, the view first, and tells the cut beat again only if its words
were sounding; Stop keeps its place for resume; places nest; a place in a
revision no longer in use is refused and set aside. The page's route and
the brain's tell_package_story go through the one function.

Speech is the real SpeechScheduler over test_speech's fake synthesizer and
recording transport, over the example story deck."""
from __future__ import annotations

import json

import pytest

from jarvis import package_place, work_packages
from jarvis.package_place import ViewError, view_from
from tests.fixtures.waiting import until_async
from tests.test_brain_page_parity import HOSTILE, _call, _walled, wired  # noqa: F401
from tests.test_package_modes import BROWSER
from tests.test_package_story import Teller, deck  # noqa: F401  (the fixture)
from tests.test_speech import Harness, h  # noqa: F401  (the fixture)
from tests.test_work_packages import marks  # noqa: F401  (the fixture)

VIEW = {"page": "#go-1", "scroll": [{"at": ".deck", "x": 0, "y": 340}, {"at": "", "x": 0, "y": 0}],
        "part": "#leaves", "words": "Good leaves Loose, and kept dry.", "selection": [0, 11],
        "selected": "Good leaves", "focused": None}


def said(t):
    return [text for _, _, text in t.h.audio()]


def state(t):
    f = t.frames[-1] if t.frames else {}
    return f.get("state"), f.get("slide"), f.get("beat")


async def first_beat_heard(t):
    """Tell slide 1 and play its first beat: its words now at beat 2."""
    await t.teller.tell(t.deck.id)
    await t.h.sent(1)
    utt, idx, text = t.h.audio()[0]
    assert text == "Three things make a decent pot of tea."
    await t.h.ack(utt, idx)
    assert await until_async(lambda: len(t.h.audio()) > 1), "beat 2 never went out"


def teller(h, deck, **kw):
    t = Teller(h)
    t.deck = deck
    for name, value in kw.items():
        setattr(t.teller, name, value)
    return t


# -- the place ---------------------------------------------------------------------------

def test_a_view_is_checked_and_bounded():
    v = view_from({**VIEW, "words": "x " * 2000})
    assert v.page == "#go-1" and v.scroll == ((".deck", 0, 340), ("", 0, 0))
    assert len(v.words) <= package_place.MAX_WORDS and v.selection == (0, 11)
    for bad in ({"scroll": "down"}, {"scroll": [{"at": ".a", "y": "far"}]},
                {"part": "a\nb"}, {"selection": [5, 2]}, {"page": 3}, "view"):
        with pytest.raises(ViewError):
            view_from(bad)
    assert view_from(None) == package_place.View()


def test_what_a_slide_had_revealed_by_a_beat(deck):
    story = deck.story()
    assert package_place.revealed(story, 3, 3, through=False) == [
        {"show": "#s3"}, {"click": ".tea[data-detail='green']"}]
    assert package_place.revealed(story, 3, 4, through=True)[-1] == {"click": "#detail-green .close"}
    assert package_place.revealed(story, 1, 1, through=False) == [{"show": "#s1"}]
    assert package_place.revealed(None, 1, 1, through=True) == []


def test_a_revision_is_the_bytes_it_was_unpacked_from(deck):
    assert package_place.revision_of(deck) == work_packages.sha256_of(deck) != ""


# -- ask ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ask_while_told_keeps_the_slide_and_beat_and_pauses_the_words(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    told = await t.teller.act("ask", deck.id, view=VIEW)
    place = told.place
    assert (place.state, place.slide, place.beat, place.speaking) == ("telling", 1, 2, True)
    assert place.view.part == "#leaves" and place.revision == work_packages.sha256_of(deck)
    assert told.asks == 1
    assert await until_async(lambda: state(t) == ("paused", 1, 2)), t.frames
    heard = len(h.audio())
    await t.play_for(0.2)
    assert len(h.audio()) == heard, "nothing more is said while he asks"
    asks = [f for f in t.others if f["type"] == "package_asks"][-1]["asks"]
    assert asks == [{"package": deck.id, "state": "telling", "slide": 1, "beat": 2,
                     "speaking": True, "page": "#go-1"}]


@pytest.mark.asyncio
async def test_his_speaking_over_a_slide_keeps_its_place_as_an_ask(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    await h.sched.barge_in(reason="he spoke")
    assert await until_async(lambda: state(t) == ("paused", 1, 2)), t.frames
    place = t.teller.asks[-1]
    assert (place.slide, place.beat, place.speaking) == (1, 2, True)


# -- return ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_return_restores_the_view_first_then_tells_the_cut_beat_again(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    await t.teller.act("ask", deck.id, view=VIEW)
    assert await until_async(lambda: state(t)[0] == "paused")
    before = len(h.audio())
    told = await t.teller.act("return", "")
    assert told.package.id == deck.id and told.asks == 0
    back = next(f for f in t.others if f["type"] == "package_return")
    assert back["view"]["scroll"] == [{"at": ".deck", "x": 0, "y": 340}, {"at": "", "x": 0, "y": 0}]
    assert back["view"]["part"] == "#leaves" and back["revealed"] == [{"show": "#s1"}]
    assert await until_async(lambda: len(h.audio()) > before)
    assert said(t)[before] == "Fresh water, boiled once.", "the cut beat, from its beginning"
    assert state(t) == ("telling", 1, 2)
    await t.ended()
    assert said(t)[-1] == "Black tea takes it hotter, and longer."


@pytest.mark.asyncio
async def test_nested_asks_return_one_at_a_time_and_a_paused_story_stays_paused(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    await t.teller.act("ask", deck.id, view=VIEW)              # outer: words sounding
    assert await until_async(lambda: state(t) == ("paused", 1, 2))
    inner = {**VIEW, "page": "#go-3", "part": ".tea[data-detail='green']", "words": "Green"}
    told = await t.teller.act("ask", deck.id, view=inner)       # inner: paused already
    assert told.asks == 2 and not told.place.speaking and told.place.state == "paused"

    heard = len(h.audio())
    back = await t.teller.act("return", "")
    assert back.place.view.page == "#go-3" and back.asks == 1
    await t.play_for(0.3)
    assert len(h.audio()) == heard, "a paused presentation stays paused"
    assert state(t)[0] == "paused" and t.teller.telling is not None

    back = await t.teller.act("return", "")
    assert back.place.view.page == "#go-1" and back.asks == 0
    assert await until_async(lambda: len(h.audio()) > heard)
    assert said(t)[heard] == "Fresh water, boiled once."
    assert (await t.teller.act("return", "")).problem.startswith("There is no place to go back")


@pytest.mark.asyncio
async def test_a_place_in_a_revision_no_longer_in_use_is_refused_and_set_aside(h, deck):
    t = teller(h, deck)
    await t.teller.act("ask", deck.id, view=VIEW)
    work_packages._write_record(deck.folder.name, "0" * 64, "another-attachment")
    told = await t.teller.act("return", "")
    assert told.package is None and "not the revision he asked from" in told.problem
    assert t.teller.asks == [] and not any(f["type"] == "package_return" for f in t.others)

    t = teller(h, deck, _replaced=lambda _id: "replaced by a newer revision")
    await t.teller.act("ask", deck.id)
    told = await t.teller.act("return", "")
    assert "replaced by a newer revision" in told.problem and t.teller.asks == []


@pytest.mark.asyncio
async def test_a_return_whose_page_cannot_open_keeps_the_place(h, deck):
    t = teller(h, deck)
    await t.teller.act("ask", deck.id)

    async def no_page(_reference):
        return None, "No JARVIS page is open to show it on."
    t.teller._open_page = no_page
    told = await t.teller.act("return", "")
    assert told.problem.startswith("No JARVIS page") and len(t.teller.asks) == 1


# -- stop keeps the place ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_keeps_the_place_and_return_tells_that_beat_again(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    await t.teller.stop()
    place = t.teller.asks[-1]
    assert (place.slide, place.beat, place.speaking) == (1, 2, True)
    assert t.teller.ended.place == place
    before = len(h.audio())
    told = await t.teller.act("return", "")
    assert told.slide == 1 and t.teller.asks == []
    assert await until_async(lambda: len(h.audio()) > before)
    assert said(t)[before] == "Fresh water, boiled once."


@pytest.mark.asyncio
async def test_resume_from_a_pause_goes_on_from_its_beat_and_leaves_its_asks_behind(h, deck):
    t = teller(h, deck)
    await first_beat_heard(t)
    await h.sched.barge_in(reason="he spoke")
    assert await until_async(lambda: state(t)[0] == "paused")
    assert len(t.teller.asks) == 1
    before = len(h.audio())
    await t.teller.act("resume", deck.id)
    assert await until_async(lambda: len(h.audio()) > before)
    assert said(t)[before] == "Fresh water, boiled once." and t.teller.asks == []


# -- the page's route and the brain's tool -------------------------------------------------

def test_the_page_and_the_brain_ask_and_return_through_one_function(wired, deck):
    server = wired.server
    r = wired.client.post(f"/api/packages/{deck.id}/view",
                          json={"view": {**VIEW, "words": HOSTILE}}, headers=BROWSER)
    assert r.status_code == 204
    assert server.story_teller.views[deck.id].words == HOSTILE
    assert wired.client.post(f"/api/packages/{deck.id}/view", json={"view": {"part": 3}},
                             headers=BROWSER).status_code == 400

    got = _call(wired, "tell_package_story", package=deck.id, action="place")
    assert got["ok"] and "0 place(s) kept" in got["text"]
    _walled(got["text"])
    assert "The part he selected: Matt" in got["text"]

    r = wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "ask", "view": VIEW},
                          headers=BROWSER)
    assert r.status_code == 200 and r.json()["said"].startswith("Kept his place.")
    assert any("to ask you about what he is looking at" in n for n in server._notes_for_brain)
    assert wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "ask"}) \
        .status_code == 403, "his click, or his own turn"
    got = _call(wired, "tell_package_story", package=deck.id, action="ask")
    assert got["ok"] and "2 place(s) kept" in got["text"]
    _walled(got["text"].split("Kept his place. ", 1)[1])

    # No page is open to go back to: refused in words, the place kept.
    got = _call(wired, "tell_package_story", action="return")
    assert "No JARVIS page is open" in got["text"] and len(server.story_teller.asks) == 2
    r = wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "return"},
                          headers=BROWSER)
    assert r.status_code == 409 and len(server.story_teller.asks) == 2


def test_a_reload_is_told_the_places_it_can_go_back_to(wired, deck):
    server = wired.server
    wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "ask"}, headers=BROWSER)
    frames = server._opening_frames()
    asks = next(f for f in frames if f["type"] == "package_asks")["asks"]
    assert [a["package"] for a in asks] == [deck.id]
    assert json.dumps(asks)                     # plain JSON, the page's own numbers


# -- a pending decision ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_asking_while_a_question_waits_returns_to_it_still_waiting(h, tmp_path, marks,
                                                                         monkeypatch):
    from jarvis import story_teller
    monkeypatch.setattr(story_teller, "ANSWER_POLL_SEC", 0.02)
    monkeypatch.setattr(story_teller, "FLOOR_SETTLE_SEC", 0.02)
    from tests.test_package_modes import PAGE, unpacked
    from tests.test_story_waiting import STORY, answer
    choices = unpacked(tmp_path, {**PAGE, "story": "story.json"},
                       {"web/index.html": "x", "story.json": json.dumps(STORY)})
    t = teller(h, choices)
    await t.teller.tell(choices.id, 2)
    await h.ack_all(until=lambda: bool(t.frames) and t.frames[-1].get("question") == "tea")
    told = await t.teller.act("ask", choices.id, view={"part": "#tea", "words": "Which tea?"})
    assert (told.place.state, told.place.question, told.place.speaking) == ("waiting", "tea", False)
    heard = len(h.audio())
    back = await t.teller.act("return", "")
    assert back.package.id == choices.id and back.asks == 0
    await t.play_for(0.3)
    assert len(h.audio()) == heard and t.teller.telling.state == "waiting", \
        "nothing is said again: it waits for his answer, as it did"
    answer(choices, "tea", "green")
    await h.ack_all(until=lambda: t.frames[-1].get("question") == "cups")
