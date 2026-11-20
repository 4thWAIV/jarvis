"""
sessions.py — the sessions a package is made with (sessions.yaml, approved
by Tim 2026-11-18 and 2026-11-19).

Composition, each type with its own interface (his word, 2026-11-18):

- `ChatSession`: one role's conversation with a model, on the brain's own
  transport (claude_provider / codex_provider: one transport per brain,
  shared by the voice brain and the roles), launched on JARVIS's own
  settings with only the role's tools (roles.py). One-shot or long-lived.
- `SwarmSession`: several long-lived ChatSessions in one conversation,
  every message sent to the others word for word; votes and questions.
- `DecisionSession`, `ClassificationSession`, `ExtractionSession`: designed
  around Jev and CDM; their Brain implementations run on the brain
  providers (the Team ones, on the team server's models, come later).
- `MarkingSession`: the text split by a program (text_parts.py), then
  every part scored against every label, one call a file.
- `RetrievalSession` and `EditingSession`: a ChatSession with its inputs
  structured.

Every model is picked by model_tiers.pick (the role's tier on the brain
engines.json gives its kind) and swept when it is not available; the
step's log says which model each session took. A session that cannot give
an answer raises `SessionFailed`, which is the step's Error.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol
from xml.sax.saxutils import quoteattr

from jarvis import (brain_provider, evidence_types, model_tiers, private_files, role_mcp,
                    role_prompts, roles, settings_service, text_parts)
from jarvis.brain import BrainConfig

log = logging.getLogger("jarvis.sessions")


class SessionFailed(RuntimeError):
    """A session could not give an answer: the step goes to Error with it."""


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


# A model the provider refuses by name when the session starts is not
# available: the sweep goes on to the next.
def model_refused(error: str) -> bool:
    words = (error or "").lower()
    return "model" in words and any(w in words for w in (
        "not supported", "does not exist", "not available", "not found",
        "issue with the selected model", "may not exist"))


class ChatSession:
    """One role's conversation with a model (sessions.yaml ChatSession).

    In: the role (its tier, effort, tools and lifetime, roles.py), its
    prompt as the system prompt, the run folder it works in and the folders
    it may read, an answer format where the answer has a fixed form, and
    the turns, in order. Out: each turn's reply, and the session's id, so a
    long-lived session is resumed after a restart, never started fresh.
    """

    def __init__(self, role: str, prompt: str, folder: Path, *,
                 pick: model_tiers.Pick, reads: tuple[Path, ...] = (),
                 answer_format: Optional[dict] = None, resume: Optional[str] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 role_spec: Optional[roles.Role] = None):
        self.role = role_spec or roles.role(role)
        self.prompt = prompt
        self.folder = Path(folder)
        self.pick = pick
        self.reads = tuple(Path(r) for r in reads)
        self.answer_format = answer_format
        self.session: Optional[str] = resume
        self.model: Optional[str] = None
        self.info: Optional[brain_provider.SessionInfo] = None
        self._on_log = on_log or (lambda line: log.info(line))
        self._child: Optional[brain_provider.ProviderSession] = None
        self._parts: list[str] = []
        self._done: Optional[asyncio.Future] = None

    @classmethod
    def for_role(cls, role: str, prompt: str, folder: Path, *, requester: Optional[str] = None,
                 engine: Optional[str] = None, model: Optional[str] = None,
                 author_engine: Optional[str] = None, **kwargs) -> "ChatSession":
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
        if not self.role.tools:
            return None
        path = self.folder / ".jarvis-role" / f"{self.role.name}-mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"mcpServers": {roles.SERVER_NAME: role_mcp.entry(self.role.name)}}
        private_files.replace(path, json.dumps(body, indent=2).encode("utf-8"))
        return path

    def _config(self, model: str) -> BrainConfig:
        settings_service.load()     # his saved program paths, in any process
        new_id = None
        if not self.one_shot and not self.session and self.pick.engine == "claude":
            new_id = str(uuid.uuid4())
        launch = roles.Launch(role=self.role, mcp_config=self._mcp_config(), reads=self.reads,
                              answer_format=self.answer_format if self.one_shot else None,
                              session_id=new_id)
        if new_id:
            self.session = new_id
        return BrainConfig(home=self.folder, provider=brain_provider.ENGINES[self.pick.engine],
                           model=model, effort=self.role.effort, resume=self.session if
                           not new_id else None, role=launch,
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
        try:
            await self._child.open(self.prompt)
        except OSError as e:
            raise SessionFailed(str(e)) from e
        self.model = model

    async def open(self) -> None:
        """Started on the first model of the sweep the provider accepts."""
        last = ""
        for model in self.pick.tried or [self.pick.model]:
            try:
                await self._open_one(model)
            except SessionFailed as e:
                last = str(e)
                if model_refused(last):
                    self._on_log(f"{self.role.name}: {self.pick.engine} refused {model}: {last}")
                    continue
                raise
            self._on_log(f"{self.role.name}: {self.pick.engine} {model} "
                         f"(tier {self.pick.tier}, {self.pick.why}, effort {self.role.effort})")
            return
        raise model_tiers.NoModel(f"{self.role.name}: no {self.pick.engine} model in tier "
                                  f"{self.pick.tier} or above was accepted ({last})")

    async def ask(self, text: str, timeout: float,
                  answer_format: Optional[dict] = None) -> Reply:
        """One turn: its reply, and its JSON answer where it has a form (the
        one-shot's own, or this turn's)."""
        if self._child is None:
            raise SessionFailed(f"the {self.role.name} session is not open")
        form = answer_format if answer_format is not None else self.answer_format
        if answer_format is not None and self.pick.engine == "claude":
            # Claude takes a form per process, not per turn: this turn's is
            # given in the message, and checked here (participants.md 5).
            text = f"{text}\n\n{SCHEMA_GIVEN}\n{json.dumps(answer_format)}"
        self._parts = []
        self._done = asyncio.get_running_loop().create_future()
        try:
            async with asyncio.timeout(timeout):
                await self._child.send(text, answer_format=answer_format)
                said = await self._done
        except TimeoutError as e:
            raise SessionFailed(f"the {self.role.name} session did not answer within "
                                f"{timeout:.0f} s") from e
        except OSError as e:
            raise SessionFailed(str(e)) from e
        answer = json_answer(said) if form is not None else None
        if form is not None and not isinstance(answer, dict):
            raise SessionFailed(f"the {self.role.name} session's answer was not in its form")
        return Reply(text=said, answer=answer)

    async def close(self) -> None:
        if self._done is not None and not self._done.done():
            self._done.cancel()
        if self._child is not None:
            await self._child.close(5)

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


# ── SwarmSession ──────────────────────────────────────────────────────────

OPEN, PROVEN, FAILED = "open", "proven", "failed"
YES, NO = "yes", "no"
ROUNDS_MAX = 3          # after three rounds without passing, nothing is built
VOTE_FORM = _object({"question": _TEXT, "vote": {"type": "string", "enum": [YES, NO]},
                     "reason": _TEXT})


@dataclass
class Question:
    id: str
    asks: str
    author_votes: bool = False      # the author never votes on whether its own script passes


@dataclass
class Vote:
    question: str
    participant: str
    vote: str
    reason: str


class SwarmSession:
    """Several long-lived ChatSessions in one conversation (sessions.yaml
    SwarmSession; workflow.yaml swarm). The program runs the turns and
    keeps the log word for word, never summarising or rewriting: each
    participant is sent every new message of the others, verbatim, with
    who said it. A vote is a turn in each voter's own session, yes or no
    with a reason; a majority of yes proves a question, anything else
    (a majority of no, a tie) fails it; a question reopens when the script
    changes what it was about. The Theory of Mind reader never votes, and
    the author not on whether its own script passes.
    """

    def __init__(self, participants: dict[str, ChatSession], questions: list[Question],
                 background: Optional[dict] = None, *, author: str = "author",
                 non_voters: tuple[str, ...] = ("theory_of_mind_reader",)):
        self.participants = participants
        self.background = background or {}
        self.questions = {q.id: q for q in questions}
        self.state = {q.id: OPEN for q in questions}
        self.closed_by: dict[str, list[Vote]] = {}
        self.votes: list[Vote] = []
        self.conversation: list[dict] = []
        self.rounds = 0
        self.author = author
        self.non_voters = non_voters
        self._seen = {name: 0 for name in participants}

    def _new_for(self, name: str) -> str:
        """Every message of the others since this participant last spoke,
        word for word, each with who said it."""
        new = [m for m in self.conversation[self._seen[name]:] if m["from"] != name]
        self._seen[name] = len(self.conversation)
        return "\n\n".join(f"{m['from']}:\n{m['text']}" for m in new)

    def say(self, name: str, text: str) -> None:
        """A message into the conversation, as it was written (the
        program's own, or a participant's)."""
        self.conversation.append({"from": name, "text": text})

    async def turn(self, name: str, timeout: float, add: str = "") -> str:
        """One participant's turn: it is sent what the others said since
        its last, and `add` (the program's own message to it); its reply
        goes into the conversation."""
        sent = "\n\n".join(t for t in (self._new_for(name), add) if t)
        reply = await self.participants[name].ask(sent, timeout)
        self.say(name, reply.text)
        self._seen[name] = len(self.conversation)
        return reply.text

    def voters(self, question: str) -> list[str]:
        q = self.questions[question]
        return [n for n in self.participants
                if n not in self.non_voters and (n != self.author or q.author_votes)]

    async def vote(self, question: str, timeout: float) -> str:
        """Every voter votes on one question, each in its own session; the
        question's state after the count."""
        q = self.questions[question]
        cast: list[Vote] = []
        for name in self.voters(question):
            sent = "\n\n".join(t for t in (self._new_for(name),
                                           f"A vote is called on {q.id}: {q.asks}") if t)
            reply = await self.participants[name].ask(sent, timeout, answer_format=VOTE_FORM)
            answer = reply.answer or {}
            said = str(answer.get("vote") or "").lower()
            if said not in (YES, NO) or not str(answer.get("reason") or "").strip():
                raise SessionFailed(f"{name}'s vote on {q.id} was not yes or no with a reason")
            vote = Vote(q.id, name, said, str(answer["reason"]).strip())
            cast.append(vote)
            self.say(name, f"Vote on {q.id}: {vote.vote}. {vote.reason}")
            self._seen[name] = len(self.conversation)
        self.votes += cast
        yes = sum(1 for v in cast if v.vote == YES)
        self.state[q.id] = PROVEN if yes > len(cast) - yes else FAILED
        self.closed_by[q.id] = cast
        return self.state[q.id]

    def reopen(self, question: str) -> None:
        """The script changed what the question was about."""
        self.state[question] = OPEN
        self.closed_by.pop(question, None)

    async def vote_open(self, timeout: float) -> None:
        """The program calls a vote on every open question (the end of a
        round)."""
        for qid, state in list(self.state.items()):
            if state == OPEN:
                await self.vote(qid, timeout)

    @property
    def passed(self) -> bool:
        return all(s == PROVEN for s in self.state.values())

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
        findings = [{"question": qid, "state": state,
                     "against": [dataclasses.asdict(v) for v in self.closed_by.get(qid, [])
                                 if v.vote == NO]}
                    for qid, state in self.state.items() if state != PROVEN]
        return {"result": "passed" if self.passed else "not passed",
                "questions": [{"id": qid, "state": state,
                               "closed_by": [dataclasses.asdict(v)
                                             for v in self.closed_by.get(qid, [])]}
                              for qid, state in self.state.items()],
                "votes": [dataclasses.asdict(v) for v in self.votes],
                "conversation": list(self.conversation),
                "findings": findings, "rounds": self.rounds}


# ── DecisionSession ───────────────────────────────────────────────────────

YES_NO, CHOICE, SCALE = "yes_no", "choice", "scale"
# A Brain decision asks each question this many times (today's
# briefing_prep.decide).
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
        self.role = dataclasses.replace(roles.role(role), lifetime=roles.ONE_SHOT, builtins=(),
                                        tools=(), commands=(), writes=False)
        self.folder = Path(folder)
        self.asks = asks
        self.pick_args = pick_args

    async def _ask(self, question: DecisionQuestion, text: str, timeout: float):
        session = ChatSession.for_role(self.role.name, question.asks, self.folder,
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
        raise SessionFailed(f"{whose} was not scored against exactly {', '.join(names)}")
    out = {}
    for name in names:
        value = scores[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise SessionFailed(f"{whose}'s score for {name} is not between 0 and 1")
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
        session = ChatSession.for_role("classification", system, self.folder,
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
        session = ChatSession.for_role("extraction", system, self.folder, answer_format=form,
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


@dataclass(frozen=True)
class Known:
    """One item of Tim's material with its knowledge state."""
    text: str
    state: str                      # knows, had_access, probably_does_not_know
    source: str = ""


class MarkingSession:
    """A text's sentences and phrases (split by text_parts), each scored
    against every label, each label on its own; one call a file. Tim's
    material comes with its knowledge states, for the labels that depend on
    him."""

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    async def mark(self, text: str, labels: list[Label], knowledge: list[Known],
                   timeout: float) -> list[dict]:
        segments = text_parts.split(text)
        if not segments:
            return []
        names = [label.name for label in labels]
        form = _object({"scores": _array(_object({
            "id": _TEXT, "scores": _object({n: _SCORE for n in names})}))})
        system, first = role_prompt("marking", {
            "{the parts, by id}": "\n".join(_tag("part", s.text, id=s.id, kind=s.kind,
                                                 within=s.within) for s in segments),
            "{each label: name, what it means, an example}":
                "\n".join(_tag("label", f"{label.means}\nExample: {label.example}"
                               if label.example else label.means, name=label.name)
                          for label in labels),
            "{Tim's material, each item with its knowledge state}":
                "\n".join(_tag("material", k.text, state=k.state, source=k.source or None)
                          for k in knowledge)})
        session = ChatSession.for_role("marking", system, self.folder, answer_format=form,
                                       **self.pick_args)
        answer = (await session.once(first, timeout)).answer
        by_id = {str(s.get("id")): s.get("scores") for s in answer.get("scores") or []
                 if isinstance(s, dict)}
        if set(by_id) != {s.id for s in segments}:
            raise SessionFailed("marking did not score exactly the parts given")
        return [{"id": s.id, "segment": s.text, "kind": s.kind,
                 "position": {"start": s.start, "end": s.end}, "within": s.within,
                 "scores": _check_scores(by_id[s.id], names, s.id)} for s in segments]


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


class RetrievalSession:
    """Works out what each free-text reference names (sessions.yaml
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
        session = ChatSession.for_role("retrieval", system, self.folder, answer_format=form,
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


class EditingSession:
    """The one editor (sessions.yaml EditingSession): a ChatSession on the
    editor role, given the script, the same script marked part by part,
    and the information to take facts from, each item marked with its
    knowledge state. It decides part by part what edit each needs; the
    program applies the edits and refuses any whose old text is not at its
    offsets."""

    def __init__(self, chat: ChatSession):
        self.chat = chat

    async def edit(self, text: str, marked: list[dict], information: list[Known],
                   timeout: float, source: str = "") -> dict:
        material = "\n\n".join((
            _tag("script", text, file=source or None),
            "\n".join(_tag("part", m["segment"], id=m["id"], kind=m["kind"],
                           start=m["position"]["start"], end=m["position"]["end"],
                           scores=json.dumps(m["scores"], sort_keys=True)) for m in marked),
            "\n".join(_tag("information", k.text, state=k.state, source=k.source or None)
                      for k in information)))
        reply = await self.chat.ask(material, timeout, answer_format=EDIT_FORM)
        answer = reply.answer or {}
        edits = [e for e in answer.get("edits") or [] if isinstance(e, dict)]
        new_text, applied, refused = apply_edits(text, edits)
        return {"text": new_text, "applied": applied, "refused": refused,
                "sent_back": [s for s in answer.get("sent_back") or [] if isinstance(s, dict)],
                "reply": reply.text}
