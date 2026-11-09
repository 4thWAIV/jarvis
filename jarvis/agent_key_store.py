"""The record of agents' public keys, their onboarding and their rights.

JARVIS keeps only public keys. Each row of `agent_keys` is one key: whose
it is (`principal`: `agent:<session id>`, or `workkey:<fingerprint>` for a
work-item key), the session that submitted it, and where it stands:

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
transaction. Rights are rows of `agent_rights` (agent_rights.py judges
them), kept by principal, so a session's rights outlive a key it replaces.
`agent_key_events` keeps each change, with who did it and when.
(`legacy_admissions` counts access-key use per day while the cut-over guard
still accepts them, legacy_access.py.)

Schema `agent_keys_schema`, through run_store.migrate. This module is the
only writer of its tables, one transaction per change.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Iterable, Optional

from jarvis import agent_keys, agent_rights, run_store

PENDING, WAITING, ONBOARDED, REVOKED = "pending", "waiting", "onboarded", "revoked"
AGENT, WORK_KEY = "agent:", "workkey:"
# How long a submission waits for its session's transcript to show it.
PROOF_WINDOW_SEC = 10 * 60.0
PENDING_MAX = 50
# How Tim's acts are recorded (`onboarded_by`, `revoked_by`).
ON_THE_PAGE, BY_VOICE = "page", "voice"

_SCHEMA = """
    CREATE TABLE agent_keys (
        fingerprint   TEXT PRIMARY KEY,
        public_key    TEXT NOT NULL,
        principal     TEXT NOT NULL,
        session_id    TEXT NOT NULL DEFAULT '',
        provider      TEXT NOT NULL DEFAULT '',
        state         TEXT NOT NULL,
        submitted_at  REAL NOT NULL,
        proven_at     REAL,
        proven_by     TEXT NOT NULL DEFAULT '',
        onboarded_at  REAL,
        onboarded_by  TEXT NOT NULL DEFAULT '',
        revoked_at    REAL,
        revoked_by    TEXT NOT NULL DEFAULT '',
        revoked_why   TEXT NOT NULL DEFAULT '',
        label         TEXT NOT NULL DEFAULT '',
        work_id       TEXT NOT NULL DEFAULT '',
        issued_by     TEXT NOT NULL DEFAULT '',
        expires_at    REAL
    );
    CREATE INDEX agent_keys_session ON agent_keys(session_id, state);
    CREATE TABLE agent_rights (
        principal   TEXT NOT NULL,
        "right"     TEXT NOT NULL,
        scope       TEXT NOT NULL,
        granted_at  REAL NOT NULL,
        granted_by  TEXT NOT NULL,
        PRIMARY KEY (principal, "right", scope)
    );
    CREATE TABLE agent_key_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint  TEXT NOT NULL,
        kind         TEXT NOT NULL,
        at           REAL NOT NULL,
        detail       TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE legacy_admissions (
        day    TEXT NOT NULL,
        label  TEXT NOT NULL,
        count  INTEGER NOT NULL,
        PRIMARY KEY (day, label)
    );
"""

# 2: what is held because the key that signed it (or the session that
# claimed it, unsigned) is not an onboarded agent's: the subject (`key:<fp>`
# or `session:<id>`), what it was and where it is kept, the Work item it
# was for, and how it was settled (released once the agent is onboarded,
# refused once its key is refused). The error package that tells Tim is
# kept per subject.
_HELD = """
    CREATE TABLE agent_key_held (
        id             TEXT PRIMARY KEY,
        subject        TEXT NOT NULL,
        fingerprint    TEXT NOT NULL DEFAULT '',
        session_id     TEXT NOT NULL DEFAULT '',
        kind           TEXT NOT NULL,
        what           TEXT NOT NULL,
        path           TEXT NOT NULL DEFAULT '',
        detail         TEXT NOT NULL DEFAULT '{}',
        work_id        TEXT NOT NULL DEFAULT '',
        held_at        REAL NOT NULL,
        state          TEXT NOT NULL,
        settled_at     REAL
    );
    CREATE INDEX agent_key_held_subject ON agent_key_held(subject, state);
    CREATE TABLE agent_key_errors (
        subject        TEXT PRIMARY KEY,
        package_id     TEXT NOT NULL DEFAULT '',
        inbox_id       TEXT NOT NULL DEFAULT '',
        made_at        REAL NOT NULL
    );
