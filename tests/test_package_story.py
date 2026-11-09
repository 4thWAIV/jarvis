"""A page package's story (`jarvis/package_story.py`, `jarvis/story_teller.py`,
the scheduler's `narrate` and `withdraw`, `packages_api.py`'s story routes and
the brain's `tell_package_story`): the story file checked on unpack like the
rest of package.json, each beat spoken through the one speech path with its
cue on its audio frames, and the page's buttons and the brain's tool telling
and stopping it through one function.

Speech is the real SpeechScheduler over test_speech's fake synthesizer and
recording transport; nothing is synthesized or played for real."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest

from jarvis import package_story, spoken_words, work_packages
from jarvis.package_story import StoryError
from tests.fixtures.waiting import until_async
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_package_modes import BROWSER, EXAMPLES, PAGE, unpacked
from tests.test_speech import Harness, h  # noqa: F401  (the fixture)
from tests.test_work_packages import marks  # noqa: F401  (the fixture)

STORY = {"title": "T", "slides": [
    {"title": "One", "target": "#s1", "beats": [
        {"say": "First words. Second sentence.", "targets": ["#a", "#b"]},
        {"say": "Then this.", "actions": [{"click": "#open"}], "targets": ["#c"]}]},
    {"title": "Two", "beats": [{"say": "Last."}]}]}


def _story(**changes) -> bytes:
    return json.dumps({**STORY, **changes}).encode()


def example():
    """The example story deck, zipped by the packing tool and unpacked."""
    sys.path.insert(0, str(EXAMPLES))
    try:
        import zip_package
    finally:
        sys.path.remove(str(EXAMPLES))
    return zip_package


@pytest.fixture
def deck(tmp_path, marks):
    out = tmp_path / "story-deck.zip"
    assert example().main([str(EXAMPLES / "story-deck"), str(out)]) == 0
    package, _ = work_packages.unpack(out, "5t0ry")
    return package


# -- the format ------------------------------------------------------------------------

def test_the_example_story_is_checked_and_each_beat_first_shows_its_slide(deck):
    assert deck.manifest.story == "story.json" and deck.manifest.runs == "page"
    assert json.loads(deck.manifest.as_json())["story"] == "story.json"
    story = deck.story()
    assert story.outline() == {"title": "A pot of tea", "language": "en", "slides": [
        "Three things a good pot of tea needs", "Four steps", "Each tea has its own time"],
        "asks": []}
    cues = story.cues(deck.id, 3)
    words, cue = cues[1]
    assert words == "Green tea wants water well off the boil."
    assert cue == {"package": deck.id, "beat": "3.2", "slide": 3,
                   "targets": ["#detail-green .temp"],
                   "actions": [{"show": "#s3"}, {"click": ".tea[data-detail='green']"}]}
    assert [c["beat"] for _, c in story.cues(deck.id, 1)] == ["1.1", "1.2", "1.3", "1.4"]


@pytest.mark.parametrize("raw,said", [
    (b"{", "not valid JSON"),
    (_story(slides=[]), "slides is not a list with something in it"),
    (_story(language="fr"), "language is not one of en, es"),
    (_story(title=""), "title is missing"),
    (_story(slides=[{"title": "x", "beats": [{"say": "a", "actions": [{"type": "x"}]}]}]),
     "Slide 1, beat 1's action 1 is not one of"),
    (_story(slides=[{"title": "x", "beats": [{"say": "a" * 801}]}]), "longer than 800"),
    (_story(slides=[{"title": "x", "beats": [{"say": "a", "targets": ["#a\n#b"]}]}]),
     "is not one line"),
    (_story(slides=[{"title": "x", "beats": [{"say": "a", "targets": "#a"}]}]),
     "targets is not a list"),
    (b" " * (package_story.STORY_MAX_BYTES + 1), "over 256 KB"),
])
def test_a_story_that_cannot_be_told_is_refused_with_the_reason(raw, said):
    with pytest.raises(StoryError, match=said):
        package_story.parse(raw)


@pytest.mark.parametrize("manifest,files,said", [
    ({**PAGE, "story": "story.json"}, {"web/index.html": "x"}, "story file 'story.json' is not"),
    ({**PAGE, "story": "story.json"}, {"web/index.html": "x", "story.json": "{"},
     "story file 'story.json' is not valid: The story is not valid JSON"),
    ({**PAGE, "runs": "agent", "brief": "B.md", "story": "s.json"},
     {"B.md": "x", "s.json": json.dumps(STORY)}, "only a page package tells one"),
    ({**PAGE, "story": 3}, {"web/index.html": "x"}, "story is not a file's path"),
])
def test_unpacking_checks_the_story_like_the_rest_of_package_json(tmp_path, marks, manifest,
                                                                   files, said):
    with pytest.raises(work_packages.PackageError, match=said):
        unpacked(tmp_path, manifest, files)


def test_the_packing_tool_refuses_a_package_without_its_story_file(tmp_path, capsys):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "index.html").write_text("x")
    (folder / "package.json").write_text(json.dumps(
        {"name": "p", "version": "1", "purpose": "x", "runs": "page", "entry": "index.html",
         "story": "story.json", "returns": []}))
    assert example().main([str(folder), str(tmp_path / "p.zip")]) == 1
    assert "no story file story.json" in capsys.readouterr().out


# -- the speech path -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_each_beat_s_chunks_carry_its_cue_and_its_last_chunk_ends_it(h):
    story = package_story.parse(_story())
    utt = h.sched.narrate(story.cues("pkg", 1))
    await h.ack_all()
    assert await h.sched.wait_for(utt, timeout=2)
    frames = [m for m in h.msgs if m["type"] == "audio"]
    assert [m["text"] for m in frames] == ["First words.", "Second sentence.", "Then this."]
    assert [(m["cue"]["beat"], m["cue"]["last"]) for m in frames] == \
        [("1.1", False), ("1.1", True), ("1.2", True)]
    assert frames[0]["cue"]["targets"] == ["#a", "#b"] and frames[0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_withdraw_stops_one_utterance_and_leaves_the_rest(h):
    story = package_story.parse(_story())
    told = h.sched.narrate(story.cues("pkg", 1))
    other = await h.sched.say("Something else.")
    assert await until_async(lambda: told.outcomes()[0] == "queued"), told.outcomes()
    await h.sched.withdraw(told, reason="test")
    assert told.was_cancelled and "stop" in h.types()
    await h.ack_all()
    assert await h.sched.wait_for(other, timeout=2)
    assert ("Something else." in [t for _, _, t in h.audio()])


# -- the teller --------------------------------------------------------------------------

class Teller:
    """The story teller over the harness's scheduler, opening any page
    package as `_open_package_page` would, recording its frames."""

    def __init__(self, harness):
        from jarvis.story_teller import StoryTeller
        self.h, self.frames = harness, []

        async def open_page(reference):
            try:
                return work_packages.find(reference, work_packages.PAGE), ""
            except work_packages.PackageError as e:
                return None, str(e)

        async def announce(frame):
            self.frames.append(frame)
        self.teller = StoryTeller(lambda: harness.sched, open_page, announce)

    def states(self):
        return [(f["state"], f["slide"]) for f in self.frames]

    async def ended(self):
        task = self.teller.telling.task if self.teller.telling else None
        await self.h.ack_all(until=lambda: task is None or task.done())

    async def first_heard(self):
        """Wait for the slide being told to sound, then play its first chunk:
        a story stopped or spoken over mid-slide."""
        before = len(self.h.audio())
        assert await until_async(lambda: len(self.h.audio()) > before), "the slide never sounded"
        utt, idx, _ = self.h.audio()[before]
        await self.h.ack(utt, idx)

    async def play_for(self, seconds):
        """Play whatever is sent for `seconds` of real time: the time for
        something that must not happen (more words, the next slide) to show."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await self.h.ack_all(until=lambda: True)
            await asyncio.sleep(0.03)


