"""The gather step: the handoff's evidence resolved and kept, with what
JARVIS knows from its own records. Tim limited this step to evidence
resolution on 2026-11-18 and 2026-11-19 (workflow.yaml gather).

Run as the first step of a package's run (package_jobs.PRODUCERS), in
JARVIS's own process, once per package: a later run of the same package
reuses the evidence already gathered (package_evidence.gathered) and only
waits for it to be indexed. A new package reads everything again: every
program read (the files, the content, each reference, the Work item, the
PRs and CI runs recorded against it, the sending session's transcript and
Tim's record) is live, so it is always read, and the gather unit's key
(step_cache.py) is the hash of what was read. The one model call, the
labels to RetrievalSession, is two units: `gather-lookups`, keyed by the
labels, the retrieval role and its code, and everything the program read
before it, keeps the lookups retrieval made; `gather-labels`, its answer,
is keyed by that and what those lookups find when made again now. It is
reused only while all of that is unchanged, and the typed entries it gave
are always fetched again. The steps after it key on each
stored file's hash, so an unchanged file is never marked again and a
changed one always is. In order:

  copy_files             each file entry copied into `files/`, its name kept
                         and its hash recorded; not there, not_found
  keep_content           each content entry into `given/<n>.md`, as given
  resolve_typed          every other type fetched by its program
                         (evidence_readers) into `retrieved/<type>-<id>.md`;
                         a reference named twice (in the handoff and on the
                         Work item) is one file
  gather_jarvis_material the Work item (its pieces, reports, notes and
                         history), the PRs and CI runs recorded against it,
                         the sending session's commands with their results
                         and its final messages, and Tim's record (what he
                         said on the Work item, then what he said anywhere
                         else that bears on the package, found in the
                         engine); into `jarvis/`, the PRs and CI runs into
                         `retrieved/`
  retrieve_labels        the free-text labels, all in one call, to
                         RetrievalSession (tier 3), which answers with typed
                         entries and why each matches and never fetches;
                         each entry is then handled as one of its type
  set_knowledge_state    each row's state, by program (package_evidence)
  manifest               `manifest.json`, one row per stored file
  index                  indexed urgently and waited for, then linked

Nothing here classifies, marks, summarises, judges or selects.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from jarvis import evidence_readers, package_evidence as pe, session_transcript, step_cache
from jarvis.package_evidence import OK, NOT_FOUND, Row

STEP = "gather"


def _retrieval(doc: dict):
    """The RetrievalSession for this run: on the brain providers, every
    lookup it makes logged (`LOOKUPS_LOG`), so what it found is its
    answer's input (`found_digest`)."""
    from jarvis import sessions
    return sessions.BrainRetrievalSession(Path(doc["folder"]), requester=doc.get("engine"),
                                          refused=step_cache.refused(doc),
                                          search_log=lookups_log(doc))


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

