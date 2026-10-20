"""The session needs board: sessions write what they need from the user,
JARVIS reads it out and says when something new arrives."""

import asyncio
import os
import time

import pytest

import session_needs as sn

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

@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    import data_paths
    importlib.reload(data_paths)
    import server as server_module
    importlib.reload(server_module)
    return server_module, data_paths.needs_dir()


def test_the_folder_is_in_the_data_dir(wired, tmp_path):
    _server, folder = wired
    assert folder == tmp_path / "data" / "needs" and folder.is_dir()


def test_the_tool_reads_every_board_wrapped_as_untrusted(wired):
    server, folder = wired
    _write(folder, "agent-guard", BOARD)
    _write(folder, "jarvis", "# jarvis\n\n## Decisions\n- restart: Restart JARVIS. Recommended: yes.")

    out = server.tool_session_needs({})

    assert out.startswith("Four items from two projects, page 1 of 1")
    assert "bring-back-highlight" in out and "restart: Restart JARVIS" in out
    assert 'untrusted="true"' in out
    assert server.TAINTING_TOOLS["session_needs"]


def test_the_tool_pages_a_long_board(wired):
    server, folder = wired
    _write(folder, "big", "\n".join(f"- item-{i}: something to decide" for i in range(60)))

    first = server.tool_session_needs({})
    assert "page 1 of" in first and "Call session_needs again with page 2" in first
    assert len(first) <= server.TOOL_RESULT_CAP


def test_an_empty_board_says_nothing_is_waiting(wired):
    server, folder = wired
    _write(folder, "done", "# done\n")

    assert "No session has anything waiting" in server.tool_session_needs({})


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, immediate=None):
        self.said.append(text)


@pytest.mark.asyncio
async def test_a_board_is_announced_and_the_brain_is_told(wired, monkeypatch, tmp_path):
    server, _folder = wired
    sp = _Speech()
    monkeypatch.setattr(server, "speech", sp)
    monkeypatch.setattr(server, "_notes_for_brain", [])
    _write(tmp_path / "b", "agent-guard", BOARD)
    [board] = sn.read_boards(tmp_path / "b")

    await server._announce_board(board)

    assert sp.said == ["agent-guard has three things for you, sir."]
    assert "call session_needs" in server._with_notes_for_brain("go on")


def test_the_brain_is_offered_the_tool():
    import jarvis_mcp
    assert "session_needs" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}


# --- read_file may read the board, and nothing else of JARVIS's data ---------

def test_read_file_may_read_the_needs_board_but_no_other_jarvis_data(
        tmp_path, monkeypatch):
    import repo_read
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
