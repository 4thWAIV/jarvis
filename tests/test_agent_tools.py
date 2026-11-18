"""An agent's MCP tools (agent_mcp.py): the brain's reading tools through
POST /api/agent-tool, signed with the agent's own key and judged by its
`research` right, answered by the brain's own handlers and walled the same
way; the agent commands as tools calling the commands' own `main`; the
package job tools; and session_skill installing the server in every home.

Nothing here reaches a real ~/.claude, ~/.codex, session or JARVIS."""
from __future__ import annotations

import json

import pytest

from jarvis import agent_mcp, agent_rights, package_jobs, work_report, work_store
from tests.test_brain_page_parity import HOSTILE, _Brain, _call, _walled, wired  # noqa: F401

RESEARCH = agent_rights.TOOLS["research"]


@pytest.fixture
def researcher(wired):
    from tests.agent_key_fixtures import onboarded_auth
    return onboarded_auth(("work", "research"), "sess-researcher")


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "speech"
    folder.mkdir()
    return work_store.create(HOSTILE, "folder", str(folder))


def _agent_call(w, auth, tool, **arguments):
    return w.client.post(agent_rights.AGENT_TOOL_PATH, auth=auth,
                         json={"tool": tool, "arguments": arguments})


# ── the right ────────────────────────────────────────────────────────────

def test_research_names_only_the_brain_s_reading_tools(wired):
    server = wired.server
    assert "research" in agent_rights._shipped().standard
    assert agent_rights.RIGHTS["research"].covers("POST", agent_rights.AGENT_TOOL_PATH)
    assert set(RESEARCH) <= set(server.TOOL_HANDLERS)
    assert not set(RESEARCH) & server.ACTING_TOOLS, "no acting tool beyond what agents do"


def test_a_work_item_key_s_scope_reaches_no_tool(wired, monkeypatch):
    from types import SimpleNamespace
    from jarvis import agent_key_store
    monkeypatch.setattr(agent_key_store, "grants", lambda principal: [
        SimpleNamespace(right="research", scope="work:w1")])
    assert agent_rights.tools_of("workkey:fp") == frozenset()
    assert not agent_rights.allows_tool("agent:s", "work_status"), "not onboarded: nothing"
    monkeypatch.setattr(agent_key_store, "is_onboarded", lambda session: True)
    monkeypatch.setattr(agent_key_store, "grants", lambda principal: [])
    assert agent_rights.allows_tool("agent:s", "work_status")
    assert not agent_rights.allows_tool("agent:s", "spawn_run")


# ── the route ────────────────────────────────────────────────────────────

def test_an_agent_gets_the_brain_s_own_answer_walled_the_same(wired, researcher, item):
    brain = _call(wired, "work_status", item=item.id)
    marks_before = list(wired.server.brain_instance.marks)

    r = _agent_call(wired, researcher, "work_status", item=item.id)

    assert r.status_code == 200, r.text
    assert r.json() == brain, "the same handler, the same cap, the same wall"
    _walled(r.json()["text"])
    assert wired.server.brain_instance.marks == marks_before, \
        "an agent's read taints nothing of the brain's turn"


def test_an_agent_s_read_leaves_the_message_he_is_on_alone(wired, researcher, monkeypatch):
    from jarvis import messages_api
    monkeypatch.setattr(messages_api, "whats_new", lambda: [{"id": 42}])
    wired.server._message_on = None
    wired.server.brain_instance = _Brain()

    r = _agent_call(wired, researcher, "messages_to_him", new=True)

    assert r.status_code == 200 and r.json()["ok"], r.text
    assert wired.server._message_on is None
    _call(wired, "messages_to_him", new=True)
    assert wired.server._message_on == 42, "the brain's own read still puts him on it"


def test_a_tool_the_rights_do_not_name_is_refused(wired, researcher):
    wired.server.brain_instance = _Brain()
    for tool in ("spawn_run", "steer_session", "remember", "no_such_tool"):
        r = _agent_call(wired, researcher, tool)
        assert r.status_code == 403, tool
        assert "rights do not cover" in r.json()["error"]


