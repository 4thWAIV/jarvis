"""The session needs board: sessions write what they need from the user,
JARVIS reads it out and says when something new arrives."""

import asyncio
import os
import time

import pytest

from jarvis import session_needs as sn

BOARD = """# agent-guard
Updated: 2026-10-19 14:50

## Decisions
- bring-back-highlight: Port the highlight file to ts/metalang. Recommended: yes.
- commit-metalang: Commit ts/metalang on its own branch. Recommended: yes.

## Questions
- uri-pattern: Is the uri pattern the one you want? Recommended: yes.

## Blockers
"""


def _write(folder, name, text, mtime=None):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{name}.md"
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


# --- reading -----------------------------------------------------------------

def test_a_board_counts_one_item_per_bullet(tmp_path):
    _write(tmp_path, "agent-guard", BOARD)

    [board] = sn.read_boards(tmp_path)

    assert board.project == "agent-guard"
    assert board.items == 3


def test_boards_come_back_by_project_and_other_files_are_ignored(tmp_path):
    _write(tmp_path, "zeta", "- one")
    _write(tmp_path, "alpha", "- one")
    (tmp_path / "notes.txt").write_text("- not a board")

    assert [b.project for b in sn.read_boards(tmp_path)] == ["alpha", "zeta"]


def test_a_missing_folder_is_an_empty_board(tmp_path):
    assert sn.read_boards(tmp_path / "nowhere") == []


def test_a_runaway_file_is_cut(tmp_path):
    _write(tmp_path, "big", "- x\n" * 5000)

    [board] = sn.read_boards(tmp_path)

    assert len(board.text) < sn.MAX_FILE_CHARS + 100
    assert board.text.endswith("(the rest of this file was cut)")


# --- change tracking ---------------------------------------------------------

def test_the_first_look_announces_nothing(tmp_path):
    _write(tmp_path, "agent-guard", BOARD)
    tracker = sn.ChangeTracker()

    assert tracker.changed(sn.read_boards(tmp_path)) == []


def test_a_new_or_rewritten_board_is_announced_once(tmp_path):
    tracker = sn.ChangeTracker()
    tracker.changed(sn.read_boards(tmp_path))

    _write(tmp_path, "agent-guard", BOARD, mtime=time.time() - 100)
    assert [b.project for b in tracker.changed(sn.read_boards(tmp_path))] == ["agent-guard"]
    assert tracker.changed(sn.read_boards(tmp_path)) == [], "once, not every poll"

    _write(tmp_path, "agent-guard", BOARD + "- more: x\n", mtime=time.time())
    assert [b.items for b in tracker.changed(sn.read_boards(tmp_path))] == [4]


def test_an_emptied_board_is_not_announced(tmp_path):
    tracker = sn.ChangeTracker()
    tracker.changed(sn.read_boards(tmp_path))
    _write(tmp_path, "agent-guard", "# agent-guard\n\n## Decisions\n")

    assert tracker.changed(sn.read_boards(tmp_path)) == []


# --- the tool and the announcement -------------------------------------------

GUARD, APPROVAL, DIAG = "sess-guard-0001", "sess-approval-01", "sess-diag-00001"
NAMES = {GUARD: "Agent Guard", APPROVAL: "Approval Authority", DIAG: "Diagnostics"}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    sessions = [SimpleNamespace(project=p, voice_name=NAMES[sid], session_id=sid,
                                cwd=str(tmp_path / p), provider="anthropic", state="idle",
                                agents_active=0, agents_capped=False)
                for p, sid in (("agent-guard", GUARD), ("agent-guard", APPROVAL),
                               ("jarvis", DIAG))]
    monkeypatch.setattr(server_module, "_snapshot_or_empty",
                        lambda: SimpleNamespace(sessions=sessions))
    monkeypatch.setattr(server_module.agent_directory, "names_by_session", lambda: dict(NAMES))
    monkeypatch.setattr(server_module.session_watch, "config_roots",
                        lambda: [tmp_path / "claude"])
    return server_module, data_paths.needs_dir()


def _signed(path, session):
    """`<board>.sig` by `session`'s onboarded agent's own key, as `python -m
    jarvis.agent_key sign` writes it: the only proof of who wrote a board."""
    from jarvis import agent_key, agent_keys
    from tests.agent_key_fixtures import onboarded_auth
    agent = onboarded_auth(session=session).agent
    agent_key.sig_path(path).write_text(agent_keys.sign(
        agent.private, agent_keys.MESSAGE, agent_key.message_text(path, path.read_bytes())))


def _board(tmp_path, folder, name, text, session):
    p = _write(folder, name, text, mtime=time.time() - 120)
    _signed(p, session)
    return p


