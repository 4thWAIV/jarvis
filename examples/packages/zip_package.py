#!/usr/bin/env python3
"""Zip a work package's folder so it can be sent in a message.

    python3 examples/packages/zip_package.py examples/packages/spanish-speech-test [OUT.zip]

package.json goes at the zip's root, as JARVIS requires, and every other
file beside it with its permissions, so its scripts stay executable. What a
run of the package makes (results/, model/) and macOS's .DS_Store are left
out. OUT.zip defaults to <name>-<version>.zip in the current folder.
docs/work-packages.md has the format.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

LEFT_OUT = {"results", "model", "__pycache__", ".DS_Store"}


def files(folder: Path) -> list[Path]:
    out = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in LEFT_OUT)
        out += [Path(root) / n for n in sorted(names) if n not in LEFT_OUT]
    return out


def main(argv: list[str]) -> int:
    if not argv or len(argv) > 2:
        print(__doc__.strip().splitlines()[2])
        return 2
    folder = Path(argv[0]).resolve()
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    if not (folder / manifest["skill"]).is_file():
        print(f"{folder} has no skill file {manifest['skill']}.")
        return 1
    out = Path(argv[1] if len(argv) == 2 else f"{manifest['name']}-{manifest['version']}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files(folder):
            z.write(path, path.relative_to(folder).as_posix())
    print(f"{out.resolve()} ({out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
