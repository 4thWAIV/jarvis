"""How much each slot of JARVIS's named layouts holds, worked out by
program from the layout spec (presentation_assets/layout-spec.json) alone:
at the smallest frame (662 x 489), with every word at its style's floor.
No browser and no font: a word's width is taken as the spec's
`capacity.char_em` of its size, an average character wider than most, so
content within capacity fits wherever the deck is drawn (Tim 2026-11-22,
construction-drafts.md "Visuals, the salvage, and sizing": sizing comes
from the language; no per-package browser measurement).

Every object type has its height worked out the way presentation.css and
the generated CSS lay it out (`needs`): text by its paragraphs, a table by
its rows, a callout, figures and steps by their parts, a bar object by its
bars, and a chart by its legend, its labels and the least plot it keeps
(its labels must fit their places too). A diagram is laid out by program
(diagram_layout) where its slot holds least for its words across the
frame range, and what does not fit is said by its layout: more ranks,
nodes side by side, levels, leaves or nodes round a ring or along an axis
than its slot holds (`holds`, each worked out from the spec), or words over
the lines a node or a link allows. Pictures and vectors hold no words and
are proven by picture_proof.

`problems` is what presentation_source refuses as a deck is written:
an object over its slot's capacity, naming the slot and how much over; a
title over its place; and a slide of only prose over the capacity of one
paragraph (`capacity.prose_paragraph_lines` lines of its widest text
slot), each said so the author can split it or show it as a visual. The
deck.js text fit (each slide's words grown to fill it, never under the
floor) stays as rendering: within capacity, it always finds a size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from jarvis import layout_spec
from jarvis.presentation_schema import (BarObject, CalloutObject, ChartObject, ContentSlide,
                                        DiagramObject, FiguresObject, StepsObject, TableObject,
                                        TextObject, words)

# What to do instead, said with every refusal.
SHOW_IT = ("show what can be shown: a sequence as numbered steps, numbers as a chart from a "
           "dataset or as key figures, a comparison as a comparison or a table, a warning or a "
           "finding as a callout, a process, a loop, a structure or a schedule as a diagram (a "
           "flow, a cycle, a hierarchy or a timeline)")


def frame() -> tuple[float, float]:
    return tuple(layout_spec.spec()["frame"]["min"])


def _u() -> float:
    return layout_spec.unit(frame())


def floor_px(style: str) -> float:
    return layout_spec.spec()["styles"][style]["min_px"]


def line_px(style: str, px: float | None = None) -> float:
    return (px or floor_px(style)) * layout_spec.spec()["styles"][style]["line"]


def char_px(px: float) -> float:
    return px * layout_spec.spec()["capacity"]["char_em"]


def lines_of(text: str, width: float, px: float) -> int:
    """Lines `text` takes wrapped into `width` px at `px` a word size: whole
    words while they fit, a word longer than a line broken across lines."""
    per = max(1, int(width // char_px(px)))
    lines, used = 0, None
    for paragraph in text.split("\n"):
        used = None
        for word in paragraph.split():
            n = len(word)
            if used is None:
                lines += math.ceil(n / per)
                used = n % per or per
            elif used + 1 + n <= per:
                used += 1 + n
            else:
                lines += math.ceil(n / per)
                used = n % per or per
        if used is None:
            lines += 1
    return max(1, lines)


@dataclass(frozen=True)
class Need:
    """An object's height at the floor and what else does not fit."""
    px: float
    problems: tuple[str, ...] = ()


def _text(obj: TextObject, width: float) -> Need:
    body = floor_px("body")
    total = 0.0
    for p in obj.paragraphs:
        style = "heading" if p.role == "heading" else "body"
        px = floor_px(style)
        indent = 1.3 * body if p.role in ("bullet", "numbered") else 0
        said = "".join(r.text for r in p.runs)
        total += lines_of(said, width - indent, px) * line_px(style)
        if p.role in ("paragraph", "heading"):
            total += 0.35 * px
    return Need(total)


def _table(obj: TableObject, width: float) -> Need:
    u, px = _u(), floor_px("cell")
    rows = ([obj.head] if obj.head else []) + [r.cells for r in obj.rows or []]
    columns = len(rows[0]) if rows else 1
    inner = width / columns - 16 * u
    total = sum(max(lines_of(c, inner, px) for c in row) * line_px("cell") + 8 * u + 1
                for row in rows)
    return Need(total)


def _callout(obj: CalloutObject, width: float) -> Need:
    c, u, px = layout_spec.spec()["components"]["callout"], _u(), floor_px("body")
    inner = width - c["border_px"] - 2 * c["pad"][0] * u
    n = lines_of(obj.says, inner, px) + (lines_of(obj.label, inner, px) if obj.label else 0)
    return Need(2 * c["pad"][1] * u + n * line_px("body"))


