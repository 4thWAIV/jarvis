"""The package steps' cache, keyed by their inputs (construction-drafts.md
"Step cache keyed by inputs", approved by Tim 2026-11-22: "Yes, GO"; the
standard design, as make and Bazel do it).

Every package step is a function of named inputs. A unit of work (`Unit`:
its kind, the name of what it makes within the kind, its inputs) has a key,
the SHA-256 of its inputs in canonical JSON (`key`): content hashes of the
files it reads, the outputs (by their digest) of the units it reads, and
the words it is given. Its output (files, and a value) is kept under that
key with how long it took (`keep`), in one folder a key under
`data_paths.package_cache_dir()`, shared by every package and run. A run
works out each unit's key and reuses the kept output when it exists
(`reuse`): only units whose inputs changed, and those that read them, run.

Model calls are not deterministic, so a key is of the inputs, never of the
output; a unit that reads another names that unit's output digest
(`Entry.digest`, worked out when the output was kept), so a unit made
again with a different output changes every unit after it. Only work that
finished is kept: a unit that failed or asked the sender and has not had
its answers yet keeps nothing (a failed action is never cached).

Each unit, reused or computed, is written into the run's timing.json
(run_report.unit): its kind and name, its key, `reused` or `computed`, its
seconds, and for a reused one the run that made it. A unit computed when
its key was already kept is `recomputed`: by construction it never
happens, and the tests fail any that does. A unit whose kept output was
rejected downstream (a kept deck source that no longer builds) is made
again and replaces it (`computed(..., replace=True)`, outcome `replaced`),
so the next run reuses the working one, never the rejected one again.

Every key covers the tool as well as the material, as make and Bazel do:
the source of the code that shapes the output (`code`, each module's
SHA-256, never a hand-raised number), and for a model step each role's
approved prompt, its tier, effort and release, and the engine and model
its engines.json kind chooses (`role_tool`). A changed prompt, role,
engine choice or line of code changes every key that depends on it.

Each entry's `used` file is touched whenever it is reused; entries unused
for `PRUNE_AFTER_SEC` (30 days) are removed at server start (`prune`).

The units (package_jobs.STEPS): `gather`; `mark-file` (one a file of the
evidence); `research`; the swarm's stages `swarm-outline`, `swarm-fill`,
`swarm-passes` and `swarm-revision` (one a revision), and `script-slide`
(one a slide); `deck-header`, `deck-source` and `deck-review`
(one a slide each), `deck-build` and `deck`; and `deliver`.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from jarvis import data_paths

log = logging.getLogger("jarvis.step_cache")

# The cache's own layout (entry.json and files/); what a unit computes is
# versioned by its code's hash in its key (`code`), never by hand.
VERSION = "step-cache/1"
REUSED, COMPUTED, RECOMPUTED, REPLACED = "reused", "computed", "recomputed", "replaced"
OUTCOMES = (REUSED, COMPUTED, RECOMPUTED, REPLACED)
ENTRY = "entry.json"
FILES = "files"
USED = "used"                           # touched each time the entry is reused
PRUNE_AFTER_SEC = 30 * 24 * 3600        # entries unused this long are removed at start


def canonical(value: Any) -> str:
    """A value as canonical JSON: keys sorted, no spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path: Path) -> Optional[str]:
    """A file's SHA-256, or None when it is not there."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def files_sha(folder: Path, names: Iterable[str]) -> dict[str, Optional[str]]:
    """Each named file's SHA-256 under `folder` (a folder's files, each)."""
    return {rel: file_sha(Path(folder) / rel) for rel in expand(folder, names, missing=True)}


# ── the tool, in every key ────────────────────────────────────────────────

_SOURCE: dict[tuple, str] = {}


def _path_sha(path: Path) -> Optional[str]:
    """A file's SHA-256, worked out once for its size and modification
    time."""
    try:
        st = path.stat()
    except OSError:
        return None
    mark = (str(path), st.st_mtime_ns, st.st_size)
    if mark not in _SOURCE:
        _SOURCE[mark] = file_sha(path) or ""
    return _SOURCE[mark]


def module_path(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin:
        raise ModuleNotFoundError(module)
    return Path(spec.origin)


def code(*modules: str) -> dict[str, Optional[str]]:
    """The code that shapes a unit's output, as its key names it: each
    module's source file's SHA-256 (the module itself, never a version
    number raised by hand)."""
    return {m: _path_sha(module_path(m)) for m in sorted(set(modules))}


def tree_sha(folder: Path, skip: tuple[str, ...] = ("__pycache__",)) -> dict[str, Optional[str]]:
    """Every file under a folder (a folder of shipped files, such as the
    shared deck), by its path in it, with its SHA-256."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    return {str(p.relative_to(folder)): _path_sha(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and not any(part in skip for part in p.relative_to(folder).parts)
            and not p.name.startswith(".")}


