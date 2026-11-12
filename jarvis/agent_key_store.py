"""The record of agents' public keys, their onboarding and their rights.

JARVIS keeps only public keys. Each key is one entry of its holder's
document (agent_record.py): whose it is (`principal`: `agent:<session id>`,
kept in that session's `agent` document, or `workkey:<fingerprint>` for a
work-item key, a `workkey` document of its own), the session that
submitted it, and where it stands:

  pending    submitted (POST /api/agent-keys, self-signed); the claimed
             session's own transcript has not yet shown it submitting
  waiting    proven: it waits for Tim to switch the agent on, which is his
             act of onboarding it (the Agents tab, or by voice)
  onboarded  his: signed requests, packages and message files from it are
             the agent's own, within its rights
  revoked    no longer admitted (his Revoke, a newer key onboarded for the
             same session, its agent dropped from the list, its Work item
             closed, or a work key's 30 days up)

A pending key not proven within PROOF_WINDOW_SEC is dropped. At most
PENDING_MAX keys are pending at once, the oldest dropped first. A session
has one onboarded key: onboarding a new one revokes the old in the same
write. Rights are the holder's `rights` (agent_rights.py judges them),
kept by principal, so a session's rights outlive a key it replaces. Each
change is an event of the holder's document, its version, with who did it
and when. What an unknown key tried to send is a `hold` document per
subject, and the cut-over guard's facts and access-key counts are
`settings` documents (legacy_access.py).

Every read is answered from agent_record.CACHE (the web gate reads keys
and rights on every signed request); every write is one atomic write
through agent_record.change, which refreshes it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from jarvis import agent_keys, agent_record, agent_rights, records
from jarvis.agent_record import CACHE

PENDING, WAITING, ONBOARDED, REVOKED = "pending", "waiting", "onboarded", "revoked"
AGENT, WORK_KEY = "agent:", "workkey:"
# How long a submission waits for its session's transcript to show it.
PROOF_WINDOW_SEC = 10 * 60.0
PENDING_MAX = 50
# How Tim's acts are recorded (`onboarded_by`, `revoked_by`).
ON_THE_PAGE, BY_VOICE = "page", "voice"

MAPPINGS = agent_record.MAPPINGS


class KeyRefused(ValueError):
    """A submission or act that cannot be done, with why."""


@dataclass(frozen=True)
class AgentKey:
    fingerprint: str
    public_key: str
    principal: str
    session_id: str
    provider: str
    state: str
    submitted_at: float
    proven_at: Optional[float]
    proven_by: str
    onboarded_at: Optional[float]
    onboarded_by: str
    revoked_at: Optional[float]
    revoked_by: str
    revoked_why: str
    label: str
    work_id: str
    issued_by: str
    expires_at: Optional[float]

    def admitted(self, now: Optional[float] = None) -> bool:
        """Onboarded, and not past its expiry."""
        now = time.time() if now is None else now
        return self.state == ONBOARDED and (self.expires_at is None or self.expires_at > now)

    def public(self) -> dict:
        """What the page and the brain are shown: never anything secret,
        since nothing here is."""
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class Grant:
    principal: str
    right: str
    scope: str
    granted_at: float
    granted_by: str


def _key(k: dict) -> AgentKey:
    return AgentKey(**{f: k.get(f, agent_record.KEY_DEFAULTS.get(f)) for f in AgentKey.__dataclass_fields__})


def _new_key(fp: str, public: str, principal: str, state: str, now: float, **fields) -> dict:
    return {**agent_record.KEY_DEFAULTS, "fingerprint": fp, "public_key": public,
            "principal": principal, "state": state, "submitted_at": float(now), **fields}


def _find(doc: Optional[dict], fingerprint: str) -> Optional[dict]:
    if doc is None:
        return None
    return next((k for k in doc["keys"] if k.get("fingerprint") == fingerprint), None)


def _doc_of(w: agent_record.Writer, fingerprint: str) -> tuple[Optional[str], Optional[dict]]:
    """(path, key) of a key held now, read through the writer."""
    path = CACHE.key_path(fingerprint)
    if path is None:
        return None, None
    return path, _find(w.doc(path), fingerprint)


def _event(w: agent_record.Writer, path: str, fingerprint: str, kind: str, now: float,
           **detail) -> None:
    ev = agent_record.event(fingerprint, kind, now, **detail)
    w.event(path, ev, agent_record.event_summary(ev))


def _onboarded_on(by: str) -> str:
    """Who granted an agent's default rights."""
    return f"default, onboarded on {by}"


