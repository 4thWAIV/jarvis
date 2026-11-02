"""The UI navigation contract (jarvis/ui_contract/): one schema, read by both
sides. The page's generated copy is current; Python and the page's own
validator (frontend/src/uiValidate.ts, run under node) agree on every shared
case; inconsistent declarations are refused as a whole; and the brain's
ui_* tools are built from the same declarations.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import ui_contract

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "fixtures" / "ui_contract_cases.json").read_text())


def test_the_pages_copy_is_current():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_ui_contract.py"), "--check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_the_packaged_contract_loads_and_is_versioned():
    c = ui_contract.load()
    assert c.version == 1
    assert {"right.tab.select", "main.tab.select", "network.open", "network.close"} <= set(c.commands)
    assert {v["region"] for v in c.views.values()} == set(c.protocol["regions"])


def _python_verdicts() -> dict:
    return {
        "args": [ui_contract.args_problem(c["command"], c["args"]) for c in CASES["args"]],
        "targets": [ui_contract.target_problem(c["ref"]) for c in CASES["targets"]],
        "operations": [ui_contract.operation_problem(c["op"]) for c in CASES["operations"]],
    }


@pytest.mark.parametrize("group", ["args", "targets", "operations"])
def test_python_validates_every_shared_case(group):
    got = _python_verdicts()[group]
    for case, problem in zip(CASES[group], got):
        assert (problem is None) == case["ok"], (case, problem)


def test_the_page_validates_every_shared_case_the_same_way():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the page's validator")
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings",
                          str(ROOT / "tests" / "ui_validate_cases.mts"),
                          str(ROOT / "tests" / "fixtures" / "ui_contract_cases.json")],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    page = json.loads(out.stdout)
    for group in ("args", "targets", "operations"):
        for case, problem in zip(CASES[group], page[group]):
            assert (problem is None) == case["ok"], (group, case, problem)


def _declarations():
    c = ui_contract.load()
    protocol = copy.deepcopy(c.protocol)
    families = [json.loads((ui_contract.HERE / rel).read_text()) for rel in protocol["families"]]
    return protocol, families


def _refused(protocol, families, words):
    with pytest.raises(ui_contract.ContractError) as err:
        ui_contract.build(protocol, families)
    assert words in str(err.value)


def test_a_disconnected_junction_is_refused():
    protocol, families = _declarations()
    protocol["pipeline"]["junctions"].append({"id": "rail.island", "region": "main", "label": "x"})
    _refused(protocol, families, "junction rail.island is not connected to the orb")


def test_a_duplicate_presentation_or_declaration_is_refused():
    protocol, families = _declarations()
    shell = next(f for f in families if f["family"] == "shell")
    shell["views"].append(dict(shell["views"][0]))
    _refused(protocol, families, "view left.fixed is declared twice")
    protocol, families = _declarations()
    net = next(f for f in families if f["family"] == "network")
    node = net["kinds"][0]
    node["presentations"].append(dict(node["presentations"][0]))
    _refused(protocol, families, "target kind node is presented twice in right.network")


def test_a_reveal_through_an_undeclared_command_is_refused():
    protocol, families = _declarations()
    net = next(f for f in families if f["family"] == "network")
    net["kinds"][0]["presentations"][1]["reveal"]["command"] = "network.teleport"
    _refused(protocol, families, "revealed by unknown command network.teleport")


def test_a_part_a_kind_does_not_declare_is_refused():
    protocol, families = _declarations()
    net = next(f for f in families if f["family"] == "network")
    net["kinds"][0]["presentations"][0]["parts"].append("temperature")
    _refused(protocol, families, "presents parts it does not declare")


def test_an_undeclared_side_effect_is_refused():
    protocol, families = _declarations()
    shell = next(f for f in families if f["family"] == "shell")
    shell["commands"][0]["effects"].append({"effect": "queue.secretly-sent"})
    _refused(protocol, families, "declares unknown effect queue.secretly-sent")


def test_the_tools_come_from_the_declarations():
    specs = {s["name"]: s for s in ui_contract.tool_specs()}
    assert set(specs) == {"ui_map", "ui_navigate", "ui_indicate", "ui_journey", "ui_clear"}
    c = ui_contract.load()
    assert set(specs["ui_navigate"]["inputSchema"]["properties"]["command"]["enum"]) == set(c.commands)
    target = specs["ui_indicate"]["inputSchema"]["properties"]["target"]
    assert set(target["properties"]["kind"]["enum"]) == set(c.kinds)
    assert target["additionalProperties"] is False
    # A journey's steps are ui_navigate's and ui_indicate's own arguments,
    # less the request's own (the page and the revision it was planned at).
    request_level = {"client", "expectedNavigationRevision"}
    steps = specs["ui_journey"]["inputSchema"]["properties"]["steps"]
    assert steps["maxItems"] == c.protocol["limits"]["journeyMaxSteps"]
    navigate, indicate = steps["items"]["oneOf"]
    assert navigate["properties"] == {k: v for k, v in specs["ui_navigate"]["inputSchema"]["properties"].items()
                                      if k not in request_level}
    assert indicate["properties"] == {k: v for k, v in specs["ui_indicate"]["inputSchema"]["properties"].items()
                                      if k not in request_level}
    # No way to hand the page code, a selector, a URL or coordinates.
    def names(schema):
        for name, sub in schema.get("properties", {}).items():
            yield name
            yield from names(sub)
        items = schema.get("items")
        for sub in [items, *items.get("oneOf", [])] if items else []:
            yield from names(sub)
    for spec in specs.values():
        assert not set(names(spec["inputSchema"])) & {"selector", "script", "code", "url", "x", "y", "rect"}
