"""The sessions a package is made with (sessions.py, sessions.yaml): the
four model tiers and the sweep (model_tiers.py), each role's launch and
tools (roles.py, role_mcp.py) on the brains' own transports, and the
sessions' inputs and outputs, with the model faked at the session.
"""
import asyncio
import json
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
    pick = model_tiers.pick("judges", 2, requester="codex", listed=_listed)
    assert (pick.engine, pick.model, pick.why) == ("codex", "gpt-6.2-sol", "requester")
    engine_choice.set_kind("marking", "fixed", "claude", None)
    assert model_tiers.pick("marking", 3, listed=_listed).model == "sonnet"
    engine_choice.set_kind("marking", "fixed", "claude", "opus")
    assert model_tiers.pick("marking", 3, listed=_listed).model == "opus", \
        "a model named in Settings is his choice"


def test_the_judges_go_on_the_other_brain_only_when_set_and_two_brains_exist():
    engine_choice.set_judges_other_brain(True)
    one = model_tiers.pick("judges", 2, requester="claude", author_engine="claude",
                           listed=_listed)
    assert one.engine == "claude", "ignored with only one brain"
    brains_config.add("openai")
    other = model_tiers.pick("judges", 2, requester="claude", author_engine="claude",
                             listed=_listed)
    assert (other.engine, other.model, other.why) == ("codex", "gpt-6.2-sol",
                                                      "judges_other_brain")
    assert model_tiers.pick("editor", 1, requester="claude", author_engine="claude",
                            listed=_listed).engine == "claude", "the judges only"
    engine_choice.set_judges_other_brain(False)
    assert model_tiers.pick("judges", 2, requester="claude", author_engine="claude",
                            listed=_listed).engine == "claude"


def test_every_role_is_an_entry_in_engines_json_on_the_requester_s_engine():
    loaded = engine_choice.load()
    for role in roles.ROLES.values():
        assert role.kind in engine_choice.KINDS
    for kind in engine_choice.ROLE_KINDS:
        assert loaded["kinds"][kind] == {"pick": "requester"}
    assert loaded["judges_other_brain"] is False
    assert engine_choice.view()["judges_other_brain"] is False


# ── each role's tools and launch ──────────────────────────────────────────

