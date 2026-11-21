"""
sessions.py — the sessions a package is made with (sessions.yaml, approved
by Tim 2026-11-18 and 2026-11-19).

Composition, each type with its own interface (his word, 2026-11-18:
"Composition is most sensible but each type has it's own intrface."):
each is a `Protocol` its callers depend on, and its Brain implementation
runs on the brain providers (the Team ones, on the team server's models,
come later).

- `ChatSession` (`BrainChatSession`): one role's conversation with a
  model, on the brain's own transport (claude_provider / codex_provider:
  one transport per brain, shared by the voice brain and the roles),
  launched on JARVIS's own settings, locked down and released to exactly
  its role's `roles.Release`. One-shot or long-lived.
- `SwarmSession` (`BrainSwarmSession`): several long-lived ChatSessions in
  one conversation, every message sent to the others word for word;
  votes and questions.
- `DecisionSession`, `ClassificationSession`, `ExtractionSession`: designed
  around Jev and CDM.
- `MarkingSession` (`BrainMarkingSession`): the text split by a program
  (text_parts.py), then every part scored against every label, in batches
  small enough to be answered whole, each given the whole file; a script
  slide by slide.
- `RetrievalSession` and `EditingSession` (`BrainRetrievalSession`,
  `BrainEditingSession`): a ChatSession with its inputs structured.
- `SummarizationSession` (`BrainSummarizationSession`): sources summarised
  for a purpose, each summary citing the paths it rests on; one citing a
  path not in its source is rejected by the program.

Every model is picked by model_tiers.pick (the role's tier on the brain
engines.json gives its kind) and swept when it is not available; the
step's log says which model each session took. A session that cannot give
an answer raises `SessionFailed`, which is the step's Error.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol
from xml.sax.saxutils import quoteattr, unescape

from jarvis import (brain_provider, evidence_types, model_tiers, private_files, role_mcp,
                    role_prompts, roles, run_executor, settings_service, text_parts)
from jarvis.brain import BrainConfig

log = logging.getLogger("jarvis.sessions")


class SessionFailed(RuntimeError):
    """A session could not give an answer: the step goes to Error with it."""


class NotInForm(SessionFailed):
    """A session answered, but not in the form its call gave."""


# ── answers in a fixed form ───────────────────────────────────────────────

def _object(properties: dict) -> dict:
    """A JSON schema both brains take as an answer format: every property
    required, nothing else allowed."""
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


_TEXT = {"type": "string"}
_SCORE = {"type": "number", "minimum": 0, "maximum": 1}
_OFFSET = {"type": "integer", "minimum": 0}


def json_answer(text: str):
    """The JSON object a reply holds: the whole reply, else the last object
    in it (a long-lived Claude session's turn, whose form is checked here
    rather than by the CLI)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder, found, at = json.JSONDecoder(), None, text.find("{")
    while at != -1:
        try:
            found, end = decoder.raw_decode(text, at)
            at = text.find("{", end)
        except ValueError:
            at = text.find("{", at + 1)
    return found


def _tag(tag: str, text: str, **attrs) -> str:
    """One item of material as a tagged document. Its words are given as
    they are, never escaped: a value copied from them, and its offsets,
    must be the text's own."""
    shown = "".join(f" {k}={quoteattr(str(v))}" for k, v in attrs.items() if v is not None)
    return f"<{tag}{shown}>{text}</{tag}>"


# ── ChatSession ───────────────────────────────────────────────────────────

# The approved prompts' own words for an answer in a fixed form.
SCHEMA_GIVEN = "(Answer in the schema given with this call.)"


@dataclass
class Reply:
    text: str
    answer: object = None           # the JSON answer, where the turn had a form


# A model the provider refuses by name is not available: the sweep goes on
# to the next. Claude Code says so on the first turn ("There's an issue with
# the selected model ... It may not exist or you may not have access to
# it"); Codex when the thread or the turn starts ("The 'gpt-6-luna' model
# is not supported when using Codex with a ChatGPT account").
def model_refused(error: str) -> bool:
    words = (error or "").lower()
    return "model" in words and any(w in words for w in (
        "not supported", "does not exist", "not available", "not found",
        "issue with the selected model", "may not exist"))


class ChatSession(Protocol):
    """One role's conversation with a model (sessions.yaml ChatSession):
    a general inference chat, one-shot or long-lived. In: the role, its
    prompt, the run folder and the folders it may read, an answer format
    where the answer has a fixed form, and the turns, in order. Out: each
    turn's reply, and its session (`session`), so a long-lived one is
    resumed after a restart."""

    role: roles.Role
    session: Optional[str]
    model: Optional[str]

    @property
    def one_shot(self) -> bool: ...

    async def open(self) -> None: ...

    async def ask(self, text: str, timeout: float,
                  answer_format: Optional[dict] = None, strict: bool = True) -> "Reply": ...

    async def close(self) -> None: ...

    async def once(self, text: str, timeout: float) -> "Reply": ...