def test_an_agent_without_research_is_refused_at_the_gate(wired, item):
    from tests.agent_key_fixtures import onboarded_auth
    worker = onboarded_auth(session="sess-worker")
    _rights({"agents": {"sess-worker": {"remove": ["research"]}}})
    r = _agent_call(wired, worker, "work_status")
    assert r.status_code == 403


# ── the one rights file ──────────────────────────────────────────────────

def _rights(change: dict) -> None:
    """Write the data folder's rights file: the shipped standard, changed."""
    shipped = json.loads(agent_rights.SHIPPED_FILE.read_text())
    agent_rights.rights_file().write_text(json.dumps({**shipped, **change}))


def test_onboarding_copies_no_rights_and_the_file_gives_them(wired):
    from jarvis import agent_key_store
    from tests.agent_key_fixtures import onboarded_auth
    onboarded_auth(session="sess-plain")
    assert agent_key_store.grants("agent:sess-plain") == [], "nothing copied onto the agent"
    assert [g.right for g in agent_rights.granted("agent:sess-plain")] == sorted(
        agent_rights._shipped().standard)


def test_a_right_added_to_the_standard_reaches_an_onboarded_agent_at_once(wired):
    from tests.agent_key_fixtures import onboarded_auth
    onboarded_auth(session="sess-std")
    assert not agent_rights.allows("agent:sess-std", "POST", "/api/runs")
    _rights({"standard": [*agent_rights._shipped().standard, "runs"]})
    assert agent_rights.allows("agent:sess-std", "POST", "/api/runs")


def test_an_exception_adds_and_removes_by_session_or_name(wired):
    from jarvis import run_store
    from tests.agent_key_fixtures import onboarded_auth
    onboarded_auth(session="sess-exc")
    name = run_store.agent_names()["sess-exc"]
    _rights({"agents": {"sess-exc": {"add": ["agents"]},
                        name.upper(): {"remove": ["research", "watch"]}}})
    rights = [g.right for g in agent_rights.granted("agent:sess-exc")]
    assert rights == ["agents", "packages", "work", "workflows"]
    assert agent_rights.tools_of("agent:sess-exc") == frozenset()


def test_rights_stored_before_count_only_where_they_differ(wired):
    from tests.agent_key_fixtures import onboarded_auth
    onboarded_auth(agent_rights.LEGACY_STANDARD, "sess-old")
    assert "research" in [g.right for g in agent_rights.granted("agent:sess-old")]
    onboarded_auth(("work", "captures"), "sess-odd")
    assert [g.right for g in agent_rights.granted("agent:sess-odd")] == [
        "captures", "research", "work"]


def test_a_malformed_file_falls_back_to_the_shipped_default_and_says_so(wired, caplog):
    from tests.agent_key_fixtures import onboarded_auth
    onboarded_auth(session="sess-bad")
    agent_rights.rights_file().write_text('{"standard": ["work", "flying"]}')
    with caplog.at_level("WARNING", logger="jarvis.agent_rights"):
        rights = [g.right for g in agent_rights.granted("agent:sess-bad")]
    assert rights == sorted(agent_rights._shipped().standard)
    assert "shipped default" in caplog.text and "flying" in caplog.text


def test_the_tool_token_and_the_page_are_refused(wired):
    from jarvis import data_paths
    body = {"tool": "work_status", "arguments": {}}
    token = wired.client.post(agent_rights.AGENT_TOOL_PATH, json=body, headers={
        "Authorization": f"Bearer {data_paths.ensure_tool_token()}"})
    page = wired.client.post(agent_rights.AGENT_TOOL_PATH, json=body,
                             headers={"Origin": "http://localhost:5173"})
    unsigned = wired.client.post(agent_rights.AGENT_TOOL_PATH, json=body)
    assert (token.status_code, page.status_code, unsigned.status_code) == (403, 403, 403)


