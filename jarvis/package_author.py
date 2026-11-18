"""Fresh authorship before the existing package factory builds or delivers.

Uses the same provider sessions as the conversational runtime, with the explicit
coding profile. The originating process owns validation and delivery afterwards.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jarvis import settings_service
from jarvis import author_proof, brain_provider, data_paths, engine_choice, session_skill
from jarvis.brain import BrainConfig
from jarvis.decision_package import Refused

ENGINES = brain_provider.ENGINES
SKILL = data_paths.PACKAGE_DIR / "session_skills" / "human-package-author" / "SKILL.md"
GUIDE = data_paths.SOURCE_ROOT / "docs" / "making-a-package.md"


def read_handoff(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused([f"Cannot read author handoff {path}: {error}"]) from None
    problems = []
    if not isinstance(value, dict):
        raise Refused(["Author handoff must be a JSON object."])
    for key in ("purpose", "audience"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            problems.append(f"Author handoff needs {key} as nonempty text.")
    for key in ("questions", "evidence"):
        if (not isinstance(value.get(key), list) or not value[key]
                or any(not isinstance(item, str) or not item.strip() for item in value[key])):
            problems.append(f"Author handoff needs a nonempty {key} list of text.")
    if "approval" in value and not isinstance(value["approval"], bool):
        problems.append("Author handoff's approval is true (ask Tim to approve the architecture "
                        "design) or false.")
    if problems:
        raise Refused(problems)
    return value


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
        return ("Write a deck as presentation.yaml (presentation/1) in your working directory, or, only "
                "for a working tool, a folder package under package/ with package.json. A deck says "
                "words, structure, pictures by path, narration and decisions; JARVIS lays it out: name "
                "one of its layouts on each content slide and a slot for each object, never a theme, "
                "rectangle, size or style (the guide lists them; python -m jarvis.make_package "
                "presentation.yaml --fill-assets writes each picture's size and hash). Put supporting "
                "assets alongside that source, and your evidence/review notes in evidence.md. A deck follows the skill's "
                "decision-memo order (what it is about and where it stands, what was already "
                "decided, what happened and why, the options and what changes, then the "
                "decision, technical detail last), gives its \"purpose\" and one-sentence "
                "\"conclusion\" (the bottom line, never a bare request) and, when it asks "
                "anything, its \"situation\" (subject, happened, approved_before, changes), "
                "which JARVIS draws as its first slide (without one, its own first slide "
                "is an opening that shows both), and gives each decision its "
                "\"why_you\" and \"evidence\" "
                "(docs/making-a-package.md). A handoff's suggested shape is input; where it "
                "disagrees with that order, follow the order. Validate with this "
                "installation’s exact command: " + session_skill._module("make_package", cert=False)
                + " <source> --check-only. ")
    if len(documents) > 1:
        return _set_assignment(documents)
    document = documents[0]
    return ("This package explains one checked canonical architecture document. Its exact "
            "revision is in " + str(document.folder) + ": source.yaml is the canonical YAML, "
            "view.html its rendered page, check.json the checker's evidence and document.json "
            "its identity, coverage and what lies outside the language. JARVIS projects that "
            "exact revision into the package itself, with the handoff's purpose as its purpose; "
            "you write its plain explanation, package/UNDERSTANDING.md, for the handoff's "
            "audience and questions, in the skill's decision-memo order: open it with what the "
            "architecture is for, the problem or question it serves, why it is in front of "
            "him now and what he will decide at the end; walk one concrete input through "
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
            "views: name in its `scenario` one concrete input (one line), and walk that same "
            "input through every view in turn, in the order above: every slide names its "
            "`view` (the document id), each view is told whole before the next, and every view "
            "has at least one slide. Declare any questions the reader should answer in its "
            "`questions` (the guide's Architecture documents section has the format; the factory "
            "adds the standard vote last, so never ask it). " + _pointing(documents)
            + " Nothing else goes under package/. " + _check_line(documents))


class FreshSession:
    """One new provider session in its own folder, with the coding profile
    (authoring) and no resume or fallback: the fresh author, and the cold
    reader that checks what it wrote (package_reader). `identity` is the
    session the provider reports; every turn must keep it."""

    def __init__(self, folder: Path, choice: engine_choice.Choice, transcript: Path):
        self.folder, self.choice, self.transcript = folder, choice, transcript
        self.identity: str | None = None
        self.model: str | None = None
        provider = brain_provider.provider(ENGINES[choice.engine])
        settings_service.load()     # this command's own process: his saved program paths
        config = BrainConfig(home=folder, provider=provider.name, model=choice.model, effort=None,
                             authoring=True, warmup_timeout=60, turn_timeout=60,
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


async def author(handoff: Path, engine: str, out: Path, *, source: Path | None = None,
                 timeout: float = 1800, session: str = "", work: str = "",
                 workflow: str = "", document: Document | tuple[Document, ...] | None = None, finish=None) -> Path:
    """One new session, no resume or fallback; return only its own package
    source (for a `document`, its package/ explanation). `finish(source,
    turn)` builds it; `turn` is another turn of the same author."""
    evidence = read_handoff(handoff)
    if engine not in ENGINES:
        raise Refused(["Choose the caller's own engine with --engine claude or --engine codex."])
    if not session:
        raise Refused(["Give --session for the originating session's answer route."])
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
    prompt = (author_proof.PROMPT_HEAD + " You are the fresh author; do not launch another "
              "author. Read " + str(SKILL)
              + " and the production guide " + str(GUIDE) + ". Independently research the "
              "handoff evidence and answer its audience's questions. The handoff is at "
              + str(handoff) + "; resolve its relative evidence paths from " + str(handoff.parent)
              + ". " + _assignment(document) +
              "Run that check yourself last, after your final change: JARVIS admits the "
              "package only when your own final check printed exactly the source delivered. "
              "The supplied skill, guide and command are sufficient even if no user skill copy "
              "has been installed. Do not deliver, invoke creation without "
              "--check-only, modify application code or send messages. The originating command "
              "owns building, proving and delivery. If evidence prevents a truthful package, "
              "write author-blocked.md explaining the precise gap and do not create source. "
              "Do not ask for an invented product decision.\n\nHandoff:\n"
              + json.dumps(evidence, ensure_ascii=False, indent=2))
    if source:
        prompt += "\nExisting package source is evidence to investigate, not an answer to preserve: " + str(source)

    async def turn(message):
        await child.turn(message, timeout)

    try:
        child = FreshSession(folder, choice, folder / "author-response.txt")
        await child.start("You are an independent human-package-author producing source for "
                          "the existing package factory.", prompt, timeout)
        receipt["author_session"], receipt["model"] = child.identity, child.model
        if not child.identity or child.identity == session:
            raise OSError("Author did not report a distinct fresh session identity.")
        if (folder / "author-blocked.md").exists():
            raise OSError("Author needs evidence: " + str(folder / "author-blocked.md"))
        if not documents and (folder / "content.json").exists():
            from jarvis.make_package import JSON_DECK_RETIRED
            raise OSError("Author wrote content.json. " + JSON_DECK_RETIRED)
        candidates = [p for p in (folder / "presentation.yaml", folder / "package") if p.exists()]
        if documents and candidates != [folder / "package"]:
            raise OSError("Author must produce package/UNDERSTANDING.md (and optionally "
                          "package/story.json), and no presentation.yaml, in " + str(folder))
        if len(candidates) != 1:
            raise OSError("Author must produce exactly one presentation.yaml or package/ source in " + str(folder))
        result = candidates[0]
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


def claim_of(source: Path) -> author_proof.Claim:
    """The author claim the signature carries: the engine and fresh session
    its receipt names (beside the source) and the source's digest now."""
    try:
        receipt = json.loads((source.parent / "author-receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = {}
    try:
        digest = author_proof.source_digest(source)
    except (OSError, ValueError) as e:
        raise Refused([f"The authored source cannot be read: {e}"]) from None
    claim = author_proof.Claim.of({"engine": receipt.get("engine"),
                                   "session": receipt.get("author_session"), "source": digest})
    if claim is None or receipt.get("status") != "authored":
        raise Refused([f"No fresh author receipt for {source}: the package was not authored "
                       f"by this command's fresh author."])
    return claim


def prepare(handoff: Path | None, engine: str | None, out: Path, **kwargs) -> Path:
    if handoff is None:
        raise Refused(["Creation requires --handoff with purpose, audience, questions and evidence; "
                       "the factory launches a fresh author before building. Use --check-only to validate source."])
    return asyncio.run(author(handoff.resolve(), engine or "", out, **kwargs))
