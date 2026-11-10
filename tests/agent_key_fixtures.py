"""A stand-in for ssh-keygen at agent_keys.RUNNER, and an agent that holds
a key: what every agent-key test signs and checks with.

FakeKeygen answers the three calls agent_keys makes the way ssh-keygen
does: `-t ed25519 -f <file>` writes a private file (0600) and a real-shaped
`ssh-ed25519` public key; `-Y sign` prints an armored SSHSIG whose blob
names that public key and the namespace, so agent_keys.signed_by reads it
as it reads a real one; `-Y verify` checks it against the allowed-signers
file. The "signature" is a hash keyed by the public key, so a forged or
changed one fails, as a real one would. One test marked `ssh_keygen` runs
the real tool instead.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from jarvis import agent_keys


def _string(b: bytes) -> bytes:
    return len(b).to_bytes(4, "big") + b


def _blob(raw: bytes) -> bytes:
    return _string(b"ssh-ed25519") + _string(raw)


class FakeKeygen:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, data=None, timeout=None):
        self.calls.append(list(args))
        data = data or b""
        if "-t" in args:
            path = Path(args[args.index("-f") + 1])
            secret = os.urandom(32)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, secret.hex().encode())
            os.close(fd)
            raw = hashlib.sha256(secret).digest()
            path.with_name(path.name + ".pub").write_text(
                f"ssh-ed25519 {base64.b64encode(_blob(raw)).decode()} fake\n")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["-Y", "sign"]:
            private = Path(args[args.index("-f") + 1])
            namespace = args[args.index("-n") + 1].encode()
            raw = hashlib.sha256(bytes.fromhex(private.read_text())).digest()
            return subprocess.CompletedProcess(args, 0, self.armored(raw, namespace, data), b"")
        if args[:2] == ["-Y", "verify"]:
            allowed = Path(args[args.index("-f") + 1]).read_text().split()
            namespace = args[args.index("-n") + 1].encode()
            raw = _raw(allowed[2])
            sig = Path(args[args.index("-s") + 1]).read_text()
            good = sig == self.armored(raw, namespace, data).decode()
            return subprocess.CompletedProcess(args, 0 if good else 255, b"", b"")
        raise AssertionError(f"unexpected ssh-keygen call {args}")

    @staticmethod
    def armored(raw: bytes, namespace: bytes, data: bytes) -> bytes:
        mac = hashlib.sha256(raw + b"|" + namespace + b"|" + data).digest()
        blob = (b"SSHSIG" + (1).to_bytes(4, "big") + _string(_blob(raw)) + _string(namespace)
                + _string(b"") + _string(b"sha512") + _string(_string(b"ssh-ed25519") +
                                                            _string(mac)))
        return agent_keys.armor(base64.b64encode(blob).decode()).encode()


def _raw(public_b64: str) -> bytes:
    blob = base64.b64decode(public_b64)
    n = int.from_bytes(blob[:4], "big")
    m = int.from_bytes(blob[4 + n:8 + n], "big")
    return blob[8 + n:8 + n + m]


class Agent:
    """An agent with its own key in `home` (its config home)."""

    def __init__(self, home: Path, session_id: str):
        self.home, self.session_id = home, session_id
        self.private, self.public = agent_keys.make_pair(
            agent_keys.key_folder(session_id, home), f"jarvis-agent {session_id}")
        self.fingerprint = agent_keys.fingerprint(self.public)

    def headers(self, method: str, path: str, body: bytes = b"",
                host: str = "testserver", now: float | None = None) -> dict:
        return {"Authorization": agent_keys.request_header(
            self.private, self.public, method, f"http://{host}{path}", body, now)}

    def post(self, client, path: str, body: dict | None = None):
        data = json.dumps(body or {}).encode()
        return client.post(path, content=data, headers={
            **self.headers("POST", path, data), "Content-Type": "application/json"})

    def get(self, client, path: str):
        return client.get(path, headers=self.headers("GET", path))

    def onboarded(self, by: str = "page") -> "Agent":
        """Submitted, proven and onboarded straight through the store, for
        tests about what an onboarded key does."""
        from jarvis import agent_key_store
        key = agent_key_store.submit(self.public, self.session_id)
        if key.state == agent_key_store.ONBOARDED:
            return self
        agent_key_store.proven(key.fingerprint, "transcript")
        assert agent_key_store.onboard(self.session_id, by) is not None
        return self

    def submit_line(self) -> dict:
        """The transcript line of this agent's assistant running its submit
        command, as Claude Code writes it."""
        return {"type": "assistant", "timestamp": time.time(), "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {
                "command": f"python -m jarvis.agent_key submit --session {self.session_id} "
                           f"--fingerprint {self.fingerprint}"}}]}}


class AgentAuth(httpx.Auth):
    """Signs every request a TestClient sends with one agent's key, as the
    report commands do: `client.post(path, json=..., auth=AgentAuth(agent))`
    or `TestClient(app, auth=...)`."""
    requires_request_body = True

    def __init__(self, agent: Agent):
        self.agent = agent

    def auth_flow(self, request):
        request.headers["Authorization"] = agent_keys.request_header(
            self.agent.private, self.agent.public, request.method, str(request.url),
            request.content)
        yield request


_sessions = itertools.count()
_granted: set[str] = set()


def onboarded_auth(rights: tuple[str, ...] | None = None, session: str | None = None
                   ) -> AgentAuth:
    """A new onboarded agent's signing (the default rights, or exactly
    `rights`), for a test about what a signed request does."""
    from contextlib import closing
    from jarvis import agent_key_store, agent_rights
    from jarvis import run_store
    session = session or f"sess-test-{next(_sessions):04d}"
    run_store.init_db()
    if session not in run_store.agent_names():
        # An onboarded agent is one on the agents list (keys of agents
        # dropped from it are revoked).
        running = {r["session_id"]: (r["name"], r["state"]) for r in run_store.list_agents()
                   if not r.get("ended_at")}
        running[session] = (session.removeprefix("sess-"), "idle")
        run_store.sync_agents(running)
    agent = Agent(agent_keys.config_home(session), session).onboarded()
    if rights is not None and agent.fingerprint not in _granted:
        _granted.add(agent.fingerprint)
        principal = agent_key_store.agent_principal(session)
        with closing(agent_key_store._connect()) as conn, conn:
            conn.execute("DELETE FROM agent_rights WHERE principal=?", (principal,))
            agent_key_store._grant(conn, principal, rights, agent_rights.ANY_SCOPE, "test",
                                   time.time())
    return AgentAuth(agent)


def signing(client, auth: AgentAuth):
    """A client whose every request `auth` signs (the reporter's own)."""
    client.auth = auth
    return client