# ── the agent's side ─────────────────────────────────────────────────────

class _Through:
    """workflow_report's client, over the test app, signed by one agent:
    what a command's or a tool's request reaches JARVIS as."""

    def __init__(self, w, auth):
        self.w, self.auth, self.timeout, self.posts = w, auth, 10.0, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, path, json):
        self.posts.append((path, json))
        return self.w.client.post(path, json=json, auth=self.auth)

    def get(self, path, params=None):
        return self.w.client.get(path, params=params, auth=self.auth)


def test_the_session_is_jarvis_s_then_claude_code_s_then_the_call_s(monkeypatch):
    monkeypatch.delenv("JARVIS_AGENT_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert agent_mcp.session_of({"as_session": "s3"}) == "s3"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2")
    assert agent_mcp.session_of({"as_session": "s3"}) == "s2"
    monkeypatch.setenv("JARVIS_AGENT_SESSION", "s1")
    assert agent_mcp.session_of({"as_session": "s3"}) == "s1"


def test_a_research_tool_is_signed_with_the_agent_s_key_and_answered(
        wired, researcher, item, monkeypatch):
    asked = []

    def signed(cert, session, right):
        asked.append((session, right))
        return through
    through = _Through(wired, researcher)
    monkeypatch.setattr(agent_mcp, "signed_client", signed)
    monkeypatch.setenv("JARVIS_AGENT_SESSION", "sess-researcher")

    ok, text, image = agent_mcp.call("work_status", {"item": item.id})

    assert ok and image is None
    assert asked == [("sess-researcher", "research")]
    assert through.posts == [(agent_rights.AGENT_TOOL_PATH,
                              {"tool": "work_status", "arguments": {"item": item.id}})]
    _walled(text)


def test_a_refused_tool_says_why(wired, researcher, monkeypatch):
    monkeypatch.setattr(agent_mcp, "signed_client", lambda *a: _Through(wired, researcher))
    ok, text, _ = agent_mcp.call("spawn_run", {})
    assert not ok and "403" in text and "rights do not cover" in text


def test_a_command_tool_is_the_command_s_own_main(wired, monkeypatch, tmp_path):
    from tests.agent_key_fixtures import onboarded_auth
    reporter = onboarded_auth(("work",), "sess-cmd")
    monkeypatch.setattr(work_report, "signed_client",
                        lambda cert, session, right: _Through(wired, reporter))
    folder = tmp_path / "captions"
    folder.mkdir()

    ok, started, _ = agent_mcp.call("work_start", {"title": "Captions", "folder": str(folder)})
    assert ok, started
    ok, line, _ = agent_mcp.call("work_piece", {
        "id": started, "key": "wire", "summary": "Wiring the engine.", "title": "Wire",
        "who": "agent:sess-cmd", "state": "ready", "evidence": ["abc1234"]})
    assert ok, line

    piece = work_store.get(started).pieces[0]
    assert (piece.key, piece.note) == ("wire", "Wiring the engine.")
    assert [e.value for e in piece.evidence] == ["abc1234"]


def test_a_command_s_argv_is_its_command_line(monkeypatch):
    monkeypatch.setattr(agent_mcp, "CERT", None)
    piece = agent_mcp._COMMANDS["work_piece"].argv(
        {"id": "w1", "key": "k", "summary": "s", "evidence": ["a", "b"], "cleared": True,
         "state": "working", "as_session": "ignored"}, "sess")
    assert piece == ["jarvis.work_report", "--as", "sess", "piece", "w1", "k", "s",
                     "--state", "working", "--evidence", "a", "--evidence", "b", "--cleared"]
    doc = agent_mcp._COMMANDS["language_document"].argv(
        {"operation": "validate", "source": "/a.yaml", "arguments": {"work_id": "w"}}, "")
    assert doc == ["validate", "--source", "/a.yaml", "--json", '{"work_id": "w"}']


