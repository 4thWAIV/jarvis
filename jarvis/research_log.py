"""research_log.py — every search a package run's roles make, logged by a
program, and which hits the briefing used (workflow.yaml research,
log_searches and record_hits_used, approved by Tim 2026-11-18).

One file a run, `research-log.jsonl` in the run's folder, one JSON line a
search, appended by the role's own tool server (role_mcp.py, `--log`) as it
answers: when, which role, the tool, what it was asked (its arguments), the
hits (the paths its answer names, read by program, `hit_paths`) and the
answer itself. Once the script passes the swarm, `mark_used` matches each
hit's path against the script, and records every hit whose path the script
cites as used, in `research-used.json` beside it.

Imported by the role's tool server (a child process): standard library only.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

NAME = "research-log.jsonl"
USED = "research-used.json"

# The fields of a JSON answer that name where a hit is (knowledge_search's
# `key`, a file's `path`, a record's `source` ...).
_PATH_FIELD = re.compile(r'"(key|path|item|source|file)"\s*:\s*"((?:[^"\\]|\\.)*)"')
# search_history's lines: `source:line: when: text`.
_HISTORY_LINE = re.compile(r"^(\S+?:\d+):", re.MULTILINE)


def hit_paths(tool: str, arguments: dict, text: str) -> list[str]:
    """The paths an answer names, each once, in its order: the path fields
    of its JSON, a history search's `source:line`, a history read's source."""
    found: list[str] = []
    if tool == "search_history":
        found = _HISTORY_LINE.findall(text or "")
    elif tool == "read_history":
        source = str((arguments or {}).get("source") or "").strip()
        found = [source] if source else []
    else:
        for _field, raw in _PATH_FIELD.findall(text or ""):
            try:
                value = json.loads(f'"{raw}"')
            except ValueError:
                continue
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
    return list(dict.fromkeys(found))


def record(log: Path, role: str, tool: str, arguments: dict, ok: bool, text: str) -> None:
    """One search, appended as one line. Never raises: a search the log
    cannot keep still answers."""
    line = {"at": time.time(), "role": role, "tool": tool, "arguments": arguments or {},
            "ok": bool(ok), "hits": hit_paths(tool, arguments, text), "answer": text}
    try:
        with open(log, "a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
            out.flush()
            os.fsync(out.fileno())
    except OSError:
        pass


def read(log: Path) -> list[dict]:
    """Every search logged, in order."""
    try:
        lines = Path(log).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def cited(path: str, text: str, roots: tuple[str, ...] = ()) -> bool:
    """The script cites this hit's path: the path itself is in it, or the
    path below one of `roots` (the package's evidence path, the run's
    folder) as the script names it from there."""
    if path and path in text:
        return True
    for root in roots:
        root = root.rstrip("/") + "/"
        if root != "/" and path.startswith(root) and path[len(root):] in text:
            return True
    return False


def mark_used(log: Path, script: str, roots: tuple[str, ...] = (),
              out: Optional[Path] = None) -> dict:
    """Which hits the briefing used (record_hits_used): each search, and
    the hits of it whose path the passed script cites; written beside the
    log. The record."""
    searches = read(log)
    used = []
    for n, entry in enumerate(searches, 1):
        hits = [h for h in entry.get("hits") or [] if cited(str(h), script, roots)]
        if hits:
            used.append({"search": n, "role": entry.get("role"), "tool": entry.get("tool"),
                         "arguments": entry.get("arguments"), "used": hits})
    result = {"searches": len(searches), "hits": sum(len(e.get("hits") or [])
                                                      for e in searches),
              "used": used, "at": time.time()}
    target = Path(out) if out else Path(log).with_name(USED)
    tmp = target.with_name(f".{target.name}.part")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)
    return result
