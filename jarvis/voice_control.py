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

With them are his patience (`patience`): how many seconds of quiet end his
turn, One value governs both sides of the conversation: the turn
controller's hold and how long the speech scheduler waits before JARVIS
starts talking after he has spoken. Read when a voice client connects;
nothing saved, or anything unreadable, is the current constant
(speech.END_OF_TURN_SEC); and his capture sounds (`sounds`, on or off,
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

The pause cues (PAUSE_CUES, in the order they play) are timed in seconds
before the end of his turn (`before`), never on a clock of their own: the
end of his turn is his patience after the last word accepted as his, and a
pause cue plays at patience - before seconds of quiet. So each pause cue's
`before` must be more than the next one's (tentative, then deciding, then
likely), each within BEFORE_MIN to BEFORE_MAX, and a cue whose time falls
at or before the moment he stopped (before >= patience) does not play at
that patience. Changing his patience never invalidates them. The defaults
(2.5, 1.75 and 1.0) give the trial timings in turn-taking.md at a patience
of 4: tentative after 1.5 s, deciding after 2.25 s, likely-end after
3 s, the commit at 4. `volume` is one cue
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
from jarvis.speech import END_OF_TURN_SEC

log = logging.getLogger("jarvis.voice_control")

STOP_MODES = ("keep", "discard")
SWITCH_MODES = ("continue", "stop")
# What a user who has chosen nothing gets. Provisional: the defaults are
# assessed separately, during validation.
DEFAULTS = {"stop": "keep", "switch": "stop", "sounds": True}
# His patience may be set within these bounds, in seconds.
PATIENCE_MIN, PATIENCE_MAX = 0.5, 10.0
# The capture cues: the two that mark listening, the pause cues that mark
# the stages of a pause in his turn (in the order they play, each earlier
# one further before the end of his turn), and the quiet presets each may
# play.
LISTEN_CUES = ("start", "stop")
PAUSE_CUES = ("tentative", "deciding", "likely")
CUES = LISTEN_CUES + PAUSE_CUES
TONES = ("rise", "fall", "pulse", "low", "double", "resolve")
# A pause cue's seconds before the end of his turn.
BEFORE_MIN, BEFORE_MAX = 0.25, PATIENCE_MAX
# The cue volume, in percent of the page's quiet ceiling.
VOLUME_MIN, VOLUME_MAX = 5, 100
CUE_DEFAULTS = {
    "start": {"on": True, "tone": "rise"},
    "stop": {"on": True, "tone": "fall"},
    "tentative": {"on": True, "tone": "pulse", "before": 2.5},
    "deciding": {"on": True, "tone": "low", "before": 1.75},
    "likely": {"on": True, "tone": "resolve", "before": 1.0},
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
                "SELECT stop_mode, switch_mode, patience_sec, capture_sounds, cue_settings, "
                "cue_volume FROM voice_input_settings WHERE user_key = ?", (user,)).fetchone()
    except (sqlite3.Error, RuntimeError) as e:
        log.warning("voice input settings could not be read (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be read.") from e


def _answer(user: str, stop: str, switch: str, saved: bool, patience: float, sounds: bool,
            cues: dict, volume: int) -> dict:
    return {"stop": stop, "switch": switch, "saved": saved, "patience": patience,
            "sounds": sounds, "cues": cues, "volume": volume,
            "scope": "local" if user == LOCAL_OWNER else "member"}


def defaults(user: str = LOCAL_OWNER) -> dict:
    """What `read` answers for a user who has chosen nothing."""
    return _answer(user, DEFAULTS["stop"], DEFAULTS["switch"], False, END_OF_TURN_SEC,
                   DEFAULTS["sounds"], _cues_in_force(None), VOLUME_DEFAULT)


def read(user: Optional[str] = None) -> dict:
    """The settings in force for `user` (the user now, by default):
    {stop, switch, patience, sounds, cues, volume, saved, scope}. Raises
    Unreadable if the store cannot be read, and treats a saved row it does
    not understand as unreadable too. A saved patience, cue or volume that
    is not one JARVIS knows is the default instead."""
    user = user or user_key()
    row = _row(user)
    if row is None:
        return defaults(user)
    stop, switch = row["stop_mode"], row["switch_mode"]
    if stop not in STOP_MODES or switch not in SWITCH_MODES:
        raise Unreadable("Your saved voice input settings are not ones JARVIS knows.")
    patience = float(row["patience_sec"]) if _patience_ok(row["patience_sec"]) else END_OF_TURN_SEC
    sounds = DEFAULTS["sounds"] if row["capture_sounds"] is None else bool(row["capture_sounds"])
    volume = int(row["cue_volume"]) if _volume_ok(row["cue_volume"]) else VOLUME_DEFAULT
    return _answer(user, stop, switch, True, patience, sounds,
                   _cues_in_force(_saved_cues(row["cue_settings"])), volume)


