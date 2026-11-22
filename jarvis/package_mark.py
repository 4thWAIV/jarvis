"""The mark step: what gather collected, read and marked (workflow.yaml mark,
approved by Tim 2026-11-18: "3. I approve it.").

Run after gather in JARVIS's own process (package_jobs.PRODUCERS), once per
package like the evidence it reads: a later run of the same package reuses
what is marked and only waits for it to be indexed. In order:

  type_files          each stored file given a type from its content and name
                      (evidence_structured.type_of), written into its
                      manifest row (`file_type`); one no reader here knows is
                      "no reader", never guessed
  read_structured     each file of a structured type parsed by program for
                      its type's facts, each with its file position
  extract_prose       the prose parts of each log and transcript
                      (evidence_structured.prose_of) to ExtractionSession
                      (tier 4), one call a file, for that type's facts
                      (evidence_structured.TYPE_FACTS), copied exactly; the
                      program keeps only a value found verbatim at its
                      position. A prose file is not extracted: the design
                      names no facts for one, and its marks say so
                      (`NO_PROSE_FACTS`)
  mark_prose          every prose file (evidence_structured.is_prose_file:
                      Tim's messages, the sender's reason and situation,
                      docs, notes, PR descriptions), each in full, to
                      MarkingSession (tier 3): split into sentences by
                      program (text_parts), each whole sentence given at
                      most its top 3 of the 28 labels (briefing_labels),
                      each scored 0.5 or more; with Tim's material and
                      its knowledge states, for the labels that depend on
                      him. The files are grouped so similar ones share pages
                      (`grouped`: the same type, the same folder, close in
                      meaning), packed into pages that fit the model and
                      asked in parallel (pages.py, Mark rebuilt for speed,
                      Tim 2026-11-21)
  relate_to_questions every fact and marked sentence to ClassificationSession
                      (tier 3), packed and asked the same way, each given
                      at most its top 3 of the handoff's questions, plus
                      background, each 0.5 or more; given without knowledge
                      states. A pipeline (`read_and_score`): the program's
                      facts classified at once, extraction's when it
                      answers, and each page's sentences as soon as that
                      page is marked, while the other pages are still
                      marked; every role's calls threads on its one app
                      server for the step (package_jobs.in_step)

What it read is kept beside the evidence, one file for each stored file,
`mark/<stored file>.json` (its type, facts, marked parts and their scores
against the questions, every one traceable to its file and position), and
`mark/marked.json` naming them, the labels and the questions; indexed with
the raw files and waited for as gather's are. The package's record says
when it was marked.

Retrieval, extraction and classification are never told what Tim knows
(workflow.yaml tim_knowledge who_gets_what reading_steps); marking is,
for known-to-him, reasks-settled and settled-and-open.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import (briefing_labels, evidence_structured as es, package_evidence as pe,
                    records)
from jarvis.package_evidence import HAD_ACCESS, KNOWS, PROBABLY_NOT, Row

STEP = "mark"
MARK = "mark"
INDEX = f"{MARK}/marked.json"
BY_PROGRAM, BY_EXTRACTION = "program", "extraction"


def pick_args(doc: dict) -> dict:
    """Each session's model: its role's tier on the brain the run's kind of
    agent goes on (model_tiers.pick), the request's engine its requester."""
    return {"requester": doc.get("engine")}


def _extraction(doc: dict):
    from jarvis import sessions
    return sessions.BrainExtractionSession(Path(doc["folder"]), **pick_args(doc))


def _marking(doc: dict):
    from jarvis import sessions
    return sessions.BrainMarkingSession(Path(doc["folder"]), **pick_args(doc))


def _classification(doc: dict):
    from jarvis import sessions
    return sessions.BrainClassificationSession(Path(doc["folder"]), **pick_args(doc))


EXTRACTION: Callable[[dict], object] = _extraction
MARKING: Callable[[dict], object] = _marking
CLASSIFICATION: Callable[[dict], object] = _classification


# ── each type's facts, for extraction ─────────────────────────────────────

# Why a prose file is not extracted: workflow.yaml mark extract_prose asks
# for "that type's facts", and the design names facts only for the
# structured types (read_structured); none for prose. Waiting on the owner.
NO_PROSE_FACTS = ("not extracted: the design names no facts for a prose file "
                  "(workflow.yaml mark extract_prose, \"that type's facts\")")


