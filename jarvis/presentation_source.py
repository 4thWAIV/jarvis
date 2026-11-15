"""Read presentation YAML with source locations and validate its semantic graph."""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.presentation_schema import (BarObject, ContentSlide, Ellipse, ImageObject,
                                       Presentation, Rectangle, ShapePath, TextObject, VectorObject)


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


def read_source(path: Path) -> Source:
    """Parse nodes explicitly: no implicit date coercion or last-key-wins mappings."""
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
            diagnostics.append(source.diagnostic(code, item["msg"], candidate))
        raise DeckCompileError(tuple(diagnostics)) from None
    _semantics(source, data)
    return source


def _semantics(source: Source, data: dict) -> None:
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
    if not isinstance(doc.slides[0], ContentSlide):
        source.fail("production", "The opening must be a content slide.", ("slides", 0))
    if min(doc.theme.bodyPx, doc.theme.headingPx) < doc.theme.minimumPx:
        source.fail("accessibility", "Body and heading size must meet the effective readable floor.", ("theme",))
    layouts = {layout.id: layout for layout in doc.layouts}
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
            evidence = source.path.parent / reference.locator
            if not evidence.is_file() or evidence.is_symlink():
                source.fail("reference", "File evidence must identify a regular source file.", member=reference.id)
    for asset in doc.assets:
        provenance(asset.source, asset.id)
        _asset(source, asset)
    for layout in doc.layouts:
        for index, region in enumerate(layout.regions):
            r = region.rect
            if r.x < 0 or r.y < 0 or r.x + r.width > 100 or r.y + r.height > 100:
                source.fail("layout", "Region must fit the content rectangle [0,100] × [0,100].", member=region.id)
            for other in layout.regions[:index]:
                q = other.rect
                if max(r.x, q.x) < min(r.x + r.width, q.x + q.width) and max(r.y, q.y) < min(r.y + r.height, q.y + q.height):
                    source.fail("layout", f"Region overlaps {other.id!r}.", member=region.id)
    placed = []
    for slide in doc.slides:
        if not isinstance(slide, ContentSlide):
            if slide.decision not in decisions:
                source.fail("reference", "Decision slide names an unknown decision.", member=slide.id)
            placed.append(slide.decision)
            continue
        layout = layouts.get(slide.layout)
        if layout is None:
            source.fail("reference", "Unknown slide layout.", member=slide.id)
        regions = {region.id for region in layout.regions}
        ids = [obj.id for obj in slide.objects]
        if len(slide.readingOrder) != len(ids) or set(slide.readingOrder) != set(ids):
            source.fail("accessibility", "Reading order must name every slide object exactly once.", member=slide.id)
        occupied = set()
        visible = set(ids)
        for obj in slide.objects:
            provenance(obj.source, obj.id)
            if obj.region not in regions or obj.region in occupied:
                source.fail("reference", "Object region must exist in its layout and be occupied at most once.", member=obj.id)
            occupied.add(obj.region)
            if isinstance(obj, TextObject):
                for paragraph in obj.paragraphs:
                    visible.add(paragraph.id)
                    visible.update(run.id for run in paragraph.runs)
            elif isinstance(obj, ImageObject):
                if obj.asset not in assets:
                    source.fail("reference", "Unknown image asset.", member=obj.id)
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
            if option.picture not in assets:
                source.fail("reference", "Decision picture names an unknown asset.", member=option.id)


def data_at(path, data):
    for key in path:
        data = data[key]
    return data


def _asset(source: Source, asset) -> None:
    relative = Path(asset.path)
    reserved = {"index.html", "deck.css", "deck.js", "deck-data.js", "story.json", "package.json", "SKILL.md", "UNDERSTANDING.md", "presentation.js", "presentation.css", "presentation-data.js", "source.yaml", "target-index.json"}
    if not relative.parts or relative.as_posix() in reserved or relative.parts[0] in {".jarvis", "results", "scripts", "reference-evidence"} or ".." in relative.parts:
        source.fail("asset", "Asset path collides with generated package files or traverses directories.", member=asset.id)
    path = source.path.parent / asset.path
    if Path(asset.path).is_absolute() or not path.resolve().is_relative_to(source.path.parent) or path.is_symlink() or not path.is_file():
        source.fail("asset", "Asset must be a regular file inside the source directory.", member=asset.id)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != asset.sha256:
        source.fail("asset", "Asset SHA-256 differs from the declared bytes.", member=asset.id)
    signatures = {"image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                  "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
                  "image/webp": raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"}
    if asset.mime in signatures:
        if not signatures[asset.mime]:
            source.fail("asset", "Asset bytes do not match the declared MIME type.", member=asset.id)
        return  # Browser measurement verifies intrinsic dimensions and full decoding.
    try:
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("SVG declarations and entities are not allowed")
        root = ET.fromstring(raw)
        allowed = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc", "defs", "linearGradient", "radialGradient", "stop", "clipPath", "mask"}
        if root.tag.split("}")[-1] != "svg":
            raise ValueError("The root is not SVG")
        for element in root.iter():
            if element.tag.split("}")[-1] not in allowed:
                raise ValueError("SVG has an unsupported element")
            for key, value in element.attrib.items():
                name = key.split("}")[-1].lower()
                if name.startswith("on") or name in {"href", "style"}:
                    raise ValueError("SVG has an executable, linked or styled attribute")
                if "url(" in value.lower() and not re.fullmatch(r"url\(#[a-zA-Z_][\w.-]*\)", value):
                    raise ValueError("SVG has an external reference")
        def dimension(name):
            value = root.attrib.get(name, "")
            if not re.fullmatch(r"\d+(?:\.\d+)?(?:px)?", value):
                raise ValueError("SVG needs intrinsic pixel width and height")
            return float(value.removesuffix("px"))
        if (dimension("width"), dimension("height")) != (asset.width, asset.height):
            raise ValueError("SVG dimensions differ from the declaration")
    except (ET.ParseError, ValueError) as error:
        source.fail("asset", str(error), member=asset.id)


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
