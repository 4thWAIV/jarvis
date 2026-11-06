"""The turn controller (turn_controller.py) on a fake clock: a turn is
committed once, whole, only after the hold with nothing newer, never by a
timer whose turn, revision or deadline has moved on; continuation just
before, at and after the deadline; an unfinished interim and a lagging
recogniser hold the commit, and a recogniser that never catches up gives the
words back as a draft; stale epochs and discards; speech before the reply's
audio takes the floor back; and a closed socket commits nothing."""

import pytest

from jarvis.turn_controller import INTERVAL_DEFAULTS, PAUSE_STAGES, TurnController, pause_schedule


class Clock:
    """A fake clock and timers: `advance` fires what is due, in order. A
    cancelled timer's callback still runs if `leaky` (as an asyncio
    callback already on its way would), so the controller's own checks are
    what is tested."""

    def __init__(self, leaky=False):
        self.now = 0.0
        self.timers: list[list] = []          # [due, callback, cancelled]
        self.leaky = leaky

    def call_later(self, delay, callback):
        clock = self

        class Handle:
            def __init__(self):
                self.entry = [clock.now + delay, callback, False]
                clock.timers.append(self.entry)

            def cancel(self):
                self.entry[2] = True
        return Handle()

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = sorted((t for t in self.timers if t[0] <= end), key=lambda t: t[0])
            if not due:
                break
            entry = due[0]
            self.timers.remove(entry)
            self.now = max(self.now, entry[0])
            if not entry[2] or self.leaky:
                entry[1]()
        self.now = end


class Harness:
    def __init__(self, hold=1.5, leaky=False, frames=None, floor=None, intervals=None):
        self.clock = Clock(leaky)
        self.intervals = intervals      # {stage: seconds after the stage before}, or a hold alone
        self.paused: list[tuple] = []   # (turn, stage, when)
        self.committed: list[tuple] = []
        self.stalled: list[tuple] = []
        self.resumed: list[int] = []
        self.frames = frames            # {client: (sent, decoded)}
        self.floor = floor or {}
        self.c = TurnController(
            self._commit, hold=hold,
            schedule=(lambda: pause_schedule(self.intervals)) if intervals else None,
            coverage=lambda client: (self.frames or {}).get(client),
            epoch_floor=lambda client: self.floor.get(client),
            on_stalled=lambda turn, text: self.stalled.append((turn, text)),
            on_resumed=self.resumed.append,
            on_pause=lambda turn, stage: self.paused.append((turn, stage, self.clock.now)),
            clock=lambda: self.clock.now, call_later=self.clock.call_later,
            spawn=self._spawn)

    async def _commit(self, text, heard, turn):
        self.committed.append((text, heard, turn))

    def _spawn(self, coro):
        # Run the commit coroutine to completion at once (it never awaits).
        try:
            coro.send(None)
        except StopIteration:
            pass

    def texts(self):
        return [t for t, _, _ in self.committed]


# ── the hold ─────────────────────────────────────────────────────────────

def test_finals_with_a_pause_between_are_one_turn_committed_once():
    h = Harness()
    h.c.final("send a message to agent guard", 1)
    h.clock.advance(1.0)
    h.c.final("asking for its status", 2)
    h.clock.advance(1.4)
    assert h.committed == [], "nothing goes while he may still be talking"
    h.clock.advance(0.2)
    assert h.committed == [("send a message to agent guard asking for its status", 2, h.committed[0][2])]
    h.clock.advance(10)
    assert len(h.committed) == 1


@pytest.mark.parametrize("before", [0.001, 0.0])
def test_continuation_just_before_or_at_the_deadline_keeps_the_turn(before):
    """The timer for the old deadline fires after the new word arrived at
    (or just before) it: it must not commit, even if it was not cancelled."""
    h = Harness(leaky=True)
    h.c.final("turn off", 1)
    h.clock.now = 1.5 - before                   # the old timer's callback is not run yet
    h.c.final("the lights in the hall", 2)
    h.clock.advance(before + 0.01)
    assert h.committed == []
    h.clock.advance(1.5)
    assert h.texts() == ["turn off the lights in the hall"]