def agent_principal(session_id: str) -> str:
    return AGENT + session_id


# -- submission --------------------------------------------------------------------------

def submit(public_key: str, session_id: str, provider: str = "",
           now: Optional[float] = None) -> AgentKey:
    """Record a key a session submitted as pending, or the one it already
    has. Refused for a malformed key or session, or a key already revoked."""
    now = time.time() if now is None else now
    try:
        public = agent_keys.normalise(public_key)
    except agent_keys.AgentKeyError as e:
        raise KeyRefused(str(e))
    if not agent_keys.SESSION_ID.fullmatch(str(session_id or "")):
        raise KeyRefused("A session id is letters, digits, dashes and underscores.")
    fp = agent_keys.fingerprint(public)

    def run(w: agent_record.Writer) -> AgentKey:
        _path, found = _doc_of(w, fp)
        if found is not None:
            if found["session_id"] != session_id:
                raise KeyRefused("That key was submitted for another session.")
            if found["state"] == REVOKED:
                raise KeyRefused("That key was revoked; make a new one with init.")
            return _key(found)
        pending = sorted((k for k in CACHE.keys() if k.get("state") == PENDING),
                         key=lambda k: k["submitted_at"])
        for old in pending[:max(0, len(pending) - PENDING_MAX + 1)]:
            path, key = _doc_of(w, old["fingerprint"])
            if key is None or key["state"] != PENDING:
                continue
            w.doc(path)["keys"].remove(key)
            _event(w, path, key["fingerprint"], "dropped", now, why="too many pending")
        doc = w.agent(session_id)
        key = _new_key(fp, public, agent_principal(session_id), PENDING, now,
                       session_id=session_id, provider=str(provider or "")[:32])
        doc["keys"].append(key)
        _event(w, agent_record.agent_path(session_id), fp, "submitted", now, session=session_id)
        return _key(key)
    return agent_record.change(run, by=records.by(agent_principal(session_id)))


def to_prove(now: Optional[float] = None) -> list[AgentKey]:
    """The pending keys still within their proof window."""
    now = time.time() if now is None else now
    keys = [k for k in CACHE.keys() if k.get("state") == PENDING
            and k["submitted_at"] >= now - PROOF_WINDOW_SEC]
    return [_key(k) for k in sorted(keys, key=lambda k: k["submitted_at"])]


def drop_unproven(now: Optional[float] = None) -> list[str]:
    """Drop every pending key past its proof window. Their fingerprints."""
    now = time.time() if now is None else now
    late = [k["fingerprint"] for k in CACHE.keys() if k.get("state") == PENDING
            and k["submitted_at"] < now - PROOF_WINDOW_SEC]
    if not late:
        return []

    def run(w: agent_record.Writer) -> list[str]:
        dropped = []
        for fp in late:
            path, key = _doc_of(w, fp)
            if key is None or key["state"] != PENDING or key["submitted_at"] >= now - PROOF_WINDOW_SEC:
                continue
            w.doc(path)["keys"].remove(key)
            _event(w, path, fp, "dropped", now, why="not proven in time")
            dropped.append(fp)
        return dropped
    return agent_record.change(run)


def proven(fingerprint: str, evidence: str, now: Optional[float] = None) -> Optional[AgentKey]:
    """A pending key its session's own transcript showed submitting: it now
    waits for Tim. None when it is no longer pending."""
    now = time.time() if now is None else now

    def run(w: agent_record.Writer) -> Optional[AgentKey]:
        path, key = _doc_of(w, fingerprint)
        if key is None or key["state"] != PENDING:
            return None
        key.update(state=WAITING, proven_at=float(now), proven_by=evidence)
        _event(w, path, fingerprint, "proven", now, evidence=evidence)
        return _key(key)
    return agent_record.change(run)