def _figures(obj: FiguresObject, width: float) -> Need:
    f, u = layout_spec.spec()["components"]["figures"], _u()
    count = len(obj.items)
    inner = (width - (count - 1) * f["gap"] * u) / count - 2 * f["pad"][0] * u
    tallest = max(lines_of(i.value, inner, floor_px("figure")) * line_px("figure")
                  + lines_of(i.label, inner, floor_px("body")) * line_px("body")
                  for i in obj.items)
    return Need(2 * f["pad"][1] * u + tallest)


def _steps(obj: StepsObject, width: float) -> Need:
    s, u, px = layout_spec.spec()["components"]["steps"], _u(), floor_px("body")
    inner = width - (s["marker"] + s["marker_gap"]) * px
    total = sum(max(s["marker"] * px,
                    (lines_of(i.title, inner, px) + (lines_of(i.says, inner, px) if i.says else 0))
                    * line_px("body")) for i in obj.items)
    return Need(total + (len(obj.items) - 1) * s["gap"] * u)


def _bars(obj: BarObject, width: float) -> Need:
    px = floor_px("body")
    gap = 0.6 * px
    label = max(5 * px, (width - gap) / 3)
    rows = sum(max((lines_of(b.label, label, px) + lines_of(f"{b.value:g} {obj.units}", label, px))
                   * line_px("body"), 1.8 * px) for b in obj.bars)
    return Need(rows + len(obj.bars) * gap + line_px("body"))


def _chart(obj: ChartObject, width: float) -> Need:
    """A chart's legend, x labels and half-line margins, and the least plot
    it keeps; its labels must fit their places, its bars be wide enough."""
    c = layout_spec.spec()["chart"]
    style = c["style"]
    px = layout_spec.chart_px(_u())
    lh, cw = line_px(style, px), char_px(px)
    drawn = obj._drawn or {}
    series = drawn.get("series") or []
    problems = []
    # The legend wraps its series' names across the width.
    legend, used = 1, 0.0
    for s in series:
        item = (c["swatch"] + c["swatch_gap"]) * px + len(s["label"]) * cw
        if used and used + c["legend_gap"] * px + item > width:
            legend, used = legend + 1, item
        else:
            used += (c["legend_gap"] * px if used else 0) + item
    y_width = max((len(t["label"]) for t in drawn.get("y_ticks") or []), default=1) * cw \
        + c["axis_gap"] * px
    plot_width = width - y_width
    if drawn.get("categories"):
        n = len(drawn["categories"])
        column = plot_width / n
        x_lines = max(lines_of(label, column, px) for label in drawn["categories"])
        if x_lines > c["x_lines_max"]:
            problems.append(f"its x labels need {x_lines} lines in columns {column:.0f} px wide "
                            f"(at most {c['x_lines_max']}): show fewer rows or shorter labels")
        if drawn.get("kind") == "bar":
            bar = column * c["band"] / max(1, len(series))
            if bar < c["mark_min_px"]:
                problems.append(f"each bar has {bar:.1f} px (at least {c['mark_min_px']}): show "
                                f"fewer rows or series")
        x_height = min(x_lines, c["x_lines_max"]) * lh
    else:
        ticks = drawn.get("x_ticks") or []
        widest = max((len(t["label"]) for t in ticks), default=1) * cw
        if len(ticks) > 1 and widest > plot_width / (len(ticks) - 1):
            problems.append("its x tick labels run into each other: give the chart more width")
        x_height = lh
    return Need(legend * lh + x_height + lh + c["plot_min_px"], tuple(problems))


NEEDS = {TextObject: _text, TableObject: _table, CalloutObject: _callout,
         FiguresObject: _figures, StepsObject: _steps, BarObject: _bars, ChartObject: _chart}


def drawn_diagram(obj: DiagramObject, box) -> dict:
    """A diagram as diagram_layout lays it out in `box`: the one read as the
    source was (presentation_source), else laid out now."""
    from jarvis import diagram_layout
    if obj._drawn is None:
        obj._drawn = diagram_layout.layout(obj, box)
    return obj._drawn


