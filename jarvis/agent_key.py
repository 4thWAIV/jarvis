"""An agent's key, from the agent's own session: make it, submit it, grant.

    python -m jarvis.agent_key init --session <your session id>
    python -m jarvis.agent_key submit --session <your session id> --fingerprint <SHA256:...>
    python -m jarvis.agent_key status --session <your session id>
    python -m jarvis.agent_key grant --work <item id> --into <folder> --session <your session id>
    python -m jarvis.agent_key renew --work-key <SHA256:...> --session <your session id>

`init` makes the agent's Ed25519 key pair (or finds the one there) in
`<its config home>/jarvis/agent-keys/<session id>/` and prints its
fingerprint and the exact `submit` line to run. `submit` sends the public
key to JARVIS, signed by the key itself; JARVIS then looks in that
session's own transcript for this command naming the session and the
fingerprint (proof it was this session that ran it), and the key waits for
Tim to switch the agent on, which onboards it. Everything the agent sends
is signed with it (`python -m jarvis.message`, work_report.py,
workflow_report.py, make_package). `status` says where the key stands. `grant` makes a key limited to one Work
item in `<folder>/.jarvis/work-key` for whoever works there (as
`make_package --work` puts one in an agent package) and has JARVIS record
it, signed by your key; it ends when the item closes or after 30 days,
which `renew` extends. Run every one of them yourself, in your own session:
the proof is your own tool call.

Standard library only (agent_keys.py and workflow_report's client), so it
runs under whichever Python the session has.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import agent_keys  # noqa: E402
from jarvis.workflow_report import _Client, base_url  # noqa: E402

SUBMIT_ROUTE = "/api/agent-keys"


def submit_text(session_id: str, public_key: str) -> bytes:
    """What a submission's self-signature is over."""
    return "\n".join([agent_keys.SUBMIT, session_id, agent_keys.normalise(public_key)]
                     ).encode("utf-8")


def _key(session_id: str) -> tuple[Path, str]:
    found = agent_keys.session_key(session_id)
    if not found:
        raise SystemExit(f"Session {session_id} has no key yet: run "
                         f"python -m jarvis.agent_key init --session {session_id}")
    return found


def init(session_id: str) -> int:
    folder = agent_keys.key_folder(session_id)
    _private, public = agent_keys.make_pair(folder, f"jarvis-agent {session_id}")
    fp = agent_keys.fingerprint(public)
    print(f"Your key is in {folder} (the private half never leaves it).")
    print(f"Fingerprint: {fp}")
    print("Now submit it, yourself, in this session, with the same command you ran init with:")
    print(f"python -m jarvis.agent_key submit --session {session_id} --fingerprint {fp}")
    return 0


def submit(session_id: str, fingerprint: str, cert: Path | None) -> int:
    private, public = _key(session_id)
    if agent_keys.fingerprint(public) != fingerprint:
        raise SystemExit(f"Your key's fingerprint is {agent_keys.fingerprint(public)}, not "
                         f"{fingerprint}; give the one init printed.")
    signature = agent_keys.compact(agent_keys.sign(private, agent_keys.SUBMIT,
                                                   submit_text(session_id, public)))
    body = json.dumps({"session": session_id, "public_key": public,
                       "signature": signature}).encode()
    with _Client(base_url(), "", cert) as client:
        r = client.send_bytes("POST", client.base_url + SUBMIT_ROUTE, body, "application/json")
    if r.status_code != 200:
        raise SystemExit(f"JARVIS refused it ({r.status_code}): {r.text}")
    print(f"Submitted {fingerprint} for session {session_id}. JARVIS checks your own "
          "transcript for this command, then the key waits for Tim: switching your agent on "
          "in the Agents tab onboards it. Until then nothing you sign is taken as yours.")
    return 0


def _signed_post(session_id: str, cert: Path | None, route: str, body: dict) -> dict:
    private, public = _key(session_id)
    data = json.dumps(body).encode()
    with _Client(base_url(), "", cert, (private, public)) as client:
        r = client.send_bytes("POST", client.base_url + route, data, "application/json")
    if r.status_code != 200:
        raise SystemExit(f"JARVIS refused it ({r.status_code}): {r.text}")
    return r.json()


def grant(work_id: str, into: Path, session_id: str, cert: Path | None) -> int:
    folder = into.resolve().joinpath(agent_keys.WORK_KEY[0])
    _private, public = agent_keys.make_pair(folder, f"jarvis-work {work_id}",
                                            name=agent_keys.WORK_KEY[1])
    answer = _signed_post(session_id, cert, SUBMIT_ROUTE + "/work-keys",
                          {"work": work_id, "public_key": public})
    print(f"A key for work item {work_id} is in {folder} ({answer['fingerprint']}). Whoever "
          "works in that folder reports on that item with it, and on nothing else.")
    return 0


def renew(fingerprint: str, session_id: str, cert: Path | None) -> int:
    answer = _signed_post(session_id, cert,
                          SUBMIT_ROUTE + "/work-keys/" + fingerprint + "/renew", {})
    print(f"Renewed {answer['fingerprint']} for another 30 days.")
    return 0


def status(session_id: str, cert: Path | None) -> int:
    found = agent_keys.session_key(session_id)
    if not found:
        print(f"Session {session_id} has no key: run init.")
        return 1
    private, public = found
    url = base_url() + SUBMIT_ROUTE + "/mine"
    with _Client(base_url(), "", cert, (private, public)) as client:
        r = client.send_bytes("GET", url, None, "application/json")
    print(f"Fingerprint {agent_keys.fingerprint(public)}: " + (
        r.json().get("state", "unknown") if r.status_code == 200 else
        f"JARVIS answered {r.status_code}: {r.text}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.agent_key")
    parser.add_argument("--cert", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "submit", "status", "grant", "renew"):
        p = sub.add_parser(name)
        p.add_argument("--session", required=True)
        if name == "submit":
            p.add_argument("--fingerprint", required=True)
        if name == "grant":
            p.add_argument("--work", required=True)
            p.add_argument("--into", type=Path, required=True)
        if name == "renew":
            p.add_argument("--work-key", dest="work_key", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return init(args.session)
        if args.command == "submit":
            return submit(args.session, args.fingerprint, args.cert)
        if args.command == "grant":
            return grant(args.work, args.into, args.session, args.cert)
        if args.command == "renew":
            return renew(args.work_key, args.session, args.cert)
        return status(args.session, args.cert)
    except agent_keys.AgentKeyError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
