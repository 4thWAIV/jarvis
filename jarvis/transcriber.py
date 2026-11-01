"""
transcriber.py — the ears. Local speech recognition: Banafo's Kroko
streaming Zipformer (the 2025-08-06 community models, English and Spanish)
through sherpa-onnx, on the CPU, with nothing sent off the machine. One
model is loaded at a time; switching language loads the other in its place.

Kroko replaced NVIDIA's Nemotron Speech Streaming EN 0.6B, which decoded at
0.5 of real time on an idle i9-9980HK but fell to 2.4 times slower than real
time when the machine was busy, so speech was dropped. Measured on the same
machine and audio, with eight CPU-bound processes running, Kroko decodes at
0.27 of real time (0.09 idle), with fewer word errors on spoken commands
(2.7% against 3.6%), in a quarter of the memory.

Three layers, each usable without the one above it:

- The models' identities (`MODELS`, one `SpeechModel` per language, and
  `MODEL_FILES`) and where each lives (`model_dir`), shared by the installer
  (speech_models.py, and scripts/setup_speech_input.py over it), preflight
  and the runtime, so they can never disagree about what "installed" means.
- `RecognitionEngine` / `RecognitionSession`: synchronous recognition of
  PCM16 audio into interim and final `RecognitionEvent`s. `SherpaEngine` is
  the real one; sherpa_onnx is imported only when it is built, so importing
  this module (and running JARVIS typed-only) never loads native code.
- `TranscriptionService`: the asynchronous adapter. One worker thread owns
  the engine (one model's) and the one open session, so the decoder is never called from
  two threads at once nor closed while an inference is running; the event
  loop only queues audio and reads results. The audio waiting for it is
  bounded (`QUEUE_LIMIT_SEC`): past that the capture is refused as
  overloaded, never trimmed, because dropping the start of a command is
  worse than saying it was not heard.

The wire protocol (`jarvis/transcribeProtocol.json`) is read here and
by the page, so both sides use one definition. The socket itself is
transcription_api.py. Nothing here stores audio or transcripts.
"""
from __future__ import annotations

import array
import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

from jarvis import data_paths

log = logging.getLogger("jarvis.transcriber")

PROTOCOL = json.loads(
    (data_paths.PACKAGE_DIR / "transcribeProtocol.json")
    .read_text(encoding="utf-8"))

SAMPLE_RATE: int = PROTOCOL["sampleRate"]
BYTES_PER_SECOND = SAMPLE_RATE * 2
QUEUE_LIMIT_SEC: float = PROTOCOL["queueLimitSec"]
INTERIM_EVERY_SEC: float = PROTOCOL["interimEverySec"]

RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"


@dataclass(frozen=True)
class SpeechModel:
    """One model JARVIS can listen with: the language it hears, its release
    asset's name, the digest GitHub publishes for that asset (its `digest`
    field, checked before anything is extracted) and the asset's size."""
    language: str
    label: str
    name: str
    sha256: str
    archive_bytes: int

    @property
    def url(self) -> str:
        return f"{RELEASE_URL}/{self.name}.tar.bz2"


MODELS: tuple[SpeechModel, ...] = (
    SpeechModel("en", "English", "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06",
                "c8676e5ff9ac2a85296e53ee0fd4d5fb1db6770e7a7647166eeafe349ade6834",
                57_267_600),
    # Measured before it was added (2026-11-01): 0.06 of real time on two
    # threads, 130-170 MB more memory than English, 4.5-7 s to load on an
    # i9-9980HK; 6.4% words wrong on synthetic voices, 14.2% on a real one.
    SpeechModel("es", "Spanish", "sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06",
                "31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1",
                124_394_665),
)
DEFAULT_LANGUAGE = "en"
ENCODER, DECODER, JOINER, TOKENS = "encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"
MODEL_FILES = (ENCODER, DECODER, JOINER, TOKENS)
SETUP_COMMAND = ".venv/bin/python scripts/setup_speech_input.py"

