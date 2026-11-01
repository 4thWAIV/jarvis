#!/usr/bin/env python3
"""Measure a local speech model on this Mac, through transcriber.py's own
engine and session classes, on spoken commands.

    .venv/bin/python scripts/bench_speech_input.py [--threads 1 2 4] [--json]
        [--model DIR ...] [--clips DIR]

--model names a model folder (the four files `SherpaEngine` loads); give it
more than once to compare models on the same clips. Without it, the installed
model (`transcriber.model_dir()`) is measured.

--clips names a folder of 16 kHz mono PCM16 WAV files, each with its
reference transcript beside it in a .txt file of the same name. Without it,
the English commands below are synthesised with macOS's `say`.

Each model and thread count runs in its own child process, so its cold load
and peak memory are its own. For each it records:

- load: seconds to build the recognizer (cold, from disk).
- rtf: decode time divided by audio time, feeding as fast as it decodes.
- chunk_ms: the mean and worst decode time of one 100 ms frame.
- first_interim / final: seconds from the start of the audio to the first
  interim, and from the end of the speech to its final, feeding 100 ms frames
  at real-time pace and then silence until the final arrives.
- silence: finals produced by 30 s of silence (must be none).
- repeat: finals for the same sentence said twice with a pause (must be two).
- split: clips whose speech came back as more than one final.
- peak_rss_mb (and rss_before_load_mb, before the model was built), and the
  WER of each clip against its reference: `wer` as written, `wer_folded`
  with accents ignored.
- load_avg: the machine's 1-minute load average before and after, since a
  busy machine slows every figure.

Every clip is fed after half a second of silence. It reads no microphone,
writes nothing into the clips folder and keeps nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
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
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (transcriber.SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{path} is not {transcriber.SAMPLE_RATE} Hz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def _clips(folder: Path | None, scratch: Path) -> dict[str, tuple[str, bytes]]:
    """Each clip's name -> (reference, PCM16): the WAV files in `folder` with
    the .txt beside each, or the English commands said by `say`."""
    if folder is None:
        return {text: (text, _spoken(text, scratch)) for text in COMMANDS}
    clips = {}
    for wav in sorted(folder.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if not ref.is_file():
            raise SystemExit(f"{wav} has no reference transcript at {ref}")
        clips[wav.stem] = (ref.read_text(encoding="utf-8").strip(),
                           LEAD_SILENCE + _wav(wav))
    if not clips:
        raise SystemExit(f"no .wav files in {folder}")
    return clips


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)]


def _words(text: str) -> list[str]:
    """Lowercased words, accented letters kept, punctuation dropped."""
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text.lower())


def _folded(text: str) -> str:
    """`text` with its accents taken off (si for sí, n for ñ)."""
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(c))


def _wer(ref: str, hyp: str, fold: bool = False) -> float:
    if fold:
        ref, hyp = _folded(ref), _folded(hyp)
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
            "finals": len(finals), "text": " ".join(finals)}


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


def _rss_mb() -> float:
    """Peak resident memory so far (ru_maxrss is bytes on macOS, KiB on Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1 << 20) if sys.platform == "darwin" else peak / (1 << 10)


def child(threads: int, model: Path, clip_dir: Path | None) -> dict:
    load_before = os.getloadavg()[0]
    folder = Path(tempfile.mkdtemp(prefix="jarvis-bench-"))
    clips = _clips(clip_dir, folder)
    rss_before = _rss_mb()
    t = time.monotonic()
    engine = transcriber.SherpaEngine(model, threads=threads)
    load = time.monotonic() - t
    cpu0 = time.process_time()
    wall0 = time.monotonic()
    result: dict = {"model": model.name, "threads": threads, "load": load,
                    "rss_before_load_mb": rss_before, "samples": {}}
    for name, (ref, pcm) in clips.items():
        fast = _fast(engine, pcm)
        rt = _realtime(engine, pcm)
        result["samples"][name] = {
            **{k: fast[k] for k in ("rtf", "chunk_ms_mean", "chunk_ms_max")},
            "first_interim": rt["first_interim"], "final": rt["final"],
            "finals": rt["finals"],
            "wer_fast": _wer(ref, fast["text"]),
            "wer": _wer(ref, rt["text"]),
            "wer_folded": _wer(ref, rt["text"], fold=True),
            "reference": ref, "text": rt["text"]}
    result["cpu_share"] = (time.process_time() - cpu0) / (time.monotonic() - wall0)
    result["silence_finals"] = len(_finals(engine, b"\x00" * transcriber.BYTES_PER_SECOND * 30))
    zero = next(iter(clips.values()))[1]
    pause = b"\x00" * transcriber.BYTES_PER_SECOND * 2
    result["repeat_finals"] = _finals(engine, zero + pause + zero)
    result["peak_rss_mb"] = _rss_mb()
    result["load_avg"] = [load_before, os.getloadavg()[0]]
    shutil.rmtree(folder, ignore_errors=True)
    return result


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(value, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def summary(results: list[dict]) -> str:
    """One Markdown row per model and thread count, means over the clips."""
    lines = ["| model | threads | load s | RTF | chunk ms max | final s (max) | "
             "WER | WER folded | split | peak MB | load avg |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = list(r["samples"].values())
        finals = [x["final"] for x in s if x["final"] is not None]
        lines.append("| " + " | ".join([
            r["model"], str(r["threads"]), _fmt(r["load"], ".2f"),
            _fmt(_mean([x["rtf"] for x in s]), ".3f"),
            _fmt(max(x["chunk_ms_max"] for x in s), ".0f"),
            f"{_fmt(_mean(finals), '.2f')} ({_fmt(max(finals, default=None), '.2f')})",
            _fmt(100 * _mean([x["wer"] for x in s]), ".1f") + "%",
            _fmt(100 * _mean([x["wer_folded"] for x in s]), ".1f") + "%",
            f"{sum(x['finals'] > 1 for x in s)}/{len(s)}",
            _fmt(r["peak_rss_mb"], ".0f"),
            "{:.1f} -> {:.1f}".format(*r["load_avg"])]) + " |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--model", type=Path, action="append",
                        help="a model folder; repeat to compare models")
    parser.add_argument("--clips", type=Path,
                        help="WAV files, each with a .txt reference beside it")
    parser.add_argument("--child", type=int)
    parser.add_argument("--json", action="store_true",
                        help="every clip's figures as JSON, not the summary table")
    args = parser.parse_args(argv)
    models = [m.expanduser() for m in (args.model or [transcriber.model_dir()])]
    clips = args.clips.expanduser() if args.clips else None
    if args.child:
        print(json.dumps(child(args.child, models[0], clips)))
        return 0
    for model in models:
        problem = transcriber.model_problem(model)
        if problem:
            print(problem)
            return 1
    results = []
    for model in models:
        for n in args.threads:
            cmd = [sys.executable, __file__, "--child", str(n), "--model", str(model)]
            if clips:
                cmd += ["--clips", str(clips)]
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
            results.append(json.loads(out.strip().splitlines()[-1]))
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    print(summary(results))
    for r in results:
        print(f"\n{r['model']}, {r['threads']} threads: "
              f"{len(r['repeat_finals'])} finals for a clip said twice (want 2), "
              f"{r['silence_finals']} for 30 s of silence (want 0)")
        for name, x in r["samples"].items():
            if x["wer"]:
                print(f"  {name}: {x['reference']!r} -> {x['text']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
