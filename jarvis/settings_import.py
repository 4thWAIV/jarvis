"""The one-time copy of `.env` into Settings.

`.env` was where settings were kept until every setting became a Settings
control. At the first start after that, `start_file_once` (before anything
binds) and `records_once` (once the records are open) read `.env` with the
one parser (`parse_env_lines`), copy each setting it holds into its place
(the start file, the Keychain, its area's record, brains.json or
engines.json) and record that it was done (`/settings/env-import.settings`,
and the start file's `imported_from_env`). After that `.env` is read only
for the data folder it may name (`_follow_data_folder`, until Settings has
a data folder control); nothing loads it into the environment.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from jarvis import data_paths, env_names, settings_service, settings_specs, start_settings

log = logging.getLogger("jarvis.settings_import")

DONE_PATH = "/settings/env-import.settings"


def parse_env_lines(text: str) -> list[tuple[str, str]]:
    """Every (key, value) a reader of `.env` sees in `text`, in order."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out.append((k.strip(), v.strip().strip('"').strip("'")))
    return out


def env_file() -> Path:
    """The `.env` copied from (data_paths.env_file_path)."""
    return data_paths.env_file_path()


def _env_values() -> dict[str, str]:
    path = env_file()
    try:
        text = path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return {}
    except OSError as e:
        log.warning("could not read %s for the one-time copy (%s)", path, e)
        return {}
    return {k: v for k, v in parse_env_lines(text)
            if v and v != "your-fish-audio-api-key-here"}


def _parsed(store: str) -> dict:
    out = {}
    for name, text in _env_values().items():
        s = settings_specs.BY_ENV.get(name)
        if s is None or s.store != store:
            continue
        try:
            out[s.key] = settings_service.parse_text(s, text)
        except settings_service.Refused as e:
            log.warning("%s in .env was not copied: %s", name, e)
    return out


# The data folder `.env` named (JARVIS_DATA_DIR) and this start uses, or None.
_data_dir_from_env: str | None = None


def data_dir_from_env() -> str | None:
    """The data folder `.env` named that this start uses (said in Settings,
    Server), or None."""
    return _data_dir_from_env


def _follow_data_folder() -> None:
    """While `.env` was loaded, a JARVIS_DATA_DIR line in it moved the whole
    data folder. Settings has no data folder control (the owner's question
    5, docs/planning/settings-only/design.md), so a folder `.env` names is
    used for this start, before anything opens a data folder, and Settings,
    Server says so: an upgrade never starts on an empty default folder and
    copies `.env` into it. A JARVIS_DATA_DIR this process was given (an
    isolated instance) always wins."""
    global _data_dir_from_env
    if env_names.is_set("JARVIS_DATA_DIR"):
        return
    raw = _env_values().get("JARVIS_DATA_DIR", "").strip()
    if not raw:
        return
    folder = str(Path(raw).expanduser())
    env_names.publish("JARVIS_DATA_DIR", folder)
    _data_dir_from_env = folder
    log.warning("using the data folder %s named in %s; Settings has no data folder "
                "control yet", folder, env_file())


def start_file_once() -> None:
    """Copy `.env`'s start settings into the start file, when there is no
    start file yet; first, the data folder `.env` named. Never raises."""
    try:
        _follow_data_folder()
    except Exception as e:  # noqa: BLE001
        log.warning("the data folder in .env could not be followed: %s", e)
    try:
        found = _parsed("start")
        if found and not start_settings.path().exists():
            start_settings.change(found, imported=True)
            log.info("copied %s from .env into the start file", ", ".join(sorted(found)))
    except Exception as e:  # noqa: BLE001 - a start is never stopped by the copy
        log.warning("the one-time copy of .env's start settings failed: %s", e)


def records_once() -> None:
    """Copy `.env`'s other settings into their records, the Keychain and
    their own files, once. Never raises."""
    try:
        from jarvis import records
        if records.get(DONE_PATH) is not None:
            return
        copied = sorted(_copy_records() + _copy_secrets() + _copy_elsewhere())
        records.write(DONE_PATH, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "copied": copied, "from": str(env_file())},
                      change="Copy settings from .env once")
        if copied:
            log.info("copied %s from .env into Settings, once", ", ".join(copied))
    except Exception as e:  # noqa: BLE001
        log.warning("the one-time copy of .env into Settings failed: %s", e)


def imported() -> dict | None:
    """When `.env` was copied in and what came across, or None."""
    try:
        from jarvis import records
        rec = records.get(DONE_PATH)
    except Exception:  # noqa: BLE001
        return None
    return None if rec is None else rec.data


def _copy_records() -> list[str]:
    found = _parsed("record")
    if found:
        settings_service.save_imported(found)
    return [settings_specs.spec(k).env for k in found]


def _copy_secrets() -> list[str]:
    out = []
    for key, value in _parsed("secret").items():
        if value and not settings_service.value(key):
            settings_service.change(key, value)
            out.append(settings_specs.spec(key).env)
    return out


def _copy_elsewhere() -> list[str]:
    values = _env_values()
    out = []
    provider = values.get("JARVIS_BRAIN_PROVIDER", "").strip()
    model = values.get("JARVIS_BRAIN_MODEL", "").strip()
    if provider or model:
        from jarvis import brains_config
        try:
            name = provider or brains_config.load()["default"]
            if name not in brains_config.load()["brains"]:
                brains_config.add(name)
            if provider:
                brains_config.set_default(name)
                out.append("JARVIS_BRAIN_PROVIDER")
            if model:
                brains_config.set_model(name, model)
                out.append("JARVIS_BRAIN_MODEL")
        except Exception as e:  # noqa: BLE001
            log.warning("the brain in .env was not copied: %s", e)
    run_model = values.get("JARVIS_RUN_MODEL", "").strip()
    if run_model:
        from jarvis import engine_choice
        try:
            engine_choice.set_kind("run", "fixed", "claude", run_model)
            out.append("JARVIS_RUN_MODEL")
        except Exception as e:  # noqa: BLE001
            log.warning("JARVIS_RUN_MODEL in .env was not copied: %s", e)
    return out
