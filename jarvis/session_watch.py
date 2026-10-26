"""Watch every Claude Code session on this machine.

Three levels, and the distinction matters: a *process* is one `claude`; a
*conversation* is one `sessionId` and one transcript; a *project* is one cwd.
Measured on 2026-09-03, 17 live processes were 14 conversations in 10
projects — three processes shared one hammer conversation. Counting processes
is what made JARVIS miscount sessions out loud, so speech uses conversations.

Pure filesystem reading: no asyncio, no server imports, no LLM. Every parse is
failure-tolerant, because these files are written live by other processes and
will be read mid-write. The one write is the watcher's, through the message
store it is handed (`SessionWatcher(messages=...)`): what it found of each
message JARVIS sent.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from jarvis import data_paths
from jarvis.session_provider import Tracking

# Both roots must be read: the CLI's default and the one Orcha sets via
# CLAUDE_CONFIG_DIR. On the dev machine `~/.claude/sessions` was empty and
# every session lived under `~/.claude-orcha` — a plain-Terminal user is the
# mirror image, so neither root may be dropped.
DEFAULT_ROOTS = ("~/.claude", "~/.claude-orcha")

# Who runs every session this module finds: they are all Claude Code
# sessions, so Anthropic. The page shows this provider's logo on each
# agent; a watcher for another tool gives its sessions its own.
PROVIDER = "anthropic"


def config_roots() -> list[Path]:
    """Every directory that may hold a `sessions/` roster, in priority order."""
    roots = [Path(r).expanduser() for r in DEFAULT_ROOTS]
    extra = os.getenv("JARVIS_CLAUDE_CONFIG_DIRS", "")
    for raw in extra.split(os.pathsep):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser()
            if p not in roots:
                roots.append(p)
    return roots


def pid_alive(pid) -> bool:
    """True if the process exists. Signal 0 checks without touching it.

    `pid` must be a positive integer: 0 means "this process's group" and a
    negative pid means "that group" to `os.kill`, neither of which is a real
    process, so both are rejected before the syscall.
    """
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def encode_cwd(cwd: str) -> str:
    """The CLI's transcript directory name: EVERY non-alphanumeric becomes `-`.

    Not just `/`. A worktree path contains dots, and the `/`-only shortcut
    silently fails to find its transcript.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _ms(value) -> float | None:
    """Roster timestamps are epoch milliseconds; we work in seconds."""
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RosterEntry:
    """One `sessions/<pid>.json` — one live `claude` process."""
    pid: int
    session_id: str
    cwd: str
    name: str
    root: Path
    kind: str = "interactive"
    entrypoint: str = "cli"
    status: str | None = None
    waiting_for: str | None = None
    started_at: float | None = None
    status_updated_at: float | None = None
    socket_path: str | None = None
    version: str = ""

    @property
    def steerable(self) -> bool:
        """A process can only be steered if it bound an inbox socket.

        Measured: 4 of 17 live entries had none. `ListAgents` cannot see those
        at all, which is why this watcher exists.
        """
        return bool(self.socket_path) and Path(self.socket_path).exists()


def _parse_entry(path: Path, root: Path) -> RosterEntry | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None            # unreadable, empty, or caught mid-write
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data["pid"])
        session_id = str(data["sessionId"])
        cwd = str(data["cwd"])
    except (KeyError, TypeError, ValueError):
        return None            # without these three it is not a session
    return RosterEntry(
        pid=pid,
        session_id=session_id,
        cwd=cwd,
        name=str(data.get("name") or Path(cwd).name or session_id[:8]),
        root=root,
        kind=str(data.get("kind") or "interactive"),
        entrypoint=str(data.get("entrypoint") or "cli"),
        status=data.get("status") if isinstance(data.get("status"), str) else None,
        waiting_for=(data.get("waitingFor")
                     if isinstance(data.get("waitingFor"), str) and data.get("waitingFor")
                     else None),
        started_at=_ms(data.get("startedAt")),
        status_updated_at=_ms(data.get("statusUpdatedAt")),
        socket_path=(str(data["messagingSocketPath"])
                     if isinstance(data.get("messagingSocketPath"), str) else None),
        version=str(data.get("version") or ""),
    )


def read_roster(roots: list[Path] | None = None) -> list[RosterEntry]:
    """Every well-formed roster entry across every root. Never raises."""
    entries: list[RosterEntry] = []
    seen: set[tuple[int, str]] = set()
    for root in (roots if roots is not None else config_roots()):
        d = Path(root) / "sessions"
        try:
            files = sorted(d.glob("*.json"))
        except OSError:
            continue
        for f in files:
            entry = _parse_entry(f, Path(root))
            if entry is None:
                continue
            key = (entry.pid, entry.session_id)
            if key in seen:          # the same pid registered under two roots
                continue
            seen.add(key)
            entries.append(entry)
    return entries


# A 64 KB tail was sufficient for every one of the 14 live transcripts on
# 2026-09-03, including a 95 MB one, because `ai-title` and `last-prompt` are
# rewritten on every turn and therefore always sit near the end.
TAIL_BYTES = 64 * 1024
MAX_RECENT_TOOLS = 5
MAX_TEXT = 600


@dataclass
class Recap:
    """What a session is doing, read from the end of its transcript."""
    exists: bool = False
    title: str | None = None          # the CLI's own `aiTitle`
    last_prompt: str | None = None    # the CLI's own `lastPrompt`
    last_text: str | None = None      # what the session last said
    recent_tools: list[str] = field(default_factory=list)
    # The main conversation's turn as its transcript last marked it: "open"
    # after a turn starts (his words, a message, a background task's notice),
    # "closed" after the turn-end marker, None when the tail shows neither.
    # Subagents write elsewhere, so this is the main conversation's alone.
    turn: str | None = None
    turn_marked_at: float | None = None

    def summary(self) -> str | None:
        """One line describing the session: its title, else its last prompt."""
        return self.title or self.last_prompt


def transcript_path(root: Path, cwd: str, session_id: str) -> Path:
    return Path(root) / "projects" / encode_cwd(cwd) / f"{session_id}.jsonl"


