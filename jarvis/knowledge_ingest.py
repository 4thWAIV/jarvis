"""Ingest: JARVIS's sources made into the engine's documents (design section
7.14).

It runs in JARVIS's code, never in the engine (I2), and never in the server
process either: reading, hashing, chunking and word folding are CPU-bound
Python that would hold the GIL against the voice path. Each pass is a
short-lived child, `python -m jarvis.knowledge_ingest`, started by the
server at background priority with the engine's address, pid and token on
its stdin, stopped and continued by the server with the pace (I4), and
ending after `PASS_SECONDS` or `PASS_BYTES` read, saying whether more is
left.

One adapter per source, each built on a reader that already exists:

| source        | read through                          | trust                                   |
|---------------|---------------------------------------|-----------------------------------------|
| notes         | the brain's memory, journal, projects | own                                     |
| conversation  | conversation_log (a change feed by id)| his words and his captures own; JARVIS's turns untrusted until the log records a turn's taint; agents' words and notices untrusted |
| runs          | run_store                             | the prompt own; summary and result untrusted (an agent's words) |
| workflows     | workflow_store events (by seq)        | untrusted (a session's report)          |
| folders       | folder_service under the FILES reader | own                                     |

Messages to and from sessions are in the conversation log already, so they
are read there once. Each source's position (an id, a seq, or a ledger of
file sizes and times, hashing only what moved) is a watermark the engine
keeps durably in the same transaction as the documents it produced, so a
pass after any downtime catches up exactly. A file gone or no longer
readable is forgotten.

Documents and chunks: a document is keyed by the hash of its source
reference; its chunks by the hash of (source, span, text), so unchanged text
writes nothing. Markdown is chunked by heading, then paragraph, to at most
`CHUNK_MAX` characters, ending on a whole line, with one sentence of
overlap. Conversation items, workflow events and run fields are appended
to a per-day, per-workflow or per-run document as chunks of their own.

References are written `jref:1:<node>:<kind>:<id>[/<path>]`; phase 1's
`workspace_ref.py` takes over the one form.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import quote

from jarvis import data_paths, workspace_ref

log = logging.getLogger("jarvis.knowledge_ingest")

PASS_SECONDS = 20.0
PASS_BYTES = 64 << 20
CHUNK_MAX = 1000
OVERLAP_MAX = 200
FILE_MAX_BYTES = 512 << 10
FOLDER_FILES_MAX = 400
CONVERSATION_BATCH = 200
DOC_BATCH = 20

_HEADING = re.compile(r"(#{1,6})[ \t]+(.*\S)[ \t]*")   # one whole line: used with fullmatch
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def jref(kind: str, ident: str, path: Optional[str] = None) -> str:
    """A source's reference, printed by `workspace_ref` (the one printer)."""
    return workspace_ref.format(workspace_ref.ref(kind, ident, path or None))


def doc_key(source: str) -> str:
    return "doc:" + hashlib.sha256(source.encode()).hexdigest()


def chunk_key(source: str, span: tuple[int, int], text: str) -> str:
    h = hashlib.sha256(f"{source}\x00{span[0]}:{span[1]}\x00{text}".encode()).hexdigest()
    return "chunk:" + h[:32]


# --- chunking -----------------------------------------------------------------

@dataclass
class Chunk:
    span: tuple[int, int]
    heading_path: list[str]
    text: str


def _last_sentence(text: str) -> str:
    parts = [p for p in _SENTENCE_END.split(text.strip()) if p]
    tail = parts[-1] if parts else ""
    return tail if len(tail) <= OVERLAP_MAX else ""


