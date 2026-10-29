"""Post a message into a running Claude Code session.

The wire format is one JSON line on the session's inbox Unix socket. It carries
PEER authority, not the user's: the session receives it as a new turn (or
queues it between tool calls if busy), but it cannot dismiss a permission
prompt or a modal dialog. On this machine those are the two `waitingFor`
reasons that actually occur, so the caller must check before promising a fix.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

SENT = "sent"          # the bytes left over the socket — NOT that the target
                        # session accepted or even received them; no reply is
                        # ever read back to confirm that
NOT_LIVE = "not_live"
REFUSED = "refused"
FAILED = "failed"


def failed(reason: str) -> str:
    """`failed` with the reason the channel gave, as it is recorded in a
    message's `failure`: "failed: <reason>". Callers still compare against
    the plain outcomes; `outcome_code` reads one back."""
    reason = " ".join((reason or "").split())[:300]
    return f"{FAILED}: {reason}" if reason else FAILED


def outcome_code(outcome: str | None) -> str | None:
    """The outcome without any reason `failed` carries."""
    return outcome.split(":", 1)[0] if outcome else outcome

# The first line of every message JARVIS sends into a session, whichever
# provider runs it. Without it the session saw only "another Claude session",
# answered by messaging whichever session it guessed, and once recorded an
# approval as coming from a session that had never written to it. The answer
# belongs in its own transcript, where JARVIS reads it.
STEER_SIGNATURE = (
    "[From JARVIS, Tim's voice assistant, relaying Tim. Answer by ending your "
    "turn with your full reply; JARVIS reads it from your transcript and "
    "tells Tim. Do not send your answer to another session.]\n\n")


def message_body(text: str) -> str:
    """What a message says after the signature: Tim's own words, which is
    what finds the message again in the session's transcript (each provider's
    `track`). The signature is the same on every message, so it identifies
    none of them."""
    if text.startswith(STEER_SIGNATURE):
        text = text[len(STEER_SIGNATURE):]
    return text.strip()


# Claude Code's inbox takes a message's place in its own queue from this
# field (its inbox handler reads `priority`, defaulting to "next": taken in
# between two tool calls of the turn under way). "now" preempts that turn:
# the session stops what it is doing and takes the message at once. Read
# from Claude Code 2.1.309's inbox handler.
INTERRUPT_PRIORITY = "now"


def post_to_session(socket_path: str | None, prompt: str,
                    timeout: float = 5.0, interrupt: bool = False) -> str:
    """Deliver one prompt. Returns `sent`, `not_live`, `refused`, or `failed`.

    A missing socket file and a stale one left by a dead process are both
    `not_live`: from the user's point of view there is nothing to talk to.
    """
    if not prompt or not prompt.strip():
        return REFUSED
    if not socket_path or not Path(socket_path).exists():
        return NOT_LIVE

    lines = []
    # What is actually known about this token, recorded so nobody "fixes" it
    # into something it cannot be: it is optional on macOS (the target may
    # require none at all); we can only ever send OUR OWN — there is no way
    # to look up another process's; tokens observably differ between
    # sessions (3 distinct values seen across 7 live sessions on one
    # machine, and a server started from a plain terminal has none). If the
    # target validates a token and ours does not match — or it has none and
    # we send one — the send below can fail silently from our side: a
    # successful `sendall` proves the bytes left this process, not that the
    # target accepted them. See the SENT docstring above and the wording at
    # the call site in server.py's `_perform_staged_steers`.
    token = os.getenv("CLAUDE_CODE_MESSAGING_TOKEN", "")
    if token:
        lines.append(json.dumps({"type": "auth", "token": token}))
    line = {"type": "user", "message": {"role": "user", "content": prompt.strip()}}
    if interrupt:
        line["priority"] = INTERRUPT_PRIORITY
    lines.append(json.dumps(line))
    payload = ("\n".join(lines) + "\n").encode()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError):
        return NOT_LIVE           # a stale .sock from a process that has gone
    except OSError as e:
        return failed(f"the inbox socket refused the connection: {e}")
    try:
        sock.sendall(payload)
    except OSError as e:
        return failed(f"the inbox socket did not take the message: {e}")
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return SENT
