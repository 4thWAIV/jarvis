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
slot), each said so the author can split it or show it as a visual.
`decision_problems` and `answers_problems` hold a decision's slide and the
answers slide, the shared deck's own, to what they hold the same way,
worked out from its layout in deck.css (below); a decision's objects (what
its slide shows beside its words) in the band above its options, each in
its column, laid out in the height the rest of the slide leaves
(`shown_box`). The
deck.js text fit (each slide's words grown to fill it, never under the
floor) stays as rendering: within capacity, it always finds a size.
"""
from __future__ import annotations

import math
import re
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


# ── a decision's slide and the answers slide ─────────────────────────────
# JARVIS's own slides, drawn by the shared deck (examples/packages/
# decision-deck, deck.js decisionSlide and answersSlide) in its own layout
# (deck.css). Their capacity is worked out the same way, from deck.css's
# lengths in deck units at the smallest frame, every word at its floor and
# the slide's fit at the least the deck draws it at (layout-spec `fit`
# `scale_min`), in the tight spacing the deck falls back to there (deck.js
# fitSlide: a slide that fits so is drawn whole, nothing clipped and
# nothing to scroll, and the deck's own fit draws it larger wherever it has
# room). Where a rule of the decision's own layout outranks the tight one
# (a filled decision's gaps), the larger length is counted.

def _deck_css() -> str:
    from jarvis import data_paths
    path = data_paths.SOURCE_ROOT / "examples" / "packages" / "decision-deck" / "deck.css"
    return path.read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """The declarations of deck.css's rule for exactly `selector`."""
    found = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", _deck_css())
    if found is None:
        raise ValueError(f"deck.css has no rule for {selector!r}")
    return found.group(1)


def css_units(selector: str, prop: str) -> list[float]:
    """The deck-unit lengths (calc(N * var(--u)...)) of `prop` in deck.css's
    rule for `selector`, in order."""
    found = re.search(r"(?:^|[;{\s])" + re.escape(prop) + r"\s*:([^;]*)", _rule(selector))
    if found is None:
        raise ValueError(f"deck.css's {selector!r} sets no {prop}")
    numbers = [float(n) for n in re.findall(r"calc\(\s*([\d.]+)\s*\*\s*var\(--u\)", found.group(1))]
    if not numbers:
        raise ValueError(f"deck.css's {selector!r} {prop} is in no deck units")
    return numbers


def css_line(selector: str) -> float:
    found = re.search(r"line-height:\s*([\d.]+)", _rule(selector))
    if found is None:
        raise ValueError(f"deck.css's {selector!r} sets no line-height")
    return float(found.group(1))


def css_control(selector: str) -> float:
    """A control's least height in px at the smallest frame:
    max(N px, M deck units)."""
    found = re.search(r"min-height:\s*max\(\s*([\d.]+)px,\s*calc\(\s*([\d.]+)\s*\*\s*var\(--u\)",
                      _rule(selector))
    if found is None:
        raise ValueError(f"deck.css's {selector!r} sets no min-height of max(px, units)")
    return max(float(found.group(1)), float(found.group(2)) * _u())


def css_px(selector: str, prop: str) -> float:
    found = re.search(r"(?:^|[;{\s])" + re.escape(prop) + r"\s*:\s*([\d.]+)px", _rule(selector))
    if found is None:
        raise ValueError(f"deck.css's {selector!r} sets no {prop} in px")
    return float(found.group(1))


TIGHT_CONTROLS = ".slide.tight .choose, .slide.tight .vote button, .slide.tight .vote .words"
TIGHT_GROUPS = ".slide.tight .decide, .slide.tight .main, .slide.tight .context"


class _Deck:
    """The shared deck's decision layout at the smallest frame, the words
    at their floor and the slide's fit at its least."""

    def __init__(self):
        self.u, self.fit = _u(), layout_spec.spec()["fit"]["scale_min"]
        self.px = floor_px("body")
        self.body_line = self.px * layout_spec.spec()["styles"]["body"]["line"]
        self.question_line = floor_px("heading") * css_line("h2")
        self.name_line = self.px * css_line(".option h3")
        self.char = char_px(self.px)
        pad = css_units(".slide.tight", "padding")
        w, h = frame()
        self.width = w - 2 * pad[1] * self.u
        self.height = h - (pad[0] + pad[2]) * self.u
        self.gap = css_units(".slide.tight", "gap")[0] * self.u
        button = css_units(".slide.tight button", "padding")
        self.button_pad = button[1] * self.u
        self.button_h = self.body_line + 2 * button[0] * self.u + 2
        self.control = max(css_control(TIGHT_CONTROLS), self.button_h)

    def at(self, units: float, fitted: bool = False) -> float:
        return units * self.u * (self.fit if fitted else 1)

    def lines(self, text: str, width: float) -> int:
        return lines_of(" ".join(text.split()), width, self.px)

    def lines_kept(self, text: str, width: float) -> int:
        """Lines of words drawn with their line breaks kept (deck.css .why,
        white-space: pre-line): each line wrapped on its own."""
        return sum(lines_of(" ".join(line.split()), width, self.px)
                   for line in str(text).splitlines() if line.strip()) or 1

    def button_w(self, label: str) -> float:
        return len(label) * self.char + 2 * self.button_pad + 2