# -- Tim's acts --------------------------------------------------------------------------

def onboard(session_id: str, by: str, label: str = "",
            now: Optional[float] = None) -> Optional[AgentKey]:
    """Tim switched the agent on: its waiting key is onboarded, recorded as
    his act (`by`: the page or his voice) with the time, given the default
    rights (agent_rights.DEFAULT_RIGHTS) unless the agent already has
    rights, and any key it had before revoked, all in one write. `label` is
    an access key's label that names exactly this agent, kept so the
    decisions it asked under that key stay its own. None when no key
    waits."""
    now = time.time() if now is None else now
    principal = agent_principal(session_id)

    def run(w: agent_record.Writer) -> Optional[AgentKey]:
        doc = w.agent(session_id, create=False)
        if doc is None:
            return None
        mine = [k for k in doc["keys"] if k.get("session_id") == session_id
                and k.get("principal") == principal]
        waiting = [k for k in mine if k["state"] == WAITING]
        if not waiting:
            return None
        found = max(waiting, key=lambda k: (k.get("proven_at") is not None, k.get("proven_at") or 0))
        path = agent_record.agent_path(session_id)
        for old in mine:
            if old["state"] == ONBOARDED:
                old.update(state=REVOKED, revoked_at=float(now), revoked_by=by,
                           revoked_why="replaced by a newer key")
                _event(w, path, old["fingerprint"], "revoked", now, by=by, why="replaced")
        found.update(state=ONBOARDED, onboarded_at=float(now), onboarded_by=by, label=label)
        if not any(g.get("principal") == principal for g in doc["rights"]):
            _grant(doc, principal, agent_rights.DEFAULT_RIGHTS, agent_rights.ANY_SCOPE,
                   _onboarded_on(by), now)
        _event(w, path, found["fingerprint"], "onboarded", now, by=by, label=label)
        return _key(found)
    return agent_record.change(run, by=agent_record.caller_for(by), full_sync=True)


def revoke(fingerprint: str, by: str, why: str, now: Optional[float] = None
           ) -> Optional[AgentKey]:
    """No longer admitted. A pending or waiting key is refused the same
    way. None when there is no such key or it is already revoked."""
    now = time.time() if now is None else now

    def run(w: agent_record.Writer) -> Optional[AgentKey]:
        path, key = _doc_of(w, fingerprint)
        if key is None or key["state"] == REVOKED:
            return None
        key.update(state=REVOKED, revoked_at=float(now), revoked_by=by, revoked_why=why)
        _event(w, path, fingerprint, "revoked", now, by=by, why=why)
        return _key(key)
    return agent_record.change(run, by=agent_record.caller_for(by),
                               full_sync=by in (ON_THE_PAGE, BY_VOICE))


def revoke_where(by: str, why: str, sessions: Iterable[str] = (), work_ids: Iterable[str] = (),
                 now: Optional[float] = None) -> list[AgentKey]:
    """Revoke every live key of these sessions or work items."""
    now = time.time() if now is None else now
    sessions, work_ids = list(sessions), list(work_ids)
    keys = CACHE.keys()
    fps = [k["fingerprint"] for s in sessions for k in keys if k.get("session_id") == s
           and k.get("principal") == agent_principal(s) and k.get("state") != REVOKED]
    fps += [k["fingerprint"] for w in work_ids for k in keys if k.get("work_id") == w
            and k.get("state") != REVOKED]
    out = []
    for fp in fps:
        done = revoke(fp, by, why, now)
        if done:
            out.append(done)
    return out


# -- reading -----------------------------------------------------------------------------

def get(fingerprint: str) -> Optional[AgentKey]:
    found = CACHE.key(fingerprint)
    return _key(found) if found is not None else None


