"""The folder catalog's one durable record: documents in the engine that
holds JARVIS's records (`records.py`; round 3 of
docs/planning/knowledge-engine/plan-uds.md, "JARVIS's records become
documents, redesigned").

  /folders/<folder id>          one folder (a `folder` item: its path has no
                                suffix, and its files' Content items are the
                                ones below it, `/folders/<id>/<rel>`): its
                                label, role, lifecycle and revision; its
                                locations, each with its access scopes (one
                                current location: a reconnect retires it and
                                adds the next location revision); the
                                registration request keys that named it; and
                                in each version the location events it
                                records (`events`), so its history is its
                                versions
  /proposals/folders/<id>.proposal
                                a path discovery found, by node and canonical
                                path: discovered, ignored or registered, with
                                which providers name it and how many sessions
  /proposals/folder-sessions/<id>.proposal
                                one provider session as discovery saw it, the
                                evidence for a candidate: its cwd, its
                                history folder, kept after the session ends
  /folders/discovery-history/<history folder>
                                the transcripts discovery has read in one
                                history folder: -1 once a file's cwd was found
                                (never read again), else the size it had
  /settings/folder-discovery.settings
                                each discovery source's progress
  /settings/folder-catalog.settings
                                the last location event and observation ids
                                given (new ones count on from them)

Every change is one write of the documents it touches (`records.atomic`),
with the revision moved inside it. This module reads no transcript and
checks no permission: discovery hands it what it observed
(folder_discovery.py), the service decides what may be registered and read
(folder_service.py, folder_policy.py). It publishes nothing; the caller
hints after it returns, so a hint never runs ahead of a write. Its writes
are made by one process (the server) and serialized by `_LOCK`, so a
discovery pass can read every candidate and observation in one scan and
write them in batches the engine takes.

`MAPPINGS` moves the tables this record replaced (`folders`,
`folder_locations`, `folder_access_scopes`, `folder_candidates`,
`folder_observations`, `folder_location_events`, `folder_registrations`,
`folder_discovery`, `folder_history_files`) into these documents and back,
row for row (`knowledge_move`).
"""
from __future__ import annotations

import copy as _copy
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from jarvis import records
from jarvis.folder_contract import (Availability, CandidateState, FolderError, Lifecycle,
                                    LIVE_WITHIN_SEC)

SCHEMA_VERSION = 1
"""The record's shape version (the old `folder_schema` table's last value)."""

FOLDERS = "/folders"
HISTORY = "/folders/discovery-history"
CANDIDATES = "/proposals/folders"
SESSIONS = "/proposals/folder-sessions"
CATALOG = "/settings/folder-catalog.settings"
DISCOVERY = "/settings/folder-discovery.settings"
CATALOG_AREA = "folder-catalog"         # each settings document says whose it is (`area`)
DISCOVERY_AREA = "folder-discovery"
BATCH = 900                     # documents a write carries (the engine takes 1000)
_LIVE = [["lifecycle", "exists", True]]       # a folder document (Content items have none)
_LOCK = threading.RLock()


def _no_suffix(value: str) -> str:
    """`records.segment`, which encodes every `.`, so the name has no
    suffix and stays a folder item's."""
    return records.segment(value)


def folder_path(folder_id: str) -> str:
    return f"{FOLDERS}/{_no_suffix(folder_id)}"


def candidate_path(candidate_id: str) -> str:
    return f"{CANDIDATES}/{records.segment(candidate_id)}.proposal"


def session_path(observation_id: int) -> str:
    return f"{SESSIONS}/{int(observation_id)}.proposal"


def history_path(history_dir: str) -> str:
    return f"{HISTORY}/{_no_suffix(history_dir)}"


# ── records ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Location:
    id: str
    node_id: str
    supplied_path: str
    canonical_path: str
    fs_identity: Optional[str]
    revision: int
    availability: str
    last_checked_at: Optional[float]
    last_seen_at: Optional[float]
    error_code: Optional[str]


@dataclass(frozen=True)
class Scope:
    id: str
    reader_kind: str
    permitted_subtree: str
    policy_version: int
    enabled: bool


@dataclass(frozen=True)
class Folder:
    id: str
    owner_id: Optional[str]
    display_name: str
    role: str
    lifecycle: str
    revision: int
    created_at: float
    updated_at: float
    removed_at: Optional[float]
    location: Location
    scope: Scope


@dataclass(frozen=True)
class NewLocation:
    """A location the service has validated, for a registration or a
    reconnect."""
    node_id: str
    supplied_path: str
    canonical_path: str
    fs_identity: Optional[str]
    availability: str
    reader_kind: str
    permitted_subtree: str
    policy_version: int


@dataclass(frozen=True)
class Registered:
    folder: Folder
    outcome: str        # "created", "existing", "reactivated" or "replayed"


@dataclass(frozen=True)
class Candidate:
    id: str
    node_id: str
    path: str
    proposed_role: str
    state: str
    first_seen_at: float
    last_observed_at: float
    registered_folder_id: Optional[str]
    providers: list[str]
    sessions: int


@dataclass(frozen=True)
class Observation:
    """One provider session as discovery saw it (folder_discovery.Seen,
    with its paths already turned into keys)."""
    provider: str
    profile_root: str
    native_session_id: str
    observed_cwd: str
    cwd_key: str
    history_dir: str = ""
    history_file: str = ""
    evidence: str = "transcript"
    live: bool = False
    associated: dict = field(default_factory=dict)
    cwd_role: str = "data"


@dataclass(frozen=True)
class RelatedSession:
    provider: str
    profile_root: str
    native_session_id: str
    observed_cwd: str
    history_file: str
    evidence: str
    associated: dict
    first_observed_at: float
    observed_at: float
    live: bool
    agent_name: Optional[str]
    relation: str       # "cwd", "inside" or "history"


@dataclass(frozen=True)
class LocationEvent:
    id: int
    event: str
    prior: dict
    new: dict
    at: float


@dataclass(frozen=True)
class Progress:
    source: str
    cursor: Optional[str]
    listed: int
    read: int
    pending: int
    complete: bool
    last_run_at: Optional[float]
    last_ok_at: Optional[float]
    last_error: Optional[str]


