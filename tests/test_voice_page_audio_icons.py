"""The three audio controls, each with its own job.

- The microphone's Mute (#btn-mute): unchanged, microphone processing on or
  off, nothing else.
- The speaker (#btn-speaker): mutes and unmutes JARVIS's speech, the
  `speech_muted` voice input setting (voice_control.py); its look is the
  mute and what JARVIS is doing. Stop stays its own control.
- The headset, in the power button's place (#btn-mic-off): the whole audio
  system off (Mic off and speech muted) or back on, its look the combined
  state, still in every state.

The meter, Stop, Send and the health dot stay where they were, in the
message input and in the centre's corner.

Headless Chromium on the built page, with the voice socket replaced by
Playwright (test_voice_page_conversation), silent WAV chunks for audio, and
the voice input settings route answered here as voice_control answers it.
"""
import asyncio
import json
import time

import pytest

from tests.conftest import PAGE_VOICE_INPUT
from tests.test_agents_list_fits import async_api
from tests.test_voice_page_captions import _silence_wav
from tests.test_voice_page_conversation import _Voice, _open

pytestmark = pytest.mark.page

CONTROLS = ["mic-meter", "btn-mute", "btn-mic-off", "btn-speaker"]
ORDER = """sel => [...document.querySelector(sel).children].map(e => e.id || e.className)"""
# Whether each selector's element is in the message input's row, after the one before it.
IN_ORDER = """sels => sels.map(s => document.querySelector('.composer-footer ' + s))
  .every((e, i, all) => e && (i === 0 || (all[i - 1].compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)))"""
LOOK = """() => ({hearing: document.querySelector('#btn-mic-off').dataset.hearing,
                 cups: document.querySelector('#btn-mic-off').dataset.saying,
                 allOff: document.querySelector('#btn-mic-off').dataset.allOff,
                 speaker: document.querySelector('#btn-speaker').dataset.saying})"""
# Every running animation on the headset (none: it never moves).
HEADSET_MOVES = "() => document.getElementById('btn-mic-off').getAnimations({subtree: true}).length"


class Settings:
    """The voice input settings route, as voice_control answers it: a POST
    changes what the next GET reads."""

    def __init__(self):
        self.now = {**PAGE_VOICE_INPUT, "speech_muted": False}
        self.posts = []

    async def __call__(self, route):
        if route.request.method == "POST":
            change = json.loads(route.request.post_data or "{}")
            self.posts.append(change)
            self.now = {**self.now, **change, "saved": True}
        await route.fulfill(json=self.now)


async def _opened(p, page_url, voice):
    settings = Settings()
    browser, page = await _open(p, page_url, voice, routes={"**/api/settings/voice-input": settings})
    return browser, page, settings


def _hushes(voice):
    return [m for m in voice.got if m.get("type") == "hush"]