def admitted(fingerprint: str, now: Optional[float] = None) -> Optional[AgentKey]:
    """The key if it is onboarded and live, else None."""
    found = get(fingerprint)
    return found if found is not None and found.admitted(now) else None


_ORDER = {WAITING: 0, ONBOARDED: 1, PENDING: 2}


def for_session(session_id: str) -> Optional[AgentKey]:
    """The agent's key that matters now: one waiting for Tim, else its
    onboarded one, else its latest pending one, else None."""
    doc = CACHE.get(agent_record.agent_path(session_id)) or {}
    keys = [k for k in doc.get("keys") or () if k.get("session_id") == session_id
            and k.get("principal") == agent_principal(session_id) and k.get("state") != REVOKED]
    keys.sort(key=lambda k: (_ORDER[k["state"]], -k["submitted_at"]))
    return _key(keys[0]) if keys else None


def by_session() -> dict[str, AgentKey]:
    """`for_session` for every session with a live key, in one read."""
    keys = [k for k in CACHE.keys() if str(k.get("principal", "")).startswith(AGENT)
            and k.get("state") != REVOKED]
    out: dict[str, AgentKey] = {}
    for k in sorted(keys, key=lambda k: (_ORDER[k["state"]], -k["submitted_at"])):
        out.setdefault(k["session_id"], _key(k))
    return out


def listing(include_revoked: bool = False) -> list[AgentKey]:
    keys = [k for k in CACHE.keys() if include_revoked or k.get("state") != REVOKED]
    return [_key(k) for k in sorted(keys, key=lambda k: -k["submitted_at"])]


def events(fingerprint: str) -> list[dict]:
    """The key's changes, oldest first: the events its holder's versions
    recorded (a dropped key's, from whichever holder recorded them)."""
    path = CACHE.key_path(fingerprint)
    paths = [path] if path else [p for p, _ in CACHE.docs() if agent_record.is_holder(p)]
    out = []
    for p in paths:
        for ev in agent_record.events_of(p):
            if ev.get("fingerprint") == fingerprint:
                out.append({"kind": ev["kind"], "at": ev["at"],
                            **json.loads(ev.get("detail") or "{}")})
    return out


# -- rights ------------------------------------------------------------------------------

def _grant(doc: dict, principal: str, rights: Iterable[str], scope: str, by: str,
           now: float) -> None:
    """Grant `rights` to `principal` in its holder's document `doc` (a
    grant it already has is kept as it is)."""
    for right in rights:
        if right not in agent_rights.RIGHTS:
            raise KeyRefused(f"There is no right {right}.")
        if any(g.get("principal") == principal and g.get("right") == right
               and g.get("scope") == scope for g in doc["rights"]):
            continue
        doc["rights"].append({"principal": principal, "right": right, "scope": scope,
                              "granted_at": float(now), "granted_by": by})


def _set_rights(principal: str, rights: Iterable[str], scope: str, by: str,
                now: Optional[float] = None) -> None:
    """Replace every grant of `principal` with `rights` in `scope` (tests
    give an agent exactly the rights they mean to)."""
    now = time.time() if now is None else now
    rights = list(rights)

    def run(w: agent_record.Writer) -> None:
        doc = w.holder(principal)
        doc["rights"] = [g for g in doc["rights"] if g.get("principal") != principal]
        _grant(doc, principal, rights, scope, by, now)
        w.changed(agent_record.holder_path(principal), "rights")
    agent_record.change(run)


def grants(principal: str) -> list[Grant]:
    doc = CACHE.get(agent_record.holder_path(principal)) or {}
    held = [g for g in doc.get("rights") or () if g.get("principal") == principal]
    return [Grant(g["principal"], g["right"], g["scope"], g["granted_at"], g["granted_by"])
            for g in sorted(held, key=lambda g: g["right"])]


# -- what is held for a key not onboarded ------------------------------------------------

HELD, RELEASED, REFUSED = "held", "released", "refused"


