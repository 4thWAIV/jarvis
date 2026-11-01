"""The folder catalog's one service: what the page's routes (folders_api.py)
and the brain's folder tools (server.py) both call, so a path is validated,
a reader chosen and a read bounded in exactly one place.

  resolve / register      a path the user gave: absolute or `~/`, a real
                          directory this user can list, refused where the
                          policy refuses it, its reader shown before saving
                          and bound to the registration
  list_folders / detail   the catalog, and one folder with its sessions and
                          location history
  edit / unregister /     label and role at an expected revision; removal
  reconnect               from the catalog (never of a file); a new location
                          for the same ID, confirmed when it is a different
                          directory
  refresh / check_all     availability now: Available, Missing, Access
                          denied or Unavailable node
  entries / preview       one page of a directory, one window of a file,
                          through the folder's reader, with containment,
                          policy and the location revision checked again on
                          every request
  work_location           where a run may work in a folder: the same checks,
                          and an ordinary (files) folder only
  candidates / candidate_action / discover
                          what discovery found, and Ignore and Restore

Every method is blocking (database and filesystem) and is run off the
event loop by its callers. Every refusal is a folder_contract.FolderError.
The service publishes nothing; its callers hint after it returns.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from jarvis import folder_discovery, folder_policy, folder_readers
from jarvis.claude_provider import ClaudeTranscripts
from jarvis.codex_provider import CodexTranscripts
from jarvis.folder_contract import (Availability, CATALOG_PAGE, CandidateState, FolderError,
                                    Lifecycle, POLICY_VERSION, QUERY_MAX_CHARS, Reader, Role)
from jarvis.folder_policy import Policy
from jarvis.folder_store import Folder, FolderStore, NewLocation

NAME_MAX = 120
_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_MESSAGES = {Reader.CLAUDE_HISTORY: ClaudeTranscripts().message,
             Reader.CODEX_HISTORY: CodexTranscripts().message}


# ── cursors ──────────────────────────────────────────────────────────────

def _encode(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode(cursor: Optional[str]) -> Optional[dict]:
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, binascii.Error):
        raise FolderError("invalid_request") from None
    if not isinstance(payload, dict):
        raise FolderError("invalid_request")
    return payload


# ── paths ────────────────────────────────────────────────────────────────

def supplied_path(raw) -> Path:
    """A path as the user gave it: absolute, or `~` / `~/...` for his home.
    Anything relative has no base anyone agreed on, so it is refused."""
    text = str(raw or "").strip()
    if not text or "\x00" in text:
        raise FolderError("invalid_request")
    if text == "~" or text.startswith("~/"):
        return Path(os.path.expanduser(text))
    if not os.path.isabs(text):
        raise FolderError("relative_path")
    return Path(text)


def relative_path(raw) -> Path:
    """A path inside a folder, as the page or a tool gave it. `..`, an
    absolute path and a NUL are refused before anything is opened."""
    text = str(raw or "").strip()
    if "\x00" in text or "\\" in text or text.startswith(("/", "~")):
        raise FolderError("forbidden")
    text = text.rstrip("/")
    rel = Path(text) if text else Path()
    if rel.is_absolute() or any(p == ".." for p in rel.parts):
        raise FolderError("forbidden")
    return Path(*[p for p in rel.parts if p not in ("", ".")])


def availability_of(path: Path) -> tuple[Availability, Optional[str]]:
    """Whether a local location can be listed by this user now."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return Availability.MISSING, "ENOENT"
    except PermissionError:
        return Availability.ACCESS_DENIED, "EACCES"
    except OSError as e:
        return Availability.MISSING, f"E{e.errno}"
    if not stat.S_ISDIR(info.st_mode):
        return Availability.MISSING, "ENOTDIR"
    try:
        with os.scandir(path) as it:
            next(it, None)
    except PermissionError:
        return Availability.ACCESS_DENIED, "EACCES"
    except OSError as e:
        return Availability.MISSING, f"E{e.errno}"
    return Availability.AVAILABLE, None


