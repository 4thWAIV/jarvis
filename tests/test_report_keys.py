"""Each report an agent sends lands at most once (report_keys.py), and a
reporter that timed out asks whether it landed before it sends it again
(workflow_report.report, the one client work_report.py and message.py use).

The server keeps each key with its route's answer: a repeat is answered
with the original answer and writes nothing; a key still being written is
refused, not run twice; one claimed by an earlier run that never finished
is unknown. The client treats a timeout as unknown: it reports the answer
it finds for its key, or sends once more with the same key."""
from __future__ import annotations

import json

import pytest

from jarvis import report_keys, work_report, work_store, workflow_report, workflow_store
from tests.test_brain_page_parity import wired  # noqa: F401  (the fixture)

KEY = "k" * 24


@pytest.fixture
def agent(wired):
    from tests.agent_key_fixtures import onboarded_auth
    return onboarded_auth(("work", "workflows"), "sess-reporter")


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "speech"
    folder.mkdir()
    return work_store.create("Speech migration", "folder", str(folder))


def _event(wired, agent, item_id, summary, key=KEY):
    return wired.client.post(f"/api/work/{item_id}/events?report={key}", auth=agent,
                             json={"kind": "update", "summary": summary, "detail": {}})


def test_a_repeated_key_is_answered_with_the_original_answer_and_writes_once(
        wired, agent, item):
    first = _event(wired, agent, item.id, "Engine wired.")
    assert first.status_code == 200 and report_keys.REPEATED_HEADER not in first.headers
    again = _event(wired, agent, item.id, "Engine wired.")
    assert again.status_code == 200
    assert again.headers[report_keys.REPEATED_HEADER] == "repeated"
    assert again.json() == first.json()
    updates = [e for e in work_store.events(item.id) if e.summary == "Engine wired."]
    assert len(updates) == 1


def test_a_refusal_is_kept_and_repeated_too(wired, agent, item):
    first = wired.client.post(f"/api/work/{item.id}/events?report={KEY}", auth=agent,
                              json={"kind": "approved", "summary": "Done.", "detail": {}})
    assert first.status_code == 409
    again = wired.client.post(f"/api/work/{item.id}/events?report={KEY}", auth=agent,
                              json={"kind": "approved", "summary": "Done.", "detail": {}})
    assert (again.status_code, again.json()) == (409, first.json())


def test_every_reporting_route_takes_its_key_once(wired, agent, item):
    started = wired.client.post(f"/api/work?report={'s' * 20}", auth=agent, json={
        "title": "Captions", "source": {"kind": "folder", "path": item.source_path}})
    again = wired.client.post(f"/api/work?report={'s' * 20}", auth=agent, json={
        "title": "Captions", "source": {"kind": "folder", "path": item.source_path}})
    assert started.status_code == 200 and again.json()["id"] == started.json()["id"]
    assert sum(i.title == "Captions" for i in work_store.listing()) == 1
    flow = wired.client.post(f"/api/workflows?report={'w' * 20}", auth=agent,
                             json={"name": "Rebrand", "project": "site", "phases": ["plan"]})
    flow_again = wired.client.post(f"/api/workflows?report={'w' * 20}", auth=agent,
                                   json={"name": "Rebrand", "project": "site", "phases": ["plan"]})
    assert flow.status_code == 200 and flow_again.json() == flow.json()
    wid = flow.json()["id"]
    for _ in range(2):
        r = wired.client.post(f"/api/workflows/{wid}/events?report={'e' * 20}", auth=agent,
                              json={"kind": "task_added", "item": "copy",
                                    "summary": "Copy added.", "detail": {}})
        assert r.status_code == 200
    assert sum(e.kind == "task_added" for e in workflow_store.events(wid)) == 1


def test_keys_are_the_caller_s_own(wired, agent, item):
    from tests.agent_key_fixtures import onboarded_auth
    other = onboarded_auth(("work", "workflows"), "sess-other")
    _event(wired, agent, item.id, "Engine wired.")
    mine = wired.client.get(f"/api/reports/{KEY}", auth=agent).json()
    assert mine["state"] == "done" and mine["status"] == 200
    assert wired.client.get(f"/api/reports/{KEY}", auth=other).json() == {"state": "none"}
    # The other agent's same key is its own report, written.
    r = _event(wired, other, item.id, "Captions wired.")
    assert r.status_code == 200 and report_keys.REPEATED_HEADER not in r.headers


def test_a_key_still_being_written_is_not_run_twice_and_one_from_before_is_unknown(
        wired, agent, item):
    principal = "agent:sess-reporter"
    assert report_keys.claim(principal, f"/api/work/{item.id}/events", KEY) is None
    r = _event(wired, agent, item.id, "Engine wired.")
    assert r.status_code == 409 and r.json()["report"] == "pending"
    assert wired.client.get(f"/api/reports/{KEY}", auth=agent).json() == {"state": "pending"}
    report_keys.BOOT, before = "another-run", report_keys.BOOT
    try:
        r = _event(wired, agent, item.id, "Engine wired.")
        assert r.status_code == 409 and r.json()["report"] == "unknown"
    finally:
        report_keys.BOOT = before
    assert not any(e.summary == "Engine wired." for e in work_store.events(item.id))