# ── documents to records ────────────────────────────────────────────────

def _current(doc: dict) -> Optional[dict]:
    """The folder's current location (the one not retired) with a scope."""
    for loc in doc.get("locations") or []:
        if loc.get("retired_at") is None and loc.get("scopes"):
            return loc
    return None


def _folder(doc: Optional[dict]) -> Optional[Folder]:
    if not doc:
        return None
    loc = _current(doc)
    if loc is None:
        return None
    s = loc["scopes"][0]
    return Folder(doc["id"], doc.get("owner_id"), doc["display_name"], doc["role"], doc["lifecycle"],
                  doc["revision"], doc["created_at"], doc["updated_at"], doc.get("removed_at"),
                  Location(loc["id"], loc["node_id"], loc["supplied_path"], loc["canonical_path"],
                           loc.get("fs_identity"), loc["revision"], loc["availability"],
                           loc.get("last_checked_at"), loc.get("last_seen_at"), loc.get("error_code")),
                  Scope(s["id"], s["reader_kind"], s["permitted_subtree"], s["policy_version"],
                        bool(s["enabled"])))


def _location_json(loc: Location) -> dict:
    """A location as the events record it: where, never anything read there."""
    return {"location_id": loc.id, "node_id": loc.node_id, "supplied_path": loc.supplied_path,
            "canonical_path": loc.canonical_path, "fs_identity": loc.fs_identity,
            "revision": loc.revision, "availability": loc.availability}


def _candidate(doc: dict) -> Candidate:
    return Candidate(doc["id"], doc["node_id"], doc["path_key"], doc["proposed_role"], doc["state"],
                     doc["first_seen_at"], doc["last_observed_at"], doc.get("registered_folder_id"),
                     list(doc.get("providers") or []), int(doc.get("sessions") or 0))


def _nocase(text: str) -> str:
    """SQLite's NOCASE: ASCII letters folded, nothing else."""
    return "".join(c.lower() if c.isascii() else c for c in str(text))


def _fingerprint(observations: Iterable[dict], cand_id: str) -> str:
    """Which providers and kinds of evidence name a candidate: one entry per
    distinct (provider, evidence, as cwd) among its observations."""
    seen = {(o["provider"], o["evidence"], o.get("cwd_candidate_id") == cand_id)
            for o in observations
            if o.get("cwd_candidate_id") == cand_id or o.get("history_candidate_id") == cand_id}
    return "|".join(sorted(f"{p}:{'cwd' if as_cwd else 'history'}" for p, _e, as_cwd in seen))


def _counted(observations: Iterable[dict], cand_id: str) -> tuple[list[str], int]:
    mine = [o for o in observations
            if o.get("cwd_candidate_id") == cand_id or o.get("history_candidate_id") == cand_id]
    return sorted({o["provider"] for o in mine}), len(mine)


def _changed(before: Optional[dict], after: dict) -> bool:
    return before is None or before != after