@dataclass(frozen=True)
class Resolved:
    """A location the user gave, validated and with its reader."""
    supplied: str
    canonical: Path
    identity: str
    policy: Policy


def resolve_location(raw) -> Resolved:
    path = supplied_path(raw)
    canonical = Path(os.path.realpath(path))
    try:
        info = os.stat(canonical)
    except FileNotFoundError:
        raise FolderError("missing") from None
    except PermissionError:
        raise FolderError("access_denied") from None
    if not stat.S_ISDIR(info.st_mode):
        raise FolderError("not_a_directory")
    policy = folder_policy.policy_for(canonical)
    state, _code = availability_of(canonical)
    if state != Availability.AVAILABLE:
        raise FolderError("access_denied" if state == Availability.ACCESS_DENIED else "missing")
    return Resolved(str(path), canonical, f"{info.st_dev}:{info.st_ino}", policy)


# ── shapes ───────────────────────────────────────────────────────────────

def _clean_name(raw) -> Optional[str]:
    if raw is None:
        return None
    name = " ".join(str(raw).split())[:NAME_MAX]
    if not name:
        raise FolderError("invalid_request", field="display_name")
    return name


def _role(raw) -> Optional[str]:
    if raw is None:
        return None
    try:
        return Role(str(raw)).value
    except ValueError:
        raise FolderError("invalid_request", field="role") from None


def _policy_json(policy: Policy, enabled: bool = True) -> dict:
    return {"reader": policy.reader.value, "description": policy.describe(),
            "permitted_subtree": policy.permitted_subtree, "policy_version": POLICY_VERSION,
            "enabled": enabled}


