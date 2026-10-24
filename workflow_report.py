"""Report a workflow's steps to JARVIS's workflow monitor.

The session running a long workflow calls this at every step, so the one
durable record (workflow_store.py) always has the latest state and JARVIS
can say it. It needs an access key in the `workflows` category in
JARVIS_ACCESS_KEY (`python access_keys.py create <label> workflows`), and
reaches JARVIS at JARVIS_URL (default https://localhost:8340).

    python workflow_report.py start <name> --project <project> [--phase <phase> ...]
    python workflow_report.py event <id> <kind> <summary> [--item <slug>] [--key <slug>]
        [--done N] [--total N] [--outcome succeeded|failed|cancelled|skipped]
        [--phase <slug>] [--title <title>] [--option <option> ...]
        [--recommendation <text>] [--until <when>] [--session <id>] [--provider <name>]
    python workflow_report.py answer <id> <key>

`start` prints the workflow's id. `answer` prints the user's answer to an
open decision once he has given it, and exits 3 while he has not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_URL = "https://localhost:8340"
NOT_ANSWERED = 3


def _client() -> httpx.Client:
    key = os.environ.get("JARVIS_ACCESS_KEY", "").strip()
    if not key:
        raise SystemExit("Set JARVIS_ACCESS_KEY to an access key in the workflows category.")
    cert = Path(__file__).with_name("cert.pem")
    return httpx.Client(base_url=os.environ.get("JARVIS_URL", DEFAULT_URL).rstrip("/"),
                        headers={"Authorization": f"Bearer {key}"},
                        verify=str(cert) if cert.exists() else True, timeout=10.0)


def _detail(args) -> dict:
    detail = {}
    for name in ("key", "done", "total", "outcome", "phase", "title", "recommendation",
                 "until", "provider"):
        value = getattr(args, name)
        if value is not None:
            detail[name] = value
    if args.option:
        detail["options"] = args.option
    if args.session:
        detail["session_id"] = args.session
    return detail


def _post(client: httpx.Client, path: str, body: dict) -> dict:
    r = client.post(path, json=body)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise SystemExit(f"JARVIS refused it ({r.status_code}): "
                         f"{data.get('error') or data.get('detail') or r.text}")
    return data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="workflow_report.py")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("name")
    start.add_argument("--project", required=True)
    start.add_argument("--phase", action="append", default=[])
    event = sub.add_parser("event")
    event.add_argument("id")
    event.add_argument("kind")
    event.add_argument("summary")
    event.add_argument("--item")
    for name in ("key", "outcome", "phase", "title", "recommendation", "until", "session",
                 "provider"):
        event.add_argument(f"--{name}")
    event.add_argument("--done", type=int)
    event.add_argument("--total", type=int)
    event.add_argument("--option", action="append", default=[])
    answer = sub.add_parser("answer")
    answer.add_argument("id")
    answer.add_argument("key")
    args = parser.parse_args(argv[1:])

    with _client() as client:
        if args.command == "start":
            print(_post(client, "/api/workflows", {"name": args.name, "project": args.project,
                                                   "phases": args.phase})["id"])
            return 0
        if args.command == "event":
            w = _post(client, f"/api/workflows/{args.id}/events",
                      {"kind": args.kind, "item": args.item, "summary": args.summary,
                       "detail": _detail(args)})["workflow"]
            print(f"{w['state']}: phase {w['phases_done']}/{w['phases_total']}, "
                  f"tasks {w['tasks_done']}/{w['tasks_total']}")
            return 0
        r = client.get(f"/api/workflows/{args.id}/events", params={"kinds": "decision_made"})
        r.raise_for_status()
        for e in reversed(r.json()["events"]):
            if e["detail"].get("key", e["item"]) == args.key and "answer" in e["detail"]:
                print(e["detail"]["answer"])
                return 0
        print(f"Decision {args.key} has not been answered yet.", file=sys.stderr)
        return NOT_ANSWERED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
