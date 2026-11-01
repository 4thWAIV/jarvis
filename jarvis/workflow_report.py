"""Report a workflow's steps to JARVIS's workflow monitor.

The session running a long workflow calls this at every step, so the one
durable record (workflow_store.py) always has the latest state and JARVIS
can say it. It needs an access key in the `workflows` category in
JARVIS_ACCESS_KEY (`python -m jarvis.access_keys create <label> workflows`), and
trusts the certificate given with --cert (the talk-to-jarvis skill gives
JARVIS's own). It reaches JARVIS at JARVIS_URL when that is set (a reporter
that runs outside this data folder says where JARVIS is), else at the address
the running server recorded (`server_address.running()`), else at
https://localhost:8340. A recorded loopback address (127.0.0.1 or ::1) is
dialled as localhost on the recorded scheme and port, because the certificate
names localhost and not the address. It runs as a plain script, under
whichever Python the session has, so it imports nothing outside the standard
library and the package's own `server_address`.

    python workflow_report.py [--cert <cert.pem>] start <name> --project <project> [--phase <phase> ...]
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
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if not __package__:
    # Run as a script (`python3 .../jarvis/workflow_report.py`): put the
    # package's parent on the path so this copy's own package is imported.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import server_address  # noqa: E402

DEFAULT_URL = "https://localhost:8340"
# Hosts the certificate does not name; a server recorded on one is dialled as localhost.
LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")
NOT_ANSWERED = 3


class _Response:
    def __init__(self, status_code: int, body: bytes, content_type: str):
        self.status_code, self.text = status_code, body.decode("utf-8", "replace")
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise SystemExit(f"JARVIS answered {self.status_code}: {self.text}")


class _Client:
    """Just enough HTTP for this command, with the standard library only, so
    it runs under whichever Python the reporting session has."""

    def __init__(self, base_url: str, key: str, cert: Path | None):
        self.base_url, self.key = base_url, key
        self.context = ssl.create_default_context(cafile=str(cert) if cert and cert.exists() else None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _send(self, method: str, path: str, body: dict | None = None,
              params: dict | None = None) -> _Response:
        url = self.base_url + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=10) as r:
                return _Response(r.status, r.read(), r.headers.get("content-type", ""))
        except urllib.error.HTTPError as e:
            return _Response(e.code, e.read(), e.headers.get("content-type", ""))

    def post(self, path: str, json: dict) -> _Response:
        return self._send("POST", path, body=json)

    def get(self, path: str, params: dict | None = None) -> _Response:
        return self._send("GET", path, params=params)


def _client(cert: Path | None) -> _Client:
    key = os.environ.get("JARVIS_ACCESS_KEY", "").strip()
    if not key:
        raise SystemExit("Set JARVIS_ACCESS_KEY to an access key in the workflows category.")
    return _Client(base_url(), key, cert)


def base_url() -> str:
    """JARVIS_URL when set, else the running server's recorded address (a
    loopback one as localhost), else DEFAULT_URL."""
    explicit = os.environ.get("JARVIS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    found = server_address.running()
    if found is None:
        return DEFAULT_URL
    address = found.address
    if address.host in LOOPBACK_ADDRESSES:
        address = address._replace(host="localhost")
    return address.url


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


def _post(client, path: str, body: dict) -> dict:
    r = client.post(path, json=body)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise SystemExit(f"JARVIS refused it ({r.status_code}): "
                         f"{data.get('error') or data.get('detail') or r.text}")
    return data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="workflow_report.py")
    parser.add_argument("--cert", type=Path)
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

    with _client(args.cert) as client:
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
