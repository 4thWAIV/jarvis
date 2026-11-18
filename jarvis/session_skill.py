"""Shared skills for the user's Claude Code and Codex sessions.

A session reaches the user through JARVIS's one signed route (`python -m
jarvis.message`, every decision a package made with `python -m
jarvis.make_package`) and reports its work with `work_report.py` and
`workflow_report.py`. A session only knows how if something tells it, so the
repo ships the templates under `session_skills/` and this installs them into
every Claude Code config root and the user's Codex home. The data folder and
the certificate are this install's, so the installed copy is the template
rendered with its real paths and commands, not a link to it.

The same install puts JARVIS's agent MCP server (agent_mcp.py, its
`entry()`) in each of those homes, as the user-scope server `jarvis`: in a
Claude Code root's `.claude.json` (`~/.claude.json` for `~/.claude`) under
`mcpServers`, and in the Codex home's `config.toml` as a block between two
marker lines that only this rewrites. Everything else in either file is
left as it was.

Nothing here runs on its own: `python -m jarvis.session_skill install` writes it, and
preflight only reports `status()`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from jarvis import codex_sessions
from jarvis import data_paths
from jarvis import session_watch
from jarvis import settings_service

NAME = "talk-to-jarvis"
TEMPLATE = data_paths.PACKAGE_DIR / "session_skills" / NAME / "SKILL.md"

# The agent MCP server's name in every home (its tools are mcp__jarvis__*).
MCP_NAME = "jarvis"
CODEX_BEGIN = "# BEGIN jarvis agent tools (python -m jarvis.session_skill install)"
CODEX_END = "# END jarvis agent tools"

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
            .replace("{{retrieve_attachment}}", _module("retrieve_attachment"))
            .replace("{{language_document}}", _module("language_document"))
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


def _claude_config(root: Path) -> Path:
    """The Claude Code user config a config root reads: `~/.claude.json`
    for the default `~/.claude`, else `<root>/.claude.json`."""
    root = root.expanduser()
    return (Path.home() / ".claude.json" if root == Path.home() / ".claude"
            else root / ".claude.json")


def mcp_targets() -> list[Path]:
    """Where the agent MCP server goes: each existing Claude Code config
    root's user config, and the Codex home's config.toml if he has one."""
    out = [_claude_config(r) for r in session_watch.config_roots() if r.is_dir()]
    codex = codex_sessions.codex_home()
    if codex.is_dir():
        out.append(codex / "config.toml")
    return out


def _claude_entry() -> dict:
    from jarvis import agent_mcp
    return {"type": "stdio", **agent_mcp.entry()}


def _codex_block() -> str:
    from jarvis import agent_mcp, codex_provider
    return "\n".join([CODEX_BEGIN, *codex_provider.mcp_server_toml(MCP_NAME, agent_mcp.entry())[1:],
                      CODEX_END]) + "\n"


_CODEX_BLOCK_RE = re.compile(re.escape(CODEX_BEGIN) + r".*?" + re.escape(CODEX_END) + r"\n?",
                             re.DOTALL)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _mcp_state(path: Path) -> str:
    text = _read(path)
    if text is None:
        return MISSING
    if path.suffix == ".toml":
        found = _CODEX_BLOCK_RE.search(text)
        if found is None:
            return MISSING
        return INSTALLED if found.group(0).rstrip("\n") == _codex_block().rstrip("\n") \
            else OUT_OF_DATE
    try:
        servers = json.loads(text).get("mcpServers") or {}
    except (ValueError, AttributeError):
        return OUT_OF_DATE
    if MCP_NAME not in servers:
        return MISSING
    return INSTALLED if servers[MCP_NAME] == _claude_entry() else OUT_OF_DATE


def _write_whole(path: Path, text: str) -> None:
    """`text` as the whole of `path`, by one rename (the program that owns
    the file never reads half of it), keeping its permissions (0600 when
    new)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _install_mcp(path: Path) -> bool:
    """Put the agent MCP server in `path`. False, changing nothing, when the
    file cannot be read as its program writes it, or (Codex) already names
    a `jarvis` server outside the block this writes."""
    text = _read(path)
    if path.suffix == ".toml":
        rest = _CODEX_BLOCK_RE.sub("", text or "")
        if re.search(rf"^\s*\[mcp_servers\.{MCP_NAME}(\.|\])", rest, re.MULTILINE):
            return False
        _write_whole(path, (rest.rstrip("\n") + "\n\n" if rest.strip() else "") + _codex_block())
        return True
    try:
        config = json.loads(text) if text else {}
    except ValueError:
        return False
    if not isinstance(config, dict) or not isinstance(config.get("mcpServers", {}), dict):
        return False
    config.setdefault("mcpServers", {})[MCP_NAME] = _claude_entry()
    _write_whole(path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return True


def _installed_text(path: Path) -> str:
    relative = path.relative_to(next(p for p in path.parents if p.name == "skills"))
    return render(relative.parts[0], str(Path(*relative.parts[1:])))


def status() -> dict[Path, str]:
    """Each target (the skill's files, then the agent MCP server's
    configs), installed, missing, or out of date with this install."""
    out = {}
    for path in targets():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            out[path] = MISSING
            continue
        out[path] = INSTALLED if text == _installed_text(path) else OUT_OF_DATE
    for path in mcp_targets():
        out[path] = _mcp_state(path)
    return out


def install() -> list[Path]:
    """Write the rendered skill to every target, and the agent MCP server
    into every home's config. The paths written."""
    written = []
    for path in targets():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_installed_text(path), encoding="utf-8")
        written.append(path)
    for path in mcp_targets():
        if _install_mcp(path):
            written.append(path)
    return written


def main(argv: list[str]) -> int:
    if argv[1:] != ["install"]:
        print("usage: python -m jarvis.session_skill install", file=sys.stderr)
        return 2
    # Its own process: the extra Claude Code and Codex homes he saved in
    # Settings, Agents are read from the records before the targets are.
    settings_service.load_once()
    written = install()
    if not written:
        print("No Claude Code or Codex home found; nothing installed.")
        return 1
    for path in written:
        print(f"installed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
