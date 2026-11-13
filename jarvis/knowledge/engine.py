"""The engine object: one directory, one writer lock, and the parts over it.

`Engine.open(directory)` takes `knowledge/LOCK` with `flock`, so a second
engine on the same directory refuses to start (design section 7.2), and
opens the parts. The HTTP layer (`serve.py`) and the in-process test
transport both call the same routes over one `Engine`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from jarvis.knowledge import lanes, oscompat

log = logging.getLogger("jarvis.knowledge.engine")

CARDS_REFRESH_SEC = 600.0
SNAPSHOT_EVERY_SEC = 24 * 3600.0   # a snapshot a day: the engine holds records and their history


class EngineLocked(RuntimeError):
    """Another engine holds this directory's writer lock."""


def lock_is_free(directory: Path) -> bool:
    """Whether nobody holds `directory`'s writer lock now. Taking it and
    letting it go again at once is the only reliable test."""
    try:
        fd = os.open(str(directory / "LOCK"), os.O_RDWR | os.O_CREAT | oscompat.O_NOFOLLOW | oscompat.O_BINARY, 0o600)
    except FileNotFoundError:
        return True
    try:
        oscompat.lock(fd)
    except BlockingIOError:
        return False
    else:
        oscompat.unlock(fd)
        return True
    finally:
        os.close(fd)


