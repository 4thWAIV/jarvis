"""
voice_control.py — the voice control contract's shared pieces on the server
(docs/planning/quiet-conversation/contract.md): for now, the two voice input
settings and the one service the page's route and the brain's tools call,
and the words the page and the server both name on the voice socket
(the page's copy is `frontend/src/voiceControl.ts`).

  stop    "When I stop the microphone": `keep` the words already captured
          (only the audio from before the stop is finished, and its words go
          under the ordinary turn rules; never a new automatic send), or
          `discard` unfinished input (live and finishing captures, the turn
          being gathered and late recognition are all thrown away).
  switch  "When I switch away from JARVIS": `continue` a listening session
          he already started, or `stop` listening (the stop setting then
          decides what becomes of what he was saying). Coming back never
          starts listening, and neither setting can turn on a microphone
          that is muted or off.

With them are his pause intervals (`intervals`, one per pause stage,
turn_controller.PAUSE_STAGES) and his capture sounds (`sounds`, on or off,
default on): the master switch for every capture cue. Off plays none;
what the page shows is the same either way.

The capture cues (`cues`, `volume`) are five quiet sounds, each on or off
and each one tone of the fixed presets (TONES; the page holds their
notes, voice.ts):

  start      listening really started
  stop       listening really stopped, for any reason
  tentative  he has paused in a gathered turn: JARVIS thinks he may be done
  deciding   he is still quiet: JARVIS is deciding whether he is done
  likely     he is still quiet: the turn is about to be sent

A pause in his turn has four stages, in order (turn_controller.PAUSE_STAGES):
the three pause cues, then the send. Each stage's interval (`intervals`,
INTERVAL_MIN to INTERVAL_MAX seconds, defaults 1.0, 0.75, 0.75 and 0.5) is
measured from the stage before, the first from when his speech was last
heard (the hold start): to the tentative cue, then to the deciding cue,
then to the likely cue, then to the send. Each stage's time after he
stopped is their running sum (`schedule`, turn_controller.pause_schedule,
the one function that works it out), so every cue that is on plays, in
order, before the turn is sent, and none can play after a later one or
after the send. A cue that is off keeps its interval: the time passes, with
no sound. His patience (`patience`) is the total, the send's time (3.0 s
by default): never set on its own, it governs both sides of the
conversation, the turn controller's hold and how long the speech
scheduler waits before JARVIS starts talking after he has spoken. Patience
saved on its own before (`patience_sec`) and the cues' shares of it
(`percent`) were dropped by migration 7: those rows read with the default
intervals, a total of 3.0 s, keeping each cue's tone and on or off.
`volume` is one cue
volume, VOLUME_MIN to VOLUME_MAX percent of a quiet ceiling the page sets.

They are per user, on this JARVIS (the node's SQLite, its own schema
version through run_store.migrate): the team member signed in, by GitHub
id, or else this JARVIS's local owner. Never a team-wide choice. A user who
has chosen nothing has the defaults (DEFAULTS, assessed separately during
validation; choosing a default never overwrites a saved choice). Changing
one applies from the next stop or the next time the page is left: the
change itself submits, discards and starts nothing.

`read` and `change` are what the route (`/api/settings/voice-input`) and the
brain's `voice_input_settings` and `set_voice_input_settings` call, with the
same validation and the same answer. A change that is saved is published
once (`FRAME` on /ws/sessions) so every open page reads them again. A store
that cannot be read raises; the page then keeps Start listening unavailable,
and nothing is inferred from the failure.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from typing import Callable, Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from jarvis import run_store
from jarvis.turn_controller import (DEFAULT_PATIENCE, INTERVAL_DEFAULTS, PAUSE_CUES, PAUSE_STAGES,
                                    pause_schedule)

log = logging.getLogger("jarvis.voice_control")

STOP_MODES = ("keep", "discard")
SWITCH_MODES = ("continue", "stop")
# What a user who has chosen nothing gets. Provisional: the defaults are
# assessed separately, during validation.
DEFAULTS = {"stop": "keep", "switch": "stop", "sounds": True}
# The capture cues: the two that mark listening, the pause cues that mark
# the stages of a pause in his turn (in the order they play), and the quiet
# presets each may play. The pause cues, the pause stages (the pause cues,
# then the send) and their default intervals are turn_controller's.
LISTEN_CUES = ("start", "stop")
CUES = LISTEN_CUES + PAUSE_CUES
TONES = ("rise", "fall", "pulse", "low", "double", "resolve")
# A pause stage's interval after the stage before, in seconds.
INTERVAL_MIN, INTERVAL_MAX = 0.25, 5.0
# The cue volume, in percent of the page's quiet ceiling.
VOLUME_MIN, VOLUME_MAX = 5, 100
CUE_DEFAULTS = {
    "start": {"on": True, "tone": "rise"},
    "stop": {"on": True, "tone": "fall"},
    "tentative": {"on": True, "tone": "pulse"},
    "deciding": {"on": True, "tone": "low"},
    "likely": {"on": True, "tone": "resolve"},
}
VOLUME_DEFAULT = 50
LOCAL_OWNER = "local"
PATH = "/api/settings/voice-input"
# The hint every open page gets when the settings change (/ws/sessions).
FRAME = "voice_input_settings"

_MIGRATIONS = {
    1: """
        CREATE TABLE IF NOT EXISTS voice_input_settings (
            user_key    TEXT PRIMARY KEY,
            stop_mode   TEXT NOT NULL CHECK (stop_mode IN ('keep', 'discard')),
            switch_mode TEXT NOT NULL CHECK (switch_mode IN ('continue', 'stop')),
            updated_at  REAL NOT NULL
        );
    """,
    2: """
        ALTER TABLE voice_input_settings ADD COLUMN patience_sec REAL;
    """,
    # NULL: never chosen, the default (on).
    3: """
        ALTER TABLE voice_input_settings ADD COLUMN capture_sounds INTEGER
            CHECK (capture_sounds IN (0, 1));
    """,
    # The cues' choices as JSON, only what he chose (NULL: none); the volume
    # NULL until chosen.
    4: """
        ALTER TABLE voice_input_settings ADD COLUMN cue_settings TEXT;
        ALTER TABLE voice_input_settings ADD COLUMN cue_volume INTEGER;
    """,
    # The deciding pause cue came after his tentative and likely-end timings
    # could be chosen. A row whose timings in force (saved, else the
    # defaults then: 2.5 and 1.0) do not have the deciding cue's default
    # (1.75) between them would read as out of order and lose them, so it
    # gets a deciding cue halfway between instead; every other row reads with
    # the default deciding cue, unchanged.
    5: """
        UPDATE voice_input_settings
           SET cue_settings = json_set(cue_settings, '$.deciding',
                                       json(json_object('before', (e.t + e.l) / 2.0)))
          FROM (SELECT user_key AS k,
                       CASE WHEN json_type(cue_settings, '$.tentative.before') IN ('integer', 'real')
                             AND json_extract(cue_settings, '$.tentative.before') BETWEEN 0.25 AND 10
                            THEN json_extract(cue_settings, '$.tentative.before') ELSE 2.5 END AS t,
                       CASE WHEN json_type(cue_settings, '$.likely.before') IN ('integer', 'real')
                             AND json_extract(cue_settings, '$.likely.before') BETWEEN 0.25 AND 10
                            THEN json_extract(cue_settings, '$.likely.before') ELSE 1.0 END AS l
                  FROM voice_input_settings
                 WHERE json_valid(cue_settings) AND json_type(cue_settings) = 'object'
                   AND json_type(cue_settings, '$.deciding') IS NULL) AS e
         WHERE voice_input_settings.user_key = e.k AND e.t > e.l
           AND NOT (e.t > 1.75 AND 1.75 > e.l);
    """,
    # The pause cues are timed as a share of his patience now, not in
    # seconds before the end of his turn. A saved lead in seconds cannot be
    # turned into a share (the same lead is a different share at every
    # patience, and a lead longer than his patience never played), so each
    # is dropped: those cues read with the default shares, keeping their
    # tone and on or off.
    6: """
        UPDATE voice_input_settings
           SET cue_settings = json_remove(cue_settings, '$.tentative.before',
                                          '$.deciding.before', '$.likely.before')
         WHERE json_valid(cue_settings) AND json_type(cue_settings) = 'object';
    """,
    # The pause is four intervals now, each after the stage before, and his
    # patience is their total. A patience saved on its own and the cues'
    # shares of it cannot become intervals (he never chose them), so both
    # are dropped: those rows read with the default intervals (a total of
    # 3.0 s), keeping each cue's tone and on or off. The intervals he
    # chooses are saved as JSON, only those he chose (NULL: none).
    7: """
        ALTER TABLE voice_input_settings ADD COLUMN pause_intervals TEXT;
        UPDATE voice_input_settings SET patience_sec = NULL;
        UPDATE voice_input_settings
           SET cue_settings = json_remove(cue_settings, '$.tentative.percent',
                                          '$.deciding.percent', '$.likely.percent')
         WHERE json_valid(cue_settings) AND json_type(cue_settings) = 'object';
    """,
}

Publish = Callable[[dict], None]
_publish: Optional[Publish] = None


class Invalid(ValueError):
    """A change refused: its code, and its words in English."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_json(self) -> dict:
        return {"code": self.code, "error": self.message}


