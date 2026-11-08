#!/usr/bin/env python3
"""Install the embedding model the knowledge engine's vector search runs on.

    .venv/bin/python scripts/setup_embedding_model.py [--files FOLDER]

Installs multilingual-e5-small (int8 ONNX, about 135 MB: the 118 MB model
and its 17 MB tokenizer) from Xenova/multilingual-e5-small at a pinned
commit, through `embedding_models.install`: each file downloaded into a
temporary folder beside the destination, checked against its published
SHA-256 and size, and the folder renamed into place complete, so an
interrupted run never looks installed. The destination is
`<data>/models/embedding/multilingual-e5-small-int8`.

--files uses a folder already holding the two files (a previous download,
under their published names or their installed ones); they are checked
against the same digests. JARVIS downloads the model only when asked: by
this script, or by the Knowledge view's Install and the brain's
install_embedding_model.

The model is MIT licensed; onnxruntime (requirements.txt) runs it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import embedding_models  # noqa: E402


def _print_progress(state: str, done: int, total: int) -> None:
    if state == "downloading":
        if total:
            print(f"\r  {done >> 20} / {total >> 20} MiB", end="", flush=True)
    else:
        print(f"\n  {state}", flush=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=Path, default=None,
                        help="a folder already holding the model's two files")
    args = parser.parse_args(argv)
    model = embedding_models.DEFAULT
    print(f"{model.label}: {model.repo}@{model.revision[:8]}")
    try:
        where = embedding_models.install(model, files_from=args.files, progress=_print_progress)
    except embedding_models.InstallRefused as refused:
        raise SystemExit(str(refused)) from None
    print(f"Installed: {where}")
    problem = embedding_models.runtime_problem()
    if problem:
        print(f"But it cannot run yet: {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
