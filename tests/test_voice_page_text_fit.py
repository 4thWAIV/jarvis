"""Each slide's words are drawn as large as the slide allows (Tim's
standing preference: text large, using the slide's space): the shared deck
fits every slide's words to its frame (deck.js fitSlide, a binary search on
the slide's scale between the 13 px floor and the ceiling, run again on
every resize), so a sparse slide draws its words well past the floor and a
dense one comes down to it, never under it, nothing clipped. Measured in
CI on fixture decks (tests/deck_measure.py: the built deck drawn in
JARVIS's frame in headless Chromium), at the smallest and the largest
frame; no package is measured on its way. What does not fit at the floor is
refused as the deck is written (deck_capacity, tests/test_deck_capacity.py).

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import copy

import pytest
import yaml

from jarvis import layout_spec, make_package as mp
from tests import deck_measure as deck_fit

pytestmark = pytest.mark.page

FLOOR = layout_spec.spec()["text"]["min_px"]
ENDS = [(662, 489), (1600, 900)]
SENTENCE = ("Each repair is checked on the live system before the next one starts, and what "
            "it changed is written to the item it belongs to. ")


def _deck(folder):
    deck = copy.deepcopy(mp.EXAMPLE)
    # Nearly as much as the body slot holds at the floor: six steps, each with
    # two sentences (deck_capacity lets it through; the fit brings it down).
    dense = {"id": "dense", "type": "content", "title": "Everything the repairs change",
             "layout": "title-and-body",
             "objects": [{"id": "dense-words", "type": "steps", "slot": "body",
                          "source": {"reference": "lunch-plan"},
                          "items": [{"id": f"dense-{n}", "title": f"Repair {n}",
                                     "says": SENTENCE.strip()} for n in range(1, 7)]}],
             "readingOrder": ["dense-words"],
             "narration": [{"id": "dense-cue", "focus": ["dense-words"],
                            "say": "Everything the repairs change."}], "notes": ""}
    table = {"id": "table", "type": "content", "title": "Who approves what",
             "layout": "table",
             "objects": [{"id": "who", "type": "table", "slot": "table", "alt": "Who approves",
                          "source": {"reference": "lunch-plan"},
                          "head": ["Area", "Needs"], "rowHeads": True,
                          "rows": [{"id": f"who-{n}", "cells": [f"Area {n}", "Your yes."]}
                                   for n in range(1, 4)]}],
             "readingOrder": ["who"],
             "narration": [{"id": "table-cue", "focus": ["who"], "say": "Who approves what."}],
             "notes": ""}
    deck["slides"][2:2] = [dense, table]
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(deck, sort_keys=False, allow_unicode=True), encoding="utf-8")
    made = mp.make_presentation(source, folder / "out")
    return folder / "out" / "compiled" / made.deck["name"]


def _drawn(fit: dict, slide_id: str) -> dict:
    slide = deck_fit.of_slide(fit, slide_id)
    return {tuple(d["frame"]): d["text_px"] for d in slide["drawn"]}


def test_sparse_words_grow_and_dense_words_never_go_under_the_floor(tmp_path):
    fit = deck_fit.measure(_deck(tmp_path), ENDS)
    assert fit["fits"], "\n".join(deck_fit.problems(fit))
    assert [s["id"] for s in fit["slides"]] == ["opening", "what-changed", "dense", "table",
                                                "where", "answers"]
    for slide in fit["slides"]:
        for seen in slide["drawn"]:
            assert seen["text_px"]["least"] >= FLOOR, (slide["id"], seen)
    sparse, dense = _drawn(fit, "what-changed"), _drawn(fit, "dense")
    # The deck size before the fit was 18 deck units, at most 21 px: 13.5 px
    # at 662 x 489 and 21 px at 1600 x 900.
    assert sparse[(662, 489)]["least"] >= 20 and sparse[(1600, 900)]["least"] >= 32
    assert dense[(662, 489)]["least"] < 16, "the dense slide comes down toward the floor"
    assert dense[(662, 489)]["typical"] < sparse[(662, 489)]["typical"]
    assert dense[(1600, 900)]["typical"] < sparse[(1600, 900)]["typical"]
    # A sparse table and a sparse decision grow too.
    assert _drawn(fit, "table")[(662, 489)]["least"] > 18
    assert _drawn(fit, "where")[(1600, 900)]["least"] > 21


def test_the_fit_follows_the_frame(tmp_path):
    """Drawn at one frame and then another, each slide is fitted again:
    the same as drawing it at the second frame from the start."""
    folder = _deck(tmp_path)
    there = deck_fit.measure(folder, [(1600, 900), (662, 489)])
    fresh = deck_fit.measure(folder, [(662, 489)])
    assert [_drawn(there, s)[(662, 489)] for s in ("what-changed", "dense")] == \
        [_drawn(fresh, s)[(662, 489)] for s in ("what-changed", "dense")]