class Unreadable(RuntimeError):
    """The store could not be read or written."""


def user_key() -> str:
    """Whose settings these are: the team member signed in on this JARVIS,
    by GitHub id, or its local owner."""
    from jarvis import team_client
    connection = team_client.connection
    if not connection.signed_in():
        return LOCAL_OWNER
    member = (connection.snapshot().get("me") or {}).get("github_id")
    return f"github:{member}" if type(member) is int else LOCAL_OWNER


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    with conn:
        run_store.migrate(conn, "voice_control_schema", _MIGRATIONS, "voice control")
    return conn


def _row(user: str) -> Optional[sqlite3.Row]:
    try:
        with closing(_connect()) as conn:
            return conn.execute(
                "SELECT stop_mode, switch_mode, capture_sounds, cue_settings, cue_volume, "
                "pause_intervals FROM voice_input_settings WHERE user_key = ?", (user,)).fetchone()
    except (sqlite3.Error, RuntimeError) as e:
        log.warning("voice input settings could not be read (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be read.") from e


def _answer(user: str, stop: str, switch: str, saved: bool, intervals: dict, sounds: bool,
            cues: dict, volume: int) -> dict:
    schedule = dict(pause_schedule(intervals))
    return {"stop": stop, "switch": switch, "saved": saved, "intervals": intervals,
            "schedule": schedule, "patience": schedule[PAUSE_STAGES[-1]],
            "sounds": sounds, "cues": cues, "volume": volume,
            "scope": "local" if user == LOCAL_OWNER else "member"}