"""

# 3: a few named facts of the record's own: when the cut-over guard began
# watching access-key use, and when access keys stopped being accepted
# (legacy_access.py).
_SETTINGS = """
    CREATE TABLE agent_key_settings (
        name   TEXT PRIMARY KEY,
        value  TEXT NOT NULL,
        at     REAL NOT NULL
    );
"""

_MIGRATIONS = {1: _SCHEMA, 2: _HELD, 3: _SETTINGS}

_migrated: set[str] = set()
_migrating = threading.Lock()


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


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                run_store.migrate(conn, "agent_keys_schema", _MIGRATIONS, "agent keys")
                conn.commit()
                _migrated.add(path)
    return conn


def _row(r) -> AgentKey:
    return AgentKey(**{k: r[k] for k in AgentKey.__dataclass_fields__})


def _event(conn, fingerprint: str, kind: str, now: float, **detail) -> None:
    conn.execute("INSERT INTO agent_key_events (fingerprint, kind, at, detail) VALUES (?,?,?,?)",
                 (fingerprint, kind, now, json.dumps(detail, sort_keys=True)))


def agent_principal(session_id: str) -> str:
    return AGENT + session_id


# -- submission --------------------------------------------------------------------------

def submit(public_key: str, session_id: str, provider: str = "",
           now: Optional[float] = None) -> AgentKey:
    """Record a key a session submitted as pending, or the row it already
    has. Refused for a malformed key or session, or a key already revoked."""
    now = time.time() if now is None else now
    try:
        public = agent_keys.normalise(public_key)
    except agent_keys.AgentKeyError as e:
        raise KeyRefused(str(e))
    if not agent_keys.SESSION_ID.fullmatch(str(session_id or "")):
        raise KeyRefused("A session id is letters, digits, dashes and underscores.")
    fp = agent_keys.fingerprint(public)
    with closing(_connect()) as conn, conn:
        found = conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?", (fp,)).fetchone()
        if found is not None:
            if found["session_id"] != session_id:
                raise KeyRefused("That key was submitted for another session.")
            if found["state"] == REVOKED:
                raise KeyRefused("That key was revoked; make a new one with init.")
            return _row(found)
        pending = conn.execute("SELECT fingerprint FROM agent_keys WHERE state=? ORDER BY "
                               "submitted_at", (PENDING,)).fetchall()
        for old in pending[:max(0, len(pending) - PENDING_MAX + 1)]:
            conn.execute("DELETE FROM agent_keys WHERE fingerprint=?", (old["fingerprint"],))
            _event(conn, old["fingerprint"], "dropped", now, why="too many pending")
        conn.execute("INSERT INTO agent_keys (fingerprint, public_key, principal, session_id, "
                     "provider, state, submitted_at) VALUES (?,?,?,?,?,?,?)",
                     (fp, public, agent_principal(session_id), session_id,
                      str(provider or "")[:32], PENDING, now))
        _event(conn, fp, "submitted", now, session=session_id)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (fp,)).fetchone())


def to_prove(now: Optional[float] = None) -> list[AgentKey]:
    """The pending keys still within their proof window."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_keys WHERE state=? AND submitted_at>=? "
                            "ORDER BY submitted_at", (PENDING, now - PROOF_WINDOW_SEC)).fetchall()
    return [_row(r) for r in rows]


