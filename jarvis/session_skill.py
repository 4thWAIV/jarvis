"""Shared skills for the user's Claude Code and Codex sessions.

A session reaches the user through JARVIS's one signed route (`python -m
jarvis.message`, every decision a package made with `python -m
jarvis.make_package`) and reports its work with `work_report.py` and
`workflow_report.py`. A session only knows how if something tells it, so the
repo ships the templates under `session_skills/` and this installs them into
every Claude Code config root and the user's Codex home. The data folder and
the certificate are this install's, so the installed copy is the template
rendered with its real paths and commands, not a link to it.

Nothing here runs on its own: `python -m jarvis.session_skill install` writes it, and
preflight only reports `status()`.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from jarvis import codex_sessions
from jarvis import data_paths
from jarvis import session_watch

NAME = "talk-to-jarvis"
TEMPLATE = data_paths.PACKAGE_DIR / "session_skills" / NAME / "SKILL.md"

INSTALLED = "installed"
MISSING = "missing"
OUT_OF_DATE = "out_of_date"


def _reporter(script: str) -> str:
    """A reporting command with this install's certificate, quoted for a shell."""
    return " ".join(shlex.quote(str(p)) for p in (
        data_paths.PACKAGE_DIR / script, "--cert", data_paths.CONFIG_DIR / "cert.pem"))


def _module(name: str, cert: bool = True) -> str:
    """A JARVIS command (`python -m jarvis.<name>`) with the Python this
    install runs on, quoted for a shell, with this install's certificate
    when it takes one. It runs from any folder (so a package's project
    defaults to the session's own), finding JARVIS by PYTHONPATH; -P keeps
    the session's folder off sys.path, so nothing there is imported in
    JARVIS's place."""
    return (f"PYTHONPATH={shlex.quote(str(data_paths.PACKAGE_DIR.parent))} "
            f"{shlex.quote(sys.executable)} -P -m jarvis.{name}"
            + (f" --cert {shlex.quote(str(data_paths.CONFIG_DIR / 'cert.pem'))}" if cert else ""))


def render(name: str = NAME, relative: str = "SKILL.md") -> str:
    """The skill with this install's folders filled in."""
    from jarvis.language_documents import authoring_contract
    return ((TEMPLATE.parent.parent / name / relative).read_text(encoding="utf-8")
            .replace("{{answers_dir}}", str(data_paths.answers_dir()))
            .replace("{{workflow_report}}", _reporter("workflow_report.py"))
            .replace("{{work_report}}", _reporter("work_report.py"))
            .replace("{{message}}", _module("message"))
            .replace("{{make_package}}", _module("make_package", cert=False))
            .replace("{{agent_key}}", _module("agent_key"))
            .replace("{{making_a_package}}",
                     str(data_paths.SOURCE_ROOT / "docs" / "making-a-package.md"))
            + "\n\n" + authoring_contract() + "\n")


def targets() -> list[Path]:
    """Where the shared skill files go: `skills/` under every
    Claude Code config root that exists, and under the user's Codex home if
    he has one. A tool he does not use gets nothing."""
    homes = [r for r in session_watch.config_roots() if r.is_dir()]
    codex = codex_sessions.codex_home()
    if codex.is_dir():
        homes.append(codex)
    return [home / "skills" / path.relative_to(TEMPLATE.parent.parent)
            for home in homes for path in sorted(TEMPLATE.parent.parent.rglob("*")) if path.is_file()]


def _installed_text(path: Path) -> str:
    relative = path.relative_to(next(p for p in path.parents if p.name == "skills"))
    return render(relative.parts[0], str(Path(*relative.parts[1:])))


def status() -> dict[Path, str]:
    """Each target, installed, missing, or out of date with this install."""
    out = {}
    for path in targets():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            out[path] = MISSING
            continue
        out[path] = INSTALLED if text == _installed_text(path) else OUT_OF_DATE
    return out


def install() -> list[Path]:
    """Write the rendered skill to every target. The paths written."""
    written = []
    for path in targets():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_installed_text(path), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if argv[1:] != ["install"]:
        print("usage: python -m jarvis.session_skill install", file=sys.stderr)
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