async def _until(predicate, seconds=5.0):
    for _ in range(int(seconds / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def _speak(page, voice, utt):
    voice.send({"type": "reply", "utt": utt, "at": time.time(), "text": "A long reply.",
                "done": True, "outcome": "succeeded"},
               {"type": "audio", "utt": utt, "idx": 0, "text": "A long reply.",
                "kind": "reply", "data": _silence_wav(8)})
    await page.wait_for_function("document.querySelector('#btn-speaker').dataset.saying === 'speaking'")


@pytest.mark.asyncio
async def test_the_controls_keep_their_places_and_mute_still_only_mutes(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page, settings = await _opened(p, page_url, voice)
        assert await page.evaluate(ORDER, "#mic-control") == CONTROLS
        assert await page.evaluate(IN_ORDER, ["#composer-help", "#health-slot", "#stop-slot",
                                              ".composer-send", "#mic-control"])
        for button in ("#btn-mute", "#btn-mic-off", "#btn-speaker"):
            box = await page.locator(button).bounding_box()
            assert (round(box["width"]), round(box["height"])) == (24, 24), button
        assert await page.locator("#btn-mic-off .headset-band").count() == 1
        assert await page.evaluate(LOOK) == {"hearing": "listening", "cups": "silent", "allOff": "false",
                                             "speaker": "silent"}
        # Mute: the microphone alone, as before; the headset's boom shows it.
        await page.click("#btn-mute")
        await page.wait_for_function("document.querySelector('#btn-mic-off').dataset.hearing === 'muted'")
        assert await page.text_content("#mic-silenced") == "Muted"
        assert (await page.evaluate(LOOK))["speaker"] == "silent" and settings.posts == []
        await page.click("#btn-mute")
        await page.wait_for_function("document.querySelector('#btn-mic-off').dataset.hearing === 'listening'")
        await browser.close()


@pytest.mark.asyncio
async def test_the_speaker_mutes_and_unmutes_speech_and_stop_stays_its_own(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page, settings = await _opened(p, page_url, voice)
        await _speak(page, voice, 41)
        assert (await page.evaluate(LOOK))["cups"] == "speaking"
        assert await page.evaluate(HEADSET_MOVES) == 0, "the headset never moves"
        await page.wait_for_selector("#hush")
        # Stop is Stop: it stops, and mutes nothing.
        await page.click("#hush")
        assert await _until(lambda: _hushes(voice))
        await page.wait_for_function("document.querySelector('#btn-speaker').dataset.saying === 'stopped'")
        assert settings.posts == []
        # The speaker mutes speech: the setting, and the microphone untouched.
        await page.click("#btn-speaker")
        await page.wait_for_function("document.querySelector('#btn-speaker').dataset.saying === 'muted'")
        assert settings.posts == [{"speech_muted": True}]
        assert await page.get_attribute("#btn-speaker", "aria-pressed") == "true"
        assert (await page.evaluate(LOOK))["hearing"] == "listening"
        assert (await page.evaluate(LOOK))["cups"] == "muted"
        # Another page or the brain changed it: this page reads it again.
        await page.click("#btn-speaker")
        await page.wait_for_function("document.querySelector('#btn-speaker').dataset.saying === 'silent'")
        assert settings.posts[-1] == {"speech_muted": False}
        await browser.close()


@pytest.mark.asyncio
async def test_the_headset_turns_all_audio_off_and_back_on(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page, settings = await _opened(p, page_url, voice)
        await page.click("#btn-mic-off")
        await page.wait_for_function("document.querySelector('#btn-mic-off').dataset.allOff === 'true'")
        assert settings.posts == [{"speech_muted": True}]
        assert await page.get_attribute("#btn-mic-off", "aria-pressed") == "true"
        assert await page.text_content("#mic-silenced") == "Mic off"
        assert (await page.evaluate(LOOK))["speaker"] == "muted"
        assert await page.get_attribute("#btn-mic-off", "title") == \
            "Turn all audio on (microphone and JARVIS's speech)"
        await page.click("#btn-mic-off")
        await page.wait_for_function("document.querySelector('#btn-mic-off').getAttribute('aria-pressed') === 'false'")
        await page.wait_for_function("document.querySelector('#btn-speaker').dataset.saying === 'silent'")
        assert settings.posts[-1] == {"speech_muted": False}
        assert (await page.evaluate(LOOK))["allOff"] == "false"
        await browser.close()


@pytest.mark.asyncio
async def test_the_corner_box_has_the_same_controls(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page, _ = await _opened(p, page_url, voice)
        await page.click(".right-tabs [role=tab][title=Diagnostics]")
        await page.click(".voice-summary-open")
        await page.wait_for_selector("#voice-diagnostics")
        await page.wait_for_selector("#centre-mic #mic-control")
        assert await page.evaluate(ORDER, "#centre-mic #mic-control") == CONTROLS
        assert await page.locator("#btn-mic-off .headset-band").count() == 1
        await browser.close()