def drop_unproven(now: Optional[float] = None) -> list[str]:
    """Drop every pending key past its proof window. Their fingerprints."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        rows = conn.execute("SELECT fingerprint FROM agent_keys WHERE state=? AND "
                            "submitted_at<?", (PENDING, now - PROOF_WINDOW_SEC)).fetchall()
        for r in rows:
            conn.execute("DELETE FROM agent_keys WHERE fingerprint=?", (r["fingerprint"],))
            _event(conn, r["fingerprint"], "dropped", now, why="not proven in time")
    return [r["fingerprint"] for r in rows]


def proven(fingerprint: str, evidence: str, now: Optional[float] = None) -> Optional[AgentKey]:
    """A pending key its session's own transcript showed submitting: it now
    waits for Tim. None when it is no longer pending."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        moved = conn.execute("UPDATE agent_keys SET state=?, proven_at=?, proven_by=? WHERE "
                             "fingerprint=? AND state=?",
                             (WAITING, now, evidence, fingerprint, PENDING)).rowcount
        if not moved:
            return None
        _event(conn, fingerprint, "proven", now, evidence=evidence)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (fingerprint,)).fetchone())


# -- Tim's acts --------------------------------------------------------------------------

def onboard(session_id: str, by: str, label: str = "",
            now: Optional[float] = None) -> Optional[AgentKey]:
    """Tim switched the agent on: its waiting key is onboarded, recorded as
    his act (`by`: the page or his voice) with the time, given the default
    rights (agent_rights.DEFAULT_RIGHTS) unless the agent already has
    rights, and any key it had before revoked, all in one transaction.
    `label` is an access key's label that names exactly this agent, kept so
    the decisions it asked under that key stay its own. None when no key
    waits."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        found = conn.execute("SELECT * FROM agent_keys WHERE session_id=? AND state=? AND "
                             "principal=? ORDER BY proven_at DESC LIMIT 1",
                             (session_id, WAITING, agent_principal(session_id))).fetchone()
        if found is None:
            return None
        for old in conn.execute("SELECT fingerprint FROM agent_keys WHERE session_id=? AND "
                                "state=? AND principal=?",
                                (session_id, ONBOARDED, agent_principal(session_id))):
            conn.execute("UPDATE agent_keys SET state=?, revoked_at=?, revoked_by=?, "
                         "revoked_why=? WHERE fingerprint=?",
                         (REVOKED, now, by, "replaced by a newer key", old["fingerprint"]))
            _event(conn, old["fingerprint"], "revoked", now, by=by, why="replaced")
        conn.execute("UPDATE agent_keys SET state=?, onboarded_at=?, onboarded_by=?, label=? "
                     "WHERE fingerprint=?", (ONBOARDED, now, by, label, found["fingerprint"]))
        principal = agent_principal(session_id)
        if conn.execute("SELECT 1 FROM agent_rights WHERE principal=?",
                        (principal,)).fetchone() is None:
            _grant(conn, principal, agent_rights.DEFAULT_RIGHTS, agent_rights.ANY_SCOPE,
                   f"default, onboarded on {by}", now)
        _event(conn, found["fingerprint"], "onboarded", now, by=by, label=label)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (found["fingerprint"],)).fetchone())


def revoke(fingerprint: str, by: str, why: str, now: Optional[float] = None
           ) -> Optional[AgentKey]:
    """No longer admitted. A pending or waiting key is refused the same
    way. None when there is no such key or it is already revoked."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        moved = conn.execute("UPDATE agent_keys SET state=?, revoked_at=?, revoked_by=?, "
                             "revoked_why=? WHERE fingerprint=? AND state!=?",
                             (REVOKED, now, by, why, fingerprint, REVOKED)).rowcount
        if not moved:
            return None
        _event(conn, fingerprint, "revoked", now, by=by, why=why)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (fingerprint,)).fetchone())


