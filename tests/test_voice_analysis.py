"""The Diagnostics view's arithmetic (frontend/src/voiceAnalysis.ts) on a
small record: repeated messages grouped and the page's own lines merged
without doubling; notifications by source; the page just before a drop
against the rest; drops per hour with the page quiet and not.

Runs the real module under node, bundled with the esbuild Vite ships; skips
without node or the frontend's node_modules.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ESBUILD = ROOT / "frontend" / "node_modules" / ".bin" / "esbuild"


@pytest.fixture(scope="module")
def got(tmp_path_factory):
    if not shutil.which("node") or not ESBUILD.exists():
        pytest.skip("needs node and frontend/node_modules")
    out = tmp_path_factory.mktemp("analysis") / "cases.mjs"
    subprocess.run([str(ESBUILD), str(ROOT / "tests" / "js" / "voice_analysis_cases.ts"), "--bundle",
                    "--platform=node", "--format=esm", f"--outfile={out}", "--log-level=error"],
                   check=True)
    return json.loads(subprocess.run(["node", str(out)], check=True, capture_output=True,
                                     text=True).stdout)


def test_messages_are_grouped_within_a_minute_and_the_page_s_own_are_not_doubled(got):
    groups = [(g["source"], g["text"], g["count"]) for g in got["groups"]]
    assert groups == [("Status", "only here", 1), ("Input", "fell behind", 1),
                      ("Input", "fell behind", 2), ("Status", "restarting", 1)]


def test_notifications_are_totalled_by_source(got):
    assert got["sources"] == [["Input", 220], ["Status", 150]]


def test_the_half_minute_before_a_drop_is_set_against_the_rest(got):
    b = got["before"]
    assert b["drops"] == 1
    assert (b["before"]["notices"], b["before"]["longTaskMs"], b["before"]["seconds"]) == (9, 300, 29)
    assert (b["normal"]["seconds"], round(b["normal"]["notices"], 2)) == (101, round((100 + 9) / 101, 2))


def test_drops_per_hour_are_split_by_the_quiet_page(got):
    q = got["quiet"]
    assert q["quiet"] == {"hours": 1, "drops": 2}
    assert q["usual"]["drops"] == 1