class FolderService:
    """`node_id` is this node's identity (data_paths.node_id); `owner` the
    local owner's, where there is one; `live_sessions` the session
    watcher's current sessions; `wake` asks the discovery loop to look
    again soon."""

    def __init__(self, store: FolderStore, node_id: Callable[[], str],
                 owner: Callable[[], Optional[str]] = lambda: None,
                 live_sessions: Callable[[], Iterable] = lambda: (),
                 wake: Callable[[], None] = lambda: None):
        self.store, self.node_id, self.owner = store, node_id, owner
        self.live_sessions, self.wake = live_sessions, wake
        self.last_report: Optional[folder_discovery.Report] = None
        self.last_error: Optional[str] = None

    # ── shapes ──────────────────────────────────────────────────────────

    def folder_json(self, f: Folder) -> dict:
        loc, scope = f.location, f.scope
        return {
            "id": f.id, "display_name": f.display_name, "role": f.role,
            "lifecycle": f.lifecycle, "revision": f.revision, "owner_id": f.owner_id,
            "created_at": f.created_at, "updated_at": f.updated_at, "removed_at": f.removed_at,
            "location": {
                "id": loc.id, "node_id": loc.node_id, "local": loc.node_id == self.node_id(),
                "supplied_path": loc.supplied_path, "canonical_path": loc.canonical_path,
                "revision": loc.revision, "availability": loc.availability,
                "last_checked_at": loc.last_checked_at, "last_seen_at": loc.last_seen_at,
                "error_code": loc.error_code,
            },
            "access": {**_policy_json(Policy(Reader(scope.reader_kind), scope.permitted_subtree),
                                      scope.enabled),
                       "policy_version": scope.policy_version},
        }

    # ── registering ─────────────────────────────────────────────────────

    def resolve(self, path) -> dict:
        """What registering `path` would do, without doing it: where it
        resolves, the reader it would get, and the folder that already has
        it, if one does."""
        r = resolve_location(path)
        existing = self.store.find_location(self.node_id(), str(r.canonical), r.identity)
        return {"supplied_path": r.supplied, "canonical_path": str(r.canonical),
                "access": _policy_json(r.policy),
                "proposed_role": folder_policy.proposed_role(r.canonical, r.policy).value,
                "display_name": self._default_name(r),
                "existing": self.folder_json(existing) if existing else None}

    def _default_name(self, r: Resolved) -> str:
        if r.policy.reader == Reader.CODEX_HISTORY:
            return "Codex sessions"
        if r.policy.reader == Reader.CLAUDE_HISTORY:
            cwds = self.store.observed_cwds(self.node_id(), str(r.canonical))
            if len(cwds) == 1 and Path(cwds[0]).name:
                return f"{Path(cwds[0]).name} history"
        return r.canonical.name or str(r.canonical)

    def register(self, *, request_key, path, display_name=None, role=None, reader=None,
                 reactivate: bool = False) -> dict:
        """Register an existing directory: one identity per location however
        often it is asked for (`request_key` makes a retry the same request),
        and a removed folder's location only with `reactivate`. `reader`, if
        given, is the one the user was shown; a location that would now be
        read differently is refused rather than registered unseen."""
        key = str(request_key or "").strip()
        if not key or len(key) > 200:
            raise FolderError("invalid_request", field="request_key")
        r = resolve_location(path)
        if reader is not None and reader != r.policy.reader.value:
            raise FolderError("reader_changed", reader=r.policy.reader.value)
        name = _clean_name(display_name) or self._default_name(r)
        chosen_role = _role(role) or folder_policy.proposed_role(r.canonical, r.policy).value
        fingerprint = hashlib.sha256(json.dumps(
            [str(r.canonical), display_name and name, role and chosen_role,
             r.policy.reader.value, bool(reactivate)]).encode()).hexdigest()
        done = self.store.register(
            request_key=key, fingerprint=fingerprint, owner_id=self.owner(),
            display_name=name, role=chosen_role, reactivate=bool(reactivate),
            location=NewLocation(self.node_id(), r.supplied, str(r.canonical), r.identity,
                                 Availability.AVAILABLE.value, r.policy.reader.value,
                                 r.policy.permitted_subtree, POLICY_VERSION))
        return {"folder": self.folder_json(done.folder), "outcome": done.outcome}

    # ── the catalog ─────────────────────────────────────────────────────

    def list_folders(self, *, query="", role=None, availability=None, lifecycle="active",
                     cursor=None, limit=CATALOG_PAGE) -> dict:
        query = str(query or "")[:QUERY_MAX_CHARS].strip()
        limit = max(1, min(int(limit or CATALOG_PAGE), CATALOG_PAGE))
        if lifecycle not in (Lifecycle.ACTIVE, Lifecycle.REMOVED):
            raise FolderError("invalid_request", field="lifecycle")
        if availability and availability not in {a.value for a in Availability}:
            raise FolderError("invalid_request", field="availability")
        after = _decode(cursor)
        found = self.store.list(lifecycle=lifecycle, query=query, role=_role(role),
                                availability=availability,
                                after=(after["n"], after["i"]) if after else None,
                                limit=limit + 1)
        page = found[:limit]
        nxt = (_encode({"n": page[-1].display_name, "i": page[-1].id})
               if len(found) > limit else None)
        return {"folders": [self.folder_json(f) for f in page], "next_cursor": nxt,
                "counts": self.store.counts(), "node_id": self.node_id()}

    def _folder(self, folder_id) -> Folder:
        f = self.store.get(str(folder_id or ""))
        if f is None:
            raise FolderError("not_found")
        return f

    def detail(self, folder_id) -> dict:
        f = self._folder(folder_id)
        sessions = self.store.related_sessions(f.location.node_id, f.location.canonical_path)
        return {
            "folder": self.folder_json(f),
            "sessions": [{
                "provider": s.provider, "session_id": s.native_session_id,
                "profile_root": s.profile_root, "cwd": s.observed_cwd,
                "history_file": s.history_file, "evidence": s.evidence,
                "relation": s.relation, "associated": s.associated,
                "first_observed_at": s.first_observed_at, "observed_at": s.observed_at,
                "live": s.live, "agent_name": s.agent_name,
            } for s in sessions],
            "events": [{"id": e.id, "event": e.event, "prior": e.prior, "new": e.new,
                        "at": e.at} for e in self.store.events(f.id)],
            "coverage": self.coverage(),
        }

    def find(self, name: str, limit: int = 10) -> list[Folder]:
        """Active folders whose name or path holds `name`, for the brain:
        it chooses among them by ID."""
        return self.store.list(query=str(name or "")[:QUERY_MAX_CHARS].strip(), limit=limit)

    # ── changing one ────────────────────────────────────────────────────

    @staticmethod
    def _revision(raw) -> int:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise FolderError("invalid_request", field="expected_revision")
        return raw

    def edit(self, folder_id, *, expected_revision, display_name=None, role=None) -> dict:
        f = self.store.edit(str(folder_id), self._revision(expected_revision),
                            display_name=_clean_name(display_name), role=_role(role))
        return {"folder": self.folder_json(f)}

    def unregister(self, folder_id, *, expected_revision=None) -> dict:
        """Out of the catalog: its scope disabled, its record and its
        sessions' provenance kept. Nothing on disk is touched."""
        rev = None if expected_revision is None else self._revision(expected_revision)
        return {"folder": self.folder_json(self.store.unregister(str(folder_id), rev))}

    def reconnect(self, folder_id, *, expected_revision, path, replace: bool = False) -> dict:
        """The same folder ID at another location. A directory other than
        the one recorded (by filesystem identity) needs `replace`."""
        rev = self._revision(expected_revision)
        f = self._folder(folder_id)
        if f.lifecycle != Lifecycle.ACTIVE:
            raise FolderError("removed")
        r = resolve_location(path)
        if f.location.fs_identity and r.identity != f.location.fs_identity and not replace:
            raise FolderError("identity_mismatch", recorded_path=f.location.canonical_path,
                              new_path=str(r.canonical))
        done = self.store.reconnect(f.id, rev, NewLocation(
            self.node_id(), r.supplied, str(r.canonical), r.identity,
            Availability.AVAILABLE.value, r.policy.reader.value, r.policy.permitted_subtree,
            POLICY_VERSION))
        return {"folder": self.folder_json(done)}

    # ── availability ────────────────────────────────────────────────────

    def _check(self, f: Folder) -> bool:
        """Check one folder's location now and record it; True if its
        availability moved."""
        if f.location.node_id != self.node_id():
            state, code = Availability.UNAVAILABLE_NODE, None
        else:
            state, code = availability_of(Path(f.location.canonical_path))
        return self.store.record_availability(f.location.id, state.value, code)

    def refresh(self, folder_id) -> dict:
        """Check the folder's location now and ask discovery to look again
        soon; the detail as it is after the check."""
        f = self._folder(folder_id)
        self._check(f)
        self.wake()
        return {**self.detail(f.id), "discovery": "scheduled"}

    def check_all(self) -> int:
        moved = 0
        for f in self.store.located(self.node_id()):
            moved += self._check(f)
        return moved

    # ── reading ─────────────────────────────────────────────────────────

    def _readable(self, folder_id, location_revision) -> tuple[Folder, Policy, Path]:
        """The folder, its reader and its root, checked again now: active,
        enabled, on this node, at the location revision the caller read,
        there, and still given this reader by the policy."""
        f = self._folder(folder_id)
        if f.lifecycle != Lifecycle.ACTIVE or not f.scope.enabled:
            raise FolderError("removed")
        if f.location.node_id != self.node_id():
            raise FolderError("unavailable")
        if location_revision not in (None, "") and str(location_revision) != str(f.location.revision):
            raise FolderError("stale_cursor", location_revision=f.location.revision)
        root = Path(f.location.canonical_path)
        state, code = availability_of(root)
        if state != Availability(f.location.availability):
            self.store.record_availability(f.location.id, state.value, code)
        if state != Availability.AVAILABLE:
            raise FolderError("access_denied" if state == Availability.ACCESS_DENIED else "missing")
        policy = folder_policy.policy_for(root)
        if policy.reader.value != f.scope.reader_kind:
            raise FolderError("reader_changed", reader=policy.reader.value)
        return f, policy, root

    def work_location(self, folder_id) -> tuple[Folder, Path]:
        """The folder and its root, for work to run in (the brain's
        `spawn_run`): checked exactly as a read is, and only for an ordinary
        folder. A history or notes reader is read-only, so work never runs
        there."""
        f, policy, root = self._readable(folder_id, None)
        if policy.reader != Reader.FILES:
            raise FolderError("unsupported")
        return f, root

    @staticmethod
    def _check_visible(policy: Policy, root: Path, rel: Path, is_dir: bool) -> None:
        """Every component of `rel` must be one the reader shows."""
        parts = rel.parts
        for i in range(1, len(parts) + 1):
            last = i == len(parts)
            if not folder_policy.entry_visible(policy, root, Path(*parts[:i]),
                                               is_dir if last else True):
                raise FolderError("forbidden")

    def _cursor_for(self, cursor, f: Folder, rel: Path, kind: str) -> Optional[dict]:
        payload = _decode(cursor)
        if payload is None:
            return None
        if payload.get("r") != f.location.revision or payload.get("l") != f.location.id:
            raise FolderError("stale_cursor", location_revision=f.location.revision)
        if payload.get("p") != rel.as_posix() or payload.get("k") != kind:
            raise FolderError("invalid_request", field="cursor")
        return payload

    def _provenance(self, f: Folder, rel: Path) -> dict:
        return {"folder_id": f.id, "location_id": f.location.id,
                "location_revision": f.location.revision,
                "canonical_path": f.location.canonical_path, "path": rel.as_posix()}

    def entries(self, folder_id, *, path="", location_revision=None, cursor=None) -> dict:
        f, policy, root = self._readable(folder_id, location_revision)
        rel = relative_path(path)
        self._check_visible(policy, root, rel, True)
        kind = folder_readers.kind_of(root, rel)
        if kind == "link":
            raise FolderError("forbidden")          # never followed
        if kind != "dir":
            raise FolderError("not_a_directory")
        after = self._cursor_for(cursor, f, rel, "dir")
        listing = folder_readers.list_dir(
            root, rel, lambda r, d: folder_policy.entry_visible(policy, root, r, d),
            after=after["a"] if after else None)
        nxt = (_encode({"l": f.location.id, "r": f.location.revision, "p": rel.as_posix(),
                        "k": "dir", "a": listing.last_key}) if listing.more else None)
        return {
            **self._provenance(f, rel), "reader": policy.reader.value,
            "breadcrumbs": [{"name": f.display_name, "path": ""}]
                           + [{"name": p, "path": Path(*rel.parts[:i + 1]).as_posix()}
                              for i, p in enumerate(rel.parts)],
            "entries": [{"name": e.name, "path": (rel / e.name).as_posix(), "kind": e.kind,
                         "size": e.size, "modified": e.modified,
                         "transcript": e.kind == "file"
                                       and folder_policy.is_transcript(policy, rel / e.name)}
                        for e in listing.entries],
            "withheld": listing.withheld, "scanned": listing.scanned,
            "partial": listing.partial, "next_cursor": nxt,
        }

    def preview(self, folder_id, *, path, location_revision=None, cursor=None) -> dict:
        f, policy, root = self._readable(folder_id, location_revision)
        rel = relative_path(path)
        if not rel.parts:
            raise FolderError("not_a_directory")
        self._check_visible(policy, root, rel, False)
        kind = folder_readers.kind_of(root, rel)
        if kind == "link":
            raise FolderError("forbidden")          # never followed
        if kind != "file":
            raise FolderError("unsupported", kind=kind)
        transcript = folder_policy.is_transcript(policy, rel)
        at = self._cursor_for(cursor, f, rel, "file") or {}
        if transcript:
            window = folder_readers.read_transcript(root, rel, _MESSAGES[policy.reader],
                                                    offset=int(at.get("o") or 0))
        else:
            window = folder_readers.read_text(root, rel, offset=int(at.get("o") or 0),
                                              first_line=at.get("n", 1))
        nxt = None
        if window.next_offset is not None:
            nxt = _encode({"l": f.location.id, "r": f.location.revision, "p": rel.as_posix(),
                           "k": "file", "o": window.next_offset,
                           "n": (window.first_line + window.newlines)
                           if window.first_line is not None and not transcript else None})
        sessions = []
        if transcript:
            # The file's own name is its session's ID (Claude Code's
            # `<sessionId>.jsonl`, Codex's `rollout-<time>-<thread id>.jsonl`).
            found = _SESSION_ID.search(rel.stem)
            sessions = [{"provider": policy.provider,
                         "session_id": found.group(0) if found else None,
                         "transcript": rel.as_posix()}]
        return {
            **self._provenance(f, rel), "reader": policy.reader.value, "kind": window.kind,
            "size": window.size, "modified": window.modified, "lines": window.lines,
            "first_line": None if transcript else window.first_line,
            "offset": window.offset, "next_cursor": nxt, "partial": nxt is not None,
            "clipped_lines": window.clipped_lines, "skipped_records": window.skipped,
            "sessions": sessions,
        }

    # ── discovery ───────────────────────────────────────────────────────

    def candidates(self, *, state="discovered", query="", role=None, cursor=None,
                   limit=CATALOG_PAGE) -> dict:
        if state not in {s.value for s in CandidateState}:
            raise FolderError("invalid_request", field="state")
        limit = max(1, min(int(limit or CATALOG_PAGE), CATALOG_PAGE))
        after = _decode(cursor)
        found = self.store.candidates(state=state, query=str(query or "")[:QUERY_MAX_CHARS].strip(),
                                      role=_role(role),
                                      after=(after["t"], after["i"]) if after else None,
                                      limit=limit + 1)
        page = found[:limit]
        out = []
        for c in page:
            item = {"id": c.id, "path": c.path, "proposed_role": c.proposed_role,
                    "state": c.state, "providers": c.providers, "sessions": c.sessions,
                    "first_seen_at": c.first_seen_at, "last_observed_at": c.last_observed_at,
                    "registered_folder_id": c.registered_folder_id}
            state_now, _code = availability_of(Path(c.path))
            item["availability"] = state_now.value
            try:
                item["access"] = _policy_json(folder_policy.policy_for(Path(c.path)))
                item["refusal"] = None
            except FolderError as e:
                item["access"], item["refusal"] = None, e.code
            out.append(item)
        nxt = (_encode({"t": page[-1].last_observed_at, "i": page[-1].id})
               if len(found) > limit else None)
        return {"candidates": out, "next_cursor": nxt, "coverage": self.coverage()}

    def candidate_action(self, candidate_id, action) -> dict:
        if action not in ("ignore", "restore"):
            raise FolderError("invalid_request", field="action")
        c = self.store.set_candidate_state(str(candidate_id), ignored=action == "ignore")
        return {"candidate": {"id": c.id, "path": c.path, "state": c.state}}

    def coverage(self) -> dict:
        """How far discovery has got: each source's last pass."""
        progress = self.store.progress()
        return {
            "complete": bool(progress) and all(p.complete and not p.last_error for p in progress),
            "sources": [{"source": p.source, "listed": p.listed, "read": p.read,
                         "pending": p.pending, "complete": p.complete,
                         "last_run_at": p.last_run_at, "last_ok_at": p.last_ok_at,
                         "error": p.last_error} for p in progress],
        }

    def health(self, discovery: bool = True) -> dict:
        """What the Diagnostics tab judges the catalog by
        (subsystem_diagnostics.folder_catalog)."""
        return {"counts": self.store.counts(), "coverage": self.coverage(),
                "discovery": discovery, "last_error": self.last_error}

    def discover(self) -> tuple[int, int]:
        """One discovery pass and one availability pass: (candidates new or
        changed, locations whose availability moved)."""
        try:
            report = folder_discovery.discover(self.store, self.node_id(), self.live_sessions())
        except Exception as e:
            self.last_error = type(e).__name__
            raise
        self.last_report, self.last_error = report, None
        return report.changed, self.check_all()
