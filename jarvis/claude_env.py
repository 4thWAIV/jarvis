"""The environment every Claude Code child of JARVIS is given.

There is exactly one rule and it is a billing rule: JARVIS's Claude Code
children run on the user's **subscription**, never on an API key. The CLI
prefers an inherited `ANTHROPIC_API_KEY` over the login without saying so —
`claude auth status` still reports `loggedIn: true` while `apiKeySource`
quietly flips to the env key and the account's email and organisation go
blank — so a key in the environment silently moves every spawned run onto
paid API billing.

`server.py` loads `.env` into `os.environ` at import, and a developer's
`.env` legitimately holds `ANTHROPIC_API_KEY` for the older lookup paths.
That is how the key reaches a child that never wanted it.

This module exists so the scrub is written once. It was fixed for the brain
during milestone 1 and NOT for the run pipeline, which is precisely the
failure mode a second copy of the logic produces: the copy that was not
updated is the one nobody notices.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Optional

# Every ANTHROPIC_* variable, not just the key: the base URL and the model
# override redirect a child just as effectively as credentials do.
SCRUBBED_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_")
SCRUBBED_ENV_KEYS = {"CLAUDECODE"}

# asyncio.create_subprocess_exec gives a child's stdout/stderr StreamReader a
# 64 KiB *line* buffer by default (asyncio.streams._DEFAULT_LIMIT). `claude -p
# --output-format stream-json` emits one JSON object per line, and a single
# line carrying a large tool result (a big file read, a long assistant
# message, a large diff) routinely exceeds that. When it does, `readline()`
# raises `ValueError("Separator is not found, and chunk exceed the limit")` —
# which, uncaught, killed an otherwise-healthy run (run_executor.py) or the
# brain process (brain.py) outright. A run that had been working for 28
# minutes was recorded as `failed` in 0 seconds because of exactly this.
#
# This is a buffer *ceiling*, not a pre-allocation: asyncio grows the
# underlying bytearray as data arrives, so a generous limit costs nothing
# while idle. 64 MiB is comfortably larger than any single stream-json line
# JARVIS has observed in practice (the worst offenders are full-file Read
# results and large diffs, which top out in the low single-digit MiB) while
# staying small enough that even a runaway line cannot balloon memory
# unboundedly — pass this to every `create_subprocess_exec(..., limit=...)`
# that reads a Claude Code child's stdout/stderr.
STREAM_LINE_LIMIT = 64 * 1024 * 1024  # 64 MiB per line


def find_claude() -> Optional[str]:
    """The `claude` JARVIS starts when none is configured: the one on PATH.

    On Windows a launcher on PATH (npm's `claude.cmd` shim) is refused by
    `command_prefix`, so the executable it launches is found instead: npm's
    package ships `bin\\claude.exe` beside the shim's `node_modules`. Then
    the places Claude Code's own installers put `claude.exe` (its native
    installer's `~\\.local\\bin`, winget's `Links`), so a window opened
    before the install still finds it."""
    import shutil
    found = shutil.which("claude")
    if sys.platform != "win32":
        return found
    from pathlib import Path
    candidates = []
    if found and os.path.splitext(found)[1].lower() in (".cmd", ".bat", ".ps1"):
        candidates.append(Path(found).parent / "node_modules" / "@anthropic-ai"
                          / "claude-code" / "bin" / "claude.exe")
    elif found:
        return found
    home = Path(os.path.expanduser("~"))
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    candidates += [home / ".local" / "bin" / "claude.exe",
                   local / "Microsoft" / "WinGet" / "Links" / "claude.exe",
                   appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
                   / "bin" / "claude.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return found


def command_prefix(configured: str) -> list[str]:
    """The argv a configured `claude` (JARVIS_CLAUDE_PATH, or the one on
    PATH) begins with, for the brain and the run pipeline alike.

    macOS and Linux: a shell-quoted string, as always, so the setting may
    carry arguments. Windows: a path is a path. shlex's POSIX rules take a
    backslash for an escape and made the real
    C:\\Users\\...\\WinGet\\Links\\claude.EXE into C:Users...claude.EXE
    (measured), so an existing file named whole, spaces and all, is the one
    element, and anything else is split by Windows' own rules
    (CommandLineToArgvW). A .cmd or .bat launcher is refused: Windows runs
    it through cmd.exe, which parses the arguments again, and the brain's
    carry its whole system prompt."""
    if sys.platform != "win32":
        return shlex.split(configured)
    text = configured.strip()
    if not text:
        raise FileNotFoundError("no claude executable is configured")
    whole = text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text
    argv = [whole] if os.path.isfile(whole) else _windows_split(text)
    if not argv:
        raise FileNotFoundError("no claude executable is configured")
    if os.path.splitext(argv[0])[1].lower() in (".cmd", ".bat"):
        raise PermissionError(f"{argv[0]} is a batch file; JARVIS starts claude only from "
                              f"an executable, so its arguments reach it unparsed")
    return argv


def _windows_split(text: str) -> list[str]:
    """`text` split as Windows splits a command line (shell32's
    CommandLineToArgvW)."""
    import ctypes
    from ctypes import wintypes
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    if not text:
        return []
    count = ctypes.c_int(0)
    found = shell32.CommandLineToArgvW(text, ctypes.byref(count))
    if not found:
        raise ctypes.WinError()
    try:
        return [found[i] for i in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(found, ctypes.c_void_p))


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with everything that would redirect or
    re-bill a Claude Code child removed. Everything else — PATH, HOME,
    CLAUDE_CONFIG_DIR, the user's own variables — passes through untouched."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items()
            if not k.startswith(SCRUBBED_ENV_PREFIXES)
            and k not in SCRUBBED_ENV_KEYS}
