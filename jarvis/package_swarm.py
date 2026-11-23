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
             its approved prompt and its material (working_set.py,
             construction-drafts.md "The author works from a prioritized
             working set and calls back for more", Tim 2026-11-21): the
             sender's two answers, the handoff's questions, a table of
             contents of the evidence and the searches' short hits, each
             with Tim's knowledge state, and every evidence file whole with
             its marks short and in place while that fits the model's
             window (never over half of it), else for each question the
             sentences marking prioritized for it, highest first, lowest
             priority cut first, saying what was left out. In ONE turn at medium effort
             it writes `research.md` and `prep.md` together; when it needs
             more it calls back during that turn through its own tool
             server (its file tools, knowledge_search under the evidence,
             the research tools and search_at_once, several searches at
             once). What was looked up is `research-searches.md`, and
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
access to, kept apart; everyone else everything, each item marked. A share
that would not fit the participant's window whole is given as the working
set of that share instead, as the author's is, read further with its tools.

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

Every run goes through the step cache (step_cache.py, construction-drafts.md
"Step cache keyed by inputs", Tim 2026-11-22; the cache is one
package's, so only another run of the same package reuses a stage).
Research is one unit, keyed by the handoff, the package's evidence, what
the mark step read in it, and the author's role, prompt, model and the
code (`research_unit`); each stage of the swarm is one unit
(`swarm-outline`, `swarm-fill`, `swarm-passes`), keyed by the output of
the unit before it, the sender's answers to an earlier run's findings on
that stage, and what it is made with (`tool_of`: every participant's
role, prompt and model, the concerns, the labels, the embedding model and
the code). A run reuses each stage whose key is kept (its files and
swarm.json copied into its folder; every participant, on either brain,
starts a new session given the whole conversation as the stage left it,
so no session carries what was said after it, and no two runs resume one
thread) and runs the first whose inputs changed and every one after it. A
finished stage is kept; a stage that failed or waits on the sender keeps
nothing until it finishes.

A revision (the sender said what is wrong at its check, or Tim rejected the
package; package_jobs.new_run's inputs) is one more stage,
`swarm-revision`, after the passed script: the words said into the
conversation as the sender's or Tim's, the author revises the script with
them, naming the slides it changes (`changed`), and researches again only
where the words say something is missing, with its tools in that turn;
every slide it did not name is put back exactly as it was (`_put_back`);
only the slides that changed are marked again, and the owners and the
editor re-check only those (`flow["slides"]`). The deck step then makes
again only the deck slides whose script slide changed (package_deck.py).
The job records what was redone (`revision`: the slides changed and kept,
the slides the author named and put back, whether research was done again
and its searches).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import (briefing_labels, data_paths, model_tiers, package_evidence as pe,
                    package_jobs, package_mark, package_script, pages, records, research_log,
                    role_prompts, roles, run_report, script_visuals, sessions, step_cache,
                    work_research, working_set)

RESEARCH, SWARM = "research", "swarm"
RESEARCH_MD, PREP_MD = "research.md", "prep.md"
SCRIPT = package_script.FILE            # script.yaml, script/1
OUTLINE = package_script.OUTLINE_FILE   # outline.yaml, outline/1
STATE = "swarm.json"
RESULT = "swarm-result.json"
CONVERSATION = "conversation.md"
PROGRAM = sessions.PROGRAM
AUTHOR, EDITOR = "author", "editor"
# Who said what is wrong in a revision, as the conversation names them.
SENDER, TIM = "sender", "tim"
# The author's approved prompt's placeholder for its material
# (role_prompts.PROMPTS["author"].material).
AUTHOR_MATERIAL = ("{the evidence: every file whole with its marks when it fits, else the "
                   "working set; the table of contents and the search hits}")
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
    questions word for word, the structure owner's "Does each slide show
    its point?" (the visual plan, construction-drafts.md "The script
    describes each slide's visual"), and the editor's language."""
    cares, = _asked_in(READER)
    background, = _asked_in(CONTEXT)
    order, decision, shows = _asked_in(STRUCTURE)
    return [sessions.Concern("cares", cares, READER),
            sessions.Concern("background", background, CONTEXT),
            sessions.Concern("order", order, STRUCTURE),
            sessions.Concern("decision_ready", decision, STRUCTURE),
            sessions.Concern("shows_point", shows, STRUCTURE),
            sessions.Concern("language", _language(), EDITOR)]


OUTLINE_CONCERNS = ("cares", "background", "order", "decision_ready", "shows_point")
SCRIPT_CONCERNS = OUTLINE_CONCERNS + ("language",)


# ── the run ───────────────────────────────────────────────────────────────

@dataclass
class Run:
    doc: dict
    place: pe.Place
    rows: list[pe.Row]
    marked: dict
    texts: dict[str, str] = field(default_factory=dict)
    # The program's research searches (`first_searches`), given to the
    # author short in its working set, and the words of the turn its first
    # message goes with (`research_note`), counted in its window.
    looked: list[dict] = field(default_factory=list)
    turn: str = ""

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

    def script_file(self, deck: Optional[Path] = None) -> package_script.ScriptFile:
        """The script in its structured form, checked against the handoff's
        questions, its visuals' numbers against their sources and its
        slides against the outline's visual plan; at the deck (`deck`, the
        deck source), each decision slide with the author's part of it."""
        return package_script.ScriptFile(self.script, question_ids(self.doc),
                                         sources=self.sources(), outline=self.outline,
                                         deck=deck)

    def sources(self) -> script_visuals.Sources:
        """Where a value's source path is read by the program: the evidence
        (its engine path or its folder), the run's folder, and a Work item's
        or package's own files."""
        return script_visuals.Sources(
            mapped=((self.place.path, self.place.folder),
                    ("/work", data_paths.work_dir()), ("/packages", data_paths.package_dir())),
            folders=(self.place.folder, self.folder))

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
    # What the gather step made: what was written after it (the sender's
    # answers, his Reject words) reaches the swarm as what it is, said into
    # the conversation, never as material (step_cache.py: every stage's
    # inputs are the evidence as gathered).
    rows = pe.gathered_rows(pe.read_manifest(place))
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


def _tools_note(run: "Run", name: str) -> str:
    """How a participant reads what its working set leaves out."""
    tools = set(roles.role(name).tools)
    if not tools:
        return "you have no tools; work from what is here."
    said = []
    if tools & set(roles.FILE_READ_TOOLS):
        said.append(f"read_package_file and search_package_files in {run.place.folder}")
    if "knowledge_search" in tools:
        said.append(f"knowledge_search under {run.place.path}")
    if tools & set(roles.BATCH_SEARCH):
        said.append("search_at_once to run several searches at once")
    if tools & set(roles.RESEARCH_TOOLS) - {"knowledge_search"}:
        said.append("your research tools")
    return ", ".join(said) + "."


def material_set(run: Run, name: str, room_tokens: int,
                 states: Optional[tuple[str, ...]] = None) -> working_set.WorkingSet:
    """A participant's material of the evidence (working_set.build), its
    share (`states`) of it, in `room_tokens`: every file whole with its
    marks in place when that fits, else the working set. Only the
    handoff's own questions rank a sentence (never classification's
    background)."""
    return working_set.build(run.rows, run.texts, run.marked, question_ids(run.doc),
                             run.looked if name == AUTHOR else [], room_tokens,
                             reads=_tools_note(run, name), states=states)


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
    # The engine and model each participant runs on in this run, spoken
    # yet or not: a later process starts one not yet spoken on the same
    # model (build), and every stage's key names it (_had).
    kept["models"] = {**(kept.get("models") or {}),
                      **{name: {"engine": chat.pick.engine, "model": chat.model}
                         for name, chat in swarm.participants.items()}}
    kept["swarm"] = swarm.saved()
    _save(run, kept)


def _had(kept: Optional[dict]) -> dict[str, dict]:
    """The engine and model each participant runs on in this run, by name:
    the session it resumes (`participants`), else the model it was started
    on in an earlier process of the run (`models`); a participant named in
    neither is picked when its swarm is built."""
    kept = kept or {}
    return {**(kept.get("models") or {}), **(kept.get("participants") or {})}


def _still_running(doc: dict, was: Optional[dict]) -> bool:
    """Whether a model a run was started on is still the one it runs on: a
    model the package's runs found refused since is picked again."""
    if not (was or {}).get("model"):
        return False
    return was["model"] not in (step_cache.refused(doc).get(was.get("engine")) or [])


def marking_pin(doc: dict, kept: dict) -> dict:
    """The engine and model the script is marked on in this run: the one an
    earlier process of the run marked it on (`models`), else the pick now,
    kept for the run's later processes; as package_mark.MARKING takes it."""
    models = kept.setdefault("models", {})
    if not _still_running(doc, models.get("marking")):
        tool = step_cache.role_tool("marking", doc)
        if tool.get("model"):
            models["marking"] = {"engine": tool["engine"], "model": tool["model"]}
    pin = models.get("marking") or {}
    return {"engine": pin["engine"], "model": pin["model"]} if pin else {}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class _Firsts:
    """Each participant's first message, made when its first turn comes:
    its approved prompt's closing material with its share, and where the
    evidence is for one that searches it. Its share of the evidence is
    every file whole with its marks in place while that fits its window
    with what goes with it, else the working set of that share
    (working_set.build; construction-drafts.md "The author works from a
    prioritized working set and calls back for more")."""

    def __init__(self, run: Run, material: list[sessions.Known]):
        self.run = run
        self.material = material
        self.participants: dict[str, sessions.ChatSession] = {}
        self.swarm: Optional[sessions.BrainSwarmSession] = None
        self.sizes: dict[str, dict] = {}        # what each was given, for the run's log

    def _room(self, name: str) -> int:
        """The tokens its first message may take: the room its window
        leaves (working_set.room), less its system prompt, the brain's
        starting context and what goes with the first message in its turn."""
        chat = self.participants.get(name)
        pick = getattr(chat, "pick", None)
        if pick is None:
            return 1 << 62
        given = (pages.tokens(getattr(chat, "prompt", "") or system_of(name))
                 + pages.starting_context(pick.engine))
        if name == AUTHOR:
            given += pages.tokens(self.run.turn)
        else:
            given += pages.tokens(_read(self.run.outline)) + 2 * pages.tokens(_read(self.run.script))
            if self.swarm is not None:
                given += sum(pages.tokens(m["text"]) for m in self.swarm.conversation
                             if m["from"] != name)
        return working_set.room(pages.budget(pick, name)) - given

    def _fill(self, name: str, values: dict[str, str]) -> str:
        said = material_of(name, values)
        if name == EDITOR:
            # The editor's approved prompt closes on no material: it is
            # given its share of the evidence, each item marked, and the
            # prep it names ("You are given the script, ..., the prep"),
            # here.
            said = "\n".join([values["{the evidence, each item with its knowledge state}"],
                              values["{the prep}"]])
        if roles.role(name).release.tools:
            said = f"{said}\n\n{evidence_note(self.run)}" if said else evidence_note(self.run)
        return said

    def __call__(self, name: str) -> str:
        run = self.run
        prep = sessions._tag("prep", _read(run.prep), file=PREP_MD)
        if name == AUTHOR:
            values = {"{sender_answers: plainly, why_now}": _sender_answers(run.doc),
                      "{the handoff's questions, each with the sender's options where it "
                      "gives them}": _handoff_questions(run.doc), "{the prep}": prep}
            key = AUTHOR_MATERIAL
            fixed = pages.tokens(self._fill(name, {**values, key: ""}))
            ws = material_set(run, name, self._room(name) - fixed)
            self.sizes[name] = self._size(ws)
            return self._fill(name, {**values, key: ws.text})
        # Its share's placeholders (sessions.share_values gives their names),
        # each filled with its share whole and marked when that fits, else
        # the working set of it (Tim's reader: knows and had access, each its
        # own, kept apart as its share is).
        values = {**sessions.share_values(name, []), "{the prep}": prep}
        values.setdefault("{the evidence, each item with its knowledge state}", "")
        keys = [k for k in ("{knows}", "{had_access}",
                            "{the evidence, each item with its knowledge state}",
                            "{the gathered and marked material, with its knowledge state}")
                if k in values]
        left = self._room(name) - pages.tokens(self._fill(name, {**values,
                                                                  **{k: "" for k in keys}}))
        if sessions.PROBABLY_NOT not in sessions.SHARES[name]:
            knows = material_set(run, name, left, (sessions.KNOWS,))
            access = material_set(run, name, left, (sessions.HAD_ACCESS,))
            if not (knows.whole and access.whole and knows.tokens + access.tokens <= left):
                knows = material_set(run, name, left // 2, (sessions.KNOWS,))
                access = material_set(run, name, left - left // 2, (sessions.HAD_ACCESS,))
            self.sizes[name] = {"working_set": not (knows.whole and access.whole),
                                "tokens": knows.tokens + access.tokens}
            return self._fill(name, {**values, "{knows}": knows.text,
                                     "{had_access}": access.text})
        ws = material_set(run, name, left, sessions.SHARES[name])
        self.sizes[name] = self._size(ws)
        return self._fill(name, {**values, **{k: ws.text for k in keys}})

    @staticmethod
    def _size(ws: working_set.WorkingSet) -> dict:
        return {"working_set": not ws.whole, "tokens": ws.tokens, "given": ws.given,
                "total": ws.total}


def build(run: Run, kept: dict, more: tuple[sessions.Concern, ...] = (),
          effort: Optional[dict[str, str]] = None) -> sessions.BrainSwarmSession:
    """The swarm: each participant on its role, its model picked (the
    author first, the owners then on the other brain from it when Settings
    says so), or resumed on the session it had, or, not spoken yet,
    started on the model an earlier process of the run started it on;
    `more`, the concerns the deck review adds (package_deck); `effort`, a
    participant's effort for this launch in place of its role's (the
    author's research turn). Raises NoModel."""
    material = evidence(run)
    participants: dict[str, sessions.ChatSession] = {}
    author_engine = None
    for name in NAMES:
        role = roles.role(name)
        had = (kept.get("participants") or {}).get(name) or {}
        common = dict(reads=run.reads(name), search_log=run.log, on_log=_log, shown=WORKING)
        if name in (effort or {}):
            common["role_spec"] = dataclasses.replace(role, effort=effort[name])
        if had.get("session"):
            pick = model_tiers.Pick(had["engine"], had["model"], role.tier, "resumed",
                                    [had["model"]])
            chat = CHAT(name, system_of(name), run.folder, pick=pick, resume=had["session"],
                        **common)
        elif _still_running(run.doc, (kept.get("models") or {}).get(name)):
            was = kept["models"][name]
            pick = model_tiers.Pick(was["engine"], was["model"], role.tier, "kept",
                                    [was["model"]])
            chat = CHAT(name, system_of(name), run.folder, pick=pick, **common)
        else:
            pick = PICK(role.kind, role.tier, author_engine=author_engine,
                        **package_mark.pick_args(run.doc))
            chat = CHAT(name, system_of(name), run.folder, pick=pick, **common)
        if name == AUTHOR:
            author_engine = pick.engine
        participants[name] = chat
    firsts = _Firsts(run, material)
    firsts.participants = participants
    swarm = sessions.BrainSwarmSession(participants, concerns() + list(more), author=AUTHOR,
                                       material=material, first=firsts)
    firsts.swarm = swarm
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

# What the program looked up, given to the author and kept beside the run.
SEARCHES_MD = "research-searches.md"
# The author's research turn (construction-drafts.md "Research and prep
# reorganized", Tim 2026-11-21): medium effort, for that turn alone.
RESEARCH_EFFORT = "medium"
# The program's searches, in the research log, as the program's own.
SEARCHER = PROGRAM
# What a participant's file tools see in the run folder (role_files.py):
# the package's working files and the deck's source (package_deck.FOLDER),
# never the run's bookkeeping (timing.json, quality.json, swarm.json,
# run.job, job.log, research-log.jsonl, research-used.json).
WORKING = (RESEARCH_MD, PREP_MD, OUTLINE, SCRIPT, SEARCHES_MD, "deck")

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


def _write_searched(run: Run, looked: list[dict], heading: str) -> None:
    """What was looked up, written beside the run whole (SEARCHES_MD); the
    author is given it short in its working set."""
    with open(run.folder / SEARCHES_MD, "w", encoding="utf-8") as out:
        out.write(work_research.markdown(heading, looked) + "\n")


def research_note(run: Run) -> str:
    """The program's words to the author for its research: what JARVIS
    looked up for it, where what it starts from is, how it reads more, and
    where research.md and prep.md go (construction-drafts.md "Research and
    prep reorganized" and "The author works from a prioritized working set
    and calls back for more"; workflow.yaml research start_from_work_item
    and output)."""
    lines = [f"JARVIS ran every research search at once for each of your questions; their "
             f"hits are in your working set, short, and whole in {run.folder / SEARCHES_MD}."]
    if run.work_research is not None and run.work_research.is_dir():
        lines.append(f"The Work item's research is in {run.work_research}: what was looked up "
                     f"for it, each query with its hits ({work_research.QUERIES_MD}), and the "
                     f"summaries of it ({work_research.SUMMARIES}) where they are written.")
    elif run.doc.get("work"):
        lines.append(f"The Work item {run.doc['work']} has no research written yet.")
    lines.append(f"The marked evidence is in {run.place.folder / package_mark.MARK} "
                 f"({package_mark.INDEX} names each file's marks).")
    lines.append(evidence_note(run))
    lines.append("When a question needs more than your working set holds, get it now, in "
                 "this turn, with your tools: " + _tools_note(run, AUTHOR))
    lines.append(f"Write your research in {run.research}: each finding with its source path, "
                 f"for each question. Write the prep in {run.prep}. Write both in this turn.")
    return "\n".join(lines)


# The code that shapes the research and every stage's output: this module,
# the script's form and marking, the research log and the Work item's
# research (step_cache.code).
SWARM_CODE = ("jarvis.package_swarm", "jarvis.package_script", "jarvis.script_visuals",
              "jarvis.research_log",
              "jarvis.work_research", "jarvis.package_mark", "jarvis.package_evidence")


def tool_of(doc: dict, names: tuple[str, ...] = NAMES, more: tuple = (),
            kept: Optional[dict] = None) -> dict:
    """What a stage is made with, as its key names it: each participant's
    role (its approved prompt, tier, effort and release, and the engine and
    model that runs it, step_cache.role_tool: the model `kept` runs it on
    in this run, _had), the concerns signed off
    (every one's id, question and owner, and `more`, the deck's), the
    labels the script is marked with, whole, the embedding model that
    groups what is asked and left by meaning, and the code."""
    return {"roles": step_cache.roles_tool(names, doc, had=_had(kept)),
            "concerns": [dataclasses.asdict(c) for c in list(concerns()) + list(more)],
            "labels": step_cache.labels_key(),
            "marking": step_cache.role_tool("marking", doc, had=_had(kept).get("marking")),
            "embedding": step_cache.embedding_key(),
            "code": step_cache.code(*step_cache.MODEL_CODE, *SWARM_CODE)}


def research_unit(run: Run, kept: Optional[dict] = None) -> step_cache.Unit:
    """The research step's unit (step_cache.py, one package's): its inputs
    are the run's own, the handoff (its questions, the sender's answers and
    options, its evidence as named), the package's evidence (each file's
    SHA-256: the sender's answers, his words at the check and Tim's Reject
    join it), what the mark step read in it, and the author's role,
    prompt, model and code. Within the package its research is kept and
    reused unless one of these changes (Tim, 2026-11-22): what the
    program's searches and the Work item's research folder find now is
    not a reason to research again. An author `kept` runs on in this run
    (_had) is named by that model."""
    had = _had(kept).get(AUTHOR)
    return step_cache.Unit(RESEARCH, "", {
        "request": run.doc.get("request"), "work": run.doc.get("work"),
        "evidence": sorted([r.stored, r.sha256] for r in run.rows if r.stored),
        "marks": package_mark.marks_digest(run.place),
        "tool": {"author": step_cache.role_tool(AUTHOR, run.doc, had=had),
                 "code": step_cache.code(*step_cache.MODEL_CODE, *SWARM_CODE)}})


def research(doc: dict) -> None:
    """The research step of one run (package_jobs.PRODUCERS["research"]):
    reused from the step cache when its inputs are kept; else the program's
    searches, all at once, then the author's one turn, from its working
    set, writing the research and the prep at medium effort and calling
    back through its tools for anything more it needs."""
    run = _run(doc, RESEARCH)
    kept = _kept(run)
    unit = research_unit(run, kept)
    if _in_place(kept, unit) or adopt(run, unit) is not None:
        return
    began = time.time()
    clock = Clock(kept)
    search = SEARCH()
    with clock("research-searches"):
        run.looked = first_searches(run, search)
        _write_searched(run, run.looked, "What JARVIS looked up for the handoff's questions")
    run.turn = research_note(run)

    async def go():
        swarm = build(run, kept, effort={AUTHOR: RESEARCH_EFFORT})
        author = swarm.participants[AUTHOR]
        try:
            await author.open()
            with clock(RESEARCH):
                await swarm.answer(AUTHOR, TIMEOUT, run.turn, TALK)
            given = (getattr(swarm._first, "sizes", None) or {}).get(AUTHOR)
            if given:
                kept.setdefault("given", {})[AUTHOR] = given
                what = "a working set" if given["working_set"] else "the evidence whole"
                _log(f"research: the author's material, {what}, {given['tokens']:,} tokens: "
                     f"{given['given']} of {given['total']}")
        finally:
            _keep(run, swarm, kept)
            await author.close()
    package_jobs.asked(RESEARCH, "the author",
                       "JARVIS asked the author to write the research and the prep from its "
                       "working set and what its searches found.", go())
    missing = [p.name for p in (run.research, run.prep) if not p.is_file()]
    if missing:
        raise package_jobs.Blocked(RESEARCH, f"The author wrote no {' and no '.join(missing)}.",
                                   "JARVIS gave the author its working set and what its "
                                   "searches found, and asked for its research, each finding "
                                   "with its source path, for each question, and the prep.",
                                   "Read the run's conversation and log for why; a new run.")
    kept["researched"] = True
    # Their words in the engine now; their meaning vectors follow in the
    # background, and nothing waits on them.
    with run_report.wait("indexing", "research: research.md and prep.md, words only"):
        pe.index_files([run.research, run.prep, run.folder / SEARCHES_MD])
    finish_unit(run, kept, unit, began, RESEARCH_FILES)


# ── the stages kept (step_cache.py) ───────────────────────────────────────

# What the research step keeps, and what each swarm stage keeps: the run's
# files as the stage leaves them, and swarm.json (the conversation, each
# participant's session, the sign-offs, what is asked).
RESEARCH_FILES = (RESEARCH_MD, PREP_MD, research_log.NAME, SEARCHES_MD, STATE)
STAGE_FILES = (OUTLINE, SCRIPT, STATE, research_log.NAME, RESULT, CONVERSATION)


def _record(unit: step_cache.Unit, entry: step_cache.Entry) -> dict:
    return {"kind": unit.kind, "name": unit.name, "key": unit.key, "digest": entry.digest}


def _in_place(kept: dict, unit: step_cache.Unit) -> Optional[dict]:
    """The unit as done in this run's folder (swarm.json `units`), or None."""
    return next((u for u in kept.get("units") or [] if u["key"] == unit.key), None)


def digest_of(kept: dict, kind: str, name: str = "") -> Optional[str]:
    """The output digest of the last unit of `kind` done in this folder."""
    found = [u for u in kept.get("units") or [] if u["kind"] == kind and u["name"] == name]
    return found[-1]["digest"] if found else None


def adopt(run: Run, unit: step_cache.Unit) -> Optional[dict]:
    """The unit's kept output restored into this run's folder (step_cache
    reuse, from another run of the same package), its swarm.json made this
    run's, and the unit added to what is done here; swarm.json as it now stands, or None when nothing is kept
    under the unit's key."""
    entry = step_cache.reuse(unit, run.doc, run.folder)
    if entry is None:
        return None
    kept = _kept(run)
    if entry.made.get("folder") != str(run.folder):
        _fresh_sessions(kept)
    kept.setdefault("units", []).append(_record(unit, entry))
    kept.pop("pending", None)
    _save(run, kept)
    return kept


def finish_unit(run: Run, kept: dict, unit: step_cache.Unit, began: float,
                files: tuple[str, ...]) -> step_cache.Entry:
    """The unit computed: swarm.json saved, its files kept under its key,
    and the unit added to what is done here."""
    kept.pop("pending", None)
    _save(run, kept)
    entry = step_cache.computed(unit, run.doc, began, folder=run.folder, files=files)
    kept.setdefault("units", []).append(_record(unit, entry))
    _save(run, kept)
    return entry


def _fresh_sessions(kept: dict) -> None:
    """swarm.json kept by another run, made this run's: no round of this
    run's job is answered yet, and every participant, on either brain,
    starts a new session given the whole conversation as the stage left
    it. A session kept by another run holds what was said after the stage
    it was kept at (the run before's later stages), which no key covers,
    and two runs must never resume one thread."""
    asking = kept.get("asking") or {}
    kept["asking"] = {"answers_said": 0, "groups": asking.get("groups") or []}
    saved = kept.setdefault("swarm", {})
    seen = saved.setdefault("seen", {})
    for name in list(kept.get("participants") or {}):
        del kept["participants"][name]
    kept.pop("models", None)
    for name in NAMES:
        seen[name] = 0
    saved["introduced"] = []


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
        with run_report.wait("embedding", what, texts=len(texts)):
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


CHANGED = {"type": "array", "items": {"type": "string"},
           "description": "The id of each slide you change, add or remove."}


async def author_writes(swarm: sessions.BrainSwarmSession, work, stage: str, add: str,
                        named: Optional[list] = None) -> bool:
    """The author's turn writing `work` (the outline, the script): what it
    answers is checked by the program and written; one that does not check
    is said back with every problem and asked again, at most ROUNDS_MAX
    times. False when it never checked. With `named` (a revision), the
    author also names the slides it changes, added to `named`."""
    form = author_form(work)
    if named is not None:
        form = {**form, "properties": {**form["properties"], "changed": CHANGED},
                "required": [*form.get("required", []), "changed"]}
    for attempt in range(ROUNDS_MAX + 1):
        answer = await swarm.answer(swarm.author, TIMEOUT, add if attempt == 0 else "", form)
        swarm.ask(swarm.author, answer, stage)
        problems = work.take(answer)
        if not problems:
            if named is not None:
                named.extend(str(i) for i in answer.get("changed") or [] if isinstance(i, str))
            forms_changed(swarm, getattr(work, "form_changes", None) or [], stage)
            return True
        swarm.say(PROGRAM, f"The {work.key} in your answer does not check against "
                           f"{work.version}. Answer the whole {work.key} again, in its form:\n"
                  + "\n".join(f"- {p}" for p in problems))
    return False


def forms_changed(swarm: sessions.BrainSwarmSession, changes: list[dict], stage: str) -> None:
    """A slide's visual form changed after the outline was signed off (the
    author's `forms`, package_script.ScriptFile): JARVIS has changed the
    outline; said into the conversation, and the structure owner's "Does
    each slide show its point?" is open again until it signs off the new
    form."""
    if not changes:
        return
    swarm.say(PROGRAM, "The author changed the visual form the outline names, and JARVIS "
                       "changed the outline:\n"
              + "\n".join(f"- slide {c['slide']}: {c['was']} is now {c['visual']}, because "
                           f"{c['why']}" for c in changes)
              + "\nThe structure and decision owner signs off shows_point on the new form.")
    if "shows_point" in swarm.concerns:
        swarm.signoffs.append(sessions.SignOff(
            "shows_point", swarm.concerns["shows_point"].owner, sessions.NOT_YET,
            "", [form_left(changes)], stage))


def form_left(changes: list[dict]) -> dict:
    """What is left once a form changed: its owner signs off the new form."""
    return {"what": "Sign off shows_point on the new visual form of slide(s) "
                    + ", ".join(f"{c['slide']} ({c['visual']}: {c['why']})" for c in changes),
            "fix": "", "evidence_missing": ""}


def form_item(items: list[dict], changes: list[dict]) -> None:
    """The structure owner's item for a form changed in a close, added to
    the list its recheck goes through."""
    if changes:
        items.append({"id": max([int(i.get("id") or 0) for i in items] or [0]) + 1,
                      "what": form_left(changes)["what"], "raised_by": [STRUCTURE],
                      "concerns": ["shows_point"], "fixes": [], "evidence_missing": []})


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
            f"whole script: JARVIS checks it and writes it to {run.script}. "
            f"{role_prompts.AUTHOR_VISUALS}\n"
            + sessions._tag("outline", _read(run.outline), file=OUTLINE))


def revise_note(marked: list[dict]) -> str:
    return (f"{role_prompts.AUTHOR_REVISE}\n"
            + sessions._tag("marked", sessions.marked_slides(marked), file=SCRIPT)
            + "\nGive the whole script again in your answer, revised.")


def close_note(run: Run, items: list[dict]) -> str:
    """The author's words for the close: the list is in the conversation;
    where an item names missing evidence, the author researches it."""
    out = ["Work through the list above, item by item: change the script or say why not. "
           "Give the whole script in your answer. Where a slide's visual form should change "
           "from what the outline names, name it in `forms` with why."]
    if any(item["evidence_missing"] for item in items):
        out.append(f"Some items name evidence that is not in the material: research it with "
                   f"your tools, add what you find to {run.research}, each finding with its "
                   f"source path, and use it.")
    return "\n".join(out)


def _who(rev: dict) -> str:
    return "the sender" if rev.get("by") == SENDER else "Tim"


def revision_note(run: Run, rev: dict) -> str:
    """The author's words for a revision (a change at the sender's check,
    or Tim's Reject): what is wrong is above, as said; only the slides the
    words touch change."""
    return (f"Revise the script for what {_who(rev)} said is wrong, above. Give the whole "
            f"script in your answer, as `script` in the schema given with this call "
            f"({package_script.VERSION}), changed only where those words need it, and name in "
            f"`changed` the id of each slide you change, add or remove: every slide you do not "
            f"name is kept exactly as it was (JARVIS puts it back). Your research and the prep "
            f"stand: research again only where the words say something is missing, now, in "
            f"this turn, with " + _tools_note(run, AUTHOR).rstrip(".")
            + f", adding what you find to {run.research}, each finding with its source path.\n"
            + sessions._tag("outline", _read(run.outline), file=OUTLINE) + "\n"
            + sessions._tag("script", _read(run.script), file=SCRIPT))


def revision_given(rev: dict, slides: Optional[list[str]]) -> str:
    """What an owner is told in a revision's fix passes: only the slides
    that changed are re-checked."""
    if not rev or not slides:
        return ""
    return (f"This revision changes the script with what {_who(rev)} said. Re-check only the "
            f"slides that changed: {', '.join(slides)}. Every other slide was signed off before "
            f"and stays as it is.\n")


def _research_mark(run: Run) -> dict:
    """Where the research stood: research.md's digest and how many searches
    the run's search log holds."""
    return {"sha256": hashlib.sha256(_read(run.research).encode("utf-8")).hexdigest(),
            "searches": len(research_log.read(run.log)) if run.log.is_file() else 0}


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


# The swarm's stages, each a unit of the step cache (step_cache.py), in
# order; then one revision stage a revision the run is asked (a change at
# the sender's check, or Tim's Reject), and one unit a slide of the script.
OUTLINE_STAGE, FILL_STAGE, PASSES_STAGE = "swarm-outline", "swarm-fill", "swarm-passes"
STAGES = (OUTLINE_STAGE, FILL_STAGE, PASSES_STAGE)
REVISION_STAGE = "swarm-revision"
# The flow's steps within the outline stage and the fill stage.
OUTLINE_STEPS = ("outline", "outline-owners", "outline-settle", "outline-signoff")
FILL_STEPS = ("fill", "revise")
FIRST_STEP = {OUTLINE_STAGE: "outline", FILL_STAGE: "fill", PASSES_STAGE: "pass",
              REVISION_STAGE: "revision-write"}


def findings_for(doc: dict, label: str) -> list[dict]:
    """The sender's answers to the findings of earlier runs whose `label`
    stage failed (package_jobs.new_run): that stage's inputs."""
    return [{"run": f.get("run"), "questions": f.get("questions") or [],
             "answers": f.get("answers") or {}}
            for f in (doc.get("inputs") or {}).get("findings") or [] if f.get("stage") == label]


def revisions_of(doc: dict) -> list[dict]:
    """The revisions the run is asked, in order: {by, words, run}."""
    return list((doc.get("inputs") or {}).get("revisions") or [])


def words_said(words: str, by: str) -> str:
    """The words of what is wrong as they are said into the conversation:
    the sender's as its answer to its check, word for word; Tim's as they
    are."""
    if by == SENDER:
        return sessions._tag("answer", words, id=package_jobs.WHAT_IS_WRONG,
                             question=package_jobs.SENDER_CHECK)
    return words


def findings_said(findings: list[dict]) -> str:
    """Earlier runs' findings and the sender's answers to them, word for
    word, each in its own element, as _take_answers says answers."""
    out = []
    for f in findings:
        asked = {q["id"]: q.get("question", "") for q in f["questions"]}
        out += [sessions._tag("answer", v, id=k, question=asked.get(k, ""))
                for k, v in f["answers"].items()]
    return "\n".join(out)


def stage_specs(doc: dict) -> list[tuple[str, str, dict]]:
    """Each stage the swarm step runs: (kind, name, its own inputs)."""
    out = [(kind, "", {}) for kind in STAGES]
    for n, rev in enumerate(revisions_of(doc), 1):
        out.append((REVISION_STAGE, str(n), {"words": rev.get("words") or "",
                                              "by": rev.get("by") or ""}))
    return out


def stage_unit(doc: dict, kind: str, name: str, more: dict, before: str,
               tool: Optional[dict] = None) -> step_cache.Unit:
    """A stage's unit: the output it goes on from (`before`, the digest of
    the research or the stage before), its own inputs, the answers to any
    earlier run's findings on it, and what it is made with (`tool_of`)."""
    label = f"{kind}:{name}" if name else kind
    return step_cache.Unit(kind, name, {"before": before, **more,
                                        "findings": findings_for(doc, label),
                                        "tool": tool if tool is not None else tool_of(doc)})


def _put_back(run: Run, base: dict, named: list[str]) -> tuple[list[str], list[str]]:
    """After a revision's turn: every slide the author did not name is put
    back exactly as it was (its inputs did not change, so it is never made
    again); a slide added or removed counts as named. (The slides that
    changed, those put back.)"""
    now = package_script.read(run.script) or {}
    was = {s.get("id"): s for s in base.get("slides") or []}
    have = set(package_script.slide_ids(now))
    named_ = set(named) | (have - set(was)) | (set(was) - have)
    back = []
    slides = []
    for slide in now.get("slides") or []:
        sid = slide.get("id")
        if sid in was and sid not in named_ and slide != was[sid]:
            back.append(sid)
            slides.append(was[sid])
        else:
            slides.append(slide)
    if back:
        package_script.write(run.script, {**now, "slides": slides})
    return package_script.changed_slides(base, package_script.read(run.script)), back


def swarm(doc: dict) -> Optional[str]:
    """The swarm step of one run (package_jobs.PRODUCERS["swarm"]): each
    stage reused from the step cache when its inputs are kept, the first
    whose inputs changed and every one after it run; WAITING when it waits
    on the sender (questions, or what is left after its fix passes)."""
    run = _run(doc, SWARM)
    kept = _kept(run)
    research_digest = digest_of(kept, RESEARCH)
    if research_digest is None:
        raise package_jobs.Blocked(SWARM, "The research step did not finish.",
                                   "JARVIS reached the swarm step.", "A new run.")
    specs = stage_specs(doc)
    tool = tool_of(doc, kept=kept)
    before, todo = research_digest, None
    for i, (kind, name, more) in enumerate(specs):
        unit = stage_unit(doc, kind, name, more, before, tool)
        if kept.get("pending") == unit.key:
            todo = i
            break
        record = _in_place(kept, unit)
        if record is None:
            adopted = adopt(run, unit)
            if adopted is None:
                todo = i
                break
            kept, record = adopted, _in_place(adopted, unit)
            # Adopted: every participant starts a session of its own now,
            # on the model picked for it.
            tool = tool_of(doc, kept=kept)
        before = record["digest"]
    if todo is None:
        return _swarm_passed(run, kept)
    # The evidence's vectors were waited for when it was gathered; what was
    # written since (the sender's answers) waits only until its words are
    # read, its meaning following in the background.
    pe.wait_indexed(run.place, SWARM, vectors=False)
    labels = briefing_labels.for_marking()
    knowledge = package_mark.tim_known(run.rows, run.texts)
    flow = kept.setdefault("flow", {"stage": "outline", "pass": 1})
    outline, script = run.outline_file(), run.script_file()
    clock = Clock(kept)
    outcome: dict = {}
    marked: list[dict] = list(kept.get("marked") or [])

    async def go():
        nonlocal flow, before
        swarm_ = build(run, kept)
        marking = package_mark.MARKING(doc, **marking_pin(doc, kept))
        base: dict = {}

        async def mark() -> None:
            """The script marked; in a revision, only the slides that differ
            from the script before it, the others' marks kept."""
            now = script.read()
            units = package_script.units(now)
            keep: list[dict] = []
            if flow.get("revision_base") is not None:
                changed = set(package_script.changed_slides(flow["revision_base"], now))
                present = set(package_script.slide_ids(now))
                units = [u for u in units if u.slide in changed]
                keep = [m for m in marked if str(m.get("id", "")).split("/")[0] in present
                        and str(m.get("id", "")).split("/")[0] not in changed]
            with clock("mark"):
                fresh = await marking.mark_slides(units, script.text(), labels, knowledge,
                                                  TIMEOUT) if units else []
            marked[:] = keep + list(fresh)
            kept["marked"] = list(marked)

        def given(name: str) -> str:
            return (revision_given(kept.get("revision") or {}, flow.get("slides"))
                    + _work_text(name, run, script, marked, kept))

        async def revise(items: list[dict]) -> bool:
            done = await author_writes(swarm_, script, f"close-{flow['pass']}",
                                       close_note(run, items))
            if done:
                form_item(items, script.form_changes)
            return done

        def not_passed(work, label: str) -> str:
            outcome.update(result="not passed", findings=_unchecked(work, work.problems),
                           stage=label)
            return "ended"

        async def outline_stage(label: str) -> str:
            while flow["stage"] in OUTLINE_STEPS:
                stage = flow["stage"]
                if stage == "outline":
                    with clock("outline"):
                        if not await author_writes(swarm_, outline, "outline", outline_note(run)):
                            return not_passed(outline, label)
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
                            return not_passed(outline, label)
                    flow["stage"] = "outline-signoff"
                else:
                    with clock("outline-signoff"):
                        await owners_turn(swarm_, OWNERS, OUTLINE_CONCERNS, outline,
                                          "outline-signoff", lambda name: (
                                              "The author has settled the outline, above. "
                                              "Sign the outline off for your concerns.\n"
                                              + _work_text(name, run, outline, [], kept)))
                    flow["stage"] = "fill"
                    _keep(run, swarm_, kept)
                    if _ask_sender(run, swarm_, kept, OUTLINE_CONCERNS):
                        return "waiting"
                _keep(run, swarm_, kept)
            return "done"

        async def fill_stage(label: str) -> str:
            while flow["stage"] in FILL_STEPS:
                if flow["stage"] == "fill":
                    with clock("fill"):
                        if not await author_writes(swarm_, script, "fill", fill_note(run)):
                            return not_passed(script, label)
                        await mark()
                    flow["stage"] = "revise"
                else:
                    with clock("revise"):
                        if not await author_writes(swarm_, script, "revise",
                                                   revise_note(marked)):
                            return not_passed(script, label)
                        await mark()
                    flow["stage"] = "pass"
                _keep(run, swarm_, kept)
            return "done"

        async def passes(label: str) -> str:
            end = await fix_passes(run, swarm_, kept, flow, SCRIPT_CONCERNS, script,
                                   mark=mark, revise=revise, given=given, clock=clock)
            if end == "passed":
                return "done"
            if end == "waiting":
                return "waiting"
            if end == "unchecked":
                return not_passed(script, label)
            outcome.update(result=end, findings=flow.get("left") or [], stage=label)
            return "ended"

        async def revision_stage(label: str) -> str:
            rev = kept["revision"]
            if flow["stage"] == "revision-write":
                rev.setdefault("research_before", _research_mark(run))
                named: list[str] = []
                flow["revision_base"] = script.read()
                with clock(f"revision-{rev['n']}"):
                    if not await author_writes(swarm_, script, f"revision-{rev['n']}",
                                               revision_note(run, rev), named):
                        return not_passed(script, label)
                    changed, back = _put_back(run, flow["revision_base"], named)
                    await mark()
                now = script.read()
                kept_slides = [i for i in package_script.slide_ids(now) if i not in changed]
                was, research_now = rev.get("research_before") or {}, _research_mark(run)
                rev.update(named=list(dict.fromkeys(named)), slides_changed=changed,
                           put_back=back, slides_kept=kept_slides,
                           research_redone=research_now["sha256"] != was.get("sha256"),
                           searches=max(0, research_now["searches"]
                                        - int(was.get("searches") or 0)))
                swarm_.say(PROGRAM, (
                    f"The revised script changes slides {', '.join(changed)}; "
                    if changed else "The revised script changes no slide; ")
                    + (f"slides {', '.join(kept_slides)} are kept as they were"
                       if kept_slides else "no slide is kept as it was")
                    + (f" (JARVIS put back {', '.join(back)}, which you did not name)."
                       if back else "."))
                # The owners re-check only what changed; with nothing
                # changed, the whole script.
                flow.update({"stage": "pass", "pass": 1, "slides": changed or None})
                _keep(run, swarm_, kept)
            return await passes(label)

        def start(kind: str, name: str, more: dict, label: str) -> None:
            """A stage begun: the flow at its first step, and what it is
            given beyond the conversation said into it."""
            flow.clear()
            flow.update({"stage": FIRST_STEP[kind], "pass": 1})
            if kind == REVISION_STAGE:
                kept["revision"] = {"n": int(name), "by": more["by"], "words": more["words"]}
                swarm_.say(PROGRAM, (
                    f"This run revises the script: {_who(kept['revision'])} said what is "
                    f"wrong. Everything made so far is kept (the research, the prep, the "
                    f"outline and the script), and only what the words below change is made "
                    f"again. {'The sender' if more['by'] == SENDER else 'Tim'} said:"))
                swarm_.say(more["by"], words_said(more["words"], more["by"]))
            said = findings_said(findings_for(doc, label))
            if said:
                swarm_.say(PROGRAM, "The run before ended this stage with findings for the "
                                    "sender; its answers follow, and the stage is done again "
                                    "from the work as it stands.")
                swarm_.say("sender", said)
        run_stage = {OUTLINE_STAGE: outline_stage, FILL_STAGE: fill_stage,
                     PASSES_STAGE: passes, REVISION_STAGE: revision_stage}
        try:
            await swarm_.open()
            _take_answers(run, swarm_, kept)
            for kind, name, more in specs[todo:]:
                # Each stage's key names the model each participant runs
                # it on in this run (_had).
                tool = tool_of(doc, kept=kept)
                unit = stage_unit(doc, kind, name, more, before, tool)
                label = unit.label
                if kept.get("pending") != unit.key:
                    kept["pending"], kept["pending_began"] = unit.key, time.time()
                    start(kind, name, more, label)
                end = await run_stage[kind](label)
                _keep(run, swarm_, kept)
                if end == "waiting":
                    outcome["waiting"] = True
                    return
                if end != "done":
                    return
                flow.pop("revision_base", None)
                _write_result(run, {**swarm_.result(), "result": "passed", "findings": [],
                                    "passes": flow.get("pass", 1),
                                    "times": kept.get("times") or []})
                entry = finish_unit(run, kept, unit, float(kept.pop("pending_began", 0) or
                                                            time.time()), STAGE_FILES)
                before = entry.digest
                if kind == REVISION_STAGE:
                    package_jobs.noted(doc["id"], "revision", {
                        k: kept["revision"].get(k) for k in (
                            "by", "slides_changed", "slides_kept", "named", "put_back",
                            "research_redone", "searches")})
            outcome["result"] = "passed"
        finally:
            _keep(run, swarm_, kept)
            outcome["swarm"] = swarm_.result()
            await swarm_.close()
    package_jobs.asked(SWARM, "the swarm",
                       "JARVIS ran the swarm: the author, Tim's reader, the context owner, the "
                       "structure and decision owner and the editor.", go())
    if outcome.get("waiting"):
        return package_jobs.WAITING
    if outcome["result"] == "passed":
        return _swarm_passed(run, kept)
    result = {**outcome["swarm"], "result": outcome["result"],
              "findings": outcome["findings"], "passes": flow.get("pass", 1),
              "times": kept.get("times") or []}
    _write_result(run, result)
    package_jobs.noted(doc["id"], "swarm", {"result": "not passed", "passes": flow.get("pass", 1),
                                            "stage": outcome.get("stage"),
                                            "conversation": str(run.folder / CONVERSATION)})
    package_jobs.failed_swarm(doc["id"], findings_questions(result["findings"]),
                              result["findings"], stage=outcome.get("stage"))
    return package_jobs.WAITING


def _swarm_passed(run: Run, kept: dict) -> None:
    """The swarm passed (every stage done or reused): the hits its script
    used recorded, and its outcome kept on the job."""
    script = run.script_file()
    used = research_log.mark_used(run.log, package_script.sources(script.read()), roots=(
        run.place.path, str(run.place.folder), str(run.folder)))
    flow = kept.get("flow") or {}
    package_jobs.noted(run.doc["id"], "swarm", {
        "result": "passed", "passes": flow.get("pass", 1), "script": str(run.script),
        "outline": str(run.outline), "prep": str(run.prep),
        "conversation": str(run.folder / CONVERSATION),
        "hits_used": sum(len(u["used"]) for u in used["used"])})
    return None
