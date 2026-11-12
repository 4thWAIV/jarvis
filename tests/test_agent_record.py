"""The agent, hold and settings documents (agent_record.py): their move from
SQLite and back, lossless for every table they own, and the in-memory copy
the hot reads are answered from."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis import agent_key_store, agent_record, knowledge_move as move, records, run_store

TABLES = ("agents", "steers", "agent_keys", "agent_rights", "agent_key_events",
          "agent_author_proofs", "agent_key_held", "agent_key_errors", "agent_key_settings",
          "legacy_admissions")
SCHEMA = Path(__file__).resolve().parents[1] / "jarvis" / "sqlite_schema.json"


def _make_db(path: Path) -> sqlite3.Connection:
    """A SQLite file with these tables exactly as JARVIS made them."""
    objects = json.loads(SCHEMA.read_text())["schema"]["objects"]
    conn = sqlite3.connect(path)
    for o in objects:
        if o["tbl_name"] in TABLES and o["sql"]:
            conn.execute(o["sql"])
    return conn


def _fill(conn: sqlite3.Connection) -> None:
    """Rows filling every column of every table, NULLs included, and the odd
    cases: a key held by a work item and by another principal, an event of a
    key dropped before the move, a steer naming no session."""
    ins = conn.execute
    ins("INSERT INTO agents VALUES ('s1', 'hammer', 1, 'idle', 'Forge', 12.5, NULL)")
    ins("INSERT INTO agents VALUES ('s2', 'anvil', 0, '', NULL, 0, 99.25)")
    key = ("INSERT INTO agent_keys VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    ins(key, ("fp1", "ssh-ed25519 AAA1", "agent:s1", "s1", "claude", "onboarded", 1.0, 2.0,
              "transcript", 3.0, "page", None, "", "", "planning", "", "", None))
    ins(key, ("fp2", "ssh-ed25519 AAA2", "agent:s1", "s1", "", "revoked", 0.5, None, "", None,
              "", 4.0, "voice", "replaced by a newer key", "", "", "", None))
    ins(key, ("fp3", "ssh-ed25519 AAA3", "workkey:fp3", "", "", "onboarded", 5.0, None, "", 5.0,
              "agent:s1", None, "", "", "", "work-1", "agent:s1", 99999.0))
    ins(key, ("fp4", "ssh-ed25519 AAA4", "agent:s9", "s9", "codex", "pending", 6.0, None, "",
              None, "", None, "", "", "", "", "", None))
    ins(key, ("fp5", "ssh-ed25519 AAA5", "someone", "", "", "waiting", 7.0, 7.5, "x", None, "",
              None, "", "", "", "", "", None))
    right = "INSERT INTO agent_rights VALUES (?,?,?,?,?)"
    ins(right, ("agent:s1", "work", "*", 3.0, "default, onboarded on page"))
    ins(right, ("agent:s1", "watch", "*", 3.0, "default, onboarded on page"))
    ins(right, ("workkey:fp3", "work", "work:work-1", 5.0, "agent:s1"))
    ins(right, ("agent:s7", "packages", "*", 8.0, "test"))
    ev = "INSERT INTO agent_key_events (fingerprint, kind, at, detail) VALUES (?,?,?,?)"
    ins(ev, ("fp2", "submitted", 0.5, json.dumps({"session": "s1"})))
    ins(ev, ("fp1", "submitted", 1.0, json.dumps({"session": "s1"})))
    ins(ev, ("fp1", "proven", 2.0, json.dumps({"evidence": "transcript"})))
    ins(ev, ("fp2", "revoked", 3.0, json.dumps({"by": "page", "why": "replaced"})))
    ins(ev, ("fp1", "onboarded", 3.0, json.dumps({"by": "page", "label": "planning"})))
    ins(ev, ("gone", "submitted", 0.1, json.dumps({"session": "s3"})))
    ins(ev, ("gone", "dropped", 0.2, json.dumps({"why": "not proven in time"})))
    ins(ev, ("lost", "dropped", 0.3, "{}"))
    ins(ev, ("fp3", "issued", 5.0, json.dumps({"by": "agent:s1", "work": "work-1"})))
    ins("INSERT INTO agent_author_proofs VALUES ('claude:s1', 'abc', 9.0)")
    ins("INSERT INTO agent_author_proofs VALUES ('nocolon', 'def', 9.5)")
    st = ("INSERT INTO steers (session_id, voice_name, project, prompt, outcome, created_at) "
          "VALUES (?,?,?,?,?,?)")
    ins(st, ("s1", "hammer", "jarvis", "return", "ran", 10.0))
    ins(st, ("", "", "", "nobody", "no_session", 11.0))
    ins(st, ("s2", "anvil", "x", "ls", "refused", 12.0))
    held = "INSERT INTO agent_key_held VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    ins(held, ("held-1", "key:fp5", "fp5", "", "package", "a deck", "/tmp/held/a.zip",
               json.dumps({"inbox": 1}), "work-1", 20.0, "held", None))
    ins(held, ("held-2", "session:", "", "", "board", "the needs board", "", "{}", "", 21.0,
               "released", 22.0))
    ins("INSERT INTO agent_key_errors VALUES ('key:fp5', 'pkg-1', 'inbox-1', 23.0)")
    ins("INSERT INTO agent_key_errors VALUES ('session:s4', '', '', 24.0)")
    ins("INSERT INTO agent_key_settings VALUES ('cutover', 'access keys admit nothing', 30.0)")
    ins("INSERT INTO agent_key_settings VALUES ('access_keys_watched_since', '1', 29.0)")
    ins("INSERT INTO legacy_admissions VALUES ('2026-11-09', 'diagnostics-work', 3)")
    ins("INSERT INTO legacy_admissions VALUES ('2026-11-10', 'planning', 1)")
    conn.commit()


MAPPINGS = agent_record.MAPPINGS + [move.RowMapping("sqlite_sequence")]


def _rows(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {t: move.table_digest(move.read_rows(conn, move.table_info(conn, t)))
                for t in TABLES}
    finally:
        conn.close()


def test_every_table_comes_back_the_same(tmp_path):
    db = tmp_path / "jarvis.db"
    _fill(_make_db(db))
    uds = records.uds()
    move.copy(db, uds, MAPPINGS)
    report = move.verify(db, uds, MAPPINGS)
    assert report.ok, report.words()
    out = tmp_path / "back.db"
    move.export_sqlite(uds, MAPPINGS, out)
    assert _rows(out) == _rows(db)


def test_the_moved_documents_read_as_the_store_reads_them(tmp_path):
    db = tmp_path / "jarvis.db"
    _fill(_make_db(db))
    move.copy(db, records.uds(), MAPPINGS)
    agent_record.CACHE.forget()

    assert run_store.agent_names() == {"s1": "hammer", "s2": "anvil"}
    assert [a["session_id"] for a in run_store.list_agents()] == ["s1"]
    assert agent_key_store.for_session("s1").fingerprint == "fp1"
    assert agent_key_store.admitted("fp1") is not None
    assert agent_key_store.get("fp3").work_id == "work-1"
    assert [g.right for g in agent_key_store.grants("agent:s1")] == ["watch", "work"]
    assert [g.scope for g in agent_key_store.grants("workkey:fp3")] == ["work:work-1"]
    assert [e["kind"] for e in agent_key_store.events("fp1")] == ["submitted", "proven",
                                                                   "onboarded"]
    assert [e["kind"] for e in agent_key_store.events("gone")] == ["submitted", "dropped"]
    assert [e["kind"] for e in agent_key_store.events("lost")] == ["dropped"]
    assert agent_key_store.bind_author("claude:s1", "new") == "abc"
    assert [s["outcome"] for s in run_store.list_steers()] == ["refused", "no_session", "ran"]
    assert [h.id for h in agent_key_store.held()] == ["held-1"]
    assert agent_key_store.held()[0].detail == {"inbox": 1}
    assert agent_key_store.error_subject("pkg-1") == "key:fp5"
    assert agent_key_store.setting("cutover") == ("access keys admit nothing", 30.0)
    assert agent_key_store.legacy_counts(7, now=1794355200.0) == {"diagnostics-work": 3,
                                                                  "planning": 1}


def test_what_the_store_writes_after_the_move_goes_back_numbered_after(tmp_path):
    db = tmp_path / "jarvis.db"
    _fill(_make_db(db))
    uds = records.uds()
    move.copy(db, uds, MAPPINGS)
    agent_record.CACHE.forget()

    run_store.record_steer("s2", "anvil", "x", "pwd", "ran")
    agent_key_store.revoke("fp1", "voice", "his word", now=40.0)
    run_store.sync_agents({"s1": ("hammer", "working"), "s5": ("new", "idle")}, now=41.0)
    agent_key_store.count_legacy("planning")

    back = {}
    for m in agent_record.MAPPINGS:
        back.update(m.rows(move._engine_docs(uds, m)))
    steers = sorted(back["steers"], key=lambda r: r.cells["id"])
    assert [r.cells["prompt"] for r in steers][-1] == "pwd"
    assert steers[-1].cells["id"] == 4, "the next id after the largest moved"
    events = sorted(back["agent_key_events"], key=lambda r: r.cells["id"])
    assert (events[-1].cells["id"], events[-1].cells["fingerprint"], events[-1].cells["kind"]) \
        == (10, "fp1", "revoked")
    agents = {r.cells["session_id"]: r for r in back["agents"]}
    assert agents["s5"].rowid == 3 and agents["s5"].cells["last_message_at"] == 0.0
    assert agents["s2"].cells["ended_at"] == 99.25
    assert agents["s1"].cells["state"] == "working"
    assert {r.cells["state"] for r in back["agent_keys"] if r.cells["fingerprint"] == "fp1"} \
        == {"revoked"}


def test_the_store_s_documents_are_the_mapping_s_shape(tmp_path):
    """What the store writes reads back as rows, and the copy of those rows
    makes the same documents."""
    run_store.sync_agents({"s1": ("hammer", "idle")}, now=1.0)
    run_store.record_steer("s1", "hammer", "p", "return", "ran")
    held, _new = agent_key_store.hold("board", "the board", session_id="s2", now=2.0)
    agent_key_store.set_error_package("session:s2", "pkg", "inbox", now=3.0)
    agent_key_store.set_setting("cutover", "done", now=4.0)
    agent_key_store.count_legacy("lbl", now=5.0)
    agent_key_store.bind_author("claude:s1", "sha", now=6.0)
    uds = records.uds()
    back = {}
    for m in agent_record.MAPPINGS:
        back.update(m.rows(move._engine_docs(uds, m)))
    db = tmp_path / "out.db"
    conn = _make_db(db)
    for t in TABLES:
        info = move.table_info(conn, t)
        move._insert(conn, info, back[t])
    conn.commit()
    conn.close()
    with move.snapshot(db) as snap:
        docs = {d.path: d.data for m in agent_record.MAPPINGS for d in m.documents(snap)}
    for path, data in docs.items():
        stored = records.get(path).data
        assert set(stored) == set(data), path
        for field in stored:
            if field == "events":
                continue
            assert _without_numbers(stored[field]) == _without_numbers(data[field]), (path, field)


def _without_numbers(value):
    if isinstance(value, dict):
        return {k: _without_numbers(v) for k, v in value.items() if k not in ("rowid", "id")}
    if isinstance(value, list):
        return [_without_numbers(v) for v in value]
    return value


def test_hot_reads_come_from_memory(monkeypatch):
    """The gate's keys and rights, and the agents' names, are read without
    the engine once loaded; a write through the store refreshes them."""
    run_store.sync_agents({"s1": ("hammer", "idle")})
    agent_key_store.set_setting("x", "1")
    run_store.agent_names()

    def no_engine(*a, **kw):
        raise AssertionError("a hot read went to the engine")
    with monkeypatch.context() as m:
        m.setattr(records, "scan", no_engine)
        m.setattr(records, "get", no_engine)
        assert run_store.agent_names() == {"s1": "hammer"}
        assert agent_key_store.get("nope") is None
        assert agent_key_store.grants("agent:s1") == []
        assert agent_key_store.setting("x")[0] == "1"

    run_store.rename_agent("s1", "Forge")
    assert run_store.agent_names() == {"s1": "Forge"}


def test_a_forgotten_agent_keeps_its_document_while_it_holds_anything():
    run_store.sync_agents({"s1": ("hammer", "idle"), "s2": ("anvil", "idle")}, now=1.0)
    run_store.record_steer("s1", "hammer", "p", "return", "ran")
    run_store.sync_agents({}, now=2.0)
    run_store.sync_agents({}, now=3.0 + run_store.AGENT_ENDED_KEEP_SEC)
    assert run_store.agent_names() == {}
    assert records.get(agent_record.agent_path("s2")) is None
    kept = records.get(agent_record.agent_path("s1")).data
    assert kept["agent"] is None and [s["prompt"] for s in kept["steers"]] == ["return"]
    assert [s["outcome"] for s in run_store.list_steers()] == ["ran"]


@pytest.mark.parametrize("by,expected", [("page", "tim"), ("voice", "tim")])
def test_his_onboarding_is_recorded_as_his(by, expected, monkeypatch):
    from jarvis import agent_keys
    monkeypatch.setattr(agent_keys, "normalise", lambda k: k)
    monkeypatch.setattr(agent_keys, "fingerprint", lambda k: "fp-" + k[-4:])
    key = agent_key_store.submit("ssh-ed25519 AAAAkey1", "s1", now=1.0)
    agent_key_store.proven(key.fingerprint, "transcript", now=2.0)
    agent_key_store.onboard("s1", by, now=3.0)
    versions = records.history(agent_record.agent_path("s1"), fields=["events"])
    assert [v.change for v in versions] == ["submitted", "proven", "onboarded"]
    assert versions[-1].by == expected and not versions[-1].agent
    assert versions[0].agent == "agent:s1"
    assert [e["kind"] for v in versions for e in v.data["events"]] == [
        "submitted", "proven", "onboarded"]
