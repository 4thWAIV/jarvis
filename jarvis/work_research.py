"""A Work item's own research, kept current on every change of the item
(workflow.yaml research work_item_research; knowledge-graph.yaml, decided
2026-11-18: "research would just be the thigns we loooked up and our
summaries"; "When it chagnes ALL UDS is write -> index always").

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
            args = role_lookups.argv("github_run_view", {"repo": entry["repo"],
                                                         "run_id": entry["id"]})
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


def queries(item, search: Search, folder_for: Optional[FolderFor] = None) -> list[dict]:
    """Every query run for the Work item, with its hits: the key searches
    (research key_searches), its title the key terms."""
    from jarvis import work_store
    terms = item.title
    looked = [_search(search, "knowledge_search", terms, work_store.folder_of(item.id))]
    source = _source_folder(item, folder_for)
    if source:
        looked.append(_search(search, "knowledge_search", terms, source))
    looked.append(_search(search, "knowledge_search", terms))
    looked += _prs_and_runs(item.id)
    looked += _conversation(item)
    looked.append(_search(search, "knowledge_search", terms, None, ["turn"]))
    looked.append(_recall(terms))
    looked += _history(terms)
    return looked


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


def _markdown(item, looked: list[dict]) -> str:
    out = [f"# What was looked up for Work item {item.id}: {item.title}", "",
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
    package_evidence.write_file(folder / QUERIES_MD, _markdown(item, looked).encode("utf-8"))
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
    waits is made again once."""

    def __init__(self) -> None:
        self._waiting: set[str] = set()
        self._lock = threading.Condition()
        self._search: Optional[Search] = None
        self._folder_for: Optional[FolderFor] = None
        self._summarizer: Optional[Summarizer] = None
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

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._waiting:
                    self._lock.wait()
                item_id = self._waiting.pop()
                search, folder_for = self._search, self._folder_for
                summarizer = self._summarizer
            if search is None:
                continue
            try:
                refresh(item_id, search, folder_for, summarizer=summarizer)
            except Exception:  # noqa: BLE001 - the next change makes it again
                log.warning("work research: %s could not be looked up", item_id, exc_info=True)


REFRESHER = Refresher()
