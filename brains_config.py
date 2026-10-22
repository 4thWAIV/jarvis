"""
brains_config.py — which brains JARVIS has, and which one it starts with.

Kept in `<data>/jarvis/brains.json` and written by the Settings view's Brains
section:

    {"default": "anthropic",
     "brains": {"anthropic": {"model": "sonnet"}, "openai": {"model": "gpt-5.6-terra"}}}

A brain is added once it is listed here; Claude is always listed, set up as it
has always been. `JARVIS_BRAIN_PROVIDER` and `JARVIS_BRAIN_MODEL` in `.env`
still win when set, for tests and development (brain.BrainConfig.from_env).
A missing or unreadable file is Claude alone, which is how JARVIS has always
started.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Optional

import data_paths

log = logging.getLogger("jarvis.brains")

KNOWN = ("anthropic", "openai")
ALWAYS = "anthropic"


def _fresh() -> dict:
    return {"default": ALWAYS, "brains": {ALWAYS: {}}}


def load() -> dict:
    try:
        body = json.loads(data_paths.brains_path().read_text())
    except FileNotFoundError:
        return _fresh()
    except (OSError, ValueError) as e:
        log.warning(f"brains: unreadable brains.json ignored ({e})")
        return _fresh()
    out = _fresh()
    brains = body.get("brains") if isinstance(body, dict) else None
    if isinstance(brains, dict):
        for name, spec in brains.items():
            if name in KNOWN:
                model = spec.get("model") if isinstance(spec, dict) else None
                out["brains"][name] = {"model": model} if isinstance(model, str) and model else {}
    default = body.get("default") if isinstance(body, dict) else None
    if default in out["brains"]:
        out["default"] = default
    return out


def _save(cfg: dict) -> None:
    path = data_paths.brains_path()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".brains-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(name: str) -> dict:
    if name not in KNOWN:
        raise ValueError(f"no brain called {name!r}")
    cfg = load()
    cfg["brains"].setdefault(name, {})
    _save(cfg)
    return cfg


def remove(name: str) -> dict:
    cfg = load()
    if name == ALWAYS:
        raise ValueError("Claude is always there")
    if cfg["default"] == name:
        raise ValueError("choose another brain to start with first")
    cfg["brains"].pop(name, None)
    _save(cfg)
    return cfg


def set_model(name: str, model: str) -> dict:
    cfg = load()
    if name not in cfg["brains"]:
        raise ValueError(f"{name} has not been added")
    cfg["brains"][name] = {"model": model}
    _save(cfg)
    return cfg


def set_default(name: str) -> dict:
    cfg = load()
    if name not in cfg["brains"]:
        raise ValueError(f"{name} has not been added")
    cfg["default"] = name
    _save(cfg)
    return cfg


def model_for(name: str) -> Optional[str]:
    return load()["brains"].get(name, {}).get("model")
