"""The research and swarm steps (package_swarm.py, research_log.py,
sessions.BrainSwarmSession; workflow.yaml research, swarm and tim_knowledge;
the cooperative swarm, construction-drafts.md "Cooperative swarm"): a real
package's gathered and marked evidence, researched by the author with every
search logged, then the author, Tim's reader, the context owner, the
structure and decision owner and the editor in one conversation word for
word; each given exactly its share of what Tim knows; the outline, the
fill, one revision against the marks, fix passes whose leftovers are merged
by meaning, the close and the re-check; questions to the sender grouped by
meaning, the stop when answers change nothing, the hits the passed script
used, and what is left after two passes to the sender."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis import (model_tiers, package_evidence as pe, package_jobs as pj, package_mark,
                    package_script, package_swarm as ps, research_log, role_mcp, role_prompts,
                    roles, sessions, text_parts)
from jarvis.knowledge.embedder import FakeEmbedder
from tests.test_package_gather import checkout, gh, indexing, transcript  # noqa: F401
from tests.test_package_mark import Roles, _gathered

# ── the search log ────────────────────────────────────────────────────────


def test_every_search_is_logged_with_the_paths_its_answer_names(tmp_path):
    log = tmp_path / research_log.NAME
    answer = ('Matches, as JSON:\n<session-output name="knowledge" untrusted="true">\n'
              '{"hits": [{"key": "/work/w1/packages/p1/evidence/files/a.md", "score": 1},'
              ' {"key": "/folders/f1/plan \\"x\\".md"}]}\n</session-output>')
    research_log.record(log, "author", "knowledge_search", {"query": "move"}, True, answer)
    research_log.record(log, "author", "search_history", {"query": "move"}, True,
                        "2 matches:\nsess-1:14: 2026-11-19: we moved it\nnotes/x.md:3: y")
    research_log.record(log, "context_owner", "read_history",
                        {"source": "sess-1", "around": 14}, True, "the window")
    logged = research_log.read(log)
    assert [e["tool"] for e in logged] == ["knowledge_search", "search_history",
                                           "read_history"]
    assert logged[0]["hits"] == ["/work/w1/packages/p1/evidence/files/a.md",
                                 '/folders/f1/plan "x".md']
    assert logged[1]["hits"] == ["sess-1", "notes/x.md"], "the source; the line is where in it"
    assert logged[2]["hits"] == ["sess-1"] and logged[2]["role"] == "context_owner"
    assert logged[0]["arguments"] == {"query": "move"} and logged[0]["answer"] == answer

    used = research_log.mark_used(
        log, "PR 171 moved them (files/a.md). Said on 19 Nov (sess-1:14).",
        roots=("/work/w1/packages/p1/evidence",))
    assert used["searches"] == 3
    assert [(u["search"], u["used"]) for u in used["used"]] == [
        (1, ["/work/w1/packages/p1/evidence/files/a.md"]), (2, ["sess-1"]),
        (3, ["sess-1"])]
    assert json.loads((tmp_path / research_log.USED).read_text()) == used
    # A fact citing the history source without a line is the same hit used.
    no_line = research_log.mark_used(log, "Said on 19 Nov (notes/x.md).")
    assert [(u["search"], u["used"]) for u in no_line["used"]] == [(2, ["notes/x.md"])]


def test_a_role_s_tool_server_logs_every_call_it_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(role_mcp, "ROLE", "author")
    monkeypatch.setattr(role_mcp, "LOG", tmp_path / research_log.NAME)
    monkeypatch.setattr(role_mcp.jarvis_mcp, "_forward",
                        lambda tool, args, role="": (True, '{"key": "/x/y.md"}', None))
    assert role_mcp.call("knowledge_search", {"query": "q", "under": "/x"})[0]
    assert not role_mcp.call("spawn_run", {})[0], "never a tool the role lacks"
    logged = research_log.read(tmp_path / research_log.NAME)
    assert [(e["tool"], e["ok"], e["hits"]) for e in logged] == [
        ("knowledge_search", True, ["/x/y.md"]), ("spawn_run", False, [])]
    entry = role_mcp.entry("author", tmp_path / research_log.NAME)
    assert entry["args"][-2:] == ["--log", str(tmp_path / research_log.NAME)]
    assert "--log" not in role_mcp.entry("author")["args"]


def test_the_research_tools_hold_the_brain_s_own_past_conversations():
    from jarvis import agent_rights
    assert {"search_history", "read_history"} <= set(agent_rights.TOOLS["research"])
    assert roles.RESEARCH_TOOLS == tuple(agent_rights.TOOLS["research"])


# ── the swarm's participants, standing in for the brains ──────────────────

def outline_of(source: str = "files/situation.md") -> dict:
    return {"version": "outline/1", "slides": [{
        "id": "opening", "title": "The move", "point": "PR 171 moves every Work item.",
        "answers": "merge-171", "background": False, "evidence": [source]}]}


class Plan:
    """What each participant does; every turn each is sent, by role."""

    def __init__(self, run_folder: Path):
        self.folder = run_folder
        self.sent: dict[str, list[str]] = {}
        self.forms: dict[str, list[dict]] = {}
        self.opened: list[tuple[str, str | None]] = []
        self.efforts: list[tuple[str, str]] = []
        self.script = script_of("PR 171 moves every Work item.", "files/situation.md")
        self.outline = outline_of()
        self.prep = "Does Tim care: yes."
        # By owner: each turn's sign-off, in order, as (state, left) for every
        # concern it signs; resolved once the list runs out.
        self.signoffs: dict[str, list[tuple[str, list]]] = {}
        self.asks: dict[str, list[list[str]]] = {}      # by participant, each turn's asks
        self.edits: dict[str, list[dict]] = {
            "editor": [{"slide": "opening", "field": "on_screen[0].text", "start": 0, "end": 6,
                        "old": "PR 171", "new": "Pull request 171",
                        "reason": "unexplained-term", "source": ""},
                       {"slide": "opening", "field": "title", "start": 0, "end": 3,
                        "old": "XYZ", "new": "", "reason": "x", "source": ""}]}

    def _asks(self, name):
        turns = self.asks.get(name) or []
        return turns.pop(0) if turns else []

    def answer(self, name, text, form):
        self.sent.setdefault(name, []).append(text)
        self.forms.setdefault(name, []).append(form)
        props = form["properties"]
        if "outline" in props:
            assert name == "author"
            return {"message": "author outlines", "outline": self.outline,
                    "ask_sender": self._asks(name)}
        if "script" in props:
            assert name == "author"
            return {"message": "author writes", "script": self.script,
                    "ask_sender": self._asks(name)}
        if "signoff" in props:
            concerns = props["signoff"]["items"]["properties"]["concern"]["enum"]
            turns = self.signoffs.get(name) or []
            state, left = turns.pop(0) if turns else ("resolved", [])
            edits = [] if "outline" in text.split("Sign off")[0][-40:] else list(
                self.edits.get(name, [])) if "<script" in text else []
            return {"message": f"{name} speaks", "edits": edits,
                    "ask_sender": self._asks(name),
                    "signoff": [{"concern": c, "state": state,
                                 "reason": f"{name}: {c} is fine" if state == "resolved"
                                 else "", "left": left} for c in concerns]}
        assert set(props) == {"message"}
        if "Write your research in" in text:
            assert name == "author"
            (self.folder / ps.RESEARCH_MD).write_text("Finding: (files/situation.md)")
            if self.prep is not None:
                (self.folder / ps.PREP_MD).write_text(self.prep)
        return {"message": f"{name} speaks"}


def _chat_factory(plan: Plan):
    class Chat:
        def __init__(self, role, prompt, folder, *, pick, reads=(), resume=None,
                     search_log=None, on_log=None, role_spec=None):
            self.name, self.role, self.prompt = role, role_spec or roles.role(role), prompt
            plan.efforts.append((role, self.role.effort))
            self.pick, self.model, self.session = pick, pick.model, resume
            self.reads, self.search_log = reads, search_log

        @property
        def one_shot(self):
            return False

        async def open(self):
            plan.opened.append((self.name, self.session))
            self.session = self.session or f"session-{self.name}"

        async def close(self):
            pass

        async def ask(self, text, timeout, answer_format=None, strict=True):
            answer = plan.answer(self.name, text, answer_format)
            return sessions.Reply(json.dumps(answer), answer)
    return Chat


def script_of(words: str, source: str, slide: str = "opening") -> dict:
    """A one-slide script/1 whose words cite `source`."""
    return {"version": "script/1", "slides": [{
        "id": slide, "title": "The move", "on_screen": [
            {"kind": "text", "text": words, "source": "", "position": "", "path": "",
             "rows": []}],
        "narration": "It moves every item.",
        "facts": [{"fact": words, "source": source, "position": "1-40"}],
        "answers": ["merge-171"], "decision": None}]}


class Marking:
    calls: list[tuple[str, list]] = []

    async def mark_slides(self, units, context, labels, knowledge, timeout):
        Marking.calls.append((context, knowledge))
        return [{"id": f"{u.slide}/{u.field}/{s.id}", "segment": s.text, "kind": s.kind,
                 "slide": u.slide, "field": u.field,
                 "position": {"start": s.start, "end": s.end}, "within": s.within,
                 "scores": {label.name: 0.0 for label in labels}}
                for u in units for s in text_parts.split(u.text)]


@pytest.fixture
def marked(tmp_path, gh, checkout, transcript, monkeypatch):  # noqa: F811
    found = Roles()
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", found.for_role)
    doc = _gathered(tmp_path, checkout, monkeypatch)
    package_mark.mark(pj.get(doc["id"]))
    return pj.get(doc["id"])


@pytest.fixture
def plan(marked, monkeypatch):
    plan = Plan(Path(marked["folder"]))
    monkeypatch.setattr(ps, "CHAT", _chat_factory(plan))
    monkeypatch.setattr(ps, "PICK", lambda kind, tier, **kw: model_tiers.Pick(
        "claude", {1: "fable", 2: "opus"}.get(tier, "sonnet"), tier, "test", ["x"]))
    monkeypatch.setattr(package_mark, "MARKING", lambda doc: Marking())
    plan.searched = []

    def search(text, under, kinds):
        plan.searched.append((text, under, kinds))
        return {"hits": [{"key": f"{under or ''}/found.md", "source": "found.md",
                          "score": 0.5, "why": ["words"], "text": f"Found for {text}."}]}
    monkeypatch.setattr(ps, "SEARCH", lambda: search)
    embedder = FakeEmbedder(64)
    monkeypatch.setattr(ps, "EMBED", lambda texts: [list(v) for v in
                                                    embedder.embed(texts, "query")])
    Marking.calls = []
    return plan


def _research_and_swarm(doc):
    ps.research(pj.get(doc["id"]))
    return ps.swarm(pj.get(doc["id"]))


# ── research ──────────────────────────────────────────────────────────────

def test_research_is_the_program_s_searches_then_the_author_s_one_turn(marked, plan):
    ps.research(pj.get(marked["id"]))
    folder = Path(marked["folder"])
    place = pe.place_of(marked)
    assert (folder / ps.RESEARCH_MD).read_text().startswith("Finding:")
    assert (folder / ps.PREP_MD).read_text() == plan.prep, "research and prep together"
    # Every search run by the program, before the author's turn: each
    # question by words and meaning under the evidence, across the whole
    # record and in the conversation's turns.
    question = "Merge PR 171?"
    assert (question, place.path, None) in plan.searched
    assert (question, None, None) in plan.searched
    assert (question, None, ["turn"]) in plan.searched
    first = plan.sent["author"][0]
    # Its approved material: the sender's two answers, the questions, and
    # the evidence: small enough here, every file whole with its marks in
    # place, the contents of the evidence, each file with Tim's knowledge
    # state, and the searches' short hits.
    assert "PR 171 moves the Work items." in first and question in first
    assert "<contents>" in first and '<file path="files/situation.md"' in first
    assert '<document path="files/situation.md"' in first and "<left_out>" not in first
    assert "[s2 q1 .5 evidence .8] The Work items moved into folders on the copy." in first
    assert 'state="probably_does_not_know"' in first and 'state="knows"' in first
    assert "files whole" in first and "search_at_once" in first
    # What the searches found, short in the working set, and whole beside the run.
    assert f"Found for {question}." in first and "<hit " in first
    assert f"Found for {question}." in (folder / ps.SEARCHES_MD).read_text()
    # Where it starts from, and the one evidence path every search names.
    assert "knowledge_search under that path" in first and place.path in first
    assert str(place.folder / "mark") in first
    assert f"Write your research in {folder / ps.RESEARCH_MD}" in first
    assert f"Write the prep in {folder / ps.PREP_MD}" in first
    # The program's searches are in the run's search log as its own.
    logged = research_log.read(folder / research_log.NAME)
    assert logged and {e["role"] for e in logged} == {ps.PROGRAM}
    assert all(isinstance(e.get("seconds"), float) for e in logged), "each search timed"
    assert f"{place.path}/found.md" in {h for e in logged for h in e["hits"]}
    kept = json.loads((folder / ps.STATE).read_text())
    assert kept["researched"] and kept["participants"]["author"]["session"] == "session-author"
    assert kept["swarm"]["conversation"] == [{"from": "author", "text": "author speaks"}]
    assert [t["stage"] for t in kept["times"]] == ["research-searches", "research"]
    assert plan.opened == [("author", None)], "only the author researches"
    assert plan.forms["author"] == [ps.TALK], "one turn: it calls back with its tools for more"
    assert kept["given"]["author"]["working_set"] is False, "small enough: the evidence whole"
    # Medium effort for the research turn alone.
    assert ("author", ps.RESEARCH_EFFORT) in plan.efforts
    assert dict(plan.efforts)["tims_reader"] == roles.role("tims_reader").effort


def test_research_without_its_prep_is_an_error(marked, plan):
    plan.prep = None
    with pytest.raises(pj.Blocked, match="no prep.md"):
        ps.research(pj.get(marked["id"]))


def test_a_new_run_starts_from_the_earlier_run_s_research(marked, plan):
    ps.research(pj.get(marked["id"]))
    (Path(marked["folder"]) / research_log.NAME).write_text('{"tool": "knowledge_search"}\n')
    later = pj.new_run(marked["id"], "the sender answered the swarm's findings")
    plan.folder = Path(later["folder"])
    ps.research(pj.get(later["id"]))
    earlier = Path(later["folder"]) / ps.EARLIER
    assert (earlier / ps.RESEARCH_MD).read_text().startswith("Finding:")
    assert (earlier / research_log.NAME).is_file()
    assert f"An earlier run's research is in {earlier}" in plan.sent["author"][-1]


# ── the swarm ─────────────────────────────────────────────────────────────

ORDER = ["author",                                   # research
         "author", "tims_reader", "context_owner", "structure_owner",   # the outline
         "author", "tims_reader", "context_owner", "structure_owner",   # settled, signed
         "author", "author",                         # the fill, its revision
         "tims_reader", "context_owner", "structure_owner", "editor"]   # the fix pass


def _turns(folder: Path) -> list[str]:
    result = json.loads((folder / ps.RESULT).read_text())
    return [m["from"] for m in result["conversation"] if m["from"] not in (ps.PROGRAM,
                                                                          "sender")]


def test_the_outline_then_the_script_then_a_pass_signed_off_by_every_owner(marked, plan):
    place = pe.place_of(marked)
    folder = Path(marked["folder"])
    ps.research(pj.get(marked["id"]))
    research_log.record(folder / research_log.NAME, "author", "knowledge_search", {"q": "x"},
                        True, json.dumps({"hits": [
                            {"key": place.item("files/situation.md")},
                            {"key": place.item("jarvis/tim.md")}]}))
    assert ps.swarm(pj.get(marked["id"])) is None
    assert _turns(folder) == ORDER
    assert package_script.read(folder / ps.OUTLINE) == plan.outline
    # The author's outline turn and its form: the outline, the prep, and what
    # only the sender can answer; no vote in any turn.
    assert f"From your research and the prep, give the outline" in plan.sent["author"][1]
    assert set(plan.forms["author"][1]["properties"]) == {"message", "outline", "ask_sender"}
    assert "Settle any conflict between them" in plan.sent["author"][2]
    assert "Write the script from the agreed outline" in plan.sent["author"][3]
    assert '<outline file="outline.yaml">version: outline/1' in plan.sent["author"][3]
    # One revision against the marks, its approved words, the script marked.
    assert role_prompts.AUTHOR_REVISE in plan.sent["author"][4]
    assert '<marked file="script.yaml"><slide id="opening">' in plan.sent["author"][4]
    # The owners sign off on the outline only their outline concerns, and in
    # the pass every concern, the editor the language.
    enums = {name: [f["properties"]["signoff"]["items"]["properties"]["concern"]["enum"]
                    for f in forms if "signoff" in f["properties"]]
             for name, forms in plan.forms.items()}
    assert enums["tims_reader"] == [["cares"]] * 3
    assert enums["structure_owner"] == [["order", "decision_ready"]] * 3
    assert enums["editor"] == [["language"]]
    # The editor's edit applied where its old text is exactly there; the one
    # that is not refused, and said.
    script = package_script.read(folder / ps.SCRIPT)
    assert script["slides"][0]["on_screen"][0]["text"] == "Pull request 171 moves every Work item."
    result = json.loads((folder / ps.RESULT).read_text())
    said = [m["text"] for m in result["conversation"] if m["from"] == ps.PROGRAM]
    assert any("editor's edits to script.yaml: 1 applied, 1 refused" in t for t in said)
    assert result["result"] == "passed" and result["passes"] == 1
    assert {c["id"]: c["state"] for c in result["concerns"]} == {
        "cares": "resolved", "background": "resolved", "order": "resolved",
        "decision_ready": "resolved", "language": "resolved"}
    assert {s["stage"] for s in result["signoffs"]} == {"outline", "outline-signoff", "pass-1"}
    assert [t["stage"] for t in result["times"]][:3] == ["research-searches", "research",
                                                          "outline"]
    assert len(Marking.calls) == 2, "marked after the fill and after the revision"
    assert (folder / ps.CONVERSATION).read_text().count("## ") == len(result["conversation"])
    used = json.loads((folder / research_log.USED).read_text())
    assert used["used"][0]["used"] == [place.item("files/situation.md")]
    assert pj.get(marked["id"])["swarm"]["result"] == "passed"


def test_each_participant_is_given_exactly_its_share(marked, plan):
    _research_and_swarm(marked)
    first = {name: sent[0] for name, sent in plan.sent.items()}
    secret = "FAILED tests/test_x.py::test_move"           # a CI log: he never saw it
    assert secret not in first["tims_reader"] and "probably_does_not_know" not in \
        first["tims_reader"]
    assert "What Tim knows:" in first["tims_reader"]
    for name in ("context_owner", "structure_owner"):
        assert secret in first[name] and "Does Tim care: yes." in first[name], "with the prep"
        assert "knowledge_search under that path" in first[name]
    assert secret in first["editor"] and 'state="probably_does_not_know"' in first["editor"]
    assert '<marked file="script.yaml">' in first["editor"], "the editor works from the marks"
    assert "Does Tim care: yes." in first["editor"], "the editor is given the prep it names"
    assert "Does Tim care: yes." not in first["tims_reader"]
    # The script is marked with what Tim knows, never the words of what he does not.
    knowledge = Marking.calls[0][1]
    assert all(k.text == "" for k in knowledge if k.state == pe.PROBABLY_NOT)


def test_a_share_too_large_whole_is_given_as_its_working_set(marked, plan, monkeypatch):
    """construction-drafts.md "The author works from a prioritized working
    set and calls back for more": an owner's share stays its share, but past
    its window it comes as the working set of that share, read further with
    its tools; Tim's reader's two kept apart."""
    real = ps._Firsts._room
    monkeypatch.setattr(ps._Firsts, "_room", lambda self, name: (
        real(self, name) if name == "author" else 300 if name == "tims_reader" else 1_500))
    _research_and_swarm(marked)
    first = {name: sent[0] for name, sent in plan.sent.items()}
    secret = "FAILED tests/test_x.py::test_move"
    for name in ("context_owner", "structure_owner", "editor"):
        assert "<left_out>" in first[name] and "This is a working set" in first[name], name
        assert secret not in first[name], "no file whole"
        assert "read_package_file" in first[name], name
        assert "Does Tim care: yes." in first[name], "the prep still given"
    assert "<left_out>" in first["tims_reader"] and "you have no tools" in first["tims_reader"]
    assert secret not in first["tims_reader"] and "probably_does_not_know" not in \
        first["tims_reader"], "still only what he knows and had access to"


LEFT = [{"what": "The decision slide does not say what each choice does.",
         "fix": "Add one line per option.", "evidence_missing": ""}]
SAME = [{"what": "The decision slide does not say what each choice would do.",
         "fix": "Say what each option changes.", "evidence_missing": "the CI history"}]


def test_what_is_left_is_merged_worked_by_the_author_and_rechecked_by_its_owners(
        marked, plan, monkeypatch):
    plan.signoffs = {"structure_owner": [("resolved", []), ("resolved", []),
                                         ("not_yet", LEFT)],
                     "context_owner": [("resolved", []), ("resolved", []), ("not_yet", SAME)]}
    # The engine's embeddings stand in: the two wordings mean the same.
    monkeypatch.setattr(ps, "EMBED", lambda texts: [
        [1.0, 0.0] if "decision slide" in t else [0.0, 1.0] for t in texts])
    assert _research_and_swarm(marked) is None
    folder = Path(marked["folder"])
    result = json.loads((folder / ps.RESULT).read_text())
    said = [m["text"] for m in result["conversation"] if m["from"] == ps.PROGRAM]
    [listed] = [t for t in said if t.startswith("What is left, merged")]
    # One item: the two meant the same, each owner's fix kept with who raised it.
    assert "\n1. " in listed and "\n2. " not in listed
    assert "raised by context_owner, structure_owner" in listed
    assert "Suggested fix (structure_owner): Add one line per option." in listed
    assert "Evidence missing (context_owner): the CI history" in listed
    close = plan.sent["author"][-1]
    assert "Work through the list above" in close and "research it with your tools" in close
    # Only the owners with items re-check, each its own items only.
    assert _turns(folder)[len(ORDER):] == ["author", "context_owner", "structure_owner"]
    assert "Re-check only your own items" in plan.sent["structure_owner"][-1]
    assert result["result"] == "passed" and result["passes"] == 1
    assert len(Marking.calls) == 3, "marked again after the close"


def test_after_two_passes_what_is_left_goes_to_the_sender(marked, plan):
    plan.signoffs = {"tims_reader": [("resolved", []), ("resolved", [])]
                     + [("not_yet", LEFT)] * 4}
    assert _research_and_swarm(marked) == pj.WAITING
    job = pj.get(marked["id"])
    last = job["rounds"][-1]
    assert pj.kind_of(last) == pj.FINDINGS and pj.question_rounds(job) == 0
    assert [q["id"] for q in last["questions"]] == ["left-1"]
    assert last["questions"][0]["question"].startswith(
        "Not signed off: The decision slide does not say what each choice does. (raised by "
        "tims_reader) Suggested: tims_reader: Add one line per option.")
    result = json.loads((Path(marked["folder"]) / ps.RESULT).read_text())
    assert result["passes"] == 2 and result["result"] == "not passed"
    assert [s["stage"] for s in result["signoffs"] if s["owner"] == "tims_reader"] == [
        "outline", "outline-signoff", "pass-1", "recheck-1", "pass-2", "recheck-2"]
    assert [d["id"] for d in pj.to_tell_sender()] == [marked["id"]]
    done = pj.answer(marked["id"], {"left-1": "Lead with the decision."},
                     session=job["session"])
    assert done["state"] == pj.FAILED and pj.FAILED in pj.ENDED


def test_the_sender_is_asked_once_the_outline_is_signed_off_grouped_by_meaning(marked, plan):
    plan.asks = {"context_owner": [["When did CI last pass?"]],
                 "structure_owner": [["Who reviews it?"], ["When did the CI last pass?"]]}
    assert _research_and_swarm(marked) == pj.WAITING
    job = pj.get(marked["id"])
    assert job["state"] == pj.WAITING and pj.question_rounds(job) == 1
    asked = job["rounds"][-1]["questions"]
    assert [(q["id"], q["askers"]) for q in asked] == [("ask-1", 2), ("ask-2", 1)]
    assert asked[0]["question"] in ("When did CI last pass?", "When did the CI last pass?")
    assert asked[1]["question"] == "Who reviews it?"
    folder = Path(marked["folder"])
    kept = json.loads((folder / ps.STATE).read_text())
    assert kept["flow"]["stage"] == "fill", "asked after the outline was signed off"

    pj.answer(marked["id"], {"ask-1": "Tuesday, run 99880."}, session=job["session"])
    assert (pe.place_of(marked).folder / "sender-answers/1.md").is_file()
    plan.opened.clear()
    assert ps.swarm(pj.get(marked["id"])) is None
    assert ("author", "session-author") in plan.opened, "each session resumed"
    conversation = json.loads((folder / ps.RESULT).read_text())["conversation"]
    said = [m for m in conversation if m["from"] == "sender"]
    assert said == [{"from": "sender", "text": f'<answer id="ask-1" question="{asked[0]["question"]}">'
                                               'Tuesday, run 99880.</answer>'}]
    assert "Tuesday, run 99880." in plan.sent["author"][3], "the author hears it as it fills"


def test_questions_are_grouped_by_meaning_one_wording_each_most_asked_first():
    # swarm ask_sender merged (Tim 2026-11-20): grouped by the engine's
    # embeddings, one wording kept a group, ordered by how many asked.
    def unit(*xs):
        n = sum(x * x for x in xs) ** 0.5
        return [x / n for x in xs]
    asks = [{"stage": "outline", "from": "editor", "ask": "Who reviews?"},
            {"stage": "outline", "from": "author", "ask": "When did CI pass?"},
            {"stage": "pass-1", "from": "tims_reader", "ask": "When was CI green?"},
            {"stage": "pass-1", "from": "editor", "ask": "Last green CI run?"},
            {"stage": "pass-1", "from": "author", "ask": "Who reviews it?"}]
    vectors = [unit(0, 0, 1), unit(1, 0.10, 0), unit(1, 0.12, 0), unit(1, 0.30, 0.0),
               unit(0, 0.05, 1)]
    groups = ps.group_by_meaning(asks, vectors)
    assert [(g["id"], g["askers"], len(g["asked"])) for g in groups] == [
        ("ask-1", 3, 3), ("ask-2", 2, 2)]
    assert groups[0]["question"] == "When was CI green?", "the wording nearest the rest"
    assert groups[1]["question"] == "Who reviews?", "on a tie, the first asked"
    apart = ps.group_by_meaning(asks[:2], [unit(1, 0), unit(0.8, 0.6)])
    assert len(apart) == 2, f"a cosine of 0.8 is under {ps.SAME_MEANING}: two meanings"


def test_asking_stops_once_answers_change_nothing_still_open(marked, plan):
    plan.signoffs = {"tims_reader": [("not_yet", LEFT)] * 10}
    plan.asks = {"tims_reader": [["What broke?"]] * 10}
    assert _research_and_swarm(marked) == pj.WAITING
    pj.answer(marked["id"], {"ask-1": "Nothing."},
              session=pj.get(marked["id"])["session"])
    assert ps.swarm(pj.get(marked["id"])) == pj.WAITING
    job = pj.get(marked["id"])
    assert pj.question_rounds(job) == 1, "the answers changed nothing still open: no more asked"
    assert pj.kind_of(job["rounds"][-1]) == pj.FINDINGS, "two passes, and the swarm failed"


def test_only_rounds_of_questions_count_toward_five(marked):
    for n in range(pj.MAX_ROUNDS):
        pj.failed_swarm(marked["id"], [{"id": "x", "question": "?"}], [])
        assert pj.ask(marked["id"], [{"id": f"q{n}", "question": "?"}])
    assert not pj.ask(marked["id"], [{"id": "q", "question": "?"}])
    assert pj.question_rounds(pj.get(marked["id"])) == pj.MAX_ROUNDS


