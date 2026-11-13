"""
codex_sessions.py — OpenAI's Codex as a session provider (session_provider.py).

The Codex sessions the user runs in his terminals: found from the process
list, matched to their threads in Codex's own state database, described from
the end of each thread's transcript, and sent messages through Codex's own
queue (`codex queue`), which a running session picks up.

Everything here only reads Codex's files, except `send`, which asks Codex to
queue a message. It reads the user's Codex home (`~/.codex`, or
`JARVIS_CODEX_SESSIONS_HOME`), never JARVIS's own brain home
(`data_paths.codex_home()`): the OpenAI brain is not one of his sessions.

Measured against Codex CLI 0.154 and the 0.163 daemon; see
.agents/in-progress/session-providers-design.md for what each part rests on
and Codex's limits (no record of a pending approval, so no "needs you" for
one, and no keystroke answers).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from jarvis import session_steer, worker_activity
from jarvis.codex_runtime import RuntimeObserver
from jarvis.session_provider import Tracking
from jarvis.session_watch import (Activity, session_status, SessionState, Subagent,
                           _looks_like_a_question, objects_since, project_name,
                           tail_windows, TranscriptEntry, TRANSCRIPT_PAGE, lines_before,
                           tool_gist, _first_lines, _long)

log = logging.getLogger("jarvis.sessions")

NAME = "openai"
TAIL_BYTES = 256 * 1024
MAX_TEXT = 4000
RECENT_TOOLS = 5
# A new session's thread is created after its process starts; allow for the
# clocks and the first write.
NEW_THREAD_GRACE_SEC = 60.0
QUEUE_TIMEOUT_SEC = 30.0

# Subcommands that run the terminal UI: a session the user is in. Anything
# else (app-server, exec, mcp, queue, ...) is Codex doing something else.
_UI_SUBCOMMANDS = {"resume", "fork"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Codex puts its own context into the conversation as user messages.
_INJECTED = re.compile(r"^\s*(<[a-z_]+>|# [A-Za-z.]+ instructions for )")


def codex_home() -> Path:
    raw = os.getenv("JARVIS_CODEX_SESSIONS_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


@dataclass
class CodexProcess:
    pid: int
    started: float                  # epoch seconds
    args: list[str]
    cwd: str

    @property
    def thread_hint(self) -> Optional[str]:
        """The thread a `codex resume <id>` names, if it names one."""
        for a in self.args[1:]:
            if _UUID.fullmatch(a):
                return a
        return None


def _etime_seconds(text: str) -> Optional[float]:
    """ps's elapsed time, `[[dd-]hh:]mm:ss`, in seconds."""
    days, _, rest = text.strip().rpartition("-")
    parts = [int(p) for p in rest.split(":") if p.isdigit()]
    if not parts:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3:]
    return (int(days) if days.isdigit() else 0) * 86400 + h * 3600 + m * 60 + sec


def _cwd_of(pid: int) -> Optional[str]:
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((line[1:] for line in out.splitlines() if line.startswith("n")), None)


# Each Codex process's folder, asked of lsof once: by pid, with the start
# time and arguments it was seen with, so a pid used again by another
# process is asked again. `ps`'s elapsed time is whole seconds, so a start
# within `_SAME_START_SEC` is the same start.
_cwds: dict[int, tuple[float, tuple, str]] = {}
_SAME_START_SEC = 2.0


def _cached_cwd(pid: int, started: float, args: list[str]) -> Optional[str]:
    held = _cwds.get(pid)
    if held is not None and held[1] == tuple(args) and abs(held[0] - started) <= _SAME_START_SEC:
        return held[2]
    cwd = _cwd_of(pid)
    if cwd:
        _cwds[pid] = (started, tuple(args), cwd)
    return cwd