def defaults(user: str = LOCAL_OWNER) -> dict:
    """What `read` answers for a user who has chosen nothing."""
    return _answer(user, DEFAULTS["stop"], DEFAULTS["switch"], False, _intervals_in_force(None),
                   DEFAULTS["sounds"], _cues_in_force(None), VOLUME_DEFAULT)


def read(user: Optional[str] = None) -> dict:
    """The settings in force for `user` (the user now, by default):
    {stop, switch, intervals, schedule, patience, sounds, cues, volume,
    saved, scope}: `schedule` is each pause stage's seconds after he
    stopped, `patience` the send's. Raises Unreadable if the store cannot
    be read, and treats a saved row it does not understand as unreadable
    too. A saved interval, cue or volume that is not one JARVIS knows is
    the default instead."""
    user = user or user_key()
    row = _row(user)
    if row is None:
        return defaults(user)
    stop, switch = row["stop_mode"], row["switch_mode"]
    if stop not in STOP_MODES or switch not in SWITCH_MODES:
        raise Unreadable("Your saved voice input settings are not ones JARVIS knows.")
    sounds = DEFAULTS["sounds"] if row["capture_sounds"] is None else bool(row["capture_sounds"])
    volume = int(row["cue_volume"]) if _volume_ok(row["cue_volume"]) else VOLUME_DEFAULT
    return _answer(user, stop, switch, True, _intervals_in_force(_saved(row["pause_intervals"])),
                   sounds, _cues_in_force(_saved_cues(row["cue_settings"])), volume)


