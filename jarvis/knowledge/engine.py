"""The engine object: one directory, one writer lock, and the parts over it.

`Engine.open(directory)` takes `knowledge/LOCK` with `flock`, so a second
engine on the same directory refuses to start (design section 7.2), and
opens the parts. The HTTP layer (`serve.py`) and the in-process test
transport both call the same routes over one `Engine`.
"""
from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.knowledge.engine")


class EngineLocked(RuntimeError):
    """Another engine holds this directory's writer lock."""


def lock_is_free(directory: Path) -> bool:
    """Whether nobody holds `directory`'s writer lock now. Taking it and
    letting it go again at once is the only reliable test."""
    try:
        fd = os.open(str(directory / "LOCK"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except FileNotFoundError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
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

    @classmethod
    def open(cls, directory: Path) -> "Engine":
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        fd = os.open(str(directory / "LOCK"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        from jarvis.knowledge.lexical import Lexical
        self.lexical = Lexical(self.docs)
        from jarvis.knowledge import embedder
        from jarvis.knowledge.vectors import VectorIndex
        self.vectors = VectorIndex(self.docs, embedder.current())

    compact_in_child = True

    def health(self) -> dict:
        store = self.store.health() if self.store is not None else {"state": "ok"}
        return {"state": store.get("state", "ok"), "pid": os.getpid(), "boot_id": self.boot_id,
                "uptime_s": round(time.time() - self.started, 3), "pace": self.pace,
                "store": store,
                "documents": {"resident_bytes": self.docs.resident_bytes()} if self.docs else {},
                "keyword": {"chunks": self.lexical.live} if self.lexical else {},
                "vectors": self.vectors.health() if self.vectors else {}}

    def set_pace(self, mode: str) -> None:
        """`hold` while hearing needs the machine (I4): the compaction child
        is stopped and no maintenance starts; `free` continues it."""
        if mode not in ("hold", "free"):
            raise ValueError("pace is hold or free")
        self.pace = mode
        if self.store is not None:
            self.store.set_paused(mode == "hold")
        if self.vectors is not None:
            self.vectors.set_paused(mode == "hold")

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
        return self.docs.write(puts, deletes,
                               extra=lambda pending: self.graph.ops(edges, edge_deletes, pending))

    def maintain(self) -> None:
        """One maintenance step, run by the engine's own timer while the
        floor is free: flush on the time rule, then compact a full tier."""
        if self.pace == "hold" or self.store is None or not self.store.writable:
            return
        self.store.maybe_flush()
        if self.pace == "free" and self.store.pick_tier():
            self.store.compact()
        if self.pace == "free" and self.vectors is not None:
            self.vectors.warm()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._close_parts()
            finally:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._lock_fd)

    def _close_parts(self) -> None:
        """Flushes and closes the parts; each step adds its part here."""
        if self.vectors is not None:
            self.vectors.close()
        if self.store is not None:
            self.store.close()


def _part_types() -> dict:
    from jarvis.knowledge.documents import IndexPart
    from jarvis.knowledge.lexical import LexicalPart
    from jarvis.knowledge.vectors import VectorPart
    return {t.name: t for t in (IndexPart, LexicalPart, VectorPart)}


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