def retrieve_labels(g: Gathering, labels: list[tuple[int, dict]],
                    answered: Optional[list] = None) -> list:
    """The labels to RetrievalSession in one call (unless `answered`, its
    answer kept from before, is given); each typed entry it gives handled
    as an entry of its type, fetched by its program now; a label it finds
    nothing for is not_found with what it searched. A model sweep that
    finds no model, or a session that cannot answer, puts the run in
    Error. Its answer."""
    from jarvis import package_jobs, sessions
    if not labels:
        return []
    if answered is None:
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
    return list(answered)


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
    """Tim's record on these Work items (workflow.yaml gather
    gather_jarvis_material: his messages and answers on the Work item, his
    answers on its earlier packages, his Reject words): his messages on
    each, his answers to its decisions, his own acts on it (his review, his
    note when he said it is not done, a hold he released, work he added),
    his answers on every package sent for it, open or closed, with his own
    words, and his Reject of its package runs, with his words. Part 1 of
    his record (`tim_record`), its headings under that part's."""
    from jarvis import conversation_log, package_inbox, package_jobs, work_store
    parts: list[str] = []
    for work_id in work_ids:
        item = work_store.get(work_id)
        parts += [f"### Work item {work_id}: {item.title}", ""]
        said = [i for i in _all(lambda b: conversation_log.work_thread(work_id, b))
                if not i.inbound]
        parts += ["#### Their messages", ""] + [f"- {i.id} ({i.at:.0f}): {i.text}" for i in said] \
            + (["- none"] if not said else []) + [""]
        answered = [a for a in item.answered if a.where != work_store.WITHDRAWN
                    and a.where not in work_store.PACKAGE_CLOSED]
        parts += ["#### Their answers to its decisions", ""] + \
            [f"- {a.key}: {a.question} Their answer ({a.where}): {a.answer}" for a in answered] + \
            (["- none"] if not answered else []) + [""]
        acts = work_store.events(work_id, kinds=set(work_store.HIS_KINDS)
                                 - {work_store.DECISION_MADE}, limit=10_000)
        parts += ["#### Their acts on it", ""] + \
            [f"- {e.seq} {e.kind} ({e.at:.0f}): {e.summary}"
             + (f" {json.dumps(e.detail, ensure_ascii=False)}" if e.detail else "")
             for e in acts] + (["- none"] if not acts else []) + [""]
        if item.review_note:
            parts += ["#### Their note when they said it is not done yet", "", item.review_note, ""]
        parts += ["#### Their answers on its earlier packages", ""]
        packages = package_inbox.every_for_work(work_id)
        for row in packages:
            why = f" ({row.closed_why})" if row.closed_why else ""
            parts.append(f"- package {row.id} ({row.title}), {row.state}{why}:")
            answers = [q for q in evidence_readers.package_questions(row) if q.answer]
            parts += [f"  - {q.id}: {q.question} Their answer: {q.answered_id(note_label='their words')}" for q in answers]
            if not answers:
                parts.append("  - nothing answered")
        if not packages:
            parts.append("- none")
        parts += ["", "#### Their Reject of its package runs", ""]
        rejected = package_jobs.rejections_on_work(work_id)
        parts += [f"- {r['package']} {r['run']}: {r['words']}" for r in rejected] + \
            (["- none"] if not rejected else [])
        parts.append("")
    return "\n".join(parts)


def rejections_text(doc: dict) -> str:
    """His Reject words on the delivered runs of this package and of every
    earlier package asked with the same handoff by the same session (a
    package with no Work item), each with its package and run."""
    from jarvis import package_jobs
    said = package_jobs.rejections_of_request(doc)
    if not said:
        return ""
    return "\n".join(["### Their Reject of this package's earlier runs, and of earlier packages "
                      "asked with the same handoff", ""]
                     + [f"- {r['package']} {r['run']}: {r['words']}" for r in said] + [""])


# Tim's record comes from everything he has said (construction-drafts.md,
# decided by Tim): what he said on the package's own Work item, and what he
# said anywhere else that bears on the package's questions and evidence,
# found in the engine by words and meaning. The two are kept apart, the
# package's own first and weighted higher.
OWN_PART = ("## Part 1: on this package's own Work item (weighted higher: their word on this "
            "work itself)")
ELSEWHERE_PART = ("## Part 2: what they said anywhere else that bears on this package's "
                  "questions and evidence (weighted lower than Part 1)")
# The records that hold what he said: the conversation's turns, messages
# (his to agents), Work items (his answers and decisions) and packages (his
# answers in them).
HIS_RECORDS = ("turn", "message", "work", "package")
# His words in the conversation's turns: what he said and typed.
HIS_TURNS = ("heard", "typed")
# His words in a message document: what he sent an agent.
HIS_MESSAGE_ITEMS = ("to_agent",)


def _patience() -> float:
    """How long a search for his words is waited for: the run's own wall
    clock (Settings' run_timeout_sec, run_executor.bounds), never a
    deadline that drops results."""
    from jarvis import run_executor
    return run_executor.bounds()[0]


def _engine_search():
    from jarvis import work_research
    return work_research.engine_search(_patience())


# How Tim's record searches the engine for what he said elsewhere (tests
# give their own): work_research.Search.
SEARCH: Callable[[], Callable] = _engine_search


def tim_terms(doc: dict) -> list[str]:
    """What his words elsewhere are searched for: each of the handoff's
    questions, and the words of its evidence (a label's or content's text,
    and why any entry was given), each once."""
    request = doc.get("request") or {}
    terms = [str(q.get("text") or "") for q in request.get("questions") or []]
    for entry in request.get("evidence") or []:
        if entry.get("type") in ("label", "content"):
            terms.append(str(entry.get("text") or ""))
        terms.append(str(entry.get("why") or ""))
    return [t for t in dict.fromkeys(t.strip() for t in terms) if t]