def tail_objects(path: Path, nbytes: int = TAIL_BYTES) -> list[dict]:
    """Parse the JSON objects in the last `nbytes` of a JSONL file.

    Seeking into a multi-megabyte file always lands mid-line, so the first
    (partial) line is discarded. Unparseable lines are skipped: this file is
    appended to by another process while we read it.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size > nbytes:
                fh.seek(size - nbytes)
                fh.readline()          # discard the partial line
            else:
                fh.seek(0)
            raw = fh.read()
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _clip(text, limit: int = MAX_TEXT) -> str | None:
    """One line of clipped text, or None — for a value of ANY shape.

    Every caller reads a field out of somebody else's transcript, so the type
    check belongs here rather than at each call site: the call sites had it
    twice out of three times, and the third raised AttributeError on a
    non-string `text` block, which escaped to `SessionWatcher._loop`, was
    logged as a warning and swallowed, and left `self.snapshot` FROZEN at the
    last good poll for as long as that line stayed inside the 64 KB tail.
    `/api/sessions` went on answering 200 the whole time.
    """
    if not isinstance(text, str) or not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def recap_from(objs: list[dict]) -> Recap:
    """Reduce transcript lines to a recap. Unknown line types are ignored —
    19 types were observed on the live machine and only these five are used."""
    r = Recap(exists=True)
    tools: list[str] = []
    for o in objs:
        kind = o.get("type")
        if kind == "ai-title":
            r.title = _clip(o.get("aiTitle"), 200) or r.title
        elif kind == "last-prompt":
            r.last_prompt = _clip(o.get("lastPrompt")) or r.last_prompt
        elif kind == "system" and o.get("subtype") == "turn_duration" \
                and not o.get("isSidechain"):
            r.turn, r.turn_marked_at = "closed", iso_seconds(o.get("timestamp"))
        elif kind == "user" and _starts_a_turn(o):
            r.turn, r.turn_marked_at = "open", iso_seconds(o.get("timestamp"))
        elif kind == "assistant" and not o.get("isSidechain"):
            message = o.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    r.last_text = _clip(block.get("text")) or r.last_text
                elif block.get("type") == "tool_use" and block.get("name"):
                    # `_clip` like its three siblings above, and for the same
                    # reason: this is a string out of somebody else's
                    # transcript, of any length and any shape, and it reaches
                    # a header line in `tool_session_detail`.
                    tools.append(_clip(block["name"]) or "")
    r.recent_tools = tools[-MAX_RECENT_TOOLS:]
    return r


# A whole reply can be far longer than the recap's 64 KB tail is meant for,
# so the reply reader looks further back. Still a tail: transcripts reach
# tens of megabytes, and only the last turn is wanted.
REPLY_TAIL_BYTES = 2 * 1024 * 1024


def _is_prompt(obj: dict) -> bool:
    """A user line that starts a new turn: typed text, not a tool result."""
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return bool(content.strip())
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "text" for b in content)


# How far back the reply reader looks, widening only when the turns it can
# see have no text: a whole reply first, then further back for one whose
# latest turns were all tool calls. Never the whole of a long transcript.
REPLY_WINDOWS = (REPLY_TAIL_BYTES, 16 * 1024 * 1024)


def tail_windows(path: Path):
    """(objects, whole) for each of REPLY_WINDOWS in turn, smallest first;
    `whole` once a window holds the entire file, after which there is no
    wider one."""
    for nbytes in REPLY_WINDOWS:
        objs = tail_objects(path, nbytes)
        try:
            whole = path.stat().st_size <= nbytes
        except OSError:
            return
        yield objs, whole
        if whole:
            return


def _turn_texts(objs: list[dict]) -> list[tuple[bool, str]]:
    """Each turn in view, oldest first: whether its prompt is in view (a turn
    cut off by the window's start is not), and everything it wrote."""
    turns: list[tuple[bool, list[str]]] = [(False, [])]
    for o in objs:
        if o.get("isSidechain"):
            continue
        kind = o.get("type")
        if kind == "user" and _is_prompt(o):
            turns.append((True, []))
        elif kind == "assistant":
            message = o.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if (isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                        and block["text"].strip()):
                    turns[-1][1].append(block["text"].strip())
    return [(seen, "\n\n".join(parts)) for seen, parts in turns]


def last_reply_from(objs: list[dict], whole: bool = True) -> str | None:
    """Everything the session wrote in its latest turn that wrote anything,
    unclipped: the last turn, else the one before, and so on back. A turn
    whose start is outside the window counts only if the window is the
    whole file, since otherwise it may be missing its beginning.

    `recap_from` keeps only the LAST text block, cut at MAX_TEXT. A reply
    that asks three questions across several blocks lost the rest that way,
    which is what this exists to stop.
    """
    for seen, text in reversed(_turn_texts(objs)):
        if text and (seen or whole):
            return text
    return None


def read_last_reply(cwd: str, session_id: str,
                    roots: list[Path] | None = None) -> str | None:
    """A session's whole latest reply, from the first root that has its
    transcript, or None if it has said nothing."""
    for root in (roots if roots is not None else config_roots()):
        path = transcript_path(root, cwd, session_id)
        if path.exists():
            for objs, whole in tail_windows(path):
                reply = last_reply_from(objs, whole)
                if reply:
                    return reply
            return None
    return None


# ── a session's transcript, entry by entry ─────────────────────────────────
#
# What the Workflows view's Transcript tab shows: each prompt, reply, tool
# call and tool result, newest last, read back from the end of the file a
# page at a time. `offset` is each entry's line's byte offset, so "earlier"
# reads only the bytes before the oldest entry shown. Everything here is
# another session's text, shown on the page as text and never to the brain.

TRANSCRIPT_PAGE = 200                 # entries per page
TRANSCRIPT_TEXT_MAX = 4000            # a prompt or reply, per entry
RESULT_LINES = 6                      # a tool result: its first lines
RESULT_TEXT_MAX = 600


@dataclass(frozen=True)
class TranscriptEntry:
    offset: int            # byte offset of its line in the transcript: the cursor for "earlier"
    at: float | None
    role: str              # "you" (a prompt), "jarvis" (a message from JARVIS), "agent", "tool", "result"
    text: str
    tool: str | None = None


def lines_before(path: Path, before: int | None, limit: int,
                 parse, window: int = 512 * 1024) -> list[TranscriptEntry]:
    """The last `limit` entries that `parse(offset, obj)` makes of the lines
    ending before byte `before` (the end of the file when None), oldest
    first. Reads back a window at a time until it has `limit` or reaches the
    start; never more of the file than that."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    end = size if before is None else max(0, min(before, size))
    entries: list[TranscriptEntry] = []
    while end > 0 and len(entries) < limit:
        start = max(0, end - window)
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(end - start)
        cut = 0
        if start > 0:
            cut = raw.find(b"\n") + 1           # the first line is partial
            if cut == 0:                        # one line longer than the window
                window *= 4
                continue
        page: list[TranscriptEntry] = []
        offset = start + cut
        for line in raw[cut:].split(b"\n"):
            here, offset = offset, offset + len(line) + 1
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                page.extend(parse(here, obj))
        entries = page + entries
        end = start + cut
    if len(entries) <= limit:
        return entries
    # A page starts on a whole line: one line can make several entries (a
    # reply and the tool call after it), and the next page's cursor is that
    # line's offset, so a line split here would lose its first entries.
    first = entries[-limit].offset
    return [e for e in entries if e.offset >= first]


def _first_lines(text: str) -> str:
    lines = [l for l in str(text).splitlines() if l.strip()][:RESULT_LINES]
    out = "\n".join(lines)
    return out if len(out) <= RESULT_TEXT_MAX else out[: RESULT_TEXT_MAX - 1] + "…"


def _long(text: str) -> str:
    text = str(text).strip()
    return text if len(text) <= TRANSCRIPT_TEXT_MAX else text[: TRANSCRIPT_TEXT_MAX - 1] + "…"


# The one argument that says what a tool call did, by the names tools use.
_TOOL_GIST_KEYS = ("command", "file_path", "path", "pattern", "url", "query",
                   "description", "prompt", "skill")


def tool_gist(arguments) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return _first_lines(arguments)
    if isinstance(arguments, dict):
        for key in _TOOL_GIST_KEYS:
            if isinstance(arguments.get(key), str) and arguments[key].strip():
                return _first_lines(arguments[key])
    return ""


def _claude_entries(offset: int, o: dict, calls: dict | None = None) -> list[TranscriptEntry]:
    """`calls` maps each tool call's id to its tool's name as calls are read,
    so a result can say which tool it came from."""
    calls = {} if calls is None else calls
    if o.get("isSidechain"):
        return []
    at = iso_seconds(o.get("timestamp"))
    kind = o.get("type")
    message = o.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    out: list[TranscriptEntry] = []
    if kind == "user":
        if isinstance(content, str) or _is_prompt(o):
            text = _user_text(o).strip()
            if text and not o.get("isCompactSummary"):
                peer = isinstance(o.get("origin"), dict) and o["origin"].get("kind") == "peer"
                if peer or not o.get("isMeta"):
                    out.append(TranscriptEntry(offset, at, "jarvis" if peer else "you", _long(text)))
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                result = block.get("content")
                if isinstance(result, list):
                    result = "\n".join(b.get("text", "") for b in result if isinstance(b, dict))
                out.append(TranscriptEntry(offset, at, "result", _first_lines(result or ""),
                                           tool=calls.get(block.get("tool_use_id"))))
    elif kind == "attachment" and isinstance(o.get("attachment"), dict) \
            and o["attachment"].get("type") == "queued_command":
        out.append(TranscriptEntry(offset, at, "jarvis", _long(o["attachment"].get("prompt", ""))))
    elif kind == "assistant":
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                out.append(TranscriptEntry(offset, at, "agent", _long(block["text"])))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")[:80]
                if block.get("id"):
                    calls[block["id"]] = name
                out.append(TranscriptEntry(offset, at, "tool", tool_gist(block.get("input")),
                                           tool=name))
    return out


def read_transcript(cwd: str, session_id: str, roots: list[Path] | None = None,
                    before: int | None = None, limit: int = TRANSCRIPT_PAGE) -> list[TranscriptEntry]:
    """A Claude Code session's transcript, the last `limit` entries before
    `before`, from the first root that has it."""
    for root in (roots if roots is not None else config_roots()):
        path = transcript_path(root, cwd, session_id)
        if path.exists():
            calls: dict = {}
            return lines_before(path, before, limit,
                                lambda offset, o: _claude_entries(offset, o, calls))
    return []


# ── where a message stands in a transcript ─────────────────────────────────
#
# A message is found by Tim's words (the text after JARVIS's signature) in
# lines written no earlier than it was staged. Measured on Claude Code
# 2.1.300 transcripts that had received JARVIS's messages:
#
#   delivered  a `queue-operation` line, `operation: "enqueue"`, whose
#              `content` is the whole message as sent.
#   started    to an idle session: a `dequeue`, then a user line carrying the
#              message (`isMeta`, `origin.kind: "peer"`, content "Another
#              Claude session sent a message:\n…"), which begins its own turn.
#              To a busy one: a `remove`, then an `attachment` of type
#              `queued_command` whose `prompt` is the message — it is taken
#              into the turn in progress, between two tool calls, and that
#              turn is the one that answers it.
#   answered   a `system` line of subtype `turn_duration` ends the turn: 41 of
#              41 turns on a JARVIS message had one. A new turn starting is
#              taken as the end too, should one ever be missing.
#   reply      the text the turn wrote after its last tool call: its final
#              answer, every block of it.

def iso_seconds(stamp) -> float | None:
    """An ISO-8601 transcript timestamp ("…Z") in epoch seconds, or None."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# How far back from the end a message is looked for: the tail is widened
# from the first size, four times over each time, until its first dated line
# is older than the message or it is the whole file, but never past the last.
# Transcripts reach tens of megabytes; a message is near the end of one.
TRACK_TAIL_START = 256 * 1024
TRACK_TAIL_MAX = 64 * 1024 * 1024


def objects_since(path: Path, since: float) -> list[dict]:
    """The transcript's lines from about `since` to its end."""
    nbytes = TRACK_TAIL_START
    while True:
        objs = tail_objects(path, nbytes)
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        if nbytes >= size or nbytes >= TRACK_TAIL_MAX:
            return objs
        first = next((t for t in (iso_seconds(o.get("timestamp")) for o in objs)
                      if t is not None), None)
        if first is not None and first < since:
            return objs
        nbytes *= 4


def _user_text(obj: dict) -> str:
    """Everything a user line says as text, whatever its content's shape."""
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str))
    return ""


