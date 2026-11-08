"""The embedding model the knowledge engine's vector search runs on (design
section 7.11): which one, where it lives, installing it, and the service
the page's routes and the brain's tools share.

One model: multilingual-e5-small, quantised to int8, as Xenova's ONNX
export publishes it at a pinned commit, run by onnxruntime (e5_onnx.py).
It was chosen on the embedder trial's numbers (phase0/results.md): the only
candidate that made vectors find anything (35 percent of the questions
alone, where Apple's sentence embedding found none), 10 ms a question
against 313 ms for NLContextualEmbedding, and 13 minutes for the whole
corpus; one space for English and Spanish; MIT licence.

Installing is the speech models' sequence (model_files.py): each file
downloaded into a temporary folder beside the destination, checked against
its published SHA-256 and size, written under its own fixed name only (the
files are not an archive, so nothing is extracted and no name from the
download is used), a `model.json` recording what was installed, and the
whole folder renamed into place, so an interrupted install never looks
installed. Nothing is downloaded except when asked: by
`scripts/setup_embedding_model.py`, the install route or the acting
`install_embedding_model`.

The folder is `<data>/models/embedding/<name>`
(`data_paths.embedding_models_dir`). The engine finds it there itself
(`embedder.RUNNER`), so installing needs no restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jarvis import data_paths
from jarvis.model_files import InstallRefused, Progress, download, quiet, sha256_of

log = logging.getLogger("jarvis.embedding_models")


@dataclass(frozen=True)
class ModelFile:
    remote: str          # its path in the repository at `revision`
    name: str            # the one name it is written under
    sha256: str
    size: int


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    label: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]
    dim: int
    runtime: str         # which class runs it (embedder.RUNTIMES)
    licence: str
    # The engine's extra resident memory with it loaded and embedding,
    # measured (phase5/results.md), added to the engine's budget.
    memory_mb: int

    @property
    def space(self) -> str:
        """The vector space's ID: the model and its pinned revision, so a
        changed model builds a new space beside the old one."""
        return f"{self.name}@{self.revision[:8]}"

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def url(self, f: ModelFile) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{f.remote}"


E5_SMALL = EmbeddingModel(
    name="multilingual-e5-small-int8",
    label="multilingual-e5-small",
    repo="Xenova/multilingual-e5-small",
    revision="761b726dd34fb83930e26aab4e9ac3899aa1fa78",
    files=(ModelFile("onnx/model_quantized.onnx", "model.onnx",
                     "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193", 118308185),
           ModelFile("tokenizer.json", "tokenizer.json",
                     "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39", 17082730)),
    dim=384, runtime="onnx-e5", licence="MIT", memory_mb=600)

MODELS: tuple[EmbeddingModel, ...] = (E5_SMALL,)
DEFAULT = E5_SMALL
SETUP_COMMAND = ".venv/bin/python scripts/setup_embedding_model.py"


def model_dir(model: EmbeddingModel = DEFAULT) -> Path:
    return data_paths.embedding_models_dir() / model.name


def model_problem(directory: Optional[Path] = None, model: EmbeddingModel = DEFAULT) -> Optional[str]:
    """Why the model in `directory` cannot be used, or None: every file
    there, a regular file (never a link) of its published size, and the
    record of what was installed. The digests were checked at install."""
    directory = directory or model_dir(model)
    if not directory.is_dir() or directory.is_symlink():
        return f"not installed; {SETUP_COMMAND} installs it"
    for f in model.files:
        path = directory / f.name
        if path.is_symlink() or not path.is_file():
            return f"{f.name} is missing"
        if path.stat().st_size != f.size:
            return f"{f.name} is not the published file"
    try:
        record = json.loads((directory / "model.json").read_text())
    except (OSError, ValueError):
        return "model.json is missing"
    if record.get("revision") != model.revision:
        return "it is another revision of the model"
    return None


def install(model: EmbeddingModel = DEFAULT, *, files_from: Optional[Path] = None,
            progress: Progress = quiet) -> Path:
    """Install `model` into `model_dir(model)` and return that folder; an
    installed model is left as it is. `files_from` is a folder already
    holding the files under their remote names or their own (a previous
    download), checked like a download. Raises InstallRefused."""
    destination = model_dir(model)
    if model_problem(destination, model) is None:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".embedding-model-", dir=destination.parent) as work:
        staged = Path(work) / model.name
        staged.mkdir()
        for f in model.files:
            target = staged / f.name
            local = None
            if files_from is not None:
                for candidate in (files_from / f.remote, files_from / Path(f.remote).name, files_from / f.name):
                    if candidate.is_file() and not candidate.is_symlink():
                        local = candidate
                        break
                if local is None:
                    raise InstallRefused(f"Refused: {files_from} has no {Path(f.remote).name}.")
                shutil.copyfile(local, target)
            else:
                download(model.url(f), target, progress)
            progress("verifying", f.size, f.size)
            size = target.stat().st_size
            digest = sha256_of(target)
            if size != f.size or digest != f.sha256:
                raise InstallRefused(f"Refused: {f.name} has SHA-256 {digest} and {size} bytes, but "
                                     f"{model.repo}@{model.revision[:8]} publishes {f.sha256} and {f.size}.")
        progress("installing", model.download_bytes, model.download_bytes)
        _compile_tokenizer(staged)
        (staged / "model.json").write_text(json.dumps(
            {"name": model.name, "repo": model.repo, "revision": model.revision, "dim": model.dim,
             "runtime": model.runtime, "files": {f.name: f.sha256 for f in model.files}}, indent=2) + "\n")
        problem = model_problem(staged, model)
        if problem:
            raise InstallRefused(f"Refused: the install is incomplete: {problem}.")
        if destination.exists():
            raise InstallRefused(f"Refused: {destination} exists but is incomplete and is not "
                                 "removed for you; move it aside and try again.")
        staged.rename(destination)
    return destination


def _compile_tokenizer(folder: Path) -> None:
    """The tokenizer's compact file beside its JSON (unigram.COMPACT), made
    in a child process: reading the JSON once costs some 200 MB that a
    long-lived process would keep."""
    root = str(Path(__file__).resolve().parent.parent)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from pathlib import Path; from jarvis.knowledge.unigram import compile_file; "
            "compile_file(Path(sys.argv[2]), Path(sys.argv[3]))")
    done = subprocess.run([sys.executable, "-I", "-c", code, root, str(folder / "tokenizer.json"),
                           str(folder / "tokenizer.bin")], capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise InstallRefused(f"Refused: the tokenizer could not be read: {done.stderr.strip()[-300:]}")


def runtime_problem() -> Optional[str]:
    """Why the runtime cannot run the model here, or None."""
    try:
        import onnxruntime  # noqa: F401
    except Exception as e:  # noqa: BLE001 - an import that fails is the answer
        return f"onnxruntime cannot be imported ({type(e).__name__}); .venv/bin/pip install -r requirements.txt"
    return None


def extra_memory_mb() -> int:
    """What the engine's memory budget grows by while the model is
    installed and runnable here."""
    return DEFAULT.memory_mb if model_problem() is None and runtime_problem() is None else 0


# ── the service the page and the brain share ─────────────────────────────────

class Refused(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status, self.message, self.code = status, message, code

    def to_json(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


@dataclass
class Install:
    state: str            # downloading, verifying, installing, installed, failed
    done_bytes: int = 0
    total_bytes: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = None


RUNNING = frozenset({"downloading", "verifying", "installing"})
_install: Optional[Install] = None
_tasks: set[asyncio.Task] = set()


def status(model: EmbeddingModel = DEFAULT) -> dict:
    """What the page's Knowledge view and the brain's `embedding_model`
    show: the model, its size, whether it is installed and runnable, and
    how an install is going."""
    problem = model_problem(model=model)
    runtime = runtime_problem()
    return {"name": model.name, "label": model.label, "repo": model.repo, "revision": model.revision,
            "dim": model.dim, "licence": model.licence, "download_bytes": model.download_bytes,
            "space": model.space, "installed": problem is None, "problem": problem,
            "runtime": "onnxruntime", "runtime_problem": runtime,
            "install": None if _install is None else {
                "state": _install.state, "done_bytes": _install.done_bytes,
                "total_bytes": _install.total_bytes, "error": _install.error,
                "error_code": _install.error_code}}


def start_install(model: EmbeddingModel = DEFAULT) -> dict:
    """Start downloading and installing the model in the background, unless
    it is installed or already on its way; its progress is in `status`."""
    global _install
    if model_problem(model=model) is None or (_install is not None and _install.state in RUNNING):
        return status(model)
    _install = Install("downloading", 0, model.download_bytes)
    task = asyncio.get_running_loop().create_task(_run_install(model, _install))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return status(model)


async def _run_install(model: EmbeddingModel, progress: Install) -> None:
    done_before = [0]

    def report(state: str, done: int, total: int) -> None:
        progress.state = state
        if state == "downloading":
            progress.done_bytes = done_before[0] + done
        elif state == "verifying":
            done_before[0] += total
            progress.done_bytes = done_before[0]

    log.info("installing embedding model %s", model.name)
    try:
        await asyncio.to_thread(install, model, progress=report)
    except InstallRefused as refused:
        progress.state, progress.error, progress.error_code = "failed", str(refused), "install-refused"
    except Exception as exc:  # noqa: BLE001 - a failed download is said, not raised
        log.exception("embedding model %s did not install", model.name)
        progress.state, progress.error = "failed", f"The download failed: {type(exc).__name__}"
        progress.error_code = "download-failed"
    else:
        progress.state = "installed"
        progress.done_bytes = model.download_bytes
    log.info("embedding model %s: %s", model.name, progress.state)
