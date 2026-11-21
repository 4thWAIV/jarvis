"""The gather step: the handoff's evidence resolved and kept, with what
JARVIS knows from its own records (workflow.yaml gather, decided
2026-11-18 and 2026-11-19: "THIS is just resolution not anything else").

Run as the first step of a package's run (package_jobs.PRODUCERS), in
JARVIS's own process, once per package: a later run of the same package
reuses the evidence already gathered (package_evidence.gathered) and only
waits for it to be indexed. In order:

  copy_files             each file entry copied into `files/`, its name kept
                         and its hash recorded; not there, not_found
  keep_content           each content entry into `given/<n>.md`, as given
  retrieve_labels        the free-text labels, all in one call, to
                         RetrievalSession (tier 3), which answers with typed
                         entries and why each matches and never fetches;
                         each entry is then handled as one of its type
  resolve_typed          every other type fetched by its program
                         (evidence_readers) into `retrieved/<type>-<id>.md`;
                         a reference named twice (in the handoff and on the
                         Work item) is one file
  gather_jarvis_material the Work item (its pieces, reports, notes and
                         history), the PRs and CI runs recorded against it,
                         the sending session's commands with their results
                         and its final messages, and Tim's record on it;
                         into `jarvis/`, the PRs and CI runs into `retrieved/`
  set_knowledge_state    each row's state, by program (package_evidence)
  manifest               `manifest.json`, one row per stored file
  index                  indexed urgently and waited for, then linked

Nothing here classifies, marks, summarises, judges or selects.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Optional

from jarvis import evidence_readers, package_evidence as pe, session_transcript
from jarvis.package_evidence import OK, NOT_FOUND, Row

STEP = "gather"


def _retrieval(doc: dict):
    """The RetrievalSession for this run: on the brain providers."""
    from jarvis import sessions
    return sessions.BrainRetrievalSession(Path(doc["folder"]), requester=doc.get("engine"))


RETRIEVAL: Callable[[dict], object] = _retrieval


class Gathering:
    """One package's evidence as it is gathered: its rows, in order, and the
    references already stored (by type and fields), so each is one file."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.place = pe.place_of(doc)
        self.rows: list[Row] = []
        self._stored: dict[tuple, Row] = {}
        self._names: set[str] = set()

    def _row(self, row: Row) -> Row:
        self.rows.append(row)
        return row

    def _unique(self, folder: str, name: str, n: int) -> str:
        """`folder/name`, or `folder/<n>/name` when the name is taken."""
        stored = f"{folder}/{name}"
        if stored in self._names:
            stored = f"{folder}/{n}/{name}"
        self._names.add(stored)
        return stored

    # copy_files
    def file(self, entry: dict, origin: str, n: int, why: str = "") -> Row:
        path = Path(entry["path"]).expanduser()
        rid = f"e{n}"
        if not path.is_file():
            return self._row(Row(rid, "file", str(path), resolved=NOT_FOUND, entry=entry,
                                 origin=origin, why=why, reason=f"There is no file at {path}."))
        key = ("file", str(path.resolve()))
        if key in self._stored:
            return self._stored[key]
        stored = self._unique(pe.FILES, path.name, n)
        sha, size = self.place.write(stored, path.read_bytes())
        row = self._row(Row(rid, "file", str(path), stored, sha, size, entry=entry,
                            origin=origin, why=why))
        self._stored[key] = row
        return row

    # keep_content
    def content(self, entry: dict, n: int) -> Row:
        stored = f"{pe.GIVEN}/{n}.md"
        sha, size = self.place.write(stored, entry["text"].encode("utf-8"))
        return self._row(Row(f"e{n}", "content", f"handoff evidence entry {n}", stored, sha, size,
                             entry=entry))

    # resolve_typed
    def typed(self, entry: dict, origin: str, n: int, why: str = "") -> Row:
        key = evidence_readers.key_of(entry)
        if key in self._stored:
            return self._stored[key]
        found = evidence_readers.read(entry)
        if found.entry is not None:          # a GitHub address: read as its own type
            row = self.typed(found.entry, origin, n, why)
            self._stored[key] = row
            return row
        rid = f"e{n}" if origin == "handoff" else f"{origin.replace(' ', '-')}-{n}"
        if found.resolved != OK:
            row = self._row(Row(rid, entry["type"], found.source or json.dumps(entry),
                                resolved=found.resolved, reason=found.reason, entry=entry,
                                origin=origin, why=why))
        else:
            stored = self._unique(pe.RETRIEVED, evidence_readers.name_of(entry), n)
            sha, size = self.place.write(stored, found.text.encode("utf-8"))
            row = self._row(Row(rid, entry["type"], found.source, stored, sha, size,
                                entry=entry, origin=origin, why=why,
                                record=found.source if found.source.startswith("/") else None))
        self._stored[key] = row
        return row

    def jarvis(self, stored: str, text: str, source: str, record: Optional[str] = None) -> Row:
        sha, size = self.place.write(stored, text.encode("utf-8"))
        return self._row(Row(stored.rsplit("/", 1)[-1].rsplit(".", 1)[0], pe.JARVIS_TYPE,
                             source, stored, sha, size, origin="jarvis", record=record))