def _starts_a_turn(obj: dict) -> bool:
    """A user line that begins a turn of its own: something typed, or a
    message from another session. Not a tool result, not the summary a
    compaction writes, and not the text Claude Code adds to a turn under way
    (`isMeta` — except a peer message, which is `isMeta` and is a turn)."""
    if obj.get("type") != "user" or obj.get("isSidechain"):
        return False
    if obj.get("isCompactSummary") or obj.get("isVisibleInTranscriptOnly"):
        return False
    if not _is_prompt(obj):
        return False
    origin = obj.get("origin")
    if obj.get("isMeta") and not (isinstance(origin, dict) and origin.get("kind") == "peer"):
        return False
    return True


# A turn on a message that ends without a word (interrupted, typically, by
# something typed into the session) is not taken as its answer at once: the
# session's next turn, begun within this long and not by another session's
# message, is, if it says anything. Until then the message stays open.
LATE_ANSWER_SEC = 10 * 60


def _is_peer(obj: dict) -> bool:
    origin = obj.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "peer"


def _turn_from(objs: list[dict], i: int, clock: float | None):
    """Follow the turn under way from objs[i] to its end: (the index after
    it, when it ended or None if it has not, its final answer's blocks, the
    latest time seen)."""
    parts: list[str] = []
    for j in range(i, len(objs)):
        o = objs[j]
        if o.get("isSidechain"):
            continue
        ts = iso_seconds(o.get("timestamp"))
        kind = o.get("type")
        if kind == "system" and o.get("subtype") == "turn_duration":
            return j + 1, (ts if ts is not None else clock), parts, clock
        if _starts_a_turn(o):
            return j, clock, parts, clock     # the last moment of the turn before it
        if ts is not None:
            clock = ts if clock is None else max(clock, ts)
        if kind == "assistant":
            message = o.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    parts = []         # what came before a tool call was not the answer
                elif (block.get("type") == "text" and isinstance(block.get("text"), str)
                        and block["text"].strip()):
                    parts.append(block["text"].strip())
    return len(objs), None, parts, clock


def track_from(objs: list[dict], body: str, since: float,
               now: float | None = None) -> Tracking:
    """Where the message whose words are `body`, staged at `since`, stands
    in these Claude Code transcript lines. See the note above, and
    LATE_ANSWER_SEC for a turn that ended without a word."""
    if not body:
        return Tracking()
    now = time.time() if now is None else now
    clock = None                       # the latest time the transcript has shown
    delivered = started = None
    i = 0
    while i < len(objs) and started is None:
        o = objs[i]
        i += 1
        if o.get("isSidechain"):
            continue
        ts = iso_seconds(o.get("timestamp"))
        kind = o.get("type")
        if ts is not None:
            clock = ts if clock is None else max(clock, ts)
        if ts is not None and ts < since:
            continue
        if (delivered is None and kind == "queue-operation"
                and o.get("operation") == "enqueue"
                and isinstance(o.get("content"), str) and body in o["content"]):
            delivered = ts if ts is not None else clock
            continue
        attachment = o.get("attachment") if kind == "attachment" else None
        taken_midturn = (isinstance(attachment, dict)
                         and attachment.get("type") == "queued_command"
                         and isinstance(attachment.get("prompt"), str)
                         and body in attachment["prompt"])
        if taken_midturn or (_starts_a_turn(o) and body in _user_text(o)):
            started = clock if clock is not None else since
            if delivered is None:
                delivered = started
    if started is None:
        return Tracking(delivered_at=delivered)
    i, answered, parts, clock = _turn_from(objs, i, clock)
    if answered is not None and not parts:
        # Ended without a word: the next turn may carry the answer.
        late = _late_answer(objs, i, answered, clock, now)
        if late is None:
            return Tracking(delivered_at=delivered, started_at=started)
        answered, parts = late
    reply = ("\n\n".join(parts) or None) if answered is not None else None
    return Tracking(delivered_at=delivered, started_at=started,
                    answered_at=answered, reply=reply)


def _late_answer(objs: list[dict], i: int, ended: float, clock: float | None,
                 now: float):
    """After a turn on a message ended without a word at `ended`: (when the
    answer ended, its blocks) — the next turn's, if it began within
    LATE_ANSWER_SEC and not with another session's message, and said
    something — or the wordless end itself once that can no longer happen.
    None while it still can (the message stays open)."""
    deadline = ended + LATE_ANSWER_SEC
    for j in range(i, len(objs)):
        o = objs[j]
        if not _starts_a_turn(o):
            continue
        ts = iso_seconds(o.get("timestamp"))
        if _is_peer(o) or (ts is not None and ts > deadline):
            return ended, []
        _, answered, parts, _ = _turn_from(objs, j + 1, ts if ts is not None else clock)
        if answered is None:
            return None                # that turn is still going
        return (answered, parts) if parts else (ended, [])
    return None if now < deadline else (ended, [])


def _roots_of(group: list, primary) -> list[Path]:
    """Every root this conversation registered in, the primary's first.

    Order matters: where two roots both hold a transcript (they are
    hardlinked on the live machine, so this is the normal case) the
    primary's is the one read.
    """
    out = [primary.root]
    for e in group:
        if e.root not in out:
            out.append(e.root)
    return out


def _first_recap(roots: list[Path], cwd: str, session_id: str) -> Recap:
    """The first root that actually has this conversation's transcript.

    `Recap(exists=False)` — no transcript anywhere — is what makes a session
    `fresh`, so looking under one root and stopping reported a busy
    conversation as "not started".
    """
    for root in roots:
        recap = read_recap(root, cwd, session_id)
        if recap.exists:
            return recap
    return Recap(exists=False)


def read_recap(root: Path, cwd: str, session_id: str,
               nbytes: int = TAIL_BYTES) -> Recap:
    """Recap a session, or `Recap(exists=False)` if it has no transcript.

    Absence is meaningful: a session nobody has prompted writes no transcript
    at all, which is how `fresh` is detected without parsing anything.
    """
    path = transcript_path(root, cwd, session_id)
    if not path.exists():
        return Recap(exists=False)
    return recap_from(tail_objects(path, nbytes))


# Derived conversation states: what `session_status` says, and nothing else.
FRESH = "fresh"          # alive but never prompted (no transcript) — never announced
NEEDS_YOU = "needs_you"  # stopped at a prompt or dialog, or its turn ended on a question
WORKING = "working"      # its main conversation is in a turn
BACKGROUND = "background"  # between turns, with background agents or commands still running
IDLE = "idle"            # between turns, nothing running
GONE = "gone"            # no process left
UNKNOWN = "unknown"      # alive, but neither its transcript nor the roster says

# Keywords matched against a lowercased `waitingFor` reason to flag one that
# a peer message cannot clear. The reason set itself is OPEN — "input needed"
# turned up after only "permission prompt" and "dialog open" had been seen,
# and more will appear. "input needed" is deliberately NOT in this list: a
# peer message over the inbox socket is exactly what a session waiting on
# input wants, so it stays answerable rather than getting waved off as
# needing a human hand. An unrecognised reason is likewise treated as
# answerable by default — JARVIS attempts it and reports the real outcome,
# rather than refusing something he could have done because the string
# didn't happen to be on this list yet.
HUMAN_HAND_REASONS = ("permission", "dialog", "trust", "login", "auth")

_QUESTION_TOOLS = ("AskUserQuestion", "ExitPlanMode")


_URL_RE = re.compile(r"\S+://\S+|\bwww\.\S+")


@dataclass(frozen=True)
class Activity:
    """Every raw fact about one session, read once per poll by its provider.
    `session_status` is the only thing that turns it into a state."""
    alive: bool                       # a process for it is running
    prompted: bool                    # it has a transcript: someone has started it
    turn: str | None                  # "open", "closed" or None: its main conversation's turn
    turn_marked_at: float | None      # when its transcript marked that
    prompt_waiting: str | None        # a permission prompt or dialog it is stopped at
    asked: bool                       # its last turn ended asking him something
    background_agents: int            # its subagents written to lately
    background_commands: bool         # a background command still running
    roster_status: str | None         # the roster's own word: used only when `turn` is None
    roster_since: float | None = None  # when the roster's word last changed


@dataclass(frozen=True)
class Status:
    """A session's state, and everything that follows from it."""
    state: str
    needs: str | None                 # what it waits on him for, if anything
    between_turns: bool               # a message now starts a turn of its own
    since: float | None               # when this state began, as near as is known