def _his_words(path: str, own: set[str]) -> list[str]:
    """What he said in the record at `path`, as lines; none when it holds
    nothing of his, or is of the package's own Work items (Part 1)."""
    from jarvis import message_records, package_inbox, records, work_store
    kind = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    rec = records.get(path)
    if rec is None:
        return []
    data = rec.data
    if kind == "turn":
        if data.get("kind") not in HIS_TURNS or not data.get("text"):
            return []
        return [f"- they {'said' if data['kind'] == 'heard' else 'typed'} "
                f"(turn {data.get('id')}, {float(data.get('at') or 0):.0f}): {data['text']}"]
    if kind == "message":
        out = []
        for item in message_records.doc_items(rec):
            if item.get("kind") in HIS_MESSAGE_ITEMS and item.get("text") \
                    and item.get("about") not in own:
                out.append(f"- their message {item.get('id')} to "
                           f"{item.get('agent_name') or 'an agent'} "
                           f"({float(item.get('at') or 0):.0f}): {item['text']}")
        return out
    if kind == "work":
        item_id = str(data.get("id") or "")
        item = work_store.get(item_id) if item_id and item_id not in own else None
        if item is None:
            return []
        out = [f"- their answer on Work item {item.id} ({item.title}), {a.key}: {a.question} "
               f"Their answer ({a.where}): {a.answer}"
               for a in item.answered if a.where != work_store.WITHDRAWN
               and a.where not in work_store.PACKAGE_CLOSED and a.answer]
        if item.review_note:
            out.append(f"- their note on Work item {item.id} ({item.title}) when they said it is "
                       f"not done yet: {item.review_note}")
        return out
    if kind == "package":
        row = package_inbox.get(str(data.get("id") or ""))
        if row is None or (row.work_id and row.work_id in own):
            return []
        return [f"- their answer in package {row.id} ({row.title}), {q.id}: {q.question} "
                f"Their answer: {q.answered_id(note_label='their words')}"
                for q in evidence_readers.package_questions(row) if q.answer]
    return []


def said_elsewhere(doc: dict, own: list[str]) -> str:
    """Part 2 of Tim's record: what he said anywhere else that bears on the
    package's questions and evidence (his conversation turns, messages,
    answers and decisions), found in the engine by words and meaning, each
    search run at once (work_research.at_once, engine_search with the run's
    own patience); each record once, with the terms it was found for, in
    the order found. A search that could not be made says so."""
    from jarvis import work_research
    terms = tim_terms(doc)
    parts = [ELSEWHERE_PART, ""]
    if not terms:
        return "\n".join(parts + ["- nothing to search for: no questions or evidence words", ""])
    search = SEARCH()
    calls = [(lambda t=t, k=k: [work_research._search(search, "knowledge_search", t, None, [k])])
             for t in terms for k in HIS_RECORDS]
    looked = work_research.at_once(calls)
    found: dict[str, list[str]] = {}
    failed = []
    for q in looked:
        if q.get("error"):
            failed.append(f"- could not be searched ({q['kinds'][0]}, {q['query']}): "
                          f"{q['error']}")
        for hit in q["hits"]:
            path = str(hit.get("path") or "")
            if path:
                found.setdefault(path, [])
                if q["query"] not in found[path]:
                    found[path].append(q["query"])
    mine = set(own)
    lines = []
    for path, why in found.items():
        said = _his_words(path, mine)
        if said:
            lines += said + [f"  (found for: {'; '.join(why)})"]
    parts += lines or ["- nothing of theirs found elsewhere"]
    return "\n".join(parts + failed + [""])


def tim_record(doc: dict) -> tuple[str, str]:
    """Tim's record for a run (`jarvis/tim.md`) and its source, in two
    parts kept apart: Part 1, his record on the run's Work items (his
    Reject of their package runs among it; with no Work item, his Reject
    words on this package's earlier runs), first and weighted higher; Part
    2, what he said anywhere else that bears on the package
    (`said_elsewhere`)."""
    from jarvis import package_jobs, work_store
    ids = _work_ids(doc)
    if ids:
        own = tim_text(ids)
        source = ", ".join(work_store.path_of(i) for i in ids)
    else:
        own = rejections_text(doc) or "- no Work item, and nothing of theirs on " \
            "this package yet\n"
        source = package_jobs.path_of(doc)
    text = "\n".join(["# The current user's record on this work", "", OWN_PART, "", own,
                      said_elsewhere(doc, ids)])
    return text, source + ", the engine (their words elsewhere)"


