"""What the brain's tool calls are doing, as a bounded in-memory feed.

Every tool call the brain makes reaches `POST /internal/tool`. Once the
request names a tool JARVIS has, the server records `started`; the call then
ends exactly once, `refused` (the digest gate, the acting-tool gates),
`failed` (the handler raised, or the request went away mid-call) or
`completed` (the handler returned). `completed` means only that the handler
returned: its text is never read to decide anything. A request turned away
before it names a real tool (a bad token, an unreadable body, an unknown
tool) is a diagnostic, not an activity.

An event holds the call's id, the tool's name and category, the stage, when
it happened and, on an ending, how long the call took. Never the arguments,
the result or any words. The record is memory only: at most `KEEP_EVENTS`,
the oldest dropped first, gone on a restart (`generation` says which
process the sequence numbers belong to). `GET /api/tools/activity?since=`
reads it. There is no `/ws/sessions` hint: every tool call would send
two, and the tests that check a tool sent nothing would all see them; a
reader asks again with the last `seq` it saw.

Nothing here changes what a tool does or what it answers.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Callable, Optional

# The categories the orb tool study groups JARVIS's tools into: Data and
# memory, Communication, Work and execution, UI guidance.
DATA = "data"
COMMUNICATION = "communication"
EXECUTION = "execution"
UI = "ui"
CATEGORY_NAMES = (DATA, COMMUNICATION, EXECUTION, UI)

# The one table: every tool `/internal/tool` can run, by category.
# tests/test_tool_activity.py checks it against TOOL_SPECS and TOOL_HANDLERS.
_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    # Memory, files, folders, history, the web, the screen and JARVIS's own
    # readings, voice and settings: what is read or changed in place, with no
    # one at the other end.
    DATA: (
        "recall", "remember", "project_note", "write_journal",
        "read_file", "search_repo", "repo_overview", "list_projects",
        "github_repo", "search_history", "read_history",
        "read_page", "look_at_page", "what_is_on_screen", "look_at_screen",
        "list_folders", "find_folder", "browse_folder", "read_folder_file",
        "register_folder", "unregister_folder", "folder_detail",
        "change_folder", "folder_candidates", "change_folder_candidate",
        "attachment_location", "make_attachment_local",
        "inspect_attachment", "list_bundle_members", "read_bundle_member",
        "look_at_bundle_page", "list_packages", "package_results",
        "diagnostics", "usage_status", "connections", "speech_models",
        "voice_input_settings", "attention_updates", "mark_updates_seen",
        "set_attention_settings", "update_status",
        "skip_reading", "stop_speaking", "quiet_microphone", "voice_turn",
        "set_language", "set_voice_input_settings",
    ),
    # Messages to and from agents and people, captures, and who they are.
    COMMUNICATION: (
        "session_detail", "read_session_reply", "list_messages",
        "read_message_reply", "session_messages", "read_session_message",
        "session_needs", "capture", "read_capture", "list_captures",
        "restore_capture", "clear_capture", "merge_captures",
        "steer_session", "answer_dialog", "enable_session_inbox",
        "list_agents", "rename_agent", "set_agent_active",
        "conversation_history",
        "draft_message", "attach_message_files", "request_message_review",
        "send_message", "set_message_task", "add_companion_files",
        "prepare_repair", "set_cited_files",
        "attachment_readiness", "review_checklist", "set_review_scope",
        "people", "people_messages", "read_people_thread",
        "mark_people_message_read", "set_people_thread", "change_people_draft",
        "team_status", "find_github_user", "change_team_member", "team_network",
    ),
    # Work started, inspected or stopped: runs, builds, commands, packages,
    # workflows, programs opened, files revealed, JARVIS's own update.
    EXECUTION: (
        "spawn_run", "run_status", "cancel_run", "retry_run",
        "start_build", "build_status", "review_document", "approve_document",
        "run_command", "create_project",
        "open_in_browser", "open_in_terminal", "open_in_editor",
        "open_package", "open_local_package", "run_package_command",
        "run_package_agent", "reveal_attachment",
        "workflow_status", "answer_workflow_decision", "dismiss_workflow",
        "install_speech_model", "update_jarvis",
    ),
    # The page shown or pointed at: the white attention path's tools.
    UI: (
        "ui_map", "ui_navigate", "ui_indicate", "ui_journey", "ui_clear",
        "show_on_screen", "show_bundle_member",
        "open_package_page", "tell_package_story",
    ),
}

CATEGORIES: dict[str, str] = {
    tool: category
    for category, tools in _BY_CATEGORY.items()
    for tool in tools
}

# A tool missing from the table still has its calls recorded, under this.
UNCATEGORISED = "other"

STARTED = "started"
REFUSED = "refused"
COMPLETED = "completed"
FAILED = "failed"
ENDINGS = (REFUSED, COMPLETED, FAILED)

# Events kept: enough for several turns of calls, small enough to read whole.
KEEP_EVENTS = 500


def category_of(tool: str) -> str:
    return CATEGORIES.get(tool, UNCATEGORISED)


class ToolActivity:
    """The feed. Safe from any thread."""

    def __init__(self, keep: int = KEEP_EVENTS,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.generation = uuid.uuid4().hex
        self._events: deque = deque(maxlen=keep)
        self._seq = 0
        self._open: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._monotonic = monotonic

    def start(self, tool: str) -> str:
        """Record a call's start; return its id for `end`."""
        call_id = uuid.uuid4().hex
        with self._lock:
            self._open[call_id] = (tool, self._monotonic())
            self._append(call_id, tool, STARTED, None)
        return call_id

    def end(self, call_id: str, stage: str) -> None:
        """Record how a started call ended. A second ending is ignored."""
        if stage not in ENDINGS:
            raise ValueError(f"not an ending: {stage}")
        with self._lock:
            opened = self._open.pop(call_id, None)
            if opened is None:
                return
            tool, began = opened
            duration_ms = round((self._monotonic() - began) * 1000, 1)
            self._append(call_id, tool, stage, duration_ms)

    def since(self, seq: int = 0) -> dict:
        """Every kept event after `seq`, oldest first, with the latest
        sequence number and the oldest one still kept (None when empty)."""
        with self._lock:
            events = [dict(e) for e in self._events if e["seq"] > seq]
            oldest = self._events[0]["seq"] if self._events else None
            latest = self._seq
        return {"generation": self.generation, "seq": latest,
                "oldest": oldest, "events": events}

    def _append(self, call_id: str, tool: str, stage: str,
                duration_ms: Optional[float]) -> None:
        self._seq += 1
        event = {"seq": self._seq, "call_id": call_id, "tool": tool,
                 "category": category_of(tool), "stage": stage,
                 "at": self._clock(), "duration_ms": duration_ms}
        self._events.append(event)


activity = ToolActivity()