def chunk_text(text: str, *, markdown: bool = True) -> list[Chunk]:
    """By heading (Markdown), then paragraph, at most CHUNK_MAX characters
    ending on a whole line where one fits, with the previous chunk's last
    sentence carried over."""
    lines = text.splitlines(keepends=True)
    sections: list[tuple[list[str], int, list[str]]] = []   # (heading path, start, lines)
    path: list[str] = []
    pos = 0
    current: list[str] = []
    start = 0
    for line in lines:
        m = _HEADING.fullmatch(line.rstrip("\n")) if markdown else None
        if m:
            if "".join(current).strip():
                sections.append((list(path), start, current))
            level = len(m.group(1))
            path = path[:level - 1] + [m.group(2)]
            current, start = [line], pos
        else:
            if not current:
                start = pos
            current.append(line)
        pos += len(line)
    if "".join(current).strip():
        sections.append((list(path), start, current))

    out: list[Chunk] = []
    for heading_path, sec_start, sec_lines in sections:
        buf, buf_start, at = "", sec_start, sec_start
        carry = ""
        for line in sec_lines:
            pieces = [line[i:i + CHUNK_MAX] for i in range(0, len(line), CHUNK_MAX)] or [line]
            for piece in pieces:
                if buf and len(carry) + len(buf) + len(piece) > CHUNK_MAX:
                    text_out = (carry + buf).strip()
                    if text_out:
                        out.append(Chunk((buf_start, at), heading_path, text_out))
                    carry = _last_sentence(buf)
                    carry = carry + " " if carry else ""
                    buf, buf_start = "", at
                if not buf and len(carry) + len(piece) > CHUNK_MAX:
                    carry = ""          # no room for the overlap beside a long line
                buf += piece
                at += len(piece)
        if buf.strip():
            out.append(Chunk((buf_start, at), heading_path, (carry + buf).strip()))
    return out


# --- talking to the engine --------------------------------------------------------

@dataclass
class Item:
    """One document to write whole: its chunks replace whatever it had."""
    source: str
    label: str
    trust: str
    text: str
    markdown: bool = True
    path: Optional[str] = None
    modified: Optional[float] = None


@dataclass
class Appended:
    """Chunks added to a document that keeps the ones it has."""
    source: str                       # the document's
    label: str
    chunks: list[tuple[str, str, str]]   # (chunk source, trust, text)
    replace: bool = False             # the chunks given are all it has now


@dataclass
class Stats:
    documents: int = 0
    chunks: int = 0
    forgotten: int = 0
    bytes: int = 0
    more: bool = False
    linked: int = 0
    errors: list[str] = field(default_factory=list)


class Writer:
    def __init__(self, call: Callable[[str, str, Optional[dict]], dict], stats: Stats) -> None:
        self.call = call
        self.stats = stats

    def watermarks(self) -> dict:
        return self.call("GET", "/v1/watermarks", None)["watermarks"]

    def _existing_chunks(self, key: str) -> set[str]:
        found, after = set(), None
        while True:
            q = f"/v1/edges/{quote(key, safe=':')}?dir=in&kind=in_document&limit=1000"
            if after:
                q += "&after=" + quote(after)
            page = self.call("GET", q, None)
            found |= {e["from"] for e in page["edges"]}
            after = page.get("after")
            if not after:
                return found

    def _current_rev(self, key: str) -> Optional[str]:
        try:
            return self.call("GET", f"/v1/nodes/{quote(key, safe=':')}", None).get("_rev")
        except LookupError:
            return None

    def put(self, item: Item, marks: dict) -> None:
        key = doc_key(item.source)
        rev = hashlib.sha256(item.text.encode()).hexdigest()
        if self._current_rev(key) == rev:
            self.call("POST", "/v1/txn", {"watermarks": marks})
            return
        chunks = chunk_text(item.text, markdown=item.markdown)
        doc = {"_key": key, "_kind": "document", "_label": item.label, "_source": item.source,
               "_trust": "own", "_rev": rev}
        if item.path:
            doc["path"] = item.path
        if item.modified:
            doc["modified"] = _dt.datetime.fromtimestamp(item.modified, _dt.timezone.utc).isoformat()
        puts = [doc]
        keys = set()
        for c in chunks:
            ck = chunk_key(item.source, c.span, c.text)
            keys.add(ck)
            puts.append({"_key": ck, "_kind": "chunk", "_label": item.label, "_source": item.source,
                         "_trust": item.trust, "text": c.text, "span": list(c.span),
                         "heading_path": c.heading_path, "document": key})
        stale = sorted(self._existing_chunks(key) - keys)
        self.call("POST", "/v1/txn", {"puts": puts, "deletes": stale, "watermarks": marks})
        self.stats.documents += 1
        self.stats.chunks += len(chunks)

    def append(self, item: Appended, marks: dict) -> None:
        key = doc_key(item.source)
        puts = [{"_key": key, "_kind": "document", "_label": item.label, "_source": item.source,
                 "_trust": "own"}]
        for source, trust, text in item.chunks:
            for c in chunk_text(text, markdown=False):
                puts.append({"_key": chunk_key(source, c.span, c.text), "_kind": "chunk",
                             "_label": item.label, "_source": source, "_trust": trust,
                             "text": c.text, "span": list(c.span), "document": key})
        stale = (sorted(self._existing_chunks(key) - {p["_key"] for p in puts[1:]})
                 if item.replace else [])
        self.call("POST", "/v1/txn", {"puts": puts, "deletes": stale, "watermarks": marks})
        self.stats.chunks += len(puts) - 1

    def topic_keys(self) -> list[str]:
        found = self.call("POST", "/v1/find", {"kinds": ["topic"], "limit": 200})["documents"]
        return [d["_key"] for d in found]

    def _exists(self, key: str) -> bool:
        try:
            self.call("GET", f"/v1/nodes/{quote(key, safe=':')}", None)
            return True
        except LookupError:
            return False

    def replace_authored(self, topic: str, want: list[dict]) -> None:
        """The authored edges on `topic` become exactly `want`."""
        # What this topic's own file asserts: its outgoing links, and the
        # documents it names as about it. Links other topics' files make into
        # it are theirs.
        have = set()
        for direction, kind in (("out", None), ("in", "about")):
            q = f"/v1/edges/{quote(topic, safe=':')}?dir={direction}&limit=1000"
            if kind:
                q += f"&kind={kind}"
            for e in self.call("GET", q, None)["edges"]:
                if e.get("origin") == "authored":
                    have.add((e["from"], e["kind"], e["to"]))
        wanted = {}
        for e in want:
            needs = e.pop("_needs", None)
            if needs is None or self._exists(needs):
                wanted[(e["from"], e["kind"], e["to"])] = e
        drop = [{"from": a, "kind": k, "to": b} for a, k, b in have - set(wanted)]
        add = [e for key, e in wanted.items() if key not in have]
        if drop or add:
            self.call("POST", "/v1/txn", {"edges": add, "edge_deletes": drop})

    def forget(self, sources: list[str], marks: dict) -> None:
        if sources:
            self.call("POST", "/v1/forget", {"refs": sources})
            self.stats.forgotten += len(sources)
        self.call("POST", "/v1/txn", {"watermarks": marks})


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


