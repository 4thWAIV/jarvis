"""Telling a page package's story: the one service the page's Tell the story
and Stop (packages_api.py, `POST /api/packages/<id>/story`) and the brain's
acting `tell_package_story` both call.

Telling opens the package's page (the same `_open_package_page` as Open
page), then speaks its slides through the speech scheduler's `narrate`, the
path every reply takes: Fish synthesis in the language in force, turn-taking
as it is, and his speaking, hush or a cancel word stopping it as they stop
any reply. Each beat's chunks carry its cue, so the page points at a beat's
targets when its words start playing, not on a timer.

The beats are the package's narration: JARVIS speaks the sender's words
verbatim, in his own voice, as a reading of what the package says. They
never reach the brain, and they are never taken as JARVIS's own decisions or
as instructions; whatever of the story the brain is shown (its slides'
titles) goes back to it in an untrusted block (server.tool_tell_package_story).

Slides are queued two at a time (the one being heard and the next), so the
next is synthesized while this one plays and a long story is never
synthesized all at once. One story is told at a time; telling another, or
the same from another slide, stops the one being told first.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from jarvis import language_setting, spoken_words
from jarvis.package_story import Story
from jarvis.work_packages import Package, PackageError

log = logging.getLogger("jarvis.story_teller")

# How long one slide may take to be heard before the story is given up.
SLIDE_TIMEOUT_SEC = 30 * 60
# Slides queued with the speech scheduler at once.
AHEAD = 2

TELL, STOP = "tell", "stop"
ACTIONS = (TELL, STOP)
# What a story is doing, as the page's `package_story` frame says it.
TELLING, FINISHED, STOPPED = "telling", "finished", "stopped"


@dataclass
class Telling:
    package: Package
    story: Story
    slide: int                                   # the slide being heard, from 1
    utterances: list = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    stopped: bool = False


@dataclass(frozen=True)
class Told:
    """What a tell or a stop did: the package, its story and the slide it
    started from (None for a stop), or why nothing was done."""
    package: Optional[Package]
    story: Optional[Story] = None
    slide: Optional[int] = None
    problem: str = ""


class StoryTeller:
    """`speech()` is the speech scheduler (None until it is up);
    `open_page(reference)` opens a page package as Open page does: (the
    package, "") or (None, why not); `announce(frame)` sends a
    `package_story` frame to every page."""

    def __init__(self, speech: Callable[[], object],
                 open_page: Callable[[str], Awaitable[tuple[Optional[Package], str]]],
                 announce: Callable[[dict], Awaitable[None]]):
        self._speech = speech
        self._open_page = open_page
        self._announce = announce
        self._lock = asyncio.Lock()
        self.telling: Optional[Telling] = None

    async def act(self, action: str, reference: str, slide: Optional[int] = None) -> Told:
        """The page's buttons and the brain's tool: tell (from `slide`, or
        the first) or stop."""
        if action == STOP:
            return await self.stop()
        if action != TELL:
            return Told(None, problem=f"{action!r} is not one of {', '.join(ACTIONS)}.")
        return await self.tell(reference, slide or 1)

    async def tell(self, reference: str, slide: int = 1) -> Told:
        async with self._lock:
            speech = self._speech()
            if speech is None:
                return Told(None, problem="My voice isn't ready yet.")
            package, problem = await self._open_page(reference)
            if package is None:
                return Told(None, problem=problem)
            try:
                story = await asyncio.to_thread(package.story)
            except PackageError as e:
                return Told(None, problem=str(e))
            if story is None:
                return Told(None, problem=f"{package.id} has no story to tell.")
            if not 1 <= slide <= len(story.slides):
                return Told(None, problem=f"Its story has slides 1 to {len(story.slides)}.")
            language = spoken_words.active_language()
            if story.language != language:
                names = language_setting.NAMES
                return Told(None, problem=(
                    f"Its story is told in {names[story.language]}, and I am speaking "
                    f"{names[language]}; switch the language first."))
            await self._stop_locked()
            telling = Telling(package, story, slide)
            self.telling = telling
            telling.task = asyncio.create_task(self._tell(speech, telling))
            log.info("story of %s told from slide %d", package.id, slide)
            return Told(package, story, slide)

    async def stop(self) -> Told:
        async with self._lock:
            telling = await self._stop_locked()
            if telling is None:
                return Told(None, problem="No story is being told.")
            return Told(telling.package, telling.story)

    async def _stop_locked(self) -> Optional[Telling]:
        telling, self.telling = self.telling, None
        if telling is None:
            return None
        telling.stopped = True
        speech = self._speech()
        for utt in list(telling.utterances):
            await speech.withdraw(utt, reason="story stopped")
        if telling.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(telling.task), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                telling.task.cancel()
        return telling

    def status(self) -> Optional[dict]:
        """The story being told now, as the page's frame says it, or None."""
        t = self.telling
        return None if t is None else self._frame(t, TELLING)

    @staticmethod
    def _frame(t: Telling, state: str) -> dict:
        return {"type": "package_story", "package": t.package.id, "state": state,
                "slide": t.slide, "slides": len(t.story.slides)}

    async def _tell(self, speech, t: Telling) -> None:
        """Queue the slides AHEAD at a time and wait for each to be heard;
        stop at the first not heard in full (stopped, hushed, barged in on)."""
        heard = True
        queued: list[tuple[int, object]] = []
        upcoming = iter(range(t.slide, len(t.story.slides) + 1))
        try:
            while True:
                while len(queued) < AHEAD and not t.stopped:
                    number = next(upcoming, None)
                    if number is None:
                        break
                    utt = speech.narrate(t.story.cues(t.package.id, number))
                    t.utterances.append(utt)
                    queued.append((number, utt))
                if not queued or t.stopped:
                    break
                number, utt = queued.pop(0)
                t.slide = number
                await self._announce(self._frame(t, TELLING))
                heard = await speech.wait_for(utt, timeout=SLIDE_TIMEOUT_SEC)
                t.utterances.remove(utt)
                if not heard:
                    break
        except Exception as e:                   # the story must end, whatever happens
            log.error("story of %s failed: %s", t.package.id, e, exc_info=True)
            heard = False
        finally:
            for utt in t.utterances:
                await speech.withdraw(utt, reason="story ended")
            t.utterances.clear()
            if self.telling is t:
                self.telling = None
            await self._announce(self._frame(t, FINISHED if heard and not t.stopped
                                             else STOPPED))