def revoke_where(by: str, why: str, sessions: Iterable[str] = (), work_ids: Iterable[str] = (),
                 now: Optional[float] = None) -> list[AgentKey]:
    """Revoke every live key of these sessions or work items."""
    now = time.time() if now is None else now
    out = []
    with closing(_connect()) as conn:
        rows = [r["fingerprint"] for s in sessions for r in conn.execute(
            "SELECT fingerprint FROM agent_keys WHERE session_id=? AND principal=? AND state!=?",
            (s, agent_principal(s), REVOKED))]
        rows += [r["fingerprint"] for w in work_ids for r in conn.execute(
            "SELECT fingerprint FROM agent_keys WHERE work_id=? AND state!=?", (w, REVOKED))]
    for fp in rows:
        done = revoke(fp, by, why, now)
        if done:
            out.append(done)
    return out


# -- reading -----------------------------------------------------------------------------

def get(fingerprint: str) -> Optional[AgentKey]:
    with closing(_connect()) as conn:
        r = conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                         (fingerprint,)).fetchone()
    return _row(r) if r else None


def admitted(fingerprint: str, now: Optional[float] = None) -> Optional[AgentKey]:
    """The key if it is onboarded and live, else None."""
    found = get(fingerprint)
    return found if found is not None and found.admitted(now) else None


def for_session(session_id: str) -> Optional[AgentKey]:
    """The agent's key that matters now: one waiting for Tim, else its
    onboarded one, else its latest pending one, else None."""
    order = {WAITING: 0, ONBOARDED: 1, PENDING: 2}
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_keys WHERE session_id=? AND principal=? AND "
                            "state!=?", (session_id, agent_principal(session_id),
                                         REVOKED)).fetchall()
    rows = sorted(rows, key=lambda r: (order[r["state"]], -r["submitted_at"]))
    return _row(rows[0]) if rows else None


def by_session() -> dict[str, AgentKey]:
    """`for_session` for every session with a live key, in one read."""
    order = {WAITING: 0, ONBOARDED: 1, PENDING: 2}
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_keys WHERE principal LIKE 'agent:%' AND "
                            "state!=?", (REVOKED,)).fetchall()
    out: dict[str, AgentKey] = {}
    for r in sorted(rows, key=lambda r: (order[r["state"]], -r["submitted_at"])):
        out.setdefault(r["session_id"], _row(r))
    return out


def listing(include_revoked: bool = False) -> list[AgentKey]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_keys" + ("" if include_revoked else
                                                          " WHERE state!='revoked'")
                            + " ORDER BY submitted_at DESC").fetchall()
    return [_row(r) for r in rows]


def events(fingerprint: str) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_key_events WHERE fingerprint=? ORDER BY id",
                            (fingerprint,)).fetchall()
    return [{"kind": r["kind"], "at": r["at"], **json.loads(r["detail"] or "{}")} for r in rows]


# -- rights ------------------------------------------------------------------------------

def _grant(conn, principal: str, rights: Iterable[str], scope: str, by: str,
           now: float) -> None:
    for right in rights:
        if right not in agent_rights.RIGHTS:
            raise KeyRefused(f"There is no right {right}.")
        conn.execute('INSERT OR IGNORE INTO agent_rights (principal, "right", scope, '
                     'granted_at, granted_by) VALUES (?,?,?,?,?)',
                     (principal, right, scope, now, by))


def grants(principal: str) -> list[Grant]:
    with closing(_connect()) as conn:
        rows = conn.execute('SELECT principal, "right", scope, granted_at, granted_by FROM '
                            'agent_rights WHERE principal=? ORDER BY "right"',
                            (principal,)).fetchall()
    return [Grant(r["principal"], r["right"], r["scope"], r["granted_at"], r["granted_by"])
            for r in rows]


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


def _held(r) -> Held:
    return Held(r["id"], r["subject"], r["fingerprint"], r["session_id"], r["kind"], r["what"],
                r["path"], json.loads(r["detail"] or "{}"), r["work_id"], r["held_at"],
                r["state"], r["settled_at"])