def list_processes() -> list[CodexProcess]:
    """The Codex terminal sessions running now."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,etime=,args="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    now = time.time()
    found = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        args = parts[2].split()
        # The native binary, not the node wrapper that starts it.
        if Path(args[0]).name != "codex":
            continue
        sub = next((a for a in args[1:] if not a.startswith("-")), None)
        if sub is not None and sub not in _UI_SUBCOMMANDS and not _UUID.fullmatch(sub):
            continue
        elapsed = _etime_seconds(parts[1])
        if elapsed is None:
            continue
        cwd = _cached_cwd(int(parts[0]), now - elapsed, args)
        if not cwd:
            continue
        found.append(CodexProcess(pid=int(parts[0]), started=now - elapsed, args=args, cwd=cwd))
    for gone in set(_cwds) - {p.pid for p in found}:
        _cwds.pop(gone, None)
    return found


def _threads(home: Path) -> dict[str, dict]:
    """Codex's threads, by id, read-only."""
    path = home / "state_5.sqlite"
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        con.row_factory = sqlite3.Row
        columns = {r[1] for r in con.execute("pragma table_info(threads)")}
        # Codex 0.166 records which threads are subagents a session spawned.
        source = "thread_source" if "thread_source" in columns else "null"
        rows = con.execute(
            "select id, rollout_path, cwd, title, git_branch, created_at_ms, updated_at_ms, "
            f"{source} as thread_source from threads where archived = 0").fetchall()
    finally:
        con.close()
    return {r["id"]: dict(r) for r in rows}


# Read by the folder catalog's discovery too (folder_discovery.py): each
# thread's cwd and rollout file are the structured record of where it ran.
threads = _threads


def _thread_for(proc: CodexProcess, threads: dict[str, dict]) -> Optional[dict]:
    hint = proc.thread_hint
    if hint and hint in threads:
        return threads[hint]
    # A subagent's thread shares its parent's folder and is newer than it, but
    # no terminal runs it: a session is never one of its own subagents.
    here = [t for t in threads.values()
            if t["cwd"] == proc.cwd and t.get("thread_source") != "subagent"]
    if not here:
        return None
    # A new session: the first thread made in its folder after it started.
    newer = [t for t in here if (t["created_at_ms"] or 0) / 1000 >= proc.started - NEW_THREAD_GRACE_SEC]
    if newer and hint is None and "--last" not in proc.args:
        return min(newer, key=lambda t: t["created_at_ms"] or 0)
    # `codex resume --last`, or a picker choice: the folder's latest.
    return max(here, key=lambda t: t["updated_at_ms"] or 0)


# Each transcript's tail as it was last read, by path, with the size and
# change time it was read at: a session that wrote nothing since is not read
# and parsed again on every poll.
_TAILS: dict[str, tuple[tuple[int, int, int], list[dict]]] = {}
_TAILS_MAX = 4096


def _tail(path: str) -> list[dict]:
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (st.st_ino, st.st_size, st.st_mtime_ns)
    held = _TAILS.get(path)
    if held is not None and held[0] == key:
        return held[1]
    out = _read_tail(path)
    if len(_TAILS) >= _TAILS_MAX:
        _TAILS.clear()
    _TAILS[path] = (key, out)
    return out


def _read_tail(path: str) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return []
    lines = raw.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]                     # the first is cut
    out = []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _message(obj: dict) -> Optional[tuple[str, str]]:
    """(role, text) of a transcript line that is something said, else None."""
    if obj.get("type") != "response_item":
        return None
    p = obj.get("payload") or {}
    if p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
        return None
    text = "\n".join(c["text"] for c in p.get("content") or []
                     if isinstance(c, dict) and isinstance(c.get("text"), str)).strip()
    if not text or (p["role"] == "user" and _INJECTED.match(text)):
        return None
    return p["role"], text


