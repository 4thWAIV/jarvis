"""What a wait on Tim waits for, and ending it the moment he has answered.

A workflow's wait (`workflow_report.py event <id> wait_started ... --until
<link>`) and a Work item's piece waiting on him (`work_report.py piece ...
--state waiting_on_tim --until <link>`) may name the one answer they wait
for, its link:

    decision:<key>               a decision of the same workflow or work item
    work:<item id>:<key>         a Work item's decision
    workflow:<workflow id>:<key> a workflow's decision
    package:<id or name>         a package, until he signs it off
    package:<id or name>:<id>    one decision of a package (or its sign-off)

Whatever route he answers by (a workflow's or a Work item's decision, on
the page or by voice; a package's decision through its decide.py; signing a
package off), the store that records the answer calls `settle` in the same
transaction, and every open wait and waiting piece whose link it answers is
ended there and then as his answer, with when and how (`wait_ended` in a
workflow, a piece moved to its next state, ready by default, on a Work
item). `sweep` does the same for a link already answered or signed off when
it is found (at server start, and each time either record is read), so a
wait never outlives his answer.

A wait with no link, open for more than UNLINKED_AFTER_SEC, is shown as
"waiting, no link to an answer" (worked out when read, never stored).

The stores' own writers do the writing; this module only knows what a link
is, which answer ends which link, and who to tell once it is committed.
Work items, workflows and packages are documents (`records`): "the same
transaction" is one `records.atomic` unit, the answer's writer's own, which
`settle` stages every ended wait into, so the answer and every wait it ends
land in one conditioned write, or not at all.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

DECISION, WORK, WORKFLOW, PACKAGE = "decision", "work", "workflow", "package"
# How an answer came that is not his words: its package signed off, or the
# decision withdrawn by the agent that asked it (which also releases a wait).
SIGNED_OFF, WITHDRAWN = "signed_off", "withdrawn"
KINDS = (DECISION, WORK, WORKFLOW, PACKAGE)

# How long a wait with no link may stay open before it is flagged.
UNLINKED_AFTER_SEC = 24 * 3600

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_REF = re.compile(r"[A-Za-z0-9._-]{1,128}")


class LinkRefused(ValueError):
    """A link that names a kind of answer but not one it can be."""


@dataclass(frozen=True)
class Link:
    kind: str                 # one of KINDS
    ref: str                  # the item, workflow or package; "" for DECISION
    key: Optional[str]        # the decision; None for a whole package

    def __str__(self) -> str:
        return ":".join(p for p in (self.kind, self.ref, self.key) if p)


def parse(text) -> Optional[Link]:
    """The link `text` names, or None when it names none (a plain "until
    when", kept as words). Refused (LinkRefused) when it starts like a link
    but is not one."""
    text = str(text or "").strip()
    kind, sep, rest = text.partition(":")
    if not sep or kind.lower() not in KINDS:
        return None
    kind, parts = kind.lower(), rest.split(":")
    if kind == DECISION:
        if len(parts) == 1 and _SLUG.fullmatch(parts[0]):
            return Link(DECISION, "", parts[0])
        raise LinkRefused("A decision link is decision:<key>, its key in lowercase letters, "
                          "digits and dashes.")
    if kind in (WORK, WORKFLOW):
        if len(parts) == 2 and _REF.fullmatch(parts[0]) and _SLUG.fullmatch(parts[1]):
            return Link(kind, parts[0], parts[1])
        raise LinkRefused(f"A {kind} link is {kind}:<id>:<decision key>.")
    if 1 <= len(parts) <= 2 and _REF.fullmatch(parts[0]) and (
            len(parts) == 1 or _REF.fullmatch(parts[1])):
        return Link(PACKAGE, parts[0], parts[1] if len(parts) == 2 else None)
    raise LinkRefused("A package link is package:<id or name>, or "
                      "package:<id or name>:<decision id>.")


@dataclass(frozen=True)
class Answer:
    """One answer of his, as what it ends: a decision of a workflow, a Work
    item or a package (`key`), or a package signed off (`key` None). `refs`
    are every name its record goes by (a package's id, name, name-version
    and inbox ids)."""
    kind: str                 # work, workflow or package
    refs: frozenset
    key: Optional[str]
    how: str                  # page, voice, signed_off ...
    at: float

    def ends(self, link: Link, owner_kind: str, owner_id: str) -> bool:
        """Whether this answer ends `link`, held by a wait of `owner_kind`
        (work or workflow) `owner_id`."""
        if link.kind == DECISION:
            return (self.kind == owner_kind and owner_id in self.refs
                    and self.key is not None and link.key == self.key)
        if link.kind != self.kind or link.ref not in self.refs:
            return False
        if self.kind == PACKAGE:
            return self.key is None or link.key == self.key
        return link.key == self.key

    @property
    def by(self) -> str:
        """Whose act ended it: his (`user`), or the asker's withdrawal."""
        return "asker" if self.how == WITHDRAWN else "user"

    def said(self) -> str:
        """What ended it, in JARVIS's words."""
        if self.kind == PACKAGE and self.key is None:
            return "Tim signed off the package"
        if self.how == WITHDRAWN:
            return f"Its asker withdrew {self.key}"
        if self.how == SIGNED_OFF:
            return f"Tim signed off the package {self.key} names"
        return f"Tim answered {self.key}"


def answer(kind: str, refs, key: Optional[str], how: str, at: Optional[float] = None) -> Answer:
    return Answer(kind, frozenset(r for r in refs if r), key, how,
                  time.time() if at is None else at)


@dataclass
class Settled:
    """Which records an answer moved, to be told once committed."""
    workflows: set = field(default_factory=set)
    work: set = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.workflows or self.work)

    def add(self, other: "Settled") -> None:
        self.workflows |= other.workflows
        self.work |= other.work