def test_the_roles_have_his_tiers_efforts_and_tools():
    by = roles.ROLES
    assert {n: (r.tier, r.effort) for n, r in by.items()} == {
        "author": (1, "high"), "editor": (1, "medium"),
        "decision_ready_judge": (2, "medium"), "background_judge": (2, "medium"),
        "cares_judge": (2, "medium"), "order_judge": (2, "medium"),
        "theory_of_mind_reader": (1, "high"), "retrieval": (3, "low"), "marking": (3, "low"),
        "classification": (3, "low"), "extraction": (4, "low"), "summarization": (3, "low")}
    research = set(roles.RESEARCH_TOOLS)
    assert {"search_history", "read_history"} <= research
    files = set(roles.FILE_READ_TOOLS)
    assert set(by["author"].tools) == research | files and by["author"].release.writes
    assert by["author"].release.builtins == ("Write", "Edit"), "it reads with its file tools"
    assert (set(by["decision_ready_judge"].tools) == research | files
            == set(by["background_judge"].tools))
    assert by["editor"].tools == ("knowledge_search", *roles.FILE_READ_TOOLS)
    for name in ("author", "editor", "decision_ready_judge", "background_judge"):
        assert by[name].release.reads_files, name
        assert not {"Read", "Grep", "Glob", "Bash"} & set(by[name].release.builtins), name
    for name in ("cares_judge", "order_judge", "theory_of_mind_reader", "marking",
                 "classification", "extraction", "summarization"):
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
        "cares_judge", "R", tmp_path / "j",
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
    monkeypatch.setattr(role_mcp, "ROLE", "cares_judge")
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
    for name in ("cares_judge", "order_judge", "theory_of_mind_reader", "marking",
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
    chat = sessions.BrainChatSession("cares_judge", "R", tmp_path, pick=pick, on_log=lines.append)
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

        class Session:
            async def once(self, text, timeout):
                outer.seen[-1]["text"] = text
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
async def test_marking_scores_every_part_against_every_label(monkeypatch, tmp_path):
    text = "It broke, so we fixed it."
    labels = [sessions.Label("background", "Why it matters.", "an example"),
              sessions.Label("filler", "Words that say nothing.")]
    ids = [s.id for s in text_parts.split(text)]
    once = _Once({"scores": [{"id": i, "scores": {"background": 0.5, "filler": 0}} for i in ids]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    marked = await sessions.BrainMarkingSession(tmp_path).mark(
        text, labels, [sessions.Known("He said fix it.", "knows")], 5)
    assert [m["id"] for m in marked] == ids == ["s1", "s1.p1", "s1.p2"]
    assert marked[1] == {"id": "s1.p1", "segment": "It broke,", "kind": "phrase",
                         "position": {"start": 0, "end": 9}, "within": "s1",
                         "scores": {"background": 0.5, "filler": 0.0}}
    sent = once.seen[0]["text"]
    assert '<material state="knows">He said fix it.</material>' in sent
    once.answer = {"scores": [{"id": "s1", "scores": {"background": 1, "filler": 0}}]}
    with pytest.raises(sessions.SessionFailed):
        await sessions.BrainMarkingSession(tmp_path).mark(text, labels, [], 5)


def _scores_for(text, names=("background", "filler")):
    """A marking answer scoring exactly the parts a call was given."""
    import re as _re
    ids = _re.findall(r'<part id="([^"]+)"', text)
    return {"scores": [{"id": i, "scores": {n: 0.1 for n in names}} for i in ids]}


@pytest.mark.asyncio
async def test_marking_goes_in_batches_of_whole_sentences_each_given_the_whole_file(
        monkeypatch, tmp_path):
    # sessions.yaml MarkingSession batches (Tim 2026-11-20): one call per
    # file failed on real models; each batch is given the whole file and
    # scores only its own parts, every part against every label.
    monkeypatch.setattr(sessions, "BATCH_PARTS", 4)
    text = " ".join(f"Part {n} broke, so we fixed it." for n in range(1, 5))
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]
    once = _Once(_scores_for)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    marked = await sessions.BrainMarkingSession(tmp_path).mark(text, labels, [], 5)
    assert [m["id"] for m in marked] == [s.id for s in text_parts.split(text)]
    assert len(once.seen) == 4, "three parts a sentence, at most four parts a batch"
    for call in once.seen:
        assert f"<text>{text}</text>" in call["text"], "each batch is given the whole file"
        assert call["text"].count("<part ") == 3
    once.seen.clear()
    await sessions.BrainMarkingSession(tmp_path).mark(
        text, labels, [sessions.Known(text, sessions.KNOWS, "notes.md")], 5)
    assert "<text>" not in once.seen[0]["text"], "given once, among Tim's material"


@pytest.mark.asyncio
async def test_a_batch_not_answered_whole_is_halved_and_asked_again(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions, "BATCH_PARTS", 40)
    text = "One broke. Two broke. Three broke. Four broke."
    labels = [sessions.Label("background", "Why."), sessions.Label("filler", "Nothing.")]

    def answer(sent):
        found = _scores_for(sent)
        return found if len(found["scores"]) <= 2 else {"scores": found["scores"][:1]}
    once = _Once(answer)
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    marked = await sessions.BrainMarkingSession(tmp_path).mark(text, labels, [], 5)
    assert len(marked) == 4 and [c["text"].count("<part ") for c in once.seen] == [4, 2, 2]
    once.answer = {"scores": []}
    with pytest.raises(sessions.SessionFailed):
        await sessions.BrainMarkingSession(tmp_path).mark("One broke.", labels, [], 5)


@pytest.mark.asyncio
async def test_a_script_is_marked_slide_by_slide_with_the_whole_script(monkeypatch, tmp_path):
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
    assert len(once.seen) == 2, "one batch a slide: no batch holds two slides' words"
    assert all(f"<text>{whole}</text>" in c["text"] for c in once.seen)
    assert "second" not in once.seen[0]["text"].split("</text>")[1]
    words = next(m for m in marked if m["segment"] == "Fix the store.")
    assert words["slide"] == "opening" and words["field"] == "on_screen[1].text"
    assert words["id"] == "opening/on_screen[1].text/s1" and words["position"] == {
        "start": 0, "end": 14}


@pytest.mark.asyncio
async def test_classification_adds_background_and_takes_no_states(monkeypatch, tmp_path):
    once = _Once({"scores": [{"item": "f1", "scores": {"q1": 0.9, "background": 0.1}}]})
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", once)
    out = await sessions.BrainClassificationSession(tmp_path).classify(
        [sessions.Item("f1", "CI failed", "ci.log:3")], [sessions.ClassQuestion("q1", "Ship?")], 5)
    assert out == [{"item": "f1", "scores": {"q1": 0.9, "background": 0.1}}]
    form = once.seen[0]["answer_format"]
    scores = form["properties"]["scores"]["items"]["properties"]["scores"]
    assert scores["required"] == ["q1", "background"]


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
    out = await sessions.BrainDecisionSession("cares_judge", tmp_path).decide("text", [q], 5)
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


@pytest.mark.asyncio
async def test_the_editor_s_edits_are_applied_by_the_program(tmp_path):
    class Chat:
        async def ask(self, text, timeout, answer_format=None, strict=True):
            self.sent, self.form = text, answer_format
            answer = {"edits": [{"start": 0, "end": 2, "old": "It", "new": "The store",
                                 "reason": "vague-referent", "source": "notes.md:1"}],
                      "sent_back": [{"start": 3, "end": 8, "old": "broke", "needs": "when"}]}
            return sessions.Reply(json.dumps(answer), answer)
    chat = Chat()
    out = await sessions.BrainEditingSession(chat).edit(
        "It broke.", [{"id": "s1", "segment": "It broke.", "kind": "sentence",
                       "position": {"start": 0, "end": 9}, "scores": {"vague-referent": 0.9}}],
        [sessions.Known("The store moved.", "probably_does_not_know")], 5)
    assert out["text"] == "The store broke." and out["sent_back"][0]["needs"] == "when"
    assert chat.form is sessions.EDIT_FORM and 'state="probably_does_not_know"' in chat.sent


# ── the swarm ─────────────────────────────────────────────────────────────

class _Participant:
    def __init__(self, name, votes=None, calls=None, log=None, changed=None):
        self.name, self.votes, self.sent = name, list(votes or []), []
        self.calls, self.log = list(calls or []), log if log is not None else []
        self.changed = list(changed or [])

    async def ask(self, text, timeout, answer_format=None, strict=True):
        self.sent.append(text)
        if answer_format is sessions.REOPEN_FORM:
            said = self.changed.pop(0)
            self.log.append(f"{self.name} judges {said}")
            answer = {"question": "q", "changed": said, "reason": f"{self.name} says {said}"}
        elif answer_format is sessions.VOTE_FORM:
            vote = self.votes.pop(0)
            self.log.append(f"{self.name} votes")
            answer = {"question": "q", "vote": vote, "reason": f"{self.name} says {vote}"}
        elif answer_format is sessions.TURN_FORM:
            self.log.append(self.name)
            answer = {"message": f"{self.name} speaks",
                      "call_vote": self.calls.pop(0) if self.calls else []}
        else:
            raise AssertionError(f"unexpected form {answer_format}")
        return sessions.Reply(json.dumps(answer), answer)


@pytest.mark.asyncio
async def test_the_swarm_relays_word_for_word_and_counts_votes():
    people = {"author": _Participant("author", ["yes"]),
              "editor": _Participant("editor", ["yes", "no"]),
              "cares_judge": _Participant("cares_judge", ["no", "no"]),
              "order_judge": _Participant("order_judge", ["yes", "yes"]),
              "theory_of_mind_reader": _Participant("theory_of_mind_reader")}
    swarm = sessions.BrainSwarmSession(people, [sessions.Question("cares", "Does Tim care?"),
                                           sessions.Question("enough", "Enough?",
                                                             author_votes=True)])
    swarm.say("author", "The script, as written.")
    await swarm.turn("editor", 5)
    assert people["editor"].sent[-1].endswith("author:\nThe script, as written.")
    assert '<question id="cares">Does Tim care?</question>' in people["editor"].sent[-1], \
        "the questions to prove, by id, on its first turn"
    await swarm.turn("theory_of_mind_reader", 5)
    assert people["theory_of_mind_reader"].sent[-1].endswith(
        "author:\nThe script, as written.\n\neditor:\neditor speaks")
    assert swarm.voters("cares") == ["editor", "cares_judge", "order_judge"]
    assert "author" in swarm.voters("enough")
    assert await swarm.vote("cares", 5) == sessions.PROVEN          # 2 yes, 1 no
    assert await swarm.vote("enough", 5) == sessions.FAILED         # 2 yes, 2 no: a tie fails
    assert swarm.end_round() is None
    swarm.reopen("enough")
    assert swarm.state["enough"] == sessions.OPEN
    swarm.end_round()
    result = swarm.end_round()
    assert result["result"] == "not passed" and result["rounds"] == 3
    assert [f["question"] for f in result["findings"]] == ["enough"]


@pytest.mark.asyncio
async def test_a_participant_calls_the_vote_when_its_discussion_settles():
    people = {"author": _Participant("author"),
              "cares_judge": _Participant("cares_judge", ["yes"], calls=[["cares", "nope"]]),
              "order_judge": _Participant("order_judge", ["yes"])}
    swarm = sessions.BrainSwarmSession(people, [sessions.Question("cares", "Does Tim care?"),
                                           sessions.Question("order", "First?")])
    await swarm.turn("cares_judge", 5)
    assert swarm.state == {"cares": sessions.PROVEN, "order": sessions.OPEN}
    assert {v.called_by for v in swarm.votes} == {"cares_judge"}
    assert [m["text"] for m in swarm.conversation][0] == "cares_judge speaks", \
        "nothing is written in a participant's name but its own words"


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


@pytest.mark.asyncio
async def test_a_round_goes_author_mark_editor_then_judges_and_reader_then_votes(tmp_path):
    from jarvis import package_script
    log = []
    script = package_script.ScriptFile(tmp_path / "script.yaml", ["q1"])

    class Author(_Participant):
        async def ask(self, text, timeout, answer_format=None, strict=True):
            if answer_format is not None and "script" in answer_format["properties"]:
                self.sent.append(text)
                log.append("author")
                assert answer_format["properties"]["script"] == package_script.schema(["q1"])
                answer = {"message": "Here it is.", "call_vote": [], "script": _script()}
                return sessions.Reply(json.dumps(answer), answer)
            return await super().ask(text, timeout, answer_format, strict)

    class Editor(_Participant):
        async def ask(self, text, timeout, answer_format=None, strict=True):
            if answer_format is sessions.VOTE_FORM:
                return await super().ask(text, timeout, answer_format, strict)
            self.sent.append(text)
            log.append("editor")
            assert "call_vote" in answer_format["properties"]
            edit = answer_format["properties"]["edits"]["items"]["properties"]
            assert {"slide", "field", "start", "end", "old", "new"} <= set(edit)
            answer = {"edits": [{"slide": "opening", "field": "title", "start": 0, "end": 2,
                                 "old": "It", "new": "The store", "reason": "vague-referent",
                                 "source": "notes.md"}],
                      "sent_back": [], "call_vote": []}
            return sessions.Reply(json.dumps(answer), answer)

    people = {"author": Author("author", log=log),
              "editor": Editor("editor", ["yes"], log=log),
              "cares_judge": _Participant("cares_judge", ["yes"], log=log),
              "theory_of_mind_reader": _Participant("theory_of_mind_reader", log=log)}
    material = [sessions.Known("He asked for it.", sessions.KNOWS),
                sessions.Known("CI failed twice.", sessions.PROBABLY_NOT)]
    swarm = sessions.BrainSwarmSession(people, [sessions.Question("cares", "Does Tim care?")],
                                       material=material)

    async def mark(written):
        title = written.read()["slides"][0]["title"]
        log.append(f"mark {title}")
        return [{"id": "opening/title/s1", "segment": title, "kind": "sentence",
                 "slide": "opening", "field": "title",
                 "position": {"start": 0, "end": len(title)}, "scores": {"x": 0.5}}]
    result = await swarm.round(5, script, mark)
    assert log == ["author", "mark It broke.", "editor", "mark The store broke.",
                   "cares_judge", "theory_of_mind_reader", "editor votes", "cares_judge votes"]
    assert script.read()["slides"][0]["title"] == "The store broke."
    assert "CI failed twice." in people["editor"].sent[0], "the editor is given everything"
    assert '<marked file="script.yaml"><slide id="opening">' in people["cares_judge"].sent[0], \
        "the judges work from the marked script slide by slide"
    assert 'field="title"' in people["cares_judge"].sent[0]
    assert "title: The store broke." in people["theory_of_mind_reader"].sent[0]
    assert result["result"] == "passed"


@pytest.mark.asyncio
async def test_a_script_that_does_not_check_is_said_back_and_ends_the_round(tmp_path):
    from jarvis import package_script
    script = package_script.ScriptFile(tmp_path / "script.yaml", ["q1"])

    class Author(_Participant):
        async def ask(self, text, timeout, answer_format=None, strict=True):
            self.sent.append(text)
            bad = _script()
            del bad["slides"][0]["narration"]
            answer = {"message": "Here.", "call_vote": [], "script": bad}
            return sessions.Reply(json.dumps(answer), answer)
    people = {"author": Author("author"), "editor": _Participant("editor")}
    swarm = sessions.BrainSwarmSession(people, [sessions.Question("cares", "Does Tim care?")])

    async def mark(_):
        raise AssertionError("nothing is marked without a script that checks")
    assert await swarm.play_round(5, script, mark) is False
    assert not script.path.exists(), "a script that does not check is never written"
    said = swarm.conversation[-1]
    assert said["from"] == sessions.PROGRAM and "has no narration" in said["text"]
    assert swarm.end_round() is None and swarm.rounds == 1, "the round is still one of three"


@pytest.mark.asyncio
async def test_a_closed_question_reopens_only_when_its_voters_judge_the_change_touched_it():
    people = {"author": _Participant("author", ["yes"], changed=["yes"]),
              "editor": _Participant("editor", ["yes", "no", "yes"], changed=["no", "yes"]),
              "cares_judge": _Participant("cares_judge", ["yes", "yes", "yes"],
                                          changed=["no", "yes"]),
              "order_judge": _Participant("order_judge", ["no", "no", "no"],
                                          changed=["no", "no"])}
    swarm = sessions.BrainSwarmSession(people, [sessions.Question("cares", "Does Tim care?"),
                                                sessions.Question("enough", "Enough?",
                                                                  author_votes=True)])
    swarm.script_now("Version one.")
    assert await swarm.vote("cares", 5) == sessions.PROVEN
    assert await swarm.vote("enough", 5) == sessions.FAILED          # 2 yes, 2 no
    assert await swarm.reopen_changed(5) == [], "the script has not changed: nothing to judge"
    assert not any(p.changed == [] for p in people.values() if p.name != "author")
    swarm.script_now("Version two.")
    # cares: editor no, cares judge no, order judge no: the change did not
    # touch it. enough: author yes, editor yes, cares judge yes, order no.
    assert await swarm.reopen_changed(5) == ["enough"]
    assert swarm.state == {"cares": sessions.PROVEN, "enough": sessions.OPEN}
    assert [(r["question"], r["participant"], r["changed"]) for r in swarm.reopenings] == [
        ("cares", "editor", "no"), ("cares", "cares_judge", "no"), ("cares", "order_judge", "no"),
        ("enough", "author", "yes"), ("enough", "editor", "yes"),
        ("enough", "cares_judge", "yes"), ("enough", "order_judge", "no")]
    assert "Did the change touch enough: yes. author says yes" in \
        [m["text"] for m in swarm.conversation]
    assert swarm.result()["reopenings"] == swarm.reopenings
    again = sessions.BrainSwarmSession(people, list(swarm.questions.values()))
    again.restore(swarm.saved())
    assert again.reopenings == swarm.reopenings


def test_each_participant_is_given_exactly_its_share_of_what_tim_knows():
    material = [sessions.Known("He said ship it.", sessions.KNOWS, "chat"),
                sessions.Known("The Work tab shows it.", sessions.HAD_ACCESS),
                sessions.Known("CI failed twice.", sessions.PROBABLY_NOT, "ci.log")]
    for role in ("theory_of_mind_reader", "cares_judge", "order_judge"):
        values = sessions.share_values(role, material)
        assert set(values) == {"{knows}", "{had_access}"}
        assert "He said ship it." in values["{knows}"] and \
            "The Work tab" in values["{had_access}"]
        assert "CI failed" not in "".join(values.values()), "never what he does not know"
    for role in ("author", "editor", "decision_ready_judge", "background_judge"):
        shown = sessions.share_values(role, material)[
            "{the evidence, each item with its knowledge state}"]
        assert 'state="probably_does_not_know"' in shown and 'state="knows"' in shown
    swarm = sessions.BrainSwarmSession({"cares_judge": _Participant("c"), "editor": _Participant("e")},
                                  [], material=material)
    assert [k.state for k in swarm.share_of("cares_judge")] == [sessions.KNOWS,
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
    judge = role_prompts.PROMPTS["order_judge"].system
    assert judge.startswith("You are one of four judges") and \
        "Your question: **Is the most relevant information first?**" in judge


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
async def test_a_turn_answered_in_words_alone_is_its_message_and_calls_no_vote():
    class Words:
        async def ask(self, text, timeout, answer_format=None, strict=True):
            assert answer_format is sessions.TURN_FORM and strict is False
            return sessions.Reply("Cut s22: it repeats s14.", None)
    swarm = sessions.BrainSwarmSession({"cares_judge": Words()},
                                       [sessions.Question("cares", "Does Tim care?")])
    assert await swarm.turn("cares_judge", 5) == "Cut s22: it repeats s14."
    assert swarm.conversation == [{"from": "cares_judge", "text": "Cut s22: it repeats s14."}]
    assert swarm.state["cares"] == sessions.OPEN


@pytest.mark.asyncio
async def test_a_turn_that_fails_leaves_its_first_message_and_the_others_words_to_give():
    class Fails:
        async def ask(self, text, timeout, answer_format=None, strict=True):
            raise sessions.SessionFailed("Connection lost")
    swarm = sessions.BrainSwarmSession({"order_judge": Fails()},
                                       [sessions.Question("order", "First?")],
                                       first={"order_judge": "What Tim knows."})
    swarm.say("author", "The script.")
    with pytest.raises(sessions.SessionFailed):
        await swarm.turn("order_judge", 5)
    assert not swarm.spoke("order_judge")
    assert swarm.saved()["seen"]["order_judge"] == 0 and swarm.saved()["introduced"] == []
    again = swarm._new_for("order_judge")
    assert again.startswith("What Tim knows.") and again.endswith("author:\nThe script.")
