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
    mime: Literal["image/png", "image/jpeg", "image/webp", "image/svg+xml"]
    width: Positive
    height: Positive
    source: SourceRef


class Theme(Model):
    background: Color
    foreground: Color
    accent: Color
    muted: Color
    font: Literal["system-sans"]
    bodyPx: Positive
    headingPx: Positive
    minimumPx: Annotated[Number, Field(ge=16)]
    gapPx: Nonnegative


class Region(Model):
    id: Id
    rect: Rect


class Layout(Model):
    id: Id
    regions: Annotated[list[Region], Field(min_length=1)]


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
    region: Id
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


VisibleObject = Annotated[Union[TextObject, ImageObject, VectorObject, BarObject], Field(discriminator="type")]


class Cue(Model):
    id: Id
    say: Text
    focus: list[Id]


class ContentSlide(Model):
    id: Id
    type: Literal["content"]
    title: Text
    layout: Id
    objects: Annotated[list[VisibleObject], Field(min_length=1)]
    readingOrder: Annotated[list[Id], Field(min_length=1)]
    narration: Annotated[list[Cue], Field(min_length=1)]
    notes: str


class DecisionSlide(Model):
    id: Id
    type: Literal["decision"]
    decision: Id


class Option(Model):
    id: Id
    name: Text
    says: Text
    picture: Id


class Decision(Model):
    id: Id
    question: Text
    why_you: Text
    evidence: Annotated[list[Text], Field(min_length=1, max_length=3)]
    options: Annotated[list[Option], Field(min_length=2)]
    recommended: Id
    reason: Text


class Presentation(Model):
    version: Literal["presentation/1"]
    id: Id
    title: Text
    purpose: Text
    conclusion: Text
    language: Literal["en", "es"]
    requires: Annotated[list[Literal["presentation-core/1"]], Field(min_length=1, max_length=1)]
    theme: Theme
    layouts: Annotated[list[Layout], Field(min_length=1)]
    assets: list[Asset]
    references: Annotated[list[Reference], Field(min_length=1)]
    slides: Annotated[list[Annotated[Union[ContentSlide, DecisionSlide], Field(discriminator="type")]], Field(min_length=1)]
    decisions: list[Decision]
