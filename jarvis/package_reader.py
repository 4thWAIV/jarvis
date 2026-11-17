"""The independent reader check: a cold reader must follow a deck before
it is asked anything.

Once the fresh author's deck is built and proven (make_package), a second
fresh session, on the package author's engine choice (engine_choice) and
with its own prompt, is given only the words of the slides before the first
decision (each slide's title, its words and what JARVIS says over it, as
text: `before_the_ask`) and must answer the six cold-read questions of the
author's skill (`QUESTIONS`) from them alone. Any it cannot answer goes
back to the author once, with what was missing (`missed`); a second miss
refuses the package with the reader's answers. The reader sees no handoff,
no evidence and no source: only what Tim would see first.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from jarvis import engine_choice
from jarvis.decision_package import Refused

# The six questions of the cold read, exactly as the author's skill
# (human-package-author, "Review comprehension separately from mechanics")
# asks them.
QUESTIONS = (
    "What is this about?",
    "What did I already approve that it touches?",
    "What happened, and why?",
    "What are my options, and what changes for me under each?",
    "What is recommended, and why?",
    "What am I asked to decide?",
)
ANSWERS_FILE = "reader-answers.json"
TIMEOUT_SEC = 600


def _blocks_text(block: dict) -> list[str]:
    """The words a block shows, line by line."""
    lines = [str(block[k]) for k in ("lead", "p", "note", "kicker") if block.get(k)]
    lines += [p if isinstance(p, str) else str(p.get("text", "")) for p in block.get("points") or []]
    if block.get("picture"):
        lines.append(f"[Picture: {block.get('alt') or 'no description'}]")
    table = block.get("table") or {}
    for row in [table.get("head") or []] + list(table.get("rows") or []):
        if row:
            lines.append(" | ".join(str(c) for c in row))
    for fact in block.get("facts") or []:
        if isinstance(fact, dict):
            lines.append(" ".join(str(v) for v in fact.values()))
    return lines


def before_the_ask(deck: dict) -> str:
    """The slides before the first decision (its lead-in included in the
    decision), as text: what a reader sees and hears before anything is
    asked of him."""
    out = []
    for n, slide in enumerate(deck.get("slides") or [], 1):
        if not isinstance(slide, dict) or "decision" in slide or slide.get("lead_in"):
            break
        lines = [f"Slide {n}: {slide.get('title', '')}"]
        for block in slide.get("blocks") or []:
            if isinstance(block, dict):
                lines += _blocks_text(block)
        said = [slide["say"]] if slide.get("say") else [
            b.get("say") for b in slide.get("beats") or [] if isinstance(b, dict) and b.get("say")]
        if said:
            lines.append("JARVIS says: " + " ".join(said))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def prompt(slides: str, answers: Path) -> str:
    asked = "\n".join(f"{n}. {q}" for n, q in enumerate(QUESTIONS, 1))
    return (
        "You are a cold reader: busy, coming from other work, with no knowledge of this "
        "project beyond what is below. Below are the slides of a package, as text, up to the "
        "point where it first asks the reader to decide something; nothing else exists for "
        "you. Do not open, search or read any file, and do not guess from general knowledge "
        "what this project's own names mean. Answer each question only from these slides. "
        "When the slides do not answer one, say so: it is unanswered, and name what the slides "
        "would have to say for you to answer it. A name the slides use but never explain "
        "counts as unexplained.\n\nQuestions:\n" + asked + "\n\nWrite JSON to " + str(answers)
        + " and nothing else: {\"answers\": [{\"question\": <the question exactly>, "
        "\"answered\": true or false, \"answer\": <your answer from the slides, or empty>, "
        "\"missing\": <what the slides would need to say, or empty>}, ...]} with all six "
        "questions in order. Then stop.\n\nThe slides:\n\n" + slides)


def answers_in(path: Path) -> list[dict]:
    """The reader's answers, one per question in order, or Refused."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused([f"The cold reader wrote no answers: {error}"]) from None
    rows = value.get("answers") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != len(QUESTIONS) or not all(
            isinstance(r, dict) and r.get("question") == q and type(r.get("answered")) is bool
            for r, q in zip(rows, QUESTIONS)):
        raise Refused(["The cold reader's answers are not the six questions in order, each "
                       "answered true or false."])
    return rows


def missed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r["answered"]]


def said(rows: list[dict]) -> str:
    """What the reader could not answer and what it was missing, in lines."""
    return "\n".join(f"- {r['question']} Missing: {str(r.get('missing') or '').strip() or 'not said'}"
                     for r in rows)


async def read(deck: dict, requester: str, out: Path, attempt: int,
               timeout: float = TIMEOUT_SEC) -> list[dict]:
    """One fresh reader session over the deck's slides before the ask; its
    answers, kept in `out` with the slides it was given."""
    from jarvis.package_author import FreshSession
    choice = engine_choice.choose("package_author", requester=requester)
    out.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"reader-{attempt}-", dir=out))
    slides = before_the_ask(deck)
    (folder / "slides.txt").write_text(slides + "\n", encoding="utf-8")
    answers = folder / ANSWERS_FILE
    receipt = {"engine": choice.engine, "chosen_by": choice.why, "attempt": attempt,
               "started_at": time.time()}
    reader = None
    try:
        reader = FreshSession(folder, choice, folder / "reader-response.txt")
        reader.role = "reader"
        await reader.start("You are a cold reader checking whether a package can be "
                           "followed by someone who was not there.", prompt(slides, answers),
                           timeout)
        receipt["reader_session"] = reader.identity
        rows = answers_in(answers)
        receipt.update(status="read", missed=[r["question"] for r in missed(rows)])
        return rows
    except Refused as error:
        receipt.update(status="failed", error="; ".join(error.problems))
        raise
    except (OSError, RuntimeError, TimeoutError) as error:
        receipt.update(status="failed", error=str(error) or "The reader timed out.")
        raise Refused(["The cold reader did not finish: " + receipt["error"]]) from None
    finally:
        if reader is not None:
            await reader.close()
        receipt["finished_at"] = time.time()
        (folder / "reader-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


async def check(deck: dict, requester: str, out: Path, revise, rebuild) -> dict:
    """The cold read of a built deck. Every question answered: done. Else
    the author is told once what was missing (`revise(message)`, another
    turn of the same author), the deck built again (`rebuild()`, its new
    deck data) and read again; a second miss is Refused with the reader's
    answers."""
    rows = await read(deck, requester, out, 1)
    if not missed(rows):
        return {"status": "read", "attempts": 1}
    await revise(
        "A cold reader, given only the slides before the first decision, could not answer "
        "these questions from them:\n" + said(missed(rows)) + "\nRevise your source so "
        "those slides answer them (the situation fields and your own opening slides are "
        "where), keeping everything else true to the evidence. Then run the same check-only "
        "command again, last, after your final change.")
    rows = await read(await rebuild(), requester, out, 2)
    if missed(rows):
        raise Refused(["A cold reader still could not follow the slides before the first "
                       "decision, after one revision:\n" + said(missed(rows))
                       + "\nIts answers: " + json.dumps(rows, ensure_ascii=False)])
    return {"status": "read", "attempts": 2}
