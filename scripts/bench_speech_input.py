#!/usr/bin/env python3
"""Measure the local speech model on this Mac, through transcriber.py's own
engine and session classes, on spoken commands made with macOS's `say`.

    .venv/bin/python scripts/bench_speech_input.py [--threads 1 2 4] [--json]

Each thread count runs in its own child process, so its cold load and peak
memory are its own. For each it records:

- load: seconds to build the recognizer (cold, from disk).
- rtf: decode time divided by audio time, feeding as fast as it decodes.
- chunk_ms: the mean and worst decode time of one 100 ms frame.
- first_interim / final: seconds from the start of the audio to the first
  interim, and from the end of the speech to its final, feeding 100 ms frames
  at real-time pace and then silence until the final arrives.
- silence: finals produced by 30 s of silence (must be none).
- repeat: finals for the same sentence said twice with a pause (must be two).
- peak_rss_mb, and the WER of each command against its words.

The commands are synthesised with `say` at 16 kHz into a temporary folder,
each after half a second of silence. It reads no microphone and keeps
nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import transcriber  # noqa: E402

FRAME = transcriber.PROTOCOL["frameBytes"]
# What the benchmark says, in the shape of things said to JARVIS.
COMMANDS = (
    "send a message to agent guard asking for its current status",
    "don't restart the server until the tests pass",
    "read me the most recent reply from diagnostics",
    "remind me at three thirty to call the bank",
)
LEAD_SILENCE = b"\x00" * (transcriber.BYTES_PER_SECOND // 2)


def _spoken(text: str, folder: Path) -> bytes:
    """`text` said by macOS's default voice, as 16 kHz PCM16."""
    path = folder / f"{abs(hash(text))}.wav"
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
    return LEAD_SILENCE + _wav(path)
SILENCE = b"\x00" * FRAME


def _wav(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert w.getframerate() == transcriber.SAMPLE_RATE and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)]


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def _wer(ref: str, hyp: str) -> float:
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


def _realtime(engine, pcm: bytes) -> dict:
    session = engine.open_session()
    start = time.monotonic()
    first_interim = None
    finals: list[str] = []
    frames = _frames(pcm)
    speech_end = None
    i = 0
    while True:
        due = start + i * 0.1
        time.sleep(max(0.0, due - time.monotonic()))
        frame = frames[i] if i < len(frames) else SILENCE
        if i == len(frames):
            speech_end = time.monotonic()
        for e in session.accept_pcm16(frame):
            if e.kind == "interim" and first_interim is None:
                first_interim = time.monotonic() - start
            if e.kind == "final":
                finals.append(e.text)
        i += 1
        if speech_end and finals and i > len(frames):
            break
        if i > len(frames) + 50:
            break
    final_after = (time.monotonic() - speech_end) if finals and speech_end else None
    session.close()
    return {"first_interim": first_interim, "final": final_after,
            "text": " ".join(finals)}


def _fast(engine, pcm: bytes) -> dict:
    session = engine.open_session()
    times = []
    finals = []
    for frame in _frames(pcm):
        t = time.perf_counter()
        finals += [e.text for e in session.accept_pcm16(frame) if e.kind == "final"]
        times.append(time.perf_counter() - t)
    t = time.perf_counter()
    finals += [e.text for e in session.finish()]
    times.append(time.perf_counter() - t)
    session.close()
    audio = len(pcm) / transcriber.BYTES_PER_SECOND
    return {"rtf": sum(times) / audio, "chunk_ms_mean": 1000 * sum(times) / len(times),
            "chunk_ms_max": 1000 * max(times), "text": " ".join(finals)}


def _finals(engine, pcm: bytes) -> list[str]:
    session = engine.open_session()
    out = []
    for frame in _frames(pcm):
        out += [e.text for e in session.accept_pcm16(frame) if e.kind == "final"]
    out += [e.text for e in session.finish()]
    session.close()
    return out


def child(threads: int) -> dict:
    folder = Path(tempfile.mkdtemp(prefix="jarvis-bench-"))
    clips = {text: _spoken(text, folder) for text in COMMANDS}
    t = time.monotonic()
    engine = transcriber.SherpaEngine(threads=threads)
    load = time.monotonic() - t
    cpu0 = time.process_time()
    wall0 = time.monotonic()
    result: dict = {"threads": threads, "load": load, "samples": {}}
    for name, pcm in clips.items():
        fast = _fast(engine, pcm)
        rt = _realtime(engine, pcm)
        result["samples"][name] = {
            **{k: fast[k] for k in ("rtf", "chunk_ms_mean", "chunk_ms_max")},
            "first_interim": rt["first_interim"], "final": rt["final"],
            "wer_fast": _wer(name, fast["text"]),
            "wer_realtime": _wer(name, rt["text"]),
            "text": rt["text"]}
    result["cpu_share"] = (time.process_time() - cpu0) / (time.monotonic() - wall0)
    result["silence_finals"] = len(_finals(engine, b"\x00" * transcriber.BYTES_PER_SECOND * 30))
    zero = clips[COMMANDS[0]]
    pause = b"\x00" * transcriber.BYTES_PER_SECOND * 2
    result["repeat_finals"] = _finals(engine, zero + pause + zero)
    result["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20)
    shutil.rmtree(folder, ignore_errors=True)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--child", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.child:
        print(json.dumps(child(args.child)))
        return 0
    problem = transcriber.model_problem()
    if problem:
        print(problem)
        return 1
    results = []
    for n in args.threads:
        out = subprocess.run([sys.executable, __file__, "--child", str(n)],
                             capture_output=True, text=True, check=True).stdout
        results.append(json.loads(out.strip().splitlines()[-1]))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
