"""A Work item's own research, kept current on every change of the item
(workflow.yaml research work_item_research; knowledge-graph.yaml, decided
2026-11-18): keep the lookup results and their summaries, and index every
UDS write.

On every change of a Work item (work_store.on_changed) a program runs the
system's queries for it, the same key searches a package's research runs
(key_searches), for the Work item, its title the key terms:

  its files        knowledge_search under its own folder (`/work/<id>`,
                   its packages' evidence among them), under its source
                   folder when that is a registered folder, and across the
                   whole record (projects, folders, earlier packages)
  its PRs and CI   each PR and CI run recorded against it (its pieces'
                   evidence), looked up as it is now
  Tim's words      conversation_history (his messages on it, and the
                   conversation's turns by its title) and recall (his memory)
  the brain's own  search_history, then read_history for each turn found
  past
  agents' threads  messages_to_him: the agents' messages on it

and writes what was looked up, each query with its hits, to
`data/work/<id>/research/` (the content root maps it to
`/work/<id>/research/`), with the summaries of it a SummarizationSession
writes (sessions.yaml, approved 2026-11-20), read into the engine at once.
Every package on the Work item reads it first (research
start_from_work_item).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from jarvis import data_paths, records, sessions

log = logging.getLogger("jarvis.work_research")

FOLDER = "research"
QUERIES = "queries.json"
QUERIES_MD = "queries.md"
SUMMARIES = "summaries.md"
HITS = 10

# search(text, under, kinds) -> knowledge_api.search's answer; given by
# the server (its knowledge context).
Search = Callable[[str, Optional[str], Optional[list]], dict]
# The SummarizationSession that writes the summaries of what was looked up,
# made for the research folder (sessions.BrainSummarizationSession); given
# by the server.
Summarizer = Callable[[Path], "sessions.SummarizationSession"]


def folder_of(item_id: str) -> Path:
    """The research folder on disk."""
    return data_paths.work_dir() / records.segment(item_id) / FOLDER


def _hits(answer: dict) -> list[dict]:
    return [{"path": h.get("key"), "source": h.get("source"), "score": h.get("score"),
             "why": h.get("why"), "text": h.get("text")} for h in answer.get("hits") or []]


def _search(search: Search, tool: str, text: str, under: Optional[str] = None,
            kinds: Optional[list] = None) -> dict:
    query = {"tool": tool, "query": text, "under": under, "kinds": kinds}
    try:
        answer = search(text, under, kinds)
    except Exception as e:  # noqa: BLE001 - what was looked up says it could not be
        return {**query, "hits": [], "error": f"{type(e).__name__}: {e}"}
    return {**query, "hits": _hits(answer), "partial": answer.get("partial", False)}


def engine_search(patience: float, uds=None) -> Search:
    """knowledge_search's search straight on the knowledge engine (records'
    own, else `uds`), words and meaning, read as Tim, each call waited for
    `patience` seconds: a package step's own patience, never the brain's
    short search deadline. Each hit as knowledge_search gives it: its path,
    source, score, which index found it, and the words where it matched
    (the section, else the record's field holding most of the words)."""
    from jarvis import knowledge_api
    from jarvis.knowledge.uds import Caller, Query, Stream

    def search(text: str, under: Optional[str], kinds: Optional[list]) -> dict:
        engine = (uds or records.uds()).with_deadline(patience)
        caller = Caller(knowledge_api.TIM, agent="jarvis")
        found = engine.search(caller, Query(meaning=text, words=text, under=under or "/",
                                            types=tuple(kinds) if kinds else None,
                                            limit=HITS))
        hits = []
        for h in found:
            item = engine.get(caller, h.path, {Stream.DATA, Stream.SECTIONS})
            if item is None:
                continue
            data = dict(item.data or {})
            sections = list(item.sections or ())
            at = next((s for s in sections if s.index in h.sections),
                      sections[0]) if sections else None
            words = (at.text[:knowledge_api.ITEM_TEXT_MAX] if at is not None
                     else knowledge_api._passage(data, text))
            hits.append({"key": h.path, "source": data.get("file") or h.path,
                         "score": h.score, "why": sorted(h.matched_by), "text": words})
        return {"hits": hits}
    return search


# The registered folder holding a path on this Mac (the folder catalog's
# folder_for_cwd), its id; given by the server with its catalog.
FolderFor = Callable[[str], Optional[str]]


def _source_folder(item, folder_for: Optional[FolderFor]) -> Optional[str]:
    """The item path of the registered folder its source is in, if any."""
    try:
        folder_id = folder_for(item.source_path) if folder_for else None
    except Exception:  # noqa: BLE001 - no folder catalog: no folder to search
        return None
    return f"/folders/{records.segment(folder_id)}" if folder_id else None


def _prs_and_runs(item_id: str) -> list[dict]:
    """Each PR and CI run recorded against the item, as GitHub says it is now."""
    from jarvis import evidence_readers, package_gather, role_lookups
    out = []
    for entry in package_gather.recorded_against(item_id):
        if entry["type"] == "pr":
            args = role_lookups.argv("github_pr_view", {"repo": entry["repo"],
                                                        "number": entry["number"]})
        else:
            run = {"repo": entry["repo"], "run_id": entry["id"]}
            args = evidence_readers.run_view_args(role_lookups._repo(run),
                                                  role_lookups._number(run, "run_id"))
        ok, said = evidence_readers._gh(args)
        out.append({"tool": "github", "query": entry, "hits": [json.loads(said)] if ok else [],
                    **({} if ok else {"error": said})})
    return out


def _history(text: str) -> list[dict]:
    """The brain's own past conversations: search_history, then read_history
    for each turn found (history_read, the brain's own two tools)."""
    from jarvis import history_read
    try:
        found = history_read.search(text)
    except OSError as e:
        return [{"tool": "search_history", "query": text, "hits": [], "error": str(e)}]
    out = [{"tool": "search_history", "query": text, "hits": [
        {"path": f"{source}:{line}", "text": said}
        for (source, line), said in zip(found.found, found.lines)]}]
    for source, line in dict.fromkeys(found.found):
        query = {"source": source, "around": line}
        try:
            _sid, resolved = history_read.resolve(source)
            window = history_read.read_window(resolved, line)
        except Exception as e:  # noqa: BLE001 - what was looked up says it could not be
            out.append({"tool": "read_history", "query": query, "hits": [],
                        "error": f"{type(e).__name__}: {e}"})
            continue
        out.append({"tool": "read_history", "query": query, "hits": [
            {"path": f"{source}:{window.first}-{window.last}", "text": window.text}]})
    return out


def _conversation(item) -> list[dict]:
    """The Work item's thread: his words on it (conversation_history) and
    the agents' messages on it (messages_to_him)."""
    from jarvis import conversation_log, package_gather
    thread = package_gather._all(lambda b: conversation_log.work_thread(item.id, b))

    def hits(items):
        return [{"path": f"message:{i.id}", "at": i.at,
                 **({"from": i.sender_name} if i.inbound else {}), "text": i.text}
                for i in items]
    return [{"tool": "conversation_history", "query": {"work": item.id},
             "hits": hits([i for i in thread if not i.inbound])},
            {"tool": "messages_to_him", "query": {"work": item.id},
             "hits": hits([i for i in thread if i.inbound])}]


def _recall(text: str) -> dict:
    from jarvis import jarvis_memory
    try:
        return {"tool": "recall", "query": text, "hits": jarvis_memory.search(text, limit=HITS)}
    except Exception as e:  # noqa: BLE001
        return {"tool": "recall", "query": text, "hits": [], "error": str(e)}


def key_searches(terms: str, search: Search, unders: list[Optional[str]]
                 ) -> list[Callable[[], list[dict]]]:
    """The key searches for some key terms (research key_searches), each a
    call giving its queries with their hits: knowledge_search (words and
    meaning) under each of `unders` (None: across the whole record), the
    conversation's turns, his memory (recall) and the brain's own past
    conversations (search_history, then read_history)."""
    calls: list[Callable[[], list[dict]]] = [
        (lambda under=under: [_search(search, "knowledge_search", terms, under)])
        for under in unders]
    calls.append(lambda: [_search(search, "knowledge_search", terms, None, ["turn"])])
    calls.append(lambda: [_recall(terms)])
    calls.append(lambda: _history(terms))
    return calls


def item_searches(item) -> list[Callable[[], list[dict]]]:
    """A Work item's own lookups: its PRs and CI runs as they are now, and
    its thread (his words on it, the agents' messages on it)."""
    return [lambda: _prs_and_runs(item.id), lambda: _conversation(item)]


def _timed(call: Callable[[], list[dict]]) -> list[dict]:
    """A call's queries, each with how long the call took (`seconds`, from
    its start to its answer; work-list.md item 38: each search timed)."""
    began = time.monotonic()
    found = call()
    seconds = round(time.monotonic() - began, 3)
    return [{**q, "seconds": seconds} for q in found]


def at_once(calls: list[Callable[[], list[dict]]]) -> list[dict]:
    """Every call run at the same time, each on a thread of its own and
    timed (`_timed`); their queries in the order the calls were given."""
    if not calls:
        return []
    with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="research") as pool:
        return [q for found in pool.map(_timed, calls) for q in found]


