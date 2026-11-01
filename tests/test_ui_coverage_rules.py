"""The rules that keep new navigation inside the one registry, read with
TypeScript's own parser and type checker (tests/ui_coverage_rules.mts), not
by text matching: every control that reacts to a person runs a registered
command or carries a reviewed classification, route state is written only
by its owner (a cast does not get round it), and every target and part a
component registers is one the contract declares, keyed by its record,
and the words a component shows come from the catalog (frontend/src/locale.ts),
not written in English.

The negative fixtures (tests/fixtures/ui_coverage/rejected) are what CI must
reject; the accepted one is a view written the way the rules ask. The page
itself must follow them, except for the files still waiting on their
coverage worker (PENDING, a ratchet: it may only shrink).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ui_coverage"

# (file, rule) -> how many violations it still has, until its owner's
# coverage lands; each count must be exact so a fix shrinks it here too.
# The right panel's files are coverage-right's, the centre views'
# coverage-main's. `label-unlocalized` counts are the localization
# workers' (execution.md, LOCALIZATION OWNERSHIP): each reaches zero.
PENDING = {
    ("AgentsControl.tsx", "control-unclassified"): 1,
    ("CaptureBox.tsx", "control-unclassified"): 4,
    ("ConversationView.tsx", "control-unclassified"): 3,
    ("FolderDetail.tsx", "control-unclassified"): 14,
    ("FoldersView.tsx", "control-unclassified"): 7,
    ("MessageComposer.tsx", "control-unclassified"): 10,
    ("QueueFiles.tsx", "control-unclassified"): 3,
    ("QueueItemView.tsx", "control-unclassified"): 19,
    ("QueuePanel.tsx", "control-unclassified"): 2,
    ("VoiceDiagnosticsView.tsx", "control-unclassified"): 3,
    ("VoiceVisuals.tsx", "control-unclassified"): 1,
    ("WorkflowDetail.tsx", "control-unclassified"): 5,
    ("WorkflowsView.tsx", "control-unclassified"): 1,
}


def _violations(*dirs: Path) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to read the page's TypeScript")
    if not (ROOT / "frontend" / "node_modules" / "typescript").is_dir():
        pytest.skip("the frontend's node_modules are needed (npm ci)")
    out = subprocess.run([node, "--experimental-transform-types", "--no-warnings",
                          "--import", str(ROOT / "tests" / "ui_ts_loader.mjs"),
                          str(ROOT / "tests" / "ui_coverage_rules.mts"), *map(str, dirs)],
                         capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-4000:]
    return json.loads(out.stdout)["violations"]


def _found(violations):
    return sorted((Path(v["file"]).name, v["line"], v["rule"]) for v in violations)


def test_the_negative_fixtures_are_rejected_rule_by_rule():
    assert _found(_violations(FIXTURES / "rejected")) == [
        # A target the contract does not present there, a key from a row's
        # position, a part it does not declare and one named at run time.
        ("UndeclaredTargets.tsx", 8, "undeclared-target"),
        ("UndeclaredTargets.tsx", 14, "index-key"),
        ("UndeclaredTargets.tsx", 18, "undeclared-part"),
        ("UndeclaredTargets.tsx", 19, "undeclared-part"),
        # Words written in English: a title property, JSX text, a labelled
        # attribute, a branch of a conditional, a template and a fallback.
        ("UnlocalizedLabels.tsx", 9, "label-unlocalized"),
        ("UnlocalizedLabels.tsx", 12, "label-unlocalized"),
        ("UnlocalizedLabels.tsx", 13, "label-unlocalized"),
        ("UnlocalizedLabels.tsx", 14, "label-unlocalized"),
        ("UnlocalizedLabels.tsx", 15, "label-unlocalized"),
        ("UnlocalizedLabels.tsx", 16, "label-unlocalized"),
        # A tab of its own, a keyboard handler, a conditional control, and a
        # classification nobody reviewed.
        ("UnregisteredControls.tsx", 13, "control-unclassified"),
        ("UnregisteredControls.tsx", 15, "control-unclassified"),
        ("UnregisteredControls.tsx", 17, "control-unclassified"),
        ("UnregisteredControls.tsx", 19, "unknown-classification"),
        # Settings' and the menu's route state written from outside, each
        # through a cast.
        ("routeStateWriter.ts", 10, "route-state-write"),
        ("routeStateWriter.ts", 12, "route-state-write"),
    ]


def test_a_view_written_the_way_the_rules_ask_passes():
    assert _violations(FIXTURES / "accepted") == []


def test_the_page_follows_the_rules_but_for_the_files_still_waiting():
    counts: dict[tuple[str, str], int] = {}
    for v in _violations(ROOT / "frontend" / "src"):
        key = (Path(v["file"]).name, v["rule"])
        counts[key] = counts.get(key, 0) + 1
    new = {k: n for k, n in counts.items() if n > PENDING.get(k, 0)}
    assert new == {}, "new violations (register the command or classify the control): " + json.dumps(
        [v for v in _violations(ROOT / "frontend" / "src") if (Path(v["file"]).name, v["rule"]) in new])
    fixed = {k: (PENDING[k], counts.get(k, 0)) for k in PENDING if counts.get(k, 0) < PENDING[k]}
    assert fixed == {}, f"lower these PENDING counts to what is left: {fixed}"
