"""
engine_choice.py — which engine, and which model, each kind of agent JARVIS
starts goes on.

His design (universal Codex, section 8): every kind of agent JARVIS starts
has its own setting, "preferred provider and model": the package author
(make_package's fresh author), agent runs (`spawn_run`), builds
(`start_build`) and agent packages (`run_package_agent`). A retry is not a
kind: it keeps its own run's engine. Each setting is one of

    {"pick": "fixed", "engine": "claude" | "codex", "model": <a model or null>}
                       a brain and one of its models (null: the engine's own default)
    {"pick": "requester"}      the engine of whoever asks: the agent calling
                               make_package, or the brain for what he asks of it
    {"pick": "least_stress"}   the brain with the most plan left against its pace

and one more setting across them, "override if the selected agent is in
token stress": when on, a brain that is behind its pace gives way to the
least stressed one. Token stress is the brain board's plus/minus figure,
`usage_store.week`'s `ahead` (expected use minus actual, percentage points,
seven-day window): stressed is ahead below 0, least stressed is the highest
ahead. A brain not measured is never called stressed, nor chosen as least.
An engine he names for one agent ("on Codex") is his choice and is kept.

Kept in `<data>/jarvis/engines.json`, beside brains.json, so the page, the
brain's tools and make_package (another process) read the same choice. A
missing or unreadable file is the defaults below: what JARVIS did before
there was a choice (the author on the requester's engine, everything else
on Claude).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from jarvis import brain_provider, brains_config, data_paths, usage_store

log = logging.getLogger("jarvis.engines")

KINDS = ("package_author", "run", "build", "agent_package")
PICKS = ("fixed", "requester", "least_stress")
ENGINES = brain_provider.ENGINES

DEFAULTS = {
    "package_author": {"pick": "requester"},
    "run": {"pick": "fixed", "engine": "claude", "model": None},
    "build": {"pick": "fixed", "engine": "claude", "model": None},
    "agent_package": {"pick": "fixed", "engine": "claude", "model": None},
}


class Refused(ValueError):
    """A change that was not made: English words for the brain and the
    route, and a code and values the page words in its own language
    (left.brains.engines.refused.*)."""

    def __init__(self, message: str, code: str, **values: str) -> None:
        super().__init__(message)
        self.code = code
        self.values = values


@dataclass(frozen=True)
class Choice:
    engine: str               # "claude" or "codex"
    model: Optional[str]      # None: the engine's own default
    why: str                  # "asked", "fixed", "requester", "least_stress" or "override"


# ── the stored settings ───────────────────────────────────────────────────

def _path():
    return data_paths.brains_path().with_name("engines.json")


def _setting(raw) -> Optional[dict]:
    """A stored setting as one of the three shapes, or None."""
    if not isinstance(raw, dict) or raw.get("pick") not in PICKS:
        return None
    if raw["pick"] != "fixed":
        return {"pick": raw["pick"]}
    if raw.get("engine") not in ENGINES:
        return None
    model = raw.get("model")
    return {"pick": "fixed", "engine": raw["engine"],
            "model": model if isinstance(model, str) and model else None}


def load() -> dict:
    try:
        body = json.loads(_path().read_text())
    except FileNotFoundError:
        body = {}
    except (OSError, ValueError) as e:
        log.warning(f"engines: unreadable engines.json ignored ({e})")
        body = {}
    kinds = body.get("kinds") if isinstance(body, dict) else None
    kinds = kinds if isinstance(kinds, dict) else {}
    return {"kinds": {k: _setting(kinds.get(k)) or dict(DEFAULTS[k]) for k in KINDS},
            "override_stressed": bool(body.get("override_stressed")) if isinstance(body, dict) else False}


def _save(cfg: dict) -> None:
    path = _path()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".engines-", suffix=".json")
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


def available() -> list[str]:
    """The engines that can be chosen: each brain JARVIS has (Settings,
    Brains), Claude always."""
    added = brains_config.load()["brains"]
    return [engine for engine, provider in ENGINES.items() if provider in added]


def set_kind(kind: str, pick: str, engine: Optional[str] = None,
             model: Optional[str] = None, models: Optional[list[str]] = None) -> dict:
    """One kind's setting. `models` is the chosen brain's own list, checked
    by the caller that can read it (it may start a process)."""
    if kind not in KINDS:
        raise Refused(f"no kind of agent called {kind!r}; the kinds are {', '.join(KINDS)}",
                      "unknown-kind", kind=kind)
    if pick not in PICKS:
        raise Refused(f"choose one of {', '.join(PICKS)}", "unknown-pick", pick=str(pick))
    setting: dict = {"pick": pick}
    if pick == "fixed":
        if engine not in available():
            raise Refused(f"{engine} is not a brain JARVIS has", "not-added", engine=str(engine))
        if model and models is not None and model not in models:
            raise Refused(f"{engine} does not offer {model}", "no-such-model",
                          engine=engine, model=model)
        setting = {"pick": "fixed", "engine": engine, "model": model or None}
    cfg = load()
    cfg["kinds"][kind] = setting
    _save(cfg)
    return cfg


def set_override(on: bool) -> dict:
    cfg = load()
    cfg["override_stressed"] = bool(on)
    _save(cfg)
    return cfg


# ── token stress ──────────────────────────────────────────────────────────

def stress(engine: str) -> Optional[float]:
    """The brain board's plus/minus for this engine's brain: `ahead` in
    percentage points, or None while it is not measured."""
    week = usage_store.week(service=ENGINES[engine])
    return week["ahead"] if week.get("measured") else None


def stressed(engine: str) -> bool:
    ahead = stress(engine)
    return ahead is not None and ahead < 0


def least_stressed(engines: Optional[list[str]] = None) -> Optional[str]:
    """The measured brain with the highest ahead (the first listed on a
    tie), or None when none is measured."""
    measured = [(stress(e), i, e) for i, e in enumerate(engines or available())]
    measured = [(ahead, i, e) for ahead, i, e in measured if ahead is not None]
    if not measured:
        return None
    return max(measured, key=lambda m: (m[0], -m[1]))[2]


# ── the choice ────────────────────────────────────────────────────────────

def choose(kind: str, *, engine: Optional[str] = None, model: Optional[str] = None,
           requester: Optional[str] = None) -> Choice:
    """The engine and model for one agent of `kind`. `engine` (and `model`)
    are what he named for this one, kept as asked; `requester` the engine
    of whoever asks."""
    if engine:
        if engine not in ENGINES:
            raise Refused(f"no engine called {engine!r}", "unknown-engine", engine=engine)
        return Choice(engine, model or None, "asked")
    cfg = load()
    setting = cfg["kinds"].get(kind) or DEFAULTS[kind]
    pick = setting["pick"]
    if pick == "fixed":
        choice = Choice(setting["engine"], model or setting.get("model"), "fixed")
    elif pick == "requester":
        choice = Choice(requester if requester in ENGINES else "claude", model or None, "requester")
    else:
        choice = Choice(least_stressed() or "claude", model or None, "least_stress")
    if cfg["override_stressed"] and pick != "least_stress" and stressed(choice.engine):
        calmer = least_stressed()
        if calmer and calmer != choice.engine:
            return Choice(calmer, None, "override")
    return choice


def view() -> dict:
    """Every setting with the figures it is decided on, for the page and
    the brain alike."""
    cfg = load()
    engines = []
    for engine in available():
        ahead = stress(engine)
        engines.append({"engine": engine, "provider": ENGINES[engine], "ahead": ahead,
                        "stressed": ahead is not None and ahead < 0})
    return {**cfg, "kinds_order": list(KINDS), "engines": engines,
            "least_stressed": least_stressed()}