def read_or_defaults(user: Optional[str] = None) -> dict:
    """The settings in force, or the defaults when the store cannot be
    read: for the voice path, which never stops for them."""
    try:
        return read(user)
    except Unreadable:
        return defaults()


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _interval_ok(value: object) -> bool:
    return _number(value) and INTERVAL_MIN <= value <= INTERVAL_MAX  # type: ignore[operator]


def _volume_ok(value: object) -> bool:
    return _number(value) and VOLUME_MIN <= value <= VOLUME_MAX  # type: ignore[operator]


def _cue_problem(name: object, choice: object) -> Optional[str]:
    """Why one cue's choice is not one JARVIS takes, or None."""
    if name not in CUES:
        return f"The cues are {', '.join(CUES)}."
    if not isinstance(choice, dict) or not choice:
        return f"Say what to change about the {name} cue: on or tone."
    keys = {"on", "tone"}
    for key, value in choice.items():
        if key not in keys:
            return f"The {name} cue has {', '.join(sorted(keys))}."
        if key == "on" and not isinstance(value, bool):
            return f"The {name} cue is on (true) or off (false)."
        if key == "tone" and value not in TONES:
            return f"A cue's tone is one of {', '.join(TONES)}."
    return None


def _intervals_problem(intervals: object) -> Optional[str]:
    """Why a change to the pause intervals is not one JARVIS takes, or
    None."""
    if not isinstance(intervals, dict) or not intervals:
        return f"Say which pause interval to change: {', '.join(PAUSE_STAGES)}."
    for stage, value in intervals.items():
        if stage not in PAUSE_STAGES:
            return f"The pause intervals are {', '.join(PAUSE_STAGES)}."
        if not _interval_ok(value):
            return (f"Each pause interval is {INTERVAL_MIN:g} to {INTERVAL_MAX:g} seconds "
                    "after the stage before.")
    return None


def _saved(raw: object) -> dict:
    """A JSON object saved, or nothing."""
    try:
        saved = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        saved = {}
    return saved if isinstance(saved, dict) else {}


def _saved_cues(raw: object) -> dict:
    """The cue choices saved, only those JARVIS still takes."""
    return {name: choice for name, choice in _saved(raw).items()
            if _cue_problem(name, choice) is None}


def _intervals_in_force(saved: Optional[dict]) -> dict:
    """Each pause stage's interval: what he chose, where JARVIS still takes
    it, else the default, rounded to the hundredth."""
    saved = saved or {}
    return {stage: round(float(saved[stage]) if _interval_ok(saved.get(stage))
                         else INTERVAL_DEFAULTS[stage], 2)
            for stage in PAUSE_STAGES}


def _cues_in_force(saved: Optional[dict]) -> dict:
    """Each cue's defaults with his choices over them."""
    return {name: {**CUE_DEFAULTS[name], **((saved or {}).get(name) or {})} for name in CUES}


def schedule_of(settings: dict) -> list[tuple[str, float]]:
    """The pause stages to time in a gathered turn, each with its seconds
    after he stopped, the send last: every stage, a cue on or off (whether
    one sounds is decided when it is due)."""
    return pause_schedule(settings["intervals"])


