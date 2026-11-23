"""The package steps' cache, keyed by their inputs (construction-drafts.md
"Step cache keyed by inputs", approved by Tim 2026-11-22: "Yes, GO"; the
standard design, as make and Bazel do it).

Every package step is a function of named inputs. A unit of work (`Unit`:
its kind, the name of what it makes within the kind, its inputs) has a key,
the SHA-256 of its inputs in canonical JSON (`key`): content hashes of the
files it reads, the outputs (by their digest) of the units it reads, and
the words it is given. Its output (files, and a value) is kept under that
key with how long it took (`keep`), in one folder a key under the
package's own folder (`root`: `<package folder>/.step-cache/`, beside its
`evidence/` and `runs/`; never indexed, knowledge_content.run_bookkeeping).
The cache is one package's (Tim, 2026-11-22: "the step cache is scoped to
one package"; reusing work across two different packages has no value,
they are two different problems): every run of the same package (findings
answered, an Error run again, the sender's change, his Reject) reuses
whatever its changes did not touch, and a new package always starts
fresh, whatever its handoff. A run works out each unit's key and reuses
the kept output when it exists (`reuse`): only units whose inputs
changed, and those that read them, run.

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
the source of the code that shapes the output (`code`: each named
module's SHA-256 and every jarvis module it imports, at any depth, read
from the source by `ast` with no import run, so no list can miss one;
never a hand-raised number), and for a model step each role's
approved prompt, its tier, effort and release, and the engine and the
model that runs it (`role_tool`: model_tiers.pick's model, the one the
sessions are opened on, never the tier's name alone; on Codex the newest
of its live list). A model the brain refused in one of the package's runs
is kept in the package's cache (`refused`, `note_refused`, from
sessions.BrainChatSession's sweep through run_report.refused) and passed
over by every pick of the package after it (package_mark.pick_args), so
the key names the model the sweep ends on. A changed prompt, role, engine
choice, resolved model or line of code changes every key that depends on
it.

Each entry's `used` file is touched whenever it is reused; entries unused
for `PRUNE_AFTER_SEC` (30 days) are removed at server start (`prune`), and
so is the shared `<data>/package-cache` the cache was before.

The units (package_jobs.STEPS): `gather` (noted, never kept: a package's
evidence is gathered once, at its first run), with `gather-lookups` (what retrieval looked
up, kept as the lookups to run again) and `gather-labels` (retrieval's
answer, keyed on what those lookups find now); `mark-file` (one a file of
the evidence); `research`; the swarm's stages `swarm-outline`, `swarm-fill`, `swarm-passes` and
`swarm-revision` (one a revision); `deck-header`, `deck-source`
and `deck-review` (one a slide each), `deck-build` and `deck`;
and `deliver`.
"""
from __future__ import annotations

import ast
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
# The cache's folder in a package's own folder, and the models its runs
# found refused.
CACHE_DIR = ".step-cache"
REFUSED_FILE = "refused.json"
# Where the cache was before it was one package's: removed by `prune`.
SHARED_BEFORE = "package-cache"
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


_IMPORTS: dict[tuple, frozenset] = {}


def _source_of(module: str) -> Optional[Path]:
    """A module's source file found on disk from its top-level package's
    folder, never by importing it (a submodule's find_spec would run its
    parent's code); None for a name that is no module (a function or class
    imported from one) or that is not Python source."""
    top, _, rest = module.partition(".")
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    if not rest:
        return Path(spec.origin) if spec.origin and spec.origin.endswith(".py") else None
    for base in spec.submodule_search_locations or []:
        stem = Path(base).joinpath(*rest.split("."))
        for path in (stem.with_suffix(".py"), stem / "__init__.py"):
            if path.is_file():
                return path
    return None


