"""A Work decision on record, for the tests about what decisions do (waits,
states, answers) now that no reporter asks one (one channel, round 3: a
decision reaches him only as a package, and the route refuses
`decision_needed` and `decision_withdrawn`). It is recorded in process, as
JARVIS records its own (a package's `understood-` vote), with its asker as
the route would have recorded it, and the item is shown as the route shows
it."""
from __future__ import annotations

from jarvis import work_store

DECISION_KINDS = (work_store.DECISION_NEEDED, work_store.DECISION_WITHDRAWN)


def asker_of(auth) -> str:
    """The principal a signed request is recorded as (reporters.recorded)."""
    return f"agent:{auth.agent.session_id}"


def on_record(item_id: str, kind: str, summary: str, asker: str, **detail) -> dict:
    """`kind` (a decision asked, or withdrawn by its asker) on `item_id`, on
    record; {"item": the item as the page reads it}."""
    from jarvis import work_api
    if kind == work_store.DECISION_NEEDED:
        detail["asked_by"] = asker
    else:
        detail["by"] = asker
    item = work_store.record(work_store.WorkEvent(item_id, kind, summary, detail,
                                                  reporter=asker))
    return {"item": work_api.item_json(item)}
