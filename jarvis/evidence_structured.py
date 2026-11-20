"""
evidence_structured.py — what a program reads in a package's gathered
evidence before any model does (workflow.yaml mark, approved by Tim
2026-11-18: "3. I approve it."): each file's type, the facts of a
structured file, and the prose a model is given.

- `type_of` (type_files): a type from the file's content and name, one of
  test results, build log, CI run, diff or commit, session transcript, data
  and prose; anything else is NO_READER ("no reader"), never guessed.
- `read_structured` (read_structured): that type's facts, each with its
  file position (`Fact`): failed tests with their error, file and line;
  build errors and the failed step; CI jobs with their result and times;
  the files a diff changes; the commands a session ran and their outcome;
  the named fields of data. A value is the file's own characters at its
  position.
- `prose_of`: the prose a model is given (extract_prose, mark_prose): a
  prose file whole, and the prose parts of every other file, each piece
  mapped back to its file position (`ProseView.to_file`).

Markdown is read by its fenced blocks: a block's info string says what it
holds (`json`, `diff`, else a log), and the text outside the blocks is the
file's prose. A file JARVIS wrote while gathering (`retrieved/`, `jarvis/`)
opens with its title and its `Source:` line; outside a prose file the
headings and that line are JARVIS's, not prose. A log or transcript in a
Markdown file with no fenced block is all log.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from typing import Iterator, Optional

TEST_RESULTS, BUILD_LOG, CI_RUN, DIFF, TRANSCRIPT, DATA, PROSE = (
    "test_results", "build_log", "ci_run", "diff", "session_transcript", "data", "prose")
TYPES = (TEST_RESULTS, BUILD_LOG, CI_RUN, DIFF, TRANSCRIPT, DATA, PROSE)
NO_READER = "no reader"

# Files JARVIS writes as it gathers (package_evidence's layout).
HANDOFF = "handoff.json"
SENDER_SESSION = "jarvis/sender-session.md"
TIM_RECORD = "jarvis/tim.md"
WORK_ITEM_RECORD = "jarvis/work-item"
SENDER_ANSWERS = "sender-answers/"
WRITTEN_BY_JARVIS = ("retrieved/", "jarvis/")
# The sender's own words in its handoff: its two answers, its reason and its
# recommendation (mark_prose: "the sender's reason and situation").
HANDOFF_PROSE = ("sender_answers.plainly", "sender_answers.why_now", "purpose",
                 "recommendation")
# The retrieved files' types, by the evidence type they were read as.
_RETRIEVED = {"pr": DIFF, "commit": DIFF, "ci_run": CI_RUN, "issue": PROSE,
              "work_item": PROSE, "branch": DATA, "package": DATA, "message": DATA,
              "workflow": DATA, "run": DATA}


@dataclass(frozen=True)
class Fact:
    """One fact a program read: the field it is, the file's own characters
    at [start, end), and the facts it belongs with (one failed test, one
    job, one command)."""
    field: str
    value: str
    start: int
    end: int
    group: Optional[str] = None


def _suffix(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def is_markdown(stored: str) -> bool:
    return _suffix(stored) in ("md", "markdown")


# ── values with their positions ───────────────────────────────────────────

@dataclass(frozen=True)
class Leaf:
    """One scalar of a data file: its named field, and its span in the text
    (a string's span is its characters inside the quotes). `chars` is a
    string's raw offset of each of its decoded characters, then its end."""
    path: str
    start: int
    end: int
    decoded: object = None
    chars: Optional[tuple[int, ...]] = None


_WS = " \t\r\n"
_JSON_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t"}


class _Json:
    """JSON read with the position of every scalar."""

    def __init__(self, text: str, end: int):
        self.t, self.end, self.leaves = text, end, []

    def ws(self, i: int) -> int:
        while i < self.end and self.t[i] in _WS:
            i += 1
        return i

    def at(self, i: int) -> str:
        return self.t[i] if i < self.end else ""

    def value(self, i: int, path: str) -> int:
        i = self.ws(i)
        c = self.at(i)
        if c in ("{", "["):
            close, n = ("}" if c == "{" else "]"), 0
            i = self.ws(i + 1)
            if self.at(i) == close:
                return i + 1
            while True:
                if c == "{":
                    i = self.ws(i)
                    if self.at(i) != '"':
                        raise ValueError(f"a key at {i}")
                    key, i, _ = self.string(i)
                    i = self.ws(i)
                    if self.at(i) != ":":
                        raise ValueError(f"':' at {i}")
                    i = self.value(i + 1, f"{path}.{key}" if path else key)
                else:
                    i = self.value(i, f"{path}[{n}]")
                    n += 1
                i = self.ws(i)
                if self.at(i) == ",":
                    i += 1
                    continue
                if self.at(i) == close:
                    return i + 1
                raise ValueError(f"',' or {close!r} at {i}")
        if c == '"':
            decoded, j, chars = self.string(i)
            self.leaves.append(Leaf(path or "value", i + 1, j - 1, decoded, chars))
            return j
        m = _JSON_SCALAR.match(self.t, i, self.end)
        if not m:
            raise ValueError(f"a value at {i}")
        self.leaves.append(Leaf(path or "value", i, m.end(), json.loads(m.group(0))))
        return m.end()

    def string(self, i: int) -> tuple[str, int, tuple[int, ...]]:
        out: list[str] = []
        chars: list[int] = []
        j = i + 1
        while j < self.end:
            c = self.t[j]
            if c == '"':
                chars.append(j)
                return "".join(out), j + 1, tuple(chars)
            if c == "\\":
                e = self.t[j + 1:j + 2]
                if e == "u":
                    code, n = int(self.t[j + 2:j + 6], 16), 6
                    if 0xD800 <= code < 0xDC00 and self.t[j + 6:j + 8] == "\\u":
                        low = int(self.t[j + 8:j + 12], 16)
                        if 0xDC00 <= low < 0xE000:
                            code, n = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00), 12
                    out.append(chr(code))
                elif e in _ESCAPES:
                    out.append(_ESCAPES[e])
                    n = 2
                else:
                    raise ValueError(f"an escape at {j}")
                chars.append(j)
                j += n
                continue
            out.append(c)
            chars.append(j)
            j += 1
        raise ValueError("a string not closed")


