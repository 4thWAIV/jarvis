"""The strict presentation/1 source model; independent of rendering and delivery."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,79}$")]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")]
Number = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[Number, Field(gt=0)]
Nonnegative = Annotated[Number, Field(ge=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Rect(Model):
    x: Number
    y: Number
    width: Positive
    height: Positive


class SourceRef(Model):
    reference: Id
    member: Text | None = None


class Reference(Model):
    id: Id
    kind: Literal["authored-example", "file", "url"]
    locator: Text
    revision: Text
    description: Text


class Asset(Model):
    id: Id
    path: Text
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    mime: Literal["image/png", "image/gif", "image/jpeg", "image/webp", "image/svg+xml"]
    width: Positive
    height: Positive
    source: SourceRef


# Tim's own words, quoted: drawn highlighted in a very bright, vibrant way
# and narrated (workflow.yaml deck his_words_highlighted; the named style
# deck_review checks every quote of his uses).
HIS_WORDS = "his-words"


class Run(Model):
    id: Id
    text: Text
    emphasis: Literal["normal", "strong", "italic", "his-words"]


class Paragraph(Model):
    id: Id
    role: Literal["paragraph", "heading", "bullet", "numbered"]
    runs: Annotated[list[Run], Field(min_length=1)]


class ObjectBase(Model):
    id: Id
    slot: Id          # where in its slide's named layout (layout_spec) it goes
    source: SourceRef


class TextObject(ObjectBase):
    type: Literal["text"]
    paragraphs: Annotated[list[Paragraph], Field(min_length=1)]


class ImageObject(ObjectBase):
    type: Literal["image"]
    asset: Id
    alt: Text
    fit: Literal["contain"]


class ShapeBase(Model):
    id: Id
    fill: Color | Literal["none"]
    stroke: Color | Literal["none"]
    strokePx: Nonnegative
    alt: Text


class Rectangle(ShapeBase):
    type: Literal["rect"]
    rect: Rect
    radius: Nonnegative


class Ellipse(ShapeBase):
    type: Literal["ellipse"]
    cx: Number
    cy: Number
    rx: Positive
    ry: Positive


class ShapePath(ShapeBase):
    type: Literal["path"]
    d: Text


VectorMember = Annotated[Union[Rectangle, Ellipse, ShapePath], Field(discriminator="type")]


class VectorObject(ObjectBase):
    type: Literal["vector"]
    viewBox: Rect
    members: Annotated[list[VectorMember], Field(min_length=1)]
    alt: Text


class Bar(Model):
    id: Id
    label: Text
    value: Nonnegative
    source: SourceRef


class BarObject(ObjectBase):
    type: Literal["bar"]
    bars: Annotated[list[Bar], Field(min_length=1)]
    units: Text
    domain: Annotated[list[Number], Field(min_length=2, max_length=2)]
    alt: Text


class TableRow(Model):
    id: Id
    cells: Annotated[list[Text], Field(min_length=1)]


# -- data: a dataset frozen in the source, and views of it -----------------------------

Value = Union[Number, Text]


class Column(Model):
    id: Id
    label: Text
    type: Literal["number", "text"]
    units: Text | None = None     # shown after the label in a table's heading and on a chart's axis


class DataRow(Model):
    id: Id
    values: dict[Id, Value]       # one value for every column


class Dataset(Model):
    """Records frozen in the source, from its `source` reference: what a
    table or chart shows is drawn from these, never typed again."""
    id: Id
    source: SourceRef
    columns: Annotated[list[Column], Field(min_length=1, max_length=12)]
    rows: Annotated[list[DataRow], Field(min_length=1, max_length=200)]


class Condition(Model):
    """Keep the rows whose `column` holds one of these (exactly one given)."""
    column: Id
    equals: Value | None = None
    one_of: Annotated[list[Value], Field(min_length=1)] | None = None
    at_least: Number | None = None
    at_most: Number | None = None


class Sort(Model):
    column: Id
    order: Literal["ascending", "descending"] = "ascending"


class DataView(Model):
    """Which rows of a dataset, in which order: every `where` kept, then
    sorted, then the first `limit`."""
    dataset: Id
    where: list[Condition] = []
    sort: Sort | None = None
    limit: Annotated[int, Field(ge=1, le=50)] | None = None


class TableObject(ObjectBase):
    """Words in rows and columns: an optional heading row, and the first
    cell of each row as its heading when `rowHeads`. Either its `rows`, or
    `data` (a view of a dataset) and the dataset's `columns` to show, which
    JARVIS turns into rows (presentation_data), each cell then a target,
    `<row id>-<column id>`, as it is for literal rows given `columns`."""
    type: Literal["table"]
    head: Annotated[list[Text], Field(min_length=1)] | None = None
    rows: Annotated[list[TableRow], Field(min_length=1)] | None = None
    data: DataView | None = None
    columns: Annotated[list[Id], Field(min_length=1)] | None = None
    rowHeads: bool = False
    alt: Text


class ChartObject(ObjectBase):
    """A chart JARVIS draws from a view of a dataset: `x` one column, `y`
    one to four number columns, each a series; every bar or point a target,
    `<object id>-<row id>-<column id>`."""
    type: Literal["chart"]
    kind: Literal["bar", "line", "scatter"]
    data: DataView
    x: Id
    y: Annotated[list[Id], Field(min_length=1, max_length=4)]
    alt: Text
    _drawn: dict | None = PrivateAttr(default=None)     # presentation_data.resolve_chart


# JARVIS's own components: the author gives the words, JARVIS draws them
# (layout_spec "components"), and each fits its slot by the slot's capacity
# (deck_capacity). Each item has its own id, so narration can light one.
class CalloutObject(ObjectBase):
    """One statement set apart (a warning, a finding, the bottom line): an
    optional `label` above what it `says`."""
    type: Literal["callout"]
    label: Text | None = None
    says: Text


class Figure(Model):
    id: Id
    value: Text       # the number as it is read, units and all ("12 000 €")
    label: Text       # what it counts


class FiguresObject(ObjectBase):
    """One to four key numbers side by side."""
    type: Literal["figures"]
    items: Annotated[list[Figure], Field(min_length=1, max_length=4)]


class Step(Model):
    id: Id
    title: Text
    says: Text | None = None


class StepsObject(ObjectBase):
    """Numbered steps in order, each a title and what it says."""
    type: Literal["steps"]
    items: Annotated[list[Step], Field(min_length=1, max_length=8)]


COMPONENTS = (CalloutObject, FiguresObject, StepsObject)


# -- diagrams: nodes and links JARVIS lays out -------------------------------------------
# deck/0.1's diagrams, shapes, groups and connectors (construction-drafts.md
# "deck/0.1 families"): the author names a kind and fills its nodes, links
# and groups with words and choices; JARVIS lays every one out by program
# (diagram_layout), never from the author's coordinates, sizes or colours.
# The script's visual forms name the kinds by these words (script_visuals).
DIAGRAM_KINDS = ("flow", "cycle", "hierarchy", "timeline")
NODE_SHAPES = ("box", "rounded", "pill", "ellipse", "diamond", "cylinder", "document")
TONES = ("neutral", "accent", "good", "warn", "bad", "muted")
LINK_STYLES = ("arrow", "line", "dashed", "both")


class DiagramNode(Model):
    """One node: its words, and how it looks chosen from JARVIS's lists. A
    timeline's node also says `when`."""
    id: Id
    label: Text
    shape: Literal["box", "rounded", "pill", "ellipse", "diamond", "cylinder", "document"] = "box"
    tone: Literal["neutral", "accent", "good", "warn", "bad", "muted"] = "neutral"
    when: Text | None = None