# ── retrieve_labels ───────────────────────────────────────────────────────

def retrieve_labels(g: Gathering, labels: list[tuple[int, dict]]) -> None:
    """The labels to RetrievalSession in one call; each typed entry it gives
    handled as an entry of its type; a label it finds nothing for is
    not_found with what it searched. A model sweep that finds no model, or
    a session that cannot answer, puts the run in Error."""
    from jarvis import package_jobs, sessions
    if not labels:
        return
    references = [sessions.Reference(f"label-{n}", e["text"]) for n, e in labels]
    # The design gives retrieval no deadline: it answers when its lookups do.
    answered = package_jobs.asked(STEP, "retrieval",
                                  f"JARVIS asked retrieval what {len(labels)} label(s) name.",
                                  RETRIEVAL(g.doc).resolve(references, None))
    by_id = {a["id"]: a for a in answered}
    for n, entry in labels:
        said = by_id.get(f"label-{n}") or {"resolved": NOT_FOUND, "entries": [], "searched": ""}
        if said["resolved"] != OK:
            g._row(Row(f"e{n}", "label", entry["text"], resolved=NOT_FOUND, entry=entry,
                       reason=f"Retrieval found nothing it names; it searched: "
                              f"{said.get('searched') or 'nothing said'}"))
            continue
        for m, found in enumerate(said["entries"], 1):
            typed = {k: v for k, v in found.items() if k != "why"}
            origin = f"label {n}"
            if typed["type"] == "file":
                g.file(typed, origin, n * 100 + m, found.get("why", ""))
            else:
                g.typed(typed, origin, n * 100 + m, found.get("why", ""))


# ── gather_jarvis_material ────────────────────────────────────────────────

def _work_ids(doc: dict) -> list[str]:
    """The run's Work item (`--work`) and the Work items the handoff is
    about, each once, that JARVIS has a record of."""
    from jarvis import work_store
    ids = [doc.get("work")] + list((doc.get("request") or {}).get("work") or [])
    return [i for i in dict.fromkeys(i for i in ids if i) if work_store.get(i) is not None]


def recorded_against(work_id: str) -> list[dict]:
    """The PRs and CI runs recorded against a Work item: each piece's
    evidence that is a GitHub pull request's or CI run's address."""
    from jarvis import work_store
    item = work_store.get(work_id)
    out = []
    for piece in item.pieces if item is not None else []:
        for e in piece.evidence:
            entry = evidence_readers.github_entry(e.value) if e.kind == "url" else None
            if entry is not None and entry["type"] in ("pr", "ci_run"):
                out.append(entry)
    return out


def _all(read: Callable[[Optional[int]], list]) -> list:
    """Every item of a paged read, oldest first (`before` the oldest read)."""
    from jarvis import conversation_log
    out, before = [], None
    while True:
        page = read(before)
        out = page + out
        if len(page) < conversation_log.PAGE or not page:
            return out
        before = page[0].id


