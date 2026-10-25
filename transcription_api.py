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
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import transcriber
from transcriber import PROTOCOL, TranscriptionError, TranscriptionService

log = logging.getLogger("jarvis.transcription")

START_WAIT_SEC = 10.0

service: Optional[TranscriptionService] = None


def start(engine_factory=None) -> TranscriptionService:
    """Start loading the model in the background (from the server's
    lifespan). A missing model fails the service at once, without loading
    anything; the page is told when it connects."""
    global service
    service = TranscriptionService(engine_factory or transcriber.SherpaEngine)
    service.start()
    return service


async def stop() -> None:
    global service
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

    async def fail(error: TranscriptionError) -> None:
        log.info("transcription %s refused: %s", session, error.code)
        try:
            await send("error", code=error.code, message=error.message)
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            pass

    capture = None
    forwarder: Optional[asyncio.Task] = None
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
        await send("ready")

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
                kind = json.loads(message.get("text") or "").get("type")
            except (json.JSONDecodeError, AttributeError):
                kind = None
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
        log.info("transcription %s closed", session)