def _codex_entries(offset: int, obj: dict, calls: Optional[dict] = None) -> list[TranscriptEntry]:
    """A Codex rollout line as transcript entries: what was said, each tool
    call and what it returned, the result named by its call's tool (`calls`,
    call id to tool name, filled in as calls are read). Its own instructions
    and reasoning are left out."""
    calls = {} if calls is None else calls
    if obj.get("type") != "response_item":
        return []
    p = obj.get("payload") or {}
    at = _when(obj)
    said = _message(obj)
    if said:
        role, text = said
        if role == "user":
            return [TranscriptEntry(offset, at, "jarvis" if session_steer.STEER_SIGNATURE.strip()[:20]
                                    in text else "you", _long(text))]
        return [TranscriptEntry(offset, at, "agent", _long(text))]
    kind = p.get("type")
    if kind in ("function_call", "custom_tool_call"):
        name = str(p.get("name") or "tool")[:80]
        if p.get("call_id"):
            calls[p["call_id"]] = name
        return [TranscriptEntry(offset, at, "tool", tool_gist(p.get("arguments", p.get("input"))),
                                tool=name)]
    if kind in ("function_call_output", "custom_tool_call_output"):
        output = p.get("output")
        if isinstance(output, list):
            output = "\n".join(c.get("text", "") for c in output if isinstance(c, dict))
        elif isinstance(output, dict):
            output = output.get("content") or ""
        return [TranscriptEntry(offset, at, "result", _first_lines(output or ""),
                                tool=calls.get(p.get("call_id")))]
    return []


def _when(obj: dict) -> Optional[float]:
    stamp = obj.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Recap:
    """What the end of a rollout says, raw: whether anything was ever said,
    its turn as last marked, and what was said. The state is
    session_watch.session_status's to decide."""
    prompted: bool
    turn: Optional[str]               # "open" after task_started, "closed" after task_complete
    turn_marked_at: Optional[float]
    last_prompt: Optional[str]
    last_text: Optional[str]
    recent_tools: list[str]


def recap(objs: list[dict]) -> Recap:
    """Reduce rollout lines to a recap."""
    turn = marked = None
    prompted = False
    last_prompt = last_text = None
    tools: list[str] = []
    for obj in objs:
        p = obj.get("payload") or {}
        if obj.get("type") == "event_msg":
            kind = p.get("type")
            if kind == "task_started":
                turn, marked = "open", _when(obj)
            elif kind in ("task_complete", "turn_aborted"):
                turn, marked = "closed", _when(obj)
        said = _message(obj)
        if said:
            prompted = True
            if said[0] == "user":
                last_prompt = said[1][:MAX_TEXT]
            else:
                last_text = said[1][:MAX_TEXT]
        if obj.get("type") == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
            tools.append(str(p.get("name") or ""))
    return Recap(prompted or turn is not None, turn, marked, last_prompt, last_text,
                 [t for t in tools if t][-RECENT_TOOLS:])


def activity(r: Recap) -> Activity:
    """Codex's raw facts about a running session. It has no roster: its
    rollout's task markers are the turn, and nothing runs in its background."""
    return Activity(alive=True, prompted=r.prompted, turn=r.turn, turn_marked_at=r.turn_marked_at,
                    prompt_waiting=None, asked=_looks_like_a_question(r.last_text),
                    background_agents=0, background_commands=False, roster_status=None)


def _turn_end(objs: list[dict], i: int, clock):
    """Follow a turn from objs[i] to task_complete or turn_aborted: (the
    index after it, when it ended or None, its final answer's blocks, the
    latest time seen)."""
    parts: list[str] = []
    for j in range(i, len(objs)):
        obj = objs[j]
        ts = _when(obj)
        if ts is not None:
            clock = ts if clock is None else max(clock, ts)
        p = obj.get("payload") or {}
        if obj.get("type") == "event_msg" and p.get("type") in ("task_complete", "turn_aborted"):
            return j + 1, clock, parts, clock
        if obj.get("type") == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
            parts = []                 # what came before a tool call was not the answer
            continue
        said = _message(obj)
        if said and said[0] == "assistant":
            parts.append(said[1])
    return len(objs), None, parts, clock


