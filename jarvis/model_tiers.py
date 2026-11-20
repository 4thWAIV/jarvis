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
model, its versions oldest available to newest, and on up the tiers. The
first one available is taken and the job's log says which; a sweep that
finds none is `NoModel`, which puts the job in Error.

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
    versions newest to oldest, then each stronger family's oldest
    available to newest."""
    order = list(resolve(engine, family_of(engine, tier), listed))
    for up in range(tier - 1, 0, -1):
        order += list(reversed(resolve(engine, family_of(engine, up), listed)))
    return order


def first(engine: str, tier: int, listed: list[str]) -> str:
    """The first model the sweep finds. Raises NoModel."""
    found = sweep(engine, tier, listed)
    if not found:
        raise NoModel(f"no {engine} model in tier {tier} or above is available "
                      f"(listed: {', '.join(listed) or 'none'})")
    return found[0]


# ── the brain and model a role runs on ────────────────────────────────────

@dataclass(frozen=True)
class Pick:
    engine: str               # "claude" or "codex"
    model: str
    tier: int
    why: str                  # engine_choice's reason, or "judges_other_brain"
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
    author's brain, for the judges' Settings option. Raises NoModel."""
    from jarvis import engine_choice
    choice = engine_choice.choose(kind, engine=engine, model=model, requester=requester)
    chosen, why, named = choice.engine, choice.why, choice.model
    other = engine_choice.other_brain_for_judges(kind, author_engine) if not engine else None
    if other:
        chosen, why, named = other, "judges_other_brain", None
    available = listed(chosen)
    if named:
        return Pick(chosen, named, tier_of(chosen, named) or tier, why, [named])
    tried = sweep(chosen, tier, available)
    if not tried:
        raise NoModel(f"no {chosen} model in tier {tier} or above is available "
                      f"(listed: {', '.join(available) or 'none'})")
    return Pick(chosen, tried[0], tier, why, tried)
