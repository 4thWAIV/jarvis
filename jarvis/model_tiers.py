"""
model_tiers.py — the four model tiers, and finding a model that works.

Tim's taxonomy for now (2026-11-18): "1 - Fable / Astra 2 - Opus / Sol
3 - Sonnet / Terra 4 - haiku / Luna". A code file, not a Settings control
(his word, 2026-11-19): each brain's model families and their tier, one
line a family, so moving a family between tiers is one line. The scored
ranking (model-ranking.md) replaces it later, when there is data.

How a family becomes a model (`resolve`):
- Claude: the family name is Claude Code's alias, which always means the
  latest model (v1: latest only; the team server may give older versions'
  names later).
- Codex: every model of the family in the app server's live `model/list`,
  newest first, ordered by the version in its name (gpt-6.2 above gpt-6.1
  above gpt-6). A family pinned to an older version is named in full in
  `PINNED`.

Finding a model that works (`sweep`, his word 2026-11-19): from the
expected model down its versions, newest to oldest, then up to the next
model, its versions oldest available to newest. Nothing past the next
model is tried. The first one available is taken and the job's log says
which; a sweep that finds none is `NoModel`, which puts the job in Error.
A model the brain lists but refuses when asked is swept past the same way
(sessions.ChatSession).

Which brain a role runs on, and its model (`pick`): the brain is the one
engines.json gives that kind of agent (engine_choice, the main brains'
method, the token-stress override included); the model is the role's tier
on that brain, unless engines.json names one for it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# Each brain's model families and their tier: one line a family.
FAMILY_TIER: dict[str, dict[str, int]] = {
    "claude": {
        "fable": 1,
        "opus": 2,
        "sonnet": 3,
        "haiku": 4,
    },
    "codex": {
        "astra": 1,
        "sol": 2,
        "terra": 3,
        "luna": 4,
    },
}

# A family held to an older version, named in full ("terra": "gpt-6.1-terra").
# Empty: every family resolves to its newest listed version.
PINNED: dict[str, dict[str, str]] = {"claude": {}, "codex": {}}

TIERS = (1, 2, 3, 4)

# The tier each brain starts on when brains.json names no model: Claude's
# sonnet and Codex's terra, as before there were tiers.
DEFAULT_TIER = 3

_CODEX_MODEL = re.compile(r"gpt-(\d+(?:\.\d+)*)-([a-z]+)")


class NoModel(LookupError):
    """A sweep that found no model: the job goes to Error."""


def families(engine: str) -> list[str]:
    """The engine's families, strongest first."""
    return sorted(FAMILY_TIER[engine], key=lambda f: FAMILY_TIER[engine][f])


def family_of(engine: str, tier: int) -> str:
    """The engine's family in this tier."""
    for family, at in FAMILY_TIER[engine].items():
        if at == tier:
            return family
    raise KeyError(f"{engine} has no family in tier {tier}")


def tier_of(engine: str, model: str) -> Optional[int]:
    """The tier a model (a family alias or a full name) is in, or None."""
    family = model if engine == "claude" else _codex_family(model)
    return FAMILY_TIER[engine].get(family or "")


def _codex_family(model: str) -> Optional[str]:
    found = _CODEX_MODEL.fullmatch(model or "")
    return found.group(2) if found else None


def _codex_version(model: str) -> tuple[int, ...]:
    found = _CODEX_MODEL.fullmatch(model)
    return tuple(int(p) for p in found.group(1).split(".")) if found else ()


def resolve(engine: str, family: str, listed: list[str]) -> list[str]:
    """The family's models that are available, newest first."""
    pinned = PINNED.get(engine, {}).get(family)
    if pinned:
        return [pinned] if pinned in listed else []
    if engine == "claude":
        return [family] if family in listed else []
    found = [m for m in listed if _codex_family(m) == family]
    return sorted(found, key=_codex_version, reverse=True)


def sweep(engine: str, tier: int, listed: list[str]) -> list[str]:
    """Every model to try for this tier, in order: the expected family's
    versions newest to oldest, then the next model up's versions oldest
    available to newest (none above tier 1)."""
    order = list(resolve(engine, family_of(engine, tier), listed))
    if tier > 1:
        order += list(reversed(resolve(engine, family_of(engine, tier - 1), listed)))
    return order


