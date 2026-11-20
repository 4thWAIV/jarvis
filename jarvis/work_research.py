"""A Work item's own research, kept current on every change of the item
(workflow.yaml research work_item_research; knowledge-graph.yaml, decided
2026-11-18: "research would just be the thigns we loooked up and our
summaries"; "When it chagnes ALL UDS is write -> index always").

On every change of a Work item (work_store.on_changed) a program runs the
system's queries for it, the same key searches a package's research runs,
for the Work item:

  its files        knowledge_search of its title under its own folder
                   (`/work/<id>`, its packages' evidence among them) and
                   under its source folder when that is a registered folder
  its PRs and CI   each PR and CI run recorded against it (its pieces'
                   evidence), looked up as it is now
  Tim's words      his messages on it (the Work item's conversation), his
                   memory (recall) and the conversation's turns by its title

and writes what was looked up, each query with its hits, to
`data/work/<id>/research/` (the content root maps it to
`/work/<id>/research/`), read into the engine at once. Every package on the
Work item reads it first (research start_from_work_item).

The summaries are a SummarizationSession's (sessions.yaml, approved
2026-11-20): `SUMMARIZE` is where it is given. It has no approved prompt in
role-prompts.md yet, so none is given and no summaries are written until
it has one.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from jarvis import data_paths, records

log = logging.getLogger("jarvis.work_research")

FOLDER = "research"
QUERIES = "queries.json"
QUERIES_MD = "queries.md"
SUMMARIES = "summaries.md"
HITS = 10

# search(text, under, kinds) -> knowledge_api.search's answer; given by
# the server (its knowledge context).
Search = Callable[[str, Optional[str], Optional[list]], dict]
# The summaries of what was looked up, from a SummarizationSession; None
# until its prompt is approved.
SUMMARIZE: Optional[Callable[[list[dict], str], list[dict]]] = None


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


def _tims_words(item) -> list[dict]:
    from jarvis import conversation_log, jarvis_memory, package_gather
    said = [i for i in package_gather._all(lambda b: conversation_log.work_thread(item.id, b))
            if not i.inbound]
    out = [{"tool": "messages", "query": {"work": item.id},
            "hits": [{"path": f"message:{i.id}", "at": i.at, "text": i.text} for i in said]}]
    try:
        memory = jarvis_memory.search(item.title, limit=HITS)
    except Exception as e:  # noqa: BLE001
        out.append({"tool": "recall", "query": item.title, "hits": [], "error": str(e)})
    else:
        out.append({"tool": "recall", "query": item.title, "hits": memory})
    return out


def queries(item, search: Search, folder_for: Optional[FolderFor] = None) -> list[dict]:
    """Every query run for the Work item, with its hits."""
    from jarvis import work_store
    own = work_store.folder_of(item.id)
    looked = [_search(search, "knowledge_search", item.title, own)]
    source = _source_folder(item, folder_for)
    if source:
        looked.append(_search(search, "knowledge_search", item.title, source))
    looked += _prs_and_runs(item.id)
    looked += _tims_words(item)
    looked.append(_search(search, "knowledge_search", item.title, None, ["turn"]))
    return looked


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
            uds=None) -> Optional[Path]:
    """Run the Work item's queries, write what was looked up (and the
    summaries, once a SummarizationSession is given) and read them into the
    engine. The folder, or None for no such item."""
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
    if SUMMARIZE is not None:
        summaries = SUMMARIZE(looked, item.title)
        package_evidence.write_file(folder / SUMMARIES, "\n\n".join(
            s["text"] for s in summaries).encode("utf-8"))
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
        self._thread: Optional[threading.Thread] = None

    def start(self, search: Search, folder_for: Optional[FolderFor] = None) -> None:
        with self._lock:
            self._search, self._folder_for = search, folder_for
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
            if search is None:
                continue
            try:
                refresh(item_id, search, folder_for)
            except Exception:  # noqa: BLE001 - the next change makes it again
                log.warning("work research: %s could not be looked up", item_id, exc_info=True)


REFRESHER = Refresher()
