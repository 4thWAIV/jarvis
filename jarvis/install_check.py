"""What install.sh and install.ps1 ask JARVIS itself, so the two scripts share
one answer instead of each working it out in its own language.

    python -m jarvis.install_check stamp-ok pip|npm       exit 0 when already done
    python -m jarvis.install_check stamp-set pip|npm      record that it is done
    python -m jarvis.install_check done certs|playwright|speech|embedding|skill
                                   (skill: 0 done, 1 to install, 2 no Claude/Codex home yet)
    python -m jarvis.install_check models                 what the model downloads weigh
    python -m jarvis.install_check preflight              jarvis.preflight's checks, one line each

Only the standard library is imported at the top, so the stamps can be asked
before anything is installed. `preflight` observes and changes nothing, as
`jarvis.preflight` promises; `skill` and the two models are asked of the
modules that own them (`session_skill.status`, `transcriber.model_problem`,
`embedding_models.model_problem`), never worked out again here.

A stamp is the SHA-256 of what an install step read (requirements.txt and
pyproject.toml with the Python's version; frontend/package-lock.json), kept
inside what the step made (`.venv`, `frontend/node_modules`), so deleting
that folder forgets it. Nothing here touches the data folder, settings or
any key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

STAMPS = {
    "pip": (ROOT / ".venv" / ".jarvis-install.json", ("requirements.txt", "pyproject.toml")),
    "npm": (ROOT / "frontend" / "node_modules" / ".jarvis-install.json",
            ("frontend/package-lock.json",)),
}


def stamp_value(name: str) -> str:
    """The hash the step's inputs have now."""
    _, inputs = STAMPS[name]
    digest = hashlib.sha256()
    if name == "pip":
        digest.update(sys.version.encode())
    for relative in inputs:
        digest.update(relative.encode() + b"\0")
        try:
            digest.update((ROOT / relative).read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def stamp_ok(name: str) -> bool:
    path, _ = STAMPS[name]
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sha256") == stamp_value(name)
    except (OSError, ValueError):
        return False


def stamp_set(name: str) -> bool:
    path, _ = STAMPS[name]
    if not path.parent.is_dir():
        return False
    path.write_text(json.dumps({"sha256": stamp_value(name)}) + "\n", encoding="utf-8")
    return True


# -- what is already done ------------------------------------------------------

def playwright_installed() -> bool:
    """Chromium's install folders, as `playwright install --dry-run` names
    them, each finished (Playwright's own INSTALLATION_COMPLETE marker)."""
    result = subprocess.run([sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        return False
    places = [Path(line.split(":", 1)[1].strip()) for line in result.stdout.splitlines()
              if line.strip().startswith("Install location:")]
    return bool(places) and all((p / "INSTALLATION_COMPLETE").is_file() for p in places)


def certs_made() -> bool:
    return (ROOT / "cert.pem").is_file() and (ROOT / "key.pem").is_file()


def speech_installed() -> bool:
    from jarvis import transcriber
    return transcriber.model_problem() is None


def embedding_installed() -> bool:
    from jarvis import embedding_models
    return embedding_models.model_problem() is None


LINE_MAX = 220
NO_HOME = 2   # exit status of `done skill` when no Claude Code or Codex home exists yet


def skill_state() -> int:
    """0 installed and current, 1 to install, NO_HOME when neither Claude
    Code nor Codex has made its home yet (a first `claude` run does)."""
    from jarvis import session_skill
    found = session_skill.status()
    if not found:
        return NO_HOME
    return 0 if all(state == session_skill.INSTALLED for state in found.values()) else 1


DONE = {"certs": certs_made, "playwright": playwright_installed, "speech": speech_installed,
        "embedding": embedding_installed}


# -- what a download weighs ----------------------------------------------------

def mib(size: int) -> int:
    return round(size / (1 << 20))


def model_sizes() -> list[str]:
    from jarvis import embedding_models, transcriber
    english = transcriber.find_model(transcriber.DEFAULT_LANGUAGE)
    embedding = embedding_models.DEFAULT
    return [f"English speech model: about {mib(english.archive_bytes)} MiB",
            f"Embedding model ({embedding.label}): about {mib(embedding.download_bytes)} MiB"]


# -- preflight -----------------------------------------------------------------

def preflight_checks() -> list:
    """jarvis.preflight's own checks and the Claude provider's, run as the
    server runs them at startup (`ClaudeProvider.preflight`)."""
    from jarvis import preflight
    provider = ((preflight._check_claude_cli, preflight._check_claude_login),
                (preflight._check_anthropic_key_leftover_sync,))
    return asyncio.run(preflight.run_checks(provider_checks=provider))


def preflight_line(check) -> str:
    word = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}.get(check.status, check.status.upper())
    text = f"{word} {check.name}: {' '.join(check.message.split())}"
    if check.remedy:
        text += f" ({' '.join(check.remedy.split())})"
    # One line a check; the whole text is in jarvis.preflight and Diagnostics.
    return text if len(text) <= LINE_MAX else text[:LINE_MAX - 3] + "..."


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    if command in ("stamp-ok", "stamp-set") and len(args) == 2 and args[1] in STAMPS:
        return 0 if (stamp_ok if command == "stamp-ok" else stamp_set)(args[1]) else 1
    if command == "done" and args[1:] == ["skill"]:
        return skill_state()
    if command == "done" and len(args) == 2 and args[1] in DONE:
        return 0 if DONE[args[1]]() else 1
    if command == "models":
        print("\n".join(model_sizes()))
        return 0
    if command == "preflight":
        # Preflight informs; it never fails the install. A warning is
        # something to do, often something only the person can.
        for check in preflight_checks():
            print(preflight_line(check))
        return 0
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
