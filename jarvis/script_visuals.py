"""The script describes each slide's visual (Tim 2026-11-22, construction-
drafts.md "The script describes each slide's visual": "So if you are saying
the script should describe the slide and how it will look with the
information to fill it in when we build it but in a text description for
later building that that was what I told you to do to begin with yes.").

The outline names each slide's visual form and why it carries the point
(`FORMS`, package_script.outline_schema); the script gives each visual as
typed pieces with the content that fills it (`PIECES`), the same types as
presentation/1's objects: steps, key figures each with its source, a
callout, a comparison, a chart's dataset with each value's source path and
position, beside the text, Tim's own words, a picture and a table script/1
always had. This module is the one place those pieces are read by program:

- `piece_problems`: what a visual cannot be without (counts within what the
  deck draws, a chart's columns and numbers);
- `value_problems`: every number a chart or a key figure shows found, by the
  program, in its source at its position (`Sources` reads the source: the
  package's evidence, the run's folder, a Work item's or package's files);
- `objects_of` and `arrange`: a script slide turned into presentation/1
  objects by program, and the first of JARVIS's named layouts in which every
  object fits its slot by deck_capacity (`fit_problems`, run as the script
  is written, so a script that cannot fit is refused before the deck);
- `apply`: the deck step puts the script's objects on each deck slide, the
  author's layout and slots kept where they hold them (the author only
  arranges), and `unmatched` says any deck object that is not the script's;
- `script_edits`: a deck owner's edit of anything built from the script
  turned into the same edit of the script, by each built word's origin in
  it (`Built.origins`), so the script stays the one source of what a slide
  shows and the slide is built from it again.

A slide that asks a decision is the deck's decision slide, which shows the
question and its options only (presentation/1): when its script slide shows
anything beyond plain words (a visual, a picture, a table, Tim's words),
JARVIS builds those as a content slide just before it (`shown_id`, the
decision slide's id and "-shown", `companions`), checked and matched as
every content slide is, and the outline's visual for it is checked the same
way (`plan_problems`). A picture piece's file must be one the program reads
and a picture JARVIS draws (`picture_problems`, picture_proof), and a
chart's words with a source are found at their position as its numbers are
(`value_problems`).

A diagram's forms are presentation/1's diagram kinds (flow, cycle,
hierarchy, timeline): the script gives its nodes, links and groups, and
diagram_layout lays it out and says what breaks its kind's rules.
"""
from __future__ import annotations

import copy
import itertools
import re
from pathlib import Path
from typing import Optional

from jarvis import deck_capacity, diagram_layout, layout_spec, picture_proof, presentation_data
from jarvis.presentation_schema import (DIAGRAM_KINDS, LINK_STYLES, NODE_SHAPES, TONES,
                                        ContentSlide, Dataset)

# ── what the outline and the script name ─────────────────────────────────

# Every form a slide's visual can take: one the deck can draw.
TEXT, STEPS, FIGURES, CALLOUT, COMPARISON, TABLE, PICTURE = (
    "text", "steps", "figures", "callout", "comparison", "table", "picture")
CHARTS = {"bar_chart": "bar", "line_chart": "line", "scatter_chart": "scatter"}
# A diagram's forms are presentation/1's diagram kinds, by their own words.
DIAGRAMS = DIAGRAM_KINDS
FORMS = (TEXT, STEPS, FIGURES, CALLOUT, COMPARISON, TABLE, *CHARTS, *DIAGRAMS, PICTURE)
FORM_WORDS = ("text: words where the words are the point; steps: a sequence, numbered; "
              "figures: one to four key numbers, each with its source; callout: one warning, "
              "finding or bottom line set apart; comparison: two to four things side by side; "
              "table: words in rows and columns; bar_chart, line_chart, scatter_chart: numbers "
              "drawn from a dataset of the evidence's figures, each value with its source; "
              "flow, cycle, hierarchy, timeline: a diagram JARVIS lays out (a process with its "
              "choices, a loop, a structure, a schedule), its nodes and links; "
              "picture: a picture by path")

# The script's typed visuals beside its text, his_words, picture and table.
CHART, DIAGRAM = "chart", "diagram"
VISUAL_KINDS = (STEPS, FIGURES, CALLOUT, COMPARISON, CHART, DIAGRAM)
# What the deck draws at most (presentation_schema).
MAX_STEPS, MAX_FIGURES, MAX_SERIES, MAX_ROWS = 8, 4, 4, 50
ID = r"^[a-z][a-z0-9-]{0,79}$"

_TEXT = {"type": "string"}
_WORDS = {"type": "string", "minLength": 1, "pattern": r"\S"}
POSITION_WORDS = ("Where in that source the value is: 'line N', 'lines N-M' or 'characters "
                  "A-B' (0-based, the end not included).")


def _object(properties: dict, description: str = "") -> dict:
    out = {"type": "object", "properties": properties, "required": list(properties),
           "additionalProperties": False}
    if description:
        out["description"] = description
    return out


def _array(items: dict, description: str, least: int = 0) -> dict:
    out: dict = {"type": "array", "items": items, "description": description}
    if least:
        out["minItems"] = least
    return out


def _kind(name: str, description: str) -> dict:
    return {"type": "string", "enum": [name], "description": description}


SOURCED = {"source": {**_WORDS, "description": "Its source path."},
           "position": {**_WORDS, "description": POSITION_WORDS}}

