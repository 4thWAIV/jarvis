"""Which session did an act its own transcript shows: ran make_package for
a package (`ran`), or called a command naming itself (`called`, an agent
key's submission). A signature proves the listed agent; only this proves
which of its subagents acted.

A request or a file carries no proof of which session or subagent made it,
so the only proof is a transcript showing the act in the actor's own record
of its tool calls. No session can write into another's transcript. In the
order they are checked (the claimed session, when there is one, only says
which to check first):

- `transcript`: a Claude Code session's own transcript;
- `subagent_transcript`: a Claude Code subagent's own file
  (`subagents/agent-<id>.jsonl`), named by its meta description;
- `codex_rollout`: a Codex thread's rollout;
- `codex_subagent_rollout`: a Codex subagent's rollout, whose
  `session_meta.source.subagent.thread_spawn.parent_thread_id` names its
  parent thread and `agent_nickname` its name.

The sender must be, or be a subagent of, a listed agent (the agent
directory): a subagent shows as "<its name> (<listed parent>)" and is
replied to through its parent's session. No proof, or no listed agent, and
`proven_by` gives None (server.py then names the signing agent alone).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from jarvis import session_watch

TRANSCRIPT, SUBAGENT_TRANSCRIPT = "transcript", "subagent_transcript"
CODEX_ROLLOUT, CODEX_SUBAGENT_ROLLOUT = "codex_rollout", "codex_subagent_rollout"
CODEX = "openai"
# How much of a transcript's end is searched for the file's path.
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
@dataclass(frozen=True)
class Sender:
    session_id: str           # the listed agent's session: where a reply goes
    name: str                 # "Planning", or "Feynman (Planning)" for a subagent
    evidence: str             # which transcript proved it
    subagent: str = ""        # the subagent's own name, when one wrote it
    parent: str = ""          # the listed agent's name


def _tail(path: Path, nbytes: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _first_object(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            obj = json.loads(fh.readline())
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def codex_spawn(rollout: Path) -> tuple[str, str]:
    """(parent thread id, nickname) a Codex subagent's rollout names in its
    session_meta, or ("", "") for a thread no session spawned."""
    payload = _first_object(rollout).get("payload") or {}
    spawn = (((payload.get("source") or {}).get("subagent") or {}).get("thread_spawn") or {}) \
        if isinstance(payload, dict) else {}
    if not isinstance(spawn, dict):
        return "", ""
    nickname = spawn.get("agent_nickname") or (payload.get("agent_nickname")
                                               if isinstance(payload, dict) else "")
    return str(spawn.get("parent_thread_id") or ""), " ".join(str(nickname or "").split())


def _claude_candidates(s, roots: Iterable[Path]):
    """(transcript, evidence, subagent name) of a Claude session: its own
    transcript, then each of its subagents' files."""
    for root in roots:
        yield session_watch.transcript_path(root, s.cwd, s.session_id), TRANSCRIPT, ""
    for f, _run in session_watch._subagent_files(list(roots), s.cwd, s.session_id):
        yield f, SUBAGENT_TRANSCRIPT, None       # its name read only if it wrote


def _codex_candidates(s, threads: dict):
    """(rollout, evidence, subagent name) of a Codex thread: its own
    rollout, then each subagent thread it spawned."""
    own = threads.get(s.session_id) or {}
    if own.get("rollout_path"):
        yield Path(own["rollout_path"]), CODEX_ROLLOUT, ""
    for t in threads.values():
        if t.get("thread_source") == "subagent" and t.get("rollout_path"):
            yield Path(t["rollout_path"]), CODEX_SUBAGENT_ROLLOUT, s.session_id


def ran(transcript: Path, words: Iterable[str], since: float, whole: bool = False) -> bool:
    """Whether one line of this transcript's end (with `whole`, of all of
    it, read a line at a time: a one-time look back past the end) holds
    every one of `words` (a command and what it was run on), and it was
    written since `since`."""
    words = [w for w in words if w]
    try:
        st = transcript.lstat()
    except OSError:
        return False
    if not words or not transcript.is_file() or transcript.is_symlink() or st.st_mtime < since:
        return False
    if not whole:
        return any(all(w in line for w in words)
                   for line in _tail(transcript, TRANSCRIPT_TAIL_BYTES).splitlines())
    first = words[0].encode("utf-8")
    try:
        with open(transcript, "rb") as fh:
            for raw in fh:
                if first in raw:
                    line = raw.decode("utf-8", errors="replace")
                    if all(w in line for w in words):
                        return True
    except OSError:
        return False
    return False


