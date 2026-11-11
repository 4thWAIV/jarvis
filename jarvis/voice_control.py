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

The capture cues (`cues`, `volume`) are six quiet sounds, each on or off
and each one tone of the fixed presets (TONES; the page holds their
notes, voice.ts):

  start      listening really started
  stop       listening really stopped, for any reason
  heard      he began speaking: a new turn started gathering (the first
             word accepted as his; never echo, never again for each word)
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

His speech mute (`speech_muted`, default off) is the speaker's: muted,
JARVIS still answers in text but says nothing. The speech scheduler makes
no audio for anything begun while it is on (`speech_muted`, read as each
utterance starts, so nothing is synthesized), and turning it on stops what
is sounding at once (the server's publish hook). The page's speaker
toggles it, its headset turns it on with Mic off and off with Mic on, and
the brain may only turn it on (`mute_speech`, `turn_audio_off`): unmuting is
his act on the page.

They are per user, on this JARVIS: one `settings` document a user,
`/people/<github id or local>/settings/voice-input.settings` (`path_of`),
keeping the column names of the table it replaced: the team member signed
in, by GitHub id, or else this JARVIS's local owner. Never a team-wide choice. A user who
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
import re
import threading
import time
from typing import Callable, Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from jarvis import knowledge_move as move, records
from jarvis.turn_controller import INTERVAL_DEFAULTS, PAUSE_CUES, PAUSE_STAGES, pause_schedule

log = logging.getLogger("jarvis.voice_control")

STOP_MODES = ("keep", "discard")
SWITCH_MODES = ("continue", "stop")
# What a user who has chosen nothing gets. Provisional: the defaults are
# assessed separately, during validation.
DEFAULTS = {"stop": "keep", "switch": "stop", "sounds": True}
# His speech mute until he chooses: speech on.
SPEECH_MUTED_DEFAULT = False
# The capture cues: the two that mark listening, the pause cues that mark
# the stages of a pause in his turn (in the order they play), and the quiet
# presets each may play. The pause cues, the pause stages (the pause cues,
# then the send) and their default intervals are turn_controller's.
LISTEN_CUES = ("start", "stop")
HEARD = "heard"
CUES = LISTEN_CUES + (HEARD,) + PAUSE_CUES
TONES = ("rise", "fall", "pulse", "low", "double", "resolve")
# A pause stage's interval after the stage before, in seconds.
INTERVAL_MIN, INTERVAL_MAX = 0.25, 5.0
# The cue volume, in percent of the page's quiet ceiling.
VOLUME_MIN, VOLUME_MAX = 5, 100
CUE_DEFAULTS = {
    "start": {"on": True, "tone": "rise"},
    "stop": {"on": True, "tone": "fall"},
    "heard": {"on": True, "tone": "double"},
    "tentative": {"on": True, "tone": "pulse"},
    "deciding": {"on": True, "tone": "low"},
    "likely": {"on": True, "tone": "resolve"},
}
VOLUME_DEFAULT = 50
LOCAL_OWNER = "local"
PATH = "/api/settings/voice-input"
# The hint every open page gets when the settings change (/ws/sessions).
FRAME = "voice_input_settings"

ROOT = "/people"
NAME = "voice-input.settings"
COLUMNS = ("user_key", "stop_mode", "switch_mode", "updated_at", "patience_sec", "capture_sounds",
           "cue_settings", "cue_volume", "pause_intervals", "speech_muted")
# The voice path never waits on the engine (`_held_row`): a user's settings
# not held yet are the defaults while a read in the background fetches them,
# tried again at most this often while the engine does not answer.
RETRY_SEC = 5.0

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


def path_of(user: str) -> str:
    """Where a user's settings are: under the person, by GitHub id, or the
    local owner's (any other key by its segment; one all digits is marked
    with `=` so it never names a member)."""
    member = re.fullmatch(r"github:(\d+)", user)
    who = member.group(1) if member else records.segment(("=" + user) if user.isdigit() else user)
    return f"{ROOT}/{who}/settings/{NAME}"


class _SettingsMapping:
    """voice_input_settings, one row a user, as one document a user."""
    name = "voice-input-settings"
    tables = ("voice_input_settings",)
    under = (ROOT,)
    history = False

    def documents(self, conn):
        for row in move.table_rows(conn, "voice_input_settings"):
            yield move.Doc(path_of(str(row.cells["user_key"])), move.row_data(row))

    def rows(self, docs) -> dict:
        found = [move.data_row(d.data, COLUMNS) for d in docs if d.path.endswith(f"/settings/{NAME}")]
        return {"voice_input_settings": move.numbered(found)}


MAPPINGS = [_SettingsMapping()]

# What this process holds of each user's saved settings (None: none saved),
# by engine (records.engine_key): read once, refreshed by every read and
# every change through this module.
_held: dict[str, dict[str, Optional[dict]]] = {}
_holding = threading.Lock()
_fetching: dict[tuple[str, str], float] = {}


def _hold(key: str, user: str, data: Optional[dict]) -> None:
    with _holding:
        _held.setdefault(key, {})[user] = None if data is None else dict(data)


def forget() -> None:
    """Drop what this process holds (a test standing for a JARVIS started
    again)."""
    with _holding:
        _held.clear()
        _fetching.clear()


def _row(user: str) -> Optional[dict]:
    """The user's saved settings, read from the engine (and held)."""
    key = records.engine_key()
    try:
        rec = records.get(path_of(user))
    except Exception as e:
        log.warning("voice input settings could not be read (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be read.") from e
    data = None if rec is None else rec.data
    _hold(key, user, data)
    return data


def _held_row(user: str) -> Optional[dict]:
    """The user's saved settings as this process holds them, for the voice
    path, which never waits on the engine: read here when the engine is in
    this process (tests, an isolated JARVIS), else fetched in the background
    while this raises Unreadable (the defaults meanwhile), at most every
    RETRY_SEC."""
    key = records.engine_key()
    with _holding:
        held = _held.get(key, {})
        if user in held:
            return held[user]
    if records.in_process():
        return _row(user)
    _fetch_later(key, user)
    raise Unreadable("Your voice input settings are not read yet.")


def _fetch_later(key: str, user: str) -> None:
    with _holding:
        if time.monotonic() - _fetching.get((key, user), -RETRY_SEC) < RETRY_SEC:
            return
        _fetching[(key, user)] = time.monotonic()

    def fetch() -> None:
        try:
            if records.engine_key() == key:
                _row(user)
        except Unreadable:
            pass                       # tried again by the next miss after RETRY_SEC
    threading.Thread(target=fetch, name="voice-settings-read", daemon=True).start()


def _answer(user: str, stop: str, switch: str, saved: bool, intervals: dict, sounds: bool,
            cues: dict, volume: int, speech_muted: bool) -> dict:
    schedule = dict(pause_schedule(intervals))
    return {"stop": stop, "switch": switch, "saved": saved, "intervals": intervals,
            "schedule": schedule, "patience": schedule[PAUSE_STAGES[-1]],
            "sounds": sounds, "cues": cues, "volume": volume, "speech_muted": speech_muted,
            "scope": "local" if user == LOCAL_OWNER else "member"}


def defaults(user: str = LOCAL_OWNER) -> dict:
    """What `read` answers for a user who has chosen nothing."""
    return _answer(user, DEFAULTS["stop"], DEFAULTS["switch"], False, _intervals_in_force(None),
                   DEFAULTS["sounds"], _cues_in_force(None), VOLUME_DEFAULT, SPEECH_MUTED_DEFAULT)


def read(user: Optional[str] = None) -> dict:
    """The settings in force for `user` (the user now, by default):
    {stop, switch, intervals, schedule, patience, sounds, cues, volume,
    speech_muted, saved, scope}: `schedule` is each pause stage's seconds after he
    stopped, `patience` the send's. Read from the engine (and held for the
    voice path). Raises Unreadable if the store cannot be read, and treats
    a saved record it does not understand as unreadable too. A saved
    interval, cue or volume that is not one JARVIS knows is the default
    instead."""
    user = user or user_key()
    return _settings(user, _row(user))


def _settings(user: str, row: Optional[dict]) -> dict:
    if row is None:
        return defaults(user)
    stop, switch = row.get("stop_mode"), row.get("switch_mode")
    if stop not in STOP_MODES or switch not in SWITCH_MODES:
        raise Unreadable("Your saved voice input settings are not ones JARVIS knows.")
    sounds = DEFAULTS["sounds"] if row.get("capture_sounds") is None else bool(row["capture_sounds"])
    volume = int(row["cue_volume"]) if _volume_ok(row.get("cue_volume")) else VOLUME_DEFAULT
    muted = SPEECH_MUTED_DEFAULT if row.get("speech_muted") is None else bool(row["speech_muted"])
    return _answer(user, stop, switch, True, _intervals_in_force(_saved(row.get("pause_intervals"))),
                   sounds, _cues_in_force(_saved_cues(row.get("cue_settings"))), volume, muted)


def read_or_defaults(user: Optional[str] = None) -> dict:
    """The settings in force as this process holds them, or the defaults
    while it holds none it can use: for the voice path, which never stops
    for them and never waits on the engine."""
    try:
        user = user or user_key()
        return _settings(user, _held_row(user))
    except Unreadable:
        return defaults()


def speech_muted(user: Optional[str] = None) -> bool:
    """Whether his speech mute is on: for the speech path, which reads it as
    each utterance starts and never stops for an unreadable store (speech
    on, the default)."""
    return bool(read_or_defaults(user)["speech_muted"])


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
             sounds: object = None, cues: object = None, volume: object = None,
             speech_muted: object = None
             ) -> tuple[Optional[str], Optional[str], Optional[dict], Optional[bool],
                        Optional[dict], Optional[int], Optional[bool]]:
    """The change asked for, checked: each given value must be one of its
    setting's, and at least one must be given."""
    if all(v is None for v in (stop, switch, intervals, sounds, cues, volume, speech_muted)):
        raise Invalid("nothing-to-change",
                      "Say which setting to change: stop, switch, intervals, sounds, cues, "
                      "volume or speech_muted.")
    if speech_muted is not None and not isinstance(speech_muted, bool):
        raise Invalid("invalid-speech-muted", "Speech is muted (true) or not (false).")
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
            None if volume is None else int(round(volume)),  # type: ignore[arg-type]
            speech_muted)  # type: ignore[return-value]


