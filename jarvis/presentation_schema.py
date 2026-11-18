"""The strict presentation/1 source model; independent of rendering and delivery."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class Run(Model):
    id: Id
    text: Text
    emphasis: Literal["normal", "strong", "italic"]


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


class TableObject(ObjectBase):
    """Words in rows and columns: an optional heading row, and the first
    cell of each row as its heading when `rowHeads`."""
    type: Literal["table"]
    head: Annotated[list[Text], Field(min_length=1)] | None = None
    rows: Annotated[list[TableRow], Field(min_length=1)]
    rowHeads: bool = False
    alt: Text


VisibleObject = Annotated[Union[TextObject, ImageObject, VectorObject, BarObject, TableObject],
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
    slides: Annotated[list[Annotated[Union[ContentSlide, DecisionSlide], Field(discriminator="type")]], Field(min_length=1)]
    decisions: list[Decision]
