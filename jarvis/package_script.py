"""The script of a briefing as structured YAML: script.yaml, script/1
(workflow.yaml swarm decided "the script's format", Tim 2026-11-20: "Yes,
make sure scripts are writen as structured yaml to allow full structure and
depth.").

A list of slides in order, each with an id, a title, what is on screen (an
ordered list of pieces: text, Tim's own words with their source, a picture
by path, a table), what JARVIS says over it, the source path and position
of every fact, the questions it answers and the decision it asks if any.
It is the approved author prompt's "each slide's words and what JARVIS says
over it" written as a format: `schema` is given to the author as the answer
schema of its script turns (the prompt's words unchanged), and `problems`
is the program's check of what it answered against that schema, nothing
more. The program writes script.yaml from the checked answer (`dump`).

What the rest of the run reads from it, by program:
- `units`: every piece of words on a slide, each at its slide and field,
  which marking scores slide by slide and the editor edits by offsets
  within (`apply_edits`; Tim's own words are quoted, never edited);
- `his_words`: each piece of Tim's own words, which the deck must draw in
  the his-words style and narrate (deck his_words_check);
- `sources`: every source path the script cites (research
  record_hits_used);
- `opening`: slide 1's words, which open the message that carries the
  package (deliver write_message).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

VERSION = "script/1"
FILE = "script.yaml"
TEXT, HIS_WORDS, PICTURE, TABLE = "text", "his_words", "picture", "table"
KINDS = (TEXT, HIS_WORDS, PICTURE, TABLE)
UNANSWERED = "unanswered"
# A slide's id becomes its deck slide's id (write_deck maps one to one), so
# it is written as a deck's ids are (presentation_schema.Id).
ID = r"^[a-z][a-z0-9-]{0,79}$"

_TEXT = {"type": "string"}
_WORDS = {"type": "string", "minLength": 1, "pattern": r"\S"}


def _object(properties: dict, description: str = "") -> dict:
    out = {"type": "object", "properties": properties, "required": list(properties),
           "additionalProperties": False}
    if description:
        out["description"] = description
    return out


def _array(items: dict, description: str = "", least: int = 0) -> dict:
    out: dict = {"type": "array", "items": items}
    if least:
        out["minItems"] = least
    if description:
        out["description"] = description
    return out


def _described(base: dict, description: str) -> dict:
    return {**base, "description": description}


PIECE = _object({
    "kind": {"type": "string", "enum": list(KINDS),
             "description": "text: words on the slide; his_words: Tim's own words, quoted "
                            "exactly, with their source; picture: a picture by path; table: "
                            "words in rows and columns."},
    "text": _described(_TEXT, "The words (text, his_words), or what the picture shows "
                              "(picture). Empty for a table."),
    "source": _described(_TEXT, "his_words: where he said it, its source path. Empty "
                                "otherwise."),
    "position": _described(_TEXT, "his_words: where in that source. Empty otherwise."),
    "path": _described(_TEXT, "picture: the picture's file path. Empty otherwise."),
    "rows": _array(_array(_TEXT), "table: its rows, each a list of cells. Empty otherwise."),
}, "One piece of what is on screen.")
FACT = _object({
    "fact": _described(_WORDS, "The fact, as the slide states it."),
    "source": _described(_WORDS, f"Its source path, or {UNANSWERED!r} where none is found."),
    "position": _described(_TEXT, "Where in that source (lines, characters or section)."),
})
OPTION = _object({"id": _WORDS, "name": _WORDS, "says": _WORDS})
DECISION = _object({"id": _WORDS, "question": _WORDS,
                    "options": _array(OPTION, "The final options, which you write.")})


def schema(question_ids: list[str]) -> dict:
    """script/1 as a JSON schema both brains take as an answer format;
    `question_ids`, the handoff's questions a slide may say it answers."""
    slide = _object({
        "id": {"type": "string", "pattern": ID,
               "description": "Lower case letters, digits and dashes; it is the deck "
                              "slide's id."},
        "title": _WORDS,
        "on_screen": _array(PIECE, "What is on screen, in order."),
        "narration": _described(_WORDS, "What JARVIS says over it."),
        "facts": _array(FACT, "Every fact on the slide or in its narration, each with its "
                              "source path and position."),
        "answers": _array({"type": "string", "enum": list(question_ids)},
                          "The ids of the questions it answers.") if question_ids
        else _array(_TEXT, "The ids of the questions it answers."),
        "decision": {"anyOf": [DECISION, {"type": "null"}],
                     "description": "The decision it asks, or null. A slide that asks a "
                                    "decision is the deck's slide for that decision."},
    })
    return _object({"version": {"type": "string", "enum": [VERSION]},
                    "slides": _array(slide, "The slides, in order.", least=1)},
                   "The script: each slide's words and what JARVIS says over it.")


# ── the program's check ───────────────────────────────────────────────────