def test_the_tool_reads_every_board_wrapped_as_untrusted(wired, tmp_path):
    server, folder = wired
    _board(tmp_path, folder, "agent-guard", BOARD, GUARD)
    _board(tmp_path, folder, "jarvis",
           "# jarvis\n\n## Decisions\n- restart: Restart JARVIS. Recommended: yes.", DIAG)

    out = server.tool_session_needs({})

    assert out.startswith("Four items from two agents, page 1 of 1")
    assert "bring-back-highlight" in out and "restart: Restart JARVIS" in out
    assert 'untrusted="true"' in out
    assert server.TAINTING_TOOLS["session_needs"]


def test_the_tool_pages_a_long_board(wired, tmp_path):
    server, folder = wired
    _board(tmp_path, folder, "big", "\n".join(f"- item-{i}: something to decide"
                                              for i in range(60)), GUARD)

    first = server.tool_session_needs({})
    assert "page 1 of" in first and "Call session_needs again with page 2" in first
    assert len(first) <= server.TOOL_RESULT_CAP


def test_an_empty_board_says_nothing_is_waiting(wired, tmp_path):
    server, folder = wired
    _board(tmp_path, folder, "done", "# done\n", GUARD)

    assert "No session has anything waiting" in server.tool_session_needs({})


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, immediate=None, label=None):
        self.said.append(text)


@pytest.mark.asyncio
async def test_a_board_is_an_update_with_its_count(wired, monkeypatch, tmp_path):
    server, folder = wired
    from jarvis.attention_store import AttentionStore
    sp = _Speech()
    monkeypatch.setattr(server, "speech", sp)
    monkeypatch.setattr(server, "attention_record", AttentionStore())
    _board(tmp_path, folder, "agent-guard", BOARD, GUARD)
    [board] = server._proven_boards(sn.read_boards(folder))

    server._attend_board(board)

    assert sp.said == []
    (item,) = server.attention_record.items()
    assert (item.kind, item.source_id, item.event) == ("needs_board", "agent-guard", "changed")
    assert item.facts == {"items": 3} and item.untrusted == {"agent": "Agent Guard"}


def test_a_board_goes_by_its_proven_writer_never_its_project_s_agents(wired, tmp_path):
    """Two agents work in agent-guard: the board is the one whose key
    signed it, never "Agent Guard and Approval Authority"."""
    server, folder = wired
    _board(tmp_path, folder, "agent-guard", BOARD, APPROVAL)
    out = server.tool_session_needs({})
    assert "# Approval Authority\n" in out
    assert "Agent Guard" not in out and "# agent-guard" not in out


def test_a_board_nothing_signed_is_held_never_shown(wired, tmp_path):
    from jarvis import subsystem_diagnostics
    server, folder = wired
    _write(folder, "agent-guard", BOARD, mtime=time.time() - 120)     # nobody signed it

    assert "No session has anything waiting" in server.tool_session_needs({})
    assert server._needs_by_session() == {}
    assert server._boards_held == {"agent-guard"}
    check = subsystem_diagnostics.needs_check(None, time.time(), held=1)
    assert check.said is not None and check.said.code == "needsHeld"


def test_a_board_rewritten_is_proven_again(wired, tmp_path):
    """The proof is kept for the file as it was: a rewrite its writer has
    not signed again is not shown, whoever wrote it before."""
    server, folder = wired
    p = _board(tmp_path, folder, "agent-guard", BOARD, GUARD)
    assert "# Agent Guard\n" in server.tool_session_needs({})
    p.write_text(BOARD + "\n- another: x")
    os.utime(p, (time.time() + 30, time.time() + 30))         # rewritten after GUARD's record
    assert "No session has anything waiting" in server.tool_session_needs({})


def test_each_agent_counts_only_the_board_it_wrote(wired, tmp_path):
    server, folder = wired
    _board(tmp_path, folder, "agent-guard", BOARD, GUARD)
    assert server._needs_by_session() == {GUARD: 3}, "never Approval Authority's too"


def test_the_brain_is_offered_the_tool():
    from jarvis import jarvis_mcp
    assert "session_needs" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}


# --- read_file may read the board, and nothing else of JARVIS's data ---------

def test_read_file_may_read_the_needs_board_but_no_other_jarvis_data(
        tmp_path, monkeypatch):
    from jarvis import repo_read
    project = tmp_path / "jarvis"
    data = project / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    _write(data / "needs", "jarvis", "- restart: Restart. Recommended: yes.")
    (data / "tool-token").write_text("secret")
    (data / "jarvis").mkdir()
    (data / "jarvis" / "mcp.json").write_text("{}")

    assert repo_read.resolve_within(project, "data/needs/jarvis.md").name == "jarvis.md"
    for closed in ("data/tool-token", "data/jarvis/mcp.json"):
        with pytest.raises(repo_read.Refused):
            repo_read.resolve_within(project, closed)