def cue_sounds(settings: dict, name: str) -> bool:
    """Whether this cue makes a sound: capture sounds on, and the cue on."""
    return bool(settings["sounds"] and settings["cues"][name]["on"])


def validate(stop: object = None, switch: object = None, intervals: object = None,
             sounds: object = None, cues: object = None, volume: object = None
             ) -> tuple[Optional[str], Optional[str], Optional[dict], Optional[bool],
                        Optional[dict], Optional[int]]:
    """The change asked for, checked: each given value must be one of its
    setting's, and at least one must be given."""
    if all(v is None for v in (stop, switch, intervals, sounds, cues, volume)):
        raise Invalid("nothing-to-change",
                      "Say which setting to change: stop, switch, intervals, sounds, cues or "
                      "volume.")
    if sounds is not None and not isinstance(sounds, bool):
        raise Invalid("invalid-sounds", "Capture sounds are on (true) or off (false).")
    if intervals is not None:
        problem = _intervals_problem(intervals)
        if problem:
            raise Invalid("invalid-interval", problem)
    if stop is not None and stop not in STOP_MODES:
        raise Invalid("invalid-stop", "When I stop the microphone is keep or discard.")
    if switch is not None and switch not in SWITCH_MODES:
        raise Invalid("invalid-switch", "When I switch away from JARVIS is continue or stop.")
    if volume is not None and not _volume_ok(volume):
        raise Invalid("invalid-volume",
                      f"The cue volume is {VOLUME_MIN} to {VOLUME_MAX} percent.")
    if cues is not None:
        if not isinstance(cues, dict) or not cues:
            raise Invalid("invalid-cue", f"Say which cue to change: {', '.join(CUES)}.")
        for name, choice in cues.items():
            problem = _cue_problem(name, choice)
            if problem:
                raise Invalid("invalid-cue", problem)
    return (stop, switch,  # type: ignore[return-value]
            None if intervals is None else {k: round(float(v), 2)  # type: ignore[union-attr]
                                            for k, v in intervals.items()},  # type: ignore[union-attr]
            sounds, cues,  # type: ignore[return-value]
            None if volume is None else int(round(volume)))  # type: ignore[arg-type]


def change(stop: object = None, switch: object = None, intervals: object = None,
           sounds: object = None, cues: object = None, volume: object = None,
           user: Optional[str] = None) -> dict:
    """Save any of the settings for `user` and answer the settings now in
    force, as `read` gives them. Starts, stops, submits and discards
    nothing: they apply from the next stop or page switch (the cues and
    intervals from the next pause). A change to the cues or the intervals
    is merged into what he chose before; any intervals keep the stages in
    order, since each is measured from the stage before."""
    stop, switch, intervals, sounds, cues, volume = validate(stop, switch, intervals, sounds,
                                                             cues, volume)
    user = user or user_key()
    now = read(user)
    row = _row(user)
    chosen = _saved_cues(row["cue_settings"]) if row is not None else {}
    for name, choice in (cues or {}).items():
        chosen[name] = {**chosen.get(name, {}), **choice}
    waits = _saved(row["pause_intervals"]) if row is not None else {}
    waits.update(intervals or {})
    new = {"stop": stop or now["stop"], "switch": switch or now["switch"],
           "intervals": json.dumps(waits, sort_keys=True) if intervals else None,
           "sounds": None if sounds is None else int(sounds),
           "cues": json.dumps(chosen, sort_keys=True) if cues else None,
           "volume": volume}
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, "
                "pause_intervals, capture_sounds, cue_settings, cue_volume, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_key) DO "
                "UPDATE SET stop_mode = excluded.stop_mode, switch_mode = excluded.switch_mode, "
                "pause_intervals = COALESCE(excluded.pause_intervals, "
                "voice_input_settings.pause_intervals), "
                "capture_sounds = COALESCE(excluded.capture_sounds, "
                "voice_input_settings.capture_sounds), "
                "cue_settings = COALESCE(excluded.cue_settings, voice_input_settings.cue_settings), "
                "cue_volume = COALESCE(excluded.cue_volume, voice_input_settings.cue_volume), "
                "updated_at = excluded.updated_at",
                (user, new["stop"], new["switch"], new["intervals"], new["sounds"], new["cues"],
                 new["volume"], time.time()))
    except sqlite3.Error as e:
        log.warning("voice input settings could not be saved (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be saved.") from e
    log.info("voice input settings for %s: stop=%s switch=%s intervals=%s sounds=%s cues=%s "
             "volume=%s", user, new["stop"], new["switch"], new["intervals"], new["sounds"],
             new["cues"], new["volume"])
    saved = read(user)
    if _publish is not None:
        _publish({"type": FRAME})
    return saved


