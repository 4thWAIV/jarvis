"""
turn_controller.py — what he says, gathered into one turn until he has
finished, and committed to the brain only when it is safe to (the voice
control contract, section 7). One per voice client; it replaces the old
`server._Utterance`.

A turn has an id (`turn_id`) and a revision (`revision`) that every accepted
word moves on. Each final starts the hold again; so does every interim
heard as speech, whether or not a final is gathered yet, and an interim not
yet followed by its final keeps older finals from being committed. When the
hold's deadline comes, the commit is checked against the turn, the revision
and the deadline it was scheduled for, every one of which must still match:
cancelling a timer is never relied on, because its callback may already be
on its way. The recogniser must also have caught up with him: it must have
decoded every frame the page had sent when the hold began (coverage, read
from the transcriber's capture for the page, so the page's own word is not
trusted for it). Not yet: it waits, up to `coverage_bound`, then the turn
is `stalled`: kept, never an old fragment sent on its own, and the page
offers his three ways on (`retry` waits for the recogniser again,
`send_now` sends what was heard as it is, on his word, and
`keep_as_draft` gives it back as an unsent draft). Anything more he says
carries the stalled turn on.

Words carry the page (`clientId`) and the capture epoch they were heard in
(`captureEpoch`). Words from an epoch older than the page's current one
(the latest capture the transcriber admitted for it, or the epoch a
discard moved it to) are dropped and logged, never gathered.

After a commit, speech before the reply's audio has started takes the
floor back (`on_resumed`: the server stops that reply's reading before it
is heard, keeping its text and anything it has done) and starts the next
turn. Typed words are not gathered and never merged with the voice words
being gathered, which go on as their own turn. A discard (his stop under
Discard) drops the turn; a closed voice socket commits nothing and gives
back what was gathered.

While he pauses in a gathered turn, pause cues may be due before the
commit (`pause_cues`: each stage with its seconds before the deadline,
read again each time the hold starts; voice_control.PAUSE_CUES):
`tentative`, when JARVIS thinks he may be done, `deciding`, while it is
still deciding, and `likely`, at the threshold. Each is timed from the same
deadline as the commit and checked, when due, against the turn, the
revision and the deadline it was scheduled for, and that the turn is still
being gathered; any accepted speech moves the revision on and starts the
pause again, so a cue is given at most once per stage per pause and never
late. A stage whose time falls at or before the moment he stopped
(seconds before >= the hold) is not scheduled. `on_pause` is told the
turn and the stage; whether a sound plays is the caller's to decide.

Every time-dependent step takes its clock and timers from the caller, so
tests drive it with a fake clock.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Sequence

log = logging.getLogger("jarvis.turns")

# How often a commit waiting for the recogniser looks again, and how long
# past the deadline it waits before giving the turn back as a draft. The
# recogniser refuses audio more than two seconds behind (overload), so
# three is enough for anything still on its way.
COVERAGE_POLL_SEC = 0.1
COVERAGE_BOUND_SEC = 3.0
# A loop may run a timer up to its clock's resolution early: a pause cue
# that early is still on time.
CUE_SLACK_SEC = 0.01

Commit = Callable[[str, Optional[int], int], Awaitable[None]]
# (frames the page had sent, frames decoded) for the page's capture, or
# None when it has none open.
Coverage = Callable[[str], Optional[tuple[int, int]]]
CallLater = Callable[[float, Callable[[], None]], object]
# The pause cues to time: each stage with its seconds before the deadline.
PauseCues = Callable[[], Sequence[tuple[str, float]]]


@dataclass
class _Turn:
    id: int
    parts: list[str] = field(default_factory=list)
    heard: Optional[int] = None
    client: Optional[str] = None


class TurnController:
    """One voice client's turns. States: idle, gathering, commit-pending,
    stalled (the recogniser did not catch up; waiting for his choice),
    awaiting-reply (committed; until the server says its reply has started
    or ended)."""

    _ids = itertools.count(1)

    def __init__(self, commit: Commit, *, hold: float,
                 coverage: Coverage = lambda _client: None,
                 epoch_floor: Callable[[str], Optional[int]] = lambda _client: None,
                 on_stalled: Callable[[int, str], None] = lambda _turn, _text: None,
                 on_resumed: Callable[[int], None] = lambda _turn: None,
                 pause_cues: PauseCues = lambda: (),
                 on_pause: Callable[[int, str], None] = lambda _turn, _stage: None,
                 clock: Optional[Callable[[], float]] = None,
                 call_later: Optional[CallLater] = None,
                 spawn: Optional[Callable[[Awaitable[None]], object]] = None,
                 coverage_bound: float = COVERAGE_BOUND_SEC,
                 coverage_poll: float = COVERAGE_POLL_SEC) -> None:
        self._commit = commit
        self.hold = hold
        self._coverage = coverage
        self._epoch_floor = epoch_floor
        self._on_stalled = on_stalled
        self._on_resumed = on_resumed
        self._pause_cues = pause_cues
        self._on_pause = on_pause
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._call_later = call_later or (lambda d, f: asyncio.get_running_loop().call_later(d, f))
        self._spawn = spawn or asyncio.ensure_future
        self.coverage_bound = coverage_bound
        self.coverage_poll = coverage_poll
        self.state = "idle"
        self.revision = 0
        self._turn: Optional[_Turn] = None
        self._deadline: Optional[float] = None
        self._need: Optional[int] = None       # frames sent when the hold began
        self._open_interim = False             # speech heard and not yet finalised
        self._timer: object = None
        self._cue_timers: list[object] = []    # this pause's cues, not yet due
        self.pause_stage: Optional[str] = None # the last pause cue given in this pause
        self._discarded: dict[str, int] = {}   # page -> the epoch a discard moved it to
        self.committed: Optional[int] = None   # the turn awaiting its reply

    # ── the identities ────────────────────────────────────────────────────

    @property
    def turn_id(self) -> Optional[int]:
        return self._turn.id if self._turn else None

    @property
    def gathered_text(self) -> str:
        """What has been gathered into the turn so far: only words accepted
        as his (never echo, never a stale epoch's)."""
        return " ".join(self._turn.parts) if self._turn else ""

    def current(self, client: Optional[str], epoch: Optional[int]) -> bool:
        """Whether words from this page and epoch may be gathered (rule 1).
        Words that carry no epoch (typed, or an older page) are not judged
        by it."""
        if client is None or epoch is None:
            return True
        floor = max(self._discarded.get(client, -1), self._epoch_floor(client) or -1)
        if epoch < floor:
            log.info("turn: words from %s epoch %d dropped (current %d)", client, epoch, floor)
            return False
        return True

    # ── what he says ─────────────────────────────────────────────────────

    def final(self, text: str, heard: Optional[int] = None, client: Optional[str] = None,
              epoch: Optional[int] = None) -> bool:
        """A final heard as his speech. False if dropped as stale."""
        if not self.current(client, epoch):
            return False
        self._took_floor()
        turn = self._gathering(client)
        turn.parts.append(text)
        if heard is not None:
            turn.heard = heard
        self._open_interim = False
        self._activity()
        return True

    def interim(self, text: str = "", client: Optional[str] = None,
                epoch: Optional[int] = None) -> bool:
        """An interim heard as his speech (not echo). False if stale."""
        if not self.current(client, epoch):
            return False
        self._took_floor()
        if text.strip():
            self._open_interim = True
        if self._turn is not None:
            if client is not None and self._turn.client is None:
                self._turn.client = client
            self._activity()
        return True

    def discard(self, client: Optional[str] = None, epoch: Optional[int] = None) -> Optional[str]:
        """His stop under Discard: the turn being gathered is dropped and
        every word from an epoch before `epoch` is refused from now on.
        Returns what was dropped, for the log."""
        if client is not None and epoch is not None:
            self._discarded[client] = max(epoch, self._discarded.get(client, -1))
        dropped = self._drop()
        if dropped:
            log.info("turn: discarded %r", dropped)
        return dropped

    def close(self) -> Optional[str]:
        """The voice socket closed: nothing is committed. Returns what was
        gathered, to be kept as a draft."""
        return self._drop()

    def send_now(self) -> bool:
        """Commit now, on his word. While gathering the coverage check still
        applies; a stalled turn is sent as it was heard, because he has seen
        it could not be finished and chose to send it. False when there is
        nothing to send or the recogniser has not caught up."""
        if self._turn is None or not self._turn.parts:
            return False
        if self.state != "stalled" and (self._open_interim or not self._covered()):
            return False
        self._commit_turn()
        return True

    def retry(self) -> bool:
        """A stalled turn waits for the recogniser again, for another
        `coverage_bound`. False when no turn is stalled."""
        if self.state != "stalled" or self._turn is None:
            return False
        self.state = "commit-pending"
        self._deadline = self._clock()
        token = (self._turn.id, self.revision, self._deadline)
        self._arm(0.0, token)
        return True

    def keep_as_draft(self) -> Optional[str]:
        """A stalled turn becomes an unsent draft: nothing is sent. Returns
        its words, or None when no turn is stalled."""
        if self.state != "stalled":
            return None
        return self._drop()

    def reply_started(self, turn_id: int) -> None:
        """Its reply's audio has started: speech now is a barge-in, the
        scheduler's to handle, not a taking back of the floor."""
        if self.committed == turn_id:
            self.committed = None
            if self.state == "awaiting-reply":
                self.state = "idle"

    # ── inside ───────────────────────────────────────────────────────────

    def _took_floor(self) -> None:
        if self.committed is not None:
            turn, self.committed = self.committed, None
            if self.state == "awaiting-reply":
                self.state = "idle"
            self._on_resumed(turn)

    def _gathering(self, client: Optional[str]) -> _Turn:
        if self._turn is None:
            self._turn = _Turn(next(self._ids), client=client)
        elif client is not None and self._turn.client is None:
            self._turn.client = client
        return self._turn

    def _activity(self) -> None:
        """Accepted speech: the revision moves on and the hold starts again."""
        self.revision += 1
        self.state = "gathering"
        if self._turn is None or not self._turn.parts:
            return
        self._deadline = self._clock() + self.hold
        sent = self._frames()
        self._need = sent[0] if sent else None
        token = (self._turn.id, self.revision, self._deadline)
        self._arm(self.hold, token)
        self._arm_cues(token)

    def _arm_cues(self, token: tuple) -> None:
        """This pause's cues, each once, timed back from the deadline."""
        self._cancel_cues()
        for stage, before in self._pause_cues():
            delay = self.hold - before
            if delay > 0:
                self._cue_timers.append(self._call_later(
                    delay, lambda stage=stage, at=token[2] - before: self._cue_due(token, stage, at)))

    def _cancel_cues(self) -> None:
        for timer in self._cue_timers:
            cancel = getattr(timer, "cancel", None)
            if cancel:
                cancel()
        self._cue_timers = []
        self.pause_stage = None

    def _cue_due(self, token: tuple, stage: str, at: float) -> None:
        """A pause cue's time: given only if nothing has moved since it was
        scheduled, the turn is still being gathered, and its time has come."""
        turn_id, revision, deadline = token
        if (self._turn is None or self._turn.id != turn_id or self.revision != revision
                or self._deadline != deadline or self.state != "gathering"
                or not self._turn.parts or self._clock() < at - CUE_SLACK_SEC):
            return
        self.pause_stage = stage
        log.info("turn %d: %s pause cue (revision %d)", turn_id, stage, revision)
        self._on_pause(turn_id, stage)

    def _arm(self, delay: float, token: tuple) -> None:
        cancel = getattr(self._timer, "cancel", None)
        if cancel:
            cancel()
        self._timer = self._call_later(delay, lambda: self._due(token))

    def _frames(self) -> Optional[tuple[int, int]]:
        client = self._turn.client if self._turn else None
        return self._coverage(client) if client else None

    def _covered(self) -> bool:
        if self._need is None:
            return True
        now = self._frames()
        return now is None or now[1] >= self._need

    def _due(self, token: tuple) -> None:
        """The hold's deadline: commit only if nothing has moved since."""
        turn_id, revision, deadline = token
        if (self._turn is None or self._turn.id != turn_id or self.revision != revision
                or self._deadline != deadline or not self._turn.parts):
            return                      # a newer word, a drop or a commit came first
        now = self._clock()
        if now < deadline:
            return
        self.state = "commit-pending"
        if not self._open_interim and self._covered():
            self._commit_turn()
            return
        if now - deadline >= self.coverage_bound:
            self._stall()
            return
        self._arm(self.coverage_poll, token)

    def _commit_turn(self) -> None:
        turn = self._turn
        assert turn is not None
        self._cancel_cues()
        self._turn, self._deadline, self._need, self._timer = None, None, None, None
        self.state = "awaiting-reply"
        self.committed = turn.id
        text = " ".join(turn.parts)
        log.info("turn %d committed (revision %d)", turn.id, self.revision)
        self._spawn(self._commit(text, turn.heard, turn.id))

    def _stall(self) -> None:
        turn = self._turn
        assert turn is not None
        cancel = getattr(self._timer, "cancel", None)
        if cancel:
            cancel()
        self._timer = None
        self._cancel_cues()
        self.state = "stalled"
        log.warning("turn %d stalled: the recogniser did not catch up; waiting for his choice",
                    turn.id)
        self._on_stalled(turn.id, " ".join(turn.parts))

    def _drop(self) -> Optional[str]:
        cancel = getattr(self._timer, "cancel", None)
        if cancel:
            cancel()
        self._cancel_cues()
        turn = self._turn
        self._turn, self._deadline, self._need, self._timer = None, None, None, None
        self._open_interim = False
        self.revision += 1
        if self.state in ("gathering", "commit-pending", "stalled"):
            self.state = "idle"
        return " ".join(turn.parts) if turn and turn.parts else None