def test_continuation_after_the_deadline_is_the_next_turn():
    h = Harness()
    h.c.final("what time is it", 1)
    h.clock.advance(1.6)
    h.c.final("and the weather", 2)
    h.clock.advance(1.6)
    assert h.texts() == ["what time is it", "and the weather"]
    assert h.committed[0][2] != h.committed[1][2]


def test_an_interim_holds_the_turn_open_whether_or_not_a_final_is_gathered():
    h = Harness(leaky=True)
    h.c.interim("so what")                       # nothing gathered yet: nothing to commit
    h.clock.advance(5)
    assert h.committed == []
    h.c.final("so what I want", 1)
    for _ in range(4):
        h.clock.advance(1.0)
        h.c.interim("is")                        # he is still going
    assert h.committed == []
    h.c.final("is a report", 2)
    h.clock.advance(1.6)
    assert h.texts() == ["so what I want is a report"]


def test_an_unfinished_interim_keeps_older_finals_from_being_committed():
    h = Harness()
    h.c.final("delete the", 1)
    h.c.interim("staging")
    h.clock.advance(1.6)
    assert h.committed == [] and h.c.state == "commit-pending"
    h.c.final("staging branch", 2)               # its final arrives late
    h.clock.advance(1.6)
    assert h.texts() == ["delete the staging branch"]


def test_a_stale_timer_cannot_commit_after_a_drop():
    h = Harness(leaky=True)
    h.c.final("wipe it", 1)
    h.clock.advance(1.0)
    h.c.discard("page-a", 3)
    h.clock.advance(5)
    assert h.committed == []


# ── coverage ─────────────────────────────────────────────────────────────

def test_a_lagging_recogniser_holds_the_commit_until_it_has_caught_up():
    frames = {"page-a": (40, 30)}
    h = Harness(frames=frames)
    h.c.final("open the", 1, client="page-a", epoch=1)
    h.clock.advance(2.0)
    assert h.committed == [] and h.c.state == "commit-pending"
    frames["page-a"] = (55, 39)                  # still short of the 40 sent at the hold
    h.clock.advance(0.5)
    assert h.committed == []
    frames["page-a"] = (60, 40)
    h.clock.advance(0.2)
    assert h.texts() == ["open the"]


def _stalled():
    frames = {"page-a": (40, 10)}
    h = Harness(frames=frames)
    h.c.final("transfer the", 1, client="page-a", epoch=1)
    h.clock.advance(1.5 + 3.1)
    return h, frames


def test_lag_with_no_new_interims_never_sends_the_old_fragment_it_stalls():
    h, _frames = _stalled()
    assert h.committed == []
    assert [text for _, text in h.stalled] == ["transfer the"]
    assert h.c.state == "stalled" and h.c.gathered_text == "transfer the"
    h.clock.advance(60)
    assert h.committed == [] and h.c.state == "stalled", "it waits for his choice"


def test_a_stalled_turn_kept_as_a_draft_sends_nothing():
    h, _frames = _stalled()
    assert h.c.keep_as_draft() == "transfer the"
    assert h.c.state == "idle" and h.c.turn_id is None
    h.clock.advance(10)
    assert h.committed == []
    assert h.c.keep_as_draft() is None, "nothing stalled any more"


def test_send_now_on_a_stalled_turn_sends_it_as_heard_on_his_word():
    h, _frames = _stalled()
    assert h.c.send_now()
    assert h.texts() == ["transfer the"] and h.c.state == "awaiting-reply"


def test_retry_waits_for_the_recogniser_again_and_commits_once_it_catches_up():
    h, frames = _stalled()
    assert h.c.retry() and h.c.state == "commit-pending"
    h.clock.advance(1.0)
    assert h.committed == []
    frames["page-a"] = (40, 40)
    h.clock.advance(0.2)
    assert h.texts() == ["transfer the"]
    assert not h.c.retry(), "nothing stalled to retry"