class DiagramLink(Model):
    """A connector from one node to another, with optional words on it."""
    model_config = ConfigDict(extra="forbid", strict=True, serialize_by_alias=True,
                              validate_by_name=False)
    id: Id
    source: Id = Field(alias="from")
    to: Id
    label: Text | None = None
    style: Literal["arrow", "line", "dashed", "both"] | None = None     # else the kind's own


class DiagramGroup(Model):
    """Nodes drawn together under a label: a flow's lane, a timeline's phase."""
    id: Id
    label: Text
    nodes: Annotated[list[Id], Field(min_length=1)]


class DiagramObject(ObjectBase):
    """A diagram of one `kind`: a flow (steps and choices joined by links,
    in lanes when it has groups), a cycle (a ring through its nodes in their
    order), a hierarchy (a tree from one root) or a timeline (its nodes in
    order along one line, in phases when it has groups). Every node, link
    and group is a target narration can light."""
    type: Literal["diagram"]
    kind: Literal["flow", "cycle", "hierarchy", "timeline"]
    nodes: Annotated[list[DiagramNode], Field(min_length=2, max_length=24)]
    links: Annotated[list[DiagramLink], Field(max_length=40)] = []
    groups: Annotated[list[DiagramGroup], Field(max_length=8)] = []
    alt: Text
    _drawn: dict | None = PrivateAttr(default=None)     # diagram_layout.layout, as shares of its slot

VisibleObject = Annotated[Union[TextObject, ImageObject, VectorObject, BarObject, TableObject,
                                ChartObject, CalloutObject, FiguresObject, StepsObject,
                                DiagramObject],
                          Field(discriminator="type")]


class Cue(Model):
    id: Id
    say: Text
    focus: list[Id]