def _against(value, form: dict, at: str, out: list[str]) -> None:
    """`value` checked against the part of the schema `form` is: the forms
    `schema` uses, nothing else."""
    if "anyOf" in form:
        tried: list[list[str]] = []
        for one in form["anyOf"]:
            found: list[str] = []
            _against(value, one, at, found)
            if not found:
                return
            tried.append(found)
        out.extend(min(tried, key=len))
        return
    kind = form.get("type")
    if kind == "null":
        if value is not None:
            out.append(f"{at} is not null.")
    elif kind == "string":
        if not isinstance(value, str):
            out.append(f"{at} is not words.")
            return
        if "enum" in form and value not in form["enum"]:
            out.append(f"{at} is {value!r}, not one of {', '.join(form['enum'])}.")
        if len(value) < form.get("minLength", 0) or (
                "pattern" in form and not re.search(form["pattern"], value)):
            out.append(f"{at} is {value!r}, which is empty or not written as asked"
                       + (f" ({form['description']})" if form.get("description") else "") + ".")
    elif kind == "array":
        if not isinstance(value, list):
            out.append(f"{at} is not a list.")
            return
        if len(value) < form.get("minItems", 0):
            out.append(f"{at} has fewer than {form['minItems']} item(s).")
        for n, item in enumerate(value):
            _against(item, form["items"], f"{at}[{n}]", out)
    elif kind == "object":
        if not isinstance(value, dict):
            out.append(f"{at} is not a mapping.")
            return
        for key in form["required"]:
            if key not in value:
                out.append(f"{at} has no {key}.")
        for key in value:
            if key not in form["properties"]:
                out.append(f"{at} has {key}, which script/1 does not have.")
        for key, sub in form["properties"].items():
            if key in value:
                _against(value[key], sub, f"{at}.{key}", out)


def _piece_problems(piece: dict, at: str) -> list[str]:
    """What a piece of its kind cannot be without: words, Tim's words with
    their source, a picture's path, a table's rows."""
    kind = piece.get("kind")
    need = {TEXT: ("text",), HIS_WORDS: ("text", "source"), PICTURE: ("path",),
            TABLE: ()}.get(kind, ())
    out = [f"{at}, a {kind}, has no {k}." for k in need if not str(piece.get(k) or "").strip()]
    if kind == TABLE and not [r for r in piece.get("rows") or [] if r]:
        out.append(f"{at}, a table, has no rows.")
    return out


def problems(script, question_ids: list[str]) -> list[str]:
    """Everything in `script` that is not script/1 as `schema` gives it;
    empty when it checks."""
    out: list[str] = []
    _against(script, schema(question_ids), "the script", out)
    if out:
        return out
    seen: set[str] = set()
    for n, slide in enumerate(script["slides"]):
        at = f"slide {n + 1} ({slide['id']})"
        if slide["id"] in seen:
            out.append(f"{at}: its id is another slide's.")
        seen.add(slide["id"])
        for i, piece in enumerate(slide["on_screen"]):
            out += _piece_problems(piece, f"{at} on_screen[{i}]")
    return out


# ── the file ──────────────────────────────────────────────────────────────

def dump(script: dict) -> str:
    return yaml.safe_dump(script, sort_keys=False, allow_unicode=True, width=100)


def write(path: Path, script: dict) -> str:
    """script.yaml written whole, in one rename; its text."""
    from jarvis import package_evidence
    text = dump(script)
    package_evidence.write_file(Path(path), text.encode("utf-8"))
    return text


def read(path: Path) -> Optional[dict]:
    """The script at `path`, or None when there is none or it is not YAML."""
    try:
        found = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return found if isinstance(found, dict) else None


# ── read by program ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Unit:
    """One stretch of words on a slide: marked, and edited by offsets
    within it."""
    slide: str
    field: str                  # where in the slide: "title", "on_screen[2].text", ...
    text: str
    editable: bool = True       # Tim's own words are quoted, never edited


def units(script: dict) -> list[Unit]:
    """Every piece of words of every slide, in order: its title, what is on
    screen, what JARVIS says over it, and the decision it asks."""
    out = []
    for slide in script.get("slides") or []:
        sid = slide["id"]
        out.append(Unit(sid, "title", slide["title"]))
        for i, piece in enumerate(slide.get("on_screen") or []):
            if piece["kind"] == TABLE:
                for r, row in enumerate(piece.get("rows") or []):
                    for c, cell in enumerate(row):
                        out.append(Unit(sid, f"on_screen[{i}].rows[{r}][{c}]", cell))
            elif piece.get("text"):
                out.append(Unit(sid, f"on_screen[{i}].text", piece["text"],
                                editable=piece["kind"] != HIS_WORDS))
        out.append(Unit(sid, "narration", slide["narration"]))
        decision = slide.get("decision")
        if decision:
            out.append(Unit(sid, "decision.question", decision["question"]))
            for j, option in enumerate(decision.get("options") or []):
                out.append(Unit(sid, f"decision.options[{j}].name", option["name"]))
                out.append(Unit(sid, f"decision.options[{j}].says", option["says"]))
    return [u for u in out if u.text.strip()]


