"""The isolated JARVIS a decision package is checked in (decision_check.py).

    python -m jarvis.decision_check_server --port PORT --page DIR [--language en|es]

Run only by `decision_check`, in a child process whose environment already
points every path at a temporary folder (JARVIS_DATA_DIR, JARVIS_ENV_FILE,
JARVIS_STT_MODEL_DIR) with JARVIS_BRAIN_AUTOSTART=0. It is JARVIS's real
server, `jarvis.server:app`, on loopback over plain HTTP, with six things
changed for a check and nothing else:

- The voice page it serves is the one built for the check (`--page`), so
  the page, its sockets and the package frame all have this JARVIS's own
  origin, as the live page has the live one's.
- Speech is a stand-in synthesizer: a short silent WAV per chunk, so a story
  is told through the real scheduler, voice socket, page playback and cues
  without calling Fish Audio.
- The brain is never spawned (JARVIS_BRAIN_AUTOSTART=0), so every tool call
  is taken as on a turn he drove: the acting package tools run as they do
  when he asks.
- The session watcher, the preflight checks and the brain account lookup do
  not start: they read his live sessions and logins, which a check of one
  package has no business with.
- The language in force is the deck's, so its story is told; no speech
  model is loaded for it.
- Nothing listens beyond 127.0.0.1.
"""
from __future__ import annotations

import argparse
import io
import os
import struct
import wave
from pathlib import Path

# How long each chunk of the stand-in voice lasts: long enough for the page
# to play it and point at its beat, short enough that a story is quick.
STAND_IN_SECONDS = 0.3


def silence(seconds: float = STAND_IN_SECONDS) -> bytes:
    """A mono 16 kHz WAV of silence, which the page decodes and plays."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))
    return buf.getvalue()


def serve_page(app, page: Path) -> None:
    """The built voice page at /, and each of its folders, as server.py
    serves jarvis/web; ahead of jarvis/web's own routes when it exists."""
    from starlette.responses import FileResponse
    from starlette.staticfiles import StaticFiles

    async def index():
        return FileResponse(str(page / "index.html"))

    routes = app.router.routes
    before = len(routes)
    app.add_api_route("/", index, methods=["GET"])
    for folder in sorted(p for p in page.iterdir() if p.is_dir()):
        app.mount(f"/{folder.name}", StaticFiles(directory=str(folder)), name=folder.name)
    routes[:] = routes[before:] + routes[:before]


def prepare(server, language: str, page: Path) -> None:
    """Make the changes above on the imported server module."""
    from jarvis import spoken_words

    serve_page(server.app, page)

    async def stand_in_voice(_text: str):
        return silence()

    async def nothing(*_args, **_kwargs):
        return None

    start_brain_and_speech = server.start_brain_and_speech

    async def start_without_a_brain():
        await start_brain_and_speech()
        brain = server.brain_instance
        if brain is not None:
            # Never spawned: every call stands for one on his turn.
            type(brain).current_origin = property(lambda _self: "user")
        spoken_words.set_active_language(language)

    server._synth_for_speech = stand_in_voice
    server.start_brain_and_speech = start_without_a_brain
    server.start_session_watcher = nothing
    server.stop_session_watcher = nothing
    server._run_preflight = nothing
    server.brain_accounts.refresh = nothing


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--page", type=Path, required=True, help="the built voice page")
    args = parser.parse_args(argv)
    os.environ.update({"JARVIS_PORT": str(args.port), "JARVIS_SCHEME": "http",
                       "JARVIS_BIND_HOST": "127.0.0.1"})
    import uvicorn
    from jarvis import server
    prepare(server, args.language, args.page)
    uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
