"""Messages to Tim, one kind of thing, each on its Work item first
(one_channel round 1, docs/planning/one-channel/messages-by-work-item.md).

A message is a message to him on the conversation record
(conversation_log): from its proven sender, about a Work item or none,
saying something short, carrying a package or none, and new, read, answered
or done. Every channel's messages are recorded so (the server's
`_record_inbound`); this module is how an agent sends one, and how the page
and the brain read and mark them.

  POST /api/messages?work=<item id>[&project=&workflow=&source=]
        {"says": "...", "package": "<the zip, base64>"?}: a message from the
        signing agent (its own key, or a work-item key for its own item
        only, agent_rights `work`). `work` is in the query so the right's
        scope sees it. The ONE way an agent reaches him: `python -m
        jarvis.message`, and `make_package`, whose package is always a
        message. A decision is only ever inside a carried package: a
        request that asks one without a package is refused (`asks`). A
        message with no `work` from an agent with a piece on an open item is
        refused, naming the item(s). The package goes through the package
        inbox (its signature, the fresh author's proof and every other
        check; `project`, `workflow` and `source` as make_package gives
        them), and the message carries it. Signed by a key JARVIS holds but
        has not onboarded, it is held (the one route such a key reaches,
        web_auth.HOLDING_PATH) until he switches its agent on.
  GET  /api/messages/work           each Work item with messages, the one
                                    with the newest first: its new count,
                                    its total and its first new message
  GET  /api/messages/work/{id}      one item's conversation, oldest first
                                    (?before=<id> pages back)
  GET  /api/messages/general        the messages that belong to no item,
                                    newest first (?before=<id>)
  POST /api/messages/{id}/mark      {"state": "read"|"done"}: his mark

The reads and the mark are the brain's `messages_to_him` and `mark_message`
too, through the same functions (`work_list`, `work_conversation`,
`general_list`, the server's `_mark_message`); "What's new?" reads
`whats_new`, the Inbox's order. Every read carries other
people's words: the brain gets it walled.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import urllib.parse
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import (agent_rights, conversation_log, package_inbox, package_inbox_api,
                    packages_api, reporters, web_auth, work_store)

MESSAGES_PATH = "/api/messages"
# A message says something short; anything longer is a package's.
SAYS_MAX = 2000
# The fields a request would ask a decision with: a decision is only ever
# inside a carried package (`asks`).
DECISION_FIELDS = ("decision", "question", "options", "recommendation")
# A sentence that ends in a question mark: "?" before a space or the end.
# (A "?" inside a word or a URL's query asks nothing.)
_QUESTION = re.compile(r"\?(?=\s|$)")
# The states his mark may give; answered is given only by his answer going.
MARKS = (conversation_log.MessageState.READ, conversation_log.MessageState.DONE)


class Refused(ValueError):
    """A message that is not taken, with why, and the status to answer."""

    def __init__(self, why: str, status: int = 409):
        super().__init__(why)
        self.status = status


def asks(says: str, body: dict) -> bool:
    """Whether a request asks him something: it names a decision's fields,
    or a sentence of what it says ends in a question mark."""
    return any(body.get(f) not in (None, "", []) for f in DECISION_FIELDS) or \
        bool(_QUESTION.search(says))


def read_request(body) -> tuple[str, Optional[bytes]]:
    """(what it says, the package's bytes or None) from a request's body,
    or Refused: words, short; a decision only with a package."""
    if not isinstance(body, dict):
        raise Refused("A message is a JSON object: says, and package when it carries one.", 400)
    says = str(body.get("says") or "").strip()
    if not says:
        raise Refused("A message says something: give says.", 400)
    if len(says) > SAYS_MAX:
        raise Refused(f"A message says something short (at most {SAYS_MAX} characters); "
                      f"put the rest in a package and carry it.", 413)
    package = None
    if body.get("package"):
        try:
            package = base64.b64decode(str(body["package"]), validate=True)
        except (binascii.Error, ValueError):
            raise Refused("package is the zip's bytes in base64.", 400)
        if len(package) > package_inbox_api.MAX_BYTES:
            raise Refused(f"The zip is over {package_inbox_api.MAX_BYTES} bytes, the most a "
                          f"package may be.", 413)
    if package is None and asks(says, body):
        raise Refused("A decision or a question goes to him only inside a package that shows "
                      "him what he needs to decide (make it with python -m jarvis.make_package "
                      "and carry it with --package). Nothing was sent.")
    return says, package


def _held_session(fingerprint: str) -> str:
    """The session a key JARVIS holds (not onboarded) names, or ""."""
    from jarvis import agent_key_store
    key = agent_key_store.get(fingerprint) if fingerprint else None
    return key.session_id if key is not None else ""


def sender_session(principal: str) -> str:
    """The session a signing key sends for: an agent key's own, a work-item
    key's issuer's; "" for anything else."""
    from jarvis import agent_key_store
    if principal.startswith(reporters.AGENT_PREFIX):
        return principal[len(reporters.AGENT_PREFIX):]
    if principal.startswith(reporters.WORK_KEY_PREFIX):
        key = agent_key_store.get(principal[len(reporters.WORK_KEY_PREFIX):])
        issuer = key.issued_by if key is not None else ""
        return issuer[len(reporters.AGENT_PREFIX):] \
            if issuer.startswith(reporters.AGENT_PREFIX) else ""
    return ""


def check_about(session_id: str, work: str) -> None:
    """Refused unless the message's Work item is right for its sender: an
    item it names must be open; naming none is refused while the agent has a
    piece on an open item, naming each."""
    if work:
        item = work_store.get(work)
        if item is None:
            raise Refused(f"There is no Work item {work}; nothing was sent.", 404)
        if item.closed_at is not None or item.history:
            raise Refused(f"Work item {work} is closed; nothing was sent.")
        return
    mine = work_store.open_items_of(session_id)
    if mine:
        named = "; ".join(f"{i} ({t})" for i, t in mine)
        raise Refused(f"Name the Work item it is about with --work: you have a piece on "
                      f"{named}. Nothing was sent.")


# --- reading ---------------------------------------------------------------

def message_json(item: conversation_log.ConversationItem) -> dict:
    """One item of a Work item's conversation, or one message, as the page
    and the brain read it: from, about, says, carries, state."""
    out = {"id": item.id, "at": item.at, "kind": str(item.kind), "says": item.text,
           "about": item.about, "carries": item.carries, "state": item.state,
           "state_at": item.state_at, "message": item.is_message,
           "counterpart": {"kind": item.counterpart_kind, "ref": item.counterpart_ref},
           "source": {"kind": item.source_kind, "id": item.source_id}}
    if item.carries:
        out["package"] = _carried(item.carries)
    if item.inbound:
        out["from"] = {"sender": item.sender, "name": _shown_sender(item),
                       "proof": item.sender_evidence, "subagent": item.sender_subagent}
    else:
        out["from"] = {"sender": "tim", "name": "", "proof": None, "subagent": None}
        if item.detail.get("reply_to"):
            out["reply_to"] = item.detail["reply_to"]
    return out


def _carried(inbox_id: str) -> Optional[dict]:
    """The package a message carries, as its box shows it; None when its
    inbox row is gone."""
    entry = package_inbox.get(inbox_id)
    if entry is None:
        return None
    return {"inbox": entry.id, "package_id": entry.package_id, "title": entry.title,
            "runs": entry.runs, "decisions": entry.decisions_total,
            "answered": entry.decisions_answered, "state": entry.state}


def _shown_sender(item: conversation_log.ConversationItem) -> str:
    """The sender by its one name now (an agent renamed is shown so), else
    the name it came under."""
    return reporters.shown(item.sender or "") or item.sender_name or \
        str(item.detail.get("name") or "")


def _titles(ids) -> dict[str, str]:
    return {i: r.row["title"] for i, r in work_store.records(ids).items()}


def work_list() -> dict:
    """Each Work item with messages, the newest first: its title, how many
    are new, how many in all and its first new message."""
    threads = conversation_log.work_threads()
    titles = _titles([t.work for t in threads])
    return {"items": [{"work": t.work, "title": titles.get(t.work, ""), "new": t.new,
                       "total": t.total, "latest_at": t.latest_at,
                       "first_new": message_json(t.first_new) if t.first_new else None}
                      for t in threads],
            "new": sum(t.new for t in threads)}


def work_conversation(work_id: str, before: Optional[int] = None) -> dict:
    """One Work item's conversation, oldest first, and how many are new."""
    items = conversation_log.work_thread(work_id, before)
    return {"work": work_id, "title": _titles([work_id]).get(work_id, ""),
            "new": conversation_log.new_about(work_id),
            "messages": [message_json(i) for i in items],
            "more": bool(items) and len(items) >= conversation_log.PAGE}


def general_list(before: Optional[int] = None, read: bool = False) -> dict:
    """The messages that belong to no Work item, newest first: the new
    ones, or with `read` the ones read or answered and not done (the
    Inbox's Read section). Done ones are in neither. Both counts with each."""
    states = conversation_log.READ_STATES if read else (conversation_log.MessageState.NEW,)
    items = conversation_log.general(before, states=states)
    return {"messages": [message_json(i) for i in items],
            "new": conversation_log.new_general(),
            "read": conversation_log.read_general(),
            "more": len(items) >= conversation_log.PAGE}


def whats_new() -> list[dict]:
    """His new messages in the one order "What's new?" reads them, as the
    Inbox lists them: each Work item with new messages, the one with the
    newest message first, its new ones oldest first, each with its item's
    title (`about_title`); then the new ones that belong to no item, newest
    first."""
    new = conversation_log.new_messages()
    order = [t.work for t in conversation_log.work_threads()]
    titles = _titles(order)
    rank = {w: i for i, w in enumerate(order)}
    on_items = sorted((m for m in new if m.about),
                      key=lambda m: (rank.get(m.about, len(rank)), m.id))
    general = sorted((m for m in new if not m.about), key=lambda m: -m.id)
    return [{**message_json(m), "about_title": titles.get(m.about, "")} for m in on_items] + \
        [message_json(m) for m in general]


# --- the routes --------------------------------------------------------------

@dataclass(frozen=True)
class Links:
    """What a carried package is for, as make_package gives it, and the key
    a request was signed by when JARVIS holds it but has not onboarded it."""
    project: str = ""
    workflow: str = ""
    source: str = ""
    held_key: str = ""


@dataclass(frozen=True)
class Calls:
    """The server's functions, shared with the brain's tools."""
    # (principal, session, work, says, the package's bytes, the request's
    # reporter, links) -> (the message, the package's arrival, the package)
    send: Callable[..., Awaitable[tuple]]
    # (item id, state, how) -> (the message, "") or (None, why not)
    mark: Callable[[int, str, str], Awaitable[tuple]]


def _refused(why: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"error": why}, status_code=status)


