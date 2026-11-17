"""Tables and charts drawn from data, for presentation/1.

A deck's `datasets` are records frozen in its source. A table's or chart's
`data` is a view of one (presentation_schema.DataView: every `where` kept,
then `sort`, then the first `limit`). This module is the one place those
views are worked out, as the source is read:

- `expand_table` turns a data table into rows of words (the dataset's
  columns' labels its heading, each value as `shown`), so it is drawn and
  proven exactly as a table of literal rows is.
- `resolve_chart` turns a chart into what is drawn: its categories or x
  ticks, its y ticks, each series' points as shares of the plot (0 to 1)
  and the words it shows. presentation.js only places these by percentage;
  presentation_proof measures the words. Nothing about a chart is worked out
  twice.

Every table cell given `columns`, and every bar or point, is a target the
story can light (`cell_ids`, the chart's points' `id`).
"""
from __future__ import annotations

import math

from jarvis import layout_spec
from jarvis.presentation_schema import (ChartObject, Column, Condition, DataRow, Dataset, DataView,
                                        TableObject, TableRow)


class DataProblem(ValueError):
    def __init__(self, member: str, message: str):
        super().__init__(message)
        self.member = member


def check_dataset(dataset: Dataset) -> None:
    """Every row holds exactly one value of the right type for each column."""
    names = [c.id for c in dataset.columns]
    if len(set(names)) != len(names):
        raise DataProblem(dataset.id, "Each column of a dataset has its own id.")
    for row in dataset.rows:
        if set(row.values) != set(names):
            raise DataProblem(row.id, f"Row {row.id} gives values for {', '.join(sorted(row.values))}; "
                                      f"the dataset's columns are {', '.join(names)}.")
        for column in dataset.columns:
            if _is_number(row.values[column.id]) != (column.type == "number"):
                raise DataProblem(row.id, f"Row {row.id}'s {column.id} is not a {column.type}.")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _keeps(condition: Condition, column: Column, value) -> bool:
    if condition.equals is not None:
        return value == condition.equals
    if condition.one_of is not None:
        return value in condition.one_of
    if condition.at_least is not None and value < condition.at_least:
        return False
    return condition.at_most is None or value <= condition.at_most


def rows_of(view: DataView, datasets: dict[str, Dataset], member: str) -> tuple[Dataset, list[DataRow]]:
    """The rows `view` keeps, in its order."""
    dataset = datasets.get(view.dataset)
    if dataset is None:
        raise DataProblem(member, f"Unknown dataset {view.dataset!r}.")
    columns = {c.id: c for c in dataset.columns}
    for condition in view.where:
        column = columns.get(condition.column)
        if column is None:
            raise DataProblem(member, f"The dataset {dataset.id} has no column {condition.column!r}.")
        given = [f for f in ("equals", "one_of", "at_least", "at_most") if getattr(condition, f) is not None]
        if not given or (len(given) > 1 and set(given) != {"at_least", "at_most"}):
            raise DataProblem(member, "Each condition gives one of equals, one_of, or at_least and at_most.")
        if ("at_least" in given or "at_most" in given) and column.type != "number":
            raise DataProblem(member, f"at_least and at_most compare numbers; {column.id} is text.")
    if view.sort and view.sort.column not in columns:
        raise DataProblem(member, f"The dataset {dataset.id} has no column {view.sort.column!r} to sort by.")
    rows = [r for r in dataset.rows
            if all(_keeps(c, columns[c.column], r.values[c.column]) for c in view.where)]
    if view.sort:
        rows.sort(key=lambda r: r.values[view.sort.column], reverse=view.sort.order == "descending")
    rows = rows[:view.limit] if view.limit else rows
    if not rows:
        raise DataProblem(member, f"The view of {dataset.id} keeps no rows.")
    return dataset, rows


def shown(value, language: str, decimals: int | None = None) -> str:
    """A value as it is read: a number grouped by thousands in the deck's
    language (12,000.5 in English, 12 000,5 in Spanish, its space one that
    never breaks), else the words as given."""
    if not _is_number(value):
        return value
    if decimals is None:
        decimals = 0 if float(value).is_integer() else min(2, len(repr(float(value)).split(".")[1]))
    text = f"{value:,.{decimals}f}"
    if language == "es":
        text = text.replace(",", " ").replace(".", ",")
    return text


def _label(column: Column) -> str:
    return f"{column.label} ({column.units})" if column.units else column.label