def _row(items: list[tuple[float, bool]], width: float, gap: float) -> list[list[tuple[int, float]]]:
    """A flex-wrap row: each item (basis, grows) placed while it fits, the
    free space of each row shared by the items that grow; each row as
    (item, width) pairs."""
    rows, current, used = [], [], 0.0
    for n, (basis, _) in enumerate(items):
        if current and used + gap + basis > width:
            rows.append(current)
            current, used = [], 0.0
        used += (gap if current else 0) + basis
        current.append(n)
    if current:
        rows.append(current)
    out = []
    for row in rows:
        used = sum(items[n][0] for n in row) + gap * (len(row) - 1)
        growing = [n for n in row if items[n][1]]
        share = max(0.0, width - used) / len(growing) if growing else 0.0
        out.append([(n, items[n][0] + (share if items[n][1] else 0)) for n in row])
    return out


def _parents(decisions) -> dict[str, tuple]:
    """Each child decision's parent and the options that open it (deck.js
    PARENT)."""
    out: dict[str, tuple] = {}
    for d in decisions:
        for o in d.options:
            for child in o.opens:
                parent, opened = out.get(child, (d, []))
                opened.append(o)
                out[child] = (parent, opened)
    return out


def _opens_text(parents, decision) -> str:
    parent, opened = parents[decision.id]
    return f"{parent.id} is " + " or ".join(o.name for o in opened)


@dataclass(frozen=True)
class Part:
    """One field's share of a decision slide: its name in the source, the
    lines it takes, its width in px and its characters."""
    field: str
    said: str
    lines: int
    px: float
    width: float
    chars: int


