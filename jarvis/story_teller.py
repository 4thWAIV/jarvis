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
no story. `resume` goes on from where a running story is paused (that beat said again)
or waiting, else from the first question with no answer.

Ask and Return (package_place.py). Ask keeps exactly where he is (the slide
and the beat being heard, read from the scheduler's own outcomes, what the
story was doing, and what the page reported its frame shows: the deck slide,
scrolling, the part he selected) and pauses the words if they were sounding;
his speaking over a slide keeps its place the same way. Places nest: each
Ask is kept on top of the one before, at most MAX_ASKS. Return takes the
last place back: the page is opened if it was closed, its frame brings that
view up again (`package_return`: what the story had revealed on its slide,
the slide shown, its scrolling, the part and its keyboard focus), and only
then, if the words were sounding when he asked, that beat is told again
from its beginning; a story that was paused, waiting or stopped stays so. A
place whose package is no longer the revision it was asked about (replaced,
gone, or changed) is refused and set aside, never restored. Stop keeps its
place too, as an Ask does: Return goes back to it, and says the beat again
when its words were sounding.

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
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, Optional

from jarvis import language_setting, package_story, spoken_words, work_packages
from jarvis.package_place import (MAX_ASKS, Place, View, ViewError, revealed, revision_of,
                                  view_from)
from jarvis.package_story import Slide, Story
from jarvis.speech import Outcome
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
ASK, RETURN, PLACE = "ask", "return", "place"
ACTIONS = (TELL, RESUME, STOP, STATUS, ASK, RETURN, PLACE)
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
    told: list = field(default_factory=list)     # slides read him since the brain was told
    cut: list = field(default_factory=list)      # slides begun but not heard in full since
    under_way: Optional[int] = None              # the slide sounding now, not yet settled
    beat: int = 1                                # the beat of `slide` heard, or to go on from
    first_beat: int = 1                          # the beat the first slide is told from
    kept: bool = False                           # told from a kept place (resume, return)
    wait_first: bool = False                     # that place waited: wait again, say nothing
    asking: bool = False                         # paused by his Ask, not by his speaking


@dataclass(frozen=True)
class Ended:
    """The story last ended: how, where, by whom and when."""
    package: Package
    story: Story
    slide: int
    state: str
    by: str
    at: float
    place: Optional[Place] = None                # where it was: Return goes back here


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
    place: Optional[Place] = None                # an ask's, a return's or the place now
    asks: int = 0                                # places kept to return to, after it