def tim_text(work_ids: list[str]) -> str:
    """Tim's record on these Work items: his messages on each, his answers
    to its decisions, his note when he said it is not done, and his answers
    on its earlier packages, his Reject words among them."""
    from jarvis import conversation_log, package_inbox, work_store
    parts = ["# Tim's record on this work", ""]
    for work_id in work_ids:
        item = work_store.get(work_id)
        parts += [f"## Work item {work_id}: {item.title}", ""]
        said = [i for i in _all(lambda b: conversation_log.work_thread(work_id, b))
                if not i.inbound]
        parts += ["### His messages", ""] + [f"- {i.id} ({i.at:.0f}): {i.text}" for i in said] \
            + (["- none"] if not said else []) + [""]
        answered = [a for a in item.answered if a.where != work_store.WITHDRAWN
                    and a.where not in work_store.PACKAGE_CLOSED]
        parts += ["### His answers to its decisions", ""] + \
            [f"- {a.key}: {a.question} His answer ({a.where}): {a.answer}" for a in answered] + \
            (["- none"] if not answered else []) + [""]
        if item.review_note:
            parts += ["### His note when he said it is not done yet", "", item.review_note, ""]
        parts += ["### His answers on its earlier packages", ""]
        packages = package_inbox.for_work(work_id)
        for row in packages:
            parts.append(f"- package {row.id} ({row.title}), {row.state}:")
            for q in evidence_readers.package_questions(row):
                if q.answer:
                    parts.append(f"  - {q.id}: {q.question} His answer: {q.answered_id()}")
        if not packages:
            parts.append("- none")
        parts.append("")
    return "\n".join(parts)


def rejections_text(package: str) -> str:
    """His Reject words on this package's delivered runs, each with its run."""
    from jarvis import package_jobs
    said = package_jobs.rejections(package)
    if not said:
        return ""
    return "\n".join(["## His Reject of this package's earlier runs", ""]
                     + [f"- {r['run']}: {r['words']}" for r in said] + [""])


def refresh_tim(doc: dict) -> Row:
    """Tim's record in the package's evidence made again (`jarvis/tim.md`,
    gather_jarvis_material's), now holding his Reject words on its earlier
    runs (workflow.yaml gather: "his Reject words -> evidence/jarvis/tim.md"),
    so the next run reads them: its manifest row's hash and size made new,
    and indexed as it is written. The row."""
    g = Gathering(doc)
    ids = _work_ids(doc)
    text = tim_text(ids) if ids else "# Tim's record on this work\n"
    text = text.rstrip("\n") + "\n\n" + rejections_text(doc["package"])
    rows = [r for r in pe.read_manifest(g.place) if r.stored != pe.TIM_RECORD]
    from jarvis import package_jobs, work_store
    source = (", ".join(work_store.path_of(i) for i in ids) if ids
              else package_jobs.path_of(doc))
    sha, size = g.place.write(pe.TIM_RECORD, text.encode("utf-8"))
    row = Row("tim", pe.JARVIS_TYPE, source, pe.TIM_RECORD, sha, size, origin="jarvis")
    row.knowledge_state = pe.knowledge_state(row)
    pe.write_manifest(g.place, rows + [row])
    try:
        pe.index_now(g.place)
    except Exception:  # noqa: BLE001 - the content child reads it once the engine answers
        pass
    return row


# The tools that run a command: Claude Code's Bash, Codex's shell calls.
COMMAND_TOOLS = ("Bash", "shell", "exec_command", "local_shell_call", "shell_command")