PIECES = {
    STEPS: _object({
        "kind": _kind(STEPS, "A sequence as numbered steps."),
        "steps": _array(_object({"title": _WORDS, "says": {
            **_TEXT, "description": "What the step says, or empty."}}),
            f"The steps in order, at most {MAX_STEPS}.", least=1)}),
    FIGURES: _object({
        "kind": _kind(FIGURES, "Key figures: one to four numbers side by side."),
        "figures": _array(_object({
            "value": {**_WORDS, "description": "The number as it is read, units and all."},
            "label": {**_WORDS, "description": "What it counts."}, **SOURCED}),
            f"The figures, at most {MAX_FIGURES}, each with its source.", least=1)}),
    CALLOUT: _object({
        "kind": _kind(CALLOUT, "One warning, finding or bottom line set apart."),
        "label": {**_TEXT, "description": "A short label above it, or empty."},
        "says": _WORDS}),
    COMPARISON: _object({
        "kind": _kind(COMPARISON, "Two to four things side by side."),
        "columns": _array(_object({"title": _WORDS, "points": _array(
            _WORDS, "What it says, point by point.")}), "The columns, two to four.",
            least=2)}),
    CHART: _object({
        "kind": _kind(CHART, "Numbers drawn as a chart from a dataset of the evidence's "
                             "figures."),
        "chart": {"type": "string", "enum": ["bar", "line", "scatter"]},
        "text": {**_WORDS, "description": "What the chart shows, in words."},
        "columns": _array(_object({
            "id": {"type": "string", "pattern": ID, "description": "Lower case letters, "
                                                                     "digits and dashes."},
            "label": _WORDS, "type": {"type": "string", "enum": ["number", "text"]},
            "units": {**_TEXT, "description": "Its units, or empty."}}),
            "The dataset's columns.", least=2),
        "x": {**_WORDS, "description": "The id of the column along the bottom."},
        "y": _array(_WORDS, f"The ids of the number columns drawn, one to {MAX_SERIES}.",
                    least=1),
        "rows": _array(_object({
            "id": {"type": "string", "pattern": ID},
            "values": _array(_object({
                "column": _WORDS,
                "value": {**_WORDS, "description": "The value as it is written in its "
                                                   "source."},
                "source": {**_TEXT, "description": "Its source path; for a number column "
                                                   "always."},
                "position": {**_TEXT, "description": POSITION_WORDS + " For a number "
                                                                      "column always."}}),
                "One value for each column.", least=1)}),
            f"The dataset's rows, at most {MAX_ROWS}.", least=1)}),
    DIAGRAM: _object({
        "kind": _kind(DIAGRAM, "A diagram JARVIS lays out: its nodes, the links between them "
                               "and any groups."),
        "diagram": {"type": "string", "enum": list(DIAGRAM_KINDS),
                    "description": "flow: steps and choices joined by links; cycle: a ring "
                                   "through its nodes in order; hierarchy: a tree from one root; "
                                   "timeline: its nodes in order along one line."},
        "text": {**_WORDS, "description": "What the diagram shows, in words."},
        "nodes": _array(_object({
            "id": {"type": "string", "pattern": ID},
            "label": _WORDS,
            "shape": {"type": "string", "enum": list(NODE_SHAPES)},
            "tone": {"type": "string", "enum": list(TONES)},
            "when": {**_TEXT, "description": "A timeline's node: when; empty otherwise."}}),
            "The nodes, two to 24.", least=2),
        "links": _array(_object({
            "from": _WORDS, "to": _WORDS,
            "label": {**_TEXT, "description": "Words on the link, or empty."},
            "style": {"type": "string", "enum": ["", *LINK_STYLES],
                      "description": "Empty for the kind's own."}}),
            "The links, from one node's id to another's."),
        "groups": _array(_object({
            "id": {"type": "string", "pattern": ID}, "label": _WORDS,
            "nodes": _array(_WORDS, "The ids of its nodes.", least=1)}),
            "Lanes or phases, or none.")}),}


# ── reading numbers and positions ────────────────────────────────────────

_NUMBER = re.compile(r"[-\u2212]?(?:\d{1,3}(?: \d{3})+(?![\d,.])|\d(?:[\d,.\u00a0\u202f']*\d)?)")


def number(text: str) -> Optional[float]:
    """The one number in `text` as a value (12,000 / 12 000 / 12.5 / 12,5 /
    -3 % ...), or None when it holds none or more than one."""
    found = _NUMBER.findall(str(text))
    return _value(found[0]) if len(found) == 1 else None


def _value(token: str) -> Optional[float]:
    sign = -1 if token[0] in "-\u2212" else 1
    token = token.lstrip("-\u2212")
    for mark in ("\u00a0", "\u202f", "'", " "):
        token = token.replace(mark, "")
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        token = token.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in token or "." in token:
        mark = "," if "," in token else "."
        head, *groups = token.split(mark)
        if len(groups) > 1 or (len(groups[0]) == 3 and mark == ","):
            if all(len(g) == 3 for g in groups):
                token = "".join([head, *groups])
            else:
                return None
        else:
            token = f"{head}.{groups[0]}"
    try:
        return sign * float(token)
    except ValueError:
        return None


def numbers_in(text: str) -> list[float]:
    """Every number in `text`; one written with a separator also as it
    reads without it (1.234 is 1.234 and, in Spanish, 1234)."""
    out = []
    for token in _NUMBER.findall(text):
        for reading in (token, token.replace(",", "").replace(".", "")):
            found = _value(reading)
            if found is not None and found not in out:
                out.append(found)
    return out


_LINES = re.compile(r"^\s*(?:lines?|l\.?)\s*(\d+)(?:\s*(?:-|–|to|through)\s*(\d+))?\s*$",
                    re.I)
_CHARS = re.compile(r"^\s*(?:characters?|chars?|offsets?)\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*$",
                    re.I)


def span(text: str, position: str) -> Optional[str]:
    """The stretch of `text` a position names, or None when it names none
    the program can read, or one past the end."""
    found = _LINES.match(position or "")
    if found:
        first = int(found.group(1))
        last = int(found.group(2) or first)
        lines = text.splitlines()
        if first < 1 or last < first or last > len(lines):
            return None
        return "\n".join(lines[first - 1:last])
    found = _CHARS.match(position or "")
    if found:
        start, end = int(found.group(1)), int(found.group(2))
        return text[start:end] if 0 <= start < end <= len(text) else None
    return None


