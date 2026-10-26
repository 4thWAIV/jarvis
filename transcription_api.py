"""
transcription_api.py — `/ws/transcribe`, the socket the voice page streams
its microphone to. The page sends `start`, waits for `ready`, then sends
binary frames of mono 16 kHz PCM16 and gets `interim` and `final` text back,
which it passes to /ws/voice exactly as it passed the browser's own
recognition before. This socket never starts a turn of the brain itself.

The protocol is frontend/src/transcribeProtocol.json, read by both sides.
Every server message carries the `session` this connection was given; a
reconnect is always a new session. The web boundary (web_auth.OriginGuard)
covers this route like every other socket: the page's own Origin or the tool
token, and no access-key category includes it.

A page that asks for diagnostics (`"diagnostics": true` in its start) sends
`telemetry` once a second and is sent `health` once a second: a
voice_diagnostics.CaptureMonitor samples the capture, records the sample and
classifies it. Every capture's start and end, and every failure with the
cause in force, is recorded either way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import time

import transcriber
import voice_diagnostics
from transcriber import PROTOCOL, TranscriptionError, TranscriptionService
from voice_diagnostics import Event

log = logging.getLogger("jarvis.transcription")

START_WAIT_SEC = 10.0
# A capture that becomes ready this soon after a failure is the page
# reconnecting after it, and is recorded as a reconnect.
RECONNECT_WITHIN_SEC = 10.0

service: Optional[TranscriptionService] = None
probe = voice_diagnostics.LoopProbe()
_last_failure_at = 0.0


def start(engine_factory=None) -> TranscriptionService:
    """Start loading the model in the background (from the server's
    lifespan). A missing model fails the service at once, without loading
    anything; the page is told when it connects."""
    global service, _last_failure_at
    _last_failure_at = 0.0
    service = TranscriptionService(engine_factory or transcriber.SherpaEngine)
    service.start()
    probe.start()
    voice_diagnostics.record_soon_prune()
    return service


async def stop() -> None:
    global service
    await probe.stop()
    if service is not None:
        await service.close()
        service = None


def mount(app: FastAPI) -> None:
    """Add the socket to the app, as a route of its own like every other."""
    app.add_api_websocket_route(PROTOCOL["path"], transcribe)


def _start_problem(msg: object) -> Optional[str]:
    """Why a start message is refused, or None."""
    if not isinstance(msg, dict) or msg.get("type") != "start":
        return "The first message must be start."
    expected = {k: PROTOCOL[k] for k in ("version", "sampleRate", "channels", "format")}
    wrong = [k for k, v in expected.items() if msg.get(k) != v]
    if wrong:
        return ("Unsupported audio: expected " +
                ", ".join(f"{k}={expected[k]}" for k in wrong) + ".")
    return None


def _frame_problem(frame: bytes) -> Optional[str]:
    if not frame:
        return "An audio frame was empty."
    if len(frame) % 2:
        return "An audio frame had an odd number of bytes."
    if len(frame) > PROTOCOL["maxFrameBytes"]:
        return f"An audio frame was larger than {PROTOCOL['maxFrameBytes']} bytes."
    return None


async def transcribe(ws: WebSocket):
    await ws.accept()
    session = secrets.token_urlsafe(8)
    lock = asyncio.Lock()

    async def send(kind: str, **fields) -> None:
        assert kind in PROTOCOL["fromServer"], kind
        async with lock:
            await ws.send_text(json.dumps({"type": kind, "session": session, **fields}))

    monitor: Optional[voice_diagnostics.CaptureMonitor] = None

    async def fail(error: TranscriptionError) -> None:
        global _last_failure_at
        log.info("transcription %s refused: %s", session, error.code)
        if error.code in voice_diagnostics.INCIDENT_KINDS:
            _last_failure_at = time.monotonic()
            if monitor is not None:
                await monitor.incident(error.code, error.message)
            else:
                voice_diagnostics.record_soon(Event(
                    at=time.time(), kind=error.code, capture=session, text=error.message))
        try:
            await send("error", code=error.code, message=error.message)
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            pass

    capture = None
    forwarder: Optional[asyncio.Task] = None
    sampler: Optional[asyncio.Task] = None
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), START_WAIT_SEC))
        except (asyncio.TimeoutError, json.JSONDecodeError, KeyError):
            first = None
        problem = _start_problem(first)
        if problem:
            await fail(TranscriptionError("invalid-format", problem))
            return
        if service is None:
            await fail(TranscriptionError("model-unavailable", "Speech input is not running."))
            return
        try:
            capture = service.admit()
            if service.status == "loading":
                await send("loading")
            await service.ready()
        except TranscriptionError as error:
            if capture is not None:
                capture.abort()
            await fail(error)
            return
        log.info("transcription %s ready", session)
        reconnect = time.monotonic() - _last_failure_at < RECONNECT_WITHIN_SEC
        voice_diagnostics.record_soon(Event(
            at=time.time(), kind="reconnect" if reconnect else "ready", capture=session))
        await send("ready")

        if first.get("diagnostics") is True:
            monitor = voice_diagnostics.CaptureMonitor(
                capture, session, probe, lambda health: send("health", **health))
            sampler = asyncio.create_task(monitor.run())

        async def forward() -> None:
            async for item in capture.events():
                if isinstance(item, TranscriptionError):
                    await fail(item)
                    return
                await send(item.kind, segment=item.segment, text=item.text)

        forwarder = asyncio.create_task(forward())
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                problem = _frame_problem(message["bytes"])
                if problem:
                    capture.abort()
                    await fail(TranscriptionError("invalid-format", problem))
                    return
                try:
                    capture.feed(message["bytes"])
                except TranscriptionError as error:
                    capture.abort()
                    await fail(error)
                    return
                continue
            try:
                message_json = json.loads(message.get("text") or "")
                kind = message_json.get("type")
            except (json.JSONDecodeError, AttributeError):
                kind = None
            if kind == "telemetry":
                if monitor is not None:
                    try:
                        monitor.telemetry(voice_diagnostics.parse_telemetry(message_json))
                    except ValueError as bad:
                        log.info("transcription %s: telemetry refused (%s)", session, bad)
                continue
            if kind == "abort":
                capture.abort()
                await ws.close()
                return
            if kind == "finish":
                try:
                    await asyncio.wait_for(capture.finish(), PROTOCOL["finishLimitSec"])
                except asyncio.TimeoutError:
                    capture.abort()
                    await fail(TranscriptionError(
                        "interrupted", "Speech input could not finish in time, "
                        "so the last words were not sent."))
                    return
                await forwarder
                voice_diagnostics.record_soon(Event(
                    at=time.time(), kind="finish", capture=session))
                await send("finished")
                await ws.close()
                return
            capture.abort()
            await fail(TranscriptionError("invalid-format", f"Unexpected message {kind!r}."))
            return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        # A page that goes away mid-sentence has its words discarded: an
        # abandoned partial is never turned into a command.
        if capture is not None and not capture.closed:
            capture.abort()
        if forwarder is not None and not forwarder.done():
            forwarder.cancel()
        if sampler is not None:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        log.info("transcription %s closed", session)
