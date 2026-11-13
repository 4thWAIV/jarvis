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


def _assignment(document: Document | None) -> str:
    """What the fresh author writes, and the exact check it runs last."""
    if document is None:
        return ("Write a deck to content.json in your working directory, or write a folder "
                "package under package/ with package.json. Put supporting assets alongside that "
                "source, and your evidence/review notes in evidence.md. Validate with this "
                "installation’s exact command: " + session_skill._module("make_package", cert=False)
                + " <source> --check-only. ")
    return ("This package explains one checked canonical architecture document. Its exact "
            "revision is in " + str(document.folder) + ": source.yaml is the canonical YAML, "
            "view.html its rendered page, check.json the checker's evidence and document.json "
            "its identity, coverage and what lies outside the language. JARVIS projects that "
            "exact revision into the package itself; you write only its plain explanation, "
            "package/UNDERSTANDING.md (nothing else under package/), for the handoff's audience "
            "and questions: walk one concrete input through the architecture as the skill "
            "says, and say plainly what its coverage leaves unresolved or outside the language. "
            "Keep your evidence/review notes in evidence.md beside package/. Validate with this "
            "installation’s exact command: " + session_skill._module("make_package", cert=False)
            + " package --check-only --document " + document.document_id + " --revision "
            + document.revision + ". ")


