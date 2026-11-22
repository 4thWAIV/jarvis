"""The research and swarm steps of a package run (workflow.yaml research,
swarm and tim_knowledge, approved by Tim 2026-11-18 and 2026-11-19; the
cooperative swarm, construction-drafts.md "Cooperative swarm", approved
2026-11-21; every role's prompt word for word, role_prompts.py).

Run after mark in JARVIS's own process (package_jobs.PRODUCERS), in the
run's folder (data/work/<id>/packages/<package id>/runs/<run id>/):

  research   the program runs every research search at once (research
             key_searches; construction-drafts.md "Research and prep
             reorganized", Tim 2026-11-21): for each of the handoff's
             questions, words and meaning under the package's evidence, each
             Work item's folder and the whole record, his conversation's
             turns, his memory and the brain's own past conversations; and
             each Work item's PRs and CI runs and its thread
             (work_research's searches, straight on the engine with the
             run's own patience). The author's ChatSession (tier 1) is given
             its approved prompt, its material (the sender's two answers,
             the handoff's questions, everything gathered and marked, each
             item with Tim's knowledge state) and what the searches found,
             in ONE turn at medium effort, and writes `research.md` and
             `prep.md` together. When it needs more it names the searches
             in its answer; the program runs them at once and gives it one
             more turn. What was looked up is `research-searches.md`, and
             every search is in the run's search log (research_log.py,
             `research-log.jsonl`), the program's as `jarvis`. Research is
             done once a run, never again each pass: the author researches
             again only for what a sign-off names as missing evidence.
  swarm      the team in one conversation (sessions.BrainSwarmSession),
             logged word for word: the author; Tim's reader (does Tim care
             about each part, and what would he ask); the context owner (is
             the background right); the structure and decision owner (is
             the most relevant information first, is the decision properly
             prepared); the editor (the language). Each owner fixes its
             concern by editing the slides it touches (each edit applied by
             the program only where its old text is exactly there, with its
             reason and source), asks the sender only what only the sender
             knows, and signs its concern off in the same turn: resolved,
             with a quoted reason, or not yet, with what is left and the fix
             it suggests. Nobody votes.

             1 outline: from its research and prep, the author writes the
               outline (outline.yaml, outline/1: each slide's title, its one
               point, the question it answers, its evidence by source path);
               the three owners work on it in parallel; the author settles
               conflicts, saying why; each owner signs the outline off.
             2 fill: the author writes the script (script.yaml, script/1)
               from the agreed outline; the program marks it in batches; the
               author revises it once against the marks.
             3 fix pass: the owners and the editor in parallel edit and
               sign off; what is left is merged by the program, by meaning,
               into one numbered list, each item with who raised it and the
               fix suggested.
             4 close: the author works the list; each owner re-checks only
               its own items and signs off.
             Done when every concern is signed off; at most two fix passes
             (ROUNDS_MAX), then what is left goes to the sender.

Each participant is given exactly its share of what Tim knows (tim_knowledge
who_gets_what, sessions.SHARES): Tim's reader only what he knows and had
access to, kept apart; everyone else everything, each item marked.

Asking the sender (swarm ask_sender): what the participants ask in their
turns since the sender was last asked, every one of them, is grouped by
meaning with the engine's embeddings into one list (`group_by_meaning`: one
wording a group, the groups ordered by how many asked; merged, Tim
2026-11-20) and goes to the sender after the outline is signed off and
after each fix pass and re-check, which answers each group once
(package_jobs.ask; the job waits on it, at most 2 hours, then Error). Its
answers are kept in the evidence (sender-answers/) and said into the
conversation as the sender's, word for word. At most five rounds of
questions; asking stops once a round of answers changes nothing still
open.

A passed script's hits are recorded as used (research_log.mark_used). What
the swarm holds is kept in `swarm.json` between the run's processes, each
participant's session resumed with it, and the flow goes on from the stage
it reached (`flow`).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import (briefing_labels, model_tiers, package_evidence as pe, package_jobs,
                    package_mark, package_script, records, research_log, role_prompts, roles,
                    sessions, work_research)

RESEARCH, SWARM = "research", "swarm"
RESEARCH_MD, PREP_MD = "research.md", "prep.md"
SCRIPT = package_script.FILE            # script.yaml, script/1
OUTLINE = package_script.OUTLINE_FILE   # outline.yaml, outline/1
STATE = "swarm.json"
RESULT = "swarm-result.json"
CONVERSATION = "conversation.md"
EARLIER = "earlier"
PROGRAM = sessions.PROGRAM
AUTHOR, EDITOR = "author", "editor"
READER, CONTEXT, STRUCTURE = "tims_reader", "context_owner", "structure_owner"
# The participants, in the order their parallel turns go into the
# conversation.
NAMES = (AUTHOR, READER, CONTEXT, STRUCTURE, EDITOR)
OWNERS = (READER, CONTEXT, STRUCTURE)       # who work on the outline
FIXERS = OWNERS + (EDITOR,)                 # who work in a fix pass
ROUNDS_MAX = sessions.ROUNDS_MAX            # fix passes
# Every role session call is held to the run's own bounds, Settings'
# run_timeout_sec and run_idle_sec (run_executor.bounds, applied by
# sessions.BrainChatSession): None names no other wall clock.
TIMEOUT: Optional[float] = None

# How a participant's ChatSession is made and its model picked (tests give
# their own).
CHAT: Callable[..., sessions.ChatSession] = sessions.BrainChatSession
PICK: Callable[..., model_tiers.Pick] = model_tiers.pick


def _log(line: str) -> None:
    """A line in the job's log (the run's process writes to it)."""
    print(line, flush=True)


# ── the concerns ──────────────────────────────────────────────────────────

def _asked_in(role: str) -> list[str]:
    """An owner's sign-off questions, Tim's words in its approved prompt."""
    return re.findall(r"\*\*(.+?)\*\*", role_prompts.PROMPTS[role].system)


def _language() -> str:
    """The editor's concern, in its approved prompt's words."""
    found = re.search(r"You own (the language: .+?\.)", role_prompts.PROMPTS[EDITOR].system)
    return found.group(1)


def concerns() -> list[sessions.Concern]:
    """Each concern and its owner (owner-prompts.md): Tim's four judge
    questions word for word, and the editor's language."""
    cares, = _asked_in(READER)
    background, = _asked_in(CONTEXT)
    order, decision = _asked_in(STRUCTURE)
    return [sessions.Concern("cares", cares, READER),
            sessions.Concern("background", background, CONTEXT),
            sessions.Concern("order", order, STRUCTURE),
            sessions.Concern("decision_ready", decision, STRUCTURE),
            sessions.Concern("language", _language(), EDITOR)]


