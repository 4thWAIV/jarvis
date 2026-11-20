"""A status briefing's facts, built from the Work items' own records.

    python -m jarvis.status_briefing --work <id> [--work <id> ...] --out <folder> [--since <time>]
    python -m jarvis.status_briefing --work <id> [--work <id> ...] --check

Tim asked for status presentations that lead with what he cannot see. This
command reads each named Work item through the same functions the brain's
`work_status` reads it with (`work_store.get`, `work_api.needs_of`,
`work_api.need_words`, `work_api.is_stalled`, `work_api.WhoNames`,
`work_store.events`), never from an agent's memory, and writes two files
into `--out`:

  handoff.json          the make_package handoff (package_request's one
                        form: `questions`, `work`, typed `evidence`), the
                        evidence file first, asking one question,
                        `approve-status`, with the options Approve (the
                        status is complete and accurate) and Reject (what
                        is missing or wrong); the sending agent adds its
                        own `sender_answers` before it asks for the package
  status-evidence.json  the composition in full, every fact with the
                        evidence the record holds for it

For each item, in this order (problems before progress):

  needs_tim   what he is needed for (`needs_of`: his open decisions, the
              packages still asking him, his review), then every held piece
              and what holds it, since when and who is on it, then every
              stalled piece, then the item itself held, stalled or with an
              answer nobody followed up; or one line saying nothing on it
              needs him
  moving      pieces reported working (with JARVIS's live view, when this
              process has one, saying whether it sees them working), each
              with its latest note and evidence
  done_since  pieces reported ready for his review, and his approvals,
              since `--since` (epoch seconds, an ISO time, or `24h` / `7d`
              back; 24 hours by default), from the item's log

Nothing he can already see on the Work tab is repeated without its cause:
a fact is a held, stalled, moving or finished piece with its note and
evidence, never a recap of the row.

Making and sending the package stays make_package's: the session then runs
`python -m jarvis.make_package --handoff <out>/handoff.json --engine ...
--session ... --work <id>`. `--check` writes nothing; it lists each item's
pieces with no note, and pieces past planned or ready with no evidence, so
a stale record shows before a briefing is built on it (exit 1 when any).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from jarvis import work_api, work_store

HANDOFF = "handoff.json"
EVIDENCE = "status-evidence.json"
DEFAULT_SINCE_SEC = 24 * 3600

# The states a piece is not yet expected to have evidence in.
_NOT_STARTED = (work_store.PLANNED, work_store.READY)
# A piece reported working: evidenced by its owner's open turn, or a claim.
_MOVING = (work_store.WORKING, work_store.ACTIVITY_UNKNOWN)
# What a piece report moving to finishes, as far as a reporter can say.
_FINISHED = (work_store.VERIFYING, work_store.DONE)
_RELATIVE = re.compile(r"(\d+(?:\.\d+)?) ?([mhd])")
_UNIT_SEC = {"m": 60, "h": 3600, "d": 86400}


class BriefingRefused(ValueError):
    """An item that is not on record, or a time that cannot be read."""


# ── time ────────────────────────────────────────────────────────────────

def parse_since(text: Optional[str], now: float) -> float:
    """`--since` as epoch seconds: a number of seconds since the epoch, an
    ISO time (local when it names no zone), or a span back from now such as
    `24h`, `7d` or `90m`. None is 24 hours back."""
    if text is None or not str(text).strip():
        return now - DEFAULT_SINCE_SEC
    said = str(text).strip()
    relative = _RELATIVE.fullmatch(said.lower())
    if relative:
        return now - float(relative.group(1)) * _UNIT_SEC[relative.group(2)]
    try:
        return float(said)
    except ValueError:
        pass
    try:
        when = _dt.datetime.fromisoformat(said)
    except ValueError:
        raise BriefingRefused(f"--since {said!r} is not epoch seconds, an ISO time or a span "
                              "such as 24h or 7d.") from None
    return when.timestamp()


def local(at: Optional[float]) -> Optional[str]:
    """A time as the Mac's local clock writes it, or None."""
    if at is None:
        return None
    return _dt.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")