def session_status(a: Activity) -> Status:
    """The one way a session's state is worked out, for every provider and
    every reader: the agents list and table, the message queue, the
    announcements, the tools. Its transcript's turn markers decide, because
    the roster says `busy` while any background agent works, found live with
    a fork running while the conversation sat at its prompt; the roster's
    word decides only where the transcript shows no marker."""
    marked = a.turn_marked_at or a.roster_since
    if not a.alive:
        return Status(GONE, None, False, marked)
    if not a.prompted:
        return Status(FRESH, None, True, a.roster_since)
    if a.prompt_waiting:
        # A prompt mid-turn: the turn resumes when he answers it.
        return Status(NEEDS_YOU, a.prompt_waiting, False, a.roster_since or marked)
    turn = a.turn
    if turn is None:
        turn = {"busy": "open", "waiting": "open", "idle": "closed",
                "shell": "closed"}.get(a.roster_status or "")
        if turn is None:
            return Status(UNKNOWN, None, True, marked)
        if a.roster_status == "waiting":
            return Status(NEEDS_YOU, None, False, marked)
    if turn == "open":
        return Status(WORKING, None, False, marked)
    if a.asked:
        return Status(NEEDS_YOU, None, True, marked)
    if a.background_agents or a.background_commands:
        return Status(BACKGROUND, None, True, marked)
    return Status(IDLE, None, True, marked)


def _looks_like_a_question(text: str | None) -> bool:
    """Did the session stop to ask something?

    A bare `"?" in tail` is too eager: a URL query string counts. Measured on
    the live machine — a session that had hit its spend limit ended with
    `…/settings/usage?from=cc_cli_limit_message`, and was announced as needing
    the user. Needs-you announcements interrupt at URGENT priority, so a false
    positive here nags the user about a session that wants nothing.

    URLs are stripped first, then a question mark only counts where a question
    actually ends: at the close of the message or of one of its last lines.
    """
    if not text:
        return False
    cleaned = _URL_RE.sub(" ", text).rstrip()
    if not cleaned:
        return False
    if cleaned.endswith("?"):
        return True
    # A question can be followed by options ("...or SQLite?\n1. Postgres").
    return any(line.rstrip().endswith("?") for line in cleaned.splitlines()[-6:])


@dataclass
class SessionState:
    """One conversation: what it is, what it is doing, and how to reach it."""
    session_id: str
    cwd: str
    project: str
    state: str
    pids: list[int] = field(default_factory=list)
    primary_pid: int | None = None
    roster_name: str = ""
    voice_name: str = ""
    needs: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    last_text: str | None = None
    recent_tools: list[str] = field(default_factory=list)
    # When the SESSION STARTED. Used for age ordering (`_name_by_age`'s
    # "the newer hammer" / "the older hammer"). Do not use this for spoken
    # elapsed time — see `since` below.
    started: float | None = None
    # When the CURRENT STATE began. Used for spoken elapsed time ("hammer has
    # been waiting about an hour"). Measured on the live roster: the largest
    # gap between `startedAt` and `statusUpdatedAt` was 102 HOURS (VoyageStudios,
    # pid 37497), with another material 4.2-hour gap on a hammer session — so
    # `started` and `since` are NOT interchangeable and must stay two fields.
    # Do not "simplify" these back into one.
    since: float | None = None
    origin: str = "terminal"
    provider: str = PROVIDER
    steerable: bool = False
    socket_path: str | None = None
    # Is this the conversation the user is actually sitting at, in this
    # project? See `_mark_primary` for the rule and why it can answer "no"
    # about every conversation in a project.
    primary: bool = False
    primary_reason: str = ""
    # Subagents this conversation dispatched, counted off its own
    # `<sessionId>/subagents/` folder. `agents_active` means "written to
    # within AGENT_ACTIVE_WITHIN_SEC", never "a process is alive" — there is
    # no process here to check.
    agents_seen: int = 0
    agents_active: int = 0
    # `agents_seen` hit MAX_AGENT_FILES: it is a floor, not a total.
    agents_capped: bool = False
    # From `session_status`: a message now starts a turn of its own.
    between_turns: bool = False

    @property
    def announceable(self) -> bool:
        """`fresh` sessions are never spoken about unprompted: nobody has
        started them, so 'chitauri needs you' would be nonsense."""
        return self.state not in (FRESH,)

    @property
    def needs_a_human_hand(self) -> bool:
        """True when the thing it waits on cannot be answered over the socket."""
        reason = (self.needs or "").lower()
        return any(word in reason for word in HUMAN_HAND_REASONS)

    def summary(self) -> str | None:
        return self.title or self.last_prompt


# ── which conversation is the main one ──────────────────────────────────────
#
# There was no notion of a PRIMARY conversation before this. The rule, in
# full, because a rule nobody can state is a rule nobody can check:
#
#   Primary is decided PER PROJECT. A conversation is ELIGIBLE if it is
#   alive, has been prompted at least once (a `fresh` session has no
#   transcript and is nobody's main session), and was started interactively
#   — `entrypoint: cli`, i.e. origin "terminal". A background/SDK session is
#   never the main one: nobody is typing into it.
#
#   Among the eligible, the most recently active wins, measured by `since`
#   (the roster's `statusUpdatedAt`).
#
#   AND: if the runner-up is within PRIMARY_MARGIN_SEC of the leader, NEITHER
#   is primary. Two conversations touched a minute apart are equally live and
#   the signal cannot separate them; claiming one anyway would be a guess
#   wearing a fact's clothes.
#
# Signals deliberately NOT used: transcript size (a long conversation is an
# old one, not an active one) and whether JARVIS spawned it (his own runs are
# filtered out of the roster upstream, in server._snapshot_or_empty, so they
# never reach this rule at all).
PRIMARY_MARGIN_SEC = 120.0

PRIMARY_ONLY = "the only live conversation here"
PRIMARY_RECENT = "most recently active"
PRIMARY_TIED = "equally live as another here"
NOT_PRIMARY_BACKGROUND = "a background conversation"
NOT_PRIMARY_FRESH = "never prompted"
NOT_PRIMARY_GONE = "finished"


def _mark_primary(sessions: list[SessionState]) -> None:
    """Set `primary` / `primary_reason` on every conversation."""
    by_project: dict[str, list[SessionState]] = {}
    for s in sessions:
        if s.state == GONE:
            s.primary, s.primary_reason = False, NOT_PRIMARY_GONE
        elif s.state == FRESH:
            s.primary, s.primary_reason = False, NOT_PRIMARY_FRESH
        elif s.origin != "terminal":
            s.primary, s.primary_reason = False, NOT_PRIMARY_BACKGROUND
        else:
            s.primary, s.primary_reason = False, NOT_PRIMARY_BACKGROUND
            by_project.setdefault(s.project, []).append(s)

    for group in by_project.values():
        if len(group) == 1:
            group[0].primary, group[0].primary_reason = True, PRIMARY_ONLY
            continue
        ranked = sorted(
            group,
            key=lambda s: s.since if s.since is not None else float("-inf"),
            reverse=True)
        lead, runner = ranked[0], ranked[1]
        lead_at = lead.since
        runner_at = runner.since
        separated = (lead_at is not None and
                     (runner_at is None or lead_at - runner_at > PRIMARY_MARGIN_SEC))
        if not separated:
            # Inside the margin nothing is claimed. Every member of the tie
            # says so; the ones further back are simply background.
            for s in ranked:
                at = s.since
                tied = at is not None and lead_at is not None and \
                    lead_at - at <= PRIMARY_MARGIN_SEC
                s.primary_reason = PRIMARY_TIED if tied else NOT_PRIMARY_BACKGROUND
            continue
        lead.primary, lead.primary_reason = True, PRIMARY_RECENT
        for s in ranked[1:]:
            s.primary, s.primary_reason = False, NOT_PRIMARY_BACKGROUND


# ── subagents ───────────────────────────────────────────────────────────────

# A subagent transcript written this recently is taken to be working. This is
# a FILE age, stated as one — there is no process to check here, and calling
# it "running" would be a claim the data does not support.
AGENT_ACTIVE_WITHIN_SEC = 90.0
# Cap the stat() calls one conversation can cost a 1 Hz poll. 209 subagent
# transcripts were measured under a single conversation.
MAX_AGENT_FILES = 300


