"""The forced briefing pipeline: what the fresh author does in Markdown
before any slide exists, and the typed questions that judge it.

The fresh author (package_author.author) first writes, in its own folder,
`prep.md` (does Tim care; what he already knows, from his record; the
background he lacks; his first ten questions with sourced answers,
UNANSWERED where not found; every fact scored 1-5 for how much it changes
his answer) and `script.md` (the briefing ordered by those scores: the
answer and the ask on slide 1, each decision's own explanation just before
it, follow-up questions last). Then, at most `ROUNDS` times:

- three editors fix the script (`EDITORS`: cut, tighten, specifics). Each is
  one call to the author's engine that proposes exact before/after edits;
  each edit is held to its editor's rule here (cut only deletes, tighten
  only shortens, specifics names its prep source or marks UNANSWERED),
  applied to script.md and logged in `edits.md`. Vague claims whose fact is
  not in the prep go back to the author to find (`to_prep`).
- four judges decide (`JUDGES`: cares, decision ready, background right,
  order by relevance), each a typed question put through `decide`.
- whatever did not pass goes back to the SAME author session as one more
  turn, and it revises prep.md and script.md.

Each judge's asks are jurors that keep their own session across the rounds
(`Seat`: resumed each round, never a fresh judge with no memory): from
round 2 a juror is shown its own earlier answer and reason and the author's
reply to the findings, judges first whether what it asked before was met,
and may raise a new problem only when it does not reverse an earlier ask of
its own; one that would is quoted (`reverses`) and goes back to the author
as an ask it may keep its earlier answer to, never a reason to fail.

A script that passes every judge, with nothing sent back to the prep, is
the only thing the deck may be written from (`DECK_STEP`). One that cannot
pass in `ROUNDS` rounds is refused with the judges' findings. prep.md,
script.md and edits.md travel in the package under `.jarvis/briefing/`
(`carry`).

`decide(question_id, text, context) -> {answer, probability}` is the one
interface for a typed question (yes/no, a choice, or 0-10): asked of the
author's engine `ASKS` times, the answer the one most of them gave and the
probability the share that agreed with it. Every call is recorded through
records.py (`/briefing/decisions/<id>.decision`: the question, a digest of
its input, every answer, the answer, its probability, the package by its
author session, and when), so his later verdict on that package can be
joined to it and a cheaper model can later take over one question.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import statistics
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from jarvis.decision_package import Refused

PREP, SCRIPT, EDITS = "prep.md", "script.md", "edits.md"
FILES = (PREP, SCRIPT, EDITS)
# Where the three travel in the package, beside `.jarvis/author/`.
FOLDER = (".jarvis", "briefing")
# What the author writes in place of a script when Tim would not want it.
NOT_FOR_TIM = "not-for-tim.md"
ROUNDS = 3
ASKS = 3
ASK_AT_ONCE = 4
ASK_TIMEOUT_SEC = 600
RECORDS = "/briefing/decisions"
UNANSWERED = "UNANSWERED"
# The headings prep.md is written under, in order (the skill gives them).
PREP_SECTIONS = (
    "Does Tim care",
    "What he already knows",
    "Background he lacks",
    "His first ten questions",
    "Facts, scored",
)
# The words of the author's first turn the stand-in engines in tests look for.
FIRST_STEP_MARK = "Write prep.md and script.md now"


# -- typed questions ----------------------------------------------------------------------

YES_NO, CHOICE, SCALE = "yes/no", "choice", "0-10"


@dataclass(frozen=True)
class Question:
    """One typed question: what is asked, how it is answered, and which
    answers pass."""
    id: str
    kind: str
    asks: str
    options: tuple[str, ...] = ()
    passing: tuple = ()          # yes/no and choice: the answers that pass
    at_least: int = 0            # 0-10: the lowest answer that passes

    def valid(self, answer) -> bool:
        if self.kind == YES_NO:
            return answer in ("yes", "no")
        if self.kind == CHOICE:
            return answer in self.options
        return isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer <= 10

    def passes(self, answer) -> bool:
        if self.kind == SCALE:
            return isinstance(answer, int) and answer >= self.at_least
        return answer in self.passing

    def form(self) -> str:
        if self.kind == YES_NO:
            return '"yes" or "no"'
        if self.kind == CHOICE:
            return "exactly one of " + ", ".join(f'"{o}"' for o in self.options)
        return "a whole number from 0 to 10"


QUESTIONS: dict[str, Question] = {q.id: q for q in (
    Question("cares", YES_NO,
             "Going by the prep's record of Tim's own words and what waits on him, would Tim "
             "want this briefing at all, now? Answer no when nothing in it needs him, when it "
             "only repeats what he has, or when he has said he does not want it.",
             passing=("yes",)),
    Question("decision_ready", YES_NO,
             "Can Tim decide from this script alone? Yes only when slide 1 states the answer "
             "and the ask (Approve or Reject, or named options) with the recommendation, each "
             "decision's own explanation comes immediately before that decision, every term "
             "and code it uses is explained, and nothing he needs is left out or left to his "
             "memory of other work.",
             passing=("yes",)),
    Question("background", CHOICE,
             "Is the script's background right for Tim, judged against what the prep says he "
             "already knows and what he lacks? \"right\": it gives what he lacks, enough of "
             "it, and nothing he already has; \"lacks\": something he needs is missing; "
             "\"repeats\": it tells him what he already knows; \"both\".",
             options=("right", "lacks", "repeats", "both"), passing=("right",)),
    Question("order", SCALE,
             "How closely does the script's order follow the prep's fact scores, most "
             "relevant first? 10: every slide comes in score order, the answer and ask on "
             "slide 1, follow-up questions last; 0: the decisive facts come last.",
             at_least=8),
)}
JUDGES = ("cares", "decision_ready", "background", "order")


def input_digest(text: str, context: dict) -> str:
    """The SHA-256 of what a question was asked about: its text and the
    prep it was judged against."""
    whole = hashlib.sha256()
    whole.update(text.encode("utf-8"))
    whole.update(b"\0")
    whole.update(str(context.get("prep") or "").encode("utf-8"))
    return whole.hexdigest()


def _ask_prompt(question: Question, text: str, context: dict) -> str:
    return (
        "You answer one narrow question about a briefing for Tim, who owns the work and "
        "decides on it. Answer only from the prep and the script below: do not open, search "
        "or read anything else.\n\nThe question: " + question.asks + "\n\nReply with one JSON "
        "object and nothing else: {\"answer\": " + question.form() + ", \"why\": <one "
        "sentence, quoting the script or prep where it falls short, or saying why it "
        "passes>}.\n\n--- prep.md ---\n" + str(context.get("prep") or "") +
        "\n\n--- script.md ---\n" + text)


def _juror_prompt(question: Question, text: str, context: dict, seat: "Seat") -> str:
    """A juror's first ask, or from round 2 the same juror's next turn: its
    own earlier answers, the author's reply to the findings, and the same
    question about the revised script, judged against what it asked before."""
    if not seat.said:
        return _ask_prompt(question, text, context)
    earlier = "\n".join(f"- Round {s['round']}: you answered {json.dumps(s['answer'])}: "
                         f"{s['why'] or '(no reason given)'}" for s in seat.said)
    reply = str((context.get("replies") or {}).get(context.get("round", 0) - 1) or "").strip()
    return (
        f"Round {context.get('round')}. You are the same juror as before, answering the same "
        "question about the revised briefing. What you said before:\n" + earlier +
        "\n\nThe author's reply to the findings it was given:\n" +
        (reply or "(the author gave no reply in words; read the revised files)") +
        "\n\nFirst judge whether each problem you raised before is met in the revised script. "
        "Then answer the question. You may raise a new problem only when it does not reverse "
        "something you asked for earlier. If a problem you see would reverse one of your earlier "
        "asks, do not fail the script for it: quote that earlier ask in \"reverses\"; the "
        "author may keep its earlier answer to it. Answer only from the prep and the script "
        "below.\n\nThe question: " + question.asks + "\n\nReply with one JSON object and "
        "nothing else: {\"answer\": " + question.form() + ", \"why\": <one sentence, quoting "
        "the script or prep where it falls short, or saying why it passes>, \"earlier_met\": "
        "<true if every problem you raised before is met, else false>, \"reverses\": <your "
        "earlier ask, quoted, that a problem you see would reverse, or null>}.\n\n--- prep.md "
        "---\n" + str(context.get("prep") or "") + "\n\n--- script.md ---\n" + text)


def _last_json(text: str):
    """The last whole (outermost) JSON object in a model's reply, or None."""
    decoder = json.JSONDecoder()
    found, at = None, 0
    while (start := text.find("{", at)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            at = start + 1
            continue
        if isinstance(value, dict):
            found = value
        at = end
    return found


def _answer_of(question: Question, reply: str) -> Optional[dict]:
    value = _last_json(reply or "")
    if value is None:
        return None
    answer = value.get("answer")
    if isinstance(answer, str):
        answer = answer.strip().lower()
        if question.kind == SCALE and answer.isdigit():
            answer = int(answer)
    if isinstance(answer, float) and answer.is_integer():
        answer = int(answer)
    if not question.valid(answer):
        return None
    reverses = value.get("reverses")
    return {"answer": answer, "why": str(value.get("why") or "").strip()[:1000],
            "earlier_met": value.get("earlier_met") if isinstance(value.get("earlier_met"), bool)
            else None,
            "reverses": str(reverses).strip()[:1000] if isinstance(reverses, str)
            and reverses.strip() else None}


def agreement(question: Question, answers: list) -> tuple:
    """The answer most asks gave and the share that agreed with it. For a
    0-10 question the answer is the median (rounded down) and an ask agrees
    when it is within one of it."""
    if not answers:
        return None, 0.0
    if question.kind == SCALE:
        middle = int(statistics.median_low(answers))
        return middle, sum(1 for a in answers if abs(a - middle) <= 1) / len(answers)
    counts: dict = {}
    for a in answers:
        counts[a] = counts.get(a, 0) + 1
    best = max(counts.values())
    # A tie goes to the answer that does not pass: a judge in doubt fails.
    tied = [a for a in counts if counts[a] == best]
    answer = next((a for a in tied if not question.passes(a)), tied[0])
    return answer, best / len(answers)


@dataclass
class Seat:
    """One juror's own session, kept across the rounds: where it runs and
    the session the provider named, resumed on its next ask; and what it
    answered each round (`said`)."""
    home: Optional[Path] = None
    session: Optional[str] = None
    said: list = field(default_factory=list)


async def _ask_engine(prompt: str, choice, folder: Path, timeout: float = ASK_TIMEOUT_SEC,
                      seat: Optional[Seat] = None) -> str:
    """One turn of the author's engine choice; its reply. A new session,
    or with a `seat` that has one, that juror's own session resumed in its
    own folder (a session is resumed where it began)."""
    from jarvis.package_author import FreshSession
    folder.mkdir(parents=True, exist_ok=True)
    if seat is not None and seat.home is not None:
        home = seat.home
    else:
        home = Path(tempfile.mkdtemp(prefix="ask-", dir=folder))
    reply = home / f"reply-{len(list(home.glob('reply-*.txt'))) + 1}.txt"
    session = None
    try:
        session = FreshSession(home, choice, reply,
                               resume=seat.session if seat is not None else None)
        session.role = "briefing judge"
        await session.start("You answer one narrow question about a briefing and reply with "
                            "JSON only.", prompt, timeout)
        if seat is not None:
            if seat.session and session.identity != seat.session:
                raise OSError("A juror's resumed session is not its own.")
            seat.home, seat.session = home, session.identity
        return reply.read_text(encoding="utf-8") if reply.exists() else ""
    finally:
        if session is not None:
            await session.close()


# The engine every typed question and editor is asked through: (prompt,
# engine choice, working folder) -> the reply's text. Tests replace it.
ASK: Callable[..., Awaitable[str]] = _ask_engine
# An ask the engine could not answer (a turn failed: a provider's rate
# limit, a timeout, an exit) is asked again after these waits, in seconds,
# before it counts as no answer.
ASK_RETRY_SEC = (30.0, 90.0)


async def _asked(prompt: str, choice, folder: Path, **kwargs) -> str:
    """ASK, asked again after each of ASK_RETRY_SEC when its turn fails;
    the last failure is raised."""
    for wait in (*ASK_RETRY_SEC, None):
        try:
            return await ASK(prompt, choice, folder, **kwargs)
        except (OSError, RuntimeError, TimeoutError):
            if wait is None:
                raise
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


def _record(question: Question, text: str, context: dict, asked: list[dict], answer,
            probability: float) -> str:
    from jarvis import records
    call = uuid.uuid4().hex
    choice = context.get("choice")
    data = {"question": question.id, "kind": question.kind, "asks": question.asks,
            "input_sha256": input_digest(text, context), "answers": asked,
            "answer": answer, "probability": probability,
            "package": str(context.get("package") or ""),
            "round": context.get("round"),
            "engine": getattr(choice, "engine", None), "model": getattr(choice, "model", None),
            "at": time.time()}
    try:
        records.write(f"{RECORDS}/{call}.decision", data, change="briefing-decision",
                      summary=f"{question.id}: {answer} ({probability:.2f})")
    except Exception as error:      # the engine down, or refusing: never an unrecorded call
        raise Refused([f"The answer to {question.id} could not be recorded: {error}"]) from None
    return call


async def adecide(question_id: str, text: str, context: dict) -> dict:
    """The typed question `question_id` about `text`, asked of the author's
    engine `ASKS` times: {answer, probability, why, record}. `context`
    carries the prep (`prep`), the engine choice (`choice`), where the asks
    run (`folder`), the package by its author session (`package`) and the
    round."""
    question = QUESTIONS.get(question_id)
    if question is None:
        raise ValueError(f"No typed question {question_id!r}.")
    limit = context.get("limit") or asyncio.Semaphore(ASK_AT_ONCE)
    folder = Path(context.get("folder") or tempfile.mkdtemp(prefix="briefing-asks-"))
    # Each ask is a juror with its own seat, kept across the rounds of one
    # briefing (`context["seats"]`, made by `run`); with none, a new one.
    seats = context.get("seats", {})
    jurors = [seats.setdefault((question_id, n), Seat()) for n in range(ASKS)]

    async def once(seat: Seat) -> Optional[dict]:
        prompt = _juror_prompt(question, text, context, seat)
        async with limit:
            try:
                reply = await _asked(prompt, context.get("choice"), folder, seat=seat)
            except (OSError, RuntimeError, TimeoutError):
                return None
        said = _answer_of(question, reply)
        if said is not None:
            seat.said.append({"round": context.get("round"), **said})
        return said

    asked = [a for a in await asyncio.gather(*(once(s) for s in jurors)) if a is not None]
    if not asked:
        raise Refused([f"No ask of the {question_id} question gave an answer that could be "
                       f"read."])
    answer, probability = agreement(question, [a["answer"] for a in asked])
    record = _record(question, text, context, asked, answer, probability)
    return {"answer": answer, "probability": probability,
            "why": [a["why"] for a in asked if a["answer"] == answer and a["why"]],
            "reverses": [a["reverses"] for a in asked if a.get("reverses")],
            "record": record}


def decide(question_id: str, text: str, context: dict) -> dict:
    """`adecide` for a caller with no event loop running."""
    return asyncio.run(adecide(question_id, text, context))


def passed(question_id: str, decided: dict) -> bool:
    """A judge passes only on a passing answer most of its asks agreed on."""
    return QUESTIONS[question_id].passes(decided["answer"]) and decided["probability"] > 0.5


# -- editors -----------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(word in it for word in small)


def _cut_allows(edit: dict) -> Optional[str]:
    before, after = edit["before"], edit["after"]
    if len(after) >= len(before) or not _subsequence(_words(after), _words(before)):
        return "the cut editor may only delete words"
    return None


def _tighten_allows(edit: dict) -> Optional[str]:
    if not edit["after"].strip() or len(edit["after"]) >= len(edit["before"]):
        return "the tighten editor may only shorten, never add or delete all of it"
    return None


def _specifics_allows(edit: dict) -> Optional[str]:
    if UNANSWERED not in edit["after"] and not str(edit.get("source") or "").strip():
        return "the specifics editor names the prep's source of each fact, or marks it UNANSWERED"
    return None


@dataclass(frozen=True)
class Editor:
    id: str
    rule: str
    allows: Callable[[dict], Optional[str]]
    sends_back: bool = False


EDITORS = (
    Editor("cut",
           "Delete talk about the briefing itself, disclaimers, hedges, self-ranking (\"one of "
           "them matters most\", \"most importantly\"), restatement of a point already made, "
           "and anything that teaches Tim what the prep says he already knows. A count stays "
           "only when the very next thing is the items counted (\"Two questions:\" followed at "
           "once by the two questions). You may only delete: each edit's after is its before "
           "with words removed, or empty to remove it all.",
           _cut_allows),
    Editor("tighten",
           "Shorten each wordy passage to the plain fact. You may not add anything: each "
           "edit's after says the same thing in fewer characters.",
           _tighten_allows),
    Editor("specifics",
           "Find every vague claim: an unnamed test, file, run or person (\"the failed test\"), "
           "\"some issues\", an adjective in place of a figure (\"significantly faster\"). "
           "Replace each with the exact fact from the prep, giving the prep section or source "
           "line it comes from as the edit's source. Where the prep holds no such fact, never "
           "guess: list the claim under to_prep for the author to find, or, when nobody can "
           "know it, make the after say UNANSWERED.",
           _specifics_allows, sends_back=True),
)


def _edit_prompt(editor: Editor, script: str, prep: str) -> str:
    back = (", \"to_prep\": [{\"claim\": <the exact vague text in the script>, \"why\": <the fact "
            "it lacks>}]" if editor.sends_back else "")
    return (
        "You are the " + editor.id + " editor of a briefing for Tim. Editors fix; they do not "
        "judge. Your one rule: " + editor.rule + "\n\nWork only from the prep and the script "
        "below; do not open or read anything else. Each before must be text copied exactly "
        "from script.md, appearing there once. Reply with one JSON object and nothing else: "
        "{\"edits\": [{\"before\": <exact text>, \"after\": <its replacement>, \"why\": <one "
        "line>, \"source\": <prep section or source, or empty>}]" + back + "}. An empty list "
        "when nothing breaks your rule.\n\n--- prep.md ---\n" + prep +
        "\n\n--- script.md ---\n" + script)


@dataclass
class Edited:
    applied: list[dict] = field(default_factory=list)
    refused: list[tuple[dict, str]] = field(default_factory=list)
    to_prep: list[dict] = field(default_factory=list)


def apply_edits(editor: Editor, script: str, proposed: dict) -> tuple[str, Edited]:
    """Hold each proposed edit to the editor's rule and apply those that
    keep it, one at a time against the script as it stands."""
    result = Edited()
    for edit in proposed.get("edits") or []:
        if not isinstance(edit, dict) or not isinstance(edit.get("before"), str) \
                or not isinstance(edit.get("after"), str) or not edit["before"]:
            continue
        edit = {k: str(edit.get(k) or "") for k in ("before", "after", "why", "source")}
        if edit["after"] == edit["before"]:
            continue
        found = script.count(edit["before"])
        why = (editor.allows(edit) if found == 1 else
               "its before is not in the script" if not found else
               "its before appears more than once in the script")
        if why:
            result.refused.append((edit, why))
            continue
        script = script.replace(edit["before"], edit["after"], 1)
        result.applied.append(edit)
    if editor.sends_back:
        result.to_prep = [{"claim": str(c.get("claim") or ""), "why": str(c.get("why") or "")}
                          for c in proposed.get("to_prep") or []
                          if isinstance(c, dict) and str(c.get("claim") or "").strip()]
    return script, result


def _quoted(text: str) -> str:
    return '"' + " ".join(text.split()) + '"'


def log_edits(path: Path, round_no: int, editor: Editor, edited: Edited) -> None:
    """One section of edits.md: every change before -> after, and what was
    not applied or went back to the prep."""
    lines = [f"## round {round_no}, {editor.id}", ""]
    if not edited.applied:
        lines.append("No changes.")
    for n, e in enumerate(edited.applied, 1):
        note = "; ".join(x for x in (e["why"], e["source"] and "source: " + e["source"]) if x)
        lines.append(f"{n}. {_quoted(e['before'])} -> {_quoted(e['after'])}"
                     + (f" ({note})" if note else ""))
    for e, why in edited.refused:
        lines.append(f"- Not applied, {why}: {_quoted(e['before'])} -> {_quoted(e['after'])}")
    for c in edited.to_prep:
        lines.append(f"- Back to the prep: {_quoted(c['claim'])}"
                     + (f" ({c['why']})" if c["why"] else ""))
    with path.open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n\n")


async def edit(editor: Editor, folder: Path, context: dict) -> Edited:
    """One editor's pass over script.md: proposed by the author's engine,
    held to the rule, applied and logged."""
    script_path = folder / SCRIPT
    script = script_path.read_text(encoding="utf-8")
    prep = (folder / PREP).read_text(encoding="utf-8")
    try:
        reply = await _asked(_edit_prompt(editor, script, prep), context.get("choice"),
                             Path(context["folder"]))
    except (OSError, RuntimeError, TimeoutError) as error:
        raise Refused([f"The {editor.id} editor did not finish: {error}"]) from None
    proposed = _last_json(reply or "")
    if proposed is None:
        raise Refused([f"The {editor.id} editor's reply was not JSON."])
    script, edited = apply_edits(editor, script, proposed)
    script_path.write_text(script, encoding="utf-8")
    log_edits(folder / EDITS, context["round"], editor, edited)
    return edited


# -- the author's steps ------------------------------------------------------------------

FIRST_STEP = (
    FIRST_STEP_MARK + ", in your working directory, following the skill's briefing pipeline, "
    "and nothing else: no deck, no package, no presentation.yaml yet. prep.md has these "
    "sections, each a `## ` heading in this order: " + "; ".join(PREP_SECTIONS) + ". Research "
    "Tim's record (his conversation, messages, answered packages, the Work item's history) "
    "and the evidence with JARVIS's read tools (the jarvis_agent MCP server); every answer "
    "in prep.md names its source, and one you cannot find, nor the sender give, says "
    "UNANSWERED. script.md is the briefing in the order of the "
    "prep's fact scores, highest first: the answer and the ask on slide 1, each decision's own "
    "explanation immediately before it, follow-up questions last. If the prep shows Tim would "
    "not want this now, write " + NOT_FOR_TIM + " saying why, and no script. Editors and "
    "judges read your script next; their findings come back to you in this session.")

DECK_STEP = (
    "The script passed its editors and judges. Now write the package from script.md alone: "
    "its slides, words, narration and decisions come from script.md, in its order, and "
    "nothing that is not in it. Do not change prep.md, script.md or edits.md. ")


def prep_problems(text: str) -> list[str]:
    """What prep.md lacks of its required sections, in order."""
    headings = [h.strip().lower() for h in re.findall(r"^##\s+(.+)$", text, re.M)]
    problems, at = [], 0
    for section in PREP_SECTIONS:
        hit = next((i for i, h in enumerate(headings) if i >= at and
                    h.lstrip("0123456789. ").startswith(section.lower())), None)
        if hit is None:
            problems.append(f"prep.md has no `## {section}` section in its place.")
        else:
            at = hit + 1
    return problems


def _read_step(folder: Path) -> None:
    """After an author turn: Refused when it said Tim would not want this,
    or left no prep and script the pipeline can read."""
    not_for_tim = folder / NOT_FOR_TIM
    if not_for_tim.exists():
        raise Refused(["Not sent: the author found Tim would not want this now. "
                       + not_for_tim.read_text(encoding="utf-8").strip()[:2000]])
    missing = [name for name in (PREP, SCRIPT) if not (folder / name).is_file()
               or not (folder / name).read_text(encoding="utf-8").strip()]
    if missing:
        raise Refused(["The author wrote no " + " or ".join(missing) + ": the briefing starts "
                       "from its prep and script."])
    problems = prep_problems((folder / PREP).read_text(encoding="utf-8"))
    if problems:
        raise Refused(problems)


def _findings(verdicts: dict[str, dict]) -> list[str]:
    out = []
    for judge, decided in verdicts.items():
        if passed(judge, decided):
            continue
        why = " ".join(decided["why"]) or "no reason given"
        out.append(f"{judge}: answered {decided['answer']} (probability "
                   f"{decided['probability']:.2f}). {why}")
    return out


def _reversals(verdicts: dict[str, dict]) -> list[str]:
    """What jurors would have reversed of their own earlier asks: told to
    the author as asks it may keep its earlier answer to, never a failure."""
    return [f"{judge}: a juror saw a problem that would reverse its own earlier ask "
            f"({_quoted(asked)}): you may keep your earlier answer to that ask."
            for judge, decided in verdicts.items() for asked in decided.get("reverses") or []]


def _back_to_author(round_no: int, edits: list[Edited], findings: list[str],
                    kept: list[str] = ()) -> str:
    lines = [f"Round {round_no} of at most {ROUNDS}. The editors' changes are in script.md "
             f"and logged in {EDITS}; keep them."]
    back = [c for e in edits for c in e.to_prep]
    if back:
        lines.append("The specifics editor sends these vague claims back to the prep: find "
                     "each exact fact with JARVIS's read tools and put it, with its source, in "
                     "prep.md and script.md; one nobody can know is marked UNANSWERED.")
        lines += [f"- {_quoted(c['claim'])}" + (f": {c['why']}" if c["why"] else "")
                  for c in back]
    if findings:
        lines.append("The judges did not pass the script:")
        lines += [f"- {f}" for f in findings]
    if kept:
        lines.append("These would reverse what a juror asked of you before; keep your earlier "
                     "answer to them if you choose:")
        lines += [f"- {k}" for k in kept]
    lines.append("Revise prep.md and script.md in place, keeping prep.md's headings, and "
                 "write nothing else yet: no deck.")
    return "\n".join(lines)


async def run(folder: Path, turn: Callable[[str], Awaitable[Optional[str]]],
              context: dict) -> dict:
    """The rounds over the author's prep and script in `folder`. `turn`
    is another turn of the same author session, returning what it said.
    Every juror keeps its seat across the rounds (`seats`), and sees the
    author's reply (`replies`, by round). Returns what each round's judges
    said; Refused when the script cannot pass in `ROUNDS` rounds."""
    _read_step(folder)
    context = dict(context, limit=asyncio.Semaphore(ASK_AT_ONCE), seats={}, replies={})
    context.setdefault("folder", str(folder.parent / (folder.name + "-asks")))
    (folder / EDITS).touch()
    rounds = []
    findings: list[str] = []
    for round_no in range(1, ROUNDS + 1):
        context["round"] = round_no
        edits = [await edit(editor, folder, context) for editor in EDITORS]
        script = (folder / SCRIPT).read_text(encoding="utf-8")
        context["prep"] = (folder / PREP).read_text(encoding="utf-8")
        judged = await asyncio.gather(*(adecide(j, script, context) for j in JUDGES))
        verdicts = dict(zip(JUDGES, judged))
        findings = _findings(verdicts)
        back = [c for e in edits for c in e.to_prep]
        rounds.append({"round": round_no, "edits": sum(len(e.applied) for e in edits),
                       "to_prep": len(back),
                       "judges": {j: {"answer": v["answer"], "probability": v["probability"],
                                      "record": v["record"], "reverses": v["reverses"]}
                                  for j, v in verdicts.items()}})
        if not findings and not back:
            return {"status": "passed", "rounds": rounds, "sha256": digests(folder)}
        if round_no == ROUNDS:
            break
        said = await turn(_back_to_author(round_no, edits, findings, _reversals(verdicts)))
        context["replies"][round_no] = said if isinstance(said, str) else ""
        _read_step(folder)
    left = findings or ["specifics: vague claims still went back to the prep in the last round "
                        "(" + EDITS + ")."]
    raise Refused([f"The briefing did not pass its judges in {ROUNDS} rounds; nothing was "
                   f"built. The judges' findings:"] + left)


def digests(folder: Path) -> dict[str, str]:
    """The SHA-256 of each of the briefing's files, as it passed."""
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in FILES}


def carry(archive: Path, folder: Path) -> None:
    """Put prep.md, script.md and edits.md in the zip under
    `.jarvis/briefing/`. Refused when the author's folder lacks one, or
    when one changed after the script passed (the digests its receipt
    keeps, package_author's)."""
    from jarvis import package_signature
    missing = [name for name in FILES if not (folder / name).is_file()]
    if missing:
        raise Refused(["The briefing's " + ", ".join(missing) + " is missing beside the "
                       "authored source: every package carries its prep, script and edits."])
    try:
        receipt = json.loads((folder / "author-receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = {}
    passed_as = ((receipt.get("briefing") or {}).get("sha256") or {}) if isinstance(receipt, dict) else {}
    changed = [name for name, digest in digests(folder).items()
               if name in passed_as and passed_as[name] != digest]
    if changed:
        raise Refused(["The briefing's " + ", ".join(changed) + " changed after its script "
                       "passed the editors and judges: the package carries what passed."])
    package_signature.add_files(archive, {"/".join(FOLDER) + "/" + name:
                                          (folder / name).read_bytes() for name in FILES})
