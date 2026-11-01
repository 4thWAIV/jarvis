"""Where the running JARVIS server can be reached.

The server's own address is one thing, worked out once here: the scheme,
host and port it was started with (`JARVIS_SCHEME`, `JARVIS_BIND_HOST`,
`JARVIS_PORT`, which `server.main()` and `web_auth.adopt_bind` set), with a
bind-everything host turned into one that can be dialled. The brain's tool
channel is pointed at it (`server._write_mcp_config`), and at startup the
server writes it with its pid to `data_paths.server_address_path()`, and
removes it on a clean shutdown (`record`, `forget`).

`running()` is the one reader: the recorded address while the process that
wrote it is alive, else None. A record whose process has gone (a crash, a
kill) is stale and is ignored. `python -m jarvis.update` reads it first, so a
server on another port or on ::1 is found without any variable in the shell.
"""
from __future__ import annotations

import json
import logging
import os
from typing import NamedTuple, Optional

from jarvis import data_paths, web_auth

log = logging.getLogger("jarvis.server_address")

DEFAULT_SCHEME = "http"
DEFAULT_HOST = web_auth.JARVIS_DEFAULT_HOST
DEFAULT_PORT = web_auth.DEFAULT_API_PORT


class Address(NamedTuple):
    scheme: str
    host: str  # dialable, never bracketed
    port: int

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


class Running(NamedTuple):
    address: Address
    pid: int

    @property
    def url(self) -> str:
        return self.address.url


def dial_host(bind_host: str) -> str:
    """A host that reaches a server bound to `bind_host`. `0.0.0.0` and `::`
    bind every interface but are not themselves dialable, so loopback is
    used instead; anything else (a hostname, an address) is used as it is."""
    host = bind_host.strip().strip("[]")
    if host == "0.0.0.0":
        return "127.0.0.1"
    if host in ("::", "::1"):
        return "::1"
    return host


def own() -> Address:
    """This server's address, from what it was started with."""
    return Address(scheme=os.getenv("JARVIS_SCHEME", DEFAULT_SCHEME),
                   host=dial_host(os.getenv("JARVIS_BIND_HOST", DEFAULT_HOST)),
                   port=int(os.getenv("JARVIS_PORT", str(DEFAULT_PORT))))


def record() -> None:
    """Write this server's address and pid. A failure is logged, never
    raised: the server runs without it, and the command then looks where it
    did before."""
    address = own()
    text = json.dumps({"scheme": address.scheme, "host": address.host,
                       "port": address.port, "pid": os.getpid()})
    path = data_paths.server_address_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("could not record the server address (%s)", e)
        return
    if data_paths._write_atomically(path, text + "\n"):
        log.info("server address %s recorded in %s", address.url, path)


def forget() -> None:
    """Remove the record, but only this process's own: another server on the
    same data folder may have written over it."""
    found = _read()
    if found is None or found.pid != os.getpid():
        return
    try:
        data_paths.server_address_path().unlink()
    except OSError as e:
        log.warning("could not remove the server address record (%s)", e)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # there, under another user
    except OSError:
        return False
    return True


def _read() -> Optional[Running]:
    try:
        body = json.loads(data_paths.server_address_path().read_text())
        return Running(Address(scheme=str(body["scheme"]), host=str(body["host"]),
                               port=int(body["port"])),
                       pid=int(body["pid"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("ignoring an unreadable server address record (%s)", e)
        return None


def running() -> Optional[Running]:
    """The recorded server while its process is alive, else None."""
    found = _read()
    if found is None or found.pid <= 0 or not _alive(found.pid):
        return None
    return found
