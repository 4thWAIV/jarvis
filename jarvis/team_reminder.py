"""team_reminder.py — saying once that this JARVIS is not signed in.

A JARVIS that is not signed in to a team server works as before; it only
reminds Tim. The page shows a reminder for as long as it is not signed in
(TeamSignInReminder.tsx); this is the spoken half: once per process, after
startup has settled (SETTLE_SEC, so a saved connection has had its first
status read), one line through the ordinary notice path (`say`, the
server's `_say_notice`: the conversation notice, the brain's note and the
speech scheduler at the normal announcement priority, which waits for Tim
to finish). Signed in by then, nothing is said; nothing is said again in
this process whatever happens later.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

# How long after startup the connection is looked at.
SETTLE_SEC = 30.0
LINE = ("You're not signed in to the team server, sir; messages between people "
        "are off until you are.")
NOTE = ("Since your last turn you told him this JARVIS is not signed in to a team "
        "server, so messages between people and the Network view are off until he "
        "signs in (Sign in on the reminder at the top of the page, or Settings, Team). "
        "Everything else works as before.")
WHAT = "team sign-in"

# The notice path: (line, note for the brain, what it is about for the log).
Say = Callable[[str, str, str], Awaitable[None]]

_said = False


async def remind_once(say: Say, signed_in: Callable[[], bool],
                      settle_sec: Optional[float] = None) -> None:
    """Wait for startup to settle (SETTLE_SEC unless given), then say LINE
    unless this JARVIS is signed in or it has already been said in this
    process."""
    global _said
    await asyncio.sleep(SETTLE_SEC if settle_sec is None else settle_sec)
    if _said or signed_in():
        return
    _said = True
    await say(LINE, NOTE, WHAT)