def _imports(module: str, path: Path) -> frozenset:
    """Every module of the same top-level package the source imports,
    anywhere in it (at the top, in a function, under a condition), read
    with `ast` once for the file's size and modification time: `import
    a.b`, `from a import b` (a.b, which may be a module) and relative
    imports resolved against the module's own package."""
    try:
        st = path.stat()
    except OSError:
        return frozenset()
    mark = (str(path), st.st_mtime_ns, st.st_size, module)
    if mark in _IMPORTS:
        return _IMPORTS[mark]
    top = module.partition(".")[0]
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        tree = None
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                found.update(".".join(parts[:n]) for n in range(1, len(parts) + 1))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names
                             if alias.name != "*")
    out = frozenset(m for m in found if m == top or m.startswith(top + "."))
    _IMPORTS[mark] = out
    return out


def closure(*modules: str) -> dict[str, Path]:
    """The named modules and every module of their own top-level package
    they import, at any depth (`_imports`), each with its source file."""
    out: dict[str, Path] = {}
    todo = list(modules)
    while todo:
        name = todo.pop()
        if name in out:
            continue
        path = module_path(name) if name in modules else _source_of(name)
        if path is None:
            continue
        out[name] = path
        todo.extend(_imports(name, path))
    return out


def code(*modules: str) -> dict[str, Optional[str]]:
    """The code that shapes a unit's output, as its key names it: each
    named module and every module it imports at any depth (`closure`), by
    its source file's SHA-256 (the code itself, never a version number
    raised by hand)."""
    return {m: _path_sha(path) for m, path in sorted(closure(*set(modules)).items())}


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


# Each engine's live model list, read once a process for the keys (a
# Codex list is one app server call); the sessions read their own.
_LISTED: dict[str, list[str]] = {}


def _listed(engine: str) -> list[str]:
    from jarvis import model_tiers
    if engine not in _LISTED:
        _LISTED[engine] = list(model_tiers.listed_models(engine))
    return _LISTED[engine]


def role_tool(name: str, doc: Optional[dict], *, author_engine: Optional[str] = None,
              had: Optional[dict] = None) -> dict:
    """A model role as a key names it: the role (tier, effort, kind,
    lifetime, release), its approved prompt (system and material, word for
    word, hashed), and the engine and model that run it. A participant
    resumed on the session it had (`had`, its kept engine and model) runs
    on that model, whatever a pick says now; any other, model_tiers.pick
    for the run's requester, as the sessions pick it (the owners on the
    other brain from the author's when Settings says so), the tier
    resolved to its model on that brain (on Codex the newest its live list
    holds), the models this package's runs found refused passed over. Why
    it was picked is not named: a model picked and the same model resumed
    are one key."""
    from jarvis import model_tiers, role_prompts, roles
    role = roles.role(name)
    prompt = role_prompts.PROMPTS.get(name)
    requester = (doc or {}).get("engine")
    unreadable = None
    if had and had.get("engine") and had.get("model"):
        engine, model = had["engine"], had["model"]
    else:
        try:
            pick = model_tiers.pick(role.kind, role.tier, requester=requester,
                                    author_engine=author_engine, listed=_listed,
                                    refused=refused(doc) if doc else None)
            engine, model = pick.engine, pick.model
        except Exception as e:  # noqa: BLE001 - an unreadable choice is itself named
            engine, model, unreadable = None, None, str(e)
    return {"role": dataclasses.asdict(role),
            "prompt": digest(dataclasses.asdict(prompt)) if prompt is not None else None,
            "engine": engine, "model": model,
            **({"unreadable": unreadable} if unreadable is not None else {})}


def embedding_key() -> Optional[str]:
    """The engine's embedding model as a key names it (its vector space:
    the model and its pinned revision), None while none is installed: it
    groups by meaning what the swarm and the mark step put together."""
    from jarvis import embedding_models
    model = embedding_models.DEFAULT
    return model.space if embedding_models.model_problem(None, model) is None else None


def labels_key() -> str:
    """The 28 briefing labels as every marking reads them: each one's name,
    meaning, action, kind, origin and example (briefing_labels.LABELS)."""
    from jarvis import briefing_labels
    return digest([dataclasses.asdict(label) for label in briefing_labels.LABELS])


