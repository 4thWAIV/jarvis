"""
briefing_labels.py — the 28 labels a briefing is marked with
(workflow.yaml briefing_labels, from marking-labels.md; the mark step
approved by Tim 2026-11-18, its two practice labels, open-unknown and
deadline-default, approved the same day).

Each label is its name, what a part with it is (`means`, workflow.yaml's
words), what is done with it (`action`), whether it is kept, fixed or
removed (`kind`, marking-labels.md's Count), where it comes from
(`origin`) and the real example marking-labels.md gives for it (`example`,
empty where it gives none). The one list every role that names the labels
reads: the MarkingSession (`for_marking`), the author's keep and the fix and
remove lists, and the editor's actions.
"""
from __future__ import annotations

from dataclasses import dataclass

KEEP, FIX, REMOVE = "keep", "fix", "remove"


@dataclass(frozen=True)
class BriefingLabel:
    name: str
    means: str
    action: str
    kind: str                       # KEEP, FIX or REMOVE
    origin: str
    example: str = ""


LABELS: tuple[BriefingLabel, ...] = (
    BriefingLabel(
        "subject", "It says plainly what the thing under discussion is and why we are here.",
        "keep; expand when thin; must come before any ask", KEEP, "Tim",
        'La Epicure: "This evaluation asks what JARVIS must provide so that running a weekly '
        'business, like La Epicure, becomes an ordinary thing to ask of it."'),
    BriefingLabel(
        "bottom-line", "The answer, status or conclusion itself, in a sentence or two.",
        "keep; first after `subject`", KEEP, "Tim + practice",
        'Knowledge-engine status: "All three engines are built. The document database works '
        '... The graph database works for what is put in it, but nothing adds projects, works, '
        'Work items or decisions, so it is amber."'),
    BriefingLabel(
        "settled-and-open",
        "It says what they have already decided, and where. It then says what, if anything, is "
        "new or still open, and how that came up.",
        "keep; expand when the work follows earlier answers of theirs", KEEP, "Tim + practice",
        'Stage check 1.0.3, which they approved: "You approved the contract at L2, as written '
        '... None of this is being reopened." / "There is no decision left for you here".'),
    BriefingLabel(
        "background",
        "Context they lack and need for this decision: what came before, what the parts are, "
        "what happened and why.",
        "keep; expand when thin", KEEP, "Tim",
        'La Epicure: "La Epicure resells fresh produce in Mérida, Yucatán, and Patricia runs '
        'it. About fifty-five clients..."'),
    BriefingLabel(
        "problem",
        "The concrete problem: what happened, what is broken or wrong, why, and what it causes.",
        "keep; expand when thin", KEEP, "Tim",
        'La Epicure: "There are no business records: none of the hundred seventy-four brain '
        'tools touches products, clients, orders or payments."'),
    BriefingLabel(
        "thing-shown",
        "The thing they are asked to approve or understand, shown and explained rather than only "
        "named. That thing may be a design, the contract text, a plan, or 'the 2' items being "
        "added.",
        "keep; expand when thin", KEEP, "Tim",
        'La Epicure: "a catalog with units, price tiers, customers, a delivery fee rule, '
        'suppliers with arrival days, a rule chain for choosing suppliers, an order document, '
        'delivery evidence, and payments with balances."'),
    BriefingLabel(
        "worked-example",
        "One concrete case followed from start to finish, so an abstract design can be seen "
        "working.",
        "keep; expand when thin (a design or process with no example)",
        KEEP, "Tim + practice (his praise was for the whole page; tying it to the example is "
              "inferred from the list he replied to)",
        'La Epicure: "To make that concrete, follow one remisión. ... Chef prices apply, plus '
        'the fifty peso delivery fee. ... Total: six hundred fifty pesos."'),
    BriefingLabel(
        "evidence",
        "A specific fact with its source that bears on the decision: a name, number, test, "
        "file or measurement.",
        "keep", KEEP, "Tim + practice",
        'Knowledge-engine status: "It also stops at four hundred files a folder and skips any '
        'file over 512 kilobytes."'),
    BriefingLabel(
        "stakes",
        "Why this matters to them or their audience: what changes for them, the benefit, the risk, "
        "and what makes it their call rather than a routine one. It must be concrete. A stock "
        "line such as 'changes to X are yours to decide' is `filler`.",
        "keep; expand when thin", KEEP, "Tim",
        'La Epicure: "The outcome Patricia needs is plain. Each client gets the right produce, '
        'signed for, and every peso owed is tracked until it arrives."'),
    BriefingLabel(
        "options",
        "Each option, with what it would do, what it costs and what it rules out. Doing it "
        "properly now must be one of the options.",
        "keep; expand when thin", KEEP, "Tim + practice",
        'La Epicure: "Reject stops here: JARVIS keeps no business records, and Patricia\'s week '
        'stays on paper and spreadsheets."'),
    BriefingLabel(
        "recommendation", "The answer the author recommends, and the reason for it.",
        "keep", KEEP, "Tim + practice",
        'La Epicure: "I recommend it, because most of the base already exists and the missing '
        'part is what any small business would need."'),
    BriefingLabel(
        "next-steps",
        "What happens after their answer: the plan, the work in order, and follow-up briefs.",
        "keep", KEEP, "Tim",
        'La Epicure: "The smallest complete path is five steps, and each gives Patricia '
        'something usable on its own. First, records and the catalog..."'),
    BriefingLabel(
        "open-unknown",
        "A fact not yet known that affects the decision, with what would settle it and whether "
        "it changes the recommendation.",
        "keep only when it changes the decision; otherwise it is `disclaimer`",
        KEEP, "Practice; approved by Tim 2026-11-18",
        'La Epicure: "four facts must come from Patricia ... And handwriting reading should be '
        'measured on a few real signed remisiones before the photo step is promised."'),
    BriefingLabel(
        "deadline-default", "When the answer is needed, and what happens if no answer comes.",
        "keep; expand when absent on a decision with a time limit",
        KEEP, "Practice; approved by Tim 2026-11-18"),
    BriefingLabel(
        "bare-ask",
        "A request to approve or choose that comes before, or without, what it is about and "
        "what it approves.",
        "fix: move it after `subject` and `background`, and add `thing-shown`", FIX, "Tim",
        '"Do you approve the plan" when the plan was never shown.'),
    BriefingLabel(
        "unexplained-term", "A code, name or term that is used without saying what it is.",
        "fix: explain it in plain words (purpose, behaviour, source, consequence), or drop it",
        FIX, "Tim",
        '(where-the-loader-requirements-came-from): "E1, E3, E2, I9 and I10 and short labels '
        'like \'uninstall cleanup\'".'),
    BriefingLabel(
        "vague-referent",
        "It points at something without naming it, or uses a phrase that says nothing anyone "
        "could check.",
        "fix: name it exactly", FIX, "Tim + practice",
        '"the failed test still need to be fixed"; "every fix for that complaint was another '
        'instruction or a stand-in check".'),
    BriefingLabel(
        "data-without-point",
        "Numbers or results shown without what they measure or why they matter.",
        "fix: say what it shows and why it matters, or cut it", FIX, "Tim",
        'clear-the-last-kit-test opened on "Expected 24, Received 9".'),
    BriefingLabel(
        "unsupported-claim",
        "A figure, estimate or quality claim with no source or basis, or one the record "
        "contradicts.",
        "fix: trace it to its source and correct it, or cut it", FIX, "Tim + practice",
        '(open-additions-to-the-denice-loader): a lock file estimated at "a day of '
        'development".'),
    BriefingLabel(
        "known-to-him", "Background they already hold or have moved past.",
        "remove", REMOVE, "Tim + practice",
        "(the-sales-agent-agreement): who signs it."),
    BriefingLabel(
        "reasks-settled", "It asks them to decide again something they have already decided.",
        "remove; state the decision as `settled-and-open` instead", REMOVE, "Tim"),
    BriefingLabel(
        "off-ask",
        "Material about something they did not ask for, often in place of what they did ask for.",
        "remove", REMOVE, "Tim",
        "(the-knowledge-engine-today): test-suite scores where they had asked for the status of "
        "each engine."),
    BriefingLabel(
        "not-his-question",
        "A question put to them that is obvious, routine or already answered by their rules.",
        "remove; the agent decides and reports what it chose", REMOVE, "Tim"),
    BriefingLabel(
        "technical-detail",
        "Mechanics, the story of how the agent worked, or a lecture on a method, when none of "
        "it changes the decision.",
        "remove from the brief; move it to evidence", REMOVE, "Tim + practice",
        "a lecture on TDD where they needed what broke."),
    BriefingLabel(
        "disclaimer",
        "A hedge, disclaimer, statement of what something is not or what is absent, or an "
        "unknown that changes nothing.",
        "remove", REMOVE, "Tim + practice",
        '(denice-your-work-carried-through 1.0.2): "this is not a claim that it is this".'),
    BriefingLabel(
        "filler",
        "Words about the briefing rather than its content. That includes a count or preview "
        "not followed at once by the items it counts, a claim that something matters, a stock "
        "'why this is yours' line, and a closing summary.",
        "remove; a count stays only when the very next thing is the items counted",
        REMOVE, "Tim + practice"),
    BriefingLabel(
        "repetition", "It says again what an earlier part already said.",
        "remove", REMOVE, "Tim"),
    BriefingLabel(
        "ai-tells",
        "Formulaic AI phrasing, such as 'not just X, it's Y', 'serves as', 'pivotal', "
        "'underscore', 'It's important to note', or padding in threes.",
        "fix: rewrite it plainly, or cut it",
        FIX, "Tim + practice (his word names it; which phrases count comes from practice)"),
)

NAMES: tuple[str, ...] = tuple(label.name for label in LABELS)
BY_NAME: dict[str, BriefingLabel] = {label.name: label for label in LABELS}
# The labels that depend on what Tim knows or has decided: the only ones the
# MarkingSession is given his material, with its knowledge states, for
# (workflow.yaml tim_knowledge who_gets_what reading_steps).
DEPEND_ON_HIM: tuple[str, ...] = ("known-to-him", "reasks-settled", "settled-and-open")


def of_kind(*kinds: str) -> list[BriefingLabel]:
    """The labels of these kinds, in their order."""
    return [label for label in LABELS if label.kind in kinds]


def for_marking():
    """The 28 labels as the MarkingSession takes them: name, what it means,
    its example."""
    from jarvis import sessions
    return [sessions.Label(label.name, label.means, label.example) for label in LABELS]