class Sources:
    """A source path's text, read by the program: an engine path under one
    of `mapped` (its prefix and the folder it is on disk), a path below one
    of `folders` (absolute, or relative to each in turn). Nothing else is
    read."""

    LIMIT = 8 * 1024 * 1024

    def __init__(self, mapped: tuple = (), folders: tuple = ()):
        self.mapped = tuple((str(p).rstrip("/") + "/", Path(f)) for p, f in mapped)
        self.folders = tuple(Path(f) for f in folders)

    def file(self, path: str) -> Optional[Path]:
        """The file a source path names, or None when it names none the
        program reads."""
        found = self._file(str(path or "").strip())
        return None if found is None or ".." in found.parts else found

    def _file(self, path: str) -> Optional[Path]:
        for prefix, folder in self.mapped:
            if path.startswith(prefix):
                return folder / path[len(prefix):]
        given = Path(path)
        if given.is_absolute():
            return given if any(_below(given, f) for f in self.folders) else None
        for folder in self.folders:
            if (folder / given).is_file() and _below(folder / given, folder):
                return folder / given
        return None

    def text(self, path: str) -> Optional[str]:
        found = self.file(path)
        if found is None:
            return None
        try:
            if not found.is_file() or found.stat().st_size > self.LIMIT:
                return None
            return found.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _below(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (OSError, ValueError):
        return False


# ── the program's check of a script slide's visuals ──────────────────────

def piece_problems(piece: dict, at: str) -> list[str]:
    """What a typed visual cannot be without, beyond its form."""
    kind = piece.get("kind")
    out: list[str] = []
    if kind == STEPS and len(piece["steps"]) > MAX_STEPS:
        out.append(f"{at}, steps, has {len(piece['steps'])}; the deck draws at most "
                   f"{MAX_STEPS}: split them across slides.")
    elif kind == FIGURES:
        if len(piece["figures"]) > MAX_FIGURES:
            out.append(f"{at}, figures, has {len(piece['figures'])}; the deck draws at most "
                       f"{MAX_FIGURES}.")
        for n, figure in enumerate(piece["figures"]):
            if number(figure["value"]) is None:
                out.append(f"{at}.figures[{n}] is {figure['value']!r}: a key figure shows one "
                           f"number.")
    elif kind == COMPARISON and len(piece["columns"]) > 4:
        out.append(f"{at}, a comparison, has {len(piece['columns'])} columns; at most 4.")
    elif kind == CHART:
        out += _chart_problems(piece, at)
    elif kind == DIAGRAM:
        ids = [n["id"] for n in piece["nodes"]]
        if len(set(ids)) != len(ids):
            out.append(f"{at}, a diagram: each node has its own id.")
        for name, most in (("nodes", 24), ("links", 40), ("groups", 8)):
            if len(piece[name]) > most:
                out.append(f"{at}, a diagram, has {len(piece[name])} {name}; at most {most}: "
                           f"split it across slides.")
    return out


def _chart_problems(piece: dict, at: str) -> list[str]:
    columns = {c["id"]: c for c in piece["columns"]}
    out = []
    if len(columns) != len(piece["columns"]):
        out.append(f"{at}, a chart: each column has its own id.")
    for name in [piece["x"], *piece["y"]]:
        if name not in columns:
            out.append(f"{at}, a chart, names the column {name!r}, which it does not have.")
    if out:
        return out
    if len(piece["y"]) > MAX_SERIES or len(set(piece["y"])) != len(piece["y"]) \
            or piece["x"] in piece["y"]:
        out.append(f"{at}, a chart, draws one to {MAX_SERIES} columns, each once, none its x.")
    if any(columns[name]["type"] != "number" for name in piece["y"]):
        out.append(f"{at}, a chart: every column it draws (y) holds numbers.")
    if piece["chart"] == "scatter" and columns[piece["x"]]["type"] != "number":
        out.append(f"{at}, a scatter chart: its x column holds numbers.")
    if len(piece["rows"]) > MAX_ROWS:
        out.append(f"{at}, a chart, has {len(piece['rows'])} rows; at most {MAX_ROWS}.")
    ids = [r["id"] for r in piece["rows"]]
    if len(set(ids)) != len(ids):
        out.append(f"{at}, a chart: each row has its own id.")
    for r, row in enumerate(piece["rows"]):
        given = [v["column"] for v in row["values"]]
        if sorted(given) != sorted(columns):
            out.append(f"{at}.rows[{r}] gives values for {', '.join(given)}; the chart's "
                       f"columns are {', '.join(columns)}, one value each.")
            continue
        for v, value in enumerate(row["values"]):
            if columns[value["column"]]["type"] != "number":
                continue
            where = f"{at}.rows[{r}].values[{v}]"
            if number(value["value"]) is None:
                out.append(f"{where} is {value['value']!r}, not one number.")
            if not value["source"].strip() or not value["position"].strip():
                out.append(f"{where}, a number, has no source path and position.")
    return out


def sourced_values(slide: dict) -> list[tuple[str, dict]]:
    """Every value a slide's visuals show with its source: (where, value)
    with `value`, `source`, `position`, and `words` True for a chart's text
    value (found as words) rather than a number. Every number has its
    source; a text value is here when it names one."""
    out = []
    for i, piece in enumerate(slide.get("on_screen") or []):
        if piece.get("kind") == FIGURES:
            out += [(f"on_screen[{i}].figures[{n}]", f) for n, f in enumerate(piece["figures"])]
        elif piece.get("kind") == CHART:
            kinds = {c["id"]: c["type"] for c in piece["columns"]}
            for r, row in enumerate(piece["rows"]):
                for v, value in enumerate(row["values"]):
                    where = f"on_screen[{i}].rows[{r}].values[{v}]"
                    if kinds.get(value["column"]) == "number":
                        out.append((where, value))
                    elif str(value.get("source") or "").strip():
                        out.append((where, {**value, "words": True}))
    return out


def _folded(text: str) -> str:
    return " ".join(str(text).casefold().split())


def value_problems(slide: dict, at: str, sources: Optional[Sources]) -> list[str]:
    """Each number a chart or key figure shows, and each of a chart's words
    that names its source, found by the program in its source at its
    position, as the facts of the evidence are read: the source read, the
    position one the program reads, and the number (or the words, case and
    spacing aside) there."""
    if sources is None:
        return []
    out, read = [], {}
    for where, value in sourced_values(slide):
        said = f"{at} {where} ({value['value']!r})"
        path = value["source"].strip()
        if path not in read:
            read[path] = sources.text(path)
        text = read[path]
        if text is None:
            out.append(f"{said}: its source {path!r} cannot be read; cite the evidence file "
                       f"the value is in.")
            continue
        stretch = span(text, value["position"])
        if stretch is None:
            out.append(f"{said}: its position {value['position']!r} is not one the program "
                       f"reads in {path}. {POSITION_WORDS}")
            continue
        if value.get("words"):
            if not _folded(value["value"]) or _folded(value["value"]) not in _folded(stretch):
                out.append(f"{said}: the words are not in {path} at {value['position']}.")
            continue
        wanted = number(value["value"])
        if wanted is None or not any(abs(n - wanted) <= 1e-9 * max(1, abs(wanted))
                                     for n in numbers_in(stretch)):
            out.append(f"{said}: the number is not in {path} at {value['position']}.")
    return out


def picture_problems(slide: dict, at: str, sources: Optional[Sources]) -> list[str]:
    """Each picture piece's file: one the program reads (`Sources`) and a
    picture JARVIS draws, walked whole (picture_proof.prove)."""
    if sources is None:
        return []
    out = []
    for i, piece in enumerate(slide.get("on_screen") or []):
        if piece.get("kind") != PICTURE:
            continue
        path = str(piece.get("path") or "").strip()
        said = f"{at} on_screen[{i}], a picture ({path!r})"
        found = sources.file(path)
        if found is None or not found.is_file():
            out.append(f"{said}: there is no such file among the evidence and the run's "
                       f"folder; give the path of a picture that is there.")
            continue
        try:
            picture_proof.prove(found)
        except picture_proof.PictureProblem as problem:
            out.append(f"{said} cannot be drawn: {problem}.")
        except OSError as error:
            out.append(f"{said} cannot be read: {error}.")
    return out


# What a script slide's author is told when its visual is not the one its
# outline slide names: give it, or change the outline's form (the form
# revised after the outline was signed off, with why; the structure owner
# signs off the new form).
FORM_CHANGE_WORDS = ("or, if another form carries the point better, change the outline's "
                     "visual for this slide in `forms` with why: JARVIS changes the outline "
                     "and the structure owner signs off the new form.")


def plan_problems(script: dict, outline: Optional[dict]) -> list[str]:
    """Each slide of the script shows the visual its outline slide names
    (text: its words; a chart form: a chart of that kind; a diagram form: a
    diagram of that kind). A slide that asks a decision is checked the same
    way, its decision's own words counting as text."""
    if not outline:
        return []
    planned = {s.get("id"): s.get("visual") for s in outline.get("slides") or []
               if isinstance(s, dict)}
    out = []
    for slide in script.get("slides") or []:
        form = planned.get(slide["id"])
        if not form:
            continue
        pieces = slide.get("on_screen") or []
        kinds = {p.get("kind") for p in pieces}
        shown = (bool(kinds & {TEXT, "his_words"}) or bool(slide.get("decision"))
                 if form == TEXT
                 else any(p.get("kind") == CHART and p.get("chart") == CHARTS[form]
                          for p in pieces) if form in CHARTS
                 else any(p.get("kind") == DIAGRAM and p.get("diagram") == form
                          for p in pieces) if form in DIAGRAMS
                 else form in kinds)
        if not shown:
            out.append(f"slide {slide['id']}: the outline shows its point as {form}; its "
                       f"on_screen has no {form}. Give it that visual with its content, "
                       + FORM_CHANGE_WORDS)
    return out


# ── a script slide as presentation/1 objects ─────────────────────────────

SCRIPT_REFERENCE = "script"


def _ref(slide_id: str) -> dict:
    return {"reference": SCRIPT_REFERENCE, "member": slide_id}


Origin = tuple  # (the script's field, the offset of the built words in it, or None: all of it)


def _lines(text: str, field: str) -> list[tuple[str, str, tuple]]:
    """A text piece's lines as paragraphs: (role, words, origin), each
    line's words at their offset in the piece's text."""
    out, at = [], 0
    for line in str(text or "").splitlines(keepends=True):
        lead = len(line) - len(line.lstrip())
        stripped = line.strip()
        here = at + lead
        at += len(line)
        if not stripped:
            continue
        numbered = re.match(r"^\d+[.)]\s+", stripped)
        if stripped[:2] in ("- ", "* ", "• "):
            rest = stripped[2:]
            words, role = rest.strip(), "bullet"
            here += 2 + len(rest) - len(rest.lstrip())
        elif numbered:
            words, role = stripped[numbered.end():], "numbered"
            here += numbered.end()
        else:
            words, role = stripped, "paragraph"
        out.append((role, words, (field, here)))
    return out


def _paragraphs(oid: str, pieces: list[tuple[str, dict]]
                ) -> tuple[list[dict], dict[tuple, Origin]]:
    """The words pieces (each with its field) as one text object's
    paragraphs, and each run's origin in the script by its path in the
    object."""
    out, origins = [], {}
    for field, piece in pieces:
        if piece["kind"] == "his_words":
            lines = [("paragraph", piece["text"], (field, 0), "his-words")]
        else:
            lines = [(role, words, origin, "normal")
                     for role, words, origin in _lines(piece.get("text"), field)]
        for role, words, origin, emphasis in lines:
            n = len(out) + 1
            origins[("paragraphs", n - 1, "runs", 0, "text")] = origin
            out.append({"id": f"{oid}-p{n}", "role": role,
                        "runs": [{"id": f"{oid}-p{n}-r1", "text": words, "emphasis": emphasis}]})
    return out, origins


def _dataset(piece: dict, ident: str, sid: str, at: str
             ) -> tuple[dict, dict[str, str], dict[tuple, Origin]]:
    names = {c["id"]: f"{ident}-c{n}" for n, c in enumerate(piece["columns"], 1)}
    kinds = {c["id"]: c["type"] for c in piece["columns"]}
    columns, origins = [], {}
    for k, c in enumerate(piece["columns"]):
        column = {"id": names[c["id"]], "label": c["label"], "type": c["type"]}
        origins[("columns", k, "label")] = (f"{at}.columns[{k}].label", 0)
        if str(c.get("units") or "").strip():
            column["units"] = c["units"]
            origins[("columns", k, "units")] = (f"{at}.columns[{k}].units", 0)
        columns.append(column)
    rows = []
    for n, row in enumerate(piece["rows"], 1):
        values = {}
        for v, value in enumerate(row["values"]):
            raw, name = value["value"], names[value["column"]]
            number_ = kinds[value["column"]] == "number"
            values[name] = number(raw) if number_ else raw
            origins[("rows", n - 1, "values", name)] = (
                f"{at}.rows[{n - 1}].values[{v}].value", None if number_ else 0)
        rows.append({"id": f"{ident}-r{n}", "values": values})
    return ({"id": ident, "source": _ref(sid), "columns": columns, "rows": rows}, names,
            origins)


def _lead(text: str) -> int:
    return len(text) - len(text.lstrip())


class Built:
    """A script slide as presentation/1: its objects in reading order (no
    slots), the datasets and assets they need, and its narration; and the
    origin of each built word in the script (`origins`: by the object's or
    dataset's id and the word's path in it, the script slide's field and
    where in it), by which a deck owner's edit of a built word is the same
    edit of the script (`script_edits`). `script_id` is the script slide it
    is built from (a decision slide's for the content slide shown before
    it)."""

    def __init__(self, slide: dict, script_id: Optional[str] = None):
        self.slide = slide
        self.id = slide["id"]
        self.script_id = script_id or self.id
        self.objects: list[dict] = []
        self.datasets: list[dict] = []
        self.assets: list[dict] = []
        self.columns = 0           # how many of the objects are a comparison's columns
        self.origins: dict[tuple[str, tuple], Origin] = {("", ("title",)): ("title", 0)}
        self._build()

    def _add(self, obj: dict) -> dict:
        obj = {"id": f"{self.id}-v{len(self.objects) + 1}", **obj, "source": _ref(self.id)}
        self.objects.append(obj)
        return obj

    def _from(self, holder: str, path: tuple, field: str, offset: Optional[int] = 0) -> None:
        self.origins[(holder, path)] = (field, offset)

    def _build(self) -> None:
        words: list[tuple[str, dict]] = []

        def flush():
            if words:
                obj = self._add({"type": "text"})
                obj["paragraphs"], origins = _paragraphs(obj["id"], words)
                if not obj["paragraphs"]:
                    self.objects.pop()
                for path, origin in origins.items():
                    self._from(obj["id"], path, *origin)
                words.clear()

        for i, piece in enumerate(self.slide.get("on_screen") or []):
            kind, at = piece.get("kind"), f"on_screen[{i}]"
            if kind in (TEXT, "his_words"):
                words.append((f"{at}.text", piece))
                continue
            flush()
            if kind == PICTURE:
                obj = self._add({"type": "image"})
                asset = f"{obj['id']}-picture"
                obj.update(asset=asset, alt=piece.get("text") or "A picture.", fit="contain")
                if piece.get("text"):
                    self._from(obj["id"], ("alt",), f"{at}.text")
                self.assets.append({"id": asset, "path": Path(str(piece["path"])).name,
                                    "from": piece["path"], "source": _ref(self.id)})
            elif kind == TABLE:
                rows = [(r, row) for r, row in enumerate(piece.get("rows") or []) if row]
                obj = self._add({"type": "table"})
                if len(rows) > 1:
                    (r0, head), rows = rows[0], rows[1:]
                    obj["head"] = head
                    for c in range(len(head)):
                        self._from(obj["id"], ("head", c), f"{at}.rows[{r0}][{c}]")
                obj["rows"] = [{"id": f"{obj['id']}-r{n}", "cells": row}
                               for n, (_, row) in enumerate(rows, 1)]
                for n, (r, row) in enumerate(rows):
                    for c in range(len(row)):
                        self._from(obj["id"], ("rows", n, "cells", c), f"{at}.rows[{r}][{c}]")
                obj["alt"] = " ".join(obj.get("head") or rows[0][1])
            elif kind == STEPS:
                obj = self._add({"type": "steps"})
                obj["items"] = [{"id": f"{obj['id']}-s{n}", "title": s["title"],
                                 **({"says": s["says"]} if str(s.get("says") or "").strip()
                                    else {})}
                                for n, s in enumerate(piece["steps"], 1)]
                for n, item in enumerate(obj["items"]):
                    for key in ("title", "says"):
                        if key in item:
                            self._from(obj["id"], ("items", n, key), f"{at}.steps[{n}].{key}")
            elif kind == FIGURES:
                obj = self._add({"type": "figures"})
                obj["items"] = [{"id": f"{obj['id']}-f{n}", "value": f["value"],
                                 "label": f["label"]}
                                for n, f in enumerate(piece["figures"], 1)]
                for n in range(len(obj["items"])):
                    for key in ("value", "label"):
                        self._from(obj["id"], ("items", n, key), f"{at}.figures[{n}].{key}")
            elif kind == CALLOUT:
                obj = self._add({"type": "callout"})
                if str(piece.get("label") or "").strip():
                    obj["label"] = piece["label"]
                    self._from(obj["id"], ("label",), f"{at}.label")
                obj["says"] = piece["says"]
                self._from(obj["id"], ("says",), f"{at}.says")
            elif kind == COMPARISON:
                for k, column in enumerate(piece["columns"]):
                    obj = self._add({"type": "text"})
                    obj["paragraphs"], _ = _paragraphs(obj["id"], [("", {"kind": TEXT, "text": "\n".join(
                        [column["title"], *(f"- {p}" for p in column["points"])])})])
                    obj["paragraphs"][0]["role"] = "heading"
                    self.columns += 1
                    parts = [column["title"], *column["points"]]
                    if len(obj["paragraphs"]) == len(parts):
                        fields = [f"{at}.columns[{k}].title",
                                  *(f"{at}.columns[{k}].points[{p}]"
                                    for p in range(len(column["points"])))]
                        for n, (field, part) in enumerate(zip(fields, parts)):
                            self._from(obj["id"], ("paragraphs", n, "runs", 0, "text"), field,
                                       _lead(part))
            elif kind == CHART:
                obj = self._add({"type": "chart"})
                dataset, names, origins = _dataset(
                    piece, f"{self.id}-d{len(self.datasets) + 1}", self.id, at)
                self.datasets.append(dataset)
                for path, origin in origins.items():
                    self._from(dataset["id"], path, *origin)
                obj.update(kind=piece["chart"], data={"dataset": dataset["id"]},
                           x=names[piece["x"]], y=[names[y] for y in piece["y"]],
                           alt=piece["text"])
                self._from(obj["id"], ("alt",), f"{at}.text")
            elif kind == DIAGRAM:
                obj = self._add({"type": "diagram"})
                ids = {n["id"]: f"{obj['id']}-{n['id']}" for n in piece["nodes"]}
                obj.update(kind=piece["diagram"], nodes=[
                    {"id": ids[n["id"]], "label": n["label"], "shape": n["shape"],
                     "tone": n["tone"], **({"when": n["when"]} if str(n.get("when") or "").strip()
                                          else {})} for n in piece["nodes"]],
                    links=[{"id": f"{obj['id']}-l{k}", "from": ids.get(l["from"], l["from"]),
                            "to": ids.get(l["to"], l["to"]),
                            **({"label": l["label"]} if str(l.get("label") or "").strip() else {}),
                            **({"style": l["style"]} if l.get("style") else {})}
                           for k, l in enumerate(piece["links"], 1)],
                    groups=[{"id": f"{obj['id']}-{g['id']}", "label": g["label"],
                             "nodes": [ids.get(n, n) for n in g["nodes"]]}
                            for g in piece["groups"]],
                    alt=piece["text"])
                self._from(obj["id"], ("alt",), f"{at}.text")
                for n, node in enumerate(obj["nodes"]):
                    for key in ("label", "when"):
                        if key in node:
                            self._from(obj["id"], ("nodes", n, key), f"{at}.nodes[{n}].{key}")
                for n, link in enumerate(obj["links"]):
                    if "label" in link:
                        self._from(obj["id"], ("links", n, "label"), f"{at}.links[{n}].label")
                for n in range(len(obj["groups"])):
                    self._from(obj["id"], ("groups", n, "label"), f"{at}.groups[{n}].label")
        flush()

    @property
    def narration(self) -> list[dict]:
        return [{"id": f"{self.id}-say", "say": self.slide["narration"],
                 "focus": [self.objects[0]["id"]] if self.objects else []}]

    def slide_of(self, layout: str, slots: dict[str, str],
                 narration: Optional[list[dict]] = None) -> dict:
        objects = [{"id": o["id"], "type": o["type"], "slot": slots[o["id"]],
                    **{k: v for k, v in o.items() if k not in ("id", "type")}}
                   for o in self.objects]
        return {"id": self.id, "type": "content", "title": self.slide["title"],
                "layout": layout, "objects": objects,
                "readingOrder": [o["id"] for o in self.objects],
                "narration": narration or self.narration, "notes": ""}


def objects_of(slide: dict) -> Built:
    return Built(slide)


# Layouts tried in this order: the plainest that holds the objects first.
ORDER = ("title-and-body", "picture-and-words", "table", "two-pictures", "comparison")


def _language() -> str:
    try:
        from jarvis import spoken_words
        return spoken_words.active_language()
    except Exception:  # noqa: BLE001 - the script check never fails on the language
        return "en"


def candidates(built: Built):
    """Every (layout, slots) that takes the objects, one object a slot, each
    slot taking its type, the layout's required slots filled and its
    columns in order, plainest first."""
    accepts = layout_spec.spec()["accepts"]
    named = layout_spec.spec()["layouts"]
    objects = built.objects
    order = ("comparison",) if built.columns else ORDER
    for layout in order:
        slots = layout_spec.slots_of(layout)
        columns = named[layout].get("columns", {}).get("slots", [])
        for chosen in itertools.permutations(slots, len(objects)):
            if any(o["type"] not in accepts[s] for o, s in zip(objects, chosen)):
                continue
            if not set(named[layout]["required"]) <= set(chosen):
                continue
            used = [s for s in columns if s in chosen]
            if used != columns[:len(used)]:
                continue
            if built.columns and sum(1 for s in chosen if s in columns) != built.columns:
                continue
            yield layout, {o["id"]: s for o, s in zip(objects, chosen)}


def _fits(built: Built, slide: dict) -> list[str]:
    """What does not fit when `slide` is drawn (deck_capacity), or a reason
    it cannot be read as presentation/1."""
    try:
        model = ContentSlide.model_validate(slide)
        datasets = {d["id"]: Dataset.model_validate(d) for d in built.datasets}
        for dataset in datasets.values():
            presentation_data.check_dataset(dataset)
        for obj in model.objects:
            if obj.type == "chart":
                presentation_data.resolve_chart(obj, datasets, _language())
            elif obj.type == "table":
                presentation_data.expand_table(obj, datasets, _language())
        broken = [message for obj in model.objects if obj.type == "diagram"
                  for _, message in diagram_layout.structure(obj)]
        if broken:
            return broken
    except (ValueError, presentation_data.DataProblem) as error:
        return [str(error).splitlines()[0]]
    return [message for _, message in deck_capacity.problems(model)]


def arrange(built: Built) -> tuple[Optional[tuple[str, dict]], list[str]]:
    """The first layout and slots in which every object fits its slot, or
    (None, the problems of the arrangement nearest to fitting)."""
    best: Optional[list[str]] = None
    for layout, slots in candidates(built):
        found = _fits(built, built.slide_of(layout, slots))
        if not found:
            return (layout, slots), []
        if best is None or len(found) < len(best):
            best = found
    if best is None:
        types = ", ".join(o["type"] for o in built.objects) or "nothing"
        return None, [f"its visuals ({types}) are more than any of JARVIS's layouts holds "
                      f"on one slide: split the slide, or put its words in one text piece."]
    return None, best


def fit_problems(slide: dict, at: str) -> list[str]:
    """A content slide of the script laid out by program (deck_capacity at
    the smallest frame): refused when no layout holds it. A slide that asks
    a decision is laid out the same way when it shows more than words (the
    content slide JARVIS builds before its decision slide)."""
    if slide.get("decision"):
        if not shows_more(slide):
            return []
        built = Built(_companion(slide), slide["id"])
        _, problems = arrange(built)
        return [f"{at} (what it shows before its decision) does not fit a slide: {p}"
                for p in problems]
    built = Built(slide)
    if not built.objects:
        return [f"{at} shows nothing on screen: give it its visual."]
    _, problems = arrange(built)
    return [f"{at} does not fit a slide: {p}" for p in problems]


# ── a decision slide's visuals ───────────────────────────────────────────

SHOWN = "-shown"


def shown_id(sid: str) -> str:
    """The id of the content slide JARVIS builds before a decision slide."""
    return f"{sid}{SHOWN}"


def shows_more(slide: dict) -> bool:
    """A slide that asks a decision and shows more than plain words: a
    visual, a picture, a table or Tim's own words, which the decision slide
    (its question and options) cannot hold."""
    return bool(slide.get("decision")) and any(
        p.get("kind") != TEXT for p in slide.get("on_screen") or [] if isinstance(p, dict))


def _companion(slide: dict) -> dict:
    return {**slide, "id": shown_id(slide["id"]), "decision": None}


def companions(script: dict) -> list[tuple[str, dict]]:
    """Every slide JARVIS builds from the script, in order: (the script
    slide's id, the slide to build): each content slide, and before each
    decision slide that shows more than words, the content slide showing
    it."""
    out = []
    for s in script.get("slides") or []:
        if not isinstance(s, dict):
            continue
        if not s.get("decision"):
            out.append((s["id"], s))
        elif shows_more(s):
            out.append((s["id"], _companion(s)))
    return out


def deck_ids(script: dict) -> list[str]:
    """The deck's slide ids, in order: one a script slide, with the content
    slide shown before a decision slide that shows more than words."""
    out = []
    for s in script.get("slides") or []:
        if isinstance(s, dict):
            out += ([shown_id(s["id"])] if shows_more(s) else []) + [s["id"]]
    return out


def drawn_on(slide: dict) -> str:
    """The deck slide a script slide's on-screen pieces are drawn on."""
    return shown_id(slide["id"]) if shows_more(slide) else slide["id"]


# ── the deck step ────────────────────────────────────────────────────────

def _built(script: dict) -> list[Built]:
    return [Built(slide, sid) for sid, slide in companions(script)]


def built_slides(script: dict) -> dict:
    """Every slide JARVIS builds from the script (each content slide, and
    the slide shown before a decision that shows more than words), laid
    out in the first layout that fits: {"slides", "datasets", "assets"}."""
    out: dict = {"slides": [], "datasets": [], "assets": []}
    for built in _built(script):
        if not built.objects:
            continue
        arranged, _ = arrange(built)
        layout, slots = arranged or next(candidates(built), (ORDER[0], {}))
        if not arranged and len(slots) != len(built.objects):
            continue
        out["slides"].append(built.slide_of(layout, slots))
        out["datasets"] += built.datasets
        out["assets"] += [{k: v for k, v in a.items() if k != "from"} for a in built.assets]
    return out


def _their_slots(slide: dict, built: Built) -> dict[str, Optional[str]]:
    """The slot the author gave each built object: its object of the same
    id, else its next object of the same type, in order."""
    theirs = [o for o in slide.get("objects") or [] if isinstance(o, dict)]
    claimed: set[int] = set()
    out: dict[str, Optional[str]] = {}
    for obj in built.objects:
        found = next((n for n, o in enumerate(theirs) if o.get("id") == obj["id"]), None)
        if found is None or found in claimed:
            found = next((n for n, o in enumerate(theirs) if n not in claimed
                          and o.get("type") == obj["type"]
                          and not any(b["id"] == o.get("id") for b in built.objects)), None)
        if found is not None:
            claimed.add(found)
        out[obj["id"]] = theirs[found].get("slot") if found is not None else None
    return out


def _held(layout, slots: dict[str, Optional[str]]) -> bool:
    """The author gave every object a slot in a layout: its arrangement
    stands, and the build says what in it does not hold."""
    return isinstance(layout, str) and all(isinstance(v, str) and v for v in slots.values())


def apply(source: dict, script: dict, deck_folder: Optional[Path] = None,
          sources: Optional[Sources] = None, revision: str = "") -> dict:
    """The deck source with the script's objects on each of its content
    slides (and the slide shown before a decision that shows more than
    words, put in before it when missing), by program: the author's layout and each object's slot kept
    where the author gave every object one (its object of the same id, else
    of the same type in order; the build says what does not hold), else
    JARVIS's arrangement; the author's narration cues kept, each lighting
    only what the slide shows; the title the script's. The datasets and pictures
    they need are added (a picture copied beside the source), and the
    script itself is the reference every object names."""
    source = copy.deepcopy(source)
    slides = {s.get("id"): s for s in source.get("slides") or [] if isinstance(s, dict)}
    datasets = [d for d in source.get("datasets") or [] if isinstance(d, dict)]
    assets = [a for a in source.get("assets") or [] if isinstance(a, dict)]
    touched = False
    for built in _built(script):
        slide = slides.get(built.id)
        if slide is None and built.id != built.script_id and built.script_id in slides:
            # The slide shown before a decision is JARVIS's: put in before
            # its decision slide when the author did not write it.
            order = source.setdefault("slides", [])
            slide = {"id": built.id, "type": "content"}
            order.insert(order.index(slides[built.script_id]), slide)
            slides[built.id] = slide
        if slide is None or slide.get("type") != "content":
            continue
        if not built.objects:
            continue
        layout = slide.get("layout")
        slots = _their_slots(slide, built)
        if not _held(layout, slots):
            arranged, _ = arrange(built)
            layout, slots = arranged or next(candidates(built), (ORDER[0], None))
            if slots is None:
                continue
        narration = _kept_cues(slide.get("narration"), built)
        slide.clear()
        slide.update(built.slide_of(layout, slots, narration))
        for dataset in built.datasets:
            _put(datasets, dataset)
        for asset in built.assets:
            _put(assets, {k: v for k, v in asset.items() if k != "from"})
            if deck_folder is not None:
                _copy_picture(asset, deck_folder, sources)
        touched = True
    if touched:
        for key, items in (("datasets", datasets), ("assets", assets)):
            if items or key in source:
                source[key] = items
        refs = [r for r in source.get("references") or [] if isinstance(r, dict)]
        _put(refs, {"id": SCRIPT_REFERENCE, "kind": "file", "locator": "../script.yaml",
                    "revision": revision or "passed",
                    "description": "The passed script this deck is built from: every slide's "
                                   "visual and the source path and position of each value it "
                                   "shows."})
        source["references"] = refs
    return source


def _put(items: list[dict], item: dict) -> None:
    """`item` in place of the one with its id, else added at the end (so a
    source applied again is the same, in the same order)."""
    for n, have in enumerate(items):
        if have.get("id") == item["id"]:
            items[n] = item
            return
    items.append(item)


def _targets(built: Built) -> set[str]:
    """Every id a cue may light on the built slide: each object, paragraph,
    run, item and table row, and each bar or point of a chart."""
    out: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("id"), str):
                out.add(value["id"])
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(built.objects)
    datasets = {d["id"]: d for d in built.datasets}
    for obj in built.objects:
        if obj["type"] == "chart":
            for row in datasets[obj["data"]["dataset"]]["rows"]:
                out.update(f"{obj['id']}-{row['id']}-{y}" for y in obj["y"])
        elif obj["type"] == "table":
            out.update(r["id"] for r in obj.get("rows") or [])
    return out


