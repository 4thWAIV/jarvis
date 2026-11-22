"""The sessions a package is made with (sessions.py, sessions.yaml): the
four model tiers and the sweep (model_tiers.py), each role's launch and
tools (roles.py, role_mcp.py) on the brains' own transports, and the
sessions' inputs and outputs, with the model faked at the session.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from jarvis import (brain, brains_config, claude_provider, codex_provider, engine_choice,
                    evidence_types, jarvis_mcp, model_tiers, role_files, role_lookups,
                    role_mcp, role_prompts, roles, sessions, text_parts)

FAKE_CODEX = Path(__file__).parent / "fixtures" / "fake_codex.py"


# ── the tiers and the sweep ───────────────────────────────────────────────

def test_the_four_tiers_are_his():
    assert {t: (model_tiers.family_of("claude", t), model_tiers.family_of("codex", t))
            for t in model_tiers.TIERS} == {1: ("fable", "astra"), 2: ("opus", "sol"),
                                           3: ("sonnet", "terra"), 4: ("haiku", "luna")}


def test_codex_resolves_the_newest_of_a_family_by_the_version_in_its_name():
    listed = ["gpt-6-sol", "gpt-6.2-sol", "gpt-5.6-sol", "gpt-6.1-sol", "gpt-6.2-astra"]
    assert model_tiers.resolve("codex", "sol", listed) == \
        ["gpt-6.2-sol", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"]


def test_the_sweep_goes_down_the_family_then_up_a_model_oldest_first():
    listed = ["gpt-6.2-astra", "gpt-6-astra", "gpt-6.1-terra", "gpt-5.6-terra",
              "gpt-6.2-sol", "gpt-5.6-sol", "gpt-6-luna"]
    assert model_tiers.sweep("codex", 3, listed) == [
        "gpt-6.1-terra", "gpt-5.6-terra",            # down its versions, newest first
        "gpt-5.6-sol", "gpt-6.2-sol"]               # up one model, oldest first, and no further
    assert model_tiers.sweep("codex", 1, listed) == ["gpt-6.2-astra", "gpt-6-astra"]


def test_a_family_not_listed_is_swept_past_and_none_at_all_is_no_model():
    assert model_tiers.first("codex", 4, ["gpt-5.6-terra"]) == "gpt-5.6-terra"
    with pytest.raises(model_tiers.NoModel):
        model_tiers.first("codex", 1, ["gpt-5.6-luna"])
    with pytest.raises(model_tiers.NoModel):          # two tiers up is past the next model
        model_tiers.first("codex", 4, ["gpt-6.2-sol"])


def test_claude_is_its_alias_latest_only_and_lists_fable():
    assert claude_provider.ClaudeProvider().models() == ["fable", "opus", "sonnet", "haiku"]
    assert claude_provider.ClaudeProvider.default_model == "sonnet"
    assert model_tiers.sweep("claude", 2, ["fable", "opus", "sonnet", "haiku"]) == \
        ["opus", "fable"]


def test_codex_falls_back_to_the_sweep_never_a_fixed_name():
    assert codex_provider.fallback_model(["gpt-6.2-astra", "gpt-6.1-terra", "gpt-5.6-terra"]) \
        == "gpt-6.1-terra"
    assert codex_provider.fallback_model(["gpt-6.2-sol"]) == "gpt-6.2-sol"
    assert codex_provider.fallback_model(["gpt-6-astra"]) is None   # Codex's own default
    assert codex_provider.fallback_model([]) is None
    assert not hasattr(codex_provider, "DEFAULT_MODEL")


def _listed(engine):
    return {"claude": ["fable", "opus", "sonnet", "haiku"],
            "codex": ["gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-terra", "gpt-6-luna"]}[engine]


def test_a_role_s_brain_comes_from_engines_json_and_its_model_from_its_tier():
    brains_config.add("openai")
    pick = model_tiers.pick("owners", 2, requester="codex", listed=_listed)
    assert (pick.engine, pick.model, pick.why) == ("codex", "gpt-6.2-sol", "requester")
    engine_choice.set_kind("marking", "fixed", "claude", None)
    assert model_tiers.pick("marking", 3, listed=_listed).model == "sonnet"
    engine_choice.set_kind("marking", "fixed", "claude", "opus")
    assert model_tiers.pick("marking", 3, listed=_listed).model == "opus", \
        "a model named in Settings is his choice"


def test_the_owners_go_on_the_other_brain_only_when_set_and_two_brains_exist():
    engine_choice.set_owners_other_brain(True)
    one = model_tiers.pick("owners", 2, requester="claude", author_engine="claude",
                           listed=_listed)
    assert one.engine == "claude", "ignored with only one brain"
    brains_config.add("openai")
    other = model_tiers.pick("owners", 2, requester="claude", author_engine="claude",
                             listed=_listed)
    assert (other.engine, other.model, other.why) == ("codex", "gpt-6.2-sol",
                                                      "owners_other_brain")
    assert model_tiers.pick("editor", 1, requester="claude", author_engine="claude",
                            listed=_listed).engine == "claude", "the owners only"
    engine_choice.set_owners_other_brain(False)
    assert model_tiers.pick("owners", 2, requester="claude", author_engine="claude",
                            listed=_listed).engine == "claude"


def test_every_role_is_an_entry_in_engines_json_on_the_requester_s_engine():
    loaded = engine_choice.load()
    for role in roles.ROLES.values():
        assert role.kind in engine_choice.KINDS
    for kind in engine_choice.ROLE_KINDS:
        assert loaded["kinds"][kind] == {"pick": "requester"}
    assert loaded["owners_other_brain"] is False
    assert engine_choice.view()["owners_other_brain"] is False


def test_a_file_saved_with_the_judges_entry_is_read_as_the_owners():
    path = engine_choice._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kinds": {"judges": {"pick": "least_stress"},
                                          "theory_of_mind_reader": {"pick": "least_stress"}},
                                "judges_other_brain": True}))
    loaded = engine_choice.load()
    assert loaded["kinds"]["owners"] == {"pick": "least_stress"}
    assert loaded["kinds"]["tims_reader"] == {"pick": "least_stress"}
    assert loaded["owners_other_brain"] is True


# ── each role's tools and launch ──────────────────────────────────────────

def test_the_roles_have_his_tiers_efforts_and_tools():
    by = roles.ROLES
    assert {n: (r.tier, r.effort) for n, r in by.items()} == {
        "author": (1, "high"), "editor": (1, "medium"),
        "context_owner": (2, "medium"), "structure_owner": (2, "medium"),
        "tims_reader": (1, "high"), "retrieval": (3, "low"), "marking": (3, "low"),
        "classification": (3, "low"), "extraction": (4, "low"), "summarization": (3, "low")}
    research = set(roles.RESEARCH_TOOLS)
    assert {"search_history", "read_history"} <= research
    files = set(roles.FILE_READ_TOOLS)
    assert set(by["author"].tools) == research | files and by["author"].release.writes
    assert by["author"].release.builtins == ("Write", "Edit"), "it reads with its file tools"
    assert (set(by["context_owner"].tools) == research | files
            == set(by["structure_owner"].tools))
    assert by["context_owner"].kind == by["structure_owner"].kind == "owners"
    assert by["tims_reader"].kind == "tims_reader"
    assert by["editor"].tools == ("knowledge_search", *roles.FILE_READ_TOOLS)
    for name in ("author", "editor", "context_owner", "structure_owner"):
        assert by[name].release.reads_files, name
        assert not {"Read", "Grep", "Glob", "Bash"} & set(by[name].release.builtins), name
    for name in ("tims_reader", "marking", "classification", "extraction", "summarization"):
        assert by[name].release == roles.LOCKDOWN, name


def test_no_role_has_a_sending_command_or_a_tool_the_brain_lacks():
    from jarvis import agent_mcp
    sending = {c.name for c in agent_mcp.COMMANDS} | {s["name"] for s in agent_mcp.PACKAGE_SPECS}
    known = ({s["name"] for s in jarvis_mcp.TOOL_SPECS} | set(role_lookups.NAMES)
             | set(role_files.NAMES))
    for role in roles.ROLES.values():
        assert not set(role.tools) & sending, role.name
        assert set(role.tools) <= known, (role.name, set(role.tools) - known)
        assert "Bash" not in role.release.builtins, f"{role.name}: no role has a shell"
    assert not set(role_files.NAMES) & {s["name"] for s in jarvis_mcp.TOOL_SPECS}, \
        "a file tool is the role server's own, never a brain tool of the same name"


def test_retrieval_has_its_read_tools_and_its_github_lookups_and_no_shell():
    retrieval = roles.role("retrieval")
    assert retrieval.release.builtins == () and not retrieval.release.reads_files
    assert set(retrieval.tools) == set(roles.RETRIEVAL_TOOLS) | set(role_lookups.NAMES)
    names = {s["name"] for s in role_mcp.tool_specs("retrieval")}
    assert names == set(retrieval.tools), "its server offers every one of its tools, no more"


def test_a_reading_role_s_server_offers_its_file_tools_over_its_folders(tmp_path):
    run, evidence = tmp_path / "run", tmp_path / "evidence"
    chat = sessions.BrainChatSession(
        "editor", "R", run, reads=(evidence,),
        pick=model_tiers.Pick("claude", "opus", 1, "fixed", ["opus"]))
    entry = json.loads(chat._mcp_config().read_text())["mcpServers"][roles.SERVER_NAME]
    args = entry["args"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--read"] == [str(run), str(evidence)]
    names = {s["name"] for s in role_mcp.tool_specs("editor")}
    assert names == {"knowledge_search", *roles.FILE_READ_TOOLS}
    judge = sessions.BrainChatSession(
        "tims_reader", "R", tmp_path / "j",
        pick=model_tiers.Pick("claude", "opus", 2, "fixed", ["opus"]))
    assert judge._mcp_config() is None, "a role with no tools has no server"


def _files(tmp_path):
    run, evidence = tmp_path / "run", tmp_path / "evidence"
    (run / ".jarvis-role").mkdir(parents=True)
    (run / ".jarvis-role" / "editor-mcp.json").write_text("{}")
    (run / "prep.md").write_text("# Prep\nThe move is ready.\n")
    (evidence / "docs").mkdir(parents=True)
    (evidence / "docs" / "notes.md").write_text("one\nThe MOVE waits on PR 180.\nthree\n")
    (evidence / ".env").write_text("SECRET=1\n")
    (tmp_path / "outside.md").write_text("The move, outside.\n")
    (evidence / "link.md").symlink_to(tmp_path / "outside.md")
    return run, evidence, role_files.Files([run, evidence])


def test_the_file_tools_list_read_and_search_only_inside_the_role_s_folders(tmp_path):
    run, evidence, files = _files(tmp_path)
    ok, text = role_files.call(files, "list_package_files", {})
    listed = json.loads(text)
    assert ok and listed["folder"] == str(run)
    assert [e["name"] for e in listed["entries"]] == ["prep.md"], "hidden files are not shown"
    ok, text = role_files.call(files, "list_package_files", {"path": str(evidence)})
    assert [e["name"] for e in json.loads(text)["entries"]] == ["docs", "link.md"]
    ok, text = role_files.call(files, "read_package_file", {"path": "prep.md"})
    assert ok and json.loads(text)["lines"] == ["1\t# Prep", "2\tThe move is ready."]
    ok, text = role_files.call(files, "search_package_files", {"text": "the move", "path": str(evidence)})
    assert ok and json.loads(text)["lines"] == [
        f"{evidence / 'docs' / 'notes.md'}:2: The MOVE waits on PR 180."], "never through a link"
    for path in (str(tmp_path / "outside.md"), "../outside.md", str(evidence / "link.md"),
                 str(evidence / ".env"), ".jarvis-role/editor-mcp.json", "/etc/hosts"):
        ok, text = role_files.call(files, "read_package_file", {"path": path})
        assert not ok, path


def test_a_role_s_server_answers_its_file_tools_and_refuses_another_s(tmp_path, monkeypatch):
    run, evidence, files = _files(tmp_path)
    monkeypatch.setattr(role_mcp, "FILES", files)
    monkeypatch.setattr(role_mcp, "ROLE", "editor")
    ok, text, _ = role_mcp.call("read_package_file", {"path": "prep.md"})
    assert ok and "The move is ready." in text
    monkeypatch.setattr(role_mcp, "ROLE", "tims_reader")
    ok, text, _ = role_mcp.call("read_package_file", {"path": "prep.md"})
    assert not ok and "no tool" in text


def test_each_github_lookup_is_one_fixed_gh_argument_list_with_its_data():
    assert role_lookups.argv("github_pr_view", {"repo": "4thWAIV/jarvis", "number": 176}) == [
        "pr", "view", "176", "--repo=4thWAIV/jarvis",
        "--json=" + role_lookups._PR_VIEW_FIELDS]
    assert role_lookups.argv("github_run_list", {"repo": "o/r", "branch": "main",
                                                 "commit": "4ffd18b6"})[-2:] == [
        "--branch=main", "--commit=4ffd18b6"]
    words = role_lookups.argv("github_search_prs", {"query": "--web; rm -rf /"})
    assert words[-2:] == ["--", "--web; rm -rf /"], "search words are data after --"
    assert role_lookups.argv("github_branches", {"repo": "o/r"}) == [
        "api", "--method=GET", "repos/o/r/branches", "--paginate"]
    assert role_lookups.argv("github_commit_view", {"repo": "4thWAIV/jarvis",
                                                    "sha": "4ffd18b6"}) == [
        "api", "--method=GET", "repos/4thWAIV/jarvis/commits/4ffd18b6",
        "--jq=" + role_lookups._COMMIT_JQ]
    for name, bad in (("github_pr_view", {"repo": "o/r; ls", "number": 1}),
                      ("github_pr_view", {"repo": "o/r", "number": "1 --web"}),
                      ("github_run_view", {"repo": "o/r", "run_id": True}),
                      ("github_run_view", {"repo": "o/r", "run_id": "x"}),
                      ("github_run_list", {"repo": "o/r", "branch": "a b"}),
                      ("github_pr_list", {"repo": "o/r", "state": "draft"}),
                      ("github_search_commits", {"query": "  "}),
                      ("github_commit_view", {"repo": "o/r"}),
                      ("github_commit_view", {"repo": "o/r", "sha": "main/../x"})):
        with pytest.raises(role_lookups.Refused):
            role_lookups.argv(name, bad)
    for bad in ({"repo": "o/r"}, {"repo": "o/r", "name": "a..b"},
                {"repo": "o/r", "name": "--x"}):
        with pytest.raises(role_lookups.Refused):
            role_lookups.branch_name(bad)


def test_retrieval_finds_what_a_reference_names_and_never_fetches_it():
    # sessions.yaml RetrievalSession: "it works out what a reference names;
    # it never fetches, copies, summarises or judges"; the program fetches
    # (workflow.yaml gather resolve_typed). No lookup returns a diff, a
    # description, a CI run's jobs or logs, or a branch's commits.
    assert set(role_lookups.NAMES) == {
        "github_pr_list", "github_pr_view", "github_run_list", "github_run_view",
        "github_issue_list", "github_issue_view", "github_search_prs",
        "github_search_issues", "github_commit_view", "github_search_commits",
        "github_branches"}
    every = [role_lookups.argv(name, {"repo": "o/r", "number": 1, "run_id": 2, "sha": "abcd",
                                      "query": "words"}) for name in role_lookups.NAMES]
    for args in every:
        assert "diff" not in args and "--log-failed" not in args and "--log" not in args
        assert not any("compare/" in a for a in args)
        for a in args:
            if a.startswith("--json="):
                assert not {"jobs", "body", "comments", "files"} & set(a[7:].split(","))
    commit = role_lookups.argv("github_commit_view", {"repo": "o/r", "sha": "abcd"})
    assert "files" not in commit[-1] and "patch" not in commit[-1]


def test_the_program_fetches_what_each_github_type_names():
    # workflow.yaml entry_types and gather retrieved/: a PR's description and
    # diff, a CI run's jobs and failed logs, a branch's commits not on main;
    # the program's own fixed calls, never a role's tool.
    from jarvis import evidence_readers as er
    assert er.pr_diff_args("4thWAIV/jarvis", "176") == [
        "pr", "diff", "176", "--repo=4thWAIV/jarvis", "--color=never"]
    assert "body" in er._PR_FIELDS.split(",")
    assert er.run_view_args("o/r", "42")[-1].split(",")[-1] == "jobs"
    assert er.run_failed_logs_args("o/r", "42") == [
        "run", "view", "42", "--repo=o/r", "--log-failed"]
    assert er.branch_commits_args("o/r", "package/v2-sessions") == [
        "api", "--method=GET", "repos/o/r/compare/main...package/v2-sessions", "--paginate",
        "--jq=" + er._BRANCH_COMMITS_JQ]


def test_a_github_lookup_runs_in_the_role_s_server_never_forwarded(monkeypatch):
    calls, forwarded = [], []

    async def fake_gh(args, timeout):
        assert timeout is None, "the design gives a lookup no deadline"
        calls.append(args)
        return 0, '{"number": 176}', ""
    monkeypatch.setattr(role_lookups.gh_lookup, "gh_path", lambda: "/usr/bin/gh")
    monkeypatch.setattr(role_lookups.gh_lookup, "_run_gh", fake_gh)
    monkeypatch.setattr(jarvis_mcp, "_forward",
                        lambda tool, args, role="": forwarded.append(tool) or (True, "", None))
    monkeypatch.setattr(role_mcp, "ROLE", "retrieval")
    ok, text, _ = role_mcp.call("github_pr_view", {"repo": "o/r", "number": 176})
    assert ok and text == '{"number": 176}' and calls[0][:3] == ["pr", "view", "176"]
    ok, text, _ = role_mcp.call("github_pr_view", {"repo": "o/r", "number": "x"})
    assert not ok and text.startswith("Refused") and len(calls) == 1
    assert role_mcp.call("work_detail", {"work": "w1"})[0] and forwarded == ["work_detail"]
    monkeypatch.setattr(role_mcp, "ROLE", "editor")
    assert not role_mcp.call("github_pr_view", {"repo": "o/r", "number": 1})[0]
    assert len(calls) == 1, "no other role reaches a lookup"


def test_a_role_s_tool_server_offers_only_its_tools(monkeypatch):
    names = {s["name"] for s in role_mcp.tool_specs("editor")}
    assert names == {"knowledge_search", *roles.FILE_READ_TOOLS}
    monkeypatch.setattr(role_mcp, "ROLE", "editor")
    forwarded = []
    monkeypatch.setattr(jarvis_mcp, "_forward",
                        lambda tool, args, role="": forwarded.append((tool, role)) or
                        (True, "ok", None))
    listed = role_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert ([t["name"] for t in listed["result"]["tools"]]
            == ["knowledge_search", *roles.FILE_READ_TOOLS])
    ok, text, _ = role_mcp.call("work_status", {})
    assert not ok and "no tool" in text and not forwarded
    assert role_mcp.call("knowledge_search", {"query": "x"})[0]
    assert forwarded == [("knowledge_search", "editor")]


def _config(tmp_path, role, **kw):
    launch = roles.Launch(role=roles.role(role), mcp_config=tmp_path / "mcp.json",
                          reads=(tmp_path / "evidence",), **kw)
    return brain.BrainConfig(home=tmp_path, provider="anthropic", model="opus", effort="high",
                             role=launch)


def test_claude_launches_a_role_on_jarvis_s_own_settings_with_only_its_tools(tmp_path):
    cmd = claude_provider.role_command("claude", _config(tmp_path, "author", session_id="u-1"),
                                       "THE ROLE")
    assert cmd[:4] == ["claude", "-p", "--restricted", "--strict-mcp-config"]
    assert cmd[cmd.index("--system-prompt") + 1] == "THE ROLE"
    assert cmd[cmd.index("--tools") + 1] == "Write,Edit", "no built-in Read, Grep or Glob"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--session-id") + 1] == "u-1"
    assert "--add-dir" not in cmd, "it writes in its run folder only"
    allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--model")]
    assert roles.tool_name("search_history") in allowed
    assert {roles.tool_name(t) for t in roles.FILE_READ_TOOLS} <= set(allowed)
    reader = claude_provider.role_command("claude", _config(tmp_path, "editor"), "R")
    assert reader[reader.index("--tools") + 1] == "", "it reads with its file tools alone"
    assert "--add-dir" not in reader and "--permission-mode" not in reader
    assert roles.tool_name("read_package_file") in reader
    for never in ("--dangerously-skip-permissions", "--append-system-prompt", "--safe-mode",
                  "--bare", "--setting-sources", "--no-session-persistence"):
        assert never not in cmd


def test_a_one_shot_role_has_its_form_no_session_and_two_turns(tmp_path):
    form = {"type": "object"}
    cmd = claude_provider.role_command("claude", _config(tmp_path, "extraction",
                                                        answer_format=form), "R")
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--mcp-config" not in cmd and "--allowedTools" not in cmd
    assert "--no-session-persistence" in cmd and "--session-id" not in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == form
    assert cmd[cmd.index("--max-turns") + 1] == "2"
    retrieval = claude_provider.role_command("claude", _config(tmp_path, "retrieval"), "R")
    allowed = retrieval[retrieval.index("--allowedTools") + 1:]
    assert retrieval[retrieval.index("--tools") + 1] == "", "no shell, no built-in tool"
    assert roles.tool_name("github_pr_view") in allowed
    assert roles.tool_name("work_detail") in allowed
    assert not any(a.startswith("Bash") for a in allowed)
    assert "--max-turns" not in retrieval and "--permission-mode" not in retrieval


@pytest.fixture
def codex_log(tmp_path, monkeypatch):
    path = tmp_path / "codex.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(path))
    return lambda: [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_codex_runs_a_role_locked_down_in_jarvis_s_own_home(tmp_path, codex_log,
                                                                   monkeypatch):
    from jarvis import data_paths
    from tests.settings_fixtures import hold_setting
    hold_setting("codex_path", f"{sys.executable} {FAKE_CODEX}")
    (data_paths.codex_home() / "config.toml").write_text(
        "[mcp_servers.jarvis]\ncommand = \"x\"\n\n[mcp_servers.\"his drive\"]\ncommand = \"y\"\n")
    monkeypatch.setenv("FAKE_CODEX_ANSWER", json.dumps({"answer": "yes"}))
    pick = model_tiers.Pick("codex", "gpt-6-luna", 4, "fixed", ["gpt-6-luna"])
    form = {"type": "object", "properties": {"answer": {"type": "string"}}}
    chat = sessions.BrainChatSession("extraction", "THE ROLE", tmp_path, pick=pick, answer_format=form)
    reply = await chat.once("the text", 20)
    assert reply.answer == {"answer": "yes"} and chat.model == "gpt-6-luna"
    log = codex_log()
    start = next(e for e in log if "argv" in e and "app-server" in e["argv"])
    assert start["codex_home"] == str(data_paths.codex_home())
    disabled = [start["argv"][i + 1] for i, a in enumerate(start["argv"]) if a == "--disable"]
    assert "code_mode_host" in disabled, "the lockdown: no code-mode host"
    assert "enable_request_compression" not in disabled, "the kept plumbing stays"
    thread = next(e["thread"] for e in log if "thread" in e)
    assert thread["baseInstructions"] == "THE ROLE" and thread["developerInstructions"] is None
    assert thread["ephemeral"] is True and thread["personality"] == "none"
    assert thread["sandbox"] == "read-only" and thread["approvalPolicy"] == "never"
    assert thread["config"] == {"project_doc_max_bytes": 0, "project_root_markers": [],
                                "web_search": "disabled", "agents": {"enabled": False},
                                "include_apply_patch_tool": False}
    servers = [start["argv"][i + 1] for i, a in enumerate(start["argv"]) if a == "-c"]
    assert servers == ['mcp_servers."his drive".enabled=false',
                       "mcp_servers.jarvis.enabled=false"], \
        "every server of the home's off for its app server, none added"
    turn = next(e["turn"] for e in log if "turn" in e)
    assert turn["effort"] == "low" and turn["outputSchema"] == form


def _codex_role(tmp_path, name):
    from jarvis import data_paths
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {roles.SERVER_NAME: {
        "command": "py", "args": ["-m", "jarvis.role_mcp", "--role", name]}}}))
    launch = roles.Launch(role=roles.role(name), mcp_config=mcp, reads=(tmp_path / "evidence",))
    return launch, data_paths


def test_codex_releases_a_role_from_the_lockdown_to_exactly_its_release(tmp_path):
    run = tmp_path / "run"
    launch, _ = _codex_role(tmp_path, "editor")
    keep, config, overrides = codex_provider.role_release(launch, run)
    assert "code_mode_host" in keep and "shell_tool" not in keep, "its tools, never a shell"
    assert overrides == ["-c", 'mcp_servers.jarvis_role.command="py"',
                         "-c", 'mcp_servers.jarvis_role.default_tools_approval_mode="approve"',
                         "-c", 'mcp_servers.jarvis_role.args=["-m", "jarvis.role_mcp", '
                               '"--role", "editor"]']
    assert config["agents"] == {"enabled": False} and config["include_apply_patch_tool"] is False
    assert "default_permissions" not in config, "a reader reads with its file tools alone"
    author, _ = _codex_role(tmp_path, "author")
    keep, config, _ = codex_provider.role_release(author, run)
    assert "shell_tool" not in keep and config["include_apply_patch_tool"] is True
    profile = config["permissions"][config["default_permissions"]]
    assert profile["network"] == {"enabled": False}
    assert profile["filesystem"] == {":root": "deny", ":minimal": "read", ":slash_tmp": "deny",
                                     ":tmpdir": "deny", str(run): "write"}
    for name in roles.ROLES:
        released, _ = _codex_role(tmp_path, name)
        assert "shell_tool" not in codex_provider.role_release(released, run)[0], name
    for name in ("tims_reader", "marking",
                 "classification", "extraction", "summarization"):
        locked, _ = _codex_role(tmp_path, name)
        keep, config, overrides = codex_provider.role_release(locked, run)
        assert keep == codex_provider.ROLE_KEEP_FEATURES and not overrides, name
        assert "default_permissions" not in config and not config["include_apply_patch_tool"]


def test_codex_runs_retrieval_with_its_own_server_and_no_shell(tmp_path):
    launch, _ = _codex_role(tmp_path, "retrieval")
    keep, config, overrides = codex_provider.role_release(launch, tmp_path)
    assert keep == codex_provider.ROLE_KEEP_FEATURES | {"code_mode_host"}
    assert "shell_tool" not in keep and "default_permissions" not in config
    assert overrides and all("jarvis_role" in o for o in overrides if o != "-c")


def test_codex_switches_off_every_server_of_the_home_for_a_role(tmp_path):
    from jarvis import data_paths
    launch, _ = _codex_role(tmp_path, "retrieval")
    (data_paths.codex_home() / "config.toml").write_text(
        "[mcp_servers.jarvis]\ncommand = \"x\"\n[mcp_servers.jarvis.env]\nA = \"1\"\n"
        "[mcp_servers.jarvis_role]\ncommand = \"stale\"\n")
    _, config, overrides = codex_provider.role_release(launch, tmp_path)
    assert overrides[:2] == ["-c", "mcp_servers.jarvis.enabled=false"]
    assert "mcp_servers.jarvis_role.enabled=false" not in overrides, "its own server stays"
    assert 'mcp_servers.jarvis_role.command="py"' in overrides
    assert "mcp_servers" not in config, "a thread's own switch does not take"
    (data_paths.codex_home() / "config.toml").write_text("not = [toml")
    assert codex_provider.home_servers() == []
    (data_paths.codex_home() / "config.toml").unlink()
    assert codex_provider.home_servers() == []


@pytest.mark.asyncio
async def test_codex_starts_a_tool_role_released_and_without_the_sandbox_flag(tmp_path,
                                                                              codex_log):
    from tests.settings_fixtures import hold_setting
    hold_setting("codex_path", f"{sys.executable} {FAKE_CODEX}")
    pick = model_tiers.Pick("codex", "gpt-6.2-astra", 1, "fixed", ["gpt-6.2-astra"])
    chat = sessions.BrainChatSession("editor", "THE ROLE", tmp_path, pick=pick,
                                     reads=(tmp_path / "evidence",))
    await chat.open()
    await chat.close()
    log = codex_log()
    start = next(e for e in log if "argv" in e and "app-server" in e["argv"])
    disabled = [start["argv"][i + 1] for i, a in enumerate(start["argv"]) if a == "--disable"]
    assert "code_mode_host" not in disabled
    assert "computer_use" in disabled, "everything else stays off"
    assert any(a.startswith("mcp_servers.jarvis_role.command=") for a in start["argv"])
    assert "shell_tool" in disabled, "no role has a shell"
    thread = next(e["thread"] for e in log if "thread" in e)
    assert "default_permissions" not in thread["config"]


class _Brain:
    """A brain whose sessions refuse some models on their first turn, as
    Claude Code does, and answer on the rest."""

    def __init__(self, refused):
        self.refused, self.configs = set(refused), []
        brain_ = self

        class Session:
            def __init__(self, config, on_event, on_exit):
                self.config, self.on_event = config, on_event

            async def open(self, instructions):
                brain_.configs.append(self.config)

            async def send(self, text, answer_format=None):
                from jarvis import brain_provider
                if self.config.model in brain_.refused:
                    error = (f"There's an issue with the selected model ({self.config.model}). "
                             "It may not exist or you may not have access to it.")
                    self.on_event(self, brain_provider.TurnEnded(0, 0, error=error))
                else:
                    self.on_event(self, brain_provider.TurnEnded(0, 0, reply=f"on {self.config.model}"))

            async def close(self, timeout=5):
                pass
        self.Session = Session

    def session(self, config, on_event, on_exit):
        return self.Session(config, on_event, on_exit)


@pytest.mark.asyncio
async def test_a_model_refused_on_the_first_turn_is_swept_past(monkeypatch, tmp_path):
    from jarvis import brain_provider
    fake = _Brain(refused={"opus"})
    monkeypatch.setattr(brain_provider, "provider", lambda name=None: fake)
    lines = []
    pick = model_tiers.Pick("claude", "opus", 2, "fixed", ["opus", "fable"])
    chat = sessions.BrainChatSession("tims_reader", "R", tmp_path, pick=pick, on_log=lines.append)
    await chat.open()
    reply = await chat.ask("hello", 5)
    assert reply.text == "on fable" and chat.model == "fable"
    assert any("refused opus" in line for line in lines)
    first, second = fake.configs
    assert first.role.session_id and second.role.session_id != first.role.session_id
    assert first.resume is None and second.resume is None, "never --resume a refused try"
    assert chat.session == second.role.session_id
    fake.refused.add("fable")
    with pytest.raises(sessions.SessionFailed):        # refused after an answer: no sweep
        await chat.ask("again", 5)


@pytest.mark.asyncio
async def test_a_sweep_with_no_model_left_is_no_model(monkeypatch, tmp_path):
    from jarvis import brain_provider
    fake = _Brain(refused={"opus", "fable"})
    monkeypatch.setattr(brain_provider, "provider", lambda name=None: fake)
    pick = model_tiers.Pick("claude", "opus", 2, "fixed", ["opus", "fable"])
    chat = sessions.BrainChatSession("marking", "R", tmp_path, pick=pick)
    with pytest.raises(model_tiers.NoModel):
        await chat.once("hello", 5)
    resumed = sessions.BrainChatSession("editor", "R", tmp_path, resume="s-1",
                                   pick=model_tiers.Pick("claude", "fable", 1, "fixed", ["fable"]))
    monkeypatch.setattr(role_mcp, "entry", lambda role, log=None, folders=(): {"command": "x"})
    fake.refused = set()
    await resumed.open()
    assert fake.configs[-1].resume == "s-1" and fake.configs[-1].role.session_id is None


class _Silent:
    """A brain whose sessions take a turn and never answer: silent, or
    writing lines (`chatty`) without ever ending the turn."""

    def __init__(self, chatty=False):
        self.chatty, self.closed = chatty, []
        brain_ = self

        class Session:
            def __init__(self, config, on_event, on_exit):
                import time
                self.heard_at = time.monotonic()
                self._writing = None

            async def open(self, instructions):
                pass

            async def send(self, text, answer_format=None):
                import asyncio
                import time
                if brain_.chatty:
                    async def write():
                        while True:
                            self.heard_at = time.monotonic()
                            await asyncio.sleep(0.05)
                    self._writing = asyncio.create_task(write())

            async def close(self, timeout=5):
                if self._writing:
                    self._writing.cancel()
                brain_.closed.append(self)
        self.Session = Session

    def session(self, config, on_event, on_exit):
        return self.Session(config, on_event, on_exit)


@pytest.mark.asyncio
async def test_a_call_whose_brain_writes_nothing_fails_at_the_idle_bound(monkeypatch, tmp_path):
    """The hung marking call of a real run (2026-11-20): every role session
    call ends; one whose brain writes nothing for run_idle_sec fails, and its
    process is closed."""
    from jarvis import brain_provider, run_executor
    fake = _Silent()
    monkeypatch.setattr(brain_provider, "provider", lambda name=None: fake)
    monkeypatch.setattr(run_executor, "bounds", lambda timeout=None: (30.0, 0.3))
    chat = sessions.BrainChatSession("marking", "R", tmp_path,
                                     pick=model_tiers.Pick("claude", "sonnet", 3, "fixed",
                                                           ["sonnet"]))
    with pytest.raises(sessions.SessionFailed, match="run_idle_sec"):
        await asyncio.wait_for(chat.once("hello", None), 5)
    assert len(fake.closed) == 1, "the call's process is ended"


@pytest.mark.asyncio
async def test_a_call_that_writes_but_never_answers_fails_at_the_wall_clock(monkeypatch,
                                                                            tmp_path):
    from jarvis import brain_provider, run_executor
    fake = _Silent(chatty=True)
    monkeypatch.setattr(brain_provider, "provider", lambda name=None: fake)
    monkeypatch.setattr(run_executor, "bounds", lambda timeout=None: (0.6, 0.2))
    chat = sessions.BrainChatSession("marking", "R", tmp_path,
                                     pick=model_tiers.Pick("claude", "sonnet", 3, "fixed",
                                                           ["sonnet"]))
    with pytest.raises(sessions.SessionFailed, match="run_timeout_sec"):
        await asyncio.wait_for(chat.once("hello", None), 5)
    assert len(fake.closed) == 1


def test_a_calls_bounds_are_the_runs_own_settings():
    from jarvis import run_executor
    from tests.settings_fixtures import hold_setting
    hold_setting("run_timeout_sec", 120.0)
    hold_setting("run_idle_sec", 45.0)
    assert run_executor.bounds() == (120.0, 45.0)
    assert run_executor.bounds(77.0) == (77.0, 45.0), "a caller's own wall clock wins"


def test_model_refused_reads_both_brains_wording():
    assert sessions.model_refused("There's an issue with the selected model (fable). It may "
                                  "not exist or you may not have access to it.")
    assert sessions.model_refused("The 'gpt-6-luna' model is not supported when using Codex "
                                  "with a ChatGPT account.")
    assert not sessions.model_refused("the session ended before answering (exit 1)")


# ── the server answers a role only for its tools ──────────────────────────

def test_the_tool_channel_answers_a_role_only_for_its_own_tools(monkeypatch, tmp_path):
    import importlib
    from fastapi.testclient import TestClient
    from jarvis import data_paths
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    importlib.reload(data_paths)
    from jarvis import server
    importlib.reload(server)
    seen = []

    async def fake(args):
        seen.append(server._for_him())
        return "found"
    monkeypatch.setitem(server.TOOL_HANDLERS, "knowledge_search", fake)
    token = data_paths.ensure_tool_token()
    with TestClient(server.app) as client:
        def post(body):
            return client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                               json=body).json()
        refused = post({"tool": "work_status", "arguments": {}, "role": "editor"})
        assert refused["ok"] is False and "no tool" in refused["text"]
        assert post({"tool": "knowledge_search", "arguments": {}, "role": "editor"})["ok"]
        assert post({"tool": "knowledge_search", "arguments": {}})["ok"]
    assert seen == [False, True], "a role's call is never his turn; the brain's is"


# ── the splitter ──────────────────────────────────────────────────────────

def test_written_text_splits_into_sentences_and_their_parts_at_its_offsets():
    text = ("# The plan\nWe moved it, because Mr. Smith asked; it broke (twice) and we "
            "fixed it.\nThis wraps\nonto the next line.\n\n- First item\n- Second: done")
    found = text_parts.split(text)
    for seg in found:
        assert text[seg.start:seg.end] == seg.text
    sentences = [s.text for s in found if s.kind == text_parts.SENTENCE]
    assert sentences == ["# The plan",
                         "We moved it, because Mr. Smith asked; it broke (twice) and we fixed it.",
                         "This wraps\nonto the next line.", "- First item", "- Second: done"]
    parts = [s.text for s in found if s.within == "s2"]
    assert parts == ["We moved it,", "because Mr. Smith asked;", "it broke", "(twice)",
                     "and we fixed it."]
    assert text_parts.sentence_around(text, text.index("Smith"), text.index("Smith") + 5) == \
        sentences[1]


def test_spanish_joins_and_abbreviations():
    parts = [p.text for p in text_parts.split("Lo hizo la Sra. Pérez, pero falló porque sí.")
             if p.kind == text_parts.PHRASE]
    assert parts == ["Lo hizo la Sra. Pérez,", "pero falló", "porque sí."]


# ── the sessions, the model faked at the session ──────────────────────────

class _Once:
    def __init__(self, answer, seen=None):
        self.answer, self.seen = answer, seen if seen is not None else []

    def __call__(self, role, prompt, folder, **kwargs):
        self.seen.append({"role": role, "prompt": prompt, **kwargs})
        outer = self

        seen = outer.seen[-1]

        class Session:
            pick = model_tiers.Pick("claude", "sonnet", 3, "test", ["sonnet"])
            last_turn = None

            async def once(self, text, timeout):
                return await self.ask(text, timeout)

            async def open(self):
                pass

            async def fresh(self):
                pass

            async def close(self):
                pass

            async def ask(self, text, timeout, answer_format=None, strict=True):
                if "text" in seen:
                    outer.seen.append({**seen, "text": text})
                else:
                    seen["text"] = text
                answer = outer.answer(text) if callable(outer.answer) else outer.answer
                return sessions.Reply(json.dumps(answer), answer)
        return Session()


@pytest.mark.asyncio
async def test_extraction_keeps_only_values_at_their_positions(monkeypatch, tmp_path):
    text = "The test test_login failed. PR 42 fixed it."
    answer = {"values": [
        {"field": "test", "value": "test_login", "confidence": 0.9,
         "start": text.index("test_login"), "end": text.index("test_login") + 10},
        {"field": "pr", "value": "43", "confidence": 0.8, "start": 31, "end": 33}]}
    once = _Once(answer)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    out = await sessions.BrainExtractionSession(tmp_path).extract(
        text, [sessions.Field("test", "failed test name"), sessions.Field("pr", "PR number")], 5)
    assert out[0] == {"field": "test", "value": "test_login", "confidence": 0.9,
                      "position": {"start": 9, "end": 19}, "context": "The test test_login failed."}
    assert out[1] == {"field": "pr", "value": "", "confidence": 0.0, "position": None,
                      "context": ""}, "a value not at its position is dropped, never guessed"
    assert once.seen[0]["role"] == "extraction" and "<text>" + text in once.seen[0]["text"]
    assert "(Answer in the schema given with this call.)" in once.seen[0]["prompt"]


@pytest.mark.asyncio
async def test_marking_gives_each_sentence_its_top_labels(monkeypatch, tmp_path):
    text = "It broke, so we fixed it. Then it held."
    labels = [sessions.Label("background", "Why it matters.", "an example"),
              sessions.Label("filler", "Words that say nothing."),
              sessions.Label("jargon", "Words he does not use."),
              sessions.Label("thin", "Too little.")]
    # Smart passes (Tim 2026-11-21): whole sentences only, each with at most
    # its top 3 labels at 0.5 or more, none when none reaches 0.5.
    # The pages are shared across the workers (model_tiers.PARALLEL), so
    # each sentence may be a page of its own: each page answers its own ids.
    said = {"1:s1": [{"label": "background", "score": 0.5}], "1:s2": []}
    once = _Once(lambda text: {"parts": [
        {"id": i, "labels": said[i]} for i in re.findall(r'<part id="([^"]+)"', text)]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    marked = await sessions.BrainMarkingSession(tmp_path).mark(
        text, labels, [sessions.Known("He said fix it.", "knows")], 5)
    assert [m["id"] for m in marked] == ["s1", "s2"], "the sentences, never their phrases"
    assert marked[0] == {"id": "s1", "segment": "It broke, so we fixed it.", "kind": "sentence",
                         "position": {"start": 0, "end": 25}, "within": None,
                         "scores": {"background": 0.5}}
    assert marked[1]["scores"] == {}, "no label reached 0.5"
    sent = once.seen[0]["text"]
    assert '<material state="knows">He said fix it.</material>' in sent
    assert "s1.p1" not in sent
    form = once.seen[0]["answer_format"]["properties"]["parts"]["items"]["properties"]
    assert form["labels"]["items"]["properties"]["label"]["enum"] == [l.name for l in labels]
    assert form["labels"]["maxItems"] == 3
    assert form["labels"]["items"]["properties"]["score"]["minimum"] == 0.5
    top = [{"label": n, "score": 0.9} for n in ("background", "filler", "jargon", "thin")]
    for wrong in ({"parts": [{"id": "1:s1", "labels": []}]},                      # one missing
                  {"parts": [{"id": i, "labels": []} for i in ("1:s1", "1:s2", "9:s1")]},
                  {"parts": [{"id": i, "labels": [{"label": "made-up", "score": 1}]}
                             for i in ("1:s1", "1:s2")]},
                  {"parts": [{"id": i, "labels": [{"label": "filler", "score": 0.4}]}
                             for i in ("1:s1", "1:s2")]},                         # below 0.5
                  {"parts": [{"id": i, "labels": top} for i in ("1:s1", "1:s2")]}):  # four
        once.answer = wrong
        with pytest.raises(sessions.SessionFailed):
            await sessions.BrainMarkingSession(tmp_path).mark(text, labels, [], 5)


def _script(title="It broke.", narration="It broke on Tuesday.", words="", question="q1"):
    on_screen = [{"kind": "text", "text": "It broke.", "source": "", "position": "",
                  "path": "", "rows": []}]
    if words:
        on_screen.append({"kind": "his_words", "text": words, "source": "tim.md",
                          "position": "1-20", "path": "", "rows": []})
    return {"version": "script/1", "slides": [{
        "id": "opening", "title": title, "on_screen": on_screen, "narration": narration,
        "facts": [{"fact": "It broke.", "source": "ci.log", "position": "lines 1-3"}],
        "answers": [question], "decision": None}]}


def _scores_for(text, names=("background", "filler")):
    """A sparse marking answer for exactly the parts a call was given, the
    first label on each."""
    import re as _re
    return {"parts": [{"id": i, "labels": [{"label": names[0], "score": 0.6}]}
                      for i in _re.findall(r'<part id="([^"]+)"', text)]}


def _small_window(monkeypatch, answer_most):
    """A Claude model whose answer is at most `answer_most` tokens, so the
    pages are small enough to see."""
    monkeypatch.setitem(model_tiers.ANSWER_MOST, "claude", answer_most)


@pytest.mark.asyncio
async def test_several_files_share_a_page_each_given_whole(monkeypatch, tmp_path):
    # One worker: the pages made by the window alone, not shared across workers.
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 1, "codex": 1})
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]
    texts = [sessions.Text("files/a.md", "Alpha broke. We fixed it."),
             sessions.Text("files/b.md", "Beta broke, so we fixed it too.")]
    once = _Once(_scores_for)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    marked = await sessions.BrainMarkingSession(tmp_path).mark_files(
        texts, labels, [sessions.Known("Alpha broke. We fixed it.", sessions.KNOWS,
                                       "files/a.md")], 5)
    assert len(once.seen) == 1, "both files on one page"
    page = once.seen[0]["text"]
    assert '<file name="files/a.md">\n<text>Alpha broke. We fixed it.</text>' in page
    assert '<file name="files/b.md">\n<text>Beta broke, so we fixed it too.</text>' in page
    assert '<material state="knows" source="files/a.md"></material>' in page, \
        "a file on the page is given once: Tim's material names it by its state"
    assert [p["id"] for p in marked["files/b.md"]] == [
        s.id for s in text_parts.split(texts[1].text) if s.kind == text_parts.SENTENCE]
    assert marked["files/a.md"][0]["scores"] == {"background": 0.6}


@pytest.mark.asyncio
async def test_a_long_file_is_split_across_pages_at_its_section_breaks(monkeypatch, tmp_path):
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]
    body = " ".join(f"Point {n} holds." for n in range(1, 9))
    text = f"# One\n\n{body}\n\n# Two\n\n{body}\n\n# Three\n\n{body}\n"
    once = _Once(_scores_for)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    _small_window(monkeypatch, 700)
    marked = await sessions.BrainMarkingSession(tmp_path).mark_files(
        [sessions.Text("files/long.md", text)], labels, [], 5)
    assert len(once.seen) > 1, "too long for one page"
    assert [p["id"] for p in marked["files/long.md"]] == [
        s.id for s in text_parts.split(text) if s.kind == text_parts.SENTENCE]
    pieces = [re.findall(r'<part id="1:(s\d+)"[^>]*>([^<]*)</part>', c["text"])
              for c in once.seen]
    starts = [p[0][1] for p in pieces]
    assert all(s.startswith("# ") for s in starts[1:]), \
        f"each piece after the first starts at a heading: {starts}"
    for call in once.seen:
        assert f"<text>{text}</text>" in call["text"], "each piece given with its whole file"
    for p in marked["files/long.md"]:
        assert text[p["position"]["start"]:p["position"]["end"]] == p["segment"]


@pytest.mark.asyncio
async def test_a_page_not_answered_is_asked_again_once_then_halved(monkeypatch, tmp_path):
    # One worker: the pages made by the window alone, not shared across workers.
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 1, "codex": 1})
    text = "One broke. Two broke. Three broke. Four broke."
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]

    def answer(sent):
        found = _scores_for(sent)
        return found if len(found["parts"]) <= 2 else {"parts": found["parts"][:1]}
    once = _Once(answer)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    session = sessions.BrainMarkingSession(tmp_path)
    marked = await session.mark(text, labels, [], 5)
    assert len(marked) == 4 and sorted(c["text"].count("<part ") for c in once.seen) == \
        [2, 2, 4, 4], "asked, asked again, then each half"
    summary = session.paged.reports[-1].summary()
    assert (summary["pages"], summary["calls"], summary["asked_again"], summary["halved"]) == \
        (1, 4, 1, 1)
    once.answer = {"parts": []}
    with pytest.raises(sessions.SessionFailed):
        await sessions.BrainMarkingSession(tmp_path).mark("One broke.", labels, [], 5)


@pytest.mark.asyncio
async def test_pages_are_asked_in_parallel_each_worker_keeping_its_session(monkeypatch,
                                                                         tmp_path):
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]
    texts = [sessions.Text(f"files/{n}.md", f"File {n} broke, so we fixed it.")
             for n in range(8)]
    made, live, most = [], [0], [0]

    class Session:
        pick = model_tiers.Pick("claude", "sonnet", 3, "test", ["sonnet"])
        last_turn = None

        def __init__(self):
            self.opened, self.fresh_calls, self.asked = 0, 0, 0

        async def open(self):
            self.opened += 1

        async def fresh(self):
            self.fresh_calls += 1

        async def close(self):
            pass

        async def ask(self, text, timeout, answer_format=None, strict=True):
            live[0] += 1
            most[0] = max(most[0], live[0])
            await asyncio.sleep(0.02)
            live[0] -= 1
            self.asked += 1
            answer = _scores_for(text)
            return sessions.Reply(json.dumps(answer), answer)

    def for_role(role, prompt, folder, **kwargs):
        made.append(Session())
        return made[-1]
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", for_role)
    # Room for one file's answer at most: one sentence, its top labels.
    _small_window(monkeypatch, sessions._answer_most("parts", "labels", "1:s1",
                                                     ["background", "filler"]) + 1)
    marked = await sessions.BrainMarkingSession(tmp_path).mark_files(texts, labels, [], 5)
    assert len(marked) == 8
    asked = sum(s.asked for s in made)
    assert asked == 8, "one page a file at this window"
    assert len(made) == model_tiers.PARALLEL["claude"] == most[0], \
        "the brain's own number at once, one session each"
    assert all(s.opened == 1 and s.fresh_calls == s.asked - 1 for s in made), \
        "each opened once, a new conversation for each page after its first"


@pytest.mark.asyncio
async def test_a_script_is_marked_slide_by_slide_with_the_whole_script(monkeypatch, tmp_path):
    # One worker: the pages made by the window alone, not shared across workers.
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 1, "codex": 1})
    from jarvis import package_script
    script = _script(words="Fix the store.")
    script["slides"].append({**_script()["slides"][0], "id": "second",
                             "title": "What it costs."})
    units = package_script.units(script)
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]
    once = _Once(_scores_for)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    whole = package_script.dump(script)
    marked = await sessions.BrainMarkingSession(tmp_path).mark_slides(units, whole, labels, [],
                                                                      5)
    assert len(once.seen) == 1, "a small script is one page"
    page = once.seen[0]["text"]
    assert page.startswith(f"<text>{whole}</text>")
    assert '<slide id="opening">' in page and '<slide id="second">' in page
    words = next(m for m in marked if m["segment"] == "Fix the store.")
    assert words["slide"] == "opening" and words["field"] == "on_screen[1].text"
    assert words["id"] == "opening/on_screen[1].text/s1" and words["position"] == {
        "start": 0, "end": 14}
    assert words["scores"] == {"background": 0.6}
    # Room for the larger slide's answer, not both slides'.
    most = {slide: sum(sessions._answer_most("parts", "labels", f"1:{m['id']}",
                                             ["background", "filler"])
                       for m in marked if m["slide"] == slide) for slide in ("opening", "second")}
    _small_window(monkeypatch, max(most.values()) + 2)
    once.seen.clear()
    await sessions.BrainMarkingSession(tmp_path).mark_slides(units, whole, labels, [], 5)
    assert len(once.seen) == 2 and all(c["text"].startswith(f"<text>{whole}</text>")
                                       for c in once.seen), "every page given the script"
    assert "second" not in once.seen[0]["text"].split("</text>", 1)[1], \
        "no page holds part of two slides"


@pytest.mark.asyncio
async def test_classification_adds_background_and_takes_no_states(monkeypatch, tmp_path):
    # One worker: the pages made by the window alone, not shared across workers.
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 1, "codex": 1})
    once = _Once({"items": [{"id": "1", "questions": [{"question": "q1", "score": 0.9}]},
                            {"id": "2", "questions": []}]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    out = await sessions.BrainClassificationSession(tmp_path).classify(
        [sessions.Item("f1", "CI failed", "ci.log:3", "ci.log"),
         sessions.Item("f1", "It merged", "pr.md:1", "pr.md")],
        [sessions.ClassQuestion("q1", "Ship?")], 5)
    assert out == [{"item": "f1", "scores": {"q1": 0.9}},
                   {"item": "f1", "scores": {}}], \
        "two files' items on one page, each by its own id there, with only the questions " \
        "at 0.5 or more it was given"
    form = once.seen[0]["answer_format"]
    listed = form["properties"]["items"]["items"]["properties"]["questions"]
    entry = listed["items"]
    assert entry["properties"]["question"]["enum"] == ["q1", "background"]
    assert listed["maxItems"] == 3 and entry["properties"]["score"]["minimum"] == 0.5
    assert "state=" not in once.seen[0]["text"]


@pytest.mark.asyncio
async def test_retrieval_answers_typed_entries_or_not_found(monkeypatch, tmp_path):
    once = _Once({"references": [
        {"id": "r1", "resolved": "ok", "searched": "",
         "entries": [{"type": "pr", "repo": "o/r", "number": "176", "why": "named"},
                     {"type": "pr", "repo": "o/r", "why": "no number"}]},
        {"id": "r2", "resolved": "ok", "searched": "gh search prs", "entries": []}]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    out = await sessions.BrainRetrievalSession(tmp_path).resolve(
        [sessions.Reference("r1", "the judges PR"), sessions.Reference("r2", "that thing")], 5)
    assert out[0] == {"id": "r1", "resolved": "ok", "searched": "",
                      "entries": [{"type": "pr", "repo": "o/r", "number": "176", "why": "named"}]}
    assert out[1]["resolved"] == "not_found" and out[1]["searched"] == "gh search prs"
    assert set(evidence_types.NAMED_BY_LABEL) == set(evidence_types.FIELDS) - {"content", "label"}


@pytest.mark.asyncio
async def test_a_brain_decision_asks_each_question_apart_and_takes_the_most(monkeypatch,
                                                                          tmp_path):
    answers = iter(["yes", "no", "yes"])
    once = _Once(lambda text: {"answer": next(answers)})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    q = sessions.DecisionQuestion("ready", "Is it ready?", sessions.YES_NO)
    out = await sessions.BrainDecisionSession("tims_reader", tmp_path).decide("text", [q], 5)
    assert out == [{"id": "ready", "answer": "yes", "probability": pytest.approx(2 / 3)}]
    assert len(once.seen) == 3 and all(s["prompt"] == "Is it ready?" for s in once.seen)
    assert once.seen[0]["role_spec"].lifetime == roles.ONE_SHOT and not once.seen[0]["role_spec"].tools
    scale = sessions.DecisionQuestion("order", "How?", sessions.SCALE)
    assert sessions.agreement(scale, [3, 4, 9]) == (4, pytest.approx(2 / 3))


def test_the_program_applies_edits_only_where_their_old_text_is():
    text = "Alpha beta gamma."
    new, applied, refused = sessions.apply_edits(text, [
        {"start": 0, "end": 5, "old": "Alpha", "new": "A"},
        {"start": 6, "end": 10, "old": "BETA", "new": "b"},
        {"start": 11, "end": 16, "old": "gamma", "new": ""}])
    assert new == "A beta ." and len(applied) == 2 and refused[0]["old"] == "BETA"


def test_an_edit_whose_offsets_miss_applies_where_its_old_text_is_once():
    # owner-prompts.md: an edit names its old text; the program applies it
    # only where that text is exactly there.
    text = "Alpha beta gamma beta."
    new, applied, refused = sessions.apply_edits(text, [
        {"start": 0, "end": 4, "old": "gamma", "new": "G"},     # offsets miss, there once
        {"start": 0, "end": 4, "old": "beta", "new": "B"},      # there twice: which?
        {"start": 17, "end": 21, "old": "beta", "new": "b"}])   # at its offsets
    assert new == "Alpha beta G b." and [e["new"] for e in applied] == ["b", "G"]
    assert [e["new"] for e in refused] == ["B"]


# ── the swarm ─────────────────────────────────────────────────────────────

class _Participant:
    """A participant answering each turn as told: its sign-off (state and
    what is left) and asks; `gate` holds its answer until released."""

    def __init__(self, name, state="resolved", left=None, asks=None, log=None, gate=None):
        self.name, self.sent, self.state = name, [], state
        self.left, self.asks = left or [], asks or []
        self.log = log if log is not None else []
        self.gate = gate

    async def open(self):
        pass

    async def close(self):
        pass

    async def ask(self, text, timeout, answer_format=None, strict=True):
        self.sent.append(text)
        self.log.append(f"{self.name} asked")
        if self.gate is not None:
            await self.gate.wait()
        self.log.append(f"{self.name} answers")
        props = answer_format["properties"]
        answer = {"message": f"{self.name} speaks"}
        if "signoff" in props:
            concerns = props["signoff"]["items"]["properties"]["concern"]["enum"]
            answer.update(edits=[], ask_sender=self.asks, signoff=[
                {"concern": c, "state": self.state, "reason": "quoted" if self.state ==
                 "resolved" else "", "left": self.left} for c in concerns])
        return sessions.Reply(json.dumps(answer), answer)


CONCERNS = [sessions.Concern("cares", "Does Tim care about each part?", "tims_reader"),
            sessions.Concern("order", "First?", "structure_owner"),
            sessions.Concern("decision_ready", "Prepared?", "structure_owner")]


def _form(swarm, name):
    return sessions.owner_form(swarm.concerns_of(name), sessions.EDIT)


@pytest.mark.asyncio
async def test_owners_take_their_turns_together_each_sent_the_conversation_as_it_stood():
    import asyncio
    gate = asyncio.Event()
    log: list[str] = []
    people = {"tims_reader": _Participant("tims_reader", log=log, gate=gate),
              "structure_owner": _Participant("structure_owner", log=log, gate=gate)}
    swarm = sessions.BrainSwarmSession(people, CONCERNS, first={"tims_reader": "What he knows."})
    swarm.say("author", "The outline.")
    turn = asyncio.ensure_future(swarm.together(
        {n: ("Work on it.", _form(swarm, n)) for n in people}, 5))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert log == ["tims_reader asked", "structure_owner asked"], "both asked before either answered"
    gate.set()
    answers = await turn
    assert set(answers) == set(people)
    assert people["tims_reader"].sent[0] == "What he knows.\n\nauthor:\nThe outline.\n\nWork on it."
    assert [m["from"] for m in swarm.conversation] == ["author", "tims_reader", "structure_owner"]
    # Each hears the other's message next time, never its own.
    assert swarm._new_for("tims_reader") == "structure_owner:\nstructure_owner speaks"
    assert swarm._new_for("structure_owner") == "tims_reader:\ntims_reader speaks"


@pytest.mark.asyncio
async def test_each_owner_signs_its_own_concerns_in_its_turn_and_nobody_votes():
    left = [{"what": "Slide 3 buries the ask.", "fix": "Move it first.", "evidence_missing": ""}]
    people = {"tims_reader": _Participant("tims_reader", asks=["Who reviews it?"]),
              "structure_owner": _Participant("structure_owner", "not_yet", left)}
    swarm = sessions.BrainSwarmSession(people, CONCERNS)
    answers = await swarm.together({n: ("x", _form(swarm, n)) for n in people}, 5)
    for name, answer in answers.items():
        swarm.sign(name, answer, "pass-1")
        swarm.ask(name, answer, "pass-1")
    assert swarm.state("cares") == sessions.RESOLVED
    assert swarm.not_resolved() == ["order", "decision_ready"]
    assert swarm.left() == [{"concern": c, "owner": "structure_owner", **left[0]}
                            for c in ("order", "decision_ready")]
    assert [a["ask"] for a in swarm.to_send()] == ["Who reviews it?"]
    swarm.sent()
    assert swarm.to_send() == []
    result = swarm.result()
    assert {c["id"]: c["state"] for c in result["concerns"]} == {
        "cares": "resolved", "order": "not_yet", "decision_ready": "not_yet"}
    assert "votes" not in result and len(result["turns"]) == 2


def test_a_sign_off_that_says_nothing_is_not_yet_and_says_why():
    swarm = sessions.BrainSwarmSession({"structure_owner": _Participant("s")}, CONCERNS)
    signed = swarm.sign("structure_owner", {"signoff": [
        {"concern": "order", "state": "resolved", "reason": "", "left": []},
        {"concern": "cares", "state": "resolved", "reason": "not its concern", "left": []}]},
        "pass-1")
    assert [(s.concern, s.state) for s in signed] == [("order", "not_yet"),
                                                      ("decision_ready", "not_yet")]
    assert "gave no sign-off on order" in signed[0].left[0]["what"]
    assert swarm.state("cares") == sessions.OPEN, "only its own concerns"


@pytest.mark.asyncio
async def test_the_swarm_is_kept_and_restored_between_the_run_s_processes():
    people = {"tims_reader": _Participant("tims_reader", "not_yet",
                                          [{"what": "x", "fix": "y", "evidence_missing": ""}])}
    swarm = sessions.BrainSwarmSession(people, CONCERNS)
    answer = await swarm.answer("tims_reader", 5, "x", _form(swarm, "tims_reader"))
    swarm.sign("tims_reader", answer, "outline")
    swarm.ask("tims_reader", {"ask_sender": ["When?"]}, "outline")
    again = sessions.BrainSwarmSession(people, CONCERNS)
    again.restore(json.loads(json.dumps(swarm.saved())))
    assert again.state("cares") == sessions.NOT_YET and again.left() == swarm.left()
    assert again.conversation == swarm.conversation and again.spoke("tims_reader")
    assert again.to_send() == swarm.to_send()


def test_each_participant_is_given_exactly_its_share_of_what_tim_knows():
    material = [sessions.Known("He said ship it.", sessions.KNOWS, "chat"),
                sessions.Known("The Work tab shows it.", sessions.HAD_ACCESS),
                sessions.Known("CI failed twice.", sessions.PROBABLY_NOT, "ci.log")]
    for role in ("tims_reader",):
        values = sessions.share_values(role, material)
        assert set(values) == {"{knows}", "{had_access}"}
        assert "He said ship it." in values["{knows}"] and \
            "The Work tab" in values["{had_access}"]
        assert "CI failed" not in "".join(values.values()), "never what he does not know"
    for role in ("author", "editor", "context_owner", "structure_owner"):
        shown = sessions.share_values(role, material)[
            "{the evidence, each item with its knowledge state}"]
        assert 'state="probably_does_not_know"' in shown and 'state="knows"' in shown
    swarm = sessions.BrainSwarmSession({"tims_reader": _Participant("c"),
                                        "editor": _Participant("e")}, [], material=material)
    assert [k.state for k in swarm.share_of("tims_reader")] == [sessions.KNOWS,
                                                                sessions.HAD_ACCESS]
    assert len(swarm.share_of("editor")) == 3
    with pytest.raises(ValueError):
        sessions.share("author", [sessions.Known("x", "maybe")])


# ── the prompts ───────────────────────────────────────────────────────────

def test_every_role_has_its_approved_prompt_and_every_placeholder_is_filled():
    assert set(role_prompts.PROMPTS) == set(roles.ROLES)
    for prompt in role_prompts.PROMPTS.values():
        assert prompt.system
    with pytest.raises(role_prompts.Unfilled):
        sessions.role_prompt("retrieval", {})
    system, first = sessions.role_prompt("retrieval", {"{list}": "<reference id=\"r\">{x}</reference>"})
    assert first == "References: <reference id=\"r\">{x}</reference>"
    assert system.startswith("You work out what a free-text reference means.")
    owner = role_prompts.PROMPTS["structure_owner"].system
    assert owner.startswith("You are one of a small team making a briefing for Tim") and \
        ("Your questions: **Is the most relevant information first?** and **Is the decision "
         "properly prepared?**") in owner
    assert role_prompts.PROMPTS["tims_reader"].material.startswith("What Tim knows:\n{knows}")


def test_the_owner_prompts_are_owner_prompts_md_word_for_word():
    import re
    from jarvis import data_paths
    md = (data_paths.SOURCE_ROOT.parent / "jarvis" / ".agents" / "in-progress" /
          "package-quality" / "owner-prompts.md")
    if not md.is_file():
        pytest.skip("the design files are the owner's, outside the repository")
    parts = {t: b.strip() for t, b in (s.split("\n", 1) for s in
                                        re.split(r"^## ", md.read_text(), flags=re.M)[1:])}
    shared = parts["Shared part (every owner)"]
    for role, title in (("tims_reader", "Tim's reader"), ("context_owner", "Context owner"),
                        ("structure_owner", "Structure and decision owner")):
        prompt = role_prompts.PROMPTS[role]
        assert f"{prompt.system}\n\n{prompt.material}" == f"{shared}\n\n{parts[title]}"
    assert role_prompts.PROMPTS["editor"].system == parts["Editor (cooperative)"]
    assert role_prompts.PROMPTS["author"].system.endswith(
        "\n\n" + parts["Author: the outline (added to the approved Author prompt, which is "
                         "otherwise unchanged)"])
    assert role_prompts.AUTHOR_REVISE == parts["Author: one revision against the marks"]


# ── SummarizationSession ──────────────────────────────────────────────────

def test_a_summary_citing_a_path_not_in_its_sources_is_rejected():
    sources = [sessions.Source("knowledge_search: move", sessions.source_text(
                   [("/work/w1/notes.md", "The move is ready."),
                    ('/odd "quoted".md', "Odd.")])),
               sessions.Source("/folders/f1/plan.md", "")]
    said = [sessions.Summary("knowledge_search: move", "Ready.", ["/work/w1/notes.md"]),
            sessions.Summary("knowledge_search: move", "Odd.", ['/odd "quoted".md']),
            sessions.Summary("/folders/f1/plan.md", "The plan.", ["/folders/f1/plan.md"]),
            sessions.Summary("knowledge_search: move", "Made up.", ["/work/w1/other.md"])]
    kept = sessions.cited_in_sources(said, sources)
    assert [s.text for s in kept] == ["Ready.", "Odd.", "The plan."]


def test_a_summary_citing_another_source_s_path_is_rejected():
    sources = [sessions.Source("q1", sessions.source_text([("/a.md", "A.")])),
               sessions.Source("q2", sessions.source_text([("/b.md", "B.")]))]
    said = [sessions.Summary("q1", "A.", ["/a.md"]),
            sessions.Summary("q2", "Borrowed.", ["/a.md"]),
            sessions.Summary("q3", "Nowhere.", ["/a.md"]),
            sessions.Summary("q2", "Nothing bears on it.", [])]
    kept = sessions.cited_in_sources(said, sources)
    assert [s.text for s in kept] == ["A.", "Nothing bears on it."]


def test_the_summarization_role_is_his_tier_effort_prompt_and_entry():
    role = roles.ROLES[sessions.SUMMARIZATION]
    assert (role.tier, role.effort, role.kind, role.lifetime) == (
        3, "low", "summarization", roles.ONE_SHOT)
    assert role.release == roles.LOCKDOWN
    assert "summarization" in engine_choice.ROLE_KINDS
    assert engine_choice.load()["kinds"]["summarization"] == {"pick": "requester"}
    assert engine_choice.choose("summarization").engine == "claude"
    prompt = role_prompts.PROMPTS[sessions.SUMMARIZATION]
    assert prompt.system.startswith("(Answer in the schema given with this call.)\n\n"
                                    "You summarise what a search found, for one purpose.")
    assert prompt.placeholders() == ["{purpose}",
                                     "{sources: each id, then its hits with their paths}"]


def test_the_brain_summarization_says_what_it_lacks_without_its_prompt_or_role(
        monkeypatch, tmp_path):
    monkeypatch.delitem(role_prompts.PROMPTS, sessions.SUMMARIZATION)
    monkeypatch.delitem(roles.ROLES, sessions.SUMMARIZATION)
    with pytest.raises(sessions.SessionFailed) as failed:
        sessions.BrainSummarizationSession(tmp_path)
    assert "approved prompt" in str(failed.value) and "effort" in str(failed.value)


def test_the_brain_summarization_is_one_call_with_no_tools_and_checks_its_cites(
        monkeypatch, tmp_path):
    once = _Once({"summaries": [
        {"source_id": "q", "text": "Ready.", "cites": ["/a.md"]},
        {"source_id": "q", "text": "Invented.", "cites": ["/b.md"]}]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    sources = [sessions.Source("q", sessions.source_text([("/a.md", "It is ready.")]))]
    kept = sessions.BrainSummarizationSession(tmp_path).summarize(sources, "Work item w1")
    assert kept == [sessions.Summary("q", "Ready.", ["/a.md"])]
    assert len(once.seen) == 1 and once.seen[0]["role"] == "summarization"
    assert '<hit path="/a.md">It is ready.</hit>' in once.seen[0]["text"]
    assert once.seen[0]["text"].startswith("Work item w1\n<source id=\"q\">")


@pytest.mark.asyncio
async def test_a_turn_that_fails_leaves_its_first_message_and_the_others_words_to_give():
    class Fails:
        async def ask(self, text, timeout, answer_format=None, strict=True):
            raise sessions.SessionFailed("Connection lost")
    swarm = sessions.BrainSwarmSession({"structure_owner": Fails()}, CONCERNS,
                                       first={"structure_owner": "What Tim knows."})
    swarm.say("author", "The script.")
    with pytest.raises(sessions.SessionFailed):
        await swarm.answer("structure_owner", 5, "", _form(swarm, "structure_owner"))
    assert not swarm.spoke("structure_owner")
    assert swarm.saved()["seen"]["structure_owner"] == 0 and swarm.saved()["introduced"] == []
    again = swarm._new_for("structure_owner")
    assert again.startswith("What Tim knows.") and again.endswith("author:\nThe script.")


@pytest.mark.asyncio
async def test_codex_keeps_one_app_server_across_a_role_s_pages(tmp_path, codex_log,
                                                                monkeypatch):
    # Mark rebuilt for speed (Tim 2026-11-21): one session reused across a
    # role's pages where the brain allows: on Codex a new thread on the same
    # app server, not a new app server a call.
    from tests.settings_fixtures import hold_setting
    hold_setting("codex_path", f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.setenv("FAKE_CODEX_ANSWER", json.dumps({"answer": "yes"}))
    pick = model_tiers.Pick("codex", "gpt-6-luna", 4, "fixed", ["gpt-6-luna"])
    form = {"type": "object", "properties": {"answer": {"type": "string"}}}
    chat = sessions.BrainChatSession("marking", "THE ROLE", tmp_path, pick=pick,
                                     answer_format=form)
    await chat.open()
    first = (await chat.ask("page one", 20)).answer
    thread = chat.session
    await chat.fresh()
    second = (await chat.ask("page two", 20)).answer
    await chat.close()
    assert first == second == {"answer": "yes"} and chat.session != thread
    log = codex_log()
    assert sum(1 for e in log if "argv" in e and "app-server" in e["argv"]) == 1
    threads = [e["thread"] for e in log if "thread" in e]
    assert len(threads) == 2 and threads[0] == threads[1], "each page's thread started alike"
    assert [e["turn"]["threadId"] for e in log if "turn" in e] == ["thread-1", "thread-2"]


# ── rate limits and the run's report of each call ─────────────────────────

class _Limited:
    """A Codex-like brain: its first turn refused for a rate limit (its
    limits reported blocked first), every later one answered with usage."""

    def __init__(self):
        self.turns = 0
        brain_ = self

        class Session:
            def __init__(self, config, on_event, on_exit):
                self.on_event, self.turn_usage = on_event, None

            async def open(self, instructions):
                pass

            async def send(self, text, answer_format=None):
                from jarvis import brain_provider
                brain_.turns += 1
                if brain_.turns == 1:
                    self.on_event(self, brain_provider.UsageSeen(True, 1234.0, "primary", {}, {}))
                    self.on_event(self, brain_provider.TurnEnded(0, 0, error="usage limit"))
                    return
                self.on_event(self, brain_provider.UsageSeen(False, None, None, {}, {}))
                self.turn_usage = {"inputTokens": 900, "cachedInputTokens": 600,
                                   "outputTokens": 40}
                self.on_event(self, brain_provider.TurnEnded(900, 40, reply="{}"))

            async def close(self, timeout=5):
                pass
        self.Session = Session

    def session(self, config, on_event, on_exit):
        return self.Session(config, on_event, on_exit)


@pytest.mark.asyncio
async def test_a_rate_limited_turn_is_said_so_and_every_call_is_in_the_run_report(
        monkeypatch, tmp_path):
    from jarvis import brain_provider, run_report
    fake = _Limited()
    monkeypatch.setattr(brain_provider, "provider", lambda name=None: fake)
    pick = model_tiers.Pick("codex", "gpt-6.1-terra", 3, "fixed", ["gpt-6.1-terra"])
    doc = {"id": "j", "package": "p", "run": "run-1", "folder": str(tmp_path)}
    with run_report.step(doc, "mark"):
        chat = sessions.BrainChatSession("marking", "R", tmp_path, pick=pick)
        await chat.open()
        with pytest.raises(sessions.RateLimited) as limited:
            await chat.ask("one", 5)
        assert limited.value.resets_at == 1234.0
        await chat.ask("two", 5)
        await chat.close()
    timing = json.loads((tmp_path / run_report.TIMING).read_text())
    step, = timing["steps"]
    assert [c["outcome"] for c in step["calls"]] == ["rate_limited", "answered"]
    answered = step["calls"][1]
    assert (answered["role"], answered["engine"], answered["model"], answered["tier"],
            answered["effort"]) == ("marking", "codex", "gpt-6.1-terra", 3, "low")
    assert (answered["tokens_in"], answered["cached"], answered["tokens_out"]) == (900, 600, 40)
    assert step["app_server_starts"] == 1 and step["rate_limited"] == 1
    assert timing["totals"]["tokens"] == {"in": 900, "cached": 600, "out": 40}
    assert sessions.rate_limited("429 Too Many Requests") and not sessions.rate_limited("x")


@pytest.mark.asyncio
async def test_extraction_asks_every_file_on_one_session(monkeypatch, tmp_path):
    made = []

    class Session:
        pick = model_tiers.Pick("codex", "gpt-6.1-terra", 3, "t", ["gpt-6.1-terra"])
        answer_format = None

        def __init__(self, prompt):
            self.prompt, self.opened, self.fresh_calls, self.forms = prompt, 0, 0, []

        async def open(self):
            self.opened += 1

        async def fresh(self):
            self.fresh_calls += 1

        async def close(self):
            pass

        async def ask(self, text, timeout, answer_format=None, strict=True):
            self.forms.append(answer_format or self.answer_format)
            await asyncio.sleep(0.01)       # a turn takes time: the other worker starts
            return sessions.Reply("{}", {"values": []})

    def for_role(role, prompt, folder, answer_format=None, **kwargs):
        made.append(Session(prompt))
        made[-1].answer_format = answer_format
        return made[-1]
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", for_role)
    asks = [("a log", [sessions.Field("error", "an error")]),
            ("a transcript", [sessions.Field("command", "a command")]),
            ("another log", [sessions.Field("error", "an error")])]
    monkeypatch.setattr(model_tiers, "PARALLEL", {"codex": 1})
    out = await sessions.BrainExtractionSession(tmp_path).extract_many(asks, 5)
    assert len(made) == 1 and made[0].opened == 1 and made[0].fresh_calls == 2
    assert [f["properties"]["values"]["items"]["properties"]["field"]["enum"]
            for f in made[0].forms] == [["error"], ["command"], ["error"]], \
        "each text's form goes with its turn"
    assert [o[0]["field"] for o in out] == ["error", "command", "error"]
    # Item 38: one session per worker, as marking's workers keep theirs.
    made.clear()
    monkeypatch.setattr(model_tiers, "PARALLEL", {"codex": 2})
    out = await sessions.BrainExtractionSession(tmp_path).extract_many(asks, 5)
    assert len(made) == 2 and all(m.opened == 1 for m in made)
    assert sum(m.fresh_calls for m in made) == 1, "three texts on two workers' sessions"
    assert [o[0]["field"] for o in out] == ["error", "command", "error"], "in the asked order"


class _Held:
    """A role's session that counts what is done with it."""
    made = []

    def __init__(self, answer, engine="codex"):
        self.answer, self.opened, self.fresh_calls, self.closed, self.turns = answer, 0, 0, 0, []
        self.pick = model_tiers.Pick(engine, "m", 3, "t", ["m"])
        self.answer_format = None
        self.prompt = ""

    async def open(self):
        self.opened += 1

    async def fresh(self):
        self.fresh_calls += 1

    async def close(self):
        self.closed += 1

    async def ask(self, text, timeout, answer_format=None, strict=True):
        self.turns.append((text, answer_format))
        return sessions.Reply(json.dumps(self.answer), self.answer)