OUTLINE_CONCERNS = ("cares", "background", "order", "decision_ready")
SCRIPT_CONCERNS = OUTLINE_CONCERNS + ("language",)


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
        return self.folder / SCRIPT

    @property
    def outline(self) -> Path:
        return self.folder / OUTLINE

    def script_file(self) -> package_script.ScriptFile:
        """The script in its structured form, checked against the handoff's
        questions."""
        return package_script.ScriptFile(self.script, question_ids(self.doc))

    def outline_file(self) -> package_script.OutlineFile:
        return package_script.OutlineFile(self.outline, question_ids(self.doc))

    @property
    def work_research(self) -> Optional[Path]:
        work = self.doc.get("work")
        return work_research.folder_of(work) if work else None

    def reads(self, name: str) -> tuple[Path, ...]:
        """The folders a participant with file tools reads beside its run
        folder: the evidence, and for the author the Work item's research."""
        if not roles.role(name).release.reads_files:
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


def evidence_note(run: Run) -> str:
    """Where the package's evidence is, for a participant that searches it
    (gather scoped_search: the path is given in the agents' instructions)."""
    return (f"The package's evidence is at {run.place.path} in JARVIS's knowledge: search it "
            f"with knowledge_search under that path. On disk it is in {run.place.folder}.")


def question_ids(doc: dict) -> list[str]:
    """The handoff's questions' ids: the ones a script slide may answer."""
    return [q["id"] for q in (doc.get("request") or {}).get("questions") or []]



# ── the swarm, made and kept ──────────────────────────────────────────────

