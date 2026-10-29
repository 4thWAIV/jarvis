"""human_activity.py — when a person last used this JARVIS.

What the node update's `activity` carries to the team server: a sequence
number, how long ago, and where it came from. Never what was said, typed or
clicked.

Two ways in, and only these:

  gui, typed   POST /api/team/activity from the voice page's one activity
               collector (humanActivity.ts), throttled there. Refused unless
               it carries the page's own Origin: the tool token and access
               keys are what agents carry, and their calls are not a person.
  speech       the voice socket, once speech.user_final has accepted words
               as his (not echo); stop and cancel words count, JARVIS's own
               speech coming back through the microphone does not.

Nothing else counts: not page loads or refreshes, timers, agents, usage
refreshes or anything JARVIS says. These are input the application observed,
not proof a person was there.

The age sent is measured on the monotonic clock from the moment it was
observed in this process. The last observation is kept on disk
(data_paths.human_activity_path) so the sequence number keeps rising across
restarts, and the team server never takes a replay for a new interaction;
an observation from an earlier run is never sent again with a recomputed
age (the team server already holds its time).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from jarvis import data_paths, web_auth

log = logging.getLogger("jarvis.human_activity")

Source = Literal["gui", "speech", "typed"]
SOURCES = ("gui", "speech", "typed")
# HumanActivity.age_ms, le=86_400_000: older than a day is not sent.
MAX_AGE_MS = 86_400_000
ROUTE = "/api/team/activity"


class HumanActivity:
    """The latest observation. One per backend (`recorder`)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._wall = wall
        self._seq: Optional[int] = None
        self._observed: Optional[tuple[int, float, str]] = None   # seq, monotonic, source

    def _last_seq(self) -> int:
        """The last sequence number used, from disk on first need: -1 when
        none has ever been. An unreadable record restarts nothing but the
        count's floor, so it is logged."""
        if self._seq is None:
            self._seq = -1
            try:
                body = json.loads(data_paths.human_activity_path().read_text())
                seq = body.get("activity_seq") if isinstance(body, dict) else None
                if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                    self._seq = seq
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                log.warning(f"human_activity: unreadable record ignored ({type(e).__name__})")
        return self._seq

    def observe(self, source: Source) -> int:
        """A person used JARVIS just now. Returns the new sequence number."""
        if source not in SOURCES:
            raise ValueError(f"unknown activity source {source!r}")
        seq = self._last_seq() + 1
        self._seq = seq
        self._observed = (seq, self._clock(), source)
        data_paths._write_atomically(data_paths.human_activity_path(), json.dumps(
            {"activity_seq": seq, "observed_at": self._wall(), "source": source}) + "\n")
        return seq

    def current(self) -> Optional[dict]:
        """The node update's `activity`, or None: nothing observed in this
        run, or observed more than a day ago."""
        if self._observed is None:
            return None
        seq, at, source = self._observed
        age_ms = int(max(0.0, self._clock() - at) * 1000)
        if age_ms > MAX_AGE_MS:
            return None
        return {"activity_seq": seq, "age_ms": age_ms, "source": source}


recorder = HumanActivity()


# -- the page's route ----------------------------------------------------------

class ActivityBody(BaseModel):
    """Where it came from, and nothing else: any other field is refused, so
    no content can ride along."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["gui", "typed"]


async def post_activity(request: Request, body: ActivityBody):
    if not web_auth.origin_allowed(request.headers.get("origin")):
        return JSONResponse(status_code=403, content={
            "error": "Refused: activity is taken only from a page JARVIS serves."})
    recorder.observe(body.source)
    return Response(status_code=204)


def mount(app: FastAPI) -> None:
    app.add_api_route(ROUTE, post_activity, methods=["POST"], status_code=204)