# Two threads leave headroom for a busy machine: 0.27 of real time with eight
# CPU-bound processes running beside it, on an Intel i9-9980HK.
DEFAULT_THREADS = 2
# Trailing silence, after words, that ends a segment. sherpa-onnx's default
# is 1.2 s. With Kroko the final arrives about 1.7 s after the last word at
# 0.8, and only 0.1 s sooner at 0.5, so the longer pause that keeps a
# sentence with a pause in it whole is kept.
ENDPOINT_SILENCE_SEC = 0.8
# Silence fed after the last audio on finish, so the model's look-ahead sees
# the end of the last word before the stream is closed: Kroko drops the last
# words with less than 1.5 s (17% of command words lost at 0.66 s, none at 1.5).
TAIL_PADDING_SEC = 1.5


def find_model(language: str) -> Optional[SpeechModel]:
    """The model for a language code ("en", "es"), or None."""
    return next((m for m in MODELS if m.language == language), None)


def default_model() -> SpeechModel:
    return find_model(DEFAULT_LANGUAGE)


def setup_command(model: Optional[SpeechModel] = None) -> str:
    """The command that installs `model` (the default one if None)."""
    model = model or default_model()
    if model.language == DEFAULT_LANGUAGE:
        return SETUP_COMMAND
    return f"{SETUP_COMMAND} --language {model.language}"