def track_from(objs: list[dict], body: str, since: float,
               now: Optional[float] = None) -> Tracking:
    """Where the message whose words are `body`, staged at `since`, stands
    in these Codex transcript lines.

    Measured on Codex CLI 0.163: a message sent through `codex queue` is
    taken as a turn of its own, never into one under way (`task_started`
    with `turn_trigger: "queue"`), and the user `response_item` carrying it
    is written as that turn begins — so it is delivered and started at once.
    The turn ends at `task_complete` (or `turn_aborted`); its answer is what
    it said after its last tool call. A turn that ends without a word takes
    its answer from the next turn, as session_watch.LATE_ANSWER_SEC says."""
    from jarvis.session_watch import LATE_ANSWER_SEC
    if not body:
        return Tracking()
    now = time.time() if now is None else now
    clock = None
    delivered = None
    i = 0
    while i < len(objs) and delivered is None:
        obj = objs[i]
        i += 1
        ts = _when(obj)
        if ts is not None:
            clock = ts if clock is None else max(clock, ts)
        said = _message(obj)
        if (said and said[0] == "user" and body in said[1]
                and (ts is None or ts >= since)):
            delivered = clock if clock is not None else since
    if delivered is None:
        return Tracking()
    i, answered, parts, clock = _turn_end(objs, i, clock)
    if answered is not None and not parts:
        deadline, late = answered + LATE_ANSWER_SEC, None
        for j in range(i, len(objs)):
            obj, p = objs[j], objs[j].get("payload") or {}
            if not (obj.get("type") == "event_msg" and p.get("type") == "task_started"):
                continue
            ts = _when(obj)
            if p.get("turn_trigger") == "queue" or (ts is not None and ts > deadline):
                late = (answered, [])
                break
            _, ended, said, _ = _turn_end(objs, j + 1, ts if ts is not None else clock)
            late = None if ended is None else (ended, said) if said else (answered, [])
            if late is None:
                return Tracking(delivered_at=delivered, started_at=delivered)
            break
        else:
            if now < deadline:
                return Tracking(delivered_at=delivered, started_at=delivered)
            late = (answered, [])
        answered, parts = late
    reply = ("\n\n".join(parts) or None) if answered is not None else None
    return Tracking(delivered_at=delivered, started_at=delivered,
                    answered_at=answered, reply=reply)