class Engine:
    def __init__(self, directory: Path, lock_fd: int) -> None:
        self.directory = directory
        self._lock_fd = lock_fd
        self.boot_id = uuid.uuid4().hex
        self.started = time.time()
        self.pace = "free"
        self._closed = False
        self._close_lock = threading.Lock()
        self.store = None  # the storage core, opened by open()
        self.docs = None   # the document store over it
        self.graph = None  # the typed graph over the documents
        self.lexical = None  # the keyword index over chunk text
        self.vectors = None  # the vector index and its embedding worker
        self.linker = None   # mentions and proposals
        self.cards = None    # entity cards and the resident tier
        self.advisor = None  # query shapes and the extra indexes
        self.entities = None  # the names a question may name, for the graph
        self.items = None     # items at paths: the UDS interface (items.py)
        self.rights = None    # AccessRights at paths (rights.py)

    @classmethod
    def open(cls, directory: Path) -> "Engine":
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        fd = os.open(str(directory / "LOCK"), os.O_RDWR | os.O_CREAT | oscompat.O_NOFOLLOW | oscompat.O_BINARY, 0o600)
        try:
            oscompat.lock(fd)
        except BlockingIOError:
            os.close(fd)
            raise EngineLocked(f"another knowledge engine holds {directory / 'LOCK'}")
        engine = cls(directory, fd)
        try:
            engine._open_parts()
        except BaseException:
            engine.close()
            raise
        return engine

    def _open_parts(self) -> None:
        """Opens the storage core and the parts over it; each step of the
        build adds its part here."""
        from jarvis.knowledge.documents import DocumentStore
        from jarvis.knowledge.store import Store
        self.store = Store(self.directory, parts=default_parts(),
                           compact_in_child=self.compact_in_child)
        self.docs = DocumentStore(self.store)
        from jarvis.knowledge.graph import Graph
        self.graph = Graph(self.docs)
        from jarvis.knowledge.items import Items
        from jarvis.knowledge.rights import Rights
        self.rights = Rights(self.store)
        self.items = Items(self.docs, self.graph, self.rights)
        from jarvis.knowledge.lexical import Lexical
        self.lexical = Lexical(self.docs)
        from jarvis.knowledge import embedder
        from jarvis.knowledge.vectors import VectorIndex
        self.vectors = VectorIndex(self.docs, embedder.current() if self.indexing_enabled() else None)
        self.items.vectors = self.vectors
        self.items.lexical = self.lexical
        from jarvis.knowledge.linker import Linker
        self.linker = Linker(self.docs, self.graph, self.lexical, self.transaction)
        self.linker.items = self.items
        self._link_lock = threading.Lock()
        from jarvis.knowledge.cards import Cards
        self.cards = Cards(self)
        self.cards.load_warm(self.directory / "cache.warm")
        self._cards_refreshed = 0.0
        from jarvis.knowledge.advisor import Advisor
        self.advisor = Advisor(self.docs)
        from jarvis.knowledge.entities import Entities
        self.entities = Entities(self.docs)

    compact_in_child = True

    def health(self) -> dict:
        store = self.store.health() if self.store is not None else {"state": "ok"}
        return {"state": store.get("state", "ok"), "pid": os.getpid(), "boot_id": self.boot_id,
                "uptime_s": round(time.time() - self.started, 3), "pace": self.pace,
                "store": store,
                "documents": {"resident_bytes": self.docs.resident_bytes()} if self.docs else {},
                "keyword": {"chunks": self.lexical.live} if self.lexical else {},
                "vectors": self.vectors.health() if self.vectors else {},
                "cards": self.cards.health() if self.cards else {},
                "advisor": self.advisor.health() if self.advisor else {},
                "items": self.items.health() if self.items else {}}

    def indexing_enabled(self) -> bool:
        from jarvis import knowledge_settings
        def load():
            item = self.items.get(knowledge_settings.PATH, streams=["data"])
            return None if item is None else item.get("data")
        return knowledge_settings.read(load)["enabled"]

    def set_pace(self, mode: str) -> None:
        """`hold` while hearing needs the machine (I4): the compaction child
        is stopped and no maintenance starts; `free` continues it."""
        if mode not in ("hold", "free"):
            raise ValueError("pace is hold or free")
        self.pace = mode
        if self.store is not None:
            self.store.set_paused(mode == "hold")
        if self.vectors is not None:
            self.vectors.set_paused(mode == "hold" or not self.indexing_enabled())

    def transaction(self, body: dict) -> int:
        """One atomic write: documents put and deleted together; readers see
        all of it or none (section 7.17.4)."""
        from jarvis.knowledge.documents import DocumentError
        puts, deletes = body.get("puts") or [], body.get("deletes") or []
        if not isinstance(puts, list) or not isinstance(deletes, list):
            raise DocumentError("puts and deletes are lists")
        if not all(isinstance(k, str) for k in deletes):
            raise DocumentError("deletes are keys")
        edges, edge_deletes = body.get("edges") or [], body.get("edge_deletes") or []
        if not isinstance(edges, list) or not isinstance(edge_deletes, list):
            raise DocumentError("edges and edge_deletes are lists")
        marks = body.get("watermarks") or {}
        if not isinstance(marks, dict) or not all(isinstance(k, str) and k for k in marks):
            raise DocumentError("watermarks are an object keyed by source")
        from jarvis.knowledge.encoding import canonical
        from jarvis.knowledge.store import META
        from jarvis.knowledge.wal import Op
        mark_ops = [Op(META, ("wm:" + k).encode(), canonical(v)) for k, v in marks.items()]
        return self.docs.write(puts, deletes,
                               extra=lambda pending: self.graph.ops(edges, edge_deletes, pending) + mark_ops)

    def link(self) -> dict:
        """Run the linker over every topic; one run at a time, in the
        background lane (lanes.py)."""
        with lanes.lowered(), self._link_lock:
            return self.linker.run()

    RESOLVE_KINDS = ("topic", "project", "work", "folder", "person", "agent", "workflow", "document")

    def resolve(self, name: str, limit: int = 20) -> list[dict]:
        """Every typed match for words (design 7.12): a topic by its name or
        an alias (exact or ambiguous, said which), and any other kind by its
        label, each labelled by kind so meanings stay apart."""
        from jarvis import spoken_words
        want = " ".join(spoken_words.folded_words(name or ""))
        if not want:
            return []
        out: list[dict] = []
        for topic in self.docs.find(kinds_=["topic"], limit=1 << 30):
            aliases = topic.get("aliases") or {}
            for how, names in (("name", [topic.get("_label") or ""]), ("exact alias", aliases.get("exact") or []),
                               ("ambiguous alias", aliases.get("ambiguous") or [])):
                if any(" ".join(spoken_words.folded_words(str(n))) == want for n in names):
                    out.append({"key": topic["_key"], "kind": "topic", "label": topic.get("_label"),
                                "status": topic.get("_status"), "how": how, "trust": topic.get("_trust")})
                    break
        variants = list(dict.fromkeys([name, name.strip(), name.lower(), name.title(), name.capitalize()]))
        for kind in self.RESOLVE_KINDS[1:]:
            for d in self.docs.find(kinds_=[kind], where=[["_label", "in", variants]], limit=limit):
                if " ".join(spoken_words.folded_words(d.get("_label") or "")) == want:
                    out.append({"key": d["_key"], "kind": kind, "label": d.get("_label"),
                                "status": d.get("_status"), "how": "name", "trust": d.get("_trust")})
        return out[:limit]

    def watermarks(self) -> dict:
        """Each ingest source's position, durable with the documents it
        produced, so a pass after any downtime catches up exactly."""
        import json
        from jarvis.knowledge.store import META
        return {v.key.decode()[3:]: json.loads(v.value) for v in self.store.prefix(META, b"wm:")}

    def maintain(self) -> None:
        """One maintenance step, run by the engine's own timer in the
        background lane (serve._maintenance) while the floor is free: flush on the time rule, then compact a full tier."""
        if self.pace == "hold" or self.store is None or not self.store.writable:
            return
        self.store.maybe_flush()
        if self.pace == "free" and self.store.pick_tier():
            self.store.compact()
        if self.vectors is not None:
            enabled = self.indexing_enabled()
            self.vectors.set_paused(not enabled)
        if self.pace == "free" and self.vectors is not None and enabled:
            if self.vectors.embedder is None:
                from jarvis.knowledge import embedder
                self.vectors.attach(embedder.current())
            self.vectors.warm()
        if self.pace == "free":
            age = self.store.newest_snapshot_age()
            if age is None or age >= SNAPSHOT_EVERY_SEC:
                self.store.snapshot()
        if self.pace == "free" and self.cards is not None and \
                time.monotonic() - self._cards_refreshed >= CARDS_REFRESH_SEC:
            self._cards_refreshed = time.monotonic()
            self.cards.refresh()
        if self.pace == "free" and self.advisor is not None and self.advisor.due():
            self.advisor.save()
            self.advisor.advise(rebuild=self.rebuild_indexes)

    def rebuild_indexes(self) -> None:
        """Every generation made again in one, so a new extra index is held
        everywhere (and a dropped one nowhere)."""
        self.store.flush()
        gens = self.store.manifest.generations()
        if gens:
            self.store.compact(gens)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._close_parts()
            finally:
                try:
                    oscompat.unlock(self._lock_fd)
                finally:
                    os.close(self._lock_fd)

    def _close_parts(self) -> None:
        """Flushes and closes the parts; each step adds its part here."""
        if self.cards is not None:
            self.cards.save_warm(self.directory / "cache.warm")
        if self.advisor is not None and self.store is not None and self.store.writable:
            try:
                self.advisor.save()
            except Exception:
                log.warning("could not save the query shapes", exc_info=True)
        if self.vectors is not None:
            self.vectors.close()
        if self.store is not None:
            self.store.close()


def _part_types() -> dict:
    from jarvis.knowledge.documents import IndexPart
    from jarvis.knowledge.lexical import LexicalPart
    from jarvis.knowledge.vectors import VectorPart
    from jarvis.knowledge.advisor import ExtraIndexPart
    return {t.name: t for t in (IndexPart, LexicalPart, VectorPart, ExtraIndexPart)}


def default_parts() -> list:
    """The derived parts every engine store is built with."""
    return [t() for t in _part_types().values()]


def parts_named(names: list[str]) -> list:
    """The parts a compaction plan names, so the child builds exactly what
    the store that planned it builds."""
    types = _part_types()
    return [types[n]() for n in names]


def open_engine(directory: Optional[Path] = None) -> Engine:
    from jarvis import data_paths
    return Engine.open(directory or data_paths.knowledge_dir())