# The code every model step's output is shaped by: the sessions and their
# forms, the roles and their prompts, the tiers, the pages and working set
# its material is cut into, the text splitter and the labels.
MODEL_CODE = ("jarvis.sessions", "jarvis.roles", "jarvis.role_prompts", "jarvis.model_tiers",
              "jarvis.pages", "jarvis.working_set", "jarvis.text_parts", "jarvis.briefing_labels",
              "jarvis.engine_choice", "jarvis.step_cache")


def role_tool(name: str, *, requester: Optional[str] = None,
              author_engine: Optional[str] = None) -> dict:
    """A model role as a key names it: the role (tier, effort, kind,
    lifetime, release), its approved prompt (system and material, word for
    word, hashed), and the engine and model its engines.json kind chooses
    for this requester (engine_choice.choose; the owners on the other
    brain from the author's when Settings says so)."""
    from jarvis import engine_choice, role_prompts, roles
    role = roles.role(name)
    prompt = role_prompts.PROMPTS.get(name)
    try:
        choice = engine_choice.choose(role.kind, requester=requester)
        engine, model, why = choice.engine, choice.model, choice.why
    except Exception as e:  # noqa: BLE001 - an unreadable choice is itself named
        engine, model, why = None, None, f"unreadable: {e}"
    other = engine_choice.other_brain_for_owners(role.kind, author_engine)
    if other:
        engine, model, why = other, None, "owners_other_brain"
    return {"role": dataclasses.asdict(role),
            "prompt": digest(dataclasses.asdict(prompt)) if prompt is not None else None,
            "engine": engine, "model": model, "why": why}


def labels_key() -> str:
    """The 28 briefing labels as every marking reads them: each one's name,
    meaning, action, kind, origin and example (briefing_labels.LABELS)."""
    from jarvis import briefing_labels
    return digest([dataclasses.asdict(label) for label in briefing_labels.LABELS])


def roles_tool(names: Iterable[str], requester: Optional[str] = None,
               author: Optional[str] = "author") -> dict:
    """Several roles of one step (`role_tool`), the owners' engine worked
    out from the author's as the swarm picks it."""
    out = {}
    author_engine = None
    if author:
        out[author] = role_tool(author, requester=requester)
        author_engine = out[author]["engine"]
    for name in names:
        if name not in out:
            out[name] = role_tool(name, requester=requester, author_engine=author_engine)
    return out


@dataclass(frozen=True)
class Unit:
    """One unit of a package step's work: its kind, what it makes within
    the kind ("" for one a run), and its inputs."""
    kind: str
    name: str = ""
    inputs: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def key(self) -> str:
        return digest({"cache": VERSION, "kind": self.kind, "name": self.name,
                       "inputs": self.inputs})

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}" if self.name else self.kind


