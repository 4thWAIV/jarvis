"""Bounds an operator can remove by accident are not bounds.

Two shapes, and both were open.

1. `run_executor._resolve_default_timeout` and `_resolve_idle` say in their
   own docstrings that a run may never be unbounded — "there is deliberately
   no way to ask for no timeout", "a bound that can be set to zero is a bound
   an operator can accidentally remove, and the thing it is protecting is a
   permit the whole pipeline shares". They then checked only `> 0`, so
   `JARVIS_RUN_TIMEOUT_SEC=inf` and `=1e30` were both accepted and the wall
   clock was gone. They are Settings now (`run_timeout_sec`, `run_idle_sec`),
   which refuse such a value at the door, and the wall clock still holds
   behind it.

2. Every setting a route can write passes settings_service.check. The old
   `.env` validator had no length bound at all, so a 100 KB `USER_NAME`
   round-tripped through `POST /api/settings/preferences` and landed
   unwrapped in the brain's `--append-system-prompt`.

Both are tested as CLASSES: every `_resolve_*` in `run_executor` found from
the AST, and every text setting in settings_specs — not the two the finding
named.
"""

import ast
import importlib
import math
import sys
from pathlib import Path

import pytest
from tests.settings_fixtures import hold_setting, default_setting

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EXECUTOR = ROOT / "jarvis" / "run_executor.py"


# --- 1. every bound the executor resolves from a setting -----------------

def _resolvers() -> dict:
    """{function: setting it reads} for every `_resolve_*` in run_executor
    that takes a bound off a setting. Found in the source, so one added
    next year is bounded here before it can be removed there."""
    tree = ast.parse(EXECUTOR.read_text())
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("_resolve_"):
            continue
        # The setting is named as a literal handed to the shared helper.
        from jarvis import settings_specs
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                    and sub.value in settings_specs.BY_KEY):
                out[node.name] = sub.value
                break
    return out


RESOLVERS = _resolvers()


def test_the_resolvers_are_found_in_the_source():
    assert set(RESOLVERS) == {"_resolve_default_timeout", "_resolve_idle"}, \
        RESOLVERS
    assert set(RESOLVERS.values()) == {"run_timeout_sec", "run_idle_sec"}, RESOLVERS


@pytest.fixture
def executor(monkeypatch):
    default_setting("run_timeout_sec")
    default_setting("run_idle_sec")
    from jarvis import run_executor
    importlib.reload(run_executor)
    return run_executor


# Values that are numbers, pass `> 0`, and are not wall clocks.
UNBOUNDED = ["inf", "Infinity", "+inf", "1e30", "1e300", "9" * 40]
NOT_A_NUMBER = ["nan", "NaN"]


@pytest.mark.parametrize("name", sorted(RESOLVERS))
@pytest.mark.parametrize("raw", UNBOUNDED + NOT_A_NUMBER)
def test_no_setting_can_remove_the_wall_clock(executor, name, raw):
    """`> 0` is not "is a wall clock". `inf` passes it, and so does 1e30 —
    thirty-one orders of magnitude past the heat death of the run. The
    setting refuses them, and a value that reached the executor some other
    way is still held to the wall."""
    from jarvis import settings_service
    with pytest.raises(settings_service.Refused):
        hold_setting(RESOLVERS[name], raw)
    held = {RESOLVERS[name]: float(raw)}
    real = settings_service.value
    executor.settings_service.value = lambda key: held.get(key, real(key))
    try:
        value = getattr(executor, name)(None)
    finally:
        executor.settings_service.value = real
    assert math.isfinite(value), (name, raw, value)
    assert 0 < value <= executor.MAX_BOUND_SEC, (name, raw, value)


@pytest.mark.parametrize("name", sorted(RESOLVERS))
@pytest.mark.parametrize("explicit", [float("inf"), 1e30, float("nan")])
def test_no_caller_can_remove_it_either(executor, name, explicit):
    """A caller's own `timeout_sec` "still wins" — but not past the wall.
    `spawn_run` takes this argument from the brain."""
    value = getattr(executor, name)(explicit)
    assert math.isfinite(value), (name, explicit, value)
    assert 0 < value <= executor.MAX_BOUND_SEC, (name, explicit, value)


@pytest.mark.parametrize("name", sorted(RESOLVERS))
def test_an_ordinary_override_is_still_honoured(executor, monkeypatch, name):
    """A wall that ignores the operator is not a fix."""
    hold_setting(RESOLVERS[name], 120)
    assert getattr(executor, name)(None) == 120.0
    assert getattr(executor, name)(90.0) == 90.0


def test_the_maximum_is_a_working_day_or_less(executor):
    """The number itself is a judgement and belongs in one place: long
    enough that no real `start_build` hits it, short enough that a wedged
    run is reclaimed the same day."""
    assert executor.MAX_BOUND_SEC <= 24 * 3600
    assert executor.MAX_BOUND_SEC >= executor._DEFAULT_TIMEOUT_SEC


# --- 2. every setting a route can write ---------------------------------

@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module


def _texts():
    from jarvis import settings_specs
    return [s for s in settings_specs.SPECS if s.kind in ("text", "path", "secret")]


def test_every_text_setting_is_bounded():
    """The class, from settings_specs itself. A text setting added next year
    is bounded the day it is added, or this fails."""
    from jarvis import settings_service
    assert len(_texts()) >= 6
    for spec in _texts():
        with pytest.raises(settings_service.Refused) as refused:
            settings_service.check(spec, "x" * 100_000)
        assert refused.value.code == "tooLong", spec.key


def test_a_name_is_bounded_more_tightly_than_a_key():
    """A Fish Audio key is sixty-odd characters of opaque token. A NAME is a
    name — and it is the one that is spliced into the brain's system prompt
    (`brain.launch_prompt`: "The user's name is {…}") with nothing around
    it, so it is the one that must not be a paragraph."""
    from jarvis import settings_service, settings_specs
    name = settings_specs.spec("user_name")
    assert name.max_chars <= 64 < settings_specs.spec("fish_api_key").max_chars
    with pytest.raises(settings_service.Refused):
        settings_service.check(name, "x" * 200)
    for ok in ("Tony", "Étienne d'Arcy-Smith"):
        assert settings_service.check(name, ok) == ok
    with pytest.raises(settings_service.Refused):
        settings_service.check(settings_specs.spec("honorific"), "Your Majesty")


def test_a_control_character_is_refused():
    """`splitlines()` catches the ten separators. An ESC or a BEL is neither
    a separator nor printable, and it round-tripped into the system prompt
    and into whatever renders it."""
    from jarvis import settings_service
    for spec in _texts():
        for bad in ("\x1b[2J", "a\x07b", "a\x00b", "a\x7fb"):
            with pytest.raises(settings_service.Refused):
                settings_service.check(spec, bad)


def test_the_route_refuses_rather_than_saving_half(server):
    """`POST /api/settings/preferences` is the door the finding came
    through. It must answer 400 and save nothing."""
    from fastapi.testclient import TestClient
    from jarvis import settings_service
    with TestClient(server.app) as client:
        r = client.post("/api/settings/preferences",
                        json={"user_name": "x" * 100_000, "honorific": "sir"},
                        headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 400, r.text
        assert settings_service.value("user_name") == ""
        r = client.post("/api/settings/preferences",
                        json={"user_name": "Tony", "honorific": "sir"},
                        headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 200 and settings_service.value("user_name") == "Tony"
