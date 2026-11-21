"""The research and swarm steps of a package run (workflow.yaml research,
swarm and tim_knowledge, approved by Tim 2026-11-18 and 2026-11-19; the
role prompts and judge prompts word for word, role_prompts.py).

Run after mark in JARVIS's own process (package_jobs.PRODUCERS), in the
run's folder (data/work/<id>/packages/<package id>/runs/<run id>/):

  research   the author's ChatSession (tier 1, its research tools: the
             research right's, the brain's own past conversations among
             them) is given its approved prompt and its material: the
             sender's two answers, the handoff's questions, and everything
             gathered and marked, each item with Tim's knowledge state. It
             starts from what is there (the Work item's research,
             data/work/<id>/research/; the marked evidence; an earlier
             run's research, copied into `earlier/`), searches only for what
             each question still lacks, and writes `research.md`. Every
             search a role makes is logged by its own tool server
             (research_log.py, `research-log.jsonl`).
  swarm      the author, the one editor, the four judges and the Theory of
             Mind reader in one conversation (sessions.BrainSwarmSession),
             logged word for word. A round: the author writes or revises
             `prep.md` and `script.md`; the script is marked (MarkingSession,
             the 28 labels, with Tim's material); the editor edits it (the
             program applies only edits whose old text is exactly there);
             the judges and the reader respond; a closed question reopens when
             its voters judge the script's change touched what it was about.
             Votes on every open question
             end each round (one a participant calls is held at once), each
             a turn in that participant's own session with a reason; a
             majority closes a question, a tie fails it; the author never
             votes on whether its own script passes, the reader never votes.
             The questions to prove are the handoff's (is there enough to
             answer each; the author votes on these) and the four judges'.

Each participant is given exactly its share of what Tim knows (tim_knowledge
who_gets_what): the author and the editor everything, each item marked; the
decision ready and background judges all the evidence, marked, and the
prep (again each round, as the author revises it); the cares and order judges, and the reader, only what he knows and
had access to, kept apart; the cares judge also the script's parts new to
him (`new_to_him`).

Asking the sender (swarm ask_sender): when an enough question fails, what
its no votes said to ask goes to the sender (package_jobs.ask; the job
waits on it, at most 2 hours, then Error). The swarm round that asked is
one of its three, as every round played is; the round of questions is
counted apart, toward its own five. Its answers are kept in the
evidence (sender-answers/) and said into the conversation as the sender's,
word for word; the enough questions reopen. At most five rounds of
questions; asking stops once a round of answers changes nothing still
open.

The swarm passes when every question is proven; after three rounds without
that it has failed: the sender is sent the findings (package_jobs.
failed_swarm; the server tells it and puts them on the Work item) and its
answer starts a new run from the kept research. A passed script's hits are
recorded as used (research_log.mark_used). Everything the swarm holds is
kept in `swarm.json` between the run's processes, each participant's
session resumed with it.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import (briefing_labels, model_tiers, package_evidence as pe, package_jobs,
                    package_mark, research_log, role_prompts, roles, sessions, work_research)
from jarvis.package_evidence import HAD_ACCESS, KNOWS, PROBABLY_NOT

RESEARCH, SWARM = "research", "swarm"
RESEARCH_MD, PREP_MD, SCRIPT_MD = "research.md", "prep.md", "script.md"
STATE = "swarm.json"
RESULT = "swarm-result.json"
CONVERSATION = "conversation.md"
EARLIER = "earlier"
AUTHOR, EDITOR, READER = "author", "editor", "theory_of_mind_reader"
# The participants, in the order they speak in a round.
NAMES = (AUTHOR, EDITOR, "cares_judge", "decision_ready_judge", "background_judge",
         "order_judge", READER)
# Each judge's question's id.
JUDGE_QUESTION = {"cares_judge": "cares", "decision_ready_judge": "decision_ready",
                  "background_judge": "background", "order_judge": "order"}
ENOUGH = "enough-"
# No turn is cut off by a clock the design does not give.
TIMEOUT: Optional[float] = None

# How a participant's ChatSession is made and its model picked (tests give
# their own).
CHAT: Callable[..., sessions.ChatSession] = sessions.BrainChatSession
PICK: Callable[..., model_tiers.Pick] = model_tiers.pick


def _log(line: str) -> None:
    """A line in the job's log (the run's process writes to it)."""
    print(line, flush=True)


# ── the run ───────────────────────────────────────────────────────────────

@dataclass
class Run:
    doc: dict
    place: pe.Place
    rows: list[pe.Row]
    marked: dict
    texts: dict[str, str] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return Path(self.doc["folder"])

    @property
    def log(self) -> Path:
        return self.folder / research_log.NAME

    @property
    def research(self) -> Path:
        return self.folder / RESEARCH_MD

    @property
    def prep(self) -> Path:
        return self.folder / PREP_MD

    @property
    def script(self) -> Path:
        return self.folder / SCRIPT_MD

    @property
    def work_research(self) -> Optional[Path]:
        work = self.doc.get("work")
        return work_research.folder_of(work) if work else None

    def reads(self, name: str) -> tuple[Path, ...]:
        """The folders a participant with file tools reads beside its run
        folder: the evidence, and for the author the Work item's research."""
        if not roles.role(name).release.file_tools:
            return ()
        out = [self.place.folder]
        if name == AUTHOR and self.work_research is not None and self.work_research.is_dir():
            out.append(self.work_research)
        return tuple(out)