def test_a_command_s_refusal_is_its_words(monkeypatch):
    def refuses(argv):
        raise SystemExit("JARVIS refused it (409): held.")
    monkeypatch.setattr(work_report, "main", refuses)
    ok, text, _ = agent_mcp.call("work_close", {"id": "w1"})
    assert not ok and text == "JARVIS refused it (409): held."


def test_the_package_job_tools_call_package_jobs(monkeypatch):
    monkeypatch.setenv("JARVIS_AGENT_SESSION", "")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    ok, text, _ = agent_mcp.call("package_status", {"job": "j1"})
    assert not ok and text == "Refused: There is no package job j1."

    seen = []
    monkeypatch.setattr(package_jobs, "package_start",
                        lambda handoff, work=None, general=False, session=None:
                        seen.append((handoff, work, general, session)) or {"job": "j9"})
    ok, text, _ = agent_mcp.call("package_start", {"handoff": "/h.json", "work": "w1",
                                                   "as_session": "sess-x"})
    assert ok and json.loads(text) == {"job": "j9"}
    assert seen == [("/h.json", "w1", False, "sess-x")]
    from jarvis import env_names
    assert env_names.read("JARVIS_AGENT_SESSION") == "sess-x", "the job signs as the sender"


def test_the_server_lists_every_tool_and_dispatches(monkeypatch):
    listed = agent_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert set(RESEARCH) <= set(names)
    assert {"send_message", "work_piece", "workflow_event", "retrieve_attachment",
            "language_document", "package_start", "package_status", "package_answer"} \
        <= set(names)
    assert len(names) == len(set(names))
    assert all("as_session" in t["inputSchema"]["properties"]
               for t in listed["result"]["tools"])
    monkeypatch.setattr(agent_mcp, "call", lambda tool, args: (True, f"{tool} ok", None))
    reply = agent_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "work_status", "arguments": {}}})
    assert reply["result"]["content"] == [{"type": "text", "text": "work_status ok"}]


# ── installing it ────────────────────────────────────────────────────────

@pytest.fixture
def homes(monkeypatch, tmp_path):
    from jarvis import session_skill
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setattr(session_skill.session_watch, "config_roots", lambda: [claude])
    monkeypatch.setattr(session_skill.codex_sessions, "codex_home", lambda: codex)
    return session_skill, claude, codex


def test_install_puts_the_server_in_every_home_and_keeps_the_rest(homes):
    session_skill, claude, codex = homes
    (claude / ".claude.json").write_text(json.dumps(
        {"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    (codex / "config.toml").write_text('model = "gpt"\n\n[mcp_servers.other]\ncommand = "x"\n')
    config, toml = claude / ".claude.json", codex / "config.toml"

    assert session_skill.status()[config] == session_skill.MISSING
    written = session_skill.install()
    assert config in written and toml in written

    saved = json.loads(config.read_text())
    assert saved["theme"] == "dark" and saved["mcpServers"]["other"] == {"command": "x"}
    entry = saved["mcpServers"]["jarvis"]
    assert entry["args"][:3] == ["-P", "-m", "jarvis.agent_mcp"] and entry["type"] == "stdio"
    text = toml.read_text()
    assert text.startswith('model = "gpt"') and "[mcp_servers.other]" in text
    assert text.count("[mcp_servers.jarvis]") == 1
    assert session_skill.status()[config] == session_skill.INSTALLED
    assert session_skill.status()[toml] == session_skill.INSTALLED

    session_skill.install()
    assert toml.read_text() == text, "installing again replaces its block, never adds one"


def test_install_leaves_a_file_it_cannot_read_alone(homes):
    session_skill, claude, codex = homes
    (claude / ".claude.json").write_text("{not json")
    (codex / "config.toml").write_text('[mcp_servers.jarvis]\ncommand = "theirs"\n')
    written = session_skill.install()
    assert claude / ".claude.json" not in written and codex / "config.toml" not in written
    assert (claude / ".claude.json").read_text() == "{not json"
    assert session_skill.status()[codex / "config.toml"] == session_skill.MISSING