@pytest.mark.asyncio
async def test_a_story_is_told_slide_by_slide_to_the_end(h, deck):
    t = Teller(h)
    told = await t.teller.tell(deck.id)
    assert told.package.id == deck.id and told.slide == 1
    await t.ended()
    assert t.states() == [("telling", 1), ("telling", 2), ("telling", 3), ("finished", 3)]
    spoken = [text for _, _, text in h.audio()]
    assert spoken[0] == "Three things make a decent pot of tea."
    assert spoken[-1] == "Black tea takes it hotter, and longer."
    assert t.teller.telling is None


@pytest.mark.asyncio
async def test_stop_ends_the_voice_and_the_story(h, deck, monkeypatch):
    audio_ready = asyncio.Event()
    async def emitted(message):
        await h.emit(message)
        if message["type"] == "audio":
            audio_ready.set()
    monkeypatch.setattr(h.sched, "_emit_raw", emitted)
    t = Teller(h)
    await t.teller.tell(deck.id, 2)
    # Stop active speech; acknowledging everything can leave an inter-slide gap.
    await asyncio.wait_for(audio_ready.wait(), 5)
    assert any(m["type"] == "audio" and not m.get("_acked") for m in h.msgs)
    stopped = await t.teller.stop()
    assert stopped.package.id == deck.id and stopped.slide is None
    assert "stop" in h.types() and t.states()[-1][0] == "stopped"
    heard = len(h.audio())
    await t.play_for(0.1)
    assert len(h.audio()) == heard, "nothing more is said once it is stopped"
    again = await t.teller.stop()
    assert again.already and again.ended.state == "stopped", "a second Stop: already, not none"


