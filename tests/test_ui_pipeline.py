"""The pipeline and white attention, by their rules rather than their
pixels (the page test, test_voice_page_pipeline.py, checks the pixels):

  - one schedule: the contract's timing table drives the drawing, the orb's
    motion and the acknowledgement alike (frontend/src/uiSignal.ts, run
    under node with the orb's ownership in tests/ui_signal_cases.mts);
  - one scheduler and one lifecycle: only effectLife.ts asks for animation
    frames among the effects drawn from the orb;
  - a channel of its own: attention never touches a message's Transfer,
    its endpoints, receipts or tab lights;
  - one owner of the orb: only orbSource.ts makes the main orb transmit;
  - the corridor the CSS keeps clear is the one the pipeline runs in.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
EFFECTS = ["RayLane.tsx", "uiAttention.ts", "UiPipelineLayer.tsx", "uiPipeline.ts", "orbSource.ts",
           "effectLife.ts", "uiSignal.ts", "transferEndpoints.ts", "rayGeometry.ts"]


def _code(name: str) -> str:
    """A source file without its comments."""
    text = (SRC / name).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_the_schedule_and_the_orb_s_ownership_hold_under_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the page's schedule")
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings",
                          str(ROOT / "tests" / "ui_signal_cases.mts")],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    passed = json.loads(out.stdout)["passed"]
    assert len(passed) >= 17, passed


def test_only_the_shared_scheduler_asks_for_animation_frames():
    for name in EFFECTS:
        if name == "effectLife.ts":
            continue
        assert "requestAnimationFrame" not in _code(name), name
    assert "requestAnimationFrame" in _code("effectLife.ts")


def test_attention_is_its_own_channel_never_a_message_transfer():
    for name in ("uiAttention.ts", "UiPipelineLayer.tsx", "uiSignal.ts"):
        code = _code(name)
        for business in ("./transfers", "./agents", "agentReceipt", "agentsTabLit", ".reached(",
                         "transferTransmit", "transferReceive", "takeRayColour"):
            assert business not in code, (name, business)


def test_only_orb_source_makes_the_main_orb_transmit():
    for path in SRC.glob("*.ts*"):
        if path.name == "orbSource.ts":
            continue
        code = _code(path.name)
        assert not re.search(r"mainOrb\.current\??\.(transmit|receive|endTransmission)", code), path.name


def test_timing_is_read_from_the_contract_not_written_again():
    for name in ("uiAttention.ts", "UiPipelineLayer.tsx", "orbSource.ts"):
        code = _code(name)
        for literal in ("220", "1400", "1620", "4400", "5000", "170"):
            assert not re.search(rf"\b{literal}\b", code), (name, literal)


def test_the_css_corridor_is_the_pipeline_s():
    pipe = _code("uiPipeline.ts")
    margin = int(re.search(r"INNER_MARGIN_PX = (\d+)", pipe).group(1))
    clear = int(re.search(r"CLEAR_PX = (\d+)", pipe).group(1))
    css = (SRC / "style.css").read_text()
    corridor = int(re.search(r"--ui-corridor:\s*(\d+)px", css).group(1))
    assert corridor == margin + clear


def test_no_effect_writes_html():
    for name in EFFECTS:
        code = _code(name)
        assert "innerHTML" not in code and "insertAdjacentHTML" not in code, name
