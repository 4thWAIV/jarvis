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
  one conversation, every message sent to the others word for word,
  turns one at a time or in parallel; each owner signs its concerns off in
  its own turn (the cooperative swarm), and what only the sender knows is
  asked of it.
- `DecisionSession`, `ClassificationSession`, `ExtractionSession`: designed
  around Jev and CDM.
- `MarkingSession` (`BrainMarkingSession`): the texts split by a program
  (text_parts.py), then every part scored against every label, the texts
  packed into pages that fit the model (pages.py), several files to a
  page, each given whole beside its parts, the pages asked in parallel and
  answered sparsely; a script slide by slide. `ClassificationSession` the
  same way.
- `RetrievalSession` (`BrainRetrievalSession`): a ChatSession with its
  inputs structured. The editor works in the swarm as an owner of the
  language, through the swarm's own turns.
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
import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, TypeVar
from xml.sax.saxutils import quoteattr, unescape

from jarvis import (brain_provider, evidence_types, model_tiers, pages, private_files,
                    role_mcp, role_prompts, roles, run_executor, run_report, settings_service,
                    text_parts)
from jarvis.brain import BrainConfig

log = logging.getLogger("jarvis.sessions")

T = TypeVar("T")


class SessionFailed(RuntimeError):
    """A session could not give an answer: the step goes to Error with it."""


class NotInForm(SessionFailed):
    """A session answered, but not in the form its call gave."""


class TimeLimit(SessionFailed):
    """A session that did not start or answer within the run's bounds
    (Settings, Agents: run_timeout_sec, run_idle_sec)."""


class RateLimited(SessionFailed, pages.RateLimited):
    """A turn the brain refused as rate-limited: `resets_at`, when it says
    it takes turns again, or None."""

    def __init__(self, why: str, resets_at: Optional[float] = None):
        SessionFailed.__init__(self, why)
        self.resets_at = resets_at