def fields_for(file_type: str):
    """The facts ExtractionSession pulls out of the prose parts of a log or
    a transcript: that type's facts (evidence_structured.TYPE_FACTS); None
    for any other type, which has no facts named for extraction."""
    from jarvis import sessions
    named = es.TYPE_FACTS.get(file_type)
    return None if named is None else [sessions.Field(n, d, True) for n, d in named]


# ── one file as it is marked ──────────────────────────────────────────────

@dataclass
class FileMarks:
    row: Row
    text: str
    view: es.ProseView
    facts: list[dict] = field(default_factory=list)
    extraction: Optional[str] = None        # why its prose was not extracted
    parts: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    @property
    def stored(self) -> str:
        return self.row.stored or ""

    @property
    def prose_file(self) -> bool:
        return es.is_prose_file(self.stored, self.row.file_type, self.row.type)

    def position(self, start: int, end: int) -> Optional[dict]:
        """Where [start, end) of its prose is in the file: from its start to
        its end, and each piece's part when it runs over more than one."""
        spans = self.view.spans(start, end)
        if not spans:
            return None
        at = {"start": spans[0][0], "end": spans[-1][1]}
        if len(spans) > 1:
            at["spans"] = [{"start": a, "end": b} for a, b in spans]
        return at

    def fact(self, field_name: str, value: str, position: dict, by: str, **more) -> None:
        self.facts.append({"id": f"f{len(self.facts) + 1}", "field": field_name, "value": value,
                           "position": position, "by": by, **more})

    def json(self, place: pe.Place) -> dict:
        out = {"file": self.stored, "item": place.item(self.stored), "row": self.row.id,
               "type": self.row.file_type, "prose_file": self.prose_file, "facts": self.facts,
               "parts": self.parts, "questions": self.questions}
        if self.extraction:
            out["extraction"] = self.extraction
        return out


def type_files(place: pe.Place, rows: list[Row]) -> list[FileMarks]:
    """type_files: each stored file's type into its manifest row; the files
    a reader here knows, with their text and prose."""
    out = []
    for row in rows:
        if not row.stored:
            continue
        data = (place.folder / row.stored).read_bytes()
        row.file_type = es.type_of(row.stored, row.type, data)
        if row.file_type == es.NO_READER:
            continue
        text = data.decode("utf-8")
        out.append(FileMarks(row, text, es.prose_of(row.stored, row.file_type, text)))
    pe.write_manifest(place, rows)
    return out


def read_structured(files: list[FileMarks]) -> None:
    for f in files:
        for fact in es.read_structured(f.stored, f.row.file_type, f.text):
            f.fact(fact.field, fact.value, {"start": fact.start, "end": fact.end},
                   BY_PROGRAM, group=fact.group)


def _extraction_asks(files: list[FileMarks]) -> list[tuple[FileMarks, list]]:
    """The files whose prose is extracted, each with its type's facts; a
    prose file left unextracted, its marks saying why (`NO_PROSE_FACTS`)."""
    asked: list[tuple[FileMarks, list]] = []
    for f in files:
        if not f.view.text:
            continue
        fields = None if f.prose_file else fields_for(f.row.file_type)
        if fields is None:
            if f.prose_file:
                f.extraction = NO_PROSE_FACTS
            continue
        asked.append((f, fields))
    return asked


async def extract_prose(doc: dict, files: list[FileMarks]) -> list[dict]:
    """The prose parts of each log and transcript to ExtractionSession, all
    on one session (a new conversation a file), for that type's facts; each
    value it gives is kept only where it stands verbatim in the prose
    (sessions.BrainExtractionSession), with where that is in the file. A
    prose file is left unextracted, its marks saying why
    (`NO_PROSE_FACTS`). The facts it added, each with its file."""
    from jarvis import package_jobs
    asked = _extraction_asks(files)
    if not asked:
        return []
    answers = await package_jobs.answered(
        STEP, "extraction", f"JARVIS asked extraction for the facts of {len(asked)} file(s).",
        EXTRACTION(doc).extract_many([(f.view.text, fields) for f, fields in asked], None))
    added = []
    for (f, _), found in zip(asked, answers):
        for value in found:
            if not value["value"] or value["position"] is None:
                continue
            at = f.position(value["position"]["start"], value["position"]["end"])
            if at is None:              # all of it the separator JARVIS put between pieces
                continue
            f.fact(value["field"], value["value"], at, BY_EXTRACTION,
                   confidence=value["confidence"], context=value["context"])
            added.append((f, f.facts[-1]))
    return added


