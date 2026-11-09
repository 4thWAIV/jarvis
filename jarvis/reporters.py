"""Who reported something to JARVIS, as the way it came in proves it.

An onboarded agent signs its requests with its own key (agent_keys.py):
the request comes in as that key's principal, `agent:<session id>`, and
that session is the only one a report on it may claim as its own. A
work-item key (`workkey:<fingerprint>`) reports for the agent that issued
it. Before agents had keys they carried access keys, which admit nothing
any more; their records (`key:<label>`) stay readable, the label meaning
the listed agent (or agents, two may share a name) the agent directory
names by it (`agent_directory.same_name`), else shown as itself.

What is recorded is the principal (or `key:<label>`); the name is worked
out each time it is shown, so a renamed agent is shown by its one name.
Old `key:<label>` records stay readable, and an onboarded agent whose
access-key label was adopted (agent_key_store.onboard's `label`) still
speaks as that label, so the decisions it asked before stay its own.
Nothing here reads a request's words: only how it was admitted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from jarvis import agent_directory, web_auth

# How a recorded reporter that came in on an access key is kept: the
# decision's asker has always been kept so (work_api.asker).
KEY_PREFIX = "key:"
AGENT_PREFIX = "agent:"
WORK_KEY_PREFIX = "workkey:"


@dataclass(frozen=True)
class Reporter:
    label: str                  # the access key's label, or "" for an agent key
    name: str                   # shown: the agent's one name, else the label
    sessions: tuple[str, ...]   # the sessions it owns
    kept: str = ""              # how it is recorded
    aliases: tuple[str, ...] = ()   # every way its earlier records may name it

    def owns(self, session_id: str) -> bool:
        return bool(session_id) and session_id in self.sessions

    def is_(self, recorded_as: str) -> bool:
        """Whether a record's reporter (an asker) is this one."""
        return bool(recorded_as) and recorded_as in (self.kept, *self.aliases)


def of_label(label: str) -> Reporter:
    """The reporter an access key's label is: its listed agent, else the
    label itself."""
    label = str(label or "")
    found = agent_directory.named(label)
    kept = f"{KEY_PREFIX}{label}"
    return Reporter(label, found[0].name if found else label,
                    tuple(a.session_id for a in found), kept, (kept,))


def of_principal(principal: str) -> Optional[Reporter]:
    """The reporter an agent key's principal is: its session's agent, with
    the access-key label it was onboarded under as an alias; a work-item
    key reports for the agent that issued it."""
    from jarvis import agent_key_store
    principal = str(principal or "")
    if principal.startswith(AGENT_PREFIX):
        session = principal[len(AGENT_PREFIX):]
        try:
            key = agent_key_store.for_session(session)
        except Exception:
            key = None
        label = key.label if key is not None and key.state == agent_key_store.ONBOARDED else ""
        name = agent_directory.names_by_session().get(session, "")
        return Reporter(label, name or session, (session,), principal,
                        (principal,) + ((f"{KEY_PREFIX}{label}",) if label else ()))
    if principal.startswith(WORK_KEY_PREFIX):
        try:
            key = agent_key_store.get(principal[len(WORK_KEY_PREFIX):])
        except Exception:
            key = None
        issuer = of_principal(key.issued_by) if key is not None and key.issued_by else None
        return Reporter("", issuer.name if issuer else "an item key", (), principal,
                        (principal,))
    return None


def of_kept(kept: str) -> Optional[Reporter]:
    """The reporter a record names, or None for one no agent is proven by."""
    kept = str(kept or "")
    if kept.startswith(KEY_PREFIX):
        return of_label(kept[len(KEY_PREFIX):])
    return of_principal(kept)


def of_scope(scope: dict) -> Optional[Reporter]:
    """The reporter a request came in as, or None when no agent's key
    admitted it (the page, the tool token)."""
    principal = web_auth.principal(scope)
    return of_principal(principal) if principal else None


def recorded(scope: dict) -> str:
    """How a request's reporter is written down: the agent key's principal,
    else how it was admitted (the tool token, the page)."""
    return web_auth.principal(scope) or str(scope.get(web_auth.ADMITTED) or "")


def legacy_labels() -> list[str]:
    """The labels in the old access keys' record (`access-keys.json`),
    read only, to adopt one onto the agent it names when it is onboarded.
    Nothing is admitted by them. Never raises."""
    import json
    from jarvis import data_paths
    try:
        data = json.loads(data_paths.access_keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    keys = data.get("keys") if isinstance(data, dict) else None
    return [str(k.get("label")) for k in keys or [] if isinstance(k, dict) and k.get("label")]


def aliases(scope: dict) -> tuple[str, ...]:
    """Every way the request's reporter may be named in earlier records."""
    who = of_scope(scope)
    return (who.kept, *who.aliases) if who is not None else (recorded(scope),)


# A listing names the same few reporters many times over: each one's name
# is read from the directory at most once per this long.
SHOWN_FOR_SEC = 2.0
_shown: dict[str, tuple[float, str]] = {}


def shown(kept: str) -> str:
    """A recorded reporter as it is shown: an agent by its one name, else
    an access key's label; anything else (the tool token, the page,
    nothing) as nothing, since no agent is proven by it."""
    kept = str(kept or "")
    if not kept.startswith((KEY_PREFIX, AGENT_PREFIX, WORK_KEY_PREFIX)):
        return ""
    now = time.monotonic()
    at, name = _shown.get(kept, (0.0, ""))
    if not name or now - at > SHOWN_FOR_SEC:
        who = of_kept(kept)
        name = who.name if who is not None else ""
        _shown[kept] = (now, name)
    return name
