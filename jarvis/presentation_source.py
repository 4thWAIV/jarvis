"""Read presentation YAML with source locations and validate its semantic graph."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis import layout_spec, picture_proof
from jarvis.presentation_schema import (BarObject, ContentSlide, Cue, Ellipse, ImageObject,
                                       Paragraph, Presentation, Rectangle, Run, ShapePath,
                                       SourceRef, TableObject, TextObject, VectorObject)


_NUMBER = re.compile(r"[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?\Z")

@dataclass(frozen=True)
class DeckDiagnostic:
    code: str
    source_path: str
    line: int
    column: int
    member: str | None
    profile: str | None
    message: str


class DeckCompileError(ValueError):
    def __init__(self, diagnostics: tuple[DeckDiagnostic, ...]):
        self.diagnostics = diagnostics
        super().__init__("\n".join(f"{d.code}: {d.source_path}:{d.line}:{d.column}: {d.message}" for d in diagnostics))


@dataclass
class Source:
    path: Path
    raw: bytes
    document: Presentation
    locations: dict[tuple, tuple[int, int]]
    nodes: dict[str, tuple]
    pictures: dict = field(default_factory=dict)     # asset id: picture_proof.Picture
    language: str = "en"     # the deck's, else the one it is built in (read_source)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def diagnostic(self, code: str, message: str, path: tuple = (), member: str | None = None,
                   profile: str | None = None) -> DeckDiagnostic:
        if member is not None and not path:
            path = self.nodes.get(member, ())
        while path and path not in self.locations:
            path = path[:-1]
        line, column = self.locations.get(path, (1, 1))
        field = ".".join(map(str, path))
        return DeckDiagnostic(code, str(self.path) + (f" [{field}]" if field else ""),
                              line, column, member, profile, message)

    def fail(self, code: str, message: str, path: tuple = (), member: str | None = None) -> None:
        raise DeckCompileError((self.diagnostic(code, message, path, member),))


def read_source(path: Path, built: bool = False, language: str | None = None) -> Source:
    """Parse nodes explicitly: no implicit date coercion or last-key-wins mappings.

    `built` reads the source.yaml inside a built package, where file
    evidence is frozen under reference-evidence/ rather than at its locator.
    `language` is the one the deck is built in when its source names none
    (the opening's words, draw_opening)."""
    from jarvis.repo_read import READ_MAX_FILE_BYTES
    path = path.resolve()
    locations: dict[tuple, tuple[int, int]] = {}
    source = Source(path, b"", None, locations, {})  # type: ignore[arg-type]
    try:
        raw = path.read_bytes()
    except OSError as error:
        source.fail("parse", str(error))
    source.raw = raw
    if len(raw) > READ_MAX_FILE_BYTES:
        source.fail("parse", "Source exceeds the existing text-file read limit.")
    try:
        text = raw.decode("utf-8")
        for event in yaml.parse(text):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) or getattr(event, "tag", None):
                mark = event.start_mark
                locations[()] = (mark.line + 1, mark.column + 1)
                source.fail("parse", "YAML tags, anchors and aliases are not presentation source constructs.")
        node = yaml.compose(text, Loader=yaml.BaseLoader)
    except (UnicodeError, yaml.YAMLError) as error:
        mark = getattr(error, "problem_mark", None)
        if mark:
            locations[()] = (mark.line + 1, mark.column + 1)
        source.fail("parse", str(error))
    if node is None:
        source.fail("parse", "Source must contain one YAML mapping.")

    def visit(node, at: tuple):
        locations[at] = (node.start_mark.line + 1, node.start_mark.column + 1)
        if isinstance(node, yaml.MappingNode):
            result = {}
            marks = {}
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode):
                    source.fail("parse", "Mapping keys must be strings.", at)
                name = key.value
                if name == "<<":
                    source.fail("parse", "YAML merge keys are not presentation source constructs.", at)
                if name in result:
                    earlier = marks[name]
                    source.fail("parse", f"Duplicate mapping key {name!r}: first at {earlier}, again at "
                                f"{key.start_mark.line + 1}:{key.start_mark.column + 1}.", at)
                if key.style is None and (_NUMBER.fullmatch(name) or name in {"true", "false", "null", "~"}):
                    source.fail("parse", "Mapping keys must be strings.", at)
                marks[name] = f"{key.start_mark.line + 1}:{key.start_mark.column + 1}"
                result[name] = visit(value, at + (name,))
            return result
        if isinstance(node, yaml.SequenceNode):
            return [visit(value, at + (n,)) for n, value in enumerate(node.value)]
        value = node.value
        if node.style is not None:
            return value
        if value in {"true", "false"}:
            return value == "true"
        if value in {"null", "~", ""}:
            return None
        if _NUMBER.fullmatch(value):
            return float(value) if any(c in value for c in ".eE") else int(value)
        if value.lower() in {".inf", "-.inf", "+.inf", ".nan"}:
            source.fail("schema", "Nonfinite numbers are not supported.", at)
        return value

    data = visit(node, ())
    _no_authored_layout(source, data)
    try:
        source.document = Presentation.model_validate(data)
    except ValidationError as error:
        diagnostics = []
        for item in error.errors(include_url=False):
            # Discriminated unions include their type name in the error location.
            at = tuple(item["loc"])
            candidate = ()
            for part in at:
                if candidate + (part,) in locations:
                    candidate += (part,)
            code = "capability" if at and at[0] in {"version", "requires"} else "schema"
            # A field that is not there has no place of its own: name it.
            missing = ".".join(str(p) for p in at[len(candidate):] if isinstance(p, (str, int)))
            message = f"{missing}: {item['msg']}" if missing and item["type"] == "missing" else item["msg"]
            diagnostics.append(source.diagnostic(code, message, candidate))
        raise DeckCompileError(tuple(diagnostics)) from None
    _semantics(source, data, built)
    source.language = source.document.language or language or "en"
    draw_opening(source)
    return source


