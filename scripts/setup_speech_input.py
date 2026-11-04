#!/usr/bin/env python3
"""Install the local speech-input models JARVIS listens with.

    .venv/bin/python scripts/setup_speech_input.py [--language en|es|all] [--archive PATH]

Installs Banafo's Kroko streaming Zipformer model for a language (English
by default, about 55 MiB; Spanish is about 119 MiB), as sherpa-onnx exports
it, through `speech_models.install`: downloaded from the sherpa-onnx GitHub
release, checked against the SHA-256 GitHub publishes for that asset,
extracted into a temporary folder beside the destination, and renamed into
place complete, so an interrupted run never looks installed. The
destination is `transcriber.model_dir(model)`: `<data>/models/<model>`, or
under JARVIS_STT_MODEL_DIR.

--archive uses an archive already on disk (a previous download) instead of
downloading, for one language; it is checked against the same digest.
JARVIS downloads a model only when asked: by this script, or by its own
install route and tool (speech_models.py).

The models are Banafo's community models, under CC-BY-SA; the sherpa-onnx
engine is Apache-2.0. See https://huggingface.co/Banafo/Kroko-ASR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import speech_models, transcriber  # noqa: E402


def _print_progress(state: str, done: int, total: int) -> None:
    if state == "downloading":
        if total:
            print(f"\r  {done >> 20} / {total >> 20} MiB", end="", flush=True)
    else:
        print(f"\n  {state}", flush=True)


def install(archive: Path | None = None,
            language: str = transcriber.DEFAULT_LANGUAGE) -> Path:
    model = transcriber.find_model(language)
    if model is None:
        raise SystemExit(f"No speech model for {language!r}.")
    print(f"{model.label}: {model.url}")
    try:
        destination = speech_models.install(model, archive, _print_progress)
    except speech_models.InstallRefused as refused:
        raise SystemExit(str(refused)) from None
    print(f"Installed: {destination}")
    return destination


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    languages = [m.language for m in transcriber.MODELS]
    parser.add_argument("--language", choices=languages + ["all"],
                        default=transcriber.DEFAULT_LANGUAGE,
                        help="which model to install (default: English)")
    parser.add_argument("--archive", type=Path,
                        help="an archive already downloaded, checked like a download")
    args = parser.parse_args(argv)
    if args.language == "all":
        if args.archive:
            parser.error("--archive is one model's; name its --language")
        for language in languages:
            install(None, language)
    else:
        install(args.archive, args.language)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
