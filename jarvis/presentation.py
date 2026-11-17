"""Compile presentation/1 through the existing deck construction and policy."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jarvis import (decision_package as dp, layout_spec, package_story, package_understanding,
                    presentation_proof)
from jarvis.presentation_schema import (BarObject, ContentSlide, ImageObject, SourceRef, TableObject,
                                        TextObject, VectorObject)
from jarvis.presentation_source import (OPENING_ID, DeckCompileError, DeckDiagnostic, Source,
                                        read_source)

ASSETS = Path(__file__).with_name("presentation_assets")


@dataclass(frozen=True)
class CompiledDeck:
    source_sha256: str
    package_source: Path
    target_index: Path


def _ordered_objects(slide: ContentSlide):
    """The authored reading order governs every generated human traversal."""
    objects = {obj.id: obj for obj in slide.objects}
    return (objects[ident] for ident in slide.readingOrder)


def _targets(source: Source) -> list[dict]:
    doc = source.document
    entries = []
    decisions = {decision.id: decision for decision in doc.decisions}
    authored = SourceRef(reference=doc.id)
    for slide in doc.slides:
        def add(member, kind, name, ref, dom_id=None):
            entries.append({"document":doc.id,"source_sha256":source.digest,"member":member,
                            "slide":slide.id,"kind":kind,"accessible_name":name,
                            "reference":ref.reference,"source_member":ref.member,
                            "dom_id":dom_id or "presentation-main-" + member})
        if not isinstance(slide, ContentSlide):
            decision = decisions[slide.decision]
            add(slide.id, "slide", decision.question, authored, "d-" + decision.id)
            add(decision.id, "decision", decision.question, authored, "d-" + decision.id)
            for option in decision.options:
                add(option.id, "option", option.name, authored, "opt-" + decision.id + "-" + option.id)
            continue
        add(slide.id, "slide", slide.title, authored, "presentation-slide-" + slide.id)
        add(slide.id + "-title", "title", slide.title, authored)
        for obj in _ordered_objects(slide):
            if isinstance(obj, TextObject):
                add(obj.id,"text"," ".join(run.text for p in obj.paragraphs for run in p.runs),obj.source)
                for paragraph in obj.paragraphs:
                    add(paragraph.id,"paragraph","".join(r.text for r in paragraph.runs),obj.source)
                    for run in paragraph.runs:
                        add(run.id,"run",run.text,obj.source)
            else:
                add(obj.id,obj.type,obj.alt,obj.source)
                if isinstance(obj, VectorObject):
                    for shape in obj.members:
                        add(shape.id,shape.type,shape.alt,obj.source)
                elif isinstance(obj, BarObject):
                    for bar in obj.bars:
                        add(bar.id,"bar",f"{bar.label}: {bar.value:g} {obj.units}",bar.source)
                elif isinstance(obj, TableObject):
                    for row in obj.rows:
                        add(row.id,"row"," | ".join(row.cells),obj.source)
    return entries


def _deck(source: Source, targets: list[dict]) -> dict:
    """Project visible words and cues into the shared briefing/decision model."""
    doc=source.document
    assets={asset.id:asset for asset in doc.assets}
    selectors={target["member"]:"#"+target["dom_id"] for target in targets}
    slides=[]
    for slide in doc.slides:
        if not isinstance(slide,ContentSlide):
            slides.append({"decision":slide.decision})
            continue
        blocks=[]
        for obj in _ordered_objects(slide):
            if isinstance(obj,TextObject):
                blocks.extend({"p":"".join(run.text for run in p.runs)} for p in obj.paragraphs)
            elif isinstance(obj,ImageObject):
                blocks.append({"picture":assets[obj.asset].path,"alt":obj.alt})
            elif isinstance(obj,BarObject):
                blocks.append({"p":" ".join(f"{b.label} {b.value:g} {obj.units}" for b in obj.bars)})
            elif isinstance(obj,TableObject):
                blocks.append({"table":{**({"head":obj.head} if obj.head else {}),"rows":[r.cells for r in obj.rows],
                                        "rowHeads":obj.rowHeads}})
            else:
                blocks.append({"p":obj.alt})
        slides.append({"id":"presentation-slide-" + slide.id,"title":slide.title,"blocks":blocks,
                       **({"short":slide.short} if slide.short else {}),
                       **({"opening":True} if slide.id == OPENING_ID else {}),
                       "beats":[{"say":cue.say,"targets":[selectors[t] for t in cue.focus]} for cue in slide.narration]})
    decisions=[]
    for decision in doc.decisions:
        data=decision.model_dump(exclude_none=True)
        for field in ("picture","compare"):
            if field in data:
                data[field]=assets[data[field]].path
        for option in data["options"]:
            if "picture" in option:
                option["picture"]=assets[option["picture"]].path
            if not option.get("opens"):
                option.pop("opens",None)
        if not data.get("needs_words"):
            data.pop("needs_words",None)
        decisions.append(data)
    return {"name":doc.id,"title":doc.title,"purpose":doc.purpose,"conclusion":doc.conclusion,
            **({"language":doc.language} if doc.language else {}),"slides":slides,"decisions":decisions}


def _prove(parsed: Source, folder: Path, deck: dict, targets: list[dict], story: dict) -> None:
    """The static proof on the built folder: text fit and pictures at every
    frame, story targets and template bytes; written into the package as
    .jarvis/layout-proof.json, or DeckCompileError naming each failure."""
    failures, tightest = presentation_proof.prove_slides(parsed, parsed.pictures)
    failures += presentation_proof.prove_targets(story, targets, deck)
    failures += presentation_proof.prove_templates(folder)
    if failures:
        raise DeckCompileError(tuple(parsed.diagnostic(
            "layout", failure.message, member=failure.member if failure.member in parsed.nodes else None,
            profile=f"{failure.frame[0]:.0f}x{failure.frame[1]:.0f}" if failure.frame else None)
            for failure in failures))
    presentation_proof.write(folder, presentation_proof.record(parsed, failures, tightest))


def compile_deck(source: Path, output: Path) -> CompiledDeck:
    """Compile presentation/1 to existing package input, proven
    (presentation_proof) without a browser, or raise DeckCompileError."""
    from jarvis.make_package import built_language, prepare_deck
    parsed=read_source(source, language=built_language())
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    destination=output/parsed.document.id
    if destination.exists() or parsed.path.is_relative_to(destination):
        parsed.fail("production","Compiler output already exists or overlaps source input.")
    targets=_targets(parsed)
    try:
        deck=prepare_deck(_deck(parsed,targets),parsed.path.parent)
        builder=dp.builder()
        package_story.parse(package_story.encode(builder.story(deck)))
        with tempfile.TemporaryDirectory(prefix="presentation-",dir=output) as temporary:
            folder=Path(temporary)/parsed.document.id
            builder.build_deck(deck,parsed.path.parent,folder)
            package_understanding.prepare_deck(folder,deck,builder,parsed.path.parent)
            for asset in parsed.document.assets:
                path=folder/asset.path
                if path.name in builder.FIXED or path.resolve() in {p.resolve() for p in folder.glob("*") if p.is_file()}:
                    # Already copied decision/content pictures are the same declared bytes.
                    if path.is_file() and path.read_bytes()==(parsed.path.parent/asset.path).read_bytes():
                        continue
                    parsed.fail("asset","Asset path collides with a generated package member.",member=asset.id)
                path.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(parsed.path.parent/asset.path,path)
            for name in ("presentation.js","presentation.css"):
                shutil.copy2(ASSETS/name,folder/name)
            (folder/layout_spec.FONT_CSS).write_text(layout_spec.font_css(),encoding="utf-8")
            frozen = {}
            for reference in parsed.document.references:
                if reference.kind == "file":
                    evidence = (parsed.path.parent / reference.locator).read_bytes()
                    relative = "reference-evidence/" + reference.id + ".bin"
                    snapshot = folder / relative
                    snapshot.parent.mkdir(exist_ok=True)
                    snapshot.write_bytes(evidence)
                    frozen[reference.id] = {"path": relative, "sha256": hashlib.sha256(evidence).hexdigest()}
            model={"frozen_references":frozen,"document":parsed.document.model_dump(exclude_none=True),"source_sha256":parsed.digest,"targets":targets,"story":builder.story(deck)}
            (folder/"presentation-data.js").write_text("window.PRESENTATION = "+json.dumps(model,ensure_ascii=False)+";\n",encoding="utf-8")
            (folder/"source.yaml").write_bytes(parsed.raw)
            (folder/"target-index.json").write_text(json.dumps(targets,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
            entry=folder/"index.html"
            html=entry.read_text().replace('<link rel="stylesheet" href="deck.css">','<link rel="stylesheet" href="deck.css">\n  <link rel="stylesheet" href="presentation-font.css">\n  <link rel="stylesheet" href="presentation.css">')
            html=html.replace('<script src="deck.js"></script>','<script src="presentation-data.js"></script>\n  <script src="presentation.js"></script>\n  <script src="deck.js"></script>')
            entry.write_text(html,encoding="utf-8")
            manifest_path=folder/"package.json"
            manifest=json.loads(manifest_path.read_text())
            brief=folder/manifest["brief"]
            with brief.open("a",encoding="utf-8") as stream:
                stream.write("\n## Presentation source\n\nThe following exact source model supplies the visible objects, their reading order, named members, provenance and narration. "
                             "presentation.js renders those objects in JARVIS's named layouts; presentation.css (generated from the layout spec) places them, and .jarvis/layout-proof.json records the static proof that they fit. source.yaml preserves the authored bytes. "
                             "target-index.json binds each member to its source revision and generated element. The existing deck.js owns navigation and all answer recording.\n\n```json\n"+json.dumps(model,indent=2,ensure_ascii=False)+"\n```\n")
            for path in builder.zip_package.files(folder):
                relative=path.relative_to(folder).as_posix()
                if relative not in {"package.json",manifest["brief"]}:
                    manifest["understanding"].setdefault(relative,manifest["brief"])
            manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
            package_understanding.validate(folder,manifest,builder.zip_package.files(folder))
            _prove(parsed,folder,deck,targets,builder.story(deck))
            shutil.move(str(folder),destination)
    except (dp.Refused,package_story.StoryError,OSError) as error:
        messages=error.problems if isinstance(error,dp.Refused) else [str(error)]
        raise DeckCompileError(tuple(parsed.diagnostic("production",message) for message in messages)) from None
    return CompiledDeck(parsed.digest,destination,destination/"target-index.json")
