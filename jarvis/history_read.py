"""Searching and reading JARVIS's OWN earlier conversations. Read-only.

Tim lost a conversation to a context clear and the brain had no way to look
it up. Two places hold what was said:

* the brain provider's own transcripts (`brain_provider.HistorySource`): for
  Claude Code, the `*.jsonl` of the brain's working directory,
  `<claude config>/projects/<encoded brain home>/` — one file per generation,
  so a cleared or rotated conversation is still on disk, and
* the Markdown under `JARVIS_DATA_DIR`: `journal/`, `memory/`, `projects/`.

Nothing else is reachable from here, and that is the whole security story.
`repo_read.resolve_within` cannot be reused for it: it exists to REFUSE
JARVIS's data directory, and these folders are inside it. So the wall is
rebuilt from the same parts — realpath containment against a short list of
allowed roots, then `repo_read.sensitive_reason` on the path below the root —
and the allowed roots are the folders themselves, never the data directory,
so `tool-token`, `mcp.json`, `connections.json` and `jarvis.db` stay closed.

Everything returned is somebody's words (the user's, a session's, a page the
brain once read), so the caller wraps it as untrusted. No model, no
subprocess, no network, no new dependency.
"""

import json
import os
import re
import time
from collections import deque
from pathlib import Path

from jarvis import brain_provider
from jarvis import data_paths
from jarvis import repo_read
from jarvis.repo_read import Refused
from jarvis.session_watch import config_roots, encode_cwd

MAX_HITS = 20                  # hits reported in all
MAX_HITS_PER_SOURCE = 3        # so one busy file cannot crowd out the rest
SNIPPET_LEAD = 70              # characters of context before a hit
SNIPPET_TAIL = 90              # ...and after it
MAX_QUERY_CHARS = 200
SEARCH_SECONDS = 3.0
SEARCH_BYTE_BUDGET = 256 * 1024 * 1024
TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024    # a bigger one is not rendered
LINE_CHARS = 500               # a long message is shown as lines this long

NOTE_DIRS = ("journal", "memory", "projects")
NOTE_SUFFIX = ".md"
TRANSCRIPT_LABEL = "transcript"


def _history() -> brain_provider.HistorySource:
    """Where the running brain provider keeps its transcripts, and how to
    read them."""
    return brain_provider.provider(
        os.getenv("JARVIS_BRAIN_PROVIDER", brain_provider.DEFAULT_PROVIDER)).history()


def claude_roots() -> list[Path]:
    """Every Claude config directory whose `projects/` may hold the brain's
    transcripts: the CLI's own `CLAUDE_CONFIG_DIR` first, then the roster
    roots `session_watch` already reads. Tests replace this."""
    roots: list[Path] = []
    env = os.getenv("CLAUDE_CONFIG_DIR")
    if env:
        roots.append(Path(env).expanduser())
    for root in config_roots():
        if root not in roots:
            roots.append(root)
    return roots


def transcript_dirs() -> list[Path]:
    """The brain's own transcript folders, those that exist. The brain home
    is spelled both as given and fully resolved, because the CLI encodes
    whichever its cwd turned out to be."""
    home = data_paths.brain_home()
    spellings = {encode_cwd(str(home)), encode_cwd(os.path.realpath(str(home)))}
    found: list[Path] = []
    for root in claude_roots():
        for spelling in sorted(spellings):
            d = root / "projects" / spelling
            if d.is_dir() and d not in found:
                found.append(d)
    return found


def note_roots() -> dict[str, Path]:
    home = data_paths.brain_home()
    return {name: home / name for name in NOTE_DIRS}


# --- the wall ---------------------------------------------------------------

def _contained(root: Path, candidate: Path, suffix: str) -> Path:
    """The real path of `candidate`, if it is a `suffix` file inside `root`
    and not a credential — else `Refused`. Both sides are resolved first, so
    `..`, an absolute path elsewhere and a symlink out of `root` all fail the
    same way."""
    real_root = Path(os.path.realpath(str(root)))
    real = Path(os.path.realpath(str(candidate)))
    if real_root not in real.parents:
        raise Refused("outside")
    if real.suffix.lower() != suffix:
        raise Refused("outside")
    if repo_read.sensitive_reason(real.relative_to(real_root)):
        raise Refused("sensitive")
    return real