class ContentSlide(Model):
    id: Id
    type: Literal["content"]
    title: Text
    layout: Id        # one of JARVIS's named layouts (layout_spec.layouts)
    objects: Annotated[list[VisibleObject], Field(min_length=1)]
    readingOrder: Annotated[list[Id], Field(min_length=1)]
    narration: Annotated[list[Cue], Field(min_length=1)]
    notes: str
    short: Text | None = None     # its name in the border's Chapters


class DecisionSlide(Model):
    id: Id
    type: Literal["decision"]
    decision: Id


class Option(Model):
    id: Id
    name: Text
    says: Text
    picture: Id | None = None     # every option is pictured unless the decision says text_only
    opens: list[Id] = []          # follow-up decisions this answer opens


# The one slot of a decision's own objects: its context area, a band above
# its options (deck.css .shown), where the shared deck draws what its
# script slide shows beside the words (script_visuals: steps, figures, a
# callout, a comparison, a table, a chart, a diagram, a picture, Tim's own
# words), each object a target narration can light.
SHOWN_SLOT = "shown"
SHOWN_MAX = 4


class Decision(Model):
    id: Id
    question: Text
    options: Annotated[list[Option], Field(min_length=2)]
    recommended: Id
    reason: Text
    short: Text | None = None         # its name in the border's Chapters
    context: Text | None = None       # what he needs beside the question
    picture: Id | None = None         # a picture beside the question
    compare: Id | None = None         # one picture comparing the options
    say: Text | None = None           # what JARVIS says for it in place of the recommendation
    text_only: Text | None = None     # why its options cannot be pictured
    needs_words: list[Id] = []        # answers that need his words
    # What the slide shows beside its words, drawn in its context area (slot
    # SHOWN_SLOT), in reading order; sized by deck_capacity with the slide.
    objects: Annotated[list[VisibleObject], Field(max_length=SHOWN_MAX)] = []


class Presentation(Model):
    version: Literal["presentation/1"]
    id: Id
    title: Text
    purpose: Text
    conclusion: Text
    language: Literal["en", "es"] | None = None     # else the language in force as it is built
    requires: Annotated[list[Literal["presentation-core/1"]], Field(min_length=1, max_length=1)]
    assets: list[Asset]
    references: Annotated[list[Reference], Field(min_length=1)]
    datasets: list[Dataset] = []
    slides: Annotated[list[Annotated[Union[ContentSlide, DecisionSlide], Field(discriminator="type")]], Field(min_length=1)]
    decisions: list[Decision]


def file_paths(source: dict) -> list[str]:
    """The paths a deck source (or any part of one holding its `assets` and
    `references`) names as files beside it: each asset's `path` and each
    `file` reference's `locator`. These are the only fields that name a
    file; no other string in a source is ever a path."""
    out: list[str] = []
    for asset in source.get("assets") or []:
        if isinstance(asset, dict) and isinstance(asset.get("path"), str):
            out.append(asset["path"])
    for ref in source.get("references") or []:
        if (isinstance(ref, dict) and ref.get("kind") == "file"
                and isinstance(ref.get("locator"), str)):
            out.append(ref["locator"])
    return out


def words(obj) -> list[str]:
    """The words an object shows, in reading order: what its target is
    named by and what the shared deck's own text of it says."""
    if isinstance(obj, TextObject):
        return ["".join(run.text for run in p.runs) for p in obj.paragraphs]
    if isinstance(obj, TableObject):
        return [*(obj.head or []), *(cell for row in obj.rows or [] for cell in row.cells)]
    if isinstance(obj, BarObject):
        return [f"{bar.label} {bar.value:g} {obj.units}" for bar in obj.bars]
    if isinstance(obj, CalloutObject):
        return [w for w in (obj.label, obj.says) if w]
    if isinstance(obj, FiguresObject):
        return [f"{item.value} {item.label}" for item in obj.items]
    if isinstance(obj, StepsObject):
        return [" ".join(w for w in (item.title, item.says) if w) for item in obj.items]
    if isinstance(obj, ChartObject) and obj._drawn:
        return obj._drawn["words"]
    if isinstance(obj, DiagramObject):
        return diagram_words(obj)
    return [obj.alt]


def link_words(obj: DiagramObject, link: DiagramLink) -> str:
    """A connector named by its ends and its words: "Order to Pay: approved"."""
    labels = {node.id: node.label for node in obj.nodes}
    said = f"{labels.get(link.source, link.source)} to {labels.get(link.to, link.to)}"
    return f"{said}: {link.label}" if link.label else said


def node_words(node: DiagramNode) -> str:
    return f"{node.when}: {node.label}" if node.when else node.label


def diagram_words(obj: DiagramObject) -> list[str]:
    """A diagram's words in reading order: its groups' labels, its nodes
    (a timeline's with when), its connectors by their ends and words."""
    return ([group.label for group in obj.groups] + [node_words(n) for n in obj.nodes]
            + [link_words(obj, link) for link in obj.links])