_STEP = re.compile(r"\.?([a-z_]+)|\[(\d+)\]")


def _steps(field: str) -> list:
    steps, at = [], 0
    for found in _STEP.finditer(field):
        if found.start() != at:
            raise KeyError(field)
        steps.append(found.group(1) if found.group(1) else int(found.group(2)))
        at = found.end()
    if at != len(field) or not steps:
        raise KeyError(field)
    return steps


def _set(slide: dict, field: str, text: str) -> None:
    holder, steps = slide, _steps(field)
    for step in steps[:-1]:
        holder = holder[step]
    holder[steps[-1]] = text


def apply_edits(script: dict, edits: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """The editor's edits applied by the program, each at its slide and
    field, by offsets within that field's words (sessions.apply_edits):
    (the script, the edits applied, the edits refused: no such slide or
    field, Tim's own words, or old text not at its offsets)."""
    from jarvis import sessions
    script = copy.deepcopy(script)
    slides = {s["id"]: s for s in script.get("slides") or []}
    by_unit = {(u.slide, u.field): u for u in units(script)}
    grouped: dict[tuple[str, str], list[dict]] = {}
    refused: list[dict] = []
    for edit in edits:
        unit = by_unit.get((str(edit.get("slide")), str(edit.get("field"))))
        if unit is None or not unit.editable:
            refused.append(edit)
            continue
        grouped.setdefault((unit.slide, unit.field), []).append(edit)
    applied: list[dict] = []
    for (sid, field), group in grouped.items():
        text, done, not_done = sessions.apply_edits(by_unit[(sid, field)].text, group)
        _set(slides[sid], field, text)
        applied += done
        refused += not_done
    return script, applied, refused


def his_words(script: dict) -> list[dict]:
    """Each piece of Tim's own words, at its slide: {slide, text, source,
    position}."""
    return [{"slide": s["id"], "text": p["text"], "source": p["source"],
             "position": p.get("position", "")}
            for s in script.get("slides") or [] for p in s.get("on_screen") or []
            if p.get("kind") == HIS_WORDS]


def sources(script: dict) -> str:
    """Every source path the script cites (its facts' and his words'), one
    a line: what research's record of hits used is matched against."""
    found = [f["source"] for s in script.get("slides") or [] for f in s.get("facts") or []]
    found += [w["source"] for w in his_words(script)]
    return "\n".join(dict.fromkeys(p for p in found if p and p != UNANSWERED))


def slide_sources(slide: dict) -> list[str]:
    """The source paths one slide cites: its facts' and its quotes of Tim."""
    found = [f["source"] for f in slide.get("facts") or []]
    found += [p["source"] for p in slide.get("on_screen") or [] if p.get("kind") == HIS_WORDS]
    return [p for p in found if p and p != UNANSWERED]


def opening(script: dict) -> list[str]:
    """Slide 1 of the script: its title, then what is on screen in order
    (words, Tim's words, a table's rows); a picture is not words."""
    slide = (script.get("slides") or [{}])[0]
    lines = [slide.get("title", "")]
    for piece in slide.get("on_screen") or []:
        if piece.get("kind") in (TEXT, HIS_WORDS) and piece.get("text"):
            lines.append(piece["text"])
        elif piece.get("kind") == TABLE:
            lines += [" | ".join(row) for row in piece.get("rows") or [] if row]
    return [line for line in lines if line.strip()]


class ScriptFile:
    """A run's script.yaml as the swarm writes it (sessions.Script): the
    author answers it in its own form, the program checks it and writes it,
    and the editor's edits are applied at their slides and fields."""

    version = VERSION

    def __init__(self, path: Path, question_ids: list[str]):
        self.path = Path(path)
        self.name = self.path.name
        self.question_ids = list(question_ids)
        self.form = {"script": schema(self.question_ids)}
        self.problems: list[str] = []       # the last answer's, when it did not check

    def take(self, answer: dict) -> list[str]:
        script = answer.get("script") if isinstance(answer, dict) else None
        self.problems = (["The answer has no script."] if script is None
                         else problems(script, self.question_ids))
        if not self.problems:
            write(self.path, script)
        return self.problems

    def read(self) -> dict:
        return read(self.path) or {}

    def text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def edit(self, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
        script, applied, refused = apply_edits(self.read(), edits)
        if applied and problems(script, self.question_ids):
            # Edits that would leave a script that does not check are not
            # applied: the script stays as the author wrote it.
            return self.text(), [], refused + applied
        if applied:
            write(self.path, script)
        return self.text(), applied, refused