def refresh_tim(doc: dict) -> Row:
    """His Reject words on the package's earlier runs written into its
    evidence (`jarvis/tim-rejected.md`, workflow.yaml gather: "his Reject
    words -> evidence"), so the next run reads them: a row of its own in the
    manifest, written after the evidence was gathered as the sender's
    answers are (package_evidence.gathered_rows), indexed as it is written.
    Tim's record as gathered (`jarvis/tim.md`) stays as it was, so what
    read it is reused (step_cache.py); his words reach the next run as the
    revision they are. The row."""
    from jarvis import package_jobs
    g = Gathering(doc)
    said = package_jobs.rejections(doc["package"])
    text = "\n".join(["# The current user's Reject of this package's earlier runs", ""]
                     + [f"- {r['run']}: {r['words']}" for r in said] + [""])
    rows = [r for r in pe.read_manifest(g.place) if r.stored != pe.TIM_REJECTED]
    sha, size = g.place.write(pe.TIM_REJECTED, text.encode("utf-8"))
    row = Row("tim-rejected", pe.JARVIS_TYPE, package_jobs.path_of(doc), pe.TIM_REJECTED, sha,
              size, origin="jarvis")
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
    text, source = tim_record(g.doc)
    g.jarvis(pe.TIM_RECORD, text, source)


# ── the step ──────────────────────────────────────────────────────────────

def _handoff(g: Gathering) -> None:
    from jarvis import package_jobs
    submitted = g.doc.get("submitted")
    data = json.dumps(submitted if submitted is not None else g.doc["request"], indent=2,
                      ensure_ascii=False).encode("utf-8")
    sha, size = g.place.write(pe.HANDOFF, data)
    g._row(Row("handoff", pe.JARVIS_TYPE, package_jobs.path_of(g.doc), pe.HANDOFF, sha, size,
               origin="jarvis", record=package_jobs.path_of(g.doc)))


# The gather step's own code, in its keys (step_cache.code).
GATHER_CODE = ("jarvis.package_gather", "jarvis.evidence_readers", "jarvis.package_evidence",
               "jarvis.session_transcript", "jarvis.evidence_types")
LOOKUPS, LABELS = "gather-lookups", "gather-labels"
# Retrieval's lookups, logged as its tool server answers them
# (research_log.py), in the run's folder.
LOOKUPS_LOG = "gather-lookups.jsonl"


def _read_now(rows: list[Row]) -> str:
    """What the program read, as a key names it: each row's type, stored
    path, SHA-256 and how it resolved (never a source path, which names
    the package's own folder)."""
    return step_cache.digest([[r.type, r.stored, r.sha256, r.resolved, r.origin] for r in rows])


def lookups_log(doc: dict) -> Path:
    return Path(doc["folder"]) / LOOKUPS_LOG


def lookups_unit(doc: dict, labels: list[tuple[int, dict]], read: str) -> step_cache.Unit:
    """What the labels' retrieval was asked with: the labels, the retrieval
    role (its prompt, tier and engine choice) and its code, and everything
    the program read before it (`read`), so a changed Work item, PR, CI
    run, transcript or record of his asks retrieval again. Kept under it:
    the lookups retrieval made (each tool and its arguments), which are
    made again before its answer is reused (`labels_unit`), as a build
    tool's discovered inputs are checked."""
    return step_cache.Unit(LOOKUPS, "", {
        "labels": [[n, e.get("text") or ""] for n, e in labels], "read": read,
        "tool": {"role": step_cache.role_tool("retrieval", doc),
                 "code": step_cache.code(*step_cache.MODEL_CODE, *GATHER_CODE,
                                         "jarvis.role_lookups", "jarvis.role_mcp")}})


def labels_unit(asked: step_cache.Unit, found: str) -> step_cache.Unit:
    """The labels' retrieval answer: what it was asked with (`asked`'s key)
    and what its lookups find (`found`, `found_digest`), so a PR, CI run,
    issue or record its lookups find changed asks retrieval again."""
    return step_cache.Unit(LABELS, "", {"asked": asked.key, "found": found})