def sender_session_text(engine: str, session: str) -> tuple[Optional[str], str]:
    """The sending session's commands with their results, and its final
    messages (each turn's last words), from its own transcript; not its
    whole transcript. (text, its path) or (None, why not)."""
    path = session_transcript.find(engine or "", session)
    if path is None:
        return None, f"No {engine or 'known'} transcript of session {session} is on this Mac."
    commands: list[dict] = []
    runs: list[dict] = []       # Codex's own record of each command it ran
    by_call: dict[str, dict] = {}
    finals: list[str] = []
    last: Optional[str] = None
    for obj in session_transcript.lines(path):
        for cid, tool, given in session_transcript.calls(obj):
            if tool in COMMAND_TOOLS:
                try:
                    value = json.loads(given)
                except ValueError:
                    value = given
                command = value.get("command", value.get("cmd", given)) \
                    if isinstance(value, dict) else value
                row = {"command": command if isinstance(command, str) else json.dumps(command),
                       "at": session_transcript.when(obj), "result": None}
                commands.append(row)
                by_call[cid] = row
        for cid, output in session_transcript.results(obj):
            if cid in by_call:
                by_call[cid]["result"] = output
        run = session_transcript.command_run(obj)
        if run is not None:
            runs.append({"command": run[0], "at": session_transcript.when(obj), "result": run[1]})
        said = session_transcript.said(obj)
        if said is not None:
            role, words = said
            if role == "user":
                if last is not None:
                    finals.append(last)
                last = None
            else:
                last = words
    if last is not None:
        finals.append(last)
    # A Codex that records each command it ran with its whole output records
    # the same commands its tool calls started: its record is the one kept.
    commands = runs or commands
    out = [f"# The sending session {session}", "", f"Source: {path}", "",
           "## The commands it ran and their results", ""]
    for n, c in enumerate(commands, 1):
        out += [f"### {n}. {c['command']}", "", "```", str(c["result"] if c["result"] is not None
                                                          else "(no result recorded)"), "```", ""]
    out += ["## Its final messages", ""]
    for n, words in enumerate(finals, 1):
        out += [f"### {n}", "", words, ""]
    return "\n".join(out), str(path)


def gather_jarvis_material(g: Gathering) -> None:
    from jarvis import work_store
    ids = _work_ids(g.doc)
    for work_id in ids:
        text = evidence_readers.work_item_text(work_id)
        stored = (f"{pe.WORK_ITEM_RECORD}.md" if len(ids) == 1
                  else f"{pe.WORK_ITEM_RECORD}-{work_id}.md")
        g.jarvis(stored, text, work_store.path_of(work_id), record=work_store.path_of(work_id))
    for work_id in ids:
        for n, entry in enumerate(recorded_against(work_id), 1):
            g.typed(entry, f"work item {work_id}", n)
    text, source = sender_session_text(g.doc.get("engine") or "", g.doc["session"])
    if text is None:
        g._row(Row("sender-session", pe.JARVIS_TYPE, f"session {g.doc['session']}",
                   resolved=NOT_FOUND, reason=source, origin="jarvis"))
    else:
        g.jarvis(pe.SENDER_SESSION, text, source)
    if ids:
        g.jarvis(pe.TIM_RECORD, tim_text(ids), ", ".join(work_store.path_of(i) for i in ids))


# ── the step ──────────────────────────────────────────────────────────────

def _handoff(g: Gathering) -> None:
    from jarvis import package_jobs
    submitted = g.doc.get("submitted")
    data = json.dumps(submitted if submitted is not None else g.doc["request"], indent=2,
                      ensure_ascii=False).encode("utf-8")
    sha, size = g.place.write(pe.HANDOFF, data)
    g._row(Row("handoff", pe.JARVIS_TYPE, package_jobs.path_of(g.doc), pe.HANDOFF, sha, size,
               origin="jarvis", record=package_jobs.path_of(g.doc)))


def gather(doc: dict) -> None:
    """The gather step of one run (package_jobs.PRODUCERS["gather"])."""
    place = pe.place_of(doc)
    if pe.gathered(place) is not None:
        pe.wait_indexed(place, STEP)        # gathered once: every run reuses it
        return
    if place.folder.exists():               # a run that stopped part way: gathered again
        shutil.rmtree(place.folder)
    g = Gathering(doc)
    _handoff(g)
    labels = []
    for n, entry in enumerate((doc.get("request") or {}).get("evidence") or [], 1):
        kind = entry["type"]
        if kind == "file":
            g.file(entry, "handoff", n)
        elif kind == "content":
            g.content(entry, n)
        elif kind == "label":
            labels.append((n, entry))
        else:
            g.typed(entry, "handoff", n)
    retrieve_labels(g, labels)
    gather_jarvis_material(g)
    for row in g.rows:
        row.knowledge_state = pe.knowledge_state(row)
    pe.write_manifest(place, g.rows)
    items = pe.wait_indexed(place, STEP)
    pe.link(place, items, g.rows)
