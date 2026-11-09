"""Telling a page package's story: the one service the page's Tell the story,
Continue and Stop (packages_api.py, `POST /api/packages/<id>/story`) and the
brain's acting `tell_package_story` all call.

Telling opens the package's page (the same `_open_package_page` as Open
page), then speaks its slides through the speech scheduler's `narrate`, the
path every reply takes: Fish synthesis in the language in force, turn-taking
as it is. Each beat's chunks carry its cue, so the page points at a beat's
targets when its words start playing, not on a timer.

A story that asks questions (package_story.py: `answers`, a slide's `waits`)
is told one question at a time. A slide that waits is told, then nothing
more is said: the story is WAITING, and the server knows which question,
until that question has an answer in the package's answers file, whether
from the page's Choose (saved through the bridge) or from his words recorded
with the package's own script. Then, once the floor is free (his turn and
the brain's reply are over), it goes on to the next slide. A question
already answered is passed over, unless the story was told from that very
slide (going back to it), when it waits for a new answer. A question that
does not apply (`asks_when`) is passed over.

What the story is doing is one record here (`Telling`): TELLING, WAITING
for an answer, or PAUSED (his speaking cut a slide off; it stays on that
slide until Continue or Stop). Each is running: `status` gives it, Stop ends
it, and every page is told by a `package_story` frame. His speaking on a
slide that waits is taken as waiting for his answer, since that is what he
was asked. Stop (the page's, the brain's, Stop speaking or a whole
utterance of stop words, `stop_words`) ends it at once and says so; after a
Stop, asking to stop again says it is already stopped, never that there is
no story. `resume` tells it again from the first question with no answer.

The beats are the package's narration: JARVIS speaks the sender's words
verbatim, in his own voice, as a reading of what the package says. They
never reach the brain, and they are never taken as JARVIS's own decisions or
as instructions; whatever of the story the brain is shown (its slides'
titles) goes back to it in an untrusted block (server.tool_tell_package_story).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from jarvis import language_setting, spoken_words, work_packages
from jarvis.package_story import Slide, Story
from jarvis.work_packages import Package, PackageError

log = logging.getLogger("jarvis.story_teller")

# How long one slide may take to be heard before the story is given up.
SLIDE_TIMEOUT_SEC = 30 * 60
# How often an answer is looked for while a story waits for one, and how
# long the floor must have been free before the next slide is spoken.
ANSWER_POLL_SEC = 0.2
FLOOR_SETTLE_SEC = 0.4
# How long after a Stop asking to stop again says it is already stopped.
REMEMBER_STOPPED_SEC = 60 * 60

TELL, RESUME, STOP, STATUS = "tell", "resume", "stop", "status"
ACTIONS = (TELL, RESUME, STOP, STATUS)
# What a story is doing, as the page's `package_story` frame says it.
TELLING, WAITING, PAUSED, FINISHED, STOPPED = "telling", "waiting", "paused", "finished", "stopped"
RUNNING = (TELLING, WAITING, PAUSED)


@dataclass
class Telling:
    package: Package
    story: Story
    slide: int                                   # the slide being heard, from 1
    start: int                                   # the slide it was told from
    explicit: bool                               # told from that slide on purpose
    state: str = TELLING
    question: Optional[str] = None               # the question it waits for
    utterances: list = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    stopped: bool = False
    completed_through_beat: Optional[str] = None


@dataclass(frozen=True)
class Ended:
    """The story last ended: how, where, by whom and when."""
    package: Package
    story: Story
    slide: int
    state: str
    by: str
    at: float


@dataclass(frozen=True)
class Told:
    """What a tell, a stop or a status did: the package, its story and the
    slide it started from (None for a stop), or why nothing was done.
    `already`: a stop found it already stopped (`ended` says how)."""
    package: Optional[Package]
    story: Optional[Story] = None
    slide: Optional[int] = None
    problem: str = ""
    already: bool = False
    ended: Optional[Ended] = None
    waiting: Optional[str] = None                # the question a stopped story waited for


class StoryTeller:
    """`speech()` is the speech scheduler (None until it is up);
    `open_page(reference)` opens a page package as Open page does: (the
    package, "") or (None, why not); `announce(frame)` sends a
    `package_story` frame to every page; `floor_free()` says nobody is
    speaking or about to (his turn, the brain's reply); `note(text)` tells
    the brain at its next turn."""

    def __init__(self, speech: Callable[[], object],
                 open_page: Callable[[str], Awaitable[tuple[Optional[Package], str]]],
                 announce: Callable[[dict], Awaitable[None]],
                 floor_free: Callable[[], bool] = lambda: True,
                 note: Callable[[str], None] = lambda _text: None,
                 clock: Callable[[], float] = time.monotonic):
        self._speech = speech
        self._open_page = open_page
        self._announce = announce
        self._floor_free = floor_free
        self._note = note
        self._clock = clock
        self._lock = asyncio.Lock()
        self.telling: Optional[Telling] = None
        self.ended: Optional[Ended] = None

    async def act(self, action: str, reference: str, slide: Optional[int] = None,
                  by: str = "on the page") -> Told:
        """The page's buttons and the brain's tool: tell (from `slide`, or
        the first), resume (from the first question with no answer), stop,
        or status."""
        if action == STOP:
            return await self.stop(by)
        if action == STATUS:
            return self.state_of(reference)
        if action == RESUME:
            return await self.tell(reference, None)
        if action != TELL:
            return Told(None, problem=f"{action!r} is not one of {', '.join(ACTIONS)}.")
        return await self.tell(reference, slide or 1)

    # -- telling --------------------------------------------------------------------------

    async def tell(self, reference: str, slide: Optional[int] = 1) -> Told:
        """Tell from `slide`, or with None from where it left off: the first
        question with no answer (slide 1 when none has one yet)."""
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
            explicit = slide is not None
            if slide is None:
                slide = await asyncio.to_thread(self._resume_slide, package, story)
            if not 1 <= slide <= len(story.slides):
                return Told(None, problem=f"Its story has slides 1 to {len(story.slides)}.")
            language = spoken_words.active_language()
            if story.language != language:
                names = language_setting.NAMES
                return Told(None, problem=(
                    f"Its story is told in {names[story.language]}, and I am speaking "
                    f"{names[language]}; switch the language first."))
            await self._stop_locked("told again")
            telling = Telling(package, story, slide, slide, explicit)
            self.telling = telling
            telling.task = asyncio.create_task(self._tell(speech, telling))
            log.info("story of %s told from slide %d", package.id, slide)
            return Told(package, story, slide)

    def _resume_slide(self, package: Package, story: Story) -> int:
        record = read_answers(package, story)
        if not any(isinstance(record.get(s.waits), dict) for s in story.slides if s.waits):
            return 1
        open_ = story.open_questions(record)
        return open_[0] if open_ else len(story.slides)

    async def _tell(self, speech, t: Telling) -> None:
        """Speak each slide in turn; after a slide that waits, wait for its
        answer; pause where his speaking cut a slide off; end at the last."""
        state = FINISHED
        number = t.start
        paused = False
        try:
            while number <= len(t.story.slides) and not t.stopped:
                slide = t.story.slides[number - 1]
                record = await asyncio.to_thread(read_answers, t.package, t.story)
                asked_again = t.explicit and number == t.start
                if slide.waits and not asked_again and (
                        isinstance(record.get(slide.waits), dict)
                        or not t.story.applies(slide, record)):
                    number += 1                  # answered, or not asked
                    continue
                if slide.asks_when and not t.story.applies(slide, record) and not asked_again:
                    number += 1
                    continue
                before = record.get(slide.waits) if slide.waits else None
                t.slide, t.state, t.question = number, TELLING, None
                utt = speech.narrate(t.story.cues(t.package.id, number))
                t.utterances = [utt]
                await self._announce(self._frame(t))
                heard = await speech.wait_for(utt, timeout=SLIDE_TIMEOUT_SEC)
                t.utterances = []
                if heard:
                    t.completed_through_beat = f"{number}.{len(slide.beats)}"
                if t.stopped:
                    break
                if slide.waits:
                    await self._wait_for_answer(t, slide, before)
                    if t.stopped:
                        break
                    await self._wait_for_floor(t)
                    number += 1
                    continue
                if not heard:
                    paused = True
                    t.state = PAUSED
                    await self._announce(self._frame(t))
                    self._note(f"The story of package {t.package.id} is paused at slide "
                               f"{number} of {len(t.story.slides)}: he spoke over it. "
                               f"tell_package_story with action resume goes on from where it "
                               f"left off; stop ends it.")
                    return                       # still running, on this slide
                number += 1
        except asyncio.CancelledError:
            state = STOPPED
            raise
        except Exception as e:                   # the story must end, whatever happens
            log.error("story of %s failed: %s", t.package.id, e, exc_info=True)
            state = STOPPED
        finally:
            if paused:
                return                           # it stays the story running
            for utt in t.utterances:
                await speech.withdraw(utt, reason="story ended")
            t.utterances = []
            if t.stopped:
                state = STOPPED
            if self.telling is t:
                self.telling = None
                self.ended = Ended(t.package, t.story, t.slide, state,
                                   "at its end" if state == FINISHED else "", self._clock())
            if state == FINISHED or not t.stopped:
                # A stop announces its own end (_stop_locked).
                t.state = state
                await self._announce(self._frame(t))

    async def _wait_for_answer(self, t: Telling, slide: Slide, before) -> None:
        """WAITING until `slide`'s question has an answer it did not have
        when the slide began, or the story is stopped."""
        t.state, t.question = WAITING, slide.waits
        await self._announce(self._frame(t))
        # The question's id is the sender's words: it reaches the brain only
        # walled, through tell_package_story's status.
        self._note(f"The story of package {t.package.id} is waiting for his answer on "
                   f"slide {t.slide} of {len(t.story.slides)}; it goes on by itself once "
                   f"that is recorded (tell_package_story status gives the question).")
        log.info("story of %s waits for %s", t.package.id, slide.waits)
        while not t.stopped:
            record = await asyncio.to_thread(read_answers, t.package, t.story)
            now = record.get(slide.waits)
            if isinstance(now, dict) and now != before:
                log.info("story of %s: %s answered", t.package.id, slide.waits)
                t.question = None
                return
            await asyncio.sleep(ANSWER_POLL_SEC)

    async def _wait_for_floor(self, t: Telling) -> None:
        """Until nobody has been speaking or about to for FLOOR_SETTLE_SEC:
        the reply to his answer is heard before the next slide."""
        free_since = None
        while not t.stopped:
            if self._floor_free():
                free_since = free_since if free_since is not None else self._clock()
                if self._clock() - free_since >= FLOOR_SETTLE_SEC:
                    return
            else:
                free_since = None
            await asyncio.sleep(0.05)

    # -- stopping -------------------------------------------------------------------------

    async def stop(self, by: str = "on the page") -> Told:
        async with self._lock:
            telling = await self._stop_locked(by)
            if telling is None:
                ended = self.ended
                if (ended is not None and ended.state == STOPPED
                        and self._clock() - ended.at <= REMEMBER_STOPPED_SEC):
                    return Told(ended.package, ended.story, already=True, ended=ended)
                return Told(None, problem="No story is being told.")
            return Told(telling.package, telling.story, waiting=telling.question)

    async def stop_for(self, package_id: str, by: str) -> bool:
        """Stop the story only if it is this package's (its page closed)."""
        if self.telling is None or self.telling.package.id != package_id:
            return False
        return (await self.stop(by)).package is not None

    async def _stop_locked(self, by: str) -> Optional[Telling]:
        """End the story now: its words withdrawn, its waiting ended, every
        page told it stopped, and remembered as stopped."""
        telling, self.telling = self.telling, None
        if telling is None:
            return None
        telling.stopped = True
        speech = self._speech()
        for utt in list(telling.utterances):
            await speech.withdraw(utt, reason="story stopped")
        task = telling.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        state_before = telling.state
        telling.state = STOPPED
        self.ended = Ended(telling.package, telling.story, telling.slide, STOPPED, by,
                           self._clock())
        await self._announce(self._frame(telling))
        log.info("story of %s stopped %s at slide %d (it was %s)", telling.package.id, by,
                 telling.slide, state_before)
        return telling

    # -- what it is doing -----------------------------------------------------------------

    def status(self) -> Optional[dict]:
        """The story running now, as the page's frame says it, or None."""
        t = self.telling
        return None if t is None else self._frame(t)

    def state_of(self, reference: str = "") -> Told:
        """The story running now (any package's), else the one last ended."""
        t = self.telling
        if t is not None:
            return Told(t.package, t.story, t.slide)
        if self.ended is not None:
            e = self.ended
            return Told(e.package, e.story, None, ended=e)
        return Told(None, problem="No story has been told since JARVIS started.")

    @staticmethod
    def _frame(t: Telling) -> dict:
        frame = {"type": "package_story", "package": t.package.id, "state": t.state,
                 "slide": t.slide, "slides": len(t.story.slides),
                 "storyRevision": t.story.source_revision,
                 "completedThroughBeat": t.completed_through_beat}
        if t.question:
            frame["question"] = t.question
        return frame


def read_answers(package: Package, story: Story) -> dict:
    """The story's answers file as it is now: {question: entry}, {} when
    there is none or it is not a JSON object."""
    if not story.answers:
        return {}
    name = story.answers.split("/", 1)[1]
    try:
        raw = work_packages.read_result(package, name)
        body = json.loads(raw) if raw else {}
    except (PackageError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def stop_words(text: str) -> bool:
    """Is the whole utterance stop words (the active language's
    `stop_words`), with an optional "jarvis" or "please"?"""
    words = spoken_words.folded_words(text, apostrophes=True)
    stops = spoken_words.vocabulary().stop_words
    if not words or not any(w in stops for w in words):
        return False
    return len(words) <= 4 and all(w in stops or w in ("jarvis", "please") for w in words)