class CodexSessions:
    """Codex as a session provider."""

    name = NAME
    can_answer_dialogs = False
    can_interrupt = False             # `codex queue` only queues

    def __init__(self, home: Optional[Path] = None,
                 processes: Callable[[], list[CodexProcess]] = list_processes,
                 codex: Optional[str] = None):
        self._home = home
        self._processes = processes
        self._codex = codex
        self.runtime = RuntimeObserver(self.home)

    @property
    def home(self) -> Path:
        return self._home or codex_home()

    def close(self):
        self.runtime.close()

    def discover(self, now: float) -> list[SessionState]:
        if self._processes is list_processes:
            self.runtime.start()
        runtime_rows, runtime_at, runtime_error = self.runtime.snapshot()
        runtime_fresh = runtime_at > 0 and now - runtime_at < 10
        procs = self._processes()
        if not procs and not runtime_fresh:
            return []
        threads = _threads(self.home)
        grouped: dict[str, list[CodexProcess]] = {}
        for proc in procs:
            thread = _thread_for(proc, threads)
            if thread:
                grouped.setdefault(thread["id"], []).append(proc)
        if runtime_fresh:
            for tid in runtime_rows:
                if tid in threads and threads[tid].get("thread_source") != "subagent":
                    grouped.setdefault(tid, [])
        sessions = []
        for tid, group in grouped.items():
            thread = threads[tid]
            first = min(group, key=lambda p: p.started, default=None)
            objects = _tail(thread["rollout_path"]) if thread.get("rollout_path") else []
            r = recap(objects)
            status = session_status(activity(r))
            sessions.append(SessionState(
                session_id=tid,
                worker_activity=worker_activity.observation(objects, NAME),
                cwd=thread["cwd"],
                project=project_name(thread["cwd"]),
                state=status.state,
                needs=status.needs,
                between_turns=status.between_turns,
                pids=[p.pid for p in group],
                primary_pid=first.pid if first else None,
                roster_name="",
                title=(thread.get("title") or "").split("\n")[0][:120] or None,
                last_prompt=r.last_prompt,
                last_text=r.last_text,
                recent_tools=r.recent_tools,
                started=first.started if first else (thread.get("created_at_ms") or 0) / 1000,
                since=status.since or (thread.get("updated_at_ms") or 0) / 1000 or (first.started if first else None),
                origin="terminal" if first else "daemon",
                provider=NAME,
                # Codex's queue always takes a message; the session picks it
                # up once it has loaded its conversation.
                steerable=True,
            ))
        children = child_sessions(threads, sessions, include=runtime_rows if runtime_fresh else ())
        if runtime_fresh:
            parents = {child.session_id: child.parent_session_id for child in children}
            def belongs_to_runtime(child):
                root, seen = child.session_id, set()
                while root in parents and root not in seen:
                    seen.add(root)
                    root = parents[root]
                return root in runtime_rows
            # Loaded membership is authoritative only for this daemon's
            # families. A standalone CLI's children remain transcript-covered.
            children = [child for child in children
                        if child.session_id in runtime_rows or not belongs_to_runtime(child)]
        for session in sessions + children:
            runtime = runtime_rows.get(session.session_id) if runtime_fresh else None
            session.runtime_status = {"status": runtime.get("status"), "observed_at": runtime_at} if runtime else {"status": "unknown", "error": runtime_error}
            if runtime and runtime.get("activity"):
                session.worker_activity = worker_activity.merge_evidence(session.worker_activity, runtime["activity"])
                session.state = "working" if runtime.get("status") == "active" else "idle"
        parent_by_id = {child.session_id: child.parent_session_id for child in children}
        roots = {session.session_id: session for session in sessions}
        orphans = []
        for child in children:
            parent = child.parent_session_id
            visited = set()
            while parent in parent_by_id and parent not in visited:
                visited.add(parent)
                parent = parent_by_id[parent]
            if parent not in roots:
                orphans.append(child)  # A loaded child outlives an unloaded parent.
                continue
            roots[parent].subagents.append(Subagent(
                agent_id=child.session_id, description=child.given_name or child.title or child.session_id,
                agent_type="codex-subagent", background=True, depth=len(visited) + 1,
                state="finished" if child.worker_activity.get("state") == "idle" else "stale",
                # When it last did something: a finished child's own last turn,
                # never when the runtime was last asked about it, which moves on
                # every poll and kept finished subagents popping back up.
                started=child.started,
                written=(child.worker_activity.get("observed_at") or 0
                         if child.worker_activity.get("state") != "idle"
                         else child.since or child.started or 0),
                parent_agent_id=child.parent_session_id or "", worker_activity=child.worker_activity,
                runtime_status=child.runtime_status))
        sessions.extend(orphans)
        for session in sessions:
            session.agents_seen = len(session.subagents)
            # Only stream evidence can contribute to the live inference count.
            session.agents_active = sum(worker_activity.current(a.worker_activity, now)["generating"]
                                        for a in session.subagents)
        return sessions

    def last_reply(self, session: SessionState) -> Optional[str]:
        thread = _threads(self.home).get(session.session_id)
        if not thread or not thread.get("rollout_path"):
            return None
        # Its latest message, looking further back only when the turns in
        # view said nothing (session_watch.tail_windows).
        for objs, _whole in tail_windows(Path(thread["rollout_path"])):
            for obj in reversed(objs):
                said = _message(obj)
                if said and said[0] == "assistant":
                    return said[1]
        return None

    def transcript(self, session: SessionState, before: Optional[int] = None,
                   limit: int = TRANSCRIPT_PAGE) -> list[TranscriptEntry]:
        thread = _threads(self.home).get(session.session_id)
        if not thread or not thread.get("rollout_path"):
            return []
        calls: dict = {}
        return lines_before(Path(thread["rollout_path"]), before, limit,
                            lambda offset, obj: _codex_entries(offset, obj, calls))

    def track(self, session: SessionState, message) -> Tracking:
        thread = _threads(self.home).get(session.session_id)
        if not thread or not thread.get("rollout_path"):
            return Tracking()
        return track_from(objects_since(Path(thread["rollout_path"]), message.staged_at),
                          message.body, message.staged_at)

    def send(self, session: SessionState, text: str, interrupt: bool = False) -> str:
        if not text or not text.strip():
            return session_steer.REFUSED
        codex = self._codex or shutil.which("codex")
        if not codex:
            return session_steer.failed("the codex command is not on the PATH")
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.home)
        try:
            done = subprocess.run([*codex.split(), "queue", "--thread", session.session_id,
                                   "--message", text.strip()],
                                  capture_output=True, text=True, timeout=QUEUE_TIMEOUT_SEC, env=env)
        except subprocess.TimeoutExpired:
            log.warning("codex queue timed out")
            return session_steer.failed(f"codex queue did not finish in {QUEUE_TIMEOUT_SEC:.0f} s")
        except OSError as e:
            log.warning("codex queue could not run: %s", e)
            return session_steer.failed(f"codex queue could not run: {e}")
        if done.returncode != 0:
            # Codex's own words are the only account of why; keep them.
            said = (done.stderr or "").strip() or (done.stdout or "").strip()
            log.warning("codex queue exited %s: %s", done.returncode, said[-500:])
            return session_steer.failed(f"codex queue exited {done.returncode}"
                                        + (f": {said[-280:]}" if said else ""))
        return session_steer.SENT


