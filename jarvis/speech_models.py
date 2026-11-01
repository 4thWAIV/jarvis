"""
speech_models.py — the speech models JARVIS can listen with: installing one
and switching speech input to another.

The models themselves (their names, digests and folders) are
`transcriber.MODELS`. Installing one downloads its release archive from the
sherpa-onnx GitHub release, checks it against the SHA-256 GitHub publishes
for that asset, extracts only the model's own regular files into a
temporary folder beside the destination, and renames the complete folder
into place, so an interrupted install never looks installed. `install` is
that whole sequence, shared by scripts/setup_speech_input.py and the
service below; nothing is downloaded except when one of them is asked to.

The service is what the page's routes (`/api/speech/models*`) and the
brain's tools (`speech_models`, `install_speech_model`,
`switch_speech_model`) both call: `models` reads, `start_install` starts a
download in the background and reports its progress through `models`, and
`switch` loads another language's model in place of the one speech input
has (transcriber.TranscriptionService.switch: one model in memory at a
time) and records it in the voice record as a `model-load` event, or as
`model-unavailable` when it could not load. Which language is chosen, and
keeping that choice, is the language setting's, not this module's.

The models are Banafo's Kroko community models, under CC-BY-SA; the
sherpa-onnx engine is Apache-2.0. See https://huggingface.co/Banafo/Kroko-ASR
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from jarvis import transcriber
from jarvis import transcription_api
from jarvis import voice_diagnostics
from jarvis.transcriber import SpeechModel, TranscriptionError

log = logging.getLogger("jarvis.speech_models")

# (state, bytes done, bytes in all): "downloading", "verifying", "installing".
Progress = Callable[[str, int, int], None]
# How long one read of the download may wait. Without it a connection that
# goes dead hangs the install for good, and while it shows as downloading a
# new install of that model is not started.
DOWNLOAD_TIMEOUT_SEC = 60


# ── installing one model ─────────────────────────────────────────────────

class InstallRefused(Exception):
    """An archive that is not the release's, or not safe to extract."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, progress: Progress) -> None:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SEC) as response, \
            dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            done += len(block)
            progress("downloading", done, total)


def extract(archive: Path, model: SpeechModel, into: Path) -> Path:
    """Extract only regular files and folders under the model's own folder;
    refuse links, absolute paths and anything that climbs out."""
    prefix = model.name + "/"
    with tarfile.open(archive, "r:bz2") as tar:
        members = tar.getmembers()
        for m in members:
            name = m.name
            if not (name == model.name or name.startswith(prefix)):
                raise InstallRefused(f"Refused: unexpected entry {name!r} in the archive.")
            if ".." in Path(name).parts or Path(name).is_absolute():
                raise InstallRefused(f"Refused: unsafe path {name!r} in the archive.")
            if not (m.isfile() or m.isdir()):
                raise InstallRefused(f"Refused: {name!r} is a link or device, not a file.")
        tar.extractall(into, members=members, filter="data")
    return into / model.name


def _quiet(_state: str, _done: int, _total: int) -> None:
    pass


def install(model: SpeechModel, archive: Optional[Path] = None,
            progress: Progress = _quiet) -> Path:
    """Install `model` into `transcriber.model_dir(model)` and return that
    folder; an installed model is left as it is. `archive` is one already
    on disk, checked like a download. Raises InstallRefused."""
    destination = transcriber.model_dir(model)
    if transcriber.model_problem(destination, model) is None:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".speech-model-",
                                     dir=destination.parent) as work:
        work_dir = Path(work)
        if archive is None:
            archive = work_dir / "model.tar.bz2"
            download(model.url, archive, progress)
        size = archive.stat().st_size
        progress("verifying", size, size)
        digest = sha256_of(archive)
        if digest != model.sha256:
            raise InstallRefused(f"Refused: {archive} has SHA-256 {digest}, but the "
                                 f"release publishes {model.sha256}.")
        progress("installing", size, size)
        extracted = extract(archive, model, work_dir / "extract")
        problem = transcriber.model_problem(extracted, model)
        if problem:
            raise InstallRefused(f"Refused: the archive is incomplete. {problem}")
        if destination.exists():
            raise InstallRefused(f"Refused: {destination} exists but is incomplete "
                                 "and is not removed for you; move it aside and try again.")
        extracted.rename(destination)
    return destination


# ── the service the page and the brain share ─────────────────────────────