class StoryTeller:
    """`speech()` is the speech scheduler (None until it is up);
    `open_page(reference)` opens a page package as Open page does: (the
    package, "") or (None, why not); `announce(frame)` sends a
    `package_story` frame to every page; `floor_free()` says nobody is
    speaking or about to (his turn, the brain's reply); `note(text)` tells
    the brain at its next turn; `spoken(what, which, how)` tells it which
    slides were read him word for word and where the story stands;
    `replaced(package_id)` says why a package is no longer the revision in
    use (a newer one replaced it), or ""."""

    def __init__(self, speech: Callable[[], object],
                 open_page: Callable[[str], Awaitable[tuple[Optional[Package], str]]],
                 announce: Callable[[dict], Awaitable[None]],
                 floor_free: Callable[[], bool] = lambda: True,
                 note: Callable[[str], None] = lambda _text: None,
                 clock: Callable[[], float] = time.monotonic,
                 spoken: Callable[[str, str, str], None] = lambda *_: None,
                 replaced: Callable[[str], str] = lambda _id: ""):
        self._speech = speech
        self._open_page = open_page
        self._announce = announce
        self._floor_free = floor_free
        self._note = note
        self._spoken = spoken
        self._replaced = replaced
        self._clock = clock
        self._lock = asyncio.Lock()
        self.telling: Optional[Telling] = None
        self.ended: Optional[Ended] = None
        # What each package's frame last reported it shows, and the places
        # he asked from, the last on top.
        self.views: dict[str, View] = {}
        self.asks: list[Place] = []

    async def act(self, action: str, reference: str, slide: Optional[int] = None,
                  by: str = "on the page", view=None) -> Told:
        """The page's buttons and the brain's tool: tell (from `slide`, or
        the first), resume (from where it is paused or waiting, else the
        first question with no answer), stop, status, ask (keep the place, with `view` the
        page reported), return (to the last place kept) or place (where he
        is now, kept nowhere)."""
        if action == STOP:
            return await self.stop(by)
        if action == STATUS:
            return self.state_of(reference)
        if action == RESUME:
            return await self.tell(reference, None)
        if action == ASK:
            return await self.ask(reference, view)
        if action == RETURN:
            return await self.return_()
        if action == PLACE:
            return await self.place(reference)
        if action != TELL:
            return Told(None, problem=f"{action!r} is not one of {', '.join(ACTIONS)}.")
        return await self.tell(reference, slide or 1)

    # -- telling --------------------------------------------------------------------------

    async def tell(self, reference: str, slide: Optional[int] = 1) -> Told:
        """Tell from `slide`, or with None from where it left off: where it
        is paused (that beat told again) or waiting (that question waited on
        again), else the first question with no answer (slide 1 when none
        has one yet)."""
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
            kept = self._kept(package.id) if slide is None else None
            if slide is None and kept is None:
                slide = await asyncio.to_thread(self._resume_slide, package, story)
            told = await self._start(speech, package, story, slide, kept)
            if slide is None and told.package is not None:
                # He goes on with it: the places he asked from in it are
                # behind him.
                self.asks = [p for p in self.asks if p.package != package.id]
                await self._announce_asks()
            return told

    async def _start(self, speech, package: Package, story: Story, slide: Optional[int],
                     kept: Optional[Place] = None) -> Told:
        """Tell `story` from `slide` (told from there on purpose), or from a
        kept place: its beat told again, or its question waited on again.
        The story told now is stopped first. Under the lock."""
        if kept is not None:
            slide = kept.slide or 1
        if not 1 <= slide <= len(story.slides):
            return Told(None, problem=f"Its story has slides 1 to {len(story.slides)}.")
        language = spoken_words.active_language()
        if story.language != language:
            names = language_setting.NAMES
            return Told(None, problem=(
                f"Its story is told in {names[story.language]}, and I am speaking "
                f"{names[language]}; switch the language first."))
        await self._stop_locked("to be told again")
        telling = Telling(package, story, slide, slide, explicit=kept is None)
        if kept is not None:
            telling.kept = True
            telling.first_beat = telling.beat = max(1, min(kept.beat or 1,
                                                           len(story.slides[slide - 1].beats)))
            telling.wait_first = kept.state == WAITING and not kept.speaking
        self.telling = telling
        telling.task = asyncio.create_task(self._tell(speech, telling))
        log.info("story of %s told from slide %d, beat %d", package.id, slide, telling.beat)
        return Told(package, story, slide)

    def _kept(self, package_id: str) -> Optional[Place]:
        """Where its story, paused or waiting now, goes on from; None when it
        is not running (a stopped story resumes at the first question with
        no answer; the place its Stop kept is Return's)."""
        t = self.telling
        if t is None or t.package.id != package_id or t.state not in (PAUSED, WAITING):
            return None
        return Place(package_id, "", t.state, t.slide, t.beat, False, t.question)

    # -- ask and return -------------------------------------------------------------------

    def seen(self, package_id: str, view: View) -> None:
        """What the package's frame reports it shows now (the page sends it
        as it changes); kept for a few packages at most."""
        self.views.pop(package_id, None)
        self.views[package_id] = view
        while len(self.views) > MAX_ASKS:
            self.views.pop(next(iter(self.views)))

    def _place_now(self, package: Package) -> Place:
        """Exactly where he is in `package`: the story's slide and beat and
        what it is doing (a Stop's kept place when it is not told), and
        what its frame last reported it shows."""
        view = self.views.get(package.id, View())
        revision = revision_of(package)
        t = self.telling
        if t is not None and t.package.id == package.id:
            speaking = t.state == TELLING and bool(t.utterances)
            beat = _cut_beat(t.utterances[0], t.beat) if speaking else t.beat
            return Place(package.id, revision, t.state, t.slide, beat, speaking, t.question,
                         view, self._clock())
        e = self.ended
        if e is not None and e.package.id == package.id and e.place is not None:
            p = e.place
            return Place(package.id, revision, e.state, p.slide, p.beat, False, p.question,
                         view, self._clock())
        return Place(package.id, revision, "", view=view, at=self._clock())

    async def ask(self, reference: str, view=None) -> Told:
        """His Ask: the place he is at kept on top of any before it, and the
        story's words, if they were sounding, paused at that beat. `view`
        is what the page reports its frame shows (`package_place.view_from`)."""
        async with self._lock:
            try:
                package = await asyncio.to_thread(work_packages.find, reference,
                                                  work_packages.PAGE)
                if view is not None:
                    self.seen(package.id, view_from(view))
            except (PackageError, ViewError) as e:
                return Told(None, problem=str(e))
            place = self._place_now(package)
            await self._keep(place)
            t = self.telling
            if place.speaking and t is not None:
                t.asking = True
                t.beat = place.beat or t.beat
                for utt in list(t.utterances):
                    await self._speech().withdraw(utt, reason="he asked")
            log.info("asked from %s at slide %s, beat %s (%s)", package.id, place.slide,
                     place.beat, place.state or "no story")
            story = t.story if t is not None and t.package.id == package.id else None
            return Told(package, story, place.slide, place=place, asks=len(self.asks))

    async def _keep(self, place: Place) -> None:
        self.asks.append(place)
        del self.asks[:-MAX_ASKS]
        await self._announce_asks()

    async def _announce_asks(self) -> None:
        await self._announce({"type": "package_asks", "asks": [p.frame() for p in self.asks]})

    async def place(self, reference: str = "") -> Told:
        """Where he is now (`reference`'s package, else the one he last asked
        from, else the story told), kept nowhere."""
        if not reference:
            reference = (self.asks[-1].package if self.asks else
                         self.telling.package.id if self.telling is not None else "")
        if not reference:
            return Told(None, problem="No package is open to say where he is in.")
        try:
            package = await asyncio.to_thread(work_packages.find, reference, work_packages.PAGE)
        except PackageError as e:
            return Told(None, problem=str(e))
        place = self._place_now(package)
        story = await asyncio.to_thread(_story_of, package)
        return Told(package, story, place.slide, place=place, asks=len(self.asks))

    async def return_(self) -> Told:
        """His Return: the last place kept, taken back. Refused, and set
        aside, when its package is no longer the revision it was asked
        about. Its page is opened, the view restored, and only then, if its
        words were sounding when he asked, that beat told again from its
        beginning; otherwise the story stays as it is (paused, waiting or
        stopped, now at that place)."""
        async with self._lock:
            if not self.asks:
                return Told(None, problem="There is no place to go back to: nothing was "
                                          "asked from a package.")
            place = self.asks.pop()
            await self._announce_asks()
            stale = await asyncio.to_thread(self._stale, place)
            if stale:
                log.info("return to %s refused: %s", place.package, stale)
                return Told(None, problem=stale, place=place, asks=len(self.asks))
            package, problem = await self._open_page(place.package)
            if package is None:
                await self._keep(place)               # not stale: still there to go back to
                return Told(None, problem=problem, place=place, asks=len(self.asks))
            story = await asyncio.to_thread(_story_of, package)
            restart = bool(place.speaking and place.slide and story is not None)
            await self._announce({
                "type": "package_return", "package": package.id, "view": place.view.as_dict(),
                "revealed": revealed(story, place.slide, place.beat, through=not restart)})
            if restart:
                speech = self._speech()
                if speech is None:
                    return Told(None, problem="My voice isn't ready yet.", place=place)
                told = await self._start(speech, package, story, None, place)
                if told.package is None:
                    return Told(None, problem=told.problem, place=place, asks=len(self.asks))
            else:
                await self._settle_at(place)
            log.info("returned to %s at slide %s, beat %s (%s)", package.id, place.slide,
                     place.beat, "told again" if restart else "as it was")
            return Told(package, story, place.slide, place=place, asks=len(self.asks))

    def _stale(self, place: Place) -> str:
        """Why the place can no longer be restored, or "": its package gone,
        replaced by a newer revision, or not the revision it was asked
        about."""
        try:
            package = work_packages.find(place.package, work_packages.PAGE)
        except PackageError:
            return (f"{place.package} is no longer here, so the place he asked from cannot "
                    f"be restored; it is set aside.")
        replaced = self._replaced(package.id)
        if replaced:
            return (f"{package.id} was {replaced}, so the place he asked from is in a "
                    f"revision no longer in use and is not restored; it is set aside.")
        if revision_of(package) != place.revision:
            return (f"{package.id} is not the revision he asked from any more, so that place "
                    f"is not restored; it is set aside.")
        return ""

    async def _settle_at(self, place: Place) -> None:
        """A place returned to without telling: a paused story of its
        package stays paused, now at that slide and beat; a stopped one
        goes on from there when resumed."""
        t = self.telling
        if place.slide is None:
            return
        if t is not None and t.package.id == place.package and t.state == PAUSED:
            t.slide, t.beat = place.slide, place.beat or 1
            await self._announce(self._frame(t))
        elif t is None and self.ended is not None and self.ended.package.id == place.package \
                and self.ended.state == STOPPED:
            e = self.ended
            self.ended = Ended(e.package, e.story, place.slide, e.state, e.by, e.at, place)

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
                first = number == t.start
                asked_again = t.explicit and first
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
                beat = t.first_beat if first else 1
                t.beat = beat
                if first and t.wait_first and slide.waits:
                    heard = True                 # it waited there: it waits again, unsaid
                else:
                    utt = speech.narrate(t.story.cues(t.package.id, number)[beat - 1:])
                    t.utterances, t.under_way = [utt], number
                    await self._announce(self._frame(t))
                    heard = await speech.wait_for(utt, timeout=SLIDE_TIMEOUT_SEC)
                    t.utterances = []
                    if not heard:
                        t.beat = _cut_beat(utt, beat)
                    # Only a slide heard in full was read him word for word.
                    (t.told if heard else t.cut).append(number)
                    t.under_way = None
                if t.stopped:
                    break
                if slide.waits:
                    t.asking = False             # an Ask on a question waits for its answer
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
                    where = (f"slide {number} of {len(t.story.slides)}, at beat {t.beat} of "
                             f"{len(slide.beats)}")
                    back = ("tell_package_story with action place says what he is looking "
                            "at; action return goes back there and says that beat again; stop "
                            "ends it")
                    if t.asking:
                        t.asking = False
                        self._told(t, f"he paused {where} to ask you about what he is looking "
                                      f"at, so the story is paused there; {back}")
                    else:
                        # His speaking over it is asking too: its place is kept.
                        await self._keep(replace(self._place_now(t.package), state=TELLING,
                                                 speaking=True))
                        self._told(t, f"he spoke over {where}, so the story is paused there "
                                      f"and its place is kept; {back}")
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
                # A stop announces its own end and tells the brain (_stop_locked).
                t.state = state
                await self._announce(self._frame(t))
                self._told(t, "the story was told to its end" if state == FINISHED else
                           f"the story could not go on and ended at slide {t.slide} of "
                           f"{len(t.story.slides)}")

    async def _wait_for_answer(self, t: Telling, slide: Slide, before) -> None:
        """WAITING until `slide`'s question has an answer it did not have
        when the slide began, or the story is stopped."""
        t.state, t.question = WAITING, slide.waits
        await self._announce(self._frame(t))
        # The question's id is the sender's words: it reaches the brain only
        # walled, through tell_package_story's status.
        self._told(t, f"the story is waiting for his answer on slide {t.slide} of "
                      f"{len(t.story.slides)}; it goes on by itself once that is recorded "
                      f"(tell_package_story status gives the question)")
        log.info("story of %s waits for %s", t.package.id, slide.waits)
        while not t.stopped:
            record = await asyncio.to_thread(read_answers, t.package, t.story)
            now = record.get(slide.waits)
            if isinstance(now, dict) and now != before:
                log.info("story of %s: %s answered", t.package.id, slide.waits)
                t.question = None
                return
            await asyncio.sleep(ANSWER_POLL_SEC)

    def _told(self, t: Telling, how: str) -> None:
        """Tell the brain which slides were read him since it was last told,
        and where the story stands. Their titles are the sender's words:
        tell_package_story's status gives them, walled."""
        if t.under_way is not None:          # cut off as it ended
            t.cut.append(t.under_way)
            t.under_way = None
        told, t.told = t.told, []
        cut, t.cut = [c for c in t.cut if c not in told], []
        if cut:
            how += f"; {_slides(cut)} began but {'was' if len(set(cut)) == 1 else 'were'} " \
                   f"cut short, not heard in full"
        self._spoken(f"{_slides(told)} of its story" if told else "nothing more of its story",
                     f"package {t.package.id}", how)

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
            if telling is not None and self.ended is not None and self.ended.place is not None:
                # Stop keeps the place: Return goes back to it, and says
                # the beat again if its words were sounding.
                await self._keep(self.ended.place)
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
        # Stop keeps the place: the beat sounding (or to go on from), what
        # the story was doing, and whether its words were sounding.
        beat = _cut_beat(telling.utterances[0], telling.beat) if telling.utterances \
            else telling.beat
        kept = Place(telling.package.id, revision_of(telling.package), telling.state,
                     telling.slide, beat, telling.state == TELLING and bool(telling.utterances),
                     telling.question,
                     self.views.get(telling.package.id, View()), self._clock())
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
                           self._clock(), kept)
        await self._announce(self._frame(telling))
        self._told(telling, f"it was stopped {by}, at slide {telling.slide} of "
                            f"{len(telling.story.slides)}")
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
                 "slide": t.slide, "slides": len(t.story.slides), "beat": t.beat}
        if t.question:
            frame["question"] = t.question
        return frame