@dataclass
class Entry:
    """A unit's kept output: its files (each with its SHA-256), its value,
    its digest (of both), who made it and how long it took."""
    kind: str
    name: str
    key: str
    digest: str
    files: dict[str, str]
    value: Any
    made: dict
    seconds: float
    path: Path

    def file(self, rel: str) -> Path:
        return self.path / FILES / rel

    def text(self, rel: str) -> str:
        return self.file(rel).read_text(encoding="utf-8")


def root() -> Path:
    return data_paths.package_cache_dir()


def _dir(kind: str, key: str) -> Path:
    return root() / kind / key[:2] / key


def expand(folder: Path, names: Iterable[str], missing: bool = False) -> list[str]:
    """The files the names hold under `folder`, relative to it: a file
    itself, a folder every file under it (in order); a name not there is
    left out (with `missing`, kept)."""
    out: list[str] = []
    folder = Path(folder)
    for name in names:
        there = folder / name
        if there.is_dir():
            out += sorted(str(p.relative_to(folder)) for p in there.rglob("*") if p.is_file())
        elif there.is_file() or missing:
            out.append(str(name))
    return list(dict.fromkeys(out))


def lookup(unit: Unit) -> Optional[Entry]:
    """The unit's kept output, or None. One whose files are not all there
    (a cache cleared part way) is not kept."""
    path = _dir(unit.kind, unit.key)
    try:
        body = json.loads((path / ENTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if any(not (path / FILES / rel).is_file() for rel in body.get("files") or {}):
        return None
    return Entry(body["kind"], body.get("name") or "", body["key"], body["digest"],
                 dict(body.get("files") or {}), body.get("value"), dict(body.get("made") or {}),
                 float(body.get("seconds") or 0.0), path)


def restore(entry: Entry, folder: Path) -> list[str]:
    """The entry's files copied into `folder`, at the same relative paths;
    a file already the same is left. The paths."""
    folder = Path(folder)
    for rel, sha in entry.files.items():
        target = folder / rel
        if file_sha(target) == sha:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry.file(rel), target)
    return list(entry.files)


def _made(doc: Optional[dict]) -> dict:
    doc = doc or {}
    return {"job": doc.get("id"), "run": doc.get("run"), "package": doc.get("package"),
            "folder": doc.get("folder"), "at": time.time()}


def note(doc: Optional[dict], unit: Unit, outcome: str, seconds: float,
         entry: Optional[Entry] = None) -> None:
    """The unit, reused or computed, into the run's timing.json."""
    from jarvis import run_report
    record = {"kind": unit.kind, "name": unit.name, "key": unit.key, "outcome": outcome,
              "seconds": round(float(seconds), 3)}
    if entry is not None:
        record["digest"] = entry.digest
        if outcome == REUSED:
            record["made_by"] = {k: entry.made.get(k) for k in ("job", "run", "package")}
    if outcome == RECOMPUTED:
        log.error("step cache: %s recomputed with unchanged inputs (key %s)", unit.label,
                  unit.key)
    run_report.unit(record)


def keep(unit: Unit, doc: Optional[dict], *, folder: Optional[Path] = None,
         files: Iterable[str] = (), value: Any = None, seconds: float = 0.0,
         files_from: Optional[dict[str, bytes]] = None,
         replace: bool = False) -> tuple[str, Entry]:
    """The unit's output kept under its key: the named files of `folder`
    (a folder's files, each), or `files_from` ({relative path: bytes}), and
    a JSON value. (COMPUTED, the entry); (RECOMPUTED, the entry kept
    before) when one was kept already; with `replace` (the kept output was
    rejected downstream), the new output takes its place: (REPLACED, the
    new entry)."""
    there = lookup(unit)
    if there is not None and not replace:
        return RECOMPUTED, there
    final = _dir(unit.kind, unit.key)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{unit.key[:12]}-", dir=final.parent))
    shas: dict[str, str] = {}
    try:
        for rel in expand(folder, files) if folder is not None else []:
            target = tmp / FILES / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(folder) / rel, target)
            shas[rel] = file_sha(target) or ""
        for rel, data in (files_from or {}).items():
            target = tmp / FILES / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            shas[rel] = hashlib.sha256(data).hexdigest()
        body = {"kind": unit.kind, "name": unit.name, "key": unit.key,
                "digest": digest({"files": shas, "value": value}), "files": shas,
                "value": value, "inputs": unit.inputs, "made": _made(doc),
                "seconds": round(float(seconds), 3)}
        (tmp / ENTRY).write_text(json.dumps(body, indent=1, ensure_ascii=False, default=str),
                                 encoding="utf-8")
        (tmp / USED).touch()
        if replace and final.exists():
            # The rejected output moved aside, the new one put in its place.
            aside = final.parent / f".{unit.key[:12]}-rejected-{os.getpid()}-{time.time_ns()}"
            os.replace(final, aside)
            os.replace(tmp, final)
            shutil.rmtree(aside, ignore_errors=True)
            found = lookup(unit)
            assert found is not None
            return REPLACED, found
        try:
            os.replace(tmp, final)
        except OSError:
            # Another run kept it meanwhile: theirs stands.
            shutil.rmtree(tmp, ignore_errors=True)
            found = lookup(unit)
            if found is not None:
                return RECOMPUTED, found
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    found = lookup(unit)
    assert found is not None
    return COMPUTED, found