# ── facts ───────────────────────────────────────────────────────────────

def _evidence(piece: Optional[work_store.Piece]) -> list[dict]:
    return [{"kind": e.kind, "value": e.value} for e in piece.evidence] if piece else []


def _who(piece: work_store.Piece, names: Callable[[work_store.Who], Optional[str]]) -> str:
    """Who is on a piece by its one name, else its record's reference."""
    person = piece.who
    if person.kind == work_store.TIM:
        return "you"
    name = names(person)
    reference = f"{person.kind}:{person.ref}"
    return f"{name} ({reference})" if name else reference


def _fact(kind: str, text: str, piece: Optional[work_store.Piece] = None,
          names: Optional[Callable] = None, since: Optional[float] = None,
          more_evidence: Optional[list[dict]] = None, **extra) -> dict:
    """One fact: what it says, the piece it is about (who is on it, its
    latest note), since when, and the evidence the record holds for it."""
    fact = {"kind": kind, "text": text}
    if piece is not None:
        fact.update(piece=piece.key, title=piece.title, state=piece.state,
                    who=_who(piece, names), note=piece.note,
                    note_at=local(piece.note_at))
    if since is not None:
        fact.update(since=local(since), since_epoch=since)
    fact.update(extra)
    fact["evidence"] = _evidence(piece) + list(more_evidence or [])
    return fact


def _held_text(piece: work_store.Piece) -> str:
    holds = ", ".join(piece.until) if piece.until else "nothing named"
    how = "is blocked" if piece.state == work_store.BLOCKED else "waits on you"
    return f"Piece {piece.key} ({piece.title}) {how}, held by {holds}"


def needs_tim(item: work_store.WorkItem, names) -> list[dict]:
    """What needs him on this item, the first first: his acts
    (`work_api.needs_of`, a waiting piece's own need given below with what
    holds it), every held piece, every stalled piece, then the item itself
    held, stalled, or answered with no follow-up."""
    needs = work_api.needs_of(item)
    facts = []
    decisions = {d.key: d for d in item.decisions}
    for need in needs:
        if need["kind"] == work_api.PIECE_NEED:
            continue
        extra: dict = {}
        if need["kind"] == work_api.DECISION_NEED:
            d = decisions.get(need["key"])
            extra = {"decision": need["key"]}
            if d is not None:
                extra.update(options=d.options, recommendation=d.recommendation)
                if d.package:
                    extra["more_evidence"] = [{"kind": "package", "value": d.package}]
            facts.append(_fact(need["kind"], work_api.need_words(need),
                               since=d.opened_at if d else None, **extra))
            continue
        if "inbox" in need:
            extra = {"inbox": need["inbox"],
                     "more_evidence": [{"kind": "package", "value": need["inbox"]}]}
        facts.append(_fact(need["kind"], work_api.need_words(need), **extra))
    for p in item.pieces:
        if p.state in work_store.HELD:
            facts.append(_fact("held_piece", _held_text(p) + ".", p, names,
                               since=p.waiting_since, holds=list(p.until), then=p.then,
                               unlinked=p.unlinked))
    for p in item.pieces:
        if p.stalled:
            facts.append(_fact("stalled_piece",
                               f"Piece {p.key} ({p.title}) is claimed working but nothing "
                               "has been reported on it for over a day.",
                               p, names, since=p.note_at))
    if not item.pieces and item.state in work_store.HELD:
        facts.append(_fact("held_item",
                           f"The item {'is blocked' if item.state == work_store.BLOCKED else 'waits on you'}, "
                           f"held by {', '.join(item.until) or 'nothing named'}.",
                           holds=list(item.until)))
    if work_api.is_stalled(item, needs):
        facts.append(_fact("stalled_item", "Stalled: nobody is working on this and nothing "
                           "needs you.", since=item.updated_at))
    f = item.follow_up
    if f is not None and f.overdue:
        facts.append(_fact("no_follow_up",
                           f"You answered {f.key} ({f.question}) and "
                           f"{f.asker or 'the agent that asked'} has reported nothing since.",
                           since=f.answered_at, decision=f.key))
    return facts