def test_retry_that_still_cannot_catch_up_stalls_again():
    h, _frames = _stalled()
    h.c.retry()
    h.clock.advance(3.1)
    assert h.c.state == "stalled" and len(h.stalled) == 2 and h.committed == []


def test_more_speech_carries_a_stalled_turn_on():
    h, frames = _stalled()
    frames["page-a"] = (50, 50)
    h.c.final("money to savings", 2, client="page-a", epoch=1)
    assert h.c.state == "gathering"
    h.clock.advance(1.6)
    assert h.texts() == ["transfer the money to savings"]


def test_a_delayed_final_after_the_deadline_rejoins_the_turn_while_it_waits():
    frames = {"page-a": (40, 30)}
    h = Harness(frames=frames)
    h.c.final("schedule a", 1, client="page-a", epoch=1)
    h.clock.advance(1.8)                         # deadline passed, waiting for coverage
    turn = h.c.turn_id
    h.c.final("meeting for noon", 2, client="page-a", epoch=1)
    assert h.c.turn_id == turn and h.c.state == "gathering"
    frames["page-a"] = (60, 60)
    h.clock.advance(1.6)
    assert h.texts() == ["schedule a meeting for noon"]


def test_no_capture_means_nothing_to_wait_for():
    h = Harness(frames={})
    h.c.final("hello", 1, client="page-a", epoch=1)
    h.clock.advance(1.6)
    assert h.texts() == ["hello"]


def test_send_now_commits_at_once_but_not_before_the_recogniser_has_caught_up():
    frames = {"page-a": (40, 30)}
    h = Harness(frames=frames)
    assert h.c.send_now() is False
    h.c.final("ship it", 1, client="page-a", epoch=1)
    assert h.c.send_now() is False
    frames["page-a"] = (40, 40)
    assert h.c.send_now() is True
    assert h.texts() == ["ship it"]
    h.clock.advance(5)
    assert len(h.committed) == 1


# ── epochs and discards ──────────────────────────────────────────────────

def test_words_from_an_older_epoch_are_dropped_never_gathered():
    h = Harness(floor={"page-a": 4})
    assert h.c.final("old words", 1, client="page-a", epoch=3) is False
    assert h.c.interim("old", client="page-a", epoch=3) is False
    assert h.c.final("new words", 2, client="page-a", epoch=4) is True
    h.clock.advance(1.6)
    assert h.texts() == ["new words"]


def test_a_discard_drops_the_turn_and_refuses_late_words_from_before_it():
    h = Harness()
    h.c.final("drop the database", 1, client="page-a", epoch=2)
    assert h.c.discard("page-a", 3) == "drop the database"
    assert h.c.final("database", 2, client="page-a", epoch=2) is False
    h.clock.advance(5)
    assert h.committed == []
    assert h.c.final("never mind", 3, client="page-a", epoch=3) is True
    h.clock.advance(1.6)
    assert h.texts() == ["never mind"]


def test_another_page_s_discard_does_not_touch_this_page_s_words():
    h = Harness()
    h.c.discard("page-b", 9)
    assert h.c.final("mine", 1, client="page-a", epoch=1) is True


# ── the floor, typing and the socket ─────────────────────────────────────

def test_speech_after_the_commit_before_the_reply_is_heard_takes_the_floor_back():
    h = Harness()
    h.c.final("book a table", 1)
    h.clock.advance(1.6)
    committed = h.committed[0][2]
    assert h.c.state == "awaiting-reply"
    h.c.interim("for four")
    assert h.resumed == [committed]
    h.c.final("for four people", 2)
    h.clock.advance(1.6)
    assert h.texts() == ["book a table", "for four people"]
    assert h.resumed == [committed]


def test_speech_once_the_reply_is_playing_is_not_taking_the_floor_back():
    h = Harness()
    h.c.final("tell me a story", 1)
    h.clock.advance(1.6)
    h.c.reply_started(h.committed[0][2])
    h.c.interim("stop")
    assert h.resumed == []