def expand_table(obj: TableObject, datasets: dict[str, Dataset], language: str) -> None:
    """A data table's rows and heading from its view; a literal one checked."""
    if (obj.rows is None) == (obj.data is None):
        raise DataProblem(obj.id, "A table gives either its rows or the data it shows, not both.")
    if obj.rows is not None:
        width = len(obj.head) if obj.head else len(obj.rows[0].cells)
        if obj.columns is not None and len(obj.columns) != width:
            raise DataProblem(obj.id, f"The table names {len(obj.columns)} columns and shows {width}.")
        return
    if obj.columns is None or obj.head is not None:
        raise DataProblem(obj.id, "A data table names the dataset's `columns` to show; JARVIS writes "
                                  "its heading from their labels.")
    dataset, rows = rows_of(obj.data, datasets, obj.id)
    columns = {c.id: c for c in dataset.columns}
    for name in obj.columns:
        if name not in columns:
            raise DataProblem(obj.id, f"The dataset {dataset.id} has no column {name!r}.")
    obj.head = [_label(columns[name]) for name in obj.columns]
    obj.rows = [TableRow(id=f"{obj.id}-{row.id}",
                         cells=[shown(row.values[name], language) for name in obj.columns])
                for row in rows]


def cell_ids(obj: TableObject) -> list[list[str]]:
    """Each cell's target, `<row id>-<column id>`, when the table names its columns."""
    if not obj.columns:
        return []
    return [[f"{row.id}-{name}" for name in obj.columns] for row in obj.rows]


def ticks(low: float, high: float, count: int = 5) -> tuple[list[float], int]:
    """Round tick values covering low to high, about `count` steps of 1, 2
    or 5 times a power of ten, and the decimals their labels need."""
    if low == high:
        low, high = (low - 1, high + 1) if low else (0, 1)
    raw = (high - low) / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw - 1e-12)
    start, end = math.floor(low / step + 1e-9) * step, math.ceil(high / step - 1e-9) * step
    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    return [round(start + n * step, 10) for n in range(round((end - start) / step) + 1)], decimals


def resolve_chart(obj: ChartObject, datasets: dict[str, Dataset], language: str) -> dict:
    """What `obj` draws, set on it as `_drawn` and returned."""
    dataset, rows = rows_of(obj.data, datasets, obj.id)
    columns = {c.id: c for c in dataset.columns}
    for name in [obj.x, *obj.y]:
        if name not in columns:
            raise DataProblem(obj.id, f"The dataset {dataset.id} has no column {name!r}.")
    if any(columns[name].type != "number" for name in obj.y):
        raise DataProblem(obj.id, "A chart's y columns hold numbers.")
    if obj.kind == "scatter" and columns[obj.x].type != "number":
        raise DataProblem(obj.id, "A scatter chart's x column holds numbers.")
    if len(set(obj.y)) != len(obj.y) or obj.x in obj.y:
        raise DataProblem(obj.id, "A chart names each column once.")
    values = [row.values[name] for name in obj.y for row in rows]
    low, high = min(values), max(values)
    if obj.kind == "bar":
        low, high = min(low, 0), max(high, 0)
    y_ticks, y_decimals = ticks(low, high)
    bottom, top = y_ticks[0], y_ticks[-1]

    def share(value, lo=bottom, hi=top):
        return round((value - lo) / (hi - lo), 6)

    drawn: dict = {"kind": obj.kind, "band": layout_spec.spec()["chart"]["band"], "zero": share(min(max(0, bottom), top)),
                   "y_ticks": [{"at": share(t), "label": shown(t, language, y_decimals)} for t in y_ticks]}
    if obj.kind == "scatter":
        xs = [row.values[obj.x] for row in rows]
        x_ticks, x_decimals = ticks(min(xs), max(xs))
        drawn["x_ticks"] = [{"at": share(t, x_ticks[0], x_ticks[-1]), "label": shown(t, language, x_decimals)}
                            for t in x_ticks]
        place = lambda n, row: share(row.values[obj.x], x_ticks[0], x_ticks[-1])  # noqa: E731
    else:
        drawn["categories"] = [shown(row.values[obj.x], language) for row in rows]
        place = lambda n, row: n  # noqa: E731 (the category's index)
    drawn["series"] = []
    for name in obj.y:
        column = columns[name]
        points = []
        for n, row in enumerate(rows):
            value = row.values[name]
            said = f"{shown(row.values[obj.x], language)}: {shown(value, language)}" \
                   + (f" {column.units}" if column.units else "")
            points.append({"id": f"{obj.id}-{row.id}-{name}", "x": place(n, row), "y": share(value),
                           "name": said})
        drawn["series"].append({"column": name, "label": _label(column), "points": points})
    # The legend names every series, one too: it is where the units are read.
    drawn["words"] = ([s["label"] for s in drawn["series"]]
                      + [t["label"] for t in drawn["y_ticks"]]
                      + [t["label"] for t in drawn.get("x_ticks", [])] + drawn.get("categories", []))
    obj._drawn = drawn
    return drawn
