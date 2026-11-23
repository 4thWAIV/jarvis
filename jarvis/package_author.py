"""Fresh authorship before the existing package factory builds or delivers.

Uses the same provider sessions as the conversational runtime, with the explicit
coding profile. The originating process owns validation and delivery afterwards.

What a fresh author writes here is a hand-made folder package (package/
with a working tool's page, an agent's brief or a conversation's skill)
or an architecture document's explanation (make_package --document). A
deck is a briefing, made only by JARVIS's package workflow from a request
(package_request.py, make_package --start; workflow.yaml deliver
only_way_in), never here.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jarvis import settings_service
from jarvis import brain_provider, data_paths, engine_choice, session_skill
from jarvis.brain import BrainConfig
from jarvis.decision_package import Refused

ENGINES = brain_provider.ENGINES
SKILL = data_paths.PACKAGE_DIR / "session_skills" / "human-package-author" / "SKILL.md"
GUIDE = data_paths.SOURCE_ROOT / "docs" / "making-a-package.md"
# The fresh author's first words: which skill it uses.
PROMPT_HEAD = "Use the human-package-author skill for this authorized source-only assignment."
# Why a deck is not made here (make_package refuses one).
BRIEFING_BY_REQUEST = ("A deck for Tim is a briefing, and only JARVIS's package "
                       "workflow makes one: ask for it with python -m jarvis.make_package "
                       "--start --handoff <file> --session <your session id> (docs/"
                       "making-a-package.md, Asking JARVIS for a package).")


def read_handoff(path: Path) -> dict:
    """The sender's handoff, read and checked as JARVIS takes a request
    (package_request.read and check: the one handoff form), or Refused
    saying each problem."""
    from jarvis import package_request
    try:
        return package_request.check(package_request.read(path))
    except package_request.Refused as refused:
        raise Refused(refused.problems) from None


@dataclass(frozen=True)
class Document:
    """The checked architecture revision a fresh author explains
    (make_package --document): its identity and the folder holding it as
    the author reads it (make_package.document_evidence)."""
    document_id: str
    revision: str
    folder: Path


def _documents(document: Document | tuple[Document, ...] | None) -> tuple[Document, ...]:
    """The checked revisions a fresh author explains, in order: none, one,
    or a view set's several."""
    if document is None:
        return ()
    return (document,) if isinstance(document, Document) else tuple(document)


def _pointing(documents: tuple[Document, ...]) -> str:
    """Whether the revisions' rendered pages can be pointed at: only kit
    pages that load the story helper can (language_projection.project
    refuses a pointing story otherwise)."""
    from jarvis import package_story

    def view(document: Document) -> bytes:
        try:
            return (document.folder / "view.html").read_bytes()
        except OSError:
            return b""
    if all(package_story.HELPER_PATH.encode() in view(d) for d in documents):
        return ("Its view.html loads the story helper, so a beat may point at the targets the "
                "guide lists, each matching exactly one element of view.html.")
    return ("Its view.html does not load the story helper, so the story may not point: "
            "give beats no targets or actions.")


def _assignment(document: Document | tuple[Document, ...] | None) -> str:
    """What the fresh author writes, and the exact check it runs last."""
    documents = _documents(document)
    if not documents:
        return ("Write a folder package under package/ with package.json: an agent's brief "
                "(BRIEF.md), a conversation's skill (SKILL.md) or, only for a working tool, a page "
                "(index.html), as docs/making-a-package.md's \"A folder\" says. Write no deck "
                "(presentation.yaml): "
                + BRIEFING_BY_REQUEST + " Put supporting files alongside that source, and your "
                "evidence/review notes in evidence.md. Validate with this installation’s exact "
                "command: " + session_skill._module("make_package", cert=False)
                + " package --check-only. ")
    if len(documents) > 1:
        return _set_assignment(documents)
    document = documents[0]
    return ("This package explains one checked canonical architecture document. Its exact "
            "revision is in " + str(document.folder) + ": source.yaml is the canonical YAML, "
            "view.html its rendered page, check.json the checker's evidence and document.json "
            "its identity, coverage and what lies outside the language. JARVIS projects that "
            "exact revision into the package itself, with the handoff's question as its purpose; "
            "you write its plain explanation, package/UNDERSTANDING.md: the answer and the ask "
            "first; walk one concrete input through "
            "the architecture as the skill says, and say plainly what its coverage leaves "
            "unresolved or outside the language. A gap that needs his decision is stated as "
            "a problem first (what cannot be checked and what goes wrong because of it), "
            "then its options and what each changes for him. You may also write package/story.json, the "
            "walk-through JARVIS tells over the rendered page, with any questions the reader "
            "should answer declared in its `questions` (the guide's Architecture documents "
            "section has the format; the factory adds the standard vote last, so never ask "
            "it). " + _pointing(documents)
            + " Nothing else goes under package/. " + _check_line(documents))