def _run(doc: dict, step: str) -> Run:
    place = pe.place_of(doc)
    marked = package_mark.marked(place)
    if pe.gathered(place) is None or marked is None:
        raise package_jobs.Blocked(step, "The package's evidence was not gathered and marked.",
                                   f"JARVIS reached the {step} step.",
                                   "A new run, which gathers and marks the evidence first.")
    rows = pe.read_manifest(place)
    texts = {}
    for row in rows:
        if row.stored:
            try:
                texts[row.stored] = (place.folder / row.stored).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
    return Run(doc, place, rows, marked, texts)


# ── what each participant is given ────────────────────────────────────────

def evidence(run: Run) -> list[sessions.Known]:
    """Every stored file of the evidence, whole, with Tim's knowledge state."""
    return [sessions.Known(run.texts[r.stored], r.knowledge_state, run.place.item(r.stored))
            for r in run.rows if r.stored in run.texts]


def author_material(run: Run) -> list[sessions.Known]:
    """The gathered and marked material: each stored file whole, then what
    the mark step read in it (its facts and marked parts, each scored
    against the questions), with the file's knowledge state."""
    marks = {e["file"]: e for e in run.marked.get("files") or []}
    out = []
    for r in run.rows:
        if r.stored not in run.texts:
            continue
        out.append(sessions.Known(run.texts[r.stored], r.knowledge_state,
                                  run.place.item(r.stored)))
        entry = marks.get(r.stored)
        if entry is not None:
            out.append(sessions.Known(json.dumps(entry["marks"], ensure_ascii=False),
                                      r.knowledge_state,
                                      run.place.item(f"{package_mark.MARK}/{r.stored}.json")))
    return out


def _labels(*kinds: str) -> str:
    return "\n".join(sessions._tag("label", f"{label.means}\nExample: {label.example}"
                                   if label.example else label.means, name=label.name)
                     for label in briefing_labels.of_kind(*kinds))


def _sender_answers(doc: dict) -> str:
    said = ((doc.get("request") or {}).get("from_the_sender") or {}).get("answers") or {}
    return "\n".join(sessions._tag("answer", a.get("answer", ""), id=key,
                                   question=a.get("question", "")) for key, a in said.items())


def _handoff_questions(doc: dict) -> str:
    out = []
    for q in (doc.get("request") or {}).get("questions") or []:
        options = q.get("options_from_the_sender") or []
        shown = "\n".join(sessions._tag("option", json.dumps(o, ensure_ascii=False),
                                        whose="the sender's") for o in options)
        out.append(sessions._tag("question", q["text"] + (f"\n{shown}" if shown else ""),
                                 id=q["id"]))
    return "\n".join(out)