def test_a_closed_socket_commits_nothing_and_gives_the_words_back():
    h = Harness(leaky=True)
    h.c.final("half a", 1)
    assert h.c.close() == "half a"
    h.clock.advance(5)
    assert h.committed == []
    assert h.c.close() is None


# ── the pause cues ───────────────────────────────────────────────────────
# Four intervals, each after the stage before: with the defaults (1.0, 0.75,
# 0.75, 0.5) the cues after 1.0, 1.75 and 2.5 s of quiet and the send at 3.0.

CUES = ["tentative", "deciding", "likely"]
DEFAULT_TIMES = [("tentative", 1.0), ("deciding", 1.75), ("likely", 2.5)]
CUSTOM = {"tentative": 0.25, "deciding": 2.0, "likely": 0.5, "send": 1.25}
CUSTOM_TIMES = [("tentative", 0.25), ("deciding", 2.25), ("likely", 2.75)]


def _stages(h):
    return [stage for _, stage, _ in h.paused]


def _times(h):
    return [(stage, pytest.approx(at)) for _, stage, at in h.paused]


def test_the_schedule_is_the_running_sum_of_the_intervals_in_stage_order():
    assert PAUSE_STAGES == ("tentative", "deciding", "likely", "send")
    assert pause_schedule(INTERVAL_DEFAULTS) == [("tentative", 1.0), ("deciding", 1.75),
                                                 ("likely", 2.5), ("send", 3.0)]
    assert pause_schedule(CUSTOM) == CUSTOM_TIMES + [("send", 4.0)]


@pytest.mark.parametrize("intervals,times,send", [(INTERVAL_DEFAULTS, DEFAULT_TIMES, 3.0),
                                                  (CUSTOM, CUSTOM_TIMES, 4.0)])
def test_each_cue_comes_once_at_its_running_time_in_order_then_the_send(intervals, times, send):
    h = Harness(intervals=intervals)
    h.c.final("book a table", 1)
    for n, (stage, at) in enumerate(times):
        h.clock.advance(at - h.clock.now - 0.005)
        assert len(h.paused) == n, f"nothing before the {stage} cue's time"
        h.clock.advance(0.005)
        assert h.paused[-1] == (h.c.turn_id, stage, pytest.approx(at))
        assert h.c.pause_stage == stage and h.c.state == "gathering"
    assert h.c.hold == send
    h.clock.advance(send - h.clock.now - 0.005)
    assert h.committed == [], "every cue comes before the send"
    h.clock.advance(0.005)
    assert h.texts() == ["book a table"]
    h.clock.advance(10)
    assert _times(h) == [(stage, at) for stage, at in times], "at most once per stage per pause"


@pytest.mark.parametrize("leaky", [False, True])
@pytest.mark.parametrize("speak_at,given", [
    (1.0, []), (1.4, ["tentative"]), (1.75, ["tentative"]), (2.1, ["tentative", "deciding"]),
    (2.5, ["tentative", "deciding"]), (2.8, CUES), (3.0, CUES)])
def test_speech_at_or_between_the_boundaries_starts_again_from_the_first_interval(
        speak_at, given, leaky):
    """At each boundary (his word landing the moment a stage falls due,
    before its callback runs) and between them, accepted speech cancels
    the cues still pending and the send, the revision moves on, and a timer
    that fires anyway (leaky) gives nothing. The next pause has every cue
    again, timed from the new start."""
    h = Harness(intervals=INTERVAL_DEFAULTS, leaky=leaky)
    h.c.final("book a table", 1)
    h.clock.advance(speak_at - 0.001)
    h.clock.now = speak_at                       # the stage due now has not run yet
    h.c.interim("for")
    before = list(h.paused)
    assert _stages(h) == given
    h.clock.advance(0.9)                         # the old times pass; the new first is at 1.0
    assert h.paused == before, "a cue from before he spoke again"
    assert h.committed == [], "an interim heard as speech holds the turn"
    h.c.final("for two", 2)
    restart = h.clock.now
    h.clock.advance(3.01)
    assert [(stage, pytest.approx(at - restart)) for _, stage, at in h.paused[len(before):]] == \
        DEFAULT_TIMES
    assert h.texts() == ["book a table for two"]


