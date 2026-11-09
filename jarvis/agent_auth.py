"""Checking a signed request: the gate's half of agent keys.

A request an agent signs (agent_keys.request_header) names its key's
fingerprint, when it was made, a nonce, the SHA-256 of its body and the
signature over all of that with its method, path, query and Host. `check`
says what it proves:

  - the signature is good for a key JARVIS holds (`signed_key`), made
    within agent_keys.FRESH_SEC of now, with a nonce not seen in the last
    NONCE_KEEP_SEC (kept in memory; the 60-second window covers a restart);
  - the key is onboarded and live (`principal`), else why not (`refused`);
  - the body's bytes are the ones signed: web_auth reads the whole body
    first and checks it here before the route sees any of it.

Rights are judged by agent_rights.allows, by web_auth, once this has said
who it is. Blocking (one ssh-keygen call): called off the event loop.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from jarvis import agent_key_store, agent_keys

NONCE_KEEP_SEC = 120.0
_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()

UNKNOWN = "That request is signed by a key JARVIS does not hold."
STALE = "That request's signature is too old or from the future; sign it again."
REPLAYED = "That request was already received once."
BAD = "That request's signature does not check."
BODY = "That request's body is not the one it signed."
MALFORMED = "That request's JARVIS-Signature header is malformed."


@dataclass(frozen=True)
class Checked:
    """What a signature proved: `key` when it checked for a key JARVIS
    holds; `principal` only when that key is onboarded and live."""
    key: Optional[agent_key_store.AgentKey] = None
    principal: str = ""
    refused: str = ""


def _fresh_nonce(fingerprint: str, nonce: str, now: float) -> bool:
    with _nonce_lock:
        for k, at in list(_nonces.items()):
            if now - at > NONCE_KEEP_SEC:
                del _nonces[k]
        k = f"{fingerprint} {nonce}"
        if k in _nonces:
            return False
        _nonces[k] = now
        return True


def check(authorization: str, method: str, path: str, query: str, host: str, body: bytes,
          now: Optional[float] = None) -> Checked:
    """What the signed request proves (see the module's words). Never
    raises."""
    now = time.time() if now is None else now
    fields = agent_keys.parse_header(authorization)
    if fields is None:
        return Checked(refused=MALFORMED)
    if abs(now - fields["created"]) > agent_keys.FRESH_SEC:
        return Checked(refused=STALE)
    if agent_keys.body_digest(body) != fields["body"]:
        return Checked(refused=BODY)
    try:
        key = agent_key_store.get(fields["key"])
    except Exception:
        key = None
    if key is None:
        return Checked(refused=UNKNOWN)
    text = agent_keys.request_text(method, path, query, host or "", fields["created"],
                                   fields["nonce"], fields["body"])
    if not agent_keys.verify(key.public_key, agent_keys.REQUEST, text, fields["sig"]):
        return Checked(refused=BAD)
    if not _fresh_nonce(key.fingerprint, fields["nonce"], now):
        return Checked(refused=REPLAYED)
    if not key.admitted(now):
        return Checked(key=key, refused=not_onboarded(key))
    return Checked(key=key, principal=key.principal)


def not_onboarded(key: agent_key_store.AgentKey) -> str:
    """Why a key that checks is not admitted, in words for the agent."""
    if key.state == agent_key_store.REVOKED:
        return "That key was revoked; JARVIS no longer takes anything signed with it."
    if key.state == agent_key_store.PENDING:
        return ("That key is not proven yet: JARVIS looks for your submit command in your own "
                "transcript for ten minutes after you submit it.")
    if key.state == agent_key_store.WAITING:
        return "That key waits for Tim: it is onboarded when he switches your agent on."
    return "That key has expired."