def system_of(name: str) -> str:
    """A participant's approved prompt's role part, as its system prompt."""
    prompt = role_prompts.PROMPTS[name]
    values = {AUTHOR: {
        "{the 14 keep labels from marking-labels.md: name, what it means, example}":
            _labels(briefing_labels.KEEP),
        "{the 14 fix and remove labels: name, what it means, example}":
            _labels(briefing_labels.FIX, briefing_labels.REMOVE)}}.get(name, {})
    if name == EDITOR:
        return sessions.editor_prompt([sessions.EditLabel(label.name, label.means, label.action)
                                       for label in briefing_labels.LABELS])
    return role_prompts.fill(prompt.system, values)


def material_of(name: str, values: dict[str, str]) -> str:
    """A participant's approved prompt's closing material, filled."""
    prompt = role_prompts.PROMPTS[name]
    wanted = role_prompts.PLACEHOLDER.findall(prompt.material)
    return role_prompts.fill(prompt.material, {p: values[p] for p in wanted})


def new_to_him(run: Run, marked: list[dict]) -> list[dict]:
    """The script's sentences new to Tim, by program from the knowledge
    states (tim_knowledge): a sentence is new to him unless every evidence
    item it cites (its stored path or item path in its words) is one he
    knows or had access to; a sentence citing none is one whose state could
    not be determined, which the design counts as probably does not know."""
    out = []
    for part in marked:
        if part.get("kind") != "sentence":
            continue
        words = part["segment"]
        states = {r.knowledge_state for r in run.rows if r.stored and (
            run.place.item(r.stored) in words or r.stored in words)}
        if not states or not states <= {KNOWS, HAD_ACCESS}:
            out.append(part)
    return out


def _parts_text(parts: list[dict]) -> str:
    return "\n".join(sessions._tag("part", p["segment"], id=p["id"]) for p in parts)


def evidence_note(run: Run) -> str:
    """Where the package's evidence is, for a participant that searches it
    (gather scoped_search: the path is given in the agents' instructions)."""
    return (f"The package's evidence is at {run.place.path} in JARVIS's knowledge: search it "
            f"with knowledge_search under that path. On disk it is in {run.place.folder}.")


def judge_asks(role: str) -> str:
    """A judge's question, in its approved prompt's own words."""
    found = re.search(r"Your question: \*\*(.+?)\*\*", role_prompts.PROMPTS[role].system)
    return found.group(1)


def questions(doc: dict) -> list[sessions.Question]:
    """What the swarm proves: whether there is enough to answer each of the
    handoff's questions (the author votes; a no says what to ask the
    sender), and the four judges' questions."""
    out = [sessions.Question(f"{ENOUGH}{q['id']}",
                             f"Is there enough to answer this question? {q['text']}",
                             author_votes=True, enough=True)
           for q in (doc.get("request") or {}).get("questions") or []]
    return out + [sessions.Question(qid, judge_asks(role))
                  for role, qid in JUDGE_QUESTION.items()]


# ── the swarm, made and kept ──────────────────────────────────────────────