def hold(kind: str, what: str, fingerprint: str = "", session_id: str = "", path: str = "",
         detail: Optional[dict] = None, work_id: str = "",
         now: Optional[float] = None) -> tuple[Held, bool]:
    """Record one thing held. (the row, whether it is new): the same kind
    and words from the same subject while one is still held is that row."""
    import secrets as _secrets
    now = time.time() if now is None else now
    subject = subject_of(fingerprint, session_id)
    with closing(_connect()) as conn, conn:
        found = conn.execute("SELECT * FROM agent_key_held WHERE subject=? AND kind=? AND "
                             "what=? AND state=?", (subject, kind, what, HELD)).fetchone()
        if found is not None:
            return _held(found), False
        hid = "held-" + _secrets.token_hex(6)
        conn.execute("INSERT INTO agent_key_held (id, subject, fingerprint, session_id, kind, "
                     "what, path, detail, work_id, held_at, state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (hid, subject, fingerprint, session_id, kind, what[:300], path,
                      json.dumps(detail or {}, sort_keys=True), work_id, now, HELD))
        return _held(conn.execute("SELECT * FROM agent_key_held WHERE id=?",
                                  (hid,)).fetchone()), True


def held(subject: Optional[str] = None, state: str = HELD) -> list[Held]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_key_held WHERE state=?" +
                            (" AND subject=?" if subject else "") + " ORDER BY held_at",
                            (state, subject) if subject else (state,)).fetchall()
    return [_held(r) for r in rows]


def held_for(fingerprint: str = "", session_id: str = "") -> list[Held]:
    """Everything still held that this key signed or this session claimed."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_key_held WHERE state=? AND ((fingerprint!='' "
                            "AND fingerprint=?) OR (session_id!='' AND session_id=?)) ORDER BY "
                            "held_at", (HELD, fingerprint, session_id)).fetchall()
    return [_held(r) for r in rows]


def settle(held_id: str, state: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        return bool(conn.execute("UPDATE agent_key_held SET state=?, settled_at=? WHERE id=? AND "
                                 "state=?", (state, now, held_id, HELD)).rowcount)


def error_package(subject: str) -> Optional[dict]:
    with closing(_connect()) as conn:
        r = conn.execute("SELECT * FROM agent_key_errors WHERE subject=?", (subject,)).fetchone()
    return dict(r) if r else None


def error_subject(package_id: str) -> Optional[str]:
    """The subject an error package JARVIS made is about, or None."""
    with closing(_connect()) as conn:
        r = conn.execute("SELECT subject FROM agent_key_errors WHERE package_id=?",
                         (package_id,)).fetchone()
    return r["subject"] if r else None


def set_error_package(subject: str, package_id: str, inbox_id: str,
                      now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO agent_key_errors (subject, package_id, inbox_id, made_at) "
                     "VALUES (?,?,?,?) ON CONFLICT(subject) DO UPDATE SET package_id=excluded."
                     "package_id, inbox_id=excluded.inbox_id, made_at=excluded.made_at",
                     (subject, package_id, inbox_id, now))


def error_packages() -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM agent_key_errors")]


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
    expiry WORK_KEY_LIFE_SEC on. The same key again is that row."""
    now = time.time() if now is None else now
    try:
        public = agent_keys.normalise(public_key)
    except agent_keys.AgentKeyError as e:
        raise KeyRefused(str(e))
    fp = agent_keys.fingerprint(public)
    with closing(_connect()) as conn, conn:
        found = conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?", (fp,)).fetchone()
        if found is not None:
            if found["work_id"] != work_id or found["state"] == REVOKED:
                raise KeyRefused("That key is already another's, or revoked.")
            return _row(found)
        principal = work_principal(fp)
        conn.execute("INSERT INTO agent_keys (fingerprint, public_key, principal, state, "
                     "submitted_at, onboarded_at, onboarded_by, work_id, issued_by, expires_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (fp, public, principal, ONBOARDED, now, now, issued_by, work_id, issued_by,
                      now + WORK_KEY_LIFE_SEC))
        _grant(conn, principal, agent_rights.WORK_KEY_RIGHTS,
               agent_rights.WORK_SCOPE + work_id, issued_by, now)
        _event(conn, fp, "issued", now, work=work_id, by=issued_by)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (fp,)).fetchone())