class BrainChatSession:
    """A ChatSession on the brain providers' own transports.

    In: the role (its tier, effort, tools and lifetime, roles.py), its
    prompt as the system prompt, the run folder it works in and the folders
    it may read, an answer format where the answer has a fixed form, and
    the turns, in order. Out: each turn's reply, and the session's id, so a
    long-lived session is resumed after a restart, never started fresh.

    The model is the first of the pick's sweep the brain takes: one it
    refuses, when the session starts or on its first turn (where Claude
    Code says so), is logged and the next one tried, the first turn sent
    again on it; the job's log says which model it took.
    """

    def __init__(self, role: str, prompt: str, folder: Path, *,
                 pick: model_tiers.Pick, reads: tuple[Path, ...] = (),
                 answer_format: Optional[dict] = None, resume: Optional[str] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 role_spec: Optional[roles.Role] = None,
                 search_log: Optional[Path] = None):
        self.role = role_spec or roles.role(role)
        self.search_log = Path(search_log) if search_log is not None else None
        self.prompt = prompt
        self.folder = Path(folder)
        self.pick = pick
        self.reads = tuple(Path(r) for r in reads)
        self.answer_format = answer_format
        self._resume = resume               # a session to resume, else a new one
        self.session: Optional[str] = resume
        self.model: Optional[str] = None
        self.info: Optional[brain_provider.SessionInfo] = None
        self._on_log = on_log or (lambda line: log.info(line))
        self._child: Optional[brain_provider.ProviderSession] = None
        self._parts: list[str] = []
        self._done: Optional[asyncio.Future] = None
        self._sweep = list(pick.tried or [pick.model])
        self._at = -1                       # the sweep's model now open
        self._answered = False              # a turn has been answered on it

    @classmethod
    def for_role(cls, role: str, prompt: str, folder: Path, *, requester: Optional[str] = None,
                 engine: Optional[str] = None, model: Optional[str] = None,
                 author_engine: Optional[str] = None, **kwargs) -> "BrainChatSession":
        """The role's brain and model picked (model_tiers.pick). Raises
        model_tiers.NoModel when the sweep finds none."""
        spec = kwargs.get("role_spec") or roles.role(role)
        pick = model_tiers.pick(spec.kind, spec.tier, requester=requester, engine=engine,
                                model=model, author_engine=author_engine)
        return cls(role, prompt, folder, pick=pick, **kwargs)

    @property
    def one_shot(self) -> bool:
        return self.role.lifetime == roles.ONE_SHOT

    def _mcp_config(self) -> Optional[Path]:
        if not self.role.release.tools:
            return None
        path = self.folder / ".jarvis-role" / f"{self.role.name}-mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        folders = (self.folder, *self.reads) if self.role.release.reads_files else ()
        body = {"mcpServers": {roles.SERVER_NAME: role_mcp.entry(self.role.name,
                                                                  self.search_log, folders)}}
        private_files.replace(path, json.dumps(body, indent=2).encode("utf-8"))
        return path

    def _config(self, model: str) -> BrainConfig:
        """One try's launch. A new session is new on every try (a session
        id of its own for a long-lived Claude one, never `--resume` of a
        try the brain refused); a resumed one is resumed on each."""
        settings_service.load()     # his saved program paths, in any process
        self.session = self._resume
        new_id = None
        if not self.one_shot and self._resume is None and self.pick.engine == "claude":
            new_id = str(uuid.uuid4())
            self.session = new_id
        launch = roles.Launch(role=self.role, mcp_config=self._mcp_config(), reads=self.reads,
                              answer_format=self.answer_format if self.one_shot else None,
                              session_id=new_id)
        return BrainConfig(home=self.folder, provider=brain_provider.ENGINES[self.pick.engine],
                           model=model, effort=self.role.effort, resume=self._resume,
                           role=launch,
                           claude_path=settings_service.value("claude_path") or None,
                           codex_path=settings_service.value("codex_path") or None)

    # ── the transport's events ───────────────────────────────────────────
    def _event(self, _child, event) -> None:
        if isinstance(event, brain_provider.SessionInfo):
            self.info = event
            if event.conversation_id and (self.session is None or self.pick.engine == "codex"):
                self.session = event.conversation_id
        elif isinstance(event, brain_provider.TextDelta):
            self._parts.append(event.text)
        elif isinstance(event, brain_provider.TurnEnded):
            if self._done is not None and not self._done.done():
                if event.error:
                    self._done.set_exception(SessionFailed(event.error))
                else:
                    self._done.set_result(event.reply if event.reply is not None
                                          else "".join(self._parts))

    async def _exited(self, _child, code) -> None:
        if self._done is not None and not self._done.done():
            self._done.set_exception(SessionFailed(
                f"the {self.role.name} session ended before answering (exit {code})"))

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _open_one(self, model: str) -> None:
        provider = brain_provider.provider(brain_provider.ENGINES[self.pick.engine])
        self._child = provider.session(self._config(model), self._event, self._exited)
        wall, _ = run_executor.bounds()
        try:
            async with asyncio.timeout(wall):
                await self._child.open(self.prompt)
        except TimeoutError as e:
            raise SessionFailed(f"the {self.role.name} session did not start within "
                                f"{wall:.0f} s (Settings, Agents: run_timeout_sec)") from e
        except OSError as e:
            raise SessionFailed(str(e)) from e
        self.model = model

    def _refused(self, model: str, error: str) -> None:
        self._on_log(f"{self.role.name}: {self.pick.engine} refused {model}: {error}")

    async def _open_next(self, last: str = "") -> None:
        """Opened on the next model of the sweep the brain takes. Raises
        model_tiers.NoModel when none is left."""
        while self._at + 1 < len(self._sweep):
            self._at += 1
            model = self._sweep[self._at]
            try:
                await self._open_one(model)
            except SessionFailed as e:
                last = str(e)
                if model_refused(last):
                    self._refused(model, last)
                    continue
                raise
            self._on_log(f"{self.role.name}: {self.pick.engine} {model} "
                         f"(tier {self.pick.tier}, {self.pick.why}, effort {self.role.effort})")
            return
        raise model_tiers.NoModel(f"{self.role.name}: no {self.pick.engine} model in tier "
                                  f"{self.pick.tier} or the next one up was accepted ({last})")

    async def open(self) -> None:
        """Started on the first model of the sweep the brain accepts."""
        await self._open_next()

    async def _turn(self, text: str, timeout: Optional[float],
                    answer_format: Optional[dict]) -> str:
        """One turn, held to the run's bounds (run_executor.bounds): it
        fails once it has not answered within the wall clock (`timeout`
        where the caller names one, else Settings' run_timeout_sec), or
        once its brain has written nothing for run_idle_sec. A run always
        reaches a terminal state: a turn past its bound is SessionFailed,
        the step's Error, and its process is ended by the caller's close."""
        wall, idle = run_executor.bounds(timeout)
        self._parts = []
        self._done = asyncio.get_running_loop().create_future()
        started = time.monotonic()
        try:
            async with asyncio.timeout(wall):
                await self._child.send(text, answer_format=answer_format)
                while True:
                    heard = max(started, getattr(self._child, "heard_at", started))
                    left = idle - (time.monotonic() - heard)
                    if left <= 0:
                        raise SessionFailed(
                            f"the {self.role.name} session's brain wrote nothing for "
                            f"{idle:.0f} s (Settings, Agents: run_idle_sec)")
                    try:
                        return await asyncio.wait_for(asyncio.shield(self._done), left)
                    except TimeoutError:
                        continue        # heard since? measured again
        except TimeoutError as e:
            raise SessionFailed(f"the {self.role.name} session did not answer within "
                                f"{wall:.0f} s (Settings, Agents: run_timeout_sec)") from e
        except OSError as e:
            raise SessionFailed(str(e)) from e

    async def ask(self, text: str, timeout: float,
                  answer_format: Optional[dict] = None, strict: bool = True) -> Reply:
        """One turn: its reply, and its JSON answer where it has a form (the
        one-shot's own, or this turn's). `strict`: a reply not in its form
        fails the session; else its answer is None and its words stand."""
        if self._child is None:
            raise SessionFailed(f"the {self.role.name} session is not open")
        form = answer_format if answer_format is not None else self.answer_format
        if answer_format is not None and self.pick.engine == "claude":
            # Claude takes a form per process, not per turn: this turn's is
            # given in the message, and checked here (participants.md 5).
            text = f"{text}\n\n{SCHEMA_GIVEN}\n{json.dumps(answer_format)}"
        while True:
            try:
                said = await self._turn(text, timeout, answer_format)
                break
            except SessionFailed as e:
                if self._answered or not model_refused(str(e)):
                    raise
                # Refused on its first turn: the sweep's next model, the
                # turn sent again on it.
                self._refused(self.model or "", str(e))
                await self._close_child()
                await self._open_next(str(e))
        self._answered = True
        answer = json_answer(said) if form is not None else None
        if form is not None and not isinstance(answer, dict):
            if not strict:
                return Reply(text=said, answer=None)
            raise NotInForm(f"the {self.role.name} session's answer was not in its form")
        return Reply(text=said, answer=answer)

    async def _close_child(self) -> None:
        if self._done is not None and not self._done.done():
            self._done.cancel()
        if self._child is not None:
            child, self._child = self._child, None
            await child.close(5)

    async def close(self) -> None:
        await self._close_child()

    async def once(self, text: str, timeout: float) -> Reply:
        """A one-shot role: open, one item in, one answer out, closed."""
        try:
            await self.open()
            return await self.ask(text, timeout)
        finally:
            await self.close()


def role_prompt(role: str, values: dict[str, str]) -> tuple[str, str]:
    """A role's approved prompt filled: (system prompt, first message)."""
    prompt = role_prompts.PROMPTS[role]
    system_values = {p: values[p] for p in role_prompts.PLACEHOLDER.findall(prompt.system)
                     if p in values}
    material_values = {p: values[p] for p in role_prompts.PLACEHOLDER.findall(prompt.material)
                       if p in values}
    return (role_prompts.fill(prompt.system, system_values),
            role_prompts.fill(prompt.material, material_values))


# ── what Tim knows, and who is given what ─────────────────────────────────

# The three knowledge states (workflow.yaml tim_knowledge knowledge_state),
# set by a program from where an item came from, never by a model.
KNOWS, HAD_ACCESS, PROBABLY_NOT = "knows", "had_access", "probably_does_not_know"
STATES = (KNOWS, HAD_ACCESS, PROBABLY_NOT)


@dataclass(frozen=True)
class Known:
    """One item of Tim's material with its knowledge state."""
    text: str
    state: str                      # KNOWS, HAD_ACCESS or PROBABLY_NOT
    source: str = ""


# Each swarm participant's share of what Tim knows (workflow.yaml
# tim_knowledge who_gets_what, approved 2026-11-19): the Theory of Mind
# reader, the cares and the order judges know and had access only, kept
# apart; the author, the editor, the decision ready and the background
# judges everything, each item marked with its state.
SHARES: dict[str, tuple[str, ...]] = {
    "author": STATES,
    "editor": STATES,
    "decision_ready_judge": STATES,
    "background_judge": STATES,
    "cares_judge": (KNOWS, HAD_ACCESS),
    "order_judge": (KNOWS, HAD_ACCESS),
    "theory_of_mind_reader": (KNOWS, HAD_ACCESS),
}


