"""team_client.py — JARVIS's connection to a team server (team_server/).

Optional and entirely separate from voice and agents: nothing on an audio
path waits for it, and a team server that is down, slow or gone changes
only the Team section's status. All network work is async on the event
loop with bounded timeouts; Keychain calls run in a thread.

Connecting is device pairing (the server's /api/v1/device-authorizations):
this backend starts a ten-minute transaction with a random verifier it keeps
in memory, the page opens the server's approval URL, the user signs in with
GitHub there and approves this device with the matching code, and this
backend exchanges the transaction for a session. The session credential
goes straight into the macOS Keychain (`KeychainStore`); the page only ever
sees non-secret status. The saved connection file
(data_paths.team_connection_path) holds the server URL, its pinned server
ID and the last status.

While connected, `/api/v1/status` is read every STATUS_INTERVAL_SEC (30s)
with a REQUEST_TIMEOUT_SEC timeout and exponential backoff on failure. 401
or 403 means the session is over (expired, revoked, suspended): the
credential is deleted and the Team section asks to sign in again. Anything
else that fails leaves the credential alone and shows the last successful
contact.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlsplit

import httpx

from jarvis import data_paths

log = logging.getLogger("jarvis.team")

STATUS_INTERVAL_SEC = 30
REQUEST_TIMEOUT_SEC = 10
MAX_BACKOFF_SEC = 300
# Shown as stale after this long without a successful contact.
STALE_AFTER_SEC = 2 * STATUS_INTERVAL_SEC + REQUEST_TIMEOUT_SEC
LOGOUT_TIMEOUT_SEC = 5
API_VERSION = 1
KEYCHAIN_SERVICE = "JARVIS team server session"
# Origins allowed to break the URL rules (http, loopback/private addresses),
# for running a team server locally. Comma-separated, exact origins.
DEV_SERVERS_ENV = "JARVIS_TEAM_DEV_SERVERS"


class TeamError(Exception):
    """Something to tell the user, already in words they can act on.
    `unreachable` marks "the team server didn't answer"."""

    def __init__(self, message: str, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


# -- the Keychain ------------------------------------------------------------

class SecretStore(Protocol):
    def get(self, account: str) -> Optional[str]: ...
    def set(self, account: str, secret: str) -> None: ...
    def delete(self, account: str) -> None: ...


class KeychainStore:
    """The login Keychain, through Apple's `security` tool.

    The secret is written through `security -i` on stdin, never as a
    command-line argument, and read back from captured stdout. A write is
    confirmed by reading it back: `security -i` exits 0 even when the
    command it was fed failed.
    """

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self.service = service

    def get(self, account: str) -> Optional[str]:
        p = subprocess.run(["security", "find-generic-password", "-s", self.service,
                            "-a", account, "-w"], capture_output=True, text=True,
                           timeout=10)
        if p.returncode != 0:
            return None
        return p.stdout.rstrip("\n") or None

    def set(self, account: str, secret: str) -> None:
        if not secret or any(c in secret for c in " \t\r\n\"'\\"):
            raise TeamError("The team server sent an unusable session.")
        line = (f"add-generic-password -U -s \"{self.service}\" -a \"{account}\" "
                f"-w {secret}\n")
        subprocess.run(["security", "-i"], input=line, capture_output=True,
                       text=True, timeout=10)
        if self.get(account) != secret:
            raise TeamError("JARVIS couldn't save the team session in your Keychain.")

    def delete(self, account: str) -> None:
        subprocess.run(["security", "delete-generic-password", "-s", self.service,
                        "-a", account], capture_output=True, timeout=10)


# -- URL rules ---------------------------------------------------------------

def _dev_servers() -> set[str]:
    return {o.strip().rstrip("/") for o in os.getenv(DEV_SERVERS_ENV, "").split(",")
            if o.strip()}


def normalize_url(value: str) -> str:
    """The server's origin, or TeamError. https only, no path, query or
    credentials, except an origin listed in JARVIS_TEAM_DEV_SERVERS."""
    value = (value or "").strip().rstrip("/")
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        raise TeamError("Enter the team server's address, like https://team.example.com.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path:
        raise TeamError("Enter just the server's address, without a path.")
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    if parts.scheme != "https" and origin not in _dev_servers():
        raise TeamError("The team server's address must start with https://.")
    return origin


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


Resolver = Callable[[str, int], list[str]]


def _resolve(host: str, port: int) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM)})


async def check_destination(origin: str, resolver: Resolver = _resolve) -> None:
    """Refuse private, loopback and link-local destinations (unless the
    origin is a listed development server)."""
    if origin in _dev_servers():
        return
    parts = urlsplit(origin)
    try:
        addresses = await asyncio.to_thread(resolver, parts.hostname,
                                            parts.port or 443)
    except OSError:
        raise TeamError("JARVIS couldn't find that server — check the address.")
    if not addresses or not all(_public(a) for a in addresses):
        raise TeamError("That address is on a private network; JARVIS only "
                        "connects to public team servers.")


# -- status --------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _parse_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Pairing:
    server_url: str
    server_id: str
    user_code: str
    verification_uri_complete: str
    expires_at: float
    interval: int
    poll_token: str = field(repr=False)
    verifier: str = field(repr=False)
    task: Optional[asyncio.Task] = field(default=None, repr=False)


def _error_body(resp: httpx.Response) -> tuple[str, str]:
    try:
        err = resp.json().get("error") or {}
        return str(err.get("code") or ""), str(err.get("message") or "")
    except (ValueError, AttributeError):
        return "", ""


class TeamConnection:
    """One saved connection (or none), its status, and the pairing under way."""

    def __init__(self, store: Optional[SecretStore] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 resolver: Resolver = _resolve):
        self.store = store or KeychainStore()
        self._transport = transport
        self._resolver = resolver
        self.saved: Optional[dict] = None
        self.pairing: Optional[Pairing] = None
        self.last_error: Optional[str] = None
        # The address last paired with or disconnected from, so the Team
        # section can offer it again once a code expires or is cancelled.
        self.last_server_url: Optional[str] = None
        self.last_failure_at: Optional[float] = None
        self.failures = 0
        self.signed_out = False
        self._poller: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    # -- persistence -----------------------------------------------------------

    def load(self) -> None:
        path = data_paths.team_connection_path()
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("server_url") and data.get("server_id"):
                self.saved = data
                self.signed_out = bool(data.get("signed_out"))
        except (OSError, ValueError):
            self.saved = None

    def _save(self) -> None:
        path = data_paths.team_connection_path()
        if self.saved is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.saved["signed_out"] = self.signed_out
        data_paths._write_atomically(path, json.dumps(self.saved, indent=2))

    async def _secret(self) -> Optional[str]:
        if not self.saved:
            return None
        try:
            return await asyncio.to_thread(self.store.get, self.saved["server_id"])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("team: keychain read failed (%s)", type(e).__name__)
            return None

    async def _forget_secret(self, server_id: str) -> None:
        try:
            await asyncio.to_thread(self.store.delete, server_id)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("team: keychain delete failed (%s)", type(e).__name__)

    # -- HTTP ---------------------------------------------------------------------

    def _client(self, timeout: float = REQUEST_TIMEOUT_SEC) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0),
                                 follow_redirects=False, transport=self._transport,
                                 headers={"User-Agent": "JARVIS"})

    async def _request(self, method: str, url: str, *, token: Optional[str] = None,
                       json_body: Any = None, timeout: float = REQUEST_TIMEOUT_SEC
                       ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._client(timeout) as client:
                resp = await client.request(method, url, json=json_body, headers=headers)
        except httpx.HTTPError as e:
            raise TeamError(f"The team server didn't answer ({type(e).__name__}).",
                            unreachable=True) from None
        if 300 <= resp.status_code < 400:
            raise TeamError("The team server tried to redirect JARVIS elsewhere; "
                            "JARVIS doesn't follow team-server redirects.")
        return resp

    # -- connecting ------------------------------------------------------------------

    async def begin_pairing(self, server_url: str, device_name: str) -> dict:
        origin = normalize_url(server_url)
        await check_destination(origin, self._resolver)
        resp = await self._request("GET", origin + "/version")
        try:
            version = resp.json()
        except ValueError:
            version = {}
        if resp.status_code != 200 or version.get("service") != "jarvis-team-server":
            raise TeamError("That address isn't a JARVIS team server.")
        if version.get("api_version") != API_VERSION:
            raise TeamError("That team server speaks a different version of the "
                            "protocol; update JARVIS or the server.")
        server_id = str(version.get("server_id") or "")
        if not server_id:
            raise TeamError("The team server isn't ready yet; try again in a minute.")
        if (self.saved and self.saved["server_url"] == origin
                and self.saved["server_id"] != server_id):
            raise TeamError("This server's identity changed since JARVIS last "
                            "connected. Disconnect first if that's expected.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        resp = await self._request("POST", origin + "/api/v1/device-authorizations",
                                   json_body={"device_name": device_name[:64],
                                              "code_challenge": challenge,
                                              "code_challenge_method": "S256"})
        if resp.status_code != 200:
            code, message = _error_body(resp)
            raise TeamError(message or f"The team server refused to start pairing ({resp.status_code}).")
        body = resp.json()
        await self.cancel_pairing()
        expires = _parse_ts(body.get("expires_at")) or (_now() + 600)
        self.last_server_url = origin
        self.pairing = Pairing(
            server_url=origin, server_id=server_id, user_code=str(body["user_code"]),
            verification_uri_complete=str(body["verification_uri_complete"]),
            expires_at=expires, interval=max(2, int(body.get("interval") or 5)),
            poll_token=str(body["poll_token"]), verifier=verifier)
        if not self.pairing.verification_uri_complete.startswith(origin + "/"):
            self.pairing = None
            raise TeamError("The team server gave an approval link on another site.")
        self.last_error = None
        self.pairing.task = asyncio.create_task(self._await_approval(self.pairing))
        return self.snapshot()

    async def cancel_pairing(self) -> None:
        p, self.pairing = self.pairing, None
        if p and p.task and p.task is not asyncio.current_task():
            p.task.cancel()

    async def _await_approval(self, p: Pairing) -> None:
        url = p.server_url + "/api/v1/device-authorizations/exchange"
        try:
            while self.pairing is p:
                if _now() > p.expires_at:
                    self.last_error = "The pairing code expired. Start again."
                    break
                await asyncio.sleep(p.interval)
                try:
                    resp = await self._request("POST", url, json_body={
                        "poll_token": p.poll_token, "code_verifier": p.verifier})
                except TeamError:
                    continue  # a dropped poll; the next one may get through
                if resp.status_code == 202:
                    continue
                if resp.status_code == 429:
                    await asyncio.sleep(p.interval)
                    continue
                if resp.status_code == 200:
                    await self._adopt(p, resp.json())
                    break
                code, message = _error_body(resp)
                self.last_error = {
                    "access_denied": "The pairing was denied on the team server.",
                    "expired_token": "The pairing code expired. Start again.",
                }.get(code, message or f"Pairing failed ({resp.status_code}).")
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let a pairing take anything else down
            log.warning("team: pairing failed (%s)", type(e).__name__)
            self.last_error = "Pairing failed unexpectedly. Start again."
        finally:
            if self.pairing is p:
                self.pairing = None

    async def _adopt(self, p: Pairing, body: dict) -> None:
        token = str(body.get("session_token") or "")
        if body.get("server_id") != p.server_id:
            self.last_error = "The team server's identity changed during pairing."
            return
        old = self.saved
        old_token = await self._secret() if old else None
        await asyncio.to_thread(self.store.set, p.server_id, token)
        if old and old["server_id"] != p.server_id:
            await self._forget_secret(old["server_id"])
        if old_token and old_token != token and old and old["server_url"] == p.server_url:
            await self._remote_logout(old["server_url"], old_token)  # superseded session
        self.saved = {"server_url": p.server_url, "server_id": p.server_id,
                      "connected_at": _now(), "last_contact_at": _now()}
        self.signed_out = False
        self.failures = 0
        self.last_error = None
        self._apply_status({"team": body.get("team"), "me": body.get("me"),
                            "session_expires_at": body.get("session_expires_at")})
        self._save()
        log.info("team: connected to %s", p.server_url)
        self._wake.set()

    # -- status polling ---------------------------------------------------------------

    def _apply_status(self, status: dict) -> None:
        for key in ("team", "me", "session_expires_at", "recent_auth_until"):
            if status.get(key) is not None:
                self.saved[key] = status[key]

    async def poll_once(self) -> None:
        """One status read. Never raises."""
        async with self._lock:
            if not self.saved or self.signed_out:
                return
            token = await self._secret()
            if not token:
                self._sign_out("JARVIS has no saved session for this server; sign in again.")
                return
            try:
                resp = await self._request("GET", self.saved["server_url"] + "/api/v1/status",
                                           token=token)
            except TeamError as e:
                self._failed(str(e))
                return
            if resp.status_code in (401, 403):
                code, message = _error_body(resp)
                await self._forget_secret(self.saved["server_id"])
                self._sign_out(message or "The team server ended this session; sign in again.")
                return
            if resp.status_code != 200:
                self._failed(f"The team server answered {resp.status_code}.")
                return
            try:
                status = resp.json()
            except ValueError:
                self._failed("The team server sent an unreadable status.")
                return
            if status.get("server_id") != self.saved["server_id"]:
                await self._forget_secret(self.saved["server_id"])
                self._sign_out("The team server's identity changed; JARVIS stopped "
                               "using it. Disconnect and connect again if that's expected.")
                return
            self.failures = 0
            self.last_error = None
            self.saved["last_contact_at"] = _now()
            self._apply_status(status)
            self._save()

    def _failed(self, message: str) -> None:
        self.failures += 1
        self.last_error = message
        self.last_failure_at = _now()

    def _sign_out(self, message: str) -> None:
        self.signed_out = True
        self.last_error = message
        self._save()
        log.info("team: signed out (%s)", message)

    def next_delay(self) -> float:
        if self.failures == 0:
            return STATUS_INTERVAL_SEC
        return min(MAX_BACKOFF_SEC, STATUS_INTERVAL_SEC * (2 ** (self.failures - 1)))

    async def run(self) -> None:
        """The polling loop, for the server's lifespan."""
        while True:
            self._wake.clear()
            await self.poll_once()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.next_delay())
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self.load()
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self.run())

    async def stop(self) -> None:
        await self.cancel_pairing()
        if self._poller:
            self._poller.cancel()
            try:
                await self._poller
            except (asyncio.CancelledError, Exception):
                pass
            self._poller = None

    # -- disconnecting ---------------------------------------------------------------

    async def _remote_logout(self, server_url: str, token: str) -> bool:
        try:
            resp = await self._request("POST", server_url + "/api/v1/logout", token=token,
                                       timeout=LOGOUT_TIMEOUT_SEC)
        except TeamError:
            return False
        return resp.status_code in (200, 401)

    async def disconnect(self) -> dict:
        """Delete the local credential first, then try to end the session on
        the server. Offline, the credential is still gone."""
        await self.cancel_pairing()
        if not self.saved:
            return {"remote": "none", "message": "JARVIS wasn't connected to a team server."}
        saved = self.saved
        self.last_server_url = saved["server_url"]
        token = await self._secret()
        await self._forget_secret(saved["server_id"])
        self.saved = None
        self.signed_out = False
        self.last_error = None
        self.failures = 0
        self._save()
        if not token:
            return {"remote": "none", "message": "Disconnected."}
        if await self._remote_logout(saved["server_url"], token):
            return {"remote": "revoked", "message": "Disconnected and signed out on the server."}
        return {"remote": "unreachable",
                "message": "Disconnected. The team server couldn't be reached, so its "
                           "copy of the session ends on its own within 12 hours."}

    # -- administrator actions -----------------------------------------------------------

    async def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Forward one member-management call. Returns (status, JSON body)."""
        if not self.saved or self.signed_out:
            raise TeamError("JARVIS isn't connected to a team server.")
        token = await self._secret()
        if not token:
            raise TeamError("JARVIS has no saved session for this server; sign in again.")
        resp = await self._request(method, self.saved["server_url"] + path, token=token,
                                   json_body=body)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": {"code": "bad_response",
                                 "message": f"The team server answered {resp.status_code}."}}
        if resp.status_code == 401:
            await self._forget_secret(self.saved["server_id"])
            code, message = _error_body(resp)
            self._sign_out(message or "The team server ended this session; sign in again.")
        return resp.status_code, payload

    # -- what the page sees ---------------------------------------------------------------

    def state(self) -> str:
        if self.pairing:
            return "pairing"
        if not self.saved:
            return "disconnected"
        if self.signed_out:
            return "signed_out"
        last = self.saved.get("last_contact_at")
        if last is None or _now() - last > STALE_AFTER_SEC:
            return "stale" if self.failures else "connecting"
        return "connected"

    def snapshot(self) -> dict:
        saved = self.saved or {}
        p = self.pairing
        return {
            "state": self.state(),
            "server_url": saved.get("server_url") or (p.server_url if p else None),
            "last_server_url": saved.get("server_url") or self.last_server_url,
            "server_id": saved.get("server_id"),
            "team": saved.get("team"),
            "me": saved.get("me"),
            "session_expires_at": saved.get("session_expires_at"),
            "recent_auth_until": saved.get("recent_auth_until"),
            "last_contact_at": _iso(saved.get("last_contact_at")),
            "last_error": self.last_error,
            "poll_interval_sec": STATUS_INTERVAL_SEC,
            "pairing": None if p is None else {
                "user_code": p.user_code,
                "verification_uri_complete": p.verification_uri_complete,
                "expires_at": _iso(p.expires_at),
            },
        }


connection = TeamConnection()
