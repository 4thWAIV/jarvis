"""Every environment name JARVIS reads or sets, and the one place that reads
the environment.

No one ever leaves the dashboard: every choice a person makes is a Settings
control (`settings_specs.py`), saved as a record, a Keychain item or the start
file, never read from the environment. What is left in the environment is
not a person's to set:

* WIRING: what JARVIS (or a parent JARVIS) sets for itself and its
  children, so a child finds its server, its data folder or its session;
* PLATFORM: what the operating system or a provider's own program sets,
  read only to find things (a home folder) or to scrub them from a child;
* TEST: the test suite's harness.

SETTING names are listed too, each naming its setting, because `.env` held
them before Settings did: `settings_import` copies them in once, by reading
`.env` itself, and nothing reads them from the environment.

`read` is the only reader: it refuses a SETTING name and any name not here.
`child` builds a child process's environment, and `publish` sets a WIRING
name for this process and its children. tests/test_env_registry.py fails
any other read of the environment under jarvis/ or scripts/, any name used
that is not listed, and a SETTING with no Settings control or brain tool.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from jarvis import settings_specs

WIRING = "wiring"
PLATFORM = "platform"
TEST = "test"
SETTING = "setting"

# name -> what it is for.
WIRING_NAMES: dict[str, str] = {
    "JARVIS_BIND_HOST": "the address this server bound, published by server.main for "
                        "itself and its children",
    "JARVIS_PORT": "the port this server bound, published the same way",
    "JARVIS_SCHEME": "http or https as this server serves, published the same way",
    "JARVIS_TOOL_URL": "where the brain's MCP child reaches /internal/tool",
    "JARVIS_TOOL_TOKEN_FILE": "the tool token's file, for the brain's MCP child",
    "JARVIS_AGENT_SESSION": "an agent's own session id, for its JARVIS commands",
    "JARVIS_ACCESS_KEY": "a legacy access key for workflow_report, until the cut-over",
    "JARVIS_DECISION_CHECK_SERVER": "marks the isolated decision-check JARVIS",
    "JARVIS_URL": "the JARVIS an agent's command talks to, when it is not this Mac's own",
    "JARVIS_DATA_DIR": "the data folder of an isolated instance (a test, the decision "
                       "check)",
    "JARVIS_PYTHON": "the Python a package's command runs with",
    "JARVIS_SOURCE": "JARVIS's source folder, for a package's command",
    "PACKAGE_DIR": "a package's own folder, for its command",
    "MSYS_NO_PATHCONV": "keeps Git for Windows' openssl from rewriting a path",
    "PYTHONPATH": "the checkout's parent, so the brain's MCP child can import jarvis "
                  "(its mcp.json `env`)",
    "JARVIS_API_TARGET": "where the Vite dev server proxies /api (frontend/vite.config.ts)",
    "JARVIS_VITE_PORT": "the Vite dev server's port (frontend/vite.config.ts)",
}

PLATFORM_NAMES: dict[str, str] = {
    "CLAUDE_CODE_MESSAGING_TOKEN": "given by Claude Code to a session's own commands",
    "CLAUDECODE": "set by Claude Code inside a session, scrubbed from children",
    "CLAUDE_CONFIG_DIR": "Claude Code's home, when not ~/.claude",
    "CODEX_HOME": "Codex's home, when not ~/.codex",
    "APPDATA": "Windows' roaming application data",
    "LOCALAPPDATA": "Windows' local application data",
    "ProgramFiles": "Windows' program folder, to find Git for Windows' openssl",
    "HOME": "the user's home",
    "PATH": "where programs are found",
    "TMPDIR": "the temporary folder",
    "USER": "the user's login name",
    "CODEX_API_KEY": "read only to scrub it from Codex children",
    "PYTHONUNBUFFERED": "Python's own: a child's output unbuffered",
}

# Names read by prefix, only to scrub them from a child.
PLATFORM_PREFIXES: dict[str, str] = {
    "ANTHROPIC_": "never billed: scrubbed from every Claude Code child",
    "CLAUDE_CODE_": "a parent session's own, scrubbed from every Claude Code child",
    "OPENAI_": "scrubbed from every Codex child",
}

TEST_NAMES: dict[str, str] = {
    "JARVIS_ENV_FILE": "the .env the one-time copy reads (the suite points it away from "
                       "the developer's)",
    "JARVIS_ENGINE_AUTOSTART": "0 never starts the knowledge engine; records live in it, so "
                               "JARVIS cannot run without it",
    "JARVIS_ENGINE_EPHEMERAL": "a throwaway engine that skips every fsync",
}


def setting_names() -> dict[str, str]:
    """Each SETTING name and the setting (or other control) that holds it now."""
    out = {s.env: s.key for s in settings_specs.SPECS if s.env}
    out.update({name: target for name, (target, _tools) in settings_specs.ELSEWHERE.items()})
    return out


def kind_of(name: str) -> Optional[str]:
    if name in WIRING_NAMES:
        return WIRING
    if name in PLATFORM_NAMES or name.startswith(tuple(PLATFORM_PREFIXES)):
        return PLATFORM
    if name in TEST_NAMES:
        return TEST
    if name in setting_names():
        return SETTING
    return None


class NotAnEnvironmentName(KeyError):
    """A name the environment may not be asked for: a setting, or unlisted."""


def _readable(name: str) -> None:
    kind = kind_of(name)
    if kind is None:
        raise NotAnEnvironmentName(f"{name} is not in env_names; list it before reading it")
    if kind == SETTING:
        raise NotAnEnvironmentName(f"{name} is a setting: read it with settings_service")


def read(name: str, default: str = "") -> str:
    """The value of a WIRING, PLATFORM or TEST name, or `default`."""
    _readable(name)
    value = os.environ.get(name)
    return default if value is None else value


def is_set(name: str) -> bool:
    _readable(name)
    return name in os.environ


def with_prefix(prefix: str) -> list[str]:
    """The names set now under a listed PLATFORM prefix (to warn or scrub)."""
    if prefix not in PLATFORM_PREFIXES:
        raise NotAnEnvironmentName(f"{prefix}* is not a listed prefix")
    return sorted(k for k in os.environ if k.startswith(prefix))


def _settable(name: str) -> None:
    kind = kind_of(name)
    if kind not in (WIRING, TEST):
        raise NotAnEnvironmentName(f"{name} may not be set: only WIRING and TEST names are")


def publish(name: str, value: str, *, only_if_unset: bool = False) -> None:
    """Set a WIRING name for this process and the children it starts."""
    _settable(name)
    if only_if_unset:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value


def withdraw(name: str) -> None:
    _settable(name)
    os.environ.pop(name, None)


def child(base: Optional[Mapping[str, str]] = None, *, drop=(), drop_prefixes=(),
          **wiring: str) -> dict[str, str]:
    """A child process's environment: this one's (or `base`), without the
    names `drop` and `drop_prefixes` name, with the listed names given set
    (never a SETTING).
    The one place a whole environment is copied."""
    source = os.environ if base is None else base
    prefixes = tuple(drop_prefixes)
    env = {k: v for k, v in source.items()
           if k not in drop and not (prefixes and k.startswith(prefixes))}
    for name, value in wiring.items():
        if kind_of(name) in (None, SETTING):
            raise NotAnEnvironmentName(f"{name} may not be set for a child: list it as WIRING")
        env[name] = str(value)
    return env
