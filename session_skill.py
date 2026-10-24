"""The skill that teaches the user's own sessions to reach him through JARVIS.

JARVIS reads two folders sessions write to: the needs board
(`data_paths.needs_dir()`) and session messages
(`data_paths.session_messages_dir()`). A session only knows they exist if
something tells it, so the repo ships `session_skills/talk-to-jarvis/SKILL.md`
and this installs it into every Claude Code config root and the user's Codex
home. The data folder is configurable, so the installed copy is the template
rendered with this install's real paths, not a link to it.

Nothing here runs on its own: `python session_skill.py install` writes it, and
preflight only reports `status()`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import codex_sessions
import data_paths
import session_watch

NAME = "talk-to-jarvis"
TEMPLATE = Path(__file__).parent / "session_skills" / NAME / "SKILL.md"

INSTALLED = "installed"
MISSING = "missing"
OUT_OF_DATE = "out_of_date"


def render() -> str:
    """The skill with this install's folders filled in."""
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{needs_dir}}", str(data_paths.needs_dir()))
            .replace("{{messages_dir}}", str(data_paths.session_messages_dir()))
            .replace("{{workflow_report}}", str(Path(__file__).resolve().with_name("workflow_report.py"))))


def targets() -> list[Path]:
    """Where the skill goes: `skills/talk-to-jarvis/SKILL.md` under every
    Claude Code config root that exists, and under the user's Codex home if
    he has one. A tool he does not use gets nothing."""
    homes = [r for r in session_watch.config_roots() if r.is_dir()]
    codex = codex_sessions.codex_home()
    if codex.is_dir():
        homes.append(codex)
    return [home / "skills" / NAME / "SKILL.md" for home in homes]


def status() -> dict[Path, str]:
    """Each target, installed, missing, or out of date with this install."""
    wanted = render()
    out = {}
    for path in targets():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            out[path] = MISSING
            continue
        out[path] = INSTALLED if text == wanted else OUT_OF_DATE
    return out


def install() -> list[Path]:
    """Write the rendered skill to every target. The paths written."""
    text = render()
    written = []
    for path in targets():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if argv[1:] != ["install"]:
        print(f"usage: python {Path(__file__).name} install", file=sys.stderr)
        return 2
    written = install()
    if not written:
        print("No Claude Code or Codex home found; nothing installed.")
        return 1
    for path in written:
        print(f"installed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