def reuse(unit: Unit, doc: Optional[dict], folder: Optional[Path] = None) -> Optional[Entry]:
    """The unit's kept output, restored into `folder` when given and noted
    as reused; None when nothing is kept under its key."""
    began = time.time()
    entry = lookup(unit)
    if entry is None:
        return None
    if folder is not None:
        restore(entry, folder)
    used(entry)
    note(doc, unit, REUSED, time.time() - began, entry)
    return entry


def used(entry: Entry) -> None:
    """The entry marked used now (its `used` file touched), for `prune`."""
    try:
        (entry.path / USED).touch()
    except OSError:
        pass


def computed(unit: Unit, doc: Optional[dict], began: float, seconds: Optional[float] = None,
             **kept) -> Entry:
    """The unit computed (since `began`, or in `seconds` when its own time
    is known apart from the batch it was computed in): its output kept and
    noted."""
    seconds = time.time() - began if seconds is None else seconds
    outcome, entry = keep(unit, doc, seconds=seconds, **kept)
    note(doc, unit, outcome, seconds, entry)
    return entry


def prune(older_than: float = PRUNE_AFTER_SEC, now: Optional[float] = None) -> int:
    """Every entry not used for `older_than` seconds removed (its `used`
    file, else its entry.json, says when it was last used), with any part
    kept by a run that stopped while keeping; how many were removed. Run
    at server start."""
    now = time.time() if now is None else now
    removed = 0
    base = root()
    if not base.is_dir():
        return 0
    for kind in base.iterdir():
        if not kind.is_dir():
            continue
        for shard in kind.iterdir():
            if not shard.is_dir():
                continue
            for entry in shard.iterdir():
                if not entry.is_dir():
                    continue
                mark = entry / USED if (entry / USED).exists() else entry / ENTRY
                try:
                    last = mark.stat().st_mtime
                except OSError:
                    last = entry.stat().st_mtime    # a part kept by a run that stopped
                if now - last > older_than:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
    return removed


def run(unit: Unit, doc: Optional[dict], compute: Callable[[], Any], *,
        folder: Optional[Path] = None, files: Iterable[str] = (),
        value: Optional[Callable[[Any], Any]] = None) -> tuple[str, Entry]:
    """The unit's output: reused when kept (restored into `folder`), else
    `compute()` run and its output kept (the named files of `folder`, and
    `value(result)` when given). (REUSED or COMPUTED, the entry)."""
    entry = reuse(unit, doc, folder)
    if entry is not None:
        return REUSED, entry
    began = time.time()
    result = compute()
    entry = computed(unit, doc, began, folder=folder, files=files,
                     value=value(result) if value else None)
    return COMPUTED, entry