@dataclass(frozen=True)
class Held:
    id: str
    subject: str
    fingerprint: str
    session_id: str
    kind: str              # package, message, board, request
    what: str              # what it tried to send, in JARVIS's words with its title
    path: str              # where the held thing is kept ("" for a request)
    detail: dict
    work_id: str
    held_at: float
    state: str
    settled_at: Optional[float]


def subject_of(fingerprint: str = "", session_id: str = "") -> str:
    """Who tried: the key that signed it, else the session that claimed it."""
    return f"key:{fingerprint}" if fingerprint else f"session:{session_id}"


def _held(r: dict) -> Held:
    r = {**agent_record.HELD_DEFAULTS, **r}
    return Held(r["id"], r["subject"], r["fingerprint"], r["session_id"], r["kind"], r["what"],
                r["path"], json.loads(r["detail"] or "{}"), r["work_id"], r["held_at"],
                r["state"], r["settled_at"])


def _all_held() -> list[dict]:
    return [h for _p, d in CACHE.docs(".hold") for h in d.get("held") or ()]


def _held_id() -> str:
    import secrets
    return "held-" + secrets.token_hex(6)


def hold(kind: str, what: str, fingerprint: str = "", session_id: str = "", path: str = "",
         detail: Optional[dict] = None, work_id: str = "",
         now: Optional[float] = None) -> tuple[Held, bool]:
    """Record one thing held. (the record, whether it is new): the same kind
    and words from the same subject while one is still held is that one."""
    now = time.time() if now is None else now
    subject = subject_of(fingerprint, session_id)
    where = agent_record.hold_path(subject)

    def run(w: agent_record.Writer) -> tuple[Held, bool]:
        doc = w.doc(where, lambda: agent_record.new_hold(subject))
        found = next((h for h in doc["held"] if h.get("subject") == subject and h.get("kind") == kind
                      and h.get("what") == what and h.get("state") == HELD), None)
        if found is not None:
            return _held(found), False
        row = {"id": _held_id(), "subject": subject,
               "fingerprint": fingerprint, "session_id": session_id, "kind": kind,
               "what": what[:300], "path": path, "detail": json.dumps(detail or {}, sort_keys=True),
               "work_id": work_id, "held_at": float(now), "state": HELD, "settled_at": None}
        doc["held"].append(row)
        w.changed(where, "held")
        return _held(row), True
    return agent_record.change(run)


def held(subject: Optional[str] = None, state: str = HELD) -> list[Held]:
    rows = [h for h in _all_held() if h.get("state") == state
            and (not subject or h.get("subject") == subject)]
    return [_held(h) for h in sorted(rows, key=lambda h: h["held_at"])]


def held_for(fingerprint: str = "", session_id: str = "") -> list[Held]:
    """Everything still held that this key signed or this session claimed."""
    rows = [h for h in _all_held() if h.get("state") == HELD and (
        (h.get("fingerprint", "") != "" and h.get("fingerprint") == fingerprint)
        or (h.get("session_id", "") != "" and h.get("session_id") == session_id))]
    return [_held(h) for h in sorted(rows, key=lambda h: h["held_at"])]