def _tool_inputs(obj: dict):
    """The inputs of the tool calls one transcript line is the assistant
    making: a Claude Code `tool_use` block's input, or a Codex
    `function_call` / `custom_tool_call` / `local_shell_call`'s arguments.
    Nothing else: a user line, a tool's result or a message read is not a
    call this session made."""
    if obj.get("type") == "assistant":
        content = (obj.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block.get("input")
    elif obj.get("type") == "response_item":
        payload = obj.get("payload") or {}
        if isinstance(payload, dict) and payload.get("type") in (
                "function_call", "custom_tool_call", "local_shell_call"):
            raw = payload.get("arguments", payload.get("input", payload.get("action")))
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    pass
            yield raw


def called(transcript: Path, words: Iterable[str], since: float) -> bool:
    """Whether this transcript's end holds a tool call the assistant made
    (never a user line, a tool result or text it read) whose input holds
    every one of `words`, and the file was written since `since`. Stricter
    than `ran`: text another agent gets into this transcript is not a call
    this session made."""
    words = [w for w in words if w]
    try:
        st = transcript.lstat()
    except OSError:
        return False
    if not words or not transcript.is_file() or transcript.is_symlink() or st.st_mtime < since:
        return False
    for line in _tail(transcript, TRANSCRIPT_TAIL_BYTES).splitlines():
        if words[0] not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        for given in _tool_inputs(obj):
            text = given if isinstance(given, str) else json.dumps(given, ensure_ascii=False)
            if all(w in text for w in words):
                return True
    return False


# The most transcripts one proof reads, newest first: a listed agent with
# hundreds of subagent files is looked through only where it wrote lately.
PROOF_FILES_MAX = 64
# What each transcript showed, by what was asked and the file as it was
# (its mtime and size): a file is read again only once it has changed.
_SHOWN_MAX = 4096
_shown: dict[tuple, bool] = {}


def _showed(key, path: Path, st, shows: Callable[[Path], bool]) -> bool:
    k = (key, str(path), st.st_mtime_ns, st.st_size)
    if k not in _shown:
        if len(_shown) >= _SHOWN_MAX:
            _shown.clear()
        _shown[k] = bool(shows(path))
    return _shown[k]


def proven_by(shows: Callable[[Path], bool], claimed: Optional[str], sessions: Iterable,
              roots: Iterable[Path], names: dict, codex_threads: Callable[[], dict] = dict,
              only_claimed: bool = False, since: float = 0.0, key=None,
              own_only: bool = False, subagents_only: bool = False) -> Optional[Sender]:
    """The listed agent (or its subagent) whose own transcript `shows`
    proves the act, or None. With `only_claimed`, the claimed session alone
    is looked in: a claim is proven or refused, never moved to another.

    Bounded: only transcripts written at or after `since` (the act cannot be
    in an older one) are read, the claimed session's first and then the
    newest, at most PROOF_FILES_MAX; with a `key` naming what is asked, each
    file's answer is kept until the file changes. With `own_only`, a
    subagent's file never proves it: only the session's own transcript.
    With `subagents_only`, only a subagent's own file does: which of a
    session's subagents did it, once the session itself is proven another
    way (its key's signature)."""
    roots = list(roots)
    listed = [s for s in sessions if s.session_id in names]
    if only_claimed:
        listed = [s for s in listed if s.session_id == claimed]
    threads = None
    found = []                          # (claimed first, -mtime, path, st, evidence, s, extra)
    for s in listed:
        if getattr(s, "provider", session_watch.PROVIDER) == CODEX:
            threads = codex_threads() if threads is None else threads
            candidates = _codex_candidates(s, threads)
        else:
            candidates = _claude_candidates(s, roots)
        for path, evidence, extra in candidates:
            if own_only and evidence not in (TRANSCRIPT, CODEX_ROLLOUT):
                continue
            if subagents_only and evidence in (TRANSCRIPT, CODEX_ROLLOUT):
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            found.append((s.session_id != claimed, -st.st_mtime, str(path), path, st,
                          evidence, s, extra))
    found.sort(key=lambda f: f[:3])
    for _first, _age, _name, path, st, evidence, s, extra in found[:PROOF_FILES_MAX]:
        parent = names[s.session_id]
        if evidence == CODEX_SUBAGENT_ROLLOUT:
            spawned_by, nickname = codex_spawn(path)
            if spawned_by != extra:
                continue
        if not (_showed(key, path, st, shows) if key is not None else shows(path)):
            continue
        if evidence == CODEX_SUBAGENT_ROLLOUT:
            sub = nickname or "a subagent"
        elif evidence == SUBAGENT_TRANSCRIPT:
            sub = session_watch.read_agent_meta(path)["description"] or "a subagent"
        else:
            return Sender(s.session_id, parent, evidence, "", parent)
        return Sender(s.session_id, f"{sub} ({parent})", evidence, sub, parent)
    return None