# The opening JARVIS draws from `situation`: its slide's and words' ids,
# which no author's id may take.
OPENING_ID = "jarvis-opening"
OPENING_WORDS = OPENING_ID + "-words"
SITUATION_FIELDS = ("subject", "happened", "approved_before", "changes")
NEEDS_SITUATION = (
    "A deck that asks something opens with where things stand, which JARVIS draws as its "
    "first slide: give `situation` with subject (what this is about, named and explained "
    "for someone coming from other work), happened (what changed or failed, and why), "
    "approved_before (what he already approved that this touches, or that nothing was) "
    "and changes (what changes for him if he says yes).")


def draw_opening(source: Source) -> None:
    """A deck that asks something opens on where things stand: JARVIS draws
    its first slide from the author's `situation` and `conclusion`, in its
    `opening` layout (what it is about, what happened, what he approved
    before, what changes for him, then the bottom line), told with the
    purpose. Refused when the deck asks a decision and gives no situation."""
    from jarvis.decision_package import SITUATION_WORDS
    doc = source.document
    if doc.situation is None:
        if doc.decisions:
            source.fail("production", NEEDS_SITUATION, ("decisions",))
        return
    for ident in (OPENING_ID, OPENING_WORDS):
        if ident in source.nodes:
            source.fail("reference", f"{ident!r} names the opening JARVIS draws; give yours "
                        f"another id.", member=ident)
    words = SITUATION_WORDS.get(source.language, SITUATION_WORDS["en"])
    situation = doc.situation

    def paragraph(n: int, text: str, label: str | None = None) -> Paragraph:
        runs = [Run(id=f"{OPENING_WORDS}-{n}-label", text=label + " ", emphasis="strong")] if label else []
        runs.append(Run(id=f"{OPENING_WORDS}-{n}", text=text, emphasis="normal"))
        return Paragraph(id=f"{OPENING_WORDS}-p{n}", role="paragraph", runs=runs)

    shown = ([paragraph(1, situation.subject)]
             + [paragraph(n, getattr(situation, name), words[name])
                for n, name in enumerate(SITUATION_FIELDS[1:], 2)]
             + [paragraph(len(SITUATION_FIELDS) + 1, doc.conclusion, words["conclusion"])])
    text = TextObject(id=OPENING_WORDS, type="text", slot="words",
                      source=SourceRef(reference=doc.references[0].id), paragraphs=shown)
    opening = ContentSlide(
        id=OPENING_ID, type="content", title=doc.title, layout="opening", objects=[text],
        readingOrder=[OPENING_WORDS], notes="", short=words["short"],
        narration=[Cue(id=OPENING_ID + "-cue", focus=[OPENING_WORDS],
                       say=f"{doc.purpose} {situation.subject}")])
    doc.slides.insert(0, opening)
    for member in [OPENING_ID, OPENING_WORDS] + [p.id for p in shown]:
        source.nodes.setdefault(member, ("situation",))


