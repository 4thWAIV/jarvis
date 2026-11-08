"""Who sent a message a session dropped in `data/messages/<project>/`.

A file in a project folder carries no proof of who wrote it, and any process
can write any `From:` line, so the only proof is a transcript showing the
write: the file's full path in the writer's own record of its tool calls.
No session can write into another's transcript. In the order they are
checked (the `From:` line, when it names one, only says which to check
first):

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
`sender_of` gives None: the message is held, never shown (server.py).
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
# A transcript last written this long before the message file cannot have
# written it (the writer's record is written at or after the file).
WROTE_SLACK_SEC = 5.0


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


def wrote(transcript: Path, file_path: Path, written_at: float = 0.0) -> bool:
    """Whether this transcript's end shows its writer writing `file_path`
    (its full path in its own lines), and it was written since the file."""
    try:
        st = transcript.lstat()
    except OSError:
        return False
    if not transcript.is_file() or transcript.is_symlink() or \
            st.st_mtime < written_at - WROTE_SLACK_SEC:
        return False
    text = _tail(transcript, TRANSCRIPT_TAIL_BYTES)
    if not text:
        return False
    try:
        real = str(file_path.resolve())
    except OSError:
        real = str(file_path)
    return str(file_path) in text or real in text


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


def sender_of(file_path: Path, claimed: Optional[str], sessions: Iterable,
              roots: Iterable[Path], names: dict,
              codex_threads: Callable[[], dict] = dict) -> Optional[Sender]:
    """The proven sender of the message file `file_path`, or None.

    `sessions` are the watcher's live sessions (session_id, cwd, provider),
    `roots` the Claude config roots, `names` the agent directory's one name
    per listed session, `codex_threads` Codex's threads by id. Only listed
    sessions and their subagents are checked, the one the `From:` line names
    first."""
    roots = list(roots)
    try:
        written_at = file_path.lstat().st_mtime
    except OSError:
        written_at = 0.0
    listed = [s for s in sessions if s.session_id in names]
    listed.sort(key=lambda s: s.session_id != claimed)
    threads = None
    for s in listed:
        parent = names[s.session_id]
        if getattr(s, "provider", session_watch.PROVIDER) == CODEX:
            threads = codex_threads() if threads is None else threads
            for path, evidence, parent_id in _codex_candidates(s, threads):
                if evidence == CODEX_SUBAGENT_ROLLOUT:
                    spawned_by, nickname = codex_spawn(path)
                    if spawned_by != parent_id or not wrote(path, file_path, written_at):
                        continue
                    sub = nickname or "a subagent"
                    return Sender(s.session_id, f"{sub} ({parent})", evidence, sub, parent)
                if wrote(path, file_path, written_at):
                    return Sender(s.session_id, parent, evidence, "", parent)
            continue
        for path, evidence, _ in _claude_candidates(s, roots):
            if not wrote(path, file_path, written_at):
                continue
            if evidence == SUBAGENT_TRANSCRIPT:
                sub = session_watch.read_agent_meta(path)["description"] or "a subagent"
                return Sender(s.session_id, f"{sub} ({parent})", evidence, sub, parent)
            return Sender(s.session_id, parent, evidence, "", parent)
    return None