def moving(item: work_store.WorkItem, names) -> list[dict]:
    """Pieces reported working, each with its latest note and evidence:
    working when JARVIS sees its owner in a turn, else a claim it cannot
    see. A stalled one is a problem, listed under needs_tim instead."""
    facts = []
    for p in item.pieces:
        if p.state not in _MOVING or p.stalled:
            continue
        seen = p.state == work_store.WORKING
        doing = f": {p.live.doing}" if seen and p.live is not None and p.live.doing else ""
        text = (f"Piece {p.key} ({p.title}) is being worked on{doing}." if seen else
                f"Piece {p.key} ({p.title}) is reported working; JARVIS does not see its "
                "agent working now.")
        facts.append(_fact("moving", text, p, names, since=p.note_at, seen_working=seen))
    return facts


def done_since(item: work_store.WorkItem, since: float, names,
               events: Optional[list[work_store.WorkEvent]] = None) -> list[dict]:
    """What finished since `since`, from the item's log: each piece's latest
    report moving it to ready for review (or done), and his approval."""
    log = work_store.events(item.id, limit=10_000) if events is None else events
    pieces = {p.key: p for p in item.pieces}
    latest: dict[str, work_store.WorkEvent] = {}
    approvals = []
    for e in log:
        if e.at < since:
            continue
        if e.kind == work_store.PIECE and e.detail.get("state") in _FINISHED:
            latest[e.detail.get("key")] = e
        elif e.kind == work_store.APPROVED:
            approvals.append(e)
    facts = []
    for key, e in sorted(latest.items(), key=lambda kv: kv[1].at):
        p = pieces.get(key)
        title = p.title if p else key
        word = "done" if e.detail.get("state") == work_store.DONE else "ready for your review"
        fact = _fact("finished", f"Piece {key} ({title}) reported {word}: {e.summary}",
                     p, names, since=e.at)
        facts.append(fact)
    for e in approvals:
        facts.append(_fact("approved", f"You approved the item done: {e.summary}", since=e.at))
    return facts


def compose_item(item: work_store.WorkItem, since: float,
                 names: Optional[Callable] = None,
                 events: Optional[list[work_store.WorkEvent]] = None) -> dict:
    """One item's briefing: needs him first, then moving, then done since."""
    names = names or work_api.WhoNames()
    needs = needs_tim(item, names)
    out = {"id": item.id, "title": item.title, "state": item.state,
           "closed": item.closed_at is not None,
           "needs_tim": needs,
           "moving": moving(item, names),
           "done_since": done_since(item, since, names, events)}
    if not needs:
        out["needs_tim_line"] = f"Nothing on {item.title} ({item.id}) needs you."
    return out


def _items(ids: list[str]) -> list[work_store.WorkItem]:
    items, missing = [], []
    for ref in ids:
        found = work_store.get(ref)
        (items.append(found) if found is not None else missing.append(ref))
    if missing:
        raise BriefingRefused("No Work item with id " + ", ".join(missing) + ".")
    return items


def compose(ids: list[str], since: float, now: Optional[float] = None) -> dict:
    """The whole briefing: the items in the order named, items needing him
    first."""
    names = work_api.WhoNames()
    items = [compose_item(i, since, names) for i in _items(ids)]
    items.sort(key=lambda i: 0 if i["needs_tim"] else 1)   # stable: named order within
    return {"generated_at": local(now or time.time()), "since": local(since),
            "since_epoch": since, "items": items}


# ── the handoff ─────────────────────────────────────────────────────────

def _entry(e: dict) -> dict:
    """A fact's evidence as a typed handoff entry (package_request): a path
    is a file, a URL a url, a package a package; a commit names no
    repository, so it is a label, which retrieval resolves."""
    kind, value = e["kind"], e["value"]
    if kind == "url":
        return {"type": "url", "url": value}
    if kind == "package":
        return {"type": "package", "id": value}
    if kind == "commit":
        return {"type": "label", "text": f"commit {value}"}
    return {"type": "file", "path": value}


