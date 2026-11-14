"""A message named by its time is the message meant, never a wrong one.

"Read the message from 3:42", "the one at 15:42 from Bridge": every tool
and route that takes a message finds it through one function,
messages_api.resolve (docs/planning/brain-reads/design.md, section 3). A
time is his local time, matched to the minute against each message's
recorded time, narrowed by sender, Work item and NEW. Exactly one match
resolves; several are given back to ask which; none says so and names the
nearest. Every test here pins the zone and the clock; nothing reads a real
data/.
"""
import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jarvis import conversation_log as cl
from jarvis import messages_api as api

TZ = ZoneInfo("America/Chicago")


def _t(y, mo, d, h, mi, s=20, fold=0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=TZ, fold=fold).timestamp()


NOW = _t(2026, 11, 12, 16, 0, 0)        # Thursday 12 November 2026, 16:00 in Chicago


@pytest.fixture
def at(monkeypatch, tmp_path):
    """Record a message to him from `name` arriving at epoch `when`."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    from jarvis import run_store
    run_store.init_db()
    agents = {}

    def record(when: float, name: str = "Bridge", text: str = "Hello.", about: str = ""):
        session = f"s-{uuid.uuid4().hex[:12]}"
        agents[session] = (name, "idle")
        run_store.sync_agents(dict(agents))
        monkeypatch.setattr(cl, "time", SimpleNamespace(time=lambda: when))
        return cl.inbound(cl.ItemKind.MESSAGE_IN, cl.Counterpart.AGENT, session,
                          name, text, "agent_signature", source_kind=cl.Source.SIGNED,
                          source_id=uuid.uuid4().hex, about=about)
    return record


def resolve(now=NOW, **spec):
    return api.resolve(api.MessageSpec(**spec), now=now, tz=TZ)


# -- reading the time he gave ------------------------------------------------------------

@pytest.mark.parametrize("said,minutes,days_back", [
    ("15:42", ((15, 42),), None),
    ("3:42", ((3, 42), (15, 42)), None),
    ("3:42 pm", ((15, 42),), None),
    ("3:42pm", ((15, 42),), None),
    ("3:42 p.m.", ((15, 42),), None),
    ("3.42 PM", ((15, 42),), None),
    ("3:42 am", ((3, 42),), None),
    ("12:05 am", ((0, 5),), None),
    ("12:05 pm", ((12, 5),), None),
    ("12:05", ((0, 5), (12, 5)), None),
    ("15:42 h", ((15, 42),), None),
    ("3:42 h", ((3, 42),), None),
    ("today 15:42", ((15, 42),), 0),
    ("yesterday 3:42 pm", ((15, 42),), 1),
    ("hoy 15:42 h", ((15, 42),), 0),
    ("ayer 9:05", ((9, 5), (21, 5)), 1),
    ("at 3:42 today", ((3, 42), (15, 42)), 0),
    ("a las 15:42", ((15, 42),), None),
])
def test_the_forms_he_says_and_types(said, minutes, days_back):
    asked = api.parse_time(said)
    assert (asked.minutes, asked.days_back, asked.day) == (minutes, days_back, None)


def test_a_date_and_a_time():
    asked = api.parse_time("2026-11-10 15:42")
    assert (asked.day.isoformat(), asked.minutes) == ("2026-11-10", ((15, 42),))


@pytest.mark.parametrize("said", ["quarter to four", "25:00", "3:61", "15:42:10"])
def test_what_is_no_time_says_which_forms_are(said, at):
    found = resolve(at=said)
    assert found.item is None and "15:42" in found.why and "3:42 pm" in found.why


# -- one match, several, none ------------------------------------------------------------

def test_3_42_with_no_am_or_pm_finds_the_one_message_at_either(at):
    m = at(_t(2026, 11, 12, 15, 42))
    found = resolve(at="3:42")
    assert found.id == m.id and found.item.id == m.id


def test_3_42_finds_both_halves_of_the_day_and_asks_which(at):
    morning, afternoon = at(_t(2026, 11, 12, 3, 42)), at(_t(2026, 11, 12, 15, 42))
    found = resolve(at="3:42")
    assert found.item is None
    assert [c["id"] for c in found.candidates] == [afternoon.id, morning.id], "newest first"
    assert [c["when"] for c in found.candidates] == ["today 15:42", "today 03:42"]
    assert "ask him which" in found.why
    assert resolve(at="3:42 pm").id == afternoon.id
    assert resolve(at="3:42 am").id == morning.id
    assert resolve(at="15:42").id == afternoon.id


def test_two_in_the_same_minute_ask_which_and_the_sender_settles_it(at):
    bridge = at(_t(2026, 11, 12, 15, 42, 5), "Bridge")
    planning = at(_t(2026, 11, 12, 15, 42, 50), "Planning")
    found = resolve(at="15:42")
    assert found.item is None and {c["id"] for c in found.candidates} == {bridge.id, planning.id}
    assert {c["from"] for c in found.candidates} == {"Bridge", "Planning"}
    assert resolve(at="15:42", sender="Bridge").id == bridge.id
    assert resolve(at="3:42 pm", sender="planning").id == planning.id, "as said: any case"


def test_none_found_names_the_nearest(at):
    near = at(_t(2026, 11, 12, 15, 40))
    at(_t(2026, 11, 12, 11, 0))
    found = resolve(at="15:42")
    assert found.item is None and found.nearest
    assert [c["id"] for c in found.candidates] == [near.id]
    assert "No message to him fits" in found.why and "nearest is" in found.why


def test_two_as_near_are_both_named(at):
    before, after = at(_t(2026, 11, 12, 15, 40)), at(_t(2026, 11, 12, 15, 44))
    found = resolve(at="15:42")
    assert found.nearest and [c["id"] for c in found.candidates] == [before.id, after.id]
    assert "nearest are" in found.why


def test_nothing_near_at_all_says_so(at):
    found = resolve(at="15:42")
    assert found.item is None and not found.candidates and "none is near" in found.why


def test_a_bare_time_is_today_s_never_another_day_s(at):
    yesterday = at(_t(2026, 11, 11, 15, 42))
    today = at(_t(2026, 11, 12, 15, 42))
    assert resolve(at="15:42").id == today.id, "yesterday's is not matched while today has one"
    assert resolve(at="yesterday 15:42").id == yesterday.id
    assert resolve(at="ayer 3:42 pm").id == yesterday.id
    assert resolve(at="2026-11-11 15:42").id == yesterday.id
    assert resolve(at="today 15:42").id == today.id


def test_a_bare_time_looks_back_one_day(at):
    """At 15:00, "15:42" is yesterday's: the last 24 hours."""
    yesterday = at(_t(2026, 11, 11, 15, 42))
    assert resolve(at="15:42", now=_t(2026, 11, 12, 15, 0, 0)).id == yesterday.id
    older = at(_t(2026, 11, 10, 9, 5))
    assert resolve(at="9:05").item is None, "two days back is out of a bare time's reach"
    assert resolve(at="2026-11-10 9:05").id == older.id