def models_root() -> Path:
    """Where the models' folders are, one per model: JARVIS_STT_MODEL_DIR if
    set, otherwise `<data>/models`."""
    raw = os.getenv("JARVIS_STT_MODEL_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return data_paths.models_dir()


def model_dir(model: Optional[SpeechModel] = None) -> Path:
    """Where a model's files are: `<models_root>/<its name>`, the default
    model's if None."""
    return models_root() / (model or default_model()).name


def model_problem(directory: Optional[Path] = None,
                  model: Optional[SpeechModel] = None) -> Optional[str]:
    """What stops `model` loading from `directory` (its own folder if None),
    said so the user can fix it, or None when every file it needs is there.
    Checks files only: it never loads the model."""
    directory = directory or model_dir(model)
    command = setup_command(model)
    if not directory.is_dir():
        return (f"The speech model is not installed ({directory} is missing). "
                f"Run {command}.")
    missing = [n for n in MODEL_FILES
               if not (directory / n).is_file() or (directory / n).stat().st_size == 0]
    if missing:
        return (f"The speech model in {directory} is incomplete "
                f"(missing {', '.join(missing)}). Run {command}.")
    return None


@dataclass(frozen=True)
class RecognitionEvent:
    kind: Literal["interim", "final"]
    text: str
    segment: int


class RecognitionSession(Protocol):
    def accept_pcm16(self, pcm: bytes) -> list[RecognitionEvent]: ...
    def finish(self) -> list[RecognitionEvent]: ...
    def close(self) -> None: ...


class RecognitionEngine(Protocol):
    def open_session(self) -> RecognitionSession: ...


def pcm16_to_float(pcm: bytes) -> list[float]:
    """Little-endian signed 16-bit samples as floats in [-1, 1)."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return [s / 32768.0 for s in samples]


class SherpaEngine:
    """The real recognizer. Building it loads the model (a few seconds, and
    about 260 MB of memory), so it is built on the worker thread, once."""

    def __init__(self, directory: Optional[Path] = None,
                 threads: int = DEFAULT_THREADS, *,
                 model: Optional[SpeechModel] = None) -> None:
        directory = directory or model_dir(model)
        problem = model_problem(directory, model)
        if problem:
            raise FileNotFoundError(problem)
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise FileNotFoundError(
                "sherpa-onnx is not installed in the Python JARVIS runs on "
                f"({sys.executable}). Run .venv/bin/pip install -r requirements.txt."
            ) from exc
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(directory / TOKENS),
            encoder=str(directory / ENCODER),
            decoder=str(directory / DECODER),
            joiner=str(directory / JOINER),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            rule2_min_trailing_silence=ENDPOINT_SILENCE_SEC,
            provider="cpu",
        )

    def open_session(self) -> "SherpaSession":
        return SherpaSession(self._recognizer)


def load_engine(model: SpeechModel) -> SherpaEngine:
    """The real engine for one model: what the service builds by default."""
    return SherpaEngine(model=model)


class SherpaSession:
    """One continuous stream of audio, split into segments at each endpoint.
    A segment's interims are the text so far whenever it changes; its final
    is that text once the endpoint is reached. Silence between segments
    produces nothing, and the stream is reset, not closed, so the next word
    is not lost to a restart."""

    def __init__(self, recognizer) -> None:
        self._recognizer = recognizer
        self._stream = recognizer.create_stream()
        self._segment = 0
        self._text = ""

    def accept_pcm16(self, pcm: bytes) -> list[RecognitionEvent]:
        self._stream.accept_waveform(SAMPLE_RATE, pcm16_to_float(pcm))
        return self._decode()

    def finish(self) -> list[RecognitionEvent]:
        self._stream.accept_waveform(
            SAMPLE_RATE, [0.0] * int(TAIL_PADDING_SEC * SAMPLE_RATE))
        self._stream.input_finished()
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = self._recognizer.get_result(self._stream)
        return self._final(text)

    def close(self) -> None:
        self._stream = None

    def _decode(self) -> list[RecognitionEvent]:
        recognizer, stream = self._recognizer, self._stream
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream)
        events: list[RecognitionEvent] = []
        if recognizer.is_endpoint(stream):
            events += self._final(text)
            recognizer.reset(stream)
        elif text != self._text:
            self._text = text
            if text:
                events.append(RecognitionEvent("interim", text, self._segment))
        return events

    def _final(self, text: str) -> list[RecognitionEvent]:
        self._text = ""
        if not text:
            return []
        event = RecognitionEvent("final", text, self._segment)
        self._segment += 1
        return [event]


class TranscriptionError(Exception):
    """A failure the page is told about by code (PROTOCOL["errors"])."""

    def __init__(self, code: str, message: str, incident: bool = True) -> None:
        assert code in PROTOCOL["errors"], code
        super().__init__(message)
        self.code = code
        self.message = message
        # False for an ending JARVIS chose (a language switch), which the
        # voice record notes as the switch, not as a failure.
        self.incident = incident


Status = Literal["idle", "loading", "ready", "failed", "closed"]


@dataclass
class Window:
    """One capture's counts over a stretch of time: audio in, audio
    decoded, the worker's time spent decoding it, and finals produced."""
    received_bytes: int = 0
    decoded_bytes: int = 0
    decode_sec: float = 0.0
    finals: int = 0


class Capture:
    """One admitted recognition session, as the socket sees it. Audio goes
    in with `feed`; events come out of `events()` until the capture ends.
    Interims are coalesced to at most one per `INTERIM_EVERY_SEC`; finals
    are never delayed or coalesced."""

    def __init__(self, service: "TranscriptionService", generation: int) -> None:
        self._service = service
        self.generation = generation
        self._loop = asyncio.get_running_loop()
        self._out: asyncio.Queue = asyncio.Queue()
        self.pending_bytes = 0
        self._oldest: list[float] = []   # enqueue times of audio not yet decoded
        self._lagging = False
        self._window = Window()          # since the last take_window()
        self._last_interim = 0.0
        self._held: Optional[RecognitionEvent] = None
        self._held_timer: Optional[asyncio.TimerHandle] = None
        self._finished = asyncio.Event()
        self.closed = False

    def feed(self, pcm: bytes) -> None:
        """Queue one frame for the worker. Raises overload, rather than
        waiting or dropping, once more than QUEUE_LIMIT_SEC is unprocessed."""
        if self.closed:
            return
        now = time.monotonic()
        if (self.pending_bytes + len(pcm) > QUEUE_LIMIT_SEC * BYTES_PER_SECOND
                or (self._oldest and now - self._oldest[0] > QUEUE_LIMIT_SEC)):
            raise TranscriptionError(
                "overload", "Speech recognition fell behind, so this was not heard.")
        self.pending_bytes += len(pcm)
        self._window.received_bytes += len(pcm)
        self._oldest.append(now)
        self._service._submit(("audio", self.generation, pcm))

    def backlog(self) -> tuple[float, float]:
        """Seconds of audio waiting to be decoded, and how long the oldest
        of it has waited."""
        oldest = time.monotonic() - self._oldest[0] if self._oldest else 0.0
        return self.pending_bytes / BYTES_PER_SECOND, oldest

    def take_window(self) -> "Window":
        """What happened since the last call (voice_diagnostics samples it
        once a second), and start a new window."""
        window, self._window = self._window, Window()
        return window

    async def finish(self) -> None:
        """Decode what is already queued, emit its final, and end."""
        if self.closed:
            return
        self._service._submit(("finish", self.generation, None))
        await self._finished.wait()

    def interrupt(self, error: TranscriptionError) -> None:
        """Tell the page why this capture ends, then abort it."""
        if self.closed:
            return
        self._out.put_nowait(error)
        self.abort()

    def abort(self) -> None:
        """Discard everything outstanding; nothing more is emitted."""
        if self.closed:
            return
        self.closed = True
        self._cancel_held()
        self._service._submit(("abort", self.generation, None))
        self._service._release(self)
        self._out.put_nowait(None)

    async def events(self):
        while (item := await self._out.get()) is not None:
            yield item

    # Called on the event loop by the service, never by the worker directly.
    def _deliver(self, consumed: int, events: list[RecognitionEvent],
                 error: Optional[TranscriptionError], finished: bool,
                 decode_sec: float = 0.0) -> None:
        if self.closed:
            return
        self._window.decode_sec += decode_sec
        if consumed:
            self.pending_bytes -= consumed
            self._window.decoded_bytes += consumed
            if self._oldest:
                delay = time.monotonic() - self._oldest.pop(0)
                lagging = delay > QUEUE_LIMIT_SEC / 2
                # Once per episode, not once per frame: a slow decoder
                # otherwise writes ten lines a second.
                if lagging and not self._lagging:
                    log.warning("speech decoding is %.2fs behind the microphone", delay)
                elif self._lagging and not lagging:
                    log.info("speech decoding caught up")
                self._lagging = lagging
        for event in events:
            if event.kind == "final":
                self._window.finals += 1
                self._cancel_held()
                self._out.put_nowait(event)
            else:
                self._interim(event)
        if error is not None:
            self._cancel_held()
            self._out.put_nowait(error)
        if finished or error is not None:
            self._cancel_held()
            self.closed = True
            self._service._release(self)
            self._finished.set()
            self._out.put_nowait(None)

    def _interim(self, event: RecognitionEvent) -> None:
        wait = self._last_interim + INTERIM_EVERY_SEC - time.monotonic()
        self._held = event
        if wait <= 0:
            self._flush_held()
        elif self._held_timer is None:
            self._held_timer = self._loop.call_later(wait, self._flush_held)

    def _flush_held(self) -> None:
        self._held_timer = None
        if self._held is not None and not self.closed:
            self._out.put_nowait(self._held)
            self._last_interim = time.monotonic()
        self._held = None

    def _cancel_held(self) -> None:
        if self._held_timer is not None:
            self._held_timer.cancel()
            self._held_timer = None
        self._held = None


class TranscriptionService:
    """Owns the engine and the one worker thread that uses it, and admits
    one capture at a time. `start` begins loading the model in the
    background; a capture admitted while it loads waits in `ready`.
    `switch` loads another model in its place: one model in memory at a
    time, and the same load path as at start."""

    def __init__(self, engine_factory: Callable[[SpeechModel], RecognitionEngine],
                 model: Optional[SpeechModel] = None) -> None:
        self._engine_factory = engine_factory
        self.model: SpeechModel = model or default_model()
        self.status: Status = "idle"
        self.failure: Optional[str] = None
        self._commands: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._settled: Optional[asyncio.Event] = None
        self._active: Optional[Capture] = None
        self._generation = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._work, name="jarvis-transcriber", daemon=True)
        self._thread.start()
        self._load(self.model)

    async def switch(self, model: SpeechModel) -> None:
        """Listen with `model` from now on: the capture now open is ended
        (the page is told why and reconnects, waiting while it loads), the
        engine is let go, and `model` is loaded in its place. Returns once
        it is ready; raises as `ready` does if it could not load."""
        if self.status == "closed" or self._thread is None:
            raise TranscriptionError("model-unavailable", "Speech input is not running.")
        if model == self.model and self.status in ("loading", "ready"):
            await self.ready()
            return
        if self._active is not None:
            self._active.interrupt(TranscriptionError(
                "interrupted", f"Speech recognition is switching to {model.label}.",
                incident=False))
        self._load(model)
        await self.ready()

    def _load(self, model: SpeechModel) -> None:
        self.model = model
        self.status = "loading"
        self.failure = None
        self._settled = asyncio.Event()
        self._submit(("load", 0, (model, self._settled)))

    async def ready(self) -> None:
        """Wait for the model to finish loading; raise if it could not. A
        switch made while waiting is waited for too."""
        if self._settled is None:
            raise TranscriptionError("model-unavailable", "Speech input is not running.")
        while True:
            settled = self._settled
            await settled.wait()
            if settled is self._settled:
                break
        if self.status != "ready":
            raise TranscriptionError(
                "model-unavailable", self.failure or "Speech input is not running.")

    @property
    def active(self) -> Optional[Capture]:
        """The capture now admitted, if any."""
        return self._active

    def admit(self) -> Capture:
        """The one capture, or busy if another page already has it."""
        if self.status in ("failed", "closed", "idle"):
            raise TranscriptionError(
                "model-unavailable", self.failure or "Speech input is not running.")
        if self._active is not None:
            raise TranscriptionError(
                "busy", "Another JARVIS page is listening. Typing still works here.")
        self._generation += 1
        capture = Capture(self, self._generation)
        self._active = capture
        self._submit(("open", capture.generation, None))
        return capture

    async def close(self) -> None:
        """Stop admitting, abort the open capture, and wait for the worker
        to finish whatever inference it is in before the engine is let go."""
        self.status = "closed"
        if self._active is not None:
            self._active.abort()
        if self._thread is not None:
            self._commands.put(("stop", 0, None))
            await asyncio.to_thread(self._thread.join)
            self._thread = None

    def _submit(self, command: tuple) -> None:
        self._commands.put(command)

    def _release(self, capture: Capture) -> None:
        if self._active is capture:
            self._active = None

    def _post(self, generation: int, consumed: int = 0,
              events: Optional[list[RecognitionEvent]] = None,
              error: Optional[TranscriptionError] = None,
              finished: bool = False, decode_sec: float = 0.0) -> None:
        def deliver() -> None:
            capture = self._active
            if capture is not None and capture.generation == generation:
                capture._deliver(consumed, events or [], error, finished, decode_sec)
        self._loop.call_soon_threadsafe(deliver)

    def _settle(self, settled: asyncio.Event, status: Status,
                failure: Optional[str] = None) -> None:
        def apply() -> None:
            # A load a later switch has replaced settles nothing but itself.
            if settled is self._settled:
                if self.status != "closed":
                    self.status = status
                self.failure = failure
            settled.set()
        self._loop.call_soon_threadsafe(apply)

    def _build(self, model: SpeechModel,
               settled: asyncio.Event) -> Optional[RecognitionEngine]:
        started = time.monotonic()
        try:
            engine = self._engine_factory(model)
        except Exception as exc:
            log.error("speech model %s failed to load: %s", model.name, exc)
            self._settle(settled, "failed", str(exc) if isinstance(exc, FileNotFoundError)
                         else f"The speech model failed to load: {exc}")
            return None
        log.info("speech model %s ready in %.1fs", model.name, time.monotonic() - started)
        self._settle(settled, "ready")
        return engine

    def _work(self) -> None:
        engine: Optional[RecognitionEngine] = None
        session: Optional[RecognitionSession] = None
        generation = 0
        while True:
            kind, gen, payload = self._commands.get()
            if kind == "stop":
                break
            if kind == "load":
                if session is not None:
                    session.close()
                    session = None
                # Let the old model go before loading the next: one in memory.
                engine = None
                engine = self._build(*payload)
                continue
            if engine is None:
                continue
            if kind == "open":
                if session is not None:
                    session.close()
                session, generation = None, gen
                try:
                    session = engine.open_session()
                except Exception as exc:
                    log.exception("speech session failed to open")
                    self._post(gen, error=TranscriptionError(
                        "decoder-failed", f"Speech recognition failed: {exc}"))
                continue
            if gen != generation or session is None:
                continue   # a capture that has since been aborted or replaced
            if kind == "abort":
                session.close()
                session = None
                continue
            try:
                if kind == "audio":
                    began = time.perf_counter()
                    events = session.accept_pcm16(payload)
                    self._post(gen, consumed=len(payload), events=events,
                               decode_sec=time.perf_counter() - began)
                elif kind == "finish":
                    self._post(gen, events=session.finish(), finished=True)
                    session.close()
                    session = None
            except Exception as exc:
                log.exception("speech decoding failed")
                session.close()
                session = None
                self._post(gen, error=TranscriptionError(
                    "decoder-failed", f"Speech recognition failed: {exc}"))
        if session is not None:
            session.close()
        engine = None
        log.info("speech worker stopped")
