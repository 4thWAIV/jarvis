"""Ingest: what is left of it once JARVIS's records and notes are items (UDS
round 3; design section 7.14).

Records (turns, messages, runs, workflows, Work...) are items written by
their stores, and the engine finds each by its own words as it is
(lexical.record_text) and by its fields: nothing reads them again to make
chunks of them. The brain's notes, journal, memory, topics and settings
note are files indexed in place as items by the content child
(knowledge_content.py, the `/jarvis/brain` root), with their sections and
vectors. So ingest makes no documents any more: the `chunk` kind and the
line packer are gone, and nothing here reads SQLite or any store.

A pass is still a short-lived child, `python -m jarvis.knowledge_ingest`,
started by the server at background priority with the engine's address,
pid and token on its stdin, stopped and continued with the pace (I4). It:

1. retires, once, what earlier ingest made (every `chunk` and `document`
   document with its edges and vectors), and the watermarks of its sources;
2. hands the engine his index choices (knowledge_links.index_choices);
3. runs the linker (`POST /v1/link`), which follows the topic items and
   finds mentions and proposals.

`Adapter` and `adapters` are kept for a source that still needs reading by
a pass; none does today.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from jarvis import workspace_ref

log = logging.getLogger("jarvis.knowledge_ingest")

PASS_SECONDS = 20.0
PASS_BYTES = 64 << 20
RETIRED = "retired"             # the watermark saying earlier ingest's documents are gone
OLD_SOURCES = ("notes", "topics", "conversation", "runs", "workflows")
# What a pass holds the engine to: 3, no chunks (records and notes are items).
SCHEMA = 3


def jref(kind: str, ident: str, path: Optional[str] = None) -> str:
    """A source's reference, printed by `workspace_ref` (the one printer)."""
    return workspace_ref.format(workspace_ref.ref(kind, ident, path or None))


@dataclass
class Stats:
    documents: int = 0
    chunks: int = 0
    forgotten: int = 0
    bytes: int = 0
    more: bool = False
    linked: int = 0
    errors: list[str] = field(default_factory=list)


class Budget:
    def __init__(self, seconds: float = PASS_SECONDS, nbytes: int = PASS_BYTES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.end = clock() + seconds
        self.left = nbytes

    def spend(self, n: int) -> None:
        self.left -= n

    @property
    def spent(self) -> bool:
        return self.left <= 0 or self.clock() >= self.end


class Adapter:
    """A source a pass reads (none today)."""
    name = ""

    def run(self, call: Callable[[str, str, Optional[dict]], dict], mark: dict, budget: Budget) -> bool:
        """Read from `mark` (this source's watermark); True when more is left."""
        raise NotImplementedError


ADAPTERS: tuple[type[Adapter], ...] = ()


def retire(call: Callable[[str, str, Optional[dict]], dict], marks: dict, stats: Stats) -> None:
    """Once: forget every chunk and document earlier ingest made, and its
    sources' watermarks, so nothing is found twice or stale."""
    if (marks.get(RETIRED) or {}).get("schema") == SCHEMA:
        return
    found = call("POST", "/v1/forget", {"retired_kinds": ["chunk", "document"]})
    stats.forgotten += int(found.get("documents") or 0)
    gone = {name: {"retired": True} for name in OLD_SOURCES if name in marks}
    call("POST", "/v1/txn", {"watermarks": {**gone, RETIRED: {"schema": SCHEMA}}})


def run_pass(call: Callable[[str, str, Optional[dict]], dict], *, budget: Optional[Budget] = None,
             adapters: Optional[list[Adapter]] = None, link: bool = True) -> Stats:
    stats = Stats()
    budget = budget or Budget()
    try:
        marks = call("GET", "/v1/watermarks", None)["watermarks"]
        retire(call, marks, stats)
    except Exception as e:  # noqa: BLE001 - the rest of the pass goes on
        log.exception("retiring earlier ingest failed")
        stats.errors.append(f"retire: {type(e).__name__}: {e}")
        marks = {}
    for adapter in adapters if adapters is not None else [a() for a in ADAPTERS]:
        try:
            mark = marks.get(adapter.name) or {}
            if mark.get("schema") != SCHEMA:
                mark = {}
            if adapter.run(call, mark, budget):
                stats.more = True
        except Exception as e:  # noqa: BLE001 - one source failing leaves the rest
            log.exception("ingest of %s failed", adapter.name)
            stats.errors.append(f"{adapter.name}: {type(e).__name__}: {e}")
        if budget.spent:
            stats.more = True
            break
    try:
        from jarvis import knowledge_links
        call("POST", "/v1/indexes/choices", knowledge_links.index_choices())
    except Exception as e:  # noqa: BLE001
        stats.errors.append(f"index choices: {type(e).__name__}: {e}")
    if link:
        try:
            linked = call("POST", "/v1/link", {})
            stats.linked = linked.get("added", 0) + linked.get("removed", 0)
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"linker: {type(e).__name__}: {e}")
    return stats


def _http_call(port: int, token: str) -> Callable[[str, str, Optional[dict]], dict]:
    import http.client

    def call(method: str, path: str, body: Optional[dict]) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            data = None if body is None else json.dumps(body).encode()
            headers = {"Authorization": f"Bearer {token}", "Host": f"127.0.0.1:{port}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            payload = json.loads(r.read() or b"null")
            if r.status == 404:
                raise LookupError(path)
            if r.status >= 400:
                raise RuntimeError(f"{method} {path}: {r.status} {payload}")
            return payload
        finally:
            conn.close()
    return call


def main() -> int:
    """The child: read {port, token} from stdin, run one pass, print its
    stats as one JSON line."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        os.setpriority(getattr(os, "PRIO_DARWIN_PROCESS", os.PRIO_PROCESS), 0,
                       getattr(os, "PRIO_DARWIN_BG", 19))
    except (OSError, AttributeError):
        pass
    given = json.loads(sys.stdin.readline())
    stats = run_pass(_http_call(int(given["port"]), str(given["token"])))
    print(json.dumps(stats.__dict__), flush=True)
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    sys.exit(main())