def _decision_parts(deck: _Deck, decision, index: int, decisions, parents,
                    problems: list[str] | None = None) -> tuple[float, list[Part]]:
    """The height of a decision's slide (deck.js decisionSlide in deck.css's
    layout) and the parts its words and its objects take; what in its
    objects does not fit their places is added to `problems`."""
    d, u = decision, deck.u
    problems = problems if problems is not None else []
    by_id = {x.id: x for x in decisions}
    parts: list[Part] = []
    total = len(decisions)

    def part(field, said, text, width, line, kept=False):
        n = ((deck.lines_kept if kept else deck.lines)(text, width)) if text else 0
        parts.append(Part(field, said, n, n * line, width, len(text or "")))
        return n * line

    kicker = f"Decision {index + 1} of {total} · {d.id}"
    if d.id in parents:
        kicker += " · asked when " + _opens_text(parents, d)
    kicker += f" · {total} of {total} answered"
    heights = [deck.lines(kicker, deck.width) * deck.body_line,
               part("question", "the question", d.question, deck.width, deck.question_line)]
    rec = next((o for o in d.options if o.id == d.recommended), None)
    recommends = (f"Recommended: {rec.name}. " if rec else "") + d.reason
    heights.append(part("reason", "the recommendation and its reason", recommends, deck.width,
                        deck.body_line))
    if d.context and not d.picture:
        heights.append(part("context", "the context", d.context, deck.width, deck.body_line,
                            kept=True))
    # The options, a grid of cards (deck.js picks the columns).
    n = len(d.options)
    pictured = any(o.picture for o in d.options)
    columns = min(4, n) if pictured else min(2, n) if d.picture else 2 if n == 4 else min(3, n)
    grid_gap = css_units(".slide.tight .options", "gap")[0] * u
    pad = css_units(".slide.tight .option", "padding")
    border = css_px(".option", "border")
    card_gap = css_units(".slide.tight .option", "gap")[0] * u
    text_gap = css_units(".slide.tight .option-text", "gap")[0] * u
    badge = css_units(".option h3 .badge", "margin-left")[0] + 2 * css_units(".badge", "padding")[0]
    badge_word = "R" * (len("Recommended") + math.ceil(badge * u / deck.char))
    choose = deck.control
    decide_gap = css_units(".slide.decision.fill .decide", "gap")[0] * u

    def options_in(main: float, part) -> float:
        """The options' height in `main` px of width: their cards, each
        with its picture at its least, its words and its Choose bar."""
        column = (main - (columns - 1) * grid_gap) / columns
        inner = column - 2 * border - 2 * pad[1] * u
        cards = []
        for i, o in enumerate(d.options):
            name = f"{'ABCDEFGHIJ'[i]}. {o.name}" + (f" {badge_word}" if o.id == d.recommended else "")
            h = 2 * border + (pad[0] + pad[2]) * u
            if o.picture:
                h += css_units(".options.has-pictures .option .picture", "min-height")[0] * u + card_gap
            h += part(f"options[{i}].name", f"option {o.id}'s name", name, inner, deck.name_line)
            h += text_gap + part(f"options[{i}].says", f"option {o.id}'s text", o.says, inner,
                                 deck.body_line)
            if o.opens:
                opens = "Then asks: " + ", ".join((by_id[c].short or c) if c in by_id else c
                                                  for c in o.opens)
                h += text_gap + deck.lines(opens, inner) * deck.body_line
            h += text_gap + choose
            cards.append(h)
        rows = [max(cards[r:r + columns]) for r in range(0, n, columns)]
        height = sum(rows) + (len(rows) - 1) * grid_gap
        if pictured:
            height += css_units(".options.has-pictures", "padding-top")[0] * u
        if d.compare:
            height += css_units(".decide .compare", "min-height")[0] * u + decide_gap
        return height

    if not d.picture:
        body = options_in(deck.width, part)
        if d.objects:
            # What the slide shows (its objects) in the band above the
            # options (deck.css .shown): each object in its column, the band
            # as tall as the tallest, laid out where it holds least
            # (`shown_box`); the band's own problems said with the slide's.
            band, band_parts, band_problems = _band(deck, d, index, decisions, parents)
            parts.extend(band_parts)
            problems.extend(band_problems)
            body += band + decide_gap
    else:
        # A decision's own picture yields to the options (deck.js arrange):
        # beside them, in a column with its words, the options in what that
        # column leaves (context-side); or above them, its words beside it,
        # the options across the whole width (context-above). The deck
        # draws whichever leaves every part whole, so the slide holds what
        # either holds.
        side = deck.at(css_units(".decide.with-context", "grid-template-columns")[0], True)
        side_gap = max(css_units(".decide.with-context", "gap")[0],
                       css_units(".slide.decision.fill .decide", "gap")[0]) * u
        context_gap = css_units(TIGHT_GROUPS, "gap")[0] * u
        beside = css_units(".slide.decision.fill .context-above > .context > .picture",
                           "flex")[0] * u
        ways = []
        for way in ("side", "above"):
            mine: list[Part] = []

            def part_of(field, said, text, width, line, mine=mine):
                k = deck.lines(text, width) if text else 0
                mine.append(Part(field, said, k, k * line, width, len(text or "")))
                return k * line
            if way == "side":
                options = options_in(deck.width - side - side_gap, part_of)
                context = part_of("context", "the context", d.context, side, deck.body_line) \
                    if d.context else 0.0
                height = max(options, context)
            else:
                options = options_in(deck.width, part_of)
                words = deck.width - beside - context_gap
                context = part_of("context", "the context", d.context, words, deck.body_line) \
                    if d.context else 0.0
                height = context + decide_gap + options
            ways.append((height, mine))
        body, mine = min(ways, key=lambda w: w[0])
        parts.extend(mine)
    heights.append(body)
    # The vote row: Decide later, None of these, his words, what is recorded.
    words = deck.at(css_units(".vote .words", "flex")[0], True)
    said = deck.at(css_units(".vote .recorded", "flex")[0], True)
    recorded = max([f"Recorded: {o.name}" + (", the recommended option." if o.id == d.recommended
                                             else ".") for o in d.options]
                   + [f"{o.name} needs your words: say what to change." for o in d.options
                      if o.id in d.needs_words]
                   + ["Not answered yet.", "Recorded: None of these.", "Recorded: Decide later."]
                   + ([f"Not asked yet: it applies when {_opens_text(parents, d)}."]
                      if d.id in parents else []), key=len)
    vote_gap = css_units(".slide.tight .vote", "gap")[0] * u
    control = deck.control
    items = [(deck.button_w("Decide later"), False), (deck.button_w("None of these"), False),
             (words, True), (said, True)]
    vote = 0.0
    for row in _row(items, deck.width, vote_gap):
        h = control
        for item, width in row:
            if item == 3:
                h = max(h, deck.lines(recorded, width) * deck.body_line)
        vote += h
    vote += vote_gap * (len(_row(items, deck.width, vote_gap)) - 1)
    heights.append(vote)
    return sum(heights) + deck.gap * (len(heights) - 1), parts