# Every package asks Approve or Reject (no information-only package): a
# status briefing asks him whether the status is complete and accurate.
QUESTION_ID = "approve-status"
APPROVE, REJECT = "approve", "reject"


def handoff(briefing: dict) -> dict:
    """make_package's handoff (package_request's one form) for the
    briefing, all but the sender's own answers: its one question, with the
    options the sender sees; the items; the evidence file first, then each
    item, then every piece of evidence the facts carry, each once. The
    agent that sends it adds `sender_answers`, its own answers to the two
    questions every sender answers (package_request.QUESTIONS): no program
    answers them for it."""
    items = briefing["items"]
    titles = "; ".join(f"{i['title']} ({i['id']})" for i in items)
    evidence = [{"type": "file", "path": EVIDENCE}]
    evidence += [{"type": "work_item", "id": i["id"]} for i in items]
    for item in items:
        for section in ("needs_tim", "moving", "done_since"):
            for fact in item[section]:
                for e in fact["evidence"]:
                    entry = _entry(e)
                    if entry not in evidence:
                        evidence.append(entry)
    return {
        "questions": [{
            "id": QUESTION_ID,
            "text": f"Is the status of {titles} since {briefing['since']} complete and "
                    f"accurate?",
            "options": [
                {"id": APPROVE, "name": "Approve",
                 "says": "Approve: this status is complete and accurate"},
                {"id": REJECT, "name": "Reject", "says": "Reject: say what is missing or wrong"},
            ]}],
        "work": [i["id"] for i in items],
        "evidence": evidence,
    }


def write(ids: list[str], out: Path, since: float, now: Optional[float] = None) -> Path:
    """Write the handoff and its evidence file into `out`; the handoff's path."""
    briefing = compose(ids, since, now)
    out.mkdir(parents=True, exist_ok=True)
    (out / EVIDENCE).write_text(json.dumps(briefing, indent=2) + "\n", encoding="utf-8")
    path = out / HANDOFF
    path.write_text(json.dumps(handoff(briefing), indent=2) + "\n", encoding="utf-8")
    return path


# ── --check ─────────────────────────────────────────────────────────────

def gaps(item: work_store.WorkItem) -> list[str]:
    """The item's pieces with no note, and those past planned or ready with
    no evidence: what would make a briefing on it say less than it should."""
    found = []
    for p in item.pieces:
        if not p.note.strip() or p.note == work_store.NO_NOTE:
            found.append(f"piece {p.key} ({p.title}) has no note")
        if not p.evidence and p.state not in _NOT_STARTED:
            found.append(f"piece {p.key} ({p.title}) is {p.state} with no evidence")
    return found


def check(ids: list[str]) -> list[str]:
    """One line per item: its gaps, or that it has none."""
    lines = []
    for item in _items(ids):
        found = gaps(item)
        lines.append(f"{item.id} {item.title}: " + ("; ".join(found) if found
                                                     else "every piece has a note and evidence"))
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.status_briefing",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", action="append", required=True, help="a Work item's id")
    parser.add_argument("--out", type=Path, help="the folder to write the handoff into")
    parser.add_argument("--since", help="epoch seconds, an ISO time, or 24h / 7d back")
    parser.add_argument("--check", action="store_true",
                        help="list pieces with no note or no evidence; writes nothing")
    args = parser.parse_args(argv)
    if not args.check and args.out is None:
        parser.error("give --out for the handoff, or --check")
    from jarvis import settings_service
    settings_service.load_once()    # a command's own process: the saved settings
    try:
        if args.check:
            lines = check(args.work)
            print("\n".join(lines))
            return 1 if any(not line.endswith("every piece has a note and evidence")
                            for line in lines) else 0
        path = write(args.work, args.out, parse_since(args.since, time.time()))
    except BriefingRefused as refused:
        print(refused, file=sys.stderr)
        return 2
    print(path)
    print("Add your own sender_answers (plainly, why_now: python -m jarvis.make_package's "
          "handoff, docs/making-a-package.md) to it, then ask for the package with "
          "python -m jarvis.make_package --start --handoff " + str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