def roles_tool(names: Iterable[str], doc: Optional[dict],
               author: Optional[str] = "author", had: Optional[dict] = None) -> dict:
    """Several roles of one step (`role_tool`), the owners' engine worked
    out from the author's as the swarm picks it; `had`, each resumed
    participant's kept engine and model by name (the swarm's
    `participants`)."""
    had = had or {}
    out = {}
    author_engine = None
    if author:
        out[author] = role_tool(author, doc, had=had.get(author))
        author_engine = out[author]["engine"]
    for name in names:
        if name not in out:
            out[name] = role_tool(name, doc, author_engine=author_engine, had=had.get(name))
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


def root(doc: Optional[dict]) -> Path:
    """The package's own cache: `.step-cache/` in its folder (beside its
    evidence and runs). A unit is always one package's: a doc that names
    no package has none."""
    from jarvis import package_evidence
    if not doc or not doc.get("package"):
        raise ValueError("the step cache is one package's: the run names no package")
    return package_evidence.place_of(doc).folder.parent / CACHE_DIR


def _dir(doc: Optional[dict], kind: str, key: str) -> Path:
    return root(doc) / kind / key[:2] / key


def refused(doc: Optional[dict]) -> dict[str, list[str]]:
    """The models the package's runs found refused, by engine."""
    try:
        body = json.loads((root(doc) / REFUSED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): [str(m) for m in v] for k, v in body.items() if isinstance(v, list)}


def note_refused(doc: Optional[dict], engine: str, model: str) -> None:
    """A model the brain refused in one of the package's runs, kept so
    every later pick of the package passes over it, as the sweep did."""
    if not doc or not doc.get("package") or not model:
        return
    have = refused(doc)
    if model in have.get(engine, []):
        return
    have.setdefault(engine, []).append(model)
    path = root(doc) / REFUSED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(have, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


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


def lookup(unit: Unit, doc: Optional[dict]) -> Optional[Entry]:
    """The unit's kept output in the package's cache, or None. One whose
    files are not all there (a cache cleared part way) is not kept."""
    path = _dir(doc, unit.kind, unit.key)
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
    there = lookup(unit, doc)
    if there is not None and not replace:
        return RECOMPUTED, there
    final = _dir(doc, unit.kind, unit.key)
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
            found = lookup(unit, doc)
            assert found is not None
            return REPLACED, found
        try:
            os.replace(tmp, final)
        except OSError:
            # Another run kept it meanwhile: theirs stands.
            shutil.rmtree(tmp, ignore_errors=True)
            found = lookup(unit, doc)
            if found is not None:
                return RECOMPUTED, found
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    found = lookup(unit, doc)
    assert found is not None
    return COMPUTED, found


def reuse(unit: Unit, doc: Optional[dict], folder: Optional[Path] = None) -> Optional[Entry]:
    """The unit's kept output, restored into `folder` when given and noted
    as reused; None when nothing is kept under its key."""
    began = time.time()
    entry = lookup(unit, doc)
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


def caches() -> list[Path]:
    """Every package's cache on disk: under each Work item's packages and
    each package made for no Work item."""
    found = list(data_paths.work_dir().glob(f"*/packages/*/{CACHE_DIR}"))
    found += data_paths.package_dir().glob(f"*/{CACHE_DIR}")
    return sorted(p for p in found if p.is_dir())


def prune(older_than: float = PRUNE_AFTER_SEC, now: Optional[float] = None) -> int:
    """Every entry of every package's cache not used for `older_than`
    seconds removed (its `used` file, else its entry.json, says when it was
    last used), with any part kept by a run that stopped while keeping, and
    the shared cache of before (`SHARED_BEFORE`) removed whole; how many
    entries were removed. Run at server start."""
    now = time.time() if now is None else now
    removed = 0
    shutil.rmtree(data_paths.data_dir() / SHARED_BEFORE, ignore_errors=True)
    for base in caches():
        removed += _prune_one(base, older_than, now)
    return removed


def _prune_one(base: Path, older_than: float, now: float) -> int:
    removed = 0
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
