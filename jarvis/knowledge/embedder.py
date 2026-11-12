"""The embedder interface (design section 7.11).

The engine reaches an embedder only through `RUNNER`, a factory that
returns an `Embedder` or None. The default, `installed`, gives the model
installed under the data folder (embedding_models.py), run by the runtime
its record names (`RUNTIMES`: `onnx-e5` is multilingual-e5-small through
onnxruntime, e5_onnx.py), loaded once; with `JARVIS_KNOWLEDGE` off, no
model installed, or a runtime that cannot load, it is None, and then the vector part has nothing
to search and every query says vectors were not used. Another runtime slots
in as one more entry of `RUNTIMES`. Tests replace `RUNNER` with
`FakeEmbedder`, which hashes words into deterministic vectors.

An embedder's `id` names its space: vectors of two spaces are never
compared, so a changed model builds a new space beside the old one.

One thread embeds, taking work from a priority queue in which a query's
texts always go first, and ingest is sent in batches of at most
`INGEST_BATCH` texts, so a query waits behind at most one small batch.
Text is capped at `TEXT_MAX` characters with Markdown syntax removed.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import math
import queue
import re
import threading
from array import array
from typing import Callable, Optional, Protocol

from jarvis import spoken_words
from jarvis.knowledge import lanes

QUERY = 0
INGEST = 1
INGEST_BATCH = 4
TEXT_MAX = 1000
_MARKDOWN = re.compile(r"[#*_`>|\[\]]+")


class Embedder(Protocol):
    id: str
    dim: int

    def embed(self, texts: list[str], purpose: str) -> list[array]:
        """L2-normalised float32 vectors, one per text; `purpose` is
        `query` or `passage`."""


def _onnx_e5(folder, model) -> "Embedder":
    from jarvis.knowledge.e5_onnx import E5Onnx
    return E5Onnx(folder, model.space, model.dim)


# Which class runs a model, by the `runtime` its record names.
RUNTIMES: dict[str, Callable] = {"onnx-e5": _onnx_e5}

_loaded: dict[str, Optional["Embedder"]] = {}
_load_lock = threading.Lock()
log = logging.getLogger("jarvis.knowledge.embedder")


def installed() -> Optional["Embedder"]:
    """The installed model's embedder, loaded once per folder; None while
    `JARVIS_KNOWLEDGE` is off (the engine runs, but embeds nothing), none is
    installed, or its runtime cannot load (said once in the log)."""
    from jarvis import embedding_models, knowledge_client
    if not knowledge_client.enabled():
        return None
    model = embedding_models.DEFAULT
    folder = embedding_models.model_dir(model)
    key = str(folder)
    with _load_lock:
        if key in _loaded and _loaded[key] is not None:
            return _loaded[key]
        if embedding_models.model_problem(folder, model) is not None:
            return None
        if key in _loaded:          # it failed to load before; not again this run
            return None
        try:
            _loaded[key] = RUNTIMES[model.runtime](folder, model)
        except Exception:  # noqa: BLE001 - vectors off, the rest of the engine runs
            log.warning("the embedding model could not be loaded", exc_info=True)
            _loaded[key] = None
        return _loaded[key]


RUNNER: Optional[Callable[[], Optional[Embedder]]] = installed


def current() -> Optional[Embedder]:
    return RUNNER() if RUNNER is not None else None


def prepare(text: str) -> str:
    return _MARKDOWN.sub(" ", text or "")[:TEXT_MAX]


def normalise(values) -> array:
    norm = math.sqrt(math.fsum(v * v for v in values)) or 1.0
    return array("f", (v / norm for v in values))


class FakeEmbedder:
    """Deterministic vectors for tests: each folded word adds a signed one
    to a bucket chosen by its hash, so texts sharing words are close."""

    def __init__(self, dim: int = 32, name: str = "fake") -> None:
        self.dim = dim
        self.id = f"{name}-{dim}"
        self.calls: list[tuple[str, int]] = []

    def embed(self, texts: list[str], purpose: str) -> list[array]:
        self.calls.append((purpose, len(texts)))
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for w in spoken_words.folded_words(text):
                h = hashlib.blake2b(w.encode(), digest_size=8).digest()
                v[h[0] % self.dim] += 1.0 if h[1] & 1 else -1.0
            if not any(v):
                v[0] = 1.0
            out.append(normalise(v))
        return out


class EmbeddingQueue:
    """One embedding thread; queries before ingest; ingest in small
    batches."""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop = False
        self.batches: list[tuple[int, int]] = []    # (priority, size), for tests and health
        self._thread = threading.Thread(target=self._run, name="knowledge-embed", daemon=True)
        self._thread.start()

    def submit(self, texts: list[str], priority: int, purpose: str,
               done: Callable[[Optional[list[array]], Optional[BaseException]], None]) -> None:
        """Queue one batch; ingest batches hold at most `INGEST_BATCH` texts,
        so the caller splits them."""
        if priority == INGEST and len(texts) > INGEST_BATCH:
            raise ValueError(f"an ingest batch holds at most {INGEST_BATCH} texts")
        self._q.put((priority, next(self._seq), texts, purpose, done))

    def embed_now(self, texts: list[str], timeout: float, purpose: str = "query") -> Optional[list[array]]:
        """A query's embeddings ahead of every waiting ingest batch, or None
        past `timeout`."""
        box: dict = {}
        ready = threading.Event()

        def done(vectors, error):
            box["v"], box["e"] = vectors, error
            ready.set()
        self.submit([prepare(t) for t in texts], QUERY, purpose, done)
        if not ready.wait(timeout):
            return None
        if box.get("e") is not None:
            raise box["e"]
        return box["v"]

    def _run(self) -> None:
        # Ingest batches are background work; a query's batch is raised for
        # its span, since someone is waiting on it.
        lanes.background()
        while True:
            priority, _, texts, purpose, done = self._q.get()
            if texts is None:
                return
            try:
                if priority == QUERY:
                    with lanes.foreground():
                        vectors = self.embedder.embed(list(texts), purpose)
                else:
                    vectors = self.embedder.embed(list(texts), purpose)
                self.batches.append((priority, len(texts)))
                done(vectors, None)
            except BaseException as e:  # noqa: BLE001 - the caller decides
                done(None, e)

    def close(self) -> None:
        self._q.put((-1, -1, None, "", None))
        self._thread.join(timeout=5)