def settle(held_id: str, state: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    where = next((p for p, d in CACHE.docs(".hold")
                  if any(h.get("id") == held_id for h in d.get("held") or ())), None)
    if where is None:
        return False

    def run(w: agent_record.Writer) -> bool:
        doc = w.doc(where)
        row = next((h for h in (doc or {}).get("held") or () if h.get("id") == held_id), None)
        if row is None or row.get("state") != HELD:
            return False
        row.update(state=state, settled_at=float(now))
        w.changed(where, state)
        return True
    return agent_record.change(run)


def _error_row(e: dict) -> dict:
    return {f: e.get(f) for f in agent_record.ERROR_FIELDS}


def error_package(subject: str) -> Optional[dict]:
    doc = CACHE.get(agent_record.hold_path(subject)) or {}
    e = doc.get("error")
    return _error_row(e) if e is not None and e.get("subject") == subject else None


def error_subject(package_id: str) -> Optional[str]:
    """The subject an error package JARVIS made is about, or None."""
    for _p, d in CACHE.docs(".hold"):
        e = d.get("error")
        if e is not None and e.get("package_id") == package_id:
            return e["subject"]
    return None


def set_error_package(subject: str, package_id: str, inbox_id: str,
                      now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    where = agent_record.hold_path(subject)

    def run(w: agent_record.Writer) -> None:
        doc = w.doc(where, lambda: agent_record.new_hold(subject))
        if doc.get("error") is None:
            doc["error"] = {"subject": subject}
        doc["error"].update(package_id=package_id, inbox_id=inbox_id, made_at=float(now))
        w.changed(where, "error_package")
    agent_record.change(run)


def error_packages() -> list[dict]:
    return [_error_row(d["error"]) for _p, d in CACHE.docs(".hold") if d.get("error") is not None]


# -- work-item keys ----------------------------------------------------------------------

# How long a work-item key lives unless its issuer renews it.
WORK_KEY_LIFE_SEC = 30 * 86400.0


def work_principal(fingerprint: str) -> str:
    return WORK_KEY + fingerprint


def add_work_key(public_key: str, work_id: str, issued_by: str,
                 now: Optional[float] = None) -> AgentKey:
    """A key limited to one Work item, issued by an onboarded agent (its
    principal): onboarded at once, since the agent that holds `work` vouches
    for it, with agent_rights.WORK_KEY_RIGHTS scoped to `work:<id>` and an
    expiry WORK_KEY_LIFE_SEC on. The same key again is that key."""
    now = time.time() if now is None else now
    try:
        public = agent_keys.normalise(public_key)
    except agent_keys.AgentKeyError as e:
        raise KeyRefused(str(e))
    fp = agent_keys.fingerprint(public)
    principal = work_principal(fp)

    def run(w: agent_record.Writer) -> AgentKey:
        _path, found = _doc_of(w, fp)
        if found is not None:
            if found["work_id"] != work_id or found["state"] == REVOKED:
                raise KeyRefused("That key is already another's, or revoked.")
            return _key(found)
        doc = w.holder(principal)
        key = _new_key(fp, public, principal, ONBOARDED, now, onboarded_at=float(now),
                       onboarded_by=issued_by, work_id=work_id, issued_by=issued_by,
                       expires_at=float(now + WORK_KEY_LIFE_SEC))
        doc["keys"].append(key)
        _grant(doc, principal, agent_rights.WORK_KEY_RIGHTS, agent_rights.WORK_SCOPE + work_id,
               issued_by, now)
        _event(w, agent_record.holder_path(principal), fp, "issued", now, work=work_id,
               by=issued_by)
        return _key(key)
    return agent_record.change(run, by=agent_record.caller_for(issued_by))


def renew_work_key(fingerprint: str, by: str, now: Optional[float] = None
                   ) -> Optional[AgentKey]:
    """Another WORK_KEY_LIFE_SEC for a live work-item key, by its issuer
    alone. None when it is not one, not live, or `by` did not issue it."""
    now = time.time() if now is None else now

    def run(w: agent_record.Writer) -> Optional[AgentKey]:
        path, key = _doc_of(w, fingerprint)
        if key is None or key["state"] != ONBOARDED or key.get("issued_by") != by \
                or not str(key.get("principal", "")).startswith(WORK_KEY):
            return None
        key["expires_at"] = float(now + WORK_KEY_LIFE_SEC)
        _event(w, path, fingerprint, "renewed", now, by=by)
        return _key(key)
    return agent_record.change(run, by=agent_record.caller_for(by))


def work_keys(work_id: Optional[str] = None) -> list[AgentKey]:
    """The live work-item keys, of one item or all."""
    keys = [k for k in CACHE.keys() if str(k.get("principal", "")).startswith(WORK_KEY)
            and k.get("state") == ONBOARDED and (not work_id or k.get("work_id") == work_id)]
    return [_key(k) for k in sorted(keys, key=lambda k: k["submitted_at"])]


# -- fresh authors (author_proof.py) -----------------------------------------------------

def bind_author(author: str, package_sha: str, now: Optional[float] = None) -> str:
    """Bind one author session to one package's bytes, the first time it
    vouches; the SHA-256 it is bound to (this one, or the earlier package's)."""
    now = time.time() if now is None else now
    session = agent_record.author_session(author)
    doc = CACHE.get(agent_record.agent_path(session)) or {}
    bound = next((p for p in doc.get("author_proofs") or () if p.get("author") == author), None)
    if bound is not None:
        return bound["package_sha"]

    def run(w: agent_record.Writer) -> str:
        doc = w.agent(session)
        found = next((p for p in doc["author_proofs"] if p.get("author") == author), None)
        if found is not None:
            return found["package_sha"]
        doc["author_proofs"].append({"author": author, "package_sha": package_sha,
                                     "bound_at": float(now)})
        w.changed(agent_record.agent_path(session), "author_bound")
        return package_sha
    return agent_record.change(run, by=records.by(AGENT + session))


# -- the cut-over guard's record (legacy_access.py) --------------------------------------

def setting(name: str) -> Optional[tuple[str, float]]:
    """(value, when it was set) of one named fact, or None."""
    doc = CACHE.get(agent_record.KEY_SETTINGS) or {}
    found = next((s for s in doc.get("settings") or () if s.get("name") == name), None)
    return (found["value"], found["at"]) if found is not None else None


def set_setting(name: str, value: str, now: Optional[float] = None,
                keep: bool = False) -> None:
    """Set one named fact (with `keep`, only if it is not set yet)."""
    now = time.time() if now is None else now
    if keep and setting(name) is not None:
        return
    path = agent_record.KEY_SETTINGS

    def run(w: agent_record.Writer) -> None:
        doc = w.doc(path, lambda: agent_record.new_settings(path))
        found = next((s for s in doc["settings"] if s.get("name") == name), None)
        if found is not None:
            if keep:
                return
            found.update(value=value, at=float(now))
        else:
            doc["settings"].append({"name": name, "value": value, "at": float(now)})
        w.changed(path, "setting")
    agent_record.change(run)


def count_legacy(label: str, now: Optional[float] = None) -> None:
    """One request admitted on an access key, counted by day. Never raises."""
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    path = agent_record.LEGACY_SETTINGS

    def run(w: agent_record.Writer) -> None:
        doc = w.doc(path, lambda: agent_record.new_settings(path))
        found = next((a for a in doc["admissions"] if a.get("day") == day
                      and a.get("label") == label), None)
        if found is not None:
            found["count"] = int(found["count"]) + 1
        else:
            doc["admissions"].append({"day": day, "label": label, "count": 1})
        w.changed(path, "admitted")
    try:
        agent_record.change(run)
    except Exception:
        pass


def _admissions() -> list[dict]:
    doc = CACHE.get(agent_record.LEGACY_SETTINGS) or {}
    return list(doc.get("admissions") or ())


def legacy_counts(days: int = 7, now: Optional[float] = None) -> dict[str, int]:
    """Access-key admissions by label over the last `days` days."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%d", time.localtime(now - (days - 1) * 86400))
    out: dict[str, int] = {}
    for a in _admissions():
        if a["day"] >= since:
            out[a["label"]] = out.get(a["label"], 0) + int(a["count"])
    return dict(sorted(out.items()))


def first_legacy_day() -> Optional[float]:
    """The start of the first day an access-key admission was counted."""
    days = [a["day"] for a in _admissions() if a.get("day")]
    if not days:
        return None
    return time.mktime(time.strptime(min(days), "%Y-%m-%d"))


def last_legacy_day_end() -> Optional[float]:
    """The end of the last day an access-key admission was counted."""
    days = [a["day"] for a in _admissions() if a.get("day")]
    if not days:
        return None
    return time.mktime(time.strptime(max(days), "%Y-%m-%d")) + 86400