def _check_line(documents: tuple[Document, ...]) -> str:
    """Where the author keeps its notes, and the exact check it runs last."""
    return ("Keep your evidence/review notes in evidence.md beside package/. Validate with this "
            "installation’s exact command: " + session_skill._module("make_package", cert=False)
            + " package --check-only"
            + "".join(f" --document {d.document_id} --revision {d.revision}" for d in documents)
            + ". ")


def _set_assignment(documents: tuple[Document, ...]) -> str:
    """A view set's assignment: several checked views of one system, one
    scenario walked through every view in turn (package_story.walk_problem)."""
    where = "; ".join(f"view {n}, document {d.document_id}, in {d.folder}"
                      for n, d in enumerate(documents, 1))
    return ("This package explains a view set: " + str(len(documents)) + " checked canonical "
            "architecture documents, each a view of the same system, in this order: " + where
            + ". In each folder source.yaml is that view's canonical YAML, view.html its "
            "rendered page, check.json the checker's evidence and document.json its identity, "
            "coverage and what lies outside its language. JARVIS projects every exact revision "
            "into the package, with the handoff's purpose as its purpose; you write one plain "
            "explanation of the whole set, package/UNDERSTANDING.md, for the handoff's audience "
            "and questions, in the skill's decision-memo order: open it with what the "
            "architecture is for, the problem or question it serves, why it is in front of him "
            "now and what he will decide at the end; say what each view answers and which "
            "elements the views share (the same id names the same element in every view); say "
            "plainly what each view's coverage leaves unresolved or outside its language. A gap "
            "that needs his decision is stated as a problem first (what cannot be checked and "
            "what goes wrong because of it), then its options and what each changes for him. "
            "You must also write package/story.json, the walk-through JARVIS tells over the "
            "views: name in its `scenario` one concrete input, and walk that same "
            "input through every view in turn, in the order above: every slide names its "
            "`view` (the document id), each view is told whole before the next, and every view "
            "has at least one slide. Declare any questions the reader should answer in its "
            "`questions` (the guide's Architecture documents section has the format; the factory "
            "adds the standard vote last, so never ask it). " + _pointing(documents)
            + " Nothing else goes under package/. " + _check_line(documents))


CHECK_LAST = "Run that check yourself last, after your final change."


class FreshSession:
    """One new provider session in its own folder, with the coding profile
    (authoring) and no fallback: the fresh author. `identity` is the session
    the provider reports; every turn must keep it. `resume` continues a fresh
    author's own session (package_jobs: its sender answered its questions),
    never another's; `mcp_config` is the servers it may use (agent_mcp)."""

    def __init__(self, folder: Path, choice: engine_choice.Choice, transcript: Path, *,
                 resume: str | None = None, mcp_config: Path | None = None):
        self.folder, self.choice, self.transcript = folder, choice, transcript
        self.identity: str | None = None
        self.model: str | None = None
        provider = brain_provider.provider(ENGINES[choice.engine])
        settings_service.load()     # this command's own process: his saved program paths
        config = BrainConfig(home=folder, provider=provider.name, model=choice.model, effort=None,
                             authoring=True, resume=resume, mcp_config=mcp_config,
                             warmup_timeout=60, turn_timeout=60,
                             claude_path=settings_service.value("claude_path") or None,
                             codex_path=settings_service.value("codex_path") or None)
        self.done = asyncio.get_running_loop().create_future()
        self.child = provider.session(config, self._event, self._exited)

    def _event(self, _session, value):
        if isinstance(value, brain_provider.SessionInfo):
            self.identity = value.conversation_id
            self.model = value.model
        elif isinstance(value, brain_provider.TextDelta):
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(value.text)
        elif isinstance(value, brain_provider.TurnEnded) and not self.done.done():
            if value.error:
                self.done.set_exception(OSError(value.error))
            else:
                self.done.set_result(None)

    async def _exited(self, _session, code):
        if not self.done.done():
            self.done.set_exception(OSError(f"The {self.role} engine exited before finishing "
                                            f"(exit {code})."))

    role = "author"

    async def start(self, instructions: str, prompt: str, timeout: float) -> None:
        async with asyncio.timeout(timeout):
            await self.child.open(instructions)
            await self.child.send(prompt)
            await self.done

    async def turn(self, message: str, timeout: float) -> None:
        """Another turn of the same session; refused if the session changed."""
        identity = self.identity
        self.done = asyncio.get_running_loop().create_future()
        async with asyncio.timeout(timeout):
            await self.child.send(message)
            await self.done
        if self.identity != identity:
            raise OSError(f"The {self.role} session changed between its turns.")

    async def close(self) -> None:
        if not self.done.done():
            self.done.cancel()
        elif not self.done.cancelled():
            self.done.exception()  # retrieve an early-exit error even when open itself raised
        await self.child.close(5)