async def author(handoff: Path, engine: str, out: Path, *, source: Path | None = None,
                 timeout: float = 1800, session: str = "", work: str = "",
                 workflow: str = "", document: Document | None = None, finish=None) -> Path:
    """One new session, no resume or fallback; return only its own package
    source (for a `document`, its package/ explanation)."""
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
    provider = brain_provider.provider(ENGINES[choice.engine])
    config = BrainConfig(home=folder, provider=provider.name, model=choice.model, effort=None,
                         authoring=True, warmup_timeout=60, turn_timeout=60,
                         claude_path=os.getenv("JARVIS_CLAUDE_PATH") or None,
                         codex_path=os.getenv("JARVIS_CODEX_PATH") or None)
    done = asyncio.get_running_loop().create_future()
    receipt = {"engine": choice.engine, "requester_engine": engine, "chosen_by": choice.why,
               "origin_session": session, "work": work,
               "workflow": workflow, "handoff": str(handoff), "started_at": time.time(),
               "source_folder": str(folder), "status": "started"}
    if document is not None:
        receipt["document"] = {"document_id": document.document_id, "revision": document.revision}
    transcript = folder / "author-response.txt"

    def event(_session, value):
        if isinstance(value, brain_provider.SessionInfo):
            receipt["author_session"] = value.conversation_id
            receipt["model"] = value.model
        elif isinstance(value, brain_provider.TextDelta):
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(value.text)
        elif isinstance(value, brain_provider.TurnEnded) and not done.done():
            if value.error:
                done.set_exception(OSError(value.error))
            else:
                done.set_result(None)

    async def exited(_session, code):
        if not done.done():
            done.set_exception(OSError(f"Author engine exited before finishing (exit {code})."))

    child = provider.session(config, event, exited)
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
              "owns rendering checks and delivery. If evidence prevents a truthful package, "
              "write author-blocked.md explaining the precise gap and do not create source. "
              "Do not ask for an invented product decision.\n\nHandoff:\n"
              + json.dumps(evidence, ensure_ascii=False, indent=2))
    if source:
        prompt += "\nExisting package source is evidence to investigate, not an answer to preserve: " + str(source)
    async def turn(message):
        nonlocal done
        identity = receipt.get("author_session")
        done = asyncio.get_running_loop().create_future()
        async with asyncio.timeout(timeout):
            await child.send(message)
            await done
        if receipt.get("author_session") != identity:
            raise OSError("The author session changed during visual review.")

    try:
        async with asyncio.timeout(timeout):
            await child.open("You are an independent human-package-author producing source for the existing package factory.")
            await child.send(prompt)
            await done
        if not receipt.get("author_session") or receipt["author_session"] == session:
            raise OSError("Author did not report a distinct fresh session identity.")
        if (folder / "author-blocked.md").exists():
            raise OSError("Author needs evidence: " + str(folder / "author-blocked.md"))
        candidates = [p for p in (folder / "content.json", folder / "package") if p.exists()]
        if document is not None and candidates != [folder / "package"]:
            raise OSError("Author must produce package/UNDERSTANDING.md, and no content.json, in "
                          + str(folder))
        if len(candidates) != 1:
            raise OSError("Author must produce exactly one content.json or package/ source in " + str(folder))
        result = candidates[0]
        if result.is_symlink() or not result.resolve().is_relative_to(folder.resolve()):
            raise OSError("Author source must belong to its fresh output folder.")
        receipt.update(status="authored", source=str(result))
        (folder / "author-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if finish is not None:
            receipt["visual_review"] = await finish(result, turn)
        return result
    except Refused as error:
        receipt.update(status="failed", error="; ".join(error.problems))
        raise
    except (OSError, RuntimeError, TimeoutError) as error:
        receipt.update(status="failed", error=str(error) or "Author timed out.")
        raise Refused(["Fresh author did not finish: " + receipt["error"]]) from None
    finally:
        if not done.done():
            done.cancel()
        elif not done.cancelled():
            done.exception()  # retrieve an early-exit error even when open itself raised
        await child.close(5)
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


async def review(source: Path, archive: Path, result, turn, folder: Path) -> dict:
    """Ask the still-open author to inspect built bytes, then validate its receipt.

    A machine result never attests to content inside a picture. The author's
    response records that inspection separately, including supplied attachments.
    """
    from jarvis import bundle_archive
    with bundle_archive.open_archive(archive) as z:
        listing = bundle_archive.list_members(z)
        members = sorted(name[len(listing.root):] for name in listing.files()
                         if not name[len(listing.root):].startswith(".jarvis/"))
    request = {
        "archive": str(archive), "archive_sha256": author_proof._sha(archive),
        "source_sha256": author_proof.source_digest(source),
        "screenshots": {str(p): author_proof._sha(p) for p in result.screenshots},
        "members": members, "machine_passed": result.ok, "failures": result.failures,
    }
    folder.mkdir(parents=True, exist_ok=True)
    request_path, response_path = folder / "visual-request.json", folder / "visual-response.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n")
    request_digest = author_proof._sha(request_path)
    response_path.unlink(missing_ok=True)
    await turn(
        "Inspect the rendered package before delivery. You are the SAME fresh author. "
        "Read " + str(request_path) + ". Open EVERY screenshot listed there with your existing "
        "image-viewing tool (Claude Read or Codex view_image). Judge whether illustrations appear, "
        "their text is readable and unclipped, and they communicate the view's point. "
        "Inspect the actual built archive, not merely your source. Use existing browser/document "
        "tools for each supplied image/document page intended for human viewing. No new readers "
        "or OCR. Use the story and author understanding to identify all intended views, including "
        "tabs and expanded views. If a page view is missing, repair its story show/click actions "
        "so the factory renders it on the next attempt. Decorative and unused assets are not "
        "reader pages. If any intended view cannot be inspected, name that exact missing check. "
        "A machine failure needs repair even if the screenshots look right. Do not deliver. "
        "Write JSON to " + str(response_path) + " with request_sha256 equal to " + request_digest
        + ", status (accepted, not-applicable, repair, or blocked), notes (one nonempty text string, "
        "not a list, containing your specific findings), screenshots (the full list of screenshot paths you actually inspected), visual_material "
        "(boolean), and members (an object with EVERY request member path as a key). Each member "
        "value has use (reader, support, decorative, or unused), notes, and evidence (a list of "
        "screenshot paths or existing tool/page references for EVERY intended reader page). "
        "Use not-applicable only if the package's contents have no visual material; explain why. "
        "Support files need no separate reader inspection. An accepted result must leave the "
        "source, archive and screenshots unchanged. For repair, edit only your source/assets, "
        "run the supplied check-only command after your final edit, and return status repair. "
        "The factory allows one repair and re-render; after that it reports the exact failure."
    )
    try:
        response = json.loads(response_path.read_text())
    except (OSError, ValueError) as error:
        raise Refused([f"Author visual review is missing or unreadable: {error}"]) from None
    if not isinstance(response, dict) or response.get("request_sha256") != request_digest:
        raise Refused(["Author visual review does not name this rendered attempt."])
    status = response.get("status")
    if status in ("repair", "blocked"):
        return {**request, "review": response}
    problems = []
    if status not in ("accepted", "not-applicable"):
        problems.append("Author visual review has no acceptance.")
    if not isinstance(response.get("notes"), str) or not response["notes"].strip():
        problems.append("Author visual review has no inspection findings.")
    if not isinstance(response.get("screenshots"), list) or \
            not all(isinstance(p, str) for p in response["screenshots"]) or \
            sorted(response["screenshots"]) != sorted(request["screenshots"]):
        problems.append("Author did not inspect every rendered screenshot.")
    if type(response.get("visual_material")) is not bool or \
            (status == "not-applicable") != (response.get("visual_material") is False):
        problems.append("Author must base not-applicable on the package's actual contents.")
    reviewed = response.get("members")
    if not isinstance(reviewed, dict) or set(reviewed) != set(members):
        problems.append("Author did not account for every built member and its intended reader views.")
    else:
        for name, detail in reviewed.items():
            if not isinstance(detail, dict) or detail.get("use") not in \
                    ("reader", "support", "decorative", "unused") or not detail.get("notes"):
                problems.append(f"Author did not explain the intended use of {name}.")
            elif detail["use"] == "reader" and (not isinstance(detail.get("evidence"), list)
                    or not detail["evidence"] or not all(isinstance(e, str) and e.strip()
                                                        for e in detail["evidence"])):
                problems.append(f"Author did not inspect the intended reader pages of {name}.")
    if not result.ok:
        problems.extend(result.failures)
    if author_proof._sha(archive) != request["archive_sha256"] or \
            author_proof.source_digest(source) != request["source_sha256"] or any(
                not Path(p).is_file() or author_proof._sha(Path(p)) != digest
                for p, digest in request["screenshots"].items()):
        problems.append("The source, built bytes or screenshots changed after rendering; review is invalid.")
    if problems:
        raise Refused(problems)
    return {**request, "review": response}
