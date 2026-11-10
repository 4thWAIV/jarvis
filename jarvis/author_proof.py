"""Proof that a fresh author session wrote an agent's package (Tim's
decision `receiver-check`: verify).

`make_package --handoff` has one fresh author session (package_author.py,
Claude or Codex) write the package's source. The author ends by checking
that source with `make_package <source> --check-only`, which prints the
source's digest (`MARK`) only when it passes. The factory then puts the
authored source itself in the zip (`.jarvis/author/`) and the sending
agent's signature names the claim (`author` in agent-signature.json: the
engine, the author session and the source's digest).

The receiver (server._admit_agent_package) recomputes the digest from the
zip's own `.jarvis/author/` bytes and reads the author session's own
transcript (Claude Code's `projects/*/<session>.jsonl`, or Codex's
rollout) to confirm, bounded and with no link followed:

- it is a fresh author: its first prompt is the factory's author prompt
  (`PROMPT_HEAD`), and it is not the session that sent the package;
- it ran `make_package ... --check-only` itself (an assistant tool call)
  and that very call's result printed exactly this digest, within
  `WINDOW_SEC` before the package arrived.

What it proves is what the transcripts prove everywhere in JARVIS: the
author session's own record shows the act. It says nothing about whether
the package is good.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from jarvis import bundle_archive, codex_sessions, session_message_sender, session_watch

# What `--check-only` prints, followed by the source's digest.
MARK = "jarvis-author-source sha256:"
# Where the authored source sits in the package, beside its signature.
FOLDER = (".jarvis", "author")
# How the factory's author prompt begins: a fresh author's first prompt.
PROMPT_HEAD = "Use the human-package-author skill for this authorized source-only assignment."
ENGINES = ("claude", "codex")
# How long before arrival the author's check may have run.
WINDOW_SEC = 6 * 3600.0
# How long after arrival a held package is looked at again (a transcript
# not yet flushed or a Codex thread not yet recorded).
RECHECK_SEC = 10 * 60.0
# Clocks of two processes on one Mac: how far after arrival a line may be.
SLACK_SEC = 60.0
TAIL_BYTES = 8 * 1024 * 1024
HEAD_BYTES = 256 * 1024
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_LEFT_OUT = {bundle_archive.METADATA_FILE, bundle_archive.METADATA_FOLDER}


@dataclass(frozen=True)
class Claim:
    """What the signature says of its author."""
    engine: str
    session: str
    source: str            # the authored source's digest

    def as_dict(self) -> dict:
        return {"engine": self.engine, "session": self.session, "source": self.source}

    @classmethod
    def of(cls, value) -> Optional["Claim"]:
        """The claim a signature's `author` names, or None when it is not
        one JARVIS reads."""
        if not isinstance(value, dict):
            return None
        engine, session, source = (str(value.get(k) or "") for k in
                                   ("engine", "session", "source"))
        if engine not in ENGINES or not _SESSION.fullmatch(session) or \
                not re.fullmatch(r"[0-9a-f]{64}", source):
            return None
        return cls(engine, session, source)


# -- the source and its digest ----------------------------------------------------------

def digest_of(files: dict[str, str]) -> str:
    """One digest over (relative path, SHA-256) pairs, sorted by path."""
    whole = hashlib.sha256()
    for path in sorted(files):
        whole.update(f"{path}\0{files[path]}\n".encode())
    return whole.hexdigest()


def source_files(source: Path) -> dict[str, Path]:
    """The authored source's files by their path in the zip's author
    folder: `content.json`, or `package/<every plain file>`. A link, or
    anything that is not a plain file, is refused (ValueError)."""
    if source.is_symlink():
        raise ValueError(f"{source} is a link.")
    if source.is_file():
        return {source.name: source}
    if not source.is_dir():
        raise ValueError(f"There is no source at {source}.")
    found: dict[str, Path] = {}
    for root, dirs, names in os.walk(source, followlinks=False):
        here = Path(root)
        for d in list(dirs):
            if (here / d).is_symlink():
                raise ValueError(f"{here / d} is a link.")
        dirs[:] = sorted(d for d in dirs if d not in _LEFT_OUT)
        for name in sorted(names):
            path = here / name
            if name in _LEFT_OUT:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{path} is not a plain file.")
            found[f"{source.name}/{path.relative_to(source).as_posix()}"] = path
    return found


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(source: Path) -> str:
    """The digest `--check-only` prints and the claim names."""
    return digest_of({name: _sha(path) for name, path in source_files(source).items()})


def attach(archive: Path, source: Path) -> str:
    """Put the authored source in the zip under `.jarvis/author/`; its digest."""
    from jarvis import package_signature
    files = source_files(source)
    package_signature.add_files(archive, {"/".join(FOLDER) + "/" + name: path.read_bytes()
                                          for name, path in files.items()})
    return digest_of({name: _sha(path) for name, path in files.items()})


def digest_in_zip(zip_path: Path) -> Optional[str]:
    """The digest of the authored source the zip carries, from its own
    bytes, or None when it carries none."""
    prefix = "/".join(FOLDER) + "/"
    try:
        with bundle_archive.open_archive(zip_path) as archive:
            listing = bundle_archive.list_members(archive)
            root = listing.root
            files = {path[len(root) + len(prefix):]: m for path, m in listing.files().items()
                     if path.startswith(root + prefix)}
            if not files:
                return None
            return digest_of({name: bundle_archive.digest(archive, m)
                              for name, m in files.items()})
    except (bundle_archive.ArchiveError, OSError, zipfile.BadZipFile):
        return None


# -- the author's own transcript ---------------------------------------------------------

def _plain(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
    except OSError:
        return False


def _first_line(path: Path) -> dict:
    return session_message_sender._first_object(path)


def transcript(claim: Claim) -> Optional[Path]:
    """The author session's own transcript file, found by its id, or None."""
    if claim.engine == "claude":
        for root in session_watch.config_roots():
            projects = Path(root) / "projects"
            if not projects.is_dir():
                continue
            for path in sorted(projects.glob(f"*/{claim.session}.jsonl")):
                if _plain(path):
                    return path
        return None
    home = codex_sessions.codex_home()
    try:
        thread = codex_sessions.threads(home).get(claim.session) or {}
    except Exception:
        thread = {}
    if thread.get("rollout_path") and _plain(Path(thread["rollout_path"])):
        return Path(thread["rollout_path"])
    for path in sorted((home / "sessions").glob(f"*/*/*/rollout-*-{claim.session}.jsonl")):
        payload = _first_line(path).get("payload") or {}
        if _plain(path) and isinstance(payload, dict) and payload.get("id") == claim.session:
            return path
    return None