class Refused(Exception):
    """Why a request was not carried out, with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Install:
    """One model's download in this process: where it has got to."""
    state: str            # downloading, verifying, installing, installed, failed
    done_bytes: int = 0
    total_bytes: int = 0
    error: Optional[str] = None


RUNNING = frozenset({"downloading", "verifying", "installing"})
_installs: dict[str, Install] = {}
_tasks: set[asyncio.Task] = set()


def _model(language: str) -> SpeechModel:
    model = transcriber.find_model(str(language or "").strip().lower())
    if model is None:
        raise Refused(404, "There is a speech model for "
                      + " and ".join(f"{m.label} ({m.language})" for m in transcriber.MODELS)
                      + " only.")
    return model


def model_json(model: SpeechModel) -> dict:
    problem = transcriber.model_problem(model=model)
    install = _installs.get(model.language)
    return {"language": model.language, "label": model.label, "name": model.name,
            "archiveBytes": model.archive_bytes, "installed": problem is None,
            "problem": problem,
            "install": None if install is None else {
                "state": install.state, "doneBytes": install.done_bytes,
                "totalBytes": install.total_bytes, "error": install.error}}


def models() -> dict:
    """Which model speech input has and its state, and every model with
    whether it is installed and how its download is going."""
    service = transcription_api.service
    return {"language": service.model.language if service else None,
            "state": service.status if service else "idle",
            "failure": service.failure if service else None,
            "models": [model_json(m) for m in transcriber.MODELS]}


def start_install(language: str) -> dict:
    """Start downloading and installing a model in the background, unless it
    is installed or already on its way; its progress is in `models`."""
    model = _model(language)
    current = _installs.get(model.language)
    if (transcriber.model_problem(model=model) is None
            or (current is not None and current.state in RUNNING)):
        return model_json(model)
    progress = Install("downloading", 0, model.archive_bytes)
    _installs[model.language] = progress
    task = asyncio.get_running_loop().create_task(_install(model, progress))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return model_json(model)


async def _install(model: SpeechModel, progress: Install) -> None:
    def report(state: str, done: int, total: int) -> None:
        progress.state, progress.done_bytes = state, done
        progress.total_bytes = total or progress.total_bytes

    log.info("installing speech model %s", model.name)
    try:
        await asyncio.to_thread(install, model, None, report)
    except InstallRefused as refused:
        progress.state, progress.error = "failed", str(refused)
    except Exception as exc:
        log.exception("speech model %s did not install", model.name)
        progress.state, progress.error = "failed", f"The download failed: {exc}"
    else:
        progress.state = "installed"
    log.info("speech model %s: %s", model.name, progress.state)


async def switch(language: str) -> dict:
    """Listen with `language`'s model from now on, once it has loaded (a few
    seconds), and say so in the voice record."""
    model = _model(language)
    service = transcription_api.service
    if service is None or service.status == "closed":
        raise Refused(503, "Speech input is not running.")
    if transcriber.model_problem(model=model) is not None:
        raise Refused(409, f"The {model.label} speech model is not installed yet.")
    previous = service.model
    if model == previous and service.status == "ready":
        return models()
    started = time.monotonic()
    try:
        await service.switch(model)
    except TranscriptionError as error:
        await voice_diagnostics.record(voice_diagnostics.Event(
            at=time.time(), kind="model-unavailable", text=error.message))
        raise Refused(500, error.message) from error
    if service.model != model:
        # Another switch was asked for while this one loaded, and won.
        raise Refused(409, f"Speech input was switched to {service.model.label} "
                           f"before {model.label} had loaded.")
    await voice_diagnostics.record(voice_diagnostics.Event(
        at=time.time(), kind="model-load",
        text=(f"Speech input switched from {previous.label} to {model.label} "
              f"({model.name}) in {time.monotonic() - started:.1f} s.")))
    return models()


# ── the page's routes ────────────────────────────────────────────────────

def _refused(refused: Refused) -> JSONResponse:
    return JSONResponse(status_code=refused.status, content={"error": refused.message})


async def api_models():
    return models()


async def api_install(language: str):
    try:
        return {"model": start_install(language)}
    except Refused as refused:
        return _refused(refused)


async def api_switch(language: str = Body(embed=True)):
    try:
        return await switch(language)
    except Refused as refused:
        return _refused(refused)


def mount(app: FastAPI) -> None:
    app.add_api_route("/api/speech/models", api_models, methods=["GET"])
    app.add_api_route("/api/speech/models/{language}/install", api_install,
                      methods=["POST"])
    app.add_api_route("/api/speech/model", api_switch, methods=["POST"])
