"""What the brain is doing, as one bounded in-memory feed.

The Denice page shows every piece of the brain's work as a transmission
between that brain and its core. This is the one record it reads, at
`GET /api/brain/activity?since=<seq>`: each event is a kind, a stage, when
it happened, which brain provider was in force, and, where there is one,
who is at the other end. Never words, arguments, results or subjects: the
recorders below take only ids, names of agents, counts and timings, and
`record` refuses any field outside `FIELDS`.

The kinds, each recorded where the fact is first known (one writer each):

    turn            a brain turn (`brain.Brain._turn_locked` and its stream):
                    `started` (with `origin`: user, watcher, digest, system,
                    warmup ...), `first_output`, `streaming` at most once per
                    `PROGRESS_EVERY_SEC` while output arrives (with
                    `chars_per_sec` over that stretch and `chars` so far), then
                    exactly one of `completed`, `failed` or `cancelled`.
    tool            a call to one of JARVIS's tools (`tool_activity.py`, which
                    records into this feed): started, then refused, failed or
                    completed.
    agent_message   a message to an agent, at every status the messages record
                    takes (`run_store.create_message` / `update_message`, the
                    one writer): staged, queued, sent, undelivered, delivered,
                    working, answered, failed, cancelled; with the session id
                    and the agent's name.
    person_message  a message between people: `sent` (handed over on his
                    approval), `accepted` (the team server took it) and
                    `arrived` (stored here as his); the counterpart is
                    "a person", never who.
    session_message a session's message to him arriving
                    (`run_store.create_session_message`), with the agent.
    digest          an attention digest (`attention_store`): `started`, then
                    its outcome (silent, discarded, failed, queued, played,
                    interrupted).
    speech          something JARVIS says (`speech.SpeechScheduler`): `started`
                    when the page says its first audio began, `ended` with
                    `outcome` (played, interrupted, skipped, failed) once it is
                    done; `label` says what it is (reply, announcement,
                    readback ...).
    listening       his speech being gathered into a turn
                    (`turn_controller.TurnController`, the one writer of its
                    state): `started` when a page's turn starts gathering,
                    `ended` once it stops (committed, discarded, or the
                    capture stopped); with the turn's id, never its words.

Memory only: at most `KEEP_EVENTS`, oldest dropped first, gone on a restart
(`generation` says which process the sequence numbers belong to). There is
no `/ws/sessions` hint: a reader asks again with the last `seq` it saw.
Recording never raises into the code that records: a fault here loses an
event, never changes what JARVIS does.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Callable, Iterable, Optional

log = logging.getLogger("jarvis.brain_activity")

TURN = "turn"
TOOL = "tool"
AGENT_MESSAGE = "agent_message"
PERSON_MESSAGE = "person_message"
SESSION_MESSAGE = "session_message"
DIGEST = "digest"
SPEECH = "speech"
LISTENING = "listening"
KINDS = (TURN, TOOL, AGENT_MESSAGE, PERSON_MESSAGE, SESSION_MESSAGE, DIGEST, SPEECH,
         LISTENING)

# Turn stages.
STARTED = "started"
FIRST_OUTPUT = "first_output"
STREAMING = "streaming"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TURN_ENDINGS = (COMPLETED, FAILED, CANCELLED)

# Person-message stages.
SENT = "sent"
ACCEPTED = "accepted"
ARRIVED = "arrived"
ENDED = "ended"

# Who is at the other end of a message between people: never who.
A_PERSON = "a person"

# At most one `streaming` event per turn in this long.
PROGRESS_EVERY_SEC = 0.25

# Events kept: a few minutes of a streaming turn's progress beside its
# tool calls and messages, small enough to read whole.
KEEP_EVENTS = 2000

# Every field an event may have. Nothing that could carry words is here:
# `counterpart` is an agent's name or A_PERSON, `origin` and `label` are
# JARVIS's own fixed codes.
FIELDS = frozenset({
    "seq", "kind", "stage", "at", "provider", "counterpart", "id",
    "origin", "duration_ms", "chars", "chars_per_sec", "outcome", "label",
    "session_id", "call_id", "tool", "category",
})


class ActivityFeed:
    """The feed. Safe from any thread."""

    def __init__(self, keep: int = KEEP_EVENTS,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.generation = uuid.uuid4().hex
        self._events: deque = deque(maxlen=keep)
        self._seq = 0
        self._lock = threading.Lock()
        self._clock = clock
        self.monotonic = monotonic
        # The brain provider in force ("anthropic", "openai"), set by the
        # brain; every event not naming its own carries it.
        self.provider: Optional[str] = None
        self._turns: dict[str, dict] = {}

    # ── the record ────────────────────────────────────────────────────
    def record(self, kind: str, stage: str, **fields) -> Optional[dict]:
        """Append one event; a copy of it. Unknown fields are refused."""
        unknown = set(fields) - FIELDS
        if unknown:
            raise ValueError(f"not an activity field: {sorted(unknown)}")
        with self._lock:
            return dict(self._append(kind, stage, fields))

    def _append(self, kind: str, stage: str, fields: dict) -> dict:
        self._seq += 1
        event = {"seq": self._seq, "kind": kind, "stage": stage,
                 "at": self._clock(),
                 "provider": fields.pop("provider", None) or self.provider,
                 "counterpart": fields.pop("counterpart", None)}
        event.update(fields)
        self._events.append(event)
        return event

    def since(self, seq: int = 0, kinds: Optional[Iterable[str]] = None) -> dict:
        """Every kept event after `seq` (of `kinds`, when given), oldest
        first, with the latest sequence number and the oldest one still kept
        of any kind (None when empty)."""
        wanted = None if kinds is None else frozenset(kinds)
        with self._lock:
            events = [dict(e) for e in self._events
                      if e["seq"] > seq and (wanted is None or e["kind"] in wanted)]
            oldest = self._events[0]["seq"] if self._events else None
            latest = self._seq
        return {"generation": self.generation, "seq": latest,
                "oldest": oldest, "events": events}

    # ── turns ─────────────────────────────────────────────────────────
    def turn_started(self, origin: str, provider: Optional[str] = None) -> str:
        """A turn sent to the brain; its id for the rest."""
        turn_id = uuid.uuid4().hex
        now = self.monotonic()
        with self._lock:
            if provider:
                self.provider = provider
            self._turns[turn_id] = {"began": now, "chars": 0, "first": None,
                                    "last_at": now, "last_chars": 0}
            self._append(TURN, STARTED, {"id": turn_id, "origin": origin})
        return turn_id

    def turn_output(self, turn_id: str, chars: int) -> None:
        """`chars` more characters of output (never the characters). The
        first is `first_output`; then at most one `streaming` per
        PROGRESS_EVERY_SEC, with the rate over the stretch since the last."""
        now = self.monotonic()
        with self._lock:
            t = self._turns.get(turn_id)
            if t is None:
                return
            t["chars"] += max(0, int(chars))
            if t["first"] is None:
                t["first"] = now
                t["last_at"], t["last_chars"] = now, t["chars"]
                self._append(TURN, FIRST_OUTPUT, {
                    "id": turn_id, "chars": t["chars"],
                    "duration_ms": _ms(now - t["began"])})
                return
            span = now - t["last_at"]
            if span < PROGRESS_EVERY_SEC:
                return
            rate = (t["chars"] - t["last_chars"]) / span
            t["last_at"], t["last_chars"] = now, t["chars"]
            self._append(TURN, STREAMING, {
                "id": turn_id, "chars": t["chars"],
                "chars_per_sec": round(rate, 1),
                "duration_ms": _ms(now - t["began"])})

    def turn_ended(self, turn_id: str, outcome: str) -> None:
        """How the turn ended; a second ending is ignored."""
        if outcome not in TURN_ENDINGS:
            raise ValueError(f"not a turn ending: {outcome}")
        now = self.monotonic()
        with self._lock:
            t = self._turns.pop(turn_id, None)
            if t is None:
                return
            self._append(TURN, outcome, {
                "id": turn_id, "chars": t["chars"],
                "duration_ms": _ms(now - t["began"])})

    # ── speech ────────────────────────────────────────────────────────
    def speech_started(self, utterance_id: int, label: str) -> None:
        """The page says the first audio of this utterance began."""
        self.record(SPEECH, STARTED, id=str(utterance_id), label=label)

    def speech_ended(self, utterance_id: int, label: str, outcome: str,
                     seconds: float) -> None:
        """A started utterance is done, `seconds` after it began to sound."""
        self.record(SPEECH, ENDED, id=str(utterance_id), label=label,
                    outcome=outcome, duration_ms=_ms(seconds))

    # ── listening ─────────────────────────────────────────────────────
    def listening(self, stage: str, turn_id: int) -> None:
        """His speech started (`STARTED`) or stopped (`ENDED`) being
        gathered into turn `turn_id`."""
        if stage not in (STARTED, ENDED):
            raise ValueError(f"not a listening stage: {stage}")
        self.record(LISTENING, stage, id=str(turn_id))


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


feed = ActivityFeed()


def note(kind: str, stage: str, **fields) -> None:
    """Record into the one feed, never raising into the caller."""
    try:
        feed.record(kind, stage, **fields)
    except Exception as e:
        log.warning("brain activity not recorded (%s %s): %s", kind, stage,
                    type(e).__name__)


def safely(fn: Callable, *args, **kwargs) -> None:
    """Call one of the feed's recorders, never raising into the caller."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        log.warning("brain activity not recorded: %s", type(e).__name__)
