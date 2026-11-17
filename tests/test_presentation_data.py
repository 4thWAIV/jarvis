"""Charts and tables drawn from data (presentation_data): a dataset frozen
in presentation.yaml, views of it (where, sort, limit), bar, line and
scatter charts and data tables, every bar, point and cell a target the
story can light, each drawn by JARVIS and proven to fit without a browser."""
from __future__ import annotations

import json

import pytest

from jarvis import presentation_data
from jarvis.presentation import compile_deck
from jarvis.presentation_source import DeckCompileError, read_source
from tests.deck_fixtures import with_data
from tests.test_presentation import change, source  # noqa: F401  (the street example, copied)


def _page_data(folder):
    text = (folder / "presentation-data.js").read_text()
    return json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))


def test_charts_and_tables_from_data_compile_prove_and_light_points_and_cells(source, tmp_path):
    change(source, with_data)
    made = compile_deck(source, tmp_path / "compiled")
    rows = {t["member"]: t for t in json.loads(made.target_index.read_text())}
    assert rows["cost-chart"]["kind"] == "chart"
    assert rows["cost-chart-b1-cost"]["kind"] == "point"
    assert rows["cost-chart-b1-cost"]["accessible_name"] == "Cost (k€), North: 12 k€"
    assert rows["block-table-b3-cost"] == {**rows["block-table-b3-cost"], "kind": "cell",
                                           "accessible_name": "Cost (k€): 15.5"}
    page = _page_data(made.package_source)
    table = next(o for s in page["document"]["slides"] for o in s.get("objects", []) if o["id"] == "block-table")
    # planted or mixed, dearest first, two of them
    assert table["head"] == ["Block", "Cost (k€)", "Runoff (%)"]
    assert [r["cells"] for r in table["rows"]] == [["East", "15.5", "35"], ["North", "12", "40"]]
    bars = page["charts"]["cost-chart"]
    assert bars["categories"] == ["North", "South", "East", "West", "Centre"]
    assert [t["label"] for t in bars["y_ticks"]][0] == "0" and bars["zero"] == 0
    line = page["charts"]["runoff-line"]
    assert line["categories"] == ["Centre", "South", "West", "North", "East"], "sorted by trees"
    scatter = page["charts"]["tree-scatter"]
    assert [p["x"] for p in scatter["series"][0]["points"]][:2] == [0.72, 0.08]
    assert "x_ticks" in scatter and "categories" not in scatter


@pytest.mark.parametrize("mutate,words", [
    (lambda d: d["slides"][4]["objects"][0]["data"]["where"][0].update(one_of=["gravel"]), "keeps no rows"),
    (lambda d: d["slides"][2]["objects"][0].update(y=["cost", "colour"]), "no column 'colour'"),
    (lambda d: d["slides"][2]["objects"][0].update(y=["block"]), "y columns hold numbers"),
    (lambda d: d["slides"][3]["objects"][1].update(x="block"), "x column holds numbers"),
    (lambda d: d["datasets"][0]["rows"][0]["values"].update(cost="twelve"), "b1's cost is not a number"),
    (lambda d: d["datasets"][0]["rows"][0]["values"].pop("trees"), "b1 gives values for"),
    (lambda d: d["slides"][4]["objects"][0]["data"]["where"][0].update(at_least=3), "Each condition"),
    (lambda d: d["slides"][4]["objects"][0].update(head=["A", "B", "C"]), "names the dataset's"),
    (lambda d: d["slides"][4]["objects"][0].update(rows=[{"id": "r", "cells": ["a", "b", "c"]}]), "not both"),
    (lambda d: d["slides"][4]["objects"][0]["data"].update(dataset="nothing"), "Unknown dataset"),
])
def test_a_view_that_cannot_be_drawn_is_refused(source, mutate, words):
    change(source, lambda d: (with_data(d), mutate(d)))
    with pytest.raises(DeckCompileError, match="data:") as caught:
        read_source(source)
    assert words in str(caught.value)


def test_an_author_id_may_not_take_one_jarvis_draws(source):
    def take(d):
        with_data(d)
        d["slides"][1]["objects"][0]["id"] = "cost-chart-b2-runoff"
        d["slides"][1]["readingOrder"][0] = "cost-chart-b2-runoff"
        for cue in d["slides"][1]["narration"]:
            cue["focus"] = ["cost-chart-b2-runoff" if f == "comparison-title" else f for f in cue["focus"]]
    change(source, take)
    with pytest.raises(DeckCompileError, match="names something JARVIS draws"):
        read_source(source)


def test_too_many_bars_for_their_place_are_refused(source, tmp_path):
    def crowd(d):
        with_data(d)
        rows = d["datasets"][0]["rows"]
        rows[:] = [{"id": f"r{n}", "values": {**rows[0]["values"], "block": "B"}} for n in range(60)]
        d["slides"][2]["objects"][0].update(y=["cost", "runoff", "trees"])
        d["slides"][2]["narration"][0]["focus"] = ["cost-chart"]
        d["slides"][4]["narration"][0]["focus"] = ["block-table"]
        d["slides"][3]["narration"][0]["focus"] = ["tree-scatter"]
    change(source, crowd)
    with pytest.raises(DeckCompileError, match="each bar has .* px: show fewer rows"):
        compile_deck(source, tmp_path / "compiled")


def test_numbers_are_shown_in_the_deck_s_language_and_ticks_are_round():
    assert presentation_data.shown(12000.5, "en") == "12,000.5"
    assert presentation_data.shown(12000.5, "es") == "12 000,5"
    assert presentation_data.shown(7, "en") == "7"
    assert presentation_data.ticks(0, 87) == ([0, 20, 40, 60, 80, 100], 0)
    assert presentation_data.ticks(-3, 12)[0][0] == -5
    assert presentation_data.ticks(0.1, 0.42) == ([0.1, 0.2, 0.3, 0.4, 0.5], 1)
    assert presentation_data.ticks(0, 0.07) == ([0, 0.02, 0.04, 0.06, 0.08], 2)