def patience(user: Optional[str] = None) -> float:
    """His patience in seconds: the total of his pause intervals, or the
    defaults' total when the store cannot be read."""
    try:
        return read(user)["patience"]
    except Unreadable:
        return DEFAULT_PATIENCE


# ── the page's routes ────────────────────────────────────────────────────

def _unreadable(e: Unreadable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"code": "unreadable", "error": str(e)})


async def api_read():
    try:
        return read()
    except Unreadable as e:
        return _unreadable(e)


async def api_change(body: dict = Body(default_factory=dict)):
    try:
        return change(stop=body.get("stop"), switch=body.get("switch"),
                      intervals=body.get("intervals"), sounds=body.get("sounds"),
                      cues=body.get("cues"), volume=body.get("volume"))
    except Invalid as e:
        return JSONResponse(status_code=400, content=e.to_json())
    except Unreadable as e:
        return _unreadable(e)


def mount(app: FastAPI, publish: Optional[Publish] = None) -> None:
    global _publish
    _publish = publish
    app.add_api_route(PATH, api_read, methods=["GET"])
    app.add_api_route(PATH, api_change, methods=["POST"])


# ── the voice socket's shared words ─────────────────────────────────────

class ReplyOutcome:
    """How a reply's text ended, sent on its last `reply` frame as
    `outcome`, so unfinished text is never shown as complete."""
    SUCCEEDED = "succeeded"    # the provider finished the turn
    FAILED = "failed"          # it stopped with an error, timed out, died or hit a limit
    CANCELLED = "cancelled"    # the turn itself was cancelled before it finished
    ALL = frozenset({SUCCEEDED, FAILED, CANCELLED})


class ReplyFailure:
    """Why a reply failed, sent beside `outcome: failed` as `reason` (and
    `resetsAt` for a usage limit), so the page says it in its own language
    and nothing is spoken in its place."""
    NOT_READY = "not_ready"        # the brain is still starting
    DOWN = "down"                  # the brain has failed
    SIGN_IN = "sign_in"            # the brain's login has expired
    RATE_LIMITED = "rate_limited"  # the usage limit; `resetsAt` says until when
    ERROR = "error"                # the provider returned an error
    LOST = "lost"                  # it timed out, died, stopped or broke
    ALL = frozenset({NOT_READY, DOWN, SIGN_IN, RATE_LIMITED, ERROR, LOST})

    @classmethod
    def of_stop(cls, stop_reason: str, rate_limit: object = None) -> dict:
        """The finish arguments for a turn that stopped for `stop_reason`."""
        if stop_reason == "rate_limited":
            resets = rate_limit.get("resetsAt") if isinstance(rate_limit, dict) else None
            return {"reason": cls.RATE_LIMITED,
                    "resets_at": resets if isinstance(resets, (int, float)) else None}
        if stop_reason == "error":
            return {"reason": cls.ERROR}
        return {"reason": cls.LOST}


# The `playback` frame the page sends about one audio chunk: what it alone
# can know. `played` keeps its own frame; skipped and interrupted are the
# server's own doing (skip_to_end, hush, stop), so the page never reports them.
PLAYBACK_FRAME = "playback"
PLAYBACK_STARTED = "started"
PLAYBACK_FAILED = "failed"
