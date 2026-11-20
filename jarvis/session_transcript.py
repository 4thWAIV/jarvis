"""A session's own transcript, read whole and a line at a time: where it is
by its engine and id, and the tool calls, their results and what the
assistant said on each line.

The one reader of a session's transcript by id for what it did, shared by
the author proof (author_proof.py) and the package's gather step, which
collects the sending session's commands, their results and its final
messages (package_gather.sender_session; workflow.yaml gather
gather_jarvis_material). Claude Code's transcript is
`<config root>/projects/*/<session>.jsonl`, Codex's its thread's rollout;
no link is followed, and a long transcript (screenshots make one tens of
megabytes) is never held whole.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from jarvis import codex_sessions, session_message_sender, session_watch

ENGINES = ("claude", "codex")


def plain(path: Path) -> bool:
    """A regular file, reached through no link."""
    try:
        return path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
    except OSError:
        return False


def find(engine: str, session: str) -> Optional[Path]:
    """The session's own transcript file, found by its id, or None."""
    if engine == "claude":
        for root in session_watch.config_roots():
            projects = Path(root) / "projects"
            if not projects.is_dir():
                continue
            for path in sorted(projects.glob(f"*/{session}.jsonl")):
                if plain(path):
                    return path
        return None
    if engine != "codex":
        return None
    home = codex_sessions.codex_home()
    try:
        thread = codex_sessions.threads(home).get(session) or {}
    except Exception:
        thread = {}
    if thread.get("rollout_path") and plain(Path(thread["rollout_path"])):
        return Path(thread["rollout_path"])
    for path in sorted((home / "sessions").glob(f"*/*/*/rollout-*-{session}.jsonl")):
        payload = session_message_sender._first_object(path).get("payload") or {}
        if plain(path) and isinstance(payload, dict) and payload.get("id") == session:
            return path
    return None


def when(obj: dict) -> Optional[float]:
    """When the line was written, in epoch seconds, or None."""
    stamp = obj.get("timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return float(stamp)
    return session_watch.iso_seconds(stamp)


def text(value) -> str:
    """A result's or message's words, whatever shape the transcript gave."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text(v.get("text") if isinstance(v, dict) else v) for v in value)
    if isinstance(value, dict):
        return text(value.get("output") or value.get("content") or value.get("text") or "")
    return ""


def calls(obj: dict) -> Iterator[tuple[str, str, str]]:
    """(call id, tool name, its input as text) of each tool call the
    assistant makes on this line: Claude Code's tool_use, Codex's function
    or shell call."""
    if obj.get("type") == "assistant" and not obj.get("isSidechain"):
        content = (obj.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                yield (str(block["id"]), str(block.get("name") or ""),
                       json.dumps(block.get("input"), ensure_ascii=False))
    elif obj.get("type") == "response_item":
        p = obj.get("payload") or {}
        if isinstance(p, dict) and p.get("type") in ("function_call", "custom_tool_call",
                                                     "local_shell_call") and p.get("call_id"):
            raw = p.get("arguments", p.get("input", p.get("action")))
            yield (str(p["call_id"]), str(p.get("name") or p.get("type")),
                   raw if isinstance(raw, str) else json.dumps(raw))


def results(obj: dict) -> Iterator[tuple[str, str]]:
    """(call id, its output as text) of each tool result on this line."""
    if obj.get("type") == "user" and not obj.get("isSidechain"):
        content = (obj.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_result":
                yield str(block.get("tool_use_id") or ""), text(block.get("content"))
    elif obj.get("type") == "response_item":
        p = obj.get("payload") or {}
        if isinstance(p, dict) and p.get("type") in ("function_call_output",
                                                     "custom_tool_call_output"):
            yield str(p.get("call_id") or ""), text(p.get("output"))


def command_run(obj: dict) -> Optional[tuple[str, str]]:
    """(its command, all it printed) of a command Codex records as finished
    on this line (`item_completed` with a CommandExecution item), else None.
    A command that outlives its first wait hands its output back to a later
    poll of its session (`write_stdin`), so the tool call that started it
    never carries its output; this record holds the command and its whole
    output together."""
    if obj.get("type") != "event_msg":
        return None
    p = obj.get("payload") or {}
    item = p.get("item") if isinstance(p, dict) and p.get("type") == "item_completed" else None
    if not isinstance(item, dict) or item.get("type") != "CommandExecution":
        return None
    command = item.get("command")
    command = " ".join(map(str, command)) if isinstance(command, list) else text(command)
    return command, text(item.get("aggregated_output") or item.get("stdout") or "")


def said(obj: dict) -> Optional[tuple[str, str]]:
    """(role, words) of a line that is something said in the conversation
    (a prompt starting a turn, or the assistant's words), else None."""
    if obj.get("type") == "assistant" and not obj.get("isSidechain"):
        content = (obj.get("message") or {}).get("content")
        words = "\n".join(str(b.get("text") or "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text") \
            if isinstance(content, list) else ""
        return ("assistant", words.strip()) if words.strip() else None
    if obj.get("type") == "user" and not obj.get("isSidechain"):
        if session_watch._starts_a_turn(obj):
            words = session_watch._user_text(obj).strip()
            return ("user", words) if words else None
        return None
    return codex_sessions._message(obj)


def lines(path: Path, needles: tuple[bytes, ...] = ()) -> Iterator[dict]:
    """Each JSON object of the transcript, from its first line to its last;
    with `needles`, only those whose line holds one of them (the rest are
    passed over unparsed). Read one line at a time."""
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if needles and not any(n in raw for n in needles):
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return