def queries(item, search: Search, folder_for: Optional[FolderFor] = None) -> list[dict]:
    """Every query run for the Work item, with its hits, all at once: the
    key searches (research key_searches), its title the key terms, under
    its own folder, its source's registered folder and the whole record;
    and its own lookups."""
    from jarvis import work_store
    source = _source_folder(item, folder_for)
    unders = [work_store.folder_of(item.id)] + ([source] if source else []) + [None]
    return at_once(key_searches(item.title, search, unders) + item_searches(item))


def _query_id(q: dict) -> str:
    """A query as a source's id: the tool and what it was given."""
    said = q["query"] if isinstance(q["query"], str) else json.dumps(
        q["query"], ensure_ascii=False, sort_keys=True, default=str)
    return f"{q['tool']}: {said}" + (f" under {q['under']}" if q.get("under") else "")


def _hit_path(hit: dict) -> str:
    return str(hit.get("path") or hit.get("key") or hit.get("url") or hit.get("slug")
               or hit.get("number") or hit.get("databaseId") or "")


def _hit_words(hit: dict) -> str:
    words = hit.get("text")
    return words if isinstance(words, str) else json.dumps(hit, ensure_ascii=False,
                                                           default=str)


def sources_of(looked: list[dict]) -> list[sessions.Source]:
    """What was looked up as the SummarizationSession's sources: one a query
    that found something, its text each hit's words with its path."""
    return [sessions.Source(_query_id(q), sessions.source_text(
                [(_hit_path(h), _hit_words(h)) for h in q["hits"]]))
            for q in looked if q.get("hits")]


