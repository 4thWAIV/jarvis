"""Report a piece of work, and who is doing each part of it, to JARVIS's
Work tab.

An agent tracking work on a ZIP work package or a folder calls this, so the
one durable record (work_store.py) always says what state it is in and
exactly who is on each piece. It needs an access key in the `work` category
in JARVIS_ACCESS_KEY (`python -m jarvis.access_keys create <label> work`),
and finds JARVIS and trusts its certificate the same way as
workflow_report.py, whose standard-library client it uses.

    python work_report.py [--cert <cert.pem>] start <title> (--zip <path> | --folder <path>) [--state <state>]
    python work_report.py update <id> <summary> [--state <state>]
    python work_report.py piece <id> <key> <summary> [--title <title>] [--who <who>] [--state <state>]
    python work_report.py decision <id> <key> <question> [--option <option> ...] [--recommendation <text>]
    python work_report.py answer <id> <key>

A state is planned, working, waiting_on_tim, blocked or done. Who is
`agent:<session id>`, `run:<run id>`, `workflow:<workflow id>` or `tim`; a
new piece needs one. `start` prints the item's id. `answer` prints the
user's answer to an open decision once he has given it, and exits 3 while
he has not.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if not __package__:
    # Run as a script: put the package's parent on the path, as workflow_report does.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.workflow_report import NOT_ANSWERED, _Client, _post, base_url  # noqa: E402


def _client(cert: Path | None) -> _Client:
    key = os.environ.get("JARVIS_ACCESS_KEY", "").strip()
    if not key:
        raise SystemExit("Set JARVIS_ACCESS_KEY to an access key in the work category.")
    return _Client(base_url(), key, cert)


def _who(text: str) -> dict:
    kind, _, ref = text.strip().partition(":")
    return {"kind": kind.lower(), "ref": ref or None}


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
    update = sub.add_parser("update")
    update.add_argument("id")
    update.add_argument("summary")
    update.add_argument("--state")
    piece = sub.add_parser("piece")
    piece.add_argument("id")
    piece.add_argument("key")
    piece.add_argument("summary")
    piece.add_argument("--title")
    piece.add_argument("--who")
    piece.add_argument("--state")
    decision = sub.add_parser("decision")
    decision.add_argument("id")
    decision.add_argument("key")
    decision.add_argument("question")
    decision.add_argument("--option", action="append", default=[])
    decision.add_argument("--recommendation")
    answer = sub.add_parser("answer")
    answer.add_argument("id")
    answer.add_argument("key")
    args = parser.parse_args(argv[1:])

    with _client(args.cert) as client:
        if args.command == "start":
            source = ({"kind": "zip", "path": os.path.abspath(args.zip)} if args.zip
                      else {"kind": "folder", "path": os.path.abspath(args.folder)})
            body = {"title": args.title, "source": source}
            if args.state:
                body["state"] = args.state
            print(_post(client, "/api/work", body)["id"])
            return 0
        if args.command == "answer":
            r = client.get(f"/api/work/{args.id}/events", params={"kinds": "decision_made"})
            r.raise_for_status()
            for e in reversed(r.json()["events"]):
                if e["detail"].get("key") == args.key:
                    print(e["detail"]["answer"])
                    return 0
            print(f"Decision {args.key} has not been answered yet.", file=sys.stderr)
            return NOT_ANSWERED
        if args.command == "update":
            kind, detail = "update", ({"state": args.state} if args.state else {})
        elif args.command == "piece":
            kind, detail = "piece", {"key": args.key}
            for name in ("title", "state"):
                if getattr(args, name):
                    detail[name] = getattr(args, name)
            if args.who:
                detail["who"] = _who(args.who)
        else:
            kind, detail = "decision_needed", {"key": args.key, "options": args.option}
            if args.recommendation:
                detail["recommendation"] = args.recommendation
        summary = args.question if args.command == "decision" else args.summary
        item = _post(client, f"/api/work/{args.id}/events",
                     {"kind": kind, "summary": summary, "detail": detail})["item"]
        print(_line(item))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