def count_agents(roots, cwd: str, session_id: str,
                 now: float) -> tuple[int, int, bool]:
    """(subagent transcripts seen, how many were written recently, capped?).

    `roots` is one config root or several. Several are deduped BY FILE NAME:
    the two roots are hardlinked on the live machine, so `agent-x.jsonl`
    under each is one agent reached by two names, and unioning the listings
    naively doubles every count on the page.

    The CLI puts one file per dispatched subagent in
    `projects/<encoded cwd>/<sessionId>/subagents/`. Measured 2026-09-03:
    1,573 such files against 947 conversations.

    THE CAP COUNTS TRANSCRIPTS, NOT DIRECTORY ENTRIES. Every `agent-x.jsonl`
    has an `agent-x.json` sidecar beside it, so one live folder held 418
    entries for 209 agents — slicing the raw listing first reported 150,
    which looked exactly like a real number. Filter, then cap.

    BOTH COUNTS ARE FLOORS ONCE THE CAP IS HIT. `seen` obviously; `active`
    just as much, because the slice is taken by FILE NAME and file names are
    uncorrelated with recency — the 300 examined may hold none of the busy
    ones. The third value says so, and both pills must render it.

    Never raises: this folder belongs to another process, and files appear
    and vanish underneath it.
    """
    by_name: dict[str, Path] = {}
    for root in ([roots] if isinstance(roots, (str, Path)) else roots):
        d = (Path(root) / "projects" / encode_cwd(cwd) / session_id
             / "subagents")
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if f.name.endswith(".jsonl"):
                by_name.setdefault(f.name, f)

    transcripts = [by_name[name] for name in sorted(by_name)]
    capped = len(transcripts) > MAX_AGENT_FILES
    seen = active = 0
    for f in transcripts[:MAX_AGENT_FILES]:
        try:
            st = f.stat()
        except OSError:
            continue
        if not os.path.isfile(f):
            continue
        seen += 1
        if now - st.st_mtime <= AGENT_ACTIVE_WITHIN_SEC:
            active += 1
    return seen, active, capped


def project_name(cwd: str) -> str:
    """The project name for a cwd, tolerant of Claude Code's own worktree layout.

    Claude Code creates worktrees under `<repo>/.claude/worktrees/<branch>`, so
    a plain `Path(cwd).name` on such a path yields the branch/worktree name
    (e.g. "runs-dashboard") instead of the repo the user actually means (e.g.
    "jarvis"). When the path contains a `.claude/worktrees` segment, the
    project is the directory name that PRECEDES `.claude`.

    This covers Claude Code's own worktree convention ONLY. A worktree created
    elsewhere (via plain `git worktree add`, outside `.claude/worktrees`) is
    still named after its own directory: detecting that properly would need a
    `git` subprocess call, and this runs on a 1-second poll loop, so it is
    deliberately not attempted.

    Two worktrees of the same repo therefore share a project name here, and
    are told apart afterward by the existing voice-naming rules (folder, then
    age) — that is correct and desirable, not a bug.
    """
    parts = Path(cwd).parts
    if ".claude" in parts:
        i = parts.index(".claude")
        if i > 0 and parts[i - 1] and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            return parts[i - 1]
    return Path(cwd).name or cwd


def worktree_branch(cwd: str) -> str:
    """The worktree name in `<repo>/.claude/worktrees/<branch>`, or "".

    The other half of `project_name`: that function answers "which project
    is this", this one answers "which copy of it". Two worktrees of one repo
    share a project name on purpose, so anything that shows both at once
    needs this to tell them apart.
    """
    parts = Path(cwd).parts
    if ".claude" not in parts:
        return ""
    i = parts.index(".claude")
    if i > 0 and i + 2 < len(parts) and parts[i + 1] == "worktrees":
        return parts[i + 2]
    return ""


_ORIGIN_BY_ENTRYPOINT = {
    "cli": "terminal",
    "sdk-cli": "background",
    "claude-desktop": "desktop",
    "desktop": "desktop",
}


def _origin(entry: RosterEntry) -> str:
    return _ORIGIN_BY_ENTRYPOINT.get(entry.entrypoint, "terminal")


def brain_cwd() -> str:
    """Where JARVIS's own brain process runs: `data_paths.brain_home()`
    (`<JARVIS_DATA_DIR>/jarvis`), computed here WITHOUT importing
    `data_paths` and without its `mkdir` side effect.

    This module stays pure filesystem *reading* — it is polled once a second
    — so it must not create directories as a side effect of building a
    snapshot, which `data_paths.data_dir()` does. The formula is kept in
    exact lockstep with `data_paths.brain_home()` by
    `test_brain_cwd_matches_data_paths_brain_home` in the test suite.
    """
    raw = os.getenv("JARVIS_DATA_DIR")
    base = Path(raw).expanduser() if raw else data_paths.DEFAULT_DATA_DIR
    return str(base / "jarvis")


# The private spelling this module has always used. `usage_scan` needs the
# same answer — the brain's transcript is not the user's work either — and
# reaching into another module's underscore would be worse than one alias.
_brain_cwd = brain_cwd


def _is_own_brain(entry: RosterEntry) -> bool:
    """True for JARVIS's own brain process, never a user conversation.

    Measured live: the brain registers with `entrypoint="sdk-cli"` and a cwd
    exactly equal to the brain home directory. The cwd match is checked
    FIRST and is the deciding signal — a roster `name` of "jarvis" is NOT
    used, because a user can legitimately have a project called "jarvis"
    (this user does, at .../Projects/jarvis) and a name-based check would
    wrongly exclude that real conversation too.
    """
    if entry.entrypoint != "sdk-cli":
        return False
    try:
        return _same_dir(entry.cwd, brain_cwd())
    except (TypeError, ValueError):
        return False


def _same_dir(a: str, b: str) -> bool:
    """Two spellings of one directory.

    `realpath`, not a textual compare: `JARVIS_DATA_DIR` pointing at a
    symlink — the ordinary shape when data lives on another volume — spells
    the brain's home one way here and another way in the roster entry the
    process wrote, and the brain then appeared as one of the user's own
    conversations. `realpath` does not require the path to exist.
    """
    if not a or not b:
        return False
    return os.path.realpath(a) == os.path.realpath(b)


def _pick_primary(entries: list[RosterEntry]) -> RosterEntry:
    """Which process do we steer? Alive first, then one with a live socket,
    then the most recently active."""
    return sorted(
        entries,
        key=lambda e: (pid_alive(e.pid), e.steerable, e.status_updated_at or 0.0),
        reverse=True,
    )[0]


# The roster's words, most telling first, when several processes report.
_ROSTER_ORDER = ("waiting", "busy", "shell", "idle")


def claude_activity(entries: list[RosterEntry], recap: Recap, agents_active: int) -> Activity:
    """Claude Code's raw facts about one conversation: its roster entries,
    its transcript's tail and its subagents."""
    live = [e for e in entries if pid_alive(e.pid)]
    statuses = {e.status for e in live}
    waiting = next((e for e in live if e.waiting_for), None)
    newest = max(live, key=lambda e: e.status_updated_at or 0, default=None)
    return Activity(
        alive=bool(live),
        prompted=recap.exists,
        turn=recap.turn,
        turn_marked_at=recap.turn_marked_at,
        prompt_waiting=waiting.waiting_for if waiting else None,
        asked=(any(t in _QUESTION_TOOLS for t in recap.recent_tools)
               or _looks_like_a_question(recap.last_text)),
        background_agents=agents_active,
        background_commands="shell" in statuses,
        roster_status=next((w for w in _ROSTER_ORDER if w in statuses), None),
        roster_since=(newest.status_updated_at or newest.started_at) if newest else None,
    )


def _assign_voice_names(sessions: list[SessionState]) -> None:
    """Give every conversation a name a person can say and hear.

    Collisions measured on the live machine: `hammer` had two conversations in
    ONE directory (so the folder cannot disambiguate) and `chitauri` had three
    across TWO directories (so the folder can). Roster suffixes like `-4b` are
    never used: they are neither sayable nor hearable.
    """
    by_project: dict[str, list[SessionState]] = {}
    for s in sessions:
        by_project.setdefault(s.project, []).append(s)

    for project, group in by_project.items():
        if len(group) == 1:
            group[0].voice_name = project
            continue
        by_parent: dict[str, list[SessionState]] = {}
        for s in group:
            by_parent.setdefault(Path(s.cwd).parent.name, []).append(s)
        if len(by_parent) > 1:
            # The parent folder tells them apart — "chitauri in Desktop" vs
            # "chitauri in Projects". A folder may still hold several, so those
            # get the same fallback chain applied on top.
            for parent, sub in by_parent.items():
                base = f"{project} in {parent}"
                if len(sub) == 1:
                    sub[0].voice_name = base
                else:
                    _name_group(sub, base)
        else:
            _name_group(group, project)


def _name_group(group: list[SessionState], base: str) -> None:
    """Name several conversations that share a `base` (project, or "project
    in parent"). Tries, in order, the strongest distinguisher that actually
    distinguishes them: what they are ABOUT, then what they are DOING, and
    only as a last resort how OLD they are. Age was the original — and
    weakest — rule; a user was told "the newer one" / "the older one" with no
    hint of which conversation was which. Each rule applies to the WHOLE
    group at once: a group is never named half by topic and half by state,
    because that would make one half's names orphan the other half's
    convention when the user answers back.
    """
    if _name_by_topic(group, base):
        return
    if _name_by_state(group, base):
        return
    _name_by_age(group, base)


# Generic words that carry no topic of their own, stripped from a title
# before a short phrase is pulled from what's left.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "to", "for", "of", "in", "on", "with",
    "at", "by", "from", "is", "are", "this", "that", "it", "its", "your",
    "my", "our",
})


