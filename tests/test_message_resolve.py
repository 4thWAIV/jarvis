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
import time
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


@pytest.fixture(autouse=True)
def _the_mac_is_in_chicago(monkeypatch):
    """What JARVIS gives with no zone (`message_json`'s local, the tools'
    words) is the Mac's own zone: pinned to TZ here, so a runner in UTC
    reads the same clock as the tests."""
    if not hasattr(time, "tzset"):                     # pragma: no cover
        pytest.skip("the process zone cannot be pinned here")
    monkeypatch.setenv("TZ", TZ.key)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


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


# -- the times JARVIS writes are read back as the same message ---------------------------

@pytest.mark.parametrize("said,minutes", [
    ("09:05", ((9, 5),)), ("00:30", ((0, 30),)), ("today 09:05", ((9, 5),)),
    ("2026-11-12 12:30", ((12, 30),)), ("2026-11-12 09:05", ((9, 5),)),
    ("2026-11-12 21:05", ((21, 5),)), ("9:05", ((9, 5), (21, 5))),
])
def test_a_zero_padded_or_dated_time_is_the_24_hour_clock(said, minutes):
    assert api.parse_time(said).minutes == minutes


def test_every_local_and_when_jarvis_gives_names_that_same_message(at):
    day = (2026, 11, 11)
    sent = [at(_t(*day, h, m)) for h, m in ((0, 30), (9, 5), (12, 30), (21, 5))]
    sent.append(at(_t(2026, 11, 12, 9, 5)))           # the same minute, the next day
    for m in sent:
        assert resolve(at=api.message_json(m)["local"]).id == m.id
        # `when` is words for him; read back it is never another message,
        # though "12:30" with only a day word may be noon or midnight.
        when = api.when_said(m.at, now=NOW, tz=TZ)
        found = resolve(at=when)
        assert found.id == m.id or (found.item is None and not found.nearest
                                    and m.id in {c["id"] for c in found.candidates}), when
        assert found.id == m.id or when.endswith("12:30")


def test_a_filter_never_moves_a_zero_padded_time_to_the_other_half_of_the_day(at):
    morning, evening = at(_t(2026, 11, 12, 9, 5)), at(_t(2026, 11, 12, 21, 5) - 86400)
    cl.mark(morning.id, cl.MessageState.READ)
    found = resolve(at=api.local_time(morning.at, TZ), new=True)
    assert found.item is None, "the 09:05 message is read; 21:05 is never the one meant"
    assert evening.id not in {c["id"] for c in found.candidates} or found.nearest


# -- an id with a description is used only when it fits --------------------------------

def test_an_id_that_does_not_fit_the_time_given_asks_which(at):
    m41 = at(_t(2026, 11, 12, 15, 40), "Bridge")
    m42 = at(_t(2026, 11, 12, 15, 42), "Bridge")
    found = resolve(message=m41.id, at="15:42")
    assert found.item is None and "use neither" in found.why
    assert [c["id"] for c in found.candidates] == [m41.id, m42.id]
    assert resolve(message=m42.id, at="15:42").id == m42.id
    assert resolve(message=m41.id, sender="Bridge").id == m41.id
    wrong = resolve(message=m41.id, sender="Planning")
    assert wrong.item is None and wrong.candidates[0]["id"] == m41.id
    assert "is not a time" in resolve(message=m41.id, at="soon").why


# -- what the brain is given to name a message keeps its meaning past midnight ----------

def test_a_candidate_s_local_names_the_same_message_after_midnight(at):
    before = at(_t(2026, 11, 11, 15, 42), "Bridge")
    at(_t(2026, 11, 11, 15, 42, 40), "Planning")
    late = _t(2026, 11, 11, 23, 50, 0)
    asked = resolve(at="15:42", now=late)
    [shown] = [c for c in asked.candidates if c["id"] == before.id]
    assert shown["when"] == "today 15:42" and shown["local"] == "2026-11-11 15:42"
    today = at(_t(2026, 11, 12, 15, 42), "Bridge")
    after_midnight = _t(2026, 11, 12, 0, 10, 0)
    assert resolve(at=shown["local"], sender="Bridge", now=after_midnight).id == before.id
    assert today.id != before.id


# -- a Work item named by its title is looked for in its whole thread -------------------

def _work(tmp_path, title):
    from jarvis import work_store
    folder = tmp_path / uuid.uuid4().hex[:8]
    folder.mkdir()
    return work_store.create(title, "folder", str(folder)).id


def test_a_work_item_s_title_finds_a_message_older_than_a_day(at, tmp_path):
    work = _work(tmp_path, "Denice brain")
    old = at(_t(2026, 11, 10, 10, 0), about=work)
    assert resolve(work=work).id == old.id
    assert resolve(work="denice brain").id == old.id, "the title, as said"
    assert resolve(work="Denice Brain", new=True).id == old.id


def test_two_items_with_one_title_are_both_looked_in_and_asked_about(at, tmp_path):
    first, second = _work(tmp_path, "Agent Keys"), _work(tmp_path, "Agent Keys")
    a = at(_t(2026, 11, 9, 10, 0), about=first)
    b = at(_t(2026, 11, 10, 10, 0), about=second)
    found = resolve(work="Agent Keys")
    assert found.item is None and {c["id"] for c in found.candidates} == {a.id, b.id}
