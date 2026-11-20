"""A package's evidence: where it is, its manifest, what Tim knows of each
item, its index and its links (workflow.yaml gather, plan layout and the
steps set_knowledge_state, index and scoped_search; tim_knowledge).

Evidence is gathered once at the package level and reused by every run of
the package (Tim's answer, 2026-11-19):

    /work/<id>/packages/<package id>/evidence/      (records, the engine)
    data/work/<id>/packages/<package id>/evidence/  (bytes, on disk)

or `/packages/<package id>/evidence/` and `data/package/<package id>/
evidence/` for a package with no Work item. The content root maps each
disk folder to its path (knowledge_content.jarvis_roots), so every stored
file is an item there, split into sections and indexed by words and
meaning. In it:

    handoff.json       the handoff as submitted, hashed
    files/             each file entry, copied in, its name kept
    retrieved/         each fetched reference, `<type>-<id>.md`
    given/             each content entry, `<n>.md`, as given
    jarvis/            what JARVIS gathers from its own records
    sender-answers/    the sender's answers, `<n>.md` a time it is asked
    manifest.json      one row per stored file (`Row`)

Every row carries Tim's knowledge state, set here by a program from where
the item came, never guessed by a model (`knowledge_state`). The evidence is
indexed urgently (`index_now`: read into the engine by the content child's
own reader at once; its vectors ahead of the background build,
vectors.build_rank), and `wait_indexed` waits for every item, its vectors
included, at most INDEX_WAIT_SEC, then the run is in Error. Once indexed,
the package's own record, `<package id>.evidence` in the package's folder,
names every stored file and every record gathered, which the engine makes
its reference links (`link`).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import data_paths, records
from jarvis.knowledge import paths as item_paths

FILES, RETRIEVED, GIVEN, JARVIS, SENDER_ANSWERS = (
    "files", "retrieved", "given", "jarvis", "sender-answers")
HANDOFF, MANIFEST = "handoff.json", "manifest.json"
# What a row says came of it (gather layout manifest.json).
OK, NOT_FOUND, FAILED = "ok", "not_found", "failed"
# Tim's three knowledge states (tim_knowledge, 2026-11-19).
KNOWS, HAD_ACCESS, PROBABLY_NOT = "knows", "had_access", "probably_does_not_know"
STATES = (KNOWS, HAD_ACCESS, PROBABLY_NOT)
# The manifest's row types beside the fourteen handoff types.
JARVIS_TYPE, SENDER_ANSWER = "jarvis", "sender_answer"
# Urgent indexing is waited for 10 minutes before Error (Tim, 2026-11-19:
# "three numbers").
INDEX_WAIT_SEC = 10 * 60.0
INDEX_LOOK_SEC = 1.0
RECORD_TYPE = "evidence"


@dataclass
class Row:
    """One stored file (or one reference that gave none)."""
    id: str
    type: str                       # one of the 14, or jarvis / sender_answer
    source: str                     # its path, label, address or record path
    stored: Optional[str] = None    # its path inside evidence/, None when not stored
    sha256: Optional[str] = None
    size: Optional[int] = None
    resolved: str = OK              # ok | not_found | failed
    reason: str = ""                # why not, when not ok
    knowledge_state: str = PROBABLY_NOT
    entry: Optional[dict] = None    # the typed entry it came from
    origin: str = "handoff"         # handoff, label <id>, work item <id>, jarvis
    why: str = ""                   # a label's entry: why it matches (RetrievalSession)
    record: Optional[str] = None    # the JARVIS record it read, an item path


@dataclass(frozen=True)
class Place:
    """Where one package's evidence is: on disk and in the engine."""
    work: Optional[str]
    package: str

    @property
    def _segs(self) -> tuple[Optional[str], str]:
        return (records.segment(self.work) if self.work else None,
                records.segment(self.package))

    @property
    def folder(self) -> Path:
        """On disk."""
        work, package = self._segs
        base = data_paths.work_dir() / work / "packages" if work else data_paths.package_dir()
        return base / package / item_paths.EVIDENCE

    @property
    def path(self) -> str:
        """In the engine: the one evidence path every step, search and
        instruction names."""
        return item_paths.package_evidence_folder(*self._segs)

    @property
    def record(self) -> str:
        """The package's own record: what it gathered, linked."""
        return f"{item_paths.parent(self.path)}/{self._segs[1]}.{RECORD_TYPE}"

    def item(self, stored: str) -> str:
        """A stored file's item path."""
        return f"{self.path}/{stored}"

    def write(self, stored: str, data: bytes) -> tuple[str, int]:
        """A file written into the evidence: its SHA-256 and size."""
        return write_file(self.folder / stored, data)