def settle(unit, found: Answer, now: float) -> Settled:
    """End every open wait and waiting piece `found` answers, staged in the
    caller's unit (`records.atomic`), the one its answer is written in. The
    caller's unit lands, then it calls `tell`."""
    from jarvis import work_store, workflow_store
    return Settled(workflows=workflow_store.end_waits(unit, found, now),
                   work=work_store.end_waits(unit, found, now))


def settle_now(found: Answer) -> Settled:
    """`settle` in a unit of its own, for an answer recorded outside
    JARVIS's records (a package's decide.py writes its own file), then
    told. His answer: written with full sync."""
    from jarvis import records
    now = time.time()
    moved = records.atomic(lambda unit: settle(unit, found, now), by=records.TIM,
                           change="wait_ended", summary=found.said(), full_sync=True)
    tell(moved)
    return moved


# ── after the commit ────────────────────────────────────────────────────

# By the listener's module and name, so a module loaded again replaces its own.
_listeners: dict[str, Callable[[Settled], None]] = {}


def on_settled(listener: Callable[[Settled], None]) -> None:
    """Call `listener` with every committed Settled (the server's hints)."""
    _listeners[f"{listener.__module__}.{listener.__qualname__}"] = listener


def tell(moved: Settled) -> None:
    """Tell the listeners, after the commit. Never raises."""
    if not moved:
        return
    for listener in list(_listeners.values()):
        try:
            listener(moved)
        except Exception:
            log.warning("wait links: a listener failed", exc_info=True)


# ── answers already given ───────────────────────────────────────────────

# How a package link is found answered: (ref, key or None) -> the Answer
# that ends it, or None while it is not. Set by the server (the package
# inbox is its), so this module never reads a package itself.
PackageLookup = Callable[[str, Optional[str]], Optional[Answer]]
_package_lookup: Optional[PackageLookup] = None


def look_up_packages_with(lookup: Optional[PackageLookup]) -> None:
    global _package_lookup
    _package_lookup = lookup


def answered_package(ref: str, key: Optional[str]) -> Optional[Answer]:
    if _package_lookup is None:
        return None
    try:
        return _package_lookup(ref, key)
    except Exception:
        log.warning("wait links: package %s was not looked up", ref, exc_info=True)
        return None


def already(unit, link: Link, owner_kind: str, owner_id: str) -> Optional[Answer]:
    """The answer of his that already ends `link` (held by a wait of
    `owner_kind` `owner_id`), or None while there is none; the record it is
    read from is read through `unit` (a condition of its write)."""
    from jarvis import work_store, workflow_store
    stores = {WORK: work_store.decision_answer, WORKFLOW: workflow_store.decision_answer}
    if link.kind == DECISION:
        return stores[owner_kind](unit, owner_id, link.key)
    if link.kind in stores:
        return stores[link.kind](unit, link.ref, link.key)
    return answered_package(link.ref, link.key)


# One sweep at a time: a read during a sweep (its own, or another thread's)
# does not start another.
_sweeping = threading.Lock()


def sweep() -> Settled:
    """End every open wait whose link is already answered or signed off:
    at server start and on every read of either record. Never raises."""
    if not _sweeping.acquire(blocking=False):
        return Settled()
    try:
        from jarvis import work_store, workflow_store
        moved = Settled()
        moved.add(workflow_store.sweep_waits())
        moved.add(work_store.sweep_waits())
    except Exception:
        log.warning("wait links: the sweep failed", exc_info=True)
        return Settled()
    finally:
        _sweeping.release()
    tell(moved)
    return moved