def _kept(run: Run) -> dict:
    try:
        return json.loads((run.folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _keep(run: Run, swarm: sessions.BrainSwarmSession, kept: dict) -> None:
    """The swarm's state, and the session of each participant that has
    answered a turn (one never answered holds nothing to resume)."""
    for name, chat in swarm.participants.items():
        if getattr(chat, "session", None) and swarm.spoke(name):
            kept.setdefault("participants", {})[name] = {
                "session": chat.session, "engine": chat.pick.engine, "model": chat.model}
    kept["swarm"] = swarm.saved()
    pe.write_file(run.folder / STATE, json.dumps(kept, indent=2, ensure_ascii=False)
                  .encode("utf-8"))


class _Firsts:
    """Each participant's first message, made when its first turn comes:
    its approved prompt's closing material with its share, and where the
    evidence is for one that searches it."""

    def __init__(self, run: Run, material: list[sessions.Known]):
        self.run = run
        self.material = material
        self.marked: list[dict] = []        # the script's latest marks

    def __call__(self, name: str) -> str:
        run = self.run
        values = sessions.share_values(name, self.material)
        if name == AUTHOR:
            values = {"{sender_answers: plainly, why_now}": _sender_answers(run.doc),
                      "{the handoff's questions, each with the sender's options where it "
                      "gives them}": _handoff_questions(run.doc),
                      "{the gathered and marked material, with its knowledge state}":
                          sessions._material(sessions.share(AUTHOR, author_material(run)),
                                             marked=True)}
        values["{the prep}"] = _read(run.prep)
        values["{script}"] = _read(run.script)
        values["{the script parts new to him}"] = _parts_text(new_to_him(run, self.marked))
        said = material_of(name, values)
        if roles.role(name).release.tools:
            said = f"{said}\n\n{evidence_note(run)}" if said else evidence_note(run)
        return said


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build(run: Run, kept: dict, more: tuple[sessions.Question, ...] = ()
          ) -> tuple[sessions.BrainSwarmSession, _Firsts]:
    """The swarm: each participant on its role, its model picked (the
    author first, the judges then on the other brain when Settings says
    so), or resumed on the session it had; `more`, the questions the deck
    review adds (package_deck). Raises NoModel."""
    material = evidence(run)
    firsts = _Firsts(run, material)
    participants: dict[str, sessions.ChatSession] = {}
    author_engine = None
    for name in NAMES:
        role = roles.role(name)
        had = (kept.get("participants") or {}).get(name) or {}
        common = dict(reads=run.reads(name), search_log=run.log, on_log=_log)
        if had.get("session"):
            pick = model_tiers.Pick(had["engine"], had["model"], role.tier, "resumed",
                                    [had["model"]])
            chat = CHAT(name, system_of(name), run.folder, pick=pick, resume=had["session"],
                        **common)
        else:
            pick = PICK(role.kind, role.tier, author_engine=author_engine,
                        **package_mark.pick_args(run.doc))
            chat = CHAT(name, system_of(name), run.folder, pick=pick, **common)
        if name == AUTHOR:
            author_engine = pick.engine
        participants[name] = chat
    swarm = sessions.BrainSwarmSession(participants, questions(run.doc) + list(more),
                                       author=AUTHOR,
                                       editor=EDITOR, non_voters=(READER,), material=material,
                                       first=firsts)
    if kept.get("swarm"):
        swarm.restore(kept["swarm"])
    return swarm, firsts


# ── research ──────────────────────────────────────────────────────────────

def _carry_earlier(run: Run) -> Optional[Path]:
    """The earlier run's research (research.md and its search log), copied
    into `earlier/` to start from (record_and_start new_run carries)."""
    after = run.doc.get("after")
    if not after:
        return None
    try:
        before = package_jobs.get(after)
    except package_jobs.JobError:
        return None
    target = run.folder / EARLIER
    target.mkdir(parents=True, exist_ok=True)
    for name in (RESEARCH_MD, research_log.NAME, research_log.USED):
        source = Path(before["folder"]) / name
        if source.is_file():
            shutil.copyfile(source, target / name)
    return target


def research_note(run: Run, earlier: Optional[Path]) -> str:
    """The program's words to the author for its research: where what it
    starts from is, and where research.md goes (workflow.yaml research
    start_from_work_item and output)."""
    lines = []
    if run.work_research is not None and run.work_research.is_dir():
        lines.append(f"The Work item's research is in {run.work_research}: what was looked up "
                     f"for it, each query with its hits ({work_research.QUERIES_MD}), and the "
                     f"summaries of it ({work_research.SUMMARIES}) where they are written.")
    elif run.doc.get("work"):
        lines.append(f"The Work item {run.doc['work']} has no research written yet.")
    lines.append(f"The marked evidence is in {run.place.folder / package_mark.MARK} "
                 f"({package_mark.INDEX} names each file's marks).")
    lines.append(evidence_note(run))
    if earlier is not None:
        lines.append(f"An earlier run's research is in {earlier}: its {RESEARCH_MD} and its "
                     f"search log ({research_log.NAME}).")
    lines.append(f"Write your research in {run.research}: each finding with its source path, "
                 f"for each question.")
    return "\n".join(lines)


def research(doc: dict) -> None:
    """The research step of one run (package_jobs.PRODUCERS["research"])."""
    run = _run(doc, RESEARCH)
    kept = _kept(run)
    if kept.get("researched") and run.research.is_file():
        return
    earlier = _carry_earlier(run)

    async def go():
        swarm, _ = build(run, kept)
        author = swarm.participants[AUTHOR]
        try:
            await author.open()
            await swarm.turn(AUTHOR, TIMEOUT, add=research_note(run, earlier))
        finally:
            _keep(run, swarm, kept)
            await author.close()
    package_jobs.asked(RESEARCH, "the author",
                       "JARVIS asked the author to research the handoff's questions.", go())
    if not run.research.is_file():
        raise package_jobs.Blocked(RESEARCH, f"The author wrote no {RESEARCH_MD}.",
                                   "JARVIS asked the author for its research, each finding with "
                                   "its source path, for each question.",
                                   "Read the run's conversation and log for why; a new run.")
    kept["researched"] = True
    pe.write_file(run.folder / STATE, json.dumps(kept, indent=2, ensure_ascii=False)
                  .encode("utf-8"))


# ── the swarm ─────────────────────────────────────────────────────────────

def _take_answers(run: Run, swarm: sessions.BrainSwarmSession, kept: dict) -> None:
    """The sender's answers since the swarm last asked, said into the
    conversation as the sender's, word for word; every enough question
    reopens, and the next round is compared with what was open when it
    asked."""
    rounds = [r for r in package_jobs.get(run.doc["id"])["rounds"]
              if package_jobs.kind_of(r) == package_jobs.QUESTIONS]
    asking = kept.setdefault("asking", {})
    said = int(asking.get("answers_said") or 0)
    for r in rounds[said:]:
        if not r.get("answers"):
            break
        asked = {q["id"]: q["question"] for q in r["questions"]}
        swarm.say("sender", "\n".join(sessions._tag("answer", v, id=k, question=asked.get(k, ""))
                                      for k, v in r["answers"].items()))
        said += 1
        for qid, q in swarm.questions.items():
            if q.enough and swarm.state[qid] != sessions.OPEN:
                swarm.reopen(qid)
        asking["compare"] = asking.get("open_when_asked") or []
    asking["answers_said"] = said


def _to_ask(swarm: sessions.BrainSwarmSession) -> list[dict]:
    """What the failed enough questions' no votes said to ask the sender."""
    out = []
    for qid, q in swarm.questions.items():
        if q.enough and swarm.state[qid] == sessions.FAILED:
            for n, ask in enumerate(swarm.asks(qid), 1):
                out.append({"id": f"{qid}-{n}", "question": ask})
    return out


def _round_add(run: Run, swarm: sessions.BrainSwarmSession, firsts: _Firsts):
    """What a participant is given each round after its first, beside the
    script: the prep as the author last wrote it, to every role whose
    approved material holds it, and to the cares judge the script's parts
    new to him."""
    def add(name: str, _text: str, marked: list) -> str:
        firsts.marked = marked
        if not swarm.spoke(name):
            return ""               # its first message holds them (_Firsts)
        out = []
        if "{the prep}" in role_prompts.PLACEHOLDER.findall(
                role_prompts.PROMPTS[name].material):
            out.append(sessions._tag("prep", _read(run.prep), file=PREP_MD))
        if name == "cares_judge":
            out.append(_parts_text(new_to_him(run, marked)))
        return "\n\n".join(t for t in out if t)
    return add


def conversation_md(result: dict) -> str:
    """The whole conversation, word for word, each message with who said it."""
    out = ["# The swarm's conversation", ""]
    for n, m in enumerate(result["conversation"], 1):
        out += [f"## {n}. {m['from']}", "", m["text"], ""]
    return "\n".join(out)


def _write_result(run: Run, result: dict) -> None:
    pe.write_file(run.folder / RESULT, json.dumps(result, indent=2, ensure_ascii=False)
                  .encode("utf-8"))
    pe.write_file(run.folder / CONVERSATION, conversation_md(result).encode("utf-8"))


def findings_questions(result: dict) -> list[dict]:
    """A failed swarm's findings as the questions its sender answers: each
    question not proven, with every vote against it and what it said was
    missing."""
    out = []
    for f in result["findings"]:
        said = "; ".join(f"{v['participant']}: {v['reason']}"
                         + (f" Missing: {v['ask']}" if v.get("ask") else "")
                         for v in f["against"]) or "no vote closed it"
        out.append({"id": f["question"], "question": f"{f['asks']} Not proven. {said}"})
    return out


def swarm(doc: dict) -> Optional[str]:
    """The swarm step of one run (package_jobs.PRODUCERS["swarm"]): WAITING
    when it waits on the sender (questions, or a failed swarm's findings)."""
    run = _run(doc, SWARM)
    kept = _kept(run)
    if not kept.get("researched"):
        raise package_jobs.Blocked(SWARM, "The research step did not finish.",
                                   "JARVIS reached the swarm step.", "A new run.")
    pe.wait_indexed(run.place, SWARM)       # the sender's answers, indexed as written
    labels = briefing_labels.for_marking()
    knowledge = package_mark.tim_known(run.rows, run.texts)
    outcome: dict = {}

    async def go():
        swarm_, firsts = build(run, kept)
        marking = package_mark.MARKING(doc)

        async def mark(text: str) -> list[dict]:
            firsts.marked = await marking.mark(text, labels, knowledge, TIMEOUT)
            return firsts.marked
        try:
            await swarm_.open()
            _take_answers(run, swarm_, kept)
            asking = kept.setdefault("asking", {})
            while True:
                await swarm_.play_round(
                    TIMEOUT, run.script, mark,
                    author_add=f"Write the prep in {run.prep} and the script in {run.script}, "
                               f"or revise them.",
                    prep=lambda: _read(run.prep), add_for=_round_add(run, swarm_, firsts))
                if "compare" in asking:
                    # A round of answers that changed nothing still open: no
                    # more asking (decided "questions to the sender").
                    if sorted(swarm_.not_proven()) == sorted(asking.pop("compare")):
                        asking["stopped"] = True
                # Every round played is one of the three (plan.ends).
                result = swarm_.end_round()
                if result is not None:
                    outcome["result"] = result
                    return
                ask = [] if asking.get("stopped") else _to_ask(swarm_)
                if ask and package_jobs.ask(doc["id"], ask):
                    asking["open_when_asked"] = sorted(swarm_.not_proven())
                    outcome["waiting"] = True
                    return
                _keep(run, swarm_, kept)
        finally:
            _keep(run, swarm_, kept)
            await swarm_.close()
    package_jobs.asked(SWARM, "the swarm",
                       "JARVIS ran the swarm: the author, the editor, the four judges and the "
                       "Theory of Mind reader.", go())
    if outcome.get("waiting"):
        return package_jobs.WAITING
    result = outcome["result"]
    _write_result(run, result)
    if result["result"] == "passed":
        script = _read(run.script)
        used = research_log.mark_used(run.log, script, roots=(
            run.place.path, str(run.place.folder), str(run.folder)))
        package_jobs.noted(doc["id"], "swarm", {
            "result": "passed", "rounds": result["rounds"], "script": str(run.script),
            "prep": str(run.prep), "conversation": str(run.folder / CONVERSATION),
            "hits_used": sum(len(u["used"]) for u in used["used"])})
        return None
    package_jobs.noted(doc["id"], "swarm", {"result": "not passed", "rounds": result["rounds"],
                                            "conversation": str(run.folder / CONVERSATION)})
    package_jobs.failed_swarm(doc["id"], findings_questions(result), result["findings"])
    return package_jobs.WAITING
