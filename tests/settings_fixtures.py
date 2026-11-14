"""The test suite's one way to set a setting: held for this process
(settings_service.hold), never saved, and let go after every test
(conftest._settings_start_from_the_suite_s_own). A setting is never set
through the environment (env_names.py)."""
from __future__ import annotations

from typing import Any

from jarvis import settings_service, settings_specs

_PARSED = ("bool", "int", "float", "paths", "origins")


def hold_setting(key: str, value: Any) -> None:
    """Put `value` in force for this test. Text for a switch, a number or a
    list is read as `.env` held it ("0", "1", "a:b")."""
    spec = settings_specs.spec(key)
    if isinstance(value, str) and spec.kind in _PARSED:
        value = settings_service.parse_text(spec, value)
    settings_service.hold(key, value)


def default_setting(key: str) -> None:
    """The setting as shipped, whatever the suite holds for it."""
    settings_service.hold(key, settings_specs.spec(key).default)
