"""One writer per project: a project with a live session gets its work through
that session, not through a second, separate run editing the same files.

The user may override this for any one task; the brain says so by passing
`override`, and the run then starts as it always did.
"""

import pytest

import session_watch as sw
from tests.test_spawn_run_tool import _Executor, ready, wired  # noqa: F401


class _Watcher:
    def __init__(self, sessions):
        self.snapshot = sw.Snapshot(sessions=sessions, taken_at=1.0)


def _session(cwd, state=sw.WORKING):
    return sw.SessionState(
        session_id="s-1", cwd=str(cwd), project=cwd.name, state=state,
        roster_name=cwd.name, voice_name=cwd.name, steerable=True, since=10.0)


@pytest.fixture
def live(ready, monkeypatch, tmp_path):
    server, store, ex = ready
    (tmp_path / "chitauri").mkdir(exist_ok=True)
    monkeypatch.setattr(server, "session_watcher",
                        _Watcher([_session(tmp_path / "chitauri")]))
    return server, ex


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", [
    ("tool_spawn_run", {"project": "chitauri", "prompt": "fix the tests"}),
    ("tool_start_build", {"project": "chitauri", "spec": "# Thing\nBuild it."}),
])
async def test_a_live_session_gets_the_work_instead_of_a_new_run(live, tool, args):
    server, ex = live

    out = await getattr(server, tool)(args)

    assert ex.spawned == []
    assert "steer_session" in out and "chitauri" in out
    assert "override" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", [
    ("tool_spawn_run", {"project": "chitauri", "prompt": "fix the tests"}),
    ("tool_start_build", {"project": "chitauri", "spec": "# Thing\nBuild it."}),
])
async def test_the_user_s_override_starts_the_run_anyway(live, tool, args):
    server, ex = live

    await getattr(server, tool)({**args, "override": True})

    assert len(ex.spawned) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [sw.GONE, sw.FRESH])
async def test_a_finished_or_never_used_session_does_not_count(ready, monkeypatch,
                                                               tmp_path, state):
    server, _store, ex = ready
    monkeypatch.setattr(server, "session_watcher",
                        _Watcher([_session(tmp_path / "chitauri", state)]))

    await server.tool_spawn_run({"project": "chitauri", "prompt": "fix it"})

    assert len(ex.spawned) == 1


@pytest.mark.asyncio
async def test_a_session_in_another_project_does_not_count(ready, monkeypatch, tmp_path):
    server, _store, ex = ready
    monkeypatch.setattr(server, "session_watcher",
                        _Watcher([_session(tmp_path / "elsewhere")]))

    await server.tool_spawn_run({"project": "chitauri", "prompt": "fix it"})

    assert len(ex.spawned) == 1


def test_the_brain_is_offered_the_override():
    import jarvis_mcp
    specs = {t["name"]: t for t in jarvis_mcp.TOOL_SPECS}
    for name in ("spawn_run", "start_build"):
        assert "override" in specs[name]["inputSchema"]["properties"], name
