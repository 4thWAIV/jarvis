"""The local Folders contract: the words every part of the folder catalog
shares (roles, lifecycle, availability, reader kinds, candidate states),
the budgets every read is held to, and the one refusal they all raise.

The catalog's parts (folder_store.py, folder_policy.py, folder_readers.py,
folder_discovery.py, folder_service.py) and its two doors (folders_api.py
for the page, the folder tools in server.py for the brain) speak only these.
The page's copy of the shapes is frontend/src/folders.ts.

A folder is a place on this Mac JARVIS knows by a stable ID: its record and
its location live in the existing database (`data_paths.db_path()`), its
files stay where they are. Nothing here creates, moves, edits or deletes a
file. Projects follow in a later increment and will refer to these IDs.
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """What a folder holds, as a label. Never a permission: what may be
    read is the reader's policy (folder_policy.py), whatever the role."""
    DATA = "data"
    SOURCE = "source"
    HISTORY = "history"


class Lifecycle(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"          # unregistered: kept, unreadable, reactivated only on request


class Availability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"                  # nothing (or not a directory) at the path
    ACCESS_DENIED = "access_denied"      # the operating system refused this user
    UNAVAILABLE_NODE = "unavailable_node"  # it is another node's: never read from here
    UNKNOWN = "unknown"                  # not checked yet


class Reader(StrEnum):
    """How a folder's contents are read. Decided by folder_policy from where
    the folder is, never chosen by a caller."""
    FILES = "files"                      # an ordinary folder
    NOTES = "notes"                      # the brain's own journal, memory and project notes
    CLAUDE_HISTORY = "claude_history"    # one Claude Code projects/<cwd> history folder
    CODEX_HISTORY = "codex_history"      # Codex's sessions folder


class CandidateState(StrEnum):
    DISCOVERED = "discovered"
    IGNORED = "ignored"
    REGISTERED = "registered"


# The reader policy's version, stored on every access scope: a later policy
# can tell which scopes were granted under which rules.
POLICY_VERSION = 1

# ── budgets ──────────────────────────────────────────────────────────────
PAGE_ENTRIES = 100                  # directory entries per page
LIST_SECONDS = 2.0                  # wall clock for one directory listing
SCAN_MAX_ENTRIES = 10_000           # entries looked at to sort one page
TEXT_MAX_LINES = 200                # lines in one preview window
TEXT_MAX_BYTES = 64 * 1024          # bytes in one preview window
LINE_MAX_CHARS = 2_000              # one line, as shown
TRANSCRIPT_WINDOW_BYTES = 1024 * 1024   # raw transcript read for one window
SNIFF_BYTES = 4096                  # read to tell text from binary
CATALOG_PAGE = 100                  # folders or candidates per page
QUERY_MAX_CHARS = 200

# Discovery: how much history one pass reads, and how often passes run.
DISCOVERY_FILES_PER_PASS = 200
DISCOVERY_LIST_MAX = 20_000
DISCOVERY_SECONDS = 3.0
DISCOVERY_HEAD_BYTES = 64 * 1024
DISCOVERY_EVERY_SEC = 60.0
LIVE_WITHIN_SEC = 180.0             # an observation this recent from a live session is live


# ── the one refusal ──────────────────────────────────────────────────────

# code -> (HTTP status, what the page and the brain are told). The words are
# fixed per code: telling a caller precisely which rule refused a path is a
# probing oracle (see repo_read.sensitive_reason).
ERRORS: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "That request is not one the folder catalog understands."),
    "switched_off": (503, "The folder catalog is switched off on this JARVIS."),
    "relative_path": (422, "Give the folder as an absolute path or one starting with ~/."),
    "not_a_directory": (422, "That path is not a folder."),
    "unsupported": (422, "That cannot be done with this folder."),
    "not_found": (404, "There is no such folder in the catalog."),
    "candidate_not_found": (404, "There is no such discovered folder."),
    "missing": (404, "Nothing is at that location now."),
    "forbidden": (403, "JARVIS does not read that location."),
    "access_denied": (403, "macOS does not let JARVIS read that location."),
    "unavailable": (409, "That folder is not available on this Mac."),
    "removed": (409, "That folder was removed from the catalog."),
    "removed_exists": (409, "That location belongs to a folder removed from the catalog; "
                            "register it again to reactivate it."),
    "path_registered": (409, "Another folder in the catalog already has that location."),
    "identity_mismatch": (409, "That is a different directory from the one recorded; "
                               "confirm the replacement to reconnect."),
    "stale_revision": (409, "The folder changed since it was read; read it again."),
    "stale_cursor": (409, "The folder's location changed since that page; start again."),
    "idempotency_conflict": (409, "That request key was already used for a different request."),
    "reader_changed": (409, "That location would now be read differently; check it again."),
}


class FolderError(Exception):
    """A refusal with a stable code from ERRORS and any extra facts the
    page words it with (never credential material, never file content)."""

    def __init__(self, code: str, **extra):
        if code not in ERRORS:
            raise ValueError(f"unknown folder error code {code!r}")
        super().__init__(code)
        self.code = code
        self.extra = extra

    @property
    def status(self) -> int:
        return ERRORS[self.code][0]

    @property
    def message(self) -> str:
        return ERRORS[self.code][1]

    def to_json(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}