def _diagram(obj: DiagramObject, box) -> Need:
    """A diagram's height: what its layout uses of its slot, at the
    smallest frame. What does not fit (too many nodes for its slot, words
    over their lines) is said by its layout, in the author's terms, and its
    height is then the slot's."""
    drawn = drawn_diagram(obj, box)
    _, held = holds(box)
    if drawn["problems"]:
        return Need(held, tuple(drawn["problems"]))
    boxes = [n["box"] for n in drawn["nodes"]] + [g["box"] for g in drawn["groups"]] + \
        [link["label_box"] for link in drawn["links"] if link.get("label_box")] + \
        [n["when_box"] for n in drawn["nodes"] if n.get("when_box")]
    top = min(b[1] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    return Need((bottom - top) * held)


def needs(obj, box) -> Need | None:
    """`obj`'s height in its slot `box` (deck units) at the smallest frame
    with its words at their floor; None for a picture or a vector."""
    if isinstance(obj, DiagramObject):
        return _diagram(obj, box)
    work = NEEDS.get(type(obj))
    if work is None:
        return None
    return work(obj, layout_spec.frame_box(box, frame())[0])


def holds(box) -> tuple[float, float]:
    """A slot's inner width and height in px at the smallest frame."""
    return layout_spec.frame_box(box, frame())


def as_lines(px: float) -> float:
    """A height said as lines of body text at its floor."""
    return px / line_px("body")


def slots() -> dict[str, dict[str, dict]]:
    """Every layout's slots (a comparison's columns as one of four, the
    narrowest), each with how much it holds at the floor: its px, its lines
    of body text and the characters a body line holds."""
    out: dict[str, dict[str, dict]] = {}
    for layout, named in layout_spec.spec()["layouts"].items():
        filled = set(layout_spec.slots_of(layout))
        out[layout] = {}
        for slot, box in layout_spec.boxes(layout, filled).items():
            w, h = holds(box)
            out[layout][slot] = {"px": [round(w, 1), round(h, 1)],
                                 "lines": int(as_lines(h)),
                                 "chars_per_line": int(w // char_px(floor_px("body")))}
    return out


def _over(needed: float, held: float) -> str:
    need, hold = math.ceil(as_lines(needed)), int(as_lines(held))
    return (f"{need} lines of body text where it holds {hold}: {max(1, need - hold)} lines "
            f"({100 * (needed - held) / held:.0f}%) over")


def _advice(obj) -> str:
    if isinstance(obj, TextObject):
        return f"Split the slide in two, or {SHOW_IT}."
    if isinstance(obj, TableObject):
        return ("Show fewer rows (a view of a dataset with where or limit), fewer columns, or "
                "split it across slides.")
    if isinstance(obj, ChartObject):
        return "Show fewer rows or series, or give the chart a larger slot."
    return "Say less in it, split it across slides, or give it a larger slot."


def problems(slide: ContentSlide) -> list[tuple[str, str]]:
    """What does not fit on a content slide, each (member, words for the
    author): every object over its slot's capacity, the title over its
    place, and a slide of only prose over one paragraph."""
    out: list[tuple[str, str]] = []
    where = "662 x 489, every word at its floor"
    title_w, title_h = holds(layout_spec.spec()["title"])
    title_lines = lines_of(slide.title, title_w, floor_px("heading"))
    if title_lines * line_px("heading") > title_h + 0.5:
        out.append((slide.id, f"The title of slide {slide.id} takes {title_lines} lines; its place "
                              f"holds {int(title_h // line_px('heading'))} at {where}. Shorten it."))
    boxes = layout_spec.boxes(slide.layout, {obj.slot for obj in slide.objects})
    for obj in slide.objects:
        need = needs(obj, boxes[obj.slot])
        if need is None:
            continue
        _, held = holds(boxes[obj.slot])
        if need.px > held + 0.5:
            out.append((obj.id, f"The {obj.slot} slot of slide {slide.id} ({slide.layout}) is over "
                                f"its capacity at {where}: {obj.id} needs {_over(need.px, held)}. "
                                f"{_advice(obj)}"))
        for problem in need.problems:
            out.append((obj.id, f"The {obj.type} {obj.id} on slide {slide.id} does not fit its "
                                f"{obj.slot} slot: {problem}."))
    texts = [obj for obj in slide.objects if isinstance(obj, TextObject)]
    if texts and len(texts) == len(slide.objects):
        chars = sum(len(w) for obj in texts for w in words(obj))
        widest = max(holds(boxes[obj.slot])[0] for obj in texts)
        one = layout_spec.spec()["capacity"]["prose_paragraph_lines"] \
            * int(widest // char_px(floor_px("body")))
        if chars > one:
            out.append((slide.id, f"Slide {slide.id} is only prose, {chars} characters; one "
                                  f"paragraph here holds about {one}. Do not put long paragraphs "
                                  f"on a slide: {SHOW_IT}; or split it."))
    return out


def object_capacity(obj, box) -> dict | None:
    """What the layout data says of an object's room: its slot's capacity
    and what it needs, in lines of body text at the floor."""
    need = needs(obj, box)
    if need is None:
        return None
    _, held = holds(box)
    out = {"holds_lines": int(as_lines(held)), "needs_lines": math.ceil(as_lines(need.px)),
           "fits": need.px <= held + 0.5 and not need.problems}
    if isinstance(obj, DiagramObject):
        # What its slot holds of a diagram of its kind (diagram_layout.holds),
        # beside the nodes it has.
        out.update(kind=obj.kind, nodes=len(obj.nodes), holds=drawn_diagram(obj, box)["holds"])
    return out