def test_new_and_the_work_item_narrow_it(at):
    read = at(_t(2026, 11, 12, 15, 42, 5), about="work-a")
    fresh = at(_t(2026, 11, 12, 15, 42, 40), about="work-b")
    cl.mark(read.id, cl.MessageState.READ)
    assert resolve(at="15:42", new=True).id == fresh.id
    assert resolve(at="15:42", work="work-a").id == read.id
    assert resolve(at="15:42", work="work-c").item is None


def test_an_id_is_the_message_and_one_not_on_record_is_refused(at):
    m = at(_t(2026, 11, 12, 15, 42))
    assert resolve(message=m.id).id == m.id
    assert "no message 999" in resolve(message=999).why
    assert resolve(message=-1).item is None


def test_with_nothing_named_it_is_the_one_he_is_on(at):
    m = at(_t(2026, 11, 12, 15, 42))
    assert resolve(on=m.id).id == m.id
    assert "messages_to_him" in resolve().why


# -- daylight saving ------------------------------------------------------------------

def test_the_hour_that_happens_twice_asks_which(at):
    """1 November 2026, Chicago: 1:30 comes twice, once in CDT, once in CST."""
    first = at(_t(2026, 11, 1, 1, 30, fold=0))
    second = at(_t(2026, 11, 1, 1, 30, fold=1))
    assert second.at - first.at == 3600
    found = resolve(at="1:30 am", now=_t(2026, 11, 1, 3, 0, 0))
    assert {c["id"] for c in found.candidates} == {first.id, second.id}
    assert all(c["when"] == "today 01:30" for c in found.candidates)