def renew_work_key(fingerprint: str, by: str, now: Optional[float] = None
                   ) -> Optional[AgentKey]:
    """Another WORK_KEY_LIFE_SEC for a live work-item key, by its issuer
    alone. None when it is not one, not live, or `by` did not issue it."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        moved = conn.execute("UPDATE agent_keys SET expires_at=? WHERE fingerprint=? AND "
                             "state=? AND issued_by=? AND principal LIKE 'workkey:%'",
                             (now + WORK_KEY_LIFE_SEC, fingerprint, ONBOARDED, by)).rowcount
        if not moved:
            return None
        _event(conn, fingerprint, "renewed", now, by=by)
        return _row(conn.execute("SELECT * FROM agent_keys WHERE fingerprint=?",
                                 (fingerprint,)).fetchone())


def work_keys(work_id: Optional[str] = None) -> list[AgentKey]:
    """The live work-item keys, of one item or all."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM agent_keys WHERE principal LIKE 'workkey:%' AND "
                            "state=?" + (" AND work_id=?" if work_id else "") +
                            " ORDER BY submitted_at",
                            (ONBOARDED, work_id) if work_id else (ONBOARDED,)).fetchall()
    return [_row(r) for r in rows]


# -- the cut-over guard's record (legacy_access.py) --------------------------------------

def setting(name: str) -> Optional[tuple[str, float]]:
    """(value, when it was set) of one named fact, or None."""
    with closing(_connect()) as conn:
        r = conn.execute("SELECT value, at FROM agent_key_settings WHERE name=?",
                         (name,)).fetchone()
    return (r["value"], r["at"]) if r else None


def set_setting(name: str, value: str, now: Optional[float] = None,
                keep: bool = False) -> None:
    """Set one named fact (with `keep`, only if it is not set yet)."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO agent_key_settings (name, value, at) VALUES (?,?,?) "
                     + ("ON CONFLICT(name) DO NOTHING" if keep else
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, at=excluded.at"),
                     (name, value, now))


def count_legacy(label: str, now: Optional[float] = None) -> None:
    """One request admitted on an access key, counted by day. Never raises."""
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("INSERT INTO legacy_admissions (day, label, count) VALUES (?,?,1) "
                         "ON CONFLICT(day, label) DO UPDATE SET count=count+1", (day, label))
    except sqlite3.Error:
        pass


def legacy_counts(days: int = 7, now: Optional[float] = None) -> dict[str, int]:
    """Access-key admissions by label over the last `days` days."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%d", time.localtime(now - (days - 1) * 86400))
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT label, SUM(count) AS n FROM legacy_admissions WHERE day>=? "
                            "GROUP BY label ORDER BY label", (since,)).fetchall()
    return {r["label"]: r["n"] for r in rows}


def first_legacy_day() -> Optional[float]:
    """The start of the first day an access-key admission was counted."""
    with closing(_connect()) as conn:
        r = conn.execute("SELECT MIN(day) AS d FROM legacy_admissions").fetchone()
    if not r or not r["d"]:
        return None
    return time.mktime(time.strptime(r["d"], "%Y-%m-%d"))


def last_legacy_day_end() -> Optional[float]:
    """The end of the last day an access-key admission was counted."""
    with closing(_connect()) as conn:
        r = conn.execute("SELECT MAX(day) AS d FROM legacy_admissions").fetchone()
    if not r or not r["d"]:
        return None
    return time.mktime(time.strptime(r["d"], "%Y-%m-%d")) + 86400