def _roots() -> list[tuple[str, Path, str]]:
    """(label, directory, suffix) for everything readable."""
    history = _history()
    out = [(TRANSCRIPT_LABEL, d, history.suffix) for d in history.folders()]
    out += [(name, d, NOTE_SUFFIX) for name, d in note_roots().items()]
    return out


def source_id(label: str, root: Path, real: Path) -> str:
    """The name a source goes by in a hit and in `read_history`."""
    if label == TRANSCRIPT_LABEL:
        return f"{TRANSCRIPT_LABEL}/{real.stem}"
    return f"{label}/{real.relative_to(Path(os.path.realpath(str(root)))).as_posix()}"


def resolve(source: str) -> tuple[str, Path]:
    """(source id, real path) for a source as `search_history` printed it —
    or an absolute path, or a bare transcript name — else `Refused`."""
    raw = (source or "").strip()
    if not raw or "\x00" in raw:
        raise Refused("outside")
    roots = _roots()

    candidates: list[tuple[str, Path, str, Path]] = []
    path = Path(raw).expanduser()
    if path.is_absolute():
        candidates = [(l, r, s, path) for l, r, s in roots]
    else:
        first, _, rest = raw.partition("/")
        if first in NOTE_DIRS:
            candidates = [(l, r, s, r / rest) for l, r, s in roots if l == first]
        else:
            name = rest if first == TRANSCRIPT_LABEL else raw
            if "/" in name or name in ("", ".", ".."):
                raise Refused("outside")
            suffix = _history().suffix
            if not name.endswith(suffix):
                name += suffix
            candidates = [(l, r, s, r / name) for l, r, s in roots
                          if l == TRANSCRIPT_LABEL]
        if first in NOTE_DIRS and not rest.endswith(NOTE_SUFFIX):
            candidates = [(l, r, s, p.with_name(p.name + NOTE_SUFFIX))
                          for l, r, s, p in candidates]

    refusal: Refused | None = None
    for label, root, suffix, candidate in candidates:
        try:
            real = _contained(root, candidate, suffix)
        except Refused as r:
            # A credential outranks "not there": say the more guarded thing.
            if refusal is None or str(r) == "sensitive":
                refusal = r
            continue
        if real.is_file():
            return source_id(label, root, real), real
        refusal = refusal or Refused("missing")
    raise refusal or Refused("outside")


def _listing() -> list[tuple[str, Path, str]]:
    """(source id, path, kind) of every readable file, newest first. Anything
    the wall would refuse is simply not listed."""
    found: list[tuple[float, str, Path, str]] = []
    for label, root, suffix in _roots():
        try:
            names = list(root.rglob(f"*{suffix}"))
        except OSError:
            continue
        for candidate in names:
            try:
                real = _contained(root, candidate, suffix)
                if not real.is_file():
                    continue
                found.append((real.stat().st_mtime,
                              source_id(label, root, real), real,
                              "transcript" if label == TRANSCRIPT_LABEL else "note"))
            except (Refused, OSError):
                continue
    found.sort(key=lambda f: f[0], reverse=True)
    seen: set[Path] = set()
    out = []
    for _mtime, sid, real, kind in found:
        if real not in seen:
            seen.add(real)
            out.append((sid, real, kind))
    return out


# --- transcripts: user and assistant text only --------------------------------

def claude_message_text(obj) -> tuple[str, str] | None:
    """(role, text) of a Claude Code transcript line that is something SAID,
    else None. Tool calls, tool results, thinking, sidechains and meta lines
    are noise. Read through `claude_provider.ClaudeTranscripts`."""
    if not isinstance(obj, dict) or obj.get("type") not in ("user", "assistant"):
        return None
    if obj.get("isSidechain") or obj.get("isMeta"):
        return None
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str))
    else:
        return None
    text = text.strip()
    return (obj["type"], text) if text else None