# What a blocked author writes: the questions only its sender can answer
# (package_jobs asks them), and, optionally, its words on the gap.
QUESTIONS = "author-blocked.json"
BLOCKED = "author-blocked.md"
QUESTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
QUESTIONS_MAX = 10
QUESTION_CHARS = 1000
# The agent MCP server the author uses, signed as the sending agent
# (agent_mcp: JARVIS's read tools for research, through that agent's key).
AGENT_MCP = "jarvis_agent"
INSTRUCTIONS = ("You are an independent human-package-author producing source for the existing "
                "package factory.")


class WaitingOnSender(Exception):
    """The author asked its sender questions (package_jobs): its session is
    left as it is, to be resumed with the answers."""


def blocked(folder: Path) -> bool:
    return (folder / QUESTIONS).exists() or (folder / BLOCKED).exists()


def read_questions(folder: Path) -> list[dict]:
    """The blocked author's questions, structured: each an `id` (a short
    slug), the `question` and `why` it matters. An author that wrote only
    its words on the gap (author-blocked.md) has asked one question, those
    words."""
    try:
        raw = json.loads((folder / QUESTIONS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except (OSError, ValueError) as error:
        raise OSError(f"The author's {QUESTIONS} cannot be read: {error}") from None
    if raw is None:
        try:
            words = (folder / BLOCKED).read_text(encoding="utf-8").strip()
        except OSError:
            words = ""
        if not words:
            raise OSError(f"The author stopped without saying what it needs ({BLOCKED}).")
        return [{"id": "q1", "question": words[:QUESTION_CHARS], "why": ""}]
    items = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items or len(items) > QUESTIONS_MAX:
        raise OSError(f"The author's {QUESTIONS} needs 1 to {QUESTIONS_MAX} questions.")
    questions, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise OSError(f"Each question in {QUESTIONS} is an object.")
        ident, text = str(item.get("id") or ""), str(item.get("question") or "").strip()
        if not QUESTION_ID.fullmatch(ident) or ident in seen or not text:
            raise OSError(f"Each question in {QUESTIONS} has its own short id (lowercase "
                          f"letters, digits, hyphens) and words.")
        seen.add(ident)
        questions.append({"id": ident, "question": text[:QUESTION_CHARS],
                          "why": str(item.get("why") or "").strip()[:QUESTION_CHARS]})
    return questions


def clear_questions(folder: Path) -> None:
    for name in (QUESTIONS, BLOCKED):
        (folder / name).unlink(missing_ok=True)


def agent_mcp_config(folder: Path, session: str) -> Path:
    """The author's mcp.json: only the agent MCP server (agent_mcp.entry,
    the one description of it), signed as the sending agent's session, so
    the author researches with JARVIS's read tools within that agent's
    rights (part of the strict config: no other server of the user's)."""
    from jarvis import agent_mcp
    config = {"mcpServers": {AGENT_MCP: agent_mcp.entry(session)}}
    path = folder / "author-mcp.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


WRITING_POLL_SEC = 2.0


def _has_source(folder: Path) -> bool:
    return any((folder / name).exists() for name in ("presentation.yaml", "package"))


async def _note_writing(folder: Path, job, noted: list) -> None:
    """Tell the job once the author's source appears (writing)."""
    while not _has_source(folder):
        await asyncio.sleep(WRITING_POLL_SEC)
    noted.append(True)
    job.writing()


async def author(handoff: Path, engine: str, out: Path, *, source: Path | None = None,
                 timeout: float = 1800, session: str = "", work: str = "",
                 workflow: str = "", document: Document | tuple[Document, ...] | None = None,
                 finish=None, job=None) -> Path:
    """One fresh session, no fallback; return only its own package source
    (for a `document`, its package/ explanation), written in its first
    turn. `finish(source, turn)` builds it; `turn` is another turn of the
    same author.

    With a `job` (package_jobs), an author that needs facts only its sender
    has asks them (WaitingOnSender), after its first turn or any later one,
    and is resumed, the same session, once they are answered
    (`job.resuming`); past the job's rounds it is told to write the package
    with them unanswered. Without one, it is refused."""
    evidence = read_handoff(handoff)
    if engine not in ENGINES:
        raise Refused(["Choose the caller's own engine with --engine claude or --engine codex."])
    if not session:
        raise Refused(["Give --session for the originating session's answer route."])
    resuming = job.resuming if job is not None else None
    if resuming:
        # The same author goes on, on its own engine and in its own folder.
        choice = engine_choice.Choice(resuming["author_engine"], resuming.get("author_model"),
                                      "resumed")
        folder = Path(resuming["source_folder"])
        try:
            receipt = json.loads((folder / "author-receipt.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            receipt = {}
        receipt.update(status="resumed", resumed_at=time.time())
        clear_questions(folder)
    else:
        # The author goes on his setting for package authors (Settings, Brains):
        # the caller's own engine unless he chose otherwise, or a brain in token
        # stress gives way (engine_choice).
        choice = engine_choice.choose("package_author", requester=engine)
        folder = out / "author"
        # A rerun must not accidentally deliver stale output from a failed author.
        folder.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="source-", dir=folder))
        receipt = {"engine": choice.engine, "requester_engine": engine, "chosen_by": choice.why,
                   "origin_session": session, "work": work,
                   "workflow": workflow, "handoff": str(handoff), "started_at": time.time(),
                   "source_folder": str(folder), "status": "started"}
    documents = _documents(document)
    if documents:
        receipt["document"] = {"document_id": documents[0].document_id,
                               "revision": documents[0].revision}
    if len(documents) > 1:
        receipt["views"] = [{"document_id": d.document_id, "revision": d.revision}
                            for d in documents]
    child = None
    prompt = (PROMPT_HEAD + " You are the fresh author; do not launch another "
              "author. Read " + str(SKILL)
              + " and the production guide " + str(GUIDE) + ". Research the handoff's question "
              "yourself, from Tim's record and the evidence, with your tools: JARVIS's read "
              "tools (the " + AGENT_MCP + " MCP server: his conversation, messages, Work items, "
              "packages, knowledge search, repositories and folders). The handoff is at "
              + str(handoff) + "; resolve its relative evidence paths from " + str(handoff.parent)
              + ". " + _assignment(document) + CHECK_LAST + " "
              "The supplied skill, guide and command are sufficient even if no user skill copy "
              "has been installed. Do not deliver, invoke creation without "
              "--check-only, modify application code or send messages. The originating command "
              "owns building, proving and delivery. If a truthful package needs facts that "
              "neither the evidence nor JARVIS's tools hold, and only the sending agent can "
              "give, write " + QUESTIONS + " as {\"questions\": [{\"id\": \"short-slug\", "
              "\"question\": \"...\", \"why\": \"what the answer changes in the package\"}]}, "
              "at most " + str(QUESTIONS_MAX) + ", and stop: the sender answers them in this "
              "same session, at any step. "
              "Do not ask for an invented product decision.\n\nHandoff:\n"
              + json.dumps(evidence, ensure_ascii=False, indent=2))
    if source:
        prompt += "\nExisting package source is evidence to investigate, not an answer to preserve: " + str(source)

    noted: list = []

    async def watched(work_of):
        """One author turn, the job told when its source appears (looked
        for while the turn runs, and once more as it ends)."""
        if job is None or noted:
            await work_of
            return
        watcher = asyncio.create_task(_note_writing(folder, job, noted))
        try:
            await work_of
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if not noted and _has_source(folder):
            noted.append(True)
            job.writing()

    async def answered():
        """After every author turn: the questions it asked its sender, if
        any, go to the job (WaitingOnSender, the session kept to be
        resumed); past the job's rounds it is told to go on without them."""
        if not blocked(folder):
            return
        questions = read_questions(folder)
        if job is None:
            raise OSError("Author needs evidence: " + "; ".join(
                q["question"] for q in questions) + f" ({folder})")
        if job.ask(questions, author_session=child.identity):
            receipt.update(status="waiting", questions=questions)
            raise WaitingOnSender(questions)
        # No rounds left: Tim sees what is not known, marked so.
        clear_questions(folder)
        await watched(child.turn(job.unanswered_message(questions), timeout))
        if blocked(folder):
            raise OSError("Author still needs evidence after every question round: "
                          + "; ".join(q["question"] for q in read_questions(folder)))

    async def turn(message) -> str:
        """Another turn of the same author (a repair), followed by the
        question check. Returns what the author said in it."""
        transcript = folder / "author-response.txt"
        before = transcript.stat().st_size if transcript.exists() else 0
        await watched(child.turn(message, timeout))
        await answered()
        if not transcript.exists():
            return ""
        with transcript.open("rb") as said:
            said.seek(before)
            return said.read().decode("utf-8", errors="replace")

    try:
        mcp_config = agent_mcp_config(folder, session)
        child = FreshSession(folder, choice, folder / "author-response.txt",
                             resume=resuming["author_session"] if resuming else None,
                             mcp_config=mcp_config)
        if job is not None:
            job.researching(folder=str(folder), author_engine=choice.engine,
                            author_model=choice.model)
        await watched(child.start(INSTRUCTIONS, resuming["message"] if resuming else prompt,
                                  timeout))
        receipt["author_session"], receipt["model"] = child.identity, child.model
        if not child.identity or child.identity == session:
            raise OSError("Author did not report a distinct fresh session identity.")
        if resuming and child.identity != resuming["author_session"]:
            raise OSError("The resumed author is not the same session that asked.")
        if job is not None:
            job.authored_by(child.identity)
        await answered()
        if not _has_source(folder):
            # A resumed author that asked before writing goes on to write it.
            await turn(_assignment(document) + CHECK_LAST)
        if any((folder / name).exists() for name in ("presentation.yaml", "content.json")):
            raise Refused([BRIEFING_BY_REQUEST])
        if documents and not (folder / "package").exists():
            raise OSError("Author must produce package/UNDERSTANDING.md (and optionally "
                          "package/story.json), and no presentation.yaml, in " + str(folder))
        if not (folder / "package").exists():
            raise OSError("Author must produce a package/ source in " + str(folder))
        result = folder / "package"
        if result.is_symlink() or not result.resolve().is_relative_to(folder.resolve()):
            raise OSError("Author source must belong to its fresh output folder.")
        receipt.update(status="authored", source=str(result))
        (folder / "author-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if finish is not None:
            receipt["built"] = await finish(result, turn)
        return result
    except Refused as error:
        receipt.update(status="failed", error="; ".join(error.problems))
        raise
    except (OSError, RuntimeError, TimeoutError) as error:
        receipt.update(status="failed", error=str(error) or "Author timed out.")
        raise Refused(["Fresh author did not finish: " + receipt["error"]]) from None
    finally:
        if child is not None:
            receipt.setdefault("author_session", child.identity)
            await child.close()
        receipt["finished_at"] = time.time()
        (folder / "author-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def prepare(handoff: Path | None, engine: str | None, out: Path, **kwargs) -> Path:
    if handoff is None:
        raise Refused(["Creation requires --handoff with the question, its Work item and evidence; "
                       "the factory launches a fresh author before building. Use --check-only to validate source."])
    return asyncio.run(author(handoff.resolve(), engine or "", out, **kwargs))