def json_leaves(text: str, start: int = 0, end: Optional[int] = None) -> list[Leaf]:
    """Every scalar of the JSON document at text[start:end], with its span.
    Raises ValueError when it is not one JSON document."""
    end = len(text) if end is None else end
    scanner = _Json(text, end)
    i = scanner.ws(scanner.value(start, ""))
    if i != end:
        raise ValueError(f"more after the document at {i}")
    return scanner.leaves


def yaml_leaves(text: str) -> list[Leaf]:
    """Every scalar of a YAML document, with its span. Raises ValueError
    when it is not one YAML document."""
    import yaml
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        raise ValueError(str(e)) from e
    out: list[Leaf] = []

    def walk(node, path: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                walk(value, f"{path}.{key.value}" if path else str(key.value))
        elif isinstance(node, yaml.SequenceNode):
            for n, value in enumerate(node.value):
                walk(value, f"{path}[{n}]")
        elif isinstance(node, yaml.ScalarNode):
            out.append(Leaf(path or "value", node.start_mark.index, node.end_mark.index,
                            node.value))
    if root is not None:
        walk(root, "")
    return out


def table_leaves(text: str, delimiter: str) -> list[Leaf]:
    """Every cell of a CSV or TSV table below its header row, named by its
    row and its column's header, with its span; a quoted cell whose text is
    not the file's own (an escaped quote) is passed over."""
    out: list[Leaf] = []
    header: Optional[list[str]] = None
    pos = 0
    for n, line in enumerate(text.splitlines(keepends=True)):
        body = line.rstrip("\r\n")
        cells = next(csv.reader([body], delimiter=delimiter), [])
        if header is None:
            header = cells
        else:
            cursor = 0
            for k, cell in enumerate(cells):
                at = body.find(cell, cursor) if cell else -1
                if at < 0:
                    continue
                name = header[k] if k < len(header) and header[k] else f"column {k + 1}"
                out.append(Leaf(f"row {n}.{name}", pos + at, pos + at + len(cell), cell))
                cursor = at + len(cell)
        pos += len(line)
    return out


# ── Markdown's fenced blocks ──────────────────────────────────────────────

_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_HEADING = re.compile(r"\s{0,3}#{1,6}(\s|$)")


@dataclass(frozen=True)
class Fence:
    info: str                       # its info string's first word, lowercased
    start: int                      # its content
    end: int
    outer_start: int                # its fence lines included
    outer_end: int


def _lines_at(text: str, start: int = 0,
              end: Optional[int] = None) -> Iterator[tuple[int, str, int]]:
    """(offset, line without its line break, the next line's offset) of each
    line of text[start:end]."""
    end = len(text) if end is None else end
    pos = start
    for line in text[start:end].splitlines(keepends=True):
        yield pos, line.rstrip("\r\n"), pos + len(line)
        pos += len(line)


def _lines(text: str, start: int = 0, end: Optional[int] = None) -> Iterator[tuple[int, str]]:
    """(offset, line without its line break) of each line of text[start:end]."""
    for pos, line, _ in _lines_at(text, start, end):
        yield pos, line


def fences(text: str) -> list[Fence]:
    """Every fenced block of a Markdown text; one not closed runs to the end."""
    out: list[Fence] = []
    opened: Optional[tuple[str, int, str, int]] = None   # marker, outer start, info, content
    for pos, line, after in _lines_at(text):
        m = _FENCE.match(line)
        if opened is None:
            if m:
                info = m.group(2).strip().split(" ")[0].lower()
                opened = (m.group(1), pos, info, after)
        elif m and m.group(1)[0] == opened[0][0] and len(m.group(1)) >= len(opened[0]) \
                and not m.group(2).strip():
            end = pos
            while end > opened[3] and text[end - 1] in "\r\n":
                end -= 1
            out.append(Fence(opened[2], opened[3], end, opened[1], after))
            opened = None
    if opened is not None:
        out.append(Fence(opened[2], opened[3], len(text), opened[1], len(text)))
    return out


# ── the type of a file (type_files) ───────────────────────────────────────

_DIFF = re.compile(r"^diff --git a/\S+ b/\S+$|^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.M)
_TESTS = re.compile(
    r"^=+ .*\b\d+ (?:passed|failed|errors?)\b.* =+$"         # pytest's summary line
    r"|^(?:FAILED|ERROR) \S+::\S+"                            # pytest's short summary
    r"|^\s*Tests?:?\s+.*\b\d+ (?:failed|passed)\b"            # jest and vitest
    r"|^\s*--- FAIL: \S+|^(?:ok|FAIL)\s+\S+\s+[\d.]+s$"       # go test
    r"|^test result: (?:ok|FAILED)\."                         # cargo test
    r"|<testsuites?\b", re.M)                                 # JUnit XML
_BUILD = re.compile(
    r"##\[(?:group|error)\]|^npm ERR! |^error(?:\[E\d+\])?: |\berror TS\d+: "
    r"|make(?:\[\d+\])?: \*\*\* |^BUILD FAILED|:\d+:\d+: (?:fatal )?error: "
    r"|^[^\t\n]+\t[^\t\n]+\t\d{4}-\d\d-\d\dT\S+ ", re.M)       # gh run view --log-failed
_TEXT_SUFFIXES = ("", "txt", "text", "md", "markdown", "rst", "log", "out")
_LOG_SUFFIXES = ("log", "out")


def _is_transcript(text: str) -> bool:
    from jarvis import session_transcript
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            return False
        if isinstance(obj, dict) and (session_transcript.said(obj) is not None
                                      or any(True for _ in session_transcript.calls(obj))):
            return True
    return False


def _sniff(stored: str, text: str) -> str:
    """A file the sender gave: its type from its name and content."""
    suffix = _suffix(stored)
    if suffix == "json":
        try:
            json_leaves(text)
        except ValueError:
            return NO_READER
        return DATA
    if suffix in ("jsonl", "ndjson"):
        if _is_transcript(text):
            return TRANSCRIPT
        try:
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
        except ValueError:
            return NO_READER
        return DATA
    if suffix in ("csv", "tsv"):
        return DATA
    if suffix in ("yaml", "yml"):
        try:
            yaml_leaves(text)
        except ValueError:
            return NO_READER
        return DATA
    if suffix == "xml":
        return TEST_RESULTS if re.search(r"<testsuites?\b", text) else NO_READER
    if suffix in ("diff", "patch"):
        return DIFF
    if suffix not in _TEXT_SUFFIXES:
        return NO_READER
    if _DIFF.search(text):
        return DIFF
    if _TESTS.search(text):
        return TEST_RESULTS
    if _BUILD.search(text):
        return BUILD_LOG
    return NO_READER if suffix in _LOG_SUFFIXES else PROSE


def type_of(stored: str, row_type: str, data: bytes) -> str:
    """The type of one gathered file (type_files), from where it was stored,
    what it was gathered as, and its content; NO_READER when no reader here
    knows it."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return NO_READER
    if stored == HANDOFF:
        return DATA
    if stored == SENDER_SESSION:
        return TRANSCRIPT
    if stored == TIM_RECORD or stored.startswith(WORK_ITEM_RECORD) \
            or stored.startswith(SENDER_ANSWERS):
        return PROSE
    if stored.startswith("retrieved/") and row_type in _RETRIEVED:
        return _RETRIEVED[row_type]
    return _sniff(stored, text)


# ── the facts of a structured file (read_structured) ──────────────────────

class _Facts:
    """Facts as they are read, each group numbered in the order found."""

    def __init__(self, text: str):
        self.text, self.out, self._groups = text, [], 0

    def group(self, kind: str) -> str:
        self._groups += 1
        return f"{kind} {self._groups}"

    def add(self, field: str, start: int, end: int, group: Optional[str]) -> None:
        if end > start:
            self.out.append(Fact(field, self.text[start:end], start, end, group))

    def span(self, field: str, m: re.Match, name: str, base: int, group: Optional[str]) -> None:
        if m.group(name) is not None:
            self.add(field, base + m.start(name), base + m.end(name), group)


_PYTEST_FAILED = re.compile(
    r"^(?:FAILED|ERROR) (?P<test>(?P<file>[^\s:]+)::\S+)(?: - (?P<error>.+))?$", re.M)
_PYTEST_SECTION = re.compile(r"^_{3,} (?P<test>\S.*?) _{3,}$", re.M)
_PYTEST_WHERE = re.compile(r"^(?P<file>[^\s:]+\.\w+):(?P<line>\d+): (?P<error>\S.*)$", re.M)
_SECTION_END = re.compile(r"^=+ .* =+$", re.M)
_GO_FAIL = re.compile(r"^\s*--- FAIL: (?P<test>\S+)", re.M)
_GO_WHERE = re.compile(r"^\s+(?P<file>[\w./-]+_test\.go):(?P<line>\d+): (?P<error>.+)$", re.M)
_JEST_FAIL = re.compile(r"^\s*(?:●|×|✕|FAIL\s+(?=\S+\.(?:test|spec)\.[cm]?[jt]sx?))\s*"
                        r"(?P<test>\S.*?)\s*$", re.M)
_JEST_WHERE = re.compile(r"\(?(?P<file>[^\s():]+\.[jt]sx?):(?P<line>\d+):\d+\)?")
_CARGO_FAIL = re.compile(r"^test (?P<test>\S+) \.\.\. FAILED$", re.M)
_CARGO_WHERE = re.compile(r"panicked at (?P<file>[^\s:]+):(?P<line>\d+):\d+:?\n(?P<error>.+)$",
                          re.M)
_JUNIT_CASE = re.compile(r"<testcase\b(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</testcase>)", re.S)
_JUNIT_FAIL = re.compile(r"<(?:failure|error)\b(?P<attrs>[^>]*)", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _attrs(f: _Facts, attrs: str, base: int, names: dict[str, str], group: str) -> None:
    for m in _ATTR.finditer(attrs):
        if m.group(1) in names:
            f.add(names[m.group(1)], base + m.start(2), base + m.end(2), group)


def _tests(f: _Facts, start: int, end: int) -> None:
    """Failed tests with their error, file and line. A log as gh prints a
    run's failed steps (each line led by its job, step and time) is read a
    line at a time, after that lead."""
    led = [(at, body) for at, body in _log_bodies(f.text, start, end) if body is not None]
    if led:
        for at, body in led:
            for pattern in (_PYTEST_FAILED, _GO_FAIL, _CARGO_FAIL, _JEST_FAIL):
                m = pattern.search(body)
                if m:
                    g = f.group("failed test")
                    for name, field in (("test", "failed_test"), ("file", "file"),
                                        ("error", "error")):
                        if name in pattern.groupindex:
                            f.span(field, m, name, at, g)
                    break
        return
    region = f.text[start:end]
    for m in _PYTEST_FAILED.finditer(region):
        g = f.group("failed test")
        f.span("failed_test", m, "test", start, g)
        f.span("file", m, "file", start, g)
        f.span("error", m, "error", start, g)
    sections = list(_PYTEST_SECTION.finditer(region))
    for n, m in enumerate(sections):
        stop = sections[n + 1].start() if n + 1 < len(sections) else len(region)
        closing = _SECTION_END.search(region, m.end(), stop)
        stop = closing.start() if closing else stop
        g = f.group("failed test")
        f.span("failed_test", m, "test", start, g)
        where = list(_PYTEST_WHERE.finditer(region, m.end(), stop))
        if where:
            for name, field in (("file", "file"), ("line", "line"), ("error", "error")):
                f.span(field, where[-1], name, start, g)
    for fail, where_re in ((_GO_FAIL, _GO_WHERE), (_CARGO_FAIL, _CARGO_WHERE),
                           (_JEST_FAIL, _JEST_WHERE)):
        found = list(fail.finditer(region))
        for n, m in enumerate(found):
            g = f.group("failed test")
            f.span("failed_test", m, "test", start, g)
            stop = found[n + 1].start() if n + 1 < len(found) else len(region)
            where = where_re.search(region, m.end(), stop)
            if where:
                f.span("file", where, "file", start, g)
                f.span("line", where, "line", start, g)
                if "error" in where_re.groupindex:
                    f.span("error", where, "error", start, g)
    for m in _JUNIT_CASE.finditer(region):
        body = m.group("body") or ""
        failed = _JUNIT_FAIL.search(body)
        if not failed:
            continue
        g = f.group("failed test")
        _attrs(f, m.group("attrs"), start + m.start("attrs"),
               {"name": "failed_test", "file": "file", "line": "line"}, g)
        _attrs(f, failed.group("attrs"), start + m.start("body") + failed.start("attrs"),
               {"message": "error"}, g)


_GH_LOG = re.compile(r"^(?P<job>[^\t\n]+)\t(?P<step>[^\t\n]+)\t\d{4}-\d\d-\d\dT\S+ (?P<rest>.*)$")


def _log_bodies(text: str, start: int, end: int) -> Iterator[tuple[int, Optional[str]]]:
    """Each line of a log as gh prints a run's failed steps: (where its words
    start, its words after the job, step and time that lead it), or (its
    offset, None) for a line not led so."""
    for pos, line in _lines(text, start, end):
        m = _GH_LOG.match(line)
        yield (pos + m.start("rest"), m.group("rest")) if m else (pos, None)
_GROUP = re.compile(r"##\[group\](?P<step>.+)$")
_ERRORS = (re.compile(r"##\[error\](?P<e>.+)$"),
           re.compile(r"(?P<e>\S.*?:\d+:\d+: (?:fatal )?error: .+)$"),
           re.compile(r"^(?P<e>error(?:\[E\d+\])?: .+)$"),
           re.compile(r"(?P<e>\S.*?error TS\d+: .+)$"),
           re.compile(r"^(?P<e>npm ERR! .+)$"),
           re.compile(r"(?P<e>make(?:\[\d+\])?: \*\*\* .+)$"),
           re.compile(r"^(?P<e>BUILD FAILED.*)$"))


def _build(f: _Facts, start: int, end: int) -> None:
    """Build errors, and the step that failed."""
    seen_steps: set[tuple[str, str]] = set()
    group_step: Optional[tuple[int, int]] = None
    for pos, line in _lines(f.text, start, end):
        body, at = line, pos
        gh = _GH_LOG.match(line)
        if gh:
            key = (gh.group("job"), gh.group("step"))
            if key not in seen_steps:
                seen_steps.add(key)
                g = f.group("failed step")
                f.add("job", pos + gh.start("job"), pos + gh.end("job"), g)
                f.add("failed_step", pos + gh.start("step"), pos + gh.end("step"), g)
            body, at = gh.group("rest"), pos + gh.start("rest")
        grouped = _GROUP.search(body)
        if grouped:
            group_step = (at + grouped.start("step"), at + grouped.end("step"))
        for pattern in _ERRORS:
            m = pattern.search(body)
            if m:
                g = f.group("build error")
                f.add("build_error", at + m.start("e"), at + m.end("e"), g)
                if group_step is not None and pattern is _ERRORS[0]:
                    f.add("failed_step", group_step[0], group_step[1], g)
                break


_JOB = re.compile(r"^jobs\[(\d+)\]\.(name|conclusion|startedAt|completedAt)$")
_STEP = re.compile(r"^jobs\[(\d+)\]\.steps\[(\d+)\]\.(name|conclusion)$")
_JOB_FIELDS = {"name": "job", "conclusion": "result", "startedAt": "started",
               "completedAt": "finished"}


def _ci_run(f: _Facts, leaves: list[Leaf]) -> None:
    """A CI run's jobs with their result and times, and each step that
    failed."""
    steps: dict[tuple[str, str], dict[str, Leaf]] = {}
    for leaf in leaves:
        m = _JOB.match(leaf.path)
        if m:
            f.add(_JOB_FIELDS[m.group(2)], leaf.start, leaf.end, f"job {int(m.group(1)) + 1}")
            continue
        m = _STEP.match(leaf.path)
        if m:
            steps.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = leaf
    for (job, _), step in steps.items():
        if "name" in step and step.get("conclusion") is not None \
                and step["conclusion"].decoded == "failure":
            f.add("failed_step", step["name"].start, step["name"].end, f"job {int(job) + 1}")


def _data(f: _Facts, leaves: list[Leaf]) -> None:
    """The named fields of data."""
    for leaf in leaves:
        f.add(leaf.path, leaf.start, leaf.end, None)


_DIFF_FILE = re.compile(r"^diff --git a/\S+ b/(?P<file>\S+)$", re.M)
_PLUS_FILE = re.compile(r"^\+\+\+ b/(?P<file>\S+)$", re.M)


def _diff(f: _Facts, start: int, end: int) -> None:
    """The files a diff changes."""
    region = f.text[start:end]
    found = list(_DIFF_FILE.finditer(region)) or list(_PLUS_FILE.finditer(region))
    for m in found:
        f.span("file_changed", m, "file", start, None)


_COMMAND = re.compile(r"^### \d+\. (?P<command>.+)$", re.M)


def _session_md(f: _Facts, blocks: list[Fence]) -> None:
    """The sending session JARVIS gathered: each command, and its outcome,
    the block under it."""
    for m in _COMMAND.finditer(f.text):
        g = f.group("command")
        f.add("command", m.start("command"), m.end("command"), g)
        block = next((b for b in blocks if b.outer_start >= m.end()), None)
        if block is not None:
            f.add("outcome", block.start, block.end, g)


def _encoded_at(text: str, start: int, end: int, value: str) -> Optional[tuple[int, int]]:
    """Where a decoded string stands in a JSON line, as the line writes it."""
    for form in (json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1]):
        at = text.find(form, start, end) if form else -1
        if at >= 0:
            return at, at + len(form)
    return None


def _session_jsonl(f: _Facts) -> None:
    """A session's own transcript: each command it ran, and its outcome."""
    from jarvis import session_transcript
    from jarvis.package_gather import COMMAND_TOOLS
    by_call: dict[str, str] = {}
    for pos, line in _lines(f.text):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        end = pos + len(line)
        for cid, tool, given in session_transcript.calls(obj):
            if tool not in COMMAND_TOOLS:
                continue
            try:
                value = json.loads(given)
            except ValueError:
                value = given
            command = value.get("command", value.get("cmd")) if isinstance(value, dict) else value
            if not isinstance(command, str):
                continue
            at = _encoded_at(f.text, pos, end, command)
            if at:
                g = by_call[cid] = f.group("command")
                f.add("command", *at, g)
        for cid, output in session_transcript.results(obj):
            at = _encoded_at(f.text, pos, end, output) if cid in by_call else None
            if at:
                f.add("outcome", *at, by_call[cid])
        run = session_transcript.command_run(obj)
        if run is not None:
            g = f.group("command")
            for field, value in (("command", run[0]), ("outcome", run[1])):
                at = _encoded_at(f.text, pos, end, value)
                if at:
                    f.add(field, *at, g)


def _data_leaves(stored: str, text: str) -> list[Leaf]:
    suffix = _suffix(stored)
    try:
        if suffix == "json":
            return json_leaves(text)
        if suffix in ("jsonl", "ndjson"):
            out = []
            for n, (pos, line) in enumerate(_lines(text), 1):
                if line.strip():
                    out += [Leaf(f"line {n}.{leaf.path}", leaf.start, leaf.end, leaf.decoded,
                                 leaf.chars) for leaf in json_leaves(text, pos, pos + len(line))]
            return out
        if suffix in ("yaml", "yml"):
            return yaml_leaves(text)
        if suffix in ("csv", "tsv"):
            return table_leaves(text, "," if suffix == "csv" else "\t")
    except ValueError:
        return []
    return []


_LOGS = {TEST_RESULTS: (_tests,), BUILD_LOG: (_build,), CI_RUN: (_build, _tests)}


def read_structured(stored: str, file_type: str, text: str) -> list[Fact]:
    """The facts of one file of a structured type, in the order found."""
    f = _Facts(text)
    if file_type == NO_READER or (file_type == PROSE and not is_markdown(stored)):
        return f.out
    if not is_markdown(stored):
        if file_type == DATA:
            _data(f, _data_leaves(stored, text))
        elif file_type == TRANSCRIPT:
            _session_jsonl(f)
        elif file_type == DIFF:
            _diff(f, 0, len(text))
        else:
            for reader in _LOGS.get(file_type, ()):
                reader(f, 0, len(text))
        return f.out
    blocks = fences(text)
    if file_type == TRANSCRIPT:
        _session_md(f, blocks)
    if not blocks and file_type in (TEST_RESULTS, BUILD_LOG, DIFF):
        if file_type == DIFF:
            _diff(f, 0, len(text))
        else:
            for reader in _LOGS[file_type]:
                reader(f, 0, len(text))
    for block in blocks:
        if block.info == "json":
            try:
                leaves = json_leaves(text, block.start, block.end)
            except ValueError:
                continue
            (_ci_run if file_type == CI_RUN else _data)(f, leaves)
        elif block.info in ("diff", "patch") or file_type == DIFF:
            _diff(f, block.start, block.end)
        elif file_type not in (TRANSCRIPT, PROSE):
            for reader in _LOGS.get(file_type, ()):
                reader(f, block.start, block.end)
    return f.out


# ── the prose a model is given ────────────────────────────────────────────

@dataclass(frozen=True)
class Piece:
    """One stretch of prose: its text and where it stands in the file. A
    piece decoded from a JSON string maps each character to its raw
    offset (`chars`, then its end)."""
    text: str
    start: int
    chars: Optional[tuple[int, ...]] = None

    def raw(self, i: int) -> int:
        return self.start + i if self.chars is None else self.chars[i]


SEPARATOR = "\n\n"


@dataclass(frozen=True)
class ProseView:
    """A file's prose pieces as one text, the pieces a paragraph apart."""
    pieces: tuple[Piece, ...]

    @property
    def text(self) -> str:
        return SEPARATOR.join(p.text for p in self.pieces)

    def _offsets(self) -> list[int]:
        out, at = [], 0
        for p in self.pieces:
            out.append(at)
            at += len(p.text) + len(SEPARATOR)
        return out

    def to_file(self, start: int, end: int) -> Optional[tuple[int, int]]:
        """The file position of [start, end) of `text`; None when it is not
        inside the pieces (it runs over a separator's edge)."""
        offsets = self._offsets()
        first = last = None
        for k, (p, at) in enumerate(zip(self.pieces, offsets)):
            if at <= start < at + len(p.text):
                first = k
            if at < end <= at + len(p.text):
                last = k
        if first is None or last is None or last < first:
            return None
        return (self.pieces[first].raw(start - offsets[first]),
                self.pieces[last].raw(end - offsets[last]))


def _trim(text: str, start: int, end: int) -> Optional[Piece]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Piece(text[start:end], start) if end > start else None


def _outside(text: str, blocks: list[Fence], *, keep_headings: bool,
             drop_source: bool) -> list[Piece]:
    """The text outside the fenced blocks, as pieces: cut at each heading
    and the `Source:` line when they are not kept."""
    pieces: list[Piece] = []
    cuts = [(b.outer_start, b.outer_end) for b in blocks]
    gaps, at = [], 0
    for a, b in cuts:
        gaps.append((at, a))
        at = b
    gaps.append((at, len(text)))
    source_dropped = not drop_source
    for a, b in gaps:
        run: Optional[int] = None
        for pos, line in _lines(text, a, b):
            dropped = (not keep_headings and _HEADING.match(line)) or \
                (not source_dropped and line.startswith("Source: "))
            if dropped:
                if line.startswith("Source: "):
                    source_dropped = True
                if run is not None:
                    piece = _trim(text, run, pos)
                    pieces += [piece] if piece else []
                run = None
            elif run is None:
                run = pos
        if run is not None:
            piece = _trim(text, run, b)
            pieces += [piece] if piece else []
    return pieces


def prose_of(stored: str, file_type: str, text: str) -> ProseView:
    """The prose of one file (extract_prose, mark_prose): a prose file whole
    (outside its fenced blocks); the sender's own words in its handoff; and
    the prose parts of every other Markdown file, its words outside the
    fenced blocks of its log, transcript or record. A file with no reader
    has none."""
    if file_type == NO_READER:
        return ProseView(())
    if stored == HANDOFF:
        try:
            leaves = json_leaves(text)
        except ValueError:
            return ProseView(())
        return ProseView(tuple(Piece(str(leaf.decoded), leaf.start, leaf.chars) for leaf in leaves
                               if leaf.path in HANDOFF_PROSE and leaf.chars is not None
                               and str(leaf.decoded).strip()))
    ours = stored.startswith(WRITTEN_BY_JARVIS)
    if file_type == PROSE:
        if not is_markdown(stored):
            piece = _trim(text, 0, len(text))
            return ProseView((piece,) if piece else ())
        return ProseView(tuple(_outside(text, fences(text), keep_headings=True,
                                        drop_source=ours)))
    if not is_markdown(stored):
        return ProseView(())
    blocks = fences(text)
    if not blocks and file_type != DATA:
        return ProseView(())                # a log or transcript with no block is all log
    return ProseView(tuple(_outside(text, blocks, keep_headings=False, drop_source=ours)))