def share(role: str, material: list[Known]) -> list[Known]:
    """Exactly this participant's share of the material. Raises KeyError
    for a role with no share."""
    given = SHARES[role]
    for item in material:
        if item.state not in STATES:
            raise ValueError(f"{item.state!r} is not a knowledge state")
    return [item for item in material if item.state in given]


def _material(items: list[Known], *, marked: bool) -> str:
    return "\n".join(_tag("material", k.text, state=k.state if marked else None,
                          source=k.source or None) for k in items)


def share_values(role: str, material: list[Known]) -> dict[str, str]:
    """This participant's share as its approved prompt's placeholders: the
    two kept apart ({knows}, {had_access}) for one given those only, else
    every item with its state."""
    items = share(role, material)
    if PROBABLY_NOT not in SHARES[role]:
        return {"{knows}": _material([k for k in items if k.state == KNOWS], marked=False),
                "{had_access}": _material([k for k in items if k.state == HAD_ACCESS],
                                          marked=False)}
    marked = _material(items, marked=True)
    return {"{the evidence, each item with its knowledge state}": marked,
            "{the gathered and marked material, with its knowledge state}": marked}


def marked_parts(marked: list[dict]) -> str:
    """The marked script (MarkingSession's output) as tagged parts, each at
    its slide and field when it is a script's."""
    return "\n".join(_tag("part", m["segment"], id=m["id"], kind=m["kind"],
                          slide=m.get("slide"), field=m.get("field"),
                          start=m["position"]["start"], end=m["position"]["end"],
                          scores=json.dumps(m["scores"], sort_keys=True)) for m in marked)


def marked_slides(marked: list[dict]) -> str:
    """The marked script slide by slide: each slide's parts under it, in
    order (a text marked whole, with no slides, as its parts)."""
    slides: dict[str, list[dict]] = {}
    for m in marked:
        slides.setdefault(m.get("slide") or "", []).append(m)
    if list(slides) == [""]:
        return marked_parts(marked)
    return "\n".join(_tag("slide", "\n" + marked_parts(parts) + "\n", id=sid)
                     for sid, parts in slides.items())


# ── SwarmSession ──────────────────────────────────────────────────────────

OPEN, PROVEN, FAILED = "open", "proven", "failed"
YES, NO = "yes", "no"
ROUNDS_MAX = 3          # after three rounds without passing, nothing is built
VOTE_FORM = _object({"question": _TEXT, "vote": {"type": "string", "enum": [YES, NO]},
                     "reason": _TEXT})
# What a participant may add to any turn: the vote called on questions whose
# discussion has settled (workflow.yaml swarm plan.decision).
CALL_VOTE = {"type": "array", "items": _TEXT,
             "description": "The ids of open questions whose discussion has settled, to call "
                            "the vote on now. Empty when none."}
TURN_FORM = _object({"message": {"type": "string",
                                 "description": "What you say in the conversation."},
                     "call_vote": CALL_VOTE})


# The vote on whether there is enough to answer one of the handoff's
# questions also says, with a no, what to ask the sender (the author's
# approved prompt: "You do vote on whether there is enough to answer each
# question, and if not, what to ask the sender"; workflow.yaml swarm
# ask_sender: "only what the swarm voted it needs and research could not
# answer").
ENOUGH_FORM = _object({**VOTE_FORM["properties"], "ask_sender": {
    "type": "string",
    "description": "With a no: what to ask the sender, that research could not answer. "
                   "Empty with a yes."}})
# Whether the script's change since a closed question's vote changed what
# that question was about (workflow.yaml swarm plan.decision: "it reopens if
# the script changes what it was about"): a judgement, asked of the
# question's voters and decided as the swarm decides, by majority.
REOPEN_FORM = _object({"question": _TEXT, "changed": {"type": "string", "enum": [YES, NO]},
                       "reason": _TEXT})


@dataclass
class Question:
    id: str
    asks: str
    author_votes: bool = False      # the author never votes on whether its own script passes
    enough: bool = False            # is there enough to answer a handoff question (ENOUGH_FORM)


@dataclass
class Vote:
    question: str
    participant: str
    vote: str
    reason: str
    called_by: str = "program"      # the participant who called it, or the program
    ask: str = ""                   # an enough question's no: what to ask the sender
    script: str = ""                # the digest of the script it was cast on


def _majority(said: list[str]) -> bool:
    """The swarm's one count: more yes than no (a tie is not a majority)."""
    yes = sum(1 for s in said if s == YES)
    return yes > len(said) - yes


def script_digest(text: str) -> str:
    """The script text a vote was cast on, as its SHA-256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SwarmSession(Protocol):
    """Several long-lived ChatSessions working together in one conversation
    (sessions.yaml SwarmSession). In: the participants, the background and
    the questions to prove. Out: the conversation word for word, the
    votes, each question's state and the result (`result`)."""

    participants: dict[str, ChatSession]

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def say(self, name: str, text: str) -> None: ...

    async def turn(self, name: str, timeout: float, add: str = "") -> str: ...

    async def vote(self, question: str, timeout: float, called_by: str = "program") -> str: ...

    def reopen(self, question: str) -> None: ...

    async def round(self, timeout: float, script: "Script",
                    mark: Callable[["Script"], Awaitable[list[dict]]],
                    **parts) -> Optional[dict]: ...

    def result(self) -> dict: ...

    def saved(self) -> dict: ...

    def restore(self, saved: dict) -> None: ...


# The program's own turns in the conversation (what does not check or build,
# the deck's layout data).
PROGRAM = "jarvis"


class Script(Protocol):
    """The script a swarm writes, in its structured form (package_script:
    script.yaml, script/1)."""

    name: str                       # its file's name
    version: str                    # its format
    form: dict                      # what the author answers beside its message

    def take(self, answer: dict) -> list[str]:
        """The script in the author's answer, checked and, when it checks,
        written; every problem, empty when it checks."""

    def text(self) -> str:
        """The script as written now."""

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        """The editor's edits applied by the program and written: (the
        script's text, the edits applied, the edits refused)."""


# A participant's first message, given on its first turn: its approved
# prompt's closing material, made when that turn comes (the prep and the
# script it names exist only once the author has written them).
FirstMessage = Callable[[str], str]
# What the program adds to one participant's turn in a round, beside the
# marked script: (name, the script's text, its marked parts) -> words.
RoundAdd = Callable[[str, str, list], str]