def _head_objects(path: Path) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(HEAD_BYTES)
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _first_prompt(objs: list[dict]) -> str:
    for obj in objs:
        if obj.get("type") == "user":
            if session_watch._starts_a_turn(obj):
                return session_watch._user_text(obj)
        elif obj.get("type") == "response_item":
            said = codex_sessions._message(obj)
            if said and said[0] == "user":
                return said[1]
    return ""


def fresh(path: Path) -> bool:
    """Whether the transcript is a fresh author's: its first prompt is the
    factory's author prompt."""
    return _first_prompt(_head_objects(path)).lstrip().startswith(PROMPT_HEAD)


def _when(obj: dict) -> Optional[float]:
    stamp = obj.get("timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return float(stamp)
    return session_watch.iso_seconds(stamp)


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(v.get("text") if isinstance(v, dict) else v) for v in value)
    if isinstance(value, dict):
        return _text(value.get("output") or value.get("content") or value.get("text") or "")
    return ""


def _calls(obj: dict) -> Iterator[tuple[str, str]]:
    """(call id, its input as text) of each tool call the assistant makes
    on this line: Claude Code's tool_use, Codex's function or shell call."""
    if obj.get("type") == "assistant" and not obj.get("isSidechain"):
        content = (obj.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                yield str(block["id"]), json.dumps(block.get("input"), ensure_ascii=False)
    elif obj.get("type") == "response_item":
        p = obj.get("payload") or {}
        if isinstance(p, dict) and p.get("type") in ("function_call", "custom_tool_call",
                                                     "local_shell_call") and p.get("call_id"):
            raw = p.get("arguments", p.get("input", p.get("action")))
            yield str(p["call_id"]), raw if isinstance(raw, str) else json.dumps(raw)


def _results(obj: dict) -> Iterator[tuple[str, str]]:
    """(call id, its output as text) of each tool result on this line."""
    if obj.get("type") == "user" and not obj.get("isSidechain"):
        content = (obj.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_result":
                yield str(block.get("tool_use_id") or ""), _text(block.get("content"))
    elif obj.get("type") == "response_item":
        p = obj.get("payload") or {}
        if isinstance(p, dict) and p.get("type") in ("function_call_output",
                                                     "custom_tool_call_output"):
            yield str(p.get("call_id") or ""), _text(p.get("output"))


def _is_check(call: str) -> bool:
    return "make_package" in call and "--check-only" in call


def checked(path: Path, digest: str, at: float) -> bool:
    """Whether the transcript's end shows the session's own check-only run
    printing exactly `digest`, within WINDOW_SEC before `at`."""
    checks: set[str] = set()
    want = MARK + digest
    for obj in session_watch.tail_objects(path, TAIL_BYTES):
        for cid, call in _calls(obj):
            if _is_check(call):
                checks.add(cid)
        for cid, output in _results(obj):
            when = _when(obj)
            if cid in checks and want in output and when is not None and \
                    at - WINDOW_SEC <= when <= at + SLACK_SEC:
                return True
    return False


# -- the one check -----------------------------------------------------------------------

NO_CLAIM = ("It carries no proof that a fresh author wrote it: an agent's package is made "
            "with python -m jarvis.make_package --handoff, which has a fresh author session "
            "write it.")


def problem(author, sender_session: str, zip_path: Path, at: float,
            used: Callable[[Claim], bool] = lambda claim: False) -> Optional[str]:
    """Why this agent-signed package is not proven written by a fresh author
    (in words Tim reads), or None. `author` is its signature's claim as
    signed; `used(claim)` says whether that author already vouched for
    other bytes."""
    if author is None:
        return NO_CLAIM
    claim = Claim.of(author)
    if claim is None:
        return "Its author proof is not one JARVIS reads."
    if claim.session == sender_session:
        return ("Its author is the session that sent it, not a fresh author session.")
    found = digest_in_zip(zip_path)
    if found is None:
        return "It does not carry the source its author wrote."
    if found != claim.source:
        return "The source it carries is not the source its author proof names."
    path = transcript(claim)
    if path is None:
        return (f"No {claim.engine} transcript of its author session {claim.session} is on "
                f"this Mac.")
    if not fresh(path):
        return f"Its author session {claim.session} was not started as a fresh package author."
    if not session_message_sender._showed(("author", claim.source, int(at)), path,
                                          path.lstat(), lambda p: checked(p, claim.source, at)):
        return (f"Its author session {claim.session}'s own transcript does not show it checking "
                f"exactly this source in the {int(WINDOW_SEC // 3600)} hours before it arrived.")
    if used(claim):
        return (f"Its author session {claim.session} already vouched for another package; a "
                f"new revision needs a fresh author.")
    return None
