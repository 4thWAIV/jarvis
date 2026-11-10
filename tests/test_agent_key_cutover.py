"""The cut-over guard (legacy_access.py): old access keys keep working, as
before, until none has been used for seven days; then, for good, they admit
nothing."""
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_agent_keys import wired, keygen  # noqa: F401  (the fixtures)

KEY = "jarvis_key_" + "x" * 43
DAY = 86400.0


def _access_keys(label="diagnostics-work", categories=("work",)):
    from jarvis import data_paths
    data_paths.access_keys_path().write_text(json.dumps({"keys": [{
        "label": label, "categories": list(categories),
        "sha256": hashlib.sha256(KEY.encode()).hexdigest()}]}))


def _start(client, folder, headers):
    return client.post("/api/work", headers=headers, json={
        "title": "Keys", "source": {"kind": "folder", "path": folder}})


def test_an_access_key_still_works_while_one_was_used_this_week(wired):
    from jarvis import agent_key_store, legacy_access
    server, _, _, folder, _ = wired
    _access_keys()
    client = TestClient(server.app)
    bearer = {"Authorization": f"Bearer {KEY}"}
    r = _start(client, folder, bearer)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work/{r.json()['id']}").json()["events"][0]["reporter"] == \
        "key:diagnostics-work"
    assert agent_key_store.legacy_counts() == {"diagnostics-work": 1}
    assert client.post("/api/workflows", headers=bearer,
                       json={"name": "x", "project": "y"}).status_code == 403, \
        "only its own categories"
    assert legacy_access.pending(time.time() + 6 * DAY), "used today: kept for 7 days"


def test_seven_quiet_days_end_access_keys_for_good(wired):
    from jarvis import agent_key_store, legacy_access
    server, _, _, folder, _ = wired
    _access_keys()
    now = time.time()
    assert legacy_access.pending(now), "a week of watching first"
    agent_key_store.count_legacy("diagnostics-work", now)
    assert legacy_access.pending(now + 7 * DAY), "the last use's day still counts"
    assert not legacy_access.pending(now + 9 * DAY)
    assert agent_key_store.setting(legacy_access.CUTOVER) is not None
    agent_key_store.count_legacy("diagnostics-work", now + 10 * DAY)
    assert not legacy_access.pending(now + 10 * DAY), "once cut over, for good"
    r = _start(TestClient(server.app), folder, {"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 403
    assert "admit nothing" in server.tool_agent_keys({})


def test_with_no_access_key_on_record_there_is_nothing_to_wait_for(wired):
    from jarvis import legacy_access
    assert not legacy_access.pending()
    assert legacy_access.status()["cut_over_at"] is not None


def test_the_brain_and_the_page_say_where_the_cut_over_stands(wired):
    server, _, _, folder, _ = wired
    _access_keys()
    _start(TestClient(server.app), folder, {"Authorization": f"Bearer {KEY}"})
    assert "Access keys are still accepted: one request" in server.tool_agent_keys({})
    got = TestClient(server.app).get("/api/agent-keys").json()["access_keys"]
    assert got["used"] == {"diagnostics-work": 1} and got["cut_over_at"] is None


def test_an_access_key_never_approves_a_person_s_message(wired):
    from jarvis import web_auth
    assert web_auth.admitted_by_agent({web_auth.ADMITTED: web_auth.BY_ACCESS_KEY})