def _summaries_md(item, summaries: list[sessions.Summary]) -> str:
    out = [f"# Summaries of what was looked up for Work item {item.id}: {item.title}", ""]
    for s in summaries:
        out += [f"## {s.source_id}", "", s.text, "",
                "Rests on: " + ", ".join(s.cites) if s.cites else "Rests on: nothing cited", ""]
    return "\n".join(out)


def markdown(heading: str, looked: list[dict]) -> str:
    """What was looked up, each query with its hits, as Markdown."""
    out = [f"# {heading}", "",
           f"Looked up: {time.strftime('%Y-%m-%d %H:%M', time.localtime())}", ""]
    for q in looked:
        out += [f"## {q['tool']}: {json.dumps(q['query'], ensure_ascii=False)}"
                + (f" under {q['under']}" if q.get("under") else ""), ""]
        if q.get("error"):
            out += [f"Could not be looked up: {q['error']}", ""]
        for h in q["hits"]:
            out += ["- " + json.dumps(h, ensure_ascii=False, default=str)]
        out.append("")
    return "\n".join(out)


def refresh(item_id: str, search: Search, folder_for: Optional[FolderFor] = None,
            uds=None, summarizer: Optional[Summarizer] = None) -> Optional[Path]:
    """Run the Work item's queries, write what was looked up and the
    SummarizationSession's summaries of it, and read them into the engine.
    The folder, or None for no such item. A summarization that cannot be
    made leaves what was looked up written and is logged; the next change
    tries again."""
    from jarvis import package_evidence, work_store
    item = work_store.get(item_id)
    if item is None:
        return None
    looked = queries(item, search, folder_for)
    folder = folder_of(item_id)
    folder.mkdir(parents=True, exist_ok=True)
    package_evidence.write_file(folder / QUERIES, json.dumps(
        looked, indent=2, ensure_ascii=False, default=str).encode("utf-8"))
    package_evidence.write_file(folder / QUERIES_MD, markdown(
        f"What was looked up for Work item {item.id}: {item.title}", looked).encode("utf-8"))
    if summarizer is not None:
        try:
            summaries = summarizer(folder).summarize(
                sources_of(looked), f"What was looked up for Work item {item.id}: {item.title}")
        except Exception:  # noqa: BLE001 - what was looked up stays written
            log.warning("work research: %s could not be summarised", item_id, exc_info=True)
        else:
            package_evidence.write_file(folder / SUMMARIES,
                                        _summaries_md(item, summaries).encode("utf-8"))
    package_evidence.index_folder(folder, uds)
    return folder


