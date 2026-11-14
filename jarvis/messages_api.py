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
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    out = {"id": item.id, "at": item.at, "local": local_time(item.at),
           "when": when_said(item.at), "kind": str(item.kind), "says": item.text,
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


def work_title(work_id: str) -> str:
    """A Work item's title, or "" when there is none."""
    return _titles([work_id]).get(work_id, "")


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


# --- naming one message: by its id, or by its time ----------------------------
#
# He names a message by its id (the page, and the brain once it has one), or
# by when it came, as he says or types it: "the message from 3:42", "the one
# at 15:42 from Bridge". `resolve` is the one function every tool and route
# that takes a message goes through (the server's `_message_meant`). A time
# is his local time (the Mac's), matched to the minute against each
# message's recorded time; a sender, a Work item and NEW narrow it. Exactly
# one match is the message; several are given back to ask which; none says
# so and names the nearest. A tie is never broken by guessing.

# How far back a time with no day looks: the last day.
BARE_TIME_WINDOW_SEC = 24 * 3600
# How many messages that fit are given back to ask which.
CANDIDATES_MAX = 5
# How far either side of the time asked a nearest message is looked for.
NEAREST_SPAN_SEC = 3 * 24 * 3600

_TODAY = {"today": 0, "hoy": 0, "yesterday": 1, "ayer": 1}
_TIME = re.compile(
    r"^(?:(?P<date>\d{4}-\d{2}-\d{2})\s+|(?P<day>today|yesterday|hoy|ayer)\s+)?"
    r"(?:(?:at|a\s+las|a\s+la)\s+)?"
    r"(?P<hour>\d{1,2})[:.](?P<minute>\d{2})"
    r"\s*(?:(?P<h>h|hrs?)\b|(?P<half>a\.?\s?m\.?|p\.?\s?m\.?))?"
    r"(?:\s+(?P<after>today|yesterday|hoy|ayer))?$")


class BadTime(ValueError):
    """A time that is not one `resolve` reads, with the forms it does."""


TIME_FORMS = ('"15:42", "3:42", "3:42 pm", "3:42 p.m.", "3.42pm", "15:42 h", with "today" '
              'or "yesterday" (hoy, ayer) before it, or "2026-11-12 15:42"')


@dataclass(frozen=True)
class AskedTime:
    """A time as he gave it: the day it names (None: the last day), and the
    wall-clock (hour, minute) pairs it can mean (two for "3:42": 3:42 and
    15:42)."""
    day: Optional[date]
    days_back: Optional[int]
    minutes: tuple[tuple[int, int], ...]


def parse_time(text: str) -> AskedTime:
    """His words for a time, read: BadTime when they are not one."""
    words = " ".join(str(text or "").strip().lower().split())
    found = _TIME.match(words)
    if not found:
        raise BadTime(f'"{str(text)[:40]}" is not a time I read: give it as {TIME_FORMS}.')
    hour, minute = int(found["hour"]), int(found["minute"])
    if hour > 23 or minute > 59:
        raise BadTime(f'"{str(text)[:40]}" is not a time of day: give it as {TIME_FORMS}.')
    half = (found["half"] or "").replace(".", "").replace(" ", "")
    # A zero-padded hour ("09:05") or a dated time ("2026-11-12 12:30", the
    # form JARVIS gives as `local`) is the 24-hour clock JARVIS writes.
    clock24 = found["hour"].startswith("0") or bool(found["date"])
    if half and 1 <= hour <= 12:
        minutes = (((hour % 12) + (12 if half == "pm" else 0), minute),)
    elif found["h"] or half or clock24 or hour == 0 or hour > 12:
        minutes = ((hour, minute),)
    else:
        # "3:42" with no am or pm is 3:42 or 15:42; "12:05" is noon or midnight.
        minutes = ((hour % 12, minute), (hour % 12 + 12, minute))
    day = None
    if found["date"]:
        try:
            day = date.fromisoformat(found["date"])
        except ValueError:
            raise BadTime(f'"{found["date"]}" is not a date.')
    said_day = found["day"] or found["after"]
    return AskedTime(day, _TODAY[said_day] if said_day else None, minutes)


def _local(at: float, tz) -> datetime:
    """An epoch time as his wall clock: the Mac's own zone when `tz` is
    None (daylight saving included), else `tz`."""
    return datetime.fromtimestamp(float(at), tz)


def _day_start(day, tz) -> float:
    return datetime.combine(day, datetime.min.time(), tzinfo=tz).timestamp()


def when_said(at: float, *, now: Optional[float] = None, tz=None) -> str:
    """When a message came, as `resolve` reads it back: "today 15:42",
    "yesterday 15:42", else "2026-11-10 15:42", his local time."""
    local = _local(at, tz)
    today = _local(time.time() if now is None else now, tz).date()
    back = (today - local.date()).days
    day = {0: "today", 1: "yesterday"}.get(back, local.date().isoformat())
    return f"{day} {local:%H:%M}"


def local_time(at: float, tz=None) -> str:
    """When a message came, his local time to the minute: "2026-11-12 15:42"."""
    return f"{_local(at, tz):%Y-%m-%d %H:%M}"


@dataclass(frozen=True)
class MessageSpec:
    """How he named a message: its id, its time as he said it, who it is
    from, its Work item (id or title), only new ones, the next new one
    after the one he is on, and the one he is on."""
    message: Optional[int] = None
    at: str = ""
    sender: str = ""
    work: str = ""
    new: bool = False
    next: bool = False
    on: Optional[int] = None

    @classmethod
    def of(cls, args: dict, on: Optional[int] = None) -> "MessageSpec":
        """A tool's or a route's arguments: `message`, `at`, `from`, `work`,
        `new`, `next`."""
        raw = args.get("message")
        try:
            message = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            message = -1                           # named, and no id: refused below
        return cls(message, str(args.get("at") or "").strip(),
                   str(args.get("from") or "").strip(), str(args.get("work") or "").strip(),
                   bool(args.get("new")), bool(args.get("next")), on)

    @property
    def described(self) -> bool:
        """Named by what it is (a time, a sender, an item, new), not by id."""
        return bool(self.at or self.sender or self.work or self.new)


@dataclass(frozen=True)
class Resolved:
    """The message he means (`item`, its `id`), or none and why: with
    `candidates` (each fitting message, briefly) to ask which, or the
    nearest when none fits."""
    id: Optional[int] = None
    item: Optional[conversation_log.ConversationItem] = None
    candidates: tuple = ()
    why: str = ""
    nearest: bool = False


def candidate(item: conversation_log.ConversationItem, *, now: Optional[float] = None,
              tz=None) -> dict:
    """One message, briefly, to ask which: its id, `local` (the time to name
    it by, which keeps its meaning past midnight), `when` (the words for
    him), from, about."""
    about = _titles([item.about]).get(item.about, "") if item.about else ""
    return {"id": item.id, "local": local_time(item.at, tz),
            "when": when_said(item.at, now=now, tz=tz),
            "from": _shown_sender(item), "about": about or item.about or "",
            "new": item.state == conversation_log.MessageState.NEW}


def _fits_sender(item, sender: str) -> bool:
    """He named who it is from: its sender's one name now, or the name it
    came under, as said (any case, accents folded, a name's whole words)."""
    from jarvis.agent_directory import spoken_key
    asked = spoken_key(sender)
    names = {spoken_key(_shown_sender(item)), spoken_key(item.sender_name or "")}
    return bool(asked) and any(asked == n or f" {asked} " in f" {n} " for n in names if n)


def _fits_work(item, work: str, titles: dict) -> bool:
    from jarvis.agent_directory import spoken_key
    if not item.about:
        return False
    return item.about == work or spoken_key(titles.get(item.about, "")) == spoken_key(work)


def _fits(item, spec: MessageSpec, titles: dict) -> bool:
    """Whether a message fits everything named but its time."""
    return ((not spec.new or item.state == conversation_log.MessageState.NEW)
            and (not spec.sender or _fits_sender(item, spec.sender))
            and (not spec.work or _fits_work(item, spec.work, titles)))


def _at_minute(item, asked: AskedTime, today, tz) -> bool:
    local = _local(item.at, tz)
    if (local.hour, local.minute) not in asked.minutes:
        return False
    if asked.day is not None:
        return local.date() == asked.day
    if asked.days_back is not None:
        return local.date() == today - timedelta(days=asked.days_back)
    return True


def _window(asked: AskedTime, now: float, tz) -> tuple[float, float]:
    """The span a time is looked for in: its day, or the last day."""
    if asked.day is None and asked.days_back is None:
        # The last day, less its first minute: the same minute yesterday is
        # another day's, never today's.
        return now - BARE_TIME_WINDOW_SEC + 60, now + 60
    day = asked.day or (_local(now, tz).date() - timedelta(days=asked.days_back))
    return _day_start(day, tz), _day_start(day + timedelta(days=1), tz)


def _targets(asked: AskedTime, lo: float, hi: float, tz) -> list[float]:
    """Each epoch time in [lo, hi) the time asked names, to the minute."""
    first, last = _local(lo, tz).date(), _local(hi, tz).date()
    out, day = [], first
    while day <= last:
        for hour, minute in asked.minutes:
            t = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(
                hour=hour, minute=minute).timestamp()
            if lo - 60 <= t < hi:
                out.append(t)
        day += timedelta(days=1)
    return out


def _work_ids(work: str) -> list[str]:
    """The Work items he named: the one with that id, else every one whose
    title is his words (as said: any case, accents folded). Only items with
    messages to him are looked at by title."""
    from jarvis.agent_directory import spoken_key
    if work_store.get(work) is not None:
        return [work]
    asked = spoken_key(work)
    if not asked:
        return []
    threads = [t.work for t in conversation_log.work_threads(limit=10_000)]
    return [i for i, title in _titles(threads).items() if spoken_key(title) == asked]


def _pool(spec: MessageSpec, now: float) -> list:
    """The messages a description without a time is looked for among: the
    new ones (new), its Work item's whole thread (work, by id or title),
    else the last day's."""
    if spec.new:
        return conversation_log.new_messages()
    if spec.work:
        ids = _work_ids(spec.work)
        if ids:
            return [i for w in ids for i in conversation_log.work_thread(w, limit=10_000)
                    if i.is_message]
    return conversation_log.messages_between(now - BARE_TIME_WINDOW_SEC, now + 60)


def resolve(spec: MessageSpec, *, now: Optional[float] = None, tz=None) -> Resolved:
    """The message he means, by `spec` (see MessageSpec): by its id; else
    by its time, sender, Work item and NEW, together; else the next new
    one after the one he is on (`next`, in "What's new?"'s order); else the
    one he is on. Exactly one match resolves; several give up to
    CANDIDATES_MAX back to ask which; none says so, naming the nearest by
    time (both when two are as near). Blocking."""
    now = time.time() if now is None else float(now)
    if spec.message is not None:
        if spec.message < 0:
            return Resolved(why="Give the message's id, or when it came (at); messages_to_him "
                                "names each message and its time.")
        found = _by_id(spec.message)
        if found.item is None or not spec.described:
            return found
        return _id_as_described(found.item, spec, now, tz)
    if spec.described:
        return _described(spec, now, tz)
    if spec.next:
        order = [m["id"] for m in whats_new()]
        after = order[order.index(spec.on) + 1:] if spec.on in order else order
        if not after:
            return Resolved(why="Nothing new after it, sir.")
        return _by_id(after[0])
    if spec.on is None:
        return Resolved(why="He is on no message yet: messages_to_him with new reads what is "
                            "new, and puts him on the first.")
    return _by_id(spec.on)


def _by_id(item_id: int) -> Resolved:
    item = conversation_log.get(item_id)
    if item is None or not item.inbound:
        return Resolved(why=f"There is no message {item_id} to him on record; messages_to_him "
                            f"names each message and its time.")
    return Resolved(item.id, item)


def _fits_described(item, spec: MessageSpec, now: float, tz) -> bool:
    """Whether a message fits everything named: its sender, Work item, new
    and its time (to the minute, in its day or the last day)."""
    titles = _titles([item.about]) if spec.work and item.about else {}
    if not _fits(item, spec, titles):
        return False
    if not spec.at:
        return True
    asked = parse_time(spec.at)
    lo, hi = _window(asked, now, tz)
    return _at_minute(item, asked, _local(now, tz).date(), tz) and lo - 60 <= item.at < hi


def _id_as_described(item, spec: MessageSpec, now: float, tz) -> Resolved:
    """Named by its id and described too: the id only when the message is
    the one described; otherwise neither is used, and both come back to ask
    him which he means."""
    try:
        if _fits_described(item, spec, now, tz):
            return Resolved(item.id, item)
    except BadTime as e:
        return Resolved(why=str(e))
    described = _described(spec, now, tz)
    others = ([candidate(described.item, now=now, tz=tz)] if described.item is not None
              else list(described.candidates))
    shown = [candidate(item, now=now, tz=tz)] + [c for c in others if c["id"] != item.id]
    return Resolved(candidates=tuple(shown[:CANDIDATES_MAX + 1]),
                    why=(f"Message {item.id} does not fit {_named(spec)}: use neither; ask "
                         f"him which he means, naming each by its time and sender, then name "
                         f"it by its id alone."))


def _described(spec: MessageSpec, now: float, tz) -> Resolved:
    try:
        asked = parse_time(spec.at) if spec.at else None
    except BadTime as e:
        return Resolved(why=str(e))
    titles: dict = {}
    if asked is None:
        pool = _pool(spec, now)
    else:
        lo, hi = _window(asked, now, tz)
        pool = conversation_log.messages_between(lo - 60, hi + 60)
    if spec.work:
        titles = _titles({i.about for i in pool if i.about})
    today = _local(now, tz).date()
    fitting = [i for i in pool if _fits(i, spec, titles)]
    if asked is not None:
        lo, hi = _window(asked, now, tz)
        fitting = [i for i in fitting if _at_minute(i, asked, today, tz) and lo - 60 <= i.at < hi]
    named = _named(spec)
    if len(fitting) == 1:
        return Resolved(fitting[0].id, fitting[0])
    if fitting:
        shown = sorted(fitting, key=lambda i: -i.at)[:CANDIDATES_MAX]
        more = len(fitting) - len(shown)
        return Resolved(candidates=tuple(candidate(i, now=now, tz=tz) for i in shown),
                        why=(f"{len(fitting)} messages fit {named}"
                             f"{f' ({more} more not shown)' if more else ''}: ask him which, "
                             f"naming each by its time and sender, then name it by its id."))
    if asked is None:
        return Resolved(why=f"No message to him fits {named}.")
    return _nearest(spec, asked, now, tz, named)


def _nearest(spec: MessageSpec, asked: AskedTime, now: float, tz, named: str) -> Resolved:
    """None fits: the nearest by time that fits the rest (both when two are
    as near)."""
    lo, hi = _window(asked, now, tz)
    targets = _targets(asked, lo, hi, tz)
    near = conversation_log.messages_between(lo - NEAREST_SPAN_SEC, hi + NEAREST_SPAN_SEC)
    titles = _titles({i.about for i in near if i.about}) if spec.work else {}
    near = [i for i in near if _fits(i, spec, titles)]
    if not targets or not near:
        return Resolved(why=f"No message to him fits {named}, and none is near it.")
    # Minutes apart, each message by the minute it came in, as it is shown.
    gap = {i.id: min(abs(i.at // 60 * 60 - t) for t in targets) // 60 for i in near}
    best = min(gap.values())
    closest = sorted((i for i in near if gap[i.id] == best), key=lambda i: i.at)
    return Resolved(candidates=tuple(candidate(i, now=now, tz=tz) for i in closest),
                    nearest=True,
                    why=(f"No message to him fits {named}. The nearest "
                         f"{'are' if len(closest) > 1 else 'is'} below; say so, and ask him "
                         f"whether he means {'one of them' if len(closest) > 1 else 'it'}."))


def _named(spec: MessageSpec) -> str:
    """What he named, in JARVIS's own words: the time and filters, never
    the sender's or item's text (the brain has those)."""
    parts = []
    if spec.at:
        parts.append("the time he gave")
    if spec.sender:
        parts.append("the sender he named")
    if spec.work:
        parts.append("the Work item he named")
    if spec.new:
        parts.append("new")
    return ", ".join(parts) or "what he said"


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