class FolderStore:
    """The catalog over the record engine. `path` is accepted for callers
    that gave a store its own SQLite file before; the records are the
    engine's (`records.uds`) whichever is given."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else None

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    # ── reading folders ─────────────────────────────────────────────────

    @staticmethod
    def _docs(unit: Optional[records.Unit] = None) -> list[dict]:
        found = (unit.scan(FOLDERS, parent=FOLDERS, types=["folder"], where=_LIVE) if unit is not None
                 else records.scan(FOLDERS, parent=FOLDERS, types=["folder"], where=_LIVE))
        return [r.data for r in found if "locations" in r.data]

    def _all(self) -> list[Folder]:
        return [f for f in (_folder(d) for d in self._docs()) if f is not None]

    def get(self, folder_id: str) -> Optional[Folder]:
        if not folder_id:
            return None
        rec = records.get(folder_path(folder_id))
        return _folder(rec.data) if rec is not None and rec.data.get("id") == folder_id else None

    def list(self, *, lifecycle: Optional[str] = Lifecycle.ACTIVE, query: str = "",
             role: Optional[str] = None, availability: Optional[str] = None,
             after: Optional[tuple[str, str]] = None, limit: int = 100) -> list[Folder]:
        """Folders by name then ID, after `after` ((name, id) of the last one
        of the previous page). `query` matches the name or the path."""
        q = _nocase(query)
        out = []
        for f in self._all():
            if lifecycle and f.lifecycle != lifecycle:
                continue
            if q and not any(q in _nocase(t) for t in (f.display_name, f.location.canonical_path,
                                                        f.location.supplied_path)):
                continue
            if role and f.role != role:
                continue
            if availability and f.location.availability != availability:
                continue
            if after:
                name, fid = _nocase(f.display_name), _nocase(after[0])
                if not (name > fid or (name == fid and f.id > after[1])):
                    continue
            out.append(f)
        out.sort(key=lambda f: (_nocase(f.display_name), f.id))
        return out[:max(0, int(limit))]

    def find_location(self, node_id: str, canonical_path: str,
                      fs_identity: Optional[str]) -> Optional[Folder]:
        """The folder, active or removed, whose current location on `node_id`
        is this path or, failing that, this same directory by filesystem
        identity (a validated alias: another spelling of one directory)."""
        return self._find_location(self._all(), node_id, canonical_path, fs_identity)

    @staticmethod
    def _find_location(folders: list[Folder], node_id, canonical_path, fs_identity) -> Optional[Folder]:
        def best(match) -> Optional[Folder]:
            found = [f for f in folders if f.location.node_id == node_id and match(f)]
            found.sort(key=lambda f: f.lifecycle != Lifecycle.ACTIVE)
            return found[0] if found else None
        hit = best(lambda f: f.location.canonical_path == canonical_path)
        if hit is None and fs_identity:
            hit = best(lambda f: f.location.fs_identity == fs_identity)
        return hit

    def located(self, node_id: str) -> list[Folder]:
        """Every active folder on `node_id`, for the availability pass."""
        return [f for f in self._all()
                if f.lifecycle == Lifecycle.ACTIVE and f.location.node_id == node_id]

    def counts(self) -> dict[str, int]:
        """Active folders by availability."""
        out: dict[str, int] = {}
        for f in self._all():
            if f.lifecycle == Lifecycle.ACTIVE:
                out[f.location.availability] = out.get(f.location.availability, 0) + 1
        return out

    # ── ids ─────────────────────────────────────────────────────────────

    @staticmethod
    def _catalog(unit: records.Unit) -> dict:
        rec = unit.get(CATALOG)
        data = dict(rec.data) if rec is not None else {}
        data.setdefault("last_event_id", 0)
        data.setdefault("last_observation_id", 0)
        data.setdefault("orphans", {})
        data["area"] = CATALOG_AREA
        data["events"] = []
        return data

    def _event(self, unit: records.Unit, catalog: dict, doc: dict, location_id: Optional[str],
               event: str, prior: dict, new: dict, now: float) -> None:
        catalog["last_event_id"] += 1
        doc.setdefault("events", []).append(
            {"id": catalog["last_event_id"], "location_id": location_id, "event": event,
             "prior": prior, "new": new, "at": float(now)})
        unit.put(CATALOG, catalog)

    @staticmethod
    def _put(unit: records.Unit, doc: dict, change: str, summary: str = "") -> None:
        unit.put(folder_path(doc["id"]), doc, change=change, summary=summary or doc["display_name"])

    # ── registering ─────────────────────────────────────────────────────

    def register(self, *, request_key: str, fingerprint: str, owner_id: Optional[str],
                 display_name: str, role: str, location: NewLocation, reactivate: bool,
                 now: Optional[float] = None) -> Registered:
        """Register one validated location, in one write:

          - a request key seen before returns that registration's folder if
            the request was the same, and is refused if it was not;
          - a location an active folder already has (by path, or the same
            directory by identity) returns that folder: one identity;
          - a location a removed folder has is refused unless `reactivate`,
            which brings that folder and its ID back with its scope again;
          - otherwise a new folder, location and scope.
        """
        now = float(time.time() if now is None else now)

        def run(unit: records.Unit) -> Registered:
            docs = self._docs(unit)
            for d in docs:
                for reg in d.get("registrations") or []:
                    if reg["request_key"] == request_key:
                        if reg["fingerprint"] != fingerprint:
                            raise FolderError("idempotency_conflict")
                        return Registered(_folder(d), "replayed")
            folders = [f for f in (_folder(d) for d in docs) if f is not None]
            existing = self._find_location(folders, location.node_id, location.canonical_path,
                                           location.fs_identity)
            catalog = self._catalog(unit)
            if existing is not None and existing.lifecycle == Lifecycle.ACTIVE:
                outcome = "existing"
                doc = next(d for d in docs if d["id"] == existing.id)
                doc["events"] = []
                change = "registration"
            elif existing is not None:
                if not reactivate:
                    raise FolderError("removed_exists", folder_id=existing.id)
                outcome = "reactivated"
                doc = next(d for d in docs if d["id"] == existing.id)
                doc["events"] = []
                self._reactivate(unit, catalog, doc, existing, location, now)
                change = "reactivated"
            else:
                outcome = "created"
                doc = {"id": str(uuid.uuid4()), "owner_id": owner_id, "display_name": display_name,
                       "role": role, "lifecycle": Lifecycle.ACTIVE.value, "revision": 1,
                       "created_at": now, "updated_at": now, "removed_at": None,
                       "locations": [], "registrations": [], "events": []}
                loc = self._new_location(doc, location, 1, now)
                self._event(unit, catalog, doc, loc["id"], "registered", {},
                            _location_json(_folder(doc).location), now)
                change = "registered"
            doc.setdefault("registrations", []).append(
                {"request_key": request_key, "fingerprint": fingerprint, "created_at": now})
            self._put(unit, doc, change)
            self._link_candidate(unit, location.node_id, location.canonical_path, doc["id"])
            return Registered(_folder(doc), outcome)

        with _LOCK:
            return records.atomic(run, change="registered", summary=display_name)

    def _reactivate(self, unit, catalog: dict, doc: dict, folder: Folder, location: NewLocation,
                    now: float) -> None:
        """A removed folder brought back at a location validated again, with
        the policy's scope for it now."""
        doc.update(lifecycle=Lifecycle.ACTIVE.value, removed_at=None, revision=doc["revision"] + 1,
                   updated_at=now)
        loc = _current(doc)
        loc.update(availability=location.availability, last_checked_at=now, last_seen_at=now,
                   error_code=None)
        scope = loc["scopes"][0]
        scope.update(reader_kind=location.reader_kind, permitted_subtree=location.permitted_subtree,
                     policy_version=location.policy_version, enabled=1, updated_at=now)
        self._event(unit, catalog, doc, folder.location.id, "reactivated",
                    {"lifecycle": Lifecycle.REMOVED.value},
                    {"lifecycle": Lifecycle.ACTIVE.value, "reader_kind": location.reader_kind}, now)

    @staticmethod
    def _new_location(doc: dict, location: NewLocation, revision: int, now: float) -> dict:
        loc = {"id": str(uuid.uuid4()), "node_id": location.node_id,
               "supplied_path": location.supplied_path, "canonical_path": location.canonical_path,
               "fs_identity": location.fs_identity, "revision": revision,
               "availability": location.availability, "last_checked_at": now, "last_seen_at": now,
               "error_code": None, "created_at": now, "retired_at": None,
               "scopes": [{"id": str(uuid.uuid4()), "reader_kind": location.reader_kind,
                           "permitted_subtree": location.permitted_subtree,
                           "policy_version": location.policy_version, "enabled": 1,
                           "created_at": now, "updated_at": now}]}
        doc.setdefault("locations", []).append(loc)
        return loc

    @staticmethod
    def _candidates_at(unit: records.Unit, node_id: str, path_key: str) -> list[records.Rec]:
        return [r for r in unit.scan(CANDIDATES, types=["proposal"], watch=False,
                                     where=[["node_id", "=", node_id]])
                if r.data.get("node_id") == node_id and r.data.get("path_key") == path_key]

    def _link_candidate(self, unit: records.Unit, node_id: str, path_key: str, folder_id: str) -> None:
        for r in self._candidates_at(unit, node_id, path_key):
            data = dict(r.data, state=CandidateState.REGISTERED.value, registered_folder_id=folder_id,
                        events=[])
            if data != r.data:
                unit.put(r.path, data, change="registered", summary=path_key[-200:])

    # ── changing one ────────────────────────────────────────────────────

    @staticmethod
    def _held(unit: records.Unit, folder_id: str,
              expected_revision: Optional[int]) -> tuple[dict, Folder]:
        rec = unit.get(folder_path(folder_id)) if folder_id else None
        folder = _folder(rec.data) if rec is not None and rec.data.get("id") == folder_id else None
        if folder is None:
            raise FolderError("not_found")
        if expected_revision is not None and expected_revision != folder.revision:
            raise FolderError("stale_revision", revision=folder.revision)
        doc = rec.data
        doc["events"] = []
        return doc, folder

    def edit(self, folder_id: str, expected_revision: int, *, display_name: Optional[str] = None,
             role: Optional[str] = None, now: Optional[float] = None) -> Folder:
        """Its label and role, if it is still at `expected_revision`."""
        now = float(time.time() if now is None else now)

        def run(unit: records.Unit) -> Folder:
            doc, folder = self._held(unit, folder_id, expected_revision)
            if folder.lifecycle != Lifecycle.ACTIVE:
                raise FolderError("removed")
            doc.update(display_name=display_name if display_name is not None else folder.display_name,
                       role=role if role is not None else folder.role,
                       revision=doc["revision"] + 1, updated_at=now)
            self._put(unit, doc, "edited")
            return _folder(doc)

        with _LOCK:
            return records.atomic(run, change="edited")

    def unregister(self, folder_id: str, expected_revision: Optional[int],
                   now: Optional[float] = None) -> Folder:
        """Removed from the catalog: kept, its scope disabled, its sessions'
        provenance kept. No file is touched."""
        now = float(time.time() if now is None else now)

        def run(unit: records.Unit) -> Folder:
            doc, folder = self._held(unit, folder_id, expected_revision)
            if folder.lifecycle == Lifecycle.REMOVED:
                return folder
            doc.update(lifecycle=Lifecycle.REMOVED.value, removed_at=now,
                       revision=doc["revision"] + 1, updated_at=now)
            scope = _current(doc)["scopes"][0]
            scope.update(enabled=0, updated_at=now)
            for r in unit.scan(CANDIDATES, types=["proposal"], watch=False,
                               where=[["registered_folder_id", "=", folder_id]]):
                if (r.data.get("registered_folder_id") == folder_id
                        and r.data.get("state") == CandidateState.REGISTERED):
                    unit.put(r.path, dict(r.data, state=CandidateState.DISCOVERED.value, events=[]),
                             change="unregistered", summary=r.data["path_key"][-200:])
            self._event(unit, self._catalog(unit), doc, folder.location.id, "unregistered",
                        {"lifecycle": Lifecycle.ACTIVE.value},
                        {"lifecycle": Lifecycle.REMOVED.value}, now)
            self._put(unit, doc, "unregistered")
            return _folder(doc)

        with _LOCK:
            return records.atomic(run, change="unregistered")

    def reconnect(self, folder_id: str, expected_revision: int, location: NewLocation,
                  now: Optional[float] = None) -> Folder:
        """The folder, same ID, at a new location the service validated and
        the caller confirmed: the old location retired, a new one with the
        next location revision (so a page of the old one is refused), and
        the policy's scope for the new one."""
        now = float(time.time() if now is None else now)

        def run(unit: records.Unit) -> Folder:
            doc, folder = self._held(unit, folder_id, expected_revision)
            if folder.lifecycle != Lifecycle.ACTIVE:
                raise FolderError("removed")
            others = [f for f in (_folder(d) for d in self._docs(unit)) if f is not None]
            other = self._find_location(others, location.node_id, location.canonical_path,
                                        location.fs_identity)
            if other is not None and other.id != folder_id:
                raise FolderError("path_registered", folder_id=other.id)
            old = _current(doc)
            old["retired_at"] = now
            old["scopes"][0].update(enabled=0, updated_at=now)
            new = self._new_location(doc, location, folder.location.revision + 1, now)
            doc.update(revision=doc["revision"] + 1, updated_at=now)
            new_loc = Location(new["id"], new["node_id"], new["supplied_path"], new["canonical_path"],
                               new["fs_identity"], new["revision"], new["availability"], now, now, None)
            self._event(unit, self._catalog(unit), doc, new["id"], "reconnected",
                        _location_json(folder.location), _location_json(new_loc), now)
            self._put(unit, doc, "reconnected")
            self._link_candidate(unit, location.node_id, location.canonical_path, folder_id)
            return _folder(doc)

        with _LOCK:
            return records.atomic(run, change="reconnected")

    def record_availability(self, location_id: str, availability: str,
                            error_code: Optional[str], now: Optional[float] = None) -> bool:
        """A check of one location: always its time, its time seen when it
        was there, and an event when its availability moved. True if it
        moved. Not a metadata change: the folder's revision stays."""
        now = float(time.time() if now is None else now)

        def run(unit: records.Unit) -> bool:
            for doc in self._docs(unit):
                loc = next((l for l in doc["locations"] if l["id"] == location_id), None)
                if loc is None:
                    continue
                doc["events"] = []
                before = loc["availability"]
                loc.update(availability=availability, error_code=error_code, last_checked_at=now)
                if availability == Availability.AVAILABLE:
                    loc["last_seen_at"] = now
                moved = before != availability
                if moved:
                    self._event(unit, self._catalog(unit), doc, location_id, "availability",
                                {"availability": before},
                                {"availability": availability, "error_code": error_code}, now)
                self._put(unit, doc, "availability" if moved else "checked", availability)
                return moved
            return False

        with _LOCK:
            return records.atomic(run, change="checked")

    def events(self, folder_id: str, limit: int = 20) -> list[LocationEvent]:
        found = []
        for v in records.history(folder_path(folder_id), fields=["events"]):
            found += (v.data or {}).get("events") or []
        found.sort(key=lambda e: e["id"], reverse=True)
        return [LocationEvent(e["id"], e["event"], dict(e.get("prior") or {}), dict(e.get("new") or {}),
                              e["at"]) for e in found[:max(0, int(limit))]]

    # ── discovery ───────────────────────────────────────────────────────

    def observe(self, node_id: str, seen: list[Observation], now: Optional[float] = None) -> int:
        """Record what discovery saw: each session's observation (made or
        brought up to date, never dropped), and a candidate for each path it
        names, its cwd and its history folder. An ignored candidate whose
        evidence changed is discovered again. Returns how many candidates
        are new or changed state. Written in batches of at most `BATCH`
        documents (the engine's limit on one write), under the store's lock."""
        now = float(time.time() if now is None else now)
        if not seen:
            return 0
        with _LOCK:
            return self._observe(node_id, seen, now)

    def _observe(self, node_id: str, seen: list[Observation], now: float) -> int:
        cands = {r.data["id"]: r.data for r in records.scan(CANDIDATES, types=["proposal"])}
        loaded_c = _copy.deepcopy(cands)
        by_key = {(c["node_id"], c["path_key"]): cid for cid, c in cands.items()}
        obs = {r.data["id"]: r.data for r in records.scan(SESSIONS, types=["proposal"])}
        loaded_o = _copy.deepcopy(obs)
        obs_by_key = {(o["node_id"], o["provider"], o["profile_root"], o["native_session_id"]): oid
                      for oid, o in obs.items()}
        active = {(f.location.node_id, f.location.canonical_path): f.id for f in self._all()
                  if f.lifecycle == Lifecycle.ACTIVE}
        catalog_rec = records.get(CATALOG)
        catalog = dict(catalog_rec.data) if catalog_rec is not None else {}
        catalog.setdefault("last_event_id", 0)
        catalog.setdefault("last_observation_id", 0)
        catalog.setdefault("orphans", {})
        catalog["area"] = CATALOG_AREA
        last_obs = catalog["last_observation_id"]
        changed = 0

        def candidate(path_key: str, role: str) -> tuple[str, int]:
            cid = by_key.get((node_id, path_key))
            if cid is not None:
                cands[cid]["last_observed_at"] = now
                return cid, 0
            folder_id = active.get((node_id, path_key))
            cid = str(uuid.uuid4())
            cands[cid] = {"id": cid, "node_id": node_id, "path_key": path_key, "proposed_role": role,
                          "state": (CandidateState.REGISTERED if folder_id
                                    else CandidateState.DISCOVERED).value,
                          "fingerprint": "", "ignored_fingerprint": None, "first_seen_at": now,
                          "last_observed_at": now, "registered_folder_id": folder_id,
                          "providers": [], "sessions": 0}
            by_key[(node_id, path_key)] = cid
            return cid, 1

        touched: set[str] = set()
        recount: set[str] = set()
        for o in seen:
            cwd_cand, new = candidate(o.cwd_key, o.cwd_role)
            changed += new
            hist_cand = None
            if o.history_dir:
                hist_cand, new = candidate(o.history_dir, "history")
                changed += new
            key = (node_id, o.provider, o.profile_root, o.native_session_id)
            oid = obs_by_key.get(key)
            associated = dict(o.associated)
            if oid is None:
                last_obs += 1
                oid = last_obs
                obs[oid] = {"id": oid, "node_id": node_id, "provider": o.provider,
                            "profile_root": o.profile_root, "native_session_id": o.native_session_id,
                            "observed_cwd": o.observed_cwd, "cwd_key": o.cwd_key,
                            "history_dir": o.history_dir, "history_file": o.history_file,
                            "evidence": o.evidence, "associated": associated,
                            "cwd_candidate_id": cwd_cand, "history_candidate_id": hist_cand,
                            "first_observed_at": now, "observed_at": now,
                            "last_live_at": now if o.live else None}
                obs_by_key[key] = oid
            else:
                row = obs[oid]
                recount.update(c for c in (row.get("cwd_candidate_id"), row.get("history_candidate_id")) if c)
                # A record's evidence is kept over a sighting's: that it is
                # live says nothing new about where it ran.
                keep = o.evidence in ("live", "roster") and row["evidence"] not in ("live", "roster")
                row.update(observed_cwd=o.observed_cwd, cwd_key=o.cwd_key,
                           history_dir=o.history_dir or row["history_dir"],
                           history_file=o.history_file or row["history_file"],
                           evidence=row["evidence"] if keep else o.evidence,
                           associated=associated if associated else row["associated"],
                           cwd_candidate_id=cwd_cand,
                           history_candidate_id=hist_cand if hist_cand is not None
                           else row.get("history_candidate_id"),
                           observed_at=now,
                           last_live_at=now if o.live else row.get("last_live_at"))
            touched.update(c for c in (cwd_cand, hist_cand) if c)

        rows = list(obs.values())
        for cid in touched:
            c = cands[cid]
            fp = _fingerprint(rows, cid)
            c["fingerprint"] = fp
            if c["state"] == CandidateState.IGNORED and c.get("ignored_fingerprint") != fp:
                c["state"], c["ignored_fingerprint"] = CandidateState.DISCOVERED.value, None
                changed += 1
        for cid in touched | recount:
            if cid in cands:
                cands[cid]["providers"], cands[cid]["sessions"] = _counted(rows, cid)

        writes: list[tuple[str, dict, str]] = []
        for cid, c in cands.items():
            c.setdefault("events", [])
            before = loaded_c.get(cid)
            if before is not None:
                c["events"] = []
            if _changed(before, c):
                writes.append((candidate_path(cid), c, "observed"))
        for oid, o in obs.items():
            o.setdefault("events", [])
            before = loaded_o.get(oid)
            if before is not None:
                o["events"] = []
            if _changed(before, o):
                writes.append((session_path(oid), o, "observed"))
        if last_obs != catalog["last_observation_id"]:
            catalog["last_observation_id"] = last_obs
            catalog["events"] = []
            writes.append((CATALOG, catalog, "observed"))
        for i in range(0, len(writes), BATCH):
            chunk = writes[i:i + BATCH]

            def run(unit: records.Unit, chunk=chunk) -> None:
                for path, data, change in chunk:
                    unit.put(path, data, change=change)
            records.atomic(run, change="observed", summary=f"discovery on {node_id}"[:200])
        return changed

    def candidates(self, *, state: Optional[str] = CandidateState.DISCOVERED, query: str = "",
                   role: Optional[str] = None, after: Optional[tuple[float, str]] = None,
                   limit: int = 100) -> list[Candidate]:
        """Candidates, most recently observed first, after `after`
        ((last observed, ID) of the previous page's last)."""
        q = _nocase(query)
        out = []
        for r in records.scan(CANDIDATES, types=["proposal"]):
            c = r.data
            if state and c["state"] != state:
                continue
            if q and q not in _nocase(c["path_key"]):
                continue
            if role and c["proposed_role"] != role:
                continue
            if after and not (c["last_observed_at"] < after[0]
                              or (c["last_observed_at"] == after[0] and c["id"] > after[1])):
                continue
            out.append(c)
        out.sort(key=lambda c: (-c["last_observed_at"], c["id"]))
        return [_candidate(c) for c in out[:max(0, int(limit))]]

    def candidate(self, candidate_id: str) -> Optional[Candidate]:
        rec = records.get(candidate_path(candidate_id)) if candidate_id else None
        return _candidate(rec.data) if rec is not None and rec.data.get("id") == candidate_id else None

    def set_candidate_state(self, candidate_id: str, ignored: bool) -> Candidate:
        """Ignore a discovered candidate (it stays ignored until its evidence
        changes), or take it back to discovered. A registered one is
        refused: unregister the folder instead."""
        def run(unit: records.Unit) -> Candidate:
            rec = unit.get(candidate_path(candidate_id)) if candidate_id else None
            if rec is None or rec.data.get("id") != candidate_id:
                raise FolderError("candidate_not_found")
            c = rec.data
            if c["state"] == CandidateState.REGISTERED:
                raise FolderError("unsupported")
            if ignored:
                c.update(state=CandidateState.IGNORED.value, ignored_fingerprint=c["fingerprint"])
            else:
                c.update(state=CandidateState.DISCOVERED.value, ignored_fingerprint=None)
            c["events"] = []
            unit.put(rec.path, c, change="ignored" if ignored else "restored",
                     summary=c["path_key"][-200:])
            return _candidate(c)

        with _LOCK:
            return records.atomic(run, by=records.TIM)

    def _observations(self, node_id: str) -> list[dict]:
        return [r.data for r in records.scan(SESSIONS, types=["proposal"],
                                             where=[["node_id", "=", node_id]])
                if r.data.get("node_id") == node_id]

    def related_sessions(self, node_id: str, canonical_path: str, now: Optional[float] = None,
                         limit: int = 50) -> list[RelatedSession]:
        """The sessions whose metadata named this path: as their cwd, a cwd
        inside it, or their history folder. Most recently observed first;
        each with the agent name JARVIS lists it by, where it has one."""
        from jarvis import run_store
        now = time.time() if now is None else now
        inside = canonical_path.rstrip("/") + "/"
        found = []
        for o in self._observations(node_id):
            if o["cwd_key"] == canonical_path:
                relation = "cwd"
            elif o["history_dir"] == canonical_path:
                relation = "history"
            elif o["cwd_key"].startswith(inside):
                relation = "inside"
            else:
                continue
            found.append((o, relation))
        found.sort(key=lambda x: x[0]["observed_at"], reverse=True)
        found = found[:max(0, int(limit))]
        names = run_store.agent_names() if found else {}
        return [RelatedSession(o["provider"], o["profile_root"], o["native_session_id"],
                               o["observed_cwd"], o["history_file"], o["evidence"],
                               dict(o.get("associated") or {}), o["first_observed_at"],
                               o["observed_at"],
                               o.get("last_live_at") is not None and now - o["last_live_at"]
                               < LIVE_WITHIN_SEC,
                               names.get(o["native_session_id"]), relation) for o, relation in found]

    def observed_cwds(self, node_id: str, history_dir: str) -> list[str]:
        """The cwds the sessions kept in one history folder recorded."""
        return sorted({o["observed_cwd"] for o in self._observations(node_id)
                       if o["history_dir"] == history_dir})

    # ── discovery's own progress ────────────────────────────────────────

    def history_files_read(self, paths: list[str]) -> dict[str, int]:
        """Of `paths`, those discovery has read, with the size each had."""
        wanted = set(paths)
        if not wanted:
            return {}
        out: dict[str, int] = {}
        for r in records.scan(HISTORY, parent=HISTORY):
            for f in r.data.get("files") or []:
                if f["path"] in wanted:
                    out[f["path"]] = f["size"]
        return out

    def mark_history_files(self, read: list[tuple[str, int]], now: Optional[float] = None) -> None:
        now = float(time.time() if now is None else now)
        if not read:
            return
        by_dir: dict[str, dict[str, int]] = {}
        for p, s in read:
            by_dir.setdefault(os.path.dirname(p), {})[p] = int(s)

        def run(unit: records.Unit, part: list[tuple[str, dict[str, int]]]) -> None:
            for d, marks in part:
                rec = unit.get(history_path(d))
                data = rec.data if rec is not None else {"history_dir": d, "files": []}
                files = {f["path"]: f for f in data.get("files") or []}
                for p, s in marks.items():
                    files[p] = {**files.get(p, {}), "path": p, "size": s, "read_at": now}
                data["files"] = sorted(files.values(), key=lambda f: f["path"])
                data["events"] = []
                unit.put(history_path(d), data, change="read", summary=d[-200:])

        items = list(by_dir.items())
        with _LOCK:
            for i in range(0, len(items), BATCH // 2):
                part = items[i:i + BATCH // 2]
                records.atomic(lambda unit, part=part: run(unit, part), change="read")

    def record_progress(self, source: str, *, listed: int, read: int, pending: int,
                        complete: bool, error: Optional[str], cursor: Optional[str] = None,
                        now: Optional[float] = None) -> None:
        """One source's pass: how much it listed and read, how much is left,
        and where it got to (`cursor`, kept when a pass gives none)."""
        now = float(time.time() if now is None else now)

        def change(data: Optional[dict]) -> dict:
            data = dict(data or {"sources": []})
            data["area"] = DISCOVERY_AREA
            sources = {s["source"]: s for s in data.get("sources") or []}
            old = sources.get(source) or {}
            sources[source] = {"source": source,
                               "cursor": cursor if cursor is not None else old.get("cursor"),
                               "listed": int(listed), "read": int(read), "pending": int(pending),
                               "complete": int(bool(complete)), "last_run_at": now,
                               "last_ok_at": now if not error else old.get("last_ok_at"),
                               "last_error": error,
                               **({"rowid": old["rowid"]} if "rowid" in old else {})}
            data["sources"] = sorted(sources.values(), key=lambda s: s["source"])
            data["events"] = []
            return data

        with _LOCK:
            records.update(DISCOVERY, change, change="progress", summary=source[:200])

    def progress(self) -> list[Progress]:
        rec = records.get(DISCOVERY)
        sources = (rec.data.get("sources") or []) if rec is not None else []
        return [Progress(s["source"], s.get("cursor"), s["listed"], s["read"], s["pending"],
                         bool(s["complete"]), s.get("last_run_at"), s.get("last_ok_at"),
                         s.get("last_error"))
                for s in sorted(sources, key=lambda s: s["source"])]


# ── the move: these documents and the tables they replaced ───────────────

from jarvis import catalog_rows as _rows  # noqa: E402

TABLES = {
    "folders": ("id", "owner_id", "display_name", "role", "lifecycle", "revision", "created_at",
                "updated_at", "removed_at"),
    "folder_locations": ("id", "folder_id", "node_id", "supplied_path", "canonical_path",
                         "fs_identity", "revision", "availability", "last_checked_at",
                         "last_seen_at", "error_code", "created_at", "retired_at"),
    "folder_access_scopes": ("id", "location_id", "reader_kind", "permitted_subtree",
                             "policy_version", "enabled", "created_at", "updated_at"),
    "folder_location_events": ("id", "folder_id", "location_id", "event", "prior", "new", "at"),
    "folder_registrations": ("request_key", "fingerprint", "folder_id", "created_at"),
}
DISCOVERY_TABLES = {
    "folder_candidates": ("id", "node_id", "path_key", "proposed_role", "state", "fingerprint",
                          "ignored_fingerprint", "first_seen_at", "last_observed_at",
                          "registered_folder_id"),
    "folder_observations": ("id", "node_id", "provider", "profile_root", "native_session_id",
                            "observed_cwd", "cwd_key", "history_dir", "history_file", "evidence",
                            "associated", "cwd_candidate_id", "history_candidate_id",
                            "first_observed_at", "observed_at", "last_live_at"),
    "folder_discovery": ("source", "cursor", "listed", "read", "pending", "complete",
                         "last_run_at", "last_ok_at", "last_error"),
    "folder_history_files": ("path", "size", "read_at"),
}
REAL = {"created_at", "updated_at", "removed_at", "last_checked_at", "last_seen_at", "retired_at",
        "at", "first_seen_at", "last_observed_at", "first_observed_at", "observed_at",
        "last_live_at", "last_run_at", "last_ok_at", "read_at"}
INTEGER = {"revision", "policy_version", "enabled", "listed", "read", "pending", "complete", "size"}
ALIASED = {"folder_location_events", "folder_observations"}   # INTEGER PRIMARY KEY: the id is the rowid
_EVENT_KEYS = ("location_id", "node_id", "supplied_path", "canonical_path", "fs_identity",
               "revision", "availability", "lifecycle", "reader_kind", "error_code")


def _event_text(value: dict) -> str:
    """An event's prior or new as the store first wrote it: its keys in the
    order the writer put them (the engine keeps documents' keys sorted)."""
    order = {k: i for i, k in enumerate(_EVENT_KEYS)}
    return json.dumps({k: value[k] for k in sorted(value, key=lambda k: (order.get(k, len(order)), k))})


def _with_rowid(table: dict, name: str) -> tuple[str, ...]:
    return ("rowid",) + table[name]


class FolderMapping:
    """`folders` and their locations, scopes, registrations and location
    events: one `folder` document each, the events as its versions. A row
    with no folder to belong to is kept whole in the catalog document."""

    name = "folder"
    tables = tuple(TABLES)
    under = (FOLDERS, "/settings")
    select = {FOLDERS: {"parent": FOLDERS, "types": ["folder"], "where": _LIVE},
              "/settings": {"where": [["area", "=", CATALOG_AREA]]}}
    history = True

    def documents(self, conn):
        from jarvis.knowledge_move import Doc, Revision
        t = {name: _rows.read(conn, name, cols) for name, cols in TABLES.items()}
        orphans: dict[str, list] = {}
        docs: dict[str, dict] = {}
        for r in t["folders"]:
            docs[r["id"]] = {**_rows.plain(r, _with_rowid(TABLES, "folders")),
                             "locations": [], "registrations": [], "events": []}
        locs: dict[str, dict] = {}
        for r in t["folder_locations"]:
            if r["folder_id"] in docs:
                entry = {**_rows.plain(r, _with_rowid(TABLES, "folder_locations"), drop=("folder_id",)),
                         "scopes": []}
                docs[r["folder_id"]]["locations"].append(entry)
                locs[r["id"]] = entry
            else:
                orphans.setdefault("folder_locations", []).append(_rows.raw(r))
        for r in t["folder_access_scopes"]:
            if r["location_id"] in locs:
                locs[r["location_id"]]["scopes"].append(
                    _rows.plain(r, _with_rowid(TABLES, "folder_access_scopes"), drop=("location_id",)))
            else:
                orphans.setdefault("folder_access_scopes", []).append(_rows.raw(r))
        for r in t["folder_registrations"]:
            if r["folder_id"] in docs:
                docs[r["folder_id"]]["registrations"].append(
                    _rows.plain(r, _with_rowid(TABLES, "folder_registrations"), drop=("folder_id",)))
            else:
                orphans.setdefault("folder_registrations", []).append(_rows.raw(r))
        events: dict[str, list] = {}
        for r in t["folder_location_events"]:
            e = _rows.plain(r, TABLES["folder_location_events"], drop=("folder_id",))
            e["prior"], e["new"] = _rows.json_or_text(r["prior"]), _rows.json_or_text(r["new"])
            same = all(isinstance(e[k], dict) and _event_text(e[k]) == r[k] for k in ("prior", "new"))
            if r["folder_id"] in docs and same:
                events.setdefault(r["folder_id"], []).append(e)
            else:
                orphans.setdefault("folder_location_events", []).append(_rows.raw(r))
        for fid, doc in docs.items():
            history = tuple(Revision(dict(doc, events=[e]), by=records.JARVIS, change=e["event"],
                                     summary=str(doc["display_name"])[:200])
                            for e in sorted(events.get(fid, []), key=lambda e: e["id"]))
            yield Doc(folder_path(fid), doc, history=history)
        seq = _rows.sequences(conn)
        observations = _rows.read(conn, "folder_observations", ("id",))
        yield Doc(CATALOG, {
            "area": CATALOG_AREA,
            "last_event_id": max([r["id"] for r in t["folder_location_events"]]
                                 + [seq.get("folder_location_events", 0)]),
            "last_observation_id": max([r["id"] for r in observations]
                                       + [seq.get("folder_observations", 0)]),
            "orphans": orphans, "events": []})

    def rows(self, docs) -> dict:
        out = _rows.Tables(TABLES, REAL, INTEGER, ALIASED)
        for doc in docs:
            p, d = doc.path, doc.data
            if p == CATALOG:
                for table, kept in (d.get("orphans") or {}).items():
                    for one in kept:
                        out.add_raw(table, one)
                continue
            if _rows.parent(p) != FOLDERS or "locations" not in d:
                continue
            out.add("folders", d)
            for loc in d.get("locations") or []:
                out.add("folder_locations", {**loc, "folder_id": d["id"]})
                for s in loc.get("scopes") or []:
                    out.add("folder_access_scopes", {**s, "location_id": loc["id"]})
            for reg in d.get("registrations") or []:
                out.add("folder_registrations", {**reg, "folder_id": d["id"]})
            for v in list(doc.history) + [doc]:
                for e in v.data.get("events") or []:
                    out.add("folder_location_events", {**e, "folder_id": d["id"],
                                                       "prior": _event_text(e.get("prior") or {}),
                                                       "new": _event_text(e.get("new") or {})})
        return out.rows()


class DiscoveryMapping:
    """What discovery found: `folder_candidates` and `folder_observations`
    as `proposal` documents, `folder_history_files` one `folder` document
    per history folder, `folder_discovery` the discovery settings. A row
    that cannot be a document as it is is kept whole in the settings."""

    name = "folder-discovery"
    tables = tuple(DISCOVERY_TABLES)
    under = (CANDIDATES, SESSIONS, HISTORY, "/settings")
    select = {"/settings": {"where": [["area", "=", DISCOVERY_AREA]]}}
    history = False

    def documents(self, conn):
        from jarvis.knowledge_move import Doc
        t = {name: _rows.read(conn, name, cols) for name, cols in DISCOVERY_TABLES.items()}
        orphans: dict[str, list] = {}
        obs = []
        for r in t["folder_observations"]:
            o = _rows.plain(r, DISCOVERY_TABLES["folder_observations"])
            o["associated"] = _rows.json_or_text(r["associated"])
            if not (isinstance(o["associated"], dict)
                    and json.dumps(o["associated"], sort_keys=True) == r["associated"]):
                orphans.setdefault("folder_observations", []).append(_rows.raw(r))
                continue
            o["events"] = []
            obs.append(o)
        for r in t["folder_candidates"]:
            c = _rows.plain(r, _with_rowid(DISCOVERY_TABLES, "folder_candidates"))
            c["providers"], c["sessions"] = _counted(obs, c["id"])
            c["events"] = []
            yield Doc(candidate_path(c["id"]), c)
        for o in obs:
            yield Doc(session_path(o["id"]), o)
        hist: dict[str, list] = {}
        for r in t["folder_history_files"]:
            hist.setdefault(os.path.dirname(r["path"]), []).append(
                _rows.plain(r, _with_rowid(DISCOVERY_TABLES, "folder_history_files")))
        for d, files in hist.items():
            yield Doc(history_path(d), {"history_dir": d, "events": [],
                                        "files": sorted(files, key=lambda f: f["path"])})
        sources = [_rows.plain(r, _with_rowid(DISCOVERY_TABLES, "folder_discovery"))
                   for r in t["folder_discovery"]]
        yield Doc(DISCOVERY, {"area": DISCOVERY_AREA, "events": [], "orphans": orphans,
                              "sources": sorted(sources, key=lambda s: s["source"])})

    def rows(self, docs) -> dict:
        out = _rows.Tables(DISCOVERY_TABLES, REAL, INTEGER, ALIASED)
        for doc in docs:
            p, d = doc.path, doc.data
            if p == DISCOVERY:
                for s in d.get("sources") or []:
                    out.add("folder_discovery", s)
                for table, kept in (d.get("orphans") or {}).items():
                    for one in kept:
                        out.add_raw(table, one)
            elif p.startswith(HISTORY + "/"):
                for f in d.get("files") or []:
                    out.add("folder_history_files", f)
            elif p.startswith(CANDIDATES + "/"):
                out.add("folder_candidates", d)
            elif p.startswith(SESSIONS + "/"):
                out.add("folder_observations",
                        {**d, "associated": json.dumps(d.get("associated") or {}, sort_keys=True)})
        return out.rows()


MAPPINGS = [FolderMapping(), DiscoveryMapping()]