def write_file(target: Path, data: bytes) -> tuple[str, int]:
    """A file written whole, in one rename: its SHA-256 and size."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return hashlib.sha256(data).hexdigest(), len(data)


def place_of(doc: dict) -> Place:
    """The evidence of a run's package (package_jobs' job record)."""
    return Place(doc.get("work") or None, doc["package"])


# ── the manifest ──────────────────────────────────────────────────────────

def read_manifest(place: Place) -> list[Row]:
    try:
        rows = json.loads((place.folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Row(**r) for r in rows if isinstance(r, dict)]


def write_manifest(place: Place, rows: list[Row]) -> None:
    place.write(MANIFEST, json.dumps([asdict(r) for r in rows], indent=2,
                                     ensure_ascii=False).encode("utf-8"))


# ── what Tim knows of each item ──────────────────────────────────────────

# The stored files of JARVIS's own records, by what they are.
TIM_RECORD = "jarvis/tim.md"
WORK_ITEM_RECORD = "jarvis/work-item"
SENDER_SESSION = "jarvis/sender-session.md"


def _package_state(inbox_id: str) -> str:
    """A package presented to him: answered or signed off, he knows it;
    opened, he had access to it as a whole for now (package granularity);
    never opened, he probably does not know it."""
    from jarvis import package_inbox
    row = package_inbox.get(inbox_id)
    if row is None:
        return PROBABLY_NOT
    if row.answered_at or row.signed_off_at:
        return KNOWS
    return HAD_ACCESS if row.opened_at else PROBABLY_NOT


def _message_state(message_id: str) -> str:
    """His own words he knows; a message to him read to him or answered he
    knows; one still new he probably does not."""
    from jarvis import conversation_log
    try:
        item = conversation_log.get(int(message_id))
    except (TypeError, ValueError):
        item = None
    if item is None:
        return PROBABLY_NOT
    if not item.inbound:
        return KNOWS
    return KNOWS if item.state not in (None, conversation_log.MessageState.NEW) else PROBABLY_NOT


def knowledge_state(row: Row) -> str:
    """Tim's knowledge state of one gathered item, from where it came
    (workflow.yaml tim_knowledge knowledge_state): knows what he supplied,
    said or acted on; had access to what was where he could see it (a Work
    item, a workflow, an opened package); probably does not know the rest
    (test results, CI logs, agent transcripts, an agent's own notes, the
    sender's words, what could not be determined)."""
    if row.resolved != OK:
        return PROBABLY_NOT
    if row.type == JARVIS_TYPE:
        if row.stored == TIM_RECORD:
            return KNOWS
        if (row.stored or "").startswith(WORK_ITEM_RECORD):
            return HAD_ACCESS
        return PROBABLY_NOT
    if row.type in ("work_item", "workflow"):
        return HAD_ACCESS
    if row.type == "package" and row.entry:
        return _package_state(row.entry.get("id", ""))
    if row.type == "message" and row.entry:
        return _message_state(row.entry.get("id", ""))
    return PROBABLY_NOT


# ── the index ─────────────────────────────────────────────────────────────

def _root_and_rel(folder: Path):
    """The content root a folder of JARVIS's is under, and its path
    relative to it."""
    from jarvis import knowledge_content
    real = os.path.realpath(folder)
    for root in knowledge_content.jarvis_roots():
        base = os.path.realpath(root.path)
        if real.startswith(base + os.sep):
            return root, os.path.relpath(real, base)
    raise LookupError(f"no content root holds {folder}")


def index_folder(folder: Path, uds=None) -> list[str]:
    """Every file under a folder of JARVIS's read into the engine now by the
    content child's own reader (knowledge_content.Indexer), ahead of the
    background reading ("When it chagnes ALL UDS is write -> index
    always"); the item paths of the files it holds. A file the reader
    passes over (no item path, a sensitive name) is not one."""
    from jarvis import knowledge_content
    root, rel = _root_and_rel(folder)
    indexer = knowledge_content.Indexer(uds or records.uds())
    for found in indexer.scan(root, under=rel, reason="indexed at once"):
        indexer.extract(root, found)
    return sorted(d["_path"] for d in indexer.known(root, rel).values())


def index_now(place: Place, uds=None) -> list[str]:
    """The package's evidence read into the engine now (`index_folder`)."""
    return index_folder(place.folder, uds)


@dataclass
class Waiting:
    """What is still not indexed, and why it cannot be."""
    paths: list[str] = field(default_factory=list)
    off: bool = False               # no embedding model: vectors never come


def not_indexed(items: list[str], uds=None) -> Waiting:
    """Of these items, those not yet indexed: their text not read yet, or a
    vector (whole or a section's) not made yet. An unreadable file has
    nothing to index and is done."""
    from jarvis.knowledge.uds import Stream
    from jarvis.knowledge_content import WRITER
    uds = uds or records.uds()
    out = Waiting()
    for path in items:
        item = uds.get(WRITER, path, {Stream.DATA, Stream.VECTORS})
        if item is None or (item.data or {}).get("state") == "pending":
            out.paths.append(path)
            continue
        vectors = item.vectors
        if vectors is None:
            continue
        if vectors.get("state") == "off":
            out.off = True
            out.paths.append(path)
        elif not vectors.get("complete"):
            out.paths.append(path)
    return out


SLEEP: Callable[[float], None] = time.sleep
CLOCK: Callable[[], float] = time.monotonic


def wait_indexed(place: Place, step: str, uds=None) -> list[str]:
    """Index the evidence now and wait until every item is indexed, its
    vectors included, at most INDEX_WAIT_SEC; then the run is in Error
    (package_jobs.Blocked). The items' paths."""
    from jarvis.package_jobs import Blocked
    items = index_now(place, uds)
    started = CLOCK()
    while True:
        waiting = not_indexed(items, uds)
        if not waiting.paths:
            return items
        if waiting.off:
            raise Blocked(step, "The package's evidence cannot be indexed by meaning: no "
                          "embedding model is installed.",
                          f"JARVIS read {len(items)} evidence file(s) into the knowledge engine.",
                          "Install the embedding model (Settings, Knowledge), then a new run.")
        if CLOCK() - started >= INDEX_WAIT_SEC:
            raise Blocked(step, f"{len(waiting.paths)} of the package's {len(items)} evidence "
                          f"file(s) were not indexed within 10 minutes.",
                          f"JARVIS read the evidence into the knowledge engine at once and "
                          f"waited 10 minutes for its words and meaning to be indexed "
                          f"(still waiting: {', '.join(waiting.paths[:5])}).",
                          "Check Settings, Knowledge (indexing on, the engine running); once "
                          "it indexes, a new run.")
        SLEEP(INDEX_LOOK_SEC)


# ── the package's links ──────────────────────────────────────────────────

def link(place: Place, items: list[str], rows: list[Row]) -> str:
    """The package's own record, naming each stored file and each record it
    gathered: the engine makes a reference link from it to each (the first
    step of co-use). Its path."""
    gathered = list(dict.fromkeys(items))
    read = list(dict.fromkeys(r.record for r in rows if r.record and item_paths.is_path(r.record)))
    records.write(place.record, {"package": place.package, "work": place.work,
                                 "evidence": place.path, "gathered": gathered, "records": read,
                                 "rows": len(rows), "gathered_at": time.time()},
                  by=records.JARVIS, change="evidence gathered",
                  summary=f"{len(gathered)} file(s), {len(read)} record(s)")
    return place.record


def gathered(place: Place) -> Optional[dict]:
    """The package's record once its evidence is gathered, else None."""
    rec = records.get(place.record)
    return rec.data if rec is not None and rec.data.get("gathered_at") else None


# ── the sender's answers ─────────────────────────────────────────────────

def add_sender_answers(place: Place, round_no: int, questions: list[dict],
                       answers: dict) -> Row:
    """The sender's answers to one round of questions, written as
    `sender-answers/<n>.md`, a row in the manifest and indexed as it is
    written (swarm ask_sender; gather index)."""
    lines = [f"# The sender's answers, round {round_no}", ""]
    asked = {q.get("id"): q.get("question", "") for q in questions}
    for key, answer in answers.items():
        lines += [f"## {key}: {asked.get(key, '')}", "", str(answer), ""]
    stored = f"{SENDER_ANSWERS}/{round_no}.md"
    sha, size = place.write(stored, "\n".join(lines).encode("utf-8"))
    rows = [r for r in read_manifest(place) if r.stored != stored]
    row = Row(id=f"answers-{round_no}", type=SENDER_ANSWER, source=f"round {round_no}",
              stored=stored, sha256=sha, size=size, origin="sender")
    row.knowledge_state = knowledge_state(row)
    write_manifest(place, rows + [row])
    try:
        index_now(place)
    except Exception:  # noqa: BLE001 - the content child reads it once the engine answers
        pass
    return row
