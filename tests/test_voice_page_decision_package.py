"""The decision package command's whole check, on a small deck, in a real
isolated JARVIS (jarvis/decision_check.py): its own server process on a free
port above 19000, the voice page built by vite, a headless Chromium on its
real package frame at both centre sizes, voting checked, and the story
taken as a first-time user (waiting at each question, answered by click and
by voice), stopped from the page and the brain's tool, and resumed. It then delivers to a stand-in JARVIS on loopback,
never the live one, and the stand-in sees exactly one delivery to its package inbox, with the
token, of the bytes of the zip that was checked."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from jarvis import data_paths, decision_package as dp
from tests import dashboard_page
from tests.loopback_server import LoopbackThreadingHTTPServer
from tests.test_decision_package import small_deck, write

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


class _StandIn:
    """POST /api/packages/inbox as JARVIS answers it, recorded: the zip's
    bytes in, the package and its inbox row out (package_inbox_api.py)."""

    def __init__(self):
        self.posts: list[tuple[str, str, bytes]] = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):                     # noqa: N802 (stdlib contract)
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stand_in.posts.append((self.path, self.headers.get("Authorization", ""), body))
                answer = json.dumps({"package": {"id": "team-lunch-local1"}, "arrived": True,
                                     "inbox": {"id": "pkg-0123456789ab", "title": "Team lunch"}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

        self.server = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def stand_in():
    s = _StandIn()
    yield s
    s.server.shutdown()


def test_the_command_checks_a_deck_in_a_real_jarvis_then_delivers(tmp_path, stand_in, capsys):
    reason = dashboard_page.why_unavailable()
    if reason:
        pytest.skip(reason)
    content = write(tmp_path / "in", small_deck())
    out = tmp_path / "out"
    code = dp.main([str(content), "--out", str(out), "--deliver-to", stand_in.url])
    said = capsys.readouterr().out
    if "Executable doesn't exist" in said:          # pragma: no cover
        pytest.skip("no Playwright Chromium")
    assert code == 0, said

    for size in ("809x589", "662x489"):
        for check in ("the package frame is", "the deck's own styles are in force", "slides fit",
                      "every vote control is visible"):
            assert any(f"ok: {size}: " in line and check in line
                       for line in said.splitlines()), (size, check, said)
        shots = sorted(p.name for p in (out / "check" / size).glob("*.png"))
        assert shots == ["01-s1.png", "02-d-where.png", "03-d-when.png", "04-answers.png"]
    for check in ("first time: the story waits at where", "first time: the story waits at when",
                  "first time: every question answered, in order (where, when)",
                  "stop by the page during narration: stopped within 1s",
                  "stop by the page while waiting: stopped within 1s",
                  "stop by the brain's tool during narration: stopped within 1s",
                  "stop by the brain's tool while waiting: stopped within 1s",
                  "stop again: the brain is told it is already stopped",
                  "resume: picked up at slide 3"):
        assert any(f"ok: {check}" in line for line in said.splitlines()), (check, said)
    assert list((out / "check" / "flow").glob("*waiting-where.png"))
    assert "list_packages found it by its title's words" in said

    [(path, authorization, body)] = stand_in.posts
    assert path.partition("?")[0] == "/api/packages/inbox"
    assert authorization == f"Bearer {data_paths.ensure_tool_token()}"
    assert body == (out / "lunch-decisions-1.0.0.zip").read_bytes()
    from jarvis import decision_stamp
    assert decision_stamp.problem(out / "lunch-decisions-1.0.0.zip",
                                  data_paths.ensure_tool_token()) is None, "stamped for it"
    assert data_paths.ensure_tool_token() not in said
    assert "Arrived in Tim's package inbox as pkg-0123456789ab" in said
    assert "Nothing is shown until he asks for it." in said


def test_the_command_checks_a_presentation_and_its_one_vote(tmp_path, stand_in, capsys):
    """A presentation asks nothing of its own, so it ends with the standard
    vote (Got it, or I have questions), checked as any deck's vote is."""
    from jarvis import make_package as mp
    reason = dashboard_page.why_unavailable()
    if reason:
        pytest.skip(reason)
    content = write(tmp_path / "in", json.loads(json.dumps(mp.PRESENTATION_EXAMPLE)))
    out = tmp_path / "out"
    code = mp.main([str(content), "--out", str(out), "--deliver-to", stand_in.url])
    said = capsys.readouterr().out
    if "Executable doesn't exist" in said:          # pragma: no cover
        pytest.skip("no Playwright Chromium")
    assert code == 0, said
    lines = said.splitlines()
    for size in ("809x589", "662x489"):
        for check in ("opened it by its title", "the package frame is",
                      "the deck's own styles are in force", "slides fit"):
            assert any(f"ok: {size}: " in line and check in line for line in lines), \
                (size, check, said)
        assert any(f"ok: {size}: " in line and "every vote control is visible" in line
                   for line in lines), (size, said)
        shots = sorted(p.name for p in (out / "check" / size).glob("*.png"))
        assert shots == ["01-s1.png", "02-s2.png", "03-d-understood.png", "04-answers.png"]
    for check in ("the story went on to its end", "every beat told pointed",
                  "the story waits at understood", "every question answered, in order (understood)",
                  "stop by the page while waiting: stopped within 1s", "resume: answered understood first"):
        assert any(line.startswith("  ok: ") and check in line for line in lines), (check, said)
    [(path, _, body)] = stand_in.posts
    assert path.partition("?")[0] == "/api/packages/inbox"
    assert body == (out / "team-lunch-moved-1.0.0.zip").read_bytes()


def test_the_check_fails_a_deck_whose_styles_do_not_load(tmp_path):
    """A deck whose deck.css never reaches the frame is the browser's default
    black text, every slide stacked: the check fails it before any slide is
    measured, so it is never delivered."""
    import zipfile
    from jarvis import decision_check, make_package as mp
    reason = dashboard_page.why_unavailable()
    if reason:
        pytest.skip(reason)
    made = mp.make_deck(write(tmp_path / "in", small_deck()), tmp_path / "out")
    bare = tmp_path / "bare.zip"
    with zipfile.ZipFile(made.archive) as src, zipfile.ZipFile(bare, "w") as dst:
        for item in src.infolist():
            if item.filename.rsplit("/", 1)[-1] != "deck.css":
                body = src.read(item)
                if item.filename.rsplit("/", 1)[-1] == "package.json":
                    manifest = json.loads(body)
                    # Keep the declared member list valid so this test reaches
                    # the browser's missing-styles check rather than ingestion.
                    manifest["understanding"].pop("deck.css")
                    body = json.dumps(manifest).encode()
                dst.writestr(item, body)
    result = decision_check.check(bare, made.deck, tmp_path / "check")
    if any("Executable doesn't exist" in f for f in result.failures):   # pragma: no cover
        pytest.skip("no Playwright Chromium")
    assert not result.ok
    assert any("809x589: the deck is not styled: deck.css " in f
               for f in result.failures), result.failures
    assert any("its canvas is not styled" in f for f in result.failures), result.failures
    assert not any("slides fit" in line or "styles are in force" in line
                   for line in result.passed), result.passed
    assert (tmp_path / "check" / "809x589" / "unstyled.png").is_file()
