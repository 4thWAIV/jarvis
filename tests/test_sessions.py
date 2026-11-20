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
                    evidence_types, jarvis_mcp, model_tiers, role_mcp, role_prompts, roles,
                    sessions, text_parts)

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
        "gpt-5.6-sol", "gpt-6.2-sol",               # up one model, oldest first
        "gpt-6-astra", "gpt-6.2-astra"]             # and on up


def test_a_family_not_listed_is_swept_past_and_none_at_all_is_no_model():
    assert model_tiers.first("codex", 4, ["gpt-5.6-terra"]) == "gpt-5.6-terra"
    with pytest.raises(model_tiers.NoModel):
        model_tiers.first("codex", 1, ["gpt-5.6-luna"])


def test_claude_is_its_alias_latest_only_and_lists_fable():
    assert claude_provider.ClaudeProvider().models() == ["fable", "opus", "sonnet", "haiku"]
    assert claude_provider.ClaudeProvider.default_model == "sonnet"
    assert model_tiers.sweep("claude", 2, ["fable", "opus", "sonnet", "haiku"]) == \
        ["opus", "fable"]


def test_codex_falls_back_to_the_sweep_never_a_fixed_name():
    assert codex_provider.fallback_model(["gpt-6.2-astra", "gpt-6.1-terra", "gpt-5.6-terra"]) \
        == "gpt-6.1-terra"
    assert codex_provider.fallback_model(["gpt-6-astra"]) == "gpt-6-astra"
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
        "classification": (3, "low"), "extraction": (4, "low")}
    research = set(roles.RESEARCH_TOOLS)
    assert {"search_history", "read_history"} <= research
    assert set(by["author"].tools) == research and by["author"].writes
    assert set(by["decision_ready_judge"].tools) == research == set(by["background_judge"].tools)
    assert by["editor"].tools == ("knowledge_search",)
    for name in ("cares_judge", "order_judge", "theory_of_mind_reader", "marking",
                 "classification", "extraction"):
        assert not by[name].tools and not by[name].builtins, name


def test_no_role_has_a_sending_command_or_a_tool_the_brain_lacks():
    from jarvis import agent_mcp
    sending = {c.name for c in agent_mcp.COMMANDS} | {s["name"] for s in agent_mcp.PACKAGE_SPECS}
    known = {s["name"] for s in jarvis_mcp.TOOL_SPECS}
    for role in roles.ROLES.values():
        assert not set(role.tools) & sending, role.name
        assert set(role.tools) <= known, (role.name, set(role.tools) - known)


def test_a_role_s_tool_server_offers_only_its_tools(monkeypatch):
    names = {s["name"] for s in role_mcp.tool_specs("editor")}
    assert names == {"knowledge_search"}
    monkeypatch.setattr(role_mcp, "ROLE", "editor")
    forwarded = []
    monkeypatch.setattr(jarvis_mcp, "_forward",
                        lambda tool, args, role="": forwarded.append((tool, role)) or
                        (True, "ok", None))
    listed = role_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert [t["name"] for t in listed["result"]["tools"]] == ["knowledge_search"]
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
    assert cmd[cmd.index("--tools") + 1] == "Read,Write,Edit,Grep,Glob"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--session-id") + 1] == "u-1"
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path / "evidence")
    allowed = cmd[cmd.index("--allowedTools") + 1:]
    assert roles.tool_name("search_history") in allowed
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
    assert "Bash(gh pr view *)" in allowed and roles.tool_name("work_detail") in allowed
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
    (data_paths.codex_home() / "config.toml").write_text("[mcp_servers.jarvis]\ncommand = \"x\"\n")
    monkeypatch.setenv("FAKE_CODEX_ANSWER", json.dumps({"answer": "yes"}))
    pick = model_tiers.Pick("codex", "gpt-6-luna", 4, "fixed", ["gpt-6-luna"])
    form = {"type": "object", "properties": {"answer": {"type": "string"}}}
    chat = sessions.ChatSession("extraction", "THE ROLE", tmp_path, pick=pick, answer_format=form)
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
                                "web_search": "disabled", "include_apply_patch_tool": False,
                                "mcp_servers": {"jarvis": {"enabled": False}}}
    turn = next(e["turn"] for e in log if "turn" in e)
    assert turn["effort"] == "low" and turn["outputSchema"] == form


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
    monkeypatch.setattr(sessions.ChatSession, "for_role", once)
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
    monkeypatch.setattr(sessions.ChatSession, "for_role", once)
    marked = await sessions.MarkingSession(tmp_path).mark(
        text, labels, [sessions.Known("He said fix it.", "knows")], 5)
    assert [m["id"] for m in marked] == ids == ["s1", "s1.p1", "s1.p2"]
    assert marked[1] == {"id": "s1.p1", "segment": "It broke,", "kind": "phrase",
                         "position": {"start": 0, "end": 9}, "within": "s1",
                         "scores": {"background": 0.5, "filler": 0.0}}
    sent = once.seen[0]["text"]
    assert '<material state="knows">He said fix it.</material>' in sent
    once.answer = {"scores": [{"id": "s1", "scores": {"background": 1, "filler": 0}}]}
    with pytest.raises(sessions.SessionFailed):
        await sessions.MarkingSession(tmp_path).mark(text, labels, [], 5)