def _before(before) -> Optional[int]:
    try:
        return int(before) if before not in (None, "") else None
    except (TypeError, ValueError):
        return None


def mount(app: FastAPI, calls: Calls) -> None:

    async def send(request: Request, work: str = "", project: str = "", workflow: str = "",
                   source: str = ""):
        principal = web_auth.principal(request.scope) or ""
        held_key = "" if principal else (request.scope.get(web_auth.SIGNED_KEY) or "")
        session = await asyncio.to_thread(sender_session, principal) if principal else \
            await asyncio.to_thread(_held_session, held_key)
        if not session and not held_key:
            return _refused("A message to Tim is signed by its sender's own key "
                            "(python -m jarvis.message --as <session id>).", 403)
        try:
            body = await request.json()
        except ValueError:
            return _refused("A message is a JSON object: says, and package when it "
                            "carries one.", 400)
        try:
            says, package = read_request(body)
            if principal and package is not None and not await asyncio.to_thread(
                    agent_rights.allows, principal, "POST", package_inbox_api.INBOX_PATH,
                    urllib.parse.urlencode({"work": work.strip()})):
                raise Refused("Carrying a package needs the packages right, which this "
                              "key does not hold. Nothing was sent.", 403)
            if principal:
                await asyncio.to_thread(check_about, session, work.strip())
            item, arrival, package = await calls.send(
                principal, session, work.strip(), says, package,
                reporters.of_scope(request.scope),
                Links(project.strip(), workflow.strip(), source, held_key))
        except Refused as e:
            return _refused(str(e), e.status)
        out = {"message": message_json(item)}
        if arrival is not None:
            out.update({"inbox": package_inbox_api.entry_json(arrival.entry),
                        "arrived": arrival.fresh,
                        "superseded": [e.id for e in arrival.superseded],
                        "package": packages_api.describe(package)})
        return out

    async def by_work():
        return await asyncio.to_thread(work_list)

    async def one_work(work_id: str, before: str = ""):
        return await asyncio.to_thread(work_conversation, work_id, _before(before))

    async def no_work(before: str = "", read: str = ""):
        return await asyncio.to_thread(general_list, _before(before),
                                       read.lower() in ("1", "true", "yes"))

    async def mark(item_id: int, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        state = str((body or {}).get("state") or "")
        item, why = await calls.mark(item_id, state, "on the page")
        if item is None:
            return _refused(why, 404 if "no message" in why else 409)
        return {"message": message_json(item)}

    app.add_api_route(MESSAGES_PATH, send, methods=["POST"])
    app.add_api_route(MESSAGES_PATH + "/work", by_work, methods=["GET"])
    app.add_api_route(MESSAGES_PATH + "/work/{work_id}", one_work, methods=["GET"])
    app.add_api_route(MESSAGES_PATH + "/general", no_work, methods=["GET"])
    app.add_api_route(MESSAGES_PATH + "/{item_id}/mark", mark, methods=["POST"])
