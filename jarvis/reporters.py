"""Who reported something to JARVIS over an access key, as the key proves it.

An access key's label means an agent: the listed agent (or agents, two may
share a name) the agent directory names by it, said the same way
(`agent_directory.same_name`). Its sessions are the ones the key owns, the
only sessions a report on that key may claim as its own. A label that names
no listed agent is shown as itself, proven by the key all the same, and owns
no session.

The label is what is recorded (a workflow's events, a work report); the
name is worked out each time it is shown, so a renamed agent is shown by its
one name. Nothing here reads a request's words: only how it was admitted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from jarvis import agent_directory, web_auth

# How a recorded reporter that came in on an access key is kept: the
# decision's asker has always been kept so (work_api.asker).
KEY_PREFIX = "key:"


@dataclass(frozen=True)
class Reporter:
    label: str                  # the access key's label
    name: str                   # shown: the agent's one name, else the label
    sessions: tuple[str, ...]   # the listed agents' sessions the key owns

    def owns(self, session_id: str) -> bool:
        return bool(session_id) and session_id in self.sessions


def of_label(label: str) -> Reporter:
    """The reporter an access key's label is: its listed agent, else the
    label itself."""
    label = str(label or "")
    found = agent_directory.named(label)
    return Reporter(label, found[0].name if found else label,
                    tuple(a.session_id for a in found))


def of_scope(scope: dict) -> Optional[Reporter]:
    """The reporter a request came in as, or None when no access key
    admitted it (the page, the tool token)."""
    label = web_auth.access_key_label(scope)
    return None if label is None else of_label(label)


def recorded(scope: dict) -> str:
    """How a request's reporter is written down: `key:<label>` for an
    access key, else how it was admitted (the tool token, the page)."""
    label = web_auth.access_key_label(scope)
    return f"{KEY_PREFIX}{label}" if label is not None else str(scope.get(web_auth.ADMITTED) or "")


# A listing names the same few reporters many times over: each label's
# name is read from the directory at most once per this long.
SHOWN_FOR_SEC = 2.0
_shown: dict[str, tuple[float, str]] = {}


def shown(kept: str) -> str:
    """A recorded reporter as it is shown: a key's agent by its one name,
    else its label; anything else (the tool token, the page, nothing) as
    nothing, since no agent is proven by it."""
    kept = str(kept or "")
    if not kept.startswith(KEY_PREFIX):
        return ""
    now = time.monotonic()
    at, name = _shown.get(kept, (0.0, ""))
    if not name or now - at > SHOWN_FOR_SEC:
        name = of_label(kept[len(KEY_PREFIX):]).name
        _shown[kept] = (now, name)
    return name
