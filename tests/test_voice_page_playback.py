"""Playback on the voice page: what it reports about each chunk, and how a
reply and its reading ended.

Skip the rest (the playback bar's Skip, and Escape) and Stop speaking (the
status control's Stop) are different buttons with different frames. A chunk
whose audio will not decode is reported as failed, never as played. A reply
the provider did not finish is marked failed or cancelled, never shown as
complete.

Headless Chromium on the built page, with the voice socket replaced by
Playwright (test_voice_page_conversation) and silent WAV chunks for audio.
"""
import asyncio
import base64
import time

import pytest

from tests.test_agents_list_fits import async_api
from tests.test_voice_page_captions import _silence_wav
from tests.test_voice_page_conversation import _Voice, _open
from tests.fixtures.waiting import until_async

pytestmark = pytest.mark.page

NOT_AUDIO = base64.b64encode(b"this is not audio at all").decode()


def _of(voice, kind, utt=None):
    return [m for m in voice.got if m.get("type") == kind and (utt is None or m.get("utt") == utt)]


@pytest.mark.asyncio
async def test_a_chunk_that_will_not_decode_is_failed_never_played(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({"type": "reply", "utt": 21, "at": time.time(),
                    "text": "Broken first. Fine second.", "done": True, "outcome": "succeeded"},
                   {"type": "audio", "utt": 21, "idx": 0, "text": "Broken first.",
                    "kind": "reply", "data": NOT_AUDIO},
                   {"type": "audio", "utt": 21, "idx": 1, "text": "Fine second.",
                    "kind": "reply", "data": _silence_wav(0.2)})
        assert await until_async(lambda: _of(voice, "played", 21))
        assert _of(voice, "playback", 21)[0] == {"type": "playback", "utt": 21, "idx": 0,
                                                  "outcome": "failed"}
        assert {"type": "playback", "utt": 21, "idx": 1, "outcome": "started"} in voice.got
        assert [m["idx"] for m in _of(voice, "played", 21)] == [1], "the failed chunk is not played"
        note = page.locator(".chat-message.jarvis .chat-outcome")
        await async_api.expect(note).to_have_text("Not all read aloud")
        await browser.close()


@pytest.mark.asyncio
async def test_skip_stops_one_reading_and_stop_stops_all(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        now = time.time()
        voice.send({"type": "reply", "utt": 31, "at": now, "text": "A long reply.",
                    "done": True, "outcome": "succeeded"},
                   {"type": "audio", "utt": 31, "idx": 0, "text": "A long reply.",
                    "kind": "reply", "data": _silence_wav(8)},
                   {"type": "audio", "utt": 32, "idx": 0, "text": "An announcement after it.",
                    "kind": "announcement", "data": _silence_wav(8)})
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'speaking'")
        await page.get_by_role("button", name="Skip to end").click()
        assert await until_async(lambda: _of(voice, "skip_to_end")), voice.got
        assert _of(voice, "skip_to_end") == [{"type": "skip_to_end", "utt": 31}]
        # The announcement behind it still plays: Skip touched only reply 31.
        assert await until_async(lambda: {"type": "playback", "utt": 32, "idx": 0,
                                     "outcome": "started"} in voice.got)
        assert not _of(voice, "hush")
        await page.wait_for_selector("#hush")
        await page.click("#hush")                       # the status control's Stop
        assert await until_async(lambda: _of(voice, "hush"))
        assert _of(voice, "skip_to_end") == [{"type": "skip_to_end", "utt": 31}]
        assert not _of(voice, "played", 32)
        await async_api.expect(page.locator(".chat-message.jarvis .chat-outcome")).to_have_text(
            ["Reading skipped", "Reading stopped"])
        await browser.close()


@pytest.mark.asyncio
async def test_a_skip_by_the_brain_ends_the_reading_like_the_button(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({"type": "reply", "utt": 41, "at": time.time(), "text": "Being read.",
                    "done": True, "outcome": "succeeded"},
                   {"type": "audio", "utt": 41, "idx": 0, "text": "Being read.",
                    "kind": "reply", "data": _silence_wav(8)})
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'speaking'")
        voice.send({"type": "reading_skipped", "utt": 41},
                   {"type": "audio", "utt": 41, "idx": 1, "text": "Late audio.",
                    "kind": "reply", "data": _silence_wav(0.1)})
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state !== 'speaking'")
        await asyncio.sleep(0.4)
        assert not _of(voice, "played", 41), "late audio never revives a skipped reading"
        assert not _of(voice, "skip_to_end"), "the server skipped it; the page only follows"
        await async_api.expect(page.locator(".chat-outcome")).to_have_text("Reading skipped")
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,note", [("failed", "Reply failed · partial text"),
                                          ("cancelled", "Reply cancelled · partial text"),
                                          ("succeeded", None)])
async def test_a_reply_says_how_it_ended(page_url, outcome, note):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        at = time.time()
        voice.send({"type": "reply", "utt": 51, "at": at, "text": "Half of", "done": False})
        await page.wait_for_function(
            "document.querySelector('.chat-message.jarvis')?.textContent.includes('Responding')")
        voice.send({"type": "reply", "utt": 51, "at": at, "text": "Half of", "done": True,
                    "outcome": outcome})
        await page.wait_for_function(
            "!document.querySelector('.chat-message.jarvis').textContent.includes('Responding')")
        outcomes = page.locator(".chat-message.jarvis .chat-outcome")
        if note:
            await async_api.expect(outcomes).to_have_text([note])
        else:
            await async_api.expect(outcomes).to_have_count(0)
        await browser.close()
