#!/usr/bin/env python3
"""Measure the Spanish model on the recorded clips, with this person's own
JARVIS speech engine (`jarvis.transcriber`), and write the results.

    "$JARVIS_PYTHON" scripts/bench.py [--threads 2]

JARVIS_PYTHON and JARVIS_SOURCE are set by JARVIS when it runs a package's
step: the Python JARVIS runs on (it has sherpa-onnx) and where its `jarvis`
package is. Each clip in results/clips (NN.wav with NN.txt) is fed after
half a second of silence, 100 ms at a time as fast as it decodes, as JARVIS
feeds the microphone. For each: what was heard, the word error rate against
the reference (as written, and with accents ignored), the real-time factor,
the slowest 100 ms, and how many finals it came back as.

Writes results/bench.json (every figure) and results/summary.md (a table
for a person to read).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import unicodedata
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if os.environ.get("JARVIS_SOURCE"):
    sys.path.insert(0, os.environ["JARVIS_SOURCE"])

from jarvis import transcriber  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_model import DESTINATION  # noqa: E402

CLIPS = HERE / "results" / "clips"
FRAME = transcriber.PROTOCOL["frameBytes"]
LEAD_SILENCE = b"\x00" * (transcriber.BYTES_PER_SECOND // 2)


def _pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != \
                (transcriber.SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{path} is not {transcriber.SAMPLE_RATE} Hz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def _words(text: str) -> list[str]:
    """Lowercased words, accented letters kept, punctuation dropped."""
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text.lower())


def _folded(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(c))


def wer(ref: str, hyp: str, fold: bool = False) -> float:
    if fold:
        ref, hyp = _folded(ref), _folded(hyp)
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


def measure(engine, pcm: bytes) -> dict:
    session = engine.open_session()
    finals, times = [], []
    for i in range(0, len(pcm), FRAME):
        t = time.perf_counter()
        finals += [e.text for e in session.accept_pcm16(pcm[i:i + FRAME]) if e.kind == "final"]
        times.append(time.perf_counter() - t)
    t = time.perf_counter()
    finals += [e.text for e in session.finish() if e.kind == "final"]
    times.append(time.perf_counter() - t)
    session.close()
    return {"text": " ".join(finals), "finals": len(finals),
            "rtf": sum(times) / (len(pcm) / transcriber.BYTES_PER_SECOND),
            "chunk_ms_max": 1000 * max(times)}


def machine() -> dict:
    def sysctl(name: str) -> str:
        return subprocess.run(["sysctl", "-n", name], capture_output=True,
                              text=True).stdout.strip()
    return {"mac": platform.mac_ver()[0], "cpu": sysctl("machdep.cpu.brand_string"),
            "cores": os.cpu_count(), "memory_gb": round(int(sysctl("hw.memsize") or 0) / 2**30)}


def summary(result: dict) -> str:
    clips = result["clips"]
    mean = lambda key: sum(c[key] for c in clips) / len(clips)  # noqa: E731
    m = result["machine"]
    lines = [
        "# Spanish speech test",
        "",
        f"Model: {result['model']}, {result['threads']} threads, loaded in "
        f"{result['load_sec']:.2f} s.",
        f"Machine: {m['cpu']}, {m['cores']} cores, {m['memory_gb']} GB, macOS {m['mac']}.",
        "",
        f"- Word error rate: {100 * mean('wer'):.1f}% as written, "
        f"{100 * mean('wer_folded'):.1f}% with accents ignored, over {len(clips)} clips.",
        f"- Real-time factor: {mean('rtf'):.3f} (decode time over audio time).",
        f"- Slowest 100 ms of audio took {max(c['chunk_ms_max'] for c in clips):.0f} ms.",
        f"- Clips heard as more than one final: "
        f"{sum(c['finals'] > 1 for c in clips)} of {len(clips)}.",
        "",
        "| clip | said | heard | WER |",
        "|---|---|---|---|",
    ]
    lines += [f"| {c['clip']} | {c['reference']} | {c['text'] or '(nothing)'} | "
              f"{100 * c['wer']:.0f}% |" for c in clips]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=transcriber.DEFAULT_THREADS)
    args = parser.parse_args(argv)
    problem = transcriber.model_problem(DESTINATION)
    if problem:
        print(f"{problem} (Run scripts/fetch_model.py first.)")
        return 1
    wavs = sorted(CLIPS.glob("*.wav"))
    if not wavs:
        print(f"No clips in {CLIPS}; record them first (scripts/record.sh).")
        return 1
    t = time.monotonic()
    engine = transcriber.SherpaEngine(DESTINATION, threads=args.threads)
    result = {"model": DESTINATION.name, "threads": args.threads,
              "load_sec": time.monotonic() - t, "machine": machine(), "clips": []}
    for path in wavs:
        reference = path.with_suffix(".txt").read_text(encoding="utf-8").strip()
        got = measure(engine, LEAD_SILENCE + _pcm(path))
        result["clips"].append({"clip": path.stem, "reference": reference, **got,
                                "wer": wer(reference, got["text"]),
                                "wer_folded": wer(reference, got["text"], fold=True)})
    out = HERE / "results"
    (out / "bench.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (out / "summary.md").write_text(summary(result), encoding="utf-8")
    print(summary(result))
    print(f"Wrote {out / 'summary.md'} and {out / 'bench.json'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