def found_digest(calls: list[dict]) -> str:
    """What lookups found, as a key names it: each call (its tool and
    arguments) with whether it answered and its answer's SHA-256; a call
    made twice by its last answer."""
    by_call = {step_cache.canonical([c["tool"], c.get("arguments") or {}]):
               [bool(c.get("ok")), step_cache.digest(c.get("answer") or "")] for c in calls}
    return step_cache.digest(by_call)


def logged_lookups(doc: dict) -> list[dict]:
    """Retrieval's lookups as its tool server logged them."""
    from jarvis import research_log
    return [{"tool": e.get("tool"), "arguments": e.get("arguments") or {},
             "ok": e.get("ok"), "answer": e.get("answer") or ""}
            for e in research_log.read(lookups_log(doc)) if e.get("tool")]


def lookups_now(calls: list[dict]) -> list[dict]:
    """Each kept lookup made again now, as retrieval's tool server makes it
    (role_mcp.replay)."""
    from jarvis import role_mcp
    out = []
    for c in calls:
        ok, text = role_mcp.replay("retrieval", c["tool"], c.get("arguments") or {})
        out.append({"tool": c["tool"], "arguments": c.get("arguments") or {}, "ok": ok,
                    "answer": text})
    return out


def _retrieved(g: "Gathering", doc: dict, labels: list[tuple[int, dict]]) -> None:
    """The labels resolved: retrieval's kept answer reused when what it was
    asked with is kept and its lookups, made again now, find what they
    found; else retrieval asked, its lookups logged and kept with what
    they found."""
    asked = lookups_unit(doc, labels, _read_now(g.rows))
    had = step_cache.reuse(asked, doc)
    if had is not None:
        unit = labels_unit(asked, found_digest(lookups_now(had.value or [])))
        kept = step_cache.reuse(unit, doc)
        if kept is not None:
            retrieve_labels(g, labels, kept.value)
            return
    log = lookups_log(doc)
    log.unlink(missing_ok=True)
    asked_at = time.time()
    answered = retrieve_labels(g, labels)
    seconds = time.time() - asked_at
    calls = logged_lookups(doc)
    # Each replaces what was kept under its key: the lookups made now are
    # what the answer was made from.
    step_cache.computed(labels_unit(asked, found_digest(calls)), doc, asked_at, seconds,
                        value=answered, replace=True)
    step_cache.computed(asked, doc, asked_at, seconds,
                        value=[{"tool": c["tool"], "arguments": c["arguments"]} for c in calls],
                        replace=True)


def unit_of(doc: dict, rows: list[Row]) -> step_cache.Unit:
    """The gather step's unit: the hash of everything it read (`_read_now`
    over every row) and its code. Gather is always read again for a new
    package (its inputs are live); its key says what was read."""
    return step_cache.Unit(STEP, "", {"read": _read_now(rows),
                                      "code": step_cache.code(*GATHER_CODE)})


def gather(doc: dict) -> None:
    """The gather step of one run (package_jobs.PRODUCERS["gather"])."""
    place = pe.place_of(doc)
    held = pe.gathered(place)
    if held is not None:
        # Gathered for this package: every run of it reuses it (the design:
        # the evidence is gathered once per package).
        step_cache.note(doc, step_cache.Unit(STEP, "", {"held": held.get("gather_key")}),
                        step_cache.REUSED, 0.0)
        # Its vectors were waited for when it was gathered; what was written
        # since (the mark step's output, the sender's answers) waits only
        # until its words are read. Evidence that has lost its vectors since
        # (an engine rebuild, the model installed again) is waited for as a
        # fresh gather waits, never researched without its meaning.
        pe.wait_indexed(place, STEP, vectors=False)
        pe.wait_lost_vectors(place, STEP, held.get("gathered") or [])
        return
    if place.folder.exists():               # a run that stopped part way: gathered again
        shutil.rmtree(place.folder)
    began = time.time()
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
    gather_jarvis_material(g)
    if labels:
        _retrieved(g, doc, labels)
    for row in g.rows:
        row.knowledge_state = pe.knowledge_state(row)
    pe.write_manifest(place, g.rows)
    items = pe.wait_indexed(place, STEP)
    unit = unit_of(doc, g.rows)
    pe.link(place, items, g.rows, unit.key)
    # Read now, whatever was kept: noted as computed, never kept (a live
    # read is never reused across packages).
    step_cache.note(doc, unit, step_cache.COMPUTED, time.time() - began)