class Refresher:
    """Every changed Work item's research made again on a thread of its own,
    so a report's write never waits for it; an item changed again while it
    waits is made again once. The thread is the research's one worker: it
    keeps one SummarizationSession across the items it summarises (made
    from the summarizer for the Work items' folder at its first, item 38:
    one session reused as marking's workers keep theirs), closed when the
    research stops."""

    def __init__(self) -> None:
        self._waiting: set[str] = set()
        self._lock = threading.Condition()
        self._search: Optional[Search] = None
        self._folder_for: Optional[FolderFor] = None
        self._summarizer: Optional[Summarizer] = None
        self._summary = None                # the worker's held SummarizationSession
        self._thread: Optional[threading.Thread] = None

    def start(self, search: Search, folder_for: Optional[FolderFor] = None,
              summarizer: Optional[Summarizer] = None) -> None:
        with self._lock:
            self._search, self._folder_for = search, folder_for
            self._summarizer = summarizer
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="work-research",
                                                daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._search = None
            self._waiting.clear()
            self._lock.notify_all()

    def changed(self, ids: set) -> None:
        """work_store.on_changed's listener: never waits."""
        with self._lock:
            if self._search is None:
                return
            self._waiting |= {str(i) for i in ids}
            self._lock.notify_all()

    def _held(self, _folder: Path):
        """The worker's one SummarizationSession, made at its first use."""
        if self._summary is None and self._summarizer is not None:
            self._summary = self._summarizer(data_paths.work_dir())
        return self._summary

    def _close_summary(self) -> None:
        summary, self._summary = self._summary, None
        close = getattr(summary, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001 - closing never stops the worker
                log.warning("work research: the summarization session did not close",
                            exc_info=True)

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._waiting and not (self._search is None and self._summary):
                    self._lock.wait()
                search, folder_for = self._search, self._folder_for
                if search is None:
                    self._waiting.clear()
                    item_id = None
                else:
                    item_id = self._waiting.pop()
            if search is None:
                self._close_summary()
                continue
            try:
                refresh(item_id, search, folder_for,
                        summarizer=self._held if self._summarizer is not None else None)
            except Exception:  # noqa: BLE001 - the next change makes it again
                log.warning("work research: %s could not be looked up", item_id, exc_info=True)
                self._close_summary()


REFRESHER = Refresher()
