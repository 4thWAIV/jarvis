"""Ask and Return on the page, during real speech: the example story deck in
its sandboxed frame, JARVIS's real speech scheduler speaking each beat
through the voice socket (a stand-in synthesizer whose audio lasts as long
as the words), and the page's Ask and Return (PackageBorder.tsx) reaching
the server's `_package_story` (story_teller.py).

Ask pauses the words at the beat being heard and keeps what the frame shows
(its scrolling and the part he selected); places nest; Return, by keyboard
or click, brings the view up again first and only then, if the words were
sounding when he asked, tells that beat again from its beginning; a paused
story stays paused; his speaking over a slide keeps its place the same way;
a place in a revision no longer in use is refused, in words on the page.
"""

import asyncio

import pytest

from jarvis import work_packages
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_story import _deck, _inner, _ringed, jarvis_app, voice  # noqa: F401

pytestmark = pytest.mark.page

ASK = "#package-view [data-story-action=ask]"
BACK = "#package-view [data-story-action=return]"
NOW = "#package-view .package-story-now"


def _recording(server, voice, monkeypatch) -> list:
    """Every frame JARVIS sends the page, in order: the scheduler's audio
    and the story teller's frames alike."""
    sent = []
    emit = voice.emit

    async def recorded(msg):
        sent.append(msg)
        await emit(msg)
    monkeypatch.setattr(voice.speech, "_emit_raw", recorded)
    monkeypatch.setattr(server, "_voice_emit", recorded)
    return sent


SCROLL_AND_PICK = """([y, target]) => {
  const deck = document.querySelector('.deck');
  deck.style.scrollSnapType = 'none';          // where it is put is where it stays
  deck.scrollTo({top: y, behavior: 'instant'});
  document.querySelector(target).dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
  return deck.scrollTop;
}"""


async def _told_from(page, voice, after: int, timeout: float = 30) -> dict:
    """The first audio frame sent after `after` frames: its cue."""
    for _ in range(int(timeout / 0.05)):
        if len(voice.audio) > after:
            return voice.audio[after]["cue"]
        await asyncio.sleep(0.05)
    raise AssertionError("nothing more was told")


@pytest.mark.asyncio(loop_scope="session")
async def test_ask_pauses_at_the_beat_nests_and_return_brings_each_place_back(
        page_playwright, page_url, jarvis_app, voice, monkeypatch):
    server, _, package = jarvis_app
    sent = _recording(server, voice, monkeypatch)
    async with page_playwright as p:
        browser, page, frame = await _deck(p, page_url, jarvis_app, voice)
        try:
            await page.click("#package-view [data-story-action=tell]")
            await _ringed(page, "(r) => r.beat === '1.2'")
            await page.click(ASK)
            await async_api.expect(page.locator(NOW)).to_have_text("Paused at slide 1 of 3.")
            outer = server.story_teller.asks[-1]
            assert outer.speaking and outer.slide == 1 and outer.beat >= 2
            await async_api.expect(page.locator(BACK)).to_be_visible()
            heard = len(voice.audio)
            await asyncio.sleep(1.0)
            assert len(voice.audio) == heard, "nothing more is said while he asks"

            # A second Ask, from elsewhere in the page: scrolled to the
            # second slide, its third step chosen. The story stays paused.
            await _inner(page).evaluate(SCROLL_AND_PICK, [700, "[data-step='3']"])
            await asyncio.sleep(0.8)                      # the frame says so, the page sends it
            await page.click(ASK)
            assert await _wait(lambda: len(server.story_teller.asks) == 2)
            inner = server.story_teller.asks[-1]
            assert not inner.speaking and inner.state == "paused"
            # Where the deck came to rest (its scroll snaps to a slide).
            kept_at = next(y for at, _, y in inner.view.scroll if at == "body > main:nth-of-type(1)")
            assert kept_at == 700, inner.view
            assert inner.view.part.endswith("li:nth-of-type(3)") and inner.view.words.startswith("Steep")
            await async_api.expect(page.locator(f"{BACK} .package-go-back-count")).to_have_text("2")

            # He moves on: the third slide, a tea's detail open.
            await _inner(page).evaluate(SCROLL_AND_PICK, [5000, ".tea[data-detail='black']"])
            await frame.locator(".tea[data-detail='black']").evaluate("(b) => b.click()")

            # Return by keyboard: the inner place, its scrolling, no words.
            await page.focus(BACK)
            await page.keyboard.press("Enter")
            await _inner(page).wait_for_function(
                f"Math.abs(document.querySelector('.deck').scrollTop - {kept_at}) <= 2",
                timeout=10000)
            await asyncio.sleep(1.0)
            assert len(voice.audio) == heard, "a paused presentation stays paused"
            await async_api.expect(page.locator(NOW)).to_have_text("Paused at slide 1 of 3.")
            await async_api.expect(page.locator(f"{BACK} .package-go-back-count")).to_have_text("1")

            # Return again: the outer place, the view first, then the beat
            # cut off told again from its beginning.
            back_at = len(sent)
            await page.click(BACK)
            cue = await _told_from(page, voice, heard)
            assert cue["beat"] == f"1.{outer.beat}", "the cut beat, from its beginning"
            kinds = [m["type"] for m in sent[back_at:]]
            assert kinds.index("package_return") < kinds.index("audio"), kinds
            await _ringed(page, f"(r) => r.beat === '1.{outer.beat}' && r.added > 0")
            await async_api.expect(page.locator(BACK)).to_have_count(0)
        finally:
            await voice.speech.stop()
        await browser.close()


async def _wait(check, timeout: float = 10) -> bool:
    for _ in range(int(timeout / 0.05)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio(loop_scope="session")
async def test_his_speaking_over_a_beat_keeps_its_place_and_return_tells_it_again(
        page_playwright, page_url, jarvis_app, voice):
    server, _, _ = jarvis_app
    async with page_playwright as p:
        browser, page, frame = await _deck(p, page_url, jarvis_app, voice)
        try:
            await page.click("#package-view [data-story-action=tell]")
            await _ringed(page, "(r) => r.beat === '1.2'")
            await voice.speech.barge_in(reason="he spoke")
            await async_api.expect(page.locator(NOW)).to_have_text("Paused at slide 1 of 3.")
            await async_api.expect(page.locator(BACK)).to_be_visible()
            place = server.story_teller.asks[-1]
            assert place.speaking and place.slide == 1
            heard = len(voice.audio)
            await page.click(BACK)
            cue = await _told_from(page, voice, heard)
            assert cue["beat"] == f"1.{place.beat}"
        finally:
            await voice.speech.stop()
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_place_in_a_changed_revision_is_refused_on_the_page(
        page_playwright, page_url, jarvis_app, voice):
    server, _, package = jarvis_app
    async with page_playwright as p:
        browser, page, frame = await _deck(p, page_url, jarvis_app, voice)
        try:
            await page.click("#package-view [data-story-action=tell]")
            await _ringed(page, "(r) => r.beat === '1.1'")
            await page.click(ASK)
            await async_api.expect(page.locator(BACK)).to_be_visible()
            work_packages._write_record(package.folder.name, "0" * 64, "a-newer-copy")
            heard = len(voice.audio)
            await page.click(BACK)
            await async_api.expect(page.locator("#package-view .package-status")).to_contain_text(
                "not the revision he asked from")
            await async_api.expect(page.locator(BACK)).to_have_count(0)
            await asyncio.sleep(1.0)
            assert len(voice.audio) == heard, "nothing is restored or told"
        finally:
            await voice.speech.stop()
        await browser.close()