class BrainSwarmSession:
    """A SwarmSession of ChatSessions on the brain providers (workflow.yaml
    swarm). The program runs the turns and
    keeps the log word for word, never summarising or rewriting: each
    participant is sent every new message of the others, verbatim, with
    who said it, and is given exactly its share of what Tim knows
    (`SHARES`). Any participant may call the vote on a question when its
    discussion settles (`call_vote` on its turn); otherwise the program
    calls one on every open question at the end of each round. A vote is a
    turn in each voter's own session, yes or no with a reason (and, on an
    enough question, what a no would ask the sender); a majority of yes
    proves a question, anything else (a majority of no, a tie) fails it; a
    question reopens when the script changes what it was about: once the
    script is no longer the one it was closed on, its voters judge whether
    the change touched what it was about, and a majority of them reopens it
    (`reopen_changed`); or the sender's answers came (`reopen`). The Theory
    of Mind reader never votes, and the author not on whether its own
    script passes.

    Its state (`saved`, `restore`) is kept between the run's processes, so
    a swarm that waits on the sender goes on where it stopped, each
    participant's session resumed.
    """

    def __init__(self, participants: dict[str, ChatSession], questions: list[Question],
                 background: Optional[dict] = None, *, author: str = "author",
                 editor: str = "editor",
                 non_voters: tuple[str, ...] = ("theory_of_mind_reader",),
                 material: Optional[list[Known]] = None,
                 first: "Optional[dict[str, str] | FirstMessage]" = None,
                 editing: Optional[Callable[[ChatSession], "EditingSession"]] = None):
        self.participants = participants
        self.editing = editing or BrainEditingSession
        self.background = background or {}
        self.questions = {q.id: q for q in questions}
        self.state = {q.id: OPEN for q in questions}
        self.closed_by: dict[str, list[Vote]] = {}
        self.votes: list[Vote] = []
        self.reopenings: list[dict] = []    # each voter's judgement whether a change reopened
        self.conversation: list[dict] = []
        self.rounds = 0
        self.author = author
        self.editor = editor
        self.non_voters = non_voters
        self.material = list(material or [])
        self.shares = {name: share(name, self.material) for name in participants
                       if name in SHARES}
        self._first = first if callable(first) else dict(first or {}).get
        self._introduced: set[str] = set()
        self._seen = {name: 0 for name in participants}
        self.script = ""                    # the digest of the script now (script_digest)
        # Votes called before the swarm is open (the author's research turn,
        # when no other voter's session is), each (caller, question ids):
        # held, and called on its first round once every participant has
        # spoken (`play_round`).
        self.held: list[tuple[str, list]] = []
        self._opened = False

    @classmethod
    def for_roles(cls, names: list[str], questions: list[Question], folder: Path, *,
                  material: list[Known], values: dict[str, dict[str, str]],
                  background: Optional[dict] = None, reads: tuple[Path, ...] = (),
                  on_log: Optional[Callable[[str], None]] = None,
                  **pick_args) -> "BrainSwarmSession":
        """Each participant on its role, its approved prompt filled with its
        share of the material (`share_values`) and the rest of its values
        (`values`, by role: the script, the prep, the labels ...). The
        author is picked first: the judges go on the other brain from it
        when Settings says so (model_tiers.pick). Raises NoModel."""
        participants: dict[str, ChatSession] = {}
        first: dict[str, str] = {}
        author_engine = None
        for name in sorted(names, key=lambda n: n != "author"):
            system, first[name] = role_prompt(name, {**share_values(name, material),
                                                     **values.get(name, {})})
            participants[name] = BrainChatSession.for_role(
                name, system, folder, reads=reads, on_log=on_log,
                author_engine=author_engine, **pick_args)
            if name == "author":
                author_engine = participants[name].pick.engine
        ordered = {name: participants[name] for name in names}
        return cls(ordered, questions, background, material=material, first=first)

    async def open(self) -> None:
        for chat in self.participants.values():
            await chat.open()
        self._opened = True

    async def close(self) -> None:
        for chat in self.participants.values():
            await chat.close()

    def share_of(self, name: str) -> list[Known]:
        return self.shares.get(name, [])

    def _new_for(self, name: str) -> str:
        """Every message of the others since this participant last spoke,
        word for word, each with who said it; on its first turn its own
        first message and the questions to prove come first. Nothing is
        taken as given it until it has answered (`_heard`)."""
        new = [m for m in self.conversation[self._seen[name]:] if m["from"] != name]
        lead = []
        if name not in self._introduced:
            lead.append(self._first(name) or "")
            lead.append("The questions to prove, by id:\n" + "\n".join(
                _tag("question", q.asks, id=q.id) for q in self.questions.values()))
        return "\n\n".join([t for t in lead if t] +
                            [f"{m['from']}:\n{m['text']}" for m in new])

    def _heard(self, name: str) -> None:
        """A participant answered: everything said so far is given it, its
        first message among it."""
        self._seen[name] = len(self.conversation)
        self._introduced.add(name)

    def spoke(self, name: str) -> bool:
        """It has answered a turn of the swarm (its session holds it)."""
        return name in self._introduced

    def say(self, name: str, text: str) -> None:
        """A message into the conversation, as it was written (the
        program's own, the sender's, or a participant's)."""
        self.conversation.append({"from": name, "text": text})

    async def _called(self, name: str, ids, timeout: float) -> None:
        """The votes a participant called on its turn, each on a question
        still open. Called before the swarm is open, they are held
        (`held`) until its round."""
        if not self._opened:
            if isinstance(ids, list) and ids:
                self.held.append((name, list(ids)))
            return
        for qid in ids if isinstance(ids, list) else []:
            if self.state.get(str(qid)) == OPEN:
                await self.vote(str(qid), timeout, called_by=name)

    async def turn(self, name: str, timeout: float, add: str = "") -> str:
        """One participant's turn: it is sent what the others said since
        its last, and `add` (the program's own message to it); its message
        goes into the conversation, and the votes it calls are held."""
        return (await self.answer(name, timeout, add))["message"]

    async def answer(self, name: str, timeout: float, add: str = "",
                     more: Optional[dict] = None) -> dict:
        """A turn (`turn`) whose answer also carries `more`, the properties
        the program asks beside the message (the author's script); the whole
        answer, or the message alone when it was answered in words."""
        sent = "\n\n".join(t for t in (self._new_for(name), add) if t)
        form = TURN_FORM if not more else _object({**TURN_FORM["properties"], **more})
        reply = await self.participants[name].ask(sent, timeout, answer_format=form,
                                                  strict=False)
        # A turn answered in words alone (a long-lived Claude session holds
        # the form only as it is asked in the message): its words are its
        # message, word for word, and it calls no vote.
        answer = reply.answer if reply.answer is not None else {"message": reply.text,
                                                                "call_vote": []}
        if not isinstance(answer.get("message"), str) or not answer["message"].strip():
            raise SessionFailed(f"{name}'s turn had no message")
        self.say(name, answer["message"])
        self._heard(name)
        await self._called(name, answer.get("call_vote"), timeout)
        return answer

    def voters(self, question: str) -> list[str]:
        q = self.questions[question]
        return [n for n in self.participants
                if n not in self.non_voters and (n != self.author or q.author_votes)]

    async def vote(self, question: str, timeout: float, called_by: str = "program") -> str:
        """Every voter votes on one question, each in its own session; the
        question's state after the count."""
        q = self.questions[question]
        form = ENOUGH_FORM if q.enough else VOTE_FORM
        cast: list[Vote] = []
        for name in self.voters(question):
            sent = "\n\n".join(t for t in (self._new_for(name),
                                           "A vote is called on this question:\n"
                                           + _tag("question", q.asks, id=q.id)) if t)
            reply = await self.participants[name].ask(sent, timeout, answer_format=form)
            answer = reply.answer or {}
            said = str(answer.get("vote") or "").lower()
            if said not in (YES, NO) or not str(answer.get("reason") or "").strip():
                raise SessionFailed(f"{name}'s vote on {q.id} was not yes or no with a reason")
            ask = str(answer.get("ask_sender") or "").strip() if q.enough and said == NO else ""
            vote = Vote(q.id, name, said, str(answer["reason"]).strip(), called_by, ask,
                        self.script)
            cast.append(vote)
            self.say(name, f"Vote on {q.id}: {vote.vote}. {vote.reason}"
                     + (f" Ask the sender: {ask}" if ask else ""))
            self._heard(name)
        self.votes += cast
        self.state[q.id] = PROVEN if _majority([v.vote for v in cast]) else FAILED
        self.closed_by[q.id] = cast
        return self.state[q.id]

    def reopen(self, question: str) -> None:
        """What the question was about changed: it is open again."""
        self.state[question] = OPEN
        self.closed_by.pop(question, None)

    async def reopen_changed(self, timeout: float, only: Optional[set[str]] = None,
                             what: str = "script") -> list[str]:
        """Each closed question cast on a script that is no longer the
        script: its voters, each in its own session, judge whether the
        change touched what the question was about (yes or no with a
        reason); a majority reopens it, a tie leaves it closed. Whether the
        script changed at all is a fact (its digest); whether the change
        touched the question is their judgement. `only`: the questions
        looked at (the deck review's, on the deck's source; `what` names
        it). The ones reopened."""
        reopened = []
        for qid, cast in list(self.closed_by.items()):
            if only is not None and qid not in only:
                continue
            if all(v.script == self.script for v in cast):
                continue
            q = self.questions[qid]
            said = []
            for name in self.voters(qid):
                sent = "\n\n".join(t for t in (
                    self._new_for(name),
                    f"This question was closed ({self.state[qid]}) on an earlier {what}, and "
                    f"the {what} has changed since. Did the change touch what it was about?\n"
                    + _tag("question", q.asks, id=q.id)) if t)
                reply = await self.participants[name].ask(sent, timeout,
                                                          answer_format=REOPEN_FORM)
                answer = reply.answer or {}
                changed = str(answer.get("changed") or "").lower()
                reason = str(answer.get("reason") or "").strip()
                if changed not in (YES, NO) or not reason:
                    raise SessionFailed(f"{name}'s judgement on reopening {qid} was not yes or "
                                        f"no with a reason")
                said.append(changed)
                self.reopenings.append({"question": qid, "participant": name,
                                        "changed": changed, "reason": reason,
                                        "script": self.script})
                self.say(name, f"Did the change touch {qid}: {changed}. {reason}")
                self._heard(name)
            if _majority(said):
                self.reopen(qid)
                reopened.append(qid)
        return reopened

    def asks(self, question: str) -> list[str]:
        """What the votes that closed an enough question said to ask the
        sender, each once, in the order given."""
        return list(dict.fromkeys(v.ask for v in self.closed_by.get(question, [])
                                  if v.vote == NO and v.ask))

    async def vote_open(self, timeout: float) -> None:
        """The program calls a vote on every open question (the end of a
        round)."""
        for qid, state in list(self.state.items()):
            if state == OPEN:
                await self.vote(qid, timeout)

    def script_now(self, text: str) -> None:
        """What the votes from now on are cast on (its digest)."""
        self.script = script_digest(text)

    async def play_round(self, timeout: float, script: "Script",
                         mark: Callable[["Script"], Awaitable[list[dict]]], *,
                         author_add: str = "", prep: Optional[Callable[[], str]] = None,
                         add_for: Optional[RoundAdd] = None) -> bool:
        """One round's turns and votes (workflow.yaml swarm plan.round): the
        author revises the script, answering it in the script's own form
        (`script.form`, given as the answer schema; `author_add` the
        program's words to it), which the program checks and writes
        (`script.take`); the script is marked slide by slide (`mark`, the
        MarkingSession, after each revision); the editor edits, through its
        own session (EditingSession, given the prep, `prep()`; each edit at
        its slide and field, applied by the program, `script.edit`); the
        judges and the Theory of Mind reader respond, the judges from the
        marked script slide by slide, the reader from the script as Tim
        would read it, each with what `add_for` gives it. Votes a
        participant calls are held on its turn; then each closed question
        the change touched is reopened (`reopen_changed`, its voters'
        judgement) and the program calls one on every open question.

        A script that does not check is said into the conversation as the
        program's own turn, with every problem, and the round ends there:
        False (the round is still one of its three)."""
        answer = await self.answer(self.author, timeout, author_add, more=script.form)
        problems = script.take(answer)
        if problems:
            self.say(PROGRAM, f"The script in your answer does not check against "
                              f"{script.version}, so this round ends without it. Answer the "
                              f"whole script again, in its form:\n"
                     + "\n".join(f"- {p}" for p in problems))
            return False
        text = script.text()
        self.script_now(text)
        marked = await mark(script)
        editing = self.editing(self.participants[self.editor])
        out = await editing.edit(text, marked, self.share_of(self.editor), timeout,
                                 source=script.name, prep=prep() if prep else "",
                                 conversation=self._new_for(self.editor), call_vote=True,
                                 apply=script.edit)
        self.say(self.editor, out["reply"])
        self._heard(self.editor)
        if out["applied"]:
            text = out["text"]
            self.script_now(text)
            marked = await mark(script)
        await self._called(self.editor, out["call_vote"], timeout)
        for name in self.participants:
            if name in (self.author, self.editor):
                continue
            add = (_tag("script", text, file=script.name) if name in self.non_voters
                   else _tag("marked", marked_slides(marked), file=script.name))
            more = add_for(name, text, marked) if add_for else ""
            await self.turn(name, timeout, add="\n\n".join(t for t in (add, more) if t))
        held, self.held = self.held, []
        for caller, ids in held:
            await self._called(caller, ids, timeout)
        await self.reopen_changed(timeout)
        await self.vote_open(timeout)
        return True

    async def round(self, timeout: float, script: "Script",
                    mark: Callable[["Script"], Awaitable[list[dict]]], **parts) -> Optional[dict]:
        """One round (`play_round`), counted: the result once the swarm has
        passed or had its rounds, else None."""
        await self.play_round(timeout, script, mark, **parts)
        return self.end_round()

    @property
    def passed(self) -> bool:
        return all(s == PROVEN for s in self.state.values())

    def not_proven(self) -> set[str]:
        return {qid for qid, s in self.state.items() if s != PROVEN}

    def end_round(self) -> Optional[dict]:
        """A round is over: the result once the swarm has passed or has had
        its rounds, else None (it goes on)."""
        self.rounds += 1
        if self.passed:
            return self.result()
        if self.rounds >= ROUNDS_MAX:
            return self.result()
        return None

    def result(self) -> dict:
        """passed, or not passed with the last findings: each question not
        proven, and the reasons of the votes against it."""
        findings = [{"question": qid, "asks": self.questions[qid].asks, "state": state,
                     "against": [dataclasses.asdict(v) for v in self.closed_by.get(qid, [])
                                 if v.vote == NO]}
                    for qid, state in self.state.items() if state != PROVEN]
        return {"result": "passed" if self.passed else "not passed",
                "questions": [{"id": qid, "state": state,
                               "closed_by": [dataclasses.asdict(v)
                                             for v in self.closed_by.get(qid, [])]}
                              for qid, state in self.state.items()],
                "votes": [dataclasses.asdict(v) for v in self.votes],
                "reopenings": list(self.reopenings),
                "conversation": list(self.conversation),
                "findings": findings, "rounds": self.rounds}

    # ── kept between the run's processes ─────────────────────────────────
    def saved(self) -> dict:
        """Everything the swarm holds but its participants' sessions."""
        return {"questions": dict(self.state),
                "closed_by": {q: [dataclasses.asdict(v) for v in cast]
                              for q, cast in self.closed_by.items()},
                "votes": [dataclasses.asdict(v) for v in self.votes],
                "reopenings": list(self.reopenings),
                "conversation": list(self.conversation), "rounds": self.rounds,
                "seen": dict(self._seen), "introduced": sorted(self._introduced),
                "script": self.script, "held": [list(h) for h in self.held]}

    def restore(self, saved: dict) -> None:
        """The swarm as `saved` kept it."""
        self.state.update({q: s for q, s in (saved.get("questions") or {}).items()
                              if q in self.state})
        self.closed_by = {q: [Vote(**v) for v in cast]
                          for q, cast in (saved.get("closed_by") or {}).items()}
        self.votes = [Vote(**v) for v in saved.get("votes") or []]
        self.reopenings = list(saved.get("reopenings") or [])
        self.conversation = list(saved.get("conversation") or [])
        self.rounds = int(saved.get("rounds") or 0)
        self._seen.update({n: int(c) for n, c in (saved.get("seen") or {}).items()
                           if n in self._seen})
        self._introduced = set(saved.get("introduced") or [])
        self.script = str(saved.get("script") or "")
        self.held = [(str(h[0]), list(h[1])) for h in saved.get("held") or []
                     if isinstance(h, (list, tuple)) and len(h) == 2]


