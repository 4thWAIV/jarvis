"""The URL that reaches `open location` is DATA, proved against a real
AppleScript interpreter.

`actions.open_browser` escaped `"` and not `\\`, thirty-eight lines below the
helper that does both. That is not a theoretical hole: AppleScript reads
`\\\\` as one literal backslash, so a URL ending `x\\"` closes the string
literal and everything after it is CODE — and `do shell script` is in that
language. `applescript_escape` orders the two replacements correctly
(backslash first, then quote), which is the whole difference.

Nothing here launches a browser. `asyncio.create_subprocess_exec` is replaced
by a recorder, and the *recorded script's own URL literal* is then executed
through `osascript` inside a harness with the same shape as the real one — a
statement whose argument is that literal. A payload that escapes the literal
runs in that harness exactly as it would inside the `tell` block.

The payload writes a marker file. Its absence is the assertion, and
`test_the_payload_really_is_an_attack` runs the SAME payload through the old
escape to prove the marker can be written — without that, "the marker is
absent" would prove nothing at all.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis import actions

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="osascript is macOS only")


def _character_id_chain(text: str) -> str:
    """`text` as an AppleScript expression containing no quote character.

    The breakout leaves us in code context, where a `"` would itself be
    escaped — so a real payload builds its strings out of `character id`,
    which needs none. This is the reviewer's own technique.
    """
    return " & ".join(f"(character id {ord(c)})" for c in text)


def _payload(marker: Path) -> str:
    r"""A URL that, unescaped, runs `touch <marker>`.

    `x\"` ends the literal (the backslash is consumed as an escaped
    backslash, the quote closes the string). The next line is a statement.
    The trailing `--` comments out the template's own closing quote so the
    whole script still compiles.
    """
    return ('https://stark.example/x\\"\n'
            f'do shell script ({_character_id_chain(f"touch {marker}")})\n'
            '--')


def _run_applescript_with(literal: str) -> subprocess.CompletedProcess:
    """Execute a statement whose argument is `literal`, exactly as the real
    script does with `open location`."""
    return subprocess.run(
        ["osascript", "-e", f'set theURL to "{literal}"\nreturn theURL'],
        capture_output=True, text=True)


class _Recorder:
    """Stands in for asyncio.create_subprocess_exec. Records, never launches."""

    def __init__(self):
        self.argv: list[list[str]] = []

    async def __call__(self, *argv, **kwargs):
        self.argv.append(list(argv))
        return self

    async def communicate(self):
        return b"", b""

    @property
    def returncode(self):
        return 0


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", rec)
    return rec


def _url_literal(script: str) -> str:
    """The text `open browser` put between the quotes of `open location`."""
    assert 'open location "' in script, script
    body = script.split('open location "', 1)[1]
    tail = '"\nend tell'
    assert body.endswith(tail), body
    return body[:-len(tail)]


# --- the discriminator discriminates --------------------------------------

def test_the_payload_really_is_an_attack(tmp_path):
    """The old escape — `url.replace('"', '\\\\"')` — and this payload write
    the marker. Without this test the one below could pass on a payload that
    never worked in the first place."""
    marker = tmp_path / "pwned-by-the-old-escape"
    old_escape = _payload(marker).replace('"', '\\"')
    result = _run_applescript_with(old_escape)
    assert result.returncode == 0, result.stderr
    assert marker.exists(), (
        "the payload did not execute even under the vulnerable escape; "
        "it discriminates nothing")


# --- and the shipped code survives it -------------------------------------

@pytest.mark.asyncio
async def test_a_hostile_url_cannot_escape_the_applescript_literal(
        recorder, tmp_path):
    marker = tmp_path / "pwned"
    url = _payload(marker)

    await actions.open_browser(url, "chrome")

    assert recorder.argv, "no osascript call was recorded"
    script = recorder.argv[0][2]
    result = _run_applescript_with(_url_literal(script))

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), (
        f"the URL escaped its literal and ran a shell command; script was:\n"
        f"{script}")
    # Data, and the SAME data: newlines collapse to spaces (a URL has none),
    # nothing else changes.
    assert result.stdout.rstrip("\n") == url.replace("\n", " ")


@pytest.mark.asyncio
async def test_firefox_takes_the_same_route(recorder, tmp_path):
    marker = tmp_path / "pwned-firefox"
    await actions.open_browser(_payload(marker), "firefox")
    script = recorder.argv[0][2]
    assert 'tell application "Firefox"' in script
    assert _run_applescript_with(_url_literal(script)).returncode == 0
    assert not marker.exists()


@pytest.mark.asyncio
async def test_open_browser_uses_the_one_escaping_helper(recorder):
    """Pinned by identity, not by behaviour: a second hand-rolled escape in
    this file is how the first one got here."""
    url = 'https://stark.example/a\\b"c'
    await actions.open_browser(url, "chrome")
    assert actions.applescript_escape(url) in recorder.argv[0][2]


@pytest.mark.asyncio
async def test_reverting_a_terminal_theme_escapes_the_same_way(
        recorder, tmp_path):
    """`_revert_terminal_theme` had the identical hand-rolled escape. The
    profile name comes off the user's own machine rather than off an LLM, so
    this is the lower-reachability half of the same bug — fixed anyway."""
    marker = tmp_path / "pwned-by-a-profile-name"
    name = _payload(marker)
    await actions._revert_terminal_theme(name)
    script = recorder.argv[0][2]
    assert actions.applescript_escape(name) in script
    literal = script.split('settings set "', 1)[1].rsplit('"\nend tell', 1)[0]
    assert _run_applescript_with(literal).returncode == 0
    assert not marker.exists()
