"""No one ever leaves the dashboard: every setting is a Settings control,
and the environment carries only what JARVIS sets for itself, what the
platform sets, and the test suite's harness (jarvis/env_names.py).

This fails when:
- code under jarvis/ or scripts/ reads or sets the environment anywhere but
  env_names.py (`os.environ`, `os.getenv`, `os.putenv`, or either imported
  from os), or frontend/vite.config.ts reads a name not listed;
- a name read, published or given to a child is not listed, or is a SETTING;
- a setting has no Settings target in the UI contract, no brain tool to read
  and change it, or no words in both catalogs;
- CLAUDE.md's "Settings" and "Not settings" lists differ from the registry.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from jarvis import env_names, jarvis_mcp, settings_service, settings_specs, ui_contract

ROOT = Path(__file__).resolve().parent.parent
READERS = {"read", "is_set", "publish", "withdraw"}


def _sources() -> list[Path]:
    files = sorted((ROOT / "jarvis").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    return [p for p in files if p.name != "env_names.py" and "__pycache__" not in p.parts]


def _constants(tree: ast.Module) -> dict[str, str]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _environment_access(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "os" and node.attr in ("environ", "getenv", "putenv",
                                                             "unsetenv", "environb"):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno} os.{node.attr}")
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in ("environ", "getenv", "putenv", "environb"):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno} from os import "
                                 f"{alias.name}")
    return found


def test_nothing_reads_the_environment_but_env_names():
    found = [hit for path in _sources() for hit in _environment_access(path)]
    assert not found, "read the environment through env_names:\n" + "\n".join(found)


def _names_used(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(), str(path))
    constants = _constants(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "env_names"):
            continue
        where = f"{path.relative_to(ROOT)}:{node.lineno}"
        if node.func.attr in READERS and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((arg.value, where))
            elif isinstance(arg, ast.Name) and arg.id in constants:
                out.append((constants[arg.id], where))
            else:
                out.append((f"<not a literal: {ast.unparse(arg)}>", where))
        if node.func.attr == "child":
            out += [(kw.arg, where) for kw in node.keywords
                    if kw.arg and kw.arg not in ("drop", "drop_prefixes")]
        if node.func.attr == "with_prefix" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                out.append((arg.value + "X", where))
    return out


def test_every_name_used_is_listed_and_none_is_a_setting():
    bad = []
    for path in _sources():
        for name, where in _names_used(path):
            kind = env_names.kind_of(name)
            if kind is None or kind == env_names.SETTING:
                bad.append(f"{where} {name} ({kind or 'not listed'})")
    assert not bad, "\n".join(bad)


def test_the_vite_config_reads_only_listed_wiring():
    text = (ROOT / "frontend" / "vite.config.ts").read_text()
    names = set(re.findall(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)", text))
    assert names, "the Vite config reads its proxy target from the environment"
    assert {n: env_names.kind_of(n) for n in names} == {n: env_names.WIRING for n in names}
    assert "process.env[" not in text


def test_a_setting_name_cannot_be_read_from_the_environment():
    for name in env_names.setting_names():
        with pytest.raises(env_names.NotAnEnvironmentName):
            env_names.read(name)
    with pytest.raises(env_names.NotAnEnvironmentName):
        env_names.read("JARVIS_SOMETHING_NEW")


def test_a_child_is_never_given_a_setting():
    with pytest.raises(env_names.NotAnEnvironmentName):
        env_names.child(JARVIS_SKIP_PERMISSIONS="0")


# --- every setting is a Settings control with a brain tool and words -------------

def _contract() -> dict:
    return ui_contract.load().merged()


def _settings_items() -> set[str]:
    for kind in _contract()["kinds"]:
        if kind["kind"] == "settingsItem":
            return set(kind["keys"])
    raise AssertionError("no settingsItem kind")


def _kinds() -> set[str]:
    return {k["kind"] for k in _contract()["kinds"]}


def _catalog(lang: str) -> set[str]:
    keys = set()
    for part in ("left", "main", "right", "shell"):
        text = (ROOT / "frontend" / "src" / "messages" / f"{part}.{lang}.ts").read_text()
        keys |= set(re.findall(r'^\s*"([A-Za-z0-9_.]+)":', text, re.M))
    return keys


def test_every_setting_is_a_settings_target():
    items = _settings_items()
    missing = [s.key for s in settings_specs.SPECS if s.target not in items]
    assert not missing, missing
    for name, (target, _tools) in settings_specs.ELSEWHERE.items():
        assert target in items | _kinds(), (name, target)


def test_every_setting_has_a_brain_tool_to_read_and_one_to_change_it():
    from jarvis import server
    specs = {t["name"]: t for t in jarvis_mcp.TOOL_SPECS}
    for s in settings_specs.SPECS:
        read, act = s.tools
        assert read in specs and read in server.TOOL_HANDLERS, (s.key, read)
        assert act in specs and act in server.TOOL_HANDLERS and act in server.ACTING_TOOLS, \
            (s.key, act)
    settable = set(specs["set_jarvis_setting"]["inputSchema"]["properties"]["key"]["enum"])
    for s in settings_specs.SPECS:
        if s.tools[1] == "set_jarvis_setting":
            # His click alone (the brain's program, the address, the extra
            # origins, the Fish key): read by the brain, never changed.
            assert (s.key in settable) == (not s.page_only and s.kind != "secret"), s.key
    for _target, tools in settings_specs.ELSEWHERE.values():
        for tool in tools:
            assert tool in specs and tool in server.TOOL_HANDLERS, tool


def test_every_setting_drawn_from_the_list_has_words_in_both_catalogs():
    for lang in ("en", "es"):
        keys = _catalog(lang)
        missing = []
        for s in settings_specs.SPECS:
            if s.control == "generic":
                missing += [k for k in (f"left.field.{s.key}", f"left.field.{s.key}.help")
                            if k not in keys]
            if s.kind == "choice" and s.control == "generic":
                missing += [f"left.fields.choice.{c}" for c in s.choice_values()
                            if f"left.fields.choice.{c}" not in keys]
        for area in settings_specs.AREAS:
            if f"left.settings.section.{area}" not in keys:
                missing.append(f"left.settings.section.{area}")
        assert not missing, (lang, missing)


def test_every_refusal_is_worded_in_both_catalogs():
    source = (ROOT / "jarvis" / "settings_service.py").read_text()
    codes = set(re.findall(r'Refused\("([A-Za-z]+)"', source)) | {"pageOnly", "unknown", "failed"}
    # Said by their own controls (the Voice section's, the brain's area), not the list's.
    codes -= {"area", "voiceIsKey", "keyIsVoice"}
    for lang in ("en", "es"):
        keys = _catalog(lang)
        assert not [c for c in codes if f"left.fields.refused.{c}" not in keys], lang


def test_every_area_is_a_section_of_settings():
    sections = None
    for command in _contract()["commands"]:
        if command["id"] == "settings.section":
            sections = command["args"]["section"]["values"]
    assert set(settings_specs.AREAS) <= set(sections)


def test_every_default_is_a_value_the_setting_takes():
    for s in settings_specs.SPECS:
        if s.kind == "secret":
            continue
        assert settings_service.check(s, s.default) == s.default, s.key


# --- CLAUDE.md says the same ------------------------------------------------------

def _claude_section(title: str) -> str:
    text = (ROOT / "CLAUDE.md").read_text()
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## )", text, re.M | re.S)
    assert match, f"CLAUDE.md has no '## {title}' section"
    return match.group(1)


def test_claude_md_lists_every_setting_and_every_name_that_is_not_one():
    settings = _claude_section("Settings")
    missing = [s.key for s in settings_specs.SPECS if f"`{s.key}`" not in settings]
    assert not missing, f"CLAUDE.md's Settings list lacks {missing}"
    not_settings = _claude_section("Not settings")
    listed = {**env_names.WIRING_NAMES, **env_names.PLATFORM_NAMES, **env_names.TEST_NAMES}
    missing = [n for n in listed if f"`{n}`" not in not_settings]
    missing += [f"{p}*" for p in env_names.PLATFORM_PREFIXES if f"`{p}*`" not in not_settings]
    assert not missing, f"CLAUDE.md's Not settings list lacks {missing}"
    named = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", not_settings))
    stray = [n for n in named if env_names.kind_of(n) in (None, env_names.SETTING)
             and not n.endswith("_")]
    assert not stray, f"CLAUDE.md's Not settings list names {stray}, which env_names does not"


def test_claude_md_has_the_rule():
    rule = " ".join(_claude_section("Settings only").split())
    assert "no one ever leaves the dashboard for any reason" in rule.lower()
    assert "tests/test_env_registry.py" in rule


def test_the_registry_says_what_each_name_is_for():
    for table in (env_names.WIRING_NAMES, env_names.PLATFORM_NAMES, env_names.TEST_NAMES,
                  env_names.PLATFORM_PREFIXES):
        assert all(isinstance(v, str) and v for v in table.values())
    assert json.dumps(sorted(env_names.setting_names()))