def read_or_defaults(user: Optional[str] = None) -> dict:
    """The settings in force, or the defaults when the store cannot be
    read: for the voice path, which never stops for them."""
    try:
        return read(user)
    except Unreadable:
        return defaults()


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _patience_ok(value: object) -> bool:
    return _number(value) and PATIENCE_MIN <= value <= PATIENCE_MAX  # type: ignore[operator]


def _volume_ok(value: object) -> bool:
    return _number(value) and VOLUME_MIN <= value <= VOLUME_MAX  # type: ignore[operator]


def _cue_problem(name: object, choice: object) -> Optional[str]:
    """Why one cue's choice is not one JARVIS takes, or None."""
    if name not in CUES:
        return f"The cues are {', '.join(CUES)}."
    if not isinstance(choice, dict) or not choice:
        return f"Say what to change about the {name} cue: on, tone" + (
            " or before." if name in PAUSE_CUES else ".")
    keys = {"on", "tone", "before"} if name in PAUSE_CUES else {"on", "tone"}
    for key, value in choice.items():
        if key not in keys:
            return f"The {name} cue has {', '.join(sorted(keys))}."
        if key == "on" and not isinstance(value, bool):
            return f"The {name} cue is on (true) or off (false)."
        if key == "tone" and value not in TONES:
            return f"A cue's tone is one of {', '.join(TONES)}."
        if key == "before" and not (_number(value) and BEFORE_MIN <= value <= BEFORE_MAX):
            return (f"A pause cue plays {BEFORE_MIN:g} to {BEFORE_MAX:g} seconds before the "
                    "end of his turn.")
    return None


def _saved_cues(raw: object) -> dict:
    """The cue choices saved, only those JARVIS still takes."""
    try:
        saved = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        return {}
    return {name: choice for name, choice in saved.items()
            if _cue_problem(name, choice) is None}


def _cues_in_force(saved: Optional[dict]) -> dict:
    """Each cue's defaults with his choices over them. Saved pause timings
    out of order are the defaults' timings."""
    cues = {name: {**CUE_DEFAULTS[name], **((saved or {}).get(name) or {})} for name in CUES}
    if not _in_order(cues):
        for name in PAUSE_CUES:
            cues[name]["before"] = CUE_DEFAULTS[name]["before"]
    return cues


def _in_order(cues: dict) -> bool:
    """Each pause cue further before the end of his turn than the next."""
    leads = [cues[name]["before"] for name in PAUSE_CUES]
    return all(earlier > later for earlier, later in zip(leads, leads[1:]))


def pause_cue_leads(settings: dict) -> list[tuple[str, float]]:
    """The pause cues to time in a gathered turn, each with its seconds
    before the end of his turn: every pause cue, on or off (whether one
    sounds is decided when it is due, so turning it on or off applies at
    once)."""
    return [(name, float(settings["cues"][name]["before"])) for name in PAUSE_CUES]


def pause_cue_at(settings: dict, name: str) -> Optional[float]:
    """Seconds of quiet after which this pause cue plays at his patience,
    or None when that is not after he stopped."""
    at = round(settings["patience"] - settings["cues"][name]["before"], 2)
    return at if at > 0 else None


def pause_cue_gaps(settings: dict, name: str) -> Optional[dict]:
    """The gaps around one pause cue at his patience, each set by that cue's
    own `before` and its neighbour's (there is no shared spacing): `after`,
    (the nearest earlier pause cue that is on and plays, seconds since it)
    or None; `until`, (the nearest later one, seconds until it), or (None,
    seconds until what he said is sent) when none later plays. None when
    this cue does not play."""
    at = pause_cue_at(settings, name)
    if at is None:
        return None
    sounding = [n for n in PAUSE_CUES
                if settings["cues"][n]["on"] and pause_cue_at(settings, n) is not None]
    i = PAUSE_CUES.index(name)
    earlier = [n for n in sounding if PAUSE_CUES.index(n) < i]
    later = [n for n in sounding if PAUSE_CUES.index(n) > i]
    after = ((earlier[-1], round(at - pause_cue_at(settings, earlier[-1]), 2))  # type: ignore[operator]
             if earlier else None)
    until = ((later[0], round(pause_cue_at(settings, later[0]) - at, 2))  # type: ignore[operator]
             if later else (None, float(settings["cues"][name]["before"])))
    return {"after": after, "until": until}