def _topic_words(project: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", project.lower()) if w}


def _topic_phrase(title: str, project: str) -> str | None:
    """A short (<=2 word), sayable phrase distilled from a conversation's
    title: generic stopwords and the project's own name are stripped out,
    and the last couple of remaining words are kept — the part of a title
    that tends to carry what's actually distinct about it ("...architecture
    and handoff" -> "architecture handoff"). None if nothing is left to
    distinguish it by.
    """
    tokens = [w for w in re.split(r"[^a-z0-9]+", title.lower()) if w]
    proj_words = _topic_words(project)
    kept = [w for w in tokens if w not in _TITLE_STOPWORDS and w not in proj_words]
    if not kept:
        return None
    return " ".join(kept[-2:])


def _name_by_topic(group: list[SessionState], base: str) -> bool:
    """Name every conversation in the group by what it's ABOUT.

    Succeeds only when EVERY member has a non-empty title AND the phrases
    pulled from those titles are pairwise distinct — a title missing on even
    one member, or two titles boiling down to the same phrase, means topic
    naming cannot tell the whole group apart, so nothing is assigned and the
    caller falls through to the next rule for the group as a whole.
    """
    phrases: dict[str, str] = {}
    for s in group:
        title = (s.title or "").strip()
        if not title:
            return False
        phrase = _topic_phrase(title, s.project)
        if not phrase:
            return False
        phrases[s.session_id] = phrase
    if len(set(phrases.values())) != len(phrases):
        return False
    for s in group:
        s.voice_name = f"{base}, the {phrases[s.session_id]} one"
    return True


# Short, sayable phrases for what a conversation is doing right now, used
# when topic naming can't tell a group apart (title missing or identical)
# but their states differ.
_STATE_PHRASES = {
    NEEDS_YOU: "that needs you",
    WORKING: "that's working",
    IDLE: "that's idle",
    BACKGROUND: "that's working in the background",
    GONE: "that's finished",
    FRESH: "that hasn't started",
    UNKNOWN: "in an unclear state",
}


def _name_by_state(group: list[SessionState], base: str) -> bool:
    """Name every conversation in the group by what it is DOING.

    Succeeds only when every member's state is pairwise distinct — two
    conversations both sitting idle are not told apart by state either, so
    the caller falls through to age for the group as a whole.
    """
    states = [s.state for s in group]
    if len(set(states)) != len(states):
        return False
    for s in group:
        phrase = _STATE_PHRASES.get(s.state, "in an unclear state")
        s.voice_name = f"the {base} {phrase}"
    return True


def _name_by_age(group: list[SessionState], base: str) -> None:
    """Two conversations in one folder: 'the newer hammer' / 'the older hammer'.
    Beyond two, fall back to ordinals, which are still sayable. The last
    resort of the naming chain — it carries no information about what a
    conversation IS, only how old it is."""
    ordered = sorted(group, key=lambda s: s.started or 0.0, reverse=True)
    if len(ordered) == 2:
        ordered[0].voice_name = f"the newer {base}"
        ordered[1].voice_name = f"the older {base}"
        return
    words = ("newest", "second", "third", "fourth", "fifth")
    for i, s in enumerate(ordered):
        s.voice_name = (f"the {words[i]} {base}" if i < len(words)
                        else f"{base} number {i + 1}")


@dataclass
class Snapshot:
    """Every conversation on this machine at one instant."""
    sessions: list[SessionState] = field(default_factory=list)
    taken_at: float = 0.0

    def by_id(self, session_id: str) -> SessionState | None:
        return next((s for s in self.sessions if s.session_id == session_id), None)

    def excluding(self, session_ids) -> "Snapshot":
        """This snapshot without those conversations, RE-DERIVED.

        For JARVIS's own `claude -p` runs, which register in the roster like
        anything else (measured 2026-09-04: `entrypoint: "sdk-cli"`,
        `kind: "interactive"`). Dropping the rows is not enough on its own,
        because the two things computed ACROSS a project — the voice name and
        the "main" badge — were computed with the runs still in. A user with
        one conversation in a project a run had also touched was told about
        "the newer" and "the older" one, and then only one of them survived
        the filter.

        The same snapshot object comes back when nothing is excluded, so the
        common case costs nothing.
        """
        ids = set(session_ids)
        if not ids:
            return self
        kept = [s for s in self.sessions if s.session_id not in ids]
        if len(kept) == len(self.sessions):
            return self
        _assign_voice_names(kept)
        _mark_primary(kept)
        kept.sort(key=lambda s: (s.project, s.voice_name))
        return Snapshot(sessions=kept, taken_at=self.taken_at)

    def named(self, official: dict[str, str]) -> "Snapshot":
        """This snapshot with each session in `official` (session id -> name)
        going by that name instead of the one `_assign_voice_names` derived.

        The names the user has set are the official ones: the voice name is
        what JARVIS says, what `resolve` matches and what the agents list
        shows, so replacing it here makes all of them use the official name.
        Sessions are copied, never changed in place: the watcher's own
        snapshot is shared."""
        if not official or not any(s.session_id in official for s in self.sessions):
            return self
        sessions = [replace(s, voice_name=official[s.session_id])
                    if s.session_id in official else s for s in self.sessions]
        return Snapshot(sessions=sessions, taken_at=self.taken_at)

    def by_project(self) -> dict[str, list[SessionState]]:
        out: dict[str, list[SessionState]] = {}
        for s in self.sessions:
            out.setdefault(s.project, []).append(s)
        return out

    def needing_you(self) -> list[SessionState]:
        """Waiting conversations, most-recently-waiting first.

        Ordered by `since` (when the CURRENT STATE began) — NOT `started`
        (when the conversation began). Those two fields differ by up to 102
        hours on the live roster; sorting by `started` here would surface a
        conversation that has sat waiting for days ahead of one that started
        waiting a minute ago.
        """
        return sorted(
            (s for s in self.sessions if s.state == NEEDS_YOU),
            key=lambda s: s.since if s.since is not None else float("-inf"),
            reverse=True)


def _claude_sessions(entries: list[RosterEntry] | None, roots: list[Path] | None,
                     at: float) -> list[SessionState]:
    """Claude Code's live conversations, out of its roster and transcripts."""
    if entries is None:
        entries = read_roster(roots)
    # JARVIS's own brain registers in the roster like any Claude Code
    # session; it is never one of the user's conversations. Filtered here
    # (not in `read_roster`, whose contract is EVERY well-formed entry) so
    # every caller of `build_snapshot` gets user-facing conversations only.
    entries = [e for e in entries if not _is_own_brain(e)]

    grouped: dict[str, list[RosterEntry]] = {}
    for e in entries:
        grouped.setdefault(e.session_id, []).append(e)

    sessions: list[SessionState] = []
    for session_id, group in grouped.items():
        primary = _pick_primary(group)
        # EVERY root the conversation registered in, not just the primary's.
        # `_pick_primary` chooses by liveness and recency and knows nothing
        # about which root holds the transcript, so a session registered in
        # both roots with its file under the second one read as `fresh` —
        # "not started" — which is the one state JARVIS never announces.
        roots_here = _roots_of(group, primary)
        recap = _first_recap(roots_here, primary.cwd, session_id)
        agents_seen, agents_active, agents_capped = count_agents(
            roots_here, primary.cwd, session_id, at)
        status = session_status(claude_activity(group, recap, agents_active))
        live = [e for e in group if pid_alive(e.pid)]
        steerable_entry = next((e for e in live if e.steerable), None)
        sessions.append(SessionState(
            session_id=session_id,
            cwd=primary.cwd,
            project=project_name(primary.cwd),
            state=status.state,
            pids=[e.pid for e in group],
            primary_pid=primary.pid,
            roster_name=primary.name,
            needs=status.needs,
            title=recap.title,
            last_prompt=recap.last_prompt,
            last_text=recap.last_text,
            recent_tools=recap.recent_tools,
            started=(primary.started_at or primary.status_updated_at),
            since=status.since or primary.status_updated_at or primary.started_at,
            origin=_origin(primary),
            steerable=steerable_entry is not None,
            socket_path=steerable_entry.socket_path if steerable_entry else None,
            agents_seen=agents_seen,
            agents_active=agents_active,
            agents_capped=agents_capped,
            between_turns=status.between_turns,
        ))

    return sessions


class ClaudeSessions:
    """Claude Code as a session provider (session_provider.py): the roster
    under each config root, the transcripts, and each session's inbox
    socket."""

    name = PROVIDER
    can_answer_dialogs = True         # dialog.py, by keystroke in its Terminal tab
    can_interrupt = True              # its inbox takes priority "now"

    def __init__(self, roots: list[Path] | None = None,
                 entries: list[RosterEntry] | None = None):
        self.roots = roots
        self.entries = entries

    def discover(self, now: float) -> list[SessionState]:
        return _claude_sessions(self.entries, self.roots, now)

    def last_reply(self, session: SessionState) -> str | None:
        return read_last_reply(session.cwd, session.session_id, self.roots)

    def transcript(self, session: SessionState, before: int | None = None,
                   limit: int = TRANSCRIPT_PAGE) -> list[TranscriptEntry]:
        return read_transcript(session.cwd, session.session_id, self.roots, before, limit)

    def send(self, session: SessionState, text: str, interrupt: bool = False) -> str:
        from jarvis import session_steer
        return session_steer.post_to_session(session.socket_path, text, interrupt=interrupt)

    def track(self, session: SessionState, message) -> Tracking:
        for root in (self.roots if self.roots is not None else config_roots()):
            path = transcript_path(root, session.cwd, session.session_id)
            if path.exists():
                return track_from(objects_since(path, message.staged_at),
                                  message.body, message.staged_at)
        return Tracking()


def build_snapshot(entries: list[RosterEntry] | None = None,
                   roots: list[Path] | None = None,
                   now: float | None = None,
                   providers: list | None = None) -> Snapshot:
    """Read everything and reduce it to conversations.

    Processes are grouped by `sessionId`: measured live, 17 processes were 14
    conversations. The conversation is the unit JARVIS names and counts.

    Every session provider's conversations together (session_provider.py);
    Claude Code's alone, from `entries`/`roots`, when none are given.
    """
    import time as _time
    at = now if now is not None else _time.time()
    if providers is None:
        providers = [ClaudeSessions(roots, entries)]
    sessions: list[SessionState] = []
    for provider in providers:
        try:
            sessions.extend(provider.discover(at))
        except Exception as e:
            # One provider that cannot be read must not hide the others.
            import logging
            logging.getLogger("jarvis.sessions").warning(
                "sessions: %s could not be read: %s", provider.name, e)

    _assign_voice_names(sessions)
    _mark_primary(sessions)
    sessions.sort(key=lambda s: (s.project, s.voice_name))
    return Snapshot(sessions=sessions, taken_at=at)


# A conversation that has died is kept this long so a completion can still be
# announced after the process is gone.
GONE_RETENTION_SEC = 600.0
# Work shorter than this is a flicker, not a job worth announcing.
MIN_WORK_SEC = 30.0
# A message the provider accepted but that is not in the session's transcript
# this long after it was sent never reached the conversation: it is failed,
# and he is told the session hasn't picked it up. Both providers write the
# message into the transcript within a second of taking it when the session
# is idle, and when it is busy (Claude Code queues it, Codex holds it for the
# next turn) the queueing itself is written at once; a poll takes about 3.3
# seconds. A minute is many polls past any of that.
DELIVERY_TIMEOUT_SEC = 60.0
# A message he was told was not picked up is still looked for: a session can
# take it late, most often once he approves a permission prompt it was held
# behind, and then it is delivered and answered like any other. No message
# is looked for longer than this after it was sent; then it is failed, so
# what each poll looks for stays bounded.
MESSAGE_WATCH_SEC = 24 * 3600.0
# A session that has just answered a message is not also announced as
# finished or as having stopped on a question for this long: the answer is
# the one announcement. The roster's idle and the transcript's end of turn
# can land on different polls.
ANSWER_QUIET_SEC = 15.0


class SessionWatcher:
    """Polls the roster and publishes only the transitions worth speaking about.

    Startup is silent by construction: the first poll fills `_previous` without
    emitting, so JARVIS never greets you by reciting the fourteen sessions that
    were already open.
    """

    def __init__(self, roots: list[Path] | None = None, interval: float = 1.0,
                 providers: list | None = None, messages=None):
        self.roots = roots
        self.interval = interval
        # Every session provider watched (session_provider.py); Claude Code's
        # alone, under `roots`, when none are given.
        self.providers = providers if providers is not None else [ClaudeSessions(roots)]
        self.snapshot = Snapshot()
        self._previous: dict[str, str] = {}          # session_id -> state
        self._working_since: dict[str, float] = {}
        self._gone_at: dict[str, float] = {}
        self._gone_cache: dict[str, SessionState] = {}
        self._subscribers: list = []
        # Where the messages JARVIS sends are recorded (run_store: its
        # `open_messages` and `update_message`). Each poll looks for every
        # open one in its session's transcript (the provider's `track`) and
        # publishes `delivered`, `undelivered` and `answered`. None: no
        # messages are followed. They are read from the store every poll, so
        # a restarted watcher picks up where the last one stopped.
        self.messages = messages
        # session id -> when one of its messages was last answered.
        self._answered_at: dict[str, float] = {}
        # The poll and the server both send queued messages; one at a time.
        self._send_lock = threading.Lock()
        self._started = False
        self._task = None
        # Captured by start() so _publish() can tell whether it is running on
        # the loop's own thread. Kept as a pair (not just the loop) because
        # asyncio.AbstractEventLoop exposes no public "which thread owns me"
        # check.
        self._event_loop = None
        self._loop_thread = None

    def on_event(self, callback) -> None:
        self._subscribers.append(callback)

    def _publish(self, kind: str, session: SessionState, at: float,
                 message=None) -> None:
        """Notify every subscriber, always on the event-loop thread.

        Subscribers (e.g. server._on_session_event) commonly call
        asyncio.create_task, which requires a running event loop on the
        CALLING thread. Polling happens off that thread: _loop() drives
        poll_once() via asyncio.to_thread(...), so _publish() is invoked from
        a worker thread. Calling subscribers inline there made create_task
        raise RuntimeError("no running event loop") -- and because that
        exception was swallowed by the try/except below, the watcher looked
        healthy while /ws/sessions never delivered a single event. Route each
        callback onto the loop thread with call_soon_threadsafe whenever a
        loop was captured by start(); only call inline when no loop was
        captured, i.e. poll_once() driven directly (as the sync tests do).
        """
        import threading
        event = {"kind": kind, "at": at, "session": session_to_dict(session)}
        if message is not None:
            event["message"] = message_to_dict(message)

        def _invoke(cb) -> None:
            try:
                cb(event)
            except Exception:            # one bad subscriber must not stop the rest
                import logging
                logging.getLogger(__name__).warning(
                    "session event subscriber failed", exc_info=True)

        loop = self._event_loop
        on_loop_thread = loop is not None and threading.current_thread() is self._loop_thread
        for cb in list(self._subscribers):
            if loop is not None and not on_loop_thread:
                loop.call_soon_threadsafe(_invoke, cb)
            else:
                _invoke(cb)

    def poll_once(self, now: float | None = None) -> Snapshot:
        import time as _time
        now = now if now is not None else _time.time()
        snap = build_snapshot(now=now, providers=self.providers)

        # Carry recently-dead conversations forward so a completion can still
        # be announced after the process has exited.
        present = {s.session_id for s in snap.sessions}
        for sid, cached in list(self._gone_cache.items()):
            if sid in present:
                del self._gone_cache[sid]
                self._gone_at.pop(sid, None)
                continue
            if now - self._gone_at.get(sid, now) > GONE_RETENTION_SEC:
                del self._gone_cache[sid]
                self._gone_at.pop(sid, None)
                self._previous.pop(sid, None)
                self._working_since.pop(sid, None)
            else:
                snap.sessions.append(cached)
        for sid in list(self._previous):
            if sid not in present and sid not in self._gone_cache:
                prior = self.snapshot.by_id(sid)
                if prior is not None:
                    gone = SessionState(**{**prior.__dict__, "state": GONE,
                                           "steerable": False, "socket_path": None})
                    self._gone_cache[sid] = gone
                    self._gone_at[sid] = now
                    snap.sessions.append(gone)

        # Every session PUBLISHED must have been through naming together.
        # `build_snapshot()` already named the live sessions before any of
        # them were known here; gone-cache entries retained above (or just
        # created above) still carry the voice_name assigned in an EARLIER
        # poll, computed without knowledge of whoever is live now. A session
        # that returns from the gone-cache under a fresh session_id can
        # therefore collide with a live conversation's name unless naming is
        # redone across the full, final set.
        _assign_voice_names(snap.sessions)
        # Same reason naming is redone here: a conversation carried forward
        # from the gone-cache was ranked against whoever was live in an
        # EARLIER poll. Primary is a comparison, so it must be recomputed
        # over the final set or a dead session can still hold the crown.
        _mark_primary(snap.sessions)
        snap.sessions.sort(key=lambda s: (s.project, s.voice_name))

        first_poll = not self._started
        self.snapshot = snap                      # snapshot BEFORE notifying
        self._started = True

        # Messages before states: a session answering one is announced for
        # the answer, in place of the `needs_you` or `finished` it would
        # otherwise have earned.
        awaiting = self._track_messages(snap, now)
        self.send_queued(snap, now)

        for s in snap.sessions:
            was = self._previous.get(s.session_id)
            if s.state == WORKING and was != WORKING:
                self._working_since[s.session_id] = now

            answering = (s.session_id in awaiting
                         or now - self._answered_at.get(s.session_id, float("-inf"))
                         < ANSWER_QUIET_SEC)
            if not first_poll and s.announceable:
                # A permission prompt or dialog mid-turn is still announced:
                # the answer cannot come until he deals with it.
                if s.state == NEEDS_YOU and was != NEEDS_YOU and not (answering and not s.needs):
                    self._publish("needs_you", s, now)
                elif answering:
                    pass
                elif was == WORKING and s.state in (IDLE, BACKGROUND, GONE, UNKNOWN):
                    # A session that exits while its roster entry is still
                    # being torn down can land on `unknown` (no status field
                    # at all) rather than cleanly on `gone` — treat that the
                    # same as `idle`/`gone` for the "finished" announcement,
                    # or a session that exits this way is never reported.
                    started = self._working_since.get(s.session_id)
                    if started is not None and now - started >= MIN_WORK_SEC:
                        self._publish("finished", s, now)
            self._previous[s.session_id] = s.state

        return snap

    def send_queued(self, snap: Snapshot | None = None, now: float | None = None) -> list:
        """Send each session's oldest queued message once that session is
        free (`between_turns`, from session_status), and nothing JARVIS sent it still
        open. One message per turn, so two can never merge into one turn
        and lose their own replies. Records the outcome (the store first,
        then a `sent` or `send_failed` event) and returns the messages it
        settled. Never raises."""
        import logging
        import time as _time
        from jarvis.session_provider import for_session
        log = logging.getLogger(__name__)
        if self.messages is None:
            return []
        snap = snap if snap is not None else self.snapshot
        now = now if now is not None else _time.time()
        settled = []
        with self._send_lock:
            try:
                queued = self.messages.queued_messages()
                busy = {m.session_id for m in self.messages.open_messages()}
            except Exception:
                log.warning("queued messages could not be read", exc_info=True)
                return []
            for m in queued:
                if m.session_id in busy:
                    continue
                busy.add(m.session_id)          # this one waits behind it, if not sent
                session = snap.by_id(m.session_id)
                fields = None
                if session is None or session.state == GONE:
                    fields = {"status": "failed",
                              "failure": "its session ended before it was its turn"}
                elif now - m.staged_at > MESSAGE_WATCH_SEC:
                    fields = {"status": "failed",
                              "failure": "its session was not free within a day"}
                elif session.between_turns:
                    provider = next((p for p in self.providers if p.name == m.provider), None) \
                        or for_session(session, self.providers)
                    outcome = provider.send(session, m.text) if provider is not None else "failed"
                    fields = ({"status": "sent", "sent_at": now} if outcome == "sent"
                              else {"status": "failed", "failure": outcome})
                if fields is None:
                    continue
                try:
                    self.messages.update_message(m.id, **fields)   # the store BEFORE the event
                except Exception:
                    log.warning("message %s could not be recorded", m.id, exc_info=True)
                    continue
                m = replace(m, **fields)
                settled.append(m)
                if session is None:
                    session = SessionState(session_id=m.session_id, cwd="", project=m.project,
                                           state=GONE, voice_name=m.agent_name,
                                           provider=m.provider)
                self._publish("sent" if fields["status"] == "sent" else "send_failed",
                              session, now, message=m)
        return settled

    def _track_messages(self, snap: Snapshot, now: float) -> set[str]:
        """Follow every open message in its session's transcript, record what
        has changed (the store first, then the event), and return the
        sessions still working on one. Never raises: a store or transcript
        that cannot be read now is read again next poll."""
        import logging
        log = logging.getLogger(__name__)
        if self.messages is None:
            return set()
        try:
            open_now = self.messages.open_messages()
        except Exception:
            log.warning("open messages could not be read", exc_info=True)
            return set()
        awaiting: set[str] = set()
        for m in open_now:
            try:
                kind, session, m = self._follow(m, snap, now)
            except Exception:
                log.warning("message %s could not be followed", m.id, exc_info=True)
                awaiting.add(m.session_id)
                continue
            # In its conversation and not yet answered. Merely sent is not
            # enough: one that never arrives must not silence the session.
            if m.is_open and str(m.status) not in ("sent", "undelivered"):
                awaiting.add(m.session_id)
            if kind is not None:
                if kind == "answered":
                    self._answered_at[m.session_id] = now
                self._publish(kind, session, now, message=m)
        return awaiting

    def _follow(self, m, snap: Snapshot, now: float):
        """(the event to publish or None, the session, the message as now
        recorded) for one open message."""
        from jarvis.session_provider import for_session
        session = snap.by_id(m.session_id)
        provider = None
        if session is not None:
            provider = next((p for p in self.providers if p.name == m.provider), None) \
                or for_session(session, self.providers)
        t = provider.track(session, m) if provider is not None else Tracking()

        fields: dict = {}
        if t.delivered_at is not None and m.delivered_at is None:
            fields["delivered_at"] = t.delivered_at
        if t.started_at is not None and m.started_at is None:
            fields["started_at"] = t.started_at
        kind = None
        unseen = m.status in ("sent", "undelivered")
        if t.answered_at is not None:
            fields.update(status="answered", answered_at=t.answered_at, reply=t.reply)
            kind = "answered"
        elif t.started_at is not None:
            if m.status != "working":
                fields["status"] = "working"
                kind = "delivered" if unseen else None
        elif t.delivered_at is not None:
            if unseen:
                fields["status"] = "delivered"
                kind = "delivered"
        elif now - (m.sent_at or m.staged_at) > MESSAGE_WATCH_SEC:
            fields.update(status="failed", failure=(
                "not delivered within a day" if unseen else "not answered within a day"))
        elif m.status == "sent" and now - (m.sent_at or m.staged_at) > DELIVERY_TIMEOUT_SEC:
            fields["status"] = "undelivered"
            kind = "undelivered"
        if not fields:
            return None, session, m
        self.messages.update_message(m.id, **fields)      # the store BEFORE the event
        m = replace(m, **fields)
        if session is None:
            session = SessionState(session_id=m.session_id, cwd="", project=m.project,
                                   state=GONE, voice_name=m.agent_name, provider=m.provider)
        return kind, session, m

    @property
    def polled(self) -> bool:
        """Whether the first poll has finished. Until it has, `snapshot` is
        empty because nothing has been looked at yet, not because nothing is
        running."""
        return self._started

    async def start(self) -> None:
        import asyncio
        import threading
        if self._task is not None:
            return
        self._event_loop = asyncio.get_running_loop()
        self._loop_thread = threading.current_thread()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        import asyncio
        while True:
            try:
                await asyncio.to_thread(self.poll_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).warning("watch tick failed", exc_info=True)
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        import asyncio
        task, self._task = self._task, None
        # Clear unconditionally: a restarted watcher must never hold a loop
        # (or thread reference) belonging to an event loop that is gone.
        self._event_loop = None
        self._loop_thread = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass                          # expected: this is our own cancel()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "session watcher did not stop cleanly", exc_info=True)


def agent_name(s: SessionState) -> str:
    """The one place an agent's name comes from (the agents list, its page
    control and JARVIS's agent tools all read it from here). Today it is the
    voice name `_assign_voice_names` gives the session."""
    return s.voice_name


def message_to_dict(m) -> dict:
    """A message (run_store.Message) as an event carries it."""
    return {"id": m.id, "session_id": m.session_id, "provider": m.provider,
            "agent_name": m.agent_name, "project": m.project,
            "capture_id": m.capture_id, "text": m.text, "body": m.body,
            "status": str(m.status), "staged_at": m.staged_at,
            "sent_at": m.sent_at, "delivered_at": m.delivered_at,
            "started_at": m.started_at, "answered_at": m.answered_at,
            "reply": m.reply, "failure": m.failure}


def session_to_dict(s: SessionState) -> dict:
    """The JSON shape used by /api/sessions, /ws/sessions, and the tools."""
    return {
        "session_id": s.session_id,
        "voice_name": s.voice_name,
        "roster_name": s.roster_name,
        "project": s.project,
        "cwd": s.cwd,
        "state": s.state,
        "needs": s.needs,
        "needs_a_human_hand": s.needs_a_human_hand,
        "title": s.title,
        "summary": s.summary(),
        "last_prompt": s.last_prompt,
        "last_text": s.last_text,
        "recent_tools": list(s.recent_tools),
        # TWO stamps, never one. `started` is when the conversation began;
        # `since` is when its CURRENT STATE began. The largest gap measured
        # on the live roster was 102 HOURS, so anything that shows one where
        # it means the other is wrong by days and looks entirely plausible.
        # Either may be None: an absent stamp is an absence of evidence, and
        # 0 would render as 1970 and read as a measurement.
        "started": s.started,
        "since": s.since,
        "origin": s.origin,
        "steerable": s.steerable,
        "pids": list(s.pids),
        "primary_pid": s.primary_pid,
        # The verdict AND the reason. A bare boolean is a claim; the reason
        # is what lets a reader check it — including when it says nobody here
        # is clearly the main one.
        "primary": s.primary,
        "primary_reason": s.primary_reason,
        "agents_seen": s.agents_seen,
        "agents_active": s.agents_active,
        "agents_capped": s.agents_capped,
    }