def tim_known(rows: list[Row], texts: dict[str, str], skip: str = ""):
    """Tim's material as the MarkingSession is told it: each stored item he
    knows or had access to, whole, with its state; each he probably does not
    know named by its source and state, without its words (he does not have
    them). `skip`: a stored file given otherwise."""
    from jarvis import sessions
    out = []
    for row in rows:
        if not row.stored or row.stored == skip:
            continue
        if row.knowledge_state in (KNOWS, HAD_ACCESS):
            out.append(sessions.Known(texts.get(row.stored, ""), row.knowledge_state, row.stored))
        else:
            out.append(sessions.Known("", PROBABLY_NOT, row.stored))
    return out


def grouped(files: list[FileMarks]) -> list[FileMarks]:
    """The files in the order they are marked and scored, so similar ones
    share pages: the same type, then the same folder, then close in meaning
    (pages.grouped)."""
    from jarvis import pages
    return pages.grouped(files, kind=lambda f: f.row.file_type or "",
                         folder=lambda f: str(Path(f.stored).parent),
                         text=lambda f: f.view.text or f.text, name=lambda f: f.stored)


def _placed(f: FileMarks, part: dict) -> dict:
    """A marked part with its position read back into the file (a part is
    text_parts' cut of the prose: never all separator)."""
    return {**part, "position": f.position(part["position"]["start"], part["position"]["end"])}


async def mark_prose(doc: dict, files: list[FileMarks], rows: list[Row],
                     on_page: Optional[Callable[[list[tuple[FileMarks, dict]]], None]] = None
                     ) -> None:
    """Every prose file, in full, to MarkingSession at once, each sentence
    given at most its top 3 of the 28 labels at 0.5 or more; each
    sentence's position read back into the file. `on_page`: called with
    each page's parts, each with its file, as the page is marked."""
    from jarvis import package_jobs, sessions
    prose = [f for f in files if f.view.text and f.prose_file]
    if not prose:
        return
    by_key = {f.stored: f for f in prose}

    def page_marked(parts: dict[str, list[dict]]) -> None:
        on_page([(by_key[key], _placed(by_key[key], part))
                 for key, found in parts.items() for part in found])
    session = MARKING(doc)
    marked = await package_jobs.answered(
        STEP, "marking", f"JARVIS asked marking to mark {len(prose)} prose file(s).",
        session.mark_files([sessions.Text(f.stored, f.view.text, f.row.file_type or "")
                            for f in prose], briefing_labels.for_marking(),
                           tim_known(rows, {f.stored: f.text for f in files}), None,
                           on_page=page_marked if on_page is not None else None))
    for f in prose:
        f.parts += [_placed(f, part) for part in marked[f.stored]]


def _source(f: FileMarks, position: dict) -> str:
    return f"{f.stored} " + ", ".join(f"{s['start']}-{s['end']}"
                                       for s in position.get("spans") or [position])


def _fact_item(f: FileMarks, x: dict):
    from jarvis import sessions
    return sessions.Item(x["id"], x["value"], f"{_source(f, x['position'])}, {x['field']}",
                         f.stored)


def _part_item(f: FileMarks, p: dict):
    from jarvis import sessions
    return sessions.Item(p["id"], p["segment"], _source(f, p["position"]), f.stored)


def questions_of(doc: dict):
    from jarvis import sessions
    return [sessions.ClassQuestion(q["id"], q["text"])
            for q in (doc.get("request") or {}).get("questions") or []]


