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
the result or any words. The record is the brain's one activity feed
(`brain_activity.py`), where these are the `tool` events beside the brain's
turns and communications: memory only, bounded, the oldest dropped first,
gone on a restart (`generation` says which process the sequence numbers
belong to). `GET /api/tools/activity?since=` reads the tool events alone,
in the shape below; `GET /api/brain/activity` reads them with the rest. There is no `/ws/sessions` hint: every tool call would send
two, and the tests that check a tool sent nothing would all see them; a
reader asks again with the last `seq` it saw.

Nothing here changes what a tool does or what it answers.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional

from jarvis import brain_activity

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
        "project_notes", "edit_project_note", "remove_project_note",
        "read_file", "search_repo", "repo_overview", "list_projects",
        "github_repo", "search_history", "read_history",
        "read_page", "look_at_page", "what_is_on_screen", "look_at_screen",
        "list_folders", "find_folder", "browse_folder", "read_folder_file",
        "register_folder", "unregister_folder", "folder_detail",
        "change_folder", "folder_candidates", "change_folder_candidate",
        "attachment_location", "make_attachment_local",
        "inspect_attachment", "list_bundle_members", "read_bundle_member",
        "look_at_bundle_page", "list_packages", "package_results",
        "language_document",
        "diagnostics", "usage_status", "usage_history", "connections", "speech_models",
        "voice_input_settings", "attention_updates", "mark_updates_seen",
        "set_attention_settings", "update_status",
        "skip_reading", "stop_speaking", "quiet_microphone", "voice_turn", "set_motion",
        "mute_speech", "turn_audio_off",
        "set_language", "set_voice_input_settings", "play_cue",
        "knowledge_status", "knowledge_search", "knowledge_entity", "knowledge_proposals",
        "knowledge_topics", "link_knowledge", "change_topic", "reindex_knowledge",
        "knowledge_indexes", "change_knowledge_index", "knowledge_seeds", "change_knowledge_seed",
        "work_context", "embedding_model", "install_embedding_model",
        "projects", "project_detail", "project_candidates", "project_history", "works", "work_detail",
        "change_project", "change_project_folder", "change_project_candidate", "change_work",
        "change_accomplishment",
    ),
    # Messages to and from agents and people, captures, and who they are.
    COMMUNICATION: (
        "session_detail", "read_session_reply", "list_messages",
        "read_message_reply", "session_messages", "read_session_message",
        "read_aloud", "reply",
        "session_needs", "capture", "read_capture", "list_captures",
        "restore_capture", "clear_capture", "merge_captures",
        "steer_session", "answer_dialog", "enable_session_inbox",
        "list_workers", "list_agents", "rename_agent", "set_agent_active", "agent_keys",
        "revoke_agent_key",
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
        "work_status", "answer_work_decision", "review_work",
        "package_inbox", "open_inbox_package", "answer_package_decision",
        "sign_off_package", "close_inbox_package",
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

# Events kept by a feed of its own (a test's); the module's calls go into
# the brain's one feed, which keeps `brain_activity.KEEP_EVENTS`.
KEEP_EVENTS = 500

# What /api/tools/activity shows of each tool event.
TOOL_FIELDS = ("seq", "call_id", "tool", "category", "stage", "at", "duration_ms")


def category_of(tool: str) -> str:
    return CATEGORIES.get(tool, UNCATEGORISED)


class ToolActivity:
    """The tool calls, recorded into an activity feed. Safe from any thread."""

    def __init__(self, keep: int = KEEP_EVENTS,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 feed: Optional[brain_activity.ActivityFeed] = None):
        self.feed = feed or brain_activity.ActivityFeed(keep, clock, monotonic)
        self._open: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def generation(self) -> str:
        return self.feed.generation

    def start(self, tool: str) -> str:
        """Record a call's start; return its id for `end`."""
        call_id = uuid.uuid4().hex
        with self._lock:
            self._open[call_id] = (tool, self.feed.monotonic())
        self._record(call_id, tool, STARTED, None)
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
        duration_ms = round((self.feed.monotonic() - began) * 1000, 1)
        self._record(call_id, tool, stage, duration_ms)

    def since(self, seq: int = 0) -> dict:
        """Every kept tool event after `seq`, oldest first, with the latest
        sequence number and the oldest one the feed still keeps (None when
        empty). Other kinds share the numbers, so a reader may see gaps."""
        snapshot = self.feed.since(seq, kinds=(brain_activity.TOOL,))
        body = {key: snapshot[key] for key in ("generation", "seq", "oldest", "events")}
        body["events"] = [{k: e[k] for k in TOOL_FIELDS} for e in body["events"]]
        return body

    def _record(self, call_id: str, tool: str, stage: str,
                duration_ms: Optional[float]) -> None:
        self.feed.record(brain_activity.TOOL, stage, call_id=call_id, tool=tool,
                         category=category_of(tool), duration_ms=duration_ms)


activity = ToolActivity(feed=brain_activity.feed)