def _kept_cues(cues, built: Built) -> Optional[list[dict]]:
    """The author's narration cues, each lighting only what the built slide
    shows (a target it no longer has dropped, a cue left lighting nothing
    lights the first object); None when the author wrote none."""
    if not isinstance(cues, list) or not cues:
        return None
    targets, out = _targets(built), []
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        focus = [f for f in cue.get("focus") or [] if f in targets]
        out.append({**cue, "focus": focus or [built.objects[0]["id"]]})
    return out or None


def _copy_picture(asset: dict, deck_folder: Path, sources: Optional[Sources]) -> None:
    target = deck_folder / asset["path"]
    if target.is_file():
        return
    found = sources._file(str(asset["from"])) if sources else None
    if found is None:
        given = Path(str(asset["from"]))
        found = given if given.is_absolute() else None
    if found is not None and found.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(found.read_bytes())


def _content(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if k != "slot"}


def unmatched(script: dict, source: dict) -> list[str]:
    """Each object the script's visual makes that its deck slide does not
    show as the script gives it: every script piece maps to the same deck
    object, by program."""
    slides = {s.get("id"): s for s in source.get("slides") or [] if isinstance(s, dict)}
    datasets = {d.get("id"): d for d in source.get("datasets") or [] if isinstance(d, dict)}
    out = []
    for built in _built(script):
        slide = slides.get(built.id)
        if slide is None or slide.get("type") != "content":
            continue
        have = {o.get("id"): o for o in slide.get("objects") or [] if isinstance(o, dict)}
        for obj in built.objects:
            if obj["id"] not in have or _content(have[obj["id"]]) != _content(obj):
                out.append(f"Slide {built.id}'s {obj['type']} {obj['id']} is not the "
                           f"script's: JARVIS builds every visual from the script, and the "
                           f"deck only arranges its layout and slots.")
        for dataset in built.datasets:
            if datasets.get(dataset["id"]) != dataset:
                out.append(f"The dataset {dataset['id']} is not the script's chart on slide "
                           f"{built.id}.")
    return out