async def _cancelling(*works) -> list:
    """Every work's result; the first that fails cancels the others and is
    raised."""
    tasks = [asyncio.ensure_future(w) for w in works]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def read_and_score(doc: dict, files: list[FileMarks], rows: list[Row]) -> None:
    """extract_prose, mark_prose and relate_to_questions as one pipeline:
    every fact and marked sentence to ClassificationSession as soon as it
    is had (the program's facts at once, extraction's when it answers, a
    page's sentences as soon as that page is marked; measured on
    package-870e45145b2d, classification started only after all marking
    ended, 119 s after its 200 s), each given at most its top 3 of the
    handoff's questions, plus background, at 0.5 or more."""
    from jarvis import package_jobs
    groups: asyncio.Queue = asyncio.Queue()
    groups.put_nowait([_fact_item(f, x) for f in files for x in f.facts])

    async def fed():
        while (items := await groups.get()) is not None:
            yield items

    async def extraction() -> None:
        added = await extract_prose(doc, files)
        groups.put_nowait([_fact_item(f, x) for f, x in added])

    async def marking() -> None:
        await mark_prose(doc, files, rows, on_page=lambda parts: groups.put_nowait(
            [_part_item(f, p) for f, p in parts]))

    async def read() -> None:
        try:
            await _cancelling(extraction(), marking())
        finally:
            groups.put_nowait(None)

    async def classify():
        return await package_jobs.answered(
            STEP, "classification",
            f"JARVIS asked classification to score the facts and parts of {len(files)} "
            f"file(s) against the handoff's questions.",
            CLASSIFICATION(doc).classify_fed(fed(), questions_of(doc), None))
    _, scored = await _cancelling(read(), classify())
    by_item = {(item.group, item.id): scores for item, scores in scored}
    for f in files:
        f.questions = [{"item": x["id"], "scores": by_item[(f.stored, x["id"])]}
                       for x in f.facts + f.parts]


# ── the step ──────────────────────────────────────────────────────────────

def _write(place: pe.Place, doc: dict, files: list[FileMarks]) -> None:
    for f in files:
        place.write(f"{MARK}/{f.stored}.json",
                    json.dumps(f.json(place), indent=2, ensure_ascii=False).encode("utf-8"))
    from jarvis import sessions
    index = {"labels": list(briefing_labels.NAMES),
             "questions": [{"id": q.id, "asks": q.asks} for q in questions_of(doc)]
             + [{"id": sessions.BACKGROUND, "asks": sessions.BACKGROUND}],
             "files": [{"file": f.stored, "type": f.row.file_type,
                        "prose_file": f.prose_file, "marks": f"{MARK}/{f.stored}.json",
                        "facts": len(f.facts), "parts": len(f.parts),
                        **({"extraction": f.extraction} if f.extraction else {})}
                       for f in files]}
    place.write(INDEX, json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"))


def marked(place: pe.Place) -> Optional[dict]:
    """The package's marked evidence: `mark/marked.json`, each file's marks
    in it under `marks`; None before it is marked."""
    try:
        index = json.loads((place.folder / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in index.get("files") or []:
        entry["marks"] = json.loads((place.folder / entry["marks"]).read_text(encoding="utf-8"))
    return index


def mark(doc: dict) -> None:
    """The mark step of one run (package_jobs.PRODUCERS["mark"])."""
    from jarvis import package_jobs
    from jarvis.package_jobs import Blocked
    place = pe.place_of(doc)
    record = pe.gathered(place)
    if record is None:
        raise Blocked(STEP, "The package's evidence was not gathered.",
                      "JARVIS reached the mark step.",
                      "A new run, which gathers the evidence first.")
    if record.get("marked_at"):
        pe.wait_indexed(place, STEP, text_only=(f"/{MARK}/",))        # marked once: every run reuses it
        return
    folder = place.folder / MARK
    if folder.exists():                     # a run that stopped part way: marked again
        shutil.rmtree(folder)
    rows = pe.read_manifest(place)
    files = grouped(type_files(place, rows))
    read_structured(files)
    package_jobs.in_step(read_and_score(doc, files, rows))
    _write(place, doc, files)
    pe.wait_indexed(place, STEP, text_only=(f"/{MARK}/",))
    records.update(place.record, lambda d: {**(d or {}), "marked_at": time.time(),
                                            "marked": INDEX},
                   by=records.JARVIS, change="evidence marked",
                   summary=f"{len(files)} file(s) read and marked")