def _cut_beat(utt, default: int) -> int:
    """The beat a slide's words were at when they stopped: the first whose
    words were not all played (the scheduler's own outcomes), else
    `default`."""
    for chunk in utt.chunks:
        if chunk.outcome != Outcome.PLAYED and chunk.cue:
            try:
                return int(str(chunk.cue.get("beat", "")).rsplit(".", 1)[1])
            except (IndexError, ValueError):
                return default
    return default


def _story_of(package: Package) -> Optional[Story]:
    try:
        return package.story()
    except PackageError:
        return None


def _slides(numbers: list) -> str:
    """Slide numbers as words: "slide 2", "slides 1 to 3", "slides 1, 3 and 4"."""
    numbers = sorted(set(numbers))
    if len(numbers) == 1:
        return f"slide {numbers[0]}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"slides {numbers[0]} to {numbers[-1]}"
    return "slides " + ", ".join(map(str, numbers[:-1])) + f" and {numbers[-1]}"


def read_answers(package: Package, story: Story) -> dict:
    """The story's answers file as it is now: {question: entry}, {} when
    there is none or it is not a JSON object. An answer given before to
    the retired vote (Got it) stands for the standard vote its slide is
    now (package_story.current), so that slide is not asked again."""
    if not story.answers:
        return {}
    name = story.answers.split("/", 1)[1]
    try:
        raw = work_packages.read_result(package, name)
        body = json.loads(raw) if raw else {}
    except (PackageError, ValueError):
        return {}
    if not isinstance(body, dict):
        return {}
    if isinstance(body.get(package_story.LEGACY_VOTE_QUESTION), dict) and \
            package_story.VOTE_QUESTION not in body:
        body[package_story.VOTE_QUESTION] = body[package_story.LEGACY_VOTE_QUESTION]
    return body


def stop_words(text: str) -> bool:
    """Is the whole utterance stop words (the active language's
    `stop_words`), with an optional "jarvis" or "please"?"""
    words = spoken_words.folded_words(text, apostrophes=True)
    stops = spoken_words.vocabulary().stop_words
    if not words or not any(w in stops for w in words):
        return False
    return len(words) <= 4 and all(w in stops or w in ("jarvis", "please") for w in words)