def change(stop: object = None, switch: object = None, intervals: object = None,
           sounds: object = None, cues: object = None, volume: object = None,
           speech_muted: object = None, user: Optional[str] = None) -> dict:
    """Save any of the settings for `user` and answer the settings now in
    force, as `read` gives them. Starts, stops, submits and discards
    nothing: they apply from the next stop or page switch (the cues and
    intervals from the next pause). A change to the cues or the intervals
    is merged into what he chose before; any intervals keep the stages in
    order, since each is measured from the stage before."""
    stop, switch, intervals, sounds, cues, volume, speech_muted = validate(
        stop, switch, intervals, sounds, cues, volume, speech_muted)
    user = user or user_key()
    now = read(user)
    new = {"stop_mode": stop or now["stop"], "switch_mode": switch or now["switch"],
           "capture_sounds": None if sounds is None else int(sounds), "cue_volume": volume,
           "speech_muted": None if speech_muted is None else int(speech_muted)}

    def save(row: Optional[dict]) -> dict:
        row = dict(row or {c: None for c in COLUMNS}, user_key=user, updated_at=time.time())
        chosen = _saved_cues(row.get("cue_settings"))
        for name, choice in (cues or {}).items():
            chosen[name] = {**chosen.get(name, {}), **choice}
        waits = _saved(row.get("pause_intervals"))
        waits.update(intervals or {})
        given = {**new, "pause_intervals": json.dumps(waits, sort_keys=True) if intervals else None,
                 "cue_settings": json.dumps(chosen, sort_keys=True) if cues else None}
        row.update({k: v for k, v in given.items() if v is not None})
        return row
    try:
        row = records.update(path_of(user), save, by=records.TIM, change="voice-input-settings",
                             summary="his voice input settings changed")
    except Exception as e:
        log.warning("voice input settings could not be saved (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be saved.") from e
    _hold(records.engine_key(), user, row)
    log.info("voice input settings for %s: stop=%s switch=%s intervals=%s sounds=%s cues=%s "
             "volume=%s speech_muted=%s", user, row["stop_mode"], row["switch_mode"],
             row["pause_intervals"], row["capture_sounds"], row["cue_settings"], row["cue_volume"],
             row["speech_muted"])
    saved = _settings(user, row)
    if _publish is not None:
        _publish({"type": FRAME})
    return saved


def patience(user: Optional[str] = None) -> float:
    """His patience in seconds: the total of his pause intervals as this
    process holds them, or the defaults' total (turn_controller's
    DEFAULT_PATIENCE) while it
    holds none."""
    return read_or_defaults(user)["patience"]


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
                      cues=body.get("cues"), volume=body.get("volume"),
                      speech_muted=body.get("speech_muted"))
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
