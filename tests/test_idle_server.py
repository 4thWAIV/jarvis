"""The server idles with no page connected (Tim's yes, 2026-11-12). A
profile of a copy of the live data, no page connected, had the server at
about 80 percent of a core on its own timers. Each look that did it now
reads only what changed:

- a Codex process's folder is asked of lsof once per process;
- the voice summary the subsystem watch reads every five seconds is the
  one last worked out while nothing was written and no window edge passed;
- a wait linked to a package is looked up once per sweep however many
  holds name it.
"""
from __future__ import annotations

import time

import pytest

from jarvis import codex_sessions, voice_diagnostics as vd, wait_links
from tests.test_voice_diagnostics import healthy


# ── Codex processes ─────────────────────────────────────────────────────

class Ps:
    def __init__(self, rows: list[str]) -> None:
        self.rows = rows
        self.lsof: list[int] = []

    def __call__(self, args, **kw):
        if args[0] == "ps":
            return type("R", (), {"stdout": "\n".join(self.rows)})()
        self.lsof.append(int(args[3]))
        return type("R", (), {"stdout": f"p{args[3]}\nfcwd\nn/work/{args[3]}\n"})()


def test_a_codex_process_s_folder_is_asked_once(monkeypatch):
    codex_sessions._cwds.clear()
    ps = Ps(["  101 01:00 /opt/bin/codex", "  102 02:00 /opt/bin/codex resume"])
    monkeypatch.setattr(codex_sessions.subprocess, "run", ps)
    first = codex_sessions.list_processes()
    again = codex_sessions.list_processes()
    assert [p.cwd for p in first] == [p.cwd for p in again] == ["/work/101", "/work/102"]
    assert ps.lsof == [101, 102]                         # not again on the second look
    ps.rows = ["  101 00:05 /opt/bin/codex"]             # pid 101 is another process now
    assert [p.cwd for p in codex_sessions.list_processes()] == ["/work/101"]
    assert ps.lsof == [101, 102, 101]
    assert set(codex_sessions._cwds) == {101}            # a process gone is forgotten


# ── the voice summary ───────────────────────────────────────────────────

def test_the_voice_summary_is_not_worked_out_again_while_nothing_changed(monkeypatch):
    now = time.time()
    vd.record_sample(healthy(at=now - 30, finals=2))
    worked = []
    real = vd._summary
    monkeypatch.setattr(vd, "_summary", lambda at: worked.append(at) or real(at))
    first = vd.summary(now)
    second = vd.summary(now + 5)
    assert len(worked) == 1 and second["now"] == now + 5
    assert {k: v for k, v in second.items() if k != "now"} == {k: v for k, v in first.items() if k != "now"}
    vd.record_sample(healthy(at=now + 6, finals=1))      # a write: worked out again
    assert vd.summary(now + 7)["lastHour"]["finals"] == 3 and len(worked) == 2
    # The two minutes pass the first sample's edge: worked out again, without it.
    later = vd.summary(now + 6 + 121)
    assert len(worked) == 3 and later["last2min"] == []


# ── package links in one sweep ──────────────────────────────────────────

def test_one_sweep_looks_a_package_up_once_however_many_holds_name_it(monkeypatch):
    asked = []
    before = wait_links._package_lookup
    wait_links.look_up_packages_with(lambda ref, key: asked.append((ref, key)))

    def holds() -> wait_links.Settled:
        for _ in range(3):
            wait_links.answered_package("pkg-1", None)
        wait_links.answered_package("pkg-2", "q")
        return wait_links.Settled()
    from jarvis import work_store, workflow_store
    monkeypatch.setattr(workflow_store, "sweep_waits", holds)
    monkeypatch.setattr(work_store, "sweep_waits", holds)
    try:
        wait_links.sweep()
        assert asked == [("pkg-1", None), ("pkg-2", "q")]
        wait_links.answered_package("pkg-1", None)       # outside a sweep: asked each time
        assert asked[-1] == ("pkg-1", None) and len(asked) == 3
    finally:
        wait_links.look_up_packages_with(before)


def test_codex_s_threads_are_read_again_only_once_its_database_changed(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table threads (id, rollout_path, cwd, title, git_branch, created_at_ms, "
                "updated_at_ms, archived)")
    con.execute("insert into threads values ('t1', '', '/w', 'one', '', 1, 1, 0)")
    con.commit()
    reads = []
    real = codex_sessions._read_threads
    monkeypatch.setattr(codex_sessions, "_read_threads", lambda p: reads.append(p) or real(p))
    assert set(codex_sessions._threads(tmp_path)) == {"t1"}
    assert set(codex_sessions._threads(tmp_path)) == {"t1"} and len(reads) == 1
    con.execute("insert into threads values ('t2', '', '/w', 'two', '', 2, 2, 0)")
    con.commit()
    con.close()
    assert set(codex_sessions._threads(tmp_path)) == {"t1", "t2"} and len(reads) == 2