def _holding(monkeypatch, answer, engine="codex"):
    made = []

    def for_role(role, prompt, folder, answer_format=None, **kwargs):
        made.append(_Held(answer, engine))
        made[-1].prompt, made[-1].answer_format = prompt, answer_format
        return made[-1]
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", for_role)
    return made


@pytest.mark.asyncio
async def test_retrieval_keeps_one_session_across_its_calls_while_kept(monkeypatch, tmp_path):
    made = _holding(monkeypatch, {"references": []})
    kept = sessions.BrainRetrievalSession(tmp_path, keep=True)
    refs = [sessions.Reference("r1", "the judges PR")]
    await kept.resolve(refs, 5)
    await kept.resolve(refs, 5)
    assert len(made) == 1 and made[0].opened == 1 and made[0].fresh_calls == 1
    assert made[0].closed == 0
    await kept.close()
    assert made[0].closed == 1
    assert made[0].turns[0][1] is not None, "Codex takes the form with each turn"
    made.clear()
    await sessions.BrainRetrievalSession(tmp_path).resolve(refs, 5)
    assert made[0].closed == 1, "not kept: closed after its call"


def test_summarization_keeps_one_session_across_its_calls_while_kept(monkeypatch, tmp_path):
    made = _holding(monkeypatch, {"summaries": []}, engine="claude")
    sources = [sessions.Source("q", sessions.source_text([("/a.md", "It is ready.")]))]
    kept = sessions.BrainSummarizationSession(tmp_path, keep=True)
    kept.summarize(sources, "Work item w1")
    kept.summarize(sources, "Work item w2")
    assert len(made) == 1 and made[0].opened == 1 and made[0].fresh_calls == 1
    assert [t[1] for t in made[0].turns] == [None, None], \
        "Claude takes the form with its process, started again by fresh"
    kept.close()
    assert made[0].closed == 1