@pytest.mark.asyncio
async def test_his_speaking_pauses_the_story_and_it_is_still_running(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id)
    await t.first_heard()
    await h.sched.barge_in(reason="he spoke")
    assert await until_async(lambda: t.frames and t.frames[-1]["state"] == "paused"), t.states()
    assert t.states()[-1][0] == "paused" and t.teller.status()["state"] == "paused"
    assert (await t.teller.stop()).package.id == deck.id


@pytest.mark.asyncio
async def test_telling_from_another_slide_stops_the_one_being_told(h, deck):
    t = Teller(h)
    await t.teller.tell(deck.id)
    await t.first_heard()
    await t.teller.tell(deck.id, 3)
    assert ("stopped", 1) in t.states()
    await t.ended()
    assert t.states()[-1] == ("finished", 3)


@pytest.mark.asyncio
async def test_what_cannot_be_told_is_said_and_nothing_is_spoken(h, deck, tmp_path, marks,
                                                                  monkeypatch):
    t = Teller(h)
    assert (await t.teller.tell(deck.id, 4)).problem == "Its story has slides 1 to 3."
    plain = unpacked(tmp_path, PAGE, {"web/index.html": "x"}, "plain.zip")
    assert (await t.teller.tell(plain.id)).problem == f"{plain.id} has no story to tell."
    monkeypatch.setattr(spoken_words, "_active", spoken_words.SPANISH)
    assert "told in English, and I am speaking Spanish" in (await t.teller.tell(deck.id)).problem
    assert (await t.teller.act("dance", deck.id)).problem.startswith("'dance' is not one of")
    await asyncio.sleep(0.1)
    assert h.audio() == [] and t.frames == []


# -- the page's buttons and the brain's tool -------------------------------------------

def test_the_page_and_the_brain_tell_it_through_one_function(wired, deck, monkeypatch):
    from jarvis.story_teller import Told
    server, calls = wired.server, []

    async def act(action, reference, slide=None, by="on the page"):
        calls.append((action, reference, slide))
        package = work_packages.find(reference)
        if action == "stop":
            return Told(package, package.story())
        server.story_teller.telling = type("T", (), {"story": package.story()})()
        return Told(package, package.story(), slide or 1)
    monkeypatch.setattr(server.story_teller, "act", act)
    monkeypatch.setattr(server.story_teller, "telling", None)
    r = wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "tell", "slide": 2},
                          headers=BROWSER)
    assert r.status_code == 200 and r.json() == {
        "said": f"Telling the story of {deck.id} from slide 2 of 3."}
    got = _call(wired, "tell_package_story", package=deck.id, slide=2)
    assert got["ok"] and got["text"].startswith(f"Telling the story of {deck.id} from slide 2")
    head, _, block = got["text"].partition("Its slides:\n")
    assert "Four steps" not in head and 'untrusted="true"' in block and "2. Four steps" in block
    assert calls == [("tell", deck.id, 2)] * 2
    assert any("started the story" in n for n in server._notes_for_brain), \
        "the brain hears the page started it"
    r = wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "tell",
                                                                  "slide": "two"},
                          headers=BROWSER)
    assert r.status_code == 400
    assert wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "tell"}) \
        .status_code == 403, "a page origin or the tool token, like every change"


def test_a_refusal_comes_back_in_words(wired, deck):
    r = wired.client.post(f"/api/packages/{deck.id}/story", json={"action": "tell"},
                          headers=BROWSER)
    assert r.status_code == 409 and r.json()["error"] == "My voice isn't ready yet."
    got = _call(wired, "tell_package_story", package=deck.id)
    assert got["text"] == "My voice isn't ready yet. Nothing was changed."


def test_the_tool_acts_only_on_his_own_turn_and_is_granted(wired, deck):
    from jarvis import claude_provider, jarvis_mcp
    refused = _call(wired, "tell_package_story", origin="event", package=deck.id)
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
    server = wired.server
    assert "tell_package_story" in server.ACTING_TOOLS & set(server.TAINTING_TOOLS)
    assert "tell_package_story" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert "mcp__jarvis__tell_package_story" in claude_provider.ALLOWED_TOOLS


def test_the_helper_is_served_from_its_one_copy_and_named_in_the_outline(wired, deck):
    r = wired.client.get(package_story.HELPER_PATH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert b"story.cue" in r.content and b"innerHTML" not in r.content
    listed = wired.client.get("/api/packages").json()["packages"]
    assert listed[0]["story"]["slides"][1] == "Four steps"
    assert b'src="/api/packages/story.js"' in (deck.folder / "index.html").read_bytes()