@pytest.mark.asyncio
async def test_classification_adds_background_and_takes_no_states(monkeypatch, tmp_path):
    once = _Once({"scores": [{"item": "f1", "scores": {"q1": 0.9, "background": 0.1}}]})
    monkeypatch.setattr(sessions.ChatSession, "for_role", once)
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
    monkeypatch.setattr(sessions.ChatSession, "for_role", once)
    out = await sessions.RetrievalSession(tmp_path).resolve(
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
    monkeypatch.setattr(sessions.ChatSession, "for_role", once)
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
        async def ask(self, text, timeout, answer_format=None):
            self.sent, self.form = text, answer_format
            answer = {"edits": [{"start": 0, "end": 2, "old": "It", "new": "The store",
                                 "reason": "vague-referent", "source": "notes.md:1"}],
                      "sent_back": [{"start": 3, "end": 8, "old": "broke", "needs": "when"}]}
            return sessions.Reply(json.dumps(answer), answer)
    chat = Chat()
    out = await sessions.EditingSession(chat).edit(
        "It broke.", [{"id": "s1", "segment": "It broke.", "kind": "sentence",
                       "position": {"start": 0, "end": 9}, "scores": {"vague-referent": 0.9}}],
        [sessions.Known("The store moved.", "probably_does_not_know")], 5)
    assert out["text"] == "The store broke." and out["sent_back"][0]["needs"] == "when"
    assert chat.form is sessions.EDIT_FORM and 'state="probably_does_not_know"' in chat.sent


# ── the swarm ─────────────────────────────────────────────────────────────

class _Participant:
    def __init__(self, name, votes=None):
        self.name, self.votes, self.sent = name, list(votes or []), []

    async def ask(self, text, timeout, answer_format=None):
        self.sent.append(text)
        if answer_format is sessions.VOTE_FORM:
            vote = self.votes.pop(0)
            answer = {"question": "q", "vote": vote, "reason": f"{self.name} says {vote}"}
            return sessions.Reply(json.dumps(answer), answer)
        return sessions.Reply(f"{self.name} speaks", None)


@pytest.mark.asyncio
async def test_the_swarm_relays_word_for_word_and_counts_votes():
    people = {"author": _Participant("author", ["yes"]),
              "editor": _Participant("editor", ["yes", "no"]),
              "cares_judge": _Participant("cares_judge", ["no", "no"]),
              "order_judge": _Participant("order_judge", ["yes", "yes"]),
              "theory_of_mind_reader": _Participant("theory_of_mind_reader")}
    swarm = sessions.SwarmSession(people, [sessions.Question("cares", "Does Tim care?"),
                                           sessions.Question("enough", "Enough?",
                                                             author_votes=True)])
    swarm.say("author", "The script, as written.")
    await swarm.turn("editor", 5)
    assert people["editor"].sent[-1] == "author:\nThe script, as written."
    await swarm.turn("theory_of_mind_reader", 5)
    assert people["theory_of_mind_reader"].sent[-1] == \
        "author:\nThe script, as written.\n\neditor:\neditor speaks"
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
