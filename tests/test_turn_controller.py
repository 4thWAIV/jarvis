"""The turn controller (turn_controller.py) on a fake clock: a turn is
committed once, whole, only after the hold with nothing newer, never by a
timer whose turn, revision or deadline has moved on; continuation just
before, at and after the deadline; an unfinished interim and a lagging
recogniser hold the commit, and a recogniser that never catches up gives the
words back as a draft; stale epochs and discards; speech before the reply's
audio takes the floor back; and a closed socket commits nothing."""

import pytest

from jarvis.turn_controller import TurnController


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
    def __init__(self, hold=1.5, leaky=False, frames=None, floor=None):
        self.clock = Clock(leaky)
        self.committed: list[tuple] = []
        self.stalled: list[tuple] = []
        self.resumed: list[int] = []
        self.frames = frames            # {client: (sent, decoded)}
        self.floor = floor or {}
        self.c = TurnController(
            self._commit, hold=hold,
            coverage=lambda client: (self.frames or {}).get(client),
            epoch_floor=lambda client: self.floor.get(client),
            on_stalled=lambda turn, text: self.stalled.append((turn, text)),
            on_resumed=self.resumed.append,
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


def test_lag_with_no_new_interims_never_sends_the_old_fragment_it_becomes_a_draft():
    frames = {"page-a": (40, 10)}
    h = Harness(frames=frames)
    h.c.final("transfer the", 1, client="page-a", epoch=1)
    h.clock.advance(1.5 + 3.1)
    assert h.committed == []
    assert [text for _, text in h.stalled] == ["transfer the"]
    assert h.c.state == "idle" and h.c.turn_id is None
    h.clock.advance(10)
    assert h.committed == []


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
