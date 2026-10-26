"""The page's audio conversion (frontend/src/pcm.ts) on known signals: the
resampler keeps the first sample and the onset, gives the same output however
the input is split into blocks, passes speech frequencies and removes what
would alias; PCM16 is clipped, signed and little-endian; frames are exact.

Runs the real module under node, bundled with the esbuild Vite ships; skips
without node or the frontend's node_modules.
"""

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ESBUILD = ROOT / "frontend" / "node_modules" / ".bin" / "esbuild"


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    if not shutil.which("node") or not ESBUILD.exists():
        pytest.skip("needs node and frontend/node_modules")
    out = tmp_path_factory.mktemp("pcm") / "cases.mjs"
    subprocess.run([str(ESBUILD), str(ROOT / "tests" / "js" / "pcm_cases.ts"), "--bundle",
                    "--platform=node", "--format=esm", f"--outfile={out}", "--log-level=error"],
                   check=True)
    return json.loads(subprocess.run(["node", str(out)], check=True, capture_output=True,
                                     text=True).stdout)


@pytest.mark.parametrize("rate", ["44100", "48000"])
def test_resampling_keeps_the_first_sample_and_the_length(measured, rate):
    m = measured[rate]
    expected = m["inLength"] * 16000 / int(rate)
    # Up to the filter's look-ahead (32 input samples) is held back until
    # more input arrives, and comes out on a flush.
    assert all(expected - 12 <= n <= expected for n in m["lengths"]), m["lengths"]
    assert abs(m["flushedLength"] - expected) <= 1
    # The click at input sample 0 is in the first output samples, not lost.
    assert 0 <= m["firstNonZero"] <= 12


@pytest.mark.parametrize("rate", ["44100", "48000"])
def test_resampling_is_the_same_whatever_the_block_size(measured, rate):
    assert measured[rate]["blockMaxDiff"] < 1e-6


@pytest.mark.parametrize("rate", ["44100", "48000"])
def test_speech_frequencies_pass_and_aliasing_ones_do_not(measured, rate):
    assert 0.95 < measured[rate]["passband1k"] < 1.05
    assert measured[rate]["stopband12k"] < 0.01


@pytest.mark.parametrize("rate", ["44100", "48000"])
def test_a_step_from_silence_starts_at_once_and_settles_at_its_level(measured, rate):
    m = measured[rate]
    assert abs(m["stepSettled"] - 0.5) < 1e-3
    assert any(abs(v) > 0.05 for v in m["stepFirst"]), m["stepFirst"]


def test_sixteen_kilohertz_input_passes_through_untouched(measured):
    assert measured["passThrough"] is True


def test_pcm16_is_clipped_signed_and_little_endian(measured):
    raw = bytes(measured["pcm16"])
    assert struct.unpack("<6h", raw) == (32767, -32768, 16384, -32768, 0, 32767)


def test_channels_are_averaged(measured):
    assert measured["downmix"] == [0.5, 0]


def test_frames_are_exactly_100ms_and_nothing_is_lost_between_them(measured):
    f = measured["framer"]
    assert f["frameSamples"] == 1600
    assert f["sizes"] == [1600, 1600, 1600]
    assert f["rest"] == 200
    assert f["intact"] is True