# The least height of the band a decision's objects are drawn in, in lines
# of body text at the floor: what a picture, a chart or a diagram is laid
# out in when the rest of the slide leaves less (and the slide is then over).
BAND_MIN_LINES = 5
# What grows to fill the band: laid out in its whole height.
_FILLS = (ChartObject, DiagramObject)


def canvas_box(width_px: float, height_px: float) -> tuple[float, float, float, float]:
    """The box on the 880 x 640 canvas whose inner size at the smallest frame
    (layout_spec.frame_box) is `width_px` x `height_px`."""
    w, h = frame()
    rule = layout_spec.spec()["unit"]
    pad = 2 * layout_spec.spec()["slot_padding"] * _u()
    return (0.0, 0.0, (width_px + pad) * rule["width"] / w, (height_px + pad) * rule["height"] / h)


def shown_box(decision, index: int, decisions) -> tuple[float, float, float, float]:
    """The box each of a decision's objects is laid out in (deck.css .shown,
    the band above its options, its objects side by side in equal columns):
    its column's width and the height the rest of the slide leaves at the
    smallest frame with every word at its floor, at least BAND_MIN_LINES
    lines; as a canvas box (`canvas_box`)."""
    deck = _Deck()
    bare = decision.model_copy(update={"objects": []})
    rest, _ = _decision_parts(deck, bare, index, decisions, _parents(decisions))
    decide_gap = css_units(".slide.decision.fill .decide", "gap")[0] * deck.u
    left = deck.height - rest - decide_gap
    k = max(1, len(decision.objects))
    gap = css_units(".shown", "gap")[0] * deck.u
    width = (deck.width - (k - 1) * gap) / k
    return canvas_box(width, max(left, BAND_MIN_LINES * line_px("body")))


def shown_heights(decision, index: int, decisions) -> dict[str, float]:
    """The least height, in deck units, the shared deck keeps for each of a
    decision's objects that fills its place (a chart, a diagram, a picture):
    the band's height it was laid out in (presentation.js sets it as the
    object's min-height, so it is drawn at least as the program laid it out)."""
    if not decision.objects:
        return {}
    box = shown_box(decision, index, decisions)
    _, h = holds(box)
    pad = 2 * layout_spec.spec()["slot_padding"] * _u()
    units = (h + pad) / _u()
    from jarvis.presentation_schema import ImageObject, VectorObject
    return {o.id: round(units, 3) for o in decision.objects
            if isinstance(o, (*_FILLS, ImageObject, VectorObject))}


def _band(deck: _Deck, d, index: int, decisions, parents) -> tuple[float, list[Part], list[str]]:
    """The band of a decision's objects: its height (the tallest object in
    its column; what fills its place, as tall as the band it is laid out
    in), each object's part and what in an object does not fit its place."""
    from jarvis.presentation_schema import ImageObject, VectorObject
    box = shown_box(d, index, decisions)
    width, held = holds(box)
    parts, problems, tallest = [], [], 0.0
    for i, obj in enumerate(d.objects):
        need = needs(obj, box)
        px = held if isinstance(obj, (*_FILLS, ImageObject, VectorObject)) else 0.0
        if need is not None:
            px = max(px, need.px)
            problems += [f"The {obj.type} {obj.id} on decision {d.id}'s slide does not fit its "
                         f"place: {p}." for p in need.problems]
        said = " ".join(words(obj)) if not isinstance(obj, (ImageObject, VectorObject)) else ""
        parts.append(Part(f"objects[{i}]", f"the {obj.type} {obj.id}",
                          math.ceil(as_lines(px)), px, width, len(said)))
        tallest = max(tallest, px)
    return tallest, parts, problems