NO_LAYOUT = ("JARVIS lays out every deck: a source names one of its layouts on each content "
             "slide ({layouts}) and a slot for each object, and never carries {what}.")


def _no_authored_layout(source: Source, data) -> None:
    """A source that lays itself out (a theme, region rectangles) is
    refused, naming what replaces it."""
    if not isinstance(data, dict):
        return
    named = ", ".join(layout_spec.layouts())
    for key in ("theme", "layouts"):
        if key in data:
            source.fail("layout", NO_LAYOUT.format(layouts=named, what=f"`{key}`"), (key,))
    for n, slide in enumerate(data.get("slides") or []):
        for m, obj in enumerate((slide or {}).get("objects") or [] if isinstance(slide, dict) else []):
            if isinstance(obj, dict) and "region" in obj:
                source.fail("layout", NO_LAYOUT.format(layouts=named, what="a `region`: give it a `slot`"),
                            ("slides", n, "objects", m, "region"))


def _semantics(source: Source, data: dict, built: bool = False) -> None:
    doc = source.document

    def identities(item, at=()):
        if isinstance(item, dict):
            if "id" in item:
                ident = item["id"]
                if ident in source.nodes:
                    first = source.locations[source.nodes[ident]]
                    source.fail("reference", f"Duplicate ID {ident!r}; first at {first[0]}:{first[1]}.", at)
                source.nodes[ident] = at
            for key, value in item.items():
                identities(value, at + (key,))
        elif isinstance(item, list):
            for n, value in enumerate(item):
                identities(value, at + (n,))

    identities(data)
    for slide in doc.slides:
        if isinstance(slide, ContentSlide) and slide.id + "-title" in source.nodes:
            source.fail("reference", f"{slide.id}-title names slide {slide.id}'s title, which JARVIS "
                        f"draws; give the object another id.", member=slide.id + "-title")
    if not isinstance(doc.slides[0], ContentSlide):
        source.fail("production", "The opening must be a content slide.", ("slides", 0))
    references = {ref.id for ref in doc.references}
    assets = {asset.id: asset for asset in doc.assets}
    decisions = {decision.id: decision for decision in doc.decisions}

    def provenance(ref, member):
        if ref.reference not in references:
            source.fail("reference", f"Unknown source reference {ref.reference!r}.", member=member)
        if ref.member is None and data_at(source.nodes[member], data).get("source", {}).get("member", "absent") is None:
            source.fail("schema", "An optional source member is omitted or nonempty text, never null.", member=member)

    for reference in doc.references:
        if reference.kind == "file":
            evidence = source.path.parent / (f"reference-evidence/{reference.id}.bin" if built
                                             else reference.locator)
            if not evidence.is_file() or evidence.is_symlink():
                source.fail("reference", "File evidence must identify a regular source file.", member=reference.id)
    pictures = {}
    for asset in doc.assets:
        provenance(asset.source, asset.id)
        pictures[asset.id] = _asset(source, asset)
    source.pictures = pictures
    placed = []
    for slide in doc.slides:
        if not isinstance(slide, ContentSlide):
            if slide.decision not in decisions:
                source.fail("reference", "Decision slide names an unknown decision.", member=slide.id)
            placed.append(slide.decision)
            continue
        if slide.layout not in layout_spec.layouts():
            source.fail("layout", f"Unknown layout {slide.layout!r}: JARVIS draws every slide in one of "
                        f"its named layouts, {', '.join(layout_spec.layouts())}.", member=slide.id)
        ids = [obj.id for obj in slide.objects]
        if len(slide.readingOrder) != len(ids) or set(slide.readingOrder) != set(ids):
            source.fail("accessibility", "Reading order must name every slide object exactly once.", member=slide.id)
        slots = layout_spec.slots_of(slide.layout)
        accepts = layout_spec.spec()["accepts"]
        filled = {}
        visible = set(ids) | {slide.id + "-title"}
        for obj in slide.objects:
            provenance(obj.source, obj.id)
            if obj.slot not in slots or obj.slot in filled:
                source.fail("layout", f"Slot {obj.slot!r} is not one of the {slide.layout} layout's "
                            f"({', '.join(slots)}), or is filled twice.", member=obj.id)
            if obj.type not in accepts[obj.slot]:
                source.fail("layout", f"The {obj.slot} slot holds {' or '.join(accepts[obj.slot])}, "
                            f"not {obj.type}.", member=obj.id)
            filled[obj.slot] = obj
        named = layout_spec.spec()["layouts"][slide.layout]
        missing = [slot for slot in named["required"] if slot not in filled]
        columns = named.get("columns", {}).get("slots", [])
        used = [slot for slot in columns if slot in filled]
        if missing or used != columns[:len(used)]:
            source.fail("layout", f"The {slide.layout} layout needs {', '.join(named['required'])}"
                        + (f", its columns filled in order ({', '.join(columns)})" if columns else "")
                        + ".", member=slide.id)
        boxes = layout_spec.boxes(slide.layout, set(filled))
        for obj in slide.objects:
            if isinstance(obj, TextObject):
                for paragraph in obj.paragraphs:
                    visible.add(paragraph.id)
                    visible.update(run.id for run in paragraph.runs)
            elif isinstance(obj, ImageObject):
                if obj.asset not in assets:
                    source.fail("reference", "Unknown image asset.", member=obj.id)
                _drawn(source, pictures[obj.asset], layout_spec.slot_px(boxes[obj.slot]), obj.id)
            elif isinstance(obj, TableObject):
                width = len(obj.head) if obj.head else len(obj.rows[0].cells)
                for row in obj.rows:
                    visible.add(row.id)
                    if len(row.cells) != width:
                        source.fail("schema", f"Every row of the table has {width} cells.", member=row.id)
            elif isinstance(obj, BarObject):
                if obj.domain[0] != 0 or obj.domain[1] <= 0:
                    source.fail("schema", "The bar domain starts at zero and ends above zero.", member=obj.id)
                for bar in obj.bars:
                    visible.add(bar.id)
                    provenance(bar.source, bar.id)
                    if bar.value > obj.domain[1]:
                        source.fail("schema", "Bar value is outside its declared domain.", member=bar.id)
            elif isinstance(obj, VectorObject):
                for shape in obj.members:
                    visible.add(shape.id)
                    if isinstance(shape, ShapePath):
                        _path(source, shape.d, shape.id)
                    else:
                        if isinstance(shape, Rectangle):
                            r = shape.rect
                            if shape.radius > min(r.width, r.height) / 2:
                                source.fail("schema", "Rectangle radius exceeds half its shortest side.", member=shape.id)
                            bounds = (r.x, r.y, r.x + r.width, r.y + r.height)
                        else:
                            bounds = (shape.cx - shape.rx, shape.cy - shape.ry, shape.cx + shape.rx, shape.cy + shape.ry)
                        pad = shape.strokePx / 2 if shape.stroke != "none" else 0
                        v = obj.viewBox
                        if bounds[0] - pad < v.x or bounds[1] - pad < v.y or bounds[2] + pad > v.x + v.width or bounds[3] + pad > v.y + v.height:
                            source.fail("layout", "Stroked shape lies outside its viewBox.", member=shape.id)
        for cue in slide.narration:
            if len(set(cue.focus)) != len(cue.focus):
                source.fail("reference", "Cue focus contains duplicate targets.", member=cue.id)
            for target in cue.focus:
                if target not in visible:
                    source.fail("reference", f"Target {target!r} is not visible on this slide.", member=cue.id)
    if sorted(placed) != sorted(decisions):
        source.fail("reference", "Every decision must appear exactly once in the sequence.", ("slides",))
    for decision in doc.decisions:
        if decision.recommended not in {option.id for option in decision.options}:
            source.fail("reference", "Recommended option does not belong to this decision.", member=decision.id)
        for option in decision.options:
            if option.picture is None:
                continue
            if option.picture not in assets:
                source.fail("reference", "Decision picture names an unknown asset.", member=option.id)
            _drawn(source, pictures[option.picture],
                   layout_spec.picture_box("option", len(decision.options)), option.id)
        for field, role in (("picture", "context"), ("compare", "compare")):
            asset = getattr(decision, field)
            if asset is None:
                continue
            if asset not in assets:
                source.fail("reference", f"The decision's {field} names an unknown asset.", member=decision.id)
            _drawn(source, pictures[asset], layout_spec.picture_box(role), decision.id)


