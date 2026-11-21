"""The research and swarm steps (package_swarm.py, research_log.py,
sessions.BrainSwarmSession; workflow.yaml research, swarm and tim_knowledge):
a real package's gathered and marked evidence, researched by the author
with every search logged, then the author, the editor, the four judges and
the Theory of Mind reader in one conversation word for word; each given
exactly its share of what Tim knows; rounds, votes, questions to the sender
as rounds of their own, the stop when answers change nothing, the hits the
passed script used, and a failed swarm's findings to the sender."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis import (model_tiers, package_evidence as pe, package_jobs as pj, package_mark,
                    package_script, package_swarm as ps, research_log, role_mcp, roles, sessions,
                    text_parts)
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
    research_log.record(log, "background_judge", "read_history",
                        {"source": "sess-1", "around": 14}, True, "the window")
    logged = research_log.read(log)
    assert [e["tool"] for e in logged] == ["knowledge_search", "search_history",
                                           "read_history"]
    assert logged[0]["hits"] == ["/work/w1/packages/p1/evidence/files/a.md",
                                 '/folders/f1/plan "x".md']
    assert logged[1]["hits"] == ["sess-1", "notes/x.md"], "the source; the line is where in it"
    assert logged[2]["hits"] == ["sess-1"] and logged[2]["role"] == "background_judge"
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

class Plan:
    """What each participant does; every turn each is sent, by role."""

    def __init__(self, run_folder: Path):
        self.folder = run_folder
        self.sent: dict[str, list[str]] = {}
        self.opened: list[tuple[str, str | None]] = []
        self.votes: dict[str, list[str]] = {}      # question id prefix -> votes in order
        self.asks: list[str] = []                   # an enough no's ask, per vote
        self.changed: dict[str, list[str]] = {}     # question id prefix -> reopen judgements
        self.script = script_of("PR 171 moves every Work item.", "files/situation.md")
        self.preps: list[str] = ["Does Tim care: yes."]   # the prep each round, in order

    def vote(self, name, qid):
        for prefix, votes in self.votes.items():
            if qid.startswith(prefix) and votes:
                return votes.pop(0)
        return "yes"

    def answer(self, name, text, form):
        self.sent.setdefault(name, []).append(text)
        if form is not None and "script" in form["properties"]:
            assert name == "author" and "message" in form["properties"]
            prep = self.preps.pop(0) if len(self.preps) > 1 else self.preps[0]
            (self.folder / ps.PREP_MD).write_text(prep)
            return {"message": "author speaks", "call_vote": [], "script": self.script}
        if form is sessions.TURN_FORM:
            if name == "author" and "Write your research in" in text:
                (self.folder / ps.RESEARCH_MD).write_text("Finding: (files/situation.md)")
            return {"message": f"{name} speaks", "call_vote": []}
        if form is sessions.REOPEN_FORM:
            qid = re.search(r'Did the change touch what it was about\?\n<question id="([^"]+)"',
                            text).group(1)
            said = next((judged.pop(0) for prefix, judged in self.changed.items()
                         if qid.startswith(prefix) and judged), "yes")
            return {"question": qid, "changed": said, "reason": f"{name} judges {said}"}
        if form in (sessions.VOTE_FORM, sessions.ENOUGH_FORM):
            qid = re.search(r'A vote is called on this question:\n<question id="([^"]+)"',
                            text).group(1)
            said = self.vote(name, qid)
            out = {"question": qid, "vote": said, "reason": f"{name} says {said}"}
            if form is sessions.ENOUGH_FORM:
                out["ask_sender"] = self.asks.pop(0) if said == "no" and self.asks else ""
            return out
        assert name == "editor" and "call_vote" in form["properties"]
        return {"edits": [{"slide": "opening", "field": "on_screen[0].text", "start": 0,
                           "end": 6, "old": "PR 171", "new": "Pull request 171",
                           "reason": "unexplained-term", "source": ""},
                          {"slide": "opening", "field": "title", "start": 0, "end": 3,
                           "old": "XYZ", "new": "", "reason": "x", "source": ""}],
                "sent_back": [], "call_vote": []}


def _chat_factory(plan: Plan):
    class Chat:
        def __init__(self, role, prompt, folder, *, pick, reads=(), resume=None,
                     search_log=None, on_log=None):
            self.name, self.role, self.prompt = role, roles.role(role), prompt
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
    embedder = FakeEmbedder(64)
    monkeypatch.setattr(ps, "EMBED", lambda texts: [list(v) for v in
                                                    embedder.embed(texts, "query")])
    Marking.calls = []
    return plan


def _research_and_swarm(doc):
    ps.research(pj.get(doc["id"]))
    return ps.swarm(pj.get(doc["id"]))


# ── research ──────────────────────────────────────────────────────────────

def test_research_is_the_author_s_turn_from_what_is_there(marked, plan):
    ps.research(pj.get(marked["id"]))
    folder = Path(marked["folder"])
    assert (folder / ps.RESEARCH_MD).read_text().startswith("Finding:")
    first = plan.sent["author"][0]
    # Its approved material: the sender's two answers, the questions, and
    # everything gathered and marked, each item with Tim's knowledge state.
    assert "PR 171 moves the Work items." in first and "Merge PR 171?" in first
    assert 'state="probably_does_not_know"' in first and 'state="knows"' in first
    assert "mark/files/situation.md.json" in first, "the marks with the files"
    # Where it starts from, and the one evidence path every search names.
    place = pe.place_of(marked)
    assert f"knowledge_search under that path" in first and place.path in first
    assert str(place.folder / "mark") in first
    assert f"Write your research in {folder / ps.RESEARCH_MD}" in first
    kept = json.loads((folder / ps.STATE).read_text())
    assert kept["researched"] and kept["participants"]["author"]["session"] == "session-author"
    assert kept["swarm"]["conversation"] == [{"from": "author", "text": "author speaks"}]
    assert plan.opened == [("author", None)], "only the author researches"


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

def test_a_round_passes_and_the_script_s_hits_are_recorded_used(marked, plan):
    place = pe.place_of(marked)
    folder = Path(marked["folder"])
    ps.research(pj.get(marked["id"]))
    research_log.record(folder / research_log.NAME, "author", "knowledge_search", {"q": "x"},
                        True, json.dumps({"hits": [
                            {"key": place.item("files/situation.md")},
                            {"key": place.item("jarvis/tim.md")}]}))
    assert ps.swarm(pj.get(marked["id"])) is None
    names = [m["from"] for m in json.loads((folder / ps.RESULT).read_text())["conversation"]]
    # The research turn, then a round: author, editor, the judges, the reader, votes.
    assert names[:8] == ["author", "author", "editor", "cares_judge", "decision_ready_judge",
                         "background_judge", "order_judge", "theory_of_mind_reader"]
    # The editor's edit applied where its old text is exactly there; the one
    # that is not refused; the marked script is the edited one.
    script = package_script.read(folder / ps.SCRIPT)
    assert script["slides"][0]["on_screen"][0]["text"] == "Pull request 171 moves every Work item."
    assert "Pull request 171" in Marking.calls[-1][0], "the edited script is marked"
    result = json.loads((folder / ps.RESULT).read_text())
    assert result["result"] == "passed" and result["rounds"] == 1
    voters = {v["participant"] for v in result["votes"] if v["question"] == "cares"}
    assert voters == {"editor", "cares_judge", "decision_ready_judge", "background_judge",
                      "order_judge"}, "the author never votes on its script; the reader never"
    assert "author" in {v["participant"] for v in result["votes"]
                        if v["question"] == "enough-merge-171"}
    assert (folder / ps.CONVERSATION).read_text().count("## ") == len(result["conversation"])
    used = json.loads((folder / research_log.USED).read_text())
    assert used["used"][0]["used"] == [place.item("files/situation.md")]
    assert pj.get(marked["id"])["swarm"]["result"] == "passed"


def test_each_participant_is_given_exactly_its_share(marked, plan):
    _research_and_swarm(marked)
    first = {name: sent[0] for name, sent in plan.sent.items()}
    secret = "FAILED tests/test_x.py::test_move"           # a CI log: he never saw it
    for name in ("cares_judge", "order_judge", "theory_of_mind_reader"):
        assert secret not in first[name] and "probably_does_not_know" not in first[name]
    for name in ("decision_ready_judge", "background_judge"):
        assert secret in first[name] and "Does Tim care: yes." in first[name], "with the prep"
        assert "knowledge_search under that path" in first[name]
    assert "PR 171 moves every Work item." in first["theory_of_mind_reader"], \
        "the reader reads the script"
    # The editor's information is everything, each with its state, and the prep.
    assert secret in first["editor"] and "<prep>Does Tim care: yes.</prep>" in first["editor"]
    # The cares judge is told which parts are new to him: its slide cites a
    # file he probably does not know.
    assert ('<part id="opening/on_screen[0].text/s1">Pull request 171 moves every Work '
            'item.</part>') in first["cares_judge"]
    # The script is marked with what Tim knows, never the words of what he does not.
    knowledge = Marking.calls[0][1]
    assert all(k.text == "" for k in knowledge if k.state == pe.PROBABLY_NOT)


def test_new_to_him_is_worked_out_slide_by_slide_from_the_knowledge_states(marked, plan):
    run = ps._run(pj.get(marked["id"]), ps.SWARM)
    states = {r.stored: r.knowledge_state for r in run.rows if r.stored}
    assert states["jarvis/tim.md"] == pe.KNOWS
    script = {"version": "script/1", "slides": [
        script_of("He asked.", "jarvis/tim.md", "asked")["slides"][0],
        script_of("CI failed.", "retrieved/ci_run-99881.md", "failed")["slides"][0],
        script_of("Nothing cited.", "unanswered", "nothing")["slides"][0]]}
    package_script.write(run.script, script)
    parts = [{"id": "a", "kind": "sentence", "slide": "asked", "segment": "He asked."},
             {"id": "f", "kind": "sentence", "slide": "failed", "segment": "CI failed."},
             {"id": "n", "kind": "sentence", "slide": "nothing", "segment": "Nothing cited."},
             {"id": "np", "kind": "phrase", "slide": "nothing", "segment": "Nothing"}]
    assert [p["id"] for p in ps.new_to_him(run, parts)] == ["f", "n"]


def test_the_sender_is_asked_what_research_could_not_answer(marked, plan):
    plan.votes = {"enough-": ["no", "no", "yes", "no", "no", "yes", "yes"]}
    plan.asks = ["Who reviews it?", "When did CI last pass?", "When did the CI last pass?"]
    assert _research_and_swarm(marked) == pj.WAITING
    job = pj.get(marked["id"])
    assert job["state"] == pj.WAITING and pj.question_rounds(job) == 1
    asked = job["rounds"][-1]["questions"]
    # Grouped by meaning into one list: the two CI questions one group, kept
    # once, first because two voters asked it; the sender answers each once.
    assert [(q["id"], q["voters"]) for q in asked] == [("ask-1", 2), ("ask-2", 1)]
    assert asked[0]["question"] in ("When did CI last pass?", "When did the CI last pass?")
    assert asked[1]["question"] == "Who reviews it?"
    assert [a["ask"] for a in asked[0]["asked"]] == ["When did CI last pass?",
                                                     "When did the CI last pass?"]
    kept = json.loads((Path(marked["folder"]) / ps.STATE).read_text())
    assert kept["swarm"]["rounds"] == 1, "the round that asked is one of the three"

    pj.answer(marked["id"], {"ask-1": "Tuesday, run 99880."}, session=job["session"])
    assert (pe.place_of(marked).folder / "sender-answers/1.md").is_file()
    plan.opened.clear()
    assert ps.swarm(pj.get(marked["id"])) is None
    assert ("author", "session-author") in plan.opened, "each session resumed"
    conversation = json.loads((Path(marked["folder"]) / ps.RESULT).read_text())["conversation"]
    said = [m for m in conversation if m["from"] == "sender"]
    assert said == [{"from": "sender", "text": f'<answer id="ask-1" question="{asked[0]["question"]}">'
                                               'Tuesday, run 99880.</answer>'}]
    assert json.loads((Path(marked["folder"]) / ps.RESULT).read_text())["rounds"] == 2


def test_questions_are_grouped_by_meaning_one_wording_each_most_asked_first():
    # swarm ask_sender merged (Tim 2026-11-20): grouped by the engine's
    # embeddings, one wording kept a group, ordered by how many voters asked.
    def unit(*xs):
        n = sum(x * x for x in xs) ** 0.5
        return [x / n for x in xs]
    asks = [{"question": "enough-a", "voter": "editor", "ask": "Who reviews?"},
            {"question": "enough-a", "voter": "author", "ask": "When did CI pass?"},
            {"question": "enough-b", "voter": "cares_judge", "ask": "When was CI green?"},
            {"question": "enough-b", "voter": "editor", "ask": "Last green CI run?"},
            {"question": "enough-b", "voter": "author", "ask": "Who reviews it?"}]
    vectors = [unit(0, 0, 1), unit(1, 0.10, 0), unit(1, 0.12, 0), unit(1, 0.30, 0.0),
               unit(0, 0.05, 1)]
    groups = ps.group_by_meaning(asks, vectors)
    assert [(g["id"], g["voters"], len(g["asked"])) for g in groups] == [
        ("ask-1", 3, 3), ("ask-2", 2, 2)]
    assert groups[0]["question"] == "When was CI green?", "the wording nearest the rest"
    assert groups[1]["question"] == "Who reviews?", "on a tie, the first asked"
    apart = ps.group_by_meaning(asks[:2], [unit(1, 0), unit(0.8, 0.6)])
    assert len(apart) == 2, f"a cosine of 0.8 is under {ps.SAME_MEANING}: two meanings"


def test_asking_stops_once_answers_change_nothing_still_open(marked, plan):
    plan.votes = {"enough-": ["no"] * 30}
    plan.asks = ["What broke?"] * 30
    assert _research_and_swarm(marked) == pj.WAITING
    pj.answer(marked["id"], {"ask-1": "Nothing."},
              session=pj.get(marked["id"])["session"])
    assert ps.swarm(pj.get(marked["id"])) == pj.WAITING
    job = pj.get(marked["id"])
    assert pj.question_rounds(job) == 1, "the answers changed nothing still open: no more asked"
    assert pj.kind_of(job["rounds"][-1]) == pj.FINDINGS, "three rounds, and the swarm failed"
    result = json.loads((Path(marked["folder"]) / ps.RESULT).read_text())
    played = [m for m in result["conversation"] if m == {"from": "author",
                                                         "text": "author speaks"}]
    assert result["rounds"] == 3 and len(played) == 1 + 3, \
        "the research turn and three rounds, the one that asked among them"


def test_the_prep_the_author_revises_reaches_the_judges_given_it_each_round(marked, plan):
    plan.votes = {"order": ["no"] * 5}
    plan.preps = ["Prep one.", "Prep two.", "Prep three."]
    _research_and_swarm(marked)
    for name in ("decision_ready_judge", "background_judge"):
        turns = [t for t in plan.sent[name] if "A vote is called" not in t
                 and "Did the change touch" not in t]
        assert "Prep one." in turns[0]
        assert '<prep file="prep.md">Prep two.</prep>' in turns[1]
        assert '<prep file="prep.md">Prep three.</prep>' in turns[2]
    cares = [t for t in plan.sent["cares_judge"] if "A vote is called" not in t
             and "Did the change touch" not in t]
    assert all("<prep " not in t for t in cares[1:]), "the cares judge is never given the prep"


def test_a_failed_swarm_sends_the_sender_its_findings_and_its_answer_ends_the_run(marked,
                                                                                  plan):
    plan.votes = {"order": ["no"] * 15}
    assert _research_and_swarm(marked) == pj.WAITING
    job = pj.get(marked["id"])
    last = job["rounds"][-1]
    assert pj.kind_of(last) == pj.FINDINGS and pj.question_rounds(job) == 0
    assert [q["id"] for q in last["questions"]] == ["order"]
    assert "Is the most relevant information first? Not proven." in last["questions"][0][
        "question"] and "cares_judge says no" in last["questions"][0]["question"]
    assert json.loads((Path(marked["folder"]) / ps.RESULT).read_text())["rounds"] == 3
    assert [d["id"] for d in pj.to_tell_sender()] == [marked["id"]]
    pj.told_sender(marked["id"], 7)
    assert pj.to_tell_sender() == []
    done = pj.answer(marked["id"], {"order": "Lead with the decision."},
                     session=job["session"])
    assert done["state"] == pj.FAILED and pj.FAILED in pj.ENDED


def test_only_rounds_of_questions_count_toward_five(marked):
    for n in range(pj.MAX_ROUNDS):
        pj.failed_swarm(marked["id"], [{"id": "x", "question": "?"}], [])
        assert pj.ask(marked["id"], [{"id": f"q{n}", "question": "?"}])
    assert not pj.ask(marked["id"], [{"id": "q", "question": "?"}])
    assert pj.question_rounds(pj.get(marked["id"])) == pj.MAX_ROUNDS
