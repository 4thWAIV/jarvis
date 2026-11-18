"""What only a browser can show of a deck, proven on fixture decks in a real,
isolated JARVIS (tests/isolated_jarvis.py): its own server process on a
free port above 19000, the voice page built by vite, a headless Chromium on
its real package frame at the centre's two sizes and two of the whole
width. No package goes through a browser on its way to Tim (JARVIS proves
every deck's layout without one, presentation_proof); these tests prove,
for every change to the shared deck, the story helper and the package view,
that the deck JARVIS builds still votes by click and by decide.py, that its
story waits at every question, stops within a second from the page and the
brain's tool and resumes, and that the border's Chapters, Next, Previous and
count move through every slide the deck draws."""
from __future__ import annotations

import pytest

from jarvis import make_package as mp
from tests import dashboard_page, isolated_jarvis
from tests.deck_fixtures import write_yaml
from tests.test_decision_package import small_deck

# A browser page test: run with `pytest -m page` (see pytest.ini).
# The isolated JARVIS checks the deck's signature with the real ssh-keygen,
# so the agent's key here is a real one too (conftest fakes it otherwise).
pytestmark = [pytest.mark.page, pytest.mark.ssh_keygen]

SIZES = ("809x589", "662x489", "1600x900", "1280x713")

# What the border must do at every size (incident: decision slides missing
# from the border's navigation).
NAVIGATION = ("the border's count followed the deck",
              "a slide chosen in the border's Chapters opens in the deck",
              "Chapters lists every slide of the deck, each question marked",
              "Next and Previous reach every slide of the deck in turn")


def checked(tmp_path, deck: dict) -> isolated_jarvis.Result:
    """`deck` written as presentation.yaml, built as make_package builds it
    (laid out and proven by JARVIS), and checked in an isolated JARVIS."""
    reason = dashboard_page.why_unavailable()
    if reason:
        pytest.skip(reason)
    from jarvis import agent_keys
    made = mp.make_presentation(write_yaml(tmp_path / "in", deck), tmp_path / "out")
    signer = agent_keys.make_pair(agent_keys.key_folder("sess-1"), "jarvis-agent")
    result = isolated_jarvis.check(made.archive, made.deck, tmp_path / "check", signer, "sess-1")
    if any("Executable doesn't exist" in f for f in result.failures):   # pragma: no cover
        pytest.skip("no Playwright Chromium")
    assert result.ok, result.failures
    return result


def has(result, size: str | None, check: str) -> bool:
    prefix = f"{size}: " if size else ""
    return any(line.startswith(prefix) and check in line for line in result.passed)


def test_a_decision_deck_votes_tells_stops_resumes_and_navigates(tmp_path):
    result = checked(tmp_path, small_deck())
    for size in SIZES:
        for check in ("the package frame is", "the deck's own styles are in force",
                      "every vote control is visible",
                      "the border does not say its layout was not checked",
                      "the deck is drawn in the font JARVIS serves", *NAVIGATION):
            assert has(result, size, check), (size, check, result.passed)
    for check in ("first time: the story waits at where", "first time: the story waits at when",
                  "first time: every question answered, in order (where, when)",
                  "stop by the page during narration: stopped within 1s",
                  "stop by the page while waiting: stopped within 1s",
                  "stop by the brain's tool during narration: stopped within 1s",
                  "stop by the brain's tool while waiting: stopped within 1s",
                  "stop again: the brain is told it is already stopped",
                  "resume: picked up at slide 4",
                  "list_packages found it by its title's words"):
        assert has(result, None, check), (check, result.passed)
    assert list((tmp_path / "check" / "flow").glob("*waiting-where.png"))


def test_a_presentation_asks_nothing_on_its_slides(tmp_path):
    """A presentation asks nothing of its own, and its standard vote is asked
    at the package view's Done, never on a slide: the story is told to its
    end and no slide waits on the vote."""
    from tests.test_make_package import presentation
    result = checked(tmp_path, presentation())
    for size in SIZES:
        for check in ("opened it by its title", "the package frame is",
                      "the deck's own styles are in force", *NAVIGATION):
            assert has(result, size, check), (size, check, result.passed)
    for check in ("the story went on to its end", "every beat told pointed"):
        assert has(result, None, check), (check, result.passed)
    assert not has(result, None, "the story waits at approve-package"), \
        "no slide waits on the vote"
