"""The page's shared navigation, run under node (tests/ui_navigation_cases.mts):
one route state, every centre detail opened and closed by its registered
command, a person's control and the brain's request leaving the same state
with the same effects, a person's navigation winning over a slow one, and
reveal bringing the exact presentation into view or saying why not.

Also the static rule that keeps it that way: no module but the route
state's owner writes the route state.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"

EXPECTED = {
    "right tab", "agents tab clears its mark", "closed detail tab refused", "workflow open shows it",
    "unlisted workflow read first, then replaces", "gone workflow refused, nothing opened",
    "reopening a shown-away detail shows it again", "close goes back to Conversation",
    "close while another view is shown keeps that view", "close with nothing open refused",
    "subagent opens in the workflow slot", "thread named from the record",
    "thread read from JARVIS when not in the record", "thread with nothing on record refused",
    "voice diagnostics", "subsystem closes voice diagnostics", "voice input's row opens voice diagnostics",
    "this page's row has no detail", "unlisted subsystem refused", "folder shows the Folders view",
    "gone folder refused", "folder closed", "legacy stores go through the registry", "invalid key refused",
    "person's navigation supersedes a slow open", "aborted open opens nothing",
    "reveal: visible needs nothing", "reveal: runs the declared command", "reveal: scrolls an offscreen target",
    "reveal: a revealer brings a late item", "reveal: a gone item is refused",
    "reveal: a person's navigation cancels it", "reveal: nothing declared, nothing guessed",
    "no declared command without a handler",
    "package and received file: opened by their commands, closed back to Conversation",
}


def test_navigation_cases_pass_as_a_person_and_as_the_brain():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the page's navigation")
    if not (ROOT / "frontend" / "node_modules" / "react").is_dir():
        pytest.skip("the frontend's node_modules are needed (npm ci)")
    out = subprocess.run([node, "--experimental-transform-types", "--no-warnings",
                          "--import", str(ROOT / "tests" / "ui_ts_loader.mjs"),
                          str(ROOT / "tests" / "ui_navigation_cases.mts")],
                         capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-4000:]
    assert set(json.loads(out.stdout)["passed"]) == EXPECTED


# The route state and the detail slots are written only through their
# owners: uiNavigation.ts's handlers and the slot's own show/close/adjust,
# called from the module that created the slot.
ROUTE_WRITES = re.compile(r"\b(?:rightTab|mainView|openDetails|openNetwork|openQueue)\.set\(")
SLOT_WRITES = re.compile(r"\b(\w+Slot)\.(?:show|close|adjust)\(")
SLOT_OWNERS = {"workflowSlot": "workflows.ts", "threadSlot": "conversation.ts",
               "diagnosticsSlot": {"voiceHealth.ts", "subsystems.ts"}, "subsystemSlot": "subsystems.ts",
               "networkSlot": "network.ts", "queueSlot": "queue.ts", "packageSlot": "packages.ts",
               "memberSlot": "bundles.ts", "settingsSlot": "Settings.tsx"}


def _sources():
    return [p for p in SRC.glob("*.ts*") if p.name != "uiContract.ts"]


def test_no_module_writes_the_route_state_directly():
    offenders = [f"{p.name}: {m.group(0)}" for p in _sources() for m in ROUTE_WRITES.finditer(p.read_text())]
    assert offenders == []


def test_only_a_slots_owner_moves_it():
    offenders = []
    for p in _sources():
        for m in SLOT_WRITES.finditer(p.read_text()):
            owners = SLOT_OWNERS.get(m.group(1))
            owners = {owners} if isinstance(owners, str) else owners
            if owners is None or p.name not in owners:
                offenders.append(f"{p.name}: {m.group(0)}")
    assert offenders == []


def test_tab_rows_run_registered_commands():
    # The right panel's, the centre's and the workflow detail's rows bind a
    # command; none selects by hand.
    unbound = [p.name for p in _sources() if re.search(r"<TabRow\b[^>]*\bonSelect=", p.read_text(), re.S)]
    assert unbound == []