# Metadata is immutable for a rollout; cache only identity, never activity.
_PARENT_CACHE: dict[str, dict] = {}

def thread_identity(path: str) -> dict:
    if path in _PARENT_CACHE:
        return _PARENT_CACHE[path]
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.loads(f.readline(2 * 1024 * 1024))
        meta = obj.get("payload") or {}
        spawn = ((meta.get("source") or {}).get("subagent") or {}).get("thread_spawn") or {} if isinstance(meta.get("source"), dict) else {}
        identity = {"parent": meta.get("parent_thread_id") or spawn.get("parent_thread_id"),
                    "name": meta.get("agent_path") or spawn.get("agent_path") or meta.get("agent_nickname") or ""}
    except (OSError, ValueError, TypeError):
        return {}
    if len(_PARENT_CACHE) >= 4096:
        _PARENT_CACHE.clear()
    _PARENT_CACHE[path] = identity
    return identity

def child_sessions(threads: dict, parents: list[SessionState], include=()) -> list[SessionState]:
    """Discover descendants by recorded parent identity, never shared cwd.

    A child can itself have children. A turn record does not establish that
    its slot is still allocated: that uncertainty remains in the inventory.
    """
    known = {s.session_id for s in parents}
    candidates = {}
    for tid, thread in threads.items():
        if tid not in known and thread.get("thread_source") == "subagent":
            candidates[tid] = (thread, thread_identity(thread.get("rollout_path") or ""))
    children = []
    while candidates:
        adopted = [tid for tid, (_, ident) in candidates.items() if ident.get("parent") in known or tid in include]
        if not adopted:
            break
        for tid in adopted:
            thread, ident = candidates.pop(tid)
            objects = _tail(thread.get("rollout_path") or "")
            r = recap(objects)
            status = session_status(activity(r))
            children.append(SessionState(
                session_id=tid, cwd=thread["cwd"], project=project_name(thread["cwd"]),
                state=status.state, provider=NAME, origin="subagent",
                parent_session_id=ident.get("parent"), given_name=ident.get("name", ""),
                title=(thread.get("title") or "")[:120],
                worker_activity=worker_activity.observation(objects, NAME),
                since=status.since or (thread.get("updated_at_ms") or 0) / 1000 or None,
                between_turns=status.between_turns))
            known.add(tid)
    return children