def data_at(path, data):
    for key in path:
        data = data[key]
    return data


def _asset(source: Source, asset) -> None:
    relative = Path(asset.path)
    reserved = {"index.html", "deck.css", "deck.js", "deck-data.js", "story.json", "package.json", "SKILL.md", "UNDERSTANDING.md", "presentation.js", "presentation.css", "presentation-font.css", "presentation-data.js", "source.yaml", "target-index.json"}
    if not relative.parts or relative.as_posix() in reserved or relative.parts[0] in {".jarvis", "results", "scripts", "reference-evidence"} or ".." in relative.parts:
        source.fail("asset", "Asset path collides with generated package files or traverses directories.", member=asset.id)
    path = source.path.parent / asset.path
    if Path(asset.path).is_absolute() or not path.resolve().is_relative_to(source.path.parent) or path.is_symlink() or not path.is_file():
        source.fail("asset", "Asset must be a regular file inside the source directory.", member=asset.id)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != asset.sha256:
        source.fail("asset", "Asset SHA-256 differs from the declared bytes.", member=asset.id)
    try:
        picture = picture_proof.prove(path, asset.mime)
    except picture_proof.PictureProblem as error:
        source.fail("asset", f"Asset {asset.path}: {error}.", member=asset.id)
    if (picture.width, picture.height) != (asset.width, asset.height):
        source.fail("asset", f"Asset {asset.path} is {picture.width:g} x {picture.height:g} px, "
                    f"not the declared {asset.width:g} x {asset.height:g}.", member=asset.id)
    return picture


