"""The right panel's own navigation, run under node (tests/ui_right_cases.mts):
the Agents list's Active/All switch, its paging and bringing one agent's row
into view, each a person's control and the brain's request leaving the same
list with the same effects.

Also the static rule for the panel's controls: every navigation control in
the right panel's files carries the command it runs, and no file there
writes a detail slot's route store directly.
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
    "filter shows all, once", "filter goes back to the top", "page on and back", "page stops at the top",
    "page stops at the bottom", "reveal pages to the row", "reveal of a row showing changes nothing",
    "reveal never changes the switch", "paging needs the list on the page", "invalid direction refused",
    "leaving the page goes back to the top", "every agents command has a handler",
}

RIGHT_PANEL = ["FoldersPanel.tsx", "AgentsControl.tsx", "QueuePanel.tsx", "WorkflowsView.tsx",
               "NetworkPanel.tsx", "NetworkTile.tsx", "SubsystemList.tsx", "VoiceSummary.tsx"]


def test_agents_list_cases_pass_as_a_person_and_as_the_brain():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the page's navigation")
    if not (ROOT / "frontend" / "node_modules" / "react").is_dir():
        pytest.skip("the frontend's node_modules are needed (npm ci)")
    out = subprocess.run([node, "--experimental-transform-types", "--no-warnings",
                          "--import", str(ROOT / "tests" / "ui_ts_loader.mjs"),
                          str(ROOT / "tests" / "ui_right_cases.mts")],
                         capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-4000:]
    assert set(json.loads(out.stdout)["passed"]) == EXPECTED


# A detail's route store written as a store is a person's navigation that
# skips the command's marks; the right panel's controls bind the command.
STORE_WRITES = re.compile(r"\b(?:openWorkflow|openThread|openDiagnostics|openSubsystem|selectedFolder)\.set\(")
# Calls that navigate without saying which command they run.
UNMARKED = re.compile(r"\b(?:openItem|followLink|openFolder|openSubsystemRow|byHand)\(")


def test_right_panel_navigation_runs_marked_commands():
    offenders = []
    for name in RIGHT_PANEL:
        text = (SRC / name).read_text()
        offenders += [f"{name}: {m.group(0)}" for m in STORE_WRITES.finditer(text)]
        offenders += [f"{name}: {m.group(0)}" for m in UNMARKED.finditer(text)]
    assert offenders == []
