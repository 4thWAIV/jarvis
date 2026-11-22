"""Every part of a decision slide fits its frame at every frame the layout
proof covers (Tim 2026-11-22): the question, the recommendation, the
context, each option with its Choose bar, Decide later, None of these, his
words field and the line saying what is recorded, nothing clipped and
nothing to scroll. Measured the way the deck step measures every build
(deck_fit.measure: the built deck drawn in JARVIS's frame in headless
Chromium at every frame from 662 x 489 to 1600 x 900).

The decision here has the words of the first real package's decision that
ran past the bottom at 662 x 489 (as long, in the same places), so the
shared deck's layout (examples/packages/decision-deck, deck.css and
deck.js) is held to it.

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import copy

import pytest
import yaml

from jarvis import deck_fit, make_package as mp

pytestmark = pytest.mark.page

QUESTION = ("Put the overnight repairs ahead of the faster index searches, or send the plan "
            "back for a revision?")
CONTEXT = ("Return the plan with one live check for the monitor\n"
           "Trace an explicit build answer on the live system to the task it released and the "
           "assigned agent's first run. If no such case is there yet, follow the next one.\n"
           "If the agent stays idle: Show where the work stopped. Put the repair that starts "
           "the agent without another prompt first among the workflow fixes.\n"
           "If the agent starts: Leave the working steps alone. Show which failures remain.\n"
           "Bring back: The repairs still needed, in order, with the reason any should come "
           "before faster index searches.")
OPTIONS = [
    {"id": "reject", "name": "Reject — keep the index rework next",
     "says": "Have the monitor trace an authorized task and return a revised build order. "
             "Authorize no new repair."},
    {"id": "approve", "name": "Approve — build the repairs first",
     "says": "Give the plan the next slot, starting with cheaper automated tests. Defer the "
             "search improvement and keep the separate permission decisions."}]
REASON = ("Keep faster index searches next and have the monitor trace an authorized build "
          "before moving the repair plan ahead of the index rework.")


def _deck(folder, decision: dict) -> object:
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["decisions"] = [{**deck["decisions"][0], **decision}]
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(deck, sort_keys=False, allow_unicode=True), encoding="utf-8")
    made = mp.make_presentation(source, folder / "out")
    return folder / "out" / "compiled" / made.deck["name"]


def test_the_long_decision_fits_every_frame(tmp_path):
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT, "options": OPTIONS,
                              "recommended": "reject", "reason": REASON})
    fit = deck_fit.measure(folder)
    assert len(fit["frames"]) >= 40 and [662, 489] in fit["frames"] and \
        [1600, 900] in fit["frames"]
    assert [s["id"] for s in fit["slides"]] == ["opening", "what-changed", "where", "answers"]
    assert fit["fits"], "\n".join(deck_fit.problems(fit))


def test_three_options_with_long_names_fit_every_frame(tmp_path):
    options = [{"id": f"o{n}", "name": f"Option {n}: " + "a considerably longer name " * 2,
                "says": "What this option changes, said in two plain sentences. " * 2}
               for n in range(1, 4)]
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT.split("\n")[1],
                              "options": options, "recommended": "o1", "reason": REASON})
    fit = deck_fit.measure(folder)
    assert fit["fits"], "\n".join(deck_fit.problems(fit))


def test_what_cannot_fit_is_measured_and_said(tmp_path):
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT * 6, "options": OPTIONS,
                              "recommended": "reject", "reason": REASON})
    fit = deck_fit.measure(folder, [(662, 489), (1600, 900)])
    assert not fit["fits"]
    where = deck_fit.of_slide(fit, "where")
    assert not where["fits"] and where["cut"][0]["fit"] == "cut", "every step of the fit tried"
    parts = {p["part"] for c in where["cut"] for p in c["parts"]}
    assert {"Decide later", "None of these"} <= parts
    [problem] = deck_fit.problems(fit)
    assert problem.startswith("Slide 3 (where) does not fit even after JARVIS's own fitting")
    assert "Split it into two slides" in problem
    # One slide alone (the step cache keeps each slide's fit, and only a
    # slide whose layout changed is measured again): the same measure.
    alone = deck_fit.measure(folder, [(662, 489), (1600, 900)], only=["where"])
    assert [s["id"] for s in alone["slides"]] == ["where"]
    assert alone["slides"][0]["cut"] == where["cut"] and alone["slides"][0]["slide"] == 3