def decision_problems(decision, index: int, decisions, slide_id: str) -> list[tuple[str, str]]:
    """What does not fit on a decision's slide, each (member, words for the
    author): the slide over what it holds at the smallest frame, naming its
    longest fields, the lines each takes and how much less would fit."""
    deck = _Deck()
    found: list[str] = []
    needed, parts = _decision_parts(deck, decision, index, decisions, _parents(decisions),
                                    found)
    out = [(decision.id, problem) for problem in found]
    if needed <= deck.height + 0.5:
        return out
    over_px = needed - deck.height
    longest = sorted((p for p in parts if p.lines), key=lambda p: -p.px)
    named = "; ".join(f"{p.said} (`{p.field}`) {p.lines} line{'s' if p.lines != 1 else ''}"
                      for p in longest[:4])
    top = longest[0]
    per = max(1, int(top.width // deck.char))
    less = min(top.chars, math.ceil(over_px / deck.body_line) * per)
    where = "662 x 489, every word at its floor"
    return out + [(decision.id, f"The decision {decision.id} (slide {slide_id}) is over what its slide "
                          f"holds at {where}: it needs {_over(needed, deck.height)}. Its "
                          f"longest parts: {named}. Say less in {top.said} (`{top.field}`, about "
                          f"{less} characters less), give fewer options, or put the background "
                          f"on a content slide before it.")]


def answers_problems(decisions) -> list[tuple[str, str]]:
    """What does not fit on the answers slide (deck.js answersSlide): every
    decision a row of its name, the answer chosen and its state, then
    Approve all recommended and its confirmation."""
    if not decisions:
        return []
    deck, u = _Deck(), _u()
    parents = _parents(decisions)
    where = "662 x 489, every word at its floor"
    out: list[tuple[str, str]] = []
    first, last = css_units(".answer-list li", "grid-template-columns")
    first, last = deck.at(first, True), deck.at(last, True)
    col_gap = css_units(".answer-list li", "gap")[0] * u
    middle = deck.width - first - last - 2 * col_gap
    pick_pad = 2 * css_units(".pick", "padding")[0] * u + 2 + 2 * deck.px   # its arrow
    indent = css_units(".answer-list li.child .go", "margin-left")[0] * u
    rows = []
    for d in decisions:
        labels = [o.name + (" (recommended)" if o.id == d.recommended else "") for o in d.options]
        widest = max(labels + ["Not answered", "Decide later", "None of these"], key=len)
        need = len(widest) * deck.char + pick_pad
        # The answer's list may run on into the state's column while that
        # is empty; a child question's says it is not asked yet.
        room = middle if d.id in parents else middle + col_gap + last
        if need > room + 0.5:
            out.append((d.id, f"The answers slide lists every decision with its answer chosen "
                              f"from a list; the answer {widest!r} of {d.id} takes "
                              f"{need:.0f} px where the list holds {room:.0f} px at {where} "
                              f"(about {int((room - pick_pad) // deck.char)} characters). "
                              f"Shorten that option's name."))
        go_w = first - (indent if d.id in parents else 0) - 2 * deck.button_pad - 2
        go = deck.lines(d.short or d.id, go_w) * deck.body_line + deck.button_h - deck.body_line
        state = deck.lines("Not asked yet", last) * deck.body_line if d.id in parents else 0
        rows.append((max(go, deck.button_h, state), d))
    listed = sum(h for h, _ in rows) + css_units(".slide.tight .answer-list", "gap")[0] * u * (len(rows) - 1)
    n = len(decisions)
    kicker = f"Your answers · {n} of {n} answered"
    confirm = (f"Record the recommended option for the {n} decision{'' if n == 1 else 's'} with "
               f"no answer yet? Answers already given, Decide later included, stay as they are.")
    room = deck.width - deck.button_w("Yes, record them") - deck.button_w("No") \
        - 2 * css_units(".confirm", "gap")[0] * u
    approve = max(deck.button_h, deck.lines(confirm, room) * deck.body_line)
    needed = (deck.lines(kicker, deck.width) * deck.body_line
              + deck.lines("What is recorded", deck.width) * deck.question_line
              + listed + approve + 3 * deck.gap)
    if needed > deck.height + 0.5:
        tallest, d = max(rows, key=lambda r: r[0])
        out.append((d.id, f"The answers slide is over what it holds at {where}: its {n} "
                          f"decisions, a row each, need {_over(needed, deck.height)}. The tallest "
                          f"row is {d.id}'s, its name in the list (`short`, else its id) "
                          f"{deck.lines(d.short or d.id, first)} lines. Give each decision a "
                          f"shorter `short`, or ask fewer decisions in one package."))
    return out