def cue_sounds(settings: dict, name: str) -> bool:
    """Whether this cue makes a sound: capture sounds on, and the cue on."""
    return bool(settings["sounds"] and settings["cues"][name]["on"])


def validate(stop: object = None, switch: object = None, patience: object = None,
             sounds: object = None, cues: object = None, volume: object = None
             ) -> tuple[Optional[str], Optional[str], Optional[float], Optional[bool],
                        Optional[dict], Optional[int]]:
    """The change asked for, checked: each given value must be one of its
    setting's, and at least one must be given."""
    if all(v is None for v in (stop, switch, patience, sounds, cues, volume)):
        raise Invalid("nothing-to-change",
                      "Say which setting to change: stop, switch, patience, sounds, cues or "
                      "volume.")
    if sounds is not None and not isinstance(sounds, bool):
        raise Invalid("invalid-sounds", "Capture sounds are on (true) or off (false).")
    if patience is not None and not _patience_ok(patience):
        raise Invalid("invalid-patience",
                      f"Patience is a number of seconds from {PATIENCE_MIN:g} to {PATIENCE_MAX:g}.")
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
    return (stop, switch, None if patience is None else float(patience),  # type: ignore[return-value]
            sounds, cues,  # type: ignore[return-value]
            None if volume is None else int(round(volume)))  # type: ignore[arg-type]


def change(stop: object = None, switch: object = None, patience: object = None,
           sounds: object = None, cues: object = None, volume: object = None,
           user: Optional[str] = None) -> dict:
    """Save any of the settings for `user` and answer the settings now in
    force, as `read` gives them. Starts, stops, submits and discards
    nothing: they apply from the next stop or page switch (the cues from
    the next one due). A change to the cues is merged into what he chose
    before, and refused when it would put the pause cues out of their
    order (tentative, deciding, likely)."""
    stop, switch, patience, sounds, cues, volume = validate(stop, switch, patience, sounds,
                                                            cues, volume)
    user = user or user_key()
    now = read(user)
    row = _row(user)
    chosen = _saved_cues(row["cue_settings"]) if row is not None else {}
    for name, choice in (cues or {}).items():
        chosen[name] = {**chosen.get(name, {}), **choice}
    if cues and not _in_order({name: {**CUE_DEFAULTS[name], **chosen.get(name, {})}
                               for name in PAUSE_CUES}):
        raise Invalid("invalid-cue-timing",
                      "The pause cues play in order, tentative, then deciding, then likely: "
                      "each one's seconds before the end of his turn must be more than the "
                      "next one's.")
    new = {"stop": stop or now["stop"], "switch": switch or now["switch"],
           "patience": patience,        # None: keep what is saved
           "sounds": None if sounds is None else int(sounds),
           "cues": json.dumps(chosen, sort_keys=True) if cues else None,
           "volume": volume}
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, patience_sec, "
                "capture_sounds, cue_settings, cue_volume, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_key) DO "
                "UPDATE SET stop_mode = excluded.stop_mode, switch_mode = excluded.switch_mode, "
                "patience_sec = COALESCE(excluded.patience_sec, voice_input_settings.patience_sec), "
                "capture_sounds = COALESCE(excluded.capture_sounds, "
                "voice_input_settings.capture_sounds), "
                "cue_settings = COALESCE(excluded.cue_settings, voice_input_settings.cue_settings), "
                "cue_volume = COALESCE(excluded.cue_volume, voice_input_settings.cue_volume), "
                "updated_at = excluded.updated_at",
                (user, new["stop"], new["switch"], new["patience"], new["sounds"], new["cues"],
                 new["volume"], time.time()))
    except sqlite3.Error as e:
        log.warning("voice input settings could not be saved (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be saved.") from e
    log.info("voice input settings for %s: stop=%s switch=%s patience=%s sounds=%s cues=%s "
             "volume=%s", user, new["stop"], new["switch"], new["patience"], new["sounds"],
             new["cues"], new["volume"])
    saved = read(user)
    if _publish is not None:
        _publish({"type": FRAME})
    return saved


def patience(user: Optional[str] = None) -> float:
    """His patience in seconds: the saved value, or the current constant
    when there is none or the store cannot be read."""
    try:
        return read(user)["patience"]
    except Unreadable:
        return END_OF_TURN_SEC


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
                      patience=body.get("patience"), sounds=body.get("sounds"),
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
