"""Report a piece of work, and who is doing each part of it, to JARVIS's
Work tab.

An agent tracking work on a ZIP work package or a folder calls this, so the
one durable record (work_store.py) always says what state it is in and
exactly who is on each piece. It needs an access key in the `work` category
in JARVIS_ACCESS_KEY (`python -m jarvis.access_keys create <label> work`),
and finds JARVIS and trusts its certificate the same way as
workflow_report.py, whose standard-library client it uses.

    python work_report.py [--cert <cert.pem>] start <title> (--zip <path> | --folder <path>) [--state <state>] [--area <area>]
    python work_report.py update <id> <summary> [--state <state>] [--area <area>] [--until <hold> ...] [--cleared]
    python work_report.py piece <id> <key> <summary> [--title <title>] [--who <who>] [--state <state>]
                          [--worker <label>] [--after <key>[,<key>...]] [--evidence <commit|path|url> ...]
                          [--until <hold> ...] [--then <state>] [--cleared]
    python work_report.py decision <id> <key> <question> [--option <option> ...] [--recommendation <text>]
                          [--package <package id or name>]
    python work_report.py answer <id> <key>
    python work_report.py answers [--since <epoch seconds or ISO time>]
    python work_report.py withdraw <id> <key> <reason>
    python work_report.py close <id> [<summary>]

A reported state is planned, ready, working, verifying, waiting_on_tim or
blocked (done is his approval). A working report is intent only: the public
view reads activity_unknown until current token generation for the exact
assignment can be confirmed.
Who is `agent:<session id>`, `run:<run id>`, `workflow:<workflow id>` or
`tim`; a new piece needs one. A piece's summary is its latest note. An area
groups items on the Work tab (an empty one goes back to the source's
folder); `--worker` is your own words for who did a piece, shown beside its
owner; `--after` names the pieces it waits on (an empty value clears them);
each `--evidence` is added to the piece's.

A piece that waits on the user (`--state waiting_on_tim`) or is blocked
(`--state blocked`) is held, and must name what holds it with `--until`
(repeat it for several): `decision:<key>` (one of this item's open
decisions), `work:<item id>:<key>`, `workflow:<workflow id>:<key>` or
`package:<id or name>[:<decision id>]`; a blocker may instead be words.
Once every one is answered (or its package signed off, or its decision
withdrawn), JARVIS moves the piece on to `--then` (ready by default)
itself. Nothing else moves a held piece on: a report that would is refused,
naming what holds it, so work that can go on meanwhile is a piece of its
own. A blocker given in words is cleared by reporting the piece's next
state with `--cleared`. An item with pieces takes its state from them and
is never reported waiting or blocked itself (an item with none follows the
same rules as a piece). A decision's `--package` names
a page package (by id or name) the user can open from the decision. `withdraw`
takes back a decision you asked, with why (only its asker can; the user's
answer is never yours to give). `close`
moves the item to History for good (nothing is deleted, and nothing more
can be reported to it); a done item moves there by itself after a few
minutes. `start` prints the item's id. `answer` prints the
user's answer to an open decision once he has given it, and exits 3 while
he has not; it exits 4 when the decision was closed without an answer
because the user signed off the package it names without answering it.
`answers` prints every answer he has given to the decisions your access
key asked, since the time given (all of them without one), one per line:
item, key, when and his answer. JARVIS also writes each answer to
`<data>/answers/<project>/<item>-<key>.md` the moment he gives it.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

if not __package__:
    # Run as a script: put the package's parent on the path, as workflow_report does.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.workflow_report import NOT_ANSWERED, _Client, _post, base_url  # noqa: E402

# `answer`'s exit when the decision was closed by his signing off its package
# without answering it (work_store.DECISION_SIGNED_OFF).
SIGNED_OFF = 4


def _client(cert: Path | None) -> _Client:
    key = os.environ.get("JARVIS_ACCESS_KEY", "").strip()
    if not key:
        raise SystemExit("Set JARVIS_ACCESS_KEY to an access key in the work category.")
    return _Client(base_url(), key, cert)


def _who(text: str) -> dict:
    kind, _, ref = text.strip().partition(":")
    return {"kind": kind.lower(), "ref": ref or None}


def _evidence(text: str) -> str:
    """A relative path is made absolute where the agent runs, as --folder
    is; a commit or a URL goes as it is."""
    text = text.strip()
    if "://" in text or not (os.sep in text or text.startswith(".") or os.path.exists(text)):
        return text
    return os.path.abspath(os.path.expanduser(text))


def _since(text: str | None) -> float:
    """Epoch seconds, or an ISO 8601 time (local when it gives no zone)."""
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except ValueError:
        raise SystemExit("--since is epoch seconds or an ISO time, e.g. 2026-11-07T15:05.")


def _line(item: dict) -> str:
    on = ", ".join(f"{p['key']}: {p['who']['name'] or p['who']['kind']}" for p in item["pieces"])
    return f"{item['id']} {item['state']}" + (f" ({on})" if on else "")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="work_report.py")
    parser.add_argument("--cert", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("title")
    where = start.add_mutually_exclusive_group(required=True)
    where.add_argument("--zip")
    where.add_argument("--folder")
    start.add_argument("--state")
    start.add_argument("--area")
    update = sub.add_parser("update")
    update.add_argument("id")
    update.add_argument("summary")
    update.add_argument("--state")
    update.add_argument("--area")
    update.add_argument("--until", action="append")
    update.add_argument("--cleared", action="store_true")
    piece = sub.add_parser("piece")
    piece.add_argument("id")
    piece.add_argument("key")
    piece.add_argument("summary")
    piece.add_argument("--title")
    piece.add_argument("--who")
    piece.add_argument("--state")
    piece.add_argument("--worker")
    piece.add_argument("--after")
    piece.add_argument("--evidence", action="append", default=[])
    piece.add_argument("--until", action="append")
    piece.add_argument("--then")
    piece.add_argument("--cleared", action="store_true")
    decision = sub.add_parser("decision")
    decision.add_argument("id")
    decision.add_argument("key")
    decision.add_argument("question")
    decision.add_argument("--option", action="append", default=[])
    decision.add_argument("--recommendation")
    decision.add_argument("--package")
    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("id")
    withdraw.add_argument("key")
    withdraw.add_argument("reason")
    close = sub.add_parser("close")
    close.add_argument("id")
    close.add_argument("summary", nargs="?", default="Closed.")
    answer = sub.add_parser("answer")
    answer.add_argument("id")
    answer.add_argument("key")
    answers = sub.add_parser("answers")
    answers.add_argument("--since")
    args = parser.parse_args(argv[1:])

    with _client(args.cert) as client:
        if args.command == "start":
            source = ({"kind": "zip", "path": os.path.abspath(args.zip)} if args.zip
                      else {"kind": "folder", "path": os.path.abspath(args.folder)})
            body = {"title": args.title, "source": source}
            for name in ("state", "area"):
                if getattr(args, name):
                    body[name] = getattr(args, name)
            print(_post(client, "/api/work", body)["id"])
            return 0
        if args.command == "answer":
            r = client.get(f"/api/work/{args.id}/events",
                           params={"kinds": "decision_made,decision_signed_off"})
            r.raise_for_status()
            for e in reversed(r.json()["events"]):
                if e["detail"].get("key") == args.key:
                    if e["kind"] == "decision_signed_off":
                        print(f"Decision {args.key} was closed without an answer: the user "
                              f"signed off its package without answering it.", file=sys.stderr)
                        return SIGNED_OFF
                    print(e["detail"]["answer"])
                    return 0
            print(f"Decision {args.key} has not been answered yet.", file=sys.stderr)
            return NOT_ANSWERED
        if args.command == "answers":
            r = client.get("/api/work/answers", params={"since": _since(args.since)})
            r.raise_for_status()
            for a in r.json()["answers"]:
                when = datetime.datetime.fromtimestamp(a["answered_at"]).isoformat(timespec="seconds")
                print(f"{a['item_id']} {a['key']} {when}: {a['answer']}")
            return 0
        if args.command == "update":
            kind, detail = "update", {}
            if args.state:
                detail["state"] = args.state
            if args.area is not None:
                detail["area"] = args.area
            if args.until:
                detail["until"] = args.until
            if args.cleared:
                detail["cleared"] = True
        elif args.command == "close":
            kind, detail = "closed", {}
        elif args.command == "withdraw":
            kind, detail = "decision_withdrawn", {"key": args.key}
        elif args.command == "piece":
            kind, detail = "piece", {"key": args.key}
            for name in ("title", "state", "until", "then"):
                if getattr(args, name):
                    detail[name] = getattr(args, name)
            if args.who:
                detail["who"] = _who(args.who)
            if args.worker is not None:
                detail["worker"] = args.worker
            if args.after is not None:
                detail["after"] = [k.strip() for k in args.after.split(",") if k.strip()]
            if args.evidence:
                detail["evidence"] = [_evidence(e) for e in args.evidence]
            if args.cleared:
                detail["cleared"] = True
        else:
            kind, detail = "decision_needed", {"key": args.key, "options": args.option}
            if args.recommendation:
                detail["recommendation"] = args.recommendation
            if args.package:
                detail["package"] = args.package
        summary = (args.question if args.command == "decision"
                   else args.reason if args.command == "withdraw" else args.summary)
        item = _post(client, f"/api/work/{args.id}/events",
                     {"kind": kind, "summary": summary, "detail": detail})["item"]
        print(_line(item))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