def first(engine: str, tier: int, listed: list[str]) -> str:
    """The first model the sweep finds. Raises NoModel."""
    found = sweep(engine, tier, listed)
    if not found:
        raise NoModel(f"no {engine} model in tier {tier} or the next one up is available "
                      f"(listed: {', '.join(listed) or 'none'})")
    return found[0]


# ── the brain and model a role runs on ────────────────────────────────────

@dataclass(frozen=True)
class Pick:
    engine: str               # "claude" or "codex"
    model: str
    tier: int
    why: str                  # engine_choice's reason, or "owners_other_brain"
    tried: list[str] = field(default_factory=list)   # the sweep, in order, for the job's log


def listed_models(engine: str) -> list[str]:
    """The models the engine offers now (its provider's own list)."""
    from jarvis import brain_provider
    return list(brain_provider.provider(brain_provider.ENGINES[engine]).models())


def pick(kind: str, tier: int, *, requester: Optional[str] = None,
         engine: Optional[str] = None, model: Optional[str] = None,
         author_engine: Optional[str] = None,
         listed: Callable[[str], list[str]] = listed_models) -> Pick:
    """The brain and model for one role. `kind` is its engines.json entry;
    `engine` and `model` what was named for this one; `author_engine` the
    author's brain, for the owners' Settings option. Raises NoModel."""
    from jarvis import engine_choice
    choice = engine_choice.choose(kind, engine=engine, model=model, requester=requester)
    chosen, why, named = choice.engine, choice.why, choice.model
    other = engine_choice.other_brain_for_owners(kind, author_engine) if not engine else None
    if other:
        chosen, why, named = other, "owners_other_brain", None
    available = listed(chosen)
    if named:
        return Pick(chosen, named, tier_of(chosen, named) or tier, why, [named])
    tried = sweep(chosen, tier, available)
    if not tried:
        raise NoModel(f"no {chosen} model in tier {tier} or the next one up is available "
                      f"(listed: {', '.join(available) or 'none'})")
    return Pick(chosen, tried[0], tier, why, tried)


# ── how much a model takes and gives in one turn ──────────────────────────

# Each family's context window and the most it answers in one turn, in
# tokens, where its brain's own model list does not say (Mark rebuilt for
# speed, Tim 2026-11-21: a page fits the model's window with the
# instructions, room for the answer and room to spare). Codex's list
# (`models_cache.json` in JARVIS's Codex home) gives each model's window;
# neither brain lists what a model answers at most, so that is here for
# both, on the low side.
WINDOW: dict[str, dict[str, int]] = {
    "claude": {"fable": 200_000, "opus": 200_000, "sonnet": 200_000, "haiku": 200_000},
    "codex": {"astra": 272_000, "sol": 272_000, "terra": 272_000, "luna": 272_000},
}
ANSWER_MOST: dict[str, int] = {"claude": 32_000, "codex": 100_000}

# How many of a role's calls each brain is given at once: the three a
# managed run is held to (run_executor's permits), for each brain.
PARALLEL: dict[str, int] = {"claude": 3, "codex": 3}


def _listed_window(model: str) -> Optional[int]:
    """The model's usable window as Codex's own model list gives it (its
    window times the share Codex lets a turn use), or None when the list
    does not name it."""
    import json
    from jarvis import data_paths
    try:
        listed = json.loads((data_paths.codex_home() / "models_cache.json").read_text())
    except (OSError, ValueError):
        return None
    for entry in listed.get("models") or [] if isinstance(listed, dict) else []:
        if isinstance(entry, dict) and entry.get("slug") == model:
            window = entry.get("context_window")
            share = entry.get("effective_context_window_percent") or 100
            if isinstance(window, int) and window > 0:
                return window * int(share) // 100
    return None


def context_window(engine: str, model: str) -> int:
    """A model's context window in tokens: its brain's own list where it
    gives one (Codex), else its family's in `WINDOW`."""
    if engine == "codex":
        found = _listed_window(model)
        if found:
            return found
    family = model if engine == "claude" else (_codex_family(model) or "")
    table = WINDOW[engine]
    return table.get(family, min(table.values()))


def answer_most(engine: str) -> int:
    """The most a model of this brain answers in one turn, in tokens."""
    return ANSWER_MOST[engine]