# ── DecisionSession ───────────────────────────────────────────────────────

YES_NO, CHOICE, SCALE = "yes_no", "choice", "scale"
# A Brain decision asks each question this many times.
ASKS = 3


@dataclass(frozen=True)
class DecisionQuestion:
    id: str
    asks: str
    form: str                                   # YES_NO, CHOICE or SCALE (0..10)
    options: tuple[str, ...] = ()               # a CHOICE's options

    def schema(self) -> dict:
        if self.form == YES_NO:
            answer = {"type": "string", "enum": [YES, NO]}
        elif self.form == CHOICE:
            answer = {"type": "string", "enum": list(self.options)}
        else:
            answer = {"type": "integer", "minimum": 0, "maximum": 10}
        return _object({"answer": answer})

    def valid(self, answer) -> bool:
        if self.form == YES_NO:
            return answer in (YES, NO)
        if self.form == CHOICE:
            return answer in self.options
        return isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer <= 10


def agreement(question: DecisionQuestion, answers: list) -> tuple[object, float]:
    """The answer most asks gave and the share that agreed with it (a tie:
    the first asked of those tied). A 0-10 answer is the median, rounded
    down, and an ask agrees when within one of it (today's decide)."""
    if not answers:
        return None, 0.0
    if question.form == SCALE:
        ordered = sorted(answers)
        middle = ordered[(len(ordered) - 1) // 2]
        return middle, sum(1 for a in answers if abs(a - middle) <= 1) / len(answers)
    counts = Counter(answers)
    best = max(counts.values())
    answer = next(a for a in answers if counts[a] == best)
    return answer, best / len(answers)


class DecisionSession(Protocol):
    """Makes a clean decision, like Jev: questions in parallel that cannot
    see each other, each answered with a probability and no explanation."""

    async def decide(self, text: str, questions: list[DecisionQuestion],
                     timeout: float) -> list[dict]: ...


class BrainDecisionSession:
    """On the brain providers: each question asked `ASKS` times, each ask a
    one-shot session of its own with no tools, the answer most asks gave,
    probability the share that agreed. `role` is whose brain, tier and
    effort it runs on."""

    def __init__(self, role: str, folder: Path, *, asks: int = ASKS, **pick_args):
        self.role = dataclasses.replace(roles.role(role), lifetime=roles.ONE_SHOT,
                                        release=roles.LOCKDOWN)
        self.folder = Path(folder)
        self.asks = asks
        self.pick_args = pick_args

    async def _ask(self, question: DecisionQuestion, text: str, timeout: float):
        session = BrainChatSession.for_role(self.role.name, question.asks, self.folder,
                                       role_spec=self.role, answer_format=question.schema(),
                                       **self.pick_args)
        answer = (await session.once(text, timeout)).answer.get("answer")
        return answer if question.valid(answer) else None

    async def decide(self, text: str, questions: list[DecisionQuestion],
                     timeout: float) -> list[dict]:
        async def one(question: DecisionQuestion) -> dict:
            got = await asyncio.gather(*(self._ask(question, text, timeout)
                                         for _ in range(self.asks)), return_exceptions=True)
            answers = [a for a in got if a is not None and not isinstance(a, BaseException)]
            if not answers:
                raise SessionFailed(f"no ask of {question.id} gave an answer")
            answer, probability = agreement(question, answers)
            return {"id": question.id, "answer": answer, "probability": probability}
        return list(await asyncio.gather(*(one(q) for q in questions)))


# ── ClassificationSession ─────────────────────────────────────────────────

BACKGROUND = "background"


@dataclass(frozen=True)
class Item:
    id: str
    text: str                       # exactly as found
    source: str                     # its file and position


@dataclass(frozen=True)
class ClassQuestion:
    id: str
    asks: str


class ClassificationSession(Protocol):
    """Each item scored against every class: the handoff's questions, plus
    background (designed around CDM Classify)."""

    async def classify(self, items: list[Item], classes: list[ClassQuestion],
                       timeout: float) -> list[dict]: ...


def _check_scores(scores: dict, names: list[str], whose: str) -> dict[str, float]:
    if not isinstance(scores, dict) or set(scores) != set(names):
        raise NotInForm(f"{whose} was not scored against exactly {', '.join(names)}")
    out = {}
    for name in names:
        value = scores[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise NotInForm(f"{whose}'s score for {name} is not between 0 and 1")
        out[name] = float(value)
    return out


class BrainClassificationSession:
    """On the brain providers: one call, the items without knowledge states
    (they do not judge him)."""

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    async def classify(self, items: list[Item], classes: list[ClassQuestion],
                       timeout: float) -> list[dict]:
        names = [c.id for c in classes] + [BACKGROUND]
        form = _object({"scores": _array(_object({
            "item": _TEXT, "scores": _object({n: _SCORE for n in names})}))})
        system, first = role_prompt("classification", {
            "{the items: facts with their file and place, marked parts, each by id}":
                "\n".join(_tag("item", i.text, id=i.id, source=i.source) for i in items),
            "{the handoff's questions}":
                "\n".join(_tag("question", c.asks, id=c.id) for c in classes)})
        session = BrainChatSession.for_role("classification", system, self.folder,
                                       answer_format=form, **self.pick_args)
        answer = (await session.once(first, timeout)).answer
        by_item = {str(s.get("item")): s.get("scores") for s in answer.get("scores") or []
                   if isinstance(s, dict)}
        if set(by_item) != {i.id for i in items}:
            raise SessionFailed("classification did not score exactly the items given")
        return [{"item": i.id, "scores": _check_scores(by_item[i.id], names, i.id)}
                for i in items]


# ── ExtractionSession ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Field:
    name: str
    describes: str
    many: bool = False


class ExtractionSession(Protocol):
    """Values copied from a text, never written (designed around CDM
    Extract; local alternative GLiNER)."""

    async def extract(self, text: str, fields: list[Field], timeout: float) -> list[dict]: ...


class BrainExtractionSession:
    """On the brain providers: a fixed answer format, each value checked by
    the program to occur verbatim at its stated position; one that does
    not is dropped. A field with no value is returned empty. Its context is
    the sentence it sits in, read by the program."""

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    async def extract(self, text: str, fields: list[Field], timeout: float) -> list[dict]:
        form = _object({"values": _array(_object({
            "field": {"type": "string", "enum": [f.name for f in fields]},
            "value": _TEXT, "confidence": _SCORE, "start": _OFFSET, "end": _OFFSET}))})
        system, first = role_prompt("extraction", {
            "{text}": _tag("text", text),
            "{fields for this file's type}":
                "\n".join(_tag("field", f.describes, name=f.name,
                               many="true" if f.many else "false") for f in fields)})
        session = BrainChatSession.for_role("extraction", system, self.folder, answer_format=form,
                                       **self.pick_args)
        answer = (await session.once(first, timeout)).answer
        many = {f.name: f.many for f in fields}
        out: list[dict] = []
        for value in answer.get("values") or []:
            if not isinstance(value, dict) or value.get("field") not in many:
                continue
            start, end, said = value.get("start"), value.get("end"), value.get("value")
            if not (isinstance(start, int) and isinstance(end, int) and isinstance(said, str)
                    and said and text[start:end] == said):
                log.info("extraction: dropped %r for %s: not at its position",
                         said, value.get("field"))
                continue
            if not many[value["field"]] and any(o["field"] == value["field"] for o in out):
                continue
            out.append({"field": value["field"], "value": said,
                        "confidence": float(value.get("confidence") or 0),
                        "position": {"start": start, "end": end},
                        "context": text_parts.sentence_around(text, start, end)})
        found = {o["field"] for o in out}
        out += [{"field": f.name, "value": "", "confidence": 0.0, "position": None,
                 "context": ""} for f in fields if f.name not in found]
        return out


# ── MarkingSession ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Label:
    name: str
    means: str
    example: str = ""


class MarkingSession(Protocol):
    """Marks a text: its sentences and phrases, each scored 0..1 against
    every label (sessions.yaml MarkingSession)."""

    async def mark(self, text: str, labels: list[Label], knowledge: list[Known],
                   timeout: float) -> list[dict]: ...

    async def mark_slides(self, units: list, context: str, labels: list[Label],
                          knowledge: list[Known], timeout: float) -> list[dict]: ...


class BrainMarkingSession:
    """On the brain providers: a text's sentences and phrases (split by
    text_parts), each scored against every label, each label on its own.
    Tim's material comes with its knowledge states, for the labels that
    depend on him.

    In batches (sessions.yaml MarkingSession batches, Tim 2026-11-20: "Yes,
    create smaller batches"): the program splits the parts into batches
    small enough to be answered whole, a sentence never apart from its
    phrases; each call is given the whole file as context and scores only
    its batch, every part against every label. A batch starts at
    `BATCH_PARTS` parts; one not answered whole (not in its form, or not
    scoring exactly its parts) is split in two and each half asked again,
    down to one sentence with its phrases. A script is marked slide by
    slide (`mark_slides`): no batch holds two slides' words."""

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    async def mark(self, text: str, labels: list[Label], knowledge: list[Known],
                   timeout: float) -> list[dict]:
        """A file's prose marked. Its whole text is the context of every
        batch: given once among Tim's material when it is there (the mark
        step gives the file first), else as the text itself."""
        segments = text_parts.split(text)
        context = "" if any(k.text == text for k in knowledge) else text
        scored = await self._batches([_sentences(segments)], context, labels, knowledge, timeout)
        return [_part(s, scored[s.id]) for s in segments]

    async def mark_slides(self, units: list, context: str, labels: list[Label],
                          knowledge: list[Known], timeout: float) -> list[dict]:
        """A script marked slide by slide: `units`, each piece of its words
        with its `slide`, `field` and `text` (package_script.units); the
        whole script, `context`, given with every batch. A part's id and
        position are its slide's and field's: `<slide>/<field>/<part>`, its
        offsets within that field's words."""
        by_slide: dict[str, list[list[text_parts.Segment]]] = {}
        placed: list[tuple[object, text_parts.Segment]] = []
        for unit in units:
            segments = [dataclasses.replace(
                s, id=f"{unit.slide}/{unit.field}/{s.id}",
                within=f"{unit.slide}/{unit.field}/{s.within}" if s.within else None)
                for s in text_parts.split(unit.text)]
            by_slide.setdefault(unit.slide, []).extend(_sentences(segments))
            placed += [(unit, s) for s in segments]
        scored = await self._batches(list(by_slide.values()), context, labels, knowledge,
                                     timeout)
        return [{**_part(s, scored[s.id]), "slide": unit.slide, "field": unit.field}
                for unit, s in placed]

    async def _batches(self, groups: list[list[list[text_parts.Segment]]], context: str,
                       labels: list[Label], knowledge: list[Known],
                       timeout: float) -> dict[str, dict]:
        """Every part's scores: each group (a file, a slide) cut into batches
        of whole sentences, at most `BATCH_PARTS` parts where a sentence
        allows, each asked on its own."""
        scored: dict[str, dict] = {}
        for sentences in groups:
            batch: list[list[text_parts.Segment]] = []
            for sentence in sentences:
                if batch and sum(map(len, batch)) + len(sentence) > BATCH_PARTS:
                    scored.update(await self._whole(batch, context, labels, knowledge, timeout))
                    batch = []
                batch.append(sentence)
            if batch:
                scored.update(await self._whole(batch, context, labels, knowledge, timeout))
        return scored

    async def _whole(self, batch: list[list[text_parts.Segment]], context: str,
                     labels: list[Label], knowledge: list[Known],
                     timeout: float) -> dict[str, dict]:
        """One batch's scores; a batch not answered whole split in two and
        each half asked again."""
        try:
            return await self._score([s for sentence in batch for s in sentence], context,
                                     labels, knowledge, timeout)
        except NotInForm as e:
            if len(batch) == 1:
                raise SessionFailed(f"marking could not answer one sentence's parts whole: "
                                    f"{e}") from e
            half = len(batch) // 2
            log.info("marking: a batch of %d sentences not answered whole (%s); asked again "
                     "in two", len(batch), e)
            return {**await self._whole(batch[:half], context, labels, knowledge, timeout),
                    **await self._whole(batch[half:], context, labels, knowledge, timeout)}

    async def _score(self, segments: list[text_parts.Segment], context: str,
                     labels: list[Label], knowledge: list[Known],
                     timeout: float) -> dict[str, dict]:
        names = [label.name for label in labels]
        form = _object({"scores": _array(_object({
            "id": _TEXT, "scores": _object({n: _SCORE for n in names})}))})
        parts = "\n".join(_tag("part", s.text, id=s.id, kind=s.kind, within=s.within)
                          for s in segments)
        system, first = role_prompt("marking", {
            "{the parts, by id}": (_tag("text", context) + "\n" + parts) if context else parts,
            "{each label: name, what it means, an example}":
                "\n".join(_tag("label", f"{label.means}\nExample: {label.example}"
                               if label.example else label.means, name=label.name)
                          for label in labels),
            "{Tim's material, each item with its knowledge state}":
                "\n".join(_tag("material", k.text, state=k.state, source=k.source or None)
                          for k in knowledge)})
        session = BrainChatSession.for_role("marking", system, self.folder, answer_format=form,
                                            **self.pick_args)
        answer = (await session.once(first, timeout)).answer
        by_id = {str(s.get("id")): s.get("scores") for s in answer.get("scores") or []
                 if isinstance(s, dict)}
        if set(by_id) != {s.id for s in segments}:
            raise NotInForm(f"it scored {len(by_id)} part(s), not exactly the "
                            f"{len(segments)} given")
        return {s.id: _check_scores(by_id[s.id], names, s.id) for s in segments}


# A batch of marking starts at this many parts (sessions.yaml MarkingSession
# batches: "small enough to be answered whole"); one not answered whole is
# halved (BrainMarkingSession).
BATCH_PARTS = 40


def _sentences(segments: list[text_parts.Segment]) -> list[list[text_parts.Segment]]:
    """Each sentence with its phrases (text_parts gives a sentence, then its
    phrases)."""
    out: list[list[text_parts.Segment]] = []
    for s in segments:
        if s.kind == text_parts.SENTENCE or not out:
            out.append([s])
        else:
            out[-1].append(s)
    return out


def _part(s: text_parts.Segment, scores: dict) -> dict:
    return {"id": s.id, "segment": s.text, "kind": s.kind,
            "position": {"start": s.start, "end": s.end}, "within": s.within, "scores": scores}


# ── RetrievalSession ──────────────────────────────────────────────────────

OK, NOT_FOUND = "ok", "not_found"


@dataclass(frozen=True)
class Reference:
    id: str
    text: str                       # a free-text label, as given


def _entry(raw: dict) -> Optional[dict]:
    """A typed entry as the handoff names one, or None when it does not
    name its type's fields."""
    kind = raw.get("type")
    if kind not in evidence_types.NAMED_BY_LABEL:
        return None
    entry = {"type": kind}
    for name in evidence_types.FIELDS[kind]:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            return None
        entry[name] = value.strip()
    entry["why"] = str(raw.get("why") or "").strip()
    return entry


class RetrievalSession(Protocol):
    """Works out what each free-text reference names, as typed entries or
    not found (sessions.yaml RetrievalSession)."""

    async def resolve(self, references: list[Reference], timeout: float) -> list[dict]: ...


class BrainRetrievalSession:
    """On the brain providers: works out what each free-text reference names (sessions.yaml
    RetrievalSession): a ChatSession on the retrieval role, its read-only
    search tools only; it never fetches, copies, summarises or judges. The
    program fetches what each entry names (gather)."""

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    async def resolve(self, references: list[Reference], timeout: float) -> list[dict]:
        nullable = {"type": ["string", "null"]}
        form = _object({"references": _array(_object({
            "id": _TEXT, "resolved": {"type": "string", "enum": [OK, NOT_FOUND]},
            "entries": _array(_object({
                "type": {"type": "string", "enum": list(evidence_types.NAMED_BY_LABEL)},
                **{name: nullable for name in evidence_types.ALL_FIELDS
                   if name not in ("text",)},
                "why": _TEXT})),
            "searched": _TEXT}))})
        system, first = role_prompt("retrieval", {
            "{list}": "\n".join(_tag("reference", r.text, id=r.id) for r in references)})
        session = BrainChatSession.for_role("retrieval", system, self.folder, answer_format=form,
                                       **self.pick_args)
        answer = (await session.once(first, timeout)).answer
        by_id = {str(r.get("id")): r for r in answer.get("references") or []
                 if isinstance(r, dict)}
        out = []
        for ref in references:
            said = by_id.get(ref.id) or {}
            entries = [e for e in (_entry(raw) for raw in said.get("entries") or []
                                   if isinstance(raw, dict)) if e]
            resolved = OK if said.get("resolved") == OK and entries else NOT_FOUND
            out.append({"id": ref.id, "resolved": resolved,
                        "entries": entries if resolved == OK else [],
                        "searched": str(said.get("searched") or "")})
        return out


# ── SummarizationSession ──────────────────────────────────────────────────

SUMMARIZATION = "summarization"


@dataclass(frozen=True)
class Source:
    id: str                         # the query, or the file's path
    text: str                       # what was found: the hits' words, each with its path


@dataclass(frozen=True)
class Summary:
    source_id: str
    text: str
    cites: list[str]                # the hit paths the summary rests on


def source_text(hits: list[tuple[str, str]]) -> str:
    """A source's text: each hit's words with its path, (path, words)."""
    return "\n".join(_tag("hit", words, path=path) for path, words in hits)


_HIT_PATH = re.compile(r"<hit path=(\"[^\"]*\"|'[^']*')>")


def source_paths(sources: list[Source]) -> set[str]:
    """Every path the sources name: their ids and their hits' paths."""
    paths = {s.id for s in sources}
    for source in sources:
        paths |= {unescape(m.group(1)[1:-1], {"&quot;": '"', "&apos;": "'"})
                  for m in _HIT_PATH.finditer(source.text)}
    return paths


def cited_in_sources(summaries: list[Summary], sources: list[Source]) -> list[Summary]:
    """The summaries the program keeps: one citing a path that is not in its
    source is rejected (sessions.yaml SummarizationSession rules; the
    prompt's "Cite only paths that appear in that source")."""
    by_id = {s.id: source_paths([s]) for s in sources}
    kept = []
    for summary in summaries:
        paths = by_id.get(summary.source_id, set())
        stray = [c for c in summary.cites if c not in paths]
        if stray:
            log.info("summarization: rejected the summary of %s: it cites %s, not in its "
                     "sources", summary.source_id, ", ".join(stray))
            continue
        kept.append(summary)
    return kept


class SummarizationSession(Protocol):
    """Summarises what was found for a purpose, each summary citing the hit
    paths it rests on (sessions.yaml SummarizationSession, approved
    2026-11-20)."""

    def summarize(self, sources: list[Source], purpose: str) -> list[Summary]: ...


class BrainSummarizationSession:
    """On the brain providers: tier 3 (Sonnet / Terra), one call, no tools, a
    fixed answer format; a summary citing a path not in its source is
    rejected by the program (`cited_in_sources`). Its prompt is
    role-prompts.md Summarization, its role tier 3, effort low and its own
    engines.json entry, `summarization` (Tim, 2026-11-20). Without either
    it cannot be started and says so (`SessionFailed`)."""

    FORM = _object({"summaries": _array(_object({
        "source_id": _TEXT, "text": _TEXT, "cites": _array(_TEXT)}))})

    def __init__(self, folder: Path, timeout: Optional[float] = None, **pick_args):
        missing = [what for what, there in (
            ("an approved prompt (role-prompts.md)", SUMMARIZATION in role_prompts.PROMPTS),
            ("a role with its effort and engines.json entry (sessions.yaml role_tiers_v1, "
             "effort_v1)", SUMMARIZATION in roles.ROLES)) if not there]
        if missing:
            raise SessionFailed("the SummarizationSession cannot be started: it has no "
                                + " and no ".join(missing))
        self.folder = Path(folder)
        self.timeout = timeout
        self.pick_args = pick_args

    def summarize(self, sources: list[Source], purpose: str) -> list[Summary]:
        if not sources:
            return []
        system, first = role_prompt(SUMMARIZATION, {
            "{purpose}": purpose,
            "{sources: each id, then its hits with their paths}":
                "\n".join(_tag("source", s.text, id=s.id) for s in sources)})
        session = BrainChatSession.for_role(SUMMARIZATION, system, self.folder,
                                            answer_format=self.FORM, **self.pick_args)
        answer = asyncio.run(session.once(first, self.timeout)).answer or {}
        said = [Summary(str(s.get("source_id") or ""), str(s.get("text") or ""),
                        [str(c) for c in s.get("cites") or []])
                for s in answer.get("summaries") or [] if isinstance(s, dict)]
        return cited_in_sources(said, sources)


# ── EditingSession ────────────────────────────────────────────────────────

EDIT_FORM = _object({
    "edits": _array(_object({"start": _OFFSET, "end": _OFFSET, "old": _TEXT, "new": _TEXT,
                             "reason": _TEXT, "source": _TEXT})),
    "sent_back": _array(_object({"start": _OFFSET, "end": _OFFSET, "old": _TEXT,
                                 "needs": _TEXT}))})


@dataclass(frozen=True)
class EditLabel:
    name: str
    means: str
    action: str


def editor_prompt(labels: list[EditLabel]) -> str:
    """The editor's approved prompt, with each label's meaning and action."""
    system, _ = role_prompt("editor", {
        "{each label with its meaning and its action}":
            "\n".join(_tag("label", f"{label.means}\nAction: {label.action}", name=label.name)
                      for label in labels)})
    return system


def apply_edits(text: str, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """The edits applied by the program, latest offset first: (the text,
    the edits applied, the edits refused because their old text is not at
    their offsets or they overlap one applied)."""
    applied, refused = [], []
    taken: list[tuple[int, int]] = []
    for edit in sorted(edits, key=lambda e: e.get("start", -1), reverse=True):
        start, end, old = edit.get("start"), edit.get("end"), edit.get("old")
        if (not isinstance(start, int) or not isinstance(end, int) or not isinstance(old, str)
                or not 0 <= start <= end <= len(text) or text[start:end] != old
                or any(start < b and end > a for a, b in taken)):
            refused.append(edit)
            continue
        # Applied from the end back, so every offset still to come is the
        # original text's; `taken` is in those offsets too.
        text = text[:start] + str(edit.get("new") or "") + text[end:]
        taken.append((start, end))
        applied.append(edit)
    return text, applied, refused


class EditingSession(Protocol):
    """The one editor: edits a text guided by its marked parts and the
    information, the program applying them (sessions.yaml
    EditingSession)."""

    async def edit(self, text: str, marked: list[dict], information: list[Known],
                   timeout: float, source: str = "", conversation: str = "",
                   call_vote: bool = False, prep: str = "",
                   apply: Optional[Callable[[list[dict]], tuple[str, list, list]]] = None
                   ) -> dict: ...


class BrainEditingSession:
    """On the brain providers: the one editor, a ChatSession on the editor
    role, given the script, the same script marked part by part,
    and the information to take facts from, each item marked with its
    knowledge state. It decides part by part what edit each needs; the
    program applies the edits and refuses any whose old text is not at its
    offsets."""

    def __init__(self, chat: ChatSession):
        self.chat = chat

    async def edit(self, text: str, marked: list[dict], information: list[Known],
                   timeout: float, source: str = "", conversation: str = "",
                   call_vote: bool = False, prep: str = "",
                   apply: Optional[Callable[[list[dict]], tuple[str, list, list]]] = None
                   ) -> dict:
        """`conversation`: what the others said since the editor's last turn,
        in a swarm; `call_vote`: its answer may call votes, as any
        participant's may; `prep`: the author's prep (sessions.yaml
        EditingSession information: "the package's evidence search and the
        prep"); `apply`: a structured script's own application of the edits
        (package_script.apply_edits), each edit then naming its slide and
        field beside its offsets within that field's words, and the marked
        parts given slide by slide."""
        material = "\n\n".join(t for t in (
            conversation,
            _tag("script", text, file=source or None),
            marked_slides(marked) if apply else marked_parts(marked),
            _tag("prep", prep) if prep else "",
            "\n".join(_tag("information", k.text, state=k.state, source=k.source or None)
                      for k in information)) if t)
        form = located_form(EDIT_FORM) if apply else EDIT_FORM
        if call_vote:
            form = _object({**form["properties"], "call_vote": CALL_VOTE})
        reply = await self.chat.ask(material, timeout, answer_format=form)
        answer = reply.answer or {}
        edits = [e for e in answer.get("edits") or [] if isinstance(e, dict)]
        new_text, applied, refused = (apply or (lambda e: apply_edits(text, e)))(edits)
        return {"text": new_text, "applied": applied, "refused": refused,
                "sent_back": [s for s in answer.get("sent_back") or [] if isinstance(s, dict)],
                "call_vote": answer.get("call_vote") or [], "reply": reply.text}


def located_form(form: dict) -> dict:
    """The editor's form for a structured script: each edit and each part
    sent back also names its slide and its field, its offsets within that
    field's words."""
    where = {"slide": {"type": "string", "description": "The slide's id."},
             "field": {"type": "string",
                       "description": "The field within the slide, as the marked part names "
                                      "it (title, on_screen[0].text, narration, ...)."}}
    return _object({key: _array(_object({**where, **value["items"]["properties"]}))
                    for key, value in form["properties"].items()})