def _kept(run: Run) -> dict:
    try:
        return json.loads((run.folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(run: Run, kept: dict) -> None:
    pe.write_file(run.folder / STATE, json.dumps(kept, indent=2, ensure_ascii=False)
                  .encode("utf-8"))


def _keep(run: Run, swarm: sessions.BrainSwarmSession, kept: dict) -> None:
    """The swarm's state, and the session of each participant that has
    answered a turn (one never answered holds nothing to resume)."""
    for name, chat in swarm.participants.items():
        if getattr(chat, "session", None) and swarm.spoke(name):
            kept.setdefault("participants", {})[name] = {
                "session": chat.session, "engine": chat.pick.engine, "model": chat.model}
    kept["swarm"] = swarm.saved()
    _save(run, kept)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class _Firsts:
    """Each participant's first message, made when its first turn comes:
    its approved prompt's closing material with its share, and where the
    evidence is for one that searches it."""

    def __init__(self, run: Run, material: list[sessions.Known]):
        self.run = run
        self.material = material

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
        values["{the prep}"] = sessions._tag("prep", _read(run.prep), file=PREP_MD)
        said = material_of(name, values)
        if name == EDITOR:
            # The editor's approved prompt closes on no material: it is
            # given its share of the evidence, each item marked, and the
            # prep it names ("You are given the script, ..., the prep"),
            # here.
            said = "\n".join([sessions._material(sessions.share(EDITOR, self.material),
                                                 marked=True), values["{the prep}"]])
        if roles.role(name).release.tools:
            said = f"{said}\n\n{evidence_note(run)}" if said else evidence_note(run)
        return said


def build(run: Run, kept: dict, more: tuple[sessions.Concern, ...] = (),
          effort: Optional[dict[str, str]] = None) -> sessions.BrainSwarmSession:
    """The swarm: each participant on its role, its model picked (the
    author first, the owners then on the other brain from it when Settings
    says so), or resumed on the session it had; `more`, the concerns the
    deck review adds (package_deck); `effort`, a participant's effort for
    this launch in place of its role's (the author's research turn).
    Raises NoModel."""
    material = evidence(run)
    participants: dict[str, sessions.ChatSession] = {}
    author_engine = None
    for name in NAMES:
        role = roles.role(name)
        had = (kept.get("participants") or {}).get(name) or {}
        common = dict(reads=run.reads(name), search_log=run.log, on_log=_log)
        if name in (effort or {}):
            common["role_spec"] = dataclasses.replace(role, effort=effort[name])
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
    swarm = sessions.BrainSwarmSession(participants, concerns() + list(more), author=AUTHOR,
                                       material=material, first=_Firsts(run, material))
    if kept.get("swarm"):
        swarm.restore(kept["swarm"])
    return swarm


TALK = sessions._object({"message": {"type": "string",
                                     "description": "What you say in the conversation."}})


def author_form(work: sessions.Script) -> dict:
    """The author's turn: what it says, what it writes (`work.form`), and
    what only the sender can answer (the approved author prompt: it says,
    for each question, whether there is enough and if not what to ask)."""
    return sessions._object({**TALK["properties"], **work.form,
                             "ask_sender": sessions.ASK_SENDER})


# ── the stages' times ─────────────────────────────────────────────────────

class Clock:
    """When each stage of the swarm began and ended, kept with its state
    (`times`), so a run's report says what took how long."""

    def __init__(self, kept: dict):
        self.times = kept.setdefault("times", [])

    def __call__(self, stage: str):
        clock = self

        class _At:
            def __enter__(self):
                self.entry = {"stage": stage, "began": time.time(), "ended": None}
                clock.times.append(self.entry)

            def __exit__(self, *_):
                self.entry["ended"] = time.time()
                self.entry["seconds"] = round(self.entry["ended"] - self.entry["began"], 1)
        return _At()


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


# What the program looked up, given to the author and kept beside the run.
SEARCHES_MD = "research-searches.md"
# The author's research turn (construction-drafts.md "Research and prep
# reorganized", Tim 2026-11-21): medium effort, for that turn alone.
RESEARCH_EFFORT = "medium"
# The program's searches, in the research log, as the program's own.
SEARCHER = PROGRAM

MORE_SEARCHES = sessions._array(sessions._object({
    "words": {"type": "string", "description": "What to search for."},
    "under": {"type": "string",
              "description": "The path in JARVIS's knowledge to search under, or empty to "
                             "search everywhere JARVIS searched."},
    "why": {"type": "string", "description": "What a question still lacks that it finds."}}))
RESEARCH_FORM = sessions._object({**TALK["properties"], "more_searches": MORE_SEARCHES})


def _patience() -> float:
    """How long a research search is waited for: the run's own wall clock
    (Settings' run_timeout_sec, run_executor.bounds), as its role calls are."""
    from jarvis import run_executor
    return run_executor.bounds()[0]


# How the research step searches the engine (tests give their own).
SEARCH: Callable[[], work_research.Search] = lambda: work_research.engine_search(_patience())


def _unders(run: Run) -> list[Optional[str]]:
    """Where each question is searched (research key_searches): the
    package's evidence, each Work item's folder (its research and earlier
    packages), and across the whole record (None)."""
    from jarvis import package_gather, work_store
    return ([run.place.path] + [work_store.folder_of(w) for w in package_gather._work_ids(run.doc)]
            + [None])


def _looked_up(run: Run, calls: list) -> list[dict]:
    """The searches run at once, each logged as the program's own in the
    run's research log with its seconds (research_log.py, so the hits the
    script uses are marked used, and the step's timing, run_report, has
    each search)."""
    looked = work_research.at_once(calls)
    for q in looked:
        arguments = {"query": q["query"], "under": q.get("under"), "kinds": q.get("kinds")}
        research_log.record(run.log, SEARCHER, q["tool"], arguments, not q.get("error"),
                            json.dumps(q["hits"], ensure_ascii=False, default=str),
                            q.get("seconds"))
    return looked


def first_searches(run: Run, search: work_research.Search) -> list[dict]:
    """Every research search, at once (research key_searches, run by the
    program): for each of the handoff's questions, words and meaning under
    the package's evidence, each Work item's folder and the whole record, his
    conversation's turns, his memory and the brain's own past
    conversations; and each Work item's PRs and CI runs and its thread."""
    from jarvis import package_gather, work_store
    unders = _unders(run)
    calls = []
    for q in (run.doc.get("request") or {}).get("questions") or []:
        calls += work_research.key_searches(q["text"], search, unders)
    for work in package_gather._work_ids(run.doc):
        calls += work_research.item_searches(work_store.get(work))
    return _looked_up(run, calls)


def more_searches(run: Run, search: work_research.Search, named: list[dict]) -> list[dict]:
    """The searches the author named, at once: each its words, run as the
    key searches under the path it gave, else everywhere the first ones
    looked."""
    calls = []
    for want in named:
        words = (want.get("words") or "").strip()
        if words:
            under = (want.get("under") or "").strip() or None
            calls += work_research.key_searches(words, search,
                                                [under] if under else _unders(run))
    return _looked_up(run, calls)


def _searched_text(run: Run, looked: list[dict], heading: str, first: bool) -> str:
    """What was looked up, written beside the run (SEARCHES_MD: the first
    searches, then the author's added to it) and as the author is given it."""
    text = work_research.markdown(heading, looked)
    with open(run.folder / SEARCHES_MD, "w" if first else "a", encoding="utf-8") as out:
        out.write(text + "\n")
    return sessions._tag("searched", text, file=SEARCHES_MD)


def research_note(run: Run, earlier: Optional[Path], searched: str) -> str:
    """The program's words to the author for its research: what JARVIS
    looked up for it, where what it starts from is, and where research.md
    and prep.md go (construction-drafts.md "Research and prep reorganized";
    workflow.yaml research start_from_work_item and output)."""
    lines = [f"JARVIS ran every research search at once for each of your questions; "
             f"what each found is below ({run.folder / SEARCHES_MD})."]
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
                 f"for each question. Write the prep in {run.prep}. Write both in this turn.")
    lines.append("If a question still lacks something, name the searches that would find it "
                 "in `more_searches`: JARVIS runs them all at once and gives you what they "
                 "found, once, to finish both files. Otherwise leave it empty.")
    return "\n".join(lines) + "\n\n" + searched


def more_note(run: Run, searched: str) -> str:
    return (f"JARVIS ran the searches you named, at once; what each found is below. Finish "
            f"your research in {run.research} and the prep in {run.prep} now.\n\n" + searched)


def research(doc: dict) -> None:
    """The research step of one run (package_jobs.PRODUCERS["research"]):
    the program's searches, all at once; the author's one turn writing the
    research and the prep at medium effort; and when it names more, those
    searches at once and one more turn."""
    run = _run(doc, RESEARCH)
    kept = _kept(run)
    if kept.get("researched") and run.research.is_file() and run.prep.is_file():
        return
    earlier = _carry_earlier(run)
    clock = Clock(kept)
    search = SEARCH()
    with clock("research-searches"):
        searched = _searched_text(run, first_searches(run, search),
                                  "What JARVIS looked up for the handoff's questions", True)

    async def go():
        swarm = build(run, kept, effort={AUTHOR: RESEARCH_EFFORT})
        author = swarm.participants[AUTHOR]
        try:
            await author.open()
            with clock(RESEARCH):
                answer = await swarm.answer(AUTHOR, TIMEOUT,
                                            research_note(run, earlier, searched), RESEARCH_FORM)
            named = [w for w in answer.get("more_searches") or [] if (w.get("words") or "").strip()]
            if named:
                with clock("research-more-searches"):
                    more = await asyncio.to_thread(more_searches, run, search, named)
                    more_text = _searched_text(run, more, "What JARVIS looked up for the "
                                                          "searches the author named", False)
                with clock("research-more"):
                    await swarm.answer(AUTHOR, TIMEOUT, more_note(run, more_text), TALK)
        finally:
            _keep(run, swarm, kept)
            await author.close()
    package_jobs.asked(RESEARCH, "the author",
                       "JARVIS asked the author to write the research and the prep from what "
                       "its searches found.", go())
    missing = [p.name for p in (run.research, run.prep) if not p.is_file()]
    if missing:
        raise package_jobs.Blocked(RESEARCH, f"The author wrote no {' and no '.join(missing)}.",
                                   "JARVIS gave the author what its searches found and asked for "
                                   "its research, each finding with its source path, for each "
                                   "question, and the prep.",
                                   "Read the run's conversation and log for why; a new run.")
    kept["researched"] = True
    _save(run, kept)


# ── grouping by meaning ───────────────────────────────────────────────────

# Two texts mean the same when the engine's embeddings of their words are at
# least this close (cosine). Measured on the engine's model
# (multilingual-e5-small) on 2026-11-20: rewordings of one question 0.86 to
# 0.95, different questions at most 0.82.
SAME_MEANING = 0.85
EMBED_DEADLINE = 90.0


def _engine_embed(texts: list[str]) -> list[list[float]]:
    """Each text's vector by the knowledge engine's own model."""
    return records.uds().embed(texts, EMBED_DEADLINE)


# How texts are embedded to be grouped (tests give their own).
EMBED: Callable[[list[str]], list[list[float]]] = _engine_embed


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def meaning_groups(vectors: list[list[float]], who: list[str]) -> list[tuple[list[int], int]]:
    """The texts grouped by meaning: a text joins the first group every
    member of which it is SAME_MEANING to, else starts one; each group
    keeps one wording, its member nearest in meaning to the rest (the first
    on a tie); the groups ordered by how many (`who`, each text's) said it,
    then by which came first. Each (its members, the one kept)."""
    groups: list[list[int]] = []
    for i in range(len(vectors)):
        for group in groups:
            if all(_cosine(vectors[i], vectors[j]) >= SAME_MEANING for j in group):
                group.append(i)
                break
        else:
            groups.append([i])

    def kept(group: list[int]) -> int:
        return max(group, key=lambda i: (sum(_cosine(vectors[i], vectors[j])
                                             for j in group if j != i), -i))
    ordered = sorted(groups, key=lambda g: (-len({who[i] for i in g}), g[0]))
    return [(g, kept(g)) for g in ordered]


def group_by_meaning(asks: list[dict], vectors: list[list[float]]) -> list[dict]:
    """The questions to the sender grouped by meaning (swarm ask_sender
    merged, Tim 2026-11-20): each ask is {"stage" (where it was asked),
    "from" (who asked), "ask"}; one wording a group, with how many asked
    it (`askers`), and every merge at the SAME_MEANING line (`merges`: the
    wording kept, the one merged into it and their score), for the run's
    quality report (run_report)."""
    who = [a["from"] for a in asks]
    return [{"id": f"ask-{n}", "question": asks[k]["ask"], "askers": len({who[i] for i in g}),
             "asked": [asks[i] for i in g],
             "merges": [{"kept": asks[k]["ask"], "merged": asks[i]["ask"],
                         "score": round(_cosine(vectors[k], vectors[i]), 4)}
                        for i in g if i != k]}
            for n, (g, k) in enumerate(meaning_groups(vectors, who), 1)]


def _embedded(texts: list[str], what: str) -> list[list[float]]:
    from jarvis.knowledge_client import EngineUnavailable
    try:
        return EMBED(texts)
    except EngineUnavailable as e:
        raise package_jobs.Blocked(
            SWARM, f"The engine could not embed {what} to group them by meaning: {e}",
            f"JARVIS asked the knowledge engine for the embeddings of {what}.",
            "Install the embedding model (Settings, Knowledge) and keep the knowledge engine "
            "up, then a new run.") from None


def _once(items: list[dict]) -> list[dict]:
    """Each the first time it is said (an owner's one fix for two of its
    concerns is one fix)."""
    seen, out = set(), []
    for item in items:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def merged_left(swarm: sessions.BrainSwarmSession, among) -> list[dict]:
    """What the concerns not signed off have left, merged by meaning into
    one numbered list (construction-drafts.md "Cooperative swarm" flow 3):
    each item one wording of what is left, who raised it, each one's
    suggested fix and any evidence named as missing."""
    items = swarm.left(among)
    if not items:
        return []
    vectors = _embedded([i["what"] for i in items], "what the owners left")
    out = []
    for n, (g, k) in enumerate(meaning_groups(vectors, [i["owner"] for i in items]), 1):
        members = [items[i] for i in g]
        out.append({"id": n, "what": items[k]["what"],
                    "raised_by": list(dict.fromkeys(m["owner"] for m in members)),
                    "concerns": list(dict.fromkeys(m["concern"] for m in members)),
                    "fixes": _once([{"by": m["owner"], "fix": m["fix"]}
                                    for m in members if m["fix"]]),
                    "evidence_missing": _once([{"by": m["owner"], "needs": m["evidence_missing"]}
                                               for m in members if m["evidence_missing"]])})
    return out


def list_text(items: list[dict]) -> str:
    """The merged list as the program says it into the conversation."""
    lines = []
    for item in items:
        lines.append(f"{item['id']}. {item['what']} (raised by {', '.join(item['raised_by'])}; "
                     f"{', '.join(item['concerns'])})")
        lines += [f"   Suggested fix ({f['by']}): {f['fix']}" for f in item["fixes"]]
        lines += [f"   Evidence missing ({e['by']}): {e['needs']}"
                  for e in item["evidence_missing"]]
    return "\n".join(lines)


# ── the sender's answers ──────────────────────────────────────────────────

def _take_answers(run: Run, swarm: sessions.BrainSwarmSession, kept: dict) -> None:
    """The sender's answers since the swarm last asked, said into the
    conversation as the sender's, word for word; the next checkpoint is
    compared with what was open when it asked."""
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
        asking["compare"] = asking.get("open_when_asked") or []
    asking["answers_said"] = said


def _ask_sender(run: Run, swarm: sessions.BrainSwarmSession, kept: dict, among) -> bool:
    """At a checkpoint, what the participants asked since the sender was
    last asked, grouped by meaning, goes to the sender: True when the job
    now waits on it. A round of answers that changed nothing still open
    stops the asking (decided "questions to the sender")."""
    asking = kept.setdefault("asking", {})
    still = sorted(swarm.not_resolved(among))
    if "compare" in asking and still == sorted(asking.pop("compare")):
        asking["stopped"] = True
    asks = swarm.to_send()
    if not asks or asking.get("stopped"):
        return False
    grouped = group_by_meaning(
        [{"stage": a["stage"], "from": a["from"], "ask": a["ask"]} for a in asks],
        _embedded([a["ask"] for a in asks], "the swarm's questions to the sender"))
    if not package_jobs.ask(run.doc["id"], grouped):
        return False
    asking.setdefault("groups", []).extend(grouped)
    swarm.sent()
    asking["open_when_asked"] = still
    return True


# ── one turn of the owners, and the author's ──────────────────────────────

async def owners_turn(swarm: sessions.BrainSwarmSession, names, among, work, stage: str,
                      add_of: Callable[[str], str]) -> None:
    """Owners in parallel (each that owns one of `among`): each edits
    `work` (its edits applied by the program, in the order the owners are
    given, only where the old text is exactly there), asks the sender what
    only the sender knows, and signs its concerns off, in the one turn.
    What was applied and refused is said into the conversation."""
    turns = {}
    for name in names:
        mine = swarm.concerns_of(name, among)
        if not mine:
            continue
        sign = "\n".join(sessions._tag("concern", swarm.concerns[c].asks, id=c) for c in mine)
        turns[name] = (f"{add_of(name)}\n\nSign off, by id:\n{sign}",
                       sessions.owner_form(mine, work.edit_form))
    answers = await swarm.together(turns, TIMEOUT)
    said = []
    for name, answer in answers.items():
        edits = [e for e in answer.get("edits") or [] if isinstance(e, dict)]
        if edits:
            _, applied, refused = work.edit(edits)
            said.append(f"{name}'s edits to {work.name}: {len(applied)} applied, "
                        f"{len(refused)} refused (old text not exactly there, or it would "
                        f"not check)." + "".join(f"\n- refused: {json.dumps(e, ensure_ascii=False)}"
                                                 for e in refused))
        swarm.sign(name, answer, stage, among)
        swarm.ask(name, answer, stage)
    if said:
        swarm.say(PROGRAM, "\n".join(said))


async def author_writes(swarm: sessions.BrainSwarmSession, work, stage: str, add: str) -> bool:
    """The author's turn writing `work` (the outline, the script): what it
    answers is checked by the program and written; one that does not check
    is said back with every problem and asked again, at most ROUNDS_MAX
    times. False when it never checked."""
    form = author_form(work)
    for attempt in range(ROUNDS_MAX + 1):
        answer = await swarm.answer(swarm.author, TIMEOUT, add if attempt == 0 else "", form)
        swarm.ask(swarm.author, answer, stage)
        problems = work.take(answer)
        if not problems:
            return True
        swarm.say(PROGRAM, f"The {work.key} in your answer does not check against "
                           f"{work.version}. Answer the whole {work.key} again, in its form:\n"
                  + "\n".join(f"- {p}" for p in problems))
    return False


# ── the program's words to each ───────────────────────────────────────────

def outline_note(run: Run) -> str:
    return (f"From your research and the prep, give the outline in your answer, as "
            f"`outline` in the schema given with this call ({package_script.OUTLINE_VERSION}): "
            f"JARVIS checks it and writes it to {run.outline}.")


def settle_note(run: Run) -> str:
    return ("The owners have worked on the outline, above. Settle any conflict between them, "
            "saying why, and give the whole outline, revised or as it stands.\n"
            + sessions._tag("outline", _read(run.outline), file=OUTLINE))


def fill_note(run: Run) -> str:
    return (f"Write the script from the agreed outline, below. Give it in your answer as "
            f"`script` in the schema given with this call ({package_script.VERSION}), the "
            f"whole script: JARVIS checks it and writes it to {run.script}.\n"
            + sessions._tag("outline", _read(run.outline), file=OUTLINE))


def revise_note(marked: list[dict]) -> str:
    return (f"{role_prompts.AUTHOR_REVISE}\n"
            + sessions._tag("marked", sessions.marked_slides(marked), file=SCRIPT)
            + "\nGive the whole script again in your answer, revised.")


def close_note(run: Run, items: list[dict]) -> str:
    """The author's words for the close: the list is in the conversation;
    where an item names missing evidence, the author researches it."""
    out = ["Work through the list above, item by item: change the script or say why not. "
           "Give the whole script in your answer."]
    if any(item["evidence_missing"] for item in items):
        out.append(f"Some items name evidence that is not in the material: research it with "
                   f"your tools, add what you find to {run.research}, each finding with its "
                   f"source path, and use it.")
    return "\n".join(out)


def _work_text(name: str, run: Run, work, marked: list[dict], kept: dict) -> str:
    """What one owner is given beside its instructions: the outline or
    script as it is now; the editor the script marked part by part; those
    given the prep (`gets_prep`), the prep again when it changed since they
    were given it."""
    out = [sessions._tag(work.key, work.text(), file=work.name)]
    if name == EDITOR and marked:
        out.append(sessions._tag("marked", sessions.marked_slides(marked), file=SCRIPT))
    if gets_prep(name):
        given = kept.setdefault("prep_given", {})
        digest = hashlib.sha256(_read(run.prep).encode("utf-8")).hexdigest()
        if given.get(name) not in (None, digest):
            out.append(sessions._tag("prep", _read(run.prep), file=PREP_MD))
        given[name] = digest
    return "\n\n".join(out)


def gets_prep(name: str) -> bool:
    """A participant whose approved prompt gives it the prep: the context
    and structure owners' material holds it, and the editor's prompt names
    it among what it is given."""
    return "{the prep}" in role_prompts.PROMPTS[name].material or name == EDITOR


def _mine(items: list[dict], name: str) -> list[dict]:
    return [i for i in items if name in i["raised_by"]]


# ── results ───────────────────────────────────────────────────────────────

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


def findings_questions(findings: list[dict]) -> list[dict]:
    """A swarm's findings as the questions its sender answers: each item
    left, who raised it and the fixes suggested."""
    out = []
    for f in findings:
        fixes = "; ".join(f"{x['by']}: {x['fix']}" for x in f.get("fixes") or [])
        out.append({"id": f"left-{f['id']}",
                    "question": f"Not signed off: {f['what']} (raised by "
                                f"{', '.join(f['raised_by'])})"
                                + (f" Suggested: {fixes}" if fixes else "")})
    return out


def _unchecked(work, problems: list[str]) -> list[dict]:
    """What did not check, as the one finding the sender is sent."""
    return [{"id": 1, "what": f"The {work.key} never checked against {work.version}: "
                              + " ".join(problems),
             "raised_by": [PROGRAM], "concerns": [], "fixes": [], "evidence_missing": []}]


# ── the swarm ─────────────────────────────────────────────────────────────

async def fix_passes(run: Run, swarm: sessions.BrainSwarmSession, kept: dict, flow: dict,
                     among, work, *, mark: Callable[[], object], revise: Callable[[list], object],
                     given: Callable[[str], str], clock: Clock, prefix: str = ""
                     ) -> Optional[str]:
    """Fix passes and closes (construction-drafts.md "Cooperative swarm"
    flow 3 and 4, and the deck review the same way), from the stage `flow`
    holds: "waiting" when the sender is asked, "passed", "not passed", or
    "unchecked" when the author's revision never checked. `mark()` marks
    the work again (the script; nothing for the deck); `revise(items)` is
    the author's close, True once what it wrote checks; `given(name)` what
    each owner is given."""
    while True:
        n, stage = flow["pass"], flow["stage"]
        if stage == f"{prefix}pass":
            with clock(f"{prefix}pass-{n}"):
                await owners_turn(swarm, FIXERS, among, work, f"{prefix}pass-{n}",
                                  lambda name: given(name))
            flow["left"] = merged_left(swarm, among)
            flow["stage"] = f"{prefix}asked-after-pass"
            if not flow["left"]:
                return "passed"
            swarm.say(PROGRAM, "What is left, merged from every owner and the editor:\n"
                      + list_text(flow["left"]))
            _keep(run, swarm, kept)
            if _ask_sender(run, swarm, kept, among):
                return "waiting"
        elif stage == f"{prefix}asked-after-pass":
            with clock(f"{prefix}close-{n}"):
                if not await revise(flow["left"]):
                    return "unchecked"
                await mark()
            flow["stage"] = f"{prefix}recheck"
            _keep(run, swarm, kept)
        elif stage == f"{prefix}recheck":
            items = flow["left"]
            with clock(f"{prefix}recheck-{n}"):
                names = [o for o in FIXERS if _mine(items, o)]
                await owners_turn(swarm, names, swarm.not_resolved(among), work,
                                  f"{prefix}recheck-{n}", lambda name: (
                                      "Re-check only your own items of the list, below, against "
                                      "the work as it is now, and sign off.\n"
                                      + list_text(_mine(items, name)) + "\n\n" + given(name)))
            if swarm.resolved(among):
                return "passed"
            flow["stage"] = f"{prefix}asked-after-recheck"
            _keep(run, swarm, kept)
            if _ask_sender(run, swarm, kept, among):
                return "waiting"
        elif stage == f"{prefix}asked-after-recheck":
            if flow["pass"] >= ROUNDS_MAX:
                flow["left"] = merged_left(swarm, among)
                return "not passed"
            flow["pass"] += 1
            flow["stage"] = f"{prefix}pass"
            _keep(run, swarm, kept)
        else:
            raise ValueError(f"no stage {stage!r}")


def swarm(doc: dict) -> Optional[str]:
    """The swarm step of one run (package_jobs.PRODUCERS["swarm"]): WAITING
    when it waits on the sender (questions, or what is left after its fix
    passes)."""
    run = _run(doc, SWARM)
    kept = _kept(run)
    if not kept.get("researched"):
        raise package_jobs.Blocked(SWARM, "The research step did not finish.",
                                   "JARVIS reached the swarm step.", "A new run.")
    pe.wait_indexed(run.place, SWARM, text_only=("/mark/",))  # the sender's answers, indexed as written
    labels = briefing_labels.for_marking()
    knowledge = package_mark.tim_known(run.rows, run.texts)
    flow = kept.setdefault("flow", {"stage": "outline", "pass": 1})
    outline, script = run.outline_file(), run.script_file()
    clock = Clock(kept)
    outcome: dict = {}
    marked: list[dict] = list(kept.get("marked") or [])

    async def go():
        swarm_ = build(run, kept)
        marking = package_mark.MARKING(doc)

        async def mark() -> None:
            with clock("mark"):
                marked[:] = await marking.mark_slides(
                    package_script.units(script.read()), script.text(), labels, knowledge,
                    TIMEOUT)
            kept["marked"] = list(marked)

        def given(name: str) -> str:
            return _work_text(name, run, script, marked, kept)

        async def revise(items: list[dict]) -> bool:
            return await author_writes(swarm_, script, f"close-{flow['pass']}",
                                       close_note(run, items))
        try:
            await swarm_.open()
            _take_answers(run, swarm_, kept)
            while True:
                stage = flow["stage"]
                if stage == "outline":
                    with clock("outline"):
                        if not await author_writes(swarm_, outline, "outline", outline_note(run)):
                            outcome.update(result="not passed",
                                           findings=_unchecked(outline, outline.problems))
                            return
                    flow["stage"] = "outline-owners"
                elif stage == "outline-owners":
                    with clock("outline-owners"):
                        await owners_turn(swarm_, OWNERS, OUTLINE_CONCERNS, outline,
                                          "outline", lambda name: (
                                              "Work on the outline for your concerns.\n"
                                              + _work_text(name, run, outline, [], kept)))
                    flow["stage"] = "outline-settle"
                elif stage == "outline-settle":
                    with clock("outline-settle"):
                        if not await author_writes(swarm_, outline, "outline-settle",
                                                   settle_note(run)):
                            outcome.update(result="not passed",
                                           findings=_unchecked(outline, outline.problems))
                            return
                    flow["stage"] = "outline-signoff"
                elif stage == "outline-signoff":
                    with clock("outline-signoff"):
                        await owners_turn(swarm_, OWNERS, OUTLINE_CONCERNS, outline,
                                          "outline-signoff", lambda name: (
                                              "The author has settled the outline, above. "
                                              "Sign the outline off for your concerns.\n"
                                              + _work_text(name, run, outline, [], kept)))
                    flow["stage"] = "fill"
                    _keep(run, swarm_, kept)
                    if _ask_sender(run, swarm_, kept, OUTLINE_CONCERNS):
                        outcome["waiting"] = True
                        return
                elif stage == "fill":
                    with clock("fill"):
                        if not await author_writes(swarm_, script, "fill", fill_note(run)):
                            outcome.update(result="not passed",
                                           findings=_unchecked(script, script.problems))
                            return
                        await mark()
                    flow["stage"] = "revise"
                elif stage == "revise":
                    with clock("revise"):
                        if not await author_writes(swarm_, script, "revise",
                                                   revise_note(marked)):
                            outcome.update(result="not passed",
                                           findings=_unchecked(script, script.problems))
                            return
                        await mark()
                    flow["stage"] = "pass"
                else:
                    end = await fix_passes(run, swarm_, kept, flow, SCRIPT_CONCERNS, script,
                                           mark=mark, revise=revise, given=given, clock=clock)
                    if end == "waiting":
                        outcome["waiting"] = True
                    elif end == "unchecked":
                        outcome.update(result="not passed",
                                       findings=_unchecked(script, script.problems))
                    else:
                        outcome.update(result=end, findings=flow.get("left") or [])
                    return
                _keep(run, swarm_, kept)
        finally:
            _keep(run, swarm_, kept)
            outcome["swarm"] = swarm_.result()
            await swarm_.close()
    package_jobs.asked(SWARM, "the swarm",
                       "JARVIS ran the swarm: the author, Tim's reader, the context owner, the "
                       "structure and decision owner and the editor.", go())
    if outcome.get("waiting"):
        return package_jobs.WAITING
    result = {**outcome["swarm"], "result": outcome["result"],
              "findings": outcome["findings"] if outcome["result"] != "passed" else [],
              "passes": flow["pass"], "times": kept.get("times") or []}
    _write_result(run, result)
    if result["result"] == "passed":
        used = research_log.mark_used(run.log, package_script.sources(script.read()), roots=(
            run.place.path, str(run.place.folder), str(run.folder)))
        package_jobs.noted(doc["id"], "swarm", {
            "result": "passed", "passes": flow["pass"], "script": str(run.script),
            "outline": str(run.outline), "prep": str(run.prep),
            "conversation": str(run.folder / CONVERSATION),
            "hits_used": sum(len(u["used"]) for u in used["used"])})
        return None
    package_jobs.noted(doc["id"], "swarm", {"result": "not passed", "passes": flow["pass"],
                                            "conversation": str(run.folder / CONVERSATION)})
    package_jobs.failed_swarm(doc["id"], findings_questions(result["findings"]),
                              result["findings"])
    return package_jobs.WAITING
