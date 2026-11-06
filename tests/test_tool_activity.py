"""The tool activity feed: every call the brain makes through /internal/tool,
started and then refused, failed or completed, with no arguments, results or
words, kept in memory to a fixed size and read at /api/tools/activity."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from jarvis import jarvis_mcp, tool_activity

SECRET_ARGUMENT = "argument-words-that-must-never-leave"
SECRET_RESULT = "result-words-that-must-never-leave"


class _Brain:
    """The half of the brain the gates read."""

    ready = False

    def __init__(self, origin="user", tainted_by=None):
        self.current_origin = origin
        self.turn_untrusted_source = tainted_by

    @property
    def turn_is_tainted(self) -> bool:
        return self.turn_untrusted_source is not None

    def mark_untrusted_content(self, source: str) -> None:
        if self.turn_untrusted_source is None:
            self.turn_untrusted_source = source

    async def stop(self):
        pass


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import server as server_module
    importlib.reload(server_module)
    token = server_module.data_paths.ensure_tool_token()
    hints = []
    with TestClient(server_module.app,
                    headers={"Origin": "http://localhost:5173"}) as client:
        server_module.brain_instance = _Brain()
        monkeypatch.setattr(server_module, "_send_to_session_clients", hints.append)
        start = tool_activity.activity.since()["seq"]

        def call(tool, **arguments):
            r = client.post("/internal/tool",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"tool": tool, "arguments": arguments})
            assert r.status_code == 200, r.text
            return r.json()

        def feed():
            r = client.get(f"/api/tools/activity?since={start}")
            assert r.status_code == 200, r.text
            return r.json()

        call.client = client
        yield call, feed, server_module, hints


def _stages(feed_body, tool):
    return [e["stage"] for e in feed_body["events"] if e["tool"] == tool]


def test_a_call_that_returns_is_started_then_completed(wired, monkeypatch):
    call, feed, server, hints = wired
    monkeypatch.setitem(server.TOOL_HANDLERS, "recall", lambda args: SECRET_RESULT)
    assert call("recall", query=SECRET_ARGUMENT) == {"ok": True, "text": SECRET_RESULT}

    events = feed()["events"]
    assert [e["stage"] for e in events] == ["started", "completed"]
    started, completed = events
    assert started["call_id"] == completed["call_id"]
    assert started["tool"] == "recall" and started["category"] == "data"
    assert started["duration_ms"] is None
    assert completed["duration_ms"] >= 0
    assert completed["seq"] == started["seq"] + 1
    assert completed["at"] >= started["at"]
    assert hints == [], "the feed sends no hint"


def test_a_call_that_raises_ends_failed_and_its_reply_is_unchanged(wired, monkeypatch):
    call, feed, server, _ = wired

    def boom(args):
        raise RuntimeError(SECRET_RESULT)

    monkeypatch.setitem(server.TOOL_HANDLERS, "spawn_run", boom)
    reply = call("spawn_run", project="x", prompt=SECRET_ARGUMENT)
    assert reply["ok"] is False and reply["text"].startswith("That tool failed:")
    assert _stages(feed(), "spawn_run") == ["started", "failed"]


def test_an_acting_tool_off_his_turn_ends_refused(wired, monkeypatch):
    call, feed, server, _ = wired
    ran = []
    monkeypatch.setitem(server.TOOL_HANDLERS, "steer_session",
                        lambda args: ran.append(args) or "sent")
    server.brain_instance = _Brain(origin="watcher")
    reply = call("steer_session", name="x", prompt=SECRET_ARGUMENT)
    assert reply["ok"] is False and "not_allowed_from_event" in reply["text"]
    assert ran == []
    assert _stages(feed(), "steer_session") == ["started", "refused"]


def test_an_acting_tool_on_an_untrusted_turn_ends_refused(wired, monkeypatch):
    call, feed, server, _ = wired
    monkeypatch.setenv("JARVIS_REFUSE_UNTRUSTED_ACTIONS", "1")
    ran = []
    monkeypatch.setitem(server.TOOL_HANDLERS, "spawn_run",
                        lambda args: ran.append(args) or "started")
    server.brain_instance = _Brain(tainted_by="a file in one of your projects")
    reply = call("spawn_run", project="x", prompt=SECRET_ARGUMENT)
    assert reply["ok"] is False and ran == []
    assert _stages(feed(), "spawn_run") == ["started", "refused"]
    assert feed()["events"][-1]["category"] == "execution"


def test_a_digest_turn_refusal_ends_refused(wired, monkeypatch):
    call, feed, server, _ = wired
    monkeypatch.setitem(server.TOOL_HANDLERS, "recall", lambda args: "found")
    server.brain_instance = _Brain(origin=server.attention.DIGEST_ORIGIN)
    assert call("recall", query="x")["ok"] is False
    assert _stages(feed(), "recall") == ["started", "refused"]


def test_requests_that_name_no_real_tool_are_not_activity(wired):
    call, feed, server, hints = wired
    assert call("rm_rf")["ok"] is False
    assert feed()["events"] == [] and hints == []


def test_no_argument_result_or_word_reaches_the_feed(wired, monkeypatch):
    call, feed, server, _ = wired
    monkeypatch.setitem(server.TOOL_HANDLERS, "recall", lambda args: SECRET_RESULT)
    call("recall", query=SECRET_ARGUMENT)

    def boom(args):
        raise RuntimeError(SECRET_RESULT)

    monkeypatch.setitem(server.TOOL_HANDLERS, "remember", boom)
    call("remember", text=SECRET_ARGUMENT)
    body = feed()
    text = json.dumps(body)
    assert SECRET_ARGUMENT not in text and SECRET_RESULT not in text
    for e in body["events"]:
        assert set(e) == {"seq", "call_id", "tool", "category", "stage", "at",
                          "duration_ms"}


def test_the_route_reads_after_a_sequence_number(wired, monkeypatch):
    call, feed, server, _ = wired
    monkeypatch.setitem(server.TOOL_HANDLERS, "recall", lambda args: "x")
    call("recall")
    first = feed()
    call("recall")
    later = feed()
    assert later["generation"] == first["generation"]
    assert later["seq"] == first["seq"] + 2
    after = [e for e in later["events"] if e["seq"] > first["seq"]]
    assert [e["stage"] for e in after] == ["started", "completed"]


def test_the_route_refuses_a_negative_sequence(wired):
    call, _, _, _ = wired
    assert call.client.get("/api/tools/activity?since=-1").status_code == 422


def test_the_record_is_bounded_and_says_what_it_still_holds():
    feed = tool_activity.ToolActivity(keep=3)
    for _ in range(3):
        feed.end(feed.start("recall"), tool_activity.COMPLETED)
    body = feed.since(0)
    assert len(body["events"]) == 3
    assert body["seq"] == 6 and body["oldest"] == 4
    assert [e["seq"] for e in feed.since(5)["events"]] == [6]


def test_a_call_ends_once_and_only_with_an_ending():
    feed = tool_activity.ToolActivity()
    call = feed.start("recall")
    with pytest.raises(ValueError):
        feed.end(call, tool_activity.STARTED)
    feed.end(call, tool_activity.COMPLETED)
    feed.end(call, tool_activity.FAILED)
    assert [e["stage"] for e in feed.since()["events"]] == ["started", "completed"]
    assert feed.since()["oldest"] == 1


def test_an_empty_record_has_no_oldest():
    feed = tool_activity.ToolActivity()
    assert feed.since() == {"generation": feed.generation, "seq": 0,
                            "oldest": None, "events": []}


def test_every_exposed_tool_has_exactly_one_category(wired):
    _, _, server, _ = wired
    exposed = {spec["name"] for spec in jarvis_mcp.TOOL_SPECS}
    assert exposed == set(server.TOOL_HANDLERS)
    assert set(tool_activity.CATEGORIES) == exposed
    listed = [t for tools in tool_activity._BY_CATEGORY.values() for t in tools]
    assert len(listed) == len(set(listed)), "a tool is in two categories"
    assert set(tool_activity.CATEGORIES.values()) <= set(tool_activity.CATEGORY_NAMES)
