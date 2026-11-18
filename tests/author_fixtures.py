"""A fresh author's own transcript, as Claude Code or Codex writes it, and
a package signed as make_package signs one after its fresh author: what
every test of an onboarded agent's package admits it with
(author_proof.py). Transcripts go under the test's Claude roots
(session_watch.config_roots, which conftest points at a temporary folder)
or its Codex home (codex_sessions.codex_home, likewise)."""
from __future__ import annotations

import itertools
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from jarvis import author_proof, codex_sessions, package_signature, session_watch

_authors = itertools.count()


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def lines(engine: str, session: str, digest: str, at: float,
          prompt: str = author_proof.PROMPT_HEAD + " You are the fresh author.",
          printed: str | None = None, ran: str = "python -m jarvis.make_package "
                                                   "presentation.yaml --check-only") -> list[dict]:
    """The author's transcript: its first prompt, its own check-only call and
    that call's result printing `printed` (the digest's line by default)."""
    printed = author_proof.MARK + digest if printed is None else printed
    stamp = _iso(at)
    if engine == "claude":
        return [
            {"type": "user", "sessionId": session, "timestamp": stamp,
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "sessionId": session, "timestamp": stamp,
             "message": {"content": [{"type": "tool_use", "id": "toolu_check", "name": "Bash",
                                      "input": {"command": ran}}]}},
            {"type": "user", "sessionId": session, "timestamp": stamp,
             "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_check",
                                      "content": f"The content is right.\n{printed}\n"}]}}]
    return [
        {"type": "session_meta", "timestamp": stamp, "payload": {"id": session}},
        {"type": "response_item", "timestamp": stamp, "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text",
                                                            "text": prompt}]}},
        {"type": "response_item", "timestamp": stamp, "payload": {
            "type": "function_call", "name": "shell", "call_id": "call_check",
            "arguments": json.dumps({"command": ["bash", "-lc", ran]})}},
        {"type": "response_item", "timestamp": stamp, "payload": {
            "type": "function_call_output", "call_id": "call_check",
            "output": json.dumps({"output": f"The content is right.\n{printed}\n"})}}]


def transcript_path(engine: str, session: str, cwd: str = "/tmp/author") -> Path:
    if engine == "claude":
        return (session_watch.config_roots()[0] / "projects" / session_watch.encode_cwd(cwd)
                / f"{session}.jsonl")
    return (codex_sessions.codex_home() / "sessions" / "2026" / "11" / "09"
            / f"rollout-2026-11-09T00-00-00-{session}.jsonl")


def write_transcript(engine: str, session: str, digest: str, at: float | None = None,
                     path: Path | None = None, **kw) -> Path:
    path = path or transcript_path(engine, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o) + "\n"
                            for o in lines(engine, session, digest,
                                           time.time() if at is None else at, **kw)))
    return path


def new_session(engine: str = "claude") -> str:
    return f"author-{engine}-{next(_authors):04d}"


def authored(source: Path, engine: str = "claude", session: str | None = None,
             **kw) -> author_proof.Claim:
    """`source` as a fresh author left it: its receipt beside it
    (package_author's) and its own transcript showing its last check."""
    session = session or new_session(engine)
    write_briefing(source.parent)
    (source.parent / "author-receipt.json").write_text(json.dumps(
        {"engine": engine, "author_session": session, "status": "authored",
         "source": str(source)}))
    claim = author_proof.Claim(engine, session, author_proof.source_digest(source))
    write_transcript(engine, session, claim.source, **kw)
    return claim


def sign_authored(raw: Path, target: Path, agent, session: str, statements=(),
                  engine: str = "claude", content: str = '{"title": "Notes"}',
                  claim: dict | None = None, transcript: bool = True,
                  work_key: dict | None = None, **kw) -> dict:
    """Sign the zip at `raw` into `target` as make_package does after a fresh
    author: the authored source inside it, its transcript written, and the
    claim in the signature (or `claim` as given). The claim signed."""
    if raw != target:
        shutil.copyfile(raw, target)
    folder = target.parent / f"authored-{next(_authors):04d}"
    folder.mkdir()
    source = folder / "content.json"
    source.write_text(content)
    digest = author_proof.attach(target, source)
    if claim is None:
        claim = author_proof.Claim(engine, new_session(engine), digest).as_dict()
        if transcript:
            write_transcript(engine, claim["session"], digest, **kw)
    package_signature.sign(target, target, agent.private, agent.public, session,
                           tuple(statements), work_key, author=claim)
    return claim


def write_briefing(folder: Path) -> None:
    """The prep, script and edits a fresh author leaves beside its source
    (briefing_prep), which every package carries."""
    from jarvis import briefing_prep
    write_prep(folder)
    (folder / briefing_prep.EDITS).write_text("## round 1, cut\n\nNo changes.\n")


def write_prep(folder: Path, script: str = "## Slide 1. Approve the result\n") -> None:
    """A prep with every section the pipeline asks for, and its script."""
    from jarvis import briefing_prep
    folder.mkdir(parents=True, exist_ok=True)
    (folder / briefing_prep.PREP).write_text("# Prep\n\n" + "".join(
        f"## {section}\n\nFact (source: receipt.json).\n\n"
        for section in briefing_prep.PREP_SECTIONS))
    (folder / briefing_prep.SCRIPT).write_text(script)


def passing_reply(prompt: str, edits: dict | None = None) -> str | None:
    """What a stand-in engine answers an ask of briefing_prep: each editor
    proposes `edits` by editor id (none by default) and every judge passes;
    None for a prompt that is no such ask."""
    from jarvis import briefing_prep
    for editor in briefing_prep.EDITORS:
        if f"You are the {editor.id} editor" in prompt:
            return json.dumps((edits or {}).get(editor.id) or {"edits": []})
    for question in briefing_prep.QUESTIONS.values():
        if question.asks in prompt:
            answer = (question.at_least if question.kind == briefing_prep.SCALE
                      else question.passing[0])
            return json.dumps({"answer": answer, "why": "it passes"})
    return None


def passing_judges(edits: dict | None = None):
    """A stand-in for briefing_prep.ASK answering as `passing_reply`. The
    prompts it was given are on `.prompts`."""
    prompts: list[str] = []

    async def ask(prompt, choice, folder, timeout=0, seat=None):
        prompts.append(prompt)
        reply = passing_reply(prompt, edits)
        if reply is None:
            raise AssertionError("an ask the stand-in does not know")
        return reply
    ask.prompts = prompts
    return ask