def test_the_hour_that_never_happens_finds_nothing_and_names_the_nearest(at):
    """8 March 2026, Chicago: 2:30 am never happens."""
    at(_t(2026, 3, 8, 1, 50))
    after = at(_t(2026, 3, 8, 3, 1))
    found = resolve(at="2:30 am", now=_t(2026, 3, 8, 5, 0, 0))
    assert found.item is None and found.nearest
    assert [c["id"] for c in found.candidates] == [after.id]


# -- how a message's time is shown, in the form resolve reads -------------------------

def test_a_message_s_time_is_shown_as_resolve_reads_it(at):
    m = at(_t(2026, 11, 11, 15, 42))
    assert api.when_said(m.at, now=NOW, tz=TZ) == "yesterday 15:42"
    assert api.local_time(m.at, TZ) == "2026-11-11 15:42"
    assert api.when_said(_t(2026, 11, 2, 8, 0), now=NOW, tz=TZ) == "2026-11-02 08:00"
    for shown in ("yesterday 15:42", "2026-11-11 15:42"):
        assert resolve(at=shown).id == m.id
    out = api.message_json(m)
    assert {"local", "when"} <= set(out)


# -- the server: every tool that takes a message ----------------------------------------

def test_the_tools_name_a_message_by_its_time_and_ask_which_walled(monkeypatch, tmp_path):
    from tests.test_message_read_reply import _Speech
    import importlib
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import server
    importlib.reload(server)
    server.run_store.init_db()
    monkeypatch.setattr(server, "speech", _Speech())
    monkeypatch.setattr(server, "_notes_for_brain", [])
    when = server.time.time() - 30
    monkeypatch.setattr(server.conversation_log, "time", SimpleNamespace(time=lambda: when))
    server.run_store.sync_agents({"s-a1": ("Bridge", "idle"), "s-b2": ("Planning", "idle")})
    bridge = server.conversation_log.inbound(
        cl.ItemKind.MESSAGE_IN, cl.Counterpart.AGENT, "s-a1", "Bridge", "Ship it.",
        "agent_signature", source_kind=cl.Source.SIGNED, source_id="a")
    planning = server.conversation_log.inbound(
        cl.ItemKind.MESSAGE_IN, cl.Counterpart.AGENT, "s-b2", "Planning", "Wait.",
        "agent_signature", source_kind=cl.Source.SIGNED, source_id="b")
    shown = api.local_time(when)
    said = asyncio.run(server.tool_read_aloud({"at": shown, "from": "Bridge"}))
    assert said.startswith(f"Message {bridge.id},") and "Ship it." in said
    asked = asyncio.run(server.tool_read_aloud({"at": shown}))
    head, _, block = asked.partition("\n")
    assert "2 messages fit" in head and "Bridge" not in head, "names only inside the wall"
    assert block.startswith("<session-output") and "Bridge" in block and "Planning" in block
    marked = asyncio.run(server.tool_mark_message({"at": shown, "from": "planning",
                                                   "state": "done"}))
    assert marked == f"Message {planning.id} is done now, sir."
    assert "2 messages fit" in asyncio.run(server.tool_reply({"at": shown}))
    assert "2 messages fit" in asyncio.run(server.tool_open_inbox_package({"at": shown}))