def rate_limited(error: str) -> bool:
    """A turn's error in the brain's words that says it was refused for a
    rate limit (Codex: "rate limit", "Too Many Requests", 429)."""
    words = (error or "").lower()
    return any(w in words for w in ("rate limit", "rate-limit", "ratelimit",
                                    "too many requests", " 429"))


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
        self._tokens: tuple[int, int] = (0, 0)
        self._blocked_until: Optional[float] = None   # the brain said it refuses turns
        self._blocked = False
        # The last turn's time and tokens (its context window then, its
        # output), for the swarm's record of each turn.
        self.last_turn: Optional[dict] = None

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
        elif isinstance(event, brain_provider.UsageSeen):
            self._blocked, self._blocked_until = event.blocked, event.resets_at
        elif isinstance(event, brain_provider.TurnEnded):
            self._tokens = (event.context_tokens, event.output_tokens)
            if self._done is not None and not self._done.done():
                if event.error and (self._blocked or rate_limited(event.error)):
                    self._done.set_exception(RateLimited(
                        f"the {self.role.name} session's brain refused the turn for a rate "
                        f"limit: {event.error}", self._blocked_until if self._blocked else None))
                elif event.error:
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
        run_report.app_server(self.pick.engine)
        wall, _ = run_executor.bounds()
        try:
            async with asyncio.timeout(wall):
                await self._child.open(self.prompt)
        except TimeoutError as e:
            raise TimeLimit(f"the {self.role.name} session did not start within "
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
                        raise TimeLimit(
                            f"the {self.role.name} session's brain wrote nothing for "
                            f"{idle:.0f} s (Settings, Agents: run_idle_sec)")
                    try:
                        return await asyncio.wait_for(asyncio.shield(self._done), left)
                    except TimeoutError:
                        continue        # heard since? measured again
        except TimeoutError as e:
            raise TimeLimit(f"the {self.role.name} session did not answer within "
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
        started = time.monotonic()
        while True:
            began = time.monotonic()
            try:
                said = await self._turn(text, timeout, answer_format)
                break
            except SessionFailed as e:
                self._record(began, _outcome(e) if self._answered or not model_refused(str(e))
                             else run_report.REFUSED)
                if self._answered or not model_refused(str(e)):
                    raise
                # Refused on its first turn: the sweep's next model, the
                # turn sent again on it.
                self._refused(self.model or "", str(e))
                await self._close_child()
                await self._open_next(str(e))
        self._answered = True
        self.last_turn = {"engine": self.pick.engine, "model": self.model,
                          "seconds": round(time.monotonic() - started, 1),
                          "context_tokens": self._tokens[0], "output_tokens": self._tokens[1]}
        used = getattr(self._child, "turn_usage", None)
        if isinstance(used, dict):
            # The turn's whole use where the brain reports it (Codex: every
            # request of the turn, cached input apart).
            self.last_turn["turn_tokens"] = dict(used)
        self._record(began, run_report.ANSWERED)
        self._on_log(f"{self.role.name}: turn answered in {self.last_turn['seconds']} s, "
                     f"context {self._tokens[0]} tokens, output {self._tokens[1]} tokens")
        answer = json_answer(said) if form is not None else None
        if form is not None and not isinstance(answer, dict):
            if not strict:
                return Reply(text=said, answer=None)
            raise NotInForm(f"the {self.role.name} session's answer was not in its form")
        return Reply(text=said, answer=answer)

    def _record(self, began: float, outcome: str) -> None:
        """This call in the run's report (run_report.call): its role, brain,
        model, tier, effort, seconds, tokens in, cached and out, and how it
        ended. A turn that did not answer has no tokens of its own."""
        used = getattr(self._child, "turn_usage", None) if outcome == run_report.ANSWERED \
            else None
        tin, out = self._tokens if outcome == run_report.ANSWERED else (0, 0)
        cached = None
        if isinstance(used, dict):
            tin = int(used.get("inputTokens") or 0)
            cached = int(used.get("cachedInputTokens") or 0)
            out = int(used.get("outputTokens") or 0)
        run_report.call({"role": self.role.name, "engine": self.pick.engine,
                         "model": self.model, "tier": self.pick.tier,
                         "effort": self.role.effort,
                         "seconds": round(time.monotonic() - began, 1),
                         "tokens_in": tin, "cached": cached, "tokens_out": out,
                         "outcome": outcome, "at": time.time()})

    async def _close_child(self) -> None:
        if self._done is not None and not self._done.done():
            self._done.cancel()
        if self._child is not None:
            child, self._child = self._child, None
            await child.close(5)

    async def close(self) -> None:
        await self._close_child()

    async def fresh(self) -> None:
        """A new conversation for the next item, with nothing of the last:
        on the same brain process where the brain allows (Codex: a new
        thread on the same app server), else the session started again on
        the model it took (Claude's one-shot process answers one turn)."""
        if self._child is None:
            await self.open()
            return
        new = getattr(self._child, "new_thread", None)
        if new is not None:
            try:
                await new()
                return
            except OSError as e:
                self._on_log(f"{self.role.name}: no new thread on the same app server ({e}); "
                             "started again")
        await self._close_child()
        await self._open_one(self.model or self._sweep[max(self._at, 0)])

    async def once(self, text: str, timeout: float) -> Reply:
        """A one-shot role: open, one item in, one answer out, closed."""
        try:
            await self.open()
            return await self.ask(text, timeout)
        finally:
            await self.close()


def _outcome(e: SessionFailed) -> str:
    """How a call that did not answer ended, for the run's report."""
    if isinstance(e, RateLimited):
        return run_report.RATE_LIMITED
    if isinstance(e, TimeLimit):
        return run_report.TIME_LIMIT
    return run_report.FAILED


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
# tim_knowledge who_gets_what, approved 2026-11-19, carried to the
# cooperative swarm's roles, owner-prompts.md): Tim's reader knows and had
# access only, kept apart (the cares judge's and the Theory of Mind
# reader's); the author, the editor, the context owner and the structure
# and decision owner everything, each item marked with its state (the
# background and decision ready judges').
SHARES: dict[str, tuple[str, ...]] = {
    "author": STATES,
    "editor": STATES,
    "context_owner": STATES,
    "structure_owner": STATES,
    "tims_reader": (KNOWS, HAD_ACCESS),
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

# A concern's sign-off (owner-prompts.md, the shared part's last paragraph):
# resolved, when its owner can answer yes, with one sentence saying why,
# quoting the briefing; or not yet, naming exactly what is left and the fix
# it suggests for each.
OPEN, RESOLVED, NOT_YET = "open", "resolved", "not_yet"
SIGN_STATES = (RESOLVED, NOT_YET)
ROUNDS_MAX = 2          # at most two fix passes, then what is left goes to the sender

LEFT = _object({
    "what": {"type": "string", "description": "Exactly what is left."},
    "fix": {"type": "string", "description": "The fix you suggest for it."},
    "evidence_missing": {"type": "string",
                         "description": "The evidence it needs that is not in the material, "
                                        "for the author to research. Empty when none."}})


def signoff_form(concerns: list[str]) -> dict:
    """A sign-off on each of these concerns, in the turn's answer."""
    return _array(_object({
        "concern": {"type": "string", "enum": list(concerns)},
        "state": {"type": "string", "enum": list(SIGN_STATES)},
        "reason": {"type": "string",
                   "description": "resolved: one sentence saying why, quoting the briefing. "
                                  "not_yet: what is left, in a sentence."},
        "left": _array(LEFT)}))


ASK_SENDER = _array({"type": "string",
                     "description": "A question only the sender can answer."})


def owner_form(concerns: list[str], edit: dict) -> dict:
    """An owner's turn (owner-prompts.md, "How you take part"): what it says,
    its edits (`edit`, the form of one edit of what is being worked on), the
    questions only the sender can answer, and its sign-off on each of its
    concerns, all in the one turn."""
    return _object({"message": {"type": "string",
                                "description": "What you say in the conversation."},
                    "edits": _array(edit), "ask_sender": ASK_SENDER,
                    "signoff": signoff_form(concerns)})


@dataclass
class Concern:
    id: str
    asks: str                       # its sign-off question, word for word
    owner: str


@dataclass
class SignOff:
    concern: str
    owner: str
    state: str
    reason: str
    left: list = field(default_factory=list)    # each {"what", "fix", "evidence_missing"}
    stage: str = ""                 # where it was given (outline, pass-1, close-1, ...)


class SwarmSession(Protocol):
    """Several long-lived ChatSessions working together in one conversation
    (sessions.yaml SwarmSession), cooperatively (construction-drafts.md
    "Cooperative swarm", Tim 2026-11-21). In: the participants, the
    material and the concerns each owns. Out: the conversation word for
    word, each concern's sign-offs, what the sender is asked, and the
    result (`result`)."""

    participants: dict[str, ChatSession]

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def say(self, name: str, text: str) -> None: ...

    async def answer(self, name: str, timeout: float, add: str, form: dict) -> dict: ...

    async def together(self, turns: dict[str, tuple[str, dict]], timeout: float
                       ) -> dict[str, dict]: ...

    def result(self) -> dict: ...

    def saved(self) -> dict: ...

    def restore(self, saved: dict) -> None: ...


# The program's own turns in the conversation (what does not check or build,
# the edits applied and refused, the list of what is left).
PROGRAM = "jarvis"


class Script(Protocol):
    """What a swarm writes in a structured form (package_script: the outline,
    outline.yaml, and the script, script.yaml)."""

    name: str                       # its file's name
    key: str                        # what it is called in the author's answer
    version: str                    # its format
    form: dict                      # what the author answers beside its message
    edit_form: dict                 # one edit of it, as an owner gives it

    def take(self, answer: dict) -> list[str]:
        """What the author's answer holds, checked and, when it checks,
        written; every problem, empty when it checks."""

    def text(self) -> str:
        """As written now."""

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        """Edits applied by the program and written: (its text, the edits
        applied, the edits refused)."""


# A participant's first message, given on its first turn: its approved
# prompt's closing material, made when that turn comes (the prep it names
# exists only once the author has written it).
FirstMessage = Callable[[str], str]


class BrainSwarmSession:
    """A SwarmSession of ChatSessions on the brain providers. The program
    runs the turns and keeps the log word for word, never summarising or
    rewriting: each participant is sent every new message of the others,
    verbatim, with who said it, and is given exactly its share of what Tim
    knows (`SHARES`). Turns run one at a time (`answer`) or in parallel
    (`together`: each is sent the conversation as it stood when they
    began, and their messages go into it in the order given). Each owner
    signs its concerns off in its own turn (`sign`); nobody votes.

    Its state (`saved`, `restore`) is kept between the run's processes, so
    a swarm that waits on the sender goes on where it stopped, each
    participant's session resumed.
    """

    def __init__(self, participants: dict[str, ChatSession], concerns: list[Concern], *,
                 author: str = "author", material: Optional[list[Known]] = None,
                 first: "Optional[dict[str, str] | FirstMessage]" = None):
        self.participants = participants
        self.concerns = {c.id: c for c in concerns}
        self.signoffs: list[SignOff] = []           # every one given, in order
        self.conversation: list[dict] = []
        self.asked: list[dict] = []                 # each {"stage", "from", "ask"}
        self.turns: list[dict] = []                 # each turn's time and tokens
        self.author = author
        self.material = list(material or [])
        self.shares = {name: share(name, self.material) for name in participants
                       if name in SHARES}
        self._first = first if callable(first) else dict(first or {}).get
        self._introduced: set[str] = set()
        self._seen = {name: 0 for name in participants}

    def concerns_of(self, name: str, among=None) -> list[str]:
        """The concerns this participant owns (of `among`, when given)."""
        return [c.id for c in self.concerns.values()
                if c.owner == name and (among is None or c.id in among)]

    async def open(self) -> None:
        for chat in self.participants.values():
            await chat.open()

    async def close(self) -> None:
        for chat in self.participants.values():
            await chat.close()

    def share_of(self, name: str) -> list[Known]:
        return self.shares.get(name, [])

    def _new_for(self, name: str) -> str:
        """Every message of the others since this participant last spoke,
        word for word, each with who said it; on its first turn its own
        first message comes first. Nothing is taken as given it until it
        has answered (`_heard`)."""
        new = [m for m in self.conversation[self._seen[name]:] if m["from"] != name]
        lead = [] if name in self._introduced else [self._first(name) or ""]
        return "\n\n".join([t for t in lead if t] +
                            [f"{m['from']}:\n{m['text']}" for m in new])

    def _heard(self, name: str, upto: Optional[int] = None) -> None:
        """A participant answered: everything said up to `upto` (all, by
        default) is given it, its first message among it."""
        self._seen[name] = len(self.conversation) if upto is None else upto
        self._introduced.add(name)

    def spoke(self, name: str) -> bool:
        """It has answered a turn of the swarm (its session holds it)."""
        return name in self._introduced

    def say(self, name: str, text: str) -> None:
        """A message into the conversation, as it was written (the
        program's own, the sender's, or a participant's)."""
        self.conversation.append({"from": name, "text": text})

    async def _ask(self, name: str, timeout: float, add: str, form: dict) -> dict:
        sent = "\n\n".join(t for t in (self._new_for(name), add) if t)
        chat = self.participants[name]
        started = time.monotonic()
        reply = await chat.ask(sent, timeout, answer_format=form)
        took = getattr(chat, "last_turn", None) or {}
        self.turns.append({"participant": name, "seconds": round(time.monotonic() - started, 1),
                           **{k: v for k, v in took.items() if k != "seconds"}})
        answer = reply.answer or {}
        if not isinstance(answer.get("message"), str) or not answer["message"].strip():
            raise SessionFailed(f"{name}'s turn had no message")
        return answer

    async def answer(self, name: str, timeout: float, add: str, form: dict) -> dict:
        """One participant's turn: it is sent what the others said since its
        last, and `add` (the program's own message to it), and answers in
        `form`; its message goes into the conversation."""
        answer = await self._ask(name, timeout, add, form)
        self.say(name, answer["message"])
        self._heard(name)
        return answer

    async def together(self, turns: dict[str, tuple[str, dict]], timeout: float
                       ) -> dict[str, dict]:
        """Several participants' turns in parallel, each (add, form): each is
        sent the conversation as it stood when they began; their messages go
        into it in the order given."""
        upto = len(self.conversation)
        names = list(turns)
        answers = await asyncio.gather(*(self._ask(n, timeout, *turns[n]) for n in names))
        for name, answer in zip(names, answers):
            self.say(name, answer["message"])
            self._heard(name, upto)
        return dict(zip(names, answers))

    # ── sign-offs and asks ───────────────────────────────────────────────
    def sign(self, name: str, answer: dict, stage: str, among=None) -> list[SignOff]:
        """The owner's sign-off on each of its concerns (of `among`), from
        its turn's answer. A concern it left without a sign-off, or signed off not yet
        with nothing named as left, is not yet, the program saying why."""
        given = {str(s.get("concern")): s for s in answer.get("signoff") or []
                 if isinstance(s, dict)}
        out = []
        for cid in self.concerns_of(name, among):
            s = given.get(cid) or {}
            state, reason = str(s.get("state") or ""), str(s.get("reason") or "").strip()
            left = [{"what": str(i.get("what") or "").strip(),
                     "fix": str(i.get("fix") or "").strip(),
                     "evidence_missing": str(i.get("evidence_missing") or "").strip()}
                    for i in s.get("left") or [] if isinstance(i, dict)]
            left = [i for i in left if i["what"]]
            if state == RESOLVED and reason:
                left = []
            elif state == NOT_YET and left:
                pass
            else:
                state = NOT_YET
                left = left or [{"what": f"{name} gave no sign-off on {cid} that says what is "
                                         f"left", "fix": "", "evidence_missing": ""}]
            out.append(SignOff(cid, name, state, reason, left, stage))
        self.signoffs += out
        return out

    def latest(self, concern: str) -> Optional[SignOff]:
        found = [s for s in self.signoffs if s.concern == concern]
        return found[-1] if found else None

    def state(self, concern: str) -> str:
        last = self.latest(concern)
        return last.state if last else OPEN

    def resolved(self, concerns=None) -> bool:
        return all(self.state(c) == RESOLVED for c in (concerns or self.concerns))

    def not_resolved(self, concerns=None) -> list[str]:
        return [c for c in (concerns or self.concerns) if self.state(c) != RESOLVED]

    def left(self, concerns=None) -> list[dict]:
        """What each concern not resolved has left, by its latest sign-off:
        each {"concern", "owner", "what", "fix", "evidence_missing"}."""
        out = []
        for cid in self.not_resolved(concerns):
            last = self.latest(cid)
            for item in (last.left if last else []):
                out.append({"concern": cid, "owner": self.concerns[cid].owner, **item})
        return out

    def ask(self, name: str, answer: dict, stage: str) -> None:
        """The questions only the sender can answer, from a turn's answer."""
        for a in answer.get("ask_sender") or []:
            if isinstance(a, str) and a.strip():
                self.asked.append({"stage": stage, "from": name, "ask": a.strip(), "sent": False})

    def to_send(self) -> list[dict]:
        return [a for a in self.asked if not a["sent"]]

    def sent(self) -> None:
        for a in self.asked:
            a["sent"] = True

    def result(self) -> dict:
        """Each concern's state, every sign-off, what is left, what the
        sender was asked, the conversation word for word and each turn's
        time and tokens."""
        return {"concerns": [{"id": c.id, "asks": c.asks, "owner": c.owner,
                              "state": self.state(c.id)} for c in self.concerns.values()],
                "signoffs": [dataclasses.asdict(s) for s in self.signoffs],
                "left": self.left(), "asked": list(self.asked),
                "conversation": list(self.conversation), "turns": list(self.turns)}

    # ── kept between the run's processes ─────────────────────────────────
    def saved(self) -> dict:
        """Everything the swarm holds but its participants' sessions."""
        return {"signoffs": [dataclasses.asdict(s) for s in self.signoffs],
                "asked": list(self.asked), "turns": list(self.turns),
                "conversation": list(self.conversation),
                "seen": dict(self._seen), "introduced": sorted(self._introduced)}

    def restore(self, saved: dict) -> None:
        """The swarm as `saved` kept it."""
        self.signoffs = [SignOff(**s) for s in saved.get("signoffs") or []
                         if s.get("concern") in self.concerns]
        self.asked = list(saved.get("asked") or [])
        self.turns = list(saved.get("turns") or [])
        self.conversation = list(saved.get("conversation") or [])
        self._seen.update({n: int(c) for n, c in (saved.get("seen") or {}).items()
                           if n in self._seen})
        self._introduced = set(saved.get("introduced") or [])


# ── DecisionSession ───────────────────────────────────────────────────────

YES_NO, CHOICE, SCALE = "yes_no", "choice", "scale"
YES, NO = "yes", "no"
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
    group: str = ""                 # its file: items of one file are packed together


@dataclass(frozen=True)
class ClassQuestion:
    id: str
    asks: str


class ClassificationSession(Protocol):
    """Each item with the classes it bears on most: the handoff's
    questions, plus background (designed around CDM Classify); at most
    TOP, each scored FLOOR or more."""

    async def classify(self, items: list[Item], classes: list[ClassQuestion],
                       timeout: float) -> list[dict]: ...


# ── a role's material in pages, the answer sparse ─────────────────────────
#
# Mark rebuilt for speed (Tim 2026-11-21): the material packed into pages
# that fit the model's window (pages.py), the pages asked in parallel, one
# session kept across a worker's pages, and the answer sparse. Smart passes
# (Tim 2026-11-21, construction-drafts.md, the last Tim line): each
# sentence (or item) by its id with at most its TOP labels (or questions)
# that apply most, each scored FLOOR or more, none when none reaches FLOOR.
# The program checks every id is the page's, each given once and none
# missing, and every label one of the taxonomy's, at most TOP to an id,
# each scored between FLOOR and 1.

TOP = 3
FLOOR = 0.5
_APPLIES = {"type": "number", "minimum": FLOOR, "maximum": 1}


def _sparse_form(listed: str, named: str, names: list[str]) -> dict:
    """The sparse answer's form: `listed` (parts, items), each with its id
    and at most TOP of `named` (labels, questions), each scored FLOOR or
    more."""
    return _object({listed: {**_array(_object({
        "id": _TEXT,
        named: {**_array(_object({named[:-1]: {"type": "string", "enum": list(names)},
                                  "score": _APPLIES})),
                "maxItems": TOP,
                "description": f"At most the {TOP} {named} that apply most, each scored "
                               f"{FLOOR} or more; none when none reaches {FLOOR}."}})),
        "description": f"Every one of the {listed} given, by its id."}})


def _answer_most(listed: str, named: str, page_id: str, names: list[str]) -> int:
    """The most one entry of a sparse answer can take: its id with the TOP
    longest of `names` scored, in tokens."""
    longest = sorted(names, key=len, reverse=True)[:TOP]
    return pages.tokens(json.dumps({"id": page_id, named: [
        {named[:-1]: n, "score": 0.125} for n in longest]})) + 1


def _check_sparse(answer: object, listed: str, named: str, ids: list[str],
                  names: list[str]) -> dict[str, dict[str, float]]:
    """Each id's scores, only the names given (one left out did not reach
    FLOOR); raises pages.NotChecked unless every id given is the page's,
    once each, none missing, and every name scored is one of `names`, at
    most TOP to an id, each between FLOOR and 1."""
    wanted, allowed = set(ids), set(names)
    given = answer.get(listed) if isinstance(answer, dict) else None
    if not isinstance(given, list):
        raise pages.NotChecked(f"the answer has no list of {listed}")
    out: dict[str, dict[str, float]] = {}
    for entry in given:
        pid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(pid, str) or pid not in wanted:
            raise pages.NotChecked("it answered an id that is not on the page")
        if pid in out:
            raise pages.NotChecked("it answered one id twice")
        listed_names = entry.get(named) or []
        if len(listed_names) > TOP:
            raise pages.NotChecked(f"it gave more than {TOP} {named} to one id")
        scores: dict[str, float] = {}
        for s in listed_names:
            name = s.get(named[:-1]) if isinstance(s, dict) else None
            value = s.get("score") if isinstance(s, dict) else None
            if name not in allowed:
                raise pages.NotChecked(f"it gave a name that is not one of the {named}")
            if name in scores:
                raise pages.NotChecked(f"it gave one of the {named} twice to one id")
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not FLOOR <= value <= 1:
                raise pages.NotChecked(f"it gave a score that is not between {FLOOR} and 1")
            scores[name] = float(value)
        out[pid] = scores
    missing = wanted - set(out)
    if missing:
        raise pages.NotChecked(f"it answered {len(out)} of the page's {len(wanted)}, "
                               f"missing {len(missing)}")
    return out


class _PagedRole:
    """One role's pages asked: its sessions (the first made before packing,
    for its brain and model), its budget, and each page's answer checked.
    `reports` keeps what each run of pages took."""

    def __init__(self, role: str, folder: Path, pick_args: dict):
        self.role, self.folder, self.pick_args = role, Path(folder), pick_args
        self.reports: list[pages.Report] = []

    async def _session(self, system: str, form: dict):
        return await asyncio.to_thread(BrainChatSession.for_role, self.role, system,
                                       self.folder, answer_format=form, **self.pick_args)

    async def ask(self, system: str, form: dict, fixed_material: str,
                  docs: list[pages.Doc], material: Callable[[pages.Page], str],
                  check: Callable[[object, pages.Page], T], timeout: Optional[float],
                  failed: str) -> list[T]:
        """Every page's checked answer. `fixed_material`: the first message
        with no page in it, counted with the system prompt, the form and the
        brain's starting context as what every call holds."""
        if not any(d.atoms for d in docs):
            return []
        first = await self._session(system, form)
        pick = first.pick
        fixed = (pages.tokens(system) + pages.tokens(fixed_material)
                 + pages.tokens(json.dumps(form)) + pages.starting_context(pick.engine))
        try:
            packed = pages.pack(docs, fixed, pages.budget(pick, self.role))
        except pages.TooLarge as e:
            raise SessionFailed(f"{self.role}: {e}") from e

        async def ask_page(session, page: pages.Page) -> T:
            try:
                answer = (await session.ask(material(page), timeout)).answer
            except NotInForm as e:
                raise pages.NotChecked(str(e)) from e
            return check(answer, page)

        report = pages.Report()
        self.reports.append(report)
        wall, _ = run_executor.bounds(timeout)
        try:
            return await pages.run(
                packed, first=first, make=lambda: self._session(system, form),
                parallel=model_tiers.PARALLEL.get(pick.engine, 1), ask=ask_page,
                failed=lambda why: SessionFailed(f"{failed}: {why}"), report=report,
                wait_most=wall)
        finally:
            log.info("%s: %s", self.role, json.dumps(report.summary()))
            run_report.pages(self.role, report.summary())


class BrainClassificationSession:
    """On the brain providers: the items without knowledge states (they do
    not judge him), packed into pages (an item never split), the items of
    one file together, each item answered with at most the TOP questions
    it bears on most, each FLOOR or more."""

    def __init__(self, folder: Path, **pick_args):
        self.paged = _PagedRole("classification", folder, pick_args)

    async def classify(self, items: list[Item], classes: list[ClassQuestion],
                       timeout: float) -> list[dict]:
        names = [c.id for c in classes] + [BACKGROUND]
        form = _sparse_form("items", "questions", names)
        questions = "\n".join(_tag("question", c.asks, id=c.id) for c in classes)
        key = "{the items: facts with their file and place, marked parts, each by id}"

        def values(block: str) -> dict:
            return {key: block, "{the handoff's questions}": questions}
        system, fixed = role_prompt("classification", values(""))
        docs: list[pages.Doc] = []
        for n, item in enumerate(items):
            page_id = str(n + 1)
            atom = pages.Atom([(page_id, item)],
                              pages.tokens(_tag("item", item.text, id=page_id,
                                                source=item.source)) + 1,
                              _answer_most("items", "questions", page_id, names))
            if docs and docs[-1].key == item.group:
                docs[-1].atoms.append(atom)
            else:
                docs.append(pages.Doc(item.group, [atom], 0))

        def material(page: pages.Page) -> str:
            return role_prompt("classification", values("\n".join(
                _tag("item", item.text, id=pid, source=item.source)
                for pid, item in page.items)))[1]

        def check(answer, page: pages.Page) -> dict[str, dict[str, float]]:
            return _check_sparse(answer, "items", "questions",
                                 [pid for pid, _ in page.items], names)
        scored: dict[str, dict] = {}
        for found in await self.paged.ask(system, form, fixed, docs, material, check, timeout,
                                          "classification could not answer one item"):
            scored.update(found)
        return [{"item": item.id, "scores": scored[str(n + 1)]} for n, item in enumerate(items)]

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
    the sentence it sits in, read by the program. Several texts are asked
    on one session (`extract_many`), a new conversation for each after the
    first (on Codex a new thread on the same app server), as marking's
    workers keep theirs."""

    FORM_OF = staticmethod(lambda fields: _object({"values": _array(_object({
        "field": {"type": "string", "enum": [f.name for f in fields]},
        "value": _TEXT, "confidence": _SCORE, "start": _OFFSET, "end": _OFFSET}))}))

    def __init__(self, folder: Path, **pick_args):
        self.folder = Path(folder)
        self.pick_args = pick_args

    @staticmethod
    def _first(text: str, fields: list[Field]) -> tuple[str, str]:
        return role_prompt("extraction", {
            "{text}": _tag("text", text),
            "{fields for this file's type}":
                "\n".join(_tag("field", f.describes, name=f.name,
                               many="true" if f.many else "false") for f in fields)})

    async def extract(self, text: str, fields: list[Field], timeout: float) -> list[dict]:
        return (await self.extract_many([(text, fields)], timeout))[0]

    async def extract_many(self, asks: list[tuple[str, list[Field]]], timeout: Optional[float]
                           ) -> list[list[dict]]:
        """Each (text, its fields) answered in turn on one session (the
        approved prompt's role part is the same for every text): for each
        after the first a new conversation (`fresh`), its form the
        session's own (a Claude session started again takes it at launch)
        and given with the turn."""
        if not asks:
            return []
        out: list[list[dict]] = []
        session = None
        try:
            for text, fields in asks:
                form = self.FORM_OF(fields)
                system, first = self._first(text, fields)
                if session is None:
                    session = BrainChatSession.for_role("extraction", system, self.folder,
                                                        answer_format=form, **self.pick_args)
                    await session.open()
                    answer = (await session.ask(first, timeout)).answer
                else:
                    session.answer_format = form
                    await session.fresh()
                    answer = (await session.ask(first, timeout, answer_format=form)).answer
                out.append(self._checked(text, fields, answer or {}))
        finally:
            if session is not None:
                await session.close()
        return out

    @staticmethod
    def _checked(text: str, fields: list[Field], answer: dict) -> list[dict]:
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
class Text:
    """One file's prose to mark: its key (the stored file), its words, and
    its type, for grouping."""
    key: str
    text: str
    kind: str = ""


class MarkingSession(Protocol):
    """Marks texts: each whole sentence with at most its TOP labels that
    apply most, each scored FLOOR or more (sessions.yaml MarkingSession;
    smart passes, Tim 2026-11-21)."""

    async def mark_files(self, texts: list[Text], labels: list[Label], knowledge: list[Known],
                         timeout: float) -> dict[str, list[dict]]: ...

    async def mark_slides(self, units: list, context: str, labels: list[Label],
                          knowledge: list[Known], timeout: float) -> list[dict]: ...


@dataclass
class _Words:
    """One file or slide as it is packed: its tag and key, the whole text
    given with any page holding some of it ("" for none), and its
    sentences, each by its own id."""
    n: int
    tag: str
    key: str
    text: str
    sentences: list[text_parts.Segment]


def _break_before(text: str, sentence: text_parts.Segment, previous_end: int) -> int:
    """How strong a break comes before a sentence: a heading starts a
    section, a blank line a paragraph."""
    if text_parts.is_heading(sentence.text):
        return pages.SECTION
    return pages.PARAGRAPH if "\n\n" in text[previous_end:sentence.start] else pages.NO_BREAK


class BrainMarkingSession:
    """On the brain providers: each text's whole sentences (split by
    text_parts; a sentence's phrases are not marked, smart passes, Tim
    2026-11-21), each with at most its TOP labels that apply most, each
    FLOOR or more, each label on its own. Tim's material comes with its
    knowledge states, for the labels that depend on him.

    In pages (Mark rebuilt for speed, Tim 2026-11-21; it replaced the
    batches of sessions.yaml MarkingSession, 2026-11-20): the texts, in the
    caller's order (similar files together), packed into pages that fit the
    model's window with the instructions, the answer and a margin, several
    files to a page; a file too long for one page split across pages at its
    section breaks. Every page gives each file it holds whole as context,
    as each batch did. The answer is sparse: each sentence with at most its
    TOP labels at FLOOR or more. A script
    is marked slide by slide (`mark_slides`): a slide never shares a page
    with part of another, and the whole script is given with every page."""

    def __init__(self, folder: Path, **pick_args):
        self.paged = _PagedRole("marking", folder, pick_args)

    async def mark(self, text: str, labels: list[Label], knowledge: list[Known],
                   timeout: float) -> list[dict]:
        """One text marked: `mark_files` with it alone."""
        return (await self.mark_files([Text("text", text)], labels, knowledge, timeout))["text"]

    async def mark_files(self, texts: list[Text], labels: list[Label], knowledge: list[Known],
                         timeout: float) -> dict[str, list[dict]]:
        """Each text's sentences, by its key, each with the labels it was
        given (one left out did not reach FLOOR). A text's words are given
        once on a page: an item of Tim's material that is a file on the
        page is named there by its source and state alone."""
        words = [_Words(n + 1, "file", t.key, t.text, _sentences(text_parts.split(t.text)))
                 for n, t in enumerate(texts)]
        scored = await self._marked(words, "", labels, knowledge, timeout)
        return {t.key: [_part(s, scored[(w.n, s.id)]) for s in w.sentences]
                for t, w in zip(texts, words)}

    async def mark_slides(self, units: list, context: str, labels: list[Label],
                          knowledge: list[Known], timeout: float) -> list[dict]:
        """A script marked slide by slide: `units`, each piece of its words
        with its `slide`, `field` and `text` (package_script.units); the
        whole script, `context`, given with every page. A part's id and
        position are its slide's and field's: `<slide>/<field>/<part>`, its
        offsets within that field's words."""
        by_slide: dict[str, _Words] = {}
        placed: list[tuple[object, text_parts.Segment]] = []
        for unit in units:
            segments = [dataclasses.replace(s, id=f"{unit.slide}/{unit.field}/{s.id}")
                        for s in _sentences(text_parts.split(unit.text))]
            slide = by_slide.setdefault(unit.slide, _Words(len(by_slide) + 1, "slide",
                                                           unit.slide, "", []))
            slide.sentences.extend(segments)
            placed += [(unit, s) for s in segments]
        scored = await self._marked(list(by_slide.values()), context, labels, knowledge,
                                    timeout)
        return [{**_part(s, scored[(by_slide[unit.slide].n, s.id)]), "slide": unit.slide,
                 "field": unit.field} for unit, s in placed]

    async def _marked(self, words: list[_Words], context: str, labels: list[Label],
                      knowledge: list[Known], timeout: Optional[float]
                      ) -> dict[tuple[int, str], dict]:
        """Every part's scores, by (its file's number, its own id)."""
        names = [label.name for label in labels]
        form = _sparse_form("parts", "labels", names)
        label_block = "\n".join(_tag("label", f"{label.means}\nExample: {label.example}"
                                     if label.example else label.means, name=label.name)
                                for label in labels)
        head = _tag("text", context) + "\n" if context else ""

        def values(parts: str, on_page: set[str]) -> dict:
            return {"{the parts, by id}": head + parts,
                    "{each label: name, what it means, an example}": label_block,
                    "{Tim's material, each item with its knowledge state}": "\n".join(
                        _tag("material", "" if k.source and k.source in on_page else k.text,
                             state=k.state, source=k.source or None) for k in knowledge)}
        system, fixed = role_prompt("marking", values("", set()))

        def part_tag(w: _Words, s: text_parts.Segment) -> str:
            return _tag("part", s.text, id=f"{w.n}:{s.id}")
        docs = []
        for w in words:
            atoms, end = [], 0
            for sentence in w.sentences:
                atoms.append(pages.Atom(
                    [(w, sentence)], pages.tokens(part_tag(w, sentence)) + 1,
                    _answer_most("parts", "labels", f"{w.n}:{sentence.id}", names),
                    _break_before(w.text, sentence, end) if w.text else pages.NO_BREAK))
                end = sentence.end
            docs.append(pages.Doc(w.key, atoms, pages.tokens(_tag("text", w.text)) + 1
                                  if w.text else 0, meta=w))

        def material(page: pages.Page) -> str:
            blocks = []
            for e in page.entries:
                w = e.doc.meta
                inner = ([_tag("text", w.text)] if e.context and w.text else []) + \
                    [part_tag(w, s) for a in e.atoms for _, s in a.items]
                blocks.append(f"<{w.tag} {'name' if w.tag == 'file' else 'id'}="
                              f"{quoteattr(w.key)}>\n" + "\n".join(inner) + f"\n</{w.tag}>")
            on_page = {e.doc.key for e in page.entries if e.doc.meta.text}
            return role_prompt("marking", values("\n".join(blocks), on_page))[1]

        def check(answer, page: pages.Page) -> dict[tuple[int, str], dict]:
            ids = {f"{w.n}:{s.id}": (w.n, s.id) for w, s in page.items}
            found = _check_sparse(answer, "parts", "labels", list(ids), names)
            return {ids[pid]: scores for pid, scores in found.items()}
        scored: dict[tuple[int, str], dict] = {}
        for found in await self.paged.ask(system, form, fixed, docs, material, check, timeout,
                                          "marking could not answer one sentence"):
            scored.update(found)
        return scored


def _sentences(segments: list[text_parts.Segment]) -> list[text_parts.Segment]:
    """The whole sentences (text_parts gives a sentence, then its phrases;
    only the sentence is marked)."""
    return [s for s in segments if s.kind == text_parts.SENTENCE]


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


# ── edits ─────────────────────────────────────────────────────────────────

# One edit as a participant gives it (owner-prompts.md: "Each edit names the
# slide and field, the exact old text and the new text, and its reason"):
# where in the words it is (offsets), the exact old text, the new, its
# reason and the source of any fact it adds. The program applies only edits
# whose old text is exactly there (`apply_edits`): at the offsets, or the
# one place the words hold it when the offsets miss.
EDIT = _object({"start": _OFFSET, "end": _OFFSET, "old": _TEXT, "new": _TEXT,
                "reason": _TEXT, "source": _TEXT})


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


def _where(text: str, edit: dict) -> Optional[tuple[int, int]]:
    """Where an edit's old text is exactly there: at its offsets, else, when
    they do not hold it, the one place the text holds it (owner-prompts.md:
    an edit names its old text, and the program applies only edits whose
    old text is exactly there). None: not there, or there more than once
    with no offsets saying which."""
    start, end, old = edit.get("start"), edit.get("end"), edit.get("old")
    if not isinstance(old, str):
        return None
    if (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text)
            and text[start:end] == old):
        return start, end
    if old and text.count(old) == 1:
        at = text.find(old)
        return at, at + len(old)
    return None


def apply_edits(text: str, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """The edits applied by the program, latest place first: (the text,
    the edits applied, the edits refused because their old text is not
    exactly there (`_where`) or they overlap one applied)."""
    applied, refused = [], []
    taken: list[tuple[int, int]] = []
    placed = []
    for edit in edits:
        where = _where(text, edit)
        if where is None:
            refused.append(edit)
        else:
            placed.append((where, edit))
    for (start, end), edit in sorted(placed, key=lambda p: p[0][0], reverse=True):
        if any(start < b and end > a for a, b in taken):
            refused.append(edit)
            continue
        # Applied from the end back, so every offset still to come is the
        # original text's; `taken` is in those offsets too.
        text = text[:start] + str(edit.get("new") or "") + text[end:]
        taken.append((start, end))
        applied.append(edit)
    return text, applied, refused


def located(edit: dict) -> dict:
    """An edit of a structured file (the outline, the script): it also names
    its slide and its field, its offsets within that field's words."""
    where = {"slide": {"type": "string", "description": "The slide's id."},
             "field": {"type": "string",
                       "description": "The field within the slide (title, point, "
                                      "on_screen[0].text, narration, ...)."}}
    return _object({**where, **edit["properties"]})