# --- the adapters -----------------------------------------------------------------

class Adapter:
    name = ""

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        """Ingest from `mark` (this source's watermark); True when more is left."""
        raise NotImplementedError


def _read_note(path: Path) -> Optional[str]:
    """One note, never through a link, at most FILE_MAX_BYTES."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not (info.st_mode & 0o170000 == 0o100000) or info.st_size > FILE_MAX_BYTES:
            return None
        return os.read(fd, FILE_MAX_BYTES).decode("utf-8", "replace")
    finally:
        os.close(fd)


class Ledgered(Adapter):
    """Files followed by a ledger of (size, modified): only a file that moved
    is read and hashed; one that is gone is forgotten."""

    def files(self) -> Iterator[tuple[str, Callable[[], Optional[str]], int, float, str, str]]:
        """(ledger key, read, size, modified, source, label)."""
        raise NotImplementedError

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        ledger: dict = dict(mark.get("files") or {})
        seen = set()
        for key, read, size, modified, source, label in self.files():
            seen.add(key)
            if ledger.get(key, [None, None])[:2] == [size, modified]:
                continue
            if budget.spent:
                return True
            text = read()
            if text is None:
                continue
            budget.spend(len(text))
            ledger[key] = [size, modified, source]
            writer.put(Item(source, label, "own", text, path=key.split(":", 1)[-1], modified=modified),
                       {self.name: {"files": ledger}})
        gone = [k for k in ledger if k not in seen]
        if gone:
            sources = [ledger[k][2] for k in gone if len(ledger[k]) > 2]
            for k in gone:
                del ledger[k]
            writer.forget(sources, {self.name: {"files": ledger}})
        return False


class Notes(Ledgered):
    name = "notes"

    def files(self):
        home = data_paths.brain_home()
        for folder in ("memory", "journal", "projects"):     # topics: the Topics adapter
            base = home / folder
            if not base.is_dir() or base.is_symlink():
                continue
            for path in sorted(base.glob("*.md")):
                if path.is_symlink():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    continue
                rel = f"{folder}/{path.name}"
                yield (f"note:{rel}", lambda p=path: _read_note(p), info.st_size, info.st_mtime,
                       jref("note", folder, path.name), rel)


class Topics(Adapter):
    """Topic files (knowledge_links): each a topic document with its aliases
    and status, its words as a document of chunks, and the links he asserted
    as authored edges, replacing the ones an earlier version of the file
    made. Topics are all put before any link, so a link may name a topic
    later in the list; a link to a topic or document the engine does not
    hold is left for the next pass."""
    name = "topics"
    EDGE = {"relates_to": "relates_to", "depends_on": "depends_on", "stage_of": "stage_of",
            "precedes": "precedes", "same_as": "same_as"}

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        from jarvis import knowledge_links
        found = knowledge_links.topics()
        digest = hashlib.sha256("\x00".join(t.text for t in found).encode()).hexdigest()
        if mark.get("digest") == digest:
            return False
        budget.spend(sum(len(t.text) for t in found))
        known = {t.slug for t in found}
        puts = [{"_key": f"topic:{t.slug}", "_kind": "topic", "_label": t.name, "_status": t.status,
                 "_source": jref("note", "topics", f"{t.slug}.md"), "_trust": "own",
                 "pinned": t.pinned,
                 "aliases": {"exact": t.aliases("exact"), "ambiguous": t.aliases("ambiguous")},
                 "front": {"not_linked": t.links("not_linked")}} for t in found]
        before = {d.removeprefix("topic:") for d in writer.topic_keys()}
        gone = sorted(f"topic:{s}" for s in before - known)
        writer.call("POST", "/v1/txn", {"puts": puts, "deletes": gone})
        for t in found:
            body = Item(jref("note", "topics", f"{t.slug}.md"), f"topics/{t.slug}.md", "own", t.body)
            writer.put(body, {})
            want = []
            for field_name, kind in self.EDGE.items():
                for target in t.links(field_name):
                    if target in known and target != t.slug:
                        want.append({"from": f"topic:{t.slug}", "kind": kind, "to": f"topic:{target}",
                                     "origin": "authored"})
            for source in t.links("about_sources"):
                want.append({"from": doc_key(source), "kind": "about", "to": f"topic:{t.slug}",
                             "origin": "authored", "_needs": doc_key(source)})
            writer.replace_authored(f"topic:{t.slug}", want)
        writer.call("POST", "/v1/txn", {"watermarks": {self.name: {"digest": digest}}})
        writer.stats.documents += len(found)
        return False


class Folders(Ledgered):
    """Registered folders read under the FILES reader through the folder
    service: README*, CLAUDE.md and AGENTS.md at the root, Markdown to depth
    two and everything under docs/, each under FILE_MAX_BYTES, at most
    FOLDER_FILES_MAX a folder."""
    name = "folders"

    def __init__(self, service=None) -> None:
        self.service = service

    def _service(self):
        if self.service is None:
            from jarvis import folder_service, folder_store
            self.service = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
        return self.service

    @staticmethod
    def wanted(path: str) -> bool:
        parts = path.split("/")
        name = parts[-1]
        if len(parts) == 1 and (name.upper().startswith("README") or name in ("CLAUDE.md", "AGENTS.md")):
            return True
        if not name.lower().endswith(".md"):
            return False
        return len(parts) <= 2 or parts[0] == "docs"

    def _walk(self, service, folder_id: str) -> Iterator[dict]:
        stack = [("", 0)]
        count = 0
        while stack:
            path, depth = stack.pop()
            cursor = None
            while True:
                try:
                    page = service.entries(folder_id, path=path, cursor=cursor)
                except Exception:  # noqa: BLE001 - refused or gone: nothing from it
                    break
                for e in page["entries"]:
                    p = e["path"]
                    if e["kind"] == "dir":
                        if depth < 1 or p.split("/")[0] == "docs" and depth < 8:
                            stack.append((p, depth + 1))
                    elif e["kind"] == "file" and self.wanted(p) and (e.get("size") or 0) <= FILE_MAX_BYTES:
                        count += 1
                        if count > FOLDER_FILES_MAX:
                            return
                        yield e
                cursor = page.get("next_cursor")
                if not cursor:
                    break

    def _read(self, service, folder_id: str, path: str) -> Optional[str]:
        out, cursor, total = [], None, 0
        while True:
            try:
                window = service.preview(folder_id, path=path, cursor=cursor)
            except Exception:  # noqa: BLE001
                return None
            if window.get("kind") != "text":
                return None
            out.append("\n".join(window.get("lines") or []))
            total += sum(len(x) for x in window.get("lines") or [])
            cursor = window.get("next_cursor")
            if not cursor or total > FILE_MAX_BYTES:
                return "\n".join(out)

    def files(self):
        service = self._service()
        cursor = None
        while True:
            listing = service.list_folders(cursor=cursor)
            for f in listing["folders"]:
                access = f.get("access") or {}
                if access.get("reader") != "files" or not f["location"].get("local") \
                        or f["location"].get("availability") != "available":
                    continue
                for e in self._walk(service, f["id"]):
                    path = e["path"]
                    try:
                        workspace_ref.check_path(path)
                    except workspace_ref.RefError:
                        continue          # a name no reference can carry is not read
                    yield (f"{f['id']}:{path}",
                           lambda fid=f["id"], p=path: self._read(service, fid, p),
                           e.get("size"), e.get("modified"),
                           jref("folder_file", f["id"], path), f"{f['display_name']}/{path}")
            cursor = listing.get("next_cursor")
            if not cursor:
                return


class Conversation(Adapter):
    """The conversation log by item id; each item a chunk of its day's
    document."""
    name = "conversation"
    OWN = {"heard", "typed", "capture", "to_agent"}

    @classmethod
    def trust(cls, item) -> str:
        kind = str(item.kind)
        if kind in cls.OWN:
            return "own"
        if kind == "jarvis" and item.detail.get("tainted") is False:
            return "own"
        return "untrusted"

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        from jarvis import conversation_log
        after = int(mark.get("after") or 0)
        while not budget.spent:
            items = conversation_log.after(after, CONVERSATION_BATCH)
            if not items:
                return False
            days: dict[str, list] = {}
            for item in items:
                day = _dt.datetime.fromtimestamp(item.at, _dt.timezone.utc).date().isoformat()
                days.setdefault(day, []).append(item)
            for day, group in days.items():
                after = group[-1].id
                chunks = [(jref("conversation", str(i.id)), self.trust(i), i.text) for i in group]
                budget.spend(sum(len(t) for _, _, t in chunks))
                writer.append(Appended(jref("conversation_day", day), f"conversation {day}", chunks),
                              {self.name: {"after": after}})
        return True


class Runs(Adapter):
    """Finished runs: the prompt (his words) and the summary and result (an
    agent's), as chunks of the run's document, by a ledger of their hashes."""
    name = "runs"
    DONE = ["succeeded", "failed", "timed_out", "cancelled"]

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        from jarvis import run_store
        ledger = dict(mark.get("runs") or {})
        for r in run_store.list_runs(status=self.DONE, limit=1000):
            fields = [("prompt", "own", r.get("prompt") or ""), ("summary", "untrusted", r.get("summary") or ""),
                      ("result", "untrusted", r.get("result_text") or "")]
            digest = hashlib.sha256(json.dumps(fields).encode()).hexdigest()
            if ledger.get(r["id"]) == digest:
                continue
            if budget.spent:
                return True
            source = jref("run", r["id"])
            chunks = [(f"{source}#{name}", trust, text) for name, trust, text in fields if text.strip()]
            budget.spend(sum(len(t) for _, _, t in chunks))
            ledger[r["id"]] = digest
            writer.append(Appended(source, f"run {r['id']}", chunks, replace=True),
                          {self.name: {"runs": ledger}})
        return False


class Workflows(Adapter):
    name = "workflows"

    def run(self, writer: Writer, mark: dict, budget: Budget) -> bool:
        from jarvis import workflow_store
        seqs = dict(mark.get("seq") or {})
        for snap in workflow_store.recent(limit=50):
            while not budget.spent:
                events = workflow_store.events(snap.id, after=int(seqs.get(snap.id) or 0), limit=200)
                if not events:
                    break
                seqs[snap.id] = events[-1].seq
                chunks = [(jref("workflow_event", f"{snap.id}:{e.seq}"), "untrusted",
                           e.summary)
                          for e in events if (e.summary or "").strip()]
                budget.spend(sum(len(t) for _, _, t in chunks))
                writer.append(Appended(jref("workflow", snap.id), f"workflow {snap.id}", chunks),
                              {self.name: {"seq": seqs}})
            if budget.spent:
                return True
        return False


ADAPTERS: tuple[type[Adapter], ...] = (Notes, Topics, Conversation, Runs, Workflows, Folders)


def run_pass(call: Callable[[str, str, Optional[dict]], dict], *, budget: Optional[Budget] = None,
             adapters: Optional[list[Adapter]] = None, link: bool = True) -> Stats:
    stats = Stats()
    writer = Writer(call, stats)
    budget = budget or Budget()
    marks = writer.watermarks()
    for adapter in adapters if adapters is not None else [a() for a in ADAPTERS]:
        try:
            if adapter.run(writer, marks.get(adapter.name) or {}, budget):
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