def test_a_malformed_key_is_refused(wired, agent, item):
    r = _event(wired, agent, item.id, "Engine wired.", key="short")
    assert r.status_code == 400


def test_old_keys_are_pruned(wired):
    principal = "agent:sess-reporter"
    report_keys.claim(principal, "/api/work", KEY, now=1000.0)
    report_keys.claim(principal, "/api/work", "n" * 24)
    assert report_keys.prune() == 1
    assert report_keys.lookup(principal, KEY).state == report_keys.NONE
    assert report_keys.lookup(principal, "n" * 24).state == report_keys.PENDING


# ── the client: a timeout is unknown, not failed ─────────────────────────

class _Scripted:
    """A client whose answers are scripted: each a (status, body) or
    TimedOut, the requests it was sent kept."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def _next(self, method, path, body=None):
        self.sent.append((method, path, body))
        answer = self.answers.pop(0)
        if answer is workflow_report.TimedOut:
            raise workflow_report.TimedOut("timed out")
        status, data = answer
        return workflow_report._Response(status, json.dumps(data).encode(), "application/json")

    def post(self, path, json):
        return self._next("POST", path, json)

    def get(self, path, params=None):
        return self._next("GET", path)


def _key_of(path: str) -> str:
    return path.partition(f"?{workflow_report.REPORT_QUERY}=")[2]


def test_a_report_carries_a_key_of_its_own():
    client = _Scripted((200, {"ok": True}))
    assert workflow_report.report(client, "/api/work/w1/events", {"x": 1}) == {"ok": True}
    [(method, path, body)] = client.sent
    assert report_keys.valid(_key_of(path))
    joined = _Scripted((200, {}))
    workflow_report.report(joined, "/api/messages?work=w1", {})
    assert "?work=w1&report=" in joined.sent[0][1]


def test_a_timeout_that_landed_reports_the_original_answer_without_sending_again():
    client = _Scripted(workflow_report.TimedOut,
                       (200, {"state": "done", "status": 200, "body": {"item": {"id": "w1"}}}))
    assert workflow_report.report(client, "/api/work/w1/events", {}) == {"item": {"id": "w1"}}
    assert [m for m, _, _ in client.sent] == ["POST", "GET"]
    assert client.sent[1][1] == f"/api/reports/{_key_of(client.sent[0][1])}"


def test_a_timeout_that_did_not_land_is_sent_once_more_with_the_same_key():
    client = _Scripted(workflow_report.TimedOut, (200, {"state": "none"}), (200, {"ok": 1}))
    assert workflow_report.report(client, "/api/work/w1/events", {"a": 1}) == {"ok": 1}
    posts = [p for m, p, _ in client.sent if m == "POST"]
    assert len(posts) == 2 and posts[0] == posts[1]


def test_a_report_still_being_written_is_waited_for(monkeypatch):
    monkeypatch.setattr(workflow_report.time, "sleep", lambda s: None)
    client = _Scripted(workflow_report.TimedOut, (200, {"state": "pending"}),
                       (200, {"state": "done", "status": 200, "body": {"ok": 2}}))
    assert workflow_report.report(client, "/api/work/w1/events", {}) == {"ok": 2}
    assert [m for m, _, _ in client.sent] == ["POST", "GET", "GET"]


def test_twice_timed_out_says_it_is_unknown_and_names_the_key():
    client = _Scripted(workflow_report.TimedOut, workflow_report.TimedOut,
                       workflow_report.TimedOut)
    with pytest.raises(SystemExit, match="not known"):
        workflow_report.report(client, "/api/work/w1/events", {})


def test_work_report_survives_a_slow_answer_end_to_end(wired, agent, item, monkeypatch, capsys):
    """work_report.py through the real routes: the first answer is lost to
    a timeout after the write landed; the command asks, finds it, and the
    item has the update once."""
    client = wired.client
    lost = []

    def send(self, method, url, data, content_type):
        r = client.request(method, url, content=data, headers={"Content-Type": content_type},
                           auth=agent)
        if method == "POST" and not lost:
            lost.append(url)
            raise workflow_report.TimedOut("timed out")     # it landed; the answer did not
        return workflow_report._Response(r.status_code, r.content,
                                         r.headers.get("content-type", ""))
    monkeypatch.setattr(workflow_report._Client, "send_bytes", send)
    monkeypatch.setattr(workflow_report, "base_url", lambda: "http://testserver")
    monkeypatch.setattr(workflow_report, "signed_client",
                        lambda *a, **k: workflow_report._Client("http://testserver", "", None))
    monkeypatch.setattr(work_report, "signed_client",
                        lambda *a, **k: workflow_report._Client("http://testserver", "", None))
    assert work_report.main(["work_report.py", "update", item.id, "Engine wired."]) == 0
    assert item.id in capsys.readouterr().out
    assert sum(e.summary == "Engine wired." for e in work_store.events(item.id)) == 1