def test_continuous_talking_never_sends_and_never_gives_a_cue():
    """While he keeps talking (speech heard closer together than the first
    interval), nothing is sent and no cue plays, for as long as he talks,
    with or without a final gathered."""
    h = Harness(intervals=INTERVAL_DEFAULTS)
    for word in range(3):                        # before any final: no turn yet
        h.c.interim("so " * (word + 1))
        h.clock.advance(0.9)
    h.c.final("so the plan", 1)
    for word in range(200):                      # three minutes of talking after it
        h.clock.advance(0.9)
        h.c.interim("is " * (word + 1))
    assert h.committed == [] and h.paused == []
    h.c.final("is long", 2)
    h.clock.advance(3.0)
    assert _stages(h) == CUES and h.texts() == ["so the plan is long"]


def test_a_cue_whose_revision_moved_on_is_never_given_late():
    h = Harness(intervals=INTERVAL_DEFAULTS, leaky=True)
    h.c.final("one", 1)
    h.clock.advance(0.5)
    h.c.final("two", 2)                          # the old cues' token is stale now
    h.clock.advance(0.6)                         # past the first cue's old time (1.0)
    assert h.paused == []
    h.clock.advance(0.4)
    assert _times(h) == [("tentative", 1.5)]
    h.clock.advance(0.749)                       # past the deciding cue's old time (1.75)
    assert _stages(h) == ["tentative"]
    h.clock.advance(0.002)
    assert _times(h) == [("tentative", 1.5), ("deciding", 2.25)]


@pytest.mark.parametrize("end", ["discard", "close", "send_now"])
def test_no_cue_once_the_turn_is_dropped_closed_or_sent(end):
    h = Harness(intervals=INTERVAL_DEFAULTS, leaky=True)
    h.c.final("hello", 1)
    getattr(h.c, end)()
    h.clock.advance(10)
    assert h.paused == []


def test_no_cue_while_idle_or_only_an_interim_is_heard():
    h = Harness(intervals=INTERVAL_DEFAULTS)
    h.clock.advance(10)
    h.c.interim("so")
    h.clock.advance(10)
    assert h.paused == []


def test_no_cue_while_the_commit_waits_for_the_recogniser_or_after_a_stall():
    h = Harness(intervals=INTERVAL_DEFAULTS, frames={"page-a": (40, 10)})
    h.c.final("transfer the", 1, client="page-a")
    h.clock.advance(2.9)
    assert _stages(h) == CUES
    h.clock.advance(10)                          # commit-pending, then stalled
    assert h.c.state == "stalled" and _stages(h) == CUES
    h.c.retry()
    h.clock.advance(10)
    assert _stages(h) == CUES


def test_the_intervals_are_read_again_each_time_the_hold_starts():
    """A change applies from the next pause: the hold (his patience) and
    every cue together, so no cue is timed against another pause's send."""
    h = Harness(intervals=INTERVAL_DEFAULTS)
    h.c.final("one", 1)
    h.intervals = CUSTOM
    h.clock.advance(3.0)
    assert _times(h) == DEFAULT_TIMES and h.texts() == ["one"]
    h.c.final("two", 2)
    h.clock.advance(4.0)
    assert _times(h)[3:] == [(stage, 3.0 + at) for stage, at in CUSTOM_TIMES]
    assert h.texts() == ["one", "two"] and h.c.hold == 4.0


def test_without_a_schedule_the_hold_alone_is_timed_and_no_cue_is_given():
    h = Harness(hold=1.5)
    h.c.final("hi", 1)
    h.clock.advance(1.5)
    assert h.texts() == ["hi"] and h.paused == []
