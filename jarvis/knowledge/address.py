"""Where the running knowledge engine can be reached.

The engine writes `{scheme, host, port, pid, boot_id}` to
`data_paths.knowledge_address_path()` (0600) once it listens, and removes it
on a clean exit, only when the record is still its own. `read()` is the one
reader; `running()` returns the record only while its process lives, as
`server_address.running()` does for the server.
"""
from __future__ import annotations

import json
import logging
import os
from typing import NamedTuple, Optional

from jarvis import data_paths
from jarvis.process_inspect import State
from jarvis.server_address import _alive

log = logging.getLogger("jarvis.knowledge.address")


class EngineAddress(NamedTuple):
    scheme: str
    host: str
    port: int
    pid: int
    boot_id: str

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


def record(address: EngineAddress) -> bool:
    """Write the record. mkstemp makes the file 0600 before any byte is in it."""
    path = data_paths.knowledge_address_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return data_paths._write_atomically(path, json.dumps(address._asdict()) + "\n")


def read() -> Optional[EngineAddress]:
    try:
        body = json.loads(data_paths.knowledge_address_path().read_text())
        return EngineAddress(scheme=str(body["scheme"]), host=str(body["host"]),
                             port=int(body["port"]), pid=int(body["pid"]),
                             boot_id=str(body["boot_id"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning("ignoring an unreadable knowledge engine address (%s)", e)
        return None


def running() -> Optional[EngineAddress]:
    found = read()
    # Gone only when confirmed gone; `unknown` keeps the record, as
    # server_address.running does.
    if found is None or found.pid <= 0 or _alive(found.pid) is State.EXITED:
        return None
    return found


def forget(boot_id: str) -> None:
    """Remove the record only when it is still this engine's."""
    found = read()
    if found is None or found.boot_id != boot_id or found.pid != os.getpid():
        return
    try:
        data_paths.knowledge_address_path().unlink()
    except OSError as e:
        log.warning("could not remove the knowledge engine address (%s)", e)