def _when(obj: dict, fallback: str) -> str:
    stamp = obj.get("timestamp")
    if isinstance(stamp, str) and len(stamp) >= 16:
        return stamp[:16].replace("T", " ")
    return fallback


def _day(path: Path) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))


def _render(when: str, role: str, text: str) -> list[str]:
    """One message as display lines: `[when] role: first line`, then the rest,
    each cut at LINE_CHARS. The one place line numbers are decided."""
    lines: list[str] = []
    for i, line in enumerate(text.splitlines() or [""]):
        if i == 0:
            line = f"[{when}] {role}: {line}"
        lines.extend(line[n:n + LINE_CHARS] for n in range(0, max(len(line), 1), LINE_CHARS))
    return lines


def said_lines(obj: dict, role: str, text: str, fallback: str) -> list[str]:
    """One message of a transcript as display lines, the way read_history
    shows it; for any reader of a transcript (folder_readers.py)."""
    return _render(_when(obj, fallback), role, text)


def _messages(path: Path):
    """(first display line, when, role, text) per message, streamed."""
    fallback = _day(path)
    history = _history()
    number = 1
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            said = history.message(obj)
            if not said:
                continue
            role, text = said
            when = _when(obj, fallback)
            yield number, when, role, text
            number += len(_render(when, role, text))


def _transcript_lines(path: Path) -> list[str]:
    if path.stat().st_size > TRANSCRIPT_MAX_BYTES:
        raise Refused("huge")
    lines: list[str] = []
    for _n, when, role, text in _messages(path):
        lines.extend(_render(when, role, text))
    return lines


# --- search ------------------------------------------------------------------

class Hits:
    def __init__(self):
        self.lines: list[str] = []
        self.capped = False
        self.sources = 0


def _snippet(text: str, match: re.Match) -> str:
    start = max(0, match.start() - SNIPPET_LEAD)
    end = min(len(text), match.end() + SNIPPET_TAIL)
    body = " ".join(text[start:end].split())
    return ("…" if start else "") + body + ("…" if end < len(text) else "")


def _note_hits(path: Path, pattern, day: str):
    """(line, when, role, text) for matching lines of a Markdown file."""
    if path.stat().st_size > repo_read.READ_MAX_FILE_BYTES:
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), start=1):
        match = pattern.search(line)
        if match:
            yield number, day, "", _snippet(line, match)


def _transcript_hits(path: Path, pattern):
    for number, when, role, text in _messages(path):
        match = pattern.search(text)
        if match:
            yield number, when, role, _snippet(text, match)


def search(query: str) -> Hits:
    """Case-insensitive literal search, newest source first, at most MAX_HITS
    in all and MAX_HITS_PER_SOURCE from any one source (the latest ones)."""
    hits = Hits()
    query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return hits
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    deadline = time.monotonic() + SEARCH_SECONDS
    budget = SEARCH_BYTE_BUDGET

    for sid, path, kind in _listing():
        if time.monotonic() > deadline or budget <= 0:
            hits.capped = True
            break
        try:
            size = path.stat().st_size
            budget -= size
            day = _day(path)
            found = (_transcript_hits(path, pattern) if kind == "transcript"
                     else _note_hits(path, pattern, day))
            kept = deque(found, maxlen=MAX_HITS_PER_SOURCE)
        except OSError:
            continue
        hits.sources += 1
        for number, when, role, snippet in reversed(kept):
            who = f"{role}: " if role else ""
            hits.lines.append(f"{sid}:{number}: {when}: {who}{snippet}")
            if len(hits.lines) >= MAX_HITS:
                hits.capped = True
                return hits
    return hits


# --- read --------------------------------------------------------------------

def read_window(path: Path, around=None) -> repo_read.Window:
    """A bounded window on one resolved source. A transcript is read as its
    spoken lines (see `_render`), not as raw JSON, so line numbers match the
    ones `search` printed."""
    if path.suffix.lower() == _history().suffix:
        return repo_read.window_of_lines(_transcript_lines(path), around)
    return repo_read.read_window(path, around)