def _drawn(source: Source, picture, box: tuple[float, float], member: str) -> None:
    """A picture too small to read where it is drawn, at the smallest frame."""
    problem = picture_proof.drawn_problem(picture, box)
    if problem:
        source.fail("asset", f"Picture {member}: {problem}.", member=member)


# SVG's command grammar, not a permissive browser prefix parser. Geometry is
# measured by the same SVG engine that draws it in the rendered creation check.
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _path(source: Source, value: str, member: str) -> None:
    tokens, at = [], 0
    for match in _PATH_TOKEN.finditer(value):
        separator = value[at:match.start()]
        if separator.strip(" ,\t\r\n"):
            source.fail("schema", "Invalid SVG path token.", member=member)
        if "," in separator and (separator.count(",") != 1 or not tokens
                                  or tokens[-1].upper() in _ARITY or match.group().upper() in _ARITY):
            source.fail("schema", "Invalid SVG path comma separator.", member=member)
        tokens.append(match.group())
        at = match.end()
    if value[at:].strip(" \t\r\n") or not tokens or tokens[0] not in {"M", "m"}:
        source.fail("schema", "SVG path must begin with a move command.", member=member)
    index = 0
    while index < len(tokens):
        command = tokens[index]
        if command.upper() not in _ARITY:
            source.fail("schema", "SVG path needs a command.", member=member)
        index += 1
        end = index
        while end < len(tokens) and tokens[end].upper() not in _ARITY:
            end += 1
        numbers = tokens[index:end]
        arity = _ARITY[command.upper()]
        if (arity == 0 and numbers) or (arity and (not numbers or len(numbers) % arity)):
            source.fail("schema", f"SVG {command} command has the wrong argument count.", member=member)
        if any(not math.isfinite(float(number)) for number in numbers):
            source.fail("schema", "SVG path has a nonfinite coordinate.", member=member)
        if command.upper() == "A":
            for offset in range(0, len(numbers), 7):
                if float(numbers[offset]) < 0 or float(numbers[offset + 1]) < 0 or numbers[offset + 3] not in {"0", "1"} or numbers[offset + 4] not in {"0", "1"}:
                    source.fail("schema", "SVG arc radii and flags are invalid.", member=member)
        index = end
