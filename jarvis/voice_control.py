"""
voice_control.py — the voice control contract's shared pieces on the server
(docs/planning/quiet-conversation/contract.md): for now, the two voice input
settings and the one service the page's route and the brain's tools call.

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

import logging
import sqlite3
import time
from contextlib import closing
from typing import Callable, Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from jarvis import run_store

log = logging.getLogger("jarvis.voice_control")

STOP_MODES = ("keep", "discard")
SWITCH_MODES = ("continue", "stop")
# What a user who has chosen nothing gets. Provisional: the defaults are
# assessed separately, during validation.
DEFAULTS = {"stop": "keep", "switch": "stop"}
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


def read(user: Optional[str] = None) -> dict:
    """The settings in force for `user` (the user now, by default):
    {stop, switch, saved, scope}. Raises Unreadable if the store cannot be
    read, and treats a saved row it does not understand as unreadable too."""
    user = user or user_key()
    try:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT stop_mode, switch_mode FROM voice_input_settings "
                               "WHERE user_key = ?", (user,)).fetchone()
    except (sqlite3.Error, RuntimeError) as e:
        log.warning("voice input settings could not be read (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be read.") from e
    if row is None:
        stop, switch, saved = DEFAULTS["stop"], DEFAULTS["switch"], False
    else:
        stop, switch, saved = row["stop_mode"], row["switch_mode"], True
        if stop not in STOP_MODES or switch not in SWITCH_MODES:
            raise Unreadable("Your saved voice input settings are not ones JARVIS knows.")
    return {"stop": stop, "switch": switch, "saved": saved,
            "scope": "local" if user == LOCAL_OWNER else "member"}


def validate(stop: object = None, switch: object = None) -> tuple[Optional[str], Optional[str]]:
    """The change asked for, checked: each given value must be one of its
    setting's, and at least one must be given."""
    if stop is None and switch is None:
        raise Invalid("nothing-to-change", "Say which setting to change: stop, switch or both.")
    if stop is not None and stop not in STOP_MODES:
        raise Invalid("invalid-stop", "When I stop the microphone is keep or discard.")
    if switch is not None and switch not in SWITCH_MODES:
        raise Invalid("invalid-switch", "When I switch away from JARVIS is continue or stop.")
    return stop, switch  # type: ignore[return-value]


def change(stop: object = None, switch: object = None, user: Optional[str] = None) -> dict:
    """Save one or both settings for `user` and answer the settings now in
    force, as `read` gives them. Starts, stops, submits and discards
    nothing: they apply from the next stop or page switch."""
    stop, switch = validate(stop, switch)
    user = user or user_key()
    now = read(user)
    new = {"stop": stop or now["stop"], "switch": switch or now["switch"]}
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_key) DO UPDATE SET "
                "stop_mode = excluded.stop_mode, switch_mode = excluded.switch_mode, "
                "updated_at = excluded.updated_at",
                (user, new["stop"], new["switch"], time.time()))
    except sqlite3.Error as e:
        log.warning("voice input settings could not be saved (%s)", type(e).__name__)
        raise Unreadable("Your voice input settings could not be saved.") from e
    log.info("voice input settings for %s: stop=%s switch=%s", user, new["stop"], new["switch"])
    saved = read(user)
    if _publish is not None:
        _publish({"type": FRAME})
    return saved


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
        return change(stop=body.get("stop"), switch=body.get("switch"))
    except Invalid as e:
        return JSONResponse(status_code=400, content=e.to_json())
    except Unreadable as e:
        return _unreadable(e)


def mount(app: FastAPI, publish: Optional[Publish] = None) -> None:
    global _publish
    _publish = publish
    app.add_api_route(PATH, api_read, methods=["GET"])
    app.add_api_route(PATH, api_change, methods=["POST"])