# ── a deck owner's edit of what is built from the script ─────────────────

def _leaves(value, path: tuple = ()) -> dict[tuple, object]:
    """Every leaf of a value by its path: a dict's by key, a list's by
    index."""
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            out.update(_leaves(v, path + (k,)))
        return out
    if isinstance(value, list):
        out = {}
        for n, v in enumerate(value):
            out.update(_leaves(v, path + (n,)))
        return out
    return {path: value}


def _written(value) -> str:
    """A number as the script writes it (a dataset holds it as a number)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _field_text(slide: dict, field: str) -> Optional[str]:
    holder = slide
    for step in re.findall(r"\.?([a-z_]+)|\[(\d+)\]", field):
        key = step[0] or int(step[1])
        try:
            holder = holder[key]
        except (KeyError, IndexError, TypeError):
            return None
    return holder if isinstance(holder, str) else None


# The deck parts JARVIS sets from the script beside the objects themselves.
_SET_BY_JARVIS = ("type", "readingOrder", "notes")


def script_edits(script: dict, edited: dict) -> tuple[list[dict], list[str]]:
    """What an edit of the deck source changed in what JARVIS builds from
    the script, as edits of the script: (each edit by its script slide and
    field, its offsets within that field's words, its old and new text; why
    a change cannot be one). Both empty: the edit changed only what the
    deck arranges (a layout, a slot, the narration's cues, the header, a
    decision). A change is compared with the deck as JARVIS builds it from
    the script with the author's arrangement kept (`apply`), and each built
    word changed is put back where it came from (`Built.origins`); a word
    added, an object removed or anything else JARVIS sets is not an edit
    of the script and is said so."""
    rebuilt = apply(edited, script)
    have = {s.get("id"): s for s in edited.get("slides") or [] if isinstance(s, dict)}
    want = {s.get("id"): s for s in rebuilt.get("slides") or [] if isinstance(s, dict)}
    have_data = {d.get("id"): d for d in edited.get("datasets") or [] if isinstance(d, dict)}
    by_id = {s["id"]: s for s in script.get("slides") or [] if isinstance(s, dict)}
    edits: list[dict] = []
    why: list[str] = []

    def change(built: Built, holder: str, path: tuple, old, new) -> None:
        origin = built.origins.get((holder, path))
        where = f"slide {built.id}, {holder or 'the slide'} {'.'.join(map(str, path))}"
        if origin is None:
            why.append(f"{where}: JARVIS sets it from the script, which says what is shown; "
                       f"change what it is made from instead.")
            return
        field, offset = origin
        text = _field_text(by_id.get(built.script_id) or {}, field)
        if text is None:
            why.append(f"{where}: its words are not in the script.")
            return
        if offset is None:
            old_words, new_words, start = text, _written(new), 0
        else:
            old_words, new_words, start = str(old), _written(new), offset
        edits.append({"slide": built.script_id, "field": field, "start": start,
                      "end": start + len(old_words), "old": old_words, "new": new_words})

    def compare(built: Built, holder: str, ours, theirs) -> None:
        ours_, theirs_ = _leaves(ours), _leaves(theirs)
        if set(ours_) != set(theirs_):
            why.append(f"slide {built.id}, {holder}: a part added or removed; JARVIS builds "
                       f"it from the script, so add or remove it there.")
            return
        for path, value in ours_.items():
            if theirs_[path] != value:
                change(built, holder, path, value, theirs_[path])

    for built in _built(script):
        theirs, ours = have.get(built.id), want.get(built.id)
        if ours is None:
            continue
        if theirs is None:
            why.append(f"slide {built.id}: JARVIS builds it from the script; it stays.")
            continue
        if theirs.get("title") != ours.get("title"):
            if isinstance(theirs.get("title"), str):
                change(built, "", ("title",), ours.get("title"), theirs["title"])
            else:
                why.append(f"slide {built.id}: its title is the script's.")
        for key in _SET_BY_JARVIS:
            if theirs.get(key) != ours.get(key):
                why.append(f"slide {built.id}, {key}: JARVIS sets it from the script.")
        their_objects = {o.get("id"): o for o in theirs.get("objects") or []
                         if isinstance(o, dict)}
        for obj in ours.get("objects") or []:
            if obj["id"] not in their_objects:
                why.append(f"slide {built.id}: {obj['id']} is built from the script; remove "
                           f"what it shows there.")
                continue
            compare(built, obj["id"], _content(obj), _content(their_objects[obj["id"]]))
        extra = [i for i in their_objects if i not in {o["id"] for o in ours.get("objects") or []}]
        if extra:
            why.append(f"slide {built.id}: {', '.join(map(str, extra))} is not the script's; "
                       f"add what it shows to the script.")
        for dataset in built.datasets:
            if have_data.get(dataset["id"]) is None:
                why.append(f"the dataset {dataset['id']} is built from the script; it stays.")
            elif have_data[dataset["id"]] != dataset:
                compare(built, dataset["id"], dataset, have_data[dataset["id"]])
    return edits, why

