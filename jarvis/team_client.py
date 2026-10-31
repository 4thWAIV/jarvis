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
contact. The status also carries the team's `network_account_key`, which is
kept in memory only (never in the saved connection, the snapshot or a log).

The network exchange is the other loop while paired: every
NETWORK_INTERVAL_SEC (5s) on the monotonic clock, one node update (built by
the caller's `node_update`, given that key) goes to
/api/v1/network/nodes/self, and the team server's answer, the whole team's
NetworkSnapshot, is stored verbatim as the network cache (`NetworkCache`):
never merged with a local value. One request in flight, a
NETWORK_TIMEOUT_SEC timeout, published at once on start and on (re)pairing,
backing off on failure up to NETWORK_MAX_BACKOFF_SEC and never sooner than a
Retry-After. A 503 carrying the server's health keeps the last snapshot's
nodes, takes that health as its `server` and is stale; any other failure
keeps the last snapshot, stale, with no round trip. Whether the session is
still good is the status poll's to judge: a 401 or 403 on the exchange wakes
it rather than signing out. Each change to the cache calls `on_network`
(the `network` hint on /ws/sessions); `network_status()` is GET
/api/team/network's body.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import random
import secrets
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional, Protocol
from urllib.parse import urlsplit

import httpx

from jarvis import data_paths

log = logging.getLogger("jarvis.team")

STATUS_INTERVAL_SEC = 30
REQUEST_TIMEOUT_SEC = 10
# A streamed request's limit on each read and write, not on the whole
# transfer: a large file keeps going while bytes keep moving.
STREAM_TIMEOUT_SEC = 60
MAX_BACKOFF_SEC = 300
# Shown as stale after this long without a successful contact.
STALE_AFTER_SEC = 2 * STATUS_INTERVAL_SEC + REQUEST_TIMEOUT_SEC
LOGOUT_TIMEOUT_SEC = 5
API_VERSION = 1
KEYCHAIN_SERVICE = "JARVIS team server session"
# Origins allowed to break the URL rules (http, loopback/private addresses),
# for running a team server locally. Comma-separated, exact origins.
DEV_SERVERS_ENV = "JARVIS_TEAM_DEV_SERVERS"

NETWORK_PATH = "/api/v1/network/nodes/self"
NETWORK_INTERVAL_SEC = 5.0
# One interval: a slow answer never queues old samples behind it.
NETWORK_TIMEOUT_SEC = 5.0
NETWORK_MAX_BACKOFF_SEC = 60.0
# The handoff's bound: no accepted update for this long is stale.
NETWORK_STALE_AFTER_SEC = 15.0
# The first exchange waits up to this long, so backends started together
# do not all publish in the same instant.
NETWORK_STARTUP_JITTER_SEC = 1.0

# The states in which this JARVIS is not signed in to a team server: never
# paired (or disconnected), signed out, or part way through signing in. The
# Team Server tile says Sign in, the page shows its reminder and JARVIS says
# so once a start (team_reminder.py); network.ts's needsSignIn is the page's
# side of the same set.
NOT_SIGNED_IN = frozenset({"disconnected", "signed_out", "pairing"})

# Builds the node update from the local collectors, given the team's key
# (or None, and then no brains).
NodeUpdateBuilder = Callable[[Optional[str]], dict]


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


def iso_utc(ts: Optional[float]) -> Optional[str]:
    """An epoch time as UTC ISO 8601 with a `Z`, the form the team server's
    models take; the one formatter for times JARVIS sends or shows."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _backoff(failures: int, interval: float, ceiling: float) -> float:
    """Doubling from `interval` with each failure in a row, up to `ceiling`."""
    if failures == 0:
        return interval
    return min(ceiling, interval * (2 ** (failures - 1)))


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


@dataclass
class NetworkCache:
    """The last network snapshot the team server sent, as its bytes, and
    the few facts only this JARVIS knows about it."""
    raw: Optional[bytes] = None
    revision: Optional[int] = None
    received_at: Optional[float] = None
    failed: bool = False
    last_error: Optional[str] = None
    round_trip_ms: Optional[int] = None
    failures: int = 0
    retry_after: float = 0.0


def _snapshot_of(raw: bytes) -> Optional[dict]:
    """The NetworkSnapshot in a 200's body, or None when it isn't one."""
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if (not isinstance(body, dict) or not isinstance(body.get("revision"), int)
            or not isinstance(body.get("you_node_id"), str)
            or not isinstance(body.get("nodes"), list)
            or not isinstance(body.get("server"), dict)):
        return None
    return body


def _health_of(resp: httpx.Response) -> Optional[dict]:
    """The ServerHealth a 503 from the network route carries, or None."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("database") in ("connected", "unreachable"):
        return body
    return None


def _retry_after(resp: httpx.Response) -> float:
    """A Retry-After in seconds, bounded; 0 for none or a form not read."""
    value = resp.headers.get("retry-after", "")
    return float(min(int(value), MAX_BACKOFF_SEC)) if value.isdigit() else 0.0


async def cancel_task(task: Optional[asyncio.Task]) -> None:
    """Cancel a background task and wait until it has stopped."""
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class TeamConnection:
    """One saved connection (or none), its status, the pairing under way and
    the network exchange."""

    def __init__(self, store: Optional[SecretStore] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 resolver: Resolver = _resolve):
        self.store = store or KeychainStore()
        self._transport = transport
        self._resolver = resolver
        self.saved: Optional[dict] = None
        # Moves whenever the pairing changes (a new server or owner, or a
        # disconnect), so an answer to a request made under the old one is
        # discarded (messaging_client.TeamTransport). Signing out keeps it.
        self.generation = 0
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
        # The team's key for brain account references, from the status:
        # memory only.
        self._account_key: Optional[str] = None
        self.network = NetworkCache()
        self.on_network: Callable[[], None] = lambda: None
        self._node_update: Optional[NodeUpdateBuilder] = None
        self._exchanger: Optional[asyncio.Task] = None
        self._network_wake = asyncio.Event()

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
            self._account_key = None
            self._clear_network()
        if old_token and old_token != token and old and old["server_url"] == p.server_url:
            await self._remote_logout(old["server_url"], old_token)  # superseded session
        self.saved = {"server_url": p.server_url, "server_id": p.server_id,
                      "connected_at": _now(), "last_contact_at": _now()}
        self.generation += 1
        self.signed_out = False
        self.failures = 0
        self.last_error = None
        self._apply_status({"team": body.get("team"), "me": body.get("me"),
                            "session_expires_at": body.get("session_expires_at")})
        self._save()
        log.info("team: connected to %s", p.server_url)
        self._wake.set()
        self._network_wake.set()

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
            key = status.get("network_account_key")
            self._account_key = key if isinstance(key, str) and key else None
            self._save()
            if self.network.failures:
                self._network_wake.set()   # back in contact: publish now

    def _failed(self, message: str) -> None:
        self.failures += 1
        self.last_error = message
        self.last_failure_at = _now()

    def _sign_out(self, message: str) -> None:
        self.signed_out = True
        self.last_error = message
        self._account_key = None
        self._save()
        log.info("team: signed out (%s)", message)
        if self.network.raw is not None:
            self._network_failed(message)

    def next_delay(self) -> float:
        return _backoff(self.failures, STATUS_INTERVAL_SEC, MAX_BACKOFF_SEC)

    async def run(self) -> None:
        """The polling loop, for the server's lifespan."""
        while True:
            self._wake.clear()
            await self.poll_once()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.next_delay())
            except asyncio.TimeoutError:
                pass

    def start(self, node_update: Optional[NodeUpdateBuilder] = None,
              on_network: Optional[Callable[[], None]] = None) -> None:
        """Start the status poll and, given a node update builder, the
        network exchange."""
        self.load()
        if on_network is not None:
            self.on_network = on_network
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self.run())
        if node_update is not None:
            self._node_update = node_update
            if self._exchanger is None or self._exchanger.done():
                self._exchanger = asyncio.create_task(self.run_network())

    async def stop(self) -> None:
        await self.cancel_pairing()
        for task in (self._poller, self._exchanger):
            await cancel_task(task)
        self._poller = self._exchanger = None

    # -- the network exchange ----------------------------------------------------------

    def _paired(self) -> bool:
        return bool(self.saved) and not self.signed_out

    def _network_changed(self) -> None:
        try:
            self.on_network()
        except Exception as e:  # a hint that fails must not stop the exchange
            log.warning("team: network hint failed (%s)", type(e).__name__)

    def _network_failed(self, message: str, *, round_trip_ms: Optional[int] = None,
                        retry_after: float = 0.0) -> None:
        """A failed exchange: the last snapshot stays, stale. Only a 503
        with health keeps a round trip (`round_trip_ms`)."""
        n = self.network
        n.failed = True
        n.failures += 1
        n.last_error = message
        n.round_trip_ms = round_trip_ms
        n.retry_after = retry_after
        self._network_changed()

    def _clear_network(self) -> None:
        self.network = NetworkCache()
        self._network_changed()

    def _store_snapshot(self, raw: bytes, revision: int, round_trip_ms: int) -> None:
        n = self.network
        if n.revision is None or revision >= n.revision:
            n.raw, n.revision, n.received_at = raw, revision, _now()
        n.failed = False
        n.failures = 0
        n.last_error = None
        n.round_trip_ms = round_trip_ms
        n.retry_after = 0.0
        self._network_changed()

    def _store_health(self, health: dict, round_trip_ms: int) -> None:
        """A 503 with the server's health: the last snapshot's nodes, the
        new health. Without a snapshot yet there is nothing to put it in."""
        if self.network.raw is not None:
            snapshot = json.loads(self.network.raw)
            snapshot["server"] = health
            self.network.raw = json.dumps(snapshot, separators=(",", ":")).encode()
        self._network_failed("The team server can't reach its database.",
                             round_trip_ms=round_trip_ms)

    async def exchange_once(self) -> None:
        """One node update out, one snapshot back. Never raises."""
        if not self._paired() or self._node_update is None:
            return
        saved = self.saved
        token = await self._secret()
        if not token:
            self._network_failed("JARVIS has no saved session for this server.")
            self._wake.set()           # the status poll decides what that means
            return
        try:
            body = self._node_update(self._account_key)
        except Exception as e:
            log.warning("team: the node update could not be built (%s)", type(e).__name__)
            self._network_failed("JARVIS couldn't put together its node update.")
            return
        started = time.monotonic()
        try:
            resp = await self._request("POST", saved["server_url"] + NETWORK_PATH,
                                       token=token, json_body=body,
                                       timeout=NETWORK_TIMEOUT_SEC)
        except TeamError as e:
            if self.saved is saved and self._paired():
                self._network_failed(str(e))
            return
        round_trip_ms = round((time.monotonic() - started) * 1000)
        if self.saved is not saved or not self._paired():
            return                     # signed out or disconnected meanwhile
        if resp.status_code == 200:
            snapshot = _snapshot_of(resp.content)
            if snapshot is None:
                self._network_failed("The team server sent an unreadable network snapshot.")
            else:
                self._store_snapshot(resp.content, snapshot["revision"], round_trip_ms)
            return
        if resp.status_code == 503 and (health := _health_of(resp)) is not None:
            self._store_health(health, round_trip_ms)
            return
        _, message = _error_body(resp)
        self._network_failed(message or f"The team server answered {resp.status_code}.",
                             retry_after=_retry_after(resp))
        if resp.status_code in (401, 403):
            self._wake.set()           # the status poll decides whether it's over

    def network_delay(self) -> float:
        """How long after the start of one exchange the next one starts."""
        n = self.network
        return max(_backoff(n.failures, NETWORK_INTERVAL_SEC, NETWORK_MAX_BACKOFF_SEC),
                   n.retry_after)

    async def run_network(self) -> None:
        """The exchange loop, for the server's lifespan: one at a time,
        scheduled on the monotonic clock; idle while not paired."""
        await asyncio.sleep(random.uniform(0, NETWORK_STARTUP_JITTER_SEC))
        while True:
            self._network_wake.clear()
            started = time.monotonic()
            delay: Optional[float] = None
            if self._paired():
                await self.exchange_once()
                delay = max(0.0, self.network_delay() - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._network_wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def network_status(self) -> bytes:
        """GET /api/team/network's body: the stored snapshot exactly as the
        team server sent it (or null), and this JARVIS's own facts about
        it. The snapshot's bytes are passed through, not re-encoded."""
        n = self.network
        stale = n.raw is not None and (
            n.failed or n.received_at is None
            or _now() - n.received_at > NETWORK_STALE_AFTER_SEC)
        local = json.dumps({
            "received_at": iso_utc(n.received_at),
            "stale": stale,
            "last_error": n.last_error,
            "round_trip_ms": n.round_trip_ms,
            "last_contact_at": iso_utc((self.saved or {}).get("last_contact_at")),
            "connection": self.state(),
        }).encode()
        return b'{"snapshot":' + (n.raw or b"null") + b"," + local[1:]

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
        self.generation += 1
        self.signed_out = False
        self._account_key = None
        self._clear_network()
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

    async def _session(self) -> tuple[str, str]:
        """The saved server's URL and this session's credential."""
        if not self.saved or self.signed_out:
            raise TeamError("JARVIS isn't connected to a team server.")
        token = await self._secret()
        if not token:
            raise TeamError("JARVIS has no saved session for this server; sign in again.")
        return self.saved["server_url"], token

    async def _ended(self, resp: httpx.Response) -> None:
        """A 401 ends the session: the credential goes and the Team section
        asks to sign in again."""
        if resp.status_code == 401:
            await self._forget_secret(self.saved["server_id"])
            code, message = _error_body(resp)
            self._sign_out(message or "The team server ended this session; sign in again.")

    async def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Forward one member-management call. Returns (status, JSON body)."""
        server_url, token = await self._session()
        resp = await self._request(method, server_url + path, token=token, json_body=body)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": {"code": "bad_response",
                                 "message": f"The team server answered {resp.status_code}."}}
        await self._ended(resp)
        return resp.status_code, payload

    @asynccontextmanager
    async def stream(self, method: str, path: str, *,
                     content: Optional[AsyncIterator[bytes]] = None,
                     timeout: float = STREAM_TIMEOUT_SEC) -> AsyncIterator[httpx.Response]:
        """One streamed request on the session, for attachment bytes: an
        upload's `content` is sent as it is read and a download's body is
        read as it arrives (`resp.aiter_bytes()`), so neither is ever held
        whole. Same rules as `call`: no redirect is followed, no answer is a
        TeamError(unreachable=True), and a 401 ends the session (its body
        is read first, so the error is known)."""
        server_url, token = await self._session()
        headers = {"Authorization": f"Bearer {token}"}
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        try:
            async with self._client(timeout) as client:
                async with client.stream(method, server_url + path, content=content,
                                         headers=headers) as resp:
                    if 300 <= resp.status_code < 400:
                        raise TeamError("The team server tried to redirect JARVIS elsewhere; "
                                        "JARVIS doesn't follow team-server redirects.")
                    if resp.status_code == 401:
                        await resp.aread()
                        await self._ended(resp)
                    yield resp
        except httpx.HTTPError as e:
            raise TeamError(f"The team server didn't answer ({type(e).__name__}).",
                            unreachable=True) from None

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

    def signed_in(self) -> bool:
        return self.state() not in NOT_SIGNED_IN

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
            "last_contact_at": iso_utc(saved.get("last_contact_at")),
            "last_error": self.last_error,
            "poll_interval_sec": STATUS_INTERVAL_SEC,
            "pairing": None if p is None else {
                "user_code": p.user_code,
                "verification_uri_complete": p.verification_uri_complete,
                "expires_at": iso_utc(p.expires_at),
            },
        }


connection = TeamConnection()
